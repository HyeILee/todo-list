from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from tasks.holidays import HolidaySyncError, fetch_official_holidays
from tasks.models import Holiday


class Command(BaseCommand):
    help = "로그인이 필요 없는 한국천문연구원 달력자료에서 공휴일을 동기화합니다."

    def add_arguments(self, parser):
        parser.add_argument("--year", action="append", type=int, dest="years")
        parser.add_argument("--skip-on-error", action="store_true")

    def handle(self, *args, **options):
        current_year = timezone.localdate().year
        years = sorted(set(options["years"] or [current_year, current_year + 1]))
        for year in years:
            try:
                holidays = fetch_official_holidays(year)
            except HolidaySyncError as error:
                if options["skip_on_error"]:
                    self.stdout.write(self.style.WARNING(str(error)))
                    continue
                raise CommandError(str(error)) from error
            if not holidays:
                if options["skip_on_error"]:
                    self.stdout.write(self.style.WARNING(f"{year}년 공휴일 응답이 비어 있어 기존 정보를 유지합니다."))
                    continue
                raise CommandError(f"{year}년 공휴일 응답이 비어 있어 기존 정보를 유지합니다.")
            with transaction.atomic():
                received_dates = []
                for holiday_date, name in holidays:
                    Holiday.objects.update_or_create(
                        holiday_date=holiday_date,
                        defaults={"name": name, "source": "KASI_CALENDAR"},
                    )
                    received_dates.append(holiday_date)
                Holiday.objects.filter(holiday_date__year=year, source="KASI_CALENDAR").exclude(holiday_date__in=received_dates).delete()
            self.stdout.write(self.style.SUCCESS(f"{year}년 공휴일 {len(holidays)}건을 동기화했습니다."))
