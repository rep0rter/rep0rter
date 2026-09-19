"""Conservative story identity and material revisions, preserving every source event."""
from __future__ import annotations
import hashlib
import json
import re
import time
from dataclasses import replace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .policy import event_allowed
from .slack_text import links_in, to_plain
from .sources import plain_text, automated

SCHEMA='''
CREATE TABLE IF NOT EXISTS stories(id TEXT PRIMARY KEY, created_at REAL NOT NULL, canonical_event_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS story_events(event_id TEXT PRIMARY KEY, story_id TEXT NOT NULL REFERENCES stories(id),
 fingerprint TEXT NOT NULL, observed_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS story_members ON story_events(story_id);
CREATE TABLE IF NOT EXISTS story_posts(story_id TEXT NOT NULL REFERENCES stories(id),revision INTEGER NOT NULL,
 post_id INTEGER NOT NULL UNIQUE REFERENCES posts(id),fingerprint TEXT NOT NULL,covered_ids TEXT NOT NULL,
 created_at REAL NOT NULL,PRIMARY KEY(story_id,revision),UNIQUE(story_id,fingerprint));
'''
UPDATE=re.compile(r'閉門|不開放|非公開|closed[- ]door|invitation[- ]only|비공개|截止|取消|延期|延後|截止.{0,6}(?:改|延)|更正|新版|新增|增補|共筆|募集|徵求|招募|報名|updated?|cancel(?:led|ed)?|postpon|deadline|collaborat|募集|共同|共同編集|취소|모집|공동',re.I)
DATES=re.compile(r'(?<!\d)(?:\d{4}[-/])?\d{1,2}[-/月]\d{1,2}(?:日)?(?!\d)',re.I)
VERSIONS=re.compile(r'(?<![\w])v?\d+\.\d+(?:\.\d+)*(?:[-+][\w.-]+)?',re.I)
CORRECTION=re.compile(r'閉門|不開放|非公開|closed[- ]door|invitation[- ]only|비공개|取消|延期|延後|更正|澄清|截止.{0,6}(?:改|延)|cancel(?:led|ed)?|postpon|correct|clarif|deadline.{0,12}(?:extend|chang)|訂正|취소|정정',re.I)
RAW_URL=re.compile(r'https?://[^\s<>]+')
TRACKING={'fbclid','gclid','dclid','mc_cid','mc_eid'}


def canonical_url(url):
    try:
        p=urlsplit(url.rstrip('.,，。'))
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password: return None
        host=p.hostname.lower()
        if p.port and p.port not in (80,443): host+=':'+str(p.port)
        query=urlencode([(k,v) for k,v in sorted(parse_qsl(p.query,keep_blank_values=True)) if not k.lower().startswith('utm_') and k.lower() not in TRACKING])
        path=p.path.rstrip('/') or '/'
        # Homepages/account landing pages do not identify an announcement.
        if path=='/' or (host in ('t.me','telegram.me') and path.count('/')==1): return None
        if host in ('github.com', 'www.github.com') and path.count('/') <= 2: return None
        return urlunsplit((p.scheme.lower(),host,path,query,''))
    except ValueError:
        return None


def normalized(event):
    from .sources import plain_text
    text=plain_text(event)
    text=re.sub(r'https?://[^\s<>]+',lambda m:canonical_url(m.group(0)) or m.group(0),text)
    text=re.sub(r'<@[^>]+>|@[\w.-]+','',text)
    return re.sub(r'[\s*_~`]+','',text)


def fingerprint(event):
    return hashlib.sha256(normalized(event).encode()).hexdigest()


def urls(event):
    values = links_in(event.text) + RAW_URL.findall(plain_text(event))
    return {u for value in values if (u := canonical_url(value.rstrip(').,，。]}>')))}


def _body(event):
    return re.sub(r'[\s*_~`]+', '', RAW_URL.sub('', plain_text(event))).casefold()


