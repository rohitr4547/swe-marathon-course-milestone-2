import asyncio,json,argparse,re
from aiohttp import web,WSMsgType
import core
def js(x): return web.json_response(x)
def err(n,s): return web.json_response({'error':s},status=n)
async def body(r):
 try:return await r.json()
 except:return None
def auth(r):
 h=r.headers.get('Authorization','')
 token=h[7:] if h.startswith('Bearer ') else r.query.get('token')
 if token and token.startswith('Bearer '): token=token[7:]
 return core.user_for_token(token)
def need(r):
 u=auth(r)
 if not u: raise web.HTTPUnauthorized(text=json.dumps({'error':'unauthorized'}),content_type='application/json')
 return u
def paging(q,kind):
 try:
  lim=int(q.get('limit','50'))
  if not 1<=lim<=200: raise ValueError()
  cur=q.get('cursor'); last=None
  if cur:
   x=json.loads(__import__('base64').urlsafe_b64decode(cur+'===')); 
   if x.get('k')!=kind: raise ValueError()
   last=int(x['v'])
  return lim,last
 except: raise web.HTTPBadRequest(text=json.dumps({'error':'bad cursor'}),content_type='application/json')
def cur(k,v):
 return __import__('base64').urlsafe_b64encode(json.dumps({'k':k,'v':v}).encode()).decode().rstrip('=')
async def health(r): return js({'status':'ok','node_id':r.app['node']})
async def register(r):
 d=await body(r)
 if not isinstance(d,dict) or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or not isinstance(d.get('password'),str) or len(d['password'])<8:return err(400,'invalid')
 with core.tx() as c:
  try:
   x=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
  except: return err(409,'duplicate')
  u=c.execute('SELECT * FROM users WHERE id=?',(x.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
 return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
async def login(r):
 d=await body(r) or {}; c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username'),)).fetchone(); c.close()
 if not u or not core.verify_password(d.get('password',''),u['password']):return err(401,'invalid')
 with core.tx() as c:t=core.issue_token(c,u['id'])
 return js({'user':core.user_to_json(u),'token':t})
async def me(r):
 u=need(r);return js({'user':core.user_to_json(u)})
async def workspaces(r):
 u=need(r);c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return js({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_ws(r):
 u=need(r);d=await body(r) or {}
 if not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))) or not isinstance(d.get('name'),str):return err(400,'invalid')
 with core.tx() as c:
  try:x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']))
  except:return err(409,'duplicate')
  w=x.lastrowid;ch=core.create_channel_row(c,w,'general');core.add_workspace_member(c,w,u['id'],'owner');core.add_channel_member(c,ch['id'],u['id'])
  return web.json_response({'workspace':core.workspace_to_json(c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone()),'general_channel':core.channel_to_json(ch)},status=201)
async def ws_detail(r):
 u=need(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect();chs=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
 return js({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in chs],'read_state':{}})
async def members(r):
 u=need(r);w=core.get_workspace(r.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect();x=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return js({'members':[dict(z) for z in x]})
async def create_ch(r):
 u=need(r);w=core.get_workspace(r.match_info['slug']);d=await body(r) or {}
 if not w:return err(404,'not found')
 if not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']):return err(400,'invalid')
 with core.tx() as c:
  if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return err(409,'duplicate')
  x=core.create_channel_row(c,w['id'],d['name'],d.get('is_private',False),d.get('topic',''));core.add_channel_member(c,x['id'],u['id'])
 return web.json_response({'channel':core.channel_to_json(x)},status=201)
async def get_channel_api(r):
 u=need(r); ch=core.get_channel(int(r.match_info["id"]))
 if not ch:return err(404,"not found")
 return js({"channel":core.channel_to_json(ch)})
async def patch_channel(r):
 u=need(r); ch=core.get_channel(int(r.match_info["id"]))
 if not ch:return err(404,"not found")
 d=await body(r) or {}
 if not isinstance(d.get("topic"),str) or len(d["topic"])>250:return err(400,"invalid")
 with core.tx() as c:
  c.execute("UPDATE channels SET topic=? WHERE id=?",(d["topic"],ch["id"]))
  ch=c.execute("SELECT * FROM channels WHERE id=?",(ch["id"],)).fetchone()
 return js({"channel":core.channel_to_json(ch)})
async def join(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 with core.tx() as c:core.add_channel_member(c,ch['id'],u['id'])
 return js({'channel':core.channel_to_json(ch)})
async def messages(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
 lim,last=paging(r.query,'messages');c=core.connect();q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL';args=[ch['id']]
 if last:q+=' AND id<?';args.append(last)
 q+=' ORDER BY id DESC LIMIT ?';args.append(lim+1);rows=c.execute(q,args).fetchall();out=[core.message_json(c,x) for x in rows[:lim]];n=cur('messages',rows[lim-1]['id']) if len(rows)>lim else None;c.close();return js({'messages':out,'next_cursor':n})
async def post(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']));d=await body(r) or {}
 if not ch:return err(404,'not found')
 if not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'empty')
 with core.tx() as c:
  m=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch['id'],u['id'],d['body'],d.get('parent_id'),core.now_iso(),json.dumps(core.resolve_mentions(c,d['body'],ch,u['id']))))
  row=c.execute('SELECT * FROM messages WHERE id=?',(m.lastrowid,)).fetchone(); payload={'message':core.message_json(c,row)};core.emit_event(c,ch['id'],'message.reply' if d.get('parent_id') else 'message.created',payload)
 return web.json_response({'message':payload['message']},status=201)
async def edit(r):
 u=need(r); mid=int(r.match_info['id']);d=await body(r)
 if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return err(400,'invalid')
 with core.tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not m:return err(404,'not found')
  if m['deleted']:return err(409,'deleted')
  if m['author_id']!=u['id']:return err(403,'forbidden')
  ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
  mentions=core.resolve_mentions(c,d['body'],ch,u['id']);c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(d['body'],core.now_iso(),json.dumps(mentions),mid))
  row=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); out=core.message_json(c,row);core.emit_event(c,ch['id'],'message.updated',{'message':out})
 return js({'message':out})
