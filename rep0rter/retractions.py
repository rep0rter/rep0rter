"""Conservative Telegram removal: proven mappings only; legacy groups need confirmation."""
from __future__ import annotations
import hashlib
import json
import time
from pathlib import Path

import requests

from .publishers import telegram

SCHEMA='''CREATE TABLE IF NOT EXISTS telegram_mutations (
 id INTEGER PRIMARY KEY, target TEXT NOT NULL, message_id INTEGER NOT NULL,
 action TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'prepared', error TEXT, next_attempt REAL, lease_until REAL,
 created_at REAL NOT NULL, completed_at REAL, UNIQUE(target,message_id,digest));'''


def _mapping(post):
    value=json.loads(post['delivery'] or '{}').get('telegram')
    if isinstance(value,dict) and value.get('chat_id') and value.get('message_id') and (value.get('outbox_id') or value.get('confirmed')):
        return value
    return None


def plan_remote(store,cfg,post_ids):
    from .policy import event_allowed
    from .reporter import Candidate
    from .store import Post
    all_posts=store.conn.execute('SELECT * FROM posts ORDER BY id').fetchall()
    affected=set(post_ids); groups={}; plans=[]
    for post in all_posts:
        if post['id'] not in affected: continue
        mapping=_mapping(post)
        if mapping is None:
            if json.loads(post['delivery'] or '{}').get('telegram'):
                plans.append({'posts':[post['id']],'status':'manual','reason':'Legacy mapping lacks confirmed chat/group membership'})
            continue
        key=(str(mapping['chat_id']),int(mapping['message_id']))
        groups[key]=mapping
    for (target,message),mapping in groups.items():
        peers=[p for p in all_posts if (m:=_mapping(p)) and str(m['chat_id'])==target and int(m['message_id'])==message]
        survivors=[]
        for p in peers:
            event=store.get_event(p['event_id'])
            if p['id'] in affected or not event or not event_allowed(store,event): continue
            post=Post(p['event_id'],p['published_at'],p['score'],p['headline'],p['summary'],id=p['id'],translations=json.loads(p['translations'] or '{}'))
            survivors.append((Candidate(event,store.get_container(event.container_id),post.score),post))
        action='deleteMessage'; payload={}
        if survivors:
            if mapping.get('outbox_id') or mapping.get('format')=='photo':
                # New format is strictly one post per photo. Ambiguous shared photos require human review.
                plans.append({'posts':[p['id'] for p in peers if p['id'] in affected],'status':'manual','reason':'Shared photo ownership requires manual reconciliation'})
                continue
            try:
                messages=telegram.format_messages(survivors)
                if len(messages)!=1: raise ValueError('reconstruction exceeds one message')
            except ValueError:
                plans.append({'posts':[p['id'] for p in peers if p['id'] in affected],'status':'manual','reason':'Surviving group exceeds text limit'})
                continue
            action='editMessageText'; payload={'text':messages[0],'parse_mode':'HTML','disable_web_page_preview':True}
        plans.append({'posts':[p['id'] for p in peers if p['id'] in affected],'status':'prepared',
                      'target':target,'message_id':message,'action':action,'payload':payload})
    return plans


def queue(store,plans):
    store.conn.executescript(SCHEMA)
    with store.conn:
        for plan in plans:
            if plan['status']=='manual':
                store.conn.executemany("UPDATE retractions SET status='manual',error=? WHERE post_id=?",((plan['reason'],p) for p in plan['posts']))
                continue
            payload=json.dumps({'body':plan['payload'],'posts':plan['posts']},ensure_ascii=False,sort_keys=True)
            digest=hashlib.sha256((plan['action']+payload).encode()).hexdigest()
            store.conn.execute('''INSERT OR IGNORE INTO telegram_mutations
              (target,message_id,action,payload,digest,created_at) VALUES(?,?,?,?,?,?)''',
              (plan['target'],plan['message_id'],plan['action'],payload,digest,time.time()))


