"""Real PostgreSQL regression against a private, disposable local database.

Test-only binaries: pip install --target .deployment-tools/python pgserver==0.1.4
Neither pgserver nor its server data are deployment dependencies.
"""
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".deployment-tools" / "python"))
from pgserver._commands import POSTGRES_BIN_PATH
import psycopg
import tzdata


def main():
    before = {p: hashlib.sha256(p.read_bytes()).digest() for p in (ROOT / "data").rglob("*") if p.is_file()}
    with tempfile.TemporaryDirectory(prefix="dugyeon-pg-check-") as directory:
        temporary = Path(directory)
        # These older test binaries need ASCII paths on Korean Windows.
        install = temporary / "pginstall"
        shutil.copytree(Path(POSTGRES_BIN_PATH).parent, install)
        # The test wheel omits timezone files; use installed IANA TZif data.
        shutil.copytree(Path(tzdata.__file__).parent / "zoneinfo", install / "share" / "postgresql" / "timezone")
        binary = install / "bin"
        data = temporary / "pgdata"
        with (temporary / "native.log").open("wb") as log:
            result = subprocess.run([str(binary / "initdb.exe"), "-D", str(data), "--auth=trust", "--encoding=UTF8", "--locale=C", "-U", "postgres"], stdout=log, stderr=log)
            if result.returncode:
                raise RuntimeError("Isolated PostgreSQL initialization failed; native output suppressed")
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            start = subprocess.run([str(binary / "pg_ctl.exe"), "-D", str(data), "-l", str(temporary / "postgres.log"), "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"], stdout=log, stderr=log)
            if start.returncode:
                raise RuntimeError("Isolated PostgreSQL startup failed")
            try:
                url = f"postgresql://postgres@127.0.0.1:{port}/postgres?sslmode=disable"
                with psycopg.connect(url, autocommit=True) as connection:
                    print("Temporary PostgreSQL version:", connection.execute("SHOW server_version").fetchone()[0], flush=True)
                env = os.environ.copy()
                env.update(DATABASE_URL=url, VERCEL="", DJANGO_SECRET_KEY=secrets.token_urlsafe(64), DJANGO_DEBUG="True",
                           DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost", DJANGO_CSRF_TRUSTED_ORIGINS="", REMOTE_OPERATION_APPROVED="False",
                           BACKUP_DIR=str(temporary / "backups"), DJANGO_STATIC_ROOT=str(temporary / "staticfiles"), PYTHONIOENCODING="utf-8")
                def run(*args, environment=None):
                    print("RUN PostgreSQL", " ".join(args), flush=True)
                    subprocess.run([sys.executable, "-B", "manage.py", *args], cwd=ROOT, env=environment or env, check=True)
                run("check")
                run("migrate", "--noinput", "--verbosity", "0")
                run("makemigrations", "--check", "--dry-run")
                # SQLite snapshot/whole-file restore tests are intentionally SQLite-only.
                labels = ["tasks.test_postgres"] if "--quick" in sys.argv else [
                    "tasks.test_tasks.AccessibleFormTests", "tasks.test_tasks.DashboardRegressionTests",
                    "tasks.test_tasks.ChecklistTests", "tasks.test_tasks.RecordAndExportTests", "tasks.test_category_routes",
                    "tasks.test_deployment", "tasks.test_postgres"]
                run("test", *labels, "--verbosity", "1")
                run("backup_database")
                production = env.copy()
                production.update(VERCEL="1", DJANGO_DEBUG="False", REMOTE_OPERATION_APPROVED="True",
                                  DJANGO_ALLOWED_HOSTS="dugyeon-check.vercel.app", DJANGO_CSRF_TRUSTED_ORIGINS="https://dugyeon-check.vercel.app",
                                  DATABASE_URL=f"postgresql://postgres@127.0.0.1:{port}/postgres?sslmode=require")
                # check --deploy does not connect to the database; TLS is verified on the actual provider after linking.
                run("check", "--deploy", environment=production)
                production["DATABASE_URL"] = ""
                failed = subprocess.run([sys.executable, "-B", "manage.py", "check"], cwd=ROOT, env=production, capture_output=True)
                assert failed.returncode != 0 and b"Vercel requires PostgreSQL" in failed.stderr
                print("PASS Vercel refuses SQLite fallback", flush=True)
            finally:
                stop = subprocess.run([str(binary / "pg_ctl.exe"), "-D", str(data), "-m", "fast", "-w", "stop"], stdout=log, stderr=log)
                if stop.returncode:
                    raise RuntimeError("Temporary PostgreSQL shutdown failed")
    assert all(p.is_file() and hashlib.sha256(p.read_bytes()).digest() == digest for p, digest in before.items())
    assert set(before) == {p for p in (ROOT / "data").rglob("*") if p.is_file()}
    print("PASS PostgreSQL regressions and original SQLite/backup files unchanged", flush=True)


if __name__ == "__main__":
    main()
