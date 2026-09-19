"""Browser document requests must stay within their one-request reservation."""
from unittest.mock import MagicMock, Mock

import pytest

from rep0rter.collectors import browser


@pytest.mark.parametrize('navigation_fails', [False, True])
def test_browser_only_allows_one_main_document_and_always_closes(monkeypatch, navigation_fails):
    playwright = MagicMock()
    monkeypatch.setattr(browser, 'sync_playwright', lambda: playwright)
    chromium = playwright.__enter__.return_value.chromium
    instance = chromium.launch.return_value
    context = instance.new_context.return_value
    page = context.new_page.return_value
    url = 'https://www.odf.or.kr/rss'
    routes = []

    def navigate(*args, **kwargs):
        handler = context.route.call_args.args[1]
        for target, frame, navigation in [
            ('https://example.test/script.js', page.main_frame, False),
            (url, object(), True),
            (url, page.main_frame, True),
            (url, page.main_frame, True),  # A second document or redirect is not free.
        ]:
            route = Mock()
            route.request.url = target
            route.request.method = 'GET'
            route.request.frame = frame
            route.request.is_navigation_request.return_value = navigation
            handler(route)
            routes.append(route)
        if navigation_fails:
            raise RuntimeError('navigation failed')
        return Mock(status=429, url=url, headers={'Retry-After': '3600'}, body=lambda: b'rate limited')

    page.goto.side_effect = navigate
    if navigation_fails:
        with pytest.raises(RuntimeError, match='navigation failed'):
            browser.get_document(url)
    else:
        response = browser.get_document(url)
        assert response.status_code == 429 and response.content == b'rate limited'
        assert response.headers['Retry-After'] == '3600'
    assert [route.continue_.call_count for route in routes] == [0, 0, 1, 0]
    assert [route.abort.call_count for route in routes] == [1, 1, 0, 1]
    context.new_page.assert_called_once()
    instance.new_context.assert_called_once_with(java_script_enabled=False, service_workers='block')
    instance.close.assert_called_once()
