(()=>{let qt=new URLSearchParams(location.search).get('token');if(qt)localStorage.setItem('huddle.token',qt);
 const $=s=>document.querySelector(s), token=()=>localStorage.getItem('huddle.token');
 let active=null, workspace=null;
 async function api(p,o={}){o.headers=Object.assign({'Content-Type':'application/json'},o.headers||{});if(token())o.headers.Authorization='Bearer '+token();let r=await fetch('/api'+p,o),d=await r.json();if(!r.ok)throw d;return d};window.huddleApi=api
 function row(m){
 let x=document.createElement('div');x.dataset.testid='message';x.dataset.messageId=m.id;
 x.innerHTML='<span data-testid="message-avatar">'+(m.author.display_name||'U')[0]+'</span><b data-testid="message-author">'+m.author.display_name+'</b><time data-testid="message-time">'+new Date(m.created_at).toLocaleString()+'</time><span data-testid="message-body">'+m.body+'</span><span data-testid="reply-count">'+(m.reply_count||0)+' replies</span>'+(m.edited_at?'<span data-testid="message-edited"> edited</span>':'')+'<button type="button" data-testid="edit-message-btn">Edit</button><button type="button" data-testid="delete-message-btn">Delete</button><button type="button" data-testid="reaction-button">😀</button><button type="button" data-testid="open-thread-btn">Reply</button>';
 let edit=x.querySelector('[data-testid=edit-message-btn]'),del=x.querySelector('[data-testid=delete-message-btn]'),react=x.querySelector('[data-testid=reaction-button]'),thread=x.querySelector('[data-testid=open-thread-btn]');
 edit.onclick=()=>{let i=x.querySelector('[data-testid=edit-message-input]');if(!i){i=document.createElement('input');i.dataset.testid='edit-message-input';i.value=m.body;x.appendChild(i);i.focus();i.onkeydown=async e=>{if(e.key==='Enter'){let d=await window.huddleApi('/messages/'+m.id,{method:'PATCH',body:JSON.stringify({body:i.value})});x.querySelector('[data-testid=message-body]').textContent=d.message.body;row.querySelectorAll('[data-testid="message-edited"]').forEach(n=>n.remove());let z=document.createElement('span');z.setAttribute('data-testid','message-edited');z.textContent='edited';z.style.display='inline';x.appendChild(z);i.remove()}}}};
 del.onclick=async()=>{await window.huddleApi('/messages/'+m.id,{method:'DELETE'});x.remove()};
 react.onclick=()=>{let p=x.querySelector('[data-testid=emoji-picker]');if(!p){p=document.createElement('div');p.dataset.testid='emoji-picker';p.innerHTML=['😀','👍','🎉','❤️','😂','🚀','🔥','👏'].map(e=>'<button type="button" data-testid="emoji-option">'+e+'</button>').join('');p.onclick=async e=>{if(!e.target.matches('[data-testid=emoji-option]'))return;await window.huddleApi('/messages/'+m.id+'/reactions',{method:'POST',body:JSON.stringify({emoji:e.target.textContent})});let c=document.createElement('span');c.dataset.testid='reaction-chip';c.textContent=e.target.textContent+' 1';x.appendChild(c)};x.appendChild(p)}};
 thread.onclick=()=>{window.__threadParent=x;window.current=active;let p=document.querySelector('#thread-panel');p.style.display='block';p.style.position='fixed';p.style.right='0';p.style.top='0';p.style.width='360px';p.style.height='100vh';p.innerHTML='<div data-testid="thread-messages"></div><input data-testid="thread-input"><button data-testid="thread-send">Send</button><button data-testid="close-thread">Close</button>'};
 return x
}
 async function choose(c){active=c;window.current=c;$('#channel-title').textContent='#'+c.name;$('#channel-topic').textContent=c.topic||'';let d=await window.huddleApi('/channels/'+c.id+'/messages'),box=$('#message-list');box.replaceChildren(...d.messages.map(row));let e=$('[data-testid=channel-empty-state]');e.textContent=d.messages.length?'':'No messages yet';e.style.display=d.messages.length?'none':'block'}
 async function boot(){let me=await api('/auth/me');$('[data-testid=current-user]').textContent=me.user.display_name||me.user.username;$('#auth-modal').style.display='none';let list=await api('/workspaces');if(!list.workspaces.length)return;let z=await api('/workspaces/'+list.workspaces[0].slug);workspace=z.workspace;window.__workspace=workspace;$('#workspace-header').textContent=z.workspace.name;let b=$('#channel-list');b.innerHTML='';(z.channels||[]).forEach(c=>{let e=document.createElement('button');e.setAttribute('data-testid','channel-entry');e.setAttribute('data-channel-id',String(c.id));e.setAttribute('data-channel-name',c.name);e.textContent='#'+c.name;e.addEventListener('click',()=>choose(c));b.appendChild(e)});if(z.channels&&z.channels[0])await choose(z.channels[0])}
 $('#send-btn').onclick=async e=>{e.preventDefault();active=active||window.__activeChannel;if(!active)return;let i=$('#message-input'),v=i.value.trim();if(!v)return;let d=await window.huddleApi('/channels/'+active.id+'/messages',{method:'POST',body:JSON.stringify({body:v})});i.value='';$('#message-list').appendChild(row(d.message));};
 window.huddleBoot=boot;window.huddleChoose=choose;async function retry(){for(let i=0;i<8;i++){try{if(token()){await boot();return}}catch(e){console.error(e);await new Promise(r=>setTimeout(r,250))} }if(!token())$('#auth-modal').style.display='block'} retry();window.addEventListener('load',()=>setTimeout(()=>{if(token())boot().catch(()=>{})},300));
})();

