"""Code for Korea CLI preset uses the shared feed import workflow."""
import argparse

from rep0rter import korea
from rep0rter.config import Config


def test_korea_preset_passes_sources_and_language(monkeypatch, tmp_path):
    parser = argparse.ArgumentParser()
    korea.register_commands(parser.add_subparsers(required=True))
    args = parser.parse_args(['import-korea', '--days', '30', '--limit', '3', '--summarize'])
    cfg = Config(data_dir=tmp_path)
    calls = []

    def run_import(config, options, **kwargs):
        calls.append((config, options, kwargs))
        return 0

    monkeypatch.setattr(korea, 'run_import', run_import)
    assert args.func(cfg, args) == 0
    config, options, preset = calls[0]
    assert config is cfg
    assert (options.days, options.limit, options.summarize) == (30, 3, True)
    assert preset == dict(feeds=korea.FEEDS, language='ko', community='codeforkorea')
