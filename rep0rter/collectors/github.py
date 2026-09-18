"""Opt-in public GitHub releases, collaboration issues and explained merged PRs."""
from __future__ import annotations
import re
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote

from ..sources import normalize_text
from ..store import Container, Event
from .state import check_response, persist, read_state

API = 'https://api.github.com'
COLLABORATION_LABELS = {'help wanted', 'good first issue', 'collaboration', 'call for participation', '徵求協作', '協作邀請'}


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else 0


def is_automation(raw, kind):
    actor = raw.get('author' if kind == 'release' else 'user') or {}
    login = str(actor.get('login', '')).casefold()
    labels = {str(label.get('name', '')).casefold() for label in raw.get('labels', []) if isinstance(label, dict)}
    title = raw.get('name') or raw.get('title') or ''
    return bool(actor.get('type') == 'Bot' or login.endswith('[bot]') or
                login in {'dependabot', 'renovate', 'github-actions', 'github-actions[bot]'} or
                labels & {'dependencies', 'dependency', 'automation', 'automated', 'bot', 'ci', 'chore'} or
                re.search(r'(?i)^\w+\((?:ci|deps(?:-dev)?|dependencies|build)\)\s*:|^(?:chore|ci|build)(?:\([^)]*\))?\s*:|^(?:bump|update)\s+\S+\s+(?:from|to)\s+v?\d', title))


def meaningful_impact(body):
    """Accept native-language outcome descriptions, not a heading by itself."""
    plain = normalize_text(body or '', 'markdown')
    if len(plain.strip()) < 40:
        return False
    if re.search(r'(?im)(?:^|\n)\s*#{0,6}\s*(?:impact|outcome|成果|影響|利用者への影響|영향)\s*[:：\n]', body or ''):
        return True
    summary = re.search(r'(?im)^\s*#{1,6}\s*(?:summary|overview|概要|内容|変更内容|요약|주요 변경|변경 사항|무엇이 바뀌었나)(?:\b|[（(\s|])', body or '')
    civic = re.search(r'(?i)dataset|open data|accessibility|citizen|transport|\bbus\b|shelter|election|participatory|地図|避難|市民|公共|福祉|バス|データ|버스|정류장|노선|시민|공공|대피|데이터|산재', plain)
    benefit = re.search(r'(?i)add|enable|allow|improve|export|accessible|provide|prevent|reduce|fix|追加|表示|公開|改善|利用|配布|生成|解決|추가|제공|개선|생성|표시|연결|해결|차단', plain)
    return len(plain) >= 100 and bool(summary and civic and benefit)


def to_event(raw, repo, kind, *, editorial_override=False):
    external = str(raw['id'])
    author = raw.get('author' if kind == 'release' else 'user') or {}
    text = '\n\n'.join(filter(None, [raw.get('name') or raw.get('title') or raw.get('tag_name'), raw.get('body')]))
    labels = {str(label.get('name', '')).lower() for label in raw.get('labels', []) if isinstance(label, dict)}
    body = normalize_text(raw.get('body') or '', 'markdown')
    substantial = len(body.strip()) >= 80
    collaboration_call = len(body.strip()) >= 40 and bool(re.search(r'(?i)help wanted|looking for|contribut|collaborat|volunteer|協作|徵求|招募|参加|協力|募集|기여|모집|참여', body))
    eligible = (kind == 'release' and substantial and not raw.get('draft') and bool(raw.get('published_at')) or
                kind == 'issue' and collaboration_call and bool(labels & COLLABORATION_LABELS) or
                kind == 'pull_request' and bool(raw.get('merged_at')) and meaningful_impact(raw.get('body') or ''))
    automation = is_automation(raw, kind)
    eligible = eligible and (not automation or editorial_override)
    event_id = f'github:{repo.lower()}:{kind}:{external}'
    return Event(id=event_id, source='github', kind=kind, container_id='github:' + repo.lower(),
        ts=timestamp(raw.get('published_at') or raw.get('merged_at') or raw.get('created_at')),
        author_id='github:' + str(author.get('id', '')), author_name=author.get('login', ''),
        text=text, url=raw.get('html_url', ''), reply_count=int(raw.get('comments') or 0),
        reaction_count=int((raw.get('reactions') or {}).get('total_count') or 0),
        meta={'source_instance': 'https://github.com', 'external_id': external, 'visibility': 'public',
              'content_format': 'markdown', 'plain_text': normalize_text(text, 'markdown'),
              'canonical_object_id': event_id, 'updated_at': raw.get('updated_at'), 'observed_at': time.time(),
              'eligible': bool(eligible), 'automation': automation, 'editorial_override': editorial_override, 'avatar_url': author.get('avatar_url', ''), 'source_name': 'GitHub',
              'container_kind': 'repository', 'labels': sorted(labels),
              'engagement': {'github_comments': int(raw.get('comments') or 0), 'github_reactions': int((raw.get('reactions') or {}).get('total_count') or 0)},
              'relations': {}, 'raw_version': 1})


