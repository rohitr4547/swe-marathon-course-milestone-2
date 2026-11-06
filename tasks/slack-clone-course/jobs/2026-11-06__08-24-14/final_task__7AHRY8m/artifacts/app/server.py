import asyncio, argparse, json, re, os, time, uuid
from aiohttp import web, WSMsgType
import core

NODE=0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(request):
    try:
        x=await request.json()
        return x if isinstance(x,dict) else None
    except: return None
def require_user(request):
    h=request.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
def out(key,val,status=200,**extra): return web.json_response({key:val,**extra},status=status)
def valid_body(x): return isinstance(x,dict)
def channel_member_users(cid):
    c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(cid,)).fetchall(); c.close(); return [core.user_to_json(x) for x in rows]
def workspace_channels(wid,include=False):
    c=core.connect(); q='SELECT * FROM channels WHERE workspace_id=?'+('' if include else ' AND is_archived=0'); rows=c.execute(q,(wid,)).fetchall(); c.close(); return rows
def workspace_access(uid,wid):
    return core.member_role(wid,uid) is not None
def admin(uid,wid):
    return core.member_role(wid,uid) in ('owner','admin')

async def health(request): return web.json_response({'status':'ok','node_id':NODE})
async def register(request):
    x=await read_json(request)
    if not x or not isinstance(x.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',x['username']) or not isinstance(x.get('password'),str) or len(x['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(x['username'],)).fetchone(): return json_error(409,'username exists')
        cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(x['username'],core.hash_password(x['password']),x.get('display_name') or x['username'],core.now_iso()))
        u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); tok=core.issue_token(c,u['id'])
    return out('user',core.user_to_json(u),201,token=tok)
async def login(request):
    x=await read_json(request)
    if not x:return json_error(400,'invalid body')
    c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(x.get('username'),)).fetchone(); c.close()
    if not u or not isinstance(x.get('password'),str) or not core.verify_password(x['password'],u['password']):return json_error(401,'invalid credentials')
    with core.tx() as c: tok=core.issue_token(c,u['id'])
    return out('user',core.user_to_json(u),token=tok)
async def me(request):
    u=require_user(request)
    return out('user',core.user_to_json(u)) if u else json_error(401,'unauthorized')
async def list_workspaces(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return out('workspaces',[core.workspace_to_json(r) for r in rows])
async def create_workspace(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    x=await read_json(request)
    if not x or not isinstance(x.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',x['slug']) or not isinstance(x.get('name'),str) or not x['name']:return json_error(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(x['slug'],)).fetchone():return json_error(409,'slug exists')
        cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(x['slug'],x['name'],u['id']))
        wid=cur.lastrowid; c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'))
        ch=core.create_channel_row(c,wid,'general'); core.add_channel_member(c,ch['id'],u['id'])
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()
    return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},status=201)
async def workspace(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not workspace_access(u['id'],w['id']):return json_error(403,'forbidden')
    cs=workspace_channels(w['id'],request.query.get('include_archived') in ('1','true'))
    c=core.connect(); rs=c.execute('SELECT channel_id,last_read_event_id FROM read_state WHERE user_id=?',(u['id'],)).fetchall(); c.close()
    return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in cs],'read_state':[dict(x) for x in rs]})
async def members(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not workspace_access(u['id'],w['id']):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT m.user_id,m.role,u.username,u.display_name FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall(); c.close()
    return out('members',[dict(r) for r in rows])
async def create_channel(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return json_error(403,'forbidden')
    x=await read_json(request)
    if not x or not isinstance(x.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',x['name']) or (x.get('topic') is not None and (not isinstance(x['topic'],str) or len(x['topic'])>250)):return json_error(400,'invalid channel')
    private=bool(x.get('is_private',False))
    if private and role not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],x['name'])).fetchone():return json_error(409,'channel exists')
        ch=core.create_channel_row(c,w['id'],x['name'],private,x.get('topic','')); core.add_channel_member(c,ch['id'],u['id'])
    return out('channel',core.channel_to_json(ch),201)
