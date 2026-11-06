import argparse, json, os, re, asyncio
from aiohttp import web, WSMsgType
import core
NODE=0
def json_error(s,m): return web.json_response({'error':m},status=s)
async def read_json(r):
    try:
        x=await r.json()
        return x if isinstance(x,dict) else None
    except: return None
def require_user(r):
    h=r.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
def user_or_401(r):
    u=require_user(r); return u
def valid_body(d): return isinstance(d,dict)
async def health(r): return web.json_response({'status':'ok','node_id':NODE})
async def register(r):
    d=await read_json(r)
    if not d or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d['username']) or not isinstance(d.get('password'),str) or len(d['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(d['username'],)).fetchone():return json_error(409,'username exists')
        cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
        u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
    return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
async def login(r):
    d=await read_json(r); c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if d else '',)).fetchone();c.close()
    if not u or not isinstance(d.get('password'),str) or not core.verify_password(d['password'],u['password']):return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return web.json_response({'user':core.user_to_json(u),'token':t})
async def me(r):
    u=user_or_401(r)
    return web.json_response({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def workspaces(r):
    u=user_or_401(r)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_workspace(r):
    u=user_or_401(r)
    if not u:return json_error(401,'unauthorized')
    d=await read_json(r)
    if not d or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d['slug']) or not isinstance(d.get('name'),str) or not d['name'].strip():return json_error(400,'invalid workspace')
    try:
        with core.tx() as c:
            if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(d['slug'],)).fetchone():return json_error(409,'duplicate')
            cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']));wid=cur.lastrowid
            core.add_workspace_member(c,wid,u['id'],'owner'); cid=core.create_channel_row(c,wid,'general')
            core.add_channel_member(c,cid,u['id'])
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    except: return json_error(409,'duplicate')
    return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},status=201)
async def workspace(r):
    u=user_or_401(r)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(r.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect(); mem=c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone(); rs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close()
    if not mem:return json_error(403,'forbidden')
    return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rs],'read_state':{}})
async def members_workspace(r):
    u=user_or_401(r); w=core.get_workspace(r.match_info['slug']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not w:return json_error(404,'not found')
    c=core.connect();rs=c.execute('SELECT m.*,u.username,u.display_name FROM members m JOIN users u ON u.id=m.user_id WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
    return web.json_response({'members':[dict(x) for x in rs]})
async def create_channel(r):
    u=user_or_401(r); w=core.get_workspace(r.match_info['slug']) if u else None
    if not u:return json_error(401,'unauthorized')
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin','member'):return json_error(403,'forbidden')
    d=await read_json(r)
    if not d or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']) or len(d.get('topic',''))>250:return json_error(400,'invalid channel')
    if d.get('is_private') and core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return json_error(409,'duplicate')
        cid=core.create_channel_row(c,w['id'],d['name'],d.get('is_private',False),d.get('topic',''));core.add_channel_member(c,cid,u['id']);ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    return web.json_response({'channel':core.channel_to_json(ch)},status=201)
async def channel_join(r):
    u=user_or_401(r); ch=core.get_channel(int(r.match_info['id'])) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    ok,e=core.join_channel(u,ch['id'])
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))}) if ok else json_error(403,e)
async def channel_leave(r):
    u=user_or_401(r);ch=core.get_channel(int(r.match_info['id'])) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    with core.tx() as c:core.remove_channel_member(c,ch['id'],u['id'])
    return web.json_response({'channel':core.channel_to_json(ch)})
async def channel_members(r):
    u=user_or_401(r);ch=core.get_channel(int(r.match_info['id'])) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    return web.json_response({'members':[core.user_to_json(x) for x in rs]})
async def patch_channel(r):
    u=user_or_401(r);ch=core.get_channel(int(r.match_info['id'])) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    d=await read_json(r)
    if not d or not isinstance(d.get('topic',''),str) or len(d.get('topic',''))>250:return json_error(400,'invalid')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),ch['id']));ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return web.json_response({'channel':core.channel_to_json(ch)})
async def messages(r):
    u=user_or_401(r);ch=core.get_channel(int(r.match_info['id'])) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    try: lim,cur=core.parse_paging(r.query,'messages')
    except:return json_error(400,'invalid cursor')
    xs,n=core.list_messages(ch['id'],lim,cur);return web.json_response({'messages':xs,'next_cursor':n})
