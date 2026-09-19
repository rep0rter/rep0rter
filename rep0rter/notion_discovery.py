"""Propose GitHub allowlist candidates from a public Notion portal; never collects.

Notion publishes no read API for public pages, so this reads the undocumented
``<space>.notion.site/api/v3`` endpoints that the published site itself uses.
That is acceptable only because discovery is isolated from publishing: nothing
here is reachable from ``collect``/``report``/``run``, the output is a proposal
for a human to review, and a broken endpoint stops suggestions rather than
changing anything already published.
"""
from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urlsplit

from .collectors.state import RateLimited, check_response

API = 'https://{space}.notion.site/api/v3/{path}'

# Only these property types are ever read. Person, email, phone and file fields
# are never requested: appearing in a community directory is not consent to be
# reported on, so the safest exclusion is to never load the value at all.
#
# Type is a weak guard on its own. A portal's member directory stores handles,
# social accounts and wallet addresses as ordinary title/text/url properties,
# which these types would happily read. What actually keeps the tool away from
# such a database is the single-page scope below, not this set.
READABLE_TYPES = {'title', 'text', 'url', 'select', 'multi_select', 'status', 'date'}

# github.com paths that are not owner/repository pairs.
RESERVED_OWNERS = {'users', 'orgs', 'topics', 'about', 'features', 'sponsors', 'collections',
                   'marketplace', 'settings', 'apps', 'enterprise', 'pricing', 'login', 'join'}

GITHUB_REPO = re.compile(r'https?://(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)')


def parse_portal(url):
    """Accept exactly one https://<space>.notion.site/<slug>-<32 hex> page URL."""
    parsed = urlsplit(url or '')
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            parsed.port not in (None, 443) or not parsed.hostname.endswith('.notion.site')):
        raise ValueError('portal must be an https://<space>.notion.site/<page> URL')
    space = parsed.hostname[: -len('.notion.site')]
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', space):
        raise ValueError('portal must name a single published Notion space')
    match = re.search(r'([0-9a-f]{32})\Z', parsed.path)
    if not match:
        raise ValueError('portal URL must end with the 32 character Notion page id')
    raw = match.group(1)
    return space, f'{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}'


def _post(session, space, path, payload):
    response = session.post(API.format(space=space, path=path.split('?')[0]) +
                            ('?' + path.split('?')[1] if '?' in path else ''),
                            json=payload, timeout=30)
    if response.status_code != 200:
        raise ValueError(f'notion {path} returned HTTP {response.status_code}')
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError(f'notion {path} returned a non-object body')
    return body


def _value(record):
    """Unwrap the doubly nested {value: {value: ...}} records the API returns."""
    value = (record or {}).get('value') or {}
    return value.get('value') or value


def _text(prop):
    if not isinstance(prop, list):
        return ''
    return ''.join(part[0] for part in prop if isinstance(part, list) and part and isinstance(part[0], str))


def public_page_data(session, space, page_id):
    """Refuse anything that is not an explicitly public, undeleted portal.

    ``publicAccessRole`` is the signal that anonymous visitors may read. Note
    that ``isPublicShareLink`` is false for a published site: it marks the
    separate share-link publishing mode, not public readability. Any other role
    is refused rather than guessed, because unknown visibility is not public.
    """
    data = _post(session, space, 'getPublicPageData',
                 {'type': 'block-space', 'name': 'page', 'blockId': page_id,
                  'saveParent': False, 'showMoveTo': False})
    role = data.get('publicAccessRole')
    if data.get('requireLogin') or data.get('isDeleted') or role != 'reader':
        raise ValueError(f'notion portal is not publicly readable (publicAccessRole={role!r})')
    space_id = data.get('spaceId')
    if not isinstance(space_id, str) or not space_id:
        raise ValueError('notion portal did not resolve a space id')
    return {'space_id': space_id, 'space_name': data.get('spaceName') or '',
            'public_access_role': data.get('publicAccessRole')}


