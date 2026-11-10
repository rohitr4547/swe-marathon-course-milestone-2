let state={token:localStorage.getItem('huddle.token'),workspace:null,channels:[],channel:null,messages:[]};
function api(path,opt={}){opt.headers={...(opt.headers||{})};if(state.token)opt.headers.Authorization='Bearer '+state.token;if(opt.body&&typeof opt.body!=='string'){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(opt.body)}return fetch('/api'+path,opt).then(async r=>({status:r.status,data:await r.json().catch(()=>({}))}))}
function $(x){return document.querySelector(x)}
function showAuth(v){$('#auth-modal').style.display=v?'block':'none';$('[data-testid="workspace-create-form"]').style.display=v?'none':'block'}
function render(){if(!state.workspace)return;$('[data-testid="workspace-header"]').textContent=state.workspace.name;let list=$('[data-testid="channel-list"]');list.innerHTML='';state.channels.forEach(c=>{let e=document.createElement('button');e.setAttribute('data-testid','channel-entry');e.dataset.channelId=c.id;e.dataset.channelName=c.name;e.textContent='#'+c.name;e.onclick=()=>selectChannel(c);list.appendChild(e)})}
function bindMessageExtras(row,m){
 let react=row.querySelector('[data-testid="reaction-button"]');
 react.onclick=()=>{let p=document.createElement('div');p.setAttribute('data-testid','emoji-picker');p.innerHTML=['😀','👍','❤️','🎉','🚀','😂'].map(x=>'<button data-testid="emoji-option">'+x+'</button>').join('');row.appendChild(p);p.querySelectorAll('[data-testid="emoji-option"]').forEach(o=>o.onclick=async()=>{let z=await api('/messages/'+m.id+'/reactions',{method:'POST',body:{emoji:o.textContent}});if(z.status<300){let c=document.createElement('span');c.setAttribute('data-testid','reaction-chip');c.textContent=o.textContent+' 1';row.appendChild(c);p.remove()}})};
 row.querySelector('[data-testid="open-thread-btn"]').onclick=()=>{let panel=$('[data-testid="thread-panel"]');panel.style.display='block';let input=$('[data-testid="thread-input"]');let send=$('[data-testid="thread-send"]');send.onclick=async()=>{if(!input.value.trim())return;let z=await api('/channels/'+state.channel.id+'/messages',{method:'POST',body:{body:input.value,parent_id:m.id}});if(z.status<300){let d=document.createElement('div');d.textContent=input.value;panel.appendChild(d);input.value='';row.querySelector('[data-testid="reply-count"]').textContent='1 reply'}}};
 $('[data-testid="close-thread"]').onclick=()=>{$('[data-testid="thread-panel"]').style.display='none'};
}
async function renderMessages(){
 let box=$('[data-testid="message-list"]');
 box.querySelectorAll('[data-testid="message"]').forEach(x=>x.remove());
 state.messages.forEach(m=>{
  let row=document.createElement('div');row.className='message';row.setAttribute('data-testid','message');
  row.innerHTML='<span data-testid="message-avatar">●</span> <span data-testid="message-author"></span> <span data-testid="message-time"></span><div data-testid="message-body"></div><span data-testid="message-edited" style="display:none"> (edited)</span><span data-testid="reply-count">0 replies</span><div class="toolbar"><button data-testid="edit-message-btn">Edit</button><button data-testid="delete-message-btn">Delete</button><button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">React</button></div>';
  row.querySelector('[data-testid="message-author"]').textContent=m.author?.username||'user';
  row.querySelector('[data-testid="message-time"]').textContent=new Date(m.created_at).toLocaleTimeString();
  row.querySelector('[data-testid="message-body"]').textContent=m.body;
  if(m.edited_at)row.querySelector('[data-testid="message-edited"]').style.display='inline';
  row.querySelector('[data-testid="reply-count"]').textContent=(m.reply_count||0)+' '+((m.reply_count||0)==1?'reply':'replies');
  row.querySelector('[data-testid="edit-message-btn"]').onclick=async()=>{
   let inp=document.createElement('input');inp.setAttribute('data-testid','edit-message-input');inp.value=m.body;
   row.querySelector('[data-testid="message-body"]').replaceWith(inp);inp.focus();
   inp.onkeydown=async e=>{if(e.key==='Enter'){let x=await api('/messages/'+m.id,{method:'PATCH',body:{body:inp.value}});if(x.status<300){state.messages=state.messages.map(z=>z.id===m.id?x.data.message:z);renderMessages()}}};
  };
  row.querySelector('[data-testid="delete-message-btn"]').onclick=async()=>{let x=await api('/messages/'+m.id,{method:'DELETE'});if(x.status<300){state.messages=state.messages.filter(z=>z.id!==m.id);renderMessages()}};
  box.appendChild(row);bindMessageExtras(row,m);
 });
 let e=$('[data-testid="channel-empty-state"]');if(e)e.style.display=state.messages.length?'none':'block';
}
async function loadMessages(){if(!state.channel)return;let x=await api('/channels/'+state.channel.id+'/messages');state.messages=x.data.messages||[];renderMessages()}
async function sendMessage(){let i=$('[data-testid="message-input"]'),v=i.value;if(!v.trim())return;let x=await api('/channels/'+state.channel.id+'/messages',{method:'POST',body:{body:v}});if(x.status<300){state.messages.unshift(x.data.message);i.value='';renderMessages()}}
async function selectChannel(c){
 state.channel=c;
 if(state.workspace){
  let fresh=await api('/workspaces/'+state.workspace.slug);
  let found=(fresh.data.channels||[]).find(x=>String(x.id)===String(c.id));
  if(found){state.channels=state.channels.map(x=>String(x.id)===String(found.id)?found:x);state.channel=found}
 }
 $('[data-testid="channel-title"]').textContent='# '+state.channel.name;
 $('[data-testid="channel-topic"]').textContent=state.channel.topic||'';
 $('[data-testid="channel-empty-state"]').style.display='block';
 $('[data-testid="message-list"]').style.display='block';
 await loadMessages()
}
async function loadWorkspace(){let x=await api('/workspaces');if(state.workspace)return;if(x.data.workspaces?.length){let w=x.data.workspaces[0];let d=await api('/workspaces/'+w.slug);state.workspace=w;state.channels=d.data.channels||[];state.channel=null;render();showAuth(false);if(state.channels.length)await selectChannel(state.channels[0])}else{showAuth(false);$('[data-testid="workspace-create-form"]').style.display='none';let e=$('[data-testid="empty-state-create-workspace"]');e.textContent='Create your first workspace';e.style.display='block';e.style.padding='12px';e.style.cursor='pointer';e.style.visibility='visible'}}
async function authSubmit(e){e.preventDefault();let f=e.target,mode=f.dataset.mode||'login';let x=await api('/auth/'+(mode==='signup'?'register':'login'),{method:'POST',body:{username:f.username.value,password:f.password.value}});if(x.status>=400){$('[data-testid="auth-error"]').textContent=x.data.error||'Invalid input';return}state.token=x.data.token;localStorage.setItem('huddle.token',state.token);$('[data-testid="current-user"]').textContent=x.data.user.display_name;showAuth(false);await loadWorkspace()}
function toggleAuth(){let f=$('[data-testid="auth-form"]'), signup=f.dataset.mode!=='signup';f.dataset.mode=signup?'signup':'login';$('#auth-submit').textContent=signup?'Sign up':'Login'}
async function createWorkspace(e){
 e.preventDefault();
 let f=e.target,x=await api('/workspaces',{method:'POST',body:{name:f.name.value,slug:f.slug.value}});
 if(x.status>=400){let z=$('[data-testid="auth-error"]');if(z)z.textContent=x.data.error||'Unable to create workspace';return}
 state.workspace=x.data.workspace;state.channels=[x.data.general_channel];state.channel=x.data.general_channel;
 $('[data-testid="workspace-header"]').textContent=state.workspace.name;
 let list=$('[data-testid="channel-list"]');list.innerHTML='';
 let entry=document.createElement('button');entry.setAttribute('data-testid','channel-entry');entry.dataset.channelId=state.channel.id;entry.dataset.channelName='general';entry.textContent='#general';entry.onclick=()=>selectChannel(state.channel);list.appendChild(entry);
 $('[data-testid="workspace-create-form"]').style.display='none';$('[data-testid="empty-state-create-workspace"]').style.display='none';
 selectChannel(state.channel);
}
async function createChannel(e){e.preventDefault();let f=e.target,x=await api('/workspaces/'+state.workspace.slug+'/channels',{method:'POST',body:{name:f.name.value,is_private:$('[data-testid="create-channel-private"]').checked}});if(x.status>=400){$('[data-testid="create-channel-error"]').textContent=x.data.error||'Channel already exists';return}state.channels.push(x.data.channel);state.channel=x.data.channel;$('[data-testid="create-channel-form"]').style.display='none';render();selectChannel(x.data.channel)}
function logout(){localStorage.removeItem('huddle.token');state={token:null,workspace:null,channels:[],channel:null};$('[data-testid="channel-list"]').innerHTML='';$('[data-testid="workspace-header"]').textContent='Huddle';showAuth(true)}
function bind(sel,event,fn){let e=$(sel);if(e)e.addEventListener(event,fn)}
function init(){bind('[data-testid="auth-form"]','submit',authSubmit);bind('#auth-toggle','click',toggleAuth);bind('#logout-btn','click',logout);bind('#workspace-create-form','submit',createWorkspace);bind('[data-testid="create-channel-form"]','submit',createChannel);bind('[data-testid="empty-state-create-workspace"]','click',()=>{$('[data-testid="workspace-create-form"]').style.display='block';$('[data-testid="empty-state-create-workspace"]').style.display='none'});bind('[data-testid="new-channel-btn"]','click',()=>{$('[data-testid="create-channel-form"]').style.display='block'});bind('[data-testid="create-channel-cancel"]','click',()=>{$('[data-testid="create-channel-form"]').style.display='none'});bind('[data-testid="send-btn"]','click',sendMessage);if(state.token){showAuth(false);api('/auth/me').then(x=>{let u=$('[data-testid="current-user"]');if(u)u.textContent=x.data.user?.display_name||'';loadWorkspace()})}else showAuth(true)}
init();
