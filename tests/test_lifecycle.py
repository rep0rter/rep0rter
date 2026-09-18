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
        post = Post(source.id,180,7,'Headline','Summary')
        delivery.prepare_posts(config,store,[(Candidate(source,None,7),post)])
        class Renderer:
            def __init__(self,cfg): pass
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def render(self,*args):
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
