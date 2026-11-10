const state={token:localStorage.getItem('huddle.token'),workspace:null,channel:null,signup:false};
const $=s=>document.querySelector(`[data-testid="${s}"]`);
const api=(p,o={})=>fetch('/api'+p,{...o,headers:{'Content-Type':'application/json',...(state.token?{Authorization:'Bearer '+state.token}:{})}}).then(async r=>({status:r.status,data:await r.json().catch(()=>({}))}));
function show(s,t){let e=$(s);if(e)e.textContent=t||''}
async function load(){
 const modal=document.getElementById('auth-modal');
 if(!state.token){if(modal)modal.style.display='block';return}
 if(modal)modal.style.display='none';let me=await api('/auth/me');if(me.status!==200){localStorage.removeItem('huddle.token');state.token=null;if(modal)modal.style.display='block';let af=$('auth-form');if(af)af.style.display='block';return}
 show('current-user',me.data.user.display_name||me.data.user.username);
 let x=await api('/workspaces');if(x.data.workspaces?.length) await openWorkspace(x.data.workspaces[0].slug);
}
async function openWorkspace(slug){
 let x=await api('/workspaces/'+slug);if(x.status!==200)return;state.workspace=x.data.workspace;
 show('workspace-header',state.workspace.name);let list=$('channel-list');list.innerHTML='';
 (x.data.channels||[]).forEach(ch=>{let e=document.createElement('div');e.className='channel';e.dataset.testid='channel-entry';e.dataset.channelId=ch.id;e.dataset.channelName=ch.name;e.textContent='#'+ch.name;e.onclick=()=>openChannel(ch);list.appendChild(e)});
 if(x.data.channels?.[0])openChannel(x.data.channels[0]);
}
async function openChannel(ch){let fresh=await api('/workspaces/'+state.workspace.slug);let latest=(fresh.data.channels||[]).find(x=>String(x.id)===String(ch.id));if(latest)ch=latest;state.channel=ch;show('channel-title','#'+ch.name);show('channel-topic',ch.topic||'');let x=await api(`/channels/${ch.id}/messages`);let box=$('message-list');box.innerHTML='';(x.data.messages||[]).reverse().forEach(m=>{let e=document.createElement('div');e.dataset.testid='message';e.innerHTML=`<b>${m.author.display_name}</b> <span data-testid="message-body">${m.body}</span>`;box.appendChild(e)});show('channel-empty-state',x.data.messages?.length?'':'No messages yet');
}
async function authSubmit(e){e.preventDefault();let f=new FormData(e.target),u=f.get('username'),p=f.get('password');let path=state.signup?'/auth/register':'/auth/login';let r=await api(path,{method:'POST',body:JSON.stringify({username:u,password:p,display_name:u})});if(r.status!==200&&r.status!==201){show('auth-error',r.data.error||'Invalid credentials');return}state.token=r.data.token;localStorage.setItem('huddle.token',state.token);await load()}
async function createWorkspace(e){e.preventDefault();let f=new FormData(e.target),r=await api('/workspaces',{method:'POST',body:JSON.stringify({slug:f.get('slug')||'workspace',name:f.get('name')||'Workspace'})});if(r.status===201)await openWorkspace(r.data.workspace.slug);else show('auth-error',r.data.error)}
async function createChannel(e){e.preventDefault();if(!state.workspace)return;let f=new FormData(e.target),r=await api(`/workspaces/${state.workspace.slug}/channels`,{method:'POST',body:JSON.stringify({name:f.get('name'),is_private:$('create-channel-private')?.checked})});if(r.status===201){await openWorkspace(state.workspace.slug);if(r.data.channel)await openChannel(r.data.channel)}else show('create-channel-error',r.data.error||'Unable to create channel')}
function init(){let form=$('auth-form');if(!form)return;form.dataset.mode='login';form.onsubmit=authSubmit;$('auth-toggle').onclick=()=>{state.signup=!state.signup;form.dataset.mode=state.signup?'signup':'login';$('auth-submit').textContent=state.signup?'Sign up':'Log in';show('auth-error','')};let wf=$('workspace-create-form');if(wf)wf.onsubmit=createWorkspace;let cf=$('create-channel-form');if(cf)cf.onsubmit=createChannel;let nb=$('new-channel-btn');if(nb)nb.onclick=()=>{if(cf)cf.style.display='block';};$('logout-btn').onclick=()=>{localStorage.removeItem('huddle.token');state.token=null;location.reload()};load()}
init();
async function renderMessages(){
 if(!state.channel)return;
 let r=await api(`/channels/${state.channel.id}/messages?limit=200`), box=$('message-list'); if(!box)return;
 box.innerHTML='';
 (r.data.messages||[]).reverse().forEach(m=>{
  let e=document.createElement('div');e.dataset.testid='message';e.dataset.id=m.id;
  e.innerHTML=`<span data-testid="message-avatar">●</span> <span data-testid="message-author">${m.author.username}</span> <span data-testid="message-time">${new Date(m.created_at).toLocaleString()}</span> <span data-testid="message-body">${m.body}</span>${m.edited_at?'<span data-testid="message-edited">edited</span>':''}<span data-testid="reply-count">${m.reply_count||0} ${m.reply_count===1?'reply':'replies'}</span><button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">React</button><button data-testid="edit-message-btn">Edit</button><button data-testid="delete-message-btn">Delete</button>`;
  let edit=document.createElement('input');edit.dataset.testid='edit-message-input';edit.style.display='none';edit.value=m.body;e.appendChild(edit);
  let picker=document.createElement('div');picker.dataset.testid='emoji-picker';picker.style.display='none';['😀','🎉','👍','❤️','🚀','😂'].forEach(em=>{let b=document.createElement('button');b.dataset.testid='emoji-option';b.textContent=em;b.onclick=async()=>{await api(`/messages/${m.id}/reactions`,{method:'POST',body:JSON.stringify({emoji:em})});let chip=document.createElement('span');chip.dataset.testid='reaction-chip';chip.textContent=em+' 1';e.appendChild(chip);picker.style.display='none'};picker.appendChild(b)});e.appendChild(picker);
  e.querySelector('[data-testid=edit-message-btn]').onclick=()=>{edit.style.display='inline';edit.focus();edit.onkeydown=async ev=>{if(ev.key==='Enter'){await api(`/messages/${m.id}`,{method:'PATCH',body:JSON.stringify({body:edit.value})});await renderMessages()}}};
  e.querySelector('[data-testid=delete-message-btn]').onclick=async()=>{await api(`/messages/${m.id}`,{method:'DELETE'});await renderMessages()};
  e.querySelector('[data-testid=reaction-button]').onclick=()=>picker.style.display='block';
  e.querySelector('[data-testid=open-thread-btn]').onclick=async()=>{let p=$('thread-panel');p.classList.remove('hidden');p.style.display='block';p.dataset.parent=m.id;let rr=await api(`/messages/${m.id}/replies`);p.querySelector('[data-testid=thread-input]').insertAdjacentHTML('beforebegin',(rr.data.replies||[]).map(x=>`<div>${x.body}</div>`).join(''))};
  box.appendChild(e)
 });
}
async function bindMessageUI(){
 let send=$('send-btn');if(send)send.onclick=async()=>{let inp=$('message-input'),body=inp.value;if(!body.trim())return;await api(`/channels/${state.channel.id}/messages`,{method:'POST',body:JSON.stringify({body})});inp.value='';await renderMessages()};
 let close=$('close-thread');if(close)close.onclick=()=>{$('thread-panel').style.display='none'};
 let ts=$('thread-send');if(ts)ts.onclick=async()=>{let p=$('thread-panel'),inp=$('thread-input');if(!inp.value.trim())return;await api(`/channels/${state.channel.id}/messages`,{method:'POST',body:JSON.stringify({body:inp.value,parent_id:Number(p.dataset.parent)})});let d=document.createElement('div');d.textContent=inp.value;d.dataset.testid='thread-reply';p.insertBefore(d,inp);inp.value='';await renderMessages()};
}
const _openChannel=openChannel;
openChannel=async ch=>{await _openChannel(ch);await renderMessages();await bindMessageUI()};
bindMessageUI();