def page_collections(session, space, page_id):
    """Databases embedded on this one page; sub-pages are deliberately not followed.

    Crawling the rest of a portal is a bad trade. Measured against the Code for
    Japan portal it reached five further databases and found one additional
    repository, while pulling in a 348-row member profile directory and a
    participation log. The operator names the page that holds project links;
    discovery never wanders from it.
    """
    body = _post(session, space, 'loadCachedPageChunkV2',
                 {'page': {'id': page_id}, 'limit': 200, 'cursor': {'stack': []},
                  'chunkNumber': 0, 'verticalColumns': False})
    record_map = body.get('recordMap') or {}
    blocks = record_map.get('block') or {}
    if not blocks:
        raise ValueError('notion page chunk returned no blocks')
    found = {}
    for record in blocks.values():
        value = _value(record)
        if value.get('type') not in ('collection_view', 'collection_view_page'):
            continue
        pointer = ((value.get('format') or {}).get('collection_pointer') or {}).get('id')
        collection_id, views = value.get('collection_id') or pointer, value.get('view_ids') or []
        if collection_id and views:
            found.setdefault(collection_id, views[0])
    return sorted(found.items())


def collection_rows(session, space, space_id, collection_id, view_id, limit=300):
    """Read one database, resolving opaque property keys by schema name.

    An empty record map alongside a non-zero size hint is this endpoint's known
    silent failure: it answers HTTP 200 with no rows instead of an error. Treat
    it as a failure, never as an empty database.
    """
    body = _post(session, space, 'queryCollection?src=initial_load',
                 {'source': {'type': 'collection', 'id': collection_id, 'spaceId': space_id},
                  'collectionView': {'id': view_id, 'spaceId': space_id},
                  'loader': {'type': 'reducer', 'searchQuery': '', 'userTimeZone': 'UTC',
                             'reducers': {'collection_group_results': {'type': 'results', 'limit': limit}}}})
    record_map = body.get('recordMap') or {}
    blocks = record_map.get('block') or {}
    size_hint = int((body.get('result') or {}).get('sizeHint') or 0)
    if size_hint and not blocks:
        raise ValueError(f'notion queryCollection reported {size_hint} rows but returned none')
    collection = (record_map.get('collection') or {}).get(collection_id)
    if collection is None:
        raise ValueError('notion queryCollection returned no schema for the collection')
    schema = _value(collection).get('schema') or {}
    readable, title_property = {}, ''
    for key, spec in schema.items():
        name, kind = spec.get('name') or '', spec.get('type')
        # A renamed or retyped column must drop out here rather than silently
        # change meaning; a key is never hardcoded.
        if not name or kind not in READABLE_TYPES:
            continue
        readable[key] = name
        if kind == 'title':
            title_property = name
    rows = []
    for record in blocks.values():
        value = _value(record)
        if value.get('type') != 'page' or value.get('parent_id') != collection_id:
            continue
        properties = value.get('properties') or {}
        rows.append({name: _text(properties.get(key)) for key, name in readable.items()})
    return {'name': _text(_value(collection).get('name')), 'rows': rows, 'size_hint': size_hint,
            'title_property': title_property,
            # The endpoint's row count and the rows it returns can disagree, and
            # it never explains why. Report the gap instead of implying a
            # complete read; discovery stays useful, the operator is not misled.
            'complete': len(rows) >= size_hint,
            'skipped_property_types': sorted({spec.get('type') or '' for spec in schema.values()
                                              if spec.get('type') not in READABLE_TYPES})}


def github_repos(collection):
    """Collect owner/repository pairs mentioned in the readable text of each row."""
    found = {}
    for row in collection.get('rows') or []:
        label = row.get(collection.get('title_property') or '', '')
        for text in row.values():
            for owner, repo in GITHUB_REPO.findall(text or ''):
                if owner.lower() in RESERVED_OWNERS:
                    continue
                # Strip the suffix, never rstrip('.git'): that also eats a
                # trailing t, i, g or dot and renames the repository.
                if repo.endswith('.git'):
                    repo = repo[:-4]
                if repo:
                    found.setdefault(f'{owner}/{repo}', label)
    return found


