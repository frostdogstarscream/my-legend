"""Pure presentation of recorded recovery facts; never calls a provider."""

def flow_recovery(row, jobs, calls, query=None):
    status = row['status']
    sound = jobs.get('sound', {})
    artifacts = {a['kind'] for a in sound.get('artifacts', [])}
    remake = ('stage_basic' if sound.get('status') == 'succeeded' and 'AudioMix' in artifacts
              else 'audio_full' if {'StoryDelivery', 'RenderManifest'} <= artifacts else 'restart')
    def result(state, message, actions):
        return dict(state=state, message=message, actions=actions, remake_kind=remake,
                    last_query=query)
    if status == 'succeeded':
        return result('complete', '故事已经准备好了', [])
    if status in ('queued', 'running'):
        if jobs.get('stage') or sound.get('status') == 'succeeded':message = '正在准备舞台' if row['request'].get('output', 'h5') == 'h5' else '正在整理作品'
        elif any(s['name'] == 'story' and s['status'] != 'succeeded' for s in sound.get('stages', [])) or not sound:message = '正在写故事'
        else:message = '正在配音'
        return result('active', message, ['cancel'])
    unsettled = [c for c in calls if c['state'] != 'completed']
    if unsettled or any(j.get('unsettled_calls', 0) for j in jobs.values()):
        queryable = any(c['call_key'] in ('story:planning-budget', 'story:writing-budget') for c in unsettled)
        unavailable = not queryable or any(u.get('reason') not in ('EXTERNAL_RESULT_UNKNOWN', 'UPSTREAM_STILL_ACTIVE') for u in (query or {}).get('unresolved', []))
        message = ('暂时无法查询结果，可稍后再试或重新制作' if unavailable else
                   '已查询，仍未能确认这次制作的结果' if query else '这次制作还没有返回结果')
        return result('unknown', message, (['reconcile'] if queryable else []) + ['remake'])
    if status == 'cancelled':return result('cancelled', '制作已取消，已完成的内容仍然保留', ['remake'])
    failed_story = [c for c in calls if c.get('stage_name') == 'story' and
                    c.get('stage_status') in ('failed', 'needs_input') and
                    (c.get('checkpoint') or {}).get('phase') in ('planning', 'writing') and
                    c['call_key'] == 'story:'+c['checkpoint']['phase']+'-budget']
    terminal = {t['call_key']:t['status'] for t in (query or {}).get('terminal', [])}
    confirmed_success = bool(failed_story) and all(terminal.get(c['call_key']) == 'SUCCEEDED' for c in failed_story)
    failed_audio = any((c.get('result') or {}).get('generation_failed') for c in calls)
    if (failed_story and not confirmed_success) or failed_audio:
        return result('remake', '这次制作未能完成，已保存完成的部分', ['remake'])
    return result('resumable', '制作中断了，已保存完成的部分', ['resume', 'remake'])
