"""Migration-only admin client. Reads credentials from a private file, not argv."""
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=('import', 'check', 'collect', 'build', 'rehearse', 'start'))
parser.add_argument('--url', required=True)
parser.add_argument('--token-file', type=Path, default=Path(__file__).parent / '.dev.vars')
parser.add_argument('--archive', type=Path)
args = parser.parse_args()
secret = next(line.split('=', 1)[1].strip() for line in args.token_file.read_text().splitlines()
              if line.startswith('MIGRATION_TOKEN='))
if not args.url.startswith(('https://', 'http://localhost:', 'http://127.0.0.1:')):
    parser.error('Use HTTPS except for local development.')
body = args.archive.read_bytes() if args.archive else b''
request = urllib.request.Request(args.url.rstrip('/') + '/__admin/' + args.action,
    data=body, method='POST', headers={'Authorization': 'Bearer ' + secret,
    'Content-Type': 'application/zip' if args.archive else 'application/json',
    'User-Agent': 'rep0rter-migration/1.0'})
try:
    with urllib.request.urlopen(request, timeout=900) as response:
        print(response.status, response.read().decode())
except urllib.error.HTTPError as error:
    print(error.code, error.read().decode()[:4000])
    raise SystemExit(1)
