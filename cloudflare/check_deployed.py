"""Fail before installing Chromium when tested main is not deployed yet."""
import json
import os
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


def main():
    request = urllib.request.Request('https://rep0rter.observe.tw/healthz',
                                     headers={'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=30) as response:
        health = json.load(response)
    check(os.environ['GITHUB_SHA'], health)
    print('Tested Actions commit matches the ready native Worker:', os.environ['GITHUB_SHA'])


if __name__ == '__main__':
    main()
