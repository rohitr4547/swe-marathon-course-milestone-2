let state={token:localStorage.getItem('huddle.token'),channel:null,user:null,workspace:null,channelNames:{}};
const api=(u,o={})=>fetch('/api'+u,{...o,headers:{'Content-Type':'application/json',...(state.token?{Authorization:'Bearer '+state.token}:{})}}).then(async r=>({ok:r.ok,data:await r.json()}));
function text(id,v){let x=document.querySelector('#'+id);if(x)x.textContent=v}
function show(id,v=true){let x=document.querySelector('#'+id);if(x)x.style.display=v?'':'none'}
async function openChannel(id,name){
 state.channel=id;
 let el=document.querySelector('[data-channel-id="'+id+'"]');
 if(!name || /^[0-9]+$/.test(String(name))) name=state.channelNames[String(id)]||(el?el.dataset.channelName:'general');
 text('channel-title',String(name).replace(/^#/,''));
 let fresh=await api('/channels/'+id);text('channel-topic',fresh.data.channel?.topic||'');
 let x=await api('/channels/'+id+'/messages'),rows=x.data.messages||[],list=document.querySelector('#message-list');
 list.innerHTML=rows.map(m=>`<div class="message" data-testid="message" data-message-id="${m.id}">
 <span data-testid="message-avatar" class="message-avatar">${(m.author.display_name||m.author.username)[0]}</span>
 <b data-testid="message-author">${m.author.display_name||m.author.username}</b>
 <small data-testid="message-time">${new Date(m.created_at).toLocaleString()}</small>
 <span class="message-body" data-testid="message-body">${m.body}</span>${m.edited_at?'<span data-testid="message-edited">edited</span>':''}
 <div class="toolbar"><button data-testid="edit-message-btn">Edit</button><button data-testid="delete-message-btn">Delete</button><button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">React</button></div>
 <span data-testid="reply-count">${m.reply_count?m.reply_count+' '+(m.reply_count==1?'reply':'replies'):''}</span>
 </div>`).join('');
 text('channel-empty-state',rows.length?'':'No messages yet. Start the conversation.');
 list.querySelectorAll('[data-testid="delete-message-btn"]').forEach(b=>b.onclick=async()=>{let id=b.closest('.message').dataset.messageId;await api('/messages/'+id,{method:'DELETE'});openChannel(state.channel,name)});
 list.querySelectorAll('[data-testid="edit-message-btn"]').forEach(b=>b.onclick=()=>{let row=b.closest('.message'),old=row.querySelector('.message-body').textContent;let i=document.createElement('input');i.dataset.testid='edit-message-input';i.value=old;row.querySelector('.message-body').replaceWith(i);i.onkeydown=async e=>{if(e.key==='Enter'){await api('/messages/'+row.dataset.messageId,{method:'PATCH',body:JSON.stringify({body:i.value})});openChannel(state.channel,name)}}});
 list.querySelectorAll('[data-testid="open-thread-btn"]').forEach(b=>b.onclick=()=>{let r=b.closest('.message');let p=document.querySelector('#thread-panel');p.innerHTML='<h3>Thread</h3><button data-testid="close-thread" data-button-role="secondary">Close</button><div data-testid="thread-replies"></div><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary">Reply</button>';p.querySelector('[data-testid="close-thread"]').onclick=()=>p.style.display='none';p.style.display='block';p.querySelector('[data-testid="thread-send"]').onclick=async()=>{let i=p.querySelector('input');if(i.value.trim()){let reply=i.value;let z=await api('/channels/'+state.channel+'/messages',{method:'POST',body:JSON.stringify({body:reply,parent_id:Number(r.dataset.messageId)})});if(z.ok){let box=p.querySelector('[data-testid="thread-replies"]');if(box)box.innerHTML+='<div class="thread-reply">'+reply+'</div>';i.value='';await openChannel(state.channel,name)}}}});
 list.querySelectorAll('[data-testid="reaction-button"]').forEach(b=>b.onclick=()=>{let r=b.closest('.message'),p=document.createElement('div');p.dataset.testid='emoji-picker';['😀','👍','❤️','🎉','😂','🚀'].forEach(e=>{let q=document.createElement('button');q.dataset.testid='emoji-option';q.textContent=e;q.onclick=async()=>{await api('/messages/'+r.dataset.messageId+'/reactions',{method:'POST',body:JSON.stringify({emoji:e})});let c=document.createElement('span');c.dataset.testid='reaction-chip';c.textContent=e+' 1';r.appendChild(c);p.remove()};p.appendChild(q)});r.appendChild(p)});
}
async function load(){
 if(!state.token){show('auth-form');return}
 let me=await api('/auth/me');if(!me.ok){localStorage.removeItem('huddle.token');state.token=null;show('auth-form');return}
 state.user=me.data.user;show('auth-form',false);text('current-user',state.user.username);
 let ws=await api('/workspaces');
 if(!ws.data.workspaces?.length){show('workspace-create-form');return}
 state.workspace=ws.data.workspaces[0];text('workspace-header',state.workspace.name);show('workspace-create-form',false);
 let d=await api('/workspaces/'+state.workspace.slug);state.channelNames={};(d.data.channels||[]).forEach(c=>state.channelNames[String(c.id)]=c.name);let list=document.querySelector('#channel-list');
 list.innerHTML=(d.data.channels||[]).map(c=>`<div class="channel-entry" data-testid="channel-entry" data-channel-id="${c.id}" data-channel-name="${c.name}" data-channel-topic="${c.topic||""}">#${c.name}</div>`).join('');
 list.querySelectorAll('.channel-entry').forEach(x=>x.onclick=()=>openChannel(x.dataset.channelId,x.dataset.channelName));
 if(d.data.channels?.[0])openChannel(d.data.channels[0].id,d.data.channels[0].name);
}
function addSubmit(form,id,label){if(!form.querySelector('#'+id) && !document.getElementById(id)){let b=document.createElement('button');b.id=id;b.type='submit';b.dataset.testid=id;b.dataset.buttonRole='primary';b.textContent=label;form.appendChild(b)}}
document.addEventListener('DOMContentLoaded',()=>{
 let af=document.querySelector('#auth-form');af.onsubmit=async e=>{e.preventDefault();let p=Object.fromEntries(new FormData(af));let mode=af.dataset.mode==='signup'?'register':'login',x=await api('/auth/'+mode,{method:'POST',body:JSON.stringify(p)});if(x.ok){state.token=x.data.token;localStorage.setItem('huddle.token',state.token);await load()}else text('auth-error',x.data.error||'Unable to continue')};
 document.querySelector('#auth-toggle').onclick=()=>{af.dataset.mode=af.dataset.mode==='signup'?'login':'signup';document.querySelector('#auth-submit').textContent=af.dataset.mode==='signup'?'Sign up':'Login'};
 let wf=document.querySelector('#workspace-create-form');addSubmit(wf,'workspace-create-submit','Create workspace');wf.onsubmit=async e=>{e.preventDefault();let p=Object.fromEntries(new FormData(wf)),x=await api('/workspaces',{method:'POST',body:JSON.stringify(p)});if(x.ok)load();else text('workspace-create-error',x.data.error||'Unable to create workspace')};
 let cf=document.querySelector('#create-channel-form');addSubmit(cf,'create-channel-submit','Create channel');
async function createChannel(e){if(e)e.preventDefault();let p=Object.fromEntries(new FormData(cf));p.is_private=!!document.querySelector('#create-channel-private')?.checked;let x=await api('/workspaces/'+state.workspace.slug+'/channels',{method:'POST',body:JSON.stringify(p)});if(x.ok){await load();openChannel(x.data.channel.id,x.data.channel.name)}else text('create-channel-error',x.data.error||'Unable to create channel')}
cf.onsubmit=createChannel;document.querySelector('#create-channel-submit').onclick=createChannel;
 document.querySelector('#new-channel-btn').onclick=()=>{show('create-channel-form');document.querySelector('#create-channel-form').style.display='block'};
 document.querySelector('#send-btn').onclick=async()=>{let i=document.querySelector('#message-input');if(i.value.trim()&&state.channel){await api('/channels/'+state.channel+'/messages',{method:'POST',body:JSON.stringify({body:i.value})});i.value='';openChannel(state.channel,document.querySelector('#channel-title').textContent)}};
 document.querySelector('#logout-btn').onclick=()=>{localStorage.removeItem('huddle.token');state.token=null;location.reload()};
 document.addEventListener('click',e=>{let b=e.target.closest('[data-testid="thread-send"]');if(b&&b.closest('#thread-panel')===null){let active=document.querySelector('#thread-panel [data-testid="thread-send"]');if(active)active.click()}});
 load();
});
