const $ = (s) => document.querySelector(s);
const state = { csrf: '', mediaBase: '/media', path: '', outputPath: '', files: [], selected: new Set(), presets: [], timer: null };

function escapeHtml(value) { const div = document.createElement('div'); div.textContent = value ?? ''; return div.innerHTML; }
function bytes(n) { if (!Number.isFinite(n)) return ''; const u=['B','KB','MB','GB','TB']; let i=0; while(n>=1024&&i<u.length-1){n/=1024;i++} return `${n.toFixed(i?1:0)} ${u[i]}`; }
function toast(message) { const el=$('#toast'); el.textContent=message; el.classList.add('show'); setTimeout(()=>el.classList.remove('show'),2200); }
async function api(path, options={}) {
  options.headers = {'Content-Type':'application/json', ...(state.csrf ? {'X-CSRF-Token':state.csrf}:{}), ...(options.headers||{})};
  const response = await fetch(path, options); let data={}; try{data=await response.json()}catch{}
  if(response.status===401 && path!=='/api/login'){ showLogin(); throw new Error('登录已过期'); }
  if(!response.ok) throw new Error(data.error || `请求失败 (${response.status})`); return data;
}
function showLogin(){ $('#loginView').classList.remove('hidden'); $('#appView').classList.add('hidden'); if(state.timer)clearInterval(state.timer); }
function showApp(){ $('#loginView').classList.add('hidden'); $('#appView').classList.remove('hidden'); }

async function init(){
  const theme=localStorage.getItem('theme')||'system'; $('#theme').value=theme; applyTheme(theme);
  try{ const me=await api('/api/me'); state.csrf=me.csrf; state.mediaBase=me.media_root||'/media'; await loadApp(); }catch{ showLogin(); }
}
async function loadApp(){
  showApp(); const [presets]=await Promise.all([api('/api/presets'), browse(state.mediaBase), loadJobs()]); state.presets=presets;
  const grouped=Object.groupBy ? Object.groupBy(presets,p=>p.group) : presets.reduce((a,p)=>((a[p.group]??=[]).push(p),a),{});
  $('#preset').innerHTML=Object.entries(grouped).map(([group,items])=>`<optgroup label="${escapeHtml(group)}">${items.map(p=>`<option value="${p.id}">${escapeHtml(p.label)}</option>`).join('')}</optgroup>`).join('');
  $('#preset').value='m4a_remux';
  updatePresetHint(); if(state.timer)clearInterval(state.timer); state.timer=setInterval(loadJobs,2000);
}
function applyTheme(theme){ if(theme==='system')document.documentElement.removeAttribute('data-theme'); else document.documentElement.dataset.theme=theme; }
function mediaPath(path){ return state.mediaBase+(path?'/'+path:''); }

async function browse(path, target='#browser'){
  const data=await api(`/api/browse?path=${encodeURIComponent(path)}`); if(target==='#browser'){state.path=data.path;$('#sourcePath').value=mediaPath(data.path);$('#pathBadge').textContent=mediaPath(data.path);$('#upButton').disabled=!data.path;}
  else{state.outputPath=data.path;}
  const root=$(target); root.dataset.parent=data.parent; root.innerHTML='';
  if(data.path){const up=document.createElement('button');up.className='browser-row';up.innerHTML='<span class="icon">↰</span><span>..</span>';up.onclick=()=>browse(data.parent,target);root.append(up)}
  for(const entry of data.entries){const row=document.createElement('button');row.className='browser-row';row.innerHTML=`<span class="icon">${entry.type==='directory'?'📁':'▤'}</span><span>${escapeHtml(entry.name)}</span>`;row.onclick=()=>entry.type==='directory'?browse(entry.path,target):selectSingle(entry.path);root.append(row)}
  if(!root.children.length)root.innerHTML='<div class="empty">此文件夹为空</div>';
}
async function selectSingle(path){
  try{const data=await api(`/api/scan?path=${encodeURIComponent(path)}`);mergeFiles(data.files,true)}catch(e){toast(e.message)}
}
async function openSourcePath(){
  const path=$('#sourcePath').value.trim()||state.mediaBase;
  try{await browse(path)}catch(firstError){
    try{await selectSingle(path)}catch{toast(firstError.message)}
  }
}
async function scanCurrent(){
  const path=$('#sourcePath').value.trim()||mediaPath(state.path);$('#scanButton').disabled=true; try{const data=await api(`/api/scan?path=${encodeURIComponent(path)}&recursive=${$('#recursive').checked}`); mergeFiles(data.files,false); if(data.truncated)toast(`已达到 ${data.limit} 个文件的扫描上限`)}catch(e){toast(e.message)}finally{$('#scanButton').disabled=false}
}
function mergeFiles(files,single){
  if(!single){state.files=files;state.selected=new Set(files.map(f=>f.path))}else{for(const file of files){if(!state.files.some(f=>f.path===file.path))state.files.push(file);state.selected.add(file.path)}}renderFiles();
}
function renderFiles(){
  const root=$('#files'); if(!state.files.length)root.innerHTML='<div class="empty">没有发现支持的媒体文件</div>'; else root.innerHTML=state.files.map(f=>`<label class="file-row"><input type="checkbox" data-path="${escapeHtml(f.path)}" ${state.selected.has(f.path)?'checked':''}><span class="file-main"><strong>${escapeHtml(f.name)}</strong><small>${escapeHtml(f.path)} · ${bytes(f.size)}</small></span><span class="type-pill">${f.kind}</span></label>`).join('');
  root.querySelectorAll('input').forEach(input=>input.onchange=()=>{input.checked?state.selected.add(input.dataset.path):state.selected.delete(input.dataset.path);updateSelection()}); updateSelection();
}
function updateSelection(){const n=state.selected.size;$('#selectionCount').textContent=`已选择 ${n} 个文件`;$('#convertCount').textContent=n;$('#convertButton').disabled=n===0;$('#selectAll').checked=state.files.length>0&&n===state.files.length;$('#selectAll').indeterminate=n>0&&n<state.files.length}
function updatePresetHint(){const p=state.presets.find(x=>x.id===$('#preset').value);if(!p)return;$('#presetHint').textContent=p.remux?'✓ 仅更换 M4A 外壳，不重新编码；不会增加损失，但也不会恢复源文件已经损失的音质':p.lossless?'✓ 无损编码：不会因本次编码损失画面或声音信息（文件通常较大）':'有损压缩：会重新编码，适合减小文件体积和日常播放'}

