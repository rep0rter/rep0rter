"""Owner-defined news templates, fed by public releases or RSS 2.0 entries."""
from __future__ import annotations

from .runtime import locks as fcntl
import hashlib
import json

import re
from string import Template
import time
from urllib.parse import urlsplit
import uuid
from xml.etree import ElementTree

from . import project_sources, projects
from .collectors.github import timestamp
from .collectors.rss import to_event
from .sources import normalize_text

DEFAULT_HEADLINE = '$project: $title'
DEFAULT_SUMMARY = '$summary\n\nLearn more: $url'
FIELDS = {'project', 'title', 'summary', 'url'}


def render_template_text(template, context):
    # Deliberately plain substitution: no Jinja expressions, code or attributes.
    try:
        return Template(template).substitute(context).strip()
    except (ValueError, KeyError) as exc:
        raise projects.SubmissionError('Templates support only $project, $title, $summary and $url. Use $$ for a dollar sign.') from exc


def validate(form):
    values = projects.validate(dict(form, description='Project news automation'))
    values.pop('description')
    kind = form.get('source_kind', '')
    source = form.get('source_url', '').strip()
    if kind == 'github':
        match = re.fullmatch(r'(?:https://github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/?', source)
        if not match or any(part in ('.', '..') for part in match[1].split('/')):
            raise projects.SubmissionError('Enter a GitHub repository as owner/repository or its https://github.com URL.')
        source = match[1].lower()
    elif kind == 'rss':
        try:
            parsed = urlsplit(source)
            valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                     and not parsed.password and parsed.port in (None, 443) and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid or len(source) > 2048 or any(c.isspace() or ord(c) < 32 for c in source) or '\\' in source:
            raise projects.SubmissionError('Enter a public HTTPS RSS 2.0 feed URL on port 443.')
    else:
        raise projects.SubmissionError('Choose GitHub releases or RSS news.')
    if form.get('interval_hours') not in ('1', '6', '24'):
        raise projects.SubmissionError('Choose an hourly, six-hourly or daily check.')
    values.update(source_kind=kind, source_url=source, interval_hours=int(form['interval_hours']),
                  enabled=int(form.get('enabled') == 'yes'))
    for key, default, maximum in (('headline_template', DEFAULT_HEADLINE, 160),
                                   ('summary_template', DEFAULT_SUMMARY, 3000)):
        template = form.get(key, default).strip()
        if not template or len(template) > maximum:
            raise projects.SubmissionError(f'The {key.replace("_", " ")} must contain 1–{maximum} characters.')
        render_template_text(template, {key: 'Example' for key in FIELDS})
        values[key] = template
    return values


def save(store, account_id, values, project_id=None):
    now = time.time()
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        previous = None
        if project_id:
            previous = store.conn.execute('SELECT * FROM managed_projects WHERE id=? AND account_id=?',
                                          (project_id, account_id)).fetchone()
            if not previous:
                raise projects.SubmissionError('Project not found.', 404)
        elif store.conn.execute('SELECT COUNT(*) FROM managed_projects WHERE account_id=?', (account_id,)).fetchone()[0] >= 10:
            raise projects.SubmissionError('You can manage up to ten projects.', 429)
        project_id = project_id or uuid.uuid4().hex
        same_source = previous and (previous['source_kind'], previous['source_url']) == (values['source_kind'], values['source_url'])
        # Resuming or changing sources starts from now; do not dump old news.
        enabled_at = previous['enabled_at'] if same_source and previous['enabled'] else now
        params = dict(values, id=project_id, account_id=account_id, enabled_at=enabled_at, updated_at=now, next_run=now)
        store.conn.execute('''INSERT INTO managed_projects
            (id, account_id, title, url, author, language, source_kind, source_url, headline_template,
             summary_template, interval_hours, enabled, enabled_at, updated_at, next_run)
            VALUES (:id, :account_id, :title, :url, :author, :language, :source_kind, :source_url,
                    :headline_template, :summary_template, :interval_hours, :enabled, :enabled_at, :updated_at, :next_run)
            ON CONFLICT(id) DO UPDATE SET title=excluded.title, url=excluded.url, author=excluded.author,
            language=excluded.language, source_kind=excluded.source_kind, source_url=excluded.source_url,
            headline_template=excluded.headline_template, summary_template=excluded.summary_template,
            interval_hours=excluded.interval_hours, enabled=excluded.enabled, enabled_at=excluded.enabled_at,
            updated_at=excluded.updated_at, next_run=excluded.next_run, last_error='' ''', params)
    return project_id


