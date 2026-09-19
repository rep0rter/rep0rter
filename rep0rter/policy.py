"""Stable-ID exclusions and irreversible redaction, also kept outside DB backups."""
from __future__ import annotations

import json
from .runtime import locks as fcntl, services
import os
import tempfile

import time
import shutil
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_rules (
 scope TEXT NOT NULL, subject TEXT NOT NULL, requested_at REAL NOT NULL,
 effective_at REAL NOT NULL, removed_at REAL, reason TEXT DEFAULT '', version INTEGER NOT NULL,
 PRIMARY KEY(scope,subject));
CREATE TABLE IF NOT EXISTS event_tombstones (
 event_id TEXT PRIMARY KEY, redacted_at REAL NOT NULL, reason TEXT DEFAULT 'withdrawn');
CREATE TABLE IF NOT EXISTS retractions (
 post_id INTEGER PRIMARY KEY, requested_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 error TEXT, completed_at REAL);
"""
SCOPES = ('user', 'container', 'event')


def policy_path(store):
    return store.path.parent / 'exclusions.json'


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _import_external(store):
    path = policy_path(store)
    if path.exists():
        data = json.loads(path.read_text())
        if data.get('schema') != 1 or not isinstance(data.get('rules'), list) or not isinstance(data.get('tombstones'), list):
            raise ValueError('Invalid exclusions policy; refusing to publish')
        with store.conn:
            for rule in data['rules']:
                if rule['scope'] not in SCOPES:
                    raise ValueError('Invalid exclusion scope')
                store.conn.execute('''INSERT INTO policy_rules VALUES(:scope,:subject,:requested_at,:effective_at,:removed_at,:reason,:version)
                  ON CONFLICT(scope,subject) DO UPDATE SET removed_at=excluded.removed_at,
                  reason=excluded.reason,version=excluded.version,requested_at=excluded.requested_at,
                  effective_at=excluded.effective_at WHERE excluded.version>=policy_rules.version''', rule)
            for item in data['tombstones']:
                store.conn.execute('INSERT OR IGNORE INTO event_tombstones VALUES(:event_id,:redacted_at,:reason)', item)


def initialize(store):
    store.conn.executescript(SCHEMA)
    _import_external(store)
    # Union with the external ledger: restoring an older DB cannot revive content.
    scrubbed = False
    withdrawn_urls = {row[0] for row in store.conn.execute('SELECT url FROM events JOIN event_tombstones ON events.id=event_tombstones.event_id') if row[0]}
    with store.conn:
        for row in store.conn.execute('SELECT event_id FROM event_tombstones').fetchall():
            scrubbed = _scrub(store, row['event_id']) or scrubbed
    if scrubbed:
        _purge_media(store, withdrawn_urls)
    save(store)


def _save_locked(store):
    path = policy_path(store)
    previous = json.loads(path.read_text()) if path.exists() else {'rules': [], 'tombstones': []}
    rules = {(r['scope'],r['subject']): r for r in previous['rules']}
    for row in store.conn.execute('SELECT * FROM policy_rules ORDER BY scope,subject'):
        rule = dict(row); key = (rule['scope'],rule['subject'])
        if key not in rules or rule['version'] >= rules[key]['version']:
            rules[key] = rule
    tombstones = {r['event_id']:r for r in previous['tombstones']}
    tombstones.update({r['event_id']:dict(r) for r in store.conn.execute('SELECT * FROM event_tombstones')})
    _atomic_json(path, {'schema':1,'rules':list(rules.values()),'tombstones':list(tombstones.values())})
    if services.get() is not None:
        services.get().persist_policy(path)


def save(store):
    with policy_path(store).with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _save_locked(store)


def _excluded(store, scope, subject, occurred_at=None):
    row = store.conn.execute('SELECT removed_at FROM policy_rules WHERE scope=? AND subject=?', (scope,subject)).fetchone()
    return bool(row and (row['removed_at'] is None or occurred_at is None or occurred_at <= row['removed_at']))


def container_allowed(store, container_id):
    row = store.conn.execute("SELECT removed_at FROM policy_rules WHERE scope='container' AND subject=?", (container_id,)).fetchone()
    return not row or row['removed_at'] is not None


def user_allowed(store, user_id):
    return not _excluded(store, 'user', user_id)


def _user_id(source, author):
    if not author:
        return ''
    return author if author.startswith(source + ':') or '://' in author else source + ':' + author


def event_allowed(store, event, _visited=None):
    from .sources import automated
    if automated(event):
        return False
    visited = set() if _visited is None else _visited
    if event.id in visited:
        return True
    visited.add(event.id)
    if store.conn.execute('SELECT 1 FROM event_tombstones WHERE event_id=?', (event.id,)).fetchone():
        return False
    if (_excluded(store,'event',event.id,event.ts) or _excluded(store,'container',event.container_id,event.ts)
            or _excluded(store,'user',_user_id(event.source,event.author_id),event.ts)):
        return False
    if event.meta.get('deleted_at') or event.meta.get('content_status') == 'deleted':
        return False
    if event.meta.get('visibility', 'public' if event.source == 'slack' else 'unknown') != 'public':
        return False
    if event.meta.get('withheld_reference_count', 0):
        return False
    refs = list(event.meta.get('references', []))
    if event.parent_id:
        parent = store.get_event(event.parent_id)
        if parent and not event_allowed(store,parent,visited):
            return False
        if store.conn.execute('SELECT 1 FROM event_tombstones WHERE event_id=?',(event.parent_id,)).fetchone():
            return False
    for ref in refs:
        if not isinstance(ref,dict) or ref.get('public') is not True:
            return False
        ref_id = ref.get('event_id') or ''
        ref_container = ref.get('container_id') or ''
        ref_author = _user_id(event.source,ref.get('author_id') or '')
        if not ref_id or not ref_container:
            return False
        original = store.get_event(ref_id)
        if not ref_author and original is None:
            return False
        reference_at = original.ts if original else ref.get('ts', event.ts)
        try:
            reference_at = float(reference_at)
        except (ValueError, TypeError):
            return False
        if (_excluded(store,'event',ref_id,reference_at) or _excluded(store,'container',ref_container,reference_at)
                or (ref_author and _excluded(store,'user',ref_author,reference_at))):
            return False
        if ref_id:
            if store.conn.execute('SELECT 1 FROM event_tombstones WHERE event_id=?',(ref_id,)).fetchone():
                return False
            original = store.get_event(ref_id)
            if original and not event_allowed(store,original,visited):
                return False
    return True


def add_rule(store, scope, subject, reason='', now=None):
    if scope not in SCOPES or not subject.strip() or ':' not in subject:
        raise ValueError('Use a scope and a stable source-qualified ID')
    now = time.time() if now is None else now
    with policy_path(store).with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _import_external(store)
        version = store.conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM policy_rules').fetchone()[0]
        with store.conn:
            store.conn.execute('''INSERT INTO policy_rules VALUES(?,?,?,?,NULL,?,?)
              ON CONFLICT(scope,subject) DO UPDATE SET removed_at=NULL,reason=excluded.reason,version=excluded.version,
              requested_at=excluded.requested_at,effective_at=excluded.effective_at''',
              (scope,subject,now,now,reason,version))
        _save_locked(store)


def remove_rule(store, scope, subject, now=None):
    if scope not in SCOPES or not subject or ':' not in subject:
        raise ValueError('Use a scope and a stable source-qualified ID')
    now = time.time() if now is None else now
    with policy_path(store).with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _import_external(store)
        version = store.conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM policy_rules').fetchone()[0]
        with store.conn:
            cursor = store.conn.execute('UPDATE policy_rules SET removed_at=?,version=? WHERE scope=? AND subject=?',
                                        (now,version,scope,subject))
            if not cursor.rowcount:
                raise ValueError('Exclusion does not exist')
        _save_locked(store)


def affected(store):
    result=[]
    for row in store.conn.execute('SELECT * FROM events').fetchall():
        event=store._row_to_event(row)
        if not event_allowed(store,event):
            result.append(event.id)
    # Writer/editorial evidence may include a reply quoted only in a snapshot.
    related = _audit_dependents(store, set(result))
    return sorted(set(result) | related)


def _contains_id(value, ids):
    if isinstance(value, str):
        return value in ids
    if isinstance(value, dict):
        return any(_contains_id(item, ids) for item in value.values())
    if isinstance(value, list):
        return any(_contains_id(item, ids) for item in value)
    return False


def _audit_rows(store, ids):
    if not ids:
        return
    tables={r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('editorial_decisions', 'writer_audits'):
        if table not in tables:
            continue
        columns={r[1] for r in store.conn.execute(f'PRAGMA table_info({table})')}
        if 'event_id' not in columns:
            continue
        for row in store.conn.execute(f'SELECT rowid AS policy_rowid, * FROM {table}').fetchall():
            matched = row['event_id'] in ids
            for column in ('snapshot', 'evidence_snapshot', 'evidence_ids'):
                if column in columns and row[column]:
                    try:
                        matched = matched or _contains_id(json.loads(row[column]), ids)
                    except (ValueError, TypeError):
                        # Malformed evidence cannot establish that private data is absent.
                        matched = True
            if matched:
                yield table, row['policy_rowid'], row['event_id']


def _audit_dependents(store, ids):
    result = set(ids)
    while True:
        related = {event_id for _, _, event_id in _audit_rows(store, result)} - result
        if not related:
            return result
        result.update(related)


def _atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', encoding='utf-8', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _withdrawn_page(language):
    import html
    from .i18n import COPY, LANGUAGES, page_name
    copy = COPY[language]
    links = ' '.join(f'<a href="{page_name(code)}" lang="{code}"' +
                     (' aria-current="page"' if code == language else '') +
                     f'>{html.escape(label)}</a>'
                     for code, label in LANGUAGES.items())
    return (f'<!DOCTYPE html><html lang="{language}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex"><meta name="color-scheme" content="light dark"><title>' + html.escape(copy['withdrawn']) +
            '</title><script src="../../theme.js"></script><link rel="stylesheet" href="../../style.css">'
            '</head><body class="withdrawal-page"><main class="withdrawal-card"><h1>' + html.escape(copy['withdrawn']) + '</h1><p>' +
            html.escape(copy['withdrawal_notice']) + '</p><a class="button button-primary" href="../../' + page_name(language) +
            '">' + html.escape(copy['back_home']) + '</a><nav class="languages" aria-label="' + html.escape(copy['language']) +
            '">' + links + '</nav></main></body></html>')


def _sanitize_site(store, root, withdrawn_urls=()):
    """Fail closed for withdrawal, even if a later complete rebuild fails.

    Public artifacts are derived caches. Remove only withdrawn articles/items;
    preserve surviving article content and link targets. Corrupt feeds cannot be
    verified and become an empty feed pending the next successful full rebuild.
    """
    from bs4 import BeautifulSoup
    import xml.etree.ElementTree as ET
    from .i18n import DEFAULT_LANGUAGE, LANGUAGES, page_aliases, page_name
    rows = store.conn.execute('SELECT p.id,p.event_id FROM posts p JOIN retractions r ON p.id=r.post_id').fetchall()
    withdrawn = {str(row['id']) for row in rows}
    guids = withdrawn | {row['event_id'] for row in rows}
    if not withdrawn:
        return
    withdrawn_urls = set(withdrawn_urls)
    # Recover source links from cached withdrawn articles before removing them;
    # this also supports replay after a partially completed prior sanitization.
    for path in root.rglob('*.html'):
        cached = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
        for article in cached.find_all('article'):
            if str(article.get('id')) in withdrawn:
                withdrawn_urls.update(link['href'] for link in article.select('p.meta a[rel~=noopener][href]'))
    for path in root.rglob('*.html'):
        relative = path.relative_to(root)
        if len(relative.parts) >= 3 and relative.parts[0] == 'posts' and relative.parts[1] in withdrawn:
            document = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
            language = document.html.get('lang') if document.html else None
            if language not in LANGUAGES:
                language = next((code for code in LANGUAGES
                                 if path.name in (page_name(code), *page_aliases(code))), DEFAULT_LANGUAGE)
            _atomic_text(path, _withdrawn_page(language))
            continue
        document = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
        removed = False
        # Discovery counts/tags can reveal a withdrawn story even on pages
        # whose own articles all survive. Rebuild these from allowed posts.
        for discovery in document.select('.hashtag-discovery'):
            discovery.decompose()
            removed = True
        for article in list(document.find_all('article')):
            if str(article.get('id')) in withdrawn:
                article.decompose()
                removed = True
        # Homepage highlights duplicate selected article text outside the feed.
        # Remove the whole preview, including its source, summary, and links,
        # before any full rebuild can fail and leave an old release in service.
        for preview in list(document.select('[data-preview-post-id]')):
            if str(preview.get('data-preview-post-id')) in withdrawn:
                preview.decompose()
                removed = True
        for highlights in list(document.select('.hero-latest')):
            if not highlights.select('[data-preview-post-id]'):
                highlights.decompose()
                removed = True
        # Related-source lists in an otherwise surviving story must also stop
        # exposing an excluded author's name/link. All other links remain intact.
        for link in list(document.find_all('a', href=True)):
            if link['href'] in withdrawn_urls:
                link.decompose()
                removed = True
        if not removed:
            continue
        if relative.parts[0] == 'tags' and not document.find('article'):
            _atomic_text(path, _withdrawn_page(document.html.get('lang', DEFAULT_LANGUAGE)))
            continue
        for context in document.select('.timeline-context'):
            context.decompose()
        # A source page may have used a withdrawn source's name or headline in
        # metadata. Retain canonical/navigation links; never retain old previews.
        for meta in list(document.find_all('meta')):
            if meta.get('property', '').startswith('og:') or meta.get('name', '') in ('description', 'twitter:card', 'twitter:title', 'twitter:description', 'twitter:image'):
                meta.decompose()
        if document.title:
            document.title.string = 'rep0rter'
        for section in document.select('section.day'):
            articles = section.find_all('article')
            if not articles:
                section.decompose()
            elif section.h2 and section.h2.span:
                section.h2.span.string = str(len(articles))
        _atomic_text(path, str(document))
    for path in root.rglob('*.xml'):
        try:
            tree = ET.fromstring(path.read_bytes())
            changed = False
            for channel in tree.findall('./channel'):
                for item in list(channel.findall('item')):
                    if item.findtext('guid', '').strip() in guids:
                        channel.remove(item)
                        changed = True
            if changed:
                _atomic_text(path, ET.tostring(tree, encoding='unicode', xml_declaration=True))
        except ET.ParseError:
            _atomic_text(path, '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>rep0rter</title><description>Feed rebuilding</description></channel></rss>')
    for post_id in withdrawn:
        for language in LANGUAGES:
            for filename in (page_name(language), *page_aliases(language)):
                _atomic_text(root / 'posts' / post_id / filename, _withdrawn_page(language))


def _purge_media(store, withdrawn_urls=()):
    # Serialize with whole-generation builds so a build cannot republish a stale
    # page just after this scrub. The lock is acquired only after DB commits.
    base = store.path.parent
    runtime = services.get()
    if runtime is not None:
        runtime.hydrate_site()
    with (base / 'site-build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        roots = set()
        if (base / 'site').is_dir():
            roots.add((base / 'site').resolve())
        for name in ('.site-releases', 'site-releases'):
            releases = base / name
            if releases.is_dir():
                roots.update(path.resolve() for path in releases.iterdir() if path.is_dir())
        for root in roots:
            _sanitize_site(store, root, withdrawn_urls)
        paths = [base / 'image-cache', *(root / 'cards' for root in roots)]
        for path in paths:
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        if runtime is not None:
            runtime.publish_site(None)


def _scrub(store, event_id):
    now=time.time()
    event = store.conn.execute('SELECT text,html,author_id,author_name,url,meta FROM events WHERE id=?', (event_id,)).fetchone()
    changed = bool(event and (any(event[column] for column in ('text','html','author_id','author_name','url'))
                   or event['meta'] != json.dumps({'content_status':'deleted','visibility':'unknown'})))
    audit_rows = list(_audit_rows(store, {event_id}))
    changed = changed or bool(audit_rows)
    for row in store.conn.execute('SELECT id FROM posts WHERE event_id=?',(event_id,)).fetchall():
        store.conn.execute('INSERT OR IGNORE INTO retractions(post_id,requested_at) VALUES(?,?)',(row['id'],now))
    store.conn.execute("UPDATE events SET text='',html='',author_id='',author_name='',url='',meta=? WHERE id=?",
                       (json.dumps({'content_status':'deleted','visibility':'unknown'}),event_id))
    store.conn.execute("UPDATE posts SET headline='',summary='',translations='{}',reasons='[]' WHERE event_id=?",(event_id,))
    tables={r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'delivery_jobs' in tables:
        store.conn.execute("UPDATE delivery_jobs SET payload='{}',content_hash=NULL WHERE post_id IN (SELECT id FROM posts WHERE event_id=?)", (event_id,))
        store.conn.execute("UPDATE delivery_jobs SET status=CASE WHEN status='unknown' THEN 'unknown' ELSE 'failed' END,error='withdrawn',next_attempt=NULL,payload='{}' WHERE post_id IN (SELECT id FROM posts WHERE event_id=?) AND status!='sent'",(event_id,))
    if 'telegram_mutations' in tables:
        # Pending saved bodies can contain peers that were excluded later. The
        # transport reconstructs them before sending, so retaining text is unnecessary.
        for row in store.conn.execute('SELECT id,payload FROM telegram_mutations').fetchall():
            try:
                payload = json.loads(row['payload'])
            except (ValueError, TypeError):
                payload = {}
            if 'body' in payload:
                payload['body'] = {}
                store.conn.execute('UPDATE telegram_mutations SET payload=? WHERE id=?', (json.dumps(payload),row['id']))
    # Remove dependent evidence snapshots even when their root event differs.
    for table, rowid, _ in audit_rows:
        store.conn.execute(f'DELETE FROM {table} WHERE rowid=?', (rowid,))
    return changed


def redact(store, event_ids, reason='withdrawn'):
    event_ids=_audit_dependents(store, set(event_ids))
    # Expand dependencies before destroying IDs needed to resolve quotes.
    changed=True
    while changed:
        changed=False
        for row in store.conn.execute('SELECT * FROM events').fetchall():
            e=store._row_to_event(row)
            refs={r.get('event_id') for r in e.meta.get('references',[]) if isinstance(r,dict)}
            if e.id not in event_ids and (e.parent_id in event_ids or refs & event_ids):
                event_ids.add(e.id); changed=True
    withdrawn_urls = {row[0] for event_id in event_ids for row in store.conn.execute('SELECT url FROM events WHERE id=?', (event_id,)) if row[0]}
    now=time.time()
    with store.conn:
        store.conn.executemany('INSERT OR IGNORE INTO event_tombstones VALUES(?,?,?)',((e,now,reason) for e in event_ids))
    # Durable ledger precedes removal, so interruption replays removal on next open.
    save(store)
    with store.conn:
        for event_id in event_ids:
            _scrub(store,event_id)
        for row in store.conn.execute("SELECT subject FROM policy_rules WHERE scope='user'").fetchall():
            store.conn.execute('DELETE FROM users WHERE id=?',(row['subject'],))
    _purge_media(store, withdrawn_urls)
    return sorted(event_ids)


def register_commands(sub):
    p=sub.add_parser('exclusion',help='manage stable-ID optouts and editorial source exclusions; verify requester identity before adding an optout')
    p.add_argument('action',choices=('add','list','remove'))
    p.add_argument('--scope',choices=SCOPES)
    p.add_argument('--subject')
    p.add_argument('--reason',default='')
    p.set_defaults(func=cmd_exclusion)
    p=sub.add_parser('retract',help='preview/apply removal of excluded content; remote edits are separate')
    action=p.add_mutually_exclusive_group(required=True)
    action.add_argument('--preview',action='store_true')
    action.add_argument('--apply',action='store_true')
    p.set_defaults(func=cmd_retract)


def cmd_exclusion(cfg,args):
    from .store import Store
    with Store(cfg.db_path) as store:
        if args.action!='list':
            if not args.scope or not args.subject:
                raise ValueError('--scope and --subject required')
            if args.action=='add': add_rule(store,args.scope,args.subject,args.reason)
            else: remove_rule(store,args.scope,args.subject)
        for row in store.conn.execute('SELECT * FROM policy_rules'):
            print(json.dumps(dict(row),ensure_ascii=False))
    return 0


def cmd_retract(cfg,args):
    from .store import Store
    from .publishers.site import build
    from .retractions import plan_remote, queue
    with Store(cfg.db_path) as store:
        ids=affected(store)
        posts=[r[0] for r in store.conn.execute('SELECT id FROM posts WHERE event_id IN (%s)' % (','.join('?' for _ in ids) or 'NULL'),ids)]
        plans=plan_remote(store,cfg,posts)
        print(json.dumps({'events':ids,'posts':posts,'remote':plans},ensure_ascii=False))
        if args.apply:
            redact(store,ids)
            queue(store,plans)
            build(store,cfg)
    return 0