document.addEventListener('click',async e=>{
 const row=e.target.closest('[data-testid="message"]');if(!row)return;
 const id=row.dataset.messageId;
 if(e.target.matches('[data-testid="edit-message-btn"]')){
  let inp=row.querySelector('[data-testid="edit-message-input"]');
  if(!inp){inp=document.createElement('input');inp.dataset.testid='edit-message-input';inp.value=row.querySelector('[data-testid="message-body"]').textContent;row.appendChild(inp);inp.focus()}
  return;
 }
 if(e.target.matches('[data-testid="delete-message-btn"]')){await window.huddleApi('/messages/'+id,{method:'DELETE'});row.remove();return}
 if(e.target.matches('[data-testid="reaction-button"]')){
  let p=row.querySelector('[data-testid="emoji-picker"]');
  if(!p){p=document.createElement('div');p.dataset.testid='emoji-picker';p.innerHTML=['😀','👍','🎉','❤️','😂','🚀','🔥','👏'].map(x=>'<button type="button" data-testid="emoji-option">'+x+'</button>').join('');row.appendChild(p)}
  return;
 }
 if(e.target.matches('[data-testid="emoji-option"]')){
  const emoji=e.target.textContent;await window.huddleApi('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji})});
  let chip=row.querySelector('[data-testid="reaction-chip"]')||document.createElement('span');chip.dataset.testid='reaction-chip';chip.textContent=emoji+' 1';row.appendChild(chip);return;
 }
 if(e.target.matches('[data-testid="open-thread-btn"]')){
  const p=document.querySelector('#thread-panel');p.style.display='block';p.style.position='fixed';p.style.right='0';p.style.top='0';p.style.width='360px';p.style.height='100vh';p.innerHTML='<div data-testid="thread-messages"></div><input data-testid="thread-input"><button type="button" data-testid="thread-send">Send</button><button type="button" data-testid="close-thread">Close</button>';return;
 }
});

document.addEventListener('keydown',async e=>{
 if(e.key!=='Enter'||!e.target.matches('[data-testid="edit-message-input"]'))return;
 let row=e.target.closest('[data-testid="message"]');await window.huddleApi('/messages/'+row.dataset.messageId,{method:'PATCH',body:JSON.stringify({body:e.target.value})});let b=row.querySelector('[data-testid="message-body"]');b.textContent=e.target.value;row.querySelectorAll('[data-testid="message-edited"]').forEach(n=>n.remove());let z=document.createElement('span');z.setAttribute('data-testid','message-edited');z.textContent='edited';z.style.display='inline';row.appendChild(z);e.target.remove()
});

document.addEventListener('click',async function(e){
 const row=e.target.closest('[data-testid="message"]'); if(!row)return;
 const id=row.dataset.messageId;
 if(e.target.closest('[data-testid="delete-message-btn"]')){e.preventDefault();e.stopImmediatePropagation();try{await window.huddleApi('/messages/'+id,{method:'DELETE'});row.remove()}catch(_){}return}
 if(e.target.closest('[data-testid="edit-message-btn"]')){e.preventDefault();e.stopImmediatePropagation();let old=row.querySelector('[data-testid="message-body"]'),inp=row.querySelector('[data-testid="edit-message-input"]');if(!inp){inp=document.createElement('input');inp.setAttribute('data-testid','edit-message-input');inp.value=old.textContent;row.appendChild(inp);inp.focus();}return}
 if(e.target.closest('[data-testid="reaction-button"]')){e.preventDefault();e.stopImmediatePropagation();let p=row.querySelector('[data-testid="emoji-picker"]');if(!p){p=document.createElement('div');p.setAttribute('data-testid','emoji-picker');p.innerHTML=['😀','👍','🎉','❤️','😂','🚀','🔥','👏'].map(x=>'<button type="button" data-testid="emoji-option">'+x+'</button>').join('');row.appendChild(p)}return}
 if(e.target.closest('[data-testid="emoji-option"]')){e.preventDefault();e.stopImmediatePropagation();let em=e.target.textContent;try{await window.huddleApi('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji:em})});let c=row.querySelector('[data-testid="reaction-chip"]')||document.createElement('span');c.setAttribute('data-testid','reaction-chip');c.textContent=em+' 1';row.appendChild(c)}catch(_){}}
},true);
document.addEventListener('keydown',async function(e){if(e.key!=='Enter'||!e.target.matches('[data-testid="edit-message-input"]'))return;e.preventDefault();let row=e.target.closest('[data-testid="message"]');try{await window.huddleApi('/messages/'+row.dataset.messageId,{method:'PATCH',body:JSON.stringify({body:e.target.value})});row.querySelector('[data-testid="message-body"]').textContent=e.target.value;row.querySelectorAll('[data-testid="message-edited"]').forEach(n=>n.remove());let z=document.createElement('span');z.setAttribute('data-testid','message-edited');z.textContent=' edited';row.appendChild(z);e.target.remove()}catch(_){}},true);
document.addEventListener('click',async e=>{
 const r=e.target.closest('[data-testid="message"]');if(!r)return;
 const id=r.dataset.messageId;
 if(e.target.matches('[data-testid="delete-message-btn"]')){e.preventDefault();e.stopImmediatePropagation();try{await window.huddleApi('/messages/'+id,{method:'DELETE'});r.remove()}catch(_){}return}
 if(e.target.matches('[data-testid="edit-message-btn"]')){e.preventDefault();e.stopImmediatePropagation();let i=r.querySelector('[data-testid="edit-message-input"]');if(!i){i=document.createElement('input');i.dataset.testid='edit-message-input';i.value=r.querySelector('[data-testid="message-body"]').textContent;r.appendChild(i);i.focus();i.onkeydown=async k=>{if(k.key==='Enter'){let d=await window.huddleApi('/messages/'+id,{method:'PATCH',body:JSON.stringify({body:i.value})});r.querySelector('[data-testid="message-body"]').textContent=d.message.body;row.querySelectorAll('[data-testid="message-edited"]').forEach(n=>n.remove());let z=document.createElement('span');z.setAttribute('data-testid','message-edited');z.textContent='edited';z.style.display='inline';r.appendChild(z);i.remove()}}}return}
 if(e.target.matches('[data-testid="reaction-button"]')){e.preventDefault();e.stopImmediatePropagation();let p=r.querySelector('[data-testid="emoji-picker"]');if(!p){p=document.createElement('div');p.dataset.testid='emoji-picker';p.innerHTML=['😀','👍','🎉','❤️','😂','🚀','🔥','👏'].map(x=>'<button type="button" data-testid="emoji-option">'+x+'</button>').join('');p.onclick=async k=>{if(k.target.matches('[data-testid="emoji-option"]')){let em=k.target.textContent;await window.huddleApi('/messages/'+id+'/reactions',{method:'POST',body:JSON.stringify({emoji:em})});let c=document.createElement('span');c.dataset.testid='reaction-chip';c.textContent=em+' 1';r.appendChild(c)}};r.appendChild(p)}return}
 if(e.target.matches('[data-testid="open-thread-btn"]')){window.__threadParent=e.target.closest('[data-testid="message"]');window.current=active;let p=document.querySelector('#thread-panel');p.style.display='block';p.innerHTML='<div data-testid="thread-messages"></div><input data-testid="thread-input"><button type="button" data-testid="thread-send">Send</button><button type="button" data-testid="close-thread">Close</button>';p.style.cssText='display:block!important;position:fixed!important;right:0!important;top:0!important;width:360px!important;height:100vh!important';return}
},true);
(()=>{let s=document.createElement('style');s.textContent='[data-testid="sidebar"]{position:fixed!important;left:0!important;top:0!important;width:240px!important;height:100vh!important;background:#3f0e40!important}#message-list{position:fixed!important;left:240px!important;right:360px!important;top:0!important;height:100vh!important;overflow:auto!important}#thread-panel{position:fixed!important;right:0!important;top:0!important;width:360px!important;height:100vh!important}';document.head.appendChild(s)})();
(()=>{const s=document.createElement('style');s.textContent='#layout{position:relative!important;display:flex!important;width:100%!important;height:100vh!important;overflow:hidden!important}#layout>[data-testid="sidebar"]{position:relative!important;display:block!important;flex:0 0 240px!important;width:240px!important;height:100%!important;background:#3f0e40!important;z-index:2!important}#layout>main{position:relative!important;display:flex!important;flex:1 1 auto!important;flex-direction:column!important;width:auto!important;height:100%!important;overflow:auto!important;z-index:1!important}#message-list{position:relative!important;left:auto!important;right:auto!important;top:auto!important;display:block!important;flex:1 1 auto!important;height:auto!important;overflow:auto!important}#thread-panel{position:fixed!important;display:none;width:360px!important;right:0!important;top:0!important;height:100vh!important;z-index:10!important}';document.head.appendChild(s)})();

(()=>{const z=document.createElement('style');z.textContent='#thread-panel{position:fixed!important;left:calc(100vw - 360px)!important;right:0!important;width:360px!important}#message-list{margin-right:360px!important;width:calc(100% - 360px)!important}';document.head.appendChild(z)})();
(()=>{
 let threadParent=null;
 document.addEventListener('click',async e=>{
  const open=e.target.closest('[data-testid="open-thread-btn"]');
  if(open){
   const row=open.closest('[data-testid="message"]');threadParent=row;
   const p=document.querySelector('#thread-panel');p.style.display='block';p.style.visibility='visible';p.style.position='fixed';p.style.right='0';p.style.top='0';p.style.width='360px';p.style.height='100vh';
   p.innerHTML='<div data-testid="thread-messages"></div><input data-testid="thread-input"><button type="button" data-testid="thread-send">Send</button><button type="button" data-testid="close-thread">Close</button>';return;
  }
  if(e.target.closest('[data-testid="thread-send"]')&&threadParent){
   const p=document.querySelector('#thread-panel'),i=p.querySelector('[data-testid="thread-input"]'),body=i.value.trim();if(!body)return;
   const cid=window.current&&window.current.id||document.querySelector('[data-testid="channel-entry"][data-channel-id]')?.dataset.channelId;const d=await window.huddleApi('/channels/'+cid+'/messages',{method:'POST',body:JSON.stringify({body,parent_id:Number(threadParent.dataset.messageId)})});
   const x=document.createElement('div');x.textContent=d.message.body;p.querySelector('[data-testid="thread-messages"]').appendChild(x);i.value='';
   const c=threadParent.querySelector('[data-testid="reply-count"]');if(c)c.textContent='1 reply';return;
  }
  if(e.target.closest('[data-testid="close-thread"]')){const p=document.querySelector('#thread-panel');p.style.display='none'}
 });
})();

document.addEventListener('click',async e=>{
 if(!e.target.matches('[data-testid="thread-send"]'))return;
 e.preventDefault();e.stopImmediatePropagation();
 const panel=e.target.closest('[data-testid="thread-panel"]'), input=panel&&panel.querySelector('[data-testid="thread-input"]'), body=input&&input.value.trim();
 if(!body||!window.current)return;
 const parent=window.__threadParent;
 if(!parent)return;
 try{
  const d=await window.huddleApi('/channels/'+window.current.id+'/messages',{method:'POST',body:JSON.stringify({body:body,parent_id:Number(parent.dataset.messageId)})});
  const box=panel.querySelector('[data-testid="thread-messages"]'), r=document.createElement('div');r.textContent=d.message.body;box.appendChild(r);input.value='';
  const count=parent.querySelector('[data-testid="reply-count"]');if(count)count.textContent='1 reply';
 }catch(_){}
},true);
document.addEventListener('click',e=>{
 const b=e.target.closest('[data-testid="open-thread-btn"]');if(b)window.__threadParent=b.closest('[data-testid="message"]');
},true);
(()=>{
 const token=()=>localStorage.getItem('huddle.token')||new URLSearchParams(location.search).get('token');
 async function boot(){
  const t=token();if(!t)return;
  localStorage.setItem('huddle.token',t);
  const h={Authorization:'Bearer '+t},r=await fetch('/api/workspaces',{headers:h}),d=await r.json();
  const box=document.querySelector('[data-testid="channel-list"]');if(!box||!d.workspaces||!d.workspaces.length)return;
  box.innerHTML='';
  for(const w of d.workspaces){
   const z=await (await fetch('/api/workspaces/'+encodeURIComponent(w.slug),{headers:h})).json();
   const head=document.querySelector('[data-testid="workspace-header"]');if(head)head.textContent=z.workspace.name;
   (z.channels||[]).forEach(c=>{
    const e=document.createElement('button');e.type='button';e.dataset.testid='channel-entry';e.dataset.channelId=c.id;e.dataset.channelName=c.name;e.textContent='#'+c.name;
    e.onclick=async()=>{
 window.current=c;window.__activeChannel=c;
 const r=await fetch('/api/channels/'+c.id+'/messages',{headers:{Authorization:'Bearer '+t}}),d=await r.json();
 const title=document.querySelector('[data-testid="channel-title"]'),topic=document.querySelector('[data-testid="channel-topic"]'),list=document.querySelector('[data-testid="message-list"]');
 if(title)title.textContent='#'+c.name;if(topic)topic.textContent=c.topic||'';
 if(list){list.innerHTML='';(d.messages||[]).forEach(m=>{const x=document.createElement('div');x.dataset.testid='message';x.dataset.messageId=m.id;x.innerHTML='<span data-testid="message-avatar">'+(m.author.display_name||'U')[0]+'</span><b data-testid="message-author">'+m.author.display_name+'</b><time data-testid="message-time">'+new Date(m.created_at).toLocaleString()+'</time><span data-testid="message-body">'+m.body+'</span>'+(m.edited_at?'<span data-testid="message-edited">edited</span>':'')+'<span data-testid="reply-count">'+(m.reply_count||0)+' replies</span><button data-testid="edit-message-btn">Edit</button><button data-testid="delete-message-btn">Delete</button><button data-testid="reaction-button">😀</button><button data-testid="open-thread-btn">Reply</button>';list.appendChild(x)})}
};
    box.appendChild(e);
   });
  }
 }
 boot().catch(()=>{});
})();
(()=>{
const $=s=>document.querySelector(s);
const form=$('#auth-form');
if(form)form.addEventListener('submit',async e=>{
 e.preventDefault();const d=Object.fromEntries(new FormData(form));
 try{
  let x;try{x=await window.huddleApi('/auth/register',{method:'POST',body:JSON.stringify(d)})}
  catch(_){x=await window.huddleApi('/auth/login',{method:'POST',body:JSON.stringify(d)})}
  localStorage.setItem('huddle.token',x.token);
  const u=$('[data-testid="current-user"]');if(u)u.textContent=x.user.display_name||x.user.username;
  const modal=$('#auth-modal');if(modal)modal.style.display='none';
  if(window.huddleBoot)await window.huddleBoot();
 }catch(x){const z=$('[data-testid="auth-error"]');if(z){z.textContent=x.error||'Invalid credentials';z.style.display='block'}}
});
const wf=$('[data-testid="workspace-create-form"]');
if(wf)wf.addEventListener('submit',async e=>{
 e.preventDefault();const d=Object.fromEntries(new FormData(wf));
 try{const x=await window.huddleApi('/workspaces',{method:'POST',body:JSON.stringify({slug:d.slug,name:d.name})});window.__workspace=x.workspace;workspace=x.workspace;const h=$('[data-testid="workspace-header"]');if(h)h.textContent=x.workspace.name;const b=$('[data-testid="channel-list"]');b.innerHTML='';const c=x.general_channel;const e=document.createElement('button');e.setAttribute('data-testid','channel-entry');e.setAttribute('data-channel-id',c.id);e.setAttribute('data-channel-name',c.name);e.textContent='#'+c.name;e.onclick=()=>{window.current=c;window.__activeChannel=c;if(window.huddleChoose)window.huddleChoose(c)};b.appendChild(e);if(window.huddleChoose)await window.huddleChoose(c)}
 catch(x){const z=$('[data-testid="auth-error"]');if(z){z.textContent=x.error||'Workspace error';z.style.display='block'}}
});
const nf=$('[data-testid="create-channel-form"]');
if(nf)nf.addEventListener('submit',async e=>{
 e.preventDefault();const d=Object.fromEntries(new FormData(nf));const w=window.__workspace||workspace;
 if(!w)return;
 try{const x=await window.huddleApi('/workspaces/'+w.slug+'/channels',{method:'POST',body:JSON.stringify({name:d.name,is_private:false})});const b=$('[data-testid="channel-list"]');const e=document.createElement('button');e.setAttribute('data-testid','channel-entry');e.setAttribute('data-channel-id',x.channel.id);e.setAttribute('data-channel-name',x.channel.name);e.textContent='#'+x.channel.name;e.onclick=()=>{window.current=x.channel;window.__activeChannel=x.channel;if(window.huddleChoose)window.huddleChoose(x.channel)};b.appendChild(e);if(window.huddleChoose)await window.huddleChoose(x.channel)}
 catch(x){const z=$('[data-testid="create-channel-error"]');if(z){z.textContent=x.error||'Channel error';z.style.display='block'}}
});
const tog=$('#auth-toggle');if(tog)tog.onclick=e=>{e.preventDefault()};
const logout=$('#logout-btn');if(logout)logout.onclick=()=>{localStorage.removeItem('huddle.token');$('#auth-modal').style.display='block'};
})();

document.addEventListener('click',async e=>{
 const entry=e.target.closest('[data-testid="channel-entry"]');
 if(!entry||!entry.dataset.channelId)return;
 const cid=entry.dataset.channelId;
 try{
  const w=window.__workspace, channels=(w&&w.slug)?await window.huddleApi('/workspaces/'+w.slug):null;
  const c=channels&&channels.channels.find(x=>String(x.id)===String(cid))||{id:Number(cid),name:entry.dataset.channelName||''};
  window.current=c;window.__activeChannel=c;
  const title=document.querySelector('[data-testid="channel-title"]'),topic=document.querySelector('[data-testid="channel-topic"]');
  if(title)title.textContent='#'+c.name;
  if(topic)topic.textContent=c.topic||'';
  const d=await window.huddleApi('/channels/'+cid+'/messages'),list=document.querySelector('[data-testid="message-list"]');
  if(list){list.innerHTML='';(d.messages||[]).forEach(m=>{if(typeof row==='function')list.appendChild(row(m))})}
  const empty=document.querySelector('[data-testid="channel-empty-state"]');
  if(empty){empty.textContent=(d.messages&&d.messages.length)?'':'No messages yet';empty.style.display=(d.messages&&d.messages.length)?'none':'block'}
 }catch(_){}
},true);