def _same(left, right):
    a, b = normalized(left), normalized(right)
    if not a or not b:
        return False
    if a == b and len(a) >= 40:
        return True
    # Different dates or release versions identify separate occurrences, unless
    # the source explicitly says a date was corrected. "Registration" is not a correction.
    left_body, right_body = RAW_URL.sub('', plain_text(left)), RAW_URL.sub('', plain_text(right))
    va, vb = set(VERSIONS.findall(left_body)), set(VERSIONS.findall(right_body))
    if va and vb and va != vb:
        return False
    da, db = set(DATES.findall(left_body)), set(DATES.findall(right_body))
    common = urls(left) & urls(right)
    if da and db and da != db and not (common and CORRECTION.search(right_body)):
        return False
    # Shared URLs do not dominate textual similarity (e.g. unrelated events at a venue).
    a, b = _body(left), _body(right)
    grams = lambda text: {text[i:i+3] for i in range(len(text)-2)}
    ga, gb = grams(a), grams(b)
    similarity = len(ga & gb) / max(1, len(ga | gb))
    if common and ((min(len(a), len(b)) >= 12 and similarity >= .45)
                   or (CORRECTION.search(right_body) and len(b) >= 12)):
        return True
    return min(len(a), len(b)) >= 100 and similarity >= .85


def ensure(store):
    store.conn.executescript(SCHEMA)


def anchor_id(event):
    refs=event.meta.get('references',[])
    for ref in refs:
        if isinstance(ref,dict) and ref.get('public') is True and ref.get('event_id'):
            return ref['event_id']
    return event.meta.get('canonical_object_id') or event.id


def _assign(store,event,now):
    prior=store.conn.execute('SELECT story_id FROM story_events WHERE event_id=?',(event.id,)).fetchone()
    if prior:
        story = prior['story_id']
        anchor = anchor_id(event)
        # Older imports may have published a share before preserving its original.
        # Attach the now-verified original to that existing history, retaining post IDs.
        original = store.get_event(anchor) if anchor != event.id else None
        if original and event_allowed(store, original) and not automated(original):
            membership = store.conn.execute('SELECT story_id FROM story_events WHERE event_id=?', (anchor,)).fetchone()
            other_story = membership['story_id'] if membership else None
            # Never silently rewrite two independently published revision histories.
            conflicting_history = other_story and other_story != story and store.conn.execute(
                'SELECT 1 FROM story_posts WHERE story_id=?', (other_story,)).fetchone()
            if not conflicting_history:
                if other_story and other_story != story:
                    store.conn.execute('UPDATE story_events SET story_id=? WHERE story_id=?', (story, other_story))
                    store.conn.execute('DELETE FROM stories WHERE id=?', (other_story,))
                store.conn.execute('INSERT OR IGNORE INTO story_events VALUES(?,?,?,?)',
                                   (anchor, story, fingerprint(original), now))
                store.conn.execute('UPDATE stories SET canonical_event_id=? WHERE id=?', (anchor, story))
        return story
    anchor=anchor_id(event)
    original=store.conn.execute('SELECT story_id FROM story_events WHERE event_id=?',(anchor,)).fetchone()
    story=original['story_id'] if original else hashlib.sha256(('event:'+anchor).encode()).hexdigest()[:24]
    if not original and anchor==event.id:
        historical=store.conn.execute('''SELECT sp.story_id,e.* FROM story_posts sp
            JOIN posts p ON p.id=sp.post_id JOIN events e ON e.id=p.event_id
            WHERE sp.fingerprint=? AND e.ts BETWEEN ? AND ? ORDER BY sp.created_at LIMIT 1''',
            (fingerprint(event),event.ts-7*86400,event.ts+7*86400)).fetchone()
        if historical and event_allowed(store,store._row_to_event(historical)):
            story=historical['story_id']
            original=historical
    if not original and anchor==event.id:
        for row in store.conn.execute('''SELECT e.*,se.story_id FROM story_events se JOIN events e ON e.id=se.event_id
          WHERE e.ts BETWEEN ? AND ? ORDER BY e.ts ASC LIMIT 1000''',(event.ts-7*86400,event.ts+7*86400)).fetchall():
            other=store._row_to_event(row)
            if event_allowed(store,other) and _same(other,event):
                story=row['story_id']; break
    with store.conn:
        canonical=anchor if event.meta.get('references') else event.id
        store.conn.execute('INSERT OR IGNORE INTO stories VALUES(?,?,?)',(story,now,canonical))
        store.conn.execute('INSERT OR IGNORE INTO story_events VALUES(?,?,?,?)',(event.id,story,fingerprint(event),now))
    return store.conn.execute('SELECT story_id FROM story_events WHERE event_id=?',(event.id,)).fetchone()[0]