def preview(values):
    context = {'project': values['title'], 'title': 'Version 1.0 released',
               'summary': 'We launched a new feature and welcome community feedback.', 'url': values['url']}
    return {key: render_template_text(values[key + '_template'], context) for key in ('headline', 'summary')}


def source_items(project):
    if project['source_kind'] == 'github':
        repo = project['source_url']
        rows = json.loads(project_sources.fetch('https://api.github.com/repos/' + repo + '/releases?per_page=30'))
        if not isinstance(rows, list):
            raise ValueError('Invalid GitHub releases response.')
        return [{'id': str(row['id']), 'title': row.get('name') or row.get('tag_name') or 'New release',
                 'summary': normalize_text(row.get('body') or '', 'markdown'), 'url': row['html_url'],
                 'ts': timestamp(row['published_at'])} for row in rows
                if not row.get('draft') and not row.get('prerelease') and row.get('published_at')]
    root = ElementTree.fromstring(project_sources.fetch(project['source_url']))
    channel = root.find('channel')
    if root.tag != 'rss' or root.get('version') != '2.0' or channel is None:
        raise ValueError('Source must be an RSS 2.0 feed.')
    items = []
    for raw in channel.findall('item')[:100]:
        try:
            event = to_event(raw, project['source_url'], project['title'])
        except (ValueError, TypeError):
            continue
        items.append({'id': event.id, 'title': normalize_text(raw.findtext('title') or '', 'html'),
                      'summary': normalize_text(raw.findtext('description') or '', 'html'),
                      'url': event.url, 'ts': event.ts})
    return items


def run_due(store, *, now=None):
    """Bound each cycle and serialize workers. Posts join the next normal site build."""
    now = time.time() if now is None else now
    published = 0
    with (store.path.parent / 'project-automation.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        rows = store.conn.execute('''SELECT * FROM managed_projects WHERE enabled=1 AND next_run<=?
                                    ORDER BY next_run LIMIT 20''', (now,)).fetchall()
        for row in rows:
            project = dict(row)
            with store.conn:
                store.conn.execute('UPDATE managed_projects SET next_run=?, last_checked=? WHERE id=? AND updated_at=?',
                                   (now + project['interval_hours'] * 3600, now, project['id'], project['updated_at']))
            error = ''
            try:
                items = sorted(source_items(project), key=lambda item: item['ts'])
                for item in items:
                    if not project['enabled_at'] <= item['ts'] <= now:
                        continue
                    key = hashlib.sha256((project['id'] + ':' + item['id']).encode()).hexdigest()
                    if store.conn.execute('SELECT 1 FROM project_submissions WHERE event_id=?', ('project:' + key,)).fetchone():
                        continue
                    context = dict(project=project['title'], **{key: item[key] for key in ('title', 'summary', 'url')})
                    try:
                        values = projects.validate({
                            'title': render_template_text(project['headline_template'], context),
                            'description': render_template_text(project['summary_template'], context),
                            'url': item['url'], 'author': project['author'], 'language': project['language'],
                            'owner_confirmed': 'yes',
                        })
                    except projects.SubmissionError as exc:
                        # One oversized/malformed upstream item must not starve
                        # every later update from this project.
                        error = str(exc)
                        continue
                    projects.publish(store, project['account_id'], key, values, automation=project)
                    published += 1
            except projects.SubmissionError as exc:
                error = str(exc)
            except Exception:
                # External errors can contain credentials or attacker-controlled text.
                error = 'Could not read this source. Check the public repository or RSS 2.0 URL; the next scheduled check will retry.'
            with store.conn:
                store.conn.execute('UPDATE managed_projects SET last_error=? WHERE id=? AND updated_at=?',
                                   (error, project['id'], project['updated_at']))
    return published