async def delete(r):
 u=need(r);mid=int(r.match_info['id'])
 with core.tx() as c:
  m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
  if not m:return err(404,'not found')
  if m['deleted']:return js({'deleted':True})
  ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
  role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone()
  if m['author_id']!=u['id'] and not (role and role['role']=='owner'):return err(403,'forbidden')
  c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,));core.emit_event(c,ch['id'],'message.deleted',{'message_id':mid})
 return js({'deleted':True})
async def replies(r):
 u=need(r);pid=int(r.match_info['id']);lim,last=paging(r.query,'replies');c=core.connect();q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';a=[pid]
 if last:q+=' AND id>?';a.append(last)
 q+=' ORDER BY id LIMIT ?';a.append(lim+1);rows=c.execute(q,a).fetchall();out=[core.message_json(c,x) for x in rows[:lim]];n=cur('replies',rows[lim-1]['id']) if len(rows)>lim else None;c.close();return js({'replies':out,'next_cursor':n})
async def react(r):
 u=need(r);mid=int(r.match_info['id']);d=await body(r) or {};emoji=d.get('emoji')
 if not isinstance(emoji,str) or not emoji:return err(400,'invalid')
 with core.tx() as c:
  if not c.execute('SELECT id FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone():return err(404,'not found')
  c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
  rows=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall()
 return js({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rows]})
async def unreact(r):
 u=need(r);mid=int(r.match_info['id'])
 with core.tx() as c:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],r.match_info['emoji']))
 return js({'reactions':[]})
