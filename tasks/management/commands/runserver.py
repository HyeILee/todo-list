import ipaddress

from django.core.management.base import CommandError
from django.contrib.staticfiles.management.commands.runserver import Command as StaticfilesRunserverCommand


class Command(StaticfilesRunserverCommand):
    def handle(self, *args, **options):
        addrport = options.get("addrport") or (args[0] if args else "")
        if not addrport or addrport.isdigit():
            host = "127.0.0.1"
        elif addrport.startswith("[") and "]" in addrport:
            host = addrport[1:addrport.index("]")]
        elif ":" in addrport:
            host = addrport.rpartition(":")[0]
        else:
            host = addrport

        try:
            is_loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise CommandError("개발 서버는 로컬 주소에서만 실행할 수 있습니다. 원격 운영은 기관 승인 인프라를 사용하세요.")
        return super().handle(*args, **options)
