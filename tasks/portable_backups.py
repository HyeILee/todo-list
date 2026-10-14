"""Portable schedule backups; authentication and provider-level backups are separate."""
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
from io import BytesIO
import zipfile

from django.core import serializers
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connections, transaction
from django.utils import timezone

from .backups import BackupValidationError, MAX_ARCHIVE_SIZE, _latest_task_schema
from .exports import csv_for_records, csv_for_tasks, workbook_for_records, workbook_for_tasks
from .models import Category, DailyPhoto, DailyRecord, Task, TaskStatusHistory
from urllib.parse import urlparse


FORMAT = "dugyeon-data-v2"
LEGACY_MODELS = (Category, Task, TaskStatusHistory, DailyRecord)
MODELS = (*LEGACY_MODELS, DailyPhoto)
DATA_FILES = {"data.json", "tasks.xlsx", "tasks.csv", "records.xlsx", "records.csv"}


class ValidatedObjects(list):
    def __init__(self, objects, models):
        super().__init__(objects)
        self.restore_models = models


def write_private_backup(content, directory, prefix):
    directory = Path(directory).resolve()
    for path in (settings.BASE_DIR / "static", settings.STATIC_ROOT):
        path = Path(path).resolve()
        if directory == path or path in directory.parents:
            raise ValueError("Backup directory must not be publicly served.")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S-%f")
    target = directory / f"{prefix}-{stamp}.zip"
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return target


@contextmanager
def consistent_snapshot():
    database = connections["default"]
    if database.in_atomic_block:
        raise RuntimeError("A new transaction is required for a consistent backup.")
    with transaction.atomic():
        if database.vendor == "postgresql":
            with database.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield


def create_data_backup():
    with consistent_snapshot():
        rows = [row for model in MODELS for row in model.objects.order_by("pk")]
        tasks = list(Task.objects.filter(deleted_at__isnull=True).select_related("category").order_by("pk"))
        records = list(DailyRecord.objects.order_by("record_date"))
        files = {
            "data.json": serializers.serialize("json", rows).encode("utf-8"),
            "tasks.xlsx": workbook_for_tasks(tasks), "tasks.csv": csv_for_tasks(tasks),
            "records.xlsx": workbook_for_records(records), "records.csv": csv_for_records(records),
        }
        manifest = {
            "format": FORMAT, "scope": "schedule-data-only",
            "db_schema_version": _latest_task_schema(connections["default"]),
            "created_at": timezone.now().isoformat(),
            "photo_originals_included": False,
            "counts": {model._meta.label_lower: model.objects.count() for model in MODELS},
            "sha256": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
        }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_SIZE or sum(map(len, files.values())) > MAX_ARCHIVE_SIZE:
        raise ValueError("Backup exceeds the supported archive size.")
    return result, timezone.localtime().strftime("%Y%m%d-%H%M%S")


