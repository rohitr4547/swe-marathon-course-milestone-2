import asyncio, argparse, json, os, re, uuid, base64
from aiohttp import web, WSMsgType
import core
NODE=0
ACTIVE_USERS=set()
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(req):
    try:
        x=await req.json()
        return x if isinstance(x,dict) else None
    except: return None
def require_user(req):
    h=req.headers.get('Authorization','')
    t=h[7:] if h.startswith('Bearer ') else req.query.get('token')
    return core.user_for_token(t)
def auth(req):
    u=require_user(req); return u
def ok(data,status=200): return web.json_response(data,status=status)
def paging(req,kind):
    try:
        limit=int(req.query.get('limit','50'))
        if limit<1 or limit>200: raise ValueError()
        cur=req.query.get('cursor')
        if not cur:return limit,None
        raw=base64.urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)); x=json.loads(raw)
        if x.get('k')!=kind or not isinstance(x.get('v'),int):raise ValueError()
        return limit,x['v']
    except:return None,None
def member(ch,uid):
    return core.can_read_channel(uid,ch)
async def health(req): return ok({'status':'ok','node_id':NODE})
async def register(req):
    b=await read_json(req)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')) or not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone():return json_error(409,'duplicate username')
        cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
        u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
    return ok({'user':core.user_to_json(u),'token':t},201)
async def login(req):
    b=await read_json(req); u=None
    if b and isinstance(b.get('username'),str): 
        c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone();c.close()
    if not u or not isinstance(b.get('password'),str) or not core.verify_password(b['password'],u['password']):return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return ok({'user':core.user_to_json(u),'token':t})
async def me(req):
    u=auth(req)
    return ok({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def create_workspace(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    b=await read_json(req)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str):return json_error(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return json_error(409,'duplicate')
        cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])); wid=cur.lastrowid
        c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(wid,u['id'],'owner')); ch=core.create_channel_row(c,wid,'general');core.add_channel_member(c,ch['id'],u['id'])
    return ok({'workspace':core.workspace_to_json(coreread(wid)),'general_channel':core.channel_to_json(ch)},201)
def coreread(wid):
    c=core.connect();r=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();c.close();return r
async def workspaces(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return ok({'workspaces':[core.workspace_to_json(r) for r in rows]})
async def workspace(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect(); cs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],req.query.get('include_archived') in ('1','true'))).fetchall();c.close()
    return ok({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in cs],'read_state':{}})
async def create_channel(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) is None:return json_error(403,'forbidden')
    b=await read_json(req)
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return json_error(400,'invalid channel')
    if b.get('is_private') and core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return json_error(409,'duplicate')
        ch=core.create_channel_row(c,w['id'],b['name'],bool(b.get('is_private')),b.get('topic',''))
        core.add_channel_member(c,ch['id'],u['id'])
    return ok({'channel':core.channel_to_json(ch)},201)
