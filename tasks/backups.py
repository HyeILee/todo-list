import hashlib
import hmac
import json
import logging
import os
import sqlite3
import uuid
import zipfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from django.core.exceptions import ImproperlyConfigured
from django.conf import settings
from django.db import connections, transaction
from django.db.models import F
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from .exports import csv_for_records, csv_for_tasks, workbook_for_records, workbook_for_tasks
from .models import BackupLog, Category, DailyRecord, Task


APP_VERSION = "0.3.1"
BACKUP_FILES = {"schedule.sqlite3", "업무기록.xlsx", "업무기록.csv", "개인기록.xlsx", "개인기록.csv"}
MAX_ARCHIVE_SIZE = 256 * 1024 * 1024
error_logger = logging.getLogger("dugyeon.errors")


def create_full_backup():
    database = connections["default"]
    if database.vendor == "postgresql":
        from .portable_backups import create_data_backup
        return create_data_backup()
    if database.vendor != "sqlite":
        raise ImproperlyConfigured("전체 백업은 SQLite 데이터베이스에서만 지원합니다.")

    tasks = list(Task.objects.filter(deleted_at__isnull=True).select_related("category").order_by(F("task_date").asc(nulls_last=True), "category__sort_order", "sort_order"))
    records = list(DailyRecord.objects.exclude(today_story="", memorable_sentence="", stayed_with_me="").order_by("record_date"))

    with TemporaryDirectory(prefix="dugyeon-backup-") as temporary_directory:
        database_copy_path = Path(temporary_directory) / "schedule.sqlite3"
        database.ensure_connection()
        destination = sqlite3.connect(str(database_copy_path))
        try:
            database.connection.backup(destination)
        finally:
            destination.close()

        files = {
            "schedule.sqlite3": database_copy_path.read_bytes(),
            "업무기록.xlsx": workbook_for_tasks(tasks),
            "업무기록.csv": csv_for_tasks(tasks),
            "개인기록.xlsx": workbook_for_records(records),
            "개인기록.csv": csv_for_records(records),
        }
        applied = MigrationRecorder(database).applied_migrations()
        task_migrations = sorted(name for app, name in applied if app == "tasks")
        created_at = timezone.localtime().isoformat()
        manifest = {
            "app_version": APP_VERSION,
            "db_schema_version": task_migrations[-1] if task_migrations else "unmigrated",
            "backup_created_at": created_at,
            "time_zone": timezone.get_current_timezone_name(),
            "sha256": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
            "counts": {
                "tasks": Task.objects.count(),
                "categories": Category.objects.count(),
                "daily_records": DailyRecord.objects.count(),
            },
            "minimum_app_version": APP_VERSION,
        }
        output = BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        return output.getvalue(), timezone.localtime().strftime("%Y%m%d-%H%M")


def _backup_database_file(target_path):
    database = connections["default"]
    if database.vendor != "sqlite":
        raise ImproperlyConfigured("자동 백업은 SQLite 데이터베이스에서만 지원합니다.")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    database.ensure_connection()
    destination = sqlite3.connect(str(target_path))
    try:
        database.connection.backup(destination)
    finally:
        destination.close()


