let state={token:localStorage.getItem("huddle.token")||new URLSearchParams(location.search).get("token"),user:null,workspace:null,channel:null};
function api(method,path,body){let o={method,headers:{}};if(state.token)o.headers.Authorization="Bearer "+state.token;if(body!==undefined){o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body)}return fetch("/api"+path,o).then(async r=>({status:r.status,data:await r.json().catch(()=>({}))}))}
function showAuth(msg){document.querySelector("#auth-modal").style.display="block";document.querySelector("#workspace-create-form").style.display="none";document.querySelector("#auth-error").textContent=msg||""}
function hideAuth(){document.querySelector("#auth-modal").style.display="none"}
function renderChannels(channels){let box=document.querySelector("#channel-list");box.innerHTML="";channels.forEach(ch=>{let b=document.createElement("button");b.className="channel-entry";b.dataset.testid="channel-entry";b.dataset.channelId=ch.id;b.dataset.channelName=ch.name;b.textContent="#"+ch.name;b.onclick=()=>openChannel(ch);box.appendChild(b)})}
function renderWorkspace(w,channels){state.workspace=w;document.querySelector("#workspace-header").textContent=w.name;document.querySelector("#workspace-create-form").style.display="none";renderChannels(channels)}
function renderMessages(ms){
 let box=document.querySelector("#message-list");box.innerHTML="";
 ms.forEach(m=>{
  let row=document.createElement("div");row.className="row";row.dataset.testid="message";row.dataset.messageId=m.id;
  let av=document.createElement("span");av.dataset.testid="message-avatar";av.textContent=(m.author&&m.author.username||"?")[0].toUpperCase();
  let au=document.createElement("strong");au.dataset.testid="message-author";au.textContent=" "+(m.author&&m.author.username||"user")+" ";
  let tm=document.createElement("small");tm.dataset.testid="message-time";tm.textContent=new Date(m.created_at).toLocaleString();
  let body=document.createElement("div");body.dataset.testid="message-body";body.textContent=m.body;row.append(av,au,tm,body);
  if(m.edited_at){let ed=document.createElement("span");ed.dataset.testid="message-edited";ed.textContent=" edited";row.append(ed)}
  if(m.reply_count){let rc=document.createElement("span");rc.dataset.testid="reply-count";rc.textContent=" "+m.reply_count+(m.reply_count===1?" reply":" replies");row.append(rc)}
  let tools=document.createElement("div");tools.className="toolbar";
  let edit=document.createElement("button");edit.dataset.testid="edit-message-btn";edit.textContent="Edit";
  edit.onclick=()=>{let inp=document.createElement("input");inp.dataset.testid="edit-message-input";inp.value=m.body;row.replaceChild(inp,body);inp.focus();inp.onkeydown=e=>{if(e.key==="Enter")api("PATCH","/messages/"+m.id,{body:inp.value}).then(()=>openChannel(state.channel))}};
  let del=document.createElement("button");del.dataset.testid="delete-message-btn";del.textContent="Delete";del.onclick=()=>api("DELETE","/messages/"+m.id).then(()=>openChannel(state.channel));
  let thread=document.createElement("button");thread.dataset.testid="open-thread-btn";thread.textContent="Thread";thread.onclick=()=>openThread(m);
  let react=document.createElement("button");react.dataset.testid="reaction-button";react.textContent="React";react.onclick=()=>showPicker(m,row);
  tools.append(edit,del,thread,react);row.append(tools);box.append(row)
 })
}
function openChannel(ch){
 let refresh=state.workspace?api("GET","/workspaces/"+state.workspace.slug).then(x=>(x.data.channels||[]).find(v=>String(v.id)===String(ch.id))||ch):Promise.resolve(ch);
 refresh.then(x=>{state.channel=x;document.querySelector("#channel-title").textContent=x.name;document.querySelector("#channel-topic").textContent=x.topic||"";return api("GET","/channels/"+x.id+"/messages")}).then(x=>{let ms=x.data.messages||[];document.querySelector("#channel-empty-state").textContent=ms.length?"":"No messages yet — start the conversation.";renderMessages(ms)})
}
function showPicker(m,row){
 let old=row.querySelector('[data-testid="emoji-picker"]');if(old){old.remove();return}
 let p=document.createElement("div");p.dataset.testid="emoji-picker";["👍","❤️","🎉","😂","🚀","👀"].forEach(e=>{let b=document.createElement("button");b.dataset.testid="emoji-option";b.textContent=e;b.onclick=()=>api("POST","/messages/"+m.id+"/reactions",{emoji:e}).then(()=>{let c=document.createElement("span");c.dataset.testid="reaction-chip";c.textContent=e+" 1";row.append(c);p.remove()});p.append(b)});row.append(p)
}
function openThread(m){
 let panel=document.querySelector("#thread-panel");panel.hidden=false;panel.dataset.parentId=m.id;
 api("GET","/messages/"+m.id+"/replies").then(x=>{document.querySelector("#thread-messages").innerHTML=(x.data.replies||[]).map(z=>"<div>"+z.body+"</div>").join("")})
}
function sendMessage(){let inp=document.querySelector("#message-input"),v=inp.value;if(!state.channel||!v.trim())return;api("POST","/channels/"+state.channel.id+"/messages",{body:v}).then(()=>{inp.value="";openChannel(state.channel)})}
function sendThread(){let p=document.querySelector("#thread-panel"),v=document.querySelector("#thread-input").value;if(!v.trim())return;api("POST","/channels/"+state.channel.id+"/messages",{body:v,parent_id:Number(p.dataset.parentId)}).then(()=>{document.querySelector("#thread-input").value="";openChannel(state.channel);setTimeout(()=>openThread({id:Number(p.dataset.parentId)}),150)})}

