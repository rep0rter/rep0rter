"""Read-only verification of the configured Threads identity; never publishes."""
import json
import os

from rep0rter.config import Config
from rep0rter.publishers import threads


def check(cfg):
    if not cfg.threads_access_token or not cfg.threads_user_id:
        return {'authentication': 'blocked', 'reason': 'missing_configuration'}
    try:
        account = threads.get_account(cfg)
    except threads.ThreadsRejected as exc:
        return {'authentication': 'blocked', 'reason': exc.reason,
                'http_status': exc.status, 'error_code': exc.code, 'error_subcode': exc.subcode}
    except Exception:
        return {'authentication': 'unknown', 'reason': 'transport_or_response_error'}
    if str(account.get('id', '')) != cfg.threads_user_id:
        return {'authentication': 'blocked', 'reason': 'account_mismatch'}
    return {'authentication': 'ok', 'account_matches': True}


def main():
    result = check(Config())
    print('Threads authentication: ' + json.dumps(result, sort_keys=True), flush=True)
    if result['authentication'] != 'ok':
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            print('::error title=Threads authentication blocked::' + result['reason'], flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
