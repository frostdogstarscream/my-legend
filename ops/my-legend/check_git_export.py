"""Inspect tracked/candidate files without printing credential values."""
import json
import argparse
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--staged', action='store_true', help='Scan Git index bytes instead of working files.')
args = parser.parse_args()
paths = subprocess.check_output(['git', 'ls-files', '-z', '--cached'] + ([] if args.staged else ['--others', '--exclude-standard']), cwd=root).decode().split('\0')
patterns = {
    'provider_key': re.compile(r'\bsk-[A-Za-z0-9_.-]{20,}'),
    'github_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----'),
    'literal_bearer': re.compile(r'Bearer\s+[A-Za-z0-9_-]{32,}'),
    'environment_default': re.compile(r'''(?i)\bsetdefault\(\s*["'][^"']*(?:API_KEY|TOKEN|SECRET|PASSWORD)["']\s*,\s*["']([^"'\r\n]{20,})["']'''),
    'literal_credential': re.compile(r'''(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)["']?\s*[:=]\s*["']([^"'\r\n]{20,})["']'''),
}
findings=[]; sizes=[]
for name in sorted(set(filter(None,paths))):
    path=root/name
    if not args.staged and not path.is_file(): continue
    data = subprocess.check_output(['git', 'show', ':' + name], cwd=root) if args.staged else path.read_bytes()
    sizes.append((len(data),name))
    if len(data)>50*1024*1024:findings.append(dict(path=name,kind='large_file'))
    try:text=data.decode('utf-8')
    except (UnicodeDecodeError, OSError):continue
    for kind,pattern in patterns.items():
        for match in pattern.finditer(text):
            if kind in {'literal_credential', 'environment_default'} and (match.group(1).startswith('${') or match.group(1) in {
                    'isolated-fixture-only', 'private-fixture-token', 'fake-secret-not-to-store'}):
                continue
            findings.append(dict(path=name,line=text.count('\n',0,match.start())+1,kind=kind))
report=dict(files=len(sizes),bytes=sum(n for n,_ in sizes),largest=[dict(bytes=n,path=p) for n,p in sorted(sizes,reverse=True)[:12]],findings=findings)
print(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(bool(findings))
