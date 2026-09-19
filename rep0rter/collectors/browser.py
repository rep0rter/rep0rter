"""Single-document browser transport for explicitly configured public feeds."""
import os

import requests
from playwright.sync_api import sync_playwright


def get_document(url, timeout=30):
    # No cookies, credentials, scripts, subresources, or cross-origin redirects.
    # One permitted document request is charged by BudgetSession.
    with sync_playwright() as playwright:
        options = {'headless': True}
        if os.getenv('REP0RTER_CHROME_PATH'):
            options['executable_path'] = os.environ['REP0RTER_CHROME_PATH']
        browser = playwright.chromium.launch(**options)
        try:
            context = browser.new_context(java_script_enabled=False, service_workers='block')
            page = context.new_page()
            sent = False

            def route_document(route):
                nonlocal sent
                request = route.request
                if (not sent and request.url == url and request.method == 'GET'
                        and request.is_navigation_request() and request.frame == page.main_frame):
                    sent = True
                    route.continue_()
                else:
                    route.abort()

            context.route('**/*', route_document)
            document = page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
            if document is None:
                raise ValueError('Browser feed returned no document')
            response = requests.Response()
            response.status_code = document.status
            response.url = document.url
            response.headers.update(document.headers)
            response._content = document.body()
            return response
        finally:
            browser.close()
