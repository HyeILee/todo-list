import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.core.checks import run_checks


def main():
    failures = []
    database_path = Path(settings.DATABASES["default"]["NAME"]).resolve()
    backup_path = Path(settings.BACKUP_DIR).resolve()
    static_paths = [Path(settings.BASE_DIR / "static").resolve()]
    if getattr(settings, "STATIC_ROOT", None):
        static_paths.append(Path(settings.STATIC_ROOT).resolve())

    for protected_path, label in ((database_path, "DATABASE_PATH"), (backup_path, "BACKUP_DIR")):
        if any(protected_path == static or static in protected_path.parents for static in static_paths):
            failures.append(f"{label}는 정적 파일 디렉터리 밖이어야 합니다.")
    if not settings.REMOTE_OPERATION_APPROVED:
        if settings.DEBUG is not True:
            failures.append("로컬 개발 모드는 DJANGO_DEBUG=true여야 합니다.")
        if any(host not in settings.LOCAL_HOSTS for host in settings.ALLOWED_HOSTS):
            failures.append("승인되지 않은 원격 ALLOWED_HOSTS가 설정되었습니다.")
        print("배포 모드: 로컬 전용. 실제 내부정보를 입력하거나 네트워크에 공개하지 마세요.")
    else:
        if settings.DEBUG:
            failures.append("원격 운영은 DEBUG=false여야 합니다.")
        if not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
            failures.append("원격 운영 쿠키에 Secure 속성이 필요합니다.")
        if not settings.SECURE_SSL_REDIRECT or not settings.CSRF_TRUSTED_ORIGINS:
            failures.append("원격 운영 HTTPS 설정을 확인하세요.")
        if len(settings.SECRET_KEY) < 50:
            failures.append("운영 SECRET_KEY는 충분한 길이의 임의 비밀값이어야 합니다.")
        print("배포 모드: 승인된 원격 운영 설정")

    for check in run_checks(include_deployment_checks=settings.REMOTE_OPERATION_APPROVED):
        if check.level >= 40:
            failures.append(f"Django 검사 {check.id}: {check.msg}")

    if failures:
        for failure in failures:
            print(f"실패: {failure}", file=sys.stderr)
        return 1
    print("배포 설정 검사 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