def verify_repo(session, repo):
    """Confirm a candidate against the official GitHub API; links rot.

    Only 404 is a verdict. A throttled or failing request is raised so the
    caller can record it as unresolved: reporting a rate-limited lookup as a
    dead repository would quietly drop real candidates from the proposal.
    """
    headers = {'Accept': 'application/vnd.github+json'}
    # Optional, and the same setting the GitHub collector uses: unauthenticated
    # GitHub allows 60 requests an hour, which verifying a portal of this size
    # exhausts. A token only raises that ceiling.
    token = os.environ.get('REP0RTER_GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    response = session.get('https://api.github.com/repos/' + repo, timeout=30, headers=headers)
    if response.status_code == 404:
        return {'repository': repo, 'reachable': False, 'reason': 'HTTP 404'}
    check_response(response)
    info = response.json()
    return {'repository': info.get('full_name') or repo, 'reachable': True,
            'public': info.get('private') is False and (info.get('visibility') or 'public') == 'public',
            'archived': bool(info.get('archived')), 'pushed_at': info.get('pushed_at') or '',
            'stars': int(info.get('stargazers_count') or 0),
            'description': info.get('description') or ''}


def discover(session, portal, active_days=90, now=None):
    """Read the portal and return reviewed proposals; writes nothing anywhere."""
    now = time.time() if now is None else now
    space, page_id = parse_portal(portal)
    page = public_page_data(session, space, page_id)
    cutoff = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - active_days * 86400))
    report = {'portal': portal, 'space': space, 'space_name': page['space_name'],
              'checked_at': now, 'active_since': cutoff, 'collections': [],
              'incomplete_collections': [], 'candidates': [], 'rejected': [], 'unresolved': []}
    seen = {}
    for collection_id, view_id in page_collections(session, space, page_id):
        collection = collection_rows(session, space, page['space_id'], collection_id, view_id)
        report['collections'].append({'name': collection['name'], 'rows': len(collection['rows']),
                                      'size_hint': collection['size_hint'],
                                      'complete': collection['complete'],
                                      'skipped_property_types': collection['skipped_property_types']})
        if not collection['complete']:
            report['incomplete_collections'].append(collection['name'])
        for repo, label in github_repos(collection).items():
            seen.setdefault(repo, label)
    throttled = None
    for repo in sorted(seen):
        entry = {'repository': repo, 'notion_project': seen[repo]}
        if throttled:
            # Once GitHub throttles, further lookups would only add noise.
            report['unresolved'].append(dict(entry, reason=throttled))
            continue
        try:
            verdict = verify_repo(session, repo)
        except RateLimited as exc:
            throttled = 'github rate limit; retry after ' + time.strftime(
                '%Y-%m-%dT%H:%M:%SZ', time.gmtime(exc.retry_at))
            report['unresolved'].append(dict(entry, reason=throttled))
            continue
        except Exception as exc:
            report['unresolved'].append(dict(entry, reason=f'{type(exc).__name__}: {exc}'[:160]))
            continue
        verdict['notion_project'] = seen[repo]
        if not verdict['reachable']:
            reason = verdict.pop('reason')
        elif not verdict['public']:
            reason = 'not public'
        elif verdict['archived']:
            reason = 'archived'
        elif verdict['pushed_at'] < cutoff:
            reason = 'no push since ' + (verdict['pushed_at'][:10] or 'unknown')
        else:
            report['candidates'].append(verdict)
            continue
        report['rejected'].append(dict(verdict, reason=reason))
    report['candidates'].sort(key=lambda item: item['pushed_at'], reverse=True)
    report['rejected'].sort(key=lambda item: item['repository'])
    report['unresolved'].sort(key=lambda item: item['repository'])
    # Proposals are only trustworthy when every row and every link was resolved.
    report['complete'] = not report['unresolved'] and not report['incomplete_collections']
    return report


def register_commands(sub):
    parser = sub.add_parser(
        'notion-candidates',
        help='propose GitHub allowlist candidates from a public Notion portal; proposals only, never collection')
    parser.add_argument('--portal', required=True, help='https://<space>.notion.site/<page> URL')
    parser.add_argument('--active-days', type=int, default=90,
                        help='treat a repository as active when pushed within this window (default 90)')
    parser.set_defaults(func=cmd_candidates, readonly=True)


def cmd_candidates(cfg, args):
    from .collectors.slack_archive import make_session
    if args.active_days < 1:
        raise ValueError('--active-days must be positive')
    report = discover(make_session(), args.portal, active_days=args.active_days)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Proposals never update REP0RTER_GITHUB_REPOS; a human reviews and edits it.
    return 0 if report['candidates'] and report['complete'] else 1
