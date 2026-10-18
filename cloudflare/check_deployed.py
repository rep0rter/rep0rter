"""Fail before installing Chromium when tested main is not deployed yet."""
import json
import os
import time
import urllib.error
import urllib.request


def check(expected, health):
    deployed = health.get('source_commit')
    if deployed != expected:
        raise RuntimeError(
            f'Deployment drift: Actions commit {expected}, Worker commit {deployed}. '
            'Deploy tested main with python3 cloudflare/deploy.py --production, '
            'then dispatch Production reporting again. Do not bypass the commit guard.')
    if not health.get('ready') or health.get('runtime') != 'cloudflare-workers':
        raise RuntimeError('The native Worker is not ready for reporting.')
    if health.get('scheduler') != 'github-actions' or not health.get('scheduled'):
        raise RuntimeError('The Worker must enable the GitHub Actions runner.')


def read_health():
    # The frontend health record describes the last publication. It can retain
    # the previous SHA until the first cycle after a new engine deployment.
    request = urllib.request.Request('https://rep0rter-engine.sky-hong.workers.dev/healthz',
                                     headers={'Cache-Control': 'no-cache',
                                              'User-Agent': 'rep0rter-github-runner/1.0'})
    # A single transient Worker/edge failure must not discard the hourly cycle.
    # Only this public GET is retried; commit/readiness checks and reporting are
    # never replayed here. Request timeouts and the fixed attempt limit prevent
    # an unbounded retry loop; persistent failures stop before Chromium setup.
    for attempt, delay in enumerate((0, 2, 5), start=1):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read(65537)
            if len(body) > 65536:
                raise RuntimeError('Worker health response exceeds the preflight limit.')
            return json.loads(body)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code not in (500, 502, 503, 504):
                raise RuntimeError(f'Worker health preflight failed with HTTP {code}.') from None
            failure = f'HTTP {code}'
        except (TimeoutError, ConnectionResetError, urllib.error.URLError) as error:
            if isinstance(error, urllib.error.URLError) and not isinstance(error.reason, (TimeoutError, ConnectionResetError)):
                raise RuntimeError('Worker health preflight transport failed.') from None
            failure = 'temporary transport failure'
        if attempt == 3:
            raise RuntimeError(f'Worker health preflight failed after 3 attempts ({failure}).') from None
        print(f'Worker health preflight attempt {attempt}: {failure}; retrying public GET.')


def main():
    health = read_health()
    check(os.environ['GITHUB_SHA'], health)
    print('Tested Actions commit matches the ready native Worker:', os.environ['GITHUB_SHA'])


if __name__ == '__main__':
    main()
