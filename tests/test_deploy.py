"""Exercise the production deploy controller without Docker, GitHub or secrets."""

import importlib.util
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest


spec = importlib.util.spec_from_file_location('deployment', Path(__file__).parents[1] / 'scripts/deploy.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)

SHA = 'a' * 40
REPOSITORY = 'rep0rter/rep0rter'


def workflow(**updates):
    return dict(dict(id=1, head_sha=SHA, head_branch='main', event='push',
                     head_repository={'full_name': REPOSITORY},
                     status='completed', conclusion='success'), **updates)


@pytest.mark.parametrize('updates', [
    {'head_sha': 'b' * 40}, {'head_branch': 'feature'}, {'event': 'pull_request'},
    {'head_repository': {'full_name': 'someone/fork'}}, {'status': 'in_progress'},
    {'conclusion': 'failure'}, {'conclusion': 'cancelled'}, {'conclusion': 'skipped'},
])
def test_ci_rejects_unqualified_runs(updates):
    assert not deployment.ci_passed([workflow(**updates)], SHA, REPOSITORY)


def test_ci_requires_success_of_latest_matching_run():
    assert deployment.ci_passed([workflow()], SHA, REPOSITORY)
    assert not deployment.ci_passed([], SHA, REPOSITORY)
    assert not deployment.ci_passed([workflow(), workflow(id=2, status='in_progress')], SHA, REPOSITORY)


@pytest.fixture
def deployer(tmp_path, monkeypatch):
    item = deployment.Deployer(dict(state_dir=str(tmp_path), env_file='/private/.env',
                                     data_dir='/production/data', source_dir='/source',
                                     minimum_sha='baseline', repository=REPOSITORY))
    monkeypatch.setattr(deployment, 'run', Mock(return_value=''))
    item.fetch = Mock(return_value=SHA)
    item.eligible = Mock(return_value=True)
    return item


@pytest.mark.parametrize('state', [{'deployed_sha': SHA}, {'failed_sha': SHA}])
def test_unchanged_or_failed_commit_is_not_redeployed(deployer, state):
    deployer.state = state
    deployer.deploy = Mock()
    deployer.tick()
    deployer.deploy.assert_not_called()


def test_pending_ci_and_check_mode_do_not_deploy(deployer):
    deployer.deploy = Mock()
    deployer.eligible.return_value = False
    deployer.tick()
    deployer.eligible.return_value = True
    deployer.tick(check=True)
    deployer.deploy.assert_not_called()


def test_ci_api_failure_and_older_main_leave_production_unchanged(deployer, monkeypatch):
    deployer.deploy = Mock()
    deployer.eligible.side_effect = RuntimeError('GitHub unavailable')
    with pytest.raises(RuntimeError, match='GitHub unavailable'):
        deployer.tick()
    monkeypatch.setattr(deployment, 'run', Mock(side_effect=subprocess.CalledProcessError(1, 'git')))
    with pytest.raises(subprocess.CalledProcessError):
        deployer.tick()
    deployer.deploy.assert_not_called()


def test_failed_commit_requires_explicit_retry(deployer):
    deployer.state = {'failed_sha': SHA}
    deployer.deploy = Mock()
    deployer.tick(retry=True)
    deployer.deploy.assert_called_once_with(SHA)


def prepare_deployment(deployer):
    deployer.compose = Mock()
    deployer.freeze = Mock(side_effect=lambda source, destination, **kwargs:
                           deployment.write_json(destination, {'services': {}}))
    deployer.stop_writers = Mock()
    deployer.render = Mock()
    deployer.start = Mock()


def test_newer_push_during_build_leaves_production_running(deployer):
    prepare_deployment(deployer)
    deployer.fetch.return_value = 'newer'
    deployer.deploy(SHA)
    deployer.stop_writers.assert_not_called()
    assert 'deployed_sha' not in deployer.state


def test_success_records_commit_after_health_checks(deployer):
    prepare_deployment(deployer)
    def healthy(config):
        assert deployer.state['pending']['sha'] == SHA
        assert 'deployed_sha' not in deployer.state
    deployer.start.side_effect = healthy
    deployer.deploy(SHA)
    assert deployer.state['deployed_sha'] == SHA
    assert 'pending' not in deployer.state
    assert (deployer.root / 'current.json').stat().st_mode & 0o777 == 0o600


def test_health_failure_rolls_back_and_blocks_same_commit(deployer):
    prepare_deployment(deployer)
    deployer.start.side_effect = [RuntimeError('unhealthy'), None]
    with pytest.raises(RuntimeError, match='unhealthy'):
        deployer.tick()
    assert deployer.start.call_count == 2
    assert 'previous-' in str(deployer.start.call_args.args[0])
    assert deployer.state['failed_sha'] == SHA
    assert 'pending' not in deployer.state
    assert 'deployed_sha' not in deployer.state


def test_build_failure_does_not_stop_production(deployer):
    prepare_deployment(deployer)
    deployer.compose.side_effect = RuntimeError('build failed')
    with pytest.raises(RuntimeError, match='build failed'):
        deployer.tick()
    deployer.stop_writers.assert_not_called()
    assert deployer.state['failed_sha'] == SHA


def test_backup_failure_does_not_stop_production(deployer):
    prepare_deployment(deployer)
    def compose(config, *args, **kwargs):
        if args[0] == 'exec':
            raise RuntimeError('backup failed')
    deployer.compose.side_effect = compose
    with pytest.raises(RuntimeError, match='backup failed'):
        deployer.tick()
    deployer.stop_writers.assert_not_called()
    assert 'pending' not in deployer.state


def test_interrupted_deployment_recovers_before_fetch(deployer):
    prepare_deployment(deployer)
    old = deployer.root / 'previous.json'
    deployment.write_json(old, {'services': {'old': {}}})
    deployer.state = {'deployed_sha': 'old', 'pending': {
        'sha': SHA, 'previous': str(old), 'candidate': 'new.json',
    }}
    deployer.tick()
    assert deployer.state['deployed_sha'] == 'old'
    assert deployer.state['failed_sha'] == SHA
    assert json.loads((deployer.root / 'current.json').read_text()) == {'services': {'old': {}}}
    deployer.fetch.assert_called_once()


def test_failed_rollback_keeps_recovery_journal(deployer):
    prepare_deployment(deployer)
    deployer.state['pending'] = {'sha': SHA, 'previous': 'old.json', 'candidate': 'new.json'}
    deployer.start.side_effect = RuntimeError('rollback failed')
    with pytest.raises(RuntimeError, match='rollback failed'):
        deployer.tick()
    assert 'pending' in deployer.state
    deployer.fetch.assert_not_called()


def test_snapshot_uses_running_image_and_does_not_keep_build_recipe(deployer, monkeypatch):
    config = {'services': {'worker': {'build': {'context': '/release'}, 'image': 'rep0rter',
                                      'pull_policy': 'always'}}}
    deployer.compose = Mock(side_effect=[json.dumps(config), 'container-id'])
    run = Mock(side_effect=['sha256:old-image', None])
    monkeypatch.setattr(deployment, 'run', run)
    target = deployer.root / 'snapshot.json'
    deployer.freeze('compose.yaml', target, running=True)
    assert json.loads(target.read_text())['services']['worker'] == {'image': 'sha256:old-image'}
