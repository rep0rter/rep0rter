"""Synthetic coverage proofs for archive formatting and month boundaries."""
import html
import json
from decimal import Decimal
from unittest.mock import Mock

import pytest

from rep0rter.collectors import slack_archive as archive, slack_incremental as inc
from rep0rter.collectors.state import BudgetSession, Metrics


SEPTEMBER = '1789571777.383129'
OCTOBER = '1792587721.382019'
LOWER = Decimal('1789500000')


def document(months, active, *, php_ids=False):
    nav = f'<a class="nav-link active" href="/index/channel/C/{active}"></a>'
    for month, rows in months.items():
        nav += (f'<a class="dropdown-item" href="/index/channel/C/{month}">'
                f'<span class="badge">{len(rows)}</span></a>')
    body = ''
    for raw in months[active]:
        stamp = format(float(raw['ts']), '.14g') if php_ids else raw['ts']
        body += (f'<div class="message" id="ts-{stamp}"><span class="message-time" '
                 f'title="{html.escape(json.dumps(raw), quote=True)}"></span></div>')
    return f'<nav role="pagination">{nav}</nav><section role="feed">{body}</section>'


def transport(monkeypatch, pages, limit=8):
    session = Mock(headers={})
    session.get.side_effect = [Mock(content=b'{}', json=lambda rows=p: {'messages': rows})
                               if isinstance(p, list) else Mock(content=p.encode(), text=p)
                               for p in pages]
    monkeypatch.setattr(archive, '_get', lambda client, url, **params: client.get(url, params=params))
    return session, BudgetSession(session, Metrics(), limit=limit, interval=0)


def test_php_trailing_zero_anchor_can_prove_repeated_fractional_page(monkeypatch):
    row = {'ts': OCTOBER}
    page = document({'2026-10': [row, row]}, '2026-10', php_ids=True)
    assert 'id="ts-1792587721.382"' in page
    _, budget = transport(monkeypatch, [[row, row], [row, row], page])
    rows, complete, error, cursor = inc.scan(budget, 'C', Decimal('1792400000'))
    assert rows == [row] and complete and not error and cursor is None
    assert budget.metrics.requests == 3


def test_month_spanning_resumed_scan_checks_the_older_month(monkeypatch):
    months = {'2026-09': [{'ts': SEPTEMBER}], '2026-10': [{'ts': OCTOBER}]}
    session, budget = transport(monkeypatch, [[{'ts': SEPTEMBER}],
        document(months, '2026-10'), document(months, '2026-09')])
    rows, complete, error, cursor = inc.scan(budget, 'C', LOWER, start_before=SEPTEMBER)
    assert rows == [{'ts': SEPTEMBER}] and complete and not error and cursor is None
    assert session.get.call_args.args[0].endswith('/index/channel/C/2026-09')
    assert budget.metrics.requests == 3


@pytest.mark.parametrize('broken', ['missing_row', 'wrong_active', 'wrong_count', 'wrong_channel', 'wrong_id'])
def test_older_month_cannot_hide_missing_rows_or_wrong_identity(monkeypatch, broken):
    months = {'2026-09': [{'ts': SEPTEMBER}], '2026-10': [{'ts': OCTOBER}]}
    if broken == 'missing_row':
        months['2026-09'].append({'ts': '1789571776.000001'})
    if broken == 'wrong_channel':
        months['2026-09'][0]['channel'] = 'PRIVATE'
    old = document(months, '2026-09')
    if broken == 'wrong_active':
        old = document(months, '2026-10')
    elif broken == 'wrong_count':
        old = old.replace('class="badge">1', 'class="badge">2')
    elif broken == 'wrong_id':
        old = old.replace('id="ts-' + SEPTEMBER, 'id="ts-1789571778.383129')
    _, budget = transport(monkeypatch, [[{'ts': SEPTEMBER}], document(months, '2026-10'), old])
    _, complete, error, cursor = inc.scan(budget, 'C', LOWER, start_before=SEPTEMBER)
    assert not complete and 'did not advance' in error and cursor == SEPTEMBER


def test_budget_exhaustion_before_older_month_preserves_pending_cursor(monkeypatch):
    months = {'2026-09': [{'ts': SEPTEMBER}], '2026-10': [{'ts': OCTOBER}]}
    session, budget = transport(monkeypatch, [[{'ts': SEPTEMBER}], document(months, '2026-10')], limit=2)
    _, complete, error, cursor = inc.scan(budget, 'C', LOWER, start_before=SEPTEMBER)
    assert not complete and 'budget exhausted' in error and cursor == SEPTEMBER
    assert session.get.call_count == 2


def test_proof_has_a_fixed_three_month_bound(monkeypatch):
    months = {month: [{'ts': OCTOBER}] for month in ['2026-07', '2026-08', '2026-09', '2026-10']}
    session, budget = transport(monkeypatch, [document(months, '2026-10')])
    assert not archive.channel_window_is_complete(budget, 'C', Decimal('1784000000'), {})
    assert session.get.call_count == 1


def test_empty_json_head_is_verified_against_full_current_month(monkeypatch):
    months = {'2026-10': [{'ts': '1790942215.739429', 'subtype': 'channel_leave'}]}
    _, budget = transport(monkeypatch, [[], [], document(months, '2026-10')])
    assert inc.scan(budget, 'C', Decimal('1792400000')) == ([], True, '', None)
    assert budget.metrics.requests == 3


@pytest.mark.parametrize('broken', ['missing_eligible', 'incomplete_html', 'missing_count'])
def test_empty_json_stays_degraded_when_current_month_cannot_prove_coverage(monkeypatch, broken):
    months = {'2026-10': [{'ts': OCTOBER, 'subtype': 'channel_join'}]}
    if broken == 'missing_eligible':
        months['2026-10'].append({'ts': OCTOBER})
    page = document(months, '2026-10')
    if broken == 'incomplete_html':
        page = page.replace('class="badge">1', 'class="badge">2')
    elif broken == 'missing_count':
        page = page.replace('class="badge"', 'class="unknown"')
    _, budget = transport(monkeypatch, [[], [], page])
    _, complete, error, cursor = inc.scan(budget, 'C', Decimal('1792400000'))
    assert not complete and 'ambiguous empty page' in error and cursor is None


def test_permalink_resolution_accepts_php_stripped_zero_but_still_rejects_ambiguous_ids(monkeypatch):
    page = document({'2026-10': [{'ts': OCTOBER}]}, '2026-10', php_ids=True)
    monkeypatch.setattr(archive, '_get', Mock(return_value=Mock(text=page)))
    assert archive.verified_month_fragments(Mock(), 'C', '2026-10') == {
        'slack:C:' + OCTOBER: 'ts-1792587721.382'}
    page = document({'2026-10': [{'ts': OCTOBER}, {'ts': OCTOBER}]}, '2026-10', php_ids=True)
    monkeypatch.setattr(archive, '_get', Mock(return_value=Mock(text=page)))
    assert archive.verified_month_fragments(Mock(), 'C', '2026-10') == {}