def _apply_backup_retention(now):
    backup_directory = Path(settings.BACKUP_DIR)
    daily_cutoff = now - timedelta(days=30)
    old_daily = BackupLog.objects.filter(
        backup_type=BackupLog.BackupType.DAILY,
        status=BackupLog.Status.SUCCESS,
        started_at__lt=daily_cutoff,
    ).order_by("started_at")
    monthly_latest = {}
    for entry in old_daily:
        local_started = timezone.localtime(entry.started_at)
        monthly_latest[(local_started.year, local_started.month)] = entry
    keep_months = sorted(monthly_latest)[-12:]
    keep_ids = {monthly_latest[month].pk for month in keep_months}
    for entry in old_daily:
        source = backup_directory / entry.file_name
        if entry.pk in keep_ids:
            local_started = timezone.localtime(entry.started_at)
            monthly_name = f"schedule-monthly-{local_started:%Y%m}.sqlite3"
            target = backup_directory / monthly_name
            if source.exists() and source != target:
                os.replace(source, target)
            entry.backup_type = BackupLog.BackupType.MONTHLY
            entry.file_name = monthly_name
            entry.save(update_fields=["backup_type", "file_name"])
        else:
            source.unlink(missing_ok=True)
            entry.delete()

    monthly_entries = list(BackupLog.objects.filter(
        backup_type=BackupLog.BackupType.MONTHLY,
        status=BackupLog.Status.SUCCESS,
    ).order_by("started_at"))
    monthly_by_period = {}
    for entry in monthly_entries:
        local_started = timezone.localtime(entry.started_at)
        monthly_by_period[(local_started.year, local_started.month)] = entry
    retained_periods = set(sorted(monthly_by_period)[-12:])
    for period, entry in monthly_by_period.items():
        if period in retained_periods:
            continue
        (backup_directory / entry.file_name).unlink(missing_ok=True)
        entry.delete()


def ensure_daily_backup_before_change():
    now = timezone.now()
    today = timezone.localdate(now)
    backup_directory = Path(settings.BACKUP_DIR)
    filename = f"schedule-daily-{today:%Y%m%d}.sqlite3"
    target = backup_directory / filename
    log = None
    temporary_path = None
    try:
        if BackupLog.objects.filter(
            backup_type=BackupLog.BackupType.DAILY,
            status=BackupLog.Status.SUCCESS,
            started_at__date=today,
        ).exists():
            return True
        log = BackupLog.objects.create(
            backup_type=BackupLog.BackupType.DAILY,
            file_name=filename,
            sha256="",
            status=BackupLog.Status.STARTED,
            started_at=now,
        )
        with NamedTemporaryFile(dir=backup_directory, prefix=".daily-", suffix=".sqlite3", delete=False) as temporary_file:
            temporary_path = Path(temporary_file.name)
        _backup_database_file(temporary_path)
        digest = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
        os.replace(temporary_path, target)
        temporary_path = None
        completed = timezone.now()
        log.sha256 = digest
        log.status = BackupLog.Status.SUCCESS
        log.completed_at = completed
        log.message = "일일 SQLite 온라인 백업을 생성했습니다."
        log.save(update_fields=["sha256", "status", "completed_at", "message"])
        _apply_backup_retention(completed)
        return True
    except Exception as error:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)
        if log:
            try:
                log.status = BackupLog.Status.FAILED
                log.completed_at = timezone.now()
                log.message = f"일일 백업 실패: {type(error).__name__}"
                log.save(update_fields=["status", "completed_at", "message"])
            except Exception:
                error_logger.exception("Failed to record the daily backup failure state.")
        return False


class BackupValidationError(ValueError):
    pass


class BackupRestoreError(RuntimeError):
    pass


def _latest_task_schema(database):
    migrations = MigrationRecorder(database).applied_migrations()
    task_migrations = sorted(name for app, name in migrations if app == "tasks")
    return task_migrations[-1] if task_migrations else "unmigrated"


