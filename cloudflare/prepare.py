"""Build a credential-free source tree for pywrangler."""
from pathlib import Path
import shutil
import subprocess

here = Path(__file__).resolve().parent
root = here.parent
target = here / '.build' / 'src'
if target.exists():
    shutil.rmtree(target)
target.mkdir(parents=True)
for folder in ('rep0rter', 'assets'):
    shutil.copytree(root / folder, target / folder,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
for path in (here / 'src').glob('*.py'):
    shutil.copy2(path, target / path.name)
revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
(target / 'revision.py').write_text('SOURCE_COMMIT = ' + repr(revision) + '\n')
print('Prepared Cloudflare source (application and assets only).')