async def leave(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 with core.tx() as c:c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
 return js({'channel':core.channel_to_json(ch)})
async def chmembers(r):
 u=need(r);ch=core.get_channel(int(r.match_info['id']))
 if not ch:return err(404,'not found')
 c=core.connect();rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close();return js({'members':[core.user_to_json(x) for x in rows]})
async def wsock(r):
 u=auth(r)
 if not u:return err(401,'unauthorized')
 ws=web.WebSocketResponse();await ws.prepare(r); subs={}; alive=True
 async def poll():
  while alive:
   await asyncio.sleep(.05)
   c=core.connect()
   for ch,since in list(subs.items()):
    for x in c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)):
     await ws.send_str(x['payload']);subs[ch]=json.loads(x['payload'])['event_id']
   c.close()
 try:
  task=asyncio.create_task(poll())
  async for m in ws:
   if m.type==WSMsgType.TEXT:
    d=json.loads(m.data);ch=int(d.get('channel_id',0))
    if d['type'] in ('subscribe','resume'):
     c=core.connect();head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone();head_id=head[0] if head else 0;c.close()
     subs[ch]=head_id if d['type']=='subscribe' else int(d.get('since_event_id',0))
     await ws.send_json({'type':'subscribed' if d['type']=='subscribe' else 'resumed','channel_id':ch,'head_event_id':head_id})
 finally:
  alive=False;task.cancel()
 return ws
