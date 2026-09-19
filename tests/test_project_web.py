"""Offline OIDC validation, owner isolation and real site/RSS publishing."""
import hashlib
import time
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from joserfc import jwt
from joserfc.jwk import RSAKey
from PIL import Image
import pytest

from rep0rter.config import Config
from rep0rter.publishers import site
from rep0rter.store import Store
from rep0rter.web import create_app


@pytest.fixture
def web(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path, site_url='http://localhost', google_client_id='test-client',
                 google_client_secret='test-secret', web_secret_key='test-session-key-' * 3)
    app = create_app(cfg)
    app.testing = True
    google = app.extensions['authlib.integrations.flask_client'].create_client('google')
    key = RSAKey.generate_key(2048, parameters={'kid': 'test-key'})
    google.server_metadata.update(
        issuer='https://accounts.google.com', authorization_endpoint='https://accounts.google.com/o/oauth2/v2/auth',
        token_endpoint='https://oauth2.googleapis.com/token', id_token_signing_alg_values_supported=['RS256'],
        jwks={'keys': [key.as_dict(private=False)]}, _loaded_at=time.time(),
    )
    # Use actual templates, static staging, and RSS with tiny offline card images.
    def card(renderer, event, *args, theme="light"):
        path = renderer.cfg.site_dir / 'cards' / (hashlib.sha256(event.id.encode()).hexdigest() + ('-report' if len(args) > 2 else '') + ('-dark' if theme == 'dark' else '') + '.png')
        path.parent.mkdir(exist_ok=True, parents=True)
        Image.new('RGB', (2, 2), 'black' if theme == 'dark' else 'white').save(path)
        return path
    monkeypatch.setattr(site.CardRenderer, 'render', card)
    monkeypatch.setattr(site.CardRenderer, 'render_report', card)
    return app, cfg, google, key


def fields(response):
    soup = BeautifulSoup(response.text, 'html.parser')
    return {node['name']: node.get('value', '') for node in soup.select('input[type=hidden]')}


def login(web, monkeypatch, client=None, claims=None, signing_key=None, login_data=None):
    app, cfg, google, key = web
    client = client or app.test_client()
    csrf = fields(client.get('/submit'))['csrf']
    response = client.post('/auth/google', data={'csrf': csrf, **(login_data or {})})
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.location).query)
    assert params['redirect_uri'] == ['http://localhost/auth/google/callback']
    assert params['code_challenge_method'] == ['S256']
    assert set(params['scope'][0].split()) == {'openid', 'email', 'profile'}
    identity = dict(iss='https://accounts.google.com', aud=cfg.google_client_id, sub='google-owner-1',
                    exp=int(time.time()) + 600, iat=int(time.time()), nonce=params['nonce'][0],
                    name='A project owner', email='private@example.test', email_verified=True)
    identity.update(claims or {})
    signed = jwt.encode({'alg': 'RS256', 'kid': 'test-key'}, identity, signing_key or key)
    def exchange(**kwargs):
        assert kwargs['code_verifier']
        return {'id_token': signed, 'access_token': 'never-store-this', 'token_type': 'Bearer'}
    monkeypatch.setattr(google, 'fetch_access_token', exchange)
    response = client.get('/auth/google/callback', query_string={'code': 'fake-code', 'state': params['state'][0]})
    return client, response


def post_form(client, **changes):
    values = dict(fields(client.get('/submit')), title='Open Civic Map', description='Find accessible public spaces.',
                  url='https://example.test/map', author='Map maintainer', language='en', owner_confirmed='yes')
    return dict(values, **changes)


def project_form(client, **changes):
    values = dict(fields(client.get('/projects')), title='Open Civic Map', url='https://example.test/map',
                  author='Map maintainer', language='en', source_kind='github', source_url='example/map',
                  headline_template='$project: $title', summary_template='$summary\n$url', interval_hours='6',
                  enabled='yes', owner_confirmed='yes', action='save')
    return dict(values, **changes)


