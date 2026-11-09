"""Flow-only background workers and coordinator heartbeat."""
import argparse
import json
import os
from pathlib import Path
import time
import logging

from .cli import services
from .settings import ProductionSettings
from .registry import handlers
from .repository import ProductionRepository
from .worker import ProductionWorker
from .flows import FlowService


class FlowRepository(ProductionRepository):
    def claim(self, worker_id, lease_seconds=30, stage_names=None):
        return super().claim(worker_id,lease_seconds,stage_names,flow_only=True)


def heartbeat_path(name):
    return ProductionSettings.environment().root / ('flow-'+name+'.json')


def mark(name):
    path=heartbeat_path(name);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(pid=os.getpid(),time=time.time())),encoding='utf-8')
    temp.replace(path)


def readiness():
    statuses={}
    for name in ('coordinator','sound','stage','story'):
        try:
            value=json.loads(heartbeat_path(name).read_text(encoding='utf-8'))
            statuses[name]=time.time()-value['time']<30
        except (OSError,ValueError,KeyError):statuses[name]=False
    try:
        import httpx
        statuses['story_api']=httpx.get(ProductionSettings.environment().story_url+'/health',timeout=1,trust_env=False).status_code==200
    except Exception:statuses['story_api']=False
    try:
        from .h5_preview import load_h5_catalog
        from .spine_preview import local_root
        catalog=load_h5_catalog(local_root())
        statuses['assets']=catalog.get('actor_model') in ('fixed_commander/1','mixed_life/1')
    except Exception:statuses['assets']=False
    return dict(configured=all(statuses.values()),services=statuses,max_stage_roles=3,
                catalog_sha256=catalog['catalog_sha256'] if statuses.get('assets') else None)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('role',choices=['coordinator','sound','stage']);args=parser.parse_args()
    log_dir=ProductionSettings.environment().root/'logs';log_dir.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=str(log_dir/('flow-'+args.role+'.log')),level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s',encoding='utf-8')
    repo,store=services();repo=FlowRepository(repo.dsn,repo.schema)
    if args.role=='coordinator':
        service=FlowService(repo,store)
        while True:
            mark(args.role)
            try:service.tick()
            except Exception as exc:logging.error('FLOW_COORDINATOR_ERROR type=%s',type(exc).__name__)
            time.sleep(2)
    else:
        import threading
        def pulse():
            while True:mark(args.role);time.sleep(5)
        registry=handlers(ProductionSettings.environment())
        threading.Thread(target=pulse,daemon=True).start()
        ProductionWorker(repo,store,registry,only_configured_stages=True).run()


if __name__=='__main__':main()
