# 두견 PythonAnywhere 배포

현재 프로젝트: `<PROJECT_ROOT>`, 프로젝트 패키지 `config`, 앱 `tasks`.
로컬 Python 3.12.10 / Django 5.2.11 / SQLite. 실제 기존 DB는 `data/schedule.sqlite3`이다.
아래 `username`은 본인 PythonAnywhere 사용자명으로 바꾼다. 서버 코드 위치는
`/home/username/dugyeon`, 운영 데이터 위치는 `/home/username/schedule-data`로 설명한다.
Web 앱과 서버 가상환경은 같은 Python 버전을 선택한다. 계정에서 Python 3.12 사용 가능 여부를 확인한다.
PythonAnywhere의 Web 앱이 실행하므로 Windows 노트북이나 로컬 runserver를 켜둘 필요가 없다.

## 1. 변경 전 백업과 ZIP

수정 전 파일은 로컬 `change-backups/`에 보존되어 있다. 이 폴더에는 이전 설정의 개발용 키가
포함될 수 있으므로 공개하거나 서버에 업로드하지 않는다.

배포 ZIP에는 `manage.py`, `config/`, `tasks/`(기존 migrations 포함), `templates/`, `static/`,
`requirements.txt`, `.env.example`, `.gitignore`, 이 설명서를 포함한다.
`docs/`, `README.md`는 필요하면 포함한다. `scripts/`와 검증 보고서는 운영 실행에 필요하지 않다.

다음은 제외한다: `.venv/`, 실제 `.env` 및 비밀 설정, `data/` 전체, 모든 SQLite DB와
`-wal`/`-shm`/journal 파일, `backups/`, `change-backups/`, `.deployment-backup-location`,
`__pycache__/`, `*.pyc`, `*.log`, `debug_test_stack.txt`, `staticfiles/`, 개인 데이터 내보내기 ZIP/CSV/XLSX,
`.git/` 및 검증 산출물. `.gitignore`는 ZIP 생성 시 자동 적용되지 않으므로 직접 확인한다.
Git에서 이미 추적 중인 파일은 ignore 추가만으로 추적이 해제되지 않는다. 이번 작업은 파일을 삭제하지 않았다.

## 2. 코드 업로드와 가상환경

Files 탭으로 코드만 담은 ZIP을 `/home/username/`에 업로드하고 Bash 콘솔에서 풀어
`/home/username/dugyeon/manage.py`가 되게 한다. 기존 서버 폴더를 통째로 덮어쓰지 않는다.

```bash
cd /home/username/dugyeon
mkvirtualenv --python=/usr/bin/python3.12 dugyeon
python -m pip install -r requirements.txt
python -m pip check
mkdir -p /home/username/schedule-data/backups
chmod 700 /home/username/schedule-data
```

Windows `.venv`는 서버에서 재사용하지 않는다. gunicorn은 필요하지 않다.

## 3. 운영 .env

`/home/username/dugyeon/.env`를 만든다. 저장소/ZIP에 넣지 않는다.

```dotenv
DJANGO_SECRET_KEY=
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=username.pythonanywhere.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://username.pythonanywhere.com
SQLITE_PATH=/home/username/schedule-data/db.sqlite3
BACKUP_DIR=/home/username/schedule-data/backups
APP_TIME_ZONE=Asia/Seoul
```

SECRET_KEY는 50자 이상의 새 난수로 채운다. 아래 코드는 생성한 값을 출력하지 않고,
비어 있는 항목이 정확히 하나일 때만 채운다. 입력 완료 후 `chmod 600 .env`를 실행한다.

```bash
python - <<'PY'
from pathlib import Path
import secrets
p = Path('.env')
lines = p.read_text(encoding='utf-8').splitlines()
assert lines.count('DJANGO_SECRET_KEY=') == 1, 'Only a single empty key may be initialized'
lines[lines.index('DJANGO_SECRET_KEY=')] = 'DJANGO_SECRET_KEY=' + secrets.token_urlsafe(64)
p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY
chmod 600 .env
```

설정은 `config/settings.py`에서 읽으며 프로세스 환경변수가 .env보다 우선한다.
SECRET_KEY가 없으면 실행되지 않는다. DEBUG=False에서는 짧은 키와 개발용 접두사도 거부한다.
`REMOTE_OPERATION_APPROVED`는 DEBUG=False이면 기본값이 True다. 이전 `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `DATABASE_PATH`도 호환용으로 지원하되 새 이름을 우선한다.
Web과 Bash 모두 같은 .env를 사용한다. 비밀값을 로그나 화면에 출력하지 않는다.

## 4. 빈 운영 DB

기존 로컬 DB를 업로드하지 않는다. 지정 경로에 기존 DB가 없는지 확인한 후 실행한다.
아래 명령은 서버의 Bash 콘솔에서 가상환경을 활성화한 상태로 실행한다.

```bash
workon dugyeon
cd /home/username/dugyeon
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
python manage.py check
python manage.py check --deploy
```

운영 DB와 본인의 새 계정을 생성한다. 기존 마이그레이션을 삭제하거나 다시 만들지 않는다.
기존 로컬 DB, 백업 ZIP, 데이터 dump를 복원하지 않으면 이전 일정과 메모는 옮겨지지 않는다.
공개 회원가입은 없다. 로그인한 모든 계정이 같은 일정을 공유하므로 본인용 계정만 만든다.

기존 개인 DB를 옮길 때는 실제 업무정보 포함 여부를 본인이 확인한다.
DB에는 삭제된 일정, 기록, 계정, 세션, 관리자 이력과 인증 실패 로그도 포함된다.
이전을 선택해도 원본 DB를 직접 복사하지 말고 아래 backup API 스냅샷을 사용한다.
이번 작업은 기존 DB를 자동 복사하지 않는다. 새 SECRET_KEY에서는 다시 로그인해야 할 수 있다.

## 5. Web 탭과 WSGI

Web 탭에서 새 Web 앱을 만들고 Manual configuration과 가상환경과 같은 Python 버전을 선택한다.
Virtualenv는 `/home/username/.virtualenvs/dugyeon`, Source code / Working directory는
`/home/username/dugyeon`으로 지정한다. PythonAnywhere 기본 도메인에는 HTTPS 인증서가 제공된다.

Web 탭의 WSGI 링크를 열어 다음 Django 설정을 적용한다. 프로젝트의
`config/wsgi.py` 변경은 필요 없다. 변경 전 서버 측 WSGI도 백업한다.

```python
import os
import sys

