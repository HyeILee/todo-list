# 두견 Vercel 운영 배포

현재 구조는 Django `config.wsgi.application`, 앱 `tasks`, Python 3.12다.
Vercel에서는 외부 PostgreSQL을 사용하고 Windows에서는 기존 SQLite `data/schedule.sqlite3`를 유지한다.
기존 SQLite를 업로드하거나 외부 DB로 자동 복사하지 않는다. 운영 DB는 빈 상태로 시작한다.

## 필요한 계정과 DB

Vercel 계정 연결과 외부 PostgreSQL 연결이 필요하다. 권장 경로는 Vercel Marketplace에서
Neon PostgreSQL을 새 프로젝트에 연결하는 방식이다. 다른 PostgreSQL 제공자도 사용 가능하다.
프로젝트의 사용 요금, DB 지역, 자동 백업 및 복구 보존 기간을 제공자 화면에서 확인한다.
테스트용 DB와 운영 DB는 분리한다. Preview에는 운영 DB를 연결하지 않는다.

앱에는 제공자가 발급한 TLS 지원 pooled URL을 `DATABASE_URL`로 넣는다.
스키마 변경과 복원 작업에는 direct URL을 별도 관리 환경에서 사용한다.
이번 구현은 특정 제공자 계정이나 DB를 자동으로 생성하지 않는다.

## 비밀값과 환경변수

Vercel Project Settings → Environment Variables에 `.env.vercel.example`의 항목을 설정한다.
파일은 예시이며 실제 값은 Vercel 비밀 설정 또는 로컬의 비공개 파일에만 저장한다.
채팅, 저장소, 명령행 인자에 DB URL이나 비밀키를 붙여 넣지 않는다.

```dotenv
DJANGO_SECRET_KEY=
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=your-project.vercel.app
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-project.vercel.app
DATABASE_URL=
DJANGO_BOOTSTRAP_USERNAME=
DJANGO_BOOTSTRAP_PASSWORD=
DATABASE_PROVIDER_BACKUPS_CONFIGURED=False
APP_TIME_ZONE=Asia/Seoul
BLOB_READ_WRITE_TOKEN=
```

`DATABASE_URL` 형식은 `postgresql://USER:PASSWORD@HOST/DB?sslmode=require`다.
제공자에게서 받은 URL을 그대로 쓰되 지원하는 query 항목은 sslmode, sslrootcert, channel_binding이다.
SECRET_KEY는 50자 이상의 무작위 값으로 비공개 생성한다. Windows용 `.env`의 값을 업로드하지 않는다.
허용 호스트와 CSRF origin은 본인 프로젝트의 정확한 도메인으로 지정한다.
Preview 환경을 쓸 경우 해당 배포 호스트와 별도 테스트 DB를 설정한다.
`BLOB_READ_WRITE_TOKEN`은 비공개 Vercel Blob 저장소를 프로젝트에 연결하면 자동으로 생성된다.
공휴일은 로그인이나 API 키 없이 한국천문연구원(KASI) 공개 달력자료에서 배포할 때 동기화한다.
`DATABASE_PROVIDER_BACKUPS_CONFIGURED=True`는 자동 백업과 복구 보존 기간을 실제 확인한 뒤 설정한다.
이 값은 확인 표시이며 제공자 백업을 켜거나 실제 동작 여부를 검사하지 않는다.

Vercel 환경에서는 로컬 `.env`를 읽지 않으며 PostgreSQL URL이 없으면 실행을 거부한다.
DEBUG=True 또는 HTTPS 운영 모드 해제 상태도 거부한다. PostgreSQL 운영 연결에는 TLS를 요구한다.

## 업로드 대상

포함: `manage.py`, `config/`, `tasks/`와 기존 migrations, `templates/`, `static/`,
`requirements.txt`, `.python-version`, `vercel.json`, `.vercelignore`.
`.env.example`와 이 설명서는 비밀값이 없으므로 포함 가능하다.

제외: `.venv/`, `.env`와 실제 운영 설정 파일, 모든 DB 파일, `data/`, 기존 백업과 내보내기,
`change-backups/`, `.deployment-tools/`, `.vercel/`, 로그, 캐시.
`.vercelignore`는 CLI 업로드 제외 설정이다. Git 배포는 저장소에 개인 데이터가 이미 추적 중인지
별도로 확인해야 한다. `.gitignore`를 추가해도 기존 추적 파일은 자동으로 사라지지 않는다.
실제 설정이나 데이터가 들어 있는 프로젝트 ZIP 전체를 그대로 업로드하지 않는다.

