import argparse, asyncio, json, re, secrets
from aiohttp import web, WSMsgType
import core
NODE=0
def out(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return out({'error':msg},status)
async def body(req):
    try: x=await req.json(); return x if isinstance(x,dict) else None
    except: return None
def auth(req):
    h=req.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
async def need(req):
    u=auth(req)
    if not u: raise web.HTTPUnauthorized(text=json.dumps({'error':'unauthorized'}),content_type='application/json')
    return u
def user_obj(r): return core.user_to_json(r)
async def health(req): return out({'status':'ok','node_id':NODE})
async def register(req):
    b=await body(req)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
    try:
        with core.tx() as c:
            uid=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso())).lastrowid
            t=core.issue_token(c,uid); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        return out({'user':user_obj(u),'token':t},201)
    except Exception:return err(409,'username already exists')
async def login(req):
    b=await body(req); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if b else '',)).fetchone(); c.close()
    if not u or not core.verify_password(b.get('password',''),u['password']): return err(401,'invalid credentials')
    with core.tx() as c: t=core.issue_token(c,u['id'])
    return out({'user':user_obj(u),'token':t})
async def me(req): return out({'user':user_obj(await need(req))})
async def workspaces(req):
    u=await need(req); c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w LEFT JOIN members m ON m.workspace_id=w.id AND m.user_id=? WHERE w.owner_id=? OR m.user_id=?',(u['id'],u['id'],u['id'])).fetchall(); c.close(); return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(req):
    u=await need(req); b=await body(req)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str):return err(400,'invalid workspace')
    try:
      with core.tx() as c:
        wid=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])).lastrowid
        ch=core.create_channel_row(c,wid,'general'); core.add_workspace_member(c,wid,u['id'],'owner'); core.add_channel_member(c,ch,u['id'])
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); row=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
      return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(row)},201)
    except: return err(409,'duplicate workspace')