def _check_database_snapshot(database_bytes, manifest):
    with TemporaryDirectory(prefix="dugyeon-restore-check-") as temporary_directory:
        database_path = Path(temporary_directory) / "schedule.sqlite3"
        database_path.write_bytes(database_bytes)
        database = sqlite3.connect(str(database_path))
        try:
            if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupValidationError("SQLite 무결성 검증에 실패했습니다.")
            if database.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise BackupValidationError("SQLite 외래 키 검증에 실패했습니다.")
            tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required_tables = {"tasks_task", "tasks_category", "tasks_dailyrecord", "tasks_backuplog", "django_migrations"}
            if not required_tables.issubset(tables):
                raise BackupValidationError("백업 DB에 필요한 테이블이 없습니다.")
            counts = {
                "tasks": database.execute("SELECT COUNT(*) FROM tasks_task").fetchone()[0],
                "categories": database.execute("SELECT COUNT(*) FROM tasks_category").fetchone()[0],
                "daily_records": database.execute("SELECT COUNT(*) FROM tasks_dailyrecord").fetchone()[0],
            }
            if counts != manifest.get("counts"):
                raise BackupValidationError("manifest의 데이터 건수와 백업 DB가 일치하지 않습니다.")
            task_schema = database.execute("SELECT name FROM django_migrations WHERE app='tasks' ORDER BY name DESC LIMIT 1").fetchone()
            if not task_schema or task_schema[0] != manifest.get("db_schema_version"):
                raise BackupValidationError("manifest의 스키마 버전과 백업 DB가 일치하지 않습니다.")
        except sqlite3.Error as error:
            raise BackupValidationError("백업 DB를 읽을 수 없습니다.") from error
        finally:
            database.close()


def validate_backup_archive(archive_bytes):
    if len(archive_bytes) > MAX_ARCHIVE_SIZE:
        raise BackupValidationError("백업 ZIP 크기가 허용 범위를 초과했습니다.")
    try:
        with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
            entries = archive.infolist()
            expected_names = BACKUP_FILES | {"manifest.json"}
            if len(entries) != len(expected_names) or {entry.filename for entry in entries} != expected_names:
                raise BackupValidationError("백업 ZIP의 파일 구성이 올바르지 않습니다.")
            if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_SIZE:
                raise BackupValidationError("압축 해제 후 백업 크기가 허용 범위를 초과했습니다.")
            if any(entry.flag_bits & 1 for entry in entries):
                raise BackupValidationError("암호화된 ZIP은 지원하지 않습니다.")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if not isinstance(manifest, dict):
                raise BackupValidationError("manifest 구조가 올바르지 않습니다.")
            required_fields = {"app_version", "db_schema_version", "backup_created_at", "time_zone", "sha256", "counts", "minimum_app_version"}
            if not required_fields.issubset(manifest):
                raise BackupValidationError("manifest에 필수 정보가 없습니다.")
            checksums = manifest.get("sha256", {})
            if not isinstance(checksums, dict) or set(checksums) != BACKUP_FILES:
                raise BackupValidationError("manifest의 해시 목록이 올바르지 않습니다.")
            if any(not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value) for value in checksums.values()):
                raise BackupValidationError("manifest의 SHA-256 값 형식이 올바르지 않습니다.")
            file_contents = {name: archive.read(name) for name in BACKUP_FILES}
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError, NotImplementedError, RuntimeError) as error:
        raise BackupValidationError("읽을 수 있는 백업 ZIP이 아닙니다.") from error

    for filename, content in file_contents.items():
        actual = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(actual, checksums[filename]):
            raise BackupValidationError(f"{filename}의 SHA-256 해시가 일치하지 않습니다.")
    current_schema = _latest_task_schema(connections["default"])
    compatible_schemas = {current_schema}
    if current_schema == "0007_backupreminder":
        compatible_schemas.add("0006_dailyphoto_category")
    if manifest.get("db_schema_version") not in compatible_schemas:
        raise BackupValidationError("현재 앱과 호환되지 않는 DB 스키마 버전입니다.")
    try:
        app_version = tuple(int(value) for value in manifest["app_version"].split("."))
        minimum = tuple(int(value) for value in manifest["minimum_app_version"].split("."))
        current = tuple(int(value) for value in APP_VERSION.split("."))
    except (KeyError, AttributeError, ValueError) as error:
        raise BackupValidationError("manifest의 최소 앱 버전이 올바르지 않습니다.") from error
    if minimum > current or app_version < minimum:
        raise BackupValidationError("이 백업은 더 새로운 앱 버전이 필요합니다.")
    _check_database_snapshot(file_contents["schedule.sqlite3"], manifest)
    return file_contents["schedule.sqlite3"], manifest