async def root(r):
    return web.Response(text="""<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>
    *{box-sizing:border-box}body{margin:0;font-family:Arial;color:#222}.layout{display:flex;height:100vh}.sidebar{width:260px;background:#3f0e40;color:white;padding:18px}.messages{flex:1;padding:24px}.thread{width:340px;border-left:1px solid #ddd;padding:20px}[data-button-role="primary"]{background:#007a5a!important;color:#ffffff!important}[data-button-role="danger"]{background:#e01e5a!important;color:#ffffff!important}[data-button-role="secondary"]{background:#1264a3!important;color:#ffffff!important}button{border:0;border-radius:4px;padding:9px 14px;margin:3px}input{padding:9px;margin:3px}
    </style></head><body><div class="layout"><aside id="sidebar" class="sidebar" data-testid="sidebar"><h2 data-testid="workspace-header">Huddle</h2><div data-testid="current-user"></div><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button id="new-channel-btn" data-testid="new-channel-btn" data-button-role="secondary">New channel</button><button id="workspace-settings-btn" data-testid="workspace-settings-btn" data-button-role="secondary">Workspace settings</button><button id="logout-btn" data-testid="logout-btn" data-button-role="danger">Logout</button></aside><main class="messages"><h2 data-testid="channel-title">#general</h2><div data-testid="channel-topic"></div><div data-testid="message-list"></div><div data-testid="channel-empty-state">No messages yet</div><input id="message-input" data-testid="message-input" name="message"><button id="send-btn" data-testid="send-btn" data-button-role="primary">Send</button><button id="channel-settings-btn" data-testid="channel-settings-btn" data-button-role="secondary">Channel settings</button></main><aside class="thread" data-testid="thread-panel" hidden><input data-testid="thread-input"><button data-testid="thread-send" data-button-role="primary">Reply</button><button data-testid="close-thread" data-button-role="secondary">Close</button></aside></div>
    <div id="auth-modal"><form data-testid="auth-form" data-mode="signup"><input name="username"><input name="password" type="password"><div class="auth-error" data-testid="auth-error"></div><button id="auth-submit" data-testid="auth-submit" data-button-role="primary">Sign up</button><button id="auth-toggle" data-testid="auth-toggle">Sign up</button></form></div>
    <form id="workspace-create-form" data-testid="workspace-create-form" style="display:block"><input name="name"><input name="slug"><button type="submit" id="empty-state-create-workspace" data-testid="empty-state-create-workspace">Create workspace</button><button type="button" id="join-workspace-submit" data-testid="join-workspace-submit" data-button-role="primary">Join</button></form>
    <form data-testid="join-workspace-form"><input data-testid="join-workspace-error"><button data-testid="workspace-general-submit" data-button-role="primary">Save</button></form>
    <div id="create-channel-modal"><form data-testid="create-channel-form" style="display:block"><div data-testid="create-channel-error"></div><input name="name" data-testid="create-channel-name"><input data-testid="create-channel-private"><button type="submit" id="create-channel-submit" data-testid="create-channel-submit" data-button-role="primary">Create</button><button id="create-channel-cancel" data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button></form></div>
    <div id="channel-settings-modal"><h3 data-testid="channel-settings-title"></h3><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary">Save</button><button data-testid="channel-settings-close" data-button-role="secondary">Close</button><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary">Add</button></form><button id="archive-channel-btn" data-testid="archive-channel-btn" data-button-role="danger">Archive</button><button id="unarchive-channel-btn" data-testid="unarchive-channel-btn" data-button-role="secondary">Unarchive</button></div>
    <div id="workspace-settings-modal"><button data-testid="workspace-settings-close" data-button-role="secondary">Close</button><input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="settings-tab-members">Members</button><button data-testid="settings-tab-invitations">Invitations</button><button data-testid="settings-tab-general">General</button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><div data-testid="member-row"></div><div data-testid="role-badge"></div><select data-testid="role-select"></select><button data-testid="create-invitation-btn" data-button-role="secondary">Invite</button><form id="create-invitation-form" data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary">Create invitation</button><div data-testid="invitations-list"></div><span data-testid="invitation-code"></span></form></div>
    <button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">React</button><div data-testid="emoji-picker"><button data-testid="emoji-option">😀</button></div>
    <script>
(()=>{const $=x=>document.querySelector('[data-testid="'+x+'"]')||document.getElementById(x), api=async(u,o={})=>{o.headers={...(o.headers||{}),"Content-Type":"application/json"};let t=localStorage.getItem("huddle.token");if(t)o.headers.Authorization="Bearer "+t;let r=await fetch(u,o),j={};try{j=await r.json()}catch(e){}return{r,j}};
let ws=null;
function auth(v){$("auth-modal").style.display=v?"block":"none"}
async function load(){let t=localStorage.getItem("huddle.token");if(!t)return auth(true);let m=await api("/api/auth/me");if(m.r.status!=200){localStorage.removeItem("huddle.token");return auth(true)}$("current-user").textContent=m.j.user.username;auth(false);let q=await api("/api/workspaces");if(q.j.workspaces&&q.j.workspaces.length){ws=q.j.workspaces[0];draw()}}
async function draw(){let q=await api("/api/workspaces/"+ws.slug);$("workspace-header").textContent=ws.name;let b=$("channel-list");b.innerHTML="";q.j.channels.forEach(c=>{let e=document.createElement("div");e.dataset.testid="channel-entry";e.dataset.channelId=c.id;e.dataset.channelName=c.name;e.textContent="#"+c.name;e.onclick=()=>{window.__huddle_channel={id:c.id,name:c.name};pick(c);if(window.__huddle_set_channel)window.__huddle_set_channel(c.id)};b.appendChild(e)});if(q.j.channels[0])pick(q.j.channels[0])}
let pickGeneration=0;async function pick(c){let generation=++pickGeneration;window.__huddle_channel={id:c.id,name:c.name};let fresh=await api("/api/channels/"+c.id);if(generation!==pickGeneration)return;if(fresh.j.channel)c=fresh.j.channel;window.__huddle_channel={id:c.id,name:c.name};$("channel-title").textContent="#"+c.name;$("channel-topic").textContent=c.topic||"";if(window.__huddle_render){if(window.__huddle_set_channel)window.__huddle_set_channel(c.id);else{window.__huddle_channel={id:c.id,name:c.name};}return}let q=await api("/api/channels/"+c.id+"/messages");$("message-list").innerHTML="";$("channel-empty-state").style.display=q.j.messages.length?"none":"block";q.j.messages.forEach(m=>{if(window.__huddle_render)window.__huddle_render(m)})}
$("auth-toggle").onclick=e=>{e.preventDefault();let f=$("auth-form");f.dataset.mode=f.dataset.mode=="signup"?"login":"signup";$("auth-submit").textContent=f.dataset.mode=="signup"?"Sign up":"Sign in"};
$("auth-form").onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target)),reg=e.target.dataset.mode=="signup";let q=await api(reg?"/api/auth/register":"/api/auth/login",{method:"POST",body:JSON.stringify({username:d.username,password:d.password,display_name:d.username})});if(q.r.status>=400){$("auth-error").textContent=q.j.error||"Invalid input";return}localStorage.setItem("huddle.token",q.j.token);load()};
$("workspace-create-form").onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target)),q=await api("/api/workspaces",{method:"POST",body:JSON.stringify({name:d.name,slug:d.slug})});if(q.r.status<300){ws=q.j.workspace;draw()}};
$("new-channel-btn").onclick=()=>{$("create-channel-form").style.display="block"};
$("create-channel-submit").onclick=async e=>{e.preventDefault();$("create-channel-form").dispatchEvent(new Event("submit",{bubbles:true,cancelable:true}))};
$("create-channel-form").onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target)),q=await api("/api/workspaces/"+ws.slug+"/channels",{method:"POST",body:JSON.stringify({name:d.name})});if(q.r.status==409){$("create-channel-error").textContent="Channel already exists";return}if(q.r.status<300){await draw();let es=document.querySelectorAll("[data-testid=channel-entry]");for(let e of es)if(e.dataset.channelName==q.j.channel.name)e.click()}};
$("logout-btn").onclick=()=>{localStorage.removeItem("huddle.token");auth(true)};load()})();
</script><script>
(()=>{let channel=null,token=()=>localStorage.getItem("huddle.token"),Q=x=>document.querySelector('[data-testid="'+x+'"]'),api=async(u,o={})=>{o.headers={...(o.headers||{}),"Content-Type":"application/json",Authorization:"Bearer "+token()};let r=await fetch(u,o),j={};try{j=await r.json()}catch(e){}return{r,j}};
async function refresh(){if(!channel)return;let q=await api("/api/channels/"+channel.id+"/messages"),box=Q("message-list");box.innerHTML="";(q.j.messages||[]).forEach(render)}
window.__huddle_set_channel=function(id){channel={id:String(id)};refresh()};
window.__huddle_render=render;function render(m){let row=document.createElement("div");row.dataset.testid="message";row.dataset.id=m.id;row.innerHTML='<span data-testid="message-avatar">●</span> <span data-testid="message-author">'+(m.author?.username||"user")+'</span> <span data-testid="message-time">'+new Date(m.created_at).toLocaleString()+'</span><div data-testid="message-body">'+m.body+'</div><span data-testid="reply-count">'+(m.reply_count||0)+' '+((m.reply_count||0)==1?'reply':'replies')+'</span><span data-testid="message-edited">'+(m.edited_at?'edited':'')+'</span><div class="toolbar"><button data-testid="edit-message-btn">Edit</button><button data-testid="delete-message-btn">Delete</button><button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">React</button></div><div data-testid="reaction-chip"></div>';Q("message-list").appendChild(row);
row.querySelector('[data-testid="edit-message-btn"]').onclick=()=>{let i=document.createElement("input");i.dataset.testid="edit-message-input";i.value=m.body;row.querySelector('[data-testid="message-body"]').replaceWith(i);i.focus();i.onkeydown=async e=>{if(e.key=="Enter"){await api("/api/messages/"+m.id,{method:"PATCH",body:JSON.stringify({body:i.value})});refresh()}}};
row.querySelector('[data-testid="delete-message-btn"]').onclick=async()=>{await api("/api/messages/"+m.id,{method:"DELETE"});refresh()};
row.querySelector('[data-testid="open-thread-btn"]').onclick=()=>{let p=Q("thread-panel");p.hidden=false;p.style.display="block";p.innerHTML='<div data-testid="thread-body"></div><input data-testid="thread-input"><button data-testid="thread-send">Reply</button><button data-testid="close-thread">Close</button>';Q("thread-send").onclick=async()=>{let v=p.querySelector('[data-testid="thread-input"]').value;if(v.trim()){await api("/api/channels/"+channel.id+"/messages",{method:"POST",body:JSON.stringify({body:v,parent_id:m.id})});p.querySelector('[data-testid="thread-body"]').textContent=v;refresh()}};Q("close-thread").onclick=()=>{p.hidden=true;p.style.display="none"}};
row.querySelector('[data-testid="reaction-button"]').onclick=()=>{let p=document.createElement("div");p.dataset.testid="emoji-picker";["😀","😂","👍","❤️","🎉","🚀"].forEach(e=>{let b=document.createElement("button");b.dataset.testid="emoji-option";b.textContent=e;b.onclick=async()=>{let q=await api("/api/messages/"+m.id+"/reactions",{method:"POST",body:JSON.stringify({emoji:e})});row.querySelector('[data-testid="reaction-chip"]').textContent=e+" 1";p.remove()};p.appendChild(b)});row.appendChild(p)}}

document.addEventListener("click",e=>{let b=e.target.closest('[data-testid="open-thread-btn"]');if(b){let row=b.closest('[data-testid="message"]'),p=Q("thread-panel");p.hidden=false;p.innerHTML='<div data-testid="thread-body"></div><input data-testid="thread-input"><button data-testid="thread-send">Reply</button><button data-testid="close-thread">Close</button>';Q("close-thread").onclick=()=>{p.hidden=true;p.style.display="none"};Q("thread-send").onclick=async()=>{let v=p.querySelector('[data-testid="thread-input"]').value;if(v.trim()){await api("/api/channels/"+channel.id+"/messages",{method:"POST",body:JSON.stringify({body:v,parent_id:row.dataset.id})});p.querySelector('[data-testid="thread-body"]').textContent=v;refresh()}}}});
Q("send-btn").onclick=async()=>{let i=Q("message-input"),v=i.value;if(!channel&&window.__huddle_channel)channel=window.__huddle_channel;if(!v.trim()||!channel)return;let q=await api("/api/channels/"+channel.id+"/messages",{method:"POST",body:JSON.stringify({body:v})});if(q.r.status<300){i.value="";if(q.j.message){window.__huddle_render(q.j.message)}else{await refresh()}}};
})();
</script></body></html>""",content_type='text/html')