def get(session, path, **params):
    response = session.get(API + path, params=params, timeout=30, headers={
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    check_response(response)
    return response.json()


def collect(store, repos, session, metrics, days=2):
    from ..policy import event_allowed, container_allowed
    total = 0
    for repo in repos:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('GitHub allowlist entries must be owner/repository')
        cid = 'github:' + repo.lower()
        if not container_allowed(store, cid):
            continue
        state = read_state(store, cid)
        now = time.time()
        if now < state.get('retry_at', 0):
            from .registry import record_source_error
            record_source_error(store, cid, 'waiting for upstream rate limit reset')
            continue
        try:
            info = get(session, '/repos/' + repo)
            if info.get('private') is not False or info.get('visibility', 'public') != 'public':
                raise ValueError('GitHub repository is not explicitly public')
            store.upsert_container(Container(id=cid, source='github', name=repo, topic=info.get('description') or '', url=info['html_url']))
            lower = state.get('last_complete_ts', now - days * 86400) - 7200
            events = []
            complete = True
            for endpoint, kind, extra in [('releases', 'release', {}), ('issues', 'issue', {'state': 'all', 'sort': 'updated', 'direction': 'desc', 'since': datetime.fromtimestamp(lower, timezone.utc).isoformat()}), ('pulls', 'pull_request', {'state': 'closed', 'sort': 'updated', 'direction': 'desc'})]:
                finished = False
                for page in range(1, 11):
                    rows = get(session, '/repos/' + repo + '/' + endpoint, per_page=100, page=page, **extra)
                    if not isinstance(rows, list):
                        raise ValueError('invalid GitHub list response')
                    older = False
                    for raw in rows:
                        if kind == 'issue' and raw.get('pull_request'):
                            continue
                        updated = timestamp(raw.get('updated_at') or raw.get('published_at') or raw.get('created_at'))
                        if updated < lower:
                            older = True
                            continue
                        stable_id = f"github:{repo.lower()}:{kind}:{raw['id']}"
                        override = stable_id in {value.strip() for value in os.getenv('REP0RTER_GITHUB_EDITORIAL_OVERRIDES', '').split(',')}
                        if is_automation(raw, kind) and not override:
                            continue
                        event = to_event(raw, repo, kind, editorial_override=override)
                        event.meta.update(bootstrap=not state, recovery=bool(state.get('error')), fetched_at=now)
                        if event_allowed(store, event):
                            events.append(event)
                    # Release endpoint is created-at ordered, so never stop on updated_at.
                    if len(rows) < 100 or (older and kind != 'release'):
                        finished = True
                        break
                complete &= finished
            new_state = dict(state, last_attempt=now, error='' if complete else 'GitHub pagination budget exhausted', last_success=now if complete else state.get('last_success'))
            if complete:
                new_state['last_complete_ts'] = now
            persist(store, cid, new_state, events, metrics, now)
            total += len(events)
            if new_state['error']:
                from .registry import record_source_error
                record_source_error(store, cid, new_state['error'])
        except Exception as exc:
            persist(store, cid, dict(state, last_attempt=now, error=str(exc), retry_at=getattr(exc, 'retry_at', 0)), [], metrics, now)
            # Independent repository failures must not block the remaining allowlist.
            from .registry import record_source_error
            record_source_error(store, cid, exc)
    return total
