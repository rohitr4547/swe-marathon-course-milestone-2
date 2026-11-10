import sys, json, asyncio, re, uuid, os
from aiohttp import web, WSMsgType
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(r):
    try: return await r.json()
    except: return None
def require_user(r):
    h=r.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
def U(u): return core.user_to_json(u)
def C(r): return dict(r) if r else None
def memberships(uid,wid):
    c=core.connect(); x=c.execute('SELECT c.* FROM channels c JOIN channel_members m ON m.channel_id=c.id WHERE m.user_id=? AND c.workspace_id=? ORDER BY c.id',(uid,wid)).fetchall(); c.close(); return [C(a) for a in x]
def msg(r):
    return core.message_to_json(r)


async def register(r):
    d=await read_json(r)
    if not isinstance(d,dict) or not re.fullmatch(r'[A-Za-z0-9_]+',str(d.get('username',''))) or not isinstance(d.get('password'),str) or len(d['password'])<8:return json_error(400,'invalid registration')
    try:
        with core.tx() as c:
            cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
            uid=cur.lastrowid;t=core.issue_token(c,uid);u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        return web.json_response({'user':U(u),'token':t},status=201)
    except Exception:return json_error(409,'username already exists')
async def login(r):
    d=await read_json(r);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username','') if isinstance(d,dict) else '',)).fetchone()
    if not u or not isinstance(d.get('password'),str) or not core.verify_password(d['password'],u['password']):c.close();return json_error(401,'invalid credentials')
    t=core.issue_token(c,u['id']);c.close();return web.json_response({'user':U(u),'token':t})
async def me(r):
    u=require_user(r);return json_error(401,'unauthorized') if not u else web.json_response({'user':U(u)})
async def workspaces(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();x=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return web.json_response({'workspaces':[dict(v) for v in x]})
async def create_workspace(r):
    u=require_user(r);d=await read_json(r)
    if not u:return json_error(401,'unauthorized')
    if not isinstance(d,dict) or not re.fullmatch(r'[a-z0-9-]{2,32}',str(d.get('slug',''))) or not d.get('name'):return json_error(400,'invalid workspace')
    try:
        with core.tx() as c:
            wid=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id'])).lastrowid
            c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'));cid=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')).lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        return web.json_response({'workspace':dict(w),'general_channel':C(ch)},status=201)
    except Exception:return json_error(409,'duplicate workspace')
async def workspace(r):
    u=require_user(r);w=core.get_workspace(r.match_info['slug'])
    if not u:return json_error(401,'unauthorized')
    if not w:return json_error(404,'not found')
    return web.json_response({'workspace':dict(w),'channels':memberships(u['id'],w['id']),'read_state':{}})
async def channels(r):
    u=require_user(r);w=core.get_workspace(r.match_info['slug']);d=await read_json(r)
    if not u:return json_error(401,'unauthorized')
    if not w:return json_error(404,'not found')
    if not isinstance(d,dict) or not re.fullmatch(r'[a-z0-9-]{1,32}',str(d.get('name',''))):return json_error(400,'invalid channel')
    chk=core.connect(); exists=chk.execute('SELECT id FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone(); chk.close()
    if exists:return json_error(409,'duplicate channel')
    try:
        with core.tx() as c:
            cid=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],d['name'],int(bool(d.get('is_private'))),d.get('topic',''))).lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']));ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        return web.json_response({'channel':C(ch)},status=201)
    except Exception:return json_error(409,'duplicate channel')
async def patch_channel(r):
    u=require_user(r); ch=core.get_channel(r.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    d=await read_json(r)
    if not isinstance(d,dict) or not isinstance(d.get('topic',''),str) or len(d.get('topic',''))>250:return json_error(400,'invalid topic')
    with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),ch['id'])); ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return web.json_response({'channel':dict(ch)})
