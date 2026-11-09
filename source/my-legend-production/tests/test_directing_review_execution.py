from copy import deepcopy
from my_legend_production.production.directing_review_execution import review_execution


def test_review_movement_faces_travel_direction_without_mutating_saved_evidence():
    source={'episode_id':'ep','scene_id':'s','line_id':'l'}
    cues=[dict(id='right',type='actor.move_to',enabled=True,instance_id='a',start_frame=10,end_frame=30,
               params={'to':[.8,.825],'visible_after':True},source=source),
          dict(id='left-exit',type='actor.move_to',enabled=True,instance_id='a',start_frame=40,end_frame=70,
               params={'to':[.1,.825],'visible_after':False},source=source)]
    preview={'stage':{'cues':cues},'scene_track':{'segments':[{'start_frame':0,'end_frame':100,
        'actors':[{'instance_id':'a','position':[.2,.825],'facing':-1,'visible':True}]}]}}
    summary={'execution':[{'before':{'facing':-1}}]};old=deepcopy((preview,summary))
    result=review_execution(preview,summary)
    right,left=result['movement_runtime']
    assert all(s['facing']==1 for s in right['samples'])
    assert right['samples'][1]['position'][0]==.5
    assert all(s['facing']==-1 for s in left['samples'])
    assert left['samples'][-2]['visible'] is True and left['samples'][-1]['visible'] is False
    assert (preview,summary)==old and 'movement_runtime' not in summary
