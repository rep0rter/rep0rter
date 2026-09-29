"""Present source dates without turning day-only anchors into precise times."""
from datetime import date, datetime, timedelta, timezone

from ..config import TAIPEI


def event_date(event) -> date | datetime:
    if event.meta.get('date_precision') != 'day':
        return datetime.fromtimestamp(event.ts, TAIPEI)
    supplied = event.meta.get('source_date')
    if isinstance(supplied, str):
        try:
            return date.fromisoformat(supplied)
        except ValueError:
            pass
    # Existing Code for Japan records preserve a JST midnight anchor but no
    # source_date. Converting that anchor to Taipei would show the previous day.
    zone = timezone(timedelta(hours=9)) if event.meta.get('feed_format') == 'code4japan-json' else TAIPEI
    return datetime.fromtimestamp(event.ts, zone).date()


def event_date_text(event, *, card=False) -> str:
    value = event_date(event)
    pattern = '%Y.%m.%d' if card else '%Y-%m-%d'
    if isinstance(value, datetime):
        pattern += ' · %H:%M UTC+8' if card else ' %H:%M'
    return value.strftime(pattern)
