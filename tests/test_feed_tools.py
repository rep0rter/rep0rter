"""Public feed probing stays bounded, read-only, and explicitly user-directed."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from rep0rter import cli, feed_tools
from rep0rter.collectors import rss

URL = 'https://example.org/feed'
BODY = b'''<rss version="2.0"><channel><title>Community</title><item>
<title>Public data workshop</title><link>https://example.org/project</link>
<pubDate>Fri, 18 Sep 2026 09:00:00 +0900</pubDate>
</item></channel></rss>'''


def response(body=BODY, status=200, headers=None, url=URL):
    return SimpleNamespace(content=body, status_code=status, headers=headers or {},
                           url=url, raise_for_status=lambda: None)


def transport(body=BODY):
    return Mock(get=Mock(return_value=response(body)))


def test_probe_parses_original_publication_date_without_followups():
    session = transport()
    report, code = feed_tools.probe(URL, session=session)
    assert code == 0 and report['status'] == 'ok'
    assert report['count'] == 1 and report['format'] == 'rss2'
    assert report['latest'] == '2026-09-18T00:00:00+00:00'
    assert report['sample'][0]['title'] == 'Public data workshop'
    assert report['source'] == 'Community'
    session.get.assert_called_once_with(URL, timeout=30)


def test_discovery_only_reports_five_unique_advertised_public_links():
    html = '''<html><head>
    <link rel="alternate" type="application/rss+xml" href="file:///secret">
    <link rel="alternate" type="application/rss+xml" href="http://127.0.0.1/feed">
    <link rel="alternate" type="application/rss+xml" href="https://a:b@example.org/feed">
    <link rel="stylesheet" type="application/rss+xml" href="/style">
    <link rel="alternate" type="text/html" href="/ordinary">
    <link rel="alternate" type="application/atom+xml" href="/atom">
    <link rel="alternate" type="application/atom+xml" href="/atom">
    ''' + ''.join(f'<link rel="alternate" type="application/feed+json" href="/{i}">' for i in range(9)) + '</head></html>'
    session = transport(html.encode())
    report, code = feed_tools.probe(URL, session=session)
    assert code == 0 and report['status'] == 'discovered'
    assert len(report['discovered']) == 5
    assert report['discovered'][0]['url'] == 'https://example.org/atom'
    assert session.get.call_count == 1


@pytest.mark.parametrize('url', ['file:///tmp/secret', 'https://user:secret@example.org/',
                                'http://localhost/feed', 'http://192.168.1.1/feed',
                                'https://example.org:bad/feed'])
def test_invalid_url_never_fetches_or_echoes_credentials(url):
    session = transport()
    report, code = feed_tools.probe(url, session=session)
    assert code == 1 and 'url' not in report
    assert 'secret' not in json.dumps(report)
    session.get.assert_not_called()


def test_notion_uses_existing_collector_without_probe_http():
    session = transport()
    report, code = feed_tools.probe('https://community.notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122', session=session)
    assert code == 0 and report['status'] == 'supported'
    assert report['count'] is None and report['format'] == 'notion'
    session.get.assert_not_called()
    assert feed_tools.probe('https://community.notion.site/Home', session=session)[1] == 1


def test_odf_uses_browser_for_existing_scoped_collector_only():
    session = transport()
    session.get_browser.return_value = response(BODY.replace(b'https://example.org/project', b'https://www.odf.or.kr/archive-project/?idx=1'))
    report, code = feed_tools.probe(rss.ODF_PROJECTS, session=session)
    assert code == 0 and report['count'] == 1
    session.get_browser.assert_called_once_with(rss.ODF_RSS, timeout=30)
    session.get.assert_not_called()


def test_only_one_redirect_is_followed():
    session = transport()
    session.get.side_effect = [response(status=301, headers={'Location': '/new'}), response()]
    assert feed_tools.probe(URL, session=session)[1] == 0
    assert session.get.call_args_list[1].args == ('https://example.org/new',)
    session.get.side_effect = [response(status=301, headers={'Location': '/new'})] * 2
    assert feed_tools.probe(URL, session=session)[1] == 1


def test_redirect_to_local_or_credentials_is_rejected():
    session = transport()
    session.get.return_value = response(status=302, headers={'Location': 'http://127.0.0.1/secret'})
    assert feed_tools.probe(URL, session=session)[1] == 1
    assert session.get.call_count == 1


def test_failures_are_json_ready_and_do_not_echo_transport_details():
    session = transport(b'not a feed')
    assert feed_tools.probe(URL, session=session)[1] == 1
    session.get.side_effect = requests.RequestException('sensitive credential detail')
    report, code = feed_tools.probe(URL, session=session)
    assert code == 1 and 'sensitive' not in json.dumps(report)
    session.get.side_effect = None
    session.get.return_value = response(b'x' * (feed_tools.MAX_BYTES + 1))
    assert feed_tools.probe(URL, session=session)[1] == 1


def test_bounded_session_stops_reading_large_bodies(monkeypatch):
    result = Mock(iter_content=Mock(return_value=iter([b'x' * feed_tools.MAX_BYTES, b'x'])))
    send = Mock(return_value=result)
    monkeypatch.setattr(requests.Session, 'get', send)
    with feed_tools._BoundedSession() as session:
        assert session.trust_env is False
        with pytest.raises(ValueError, match='2 MiB'):
            session.get(URL, timeout=30)
    assert send.call_args.kwargs['allow_redirects'] is False
    result.close.assert_called_once()


def test_empty_feed_is_valid():
    report, code = feed_tools.probe(URL, session=transport(b'<rss version="2.0"><channel><title>Quiet</title></channel></rss>'))
    assert code == 0 and report['count'] == 0 and report['latest'] is None
    assert report['format'] == 'rss2'


def test_cli_catalog_and_probe_do_not_create_data_directory(monkeypatch, tmp_path, capsys):
    path = tmp_path / 'catalog.json'
    entries = [{'id': 'example', 'url': URL}]
    path.write_text(json.dumps(entries))
    monkeypatch.setattr(feed_tools, 'CATALOG_PATH', path)
    assert cli.main(['feeds', 'catalog']) == 0
    assert json.loads(capsys.readouterr().out) == entries
    monkeypatch.setattr(feed_tools, 'probe', lambda url: ({'status': 'ok'}, 0))
    assert cli.main(['feeds', 'probe', URL]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'ok'
    assert not (tmp_path / 'data').exists()


def test_field_guide_uses_recognized_endpoint_and_preserves_day_precision():
    from rep0rter.collectors.civictech_guide import ENDPOINT
    payload = {'meta': {'total': 1}, 'data': [{
        'id': '66f4698a-8417-46b6-bafa-c34fc00d584a', 'slug': 'civic-project',
        'title': 'Civic project', 'status_raw': 'Active', 'added': '2026-09-18',
    }]}
    session = transport(json.dumps(payload).encode())
    report, code = feed_tools.probe('https://civictech.guide/', session=session)
    assert code == 0 and report['format'] == 'civictech-guide-json'
    assert report['latest'] == '2026-09-18'
    session.get.assert_called_once_with(ENDPOINT, timeout=30)
