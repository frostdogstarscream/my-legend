"""Run only this project's isolated loopback services. Never log environment secrets."""
from __future__ import annotations

import argparse
import json
import msvcrt
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'runtime/my-legend-story'
ENGINE = ROOT / 'source/StoryGenerate-0dec8480e3c1b01af66407ff063f825a3fd5dedb'
PG = Path(r'C:\Program Files\PostgreSQL\16\bin')


def wait_url(url, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if httpx.get(url, timeout=2, trust_env=False).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(.5)
    raise RuntimeError('LOCAL_SERVICE_START_TIMEOUT')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend-only', action='store_true')
    parser.add_argument('--status-only', action='store_true')
    args = parser.parse_args()
    if args.status_only:
        import psycopg
        with psycopg.connect('host=127.0.0.1 port=55439 dbname=postgres user=my_legend_story',
                             password=os.environ['MY_LEGEND_DB_PASSWORD']) as db:
            db.execute('SET TRANSACTION READ ONLY')
            report = {}
            for table in ('service_plan_tasks', 'story_jobs'):
                from psycopg import sql
                report[table] = dict(db.execute(sql.SQL('SELECT status,count(*) FROM my_legend.{} GROUP BY status').format(sql.Identifier(table))).fetchall())
            if db.execute("SELECT to_regclass('my_legend_production.jobs')").fetchone()[0]:
                report['production_jobs'] = dict(db.execute('SELECT status,count(*) FROM my_legend_production.jobs GROUP BY status').fetchall())
            else:
                report['production_namespace'] = 'not_migrated'
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return
    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock = (RUNTIME / 'stack.lock').open('a+b')
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print('This project stack is already running. Open http://127.0.0.1:5193/', flush=True)
        return
    children, handles = [], []
    logs = RUNTIME / 'logs'
    logs.mkdir(exist_ok=True)
    env = dict(os.environ)
    secret = env['MY_LEGEND_SERVICE_TOKEN']
    password = env['MY_LEGEND_DB_PASSWORD']
    database = 'postgresql+psycopg://my_legend_story:' + quote(password, safe='') + '@127.0.0.1:55439/postgres'
    engine_env = {**env, 'STORY_DATABASE_URL': database, 'STORY_DATABASE_SCHEMA': 'my_legend',
                  'STORY_API_KEYS': json.dumps({secret: {'principal_id': 'my-legend-local', 'admin': True}}), 'PYTHONUTF8': '1',
                  'STORY_EVIDENCE_DIR': str(RUNTIME / 'model-evidence')}
    production_env = {
        'MY_LEGEND_PRODUCTION_DSN': 'host=127.0.0.1 port=55439 dbname=postgres user=my_legend_story',
        'MY_LEGEND_PRODUCTION_RUNTIME': str(ROOT / 'runtime/my-legend-production/independent'),
        'MY_LEGEND_PRODUCTION_MODULES': 'story,sound_plan,audio,visual_plan,visual_render,integrate',
        'MY_LEGEND_WORKSPACE': str(ROOT), 'MY_LEGEND_STORY_TOKEN': secret,
        'MY_LEGEND_WORKFLOW_PATHS': str(ROOT / 'runtime/my-legend-production/workflows.json'),
        'MY_LEGEND_MEDIA_PYTHON': str(ROOT / 'runtime/my-legend-production/.venv/Scripts/python.exe'),
        'PGPASSWORD': password,
    }
    engine_env.update(production_env)
    output = (logs / 'startup.log').open('a', encoding='utf-8')
    handles.append(output)

    def run(command, cwd=ENGINE, environment=engine_env):
        result = subprocess.run(command, cwd=cwd, env=environment, stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError('STARTUP_COMMAND_FAILED: ' + Path(command[0]).name)

    def spawn(name, command, cwd=ENGINE, environment=engine_env):
        log = (logs / (name + '.log')).open('a', encoding='utf-8')
        handles.append(log)
        child = subprocess.Popen(command, cwd=cwd, env=environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        children.append((name, child))
        return child

    try:
        data = RUNTIME / 'postgres-data'
        if not (data / 'PG_VERSION').exists():
            if data.exists() and any(data.iterdir()):
                raise RuntimeError('UNINITIALIZED_DATABASE_DIRECTORY_NOT_EMPTY')
            descriptor, pwfile = tempfile.mkstemp(prefix='init-password-', dir=RUNTIME)
            try:
                with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
                    file.write(password + '\n')
                run([str(PG / 'initdb.exe'), '-D', str(data), '-U', 'my_legend_story', '-A', 'scram-sha-256', '--encoding=UTF8', '--locale=C', '--pwfile=' + pwfile])
            finally:
                Path(pwfile).unlink(missing_ok=True)
        status = subprocess.run([str(PG / 'pg_ctl.exe'), '-D', str(data), 'status'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        if status.returncode:
            run([str(PG / 'pg_ctl.exe'), '-D', str(data), '-l', str(logs / 'postgres.log'), '-o', '-h 127.0.0.1 -p 55439', '-t', '30', 'start'])
        print('Independent PostgreSQL ready on loopback port 55439.', flush=True)
        run([sys.executable, '-m', 'drama_engine.service', 'migrate'])
        run([sys.executable, '-m', 'my_legend_production.production.cli', 'migrate'], cwd=ROOT)
        spawn('story-api', [sys.executable, '-m', 'drama_engine.service', 'serve', '--host', '127.0.0.1', '--port', '8897'])
        wait_url('http://127.0.0.1:8897/health')
        from my_legend_production.story_api import CHARACTERS
        with httpx.Client(base_url='http://127.0.0.1:8897', headers={'Authorization': 'Bearer ' + secret}, trust_env=False, timeout=20) as client:
            values = [{'character_id': c['id'], 'expected_version': 0, 'canon': {'name': c['name'], 'identity': c['description']},
                       'runtime_defaults': {'personality': c['tags']}, 'assets': {}} for c in CHARACTERS]
            result = client.post('/v1/characters/import', json={'characters': values})
            if result.status_code != 200:
                raise RuntimeError('LOCAL_CHARACTER_IMPORT_FAILED')
        spawn('story-worker', [sys.executable, str(Path(__file__).with_name('run_story_worker.py'))])
        bff_env = {k: v for k, v in env.items() if not any(x in k for x in ('API_KEY', 'DB_PASSWORD', 'SERVICE_TOKEN', 'PGPASSWORD', 'DATABASE_URL'))}
        bff_env.update(MY_LEGEND_STORY_RUNTIME=str(RUNTIME / 'app'), MY_LEGEND_STORY_TOKEN=secret, PYTHONUTF8='1')
        bff_env.update(production_env)
        worker_env = {**bff_env, 'MY_LEGEND_VISUAL_MODEL_KEY': env['DRAMA_LLM_API_KEY'],
                      'MY_LEGEND_VISUAL_MODEL_BASE_URL': env['STORY_LLM_BASE_URL'],
                      'MY_LEGEND_VISUAL_MODEL_ID': env['MY_LEGEND_VISUAL_MODEL_ID'],
                      'MY_LEGEND_AUDIO_MODEL_KEY': env['DRAMA_LLM_API_KEY'],
                      'MY_LEGEND_AUDIO_MODEL_BASE_URL': env['STORY_LLM_BASE_URL'],
                      'MY_LEGEND_AUDIO_MODEL_ID': env['MY_LEGEND_AUDIO_MODEL_ID']}
        spawn('production-worker', [sys.executable, '-m', 'my_legend_production.production.cli', 'worker'], cwd=ROOT, environment=worker_env)
        spawn('app-api', [sys.executable, '-m', 'my_legend_production.story_api'], cwd=ROOT, environment=bff_env)
        wait_url('http://127.0.0.1:8895/health')
        if not args.backend_only:
            # Preserve an already-running Vite instance; changing .env.local is reloaded by Vite.
            try:
                page = httpx.get('http://127.0.0.1:5193/', timeout=2, trust_env=False)
                existing = page.status_code == 200
                if existing and '我的传说' not in page.text:
                    raise RuntimeError('FRONTEND_PORT_USED_BY_ANOTHER_PROJECT')
            except httpx.HTTPError:
                existing = False
            if not existing:
                node = Path(r'D:\Develop\nodejs\node.exe')
                cli = ROOT / 'source/fingertip-legend-app/node_modules/@dcloudio/vite-plugin-uni/bin/uni.js'
                frontend_env = {k: v for k, v in bff_env.items() if not any(x in k for x in ('TOKEN', 'PASSWORD', 'SECRET', 'API_KEY', 'DSN', 'DATABASE_URL'))}
                spawn('frontend', [str(node), str(cli), '--host', '127.0.0.1', '--port', '5193'], cwd=ROOT / 'source/fingertip-legend-app', environment=frontend_env)
            wait_url('http://127.0.0.1:5193/')
        (RUNTIME / 'processes.json').write_text(json.dumps({'supervisor': os.getpid(), 'children': {name: proc.pid for name, proc in children}}, indent=2))
        print('Story API, Worker and prototype API ready. Open http://127.0.0.1:5193/ ; press Ctrl+C to stop this stack.', flush=True)
        while True:
            for name, child in children:
                if child.poll() is not None:
                    raise RuntimeError('CHILD_EXITED: ' + name)
            time.sleep(1)
    except KeyboardInterrupt:
        print('Stopping only the child services started by this supervisor.', flush=True)
    except Exception as exc:
        print('Story stack stopped: ' + type(exc).__name__ + '. Inspect project logs without sharing credentials.', flush=True)
        raise SystemExit(2) from None
    finally:
        for _, child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
        for handle in handles:
            handle.close()
        lock.close()


if __name__ == '__main__':
    main()
