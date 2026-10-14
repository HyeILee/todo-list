"""Launch an isolated fixture server and a headless Edge UI audit."""
import os
import secrets
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
with tempfile.TemporaryDirectory(prefix="dugyeon-browser-") as directory:
    os.environ["DATABASE_URL"] = ""
    os.environ["VERCEL"] = ""
    os.environ["BROWSER_AUDIT_PASSWORD"] = secrets.token_urlsafe(32)
    os.environ["SQLITE_PATH"] = str(Path(directory) / "fixture.sqlite3")
    os.environ["BACKUP_DIR"] = str(Path(directory) / "backups")
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from django.db import connections
    from django.utils import timezone
    from tasks.models import Category, Task, DailyRecord
    call_command("migrate", verbosity=0)
    get_user_model().objects.create_user(username="browser-audit", password=os.environ["BROWSER_AUDIT_PASSWORD"])
    for index in range(4):
        category = Category.objects.create(name=f"Test category {index}", is_archived=index == 2)
        if index < 3:
            Task.objects.create(category=category, task_date=timezone.localdate(), title=f"Test task {index}")
    DailyRecord.objects.create(record_date=timezone.localdate(), today_story="Browser fixture")
    connections.close_all()
    with (Path(directory) / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([sys.executable, "manage.py", "runserver", "127.0.0.1:8001", "--noreload"], cwd=root, stdout=log, stderr=log)
        try:
            for attempt in range(100):
                try:
                    with urlopen("http://127.0.0.1:8001/accounts/login/", timeout=1):
                        break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("Fixture server exited")
                    time.sleep(0.1)
            else:
                raise TimeoutError("Fixture server did not start")
            subprocess.run(["node", "scripts/browser_category_audit.mjs"], cwd=root, check=True, timeout=120)
        finally:
            server.terminate()
            server.wait(timeout=10)