## 빈 DB 초기화

첫 배포의 빌드 단계에서 `vercel.json`의 명령이 마이그레이션을 적용하고,
`bootstrap_initial_user`가 일회용 환경변수로 최초 일반 사용자를 만든다.
비밀번호는 Vercel 환경변수 화면에서만 입력하고 소스·채팅·로그에 남기지 않는다.
사용자가 한 명이라도 존재하면 명령은 추가 계정을 만들지 않는다. 첫 로그인과 비밀번호 변경을
확인한 뒤 `DJANGO_BOOTSTRAP_USERNAME`, `DJANGO_BOOTSTRAP_PASSWORD`를 Vercel에서 제거한다.

Vercel의 공식 Django 예제도 PostgreSQL 마이그레이션을 배포 빌드에서 실행하는 방식을 제공한다.
Preview와 Production이 같은 DB를 공유하지 않도록 하고, 향후 위험한 스키마 변경은 별도 관리 환경에서
사전 백업과 점검 후 적용한다.

별도 관리 PC에서 직접 초기화해야 할 때만 아래 절차를 대신 사용한다.

관리 PC에서 프로젝트 `.venv`를 사용한다. 기존 로컬 `.env`는 바꾸지 않고,
별도 `.env.vercel.local`에 운영 설정과 direct DATABASE_URL을 비공개 저장한다.
운영 DB는 처음에 빈 DB인지 확인한다. 다음 코드는 비밀값을 출력하지 않고 해당 파일의 설정을
자식 프로세스에 전달한다. 예제의 최상위 파일 경로는 현재 프로젝트에 맞다.

```powershell
cd "<PROJECT_ROOT>"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```python
# Run with this project's .venv Python, from the project root.
import os
from pathlib import Path
import subprocess
import sys
from dotenv import dotenv_values

environment = os.environ.copy()
environment.update({key: value for key, value in dotenv_values('.env.vercel.local').items() if value is not None})
environment['VERCEL'] = ''  # management PC, not a Vercel function
for arguments in [('check',), ('migrate', '--noinput'), ('createsuperuser',), ('check', '--deploy')]:
    subprocess.run([sys.executable, 'manage.py', *arguments], env=environment, check=True)
```

`createsuperuser`는 대화형 입력을 사용한다. 기존 데이터 dump/fixture를 로드하지 않는다.
로그인한 계정끼리 일정을 공유하는 기존 구조이므로 본인 계정만 만든다.

## 배포

Vercel에서 Django 프로젝트를 연결하거나 Vercel CLI로 배포한다. CLI는 Django 자동 인식을
지원하는 50.38.0 이상을 사용한다. Python은 `.python-version`의 3.12, 의존성은 requirements를 사용한다.
WSGI는 `config/wsgi.py`이며 정적 파일은 `/static/`이다. Vercel의 Django 지원이
`STATIC_ROOT`로 collectstatic을 실행하고 CDN에서 제공하므로 별도 프레임워크 전환이 필요 없다.

처음에는 Preview로 확인하고, 인증·데이터 저장·정적 파일·백업을 확인한 뒤 Production으로 배포한다.
운영의 정확한 호스트와 환경변수 설정을 완료한 후 CLI에서 `vercel --prod`를 사용한다.
`vercel dev`가 없어도 Windows에서는 기존 runserver 명령으로 계속 실행할 수 있다.

## 백업과 복원

PostgreSQL은 SQLite 파일 백업과 방식이 다르다.

1. 전체 DB(계정, 세션, 로그인 제한 기록 포함)는 제공자의 자동 백업과 시점 복구로 보관한다.
   보존 기간과 복구 절차를 실제로 확인하고, 정기적으로 별도 DB로 복구 테스트한다.
   제공자의 백업/PITR가 구성되지 않았다면 전체 DB 자동 백업이 마련된 상태가 아니다.
2. 앱의 백업 화면에서는 비밀번호 재확인 후 일정·기록 ZIP을 다운로드한다.
   카테고리, 보관/삭제된 일정, 상태 이력, 개인 기록과 Excel/CSV, 파일 해시가 포함된다.
   계정·세션·인증 로그·관리 로그와 백업 로그는 이 ZIP에 포함되지 않는다.
   사진은 사진첩의 `ZIP으로 전체 저장`에서 별도로 내려받는다.
3. 관리 PC에서 `backup_database`를 실행하면 동일한 일정 ZIP을 BACKUP_DIR에 저장한다.
   Vercel의 임시 디스크에 영구 백업이 있다고 표시하지 않으며 서버리스에서 해당 명령은 거부한다.
4. 일정 ZIP 복원은 관리 PC의 `restore_data_backup` 명령으로 수행한다.
   제공자 복구 또는 앱 업데이트로 인한 전체 DB 복원과 구분한다.

관리 환경의 `.env.vercel.local`에는 `BACKUP_DIR`도 추가하고 소스코드·static 밖의
비공개 경로로 지정한다. 위 Python 실행 방식에서 명령 목록을 다음으로 바꿔 실행할 수 있다.

```python
# Back up schedule data only; use the management environment configured above.
subprocess.run([sys.executable, 'manage.py', 'backup_database'], env=environment, check=True)