async function startJob(){
  if($('#deleteSources').checked && !confirm('确认启用“成功后删除源文件”？仅验证成功的文件会被删除，但删除操作不可撤销。'))return;
  $('#convertButton').disabled=true;$('#actionError').textContent='';try{await api('/api/jobs',{method:'POST',body:JSON.stringify({files:[...state.selected],preset:$('#preset').value,output_dir:$('#outputDir').value,suffix:$('#suffix').value,overwrite:$('#overwrite').value,delete_sources:$('#deleteSources').checked})});toast('转换任务已创建');await loadJobs()}catch(e){$('#actionError').textContent=e.message}finally{updateSelection()}
}
const labels={queued:'等待中',running:'转换中',completed:'已完成',completed_with_errors:'部分失败',failed:'失败',cancelled:'已取消'};
async function loadJobs(){
  try{const jobs=await api('/api/jobs');const root=$('#jobs');if(!jobs.length){root.innerHTML='<div class="empty">还没有转换任务</div>';return}root.innerHTML=jobs.map(j=>{const active=['queued','running'].includes(j.state);const cls=j.state==='completed'?'success':(['failed','completed_with_errors'].includes(j.state)?'failed':'');const results=(j.results||[]).map(r=>`<div class="${r.status==='failed'?'bad':''}">${r.status==='success'?'✓':r.status==='skipped'?'–':'✕'} ${escapeHtml(r.source)}${r.message?' — '+escapeHtml(r.message):''}</div>`).join('');return `<article class="job"><div class="job-top"><strong>${escapeHtml(j.preset_label)} · ${j.total} 个文件</strong><span class="status ${cls}">${labels[j.state]||j.state}</span></div><div class="progress"><div style="width:${j.state==='completed'?100:(j.progress||0)}%"></div></div><div class="job-meta">${j.current_file?escapeHtml(j.current_file)+' · ':''}${j.current}/${j.total} · ${j.progress||0}% ${active?`<button class="link cancel" data-id="${j.id}">取消</button>`:''}</div>${results?`<div class="job-result">${results}</div>`:''}</article>`}).join('');root.querySelectorAll('.cancel').forEach(b=>b.onclick=()=>cancelJob(b.dataset.id))}catch(e){console.warn(e)}
}
async function cancelJob(id){try{await api(`/api/jobs/${id}/cancel`,{method:'POST',body:'{}'});toast('已请求取消任务');loadJobs()}catch(e){toast(e.message)}}

$('#loginForm').onsubmit=async e=>{e.preventDefault();$('#loginError').textContent='';try{const data=await api('/api/login',{method:'POST',body:JSON.stringify({username:$('#username').value,password:$('#password').value})});state.csrf=data.csrf;state.mediaBase=data.media_root||'/media';$('#password').value='';await loadApp()}catch(err){$('#loginError').textContent=err.message}};
$('#logoutButton').onclick=async()=>{try{await api('/api/logout',{method:'POST',body:'{}'})}finally{state.csrf='';showLogin()}};
$('#theme').onchange=e=>{localStorage.setItem('theme',e.target.value);applyTheme(e.target.value)};
$('#upButton').onclick=()=>browse($('#browser').dataset.parent||'');$('#scanButton').onclick=scanCurrent;
$('#openPathButton').onclick=openSourcePath;$('#sourcePath').onkeydown=e=>{if(e.key==='Enter')openSourcePath()};
$('#selectAll').onchange=e=>{state.selected=e.target.checked?new Set(state.files.map(f=>f.path)):new Set();renderFiles()};
$('#clearButton').onclick=()=>{state.files=[];state.selected.clear();renderFiles()};$('#preset').onchange=updatePresetHint;$('#convertButton').onclick=startJob;$('#refreshJobs').onclick=loadJobs;
$('#chooseOutput').onclick=async()=>{await browse($('#outputDir').value||'','#directoryBrowser');$('#directoryDialog').showModal()};
$('#useDirectory').onclick=e=>{e.preventDefault();$('#outputDir').value=mediaPath(state.outputPath);$('#directoryDialog').close()};
$('#passwordButton').onclick=()=>$('#passwordDialog').showModal();document.querySelectorAll('[data-close-password]').forEach(b=>b.onclick=()=>$('#passwordDialog').close());
$('#passwordForm').onsubmit=async e=>{e.preventDefault();$('#passwordError').textContent='';try{await api('/api/password',{method:'POST',body:JSON.stringify({current:$('#currentPassword').value,new:$('#newPassword').value})});$('#passwordDialog').close();e.target.reset();toast('密码已更新')}catch(err){$('#passwordError').textContent=err.message}};
init();
