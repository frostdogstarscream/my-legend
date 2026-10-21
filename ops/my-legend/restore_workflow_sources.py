"""Restore the checked-in pinned workflow snapshot into a new runtime only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def restore(workspace, runtime):
    source = workspace / 'source/my-legend-workflow-dependencies'
    lock = json.loads((source/'dependency-lock.json').read_bytes())
    for name, digest in lock['files'].items():
        relative = Path(name).relative_to('dependencies')
        path = (source/relative).resolve()
        if not path.is_relative_to(source.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('WORKFLOW_SNAPSHOT_HASH_MISMATCH: ' + name)
    target = runtime/'dependencies'
    if target.exists():
        raise FileExistsError('Existing dependency directory is preserved: ' + str(target))
    target.mkdir(parents=True, exist_ok=False)
    for name in ('audio','visual'):
        shutil.copytree(source/name, target/name)
    print(json.dumps({'restored':str(target),'verified_files':len(lock['files']),
                      'next':'Install dependencies and run source/my-legend-production/tools/configure_runtime.py'},ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--runtime',type=Path)
    args=parser.parse_args()
    workspace=Path(__file__).resolve().parents[2]
    restore(workspace,(args.runtime or workspace/'runtime/my-legend-production').resolve())
