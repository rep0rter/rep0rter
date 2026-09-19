"""Offline checks at privacy/publication/operations boundaries."""
import json
from types import SimpleNamespace

from rep0rter import operations, policy
from rep0rter.store import Event, Post, Store


def fake_web(latest=None):
    def get(url, **kwargs):
        if url.endswith('.xml'):
            item = f'<item><pubDate>{latest}</pubDate></item>' if latest else ''
            body = f'<rss><channel>{item}</channel></rss>'
        else:
            body = '<html></html>'
        return SimpleNamespace(text=body, content=body.encode(), raise_for_status=lambda: None)
    return get


def seed(path):
    with Store(path) as store:
        store.set_kv('collector_health', json.dumps({'healthy': True, 'last_healthy_at': 200}))
        for i in (1, 2):
            store.upsert_events([Event(f'slack:C:{i}', 'slack', 'message', 'slack:C', 100 + i,
                                      author_id=f'U{i}', text=f'Source {i}')], now=150)
            store.add_post(Post(f'slack:C:{i}', 160 + 20*i, 7, f'Headline {i}', f'Summary {i}'))


def test_health_matches_feed_after_latest_publication_is_retracted(tmp_path):
    path = tmp_path / 'db.sqlite'
    seed(path)
    with Store(path) as store:
        policy.redact(store, ['slack:C:2'])
    result = operations.check_health(path, now=210, min_free_bytes=0, site_url='https://site.invalid',
                                    get=fake_web('Thu, 01 Jan 1970 00:03:00 +0000'))
    assert result['latest_post_at'] == 180
    assert 'feed_behind' not in result['issues']
    assert result['healthy']


def test_all_withdrawn_is_a_valid_empty_feed(tmp_path):
    path = tmp_path / 'db.sqlite'
    seed(path)
    with Store(path) as store:
        policy.redact(store, ['slack:C:1', 'slack:C:2'])
    result = operations.check_health(path, now=210, min_free_bytes=0, site_url='https://site.invalid', get=fake_web())
    assert result['latest_post_at'] is None
    assert result['healthy']


def test_health_filters_pending_exclusions_and_private_sources_readonly(tmp_path):
    path = tmp_path / 'db.sqlite'
    seed(path)
    with Store(path) as store:
        policy.add_rule(store, 'user', 'slack:U2')
        store.conn.execute("UPDATE events SET meta=? WHERE id='slack:C:1'", (json.dumps({'visibility':'private'}),))
        store.conn.commit()
    before = path.read_bytes()
    policy_before = (tmp_path / 'exclusions.json').read_bytes()
    result = operations.check_health(path, now=210, min_free_bytes=0, site_url='https://site.invalid', get=fake_web())
    assert result['latest_post_at'] is None
    assert result['healthy']
    assert path.read_bytes() == before
    assert (tmp_path / 'exclusions.json').read_bytes() == policy_before


def test_successful_remote_send_after_withdrawal_keeps_proven_mapping(tmp_path):
    from rep0rter.config import Config
    from rep0rter.delivery import DeliveryOutbox, prepare_posts
    from rep0rter.reporter import Candidate
    path = tmp_path / 'db.sqlite'
    with Store(path) as store:
        source = Event('slack:C:1', 'slack', 'message', 'slack:C', 100, author_id='U1', text='Original source')
        store.upsert_events([source], now=150)
        post = Post(source.id, 180, 7, 'Headline', 'Summary')
        config = Config(data_dir=tmp_path, telegram_chat_id='news')
        prepare_posts(config, store, [(Candidate(source, None, 7), post)])
        outbox = DeliveryOutbox(store)
        job = outbox.claim('news')
        # Privacy change races with a request already accepted by the remote API.
        policy.redact(store, [source.id])
        outbox.sent(job, 88)
        saved = json.loads(store.conn.execute('SELECT delivery FROM posts').fetchone()[0])
        assert saved['telegram']['message_id'] == 88
        assert store.conn.execute('SELECT status FROM retractions').fetchone()[0] == 'pending'


def test_exclusion_arriving_during_card_render_prevents_transport(tmp_path, monkeypatch):
    from rep0rter import delivery
    from rep0rter.config import Config
    from rep0rter.reporter import Candidate
    config = Config(data_dir=tmp_path, telegram_bot_token='fake', telegram_chat_id='news')
    with Store(config.db_path) as store:
        source = Event('slack:C:1','slack','message','slack:C',100,author_id='U1',text='Original')
        store.upsert_events([source], now=150)
        post = Post(source.id,180,7,'Headline','Summary',translations={'en':{'headline':'Headline','summary':'Summary'}})
        delivery.prepare_posts(config,store,[(Candidate(source,None,7),post)])
        class Renderer:
            def __init__(self,cfg): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def render_report(self,*args):
                policy.add_rule(store,'user','slack:U1')
                image=tmp_path/'test.png'
                image.write_bytes(b'fake-image')
                return image
        monkeypatch.setattr(delivery,'CardRenderer',Renderer)
        def forbidden(*args,**kwargs):
            raise AssertionError('Excluded content reached Telegram transport')
        monkeypatch.setattr(delivery.telegram,'send_photo',forbidden)
        assert delivery.deliver_pending(config,store)=={}
        assert store.conn.execute('SELECT status FROM delivery_jobs').fetchone()[0]=='failed'