def test_login_verifies_identity_and_keeps_credentials_private(web, monkeypatch):
    client, response = login(web, monkeypatch)
    assert response.status_code == 303
    assert response.location == '/projects'
    with client.session_transaction() as state:
        assert 'login_token' in state and 'access_token' not in state
        assert 'google-owner-1' not in str(dict(state))
    with Store(web[1].db_path) as store:
        assert store.conn.execute('SELECT google_subject FROM project_accounts').fetchone()[0] == 'google-owner-1'
        assert 'private@example.test' not in '\n'.join(store.conn.iterdump())
        assert 'never-store-this' not in '\n'.join(store.conn.iterdump())


def test_login_form_security_policy_allows_google_redirect_only(web):
    client = web[0].test_client()
    for path in ('/submit', '/projects', '/write', '/auth/sign-in'):
        response = client.get(path)
        policy = response.headers['Content-Security-Policy']
        directives = {part.strip().split()[0]: part.strip().split()[1:]
                      for part in policy.split(';') if part.strip()}
        assert directives['form-action'] == ["'self'", 'https://accounts.google.com']
        assert directives['script-src'] == ['http://localhost/theme.js']
        assert directives['frame-ancestors'] == ["'none'"]


@pytest.mark.parametrize('claims', [
    {'aud': 'another-client'}, {'iss': 'https://attacker.test'}, {'nonce': 'wrong'},
    {'exp': 1}, {'email_verified': False}, {'sub': ''},
])
def test_invalid_google_claims_cannot_sign_in(web, monkeypatch, claims):
    client, response = login(web, monkeypatch, claims=claims)
    assert response.status_code == 400
    with client.session_transaction() as state:
        assert 'login_token' not in state


def test_forged_signature_and_missing_state_are_rejected(web, monkeypatch):
    key = RSAKey.generate_key(2048, parameters={'kid': 'test-key'})
    client, response = login(web, monkeypatch, signing_key=key)
    assert response.status_code == 400
    assert client.get('/auth/google/callback?code=bad&state=unknown').status_code == 400
    assert client.get('/auth/google/callback?error=access_denied').status_code == 400


def test_state_expiration_is_checked_before_token_exchange(web, monkeypatch):
    client = web[0].test_client()
    csrf = fields(client.get('/submit'))['csrf']
    response = client.post('/auth/google', data={'csrf': csrf})
    state = parse_qs(urlsplit(response.location).query)['state'][0]
    with client.session_transaction() as data:
        data['_state_google_' + state]['exp'] = 1
        data.modified = True
    assert client.get('/auth/google/callback', query_string={'state': state, 'code': 'code'}).status_code == 400


def test_csrf_authentication_expiration_and_revocable_logout(web, monkeypatch):
    app, cfg, *_ = web
    anonymous = app.test_client()
    assert anonymous.post('/submit').status_code == 401
    assert anonymous.post('/projects').status_code == 401
    assert anonymous.post('/auth/google').status_code == 400
    client, _ = login(web, monkeypatch)
    data = post_form(client)
    assert client.post('/submit', data=dict(data, csrf='wrong')).status_code == 400
    assert client.post('/submit', data=dict(data, csrf='無效')).status_code == 400
    assert client.get('/auth/logout').status_code in (404, 405)
    cookie = client.get_cookie('rep0rter_session').value
    assert client.post('/auth/logout', data={'csrf': data['csrf']}).status_code == 303
    client.set_cookie('rep0rter_session', cookie)
    assert client.post('/submit', data=data).status_code == 401
    client, _ = login(web, monkeypatch, client=client)
    data = post_form(client)
    with Store(cfg.db_path) as store, store.conn:
        store.conn.execute('UPDATE project_sessions SET expires_at=1')
    assert client.post('/submit', data=data).status_code == 401