project_path = '/home/username/dugyeon'
if project_path not in sys.path:
    sys.path.insert(0, project_path)
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

정적 파일 설정: URL `/static/`, Directory `/home/username/dugyeon/staticfiles`.
`static/`의 CSS·JS와 Django admin 파일을 collectstatic으로 모은다.
`data/`, `schedule-data/`, 백업, .env를 Static files에 연결하지 않는다.
일반 media 첨부는 없고 백업 ZIP 복원 업로드는 인증된 Django 뷰에서 처리한다.
Web Reload를 누르고 HTTPS로 접속한다. 운영에는 runserver를 사용하지 않는다.

## 6. 배포 후 확인

- 비로그인 상태에서 `/`, `/tasks/all/`, `/records/`, `/settings/backups/`가 로그인으로 이동하는지 확인한다.
- 로그인, 날짜 이동, 캘린더, 체크리스트, 편집, Excel/CSV 다운로드, 로그아웃을 확인한다.
- CSS/JS, 휴대폰 화면, admin CSS, HTTPS 리디렉션과 Secure 쿠키를 확인한다.
- CSRF 토큰 없는 POST가 403이고 로그인 오류가 사용자 존재 여부를 특정하지 않는지 확인한다.
- django-axes 반복 실패 제한을 확인한다. 본인 운영 계정을 불필요하게 잠그지 않는다.
- 백업 명령 성공을 확인한다. 실제 데이터 복원 테스트는 별도 환경에서 수행한다.
- Web 오류 로그는 비밀값이나 메모를 공유하지 않고 확인한다.

HTTPS 인식에는 `HTTP_X_FORWARDED_PROTO=https`를 사용한다.
직접 URL 접근을 포함하여 각 뷰의 login_required와 CSRF 미들웨어를 유지한다.
계정 조건에 Web 앱 갱신이나 유효기간이 있다면 대시보드에서 확인한다.

## 7. 更新

먼저 운영 DB를 백업하고 현재 코드와 .env를 별도로 보관한다.
변경 코드만 업로드하고 .env와 schedule-data는 덮어쓰지 않는다.
가상환경에서 `pip install -r requirements.txt`, `check`, 필요하면 `migrate`,
`collectstatic --noinput`, `check --deploy`를 실행하고 Web Reload한다.
DB 스키마 변경 시 쓰기를 중단하고 작업한다. 이번에는 UI 변경을 하지 않았다.

## 8. DB 백업과 수동 복원

```bash
workon dugyeon
cd /home/username/dugyeon
python manage.py backup_database
```

BACKUP_DIR에 `database-연월일-시분초-마이크로초-식별자.sqlite3`를 만든다.
읽기 전용 연결에서 SQLite backup API로 일관된 스냅샷을 생성하고
integrity_check를 확인한다. 실행 중인 DB 파일을 단순 복사하지 않는다.
이 명령은 오래된 백업을 자동 삭제하지 않는다. 백업에는 인증 데이터도 있으므로 비공개로 보관하고
다른 안전한 위치에도 내려받는다. 화면의 기존 자동 백업에는 오래된 파일 보존 기간이 있다.

수동 복원은 Web 앱과 모든 쓰기 처리를 중단한 후 수행한다.
현재 DB를 먼저 backup_database로 별도 스냅샷에 보존한다.
복원 대상의 프로젝트 스키마와 SQLite integrity_check를 확인한다.
중단 상태에서 완성된 스냅샷을 SQLITE_PATH에 배치하고 기존 DB는
별도 이름으로 보존하여 삭제하지 않는다. 이전 WAL/SHM을 새 DB와 섞지 않고 백업을 덮어쓰지 않는다.
파일 소유자와 접근 권한을 확인하고 migrate/check, Web Reload, 재로그인을 수행한다.
새 자동 복원 처리는 추가하지 않았다. 기존 화면의 ZIP 복원 기능은 유지했다.

## 9. Windows 로컬 실행

이번 작업에서 로컬 .env에 새 비밀키를 출력 없이 생성하고 아래 설정을 저장했다.
DEBUG=True, 로컬 호스트만 허용, SQLITE_PATH=data/schedule.sqlite3, BACKUP_DIR=data/backups.
따라서 기존 실제 DB를 계속 사용한다. .env 미지정 시 DB 기본 경로는 BASE_DIR/db.sqlite3이지만,
키 미설정 시 실행되지 않으므로 .env를 보관하여 다른 DB를 잘못 사용하지 않도록 한다.

```powershell
cd "<PROJECT_ROOT>"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

## 公式資料

- https://help.pythonanywhere.com/pages/DeployExistingDjangoProject/
- https://help.pythonanywhere.com/pages/DjangoStaticFiles/
- https://help.pythonanywhere.com/pages/HTTPSSetup/
- https://pypi.org/project/python-dotenv/