def assign(store,event,now):
    if not event_allowed(store,event) or automated(event):
        raise ValueError("Excluded or automated source cannot be assigned to a story")
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        return _assign(store,event,now)


def bootstrap(store,now):
    ensure(store)
    # One-time migration is replayable; existing IDs/GUIDs and Telegram mappings stay intact.
    for post,event,_ in reversed(store.recent_posts(limit=-1)):
        if not event_allowed(store,event) or automated(event): continue
        if store.conn.execute('SELECT 1 FROM story_posts WHERE post_id=?',(post.id,)).fetchone(): continue
        story=assign(store,event,now)
        revision=store.conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM story_posts WHERE story_id=?',(story,)).fetchone()[0]
        with store.conn:
            store.conn.execute('INSERT OR IGNORE INTO story_posts VALUES(?,?,?,?,?,?)',
                (story,revision,post.id,fingerprint(event),json.dumps([event.id]),post.published_at))


def _context(store,story):
    result=[]
    for row in store.conn.execute('SELECT e.* FROM events e JOIN story_events se ON e.id=se.event_id WHERE se.story_id=? ORDER BY e.ts',(story,)):
        e=store._row_to_event(row)
        if event_allowed(store,e) and not automated(e): result.append(e)
    return result


def _mark(candidate,story,revision,digest,covered):
    candidate.event=replace(candidate.event,meta={**candidate.event.meta,'story_id':story,'story_revision':revision,
        'story_fingerprint':digest,'story_covered_ids':sorted(set(covered))})
    return candidate


def material_reply(reply, root):
    text=plain_text(reply)
    if not UPDATE.search(text):
        return False
    if re.match(r'\s*(?:謝謝|感謝|thanks?\b|thank you\b|ありがとう|감사)',text,re.I) and not re.search(r'新增|增補|更正|取消|延期|徵求|招募|updated?|cancel|postpon',text,re.I):
        return False
    dated_deadline=bool(re.search(r'截止|deadline|締切|마감',text,re.I) and DATES.search(text))
    return bool(CORRECTION.search(text) or dated_deadline or (urls(reply)-urls(root)))


