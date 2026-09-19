"""Explicit, resumable Telegram replacement without changing reports or feed IDs.

Pause the publishing worker while applying a batch. Remote deletions use the
existing mutation outbox; no replacement is prepared until every old message
has a confirmed deletion or an explicitly requested English migration notice.
The batch stores identifiers and action results, never report text.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests

from . import retractions, stories
from .delivery import DeliveryOutbox, _job_item
from .publishers import telegram
from .publishers.cards import CardRenderer

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_republish_batches (
 id TEXT PRIMARY KEY, target TEXT NOT NULL, language TEXT NOT NULL,
 status TEXT NOT NULL, manifest TEXT NOT NULL, created_at REAL NOT NULL,
 staged_at REAL
);
"""
CANNOT_DELETE = 'Telegram rejected (400: message_cannot_be_deleted)'


def ensure(store):
    DeliveryOutbox(store)
    stories.ensure(store)
    store.conn.executescript(retractions.SCHEMA + SCHEMA)


def _snapshot(store, cfg):
    target = str(cfg.telegram_target or '')
    if not target or cfg.telegram_language != 'en':
        raise ValueError('An explicit Telegram target and English language are required')
    jobs = [dict(row) for row in store.conn.execute(
        "SELECT id,post_id,target,status,message_id,error FROM delivery_jobs WHERE target IN ('',?) ORDER BY id", (target,))]
    if any(j['status'] != 'sent' and not (j['status'] == 'failed' and j['error'] == 'withdrawn') for j in jobs):
        raise ValueError('Reconcile pending, failed, or unknown deliveries before republishing')
    mappings = []
    for row in store.conn.execute('SELECT id,delivery FROM posts ORDER BY id'):
        current = json.loads(row['delivery'] or '{}').get('telegram')
        if not current:
            continue
        mapping = retractions._mapping(row)
        if mapping is None:
            raise ValueError(f"Confirm the complete legacy Telegram mapping for post {row['id']} first")
        if str(mapping['chat_id']) == target:
            mappings.append({'post_id': row['id'], 'message_id': int(mapping['message_id']),
                             'outbox_id': mapping.get('outbox_id'), 'confirmed': bool(mapping.get('confirmed'))})
    visible = {post.id for post, _, _ in store.recent_posts(limit=store.post_count() + 1)}
    visible.difference_update(row[0] for row in store.conn.execute('SELECT post_id FROM retractions'))
    story_rows = store.conn.execute('SELECT story_id,post_id,revision FROM story_posts ORDER BY revision DESC').fetchall()
    latest = {}
    for row in story_rows:
        if row['post_id'] in visible:
            latest.setdefault(row['story_id'], row['post_id'])
    old_versions = {row['post_id'] for row in story_rows} - set(latest.values())
    selected = sorted(visible - old_versions)
    if not selected:
        raise ValueError('No visible reports are available to republish')
    for post_id in selected:
        other = store.conn.execute("SELECT target FROM delivery_jobs WHERE post_id=? AND publisher='telegram'", (post_id,)).fetchone()
        if other and other['target'] not in ('', target):
            raise ValueError('A selected report has an outbox in another channel; reconcile its ownership first')
    return {'old_mappings': mappings, 'selected_posts': selected,
            'old_jobs': [{k: j[k] for k in ('id', 'post_id', 'target', 'status', 'message_id')} for j in jobs]}


def _preflight(store, cfg, selected):
    with CardRenderer(cfg) as renderer:
        for post_id in selected:
            candidate, post = _job_item(store, post_id)
            telegram.format_caption(cfg, candidate, post)
            photo = renderer.render_report(candidate.event, candidate.container, post, 'en')
            if not Path(photo).is_file() or not Path(photo).stat().st_size:
                raise ValueError(f'English report image is missing for post {post_id}')


def plan(store, cfg):
    ensure(store)
    if store.conn.execute("SELECT 1 FROM telegram_republish_batches WHERE status!='staged'").fetchone():
        raise ValueError('Resume the existing republish batch before planning another')
    snapshot = _snapshot(store, cfg)
    _preflight(store, cfg, snapshot['selected_posts'])
    if snapshot != _snapshot(store, cfg):
        raise ValueError('Reports or deliveries changed during preflight; pause the worker and plan again')
    batch_id = uuid.uuid4().hex
    with store.conn:
        store.conn.execute('INSERT INTO telegram_republish_batches VALUES(?,?,?,?,?,?,NULL)',
                           (batch_id, str(cfg.telegram_target), 'en', 'planned', json.dumps(snapshot), time.time()))
    return inspect(store, batch_id)


