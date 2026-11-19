const $ = s => document.querySelector(s);
// Keep the local app URL clean of ChatGPT attribution / campaign parameters.
if (window.location.search) {
  const cleanUrl = new URL(window.location.href);
  ['utm_source','utm_medium','utm_campaign','utm_term','utm_content'].forEach(k=>cleanUrl.searchParams.delete(k));
  if (cleanUrl.href !== window.location.href) window.history.replaceState({}, '', cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
}
let token = localStorage.getItem('campusai_token') || '';
let currentUser = null, currentTask = null, latestTrace = [], editingClassId = null;

function authHeaders(extra={}){return {...extra, ...(token?{Authorization:`Bearer ${token}`}:{})};}
function errText(d){const x=d&&d.detail;if(Array.isArray(x))return x.map(i=>i.msg||'Invalid input').join('; ');return x||'Request failed';}
async function api(url, options={}){
  const headers=authHeaders(options.headers||{});
  const r=await fetch(url,{...options,headers});
  const data=await r.json().catch(()=>({}));
  if(!r.ok){if(r.status===401){logout(false);}throw new Error(errText(data));}
  return data;
}
function showScreen(name){
  ['authScreen','appShell'].forEach(id=>$('#'+id).classList.toggle('hidden',id!==name));
}
function setAuthMode(){ $('#authError').textContent=''; }
$('#loginForm').addEventListener('submit',async e=>{
  e.preventDefault();$('#authError').textContent='';
  try{
    const d=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:$('#loginUser').value.trim(),password:$('#loginPass').value||''})});
    const data=await d.json().catch(()=>({}));
    if(!d.ok)throw new Error(data.detail||'Could not sign in.');
    token=data.token;localStorage.setItem('campusai_token',token);await loadUser();
  }catch(err){$('#authError').textContent=err.message;}
});
async function loadUser(){
  currentUser=await api('/api/me');
  applyProfile();showScreen('appShell');checkHealth();loadTimetable();
}
function paintAvatar(el,user){
  const initial=(user?.name||user?.user_id||'S').trim().charAt(0).toUpperCase();
  if(user?.avatar){el.style.backgroundImage=`url("${user.avatar}")`;el.classList.add('has-photo');el.textContent='';}
  else{el.style.backgroundImage='';el.classList.remove('has-photo');el.textContent=initial;}
}
function applyProfile(){
  $('#studentName').textContent=currentUser.name||currentUser.user_id;
  $('#studentCourse').textContent=currentUser.course||'Course not set';
  paintAvatar($('#avatar'),currentUser);paintAvatar($('#profilePhoto'),currentUser);
  $('#profileUserId').textContent=currentUser.name||currentUser.user_id;
  $('#photoRemoveBtn').classList.toggle('hidden',!currentUser.avatar);
}
$('#profileForm').addEventListener('submit',async e=>{
  e.preventDefault();$('#profileError').textContent='';
  try{
    const d=await api('/api/profile',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('#profileName').value.trim(),course:$('#profileCourse').value.trim()})});
    currentUser={...currentUser,...d.profile};applyProfile();$('#profileError').textContent='Profile saved.';
  }catch(err){$('#profileError').textContent=err.message;}
});
// ---------- Profile photo editing ----------
const crop={img:null,zoom:1,x:0,y:0,drag:null};
const cropCv=$('#cropCanvas'),cropCtx=cropCv.getContext('2d');
function cropBase(){return Math.max(cropCv.width/crop.img.width,cropCv.height/crop.img.height);}
function clampCrop(){
  const sc=cropBase()*crop.zoom,w=crop.img.width*sc,h=crop.img.height*sc;
  crop.x=Math.min(0,Math.max(cropCv.width-w,crop.x));crop.y=Math.min(0,Math.max(cropCv.height-h,crop.y));
}
function drawCrop(){
  if(!crop.img)return;clampCrop();
  const sc=cropBase()*crop.zoom;
  cropCtx.fillStyle='#000';cropCtx.fillRect(0,0,cropCv.width,cropCv.height);
  cropCtx.drawImage(crop.img,crop.x,crop.y,crop.img.width*sc,crop.img.height*sc);
}
function openCrop(file){
  $('#cropError').textContent='';
  if(!file)return;
  if(!/^image\/(jpeg|png|webp)$/.test(file.type)){alert('Please choose a JPEG, PNG or WebP image.');return;}
  if(file.size>15*1024*1024){alert('That image is too large. Choose one under 15 MB.');return;}
  const url=URL.createObjectURL(file),img=new Image();
  img.onload=()=>{
    crop.img=img;crop.zoom=1;$('#cropZoom').value=100;
    const sc=cropBase();crop.x=(cropCv.width-img.width*sc)/2;crop.y=(cropCv.height-img.height*sc)/2;
    drawCrop();$('#cropModal').classList.remove('hidden');URL.revokeObjectURL(url);
  };
  img.onerror=()=>{URL.revokeObjectURL(url);alert('Could not read that image.');};
  img.src=url;
}
function closeCrop(){$('#cropModal').classList.add('hidden');crop.img=null;$('#photoInput').value='';}
$('#photoBtn').addEventListener('click',()=>$('#photoInput').click());
$('#photoChangeBtn').addEventListener('click',()=>$('#photoInput').click());
$('#photoInput').addEventListener('change',e=>openCrop(e.target.files[0]));
$('#cropClose').onclick=closeCrop;$('#cropCancel').onclick=closeCrop;
$('#cropZoom').addEventListener('input',e=>{
  if(!crop.img)return;
  const old=crop.zoom,nz=Number(e.target.value)/100,cx=cropCv.width/2,cy=cropCv.height/2;
  crop.x=cx-(cx-crop.x)*(nz/old);crop.y=cy-(cy-crop.y)*(nz/old);crop.zoom=nz;drawCrop();
});
cropCv.addEventListener('pointerdown',e=>{cropCv.setPointerCapture(e.pointerId);crop.drag={x:e.clientX,y:e.clientY,ox:crop.x,oy:crop.y};});
cropCv.addEventListener('pointermove',e=>{
  if(!crop.drag)return;const k=cropCv.width/cropCv.getBoundingClientRect().width;
  crop.x=crop.drag.ox+(e.clientX-crop.drag.x)*k;crop.y=crop.drag.oy+(e.clientY-crop.drag.y)*k;drawCrop();
});
['pointerup','pointercancel'].forEach(ev=>cropCv.addEventListener(ev,()=>{crop.drag=null;}));
$('#cropSave').addEventListener('click',async()=>{
  if(!crop.img)return;
  const out=document.createElement('canvas');out.width=out.height=256;
  out.getContext('2d').drawImage(cropCv,0,0,256,256);
  try{
    const d=await api('/api/profile/avatar',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:out.toDataURL('image/jpeg',0.86)})});
    currentUser.avatar=d.avatar;applyProfile();closeCrop();
  }catch(err){$('#cropError').textContent=err.message;}
});
$('#photoRemoveBtn').addEventListener('click',async()=>{
  if(!confirm('Remove your profile photo?'))return;
  try{const d=await api('/api/profile/avatar',{method:'DELETE'});currentUser.avatar=d.avatar;applyProfile();}
  catch(err){alert(err.message);}
});