def expand_candidates(store,cfg,picked,now):
    from .reporter import Candidate
    bootstrap(store,now)
    picked=list(picked)
    picked_ids={c.event.id for c in picked}
    recovered = []
    for row in store.conn.execute('''SELECT e.*,se.fingerprint AS prior_fingerprint,p.id AS prior_post_id FROM events e
        JOIN posts p ON p.event_id=e.id JOIN story_events se ON se.event_id=e.id
        WHERE e.kind!='story_update' ''').fetchall():
        event=store._row_to_event(row)
        anchor = anchor_id(event)
        recovered_original = store.get_event(anchor) if anchor != event.id else event
        restored_context = (row['prior_fingerprint'] == hashlib.sha256(b'').hexdigest()
                            and recovered_original is not None
                            and event_allowed(store, recovered_original)
                            and not automated(recovered_original)
                            and len(_body(recovered_original)) >= 12)
        if ((event.id not in picked_ids or restored_context) and event_allowed(store,event) and fingerprint(event)!=row['prior_fingerprint']
                and (UPDATE.search(event.text) or restored_context)):
            candidate = Candidate(event,store.get_container(event.container_id),max(6,cfg.score_threshold),
                reasons=['recovered_source_context' if restored_context else 'material_source_correction'],
                plain_text=to_plain(event.text),user_names=store.user_names(event.source))
            if restored_context:
                candidate.event = replace(event, meta={**event.meta, 'source_context_recovered': True,
                                                       'recovery_previous_post_ids': [row['prior_post_id']]})
                # Reconcile before ordinary candidates can independently publish the original.
                recovered_story = assign(store, candidate.event, now)
                canonical_membership = store.conn.execute('SELECT story_id FROM story_events WHERE event_id=?', (anchor,)).fetchone()
                if canonical_membership and canonical_membership['story_id'] != recovered_story:
                    # Two existing published histories need an explicit editorial reconciliation.
                    continue
                recovered.append(candidate)
            else:
                picked.append(candidate)
    picked = recovered + picked
    result=[]; seen=set()
    for c in picked:
        if not event_allowed(store,c.event) or automated(c.event): continue
        story=assign(store,c.event,now)
        if story in seen: continue
        existing=store.conn.execute('SELECT * FROM story_posts WHERE story_id=? ORDER BY revision DESC LIMIT 1',(story,)).fetchone()
        context=_context(store,story)
        original=store.get_event(anchor_id(c.event))
        base=original if original and event_allowed(store,original) else c.event
        digest=fingerprint(base)
        if existing:
            # Mere repeated shares or arbitrary link chatter are not revisions.
            canonical_id=store.conn.execute('SELECT canonical_event_id FROM stories WHERE id=?',(story,)).fetchone()[0]
            if base.id != canonical_id:
                previous_urls=set().union(*(urls(source) for source in context if source.id!=base.id))
                if not CORRECTION.search(base.text) and not (urls(base)-previous_urls): continue
            restored_context = bool(c.event.meta.get('source_context_recovered'))
            if store.conn.execute('SELECT 1 FROM story_posts WHERE story_id=? AND fingerprint=?',(story,digest)).fetchone() or not (UPDATE.search(base.text) or restored_context): continue
            # A previously observed unchanged crosspost cannot roll a corrected story back.
            prior=store.conn.execute('SELECT fingerprint FROM story_events WHERE event_id=?',(base.id,)).fetchone()
            already_covered=any(base.id in json.loads(row['covered_ids']) for row in store.conn.execute('SELECT covered_ids FROM story_posts WHERE story_id=?',(story,)))
            if prior and prior['fingerprint']==digest and already_covered: continue
            revision=existing['revision']+1
            canonical=store.get_event(canonical_id)
            evidence=[canonical,base] if canonical and canonical.id!=base.id and event_allowed(store,canonical) else [base]
            if restored_context and c.event.id != base.id:
                evidence.append(c.event)
            recovery_meta = {key: c.event.meta[key] for key in ('source_context_recovered', 'recovery_previous_post_ids')
                             if key in c.event.meta}
            c.event=replace(base,id=f'story-update:{story}:{digest[:24]}',kind='story_update',parent_id=None,
                meta={**base.meta, **recovery_meta,'references':[{'event_id':e.id,'container_id':e.container_id,'author_id':e.author_id,'public':True} for e in evidence]})
            c.evidence_events=evidence
        else:
            revision=1
            c.event=base
            c.evidence_events=[base]
        _mark(c,story,revision,digest,[e.id for e in context]+[base.id])
        result.append(c);seen.add(story)
    # Published threads can acquire one material revision even though their root is no longer eligible.
    for row in store.conn.execute('''SELECT e.* FROM events e JOIN posts p ON p.event_id=e.parent_id
        WHERE e.kind='thread_reply' AND e.ts>=? ORDER BY e.ts ASC''',(now-cfg.max_item_age_hours*3600,)).fetchall():
        reply=store._row_to_event(row)
        if not event_allowed(store,reply) or automated(reply) or not UPDATE.search(reply.text): continue
        root=store.get_event(reply.parent_id)
        if not root or not root.text or not event_allowed(store,root): continue
        story=assign(store,root,now)
        if story in seen: continue
        history=store.conn.execute('SELECT * FROM story_posts WHERE story_id=? ORDER BY revision DESC',(story,)).fetchall()
        covered={e for p in history for e in json.loads(p['covered_ids'])}
        reply_rows=store.conn.execute('SELECT * FROM events WHERE parent_id=? ORDER BY ts DESC LIMIT 1000',(root.id,)).fetchall()
        fresh=sorted((r for r in (store._row_to_event(row) for row in reply_rows) if r.id not in covered and event_allowed(store,r) and not automated(r) and material_reply(r,root)),key=lambda r:(r.ts,r.id))
        if not fresh or not history: continue
        content='\n'.join(normalized(r) for r in fresh)
        digest=hashlib.sha256((fingerprint(root)+'\n'+content).encode()).hexdigest()
        revision=max(p['revision'] for p in history)+1
        newest=max(fresh,key=lambda e:e.ts)
        refs=[{'event_id':e.id,'container_id':e.container_id,'author_id':e.author_id,'public':True} for e in [root,*fresh]]
        event=replace(newest,id=f'story-update:{story}:{digest[:24]}',kind='story_update',parent_id=None,
            meta={**newest.meta,'references':refs})
        candidate=Candidate(event,store.get_container(root.container_id),max(cfg.score_threshold,6),
            reasons=['material_thread_update'],plain_text=to_plain(root.text),user_names=store.user_names(root.source))
        candidate.evidence_events=[root,*fresh];candidate.thread_events=fresh
        _mark(candidate,story,revision,digest,list(covered|{e.id for e in fresh}))
        for source in fresh:
            with store.conn:
                store.conn.execute('INSERT OR IGNORE INTO story_events VALUES(?,?,?,?)',(source.id,story,fingerprint(source),now))
        result.append(candidate);seen.add(story)
    return result