async def get_channel(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    return out('channel',core.channel_to_json(ch))
async def join(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if ch['workspace_id']:
        role=core.member_role(ch['workspace_id'],u['id'])
        w=core.get_workspace_by_id(ch['workspace_id']) if hasattr(core,'get_workspace_by_id') else None
        if not role:
            with core.tx() as c: core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
        with core.tx() as c: core.add_channel_member(c,ch['id'],u['id'])
    else:
        with core.tx() as c: core.add_channel_member(c,ch['id'],u['id'])
    return out('channel',core.channel_to_json(core.get_channel(ch['id'])))
async def leave(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    with core.tx() as c: core.remove_channel_member(c,ch['id'],u['id'])
    return out('channel',core.channel_to_json(ch))
async def update_channel(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    x=await read_json(request)
    if not x or not isinstance(x.get('topic'),str) or len(x['topic'])>250:return json_error(400,'invalid topic')
    with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(x['topic'],ch['id']))
    return out('channel',core.channel_to_json(core.get_channel(ch['id'])))
async def archive(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if request.path.endswith('archive') else 0,ch['id']))
    return out('channel',core.channel_to_json(core.get_channel(ch['id'])))
async def channel_members(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    return out('members',channel_member_users(ch['id']))
async def post_message(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    x=await read_json(request)
    if not x or not isinstance(x.get('body'),str) or not x['body'].strip():return json_error(400,'empty body')
    parent=x.get('parent_id')
    try:
        action,value=core.run_slash(u,ch,x['body'])
        if ch['is_archived'] and action=='message': return json_error(423,'archived')
        if action=='message': r=core.create_message(u,ch['id'],value,parent,x.get('file_ids',[])); return out('message',core.message_to_json(r),201)
        if action=='topic': return web.json_response({'message':None,'channel':core.channel_to_json(value)},status=201)
        if action=='invite': return web.json_response({'message':None,'invited_user':core.user_to_json(value)},status=201)
        return web.json_response({'message':None,'channel':core.channel_to_json(value)},status=201)
    except PermissionError:return json_error(403,'forbidden')
    except ValueError as e:return json_error(400,'invalid command' if str(e)=='command' else 'archived')
async def messages(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    lim=min(int(request.query.get('limit','50')),200); cur=request.query.get('cursor'); cv=core.decode_cursor('messages',cur) if cur else None
    rows=core.list_messages(ch['id'],lim,cv); more=len(rows)>lim; rows=rows[:lim]
    return web.json_response({'messages':[core.message_to_json(r) for r in rows],'next_cursor':core.encode_cursor('messages',rows[-1]['id']) if more else None})
async def replies(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    pid=int(request.match_info['id']); c=core.connect(); p=c.execute('SELECT * FROM messages WHERE id=?',(pid,)).fetchone(); c.close()
    if not p:return json_error(404,'not found')
    ch=core.get_channel(p['channel_id'])
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    rows=core.list_replies(pid,50); return web.json_response({'replies':[core.message_to_json(r) for r in rows],'next_cursor':None})
async def patch_message(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    x=await read_json(request)
    if not x or not isinstance(x.get('body'),str) or not x['body'].strip():return json_error(400,'invalid body')
    try:r=core.edit_message(u,int(request.match_info['id']),x['body'])
    except LookupError:return json_error(404,'not found')
    except PermissionError:return json_error(403,'forbidden')
    except ValueError:return json_error(409,'deleted')
    return out('message',core.message_to_json(r))
async def del_message(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    try:ok=core.delete_message(u,int(request.match_info['id']))
    except LookupError:return json_error(404,'not found')
    except PermissionError:return json_error(403,'forbidden')
    return web.json_response({'deleted':True})
async def reactions(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    x=await read_json(request); emoji=(request.match_info.get('emoji') if request.method=='DELETE' else (x.get('emoji') if x else None))
    if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
    try:r=core.react(u,int(request.match_info['id']),emoji,request.method=='POST')
    except LookupError:return json_error(404,'not found')
    except PermissionError:return json_error(403,'forbidden')
    except ValueError:return json_error(423,'archived')
    return out('reactions',r)
async def dms(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    x=await read_json(request)
    if not x or not isinstance(x.get('user_id'),int):return json_error(400,'invalid user_id')
    try:cid=core.open_dm(u,x['user_id'])
    except LookupError:return json_error(404,'not found')
    return out('channel_id',cid)
async def upload_file(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    try:
        form=await request.multipart(); part=await form.next()
        if not part or part.name!='file':return json_error(400,'missing file')
        data=await part.read()
        r=core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)
    except OverflowError:return json_error(413,'file too large')
    except:return json_error(400,'invalid file')
    return out('file',core.file_to_json(r),201)
async def get_file(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); r=c.execute('SELECT * FROM files WHERE id=?',(int(request.match_info['id']),)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    return out('file',core.file_to_json(r))
async def download_file(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); r=c.execute('SELECT * FROM files WHERE id=?',(int(request.match_info['id']),)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    return web.FileResponse(f"/app/data/files/{r['id']}",headers={'Content-Type':r['content_type'],'Content-Disposition':f'attachment; filename="{r["filename"]}"'})
async def groups(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    if request.method=='GET': return out('groups',[core.group_to_json(x) for x in rows])
    x=await read_json(request)
    if not x or not isinstance(x.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]+',x['handle']) or not isinstance(x.get('name'),str) or not isinstance(x.get('user_ids'),list):return json_error(400,'invalid group')
    try:r=core.create_group(u['id'],w['id'],x['handle'],x['name'],x['user_ids'])
    except FileExistsError:return json_error(409,'exists')
    except ValueError:return json_error(400,'invalid members')
    return out('group',core.group_to_json(r),201)
async def group_item(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect(); g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],request.match_info['handle'])).fetchone(); c.close()
    if not g:
        if request.method=='DELETE' and core.member_role(w['id'],u['id']): return web.json_response({'deleted':True})
        return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner',) and g['creator_id']!=u['id']:return json_error(403,'forbidden')
    if request.method=='DELETE':
        core.delete_group(w['id'],g['handle']); return web.json_response({'deleted':True})
    x=await read_json(request) or {}
    try:r=core.update_group(w['id'],g['handle'],x)
    except ValueError:return json_error(400,'invalid members')
    return out('group',core.group_to_json(r))

async def get_user(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); r=c.execute('SELECT * FROM users WHERE id=?',(int(request.match_info['id']),)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    return out('user',core.user_to_json(r))
async def patch_profile(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    x=await read_json(request)
    if x is None:return json_error(400,'invalid body')
    try:r=core.update_profile(u,x)
    except ValueError:return json_error(400,'invalid profile')
    return out('user',core.user_to_json(r))

async def missing_component_route(request):
    u=require_user(request)
    if not u:return json_error(401,'unauthorized')
    return json_error(404,'not found')
async def ws_handler(request):
    token=request.query.get('token','')
    u=core.user_for_token(token)
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse(); await ws.prepare(request)
    conn_id=uuid.uuid4().hex; core.touch_presence(conn_id,u['id'])
    subs={}; global_last=0; alive=True
    async def deliver():
        nonlocal global_last
        while not ws.closed:
            for gid,ev in core.globals_after(global_last):
                await ws.send_json(ev); global_last=gid
            for cid,last in list(subs.items()):
                for ev in core.events_after(cid,last):
                    await ws.send_json(ev); subs[cid]=ev['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(deliver())
    try:
        async for msg in ws:
            if msg.type!=WSMsgType.TEXT: continue
            try:x=json.loads(msg.data)
            except:continue
            cid=x.get('channel_id')
            if x.get('type')=='subscribe' and isinstance(cid,int):
                subs[cid]=core.head_event_id(cid)
                await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
            elif x.get('type')=='resume' and isinstance(cid,int):
                since=x.get('since_event_id',0); subs[cid]=since
                for ev in core.events_after(cid,since):
                    await ws.send_json(ev); subs[cid]=ev['event_id']
                await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':subs[cid]})
    finally:
        task.cancel(); core.drop_presence(conn_id)
    return ws
async def root(request):
    try:return web.FileResponse('/app/static/index.html')
    except:return web.Response(text='Huddle',content_type='text/html')
def app():
    a=web.Application()
    a.router.add_get('/api/health',health)
    a.router.add_post('/api/auth/register',register); a.router.add_post('/api/auth/login',login); a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',list_workspaces); a.router.add_post('/api/workspaces',create_workspace)
    a.router.add_get('/api/workspaces/{slug}',workspace); a.router.add_get('/api/workspaces/{slug}/members',members)
    a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_get('/api/channels/{id}',get_channel); a.router.add_post('/api/channels/{id}/join',join); a.router.add_post('/api/channels/{id}/leave',leave)
    a.router.add_patch('/api/channels/{id}',update_channel); a.router.add_post('/api/channels/{id}/archive',archive); a.router.add_post('/api/channels/{id}/unarchive',archive); a.router.add_get('/api/channels/{id}/members',channel_members)
    a.router.add_post('/api/channels/{id}/messages',post_message); a.router.add_get('/api/channels/{id}/messages',messages)
    a.router.add_get('/api/messages/{id}/replies',replies); a.router.add_patch('/api/messages/{id}',patch_message); a.router.add_delete('/api/messages/{id}',del_message)
    a.router.add_post('/api/messages/{id}/reactions',reactions); a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reactions)
    a.router.add_get('/api/ws',ws_handler)
    a.router.add_post('/api/dms',dms)
    a.router.add_post('/api/files',upload_file)
    a.router.add_get('/api/files/{id}',get_file)
    a.router.add_get('/api/files/{id}/download',download_file)
    a.router.add_get('/api/users/{id}',get_user)
    a.router.add_patch('/api/users/me',patch_profile)
    a.router.add_get('/api/workspaces/{slug}/groups',groups)
    a.router.add_post('/api/workspaces/{slug}/groups',groups)
    a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',group_item)
    a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',group_item)
    a.router.add_get('/',root)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--node',type=int,default=0); NODE=p.parse_args().node
    web.run_app(app(),host='127.0.0.1',port=8000+NODE)
