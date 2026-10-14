# 외부 회귀검증용 사본

실제 일정 DB, 계정, 세션, 비밀 설정, 백업과 로그는 포함하지 않는다.
원본 소스는 수정하지 않았으며 사본에서만 문서의 개인 경로를 익명화하고,
테스트 비밀번호를 실행 시 난수로 생성하도록 바꿨다. 앱의 환경변수 이름과 CSRF 관련 코드는 유지했다.
`.env.example`과 `.env.vercel.example`에는 빈 항목과 DEBUG=False만 있다.
이 예시만으로 앱이 실행되지 않는 것은 비밀키 누락 차단의 정상 동작이다.

## 준비

압축을 새 빈 폴더에 풀고 Python 3.12 가상환경을 만든다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

## 전체 SQLite 회귀테스트와 HTTP 검증

```powershell
.\.venv\Scripts\python.exe -B scripts\verify_pythonanywhere.py
```

검증 스크립트가 임시 SECRET_KEY와 임시 SQLite를 생성하여 전체 테스트,
collectstatic, 로그인·인증된 URL·Excel·CSRF·HTTPS 설정과 백업을 검증한다.
운영 환경변수의 DATABASE_URL을 사용하지 않도록 분리되어 있다.
실제 DB를 복원하거나 개인 정보를 입력하지 않는다.

브라우저 검증은 Windows, Node.js와 기존 Microsoft Edge가 필요하다.

```powershell
.\.venv\Scripts\python.exe -B scripts\browser_category_audit.py
```

## 실제 PostgreSQL 회귀테스트

테스트 바이너리는 ZIP에 포함하지 않는다. Windows에서 별도로 설치한다.

```powershell
.\.venv\Scripts\python.exe -m pip install --target .deployment-tools\python pgserver==0.1.4
.\.venv\Scripts\python.exe -B scripts\verify_postgres.py
```

임시 PostgreSQL 16.2에 마이그레이션을 적용하고 공통 회귀테스트와 PostgreSQL 백업·복원을 확인한다.
SQLite 파일 전체 복원 전용 테스트는 이 실행에서 제외하며 위 SQLite 전체 실행에서 검증한다.
PostgreSQL 테스트 도구는 Windows 바이너리를 사용한다. 다른 OS에서는 해당 OS용
테스트 PostgreSQL과 동등한 분리 환경을 사용한다. 실제 운영 DB에서 테스트를 실행하지 않는다.

## Vercel 배포

회귀검증 후 DEPLOY_VERCEL.md를 따른다. Vercel 계정과 외부 PostgreSQL은 따로 준비해야 한다.
운영 DATABASE_URL과 SECRET_KEY는 비밀 설정으로 등록하고 기존 개인 DB는 업로드하지 않는다.
`.vercelignore`는 배포에서 테스트·검증 도구를 제외하지만 검토 ZIP에는 테스트가 포함되어 있다.
제공자 자동 백업과 복구 보존 기간은 별도로 설정해야 한다.
ZIP 생성과 로컬 검증은 실제 Vercel 빌드·배포 성공을 의미하지 않는다.