async function logout(callApi=true){
  if(callApi&&token){try{await fetch('/api/logout',{method:'POST',headers:authHeaders()});}catch{}}
  token='';currentUser=null;ttClasses=[];deadlines=[];chatHistory=[];localStorage.removeItem('campusai_token');showScreen('authScreen');setAuthMode(false);
}
$('#logoutBtn').onclick=()=>logout();
async function checkHealth(){
  try{const d=await fetch('/api/health').then(r=>r.json());$('#connection').textContent=`AI model · ${d.model}`;$('#sidebarStatus').title=`AI connection: ${d.base_url} (click to change)`;}
  catch{$('#connection').textContent='Backend unavailable';}
}
function switchView(view){
  ['chat','timetable','deadlines','trace','evaluation','tools','profile'].forEach(v=>$('#'+v+'View').classList.toggle('hidden',v!==view));
  document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  $('#pageTitle').textContent={chat:'AI Assistant',timetable:'Timetable',deadlines:'Exams & deadlines',trace:'Decision trace',evaluation:'Evaluation',tools:'Campus tools',profile:'Profile'}[view];
  closeSidebarOnMobile();window.scrollTo(0,0);
  if(view==='profile'){ $('#profileName').value=currentUser?.name||''; $('#profileCourse').value=currentUser?.course||''; $('#profileError').textContent=''; }
  if(view==='trace')renderTrace(latestTrace);
  if(view==='evaluation')loadEvaluation();
  if(view==='timetable')loadTimetable();
  if(view==='deadlines')loadDeadlines();
  if(view==='tools')renderStudyTasks();
}
const mobileMQ=window.matchMedia('(max-width: 700px)');
function setSidebar(){
  const shell=$('#appShell'),btn=$('#sidebarToggle');
  const open=mobileMQ.matches?shell.classList.contains('sidebar-open'):!shell.classList.contains('sidebar-collapsed');
  btn.setAttribute('aria-expanded',String(open));
  btn.title=mobileMQ.matches?(open?'Close menu':'Open menu'):(open?'Collapse sidebar':'Expand sidebar');
}
function closeSidebarOnMobile(){if(mobileMQ.matches){$('#appShell').classList.remove('sidebar-open');setSidebar();}}
function toggleSidebar(){
  const shell=$('#appShell');
  if(mobileMQ.matches)shell.classList.toggle('sidebar-open');
  else{shell.classList.toggle('sidebar-collapsed');try{localStorage.setItem('campusai_sidebar',shell.classList.contains('sidebar-collapsed')?'collapsed':'expanded');}catch{}}
  setSidebar();
}
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
$('#sidebarToggle').addEventListener('click',toggleSidebar);
$('#sidebarBackdrop').addEventListener('click',closeSidebarOnMobile);
$('#profileChip').addEventListener('click',()=>switchView('profile'));
document.addEventListener('keydown',e=>{
  if(e.key!=='Escape')return;
  if(!$('#classModal').classList.contains('hidden'))closeClassModal();
  else if(!$('#deadlineModal').classList.contains('hidden'))closeDeadlineModal();
  else if(!$('#aiModal').classList.contains('hidden'))closeAiSettings();
  else if(!$('#cropModal').classList.contains('hidden'))closeCrop();
  else closeSidebarOnMobile();
});
mobileMQ.addEventListener('change',()=>{$('#appShell').classList.remove('sidebar-open');setSidebar();});
if(localStorage.getItem('campusai_sidebar')==='collapsed')$('#appShell').classList.add('sidebar-collapsed');
setSidebar();
document.querySelectorAll('.suggestion').forEach(b=>b.addEventListener('click',()=>sendMessage(b.dataset.prompt)));
function mdLite(src){
  let t=esc(src);
  t=t.replace(/```(?:[\w+-]+)?\n?([\s\S]*?)(```|$)/g,(_,c)=>`<pre><code>${c.replace(/\n$/,'')}</code></pre>`);
  t=t.replace(/`([^`\n]+)`/g,'<code>$1</code>');
  t=t.replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
  t=t.replace(/(^|[\s(])\*([^*\s][^*\n]*)\*(?=[\s).,!?:;]|$)/g,'$1<em>$2</em>');
  t=t.replace(/^[ \t]*[-*]\s+/gm,'• ');
  t=t.replace(/^#{1,4}\s+(.+)$/gm,'<strong>$1</strong>');
  return t;
}
function addMessage(text,who='assistant',cls=''){
  const wrap=document.createElement('div');wrap.className=who==='user'?'user-message':'assistant-message';
  if(who==='assistant'){const av=document.createElement('div');av.className='mini-avatar';av.textContent='C';wrap.appendChild(av);}
  const bubble=document.createElement('div');bubble.className='bubble '+cls;
  const strong=document.createElement('strong');strong.textContent=who==='user'?'You':'CampusAI';
  const body=document.createElement('div');body.className='msg-text';
  if(who==='user')body.textContent=text;else body.innerHTML=text?mdLite(text):'';
  bubble.append(strong,body);wrap.appendChild(bubble);$('#messages').appendChild(wrap);scrollChat();
  return {wrap,bubble,body};
}
function scrollChat(){const m=$('#messages');m.scrollTop=m.scrollHeight;}

function renderTrace(trace){
  const root=$('#traceContent');root.innerHTML='';
  if(!trace||!trace.length){root.className='empty-state';root.textContent='Send a request in AI Assistant to generate a decision trace.';return;}
  root.className='trace-list';
  trace.forEach(t=>{const card=document.createElement('div');card.className='trace-item';
    const title=document.createElement('strong');title.textContent=t.event.replaceAll('_',' ').toUpperCase();
    const tm=document.createElement('small');tm.textContent=t.timestamp;
    const pre=document.createElement('pre');pre.textContent=JSON.stringify(t.details,null,2);
    card.append(title,tm,pre);root.appendChild(card);});
}

let chatHistory=[],chatBusy=false,chatAbort=null;
const initialMessagesHTML=$('#messages').innerHTML;
function setBusy(b){
  chatBusy=b;const btn=document.querySelector('#chatForm .send');
  btn.classList.toggle('stop',b);btn.innerHTML=b?'Stop ■':'Send <span>↗</span>';
}
async function sendMessage(message){
  if(chatBusy)return;
  $('#chatView').classList.add('chatting');
  addMessage(message,'user');$('#pendingArea').innerHTML='';
  const {bubble,body}=addMessage('','assistant');
  body.innerHTML='<span class="typing"><i></i><i></i><i></i></span>';
  setBusy(true);chatAbort=new AbortController();
  let text='',finalMsg=null,failed=false;
  const onEvent=ev=>{
    if(ev.type==='status'){if(!text){body.classList.add('status');body.textContent=ev.text;}}
    else if(ev.type==='delta'){body.classList.remove('status');text+=ev.text;body.innerHTML=mdLite(text);scrollChat();}
    else if(ev.type==='reset'){text='';body.innerHTML='<span class="typing"><i></i><i></i><i></i></span>';}
    else if(ev.type==='error'){failed=true;bubble.classList.add('error');body.classList.remove('status');body.textContent=ev.message;
      const b=document.createElement('button');b.type='button';b.className='secondary-btn open-ai-settings';b.textContent='⚙ Open AI settings';b.onclick=openAiSettings;bubble.appendChild(b);}
    else if(ev.type==='done'){
      finalMsg=ev.message;body.classList.remove('status');body.innerHTML=mdLite(ev.message||'');
      currentTask=ev.task_id;latestTrace=ev.trace||[];renderTrace(latestTrace);
      if(ev.pending)showApproval(ev.pending,ev.task_id);
    }
  };
  try{
    const r=await fetch('/api/chat/stream',{method:'POST',headers:authHeaders({'Content-Type':'application/json'}),signal:chatAbort.signal,
      body:JSON.stringify({message,mode:$('#mode').value,history:chatHistory.slice(-12)})});
    if(!r.ok){if(r.status===401)logout(false);const d=await r.json().catch(()=>({}));throw new Error(errText(d));}
    const reader=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){
      const {value,done}=await reader.read();if(done)break;
      buf+=dec.decode(value,{stream:true});
      let i;while((i=buf.indexOf('\n'))>=0){const line=buf.slice(0,i).trim();buf=buf.slice(i+1);if(line){let ev=null;try{ev=JSON.parse(line);}catch{}if(ev){try{onEvent(ev);}catch(e){console.error('chat event error',e);}}}}
    }
  }catch(err){
    if(err.name==='AbortError'){body.classList.remove('status');body.innerHTML=text?mdLite(text):'<em>Stopped.</em>';}
    else{failed=true;bubble.classList.add('error');body.classList.remove('status');body.textContent='Error: '+err.message;}
  }finally{setBusy(false);chatAbort=null;scrollChat();}
  const reply=finalMsg||text;
  if(!failed&&reply)chatHistory.push({role:'user',content:message},{role:'assistant',content:reply});
  if(chatHistory.length>24)chatHistory=chatHistory.slice(-24);
}
function newChat(){
  if(chatAbort)chatAbort.abort();
  chatHistory=[];$('#messages').innerHTML=initialMessagesHTML;$('#pendingArea').innerHTML='';
  $('#chatView').classList.remove('chatting');$('#userInput').value='';$('#userInput').style.height='';$('#userInput').focus();
}
function showApproval(pending,taskId){
  const box=document.createElement('div');box.className='pending';
  const title=document.createElement('strong');title.textContent='Your approval is required';
  const desc=document.createElement('div');desc.textContent=pending.summary?`CampusAI wants to ${pending.summary}. This changes your saved records.`:`CampusAI wants to run ${pending.tool} with: ${JSON.stringify(pending.arguments)}. This changes your saved records.`;
  const actions=document.createElement('div');actions.className='pending-actions';
  const yes=document.createElement('button');yes.className='approve';yes.textContent='Approve';
  const no=document.createElement('button');no.className='cancel';no.textContent='Cancel';
  yes.onclick=()=>confirmAction(taskId,true,box);no.onclick=()=>confirmAction(taskId,false,box);
  actions.append(yes,no);box.append(title,desc,actions);$('#pendingArea').appendChild(box);
}
async function confirmAction(taskId,approve,box){
  try{
    const d=await api('/api/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:taskId,approve})});
    box.remove();addMessage(approve?d.message:'Okay, I cancelled that. Nothing was changed.');
    if(approve)chatHistory.push({role:'assistant',content:d.message});
    latestTrace=d.trace||[];renderTrace(latestTrace);loadTimetable();
  }catch(err){addMessage('Approval error: '+err.message);}
}
$('#chatForm').addEventListener('submit',e=>{
  e.preventDefault();
  if(chatBusy){if(chatAbort)chatAbort.abort();return;}
  const value=$('#userInput').value.trim();if(!value)return;
  $('#userInput').value='';$('#userInput').style.height='';sendMessage(value);
});
$('#userInput').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#chatForm').requestSubmit();}
});
$('#userInput').addEventListener('input',e=>{const t=e.target;t.style.height='auto';t.style.height=Math.min(t.scrollHeight,140)+'px';});
$('#newChatBtn').addEventListener('click',newChat);

function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
const DAYS=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
const BASE_SLOTS=['09:20','10:10','11:00','11:50','12:40','13:30','14:20','15:10','16:00','16:50'];
let ttClasses=[],ttBounds=[],dragId=null;
const toMin=t=>{const [h,m]=String(t).split(':').map(Number);return h*60+m;};
const fmt12=t=>{const [h,m]=t.split(':').map(Number);return `${((h+11)%12)+1}:${String(m).padStart(2,'0')} ${h<12?'AM':'PM'}`;};
function hueFor(subject){
  const key=String(subject).split(/\s+[—-]\s+/)[0].trim().toUpperCase();let h=0;
  for(const ch of key)h=(h*31+ch.charCodeAt(0))%360;return h;
}
function buildBoundaries(classes){
  const set=new Set(BASE_SLOTS);
  classes.forEach(c=>{set.add(c.start_time);set.add(c.end_time);});
  let list=[...set].sort((a,b)=>toMin(a)-toMin(b));
  return list;
}
function renderTimetable(){
  const root=$('#classList');
  const hasSunday=ttClasses.some(c=>c.day==='Sunday');
  const days=hasSunday?DAYS:DAYS.slice(0,6);
  const bounds=buildBoundaries(ttClasses);
  const idx=Object.fromEntries(bounds.map((t,i)=>[t,i]));
  ttBounds=bounds;
  const todayName=DAYS[(new Date().getDay()+6)%7];
  let html='<table class="sheet"><thead><tr><th class="corner">Day / Time</th>';
  for(let i=0;i<bounds.length-1;i++)html+=`<th>${esc(bounds[i])}<small>to ${esc(bounds[i+1])}</small></th>`;
  html+='</tr></thead><tbody>';
  for(const day of days){
    const row=ttClasses.filter(c=>c.day===day).sort((a,b)=>toMin(a.start_time)-toMin(b.start_time));
    html+=`<tr class="${day===todayName?'today':''}"><th scope="row">${day.slice(0,3)}<small>${day===todayName?'Today':day}</small></th>`;
    let col=0,k=0;
    while(col<bounds.length-1){
      const c=row[k];
      if(c&&idx[c.start_time]===col){
        const span=Math.max(1,idx[c.end_time]-col),parts=String(c.subject).split(/\s+[—-]\s+/),code=parts.length>1?parts[0]:c.subject,name=parts.length>1?parts.slice(1).join(' — '):'';
        html+=`<td class="filled" draggable="true" colspan="${span}" data-id="${c.class_id}" tabindex="0" title="${esc(c.subject)}\n${esc(fmt12(c.start_time))} – ${esc(fmt12(c.end_time))}"><div class="slot" style="--h:${hueFor(c.subject)}"><b>${esc(code)}</b>${name?`<span>${esc(name)}</span>`:''}<em><span class="room">${esc(c.room||'')}</span>${c.room&&c.instructor?' · ':''}${esc(c.instructor||'')}</em></div></td>`;
        col+=span;k++;
      }else if(c&&idx[c.start_time]<col){k++;}
      else{html+=`<td class="empty" data-day="${day}" data-start="${bounds[col]}" data-end="${bounds[col+1]}" tabindex="0"></td>`;col++;}
    }
    html+='</tr>';
  }
  html+='</tbody></table>';
  root.innerHTML=html;
  const pick=el=>{root.querySelectorAll('.sel').forEach(x=>x.classList.remove('sel'));el.classList.add('sel');};
  root.querySelectorAll('td.filled').forEach(td=>{
    const open=()=>{pick(td);const c=ttClasses.find(x=>x.class_id===Number(td.dataset.id));if(c)editClass(c.class_id,c);};
    td.onclick=open;td.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}};
  });
  root.querySelectorAll('td.empty').forEach(td=>{
    const open=()=>{pick(td);openEditor({day:td.dataset.day,start:td.dataset.start,end:td.dataset.end});};
    td.onclick=open;td.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}};
  });
  wireDnD(root);
}
async function loadTimetable(){
  if(!token)return;
  const root=$('#classList');
  if(!ttClasses.length)root.innerHTML='<div class="empty-state">Loading timetable…</div>';
  try{
    const d=await api('/api/timetable?day=all');ttClasses=d.classes||[];renderTimetable();
  }catch(err){root.innerHTML=`<div class="empty-state">${esc(err.message)}</div>`;}
}
function openEditor(pre={}){
  editingClassId=null;$('#editorTitle').textContent='Add a class';$('#classForm').reset();
  if(pre.day)$('#classDay').value=pre.day;if(pre.start)$('#classStart').value=pre.start;if(pre.end)$('#classEnd').value=pre.end;
  $('#deleteClassBtn').classList.add('hidden');$('#copyRow').classList.add('hidden');$('#classError').textContent='';
  $('#classModal').classList.remove('hidden');$('#classSubject').focus();
}
function closeClassModal(){$('#classModal').classList.add('hidden');document.querySelectorAll('#classList .sel').forEach(x=>x.classList.remove('sel'));}
$('#addClassBtn').onclick=()=>openEditor();
$('#cancelClassBtn').onclick=closeClassModal;$('#closeClassBtn').onclick=closeClassModal;
$('#classModal').addEventListener('mousedown',e=>{if(e.target.id==='classModal')closeClassModal();});
$('#cropModal').addEventListener('mousedown',e=>{if(e.target.id==='cropModal')closeCrop();});
function editClass(id,c){
  editingClassId=id;$('#editorTitle').textContent='Edit class';$('#classSubject').value=c.subject;$('#classDay').value=c.day;
  $('#classStart').value=c.start_time;$('#classEnd').value=c.end_time;$('#classRoom').value=c.room||'';$('#classInstructor').value=c.instructor||'';
  $('#deleteClassBtn').classList.remove('hidden');$('#classError').textContent='';
  $('#copyRow').classList.remove('hidden');
  document.querySelectorAll('#dayChecks input').forEach(i=>{i.checked=false;i.disabled=(i.value===c.day);});
  $('#classModal').classList.remove('hidden');$('#classSubject').focus();
}
async function deleteClass(id){
  if(!confirm('Delete this class from your timetable?'))return;
  try{await api('/api/timetable/'+id,{method:'DELETE'});closeClassModal();loadTimetable();}catch(err){$('#classError').textContent=err.message;}
}
$('#deleteClassBtn').onclick=()=>{if(editingClassId)deleteClass(editingClassId);};
$('#classForm').addEventListener('submit',async e=>{
  e.preventDefault();$('#classError').textContent='';
  const body={subject:$('#classSubject').value.trim(),day:$('#classDay').value,start_time:$('#classStart').value,end_time:$('#classEnd').value,room:$('#classRoom').value.trim(),instructor:$('#classInstructor').value.trim()};
  try{
    await api(editingClassId?'/api/timetable/'+editingClassId:'/api/timetable',{method:editingClassId?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    closeClassModal();loadTimetable();
  }catch(err){$('#classError').textContent=err.message;}
});

// ---------- Toast ----------
let toastTimer=null;
function toast(msg,isError=false){
  const t=$('#toast');t.textContent=msg;t.classList.toggle('error',!!isError);t.classList.add('show');
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('show'),isError?4200:2600);
}

// ---------- Timetable: drag to move, Ctrl/Option-drop to copy ----------
function wireDnD(root){
  const idx=Object.fromEntries(ttBounds.map((t,i)=>[t,i]));
  const clear=()=>root.querySelectorAll('.drop-ok,.drop-bad').forEach(x=>x.classList.remove('drop-ok','drop-bad'));
  const plan=(td,copy)=>{
    const c=ttClasses.find(x=>x.class_id===dragId);if(!c)return null;
    const n=Math.max(1,idx[c.end_time]-idx[c.start_time]),col=idx[td.dataset.start],day=td.dataset.day;
    const newStart=ttBounds[col],newEnd=ttBounds[col+n];
    let ok=col+n<=ttBounds.length-1;
    if(ok)ok=!ttClasses.some(x=>x.day===day&&(copy||x.class_id!==dragId)&&toMin(x.start_time)<toMin(newEnd)&&toMin(x.end_time)>toMin(newStart));
    return {c,n,col,day,newStart,newEnd,ok};
  };
  root.querySelectorAll('td.filled').forEach(td=>{
    td.addEventListener('dragstart',e=>{
      dragId=Number(td.dataset.id);td.classList.add('dragging');
      if(e.dataTransfer){e.dataTransfer.effectAllowed='copyMove';e.dataTransfer.setData('text/plain',String(dragId));}
    });
    td.addEventListener('dragend',()=>{dragId=null;td.classList.remove('dragging');clear();});
  });
  root.querySelectorAll('td.empty').forEach(td=>{
    td.addEventListener('dragover',e=>{
      if(dragId==null)return;e.preventDefault();
      const copy=e.ctrlKey||e.altKey;if(e.dataTransfer)e.dataTransfer.dropEffect=copy?'copy':'move';
      clear();const p=plan(td,copy);if(!p)return;
      const cells=[...td.parentElement.querySelectorAll('td.empty')].filter(x=>{const i=idx[x.dataset.start];return i>=p.col&&i<p.col+p.n;});
      (cells.length?cells:[td]).forEach(x=>x.classList.add(p.ok?'drop-ok':'drop-bad'));
    });
    td.addEventListener('drop',async e=>{
      e.preventDefault();const copy=e.ctrlKey||e.altKey,p=plan(td,copy);clear();
      if(!p)return;
      if(!p.ok){toast('That spot overlaps another class or runs past the last time slot.',true);return;}
      try{
        if(copy){
          await api('/api/timetable',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({day:p.day,subject:p.c.subject,start_time:p.newStart,end_time:p.newEnd,room:p.c.room||'',instructor:p.c.instructor||''})});
          toast('Class copied.');
        }else{
          if(p.day===p.c.day&&p.newStart===p.c.start_time)return;
          await api('/api/timetable/'+p.c.class_id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({day:p.day,start_time:p.newStart,end_time:p.newEnd})});
          toast(`Moved to ${p.day.slice(0,3)} ${p.newStart}.`);
        }
        dragId=null;loadTimetable();
      }catch(err){toast(err.message,true);}
    });
  });
  root.addEventListener('dragleave',e=>{if(!root.contains(e.relatedTarget))clear();});
}

// ---------- Copy a class to other days ----------
$('#copyDaysBtn').addEventListener('click',async()=>{
  const c=ttClasses.find(x=>x.class_id===editingClassId);if(!c)return;
  const days=[...document.querySelectorAll('#dayChecks input:checked')].map(i=>i.value);
  if(!days.length){$('#classError').textContent='Select at least one day to copy to.';return;}
  const ok=[],skipped=[];
  for(const day of days){
    try{
      await api('/api/timetable',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({day,subject:c.subject,start_time:c.start_time,end_time:c.end_time,room:c.room||'',instructor:c.instructor||''})});
      ok.push(day.slice(0,3));
    }catch{skipped.push(day.slice(0,3));}
  }
  await loadTimetable();
  if(!skipped.length){closeClassModal();toast(`Copied to ${ok.join(', ')}.`);}
  else $('#classError').textContent=`Copied to ${ok.join(', ')||'no days'}. Skipped ${skipped.join(', ')} because of an overlap.`;
});

// ---------- Export CSV and print ----------
function csvCell(v){
  let t=String(v??'');if(/^[=+\-@\t\r]/.test(t))t="'"+t;
  return '"'+t.replace(/"/g,'""')+'"';
}
function exportCsv(){
  if(!ttClasses.length){toast('There are no classes to export yet.',true);return;}
  const rows=[['Day','Start','End','Subject','Room','Instructor / notes'],...ttClasses.map(c=>[c.day,c.start_time,c.end_time,c.subject,c.room||'',c.instructor||''])];
  const blob=new Blob(['\ufeff'+rows.map(r=>r.map(csvCell).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='timetable.csv';
  document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1500);
  toast('Timetable exported as CSV.');
}
$('#exportCsvBtn').addEventListener('click',exportCsv);
$('#printBtn').addEventListener('click',()=>window.print());
window.addEventListener('beforeprint',()=>{
  const u=currentUser||{};
  $('#printMeta').textContent=[u.name||u.user_id,u.course,'Printed '+new Date().toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'})].filter(Boolean).join(' · ');
});

// ---------- Exams & deadlines (dates only, no countdown) ----------
let deadlines=[],editingDeadlineId=null;
function todayISO(){const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
function fmtDate(iso){const [y,m,d]=iso.split('-').map(Number);return new Date(y,m-1,d).toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short',year:'numeric'});}
async function loadDeadlines(){
  if(!token)return;
  try{const d=await api('/api/deadlines');deadlines=d.deadlines||[];renderDeadlines();}
  catch(err){$('#deadlineList').innerHTML=`<div class="empty-state">${esc(err.message)}</div>`;}
}
function renderDeadlines(){
  const root=$('#deadlineList');
  if(!deadlines.length){root.innerHTML='<div class="empty-state">No exams or deadlines yet. Select “+ Add” to save your first date.</div>';return;}
  const t=todayISO(),isUp=x=>!x.done&&x.due_date>=t;
  const upcoming=deadlines.filter(isUp),earlier=deadlines.filter(x=>!isUp(x)).sort((a,b)=>b.due_date.localeCompare(a.due_date));
  const row=x=>`<div class="dl-row ${x.done?'done':''}"><input type="checkbox" data-id="${x.id}" ${x.done?'checked':''} aria-label="Mark ${esc(x.title)} as done"><div class="dl-date">${esc(fmtDate(x.due_date))}</div><div class="dl-main"><strong>${esc(x.title)}</strong>${(x.subject||x.notes)?`<small>${esc([x.subject,x.notes].filter(Boolean).join(' · '))}</small>`:''}</div><span class="dl-kind k-${esc(x.kind.toLowerCase())}">${esc(x.kind.toUpperCase())}</span><button class="edit-dl" data-id="${x.id}" type="button">Edit</button></div>`;
  const section=(title,list,empty)=>`<div class="dl-section"><h3>${title}</h3>${list.length?`<div class="dl-rows">${list.map(row).join('')}</div>`:`<div class="empty-state">${empty}</div>`}</div>`;
  root.innerHTML=section('UPCOMING',upcoming,'Nothing upcoming.')+(earlier.length?section('COMPLETED OR PAST',earlier,''):'');
  root.querySelectorAll('input[type=checkbox]').forEach(cb=>cb.onchange=async()=>{
    try{await api('/api/deadlines/'+cb.dataset.id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({done:cb.checked})});loadDeadlines();}
    catch(err){toast(err.message,true);cb.checked=!cb.checked;}
  });
  root.querySelectorAll('.edit-dl').forEach(b=>b.onclick=()=>openDeadlineEditor(deadlines.find(x=>x.id===Number(b.dataset.id))));
}
function openDeadlineEditor(x){
  editingDeadlineId=x?x.id:null;
  $('#dlModalTitle').textContent=x?'Edit exam or deadline':'Add exam or deadline';
  $('#deadlineForm').reset();$('#dlError').textContent='';
  if(x){$('#dlTitle').value=x.title;$('#dlKind').value=x.kind;$('#dlDate').value=x.due_date;$('#dlSubject').value=x.subject||'';$('#dlNotes').value=x.notes||'';}
  $('#dlDelete').classList.toggle('hidden',!x);
  $('#deadlineModal').classList.remove('hidden');$('#dlTitle').focus();
}
function closeDeadlineModal(){$('#deadlineModal').classList.add('hidden');}
$('#addDeadlineBtn').onclick=()=>openDeadlineEditor(null);
$('#dlCancel').onclick=closeDeadlineModal;$('#dlClose').onclick=closeDeadlineModal;
$('#deadlineModal').addEventListener('mousedown',e=>{if(e.target.id==='deadlineModal')closeDeadlineModal();});
$('#deadlineForm').addEventListener('submit',async e=>{
  e.preventDefault();$('#dlError').textContent='';
  const body={title:$('#dlTitle').value.trim(),kind:$('#dlKind').value,due_date:$('#dlDate').value,subject:$('#dlSubject').value.trim(),notes:$('#dlNotes').value.trim()};
  try{
    await api(editingDeadlineId?'/api/deadlines/'+editingDeadlineId:'/api/deadlines',{method:editingDeadlineId?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    closeDeadlineModal();loadDeadlines();
  }catch(err){$('#dlError').textContent=err.message;}
});
$('#dlDelete').onclick=async()=>{
  if(!editingDeadlineId||!confirm('Delete this exam or deadline?'))return;
  try{await api('/api/deadlines/'+editingDeadlineId,{method:'DELETE'});closeDeadlineModal();loadDeadlines();}
  catch(err){$('#dlError').textContent=err.message;}
};


// ---------- AI model settings ----------
function aiResult(html,cls){const r=$('#aiResult');r.className='ai-result '+(cls||'');r.innerHTML=html||'';}
async function openAiSettings(){
  $('#aiModal').classList.remove('hidden');aiResult('');$('#aiKey').value='';
  try{
    const d=await api('/api/settings/model');
    $('#aiUrl').value=d.base_url;$('#aiModel').value=d.model;$('#aiNoThink').checked=d.no_think;
    $('#aiKey').placeholder=d.has_key?'saved: leave empty to keep it':'optional';
  }catch(err){aiResult(esc(err.message),'bad');}
  $('#aiUrl').focus();
}
function closeAiSettings(){$('#aiModal').classList.add('hidden');}
function aiBody(){return {base_url:$('#aiUrl').value.trim(),model:$('#aiModel').value.trim(),no_think:$('#aiNoThink').checked,...($('#aiKey').value?{api_key:$('#aiKey').value}:{})};}
$('#aiSettingsBtn').addEventListener('click',openAiSettings);
$('#sidebarStatus').addEventListener('click',openAiSettings);
$('#aiClose').onclick=closeAiSettings;$('#aiCancel').onclick=closeAiSettings;
$('#aiModal').addEventListener('mousedown',e=>{if(e.target.id==='aiModal')closeAiSettings();});
$('#aiTest').addEventListener('click',async()=>{
  const btn=$('#aiTest');btn.disabled=true;btn.textContent='Testing…';aiResult('Contacting the model server. The first test can take a while if the model has to load…','info');
  try{
    const d=await api('/api/settings/model/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(aiBody())});
    if(d.suggested_base_url){$('#aiUrl').value=d.suggested_base_url;}
    const list=$('#aiModels');list.innerHTML='';(d.models||[]).forEach(m=>{const o=document.createElement('option');o.value=m;list.appendChild(o);});
    const chips=(d.models||[]).length?`<div class="chips">${d.models.slice(0,12).map(m=>`<button type="button" class="chip" data-model="${esc(m)}">${esc(m)}</button>`).join('')}</div>`:'';
    const notes=[];
    if(d.suggested_base_url)notes.push(`The URL was missing <b>/v1</b>, so I changed it to <b>${esc(d.suggested_base_url)}</b>. Press Save to keep it.`);
    if(d.model_found===false)notes.push('That model name is not in the server\'s list. Pick one below.');
    if(d.thinking_detected&&d.ok)notes.push('This model is thinking before it answers, which makes replies slower. Keep “Disable thinking” ticked.');
    if(d.ok)aiResult(`✓ Connected${d.latency_ms?` · replied in ${(d.latency_ms/1000).toFixed(1)} s`:''}${d.reply_preview?`<br>“${esc(d.reply_preview)}”`:''}${notes.length?'<br>'+notes.join('<br>'):''}${chips}`,'ok');
    else aiResult(`${esc(d.error||'The test failed.')}${notes.length?'<br>'+notes.join('<br>'):''}${chips}`,'bad');
    $('#aiResult').querySelectorAll('.chip').forEach(c=>c.onclick=()=>{$('#aiModel').value=c.dataset.model;});
  }catch(err){aiResult(esc(err.message),'bad');}
  finally{btn.disabled=false;btn.textContent='Test connection';}
});
$('#aiForm').addEventListener('submit',async e=>{
  e.preventDefault();
  try{
    const d=await api('/api/settings/model',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(aiBody())});
    $('#aiKey').value='';aiResult('✓ Saved. New chats will use this model.','ok');checkHealth();toast('AI settings saved.');
  }catch(err){aiResult(esc(err.message),'bad');}
});

// ---------- Accent colour ----------
const ACCENTS={indigo:'#5968e8',blue:'#2f80ed',teal:'#0f9d8f',green:'#2f9e44',orange:'#e8730c',rose:'#e11d6a',purple:'#8b5cf6'};
function applyAccent(name){
  if(!ACCENTS[name])name='indigo';
  if(name==='indigo')delete document.documentElement.dataset.accent;else document.documentElement.dataset.accent=name;
  try{localStorage.setItem('campusai_accent',name);}catch{}
  document.querySelectorAll('#accentPicker .swatch').forEach(b=>b.setAttribute('aria-checked',String(b.dataset.accent===name)));
}
(function buildAccentPicker(){
  const root=$('#accentPicker');
  Object.entries(ACCENTS).forEach(([name,color])=>{
    const b=document.createElement('button');b.type='button';b.className='swatch';b.dataset.accent=name;
    b.style.background=color;b.title=name.charAt(0).toUpperCase()+name.slice(1);b.setAttribute('role','radio');b.setAttribute('aria-label',b.title);
    b.onclick=()=>applyAccent(name);root.appendChild(b);
  });
  let saved='indigo';try{saved=localStorage.getItem('campusai_accent')||'indigo';}catch{}
  applyAccent(saved);
})();

function calculateAttendance(){
  const attended=Number($('#attendedInput').value),total=Number($('#totalInput').value),target=Number($('#targetInput').value),out=$('#attendanceResult');
  if(!Number.isInteger(attended)||!Number.isInteger(total)||attended<0||total<0||attended>total||target<=0||target>100){out.textContent='Enter valid class counts and a target between 1 and 100%.';return;}
  const pct=total?attended/total*100:0;
  if(pct>=target){out.textContent=`Current attendance: ${pct.toFixed(2)}%. You are at or above your ${target}% target.`;return;}
  const needed=Math.ceil((target*total/100-attended)/(1-target/100));
  out.textContent=`Current attendance: ${pct.toFixed(2)}%. Attend the next ${needed} class${needed===1?'':'es'} consecutively to reach at least ${target}%.`;
}
function calculateCgpa(){
  const grades=$('#gradePointsInput').value.split(',').map(x=>Number(x.trim()));
  const credits=$('#creditsInput').value.split(',').map(x=>Number(x.trim()));
  const out=$('#cgpaResult');
  if(!grades.length||grades.length!==credits.length||grades.some(x=>!Number.isFinite(x)||x<0||x>10)||credits.some(x=>!Number.isFinite(x)||x<=0)){out.textContent='Enter matching comma-separated grade points (0–10) and positive credits.';return;}
  const total=credits.reduce((a,b)=>a+b,0),score=grades.reduce((sum,g,i)=>sum+g*credits[i],0)/total;
  out.textContent=`Weighted CGPA: ${score.toFixed(2)} / 10 across ${total} credits.`;
}
function getStudyTasks(){try{return JSON.parse(localStorage.getItem('campusai_study_tasks')||'[]');}catch{return [];}}
function saveStudyTasks(tasks){localStorage.setItem('campusai_study_tasks',JSON.stringify(tasks));}
function renderStudyTasks(){
  const root=$('#studyTaskList');if(!root)return;const tasks=getStudyTasks();root.innerHTML='';
  if(!tasks.length){const li=document.createElement('li');li.className='empty-task';li.textContent='No tasks yet. Add one above.';root.appendChild(li);return;}
  tasks.forEach((task,index)=>{const li=document.createElement('li');li.className=task.done?'task-done':'';const label=document.createElement('label');const check=document.createElement('input');check.type='checkbox';check.checked=!!task.done;check.onchange=()=>{const all=getStudyTasks();all[index].done=check.checked;saveStudyTasks(all);renderStudyTasks();};const span=document.createElement('span');span.textContent=task.text;label.append(check,span);const del=document.createElement('button');del.type='button';del.className='task-delete';del.textContent='Remove';del.onclick=()=>{const all=getStudyTasks();all.splice(index,1);saveStudyTasks(all);renderStudyTasks();};li.append(label,del);root.appendChild(li);});
}
$('#attendanceCalcBtn').addEventListener('click',calculateAttendance);
$('#cgpaCalcBtn').addEventListener('click',calculateCgpa);
$('#studyTaskForm').addEventListener('submit',e=>{e.preventDefault();const value=$('#studyTaskInput').value.trim();if(!value)return;const tasks=getStudyTasks();tasks.push({text:value,done:false});saveStudyTasks(tasks);$('#studyTaskInput').value='';renderStudyTasks();});
calculateAttendance();calculateCgpa();

async function loadEvaluation(){
  const root=$('#evalContent');root.innerHTML='';
  try{
    const d=await api('/api/evaluation');const entries=[['AI Agent',d.agent],['Rule-based baseline',d.baseline]];
    if(!entries.some(x=>x[1])){root.className='';root.innerHTML='<div class="empty-state">No evaluation data yet. Run tasks in both modes first.</div>';return;}
    root.className='metric-grid';entries.forEach(([name,v])=>{const card=document.createElement('div');card.className='metric';card.innerHTML=`<h3>${name}</h3><div class="number">${v?.rate??0}%</div><p>${v?.completed??0} completed out of ${v?.total??0} tasks</p>`;root.appendChild(card);});
  }catch(err){root.innerHTML=`<div class="empty-state">${esc(err.message)}</div>`;}
}
function applyTheme(theme){
  document.documentElement.dataset.theme=theme;
  localStorage.setItem('campusai_theme',theme);
  const icon=theme==='dark'?'☀':'☾';
  $('#themeToggle').textContent=icon;$('#authThemeToggle').textContent=icon;
}
function toggleTheme(){applyTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');}
$('#themeToggle').onclick=toggleTheme;
$('#authThemeToggle').onclick=toggleTheme;
const savedTheme=localStorage.getItem('campusai_theme')||'light';
applyTheme(savedTheme);

(async()=>{
  if(token){try{await loadUser();}catch{showScreen('authScreen');}}
  else showScreen('authScreen');
})();
