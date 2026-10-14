# 로컬 개발 및 검증

## 환경

- Python 3.10 이상과 프로젝트 고정 의존성을 설치합니다.
- 운영 데이터가 아닌 더미 데이터만 사용합니다.
- SQLite는 기본 `data/schedule.sqlite3`, 백업은 `data/backups/`에 저장됩니다. 두 경로는 정적 파일 밖입니다.
- 기본 서버와 `runserver` 명령은 loopback만 허용합니다. 외부 네트워크 포트 공개, 포트포워딩, 공개 터널을 사용하지 않습니다.

## 실행 및 확인

README의 로컬 실행 절차대로 마이그레이션, 관리자 생성, 개발 서버 시작을 수행합니다. 시작 전에 다음 검사와 테스트를 실행합니다.

```powershell
python scripts/deployment_check.py
python manage.py check
python manage.py test
```

계정은 `createsuperuser` 명령으로 직접 생성합니다. 저장소에는 기본 비밀번호가 없습니다. 설정에서 카테고리, 계정 비밀번호, 백업과 복구 영역을 관리합니다.
