"""The public talk is served by the same paths locally and on Workers."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from rep0rter.config import Config
from rep0rter.web import create_app


@pytest.mark.parametrize('path', ['/ppt', '/ppt/', '/ppt.html'])
def test_presentation_local_aliases(tmp_path, path):
    cfg = Config(data_dir=tmp_path, google_client_id='', google_client_secret='')
    cfg.ensure_dirs()
    content = '<!doctype html><html lang="en"><title>rep0rter</title></html>'
    (cfg.site_dir / 'ppt.html').write_text(content)
    client = create_app(cfg).test_client()
    response = client.get(path)
    assert response.status_code == 200
    assert response.mimetype == 'text/html'
    assert response.text == content
    assert client.head(path).data == b''


def test_presentation_worker_aliases():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required to exercise the Worker routing function')
    source = (Path(__file__).resolve().parents[1] / 'cloudflare/web/index.js').read_text()
    source = source.replace("import { DurableObject, WorkerEntrypoint } from 'cloudflare:workers';", '')
    source = source.replace('export default class ', 'class ').replace('export class ', 'class ')
    script = '''
class DurableObject { constructor(ctx) { this.ctx = ctx; } }
class WorkerEntrypoint {}
''' + source + '''
const site = new PublishedSite({storage:{sql:{exec(query,path){
  return query.startsWith('SELECT data') && path === 'ppt.html'
    ? [{data:'<html>rep0rter</html>',digest:'talk'}] : [];
}}}}, {});
const result = [];
for (const path of ['/ppt','/ppt/','/ppt.html']) {
  const response = await site.fetch(new Request('https://example.test'+path));
  result.push({status:response.status, type:response.headers.get('Content-Type'), text:await response.text()});
}
console.log(JSON.stringify(result));
'''
    result = subprocess.run([node, '--input-type=module', '-e', script], capture_output=True, text=True, check=True)
    for response in json.loads(result.stdout):
        assert response['status'] == 200
        assert response['type'].startswith('text/html')
        assert response['text'] == '<html>rep0rter</html>'
