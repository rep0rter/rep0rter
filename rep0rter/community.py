"""Validation for first-person community stories (no network or AI rewriting)."""
from datetime import date, datetime
from urllib.parse import urlsplit

from .config import TAIPEI
from .hashtags import MAX_TAGS, extract, parse
from .projects import SubmissionError


def validate(form) -> dict:
    values = {key: form.get(key, '').strip() for key in (
        'title', 'description', 'author', 'project_name', 'take_part', 'evidence', 'original', 'event_date', 'hashtags')}
    for key, label, maximum, required in (
        ('title', 'Headline', 160, True), ('description', 'What happened', 3000, True),
        ('author', 'Public display name', 100, True), ('project_name', 'Project name', 160, False),
        ('take_part', 'How to take part', 500, False), ('original', 'Original text', 5000, False),
    ):
        if (required and not values[key]) or len(values[key]) > maximum:
            raise SubmissionError(f'{label} {"is required and " if required else ""}must be at most {maximum} characters.')
    evidence = list(dict.fromkeys(line.strip() for line in values['evidence'].splitlines() if line.strip()))
    if len(evidence) > 5:
        raise SubmissionError('Include at most five evidence links, one per line.')
    for url in evidence:
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username
                     and not parsed.password and parsed.port != 0)
        except ValueError:
            valid = False
        if not valid or len(url) > 2048 or '\\' in url or any(c.isspace() or ord(c) < 32 for c in url):
            raise SubmissionError('Evidence links must be public http:// or https:// URLs without credentials.')
    try:
        values['hashtags'] = parse(values['hashtags'])
        values['hashtags'] = list(dict.fromkeys(values['hashtags'] + extract(
            values['title'] + '\n' + values['description'] + '\n' + values['original'])))[:MAX_TAGS]
    except ValueError as exc:
        raise SubmissionError(str(exc)) from exc
    try:
        day = date.fromisoformat(values['event_date']) if values['event_date'] else datetime.now(TAIPEI).date()
        if day > datetime.now(TAIPEI).date() or day.year < 1970:
            raise ValueError
    except ValueError:
        raise SubmissionError('Choose the date this happened, between 1970 and today.') from None
    if form.get('owner_confirmed') != 'yes':
        raise SubmissionError('Confirm that this is your work or you are authorized to share it publicly.')
    values.update(language='en', evidence=evidence, url=evidence[0] if evidence else '',
                  event_date=day.isoformat(), event_ts=datetime.combine(day, datetime.min.time(), TAIPEI).timestamp())
    return values
