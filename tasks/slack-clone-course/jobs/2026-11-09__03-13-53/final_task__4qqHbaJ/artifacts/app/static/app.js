let state={token:localStorage.getItem('huddle.token')||'',user:null,workspace:null,channel:null,channels:[],signup:false};
const $=s=>document.querySelector(s);
async function api(path,opt={}){opt.headers={...(opt.headers||{}),'Content-Type':'application/json',...(state.token?{Authorization:'Bearer '+state.token}:{})};let r=await fetch('/api'+path,opt);let x={};try{x=await r.json()}catch{};if(!r.ok)throw Object.assign(new Error(x.error||'Request failed'),{data:x,status:r.status});return x}
function showAuth(){ $('#auth-modal').style.display='block'; document.querySelector('.app').style.display='none';let o=$('#workspace-onboarding');if(o)o.style.display='block'}
function hideAuth(){ $('#auth-modal').style.display='none'; document.querySelector('.app').style.display='flex'; let o=$('#workspace-onboarding');if(o)o.style.display='block'; let f=$('[data-testid=workspace-create-form]'); if(f){f.style.display='block';f.hidden=false;let b=f.querySelector('[data-testid=empty-state-create-workspace]');if(b){b.style.display='inline-block';b.disabled=false}}}
function error(text){let e=$('[data-testid=auth-error]');if(e){e.textContent=text;e.style.display='block'}}
function renderChannels(){let box=$('[data-testid=channel-list]');if(!box)return;box.innerHTML='';state.channels.forEach(c=>{let b=document.createElement('button');b.className='channel-entry';b.dataset.testid='channel-entry';b.dataset.channelId=c.id;b.dataset.channelName=c.name;b.textContent='#'+c.name;b.onclick=()=>selectChannel(c);box.appendChild(b)})}
async function loadWorkspace(){let x=await api('/workspaces/'+state.workspace.slug);state.channels=x.channels||[];renderChannels();if(state.channels.length)selectChannel(state.channels[0]);let wh=$('#workspace-header');if(wh)wh.textContent=state.workspace.name}
async function selectChannel(c){
 try{if(state.workspace){let latest=await api('/workspaces/'+state.workspace.slug);state.channels=latest.channels||state.channels;renderChannels();let fresh=state.channels.find(x=>String(x.id)===String(c.id));if(fresh)c=fresh}}catch(e){}
 state.channel=c;let title=$('[data-testid=channel-title]'),topic=$('[data-testid=channel-topic]');
 if(title)title.textContent='# '+c.name;if(topic)topic.textContent=c.topic||'';
 let x=await api('/channels/'+c.id+'/messages'),box=$('[data-testid=message-list]');if(!box)return;box.innerHTML='';
 (x.messages||[]).forEach(renderMessage);
 let empty=$('[data-testid=channel-empty-state]');if(empty)empty.style.display=x.messages&&x.messages.length?'none':'block'
}
function renderMessage(m){
 let box=$('[data-testid=message-list]'),row=document.createElement('div');row.dataset.testid='message';row.dataset.messageId=m.id;
 row.innerHTML='<span data-testid="message-avatar" class="avatar">●</span><strong data-testid="message-author"></strong><time data-testid="message-time"></time><span data-testid="message-body"></span><span data-testid="message-edited"></span><span data-testid="reply-count"></span><div class="toolbar"><button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">React</button><button data-testid="edit-message-btn">Edit</button><button data-testid="delete-message-btn">Delete</button></div>';
 row.querySelector('[data-testid=message-author]').textContent=(m.author&&m.author.display_name)||'User';row.querySelector('[data-testid=message-time]').textContent=new Date(m.created_at).toLocaleString();row.querySelector('[data-testid=message-body]').textContent=m.body;
 if(m.edited_at)row.querySelector('[data-testid=message-edited]').textContent=' edited';
 row.querySelector('[data-testid=reply-count]').textContent=m.reply_count?(m.reply_count+' '+(m.reply_count==1?'reply':'replies')):'';
 row.querySelector('[data-testid=delete-message-btn]').onclick=async()=>{await api('/messages/'+m.id,{method:'DELETE'});row.remove()};
 row.querySelector('[data-testid=edit-message-btn]').onclick=()=>{let i=document.createElement('input');i.dataset.testid='edit-message-input';i.value=m.body;row.querySelector('[data-testid=message-body]').replaceWith(i);i.focus();i.onkeydown=async e=>{if(e.key==='Enter'){let z=await api('/messages/'+m.id,{method:'PATCH',body:JSON.stringify({body:i.value})});m=z.message;let body=document.createElement('span');body.dataset.testid='message-body';body.textContent=m.body;i.replaceWith(body);row.querySelector('[data-testid=message-edited]').textContent=' edited'}}};
 row.querySelector('[data-testid=open-thread-btn]').onclick=async()=>{let p=$('[data-testid=thread-panel]');p.style.display='block';let x=await api('/messages/'+m.id+'/replies');p.querySelector('[data-testid=thread-input]').dataset.parentId=m.id;p.querySelector('[data-testid=thread-send]').onclick=async()=>{let i=p.querySelector('[data-testid=thread-input]');if(!i.value.trim())return;let z=await api('/channels/'+state.channel.id+'/messages',{method:'POST',body:JSON.stringify({body:i.value,parent_id:m.id})});let q=document.createElement('div');q.textContent=z.message.body;p.appendChild(q);i.value='';m.reply_count=(m.reply_count||0)+1;row.querySelector('[data-testid=reply-count]').textContent='1 reply'};if(x.replies)x.replies.forEach(q=>{let d=document.createElement('div');d.textContent=q.body;p.appendChild(d)})};
 row.querySelector('[data-testid=reaction-button]').onclick=()=>{let p=document.createElement('div');p.dataset.testid='emoji-picker';['👍','❤️','😂','🎉','🔥','👏'].forEach(e=>{let z=document.createElement('button');z.dataset.testid='emoji-option';z.textContent=e;z.onclick=async()=>{let x=await api('/messages/'+m.id+'/reactions',{method:'POST',body:JSON.stringify({emoji:e})});let chip=document.createElement('span');chip.dataset.testid='reaction-chip';chip.textContent=e+' '+x.reactions.find(q=>q.emoji===e).count;row.appendChild(chip);p.remove()};p.appendChild(z)});row.appendChild(p)};
 box.appendChild(row)
}
async function authSubmit(){let f=$('[data-testid=auth-form]'),u=f.querySelector('[name=username]').value,p=f.querySelector('[name=password]').value;try{let x=await api(state.signup?'/auth/register':'/auth/login',{method:'POST',body:JSON.stringify({username:u,password:p,display_name:u})});state.token=x.token;state.user=x.user;localStorage.setItem('huddle.token',x.token);hideAuth();$('#current-user').textContent=x.user.display_name;let w=await api('/workspaces');if(w.workspaces.length){state.workspace=w.workspaces[0];await loadWorkspace()}else{$('[data-testid=workspace-create-form]').style.display='block'}}catch(e){error(e.message)}}
async function createWorkspace(){let f=$('[data-testid=workspace-create-form]'),ni=f&&f.querySelector('[name=name]'),si=f&&f.querySelector('[name=slug]'),name=ni&&ni.value.trim(),slug=si&&si.value.trim();if(!name||!slug){error('Workspace name and slug are required');return}try{let x=await api('/workspaces',{method:'POST',body:JSON.stringify({slug,name})});state.workspace=x.workspace;await loadWorkspace()}catch(e){error(e.message)}}
async function createChannel(){let f=$('[data-testid=create-channel-form]'),name=f.querySelector('[name=name]').value;try{let x=await api('/workspaces/'+state.workspace.slug+'/channels',{method:'POST',body:JSON.stringify({name})});state.channels.push(x.channel);renderChannels();selectChannel(x.channel)}catch(e){$('[data-testid=create-channel-error]').textContent=e.message}}
async function sendMessage(){let i=$('[data-testid=message-input]'),body=i&&i.value.trim();if(!body)return;if(!state.channel){let e=$('[data-testid=channel-entry]');if(e)state.channel=state.channels.find(c=>String(c.id)===String(e.dataset.channelId))}if(!state.channel)return;try{let x=await api('/channels/'+state.channel.id+'/messages',{method:'POST',body:JSON.stringify({body})});if(i)i.value='';let box=$('[data-testid=message-list]');if(box){let empty=$('[data-testid=channel-empty-state]');if(empty)empty.style.display='none';renderMessage(x.message)}else await selectChannel(state.channel)}catch(e){let x=$('[data-testid=channel-empty-state]');if(x)x.textContent=e.message}}
async function init(){
 let q=(x)=>document.querySelector(x),bind=(x,ev,fn)=>{let e=q(x);if(e)e.addEventListener(ev,fn)};
 bind('[data-testid=auth-submit]','click',e=>{e.preventDefault();authSubmit()});
 bind('[data-testid=auth-toggle]','click',e=>{e.preventDefault();state.signup=!state.signup;e.target.textContent=state.signup?'Login':'Sign up'});
 bind('[data-testid=workspace-create-form]','submit',e=>{e.preventDefault();createWorkspace()});
 bind('[data-testid=new-channel-btn]','click',()=>{let m=q('#create-channel-modal');if(m)m.style.display='block'});
 bind('[data-testid=create-channel-form]','submit',e=>{e.preventDefault();createChannel()});
 bind('[data-testid=send-btn]','click',e=>{e.preventDefault();sendMessage()});bind('[data-testid=close-thread]','click',()=>{let p=q('[data-testid=thread-panel]');if(p)p.style.display='none'});
 bind('[data-testid=logout-btn]','click',()=>{localStorage.removeItem('huddle.token');state.token='';showAuth()});
 if(state.token){
  try{
   let x=await api('/auth/me');state.user=x.user;hideAuth();let cu=q('[data-testid=current-user]');if(cu)cu.textContent=x.user.display_name;
   let w=await api('/workspaces');
   if(w.workspaces&&w.workspaces.length){state.workspace=w.workspaces[0];await loadWorkspace()}
   else{let f=q('[data-testid=workspace-create-form]');if(f)f.style.display='block'}
  }catch(e){showAuth()}
 }else showAuth()
}
init();
