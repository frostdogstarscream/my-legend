from copy import deepcopy
from my_legend_production.production.creation_library import wardrobe_characters, with_scene_choices
from my_legend_production.production.basic_stage import choose_room
from my_legend_production.production.flows import FlowService


def test_outfits_have_independent_stable_identities_and_bindings():
    catalog=dict(catalog_sha256='a'*64,actors=[dict(actor_id='male',outfit_ids=['coat','suit'])],outfits=[
        dict(actor_id='male',outfit_id=i,display_name=i,thumbnail={'sha256':i}) for i in ('coat','suit','invalid')])
    original=deepcopy(catalog);roles=wardrobe_characters(catalog)
    assert [r['id'] for r in roles]==['wardrobe-coat','wardrobe-suit']
    assert [r['stageBinding']['outfit_id'] for r in roles]==['coat','suit']
    assert roles[0]['thumbnail']=={'sha256':'coat'} and catalog==original


def test_scene_choices_are_frozen_in_generation_without_mutating_draft():
    source=dict(kind='creative',title='雨后咖啡',idea='两人相遇在咖啡馆',genre='生活治愈',character_ids=['wardrobe-coat'],target_duration_seconds=90)
    original=deepcopy(source)
    casting={'scene_choices':[{'id':'cafe','name':'咖啡吧'},{'id':'library','name':'大藏书馆'}]}
    request=FlowService.sound_request(source,'work',casting=casting)
    assert source==original
    assert '咖啡吧、大藏书馆' in request['source']['idea']
    assert request['source']['character_ids']==source['character_ids']
    operation={'kind':'operation','instruction':'修改结尾'}
    assert 'location' in with_scene_choices(operation,casting)['instruction']
    assert with_scene_choices(source,{})==source


def test_model_selects_each_scene_and_old_flows_keep_fallback():
    rooms=[dict(scene_id='cafe',display_name='咖啡吧'),dict(scene_id='library',display_name='大藏书馆')]
    assert choose_room({'location':'咖啡吧'},rooms,True)==(rooms[0],True)
    assert choose_room({'location':'library'},rooms,True)==(rooms[1],True)
    assert choose_room({'location':'未知地点'},rooms,True)==(rooms[1],False)
    assert choose_room({'location':'咖啡吧'},rooms,False)==(rooms[1],False)
