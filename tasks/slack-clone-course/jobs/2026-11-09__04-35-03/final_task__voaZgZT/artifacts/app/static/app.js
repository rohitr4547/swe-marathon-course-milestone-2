let state={token:localStorage.getItem('huddle.token'),user:null,workspace:null,channel:null,signup:true};
function api(method,path,body){let o={method,headers:{}};if(state.token)o.headers.Authorization='Bearer '+state.token;if(body!==undefined){o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}return fetch('/api'+path,o).then(async r=>({ok:r.ok,status:r.status,data:await r.json()}))}
function el(s){return document.querySelector(s)}
function showAuthError(t){el('[data-testid="auth-error"]').textContent=t||''}
function renderUser(){el('[data-testid="current-user"]').textContent=state.user?(state.user.display_name||state.user.username):''}
function setAuthVisible(v){el('[data-testid="auth-modal"]').style.display=v?'block':'none'}
function renderChannels(channels){let box=el('[data-testid="channel-list"]');box.innerHTML='';channels.forEach(c=>{let b=document.createElement('button');b.className='channel-entry';b.dataset.testid='channel-entry';b.dataset.channelId=c.id;b.dataset.channelName=c.name;b.textContent='#'+c.name;b.onclick=()=>selectChannel(c);box.appendChild(b)})}
async function loadWorkspace(){let r=await api('GET','/workspaces');if(!r.ok||!r.data.workspaces.length){if(state.workspace)return;state.workspace=null;el('[data-testid="workspace-header"]').textContent='No workspace';let wf=el('[data-testid="workspace-create-form"]');wf.hidden=false;wf.style.display='block';return}state.workspace=r.data.workspaces[0];el('[data-testid="workspace-create-form"]').hidden=false;el('[data-testid="workspace-create-form"]').style.display='block';let d=await api('GET','/workspaces/'+state.workspace.slug);el('[data-testid="workspace-header"]').textContent=state.workspace.name;renderChannels(d.data.channels||[])}
function humanTime(t){try{return new Date(t).toLocaleString()}catch(e){return ''}}
function renderMessages(ms){
 let box=el('[data-testid="message-list"]');box.innerHTML='';
 el('[data-testid="channel-empty-state"]').textContent=ms.length?'':'No messages yet — start the conversation.';
 ms.reverse().forEach(m=>{
  let row=document.createElement('div');row.className='message';row.dataset.testid='message';row.dataset.messageId=m.id;
  let av=document.createElement('span');av.dataset.testid='message-avatar';av.textContent='🙂';
  let au=document.createElement('b');au.dataset.testid='message-author';au.textContent=(m.author||{}).display_name||(m.author||{}).username||'User';
  let tm=document.createElement('time');tm.dataset.testid='message-time';tm.textContent=humanTime(m.created_at);
  let body=document.createElement('span');body.className='message-body';body.dataset.testid='message-body';body.textContent=m.body;
  let bar=document.createElement('div');bar.className='toolbar';
  let edit=document.createElement('button');edit.dataset.testid='edit-message-btn';edit.textContent='Edit';edit.onclick=()=>editMessage(row,m);
  let del=document.createElement('button');del.dataset.testid='delete-message-btn';del.textContent='Delete';del.onclick=()=>{let x=new XMLHttpRequest();x.open('DELETE','/api/messages/'+m.id,false);x.setRequestHeader('Authorization','Bearer '+state.token);x.send();if(x.status>=200&&x.status<300)row.remove();};
  let thread=document.createElement('button');thread.dataset.testid='open-thread-btn';thread.textContent='Thread';thread.onclick=()=>openThread(m);
  let react=document.createElement('button');react.dataset.testid='reaction-button';react.textContent='😊';react.onclick=()=>showPicker(row,m);
  bar.append(edit,del,thread,react);row.append(av,au,tm,body,bar);
  if(m.edited_at){let ed=document.createElement('span');ed.dataset.testid='message-edited';ed.textContent=' edited';row.append(ed)}
  if(m.reply_count){let rc=document.createElement('span');rc.dataset.testid='reply-count';rc.textContent=m.reply_count+' repl'+(m.reply_count==1?'y':'ies');row.append(rc)}
  (m.reactions||[]).forEach(x=>{let chip=document.createElement('span');chip.dataset.testid='reaction-chip';chip.textContent=x.emoji+' '+x.count;row.append(chip)})
  box.appendChild(row)
 })
}
async function selectChannel(c){if(state.workspace){let wr=await api('GET','/workspaces/'+state.workspace.slug);let fresh=(wr.data.channels||[]).find(x=>String(x.id)===String(c.id));if(fresh)c=fresh}state.channel=c;el('[data-testid="channel-title"]').textContent='#'+c.name;el('[data-testid="channel-topic"]').textContent=c.topic||'';let r=await api('GET','/channels/'+c.id+'/messages');renderMessages((r.data.messages||[]))}
async function editMessage(row,m){let input=document.createElement('input');input.dataset.testid='edit-message-input';input.value=m.body;row.querySelector('.message-body').replaceWith(input);input.focus();input.onkeydown=async e=>{if(e.key==='Enter'){await api('PATCH','/messages/'+m.id,{body:input.value});await selectChannel(state.channel)}}}
async function openThread(m){let panel=el('[data-testid="thread-panel"]');panel.style.display='block';panel.dataset.parentId=m.id;let r=await api('GET','/messages/'+m.id+'/replies');panel.querySelector('div').innerHTML='';(r.data.replies||[]).forEach(x=>{let d=document.createElement('div');d.textContent=x.body;panel.querySelector('div').appendChild(d)})}
function showPicker(row,m){let p=document.createElement('div');p.dataset.testid='emoji-picker';['😀','👍','❤️','🎉','🚀','😂'].forEach(e=>{let b=document.createElement('button');b.dataset.testid='emoji-option';b.textContent=e;b.onclick=()=>{let x=new XMLHttpRequest();x.open('POST','/api/messages/'+m.id+'/reactions',false);x.setRequestHeader('Authorization','Bearer '+state.token);x.setRequestHeader('Content-Type','application/json');x.send(JSON.stringify({emoji:e}));if(x.status>=200&&x.status<300){let chip=document.createElement('span');chip.dataset.testid='reaction-chip';chip.textContent=e+' 1';row.appendChild(chip);p.remove()}};p.appendChild(b)});row.appendChild(p)}
async function sendMessage(e){e.preventDefault();let i=el('[data-testid="message-input"]');if(state.channel&&i.value.trim()){let r=await api('POST','/channels/'+state.channel.id+'/messages',{body:i.value});if(r.ok){i.value='';selectChannel(state.channel)}}}

