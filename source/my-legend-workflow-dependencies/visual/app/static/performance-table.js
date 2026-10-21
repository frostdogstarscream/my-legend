/* TPM 展示层：不修改协议或重新估算时间。 */
(function (root) {
  const emotions = {neutral:'中性',happy:'高兴',sad:'悲伤',angry:'愤怒',fearful:'恐惧',surprised:'惊讶',disgusted:'厌恶',tender:'温柔',serious:'严肃',excited:'兴奋',nervous:'紧张',mysterious:'神秘',impatient:'不耐烦',restrained_angry:'克制的愤怒'};
  const actions = {idle:'待机',nod:'点头',shake_head:'摇头',look_left:'向左看',look_right:'向右看',look_up:'抬头',look_down:'低头',lean_forward:'身体前倾',lean_back:'身体后仰',turn_left:'左转',turn_right:'右转',wave:'挥手',point:'指向',tremble:'颤抖',recoil:'后缩',bow:'鞠躬'};
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const validNumber = value => typeof value === 'number' && Number.isFinite(value);
  function timestamp(ms) {
    if (!validNumber(ms) || ms < 0) return '未提供';
    const whole = Math.round(ms);
    return `${String(Math.floor(whole / 60000)).padStart(2,'0')}:${String(Math.floor(whole / 1000) % 60).padStart(2,'0')}.${String(whole % 1000).padStart(3,'0')}`;
  }
  function build(tpm) {
    const events = Array.isArray(tpm?.events) ? tpm.events : [];
    const speech = events.filter(event => event?.type === 'speech');
    const actors = new Map((Array.isArray(tpm?.actors) ? tpm.actors : []).map(a => [a.actor_id, a.character_name]));
    const locked = tpm?.timeline_status === 'locked' && speech.every(e => e.timeline_status === 'locked');
    const summary = speech.length ? `${events.length} 个事件，${speech.length} 段台词；${locked ? '已锁定' : '估算时间轴（尚未全部锁定）'}。` : '本版本暂无语音表演数据。';
    const rows = speech.map((event, index) => {
      const performance = event.performance || {};
      const emotion = performance.emotion || {};
      const primary = emotions[emotion.primary] || (emotion.primary ? '未识别情绪' : '未标注');
      const secondary = emotion.secondary ? ` / ${emotions[emotion.secondary] || '未识别次情绪'}` : '';
      const intensity = validNumber(emotion.intensity) && emotion.intensity >= 0 && emotion.intensity <= 1 ? `${Math.round(emotion.intensity * 100)}%` : '未提供';
      const rate = performance.prosody?.rate;
      const speed = validNumber(rate) && rate > 0 ? `${rate.toFixed(2)} 倍` : '未提供';
      const movements = (Array.isArray(event.actions) ? event.actions : []).map(action => {
        const name = actions[action.action] || '未识别动作';
        const timing = validNumber(action.start_offset_ms) && validNumber(action.duration_ms) ? `（台词开始后 ${(action.start_offset_ms / 1000).toFixed(2)} 秒，持续 ${(action.duration_ms / 1000).toFixed(2)} 秒）` : '';
        return `<li>${escape(name + timing)}</li>`;
      });
      return `<tr><td>${index + 1}</td><td>${escape(actors.get(event.actor_id) || '未命名角色')}</td><td class="performance-line">${escape(event.text)}</td><td class="performance-time">${timestamp(event.start_ms)}</td><td class="performance-time">${timestamp(event.end_ms)}</td><td>${escape(primary + secondary)}</td><td>${intensity}</td><td>${speed}</td><td class="performance-actions">${movements.length ? `<ul>${movements.join('')}</ul>` : '未安排动作'}</td></tr>`;
    }).join('');
    return {summary, rows: rows || '<tr><td colspan="9" class="performance-empty">暂无台词数据。生成时请勾选“同时生成 TPM 1.0 玩具发声表演协议”。</td></tr>'};
  }
  root.PerformanceTable = {build, timestamp};
  if (typeof module !== 'undefined' && module.exports) module.exports = root.PerformanceTable;
})(globalThis);