async def members(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return out({'members':[dict(x) for x in rows]})
async def set_role(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    caller=core.member_role(w['id'],u['id']); target=int(req.match_info['user_id']); b=await body(req)
    if caller not in ('owner','admin'):return err(403,'forbidden')
    if not b or b.get('role') not in ('admin','member','guest'):return err(400,'invalid role')
    old=core.member_role(w['id'],target)
    if not old:return err(404,'member')
    if target==u['id'] or old in ('owner','admin') and caller!='owner':return err(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(b['role'],w['id'],target))
    return out({'member':{'user_id':target,'role':b['role']}})

async def patch_workspace(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    b=await body(req)
    if not b:return err(400,'invalid body')
    if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return err(400,'invalid join_mode')
    with core.tx() as c:
        c.execute('UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?',(b.get('name'),b.get('join_mode'),w['id']))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return out({'workspace':core.workspace_to_json(w)})
async def channel_members(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return out({'members':[core.user_to_json(x) for x in rows]})
async def leave(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    with core.tx() as c:c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
    return out({'channel':core.channel_to_json(ch)})
async def patch_channel(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    b=await body(req)
    if not b or not isinstance(b.get('topic',''),str) or len(b.get('topic',''))>250:return err(400,'invalid topic')
    with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(b.get('topic',''),ch['id'])); ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return out({'channel':core.channel_to_json(ch)})
async def workspace(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':{}})
async def create_channel(req):
    u=await need(req); w=core.get_workspace(req.match_info['slug']); b=await body(req)
    if not w:return err(404,'not found')
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return err(400,'invalid channel')
    c0=core.connect(); exists=c0.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone(); c0.close()
    if exists:return err(409,'duplicate channel')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin','member'):return err(403,'forbidden')
    if bool(b.get('is_private')) and role not in ('owner','admin'): return err(403,'private channel')
    if isinstance(b.get('topic',''),str) and len(b.get('topic',''))>250:return err(400,'topic too long')
    try:
      with core.tx() as c:
        i=core.create_channel_row(c,w['id'],b['name'],bool(b.get('is_private')),b.get('topic','')); core.add_channel_member(c,i,u['id']); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone()
      return out({'channel':core.channel_to_json(r)},201)
    except:return err(409,'duplicate channel')
async def join(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    w=core.get_workspace(next((x['slug'] for x in [core.get_workspace('') ] if x),'')) if False else None
    if ch['workspace_id']:
        c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone(); c.close()
        if ch['is_private'] and not role:return err(403,'private channel')
        if role and role['role']=='guest':return err(403,'guest')
        with core.tx() as c:
            if not role: core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
            core.add_channel_member(c,ch['id'],u['id'])
    return out({'channel':core.channel_to_json(ch)})
async def messages(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    c=core.connect()
    if ch['is_dm'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone():
        c.close(); return err(403,'forbidden')
    rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC LIMIT 200',(ch['id'],)).fetchall(); c.close()
    return out({'messages':[core.message_to_json(x) for x in rows],'next_cursor':None})
async def get_user(req):
    await need(req); c=core.connect(); r=c.execute('SELECT * FROM users WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    return out({'user':core.user_to_json(r)}) if r else err(404,'user')
async def update_me(req):
    u=await need(req); b=await body(req)
    if b is None:return err(400,'invalid body')
    r=core.update_profile(u,b)
    return out({'user':core.user_to_json(r)}) if r else err(400,'invalid profile')
async def dms(req):
    u=await need(req); b=await body(req)
    if not b or not isinstance(b.get('user_id'),int):return err(400,'invalid user_id')
    if not core.user_for_token(next(iter([]),'')) and not core.connect().execute('SELECT id FROM users WHERE id=?',(b['user_id'],)).fetchone():return err(404,'user')
    return out({'channel_id':core.open_dm(u,b['user_id'])})
async def upload(req):
    u=await need(req)
    try:
        reader=await req.multipart(); part=await reader.next()
        if not part or part.name!='file':return err(400,'file required')
        data=await part.read(decode=False)
    except:return err(400,'invalid upload')
    if len(data)>10485760:return err(413,'file too large')
    r=core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)
    return out({'file':core.file_to_json(r)},201)
async def file_meta(req):
    await need(req); c=core.connect(); r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    return out({'file':core.file_to_json(r)}) if r else err(404,'file')
async def file_download(req):
    await need(req); c=core.connect(); r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    if not r:return err(404,'file')
    return web.Response(body=open('/app/data/files/'+str(r['id']),'rb').read(),content_type=r['content_type'],headers={'Content-Disposition':'attachment; filename="'+r['filename']+'"'})
async def post(req):
    u=await need(req); ch=core.get_channel(int(req.match_info['id'])); b=await body(req)
    if not ch:return err(404,'not found')
    c=core.connect(); allowed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.close()
    if not allowed:return err(403,'forbidden')
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty message')
    raw=b['body']
    try:
        command=core.run_slash(u,ch,raw) if raw.startswith('/') else {'body':raw}
    except ValueError:return err(400,'invalid command')
    if command.get('forbidden'):return err(403,'forbidden')
    if command.get('side'):return out(command['side'],201)
    if ch['is_archived']: return err(423,'archived')
    try:r=core.create_message(u,ch['id'],command['body'],b.get('parent_id'),b.get('file_ids',[]))
    except ValueError:return err(400,'invalid file')
    return out({'message':core.message_to_json(r)},201)
async def edit(req):
    u=await need(req); mid=int(req.match_info['id']); b=await body(req)
    if b is None or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'invalid body')
    try:r=core.edit_message(u,mid,b['body'])
    except RuntimeError:return err(409,'deleted')
    except PermissionError:return err(403,'forbidden')
    if r is None:return err(404,'not found')
    return out({'message':core.message_to_json(r)})
async def delete(req):
    u=await need(req); mid=int(req.match_info['id'])
    try:r=core.delete_message(u,mid)
    except PermissionError:return err(403,'forbidden')
    if r is None:return err(404,'not found')
    return out({'deleted':True})

async def replies(req):
    u=await need(req); parent=int(req.match_info['id'])
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(parent,)).fetchone()
    if not m:c.close(); return err(404,'not found')
    allowed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone(); rows=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(parent,)).fetchall(); c.close()
    if not allowed:return err(403,'forbidden')
    return out({'replies':[core.message_to_json(x) for x in rows],'next_cursor':None})

async def reaction(req):
    u=await need(req); mid=int(req.match_info['id']); b=await body(req)
    if not b or not isinstance(b.get('emoji'),str) or not b['emoji']:return err(400,'invalid emoji')
    r=core.react(u,mid,b['emoji'],True)
    if r is False:return err(404,'not found')
    if r=='FORBIDDEN':return err(403,'forbidden')
    return out({'reactions':r})
async def unreaction(req):
    u=await need(req); mid=int(req.match_info['id']); emoji=req.match_info['emoji']
    r=core.react(u,mid,emoji,False)
    if r is False:return err(404,'not found')
    if r=='FORBIDDEN':return err(403,'forbidden')
    return out({'reactions':r})

async def groups(req):
 u=await need(req); w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 c=core.connect(); rs=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
 return out({'groups':[core.group_to_json(x) for x in rs]})
async def group_create(req):
 u=await need(req); w=core.get_workspace(req.match_info['slug']); b=await body(req)
 if not w:return err(404,'not found')
 if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
 if not b or not isinstance(b.get('handle'),str) or not isinstance(b.get('user_ids'),list):return err(400,'invalid group')
 try:r=core.create_group(w['id'],u['id'],b['handle'],b.get('name',''),b['user_ids'])
 except Exception as e: return err(409 if 'UNIQUE' in str(e) else 400,'invalid group')
 return out({'group':core.group_to_json(r)},201)

async def group_modify(req):
 u=await need(req); w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect(); g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone(); c.close()
 if not g:return err(404,'not found')
 if g['creator_id']!=u['id'] and core.member_role(w['id'],u['id'])!='owner':return err(403,'forbidden')
 b=await body(req)
 try:r=core.update_group(w['id'],g['handle'],b.get('name') if b else None,b.get('user_ids') if b and 'user_ids' in b else None)
 except:return err(400,'invalid group')
 return out({'group':core.group_to_json(r)})
async def group_remove(req):
 u=await need(req); w=core.get_workspace(req.match_info['slug'])
 if not w:return err(404,'not found')
 c=core.connect(); g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone(); c.close()
 if not g:return out({'deleted':True})
 if g['creator_id']!=u['id'] and core.member_role(w['id'],u['id'])!='owner':return err(403,'forbidden')
 core.delete_group(w['id'],req.match_info['handle']); return out({'deleted':True})
async def c0stub(req):
    await need(req)
    return err(404,'not found')
async def ws(req):
    qt=req.query.get('token',''); u=core.user_for_token(qt[7:] if qt.startswith('Bearer ') else qt)
    if not u:return err(401,'unauthorized')
    socket=web.WebSocketResponse(); await socket.prepare(req); watched={}; global_last=max([x['id'] for x in core.globals_after(0)] or [0]); stopped=False
    conn_id=secrets.token_urlsafe(12); core.touch_presence(conn_id,u['id'])
    async def deliver():
        nonlocal global_last
        while not stopped:
            try:
                for row in core.globals_after(global_last):
                    await socket.send_str(row['payload']); global_last=row['id']
                for ch,last in list(watched.items()):
                    c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,last)).fetchall(); c.close()
                    for row in rows:
                        await socket.send_str(row['payload']); watched[ch]=row['event_id']
                await asyncio.sleep(.05)
            except Exception: break
    task=asyncio.create_task(deliver())
    try:
        async for m in socket:
            if m.type==WSMsgType.TEXT:
                try:x=json.loads(m.data)
                except:continue
                if x.get('type')=='subscribe':
                    ch=int(x['channel_id']); head=core.get_channel(ch); watched[ch]=head['last_event_id'] if head else 0
                    await socket.send_json({'type':'subscribed','channel_id':ch,'head_event_id':head['last_event_id'] if head else 0})
                elif x.get('type')=='resume':
                    ch=int(x['channel_id']); since=int(x.get('since_event_id',0))
                    c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall(); head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone(); c.close()
                    for row in rows:
                        await socket.send_str(row['payload'])
                    watched[ch]=rows[-1]['event_id'] if rows else since
                    await socket.send_json({'type':'resumed','channel_id':ch,'head_event_id':head['last_event_id'] if head else 0})
            elif m.type in (WSMsgType.CLOSE,WSMsgType.ERROR): break
    finally:
        stopped=True; task.cancel(); core.drop_presence(conn_id)
    return socket

app=web.Application(client_max_size=11*1024*1024)
app.router.add_get('/api/health',health); app.router.add_post('/api/auth/register',register); app.router.add_post('/api/auth/login',login); app.router.add_get('/api/auth/me',me)
app.router.add_patch('/api/workspaces/{slug}',patch_workspace); app.router.add_get('/api/workspaces/{slug}/members',members); app.router.add_patch('/api/workspaces/{slug}/members/{user_id}',set_role); app.router.add_get('/api/files/{id}',file_meta); app.router.add_get('/api/files/{id}/download',file_download); app.router.add_get('/api/workspaces/{slug}/groups',groups); app.router.add_post('/api/workspaces/{slug}/groups',group_create); app.router.add_patch('/api/workspaces/{slug}/groups/{handle}',group_modify); app.router.add_delete('/api/workspaces/{slug}/groups/{handle}',group_remove); app.router.add_post('/api/dms',dms); app.router.add_post('/api/files',upload); app.router.add_patch('/api/users/me',update_me); app.router.add_get('/api/users/{id}',get_user); app.router.add_get('/api/search',c0stub); app.router.add_get('/api/workspaces',workspaces); app.router.add_post('/api/workspaces',create_workspace); app.router.add_get('/api/workspaces/{slug}',workspace); app.router.add_post('/api/workspaces/{slug}/channels',create_channel)
app.router.add_post('/api/channels/{id}/join',join); app.router.add_get('/api/channels/{id}/members',channel_members); app.router.add_post('/api/channels/{id}/leave',leave); app.router.add_patch('/api/channels/{id}',patch_channel); app.router.add_get('/api/channels/{id}/messages',messages); app.router.add_post('/api/channels/{id}/messages',post)
app.router.add_patch('/api/messages/{id}',edit); app.router.add_get('/api/messages/{id}/replies',replies); app.router.add_post('/api/messages/{id}/reactions',reaction); app.router.add_delete('/api/messages/{id}/reactions/{emoji}',unreaction); app.router.add_delete('/api/messages/{id}',delete); app.router.add_get('/api/ws',ws)
app.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html')); app.router.add_get('/app.js',lambda r:web.FileResponse('/app/static/app.js'))
if __name__=='__main__':
 p=argparse.ArgumentParser(); p.add_argument('--node',type=int,default=0); NODE=p.parse_args().node
 core.init_db(); web.run_app(app,host='127.0.0.1',port=8000+NODE)
