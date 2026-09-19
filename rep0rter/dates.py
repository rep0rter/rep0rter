"""Shared calendar-date grounding for the writer and translation backfill."""
import re


def calendar_dates(text: str) -> set[tuple[int | None, int, int]]:
    """Recognize common four-locale date forms and same-month range endpoints.

    This mechanical guard cannot verify which activity a date describes or parse
    every natural-language date form. Years and relative phrases are not dates.
    """
    found = set()
    def add_date(year, month, day, end):
        found.add((year, month, day))
        # A same-month range endpoint shares the preceding date's year/month.
        # Fully written endpoints are parsed independently by the patterns below.
        endpoint = re.match(r"\s*[-–—~〜至到]\s*(\d{1,2})(?:日|일)?(?![\d/-])", text[end:])
        if endpoint:
            found.add((year, month, int(endpoint.group(1))))
    for match in re.finditer(r"(?<!\d)(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?!\d)", text):
        add_date(*map(int, match.groups()), match.end())
    for match in re.finditer(r"(?:(\d{4})[年년]\s*)?(\d{1,2})\s*[月월]\s*(\d{1,2})\s*[日일]?", text):
        year, month, day = match.groups()
        add_date(int(year) if year else None, int(month), int(day), match.end())
    for match in re.finditer(r"(?<![\d/-])(\d{1,2})/(\d{1,2})(?![\d/])", text):
        month, day = map(int, match.groups())
        add_date(None, month, day, match.end())
    months = {name: number for number, names in enumerate([
        ('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'), ('may',),
        ('jun', 'june'), ('jul', 'july'), ('aug', 'august'), ('sep', 'sept', 'september'),
        ('oct', 'october'), ('nov', 'november'), ('dec', 'december')], 1) for name in names}
    names = '|'.join(sorted(months, key=len, reverse=True))
    for pattern, order in [
        (rf"\b({names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", 'month_first'),
        (rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({names})\.?(?:\s+(\d{{4}}))?\b", 'day_first'),
    ]:
        for match in re.finditer(pattern, text, re.I):
            first, second, year = match.groups()
            month, day = (months[first.lower()], int(second)) if order == 'month_first' else (months[second.lower()], int(first))
            add_date(int(year) if year else None, month, day, match.end())
    return found


def has_unsupported_date(text: str, source_dates: set) -> bool:
    """Check calendar dates while allowing a year omitted in either text."""
    return any(
        not any((month, day) == (source_month, source_day)
                and (year is None or source_year is None or year == source_year)
                for source_year, source_month, source_day in source_dates)
        for year, month, day in calendar_dates(text)
    )
