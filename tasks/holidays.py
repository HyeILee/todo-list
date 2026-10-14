import re
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.request import Request, urlopen


KASI_CALENDAR_URL = "https://astro.kasi.re.kr/kor/life/post/calendarData"


class HolidaySyncError(Exception):
    pass


class _TableParser(HTMLParser):
    """Collect table rows without depending on KASI's presentation classes."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def _expand_dates(year, raw_date):
    date_text = re.sub(r"\([^)]*\)", "", raw_date).strip()
    if date_text.startswith("*"):
        return []
    matches = [(int(month), int(day)) for month, day in re.findall(r"(\d{1,2})월\s*(\d{1,2})일", date_text)]
    if not matches:
        return []
    start = date(year, *matches[0])
    if len(matches) == 1:
        return [start]
    end = date(year, *matches[-1])
    if end < start or (end - start).days > 10:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def parse_kasi_holidays(html, year):
    parser = _TableParser()
    parser.feed(html)
    holiday_table = next(
        (
            table
            for table in parser.tables
            if table and len(table[0]) == 4 and table[0] == ["명칭", "날짜(요일)", "명칭", "날짜(요일)"]
        ),
        None,
    )
    if holiday_table is None:
        raise HolidaySyncError(f"{year}년 KASI 페이지에서 공휴일 표를 찾지 못했습니다.")

    holidays = []
    for row in holiday_table[1:]:
        for index in (0, 2):
            if len(row) <= index + 1:
                continue
            raw_name, raw_date = row[index], row[index + 1]
            name = raw_name.replace("*", "").strip()
            if not name:
                continue
            for holiday_date in _expand_dates(year, raw_date):
                holidays.append((holiday_date, name))
    return sorted(set(holidays))


def fetch_official_holidays(year, timeout=20):
    url = f"{KASI_CALENDAR_URL}?{urlencode({'search_year': str(year)})}"
    request = Request(url, headers={"User-Agent": "To-do-List/1.0 (+holiday-calendar-sync)"})
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            html = response.read().decode(charset, errors="replace")
        return parse_kasi_holidays(html, year)
    except HolidaySyncError:
        raise
    except Exception as error:
        raise HolidaySyncError(f"{year}년 KASI 달력자료를 불러오지 못했습니다.") from error