function loadWorkspace(){document.querySelector("#workspace-create-form").style.display="block";api("GET","/workspaces").then(x=>{if(!x.data.workspaces||!x.data.workspaces.length){document.querySelector("#workspace-create-form").style.display="block";return}let w=x.data.workspaces[0];api("GET","/workspaces/"+w.slug).then(y=>{renderWorkspace(y.data.workspace,y.data.channels||[]);if(y.data.channels&&y.data.channels[0])openChannel(y.data.channels[0])})})}
function authSubmit(e){e.preventDefault();let f=e.target,d=Object.fromEntries(new FormData(f));let mode=f.dataset.mode==="login"?"login":"register";api("POST","/auth/"+mode,d).then(x=>{if(x.status!==200&&x.status!==201){showAuth(x.data.error||"Unable to sign in");return}state.token=x.data.token;localStorage.setItem("huddle.token",state.token);state.user=x.data.user;document.querySelector("#current-user").textContent=state.user.username;hideAuth();loadWorkspace()})}
function toggleAuth(){let f=document.querySelector("#auth-form");f.dataset.mode=f.dataset.mode==="signup"?"login":"signup";document.querySelector("#auth-submit").textContent=f.dataset.mode==="signup"?"Sign up":"Log in"}
function createWorkspace(e){e.preventDefault();let d=Object.fromEntries(new FormData(e.target));api("POST","/workspaces",d).then(x=>{if(x.status!==201){document.querySelector("#join-workspace-error").textContent=x.data.error||"Unable";return}renderWorkspace(x.data.workspace,[x.data.general_channel]);openChannel(x.data.general_channel)})}
function createChannel(e){e.preventDefault();if(!state.workspace)return;let d=Object.fromEntries(new FormData(e.target));d.is_private=document.querySelector("#create-channel-private").checked;api("POST","/workspaces/"+state.workspace.slug+"/channels",d).then(x=>{if(x.status!==201){document.querySelector("#create-channel-error").textContent=x.data.error||"Unable";return}document.querySelector("#create-channel-error").textContent="";let b=document.createElement("button");b.className="channel-entry";b.dataset.testid="channel-entry";b.dataset.channelId=x.data.channel.id;b.dataset.channelName=x.data.channel.name;b.textContent="#"+x.data.channel.name;b.onclick=()=>openChannel(x.data.channel);document.querySelector("#channel-list").appendChild(b);openChannel(x.data.channel)})}
function init(){let f=document.querySelector("#auth-form");f.onsubmit=authSubmit;document.querySelector("#auth-toggle").onclick=toggleAuth;document.querySelector("#workspace-create-form").onsubmit=createWorkspace;document.querySelector("#create-channel-form").onsubmit=createChannel;document.querySelector("#new-channel-btn").onclick=()=>document.querySelector("#create-channel-modal").style.display="block";document.querySelector("#send-btn").onclick=sendMessage;document.querySelector("#thread-send").onclick=sendThread;document.querySelector("#close-thread").onclick=()=>document.querySelector("#thread-panel").hidden=true;document.querySelector("#logout-btn").onclick=()=>{localStorage.removeItem("huddle.token");state.token=null;showAuth("")};if(state.token){hideAuth();api("GET","/auth/me").then(x=>{if(x.status===200){state.user=x.data.user;document.querySelector("#current-user").textContent=state.user.username;loadWorkspace()}else showAuth("Session expired")})}else showAuth("")}
init();