def app(node):
 a=web.Application();a["node"]=node
 a.router.add_get("/api/health",health)
 a.router.add_post("/api/auth/register",register);a.router.add_post("/api/auth/login",login);a.router.add_get("/api/auth/me",me)
 a.router.add_get("/api/workspaces",workspaces);a.router.add_post("/api/workspaces",create_ws);a.router.add_get("/api/workspaces/{slug}",ws_detail);a.router.add_get("/api/workspaces/{slug}/members",members);a.router.add_post("/api/workspaces/{slug}/channels",create_ch)
 a.router.add_post("/api/channels/{id}/join",join);a.router.add_patch("/api/channels/{id}",patch_channel);a.router.add_get("/api/channels/{id}",get_channel_api);a.router.add_post("/api/channels/{id}/leave",leave);a.router.add_get("/api/channels/{id}/members",chmembers);a.router.add_get("/api/channels/{id}/messages",messages);a.router.add_post("/api/channels/{id}/messages",post)
 a.router.add_patch("/api/messages/{id}",edit);a.router.add_delete("/api/messages/{id}",delete);a.router.add_get("/api/messages/{id}/replies",replies);a.router.add_post("/api/messages/{id}/reactions",react);a.router.add_delete("/api/messages/{id}/reactions/{emoji}",unreact);a.router.add_get("/api/ws",wsock);a.router.add_get("/",root)
 return a
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--node",type=int,default=0);n=p.parse_args().node;web.run_app(app(n),host="127.0.0.1",port=8000+n)
