const $ = s => document.querySelector(s);
let providers = {}, currentVersion = null, currentTpm = null;
let storyDocument = null, storyPreview = null, inputMode = 'text';
const labels = {format:'格式',character_consistency:'人物一致',dramatic_structure:'戏剧结构',dialogue_performance:'表演细节',stage_feasibility:'舞台可执行',duration_fit:'时长匹配',tpm_completeness:'TPM 完整度'};

async function api(url, options={}) {
  const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
  let data; try { data = await response.json(); } catch { data = await response.text(); }
  if (!response.ok) throw new Error(data.detail || data.error?.message || String(data));
  return data;
}
function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2500)}
function esc(text=''){return String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

async function loadConfig(){
  providers = await api('/api/v1/model-providers');
  const cfg = await api('/api/v1/config');
  $('#provider').innerHTML = Object.entries(providers).map(([v,p])=>`<option value="${v}">${esc(p.label)}</option>`).join('');
  $('#provider').value=cfg.provider; $('#model').value=cfg.model; $('#baseUrl').value=cfg.base_url; $('#apiKey').value=''; $('#apiKey').placeholder=cfg.has_api_key?'已保存本机 Key；留空即可测试或保存':'仅保存在本机 .env'; $('#demoMode').checked=cfg.demo_mode;
  $('#modelBadge').textContent = cfg.demo_mode ? '演示模式 · TPM 1.0' : `${providers[cfg.provider]?.label||cfg.provider} · ${cfg.model}`;
}
async function loadProjects(){
  const rows=await api('/api/v1/projects');
  $('#projectList').innerHTML=rows.length?rows.map(p=>`<div class="project-item"><button data-project="${p.id}"><strong>${esc(p.title)}</strong><small>${esc(p.chapter_title||'未命名章节')} · ${p.version_count} 个版本</small></button><button class="delete" data-delete="${p.id}" title="删除项目">×</button></div>`).join(''):'<p class="muted">暂无项目</p>';
}
function renderResult(data){
  currentVersion=data.version_id||data.id; currentTpm=data.tpm; $('#scriptOutput').value=data.script_text; $('#overallScore').textContent=data.quality.overall;
  $('#sourceOutput').textContent=data.source_text||'（该项目未保存原文）';
  $('#scoreList').innerHTML=Object.entries(data.quality.scores).map(([k,v])=>`<div class="score-row"><span>${labels[k]||k}</span><strong>${v}</strong><i style="--score:${v}%"></i></div>`).join('');
  $('#issueList').innerHTML=data.quality.issues.map(x=>`<li>${esc(x)}</li>`).join('');
  $('#notesList').innerHTML=(data.script.adaptation_notes||[]).map(x=>`<li>${esc(x)}</li>`).join('');
  const performance = PerformanceTable.build(data.tpm);
  $('#tpmSummary').textContent=performance.summary;
  $('#performanceSummary').textContent=performance.summary;
  $('#performanceRows').innerHTML=performance.rows;
  $('#tpmPreview').textContent=JSON.stringify(data.tpm||{},null,2);
  $('#exportTxt').href=`/api/v1/versions/${currentVersion}/export?format=txt`; $('#exportDocx').href=`/api/v1/versions/${currentVersion}/export?format=docx`; $('#exportLines').href=`/api/v1/versions/${currentVersion}/export?format=lines`; $('#exportTpm').href=`/api/v1/versions/${currentVersion}/export?format=tpm`;
  $('#result').classList.remove('hidden'); $('#result').scrollIntoView({behavior:'smooth'});
}
async function openProject(id){const p=await api(`/api/v1/projects/${id}`);if(!p.versions.length)return toast('该项目还没有成功版本');const v=p.versions[0];renderResult({...v,source_text:p.source_text});$('#title').value=p.title;$('#chapterTitle').value=p.chapter_title;$('#sourceText').value=p.source_text;updateCounter()}
function updateCounter(){ $('#counter').textContent=`${$('#sourceText').value.length.toLocaleString()} / 20,000`; }
function selectedStoryChapters(){return [...document.querySelectorAll('[data-story-chapter]:checked')].map(el=>Number(el.value));}
function switchInputMode(mode){inputMode=mode;document.querySelectorAll('[data-mode]').forEach(button=>button.classList.toggle('active',button.dataset.mode===mode));$('#textInputPanel').classList.toggle('hidden',mode!=='text');$('#storyJsonPanel').classList.toggle('hidden',mode!=='story-json');}
function renderStoryPreview(data){
  storyPreview=data; const warning=data.warnings||[];
  $('#storyPreview').innerHTML=`<h3>${esc(data.title)}</h3><p class="muted">${esc(data.genre||'未标注类型')} · ${esc(data.tone||'未标注语气')} · ${esc(data.story_id)}</p><div class="story-columns"><div><h4>角色</h4>${data.characters.map(c=>`<p><strong>${esc(c.name)}</strong> <code>${esc(c.role_id)}</code><br><small>${esc(c.identity)}</small></p>`).join('')}</div><div><h4>选择章节</h4>${data.chapters.map(c=>`<label class="story-chapter"><input type="checkbox" data-story-chapter value="${c.chapter_number}" checked> 第 ${c.chapter_number} 章《${esc(c.title)}》<small>${c.text_length.toLocaleString()} 字 · ${esc(c.summary)}</small></label>`).join('')}</div></div>${warning.length?`<div class="story-warnings"><strong>需要确认的上游提示</strong><ul>${warning.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:''}`;
  $('#storyPreview').classList.remove('hidden'); $('#storyWarningCheck').classList.toggle('hidden',!warning.length); $('#acknowledgeWarnings').checked=!warning.length;
}
async function previewStory(){try{storyDocument=JSON.parse($('#storyJson').value);const data=await api('/api/v1/story-imports/preview',{method:'POST',body:JSON.stringify({document:storyDocument})});renderStoryPreview(data);toast('故事 JSON 解析成功，请确认章节和提示')}catch(e){storyDocument=null;storyPreview=null;$('#storyPreview').classList.add('hidden');toast(`JSON 导入失败：${e.message}`)}}

$('#sourceText').addEventListener('input',updateCounter);
$('#fileInput').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;$('#sourceText').value=await file.text();updateCounter()});
$('#storyJsonFile').addEventListener('change',async e=>{const file=e.target.files[0];if(!file)return;$('#storyJson').value=await file.text();storyDocument=null;storyPreview=null;$('#storyPreview').classList.add('hidden');});
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>switchInputMode(button.dataset.mode)));
$('#previewStory').addEventListener('click',previewStory);
$('#provider').addEventListener('change',()=>{const p=providers[$('#provider').value];if(p){$('#model').value=p.model;$('#baseUrl').value=p.base_url}});
$('#openSettings').addEventListener('click',()=>$('#settingsDialog').showModal());
$('#refreshProjects').addEventListener('click',loadProjects);
$('#projectList').addEventListener('click',async e=>{const project=e.target.closest('[data-project]'),del=e.target.closest('[data-delete]');if(project)openProject(project.dataset.project);if(del&&confirm('确定删除这个项目及其全部版本吗？')){await api(`/api/v1/projects/${del.dataset.delete}`,{method:'DELETE'});toast('项目已删除');loadProjects()}});
$('#saveSettings').addEventListener('click',async()=>{try{await api('/api/v1/config',{method:'PUT',body:JSON.stringify({provider:$('#provider').value,model:$('#model').value,api_key:$('#apiKey').value,base_url:$('#baseUrl').value,demo_mode:$('#demoMode').checked})});$('#settingsDialog').close();await loadConfig();toast('模型配置已保存')}catch(e){$('#connectionMessage').textContent=e.message}});
$('#testConnection').addEventListener('click',async()=>{const button=$('#testConnection'),m=$('#connectionMessage');button.disabled=true;m.className='message pending';m.textContent='正在连接模型，请稍候…';try{const r=await api('/api/v1/model-providers/test',{method:'POST',body:JSON.stringify({provider:$('#provider').value,model:$('#model').value,api_key:$('#apiKey').value,base_url:$('#baseUrl').value})});m.className='message success';m.textContent=`✓ ${r.message||'连接成功：模型接口已响应'}`}catch(e){m.className='message error';m.textContent=`连接失败：${e.message}`}finally{button.disabled=false}});
$('#generate').addEventListener('click',async()=>{const options={duration_minutes:Number($('#duration').value),actor_limit:$('#actorLimit').value?Number($('#actorLimit').value):null,style:$('#style').value,detail_density:$('#detail').value,stage:$('#stage').value,language_mode:$('#language').value,enable_tpm:$('#enableTpm').checked};let url='/api/v1/generate',body;if(inputMode==='story-json'){if(!storyDocument||!storyPreview)return toast('请先解析并预览故事 JSON');const chapters=selectedStoryChapters();if(!chapters.length)return toast('请至少选择一章');url='/api/v1/generate-from-story-json';body={document:storyDocument,selected_chapters:chapters,acknowledged_warnings:$('#acknowledgeWarnings').checked,options};}else{const title=$('#title').value.trim(),source=$('#sourceText').value.trim();if(!title||source.length<20)return toast('请填写作品名称并输入至少 20 个字符的原文');body={title,chapter_title:$('#chapterTitle').value.trim(),source_text:source,options};}$('#generate').disabled=true;$('#progress').classList.remove('hidden');try{const data=await api(url,{method:'POST',body:JSON.stringify(body)});renderResult(data);await loadProjects();toast(data.source.startsWith('demo')?'演示初稿和 TPM 已生成':'模型初稿和 TPM 已生成')}catch(e){const detail=typeof e.message==='object'?e.message.message:e.message;toast(detail||'生成失败')}finally{$('#generate').disabled=false;$('#progress').classList.add('hidden')}});
$('#rewriteBtn').addEventListener('click',async()=>{const box=$('#scriptOutput'),selected=box.value.slice(box.selectionStart,box.selectionEnd);if(!selected)return toast('请先在剧本中选中一段内容');try{const r=await api('/api/v1/rewrite',{method:'POST',body:JSON.stringify({text:selected,mode:$('#rewriteMode').value})});box.setRangeText(r.candidate,box.selectionStart,box.selectionEnd,'select');toast('已生成修改候选，请确认后再保存')}catch(e){toast(e.message)}});

Promise.all([loadConfig(),loadProjects()]).catch(e=>toast(`初始化失败：${e.message}`));