def restore_database_archive(archive_bytes):
    if connections["default"].vendor != "sqlite":
        raise BackupRestoreError("SQLite 전체 백업 복원은 SQLite 환경에서만 지원합니다.")
    database_bytes, manifest = validate_backup_archive(archive_bytes)
    database = connections["default"]
    database_name = str(database.settings_dict["NAME"])
    if database_name == ":memory:" or database_name.startswith("file:"):
        raise BackupRestoreError("파일 기반 SQLite 데이터베이스에서만 복원할 수 있습니다.")

    database_path = Path(database_name).resolve()
    if not database_path.is_file():
        raise BackupRestoreError("현재 SQLite 데이터베이스 파일을 찾을 수 없습니다.")
    backup_directory = Path(settings.BACKUP_DIR)
    backup_directory.mkdir(parents=True, exist_ok=True)
    started_at = timezone.now()
    pre_restore, _ = create_full_backup()
    timestamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    pre_restore_path = backup_directory / f"schedule-pre-restore-{timestamp}.zip"
    with NamedTemporaryFile(dir=backup_directory, prefix=".pre-restore-", delete=False) as temporary_file:
        temporary_file.write(pre_restore)
        temporary_backup_path = Path(temporary_file.name)
    os.replace(temporary_backup_path, pre_restore_path)
    backup_log = BackupLog.objects.create(
        backup_type=BackupLog.BackupType.PRE_RESTORE,
        file_name=pre_restore_path.name,
        sha256=hashlib.sha256(pre_restore).hexdigest(),
        status=BackupLog.Status.SUCCESS,
        started_at=started_at,
        completed_at=timezone.now(),
        message="복원 전 안전 백업을 생성했습니다.",
    )

    with NamedTemporaryFile(dir=database_path.parent, prefix=f".{database_path.name}-restore-", suffix=".sqlite3", delete=False) as temporary_file:
        temporary_file.write(database_bytes)
        staged_database_path = Path(temporary_file.name)
    try:
        # The only accepted older schema differs by the additive reminder table.
        # Upgrade the staged copy before swapping; a failed upgrade leaves the live DB intact.
        if manifest["db_schema_version"] != _latest_task_schema(database):
            from django.db.backends.sqlite3.base import DatabaseWrapper
            from django.db.migrations.executor import MigrationExecutor
            staged_settings = database.settings_dict.copy()
            staged_settings["NAME"] = str(staged_database_path)
            alias = f"restore_staging_{uuid.uuid4().hex}"
            upgraded = DatabaseWrapper(staged_settings, alias=alias)
            connections[alias] = upgraded
            try:
                MigrationExecutor(upgraded).migrate([("tasks", _latest_task_schema(database))])
            finally:
                upgraded.close()
                del connections[alias]
        staged = sqlite3.connect(str(staged_database_path))
        try:
            staged.execute("DELETE FROM django_session")
            staged.execute(
                "INSERT INTO tasks_backuplog (id, backup_type, file_name, sha256, status, started_at, completed_at, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), backup_log.backup_type, backup_log.file_name, backup_log.sha256, backup_log.status, started_at.replace(tzinfo=None).isoformat(sep=" "), timezone.now().replace(tzinfo=None).isoformat(sep=" "), backup_log.message),
            )
            staged.commit()
        finally:
            staged.close()
        connections.close_all()
        if Path(f"{database_path}-wal").exists() or Path(f"{database_path}-shm").exists():
            raise BackupRestoreError("SQLite가 아직 사용 중이어서 복원을 중단했습니다.")
        os.replace(staged_database_path, database_path)
    except Exception:
        staged_database_path.unlink(missing_ok=True)
        raise
    return manifest["counts"]
