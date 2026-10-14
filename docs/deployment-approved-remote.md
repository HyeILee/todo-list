# 승인된 원격 운영

원격 사용은 기능 설정만으로 승인되지 않습니다. 기관 보안 담당자가 앱 사용, 저장 정보 등급, 서버 위치, 회사 PC/휴대폰 접근, 백업 위치와 보존, 로그, 사고 보고 절차를 승인한 뒤에만 진행합니다. 승인이 없다면 로컬 전용으로 유지하고 실제 내부정보를 입력하지 않습니다.

## 구조

기관 승인 HTTPS 프록시 → 단일 Django WSGI 서비스/인스턴스 → 영속 SQLite 파일 및 기관 승인 백업 저장소. 브라우저에서 서버 프로그램을 설치하지 않습니다. SQLite 쓰기 서버는 한 대로 제한하며 다중 서버/서버리스로 운영하지 않습니다.

## 필수 환경

- `REMOTE_OPERATION_APPROVED=true`
- `DJANGO_DEBUG=false`
- 비밀 저장소가 제공한 `DJANGO_SECRET_KEY`
- 와일드카드가 아닌 명시적 `ALLOWED_HOSTS`
- HTTPS만 포함한 `CSRF_TRUSTED_ORIGINS`
- 영속·암호화된 `DATABASE_PATH`와 기관 승인 `BACKUP_DIR`
- HTTPS 프록시는 `X-Forwarded-Proto`를 직접 설정하고 외부 입력값을 전달하지 않아야 합니다.
- 프록시 접근 제어, 인증 정책, 로그/백업 보존을 기관 표준에 맞춥니다.

운영 명령이나 컨테이너 포트 공개 예시는 제공하지 않습니다. 기관이 승인한 런타임 절차에 따라 WSGI 서비스를 구성하고, 시작 전에 `python scripts/deployment_check.py` 및 `python manage.py check --deploy`를 통과시킵니다.

무료 터널, 개인용 클라우드, 무인증 공개 URL, 우회 포트포워딩은 사용하지 않습니다.
