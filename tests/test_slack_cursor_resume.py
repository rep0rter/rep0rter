"""Synthetic public-archive windows; no production state or transports."""
import html
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from rep0rter.collectors import slack_incremental as inc
from rep0rter.collectors.state import BudgetExceeded, BudgetSession, Metrics, read_state, write_state
from rep0rter.store import Store


LOWER = Decimal('1789500000')
NEW = '1789571778.900005'
OLD = '1789571777.383129'


def month_html(*timestamps):
    rows = ''.join(
        '<div class="message" id="ts-' + ts + '"><span class="message-time" title="'
        + html.escape(json.dumps({'ts': ts, 'text': 'Synthetic civic update'}), quote=True)
        + '"></span></div>' for ts in timestamps)
    return ('<nav role="pagination"><a class="nav-link active" '
            'href="/index/channel/C/2026-09">2026-09</a>'
            '<a class="dropdown-item" href="/index/channel/C/2026-09">'
            f'2026-09<span class="badge">{len(timestamps)}</span></a></nav>'
            '<section role="feed">' + rows + '</section>')


def transport(monkeypatch, pages, *, limit):
    session = Mock(headers={})
    session.get.side_effect = [
        Mock(content=b'{}', json=lambda rows=rows: {'messages': rows})
        if isinstance(rows, list) else Mock(content=rows.encode(), text=rows)
        for rows in pages]
    budget = BudgetSession(session, Metrics(), limit=limit, interval=0)
    monkeypatch.setattr(inc.api, '_get', lambda client, url, **params: client.get(url, params=params))
    return session, budget


def test_one_page_resume_can_prove_complete_without_reloading_newer_rows(monkeypatch):
    _, first = transport(monkeypatch, [[{'ts': NEW}, {'ts': OLD}]], limit=1)
    saved, complete, _, cursor = inc.scan(first, 'C', LOWER, max_pages=1)
    assert not complete and cursor == OLD
    assert {row['ts'] for row in saved} == {NEW, OLD}

    # The newer row was durably observed in the prior scan, not this response.
    session, second = transport(monkeypatch, [[{'ts': OLD}], month_html(NEW, OLD)], limit=2)
    rows, complete, error, cursor = inc.scan(second, 'C', LOWER, max_pages=1, start_before=cursor)
    assert complete and not error and cursor is None
    assert {row['ts'] for row in rows} == {OLD}
    assert session.get.call_count == 2


def test_resume_proof_rejects_an_unseen_older_row_and_retains_cursor(monkeypatch):
    missing = '1789571777.100001'
    _, budget = transport(monkeypatch, [[{'ts': OLD}], month_html(NEW, OLD, missing)], limit=2)
    _, complete, error, cursor = inc.scan(budget, 'C', LOWER, max_pages=1, start_before=OLD)
    assert not complete and 'did not advance' in error
    assert cursor == OLD


def test_resume_html_still_requires_all_older_rows_seen_in_this_scan(monkeypatch):
    middle = '1789571777.100001'
    older = '1789571776.100001'
    _, budget = transport(monkeypatch, [
        [{'ts': middle}], [{'ts': middle}], month_html(NEW, OLD, middle, older)], limit=3)
    rows, complete, error, cursor = inc.scan(budget, 'C', LOWER, start_before=OLD)
    assert {row['ts'] for row in rows} == {middle}
    assert not complete and error and cursor == middle


def test_resume_budget_failure_preserves_previous_coverage(monkeypatch):
    session, budget = transport(monkeypatch, [[{'ts': OLD}]], limit=1)
    _, complete, error, cursor = inc.scan(budget, 'C', LOWER, max_pages=1, start_before=OLD)
    assert not complete and 'budget exhausted' in error and cursor == OLD
    assert session.get.call_count == 1


def test_resume_empty_filtered_page_preserves_cursor(monkeypatch):
    _, budget = transport(monkeypatch, [[]], limit=1)
    rows, complete, error, cursor = inc.scan(budget, 'C', LOWER, start_before=OLD)
    assert rows == [] and not complete and 'ambiguous empty page' in error
    assert cursor == OLD


def test_resume_proof_does_not_cross_months_or_accept_a_lower_coverage_boundary(monkeypatch):
    _, budget = transport(monkeypatch, [[{'ts': OLD}], month_html(NEW, OLD)], limit=2)
    assert not inc.scan(budget, 'C', Decimal('1788000000'), start_before=OLD)[1]
    assert not inc.api.channel_window_is_complete(
        Mock(), 'C', LOWER, {}, covered_after=LOWER - 1)