def process(store,cfg):
    store.conn.executescript(SCHEMA)
    now=time.time()
    with store.conn:
        store.conn.execute("UPDATE telegram_mutations SET status='unknown',error='Expired lease; verify Telegram before retry' WHERE status='sending' AND lease_until<?",(now,))
    if not cfg.telegram_bot_token or not cfg.telegram_target: return 0
    completed=0
    while True:
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            job=store.conn.execute("SELECT * FROM telegram_mutations WHERE target=? AND (status='prepared' OR (status='failed' AND next_attempt<=?)) ORDER BY id LIMIT 1",(str(cfg.telegram_target),time.time())).fetchone()
            if not job: break
            store.conn.execute("UPDATE telegram_mutations SET status='sending',lease_until=? WHERE id=?",(time.time()+120,job['id']))
        payload=json.loads(job['payload'])
        try:
            # A queued edit may predate a second withdrawal. Reconstruct peers
            # against the latest ledger immediately before the remote mutation.
            from .policy import initialize
            initialize(store)
            fresh = [plan for plan in plan_remote(store, cfg, payload['posts'])
                     if plan.get('target') == job['target'] and plan.get('message_id') == job['message_id']]
            if len(fresh) != 1 or fresh[0]['status'] != 'prepared':
                with store.conn:
                    store.conn.execute("UPDATE telegram_mutations SET status='manual',error='Mapping changed; review current peers',lease_until=NULL WHERE id=?", (job['id'],))
                continue
            plan = fresh[0]
            action = plan['action']
            payload = {'body': plan['payload'], 'posts': plan['posts']}
            with store.conn:
                store.conn.execute('UPDATE telegram_mutations SET action=?,payload=? WHERE id=?',
                                   (action, json.dumps(payload, ensure_ascii=False), job['id']))
            response=requests.post(telegram.API.format(token=cfg.telegram_bot_token,method=action),
                json={'chat_id':job['target'],'message_id':job['message_id'],**payload['body']},timeout=(10,40))
            body=response.json()
            if not isinstance(body, dict) or 'ok' not in body or response.status_code >= 500:
                raise RuntimeError('Ambiguous Telegram mutation response')
            if not body.get('ok'):
                status=int(body.get('error_code',response.status_code))
                # Repeating a completed edit is harmless and confirms desired state.
                if not (action=='editMessageText' and status==400 and 'message is not modified' in body.get('description','').lower()):
                    with store.conn:
                        store.conn.execute("UPDATE telegram_mutations SET status='failed',error=?,next_attempt=?,lease_until=NULL WHERE id=?",
                            (f'Telegram rejected ({status})',time.time()+int(body.get('parameters',{}).get('retry_after',60)) if status==429 else None,job['id']))
                    if status==429: break
                    continue
            if body.get('ok') is not True and not (action == 'editMessageText' and 'message is not modified' in body.get('description','').lower()):
                raise RuntimeError('Ambiguous Telegram mutation response')
            with store.conn:
                store.conn.execute("UPDATE telegram_mutations SET status='sent',payload=?,completed_at=?,error=NULL,lease_until=NULL WHERE id=?",
                    (json.dumps({'posts':payload['posts']}),time.time(),job['id']))
                store.conn.executemany("UPDATE retractions SET status='complete',completed_at=?,error=NULL WHERE post_id=?",((time.time(),p) for p in payload['posts']))
            completed+=1
        except Exception as exc:
            with store.conn:
                store.conn.execute("UPDATE telegram_mutations SET status='unknown',error=?,lease_until=NULL WHERE id=?",(type(exc).__name__,job['id']))
    return completed


def confirm_group(store,post_ids,target,message_id,format='text'):
    if not post_ids or not target or message_id<1: raise ValueError('Complete observed group, target and positive message ID required')
    with store.conn:
        for post_id in post_ids:
            row=store.conn.execute('SELECT delivery FROM posts WHERE id=?',(post_id,)).fetchone()
            if row is None: raise ValueError('Post missing')
            delivery=json.loads(row['delivery'] or '{}')
            delivery['telegram']={'chat_id':str(target),'message_id':message_id,'confirmed':True,'format':format}
            store.conn.execute('UPDATE posts SET delivery=? WHERE id=?',(json.dumps(delivery),post_id))


def register_commands(sub):
    p=sub.add_parser('delivery-map',help='confirm complete legacy Telegram message membership after manual verification')
    p.add_argument('--post',type=int,action='append',required=True);p.add_argument('--chat',required=True)
    p.add_argument('--message',type=int,required=True);p.add_argument('--confirm',action='store_true',required=True)
    p.set_defaults(func=cmd_map)
    p=sub.add_parser('retraction-delivery',help='inspect or explicitly reconcile remote removal jobs')
    p.add_argument('--apply',action='store_true');p.add_argument('--job',type=int)
    group=p.add_mutually_exclusive_group();group.add_argument('--retry',action='store_true');group.add_argument('--confirmed',action='store_true')
    p.set_defaults(func=cmd_delivery)


def cmd_map(cfg,args):
    from .store import Store
    with Store(cfg.db_path) as store: confirm_group(store,args.post,args.chat,args.message)
    return 0


def cmd_delivery(cfg,args):
    from .store import Store
    with Store(cfg.db_path) as store:
        store.conn.executescript(SCHEMA)
        if args.job:
            if not (args.retry or args.confirmed): raise ValueError('Choose --retry after confirming absent or --confirmed after observing removal')
            with store.conn:
                row=store.conn.execute('SELECT * FROM telegram_mutations WHERE id=?',(args.job,)).fetchone()
                if not row or row['status'] not in ('failed','unknown'): raise ValueError('Only failed/unknown jobs can be reconciled')
                store.conn.execute('UPDATE telegram_mutations SET status=?,error=NULL,next_attempt=NULL,lease_until=NULL,completed_at=? WHERE id=?',('prepared' if args.retry else 'sent',None if args.retry else time.time(),args.job))
                if args.confirmed:
                    for post_id in json.loads(row['payload']).get('posts',[]):
                        store.conn.execute("UPDATE retractions SET status='complete',completed_at=? WHERE post_id=?",(time.time(),post_id))
        elif args.retry or args.confirmed:
            raise ValueError('--retry/--confirmed requires --job')
        if args.apply: process(store,cfg)
        for row in store.conn.execute('SELECT id,target,message_id,action,status,error FROM telegram_mutations'): print(dict(row))
    return 0
