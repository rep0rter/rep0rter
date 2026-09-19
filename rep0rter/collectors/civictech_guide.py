"""One bounded public Field Guide directory snapshot, never a news feed.

API and attribution: https://civictech.guide/build (CC BY 4.0).
Directory dates describe listing provenance, not project launch dates.
"""
from datetime import datetime, timezone
import json
import re
from uuid import UUID

from ..sources import normalize_text
from .feed_common import iso_date, make_event, public_url

URL = 'https://civictech.guide/'
NAME = 'Civic Tech Field Guide'
SNAPSHOT_LIMIT = 100
ENDPOINT = URL + 'api/v1/projects/search?status=Active&sort=newest&limit=100'


def _text(row, key, required=False):
    value = row.get(key)
    if value is None and not required:
        return ''
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f'Field Guide requires a valid {key}')
    return value.strip()


def _strings(row, key):
    values = row.get(key, [])
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError(f'Malformed Field Guide {key}')
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def _date(row):
    added = _text(row, 'added')
    if added:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', added):
            raise ValueError('Field Guide added must be a calendar date')
        # A UTC anchor makes day-only records sortable; the source declares no
        # time or timezone. Do not present this anchor as a publication instant.
        stamp = datetime.strptime(added, '%Y-%m-%d').replace(tzinfo=timezone.utc)
        return stamp.timestamp(), {
            'timestamp_basis': 'directory_added', 'source_date': added,
            'date_precision': 'day', 'date_timezone': 'unspecified',
            'timestamp_anchor': 'UTC midnight for day-only directory date',
        }
    created = _text(row, 'created_at', required=True)
    stamp = iso_date(created)
    fraction = re.search(r'\.(\d+)', created)
    offset = datetime.fromisoformat(created.replace('Z', '+00:00')).strftime('%z')
    return stamp, {
        'timestamp_basis': 'database_created', 'source_date': created,
        'date_precision': 'fractional_second' if fraction else 'second',
        'date_fractional_digits': len(fraction[1]) if fraction else 0,
        'date_timezone': offset,
    }


def parse(content, feed_url):
    """Validate the whole snapshot before returning any rows; never paginate."""
    if feed_url.rstrip('/') != URL.rstrip('/'):
        raise ValueError('Unexpected Field Guide source URL')
    payload = json.loads(content)
    if not isinstance(payload, dict) or not isinstance(payload.get('meta'), dict):
        raise ValueError('Malformed Field Guide response')
    rows, total = payload.get('data'), payload['meta'].get('total')
    if (not isinstance(rows, list) or type(total) is not int or total < 0
            or len(rows) > SNAPSHOT_LIMIT or total < len(rows)):
        raise ValueError('Malformed Field Guide data or total')
    if total and not rows:
        raise ValueError('Field Guide returned an empty nonempty directory snapshot')
    unique, events = {}, []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Malformed Field Guide record')
        external_id = str(UUID(_text(row, 'id', required=True)))
        if external_id in unique:
            if row != unique[external_id]:
                raise ValueError('Conflicting duplicate Field Guide UUID')
            continue
        unique[external_id] = row
        slug = _text(row, 'slug', required=True)
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', slug):
            raise ValueError('Malformed Field Guide listing slug')
        title = normalize_text(_text(row, 'title', required=True), 'html')
        if not title:
            raise ValueError('Empty Field Guide title')
        published, dates = _date(row)
        status = _text(row, 'status_raw', required=True).casefold()
        if status not in ('active', 'inactive', 'n/a'):
            raise ValueError('Malformed Field Guide status')
        extra = {
            **dates, 'eligible': False, 'snapshot_limit': SNAPSHOT_LIMIT,
            'snapshot_total': total, 'history_complete': False,
            'api_url': ENDPOINT, 'attribution': NAME, 'license': 'CC BY 4.0',
            'categories': _strings(row, 'categories'), 'tags': _strings(row, 'tags'),
            'project_types': _strings(row, 'projectTypes'),
            'organization_types': _strings(row, 'organizationType'),
            'directory_status': status,
        }
        for field, key in [('url', 'project_url'), ('repository_url', 'repository_url')]:
            value = _text(row, field)
            if value:
                extra[key] = public_url(value)
        modified = _text(row, 'lastModified')
        if modified:
            iso_date(modified)
            extra['directory_last_modified'] = modified
        location = row.get('location')
        if location is not None:
            if not isinstance(location, dict):
                raise ValueError('Malformed Field Guide location')
            extra['location'] = {k: _text(location, k) for k in ('city', 'country')
                                 if location.get(k)}
        parts = [normalize_text(_text(row, key), 'html')
                 for key in ('description', 'longDescription')]
        body = '\n\n'.join(dict.fromkeys(part for part in parts if part))
        # Defend against unexpected filter changes; do not ingest inactive or
        # unknown-status listings as active projects.
        if status != 'active':
            continue
        event = make_event(
            feed_url=feed_url, source_name=NAME, external_id=external_id,
            url=URL + 'projects/' + slug, published=published,
            title=title, body=body, content_scope='directory_entry',
            feed_format='civictech-guide-json', extra=extra)
        event.kind = 'directory_entry'
        events.append(event)
    return NAME, events
