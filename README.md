# PERSONAL DAY LOG · To-do List

일정 체크리스트, 날짜별 글과 사진을 함께 관리하는 Django 웹앱입니다.
PC와 휴대폰의 브라우저에서 사용할 수 있으며, 이 저장소에는 프로그램 소스코드와 테스트만 포함합니다.

## 주요 기능

- 주간 달력과 카테고리별 일정 관리, 중요 일정 별표 표시
- **미정 → X → △ → O** 자동 정렬과 같은 상태 안의 수동 순서 변경
- X·△ 일정의 **다시** 등록: 기존 일정과 이력을 보존하고 다른 날짜에 미정 일정 생성
- 일정 추가·편집 중 입력 내용 보호
- 이날의 기록: 굵게, 밑줄, 글자 크기·색상 지정
- 사진 업로드·분류·삭제 및 사진첩
- Excel 일정 일괄 등록, Excel·CSV 내보내기
- 일정·기록·사진 백업과 주기별 보관 확인 알림
- 로그인 보호, 기억한 기기와 패스키를 이용한 추가 인증 기능

## 구성

- Python 3.12, Django
- Django Templates, CSS, JavaScript
- 로컬 개발: SQLite
- 원격 배포: PostgreSQL, Vercel의 비공개 사진 저장소

## 로컬 실행

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`에서 본인이 생성한 `DJANGO_SECRET_KEY`와 로컬 개발 설정을 지정하세요.
로컬 개발 시 `DJANGO_DEBUG=True`, `DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost`를 사용합니다.
실제 운영 계정이나 운영 데이터베이스 접속 정보를 개발 환경에 복사하지 마세요.

```powershell
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

로그인한 뒤 카테고리를 등록하고 일정을 추가할 수 있습니다.

## 테스트와 배포

- [분리된 테스트 환경 안내](REVIEW_GUIDE.md)
- [Vercel 배포 안내](DEPLOY_VERCEL.md)
- [로컬 운영 안내](docs/deployment-local.md)
- [원격 운영 안내](docs/deployment-approved-remote.md)
- [백업·복원 안내](docs/backup-and-restore.md)

## 공개 범위

실제 일정·일기·사진·계정 데이터, 비밀번호, 인증키, `.env`, 데이터베이스 파일과 백업은 포함하지 않습니다.
`.env.example`과 `.env.vercel.example`은 값이 비어 있는 설정 양식이며, 테스트 코드의 계정과 접속 정보는 분리된 테스트 환경용입니다.