def reserve(store,candidate,post_id):
    """Called inside post+outbox transaction. Unique story/revision prevents concurrent duplicate sends."""
    event=candidate.event;meta=event.meta
    if not meta.get('story_id'): return
    store.conn.execute('INSERT INTO story_posts VALUES(?,?,?,?,?,?)',
        (meta['story_id'],meta['story_revision'],post_id,meta['story_fingerprint'],json.dumps(meta['story_covered_ids']),time.time()))
    store.conn.execute('INSERT OR IGNORE INTO story_events VALUES(?,?,?,?)',
        (event.id,meta['story_id'],meta['story_fingerprint'],time.time()))
    for covered in meta['story_covered_ids']:
        source=store.get_event(covered)
        if source:
            store.conn.execute('UPDATE story_events SET fingerprint=? WHERE event_id=?',(fingerprint(source),covered))


def info(store,post_id):
    ensure(store)
    row=store.conn.execute('SELECT * FROM story_posts WHERE post_id=?',(post_id,)).fetchone()
    if not row: return {'revision':1,'sources':[],'versions':[]}
    sources=[{'url':e.url,'name':e.author_name or e.container_id} for e in _context(store,row['story_id']) if safe_source_url(e.url) and e.kind!='story_update']
    versions=[{'id':r['post_id'],'revision':r['revision']} for r in store.conn.execute('SELECT * FROM story_posts WHERE story_id=? ORDER BY revision',(row['story_id'],))
              if store.conn.execute('SELECT 1 FROM retractions WHERE post_id=?',(r['post_id'],)).fetchone() is None]
    return {'revision':row['revision'],'sources':sources,'versions':versions}


def safe_source_url(value):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        return False
    try:
        parsed=urlsplit(value)
        return parsed.scheme in {'https','http'} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False