async def join(r):
    u=require_user(r);ch=core.get_channel(r.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    with core.tx() as c:
        if ch['workspace_id']:
            c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
        c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
    return web.json_response({'channel':C(ch)})

async def edit_delete_message(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    try: mid=int(r.match_info['id'])
    except:return json_error(404,'not found')
    try:
        if r.method=='PATCH':
            d=await read_json(r)
            if not isinstance(d,dict): return json_error(400,'invalid json')
            return web.json_response({'message':core.edit_message(u,mid,d.get('body'))})
        ok=core.delete_message(u,mid)
        return web.json_response({'deleted':True})
    except LookupError:return json_error(404,'not found')
    except PermissionError:return json_error(403,'forbidden')
    except RuntimeError:return json_error(409,'message deleted')
    except ValueError:return json_error(400,'invalid body')
async def messages(r):
    u=require_user(r);ch=core.get_channel(r.match_info['id'])
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if r.method=='POST':
      d=await read_json(r); body=d.get('body') if isinstance(d,dict) else None
      if not isinstance(body,str) or not body.strip():return json_error(400,'empty message')
      with core.tx() as c:
       cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],body,d.get('parent_id'),core.now_iso()));i=cur.lastrowid;x=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone();core.emit_event(c,ch['id'],'message.created',{'message':{'id':x['id'],'channel_id':x['channel_id'],'author_id':x['author_id'],'body':x['body'],'parent_id':x['parent_id'],'created_at':x['created_at'],'edited_at':x['edited_at'],'reply_count':0,'files':[],'reactions':[],'mentions':[]}})
      return web.json_response({'message':msg(x)},status=201)
    c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(ch['id'],)).fetchall();c.close();return web.json_response({'messages':[msg(x) for x in rows],'next_cursor':None})
async def replies(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(r.match_info['id'],)).fetchall(); c.close()
    return web.json_response({'replies':[core.message_to_json(x) for x in rows],'next_cursor':None})
async def ws(r):
    u=core.user_for_token(r.query.get('token')); 
    if not u:return json_error(401,'unauthorized')
    q=web.WebSocketResponse();await q.prepare(r); subs={}; 
    async def pump():
      while not q.closed:
       for cid,last in list(subs.items()):
        for e in core.events_after(cid,last): await q.send_json(e);subs[cid]=e['event_id']
       await asyncio.sleep(.05)
    task=asyncio.create_task(pump())
    try:
     async for m in q:
      if m.type==WSMsgType.TEXT:
       d=json.loads(m.data);cid=int(d.get('channel_id',0))
       if d.get('type')=='subscribe':subs[cid]=core.head_event_id(cid);await q.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
       elif d.get('type')=='resume':subs[cid]=int(d.get('since_event_id',0));await q.send_json({'type':'resumed','channel_id':cid,'head_event_id':core.head_event_id(cid)})
    finally:task.cancel()
    return q
async def reactions(r):
    u=require_user(r)
    if not u:return json_error(401,'unauthorized')
    mid=int(r.match_info['id']);d=await read_json(r);emoji=d.get('emoji') if isinstance(d,dict) else None
    if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
    try:
        with core.tx() as c:c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
    except: return json_error(404,'not found')
    c=core.connect();x=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji',(mid,)).fetchall();c.close()
    return web.json_response({'reactions':[{'emoji':z['emoji'],'count':z['n'],'user_ids':[int(v) for v in z['ids'].split(',')]} for z in x]})
async def health(r):
    return web.json_response({'status':'ok','node_id':NODE})
app=web.Application()
app.router.add_get('/app.js',lambda r:web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'}));app.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html',headers={'Content-Type':'text/html'}));app.router.add_get('/api/health',health);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_workspace);app.router.add_get('/api/workspaces/{slug}',workspace);app.router.add_post('/api/workspaces/{slug}/channels',channels)
app.router.add_post('/api/channels/{id}/join',join);app.router.add_patch('/api/channels/{id}',patch_channel);app.router.add_route('*','/api/channels/{id}/messages',messages);app.router.add_patch('/api/messages/{id}',edit_delete_message);app.router.add_post('/api/messages/{id}/reactions',reactions);app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_delete('/api/messages/{id}',edit_delete_message);app.router.add_get('/api/ws',ws)
app.router.add_get('/{path:.*}',lambda r:web.FileResponse('/app/static/index.html') if os.path.exists('/app/static/index.html') else web.Response(text='Huddle'))
web.run_app(app,host='127.0.0.1',port=8000+NODE)
