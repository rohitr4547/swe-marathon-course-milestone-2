import asyncio,json,argparse,re,uuid
from aiohttp import web,WSMsgType
import core
NODE=0
def j(x,status=200): return web.json_response(x,status=status)
def err(s,m): return j({'error':m},s)
async def body(r):
    try: x=await r.json(); return x if isinstance(x,dict) else None
    except: return None
def user(r):
    a=r.headers.get('Authorization','')
    token=a[7:] if a.startswith('Bearer ') else r.query.get('token')
    return core.user_for_token(token)
def auth(r):
    u=user(r); return u
def msgjson(r):
    d=dict(r); d['author']=core.user_to_json(core.connect().execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone())
    d['parent_id']=d['parent_id']; d['reply_count']=0; d['files']=[]; d['reactions']=[]; d['mentions']=[]
    return d
async def health(r): return j({'status':'ok','node_id':NODE})
async def register(r):
    x=await body(r)
    if not x or not isinstance(x.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',x['username']) or not isinstance(x.get('password'),str) or len(x['password'])<8:return err(400,'invalid registration')
    with core.tx() as c:
        try:
            cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(x['username'],core.hash_password(x['password']),x.get('display_name') or x['username'],core.now_iso()))
            uid=cur.lastrowid;t=core.issue_token(c,uid); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        except: return err(409,'username exists')
    return j({'user':core.user_to_json(u),'token':t},201)
async def login(r):
    x=await body(r); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(x.get('username') if x else '',)).fetchone()
    if not u or not isinstance(x.get('password'),str) or not core.verify_password(x['password'],u['password']): c.close(); return err(401,'invalid credentials')
    t=core.issue_token(c,u['id']); c.close(); return j({'user':core.user_to_json(u),'token':t})
async def me(r):
    u=auth(r); return j({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspaces(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return j({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_ws(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    x=await body(r)
    if not x or not isinstance(x.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',x['slug']) or not isinstance(x.get('name'),str):return err(400,'invalid workspace')
    with core.tx() as c:
        try:cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],u['id'])); wid=cur.lastrowid;c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); ch=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,u['id']))
        except: return err(409,'duplicate workspace')
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); cc=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
    return j({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(cc)},201)
async def wsdetail(r):
    u=auth(r); w=core.get_workspace(r.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
    return j({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':{}})
async def create_ch(r):
    u=auth(r);w=core.get_workspace(r.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    x=await body(r)
    if not x or not isinstance(x.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',x['name']):return err(400,'invalid channel')
    with core.tx() as c:
        try:cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],x['name'],int(bool(x.get('is_private'))),x.get('topic','')));i=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(i,u['id']))
        except:return err(409,'duplicate channel')
        q=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
    return j({'channel':core.channel_to_json(q)},201)
async def join(r):
    u=auth(r);ch=core.get_channel(r.match_info['id'])
    if not u:return err(401,'unauthorized')
    if not ch:return err(404,'not found')
    with core.tx() as c:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
    return j({'channel':core.channel_to_json(ch)})
async def post(r):
    u=auth(r); ch=core.get_channel(r.match_info['id'])
    if not u:return err(401,'unauthorized')
    if not ch:return err(404,'not found')
    x=await body(r)
    if not x or not isinstance(x.get('body'),str) or not x['body'].strip():return err(400,'empty body')
    with core.tx() as c:
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],x['body'],x.get('parent_id'),core.now_iso()));i=cur.lastrowid;q=c.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone();frame=core.emit_event(c,ch['id'],'message.reply' if x.get('parent_id') else 'message.created',{'message':msgjson(q)})
    return j({'message':msgjson(q)},201)
async def messages(r):
    u=auth(r);ch=core.get_channel(r.match_info['id'])
    if not u:return err(401,'unauthorized')
    if not ch:return err(404,'not found')
    c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC',(ch['id'],)).fetchall();c.close()
    return j({'messages':[msgjson(x) for x in rows],'next_cursor':None})
async def ws(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    w=web.WebSocketResponse();await w.prepare(r); subs={}; q=asyncio.Queue()
    async def poll():
        while not w.closed:
            for ch,last in list(subs.items()):
                for e in core.events_after(ch,last): subs[ch]=e['event_id'];await w.send_json(e)
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    try:
        async for m in w:
            if m.type==WSMsgType.TEXT:
                x=json.loads(m.data);ch=int(x.get('channel_id',0))
                if x.get('type')=='subscribe':
                    head=core.head_event_id(ch); subs[ch]=head
                    await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head})
                elif x.get('type')=='resume':
                    since=int(x.get('since_event_id',0))
                    for e in core.events_after(ch,since):
                        subs[ch]=e['event_id']; await w.send_json(e)
                    head=core.head_event_id(ch); subs[ch]=head
                    await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':head})
    finally:task.cancel()
    return w
async def root(r):
    try:return web.FileResponse('/app/static/index.html')
    except:return web.Response(text='Huddle')
def app():
    a=web.Application();a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces/{slug}',wsdetail);a.router.add_post('/api/workspaces/{slug}/channels',create_ch);a.router.add_post('/api/channels/{id}/join',join);a.router.add_post('/api/channels/{id}/messages',post);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_get('/api/ws',ws);a.router.add_get('/',root);return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(app(),host='127.0.0.1',port=8000+NODE)
