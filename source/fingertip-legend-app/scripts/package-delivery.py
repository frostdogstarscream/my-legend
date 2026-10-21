"""Create a non-overwriting source + compiled-resource archive for this project."""
import argparse
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = args.output.resolve()
output.parent.mkdir(parents=True, exist_ok=True)

excluded_dirs = {'node_modules', '.git', 'evidence', '__pycache__'}
included = []
for path in root.rglob('*'):
    relative = path.relative_to(root)
    if not path.is_file() or excluded_dirs.intersection(relative.parts):
        continue
    if relative.parts[0] == 'dist' and relative.parts[:2] != ('dist', 'build'):
        continue
    if path.name.startswith('.env') and path.name != '.env.example':
        continue
    if path.suffix == '.log':
        continue
    included.append(path)

with ZipFile(output, 'x', compression=ZIP_DEFLATED, compresslevel=6) as archive:
    for path in sorted(included):
        archive.write(path, (Path('my-legend-app') / path.relative_to(root)).as_posix())
with ZipFile(output) as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError(f'Archive CRC failed: {bad}')
    required = ['package.json', 'package-lock.json', 'src/pages/login/index.vue', 'src/pages/home/index.vue', 'src/pages/performance/index.vue', 'src/pages/reader/index.vue', 'docs/后续功能接入说明.md', 'dist/build/h5/index.html', 'dist/build/app/manifest.json', 'dist/build/mp-weixin/app.json']
    names = set(archive.namelist())
    for name in required:
        if f'my-legend-app/{name}' not in names:
            raise RuntimeError(f'Missing delivery file: {name}')
print(f'Created {output.name}: {len(included)} files, {output.stat().st_size} bytes; CRC OK')