async function submitAuth(e){e.preventDefault();let f=e.target;let path=f.dataset.mode==='signup'?'/auth/register':'/auth/login';let b={username:f.username.value,password:f.password.value};if(f.display_name)b.display_name=f.display_name.value;let r=await api('POST',path,b);if(!r.ok){showAuthError(r.data.error||'Authentication failed');return}state.token=r.data.token;state.user=r.data.user;localStorage.setItem('huddle.token',state.token);showAuthError('');setAuthVisible(false);renderUser();await loadWorkspace()}
function toggleAuth(){let f=el('[data-testid="auth-form"]');state.signup=f.dataset.mode!=='signup';f.dataset.mode=state.signup?'signup':'login';el('[data-testid="auth-submit"]').textContent=state.signup?'Sign up':'Login';el('[data-testid="auth-toggle"]').textContent=state.signup?'Log in':'Sign up'}
async function createWorkspace(e){e.preventDefault();let f=e.target;let slug=f.elements.namedItem('slug').value;let name=f.elements.namedItem('name').value;let r=await api('POST','/workspaces',{slug:slug,name:name});if(!r.ok){el('[data-testid="auth-error"]').textContent=r.data.error||'Unable to create workspace';return}state.workspace=r.data.workspace;el('[data-testid="workspace-header"]').textContent=state.workspace.name;renderChannels([r.data.general_channel]);await selectChannel(r.data.general_channel);loadWorkspace()}
window.createWorkspace=createWorkspace;
async function createChannel(e){e.preventDefault();if(!state.workspace)return;let f=e.target,r=await api('POST','/workspaces/'+state.workspace.slug+'/channels',{name:f.name.value,is_private:el('[data-testid="create-channel-private"]').checked});if(!r.ok){el('[data-testid="create-channel-error"]').textContent=r.data.error||'Unable to create channel';return}el('[data-testid="create-channel-error"]').textContent='';let d=await api('GET','/workspaces/'+state.workspace.slug);renderChannels(d.data.channels||[]);selectChannel(r.data.channel)}
async function sendMessage(e){e.preventDefault();let i=el('[data-testid="message-input"]');if(state.channel&&i.value.trim()){let r=await api('POST','/channels/'+state.channel.id+'/messages',{body:i.value});if(r.ok){i.value='';selectChannel(state.channel)}}}
function logout(){state.token=null;state.user=null;localStorage.removeItem('huddle.token');setAuthVisible(true);renderUser()}
function init(){
 let q=new URLSearchParams(location.search).get('token');
 if(q){state.token=q;localStorage.setItem('huddle.token',q)}
 let f=el('[data-testid="auth-form"]'); f.dataset.mode='signup'; el('[data-testid="auth-submit"]').textContent='Sign up';
 f.addEventListener('submit',submitAuth);
 el('[data-testid="auth-toggle"]').addEventListener('click',toggleAuth);
 el('[data-testid="logout-btn"]').addEventListener('click',logout);
 let wf=el('[data-testid="workspace-create-form"]'); wf.style.display='block'; wf.hidden=false;
 wf.addEventListener('submit',createWorkspace);
 el('[data-testid="create-channel-form"]').addEventListener('submit',createChannel);
 el('[data-testid="new-channel-btn"]').addEventListener('click',()=>el('[data-testid="create-channel-form"]').style.display='block');
 el('[data-testid="message-input"]').closest('form').addEventListener('submit',sendMessage);el('[data-testid="thread-send"]').addEventListener('click',async e=>{e.preventDefault();let p=el('[data-testid="thread-panel"]'),i=el('[data-testid="thread-input"]');if(p.dataset.parentId&&i.value.trim()){let rr=await api('POST','/channels/'+state.channel.id+'/messages',{body:i.value,parent_id:Number(p.dataset.parentId)});i.value='';await openThread({id:Number(p.dataset.parentId)});if(rr.ok)await selectChannel(state.channel)}});el('[data-testid="close-thread"]').addEventListener('click',()=>el('[data-testid="thread-panel"]').style.display='none');
 if(state.token)api('GET','/auth/me').then(r=>{if(r.ok){state.user=r.data.user;setAuthVisible(false);renderUser();loadWorkspace()}else{state.token=null;localStorage.removeItem('huddle.token')}})
}
init();
