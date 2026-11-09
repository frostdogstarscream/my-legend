"""Idempotent local catalog registration; never invokes a generation provider."""
import os
import time
import httpx
from my_legend_production.production.creation_library import creation_library
from my_legend_production.production.h5_preview import load_h5_catalog
from my_legend_production.production.spine_preview import local_root

def main():
    roles=creation_library()['characters']
    # UI labels can change without rewriting immutable story character canon.
    catalog=load_h5_catalog(local_root())
    actors={a['actor_id']:a for a in catalog['actors']}
    registry_names={r['id']:actors[r['stageBinding']['actor_id']].get('source_display_name',r['name']) for r in roles}
    values=[dict(character_id=r['id'],expected_version=0,
                 canon=dict(name=registry_names[r['id']],identity=f"{registry_names[r['id']]}形象的故事角色，性格和经历可由你的灵感设定。"),
                 runtime_defaults={},assets={}) for r in roles]
    with httpx.Client(base_url='http://127.0.0.1:8897',trust_env=False,timeout=30,
        headers={'Authorization':'Bearer '+os.environ['MY_LEGEND_STORY_TOKEN']}) as client:
        for attempt in range(20):
            try:
                client.get('/health').raise_for_status()
                break
            except (httpx.TransportError,httpx.HTTPStatusError):
                if attempt==19:raise RuntimeError('CREATION_STORY_SERVICE_NOT_READY') from None
                time.sleep(1)
        response=client.post('/v1/characters/import',json={'characters':values})
        if response.status_code!=200:raise RuntimeError('CREATION_LIBRARY_IMPORT_FAILED:'+str(response.status_code))
    print(f'Creation wardrobe characters verified: {len(roles)}')

if __name__=='__main__':main()