def inspect(store, batch_id):
    row = store.conn.execute('SELECT * FROM telegram_republish_batches WHERE id=?', (batch_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown republish batch')
    result = dict(row)
    result['manifest'] = json.loads(result['manifest'])
    return result


def apply(store, cfg, batch_id):
    ensure(store)
    batch = inspect(store, batch_id)
    if batch['target'] != str(cfg.telegram_target) or cfg.telegram_language != batch['language']:
        raise ValueError('The republish target or language differs from the approved batch')
    if batch['status'] == 'staged':
        return batch  # Repeated application never resets replacement deliveries.
    manifest = batch['manifest']
    snapshot = {key: manifest[key] for key in ('old_mappings', 'selected_posts', 'old_jobs')}
    if snapshot != _snapshot(store, cfg):
        raise ValueError('Reports or delivery mappings changed; reconcile before resuming this batch')
    _preflight(store, cfg, snapshot['selected_posts'])
    if batch['status'] == 'planned':
        affected = [mapping['post_id'] for mapping in snapshot['old_mappings']]
        plans = retractions.plan_remote(store, cfg, affected)
        if any(p['status'] != 'prepared' or p['action'] != 'deleteMessage' or p['target'] != batch['target'] for p in plans):
            raise ValueError('Every old message needs a proven complete deletion mapping')
        expected = {m['message_id'] for m in snapshot['old_mappings']}
        if {p['message_id'] for p in plans} != expected:
            raise ValueError('Deletion plan does not cover every old message')
        retractions.queue(store, plans)
        mutations = []
        for item in plans:
            payload = json.dumps({'body': item['payload'], 'posts': item['posts']}, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256((item['action'] + payload).encode()).hexdigest()
            row = store.conn.execute('SELECT id FROM telegram_mutations WHERE target=? AND message_id=? AND digest=?',
                                     (item['target'], item['message_id'], digest)).fetchone()
            mutations.append(row['id'])
        manifest['mutation_ids'] = mutations
        with store.conn:
            store.conn.execute("UPDATE telegram_republish_batches SET status='deleting',manifest=? WHERE id=?",
                               (json.dumps(manifest), batch_id))
    retractions.process(store, cfg)
    for mutation_id in manifest['mutation_ids']:
        row = store.conn.execute('SELECT status,error,message_id FROM telegram_mutations WHERE id=?', (mutation_id,)).fetchone()
        notice = manifest.get('notice_replacements', {}).get(str(mutation_id), {})
        notice_complete = bool(row and row['status'] == 'failed' and row['error'] == CANNOT_DELETE
                               and notice.get('status') == 'sent' and notice.get('action') == 'editMessageText'
                               and notice.get('message_id') == row['message_id'] and notice.get('target') == batch['target'])
        if not row or (row['status'] != 'sent' and not notice_complete):
            return inspect(store, batch_id)
    # The deletion barrier is complete. Queue replacements and retire old
    # mappings in one transaction, preserving stable post IDs and site URLs.
    now = time.time()
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        current = inspect(store, batch_id)
        if current['status'] == 'staged':
            return current
        if snapshot != _snapshot(store, cfg):
            raise ValueError('Reports or deliveries changed before replacement staging')
        for mapping in snapshot['old_mappings']:
            row = store.conn.execute('SELECT delivery FROM posts WHERE id=?', (mapping['post_id'],)).fetchone()
            delivery = json.loads(row['delivery'] or '{}')
            delivery.pop('telegram', None)
            store.conn.execute('UPDATE posts SET delivery=? WHERE id=?', (json.dumps(delivery), mapping['post_id']))
        new_jobs = []
        for post_id in snapshot['selected_posts']:
            row = store.conn.execute("SELECT id FROM delivery_jobs WHERE post_id=? AND publisher='telegram'", (post_id,)).fetchone()
            if row:
                job_id = row['id']
                store.conn.execute("""UPDATE delivery_jobs SET target=?,status='prepared',payload='{}',content_hash=NULL,
                    message_id=NULL,lease_token=NULL,lease_until=NULL,attempts=0,next_attempt=NULL,error=NULL,
                    updated_at=? WHERE id=?""", (batch['target'], now, job_id))
            else:
                job_id = store.conn.execute('INSERT INTO delivery_jobs(post_id,target,created_at,updated_at) VALUES(?,?,?,?)',
                                            (post_id, batch['target'], now, now)).lastrowid
            new_jobs.append({'post_id': post_id, 'job_id': job_id})
        manifest['replacement_jobs'] = new_jobs
        store.conn.execute("UPDATE telegram_republish_batches SET status='staged',manifest=?,staged_at=? WHERE id=?",
                           (json.dumps(manifest), now, batch_id))
    return inspect(store, batch_id)


def replace_undeletable_with_notice(store, cfg, batch_id, mutation_id):
    """Explicit operator action for Telegram's older, undeletable text messages.

    This does not claim that deletion succeeded. The separate notice attempt is
    durable before transport, is never picked up by the deletion worker, and
    cannot be blindly retried after an ambiguous response.
    """
    ensure(store)
    if not cfg.telegram_bot_token:
        raise ValueError('A Telegram bot token is required')
    parsed = urlsplit(cfg.site_url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('A public website URL is required for the English notice')
    notice_text = ('This earlier digest has been superseded.\n\n'
                   'rep0rter now publishes in English. Read the latest reports and choose Chinese, '
                   'Japanese, or Korean translations on the website:\n'
                   + telegram._link(cfg.site_url.rstrip('/') + '/', 'Read rep0rter'))
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        batch = inspect(store, batch_id)
        manifest = batch['manifest']
        if batch['status'] != 'deleting' or batch['target'] != str(cfg.telegram_target) or cfg.telegram_language != 'en':
            raise ValueError('An active English republish batch for this channel is required')
        if mutation_id not in manifest.get('mutation_ids', []):
            raise ValueError('The deletion mutation is not part of this batch')
        row = store.conn.execute('SELECT * FROM telegram_mutations WHERE id=?', (mutation_id,)).fetchone()
        if (not row or row['target'] != batch['target'] or row['action'] != 'deleteMessage'
                or row['status'] != 'failed' or row['error'] != CANNOT_DELETE):
            raise ValueError('Only a confirmed message_cannot_be_deleted failure can become an English notice')
        notices = manifest.setdefault('notice_replacements', {})
        key = str(mutation_id)
        if key in notices:
            raise ValueError('This notice was already attempted; verify its outcome instead of retrying')
        notices[key] = {'action': 'editMessageText', 'target': batch['target'], 'message_id': row['message_id'],
                        'status': 'sending', 'created_at': time.time(),
                        'content_hash': hashlib.sha256(notice_text.encode()).hexdigest(), 'original_deleted': False}
        store.conn.execute('UPDATE telegram_republish_batches SET manifest=? WHERE id=?', (json.dumps(manifest), batch_id))
    status, error = 'unknown', 'Ambiguous notice response; verify Telegram before reconciliation'
    try:
        response = requests.post(telegram.API.format(token=cfg.telegram_bot_token, method='editMessageText'),
                                 json={'chat_id': batch['target'], 'message_id': row['message_id'],
                                       'text': notice_text, 'parse_mode': 'HTML', 'disable_web_page_preview': True},
                                 timeout=(10, 40))
        body = response.json()
        if isinstance(body, dict) and response.status_code < 500:
            if body.get('ok') is True and response.status_code == 200:
                status, error = 'sent', None
            elif body.get('ok') is False:
                code = int(body.get('error_code', response.status_code))
                reason = retractions.rejection_reason(code, body.get('description'))
                if code == 400 and reason == 'message_not_modified':
                    status, error = 'sent', None
                else:
                    status, error = 'failed', f'Telegram rejected ({code}: {reason})'
    except Exception:
        pass  # Never persist exception text: it can contain the bot token.
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        latest = inspect(store, batch_id)['manifest']
        latest['notice_replacements'][str(mutation_id)].update(status=status, error=error, completed_at=time.time())
        store.conn.execute('UPDATE telegram_republish_batches SET manifest=? WHERE id=?', (json.dumps(latest), batch_id))
    return inspect(store, batch_id)


def register_commands(sub):
    parser = sub.add_parser('telegram-republish', help='plan or resume an explicit English Telegram replacement batch; pause the worker first')
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--plan', action='store_true')
    choice.add_argument('--batch')
    parser.add_argument('--apply', action='store_true', help='delete confirmed old messages and stage replacements after the deletion barrier')
    parser.set_defaults(func=cmd_republish)
    notice = sub.add_parser('telegram-republish-notice', help='explicitly replace an undeletable old digest with an English migration notice')
    notice.add_argument('--batch', required=True)
    notice.add_argument('--mutation', type=int, required=True)
    notice.set_defaults(func=cmd_notice)


def cmd_notice(cfg, args):
    from .store import Store
    with Store(cfg.db_path) as store:
        print(json.dumps(replace_undeletable_with_notice(store, cfg, args.batch, args.mutation), ensure_ascii=False, indent=2))
    return 0


def cmd_republish(cfg, args):
    from .store import Store
    if args.plan and args.apply:
        raise ValueError('Review a plan first, then apply its explicit --batch ID')
    with Store(cfg.db_path) as store:
        ensure(store)
        result = plan(store, cfg) if args.plan else apply(store, cfg, args.batch) if args.apply else inspect(store, args.batch)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
