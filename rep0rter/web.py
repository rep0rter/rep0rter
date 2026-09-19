"""Google login and owner submission UI alongside the generated static feed."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import secrets
import time
from urllib.parse import urlsplit
import uuid

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.flask_client import OAuth
from flask import Flask, abort, g, redirect, render_template, request, session, url_for, send_from_directory
from itsdangerous import BadSignature, URLSafeTimedSerializer
from joserfc.errors import JoseError as JOSEValidationError
from requests import RequestException

from .config import Config, load_config
from .i18n import LANGUAGES, page_name
from . import community, hashtags, projects, project_automation
from .publishers import site
from .store import Store

SESSION_SECONDS = 12 * 3600


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_app(cfg: Config | None = None) -> Flask:
    cfg = cfg or load_config()
    app = Flask(__name__, static_folder=None)
    origin = urlsplit(cfg.site_url)
    if cfg.google_login_enabled:
        if len(cfg.web_secret_key) < 32:
            raise ValueError('REP0RTER_WEB_SECRET_KEY must contain at least 32 random characters.')
        if (origin.scheme not in ('http', 'https') or not origin.hostname or origin.username or origin.password
                or origin.query or origin.fragment or origin.path not in ('', '/')
                or (origin.scheme == 'http' and origin.hostname not in ('localhost', '127.0.0.1', '::1'))):
            raise ValueError('Google login requires REP0RTER_SITE_URL to be an HTTPS origin (HTTP localhost is allowed).')
    app.config.update(
        SECRET_KEY=cfg.web_secret_key,
        SESSION_COOKIE_NAME='rep0rter_session',
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=origin.scheme == 'https',
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_SECONDS),
        SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=128 * 1024,
        MAX_FORM_MEMORY_SIZE=128 * 1024,
        MAX_FORM_PARTS=20,
    )
    oauth = OAuth(app)
    google = oauth.register(
        name='google', client_id=cfg.google_client_id, client_secret=cfg.google_client_secret,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid profile email', 'code_challenge_method': 'S256', 'timeout': 15},
    )

    def store() -> Store:
        if 'store' not in g:
            g.store = Store(cfg.db_path)
        return g.store

    @app.teardown_appcontext
    def close_store(error=None):
        db = g.pop('store', None)
        if db is not None:
            db.close()

    def account():
        token = session.get('login_token')
        if not token:
            return None
        return store().conn.execute(
            '''SELECT a.* FROM project_accounts a JOIN project_sessions s ON a.id=s.account_id
               WHERE s.token_hash=? AND s.expires_at>?''', (_hash(token), time.time())
        ).fetchone()

    def csrf_token():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    def check_csrf():
        expected = session.get('csrf', '')
        supplied = request.form.get('csrf', '')
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
            abort(400, 'Your form expired. Reload the page and try again.')

    def revoke_session():
        if session.get('login_token'):
            db = store()
            with db.conn:
                db.conn.execute('DELETE FROM project_sessions WHERE token_hash=?', (_hash(session['login_token']),))

    def form_page(*, error=None, status=200, values=None, saved_post=None, story=False, preview=None):
        owner = account() if cfg.google_login_enabled else None
        token = None
        if owner:
            # A signed, account-bound id makes resubmission safe across tabs,
            # request races, lost responses and failed site builds.
            token = request.form.get('submission_token') or URLSafeTimedSerializer(
                cfg.web_secret_key, salt='story-submission' if story else 'project-submission'
            ).dumps({'account': owner['id'], 'id': uuid.uuid4().hex})
        return render_template(
            'write.html' if story else 'submit.html', cfg=cfg, owner=owner, error=error, values=values or {},
            languages=LANGUAGES, csrf=csrf_token() if cfg.google_login_enabled else '',
            submission_token=token, saved_post=saved_post, preview=preview,
        ), status

    @app.after_request
    def security_headers(response):
        if request.path.startswith(('/auth/', '/submit', '/projects', '/write')):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; script-src 'none'; style-src 'self'; "
                # Chromium applies form-action to the OAuth POST's redirect too.
                "img-src 'self'; form-action 'self' https://accounts.google.com; "
                "frame-ancestors 'none'; base-uri 'none'"
            )
        return response

    @app.get('/submit')
    def submit():
        return form_page(status=200 if cfg.google_login_enabled else 503)

    @app.route('/write', methods=['GET', 'POST'])
    def write_story():
        if not cfg.google_login_enabled:
            return form_page(story=True, status=503)
        if request.method == 'GET':
            values = {}
            try:
                tag = hashtags.normalize(request.args.get('tag', ''))
                values['hashtags'] = '#' + tag
            except ValueError:
                pass
            return form_page(story=True, values=values)
        owner = account()
        if owner is None:
            return form_page(story=True, error='Sign in with Google before publishing a story.', status=401)
        check_csrf()
        try:
            identity = URLSafeTimedSerializer(cfg.web_secret_key, salt='story-submission').loads(
                request.form.get('submission_token', ''), max_age=SESSION_SECONDS,
            )
            if identity['account'] != owner['id']:
                abort(403)
        except (BadSignature, KeyError, TypeError):
            abort(400, 'Your form expired. Reload the page and try again.')
        try:
            values = community.validate(request.form)
            if request.form.get('action') == 'preview':
                return form_page(story=True, values=request.form, preview=values)
            post_id = projects.publish(store(), owner['id'], identity['id'], values, story=True)
        except projects.SubmissionError as exc:
            return form_page(story=True, error=str(exc), status=exc.status, values=request.form)
        try:
            site.build(store(), cfg)
        except Exception:
            app.logger.error('Site build failed after saving community story %s', post_id)
            return form_page(story=True, error='Your story is saved, but the feed could not refresh yet. '
                             'Retry publishing to refresh it, or wait for the next scheduled update.',
                             status=503, values=request.form, saved_post=post_id)
        return redirect(f'/posts/{post_id}/index.html', code=303)

    @app.post('/auth/google')
    def login():
        if not cfg.google_login_enabled:
            return form_page(status=503)
        check_csrf()
        destination = request.form.get('destination')
        # Only fixed local destinations can survive the OAuth round trip.
        destination = destination if destination in ('/write', '/submit', '/projects') else '/projects'
        login_tag = request.form.get('tag', '') if destination == '/write' else ''
        revoke_session()
        session.clear()
        session['login_destination'] = destination
        if login_tag:
            try:
                session['login_tag'] = hashtags.normalize(login_tag)
            except ValueError:
                pass
        session.permanent = True
        try:
            # Fixed configuration prevents Host/forwarded-header redirect poisoning.
            return google.authorize_redirect(cfg.site_url.rstrip('/') + '/auth/google/callback',
                                             prompt='select_account')
        except (OAuthError, RequestException, ValueError):
            return form_page(story=destination == '/write', error='Google sign-in is temporarily unavailable. Please try again.', status=502)

    @app.get('/auth/google/callback')
    def callback():
        if not cfg.google_login_enabled:
            return form_page(status=503)
        try:
            state = session.get('_state_google_' + request.args.get('state', ''), {})
            if state.get('exp', 0) <= time.time() or not state.get('data', {}).get('nonce'):
                raise ValueError('Login request expired.')
            # Authlib validates state, signature, issuer, audience, expiry and nonce.
            token = google.authorize_access_token()
            info = token.get('userinfo') or {}
            subject = info.get('sub')
            if not isinstance(subject, str) or not subject or info.get('email_verified') is not True:
                raise ValueError('A verified Google account is required.')
        except (OAuthError, JOSEValidationError, RequestException, ValueError, KeyError):
            writing = session.get('login_destination') == '/write'
            session.clear()
            return form_page(story=writing, error='Google sign-in was cancelled or could not be verified. Please try again.', status=400)
        db = store()
        now = time.time()
        login_token = secrets.token_urlsafe(32)
        # Email and Google tokens are never persisted or published.
        name = str(info.get('name') or 'Project owner')[:100]
        with db.conn:
            db.conn.execute(
                '''INSERT INTO project_accounts VALUES (?, ?, ?, ?)
                   ON CONFLICT(google_subject) DO UPDATE SET name=excluded.name''',
                (uuid.uuid4().hex, subject, name, now),
            )
            owner = db.conn.execute('SELECT id FROM project_accounts WHERE google_subject=?', (subject,)).fetchone()
            db.conn.execute('DELETE FROM project_sessions WHERE expires_at<=?', (now,))
            db.conn.execute('INSERT INTO project_sessions VALUES (?, ?, ?)',
                            (_hash(login_token), owner['id'], now + SESSION_SECONDS))
        destination = session.get('login_destination', '/projects')
        login_tag = session.get('login_tag')
        session.clear()
        session.permanent = True
        session['login_token'] = login_token
        if destination == '/write':
            return redirect(url_for('write_story', **({'tag': login_tag} if login_tag else {})), code=303)
        return redirect(destination if destination in ('/submit', '/projects') else '/projects', code=303)

    @app.post('/auth/logout')
    def logout():
        if not cfg.google_login_enabled:
            return redirect(url_for('submit'), code=303)
        check_csrf()
        revoke_session()
        session.clear()
        return redirect(url_for('submit'), code=303)

    @app.post('/submit')
    def publish():
        if not cfg.google_login_enabled:
            return form_page(status=503)
        owner = account()
        if owner is None:
            return form_page(error='Sign in with Google before posting your project.', status=401)
        check_csrf()
        try:
            identity = URLSafeTimedSerializer(cfg.web_secret_key, salt='project-submission').loads(
                request.form.get('submission_token', ''), max_age=SESSION_SECONDS,
            )
            if identity['account'] != owner['id']:
                abort(403)
        except (BadSignature, KeyError, TypeError):
            abort(400, 'Your form expired. Reload the page and try again.')
        try:
            values = projects.validate(request.form)
            post_id = projects.publish(store(), owner['id'], identity['id'], values)
        except projects.SubmissionError as exc:
            return form_page(error=str(exc), status=exc.status, values=request.form)
        try:
            site.build(store(), cfg)
        except Exception:
            # The post is already durable. A retry rebuilds without inserting again.
            app.logger.error('Site build failed after saving project post %s', post_id)
            return form_page(error='Your project is saved, but the feed could not refresh yet. '
                                   'Retry publishing to refresh it, or wait for the next scheduled update.',
                             status=503, values=request.form, saved_post=post_id)
        return redirect(f'/posts/{post_id}/{page_name(values["language"])}', code=303)

    @app.get('/healthz')
    def health():
        return {'status': 'ok', 'google_login_enabled': cfg.google_login_enabled}

    @app.route('/projects', methods=['GET', 'POST'])
    @app.route('/projects/<project_id>', methods=['GET', 'POST'])
    def managed_project(project_id=None):
        if not cfg.google_login_enabled:
            return form_page(status=503)
        owner = account()
        if owner is None:
            return form_page(error='Sign in with Google to manage your project news.',
                             status=401 if request.method == 'POST' else 200)
        db = store()
        saved = None
        if project_id:
            saved = db.conn.execute('SELECT * FROM managed_projects WHERE id=? AND account_id=?',
                                    (project_id, owner['id'])).fetchone()
            if not saved:
                abort(404)
        values = dict(saved) if saved else {
            'author': owner['name'], 'language': 'en', 'source_kind': 'github', 'interval_hours': 6,
            'headline_template': project_automation.DEFAULT_HEADLINE,
            'summary_template': project_automation.DEFAULT_SUMMARY,
        }
        error, preview, status = None, None, 200
        if request.method == 'POST':
            check_csrf()
            values = request.form.to_dict()
            try:
                clean = project_automation.validate(values)
                if request.form.get('action') == 'preview':
                    preview = project_automation.preview(clean)
                else:
                    project_id = project_automation.save(db, owner['id'], clean, project_id)
                    return redirect(url_for('managed_project', project_id=project_id, saved='1'), code=303)
            except projects.SubmissionError as exc:
                error, status = str(exc), exc.status
        managed = db.conn.execute('SELECT * FROM managed_projects WHERE account_id=? ORDER BY title',
                                  (owner['id'],)).fetchall()
        return render_template('projects.html', cfg=cfg, owner=owner, projects=managed, values=values,
                               project_id=project_id, languages=LANGUAGES, csrf=csrf_token(),
                               error=error, preview=preview, saved=saved,
                               saved_notice=request.args.get('saved') == '1'), status

    @app.get('/')
    @app.get('/<path:filename>')
    def static_site(filename='index.html'):
        # Also supports local development without a separate Caddy instance.
        if filename == 'style.css':
            return send_from_directory(app.root_path + '/templates', filename)
        return send_from_directory(cfg.site_dir, filename)

    return app