async def post_message(r):
    u=user_or_401(r);ch=core.get_channel(int(r.match_info['id'])) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    d=await read_json(r)
    if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'empty body')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    if not core.require_writable(ch):return json_error(423,'archived')
    try:m=core.create_message(u,ch['id'],d['body'],d.get('parent_id'))
    except PermissionError:return json_error(403,'forbidden')
    except RuntimeError:return json_error(423,'archived')
    return web.json_response({'message':m},status=201)
async def replies(r):
    u=user_or_401(r);c=core.connect();p=c.execute('SELECT * FROM messages WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
    if not u:return json_error(401,'unauthorized')
    if not p:return json_error(404,'not found')
    try:lim,cur=core.parse_paging(r.query,'replies')
    except:return json_error(400,'invalid cursor')
    xs,n=core.list_replies(p['id'],lim,cur);return web.json_response({'replies':xs,'next_cursor':n})
async def edit(r):
    u=user_or_401(r)
    if not u:return json_error(401,'unauthorized')
    d=await read_json(r)
    if not d or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'invalid body')
    try:m=core.edit_message(u,int(r.match_info['id']),d['body'])
    except LookupError:return json_error(404,'not found')
    except RuntimeError:return json_error(409,'deleted')
    except PermissionError:return json_error(403,'forbidden')
    return web.json_response({'message':m})
async def delete(r):
    u=user_or_401(r)
    if not u:return json_error(401,'unauthorized')
    try:core.delete_message(u,int(r.match_info['id']))
    except LookupError:return json_error(404,'not found')
    except PermissionError:return json_error(403,'forbidden')
    return web.json_response({'deleted':True})
async def reaction(r):
    u=user_or_401(r)
    if not u:return json_error(401,'unauthorized')
    d=await read_json(r)
    if not d or not isinstance(d.get('emoji'),str) or not d['emoji']:return json_error(400,'invalid emoji')
    try:x=core.react(u,int(r.match_info['id']),d['emoji'],True)
    except LookupError:return json_error(404,'not found')
    return web.json_response({'reactions':x})
async def unreaction(r):
    u=user_or_401(r)
    if not u:return json_error(401,'unauthorized')
    try:x=core.react(u,int(r.match_info['id']),r.match_info['emoji'],False)
    except LookupError:return json_error(404,'not found')
    return web.json_response({'reactions':x})
async def archive(r):
    u=user_or_401(r);ch=core.get_channel(int(r.match_info['id'])) if u else None
    if not u:return json_error(401,'unauthorized')
    if not ch:return json_error(404,'not found')
    if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if r.path.endswith('archive') else 0,ch['id']))
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def ws_handler(r):
    tok=r.query.get('token','');u=core.user_for_token(tok)
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse();await ws.prepare(r); subs={}; alive=True
    async def pump():
        while alive:
            for cid,last in list(subs.items()):
                for ev in core.events_after(cid,last):
                    await ws.send_json(ev);subs[cid]=ev['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(pump())
    try:
        async for msg in ws:
            if msg.type==WSMsgType.TEXT:
                try:d=json.loads(msg.data)
                except:continue
                cid=d.get('channel_id')
                if d.get('type')=='subscribe' and isinstance(cid,int):
                    subs[cid]=core.head_event_id(cid);await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
                elif d.get('type')=='resume' and isinstance(cid,int):
                    subs[cid]=int(d.get('since_event_id',0))
                    await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':core.head_event_id(cid)})
    finally:
        alive=False;task.cancel()
    return ws
async def index(r):
    try:return web.Response(text=open('/app/static/index.html').read(),content_type='text/html')
    except:return web.Response(text='<h1>Huddle</h1>',content_type='text/html')
def app():
    a=web.Application()
    a.router.add_get('/',index);a.router.add_get('/api/health',health)
    a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_get('/api/workspaces/{slug}/members',members_workspace);a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/channels/{id}/join',channel_join);a.router.add_post('/api/channels/{id}/leave',channel_leave);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_post('/api/channels/{id}/archive',archive);a.router.add_post('/api/channels/{id}/unarchive',archive)
    a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',post_message);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_patch('/api/messages/{id}',edit);a.router.add_delete('/api/messages/{id}',delete);a.router.add_post('/api/messages/{id}/reactions',reaction);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',unreaction);a.router.add_get('/api/ws',ws_handler)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node
    core.init_db();web.run_app(app(),host='127.0.0.1',port=8000+NODE)