def test_persisted_single_page_scan_converges_next_run(tmp_path, monkeypatch):
    now = 1789600000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channel = inc.api.ChannelRow('C', 'synthetic', '', '', 10, 2,
                                datetime.fromtimestamp(float(NEW), timezone.utc))
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda _: [channel])
    _, budget = transport(monkeypatch, [[{'ts': NEW, 'text': 'Synthetic civic update'},
                                       {'ts': OLD, 'text': 'Earlier synthetic update'}]], limit=1)
    with Store(tmp_path / 'db') as store:
        inc.collect(store, session=budget)
        pending = read_state(store, 'slack:C')
        assert pending['pending_before'] == OLD and pending['pending_high'] == NEW
        assert 'last_complete_ts' not in pending
        assert not read_state(store, 'slack:health')['healthy']
        assert read_state(store, 'slack:health')['catch_up'] == {
            'resumed_channels': 0, 'cursor_progressed_channels': 1,
            'completed_channels': 0, 'pending_channels': 1}
        assert store.event_count() == 2

        now += 3600
        _, budget = transport(monkeypatch, [[{'ts': OLD, 'text': 'Earlier synthetic update'}],
                                           month_html(NEW, OLD)], limit=2)
        inc.collect(store, session=budget)
        finished = read_state(store, 'slack:C')
        assert finished['last_complete_ts'] == NEW
        assert finished['pending_before'] is None and not finished['gap']
        assert finished['last_success'] == now
        assert store.event_count() == 2
        assert read_state(store, 'slack:health')['healthy']
        assert read_state(store, 'slack:health')['catch_up'] == {
            'resumed_channels': 1, 'cursor_progressed_channels': 0,
            'completed_channels': 1, 'pending_channels': 0}


@pytest.mark.parametrize('failure', [RuntimeError, BudgetExceeded])
@pytest.mark.parametrize('resuming', [False, True])
def test_failed_snapshot_commit_cannot_advance_durable_coverage(tmp_path, monkeypatch, failure, resuming):
    now = 1789600000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channel = inc.api.ChannelRow('C', 'synthetic', '', '', 10, 2,
                                datetime.fromtimestamp(float(NEW), timezone.utc))
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda _: [channel])
    monkeypatch.setattr(inc, 'persist', Mock(side_effect=failure('Synthetic snapshot failure')))
    _, budget = transport(monkeypatch, [[{'ts': '1789400000'}]], limit=1)
    previous = {'last_complete_ts': str(LOWER), 'last_success': 123}
    if resuming:
        previous.update(pending_before=OLD, pending_lower=str(LOWER), pending_high=NEW)
    with Store(tmp_path / 'db') as store:
        with store.conn:
            write_state(store, 'slack:C', previous)
        inc.collect(store, session=budget)
        saved = read_state(store, 'slack:C')
        assert saved['last_complete_ts'] == previous['last_complete_ts']
        assert saved['last_success'] == 123
        for key in ('pending_before', 'pending_lower', 'pending_high'):
            assert saved.get(key) == previous.get(key)
        assert saved['gap'] and 'Synthetic snapshot failure' in saved['error']
        assert not read_state(store, 'slack:health')['healthy']
        assert read_state(store, 'slack:health')['catch_up']['completed_channels'] == 0


def test_partial_event_transaction_rolls_back_before_error_state_is_saved(tmp_path, monkeypatch):
    from rep0rter.collectors import state
    now = 1789600000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channel = inc.api.ChannelRow('C', 'synthetic', '', '', 10, 2,
                                datetime.fromtimestamp(float(NEW), timezone.utc))
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda _: [channel])
    original_write = state.write_state

    def fail_after_events(store, key, candidate):
        assert store.conn.in_transaction
        assert store.event_count() == 1
        assert candidate['last_complete_ts'] == NEW
        raise RuntimeError('Synthetic failure between events and cursor')

    monkeypatch.setattr(state, 'write_state', fail_after_events)
    _, budget = transport(monkeypatch, [[{'ts': NEW, 'text': 'Synthetic civic update'},
                                       {'ts': '1789400000'}]], limit=1)
    with Store(tmp_path / 'db') as store:
        with store.conn:
            original_write(store, 'slack:C', {'last_complete_ts': str(LOWER), 'last_success': 123})
        inc.collect(store, session=budget)
        saved = read_state(store, 'slack:C')
        assert store.event_count() == 0
        assert saved['last_complete_ts'] == str(LOWER) and saved['last_success'] == 123
        assert saved['gap'] and not saved.get('pending_before')
        assert not read_state(store, 'slack:health')['healthy']


def test_uncertain_durable_checkpoint_aborts_without_recoverable_error_state(tmp_path, monkeypatch):
    from rep0rter.runtime import DurabilityError
    now = 1789600000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channel = inc.api.ChannelRow('C', 'synthetic', '', '', 10, 2,
                                datetime.fromtimestamp(float(NEW), timezone.utc))
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda _: [channel])
    monkeypatch.setattr(inc, 'persist', Mock(side_effect=DurabilityError('Synthetic uncertain checkpoint')))
    _, budget = transport(monkeypatch, [[{'ts': NEW}, {'ts': '1789400000'}]], limit=1)
    with Store(tmp_path / 'db') as store:
        with store.conn:
            write_state(store, 'slack:C', {'last_complete_ts': str(LOWER)})
        with pytest.raises(DurabilityError):
            inc.collect(store, session=budget)
        assert read_state(store, 'slack:C') == {'last_complete_ts': str(LOWER)}
        assert read_state(store, 'slack:health') == {}