async def join(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if ch['workspace_id']:
        w=core.get_workspace_by_id(ch['workspace_id']) if hasattr(core,'get_workspace_by_id') else coreread(ch['workspace_id'])
        if core.member_role(w['id'],u['id']) is None:
            if w['join_mode']!='open':return json_error(403,'forbidden')
            with core.tx() as c:core.add_workspace_member(c,w['id'],u['id'])
    with core.tx() as c:core.add_channel_member(c,ch['id'],u['id'])
    return ok({'channel':core.channel_to_json(ch)})
async def messages(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    owner=False
    if ch['workspace_id']:
        cc=core.connect(); rr=cc.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone(); cc.close(); owner=bool(rr and rr['role']=='owner')
    if not member(ch,u['id']) and not owner:return json_error(403,'forbidden')
    if owner and not member(ch,u['id']):
        with core.tx() as c: core.add_channel_member(c,ch['id'],u['id'])
    if req.method=='GET':return ok({'messages':core.list_messages(ch['id']) ,'next_cursor':None})
    b=await read_json(req)
    if not b:return json_error(400,'invalid body')
    body=b.get('body')
    if body in ('/archive','/unarchive') and ch['workspace_id']:
        cc=core.connect(); rr=cc.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone(); cc.close()
        if not rr or rr['role']!='owner':return json_error(403,'forbidden')
        val=body=='/archive'
        with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(val),ch['id']))
        return ok({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
    if isinstance(body,str) and body.startswith('/') and not body.startswith('//'):
        parts=body.split(' ',1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
        if cmd=='/me':
            if not arg.strip():return json_error(400,'text')
        elif cmd=='/shrug':
            body=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
        elif cmd=='/topic':
            if not arg.strip():return json_error(400,'topic')
            with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']))
            return ok({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
        elif cmd=='/invite':
            if not re.fullmatch(r'@[A-Za-z0-9_]+',arg.strip()):return json_error(400,'invite')
            c=core.connect();target=c.execute('SELECT * FROM users WHERE username=?',(arg.strip()[1:],)).fetchone();c.close()
            if not target:return json_error(404,'user')
            with core.tx() as c:
                if ch['workspace_id']: core.add_workspace_member(c,ch['workspace_id'],target['id'])
                core.add_channel_member(c,ch['id'],target['id'])
            return ok({'message':None,'invited_user':core.user_to_json(target)},201)
        elif cmd in ('/archive','/unarchive'):
            w=core.get_workspace(ch['workspace_id']) if ch['workspace_id'] else None
            if not w or core.member_role(w['id'],u['id'])!='owner':return json_error(403,'forbidden')
            with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(cmd=='/archive',ch['id']))
            return ok({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
        else:return json_error(400,'unknown command')
    elif isinstance(body,str) and body.startswith('//'):
        body=body[1:]
    try:m=core.create_message(u,ch['id'],body,b.get('parent_id'),b.get('file_ids'))
    except PermissionError:return json_error(403,'forbidden')
    except ValueError as e:return json_error(423 if str(e)=='archived' else 400,str(e))
    return ok({'message':m},201)
async def replies(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    if not member(core.get_channel(r['channel_id']),u['id']):return json_error(403,'forbidden')
    return ok({'replies':core.list_messages(r['channel_id'],True,r['id']),'next_cursor':None})
async def edit(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    mid=int(req.match_info['id']); b=await read_json(req)
    try:
        if not b:return json_error(400,'invalid body')
        return ok({'message':core.edit_message(u,mid,b.get('body'))})
    except LookupError:return json_error(404,'not found')
    except RuntimeError:return json_error(409,'deleted')
    except PermissionError:return json_error(403,'forbidden')
    except:return json_error(400,'invalid body')
async def delete(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    try:return ok({'deleted':core.delete_message(u,int(req.match_info['id']))})
    except LookupError:return json_error(404,'not found')
    except PermissionError:return json_error(403,'forbidden')
async def reaction(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    b=await read_json(req) if req.method=='POST' else {}
    emoji=b.get('emoji') if req.method=='POST' else req.match_info.get('emoji')
    if not isinstance(emoji,str) or not emoji:return json_error(400,'emoji')
    try:
        c=core.connect(); mr=c.execute('SELECT m.channel_id FROM messages m WHERE m.id=?',(int(req.match_info['id']),)).fetchone(); c.close()
        if not mr:return json_error(404,'not found')
        if not member(core.get_channel(mr['channel_id']),u['id']):return json_error(403,'forbidden')
        return ok({'reactions':core.react(u,int(req.match_info['id']),emoji,req.method=='POST')})
    except LookupError:return json_error(404,'not found')

async def dms(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    b=await read_json(req)
    if not b or not isinstance(b.get('user_id'),int):return json_error(400,'user_id')
    c=core.connect();other=c.execute('SELECT * FROM users WHERE id=?',(b['user_id'],)).fetchone();c.close()
    if not other:return json_error(404,'not found')
    return ok({'channel_id':core.open_dm(u,b['user_id'])})
async def files(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    if req.method=='POST':
        try:
            rd=await req.multipart(); part=await rd.next()
            if not part or part.name!='file':return json_error(400,'file')
            data=await part.read()
            if len(data)>10485760:return json_error(413,'too large')
            return ok({'file':core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)},201)
        except:return json_error(400,'file')
    fid=int(req.match_info['id']);c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    return ok({'file':core.file_to_json(r)})
async def file_download(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    return web.Response(body=open('/app/data/files/'+str(r['id']),'rb').read(),content_type=r['content_type'],headers={'Content-Disposition':'attachment; filename="'+r['filename']+'"'})


async def groups(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    handle=req.match_info.get('handle')
    c=core.connect()
    g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone() if handle else None
    if req.method=='GET':
        rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall()
        out=[]
        for x in rows:
            ids=[z['user_id'] for z in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(x['id'],))]
            out.append({'id':x['id'],'handle':x['handle'],'name':x['name'],'member_user_ids':ids})
        c.close();return ok({'groups':out})
    if req.method=='POST':
        b=await read_json(req)
        if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(b.get('user_ids'),list):c.close();return json_error(400,'invalid group')
        if g:c.close();return json_error(409,'duplicate')
        valid={x['user_id'] for x in c.execute('SELECT user_id FROM workspace_members WHERE workspace_id=?',(w['id'],))}
        if any(not isinstance(x,int) or x not in valid for x in b['user_ids']):c.close();return json_error(400,'members')
        try: cur=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],b['handle'],b['name'],u['id']))
        except Exception:
            c.close();return json_error(409,'duplicate')
        for x in b['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(cur.lastrowid,x))
        c.close();return ok({'group':{'id':cur.lastrowid,'handle':b['handle'],'name':b['name'],'member_user_ids':b['user_ids']}},201)
    if not g:
        c.close()
        return ok({'deleted':True}) if req.method=='DELETE' and core.member_role(w['id'],u['id']) else json_error(404,'not found')
    if g['creator_id']!=u['id'] and core.member_role(w['id'],u['id'])!='owner':c.close();return json_error(403,'forbidden')
    if req.method=='DELETE':
        c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.close();return ok({'deleted':True})
    b=await read_json(req)
    if not b:c.close();return json_error(400,'body')
    if 'name' in b:c.execute('UPDATE groups_ SET name=? WHERE id=?',(b['name'],g['id']))
    if 'user_ids' in b:
        valid={x['user_id'] for x in c.execute('SELECT user_id FROM workspace_members WHERE workspace_id=?',(w['id'],))}
        if not isinstance(b['user_ids'],list) or any(x not in valid for x in b['user_ids']):c.close();return json_error(400,'members')
        c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
        for x in b['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],x))
    ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]
    c.close();return ok({'group':{'id':g['id'],'handle':g['handle'],'name':b.get('name',g['name']),'member_user_ids':ids}})
async def profile(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    if req.method=='GET':
        c=core.connect();r=c.execute('SELECT * FROM users WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
        return ok({'user':core.user_to_json(r)}) if r else json_error(404,'not found')
    b=await read_json(req)
    if not b:return json_error(400,'body')
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    limits={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512),'timezone':(1,128)}
    if any(k not in allowed or not isinstance(v,str) or len(v)<limits[k][0] or len(v)>limits[k][1] for k,v in b.items()):return json_error(400,'invalid profile')
    if 'timezone' in b:
        try: __import__('zoneinfo').ZoneInfo(b['timezone'])
        except:return json_error(400,'timezone')
    with core.tx() as c:
        vals={k:v for k,v in b.items() if k in allowed and isinstance(v,str)}
        if vals:c.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',(*vals.values(),u['id']))
        r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone();core.emit_global(c,{'type':'user.updated','user':core.user_to_json(r)})
    return ok({'user':core.user_to_json(r)})




async def channel_patch(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not member(ch,u['id']):return json_error(403,'forbidden')
    b=await read_json(req)
    if not b or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return json_error(400,'topic')
    with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']))
    return ok({'channel':core.channel_to_json(core.get_channel(ch['id']))})

async def channel_get(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not member(ch,u['id']):return json_error(403,'forbidden')
    return ok({'channel':core.channel_to_json(ch)})

async def channel_members(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not member(ch,u['id']):return json_error(403,'forbidden')
    c=core.connect();rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    return ok({'members':[core.user_to_json(x) for x in rows]})
async def archive_route(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    val=req.match_info['action']=='archive'
    with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(val),ch['id']))
    return ok({'channel':core.channel_to_json(core.get_channel(ch['id']))})

async def ws_handler(req):
    u=auth(req)
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse();await ws.prepare(req); subs={}; alive=True; conn_id=str(uuid.uuid4()); core.touch_presence(conn_id,u['id']); ACTIVE_USERS.add(u['id']); global_seen=0
    async def sender():
        nonlocal global_seen
        while not ws.closed:
            core.touch_presence(conn_id,u['id'])
            nonlocal_global=globals()
            for gid,frame in core.globals_after(global_seen):
                await ws.send_json(frame)
                global_seen=gid
            for ch,last in list(subs.items()):
                for e in core.events_after(ch,last):
                    await ws.send_json(e);subs[ch]=e['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(sender())
    try:
        async for msg in ws:
            if msg.type==WSMsgType.TEXT:
                x=json.loads(msg.data);ch=int(x.get('channel_id',0)); core.touch_presence(conn_id,u['id'])
                if x.get('type')=='subscribe':
                    core.touch_presence(conn_id,u['id']); subs[ch]=core.head_event_id(ch);await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':subs[ch]})
                elif x.get('type')=='resume':
                    subs[ch]=int(x.get('since_event_id',0))
                    for e in core.events_after(ch,subs[ch]):await ws.send_json(e);subs[ch]=e['event_id']
                    await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':subs[ch]})
    finally:
        task.cancel(); core.drop_presence(conn_id); ACTIVE_USERS.discard(u['id'])
    return ws
async def root(req):
    return web.FileResponse('/app/static/index.html')
def app():
    a=web.Application()
    a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces',workspaces);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/dms',dms);a.router.add_route('*','/api/workspaces/{slug}/groups',groups);a.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',groups);a.router.add_get('/api/users/{id}',profile);a.router.add_patch('/api/users/me',profile);a.router.add_post('/api/files',files);a.router.add_get('/api/files/{id}',files);a.router.add_get('/api/files/{id}/download',file_download);a.router.add_get('/api/channels/{id}',channel_get);a.router.add_patch('/api/channels/{id}',channel_patch);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_post('/api/channels/{id}/join',join);a.router.add_post('/api/channels/{id}/archive',archive_route);a.router.add_post('/api/channels/{id}/unarchive',archive_route);a.router.add_route('*','/api/channels/{id}/messages',messages);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_patch('/api/messages/{id}',edit);a.router.add_delete('/api/messages/{id}',delete)
    a.router.add_route('*','/api/messages/{id}/reactions',reaction);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction);a.router.add_get('/api/ws',ws_handler);a.router.add_get('/',root)
    return a
if __name__=='__main__':
    core.init_db();p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node
    web.run_app(app(),host='127.0.0.1',port=8000+NODE)
