"""Deployment checks against disposable fixtures, never the personal database."""
import hashlib
import http.cookiejar
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPCookieProcessor


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def main():
    originals = {p: hashlib.sha256(p.read_bytes()).digest() for p in (ROOT / "data").rglob("*") if p.is_file()}
    with tempfile.TemporaryDirectory(prefix="dugyeon-deployment-") as directory:
        temporary = Path(directory)
        env = os.environ.copy()
        env.update(DATABASE_URL="", VERCEL="")
        env.update(SQLITE_PATH=str(temporary / "fixture.sqlite3"), DATABASE_PATH=str(temporary / "fixture.sqlite3"),
                   BACKUP_DIR=str(temporary / "backups"), DJANGO_STATIC_ROOT=str(temporary / "staticfiles"),
                   DJANGO_SECRET_KEY=secrets.token_urlsafe(64), DJANGO_DEBUG="True",
                   DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost", DJANGO_CSRF_TRUSTED_ORIGINS="",
                   REMOTE_OPERATION_APPROVED="False", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")

        def run(*arguments, environment=None):
            print("RUN", " ".join(arguments), flush=True)
            subprocess.run([PYTHON, "-B", "manage.py", *arguments], cwd=ROOT, env=environment or env, check=True)

        run("check")
        run("collectstatic", "--noinput", "--verbosity", "0")
        for name in ["app.css", "phase2.css", "responsive.css", "app.js", "admin/css/base.css"]:
            assert (temporary / "staticfiles" / name).is_file(), name
        run("test", "--verbosity", "1")
        run("migrate", "--noinput", "--verbosity", "0")
        password = secrets.token_urlsafe(32)
        env["VERIFICATION_PASSWORD"] = password
        setup = "import os; from django.contrib.auth import get_user_model; get_user_model().objects.create_user(username='deployment-fixture', password=os.environ['VERIFICATION_PASSWORD'])"
        subprocess.run([PYTHON, "-B", "manage.py", "shell", "-c", setup], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
        run("backup_database")
        assert len(list((temporary / "backups").glob("database-*.sqlite3"))) == 1
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        with (temporary / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([PYTHON, "-B", "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                cookies = http.cookiejar.CookieJar()
                browser = build_opener(HTTPCookieProcessor(cookies))
                for _ in range(100):
                    try:
                        with browser.open(base + "/accounts/login/", timeout=2) as response:
                            assert response.status == 200
                            html = response.read().decode("utf-8")
                        break
                    except OSError:
                        if server.poll() is not None:
                            raise RuntimeError("Fixture server exited; inspect isolated log locally")
                        time.sleep(0.1)
                else:
                    raise TimeoutError("Fixture server did not start")
                protected = ["/", "/tasks/all/", "/records/", "/inbox/", "/categories/", "/settings/", "/settings/trash/", "/settings/backups/", "/exports/tasks.xlsx", "/exports/records.xlsx"]
                for path in protected:
                    with browser.open(base + path) as response:
                        assert "/accounts/login/" in response.url, path
                csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html).group(1)
                payload = urlencode({"username": "deployment-fixture", "password": password, "csrfmiddlewaretoken": csrf}).encode()
                with browser.open(base + "/accounts/login/", payload) as response:
                    assert response.status == 200 and response.url.rstrip("/") == base
                for path in protected:
                    with browser.open(base + path) as response:
                        assert response.status == 200 and "/accounts/login/" not in response.url, path
                for name in ["app.css", "phase2.css", "responsive.css", "app.js"]:
                    with browser.open(base + "/static/" + name) as response:
                        assert response.status == 200
                try:
                    browser.open(base + "/tasks/reorder/", b"task_ids=[]")
                except HTTPError as error:
                    assert error.code == 403
                else:
                    raise AssertionError("POST without CSRF token was accepted")
                print("PASS live HTTP: login, protected URLs, authenticated pages/Excel, static, CSRF", flush=True)
            finally:
                server.terminate()
                server.wait(timeout=10)
        production = env.copy()
        production.update(DJANGO_DEBUG="False", DJANGO_ALLOWED_HOSTS="deployment-check.pythonanywhere.com",
                          DJANGO_CSRF_TRUSTED_ORIGINS="https://deployment-check.pythonanywhere.com", REMOTE_OPERATION_APPROVED="True")
        run("check", "--deploy", environment=production)
        security = """from django.test import Client
from django.conf import settings
c = Client(HTTP_HOST='deployment-check.pythonanywhere.com', HTTP_X_FORWARDED_PROTO='https')
r = c.get('/accounts/login/')
assert r.status_code == 200
assert r.cookies['csrftoken']['secure']
assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
assert Client(HTTP_HOST='deployment-check.pythonanywhere.com').get('/').status_code == 301
assert c.get('/').status_code == 302
print('PASS production HTTPS proxy, cookies and redirects')
"""
        subprocess.run([PYTHON, "-B", "manage.py", "shell", "-c", security], cwd=ROOT, env=production, check=True)
        missing = production.copy()
        missing["DJANGO_SECRET_KEY"] = ""
        result = subprocess.run([PYTHON, "-B", "manage.py", "check"], cwd=ROOT, env=missing, capture_output=True)
        assert result.returncode != 0 and b"DJANGO_SECRET_KEY" in result.stderr
        print("PASS missing production secret refuses startup", flush=True)
    assert all(p.is_file() and hashlib.sha256(p.read_bytes()).digest() == digest for p, digest in originals.items())
    assert set(originals) == {p for p in (ROOT / "data").rglob("*") if p.is_file()}
    print("PASS original database and all existing backup files unchanged", flush=True)


if __name__ == "__main__":
    main()