def validate_data_backup(content):
    """Validate every object before modifying any current data; never expose values."""
    try:
        if len(content) > MAX_ARCHIVE_SIZE:
            raise ValueError
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) != len(DATA_FILES) + 1 or {entry.filename for entry in entries} != DATA_FILES | {"manifest.json"}:
                raise ValueError
            if sum(entry.file_size for entry in entries) > MAX_ARCHIVE_SIZE or any(entry.flag_bits & 1 for entry in entries):
                raise ValueError
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") not in {FORMAT, "dugyeon-data-v1"} or manifest.get("scope") != "schedule-data-only":
                raise ValueError
            current_schema = _latest_task_schema(connections["default"])
            compatible_schemas = {current_schema}
            if current_schema in {"0007_backupreminder", "0008_backup_cycles", "0009_conditional_passkeys"}:
                compatible_schemas.update({"0006_dailyphoto_category", "0007_backupreminder"})
            if current_schema == "0009_conditional_passkeys":
                compatible_schemas.add("0008_backup_cycles")
            if manifest.get("db_schema_version") not in compatible_schemas:
                raise ValueError
            checksums = manifest["sha256"]
            if not isinstance(checksums, dict) or set(checksums) != DATA_FILES:
                raise ValueError
            files = {name: archive.read(name) for name in DATA_FILES}
            if any(not isinstance(checksums[name], str) or not hmac.compare_digest(hashlib.sha256(value).hexdigest(), checksums[name]) for name, value in files.items()):
                raise ValueError
        raw = json.loads(files["data.json"])
        models = LEGACY_MODELS if manifest["format"] == "dugyeon-data-v1" else MODELS
        allowed = {model._meta.label_lower: model for model in models}
        counts = dict.fromkeys(allowed, 0)
        seen = set()
        if not isinstance(raw, list):
            raise ValueError
        for row in raw:
            if not isinstance(row, dict) or set(row) != {"model", "pk", "fields"} or row["model"] not in allowed or not isinstance(row["pk"], str):
                raise ValueError
            model = allowed[row["model"]]
            fields = {field.name for field in model._meta.local_fields if not field.primary_key}
            if not isinstance(row["fields"], dict) or set(row["fields"]) != fields:
                raise ValueError
            identity = (row["model"], str(row["pk"]))
            if identity in seen:
                raise ValueError
            seen.add(identity)
            counts[row["model"]] += 1
        if counts != manifest.get("counts"):
            raise ValueError
        objects = ValidatedObjects(serializers.deserialize("json", files["data.json"]), models)
        ids = {model: {item.object.pk for item in objects if type(item.object) is model} for model in models}
        if any(len(ids[model]) != counts[model._meta.label_lower] for model in models):
            raise ValueError
        for item in objects:
            obj = item.object
            excluded = [field.name for field in obj._meta.local_fields if field.is_relation]
            obj.full_clean(exclude=excluded, validate_unique=False, validate_constraints=False)
            if isinstance(obj, Task) and obj.category_id not in ids[Category]:
                raise ValueError
            if isinstance(obj, TaskStatusHistory) and obj.task_id not in ids[Task]:
                raise ValueError
        dates = [item.object.record_date for item in objects if isinstance(item.object, DailyRecord)]
        if len(set(dates)) != len(dates):
            raise ValueError
        for item in objects:
            if isinstance(item.object, DailyRecord) and not any((item.object.today_story, item.object.memorable_sentence, item.object.stayed_with_me)):
                raise ValueError
        photo_positions = set()
        for item in objects:
            if isinstance(item.object, DailyPhoto):
                photo = item.object
                url = urlparse(photo.blob_url)
                if url.scheme != "https" or not (url.hostname or "").endswith(".private.blob.vercel-storage.com") or url.username or url.password:
                    raise ValueError
                if photo.content_type not in {"image/jpeg", "image/png", "image/webp"} or not 0 < photo.size_bytes <= 2_500_000 or not 0 <= photo.position <= 9:
                    raise ValueError
                identity = (photo.record_date, photo.position)
                if identity in photo_positions:
                    raise ValueError
                photo_positions.add(identity)
        return objects, manifest
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, zipfile.BadZipFile, RuntimeError, NotImplementedError, ValidationError, serializers.base.DeserializationError):
        raise BackupValidationError("Invalid or incompatible schedule backup; current data unchanged.") from None


def restore_validated_data(objects):
    """Caller must save a safety backup and stop application writes beforehand."""
    models = getattr(objects, "restore_models", LEGACY_MODELS)
    with transaction.atomic():
        if connections["default"].vendor == "postgresql":
            with connections["default"].cursor() as cursor:
                for model in models:
                    cursor.execute(f'LOCK TABLE "{model._meta.db_table}" IN ACCESS EXCLUSIVE MODE')
        for model in reversed(models):
            model.objects.all().delete()
        for model in models:
            for item in objects:
                if type(item.object) is model:
                    item.save()
        connections["default"].check_constraints()