def test_owner_post_appears_in_real_feed_without_duplicate_or_email(web, monkeypatch):
    client, _ = login(web, monkeypatch)
    data = post_form(client, title='<script>alert(1)</script> Civic Map')
    first = client.post('/submit', data=data)
    assert first.status_code == 303
    assert client.post('/submit', data=data).location == first.location
    cfg = web[1]
    html = (cfg.site_dir / 'index.html').read_text()
    assert '&lt;script&gt;' in html and '<script>alert(1)' not in html
    assert 'Ownership self-declared' in html and 'private@example.test' not in html
    feed = ElementTree.parse(cfg.site_dir / 'feed.xml')
    assert len(feed.findall('./channel/item')) == 1
    assert feed.findtext('./channel/item/title') == data['title']
    with Store(cfg.db_path) as store:
        assert store.event_count() == store.post_count() == 1
        assert store.conn.execute("SELECT name FROM sqlite_master WHERE name='delivery_jobs'").fetchone() is None


@pytest.mark.parametrize('changes', [{'url': 'javascript:alert(1)'}, {'title': ''}, {'title': 'x' * 161},
                                    {'description': 'x' * 3001}, {'owner_confirmed': ''}, {'language': 'xx'},
                                    {'url': 'https://user:pass@example.test'}, {'url': 'https://host:bad'}])
def test_invalid_project_form_is_not_saved(web, monkeypatch, changes):
    client, _ = login(web, monkeypatch)
    assert client.post('/submit', data=post_form(client, **changes)).status_code == 400
    with Store(web[1].db_path) as store:
        assert store.post_count() == 0


def test_failed_build_can_retry_saved_post(web, monkeypatch):
    client, _ = login(web, monkeypatch)
    data = post_form(client)
    real_build = site.build
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(site, 'build', fail)
    response = client.post('/submit', data=data)
    assert response.status_code == 503 and b'project is saved' in response.data
    monkeypatch.setattr(site, 'build', real_build)
    assert client.post('/submit', data=data).status_code == 303
    with Store(web[1].db_path) as store:
        assert store.post_count() == 1


def test_owner_can_preview_save_pause_but_not_edit_another_project(web, monkeypatch):
    client, _ = login(web, monkeypatch)
    data = project_form(client)
    response = client.post('/projects', data=dict(data, action='preview'))
    assert response.status_code == 200 and b'Open Civic Map: Version 1.0 released' in response.data
    with Store(web[1].db_path) as store:
        assert store.conn.execute('SELECT COUNT(*) FROM managed_projects').fetchone()[0] == 0
    response = client.post('/projects', data=data)
    assert response.status_code == 303
    project_url = urlsplit(response.location).path
    other, _ = login(web, monkeypatch, claims={'sub': 'another-owner'})
    assert other.get(project_url).status_code == 404
    assert other.post(project_url, data=project_form(other)).status_code == 404
    assert other.post('/submit', data=post_form(client)).status_code == 400  # Different CSRF
    stolen_form = post_form(client)
    stolen_form['csrf'] = fields(other.get('/submit'))['csrf']
    assert other.post('/submit', data=stolen_form).status_code == 403
    assert client.post(project_url, data=dict(data, enabled='')).status_code == 303
    with Store(web[1].db_path) as store:
        assert store.conn.execute('SELECT enabled FROM managed_projects').fetchone()[0] == 0


def test_disabled_login_and_secure_cookie_configuration(tmp_path):
    app = create_app(Config(data_dir=tmp_path, google_client_id=None, google_client_secret=None, web_secret_key=None))
    assert app.test_client().get('/submit').status_code == 503
    assert app.test_client().get('/healthz').json['google_login_enabled'] is False
    cfg = Config(data_dir=tmp_path, google_client_id='id', google_client_secret='secret', web_secret_key='s' * 48)
    assert create_app(cfg).config['SESSION_COOKIE_SECURE'] is True
    cfg.site_url = 'http://public.example'
    with pytest.raises(ValueError, match='HTTPS'):
        create_app(cfg)


def test_excluded_account_cannot_publish(web, monkeypatch):
    client, _ = login(web, monkeypatch)
    with Store(web[1].db_path) as store, store.conn:
        owner = store.conn.execute('SELECT id FROM project_accounts').fetchone()[0]
        store.conn.execute('INSERT INTO policy_rules (scope, subject, requested_at, effective_at, version) VALUES (?, ?, ?, ?, ?)',
                           ('user', 'project:' + owner, time.time(), time.time(), 1))
    assert client.post('/submit', data=post_form(client)).status_code == 403