def test_redaction_sanitizes_all_cached_generations_even_when_rebuild_fails(tmp_path, monkeypatch):
    import hashlib
    import pytest
    from bs4 import BeautifulSoup
    from PIL import Image
    from xml.etree import ElementTree as ET
    from rep0rter.config import Config
    from rep0rter.i18n import LANGUAGES, page_name, feed_name
    from rep0rter.publishers import site
    from rep0rter.store import Container

    config=Config(data_dir=tmp_path,site_url='https://site.invalid')
    def render(renderer,event,container,names):
        path=renderer.cfg.site_dir/'cards'/(hashlib.sha256(event.id.encode()).hexdigest()+'.png')
        path.parent.mkdir(parents=True,exist_ok=True)
        Image.new('RGB',(2,2),'white').save(path)
        return path
    monkeypatch.setattr(site.CardRenderer,'render',render)
    monkeypatch.setattr(site.CardRenderer,'render_report',lambda renderer,event,container,post,language: render(renderer,event,container,{}))
    with Store(config.db_path) as store:
        store.upsert_container(Container('slack:C','slack','source'))
        for i in (1,2):
            source=Event(f'slack:C:{i}','slack','message','slack:C',100+i,author_id=f'U{i}',
                         author_name=f'AUTHOR{i}',text=f'ORIGINAL{i}',url=f'https://source.invalid/{i}')
            store.upsert_events([source],now=150)
            translations={code:{'headline':f'{code} HEADLINE{i}','summary':f'{code} SUMMARY{i}'} for code in LANGUAGES}
            store.add_post(Post(source.id,180+i,7,f'HEADLINE{i}',f'SUMMARY{i}',translations=translations))
        site.build(store,config)
        site.build(store,config)  # Keep both a current symlink and previous generation.
        survivor_before=BeautifulSoup((config.site_dir/'index.html').read_text(),'html.parser').find('article',id='1')
        survivor_links=[a['href'] for a in survivor_before.find_all('a',href=True)]
        survivor_text=survivor_before.get_text()
        policy.redact(store,['slack:C:2'])
        def fail(*args,**kwargs):
            raise RuntimeError('simulated renderer failure')
        monkeypatch.setattr(site,'_build',fail)
        with pytest.raises(RuntimeError,match='renderer failure'):
            site.build(store,config)
        for root in (tmp_path/'.site-releases').iterdir():
            for path in list(root.rglob('*.html'))+list(root.rglob('*.xml')):
                text=path.read_text()
                assert not any(secret in text for secret in ('ORIGINAL2','AUTHOR2','HEADLINE2','SUMMARY2','https://source.invalid/2'))
            assert not list((root/'cards').glob('*.png'))
            for code in LANGUAGES:
                doc=BeautifulSoup((root/'posts/2'/page_name(code)).read_text(),'html.parser')
                assert doc.find('meta',attrs={'name':'robots'})['content']=='noindex'
                assert not doc.find('meta',attrs={'property':'og:title'})
                assert doc.html['lang']==code
                guids=[item.findtext('guid') for item in ET.parse(root/feed_name(code)).findall('./channel/item')]
                assert guids==['1']
            alias=BeautifulSoup((root/'posts/2/index.en.html').read_text(),'html.parser')
            assert alias.html['lang']=='en'
            assert alias.find('meta',attrs={'name':'robots'})['content']=='noindex'
            assert [item.findtext('guid') for item in ET.parse(root/'feed.en.xml').findall('./channel/item')]==['1']
        survivor_after=BeautifulSoup((config.site_dir/'index.html').read_text(),'html.parser').find('article',id='1')
        assert survivor_after.get_text()==survivor_text
        assert [a['href'] for a in survivor_after.find_all('a',href=True)]==survivor_links


def test_withdrawal_preserves_ambiguous_delivery_for_operator_reconciliation(tmp_path):
    from rep0rter.config import Config
    from rep0rter.delivery import DeliveryOutbox,prepare_posts
    from rep0rter.reporter import Candidate
    with Store(tmp_path/'db.sqlite') as store:
        source=Event('slack:C:1','slack','message','slack:C',100,text='Original')
        store.upsert_events([source])
        cfg=Config(data_dir=tmp_path,telegram_chat_id='news')
        prepare_posts(cfg,store,[(Candidate(source,None,7),Post(source.id,180,7,'Title','Summary'))])
        outbox=DeliveryOutbox(store)
        job=outbox.claim('news')
        outbox.failed(job,'timeout',unknown=True)
        policy.redact(store,[source.id])
        assert store.conn.execute('SELECT status FROM delivery_jobs').fetchone()[0]=='unknown'
        assert outbox.claim('news') is None