# Run only AFTER disabling web app writes and stopping all other writers.
subprocess.run([
    sys.executable, 'manage.py', 'restore_data_backup', '/private/path/backup.zip',
    '--confirm', '--confirm-writes-stopped',
], env=environment, check=True)
```

복원 명령은 형식, SHA-256, 스키마, 모델 목록, 필드 길이와 관계를 검증하고,
현재 일정·기록 ZIP을 로컬 BACKUP_DIR에 먼저 저장한다. 안전 백업을 만들지 못하면 복원을 중단한다.
복원은 트랜잭션으로 처리하며 실패하면 되돌린다. 기존 계정은 유지한다.
Vercel 웹 요청의 복원 업로드는 제한하여 동시 쓰기와 영구 사전 백업 문제를 피한다.
복원이 끝나면 앱 쓰기를 다시 열고 일정·기록과 로그인을 확인한다.
SHA-256는 손상 검사용이며 파일의 신뢰성을 인증하는 서명은 아니므로 본인이 보관한 백업만 사용한다.
구형 SQLite 전체 백업 ZIP을 PostgreSQL 복원 명령에 넣지 않는다.
SQLite 로컬 환경에서는 기존 자동 파일 백업과 웹 ZIP 복원을 그대로 유지한다.

## 배포 후 확인 및 업데이트

- 비로그인 접근 차단, 로그인/로그아웃과 일반적인 오류 문구, 공개 회원가입이 없는지 확인한다.
- 휴대폰에서 캘린더, 일정 생성/수정, 체크리스트, 개인 기록, Excel 내보내기를 확인한다.
- 새로고침·재배포 후에도 DB 데이터가 유지되는지 테스트용 항목으로 확인한다.
- CSRF, Secure 쿠키, HTTPS 리디렉션, 반복 로그인 실패 제한을 확인한다.
- 백업 ZIP 다운로드와 제공자 백업 설정을 확인한다. 실제 운영 DB에 복원 테스트를 하지 않는다.
- 업데이트 전 백업하고 DB 스키마 변경은 관리 환경에서 migrate한 뒤 코드를 배포한다.
- Vercel 빌드와 런타임 로그에 비밀값이나 일정 내용을 출력하지 않는다.

## 로컬 회귀 검증

```powershell
.\.venv\Scripts\python.exe -B scripts\verify_pythonanywhere.py
.\.venv\Scripts\python.exe -B scripts\browser_category_audit.py
# PostgreSQL test binaries only, excluded from deployment:
.\.venv\Scripts\python.exe -m pip install --target .deployment-tools\python pgserver==0.1.4
.\.venv\Scripts\python.exe -B scripts\verify_postgres.py
```

검증은 임시 DB만 사용한다. PostgreSQL 검증용 서버는 16.2이며 운영 서버가 아니다.
실제 제공자 연결의 TLS와 Vercel 빌드·런타임은 계정 연결 후 별도로 확인해야 한다.

공식 자료: [Django 배포](https://vercel.com/docs/frameworks/full-stack/django),
[Python 런타임](https://vercel.com/docs/functions/runtimes/python),
[SQLite 제한](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel).
