// 唯一播放时钟取自原生 video。手机文字、角色高亮和进度只展示已冻结的帧数据。
const video=document.querySelector('#preview-video'),progress=document.querySelector('#progress');
const sheet=document.querySelector('#sheet'),errorBox=document.querySelector('#video-error');
let preview=null,atlas=null,frameKey=null,scrubbing=false;
function audioDescription(){return preview?.audio_kind==='synthetic_placeholder'?'联调占位音 · 尚无台词朗读':'已提供音轨 · 试听预览'}
if(new URLSearchParams(location.search).get('view')==='screen')document.body.classList.add('screen-only');
function icon(name){return `<svg class="icon"><use href="#i-${name}"/></svg>`}
function format(seconds){const t=Math.max(0,seconds);return `${String(Math.floor(t/60)).padStart(2,'0')}:${(t%60).toFixed(1).padStart(4,'0')}`}
function textElement(tag,text,className){const e=document.createElement(tag);e.textContent=text;if(className)e.className=className;return e}
function error(message){errorBox.textContent=message;errorBox.hidden=false}
function portrait(canvas,roleId){if(!preview||!atlas)return;const p=preview.portrait_atlas,slot=p.role_ids.indexOf(roleId),ctx=canvas.getContext('2d');ctx.clearRect(0,0,canvas.width,canvas.height);if(slot<0)return;const width=atlas.naturalWidth/p.columns;ctx.drawImage(atlas,slot*width,0,width,atlas.naturalHeight*p.crop_height_fraction,0,0,canvas.width,canvas.height)}
function sync(time=video.currentTime){
  if(!preview)return;
  const ms=Math.min(preview.duration_ms,Math.max(0,time*1000));
  // 100ms 源帧来自实际编排结果；不从台词文本推断说话人，也不沿用上一条字幕。
  const frame=preview.frames[Math.min(preview.frames.length-1,Math.floor(ms/100))];
  if(!scrubbing)progress.value=(ms/1000).toFixed(1);
  progress.style.setProperty('--progress',`${ms/preview.duration_ms*100}%`);
  const output=document.querySelector('#time');output.replaceChildren(document.createTextNode(`${format(ms/1000)} `),textElement('span',`/ ${format(preview.duration_ms/1000)}`));
  const ended=ms>=preview.duration_ms-1;
  const key=`${ended}|${frame.block_id}|${frame.speaker_id}|${frame.subtitle}`;
  if(key===frameKey)return;frameKey=key;
  const role=preview.roles.find(r=>r.role_id===frame.speaker_id),caption=document.querySelector('#current-caption');
  document.querySelector('#speaker-name').textContent=ended?'播放结束':role?.name||(frame.subtitle?'旁白':'场景');
  document.querySelector('#caption-type').textContent=ended?'重播':role?'当前对白':frame.subtitle?'当前旁白':'转场';
  document.querySelector('#line').textContent=ended?'点击下方重新播放，从头查看此片段。':frame.subtitle||'…';
  caption.classList.toggle('is-narration',!role);
  const speakerButton=document.querySelector('#speaker-card');speakerButton.disabled=!role;speakerButton.dataset.role=role?.role_id||'';speakerButton.setAttribute('aria-label',role?`查看${role.name}`:'当前无说话人');
  portrait(document.querySelector('#speaker-face'),role?.role_id);
  document.querySelectorAll('.role-card').forEach(card=>card.classList.toggle('is-speaking',card.dataset.role===role?.role_id));
}
function playbackState(){const playing=!video.paused&&!video.ended;document.querySelector('#play').setAttribute('aria-label',playing?'暂停':'播放');document.querySelector('#play .play-disc').innerHTML=icon(playing?'pause':'play');document.querySelector('#play-label').textContent=playing?'暂停播放':video.ended?'重新播放':'开始播放'}
async function play(){if(!preview)return;if(video.ended)video.currentTime=0;try{await video.play();errorBox.hidden=true}catch{error('未能开始播放，请再次点击播放，或在设置中打开完整视频。')}}
function seek(seconds){if(!preview)return;video.pause();video.currentTime=Math.min(preview.duration_ms/1000,Math.max(0,seconds));sync(video.currentTime)}
document.querySelector('#play').onclick=()=>video.paused?play():video.pause();
progress.addEventListener('input',()=>{scrubbing=true;seek(Number(progress.value));scrubbing=false});
document.querySelector('#previous').onclick=()=>{if(!preview)return;const t=video.currentTime*1000;const e=[...preview.events].reverse().find(e=>e.start_ms<t-100);seek((e?.start_ms??preview.events[0].start_ms)/1000)};
document.querySelector('#next').onclick=()=>{if(!preview)return;const e=preview.events.find(e=>e.start_ms>video.currentTime*1000+100);seek((e?.start_ms??preview.events.at(-1).start_ms)/1000)};
for(const event of ['play','pause','ended','ratechange'])video.addEventListener(event,playbackState);
for(const event of ['timeupdate','seeked','loadeddata'])video.addEventListener(event,()=>sync());
video.addEventListener('error',()=>error('视频加载失败，请检查本地预览服务或重新载入页面。'));
// 支持时以解码帧时间同步字幕；兼容路径仍读取 video.currentTime，不用自增计时器。
if('requestVideoFrameCallback' in video){const draw=(now,metadata)=>{sync(metadata.mediaTime);video.requestVideoFrameCallback(draw)};video.requestVideoFrameCallback(draw)}
document.querySelector('#fullscreen').onclick=async()=>{try{if(video.requestFullscreen)await video.requestFullscreen();else if(video.webkitEnterFullscreen)video.webkitEnterFullscreen();else openSheet('full')}catch{openSheet('full')}};
function showRole(content,roleId){const role=preview.roles.find(r=>r.role_id===roleId);if(!role)return;const row=textElement('div','', 'sheet-role'),canvas=document.createElement('canvas');canvas.width=352;canvas.height=400;portrait(canvas,roleId);const copy=document.createElement('div');copy.append(textElement('b',role.name),textElement('p',`角色 ID：${role.role_id}`));row.append(canvas,copy);content.append(row)}
function openSheet(type,roleId){
  if(!preview)return;video.pause();const content=document.querySelector('#sheet-content');content.replaceChildren();
  const titles={chapters:'播放片段',roles:'本章角色',role:'角色信息',settings:'播放设置',sound:'声音设置',full:'完整视频',home:'当前作品',create:'剧情修改',account:'预览版本'};
  content.append(textElement('h2',titles[type]||'视频预览'));
  if(type==='roles'||type==='role'){for(const role of preview.roles){if(type==='roles'||role.role_id===roleId)showRole(content,role.role_id)}}
  else if(type==='chapters'){for(const e of preview.events){const button=textElement('button','', 'sheet-event'),role=preview.roles.find(r=>r.role_id===e.role_id);button.append(textElement('time',format(e.start_ms/1000)),textElement('b',role?.name||'旁白'),textElement('small',e.text));button.onclick=()=>{seek(e.start_ms/1000);sheet.close()};content.append(button)}}
  else if(type==='settings'||type==='sound'){
    if(type==='settings'){const label=textElement('label','', 'sheet-item');label.append(textElement('span','播放速度'));const select=document.createElement('select');select.setAttribute('aria-label','播放速度');for(const value of [0.5,1,1.5,2]){const option=textElement('option',`${value.toFixed(1)}×`);option.value=value;select.append(option)}select.value=String(video.playbackRate);select.onchange=()=>video.playbackRate=Number(select.value);label.append(select);content.append(label)}
    const muteLabel=textElement('label','', 'sheet-item'),mute=document.createElement('input');mute.type='checkbox';mute.checked=video.muted;mute.setAttribute('aria-label','静音');mute.onchange=()=>video.muted=mute.checked;muteLabel.append(textElement('span','静音'),mute);content.append(muteLabel);
    const volumeLabel=textElement('label','', 'sheet-item'),volume=document.createElement('input');volume.type='range';volume.min=0;volume.max=100;volume.value=video.volume*100;volume.setAttribute('aria-label','音量');volume.oninput=()=>video.volume=Number(volume.value)/100;volumeLabel.append(textElement('span','音量'),volume);content.append(volumeLabel,textElement('p',audioDescription(),'mini-label'));
  }else if(type==='create')content.append(textElement('p','剧情修改在剧本编辑部分完成；视频预览使用已生成的固定版本。'));
  else if(type==='account')content.append(textElement('p',`源版本：${preview.source_version_id}`));
  else content.append(textElement('p',`${preview.title} · ${format(preview.duration_ms/1000)}`));
  if(['full','settings','home'].includes(type)){const link=textElement('a','打开完整视频','preview-link');link.href=preview.video_file;link.target='_blank';link.rel='noopener';content.append(link)}
  const bounds=document.querySelector('.screen').getBoundingClientRect();sheet.style.position='fixed';sheet.style.left=`${bounds.left}px`;sheet.style.width=`${bounds.width}px`;sheet.style.bottom=`${Math.max(0,innerHeight-bounds.bottom)}px`;sheet.showModal();
}
document.querySelectorAll('[data-sheet]').forEach(button=>button.onclick=()=>openSheet(button.dataset.sheet));
document.querySelector('#speaker-card').onclick=()=>openSheet('role',document.querySelector('#speaker-card').dataset.role);
document.querySelector('.sheet-close').onclick=()=>sheet.close();document.querySelector('#story-nav').onclick=()=>{if(sheet.open)sheet.close()};
sheet.addEventListener('click',e=>{if(e.target===sheet){const r=sheet.getBoundingClientRect();if(e.clientY<r.top||e.clientY>r.bottom||e.clientX<r.left||e.clientX>r.right)sheet.close()}});
async function init(){
  for(const button of document.querySelectorAll('.playback-buttons button'))button.disabled=true;
  try{
    const response=await fetch('preview.json');if(!response.ok)throw new Error('缺少视频对照数据');preview=await response.json();
    if(!preview.frames.length||!preview.events.length||!(preview.duration_ms>0))throw new Error('视频对照数据不完整');
    atlas=new Image();atlas.src=preview.portrait_atlas.file;await atlas.decode();
    document.querySelector('#video-title').textContent=preview.title;document.querySelector('.music p').textContent=audioDescription();
    document.querySelector('#role-count').textContent=`${preview.roles.length} 位角色`;
    const cast=document.querySelector('#cast-list');for(const role of preview.roles){const button=textElement('button','', 'role-card'),canvas=document.createElement('canvas');canvas.width=352;canvas.height=400;portrait(canvas,role.role_id);button.dataset.role=role.role_id;button.setAttribute('aria-label',`查看${role.name}`);button.append(canvas,textElement('span',role.name));button.onclick=()=>openSheet('role',role.role_id);cast.append(button)}
    if(video.readyState<1)await new Promise((resolve,reject)=>{video.addEventListener('loadedmetadata',resolve,{once:true});video.addEventListener('error',reject,{once:true})});
    if(Math.abs(video.duration-preview.duration_ms/1000)>1/30+0.001)throw new Error('视频与字幕时长不一致，请重新生成同版本预览');
    // 比例来自当前视频版本；纯画面与带字幕成片尺寸不同，不能沿用旧容器高度。
    if(!(preview.video_width>0&&preview.video_height>0))throw new Error('缺少视频尺寸');
    document.querySelector('.stage').style.aspectRatio=`${preview.video_width}/${preview.video_height}`;
    progress.max=String(preview.duration_ms/1000);
    // 首屏停在第一条有效事件，避免用转场黑帧作为手机视频封面。
    seek(preview.events[0].start_ms/1000);
    for(const button of document.querySelectorAll('.playback-buttons button'))button.disabled=false;
  }catch(e){preview=null;error(`预览未就绪：${e.message||'加载失败'}`);video.controls=true}
}
init();