@pytest.mark.parametrize('language, heading', [('ZH', '你的社群，下一則故事。'), ('ja', 'あなたのコミュニティ、次のストーリー。'), ('ko', '우리 커뮤니티의 다음 이야기.'), ('en', 'Your community. Your next story.')])
def test_sign_in_card_localized_private_and_shared(web, language, heading):
    client = web[0].test_client()
    page = client.get('/auth/sign-in', query_string={'lang': language, 'return_to': '/index.html?lang=ZH&q=community'})
    fragment = client.get('/auth/sign-in', query_string={'lang': language, 'fragment': '1'})
    assert page.status_code == fragment.status_code == 200
    for response in (page, fragment):
        soup = BeautifulSoup(response.text, 'html.parser')
        assert soup.select_one('[data-auth-card] h1').text == heading
        assert fields(response)['csrf']
        assert fields(response)['destination'] == '/auth/return'
        assert response.headers['Cache-Control'] == 'no-store'
        assert 'private@example.test' not in response.text
    assert BeautifulSoup(fragment.data, 'html.parser').html is None
    assert fields(page)['return_to'] == '/index.html?lang=ZH&q=community'
    soup = BeautifulSoup(page.data, 'html.parser')
    assert soup.select_one('script[src="/theme.js"]')
    assert len(soup.select('[data-theme-choice]')) == 3


@pytest.mark.parametrize('target', ['/', '/index.html?lang=ZH&q=community&sort=oldest#news', '/posts/42/index.ko.html', '/tags/開放資料/index.html', '/tags/हिन्दी/index.html', '/sources/github/index.ja.html'])
def test_reader_login_returns_to_exact_reader_url(web, monkeypatch, target):
    _, response = login(web, monkeypatch, login_data={'destination': '/auth/return', 'return_to': target})
    from urllib.parse import unquote
    assert unquote(response.location) == target


@pytest.mark.parametrize('target', ['https://evil.test/', '//evil.test/', '/\\evil.test/', '/%2f%2fevil.test/', '/%252f%252fevil.test/', '/auth/logout', '/projects', '/posts/../auth/index.html', '//[', '/index.html\nX-Test: bad'])
def test_reader_login_rejects_unsafe_returns(web, monkeypatch, target):
    _, response = login(web, monkeypatch, login_data={'destination': '/auth/return', 'return_to': target})
    assert response.location == '/'


@pytest.mark.parametrize('path, destination', [('/write?tag=civictech', '/write'), ('/submit', '/submit'), ('/projects', '/projects')])
def test_authoring_login_keeps_destination_and_hashtag(web, monkeypatch, path, destination):
    client = web[0].test_client()
    data = fields(client.get(path))
    assert data['destination'] == destination
    _, response = login(web, monkeypatch, client=client, login_data=data)
    assert response.location == destination + ('?tag=civictech' if destination == '/write' else '')


def test_authenticated_card_offers_account_actions_and_logout(web, monkeypatch):
    client, _ = login(web, monkeypatch)
    response = client.get('/auth/sign-in?fragment=1&lang=ZH')
    soup = BeautifulSoup(response.text, 'html.parser')
    assert soup.select_one('h1').text == '你已登入'
    assert soup.select_one('a[href="/write"]')
    assert soup.select_one('a[href="/projects"]')
    assert soup.select_one('form[action="/auth/logout"] input[name="csrf"]')
    assert not soup.select_one('form[action="/auth/google"]')


def test_cancelled_login_retains_language_and_reader_return(web):
    client = web[0].test_client()
    data = fields(client.get('/auth/sign-in?lang=ZH&return_to=/index.html?q=test'))
    response = client.post('/auth/google', data=data)
    state = parse_qs(urlsplit(response.location).query)['state'][0]
    response = client.get('/auth/google/callback', query_string={'error': 'access_denied', 'state': state})
    assert response.status_code == 400
    assert fields(response)['return_to'] == '/index.html?q=test'
    assert fields(response)['ui_language'] == 'zh-TW'
    assert BeautifulSoup(response.text, 'html.parser').select_one('[role=alert]')
