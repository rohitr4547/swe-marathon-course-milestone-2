import aiohttp,asyncio,json,os,sys,uuid
from aiohttp import web,WSMsgType
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
def out(data,status=200):return web.json_response(data,status=status)
def err(status,msg):return out({'error':msg},status)
async def read_json(r):
    try:return await r.json()
    except:return None
def auth(r):
    h=r.headers.get('Authorization','');return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
def need(r):
    u=auth(r)
    if not u:raise web.HTTPUnauthorized()
    return u
def wrap(fn):
    async def x(r):
        try:return await fn(r)
        except web.HTTPException:raise
        except LookupError:return err(404,'not found')
        except PermissionError as e:return err(423 if str(e)=='archived' else 403,'forbidden')
        except FileExistsError:return err(409,'conflict')
        except ValueError:return err(400,'invalid')
        except Exception as e:return err(500,str(e))
    return x
@wrap
async def health(r):return out({'status':'ok','node_id':NODE})
@wrap
async def register(r):
    d=await read_json(r)
    if not isinstance(d,dict) or not isinstance(d.get('username'),str) or not isinstance(d.get('password'),str) or not __import__('re').fullmatch(r'[A-Za-z0-9_]+',d['username']) or len(d['password'])<8:return err(400,'invalid')
    try:
        with core.tx() as c:
            cur=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
            u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone();t=core.issue_token(c,u['id'])
        return out({'user':core.user_to_json(u),'token':t},201)
    except: return err(409,'duplicate')
@wrap
async def login(r):
    d=await read_json(r);c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if isinstance(d,dict) else '',)).fetchone();c.close()
    if not u or not core.verify_password(d.get('password',''),u['password']):return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return out({'user':core.user_to_json(u),'token':t})
@wrap
async def me(r):return out({'user':core.user_to_json(need(r))})
@wrap
async def create_ws(r):
    u=need(r);d=await read_json(r)
    if not isinstance(d,dict) or not isinstance(d.get('slug'),str) or not __import__('re').fullmatch(r'[a-z0-9-]{2,32}',d['slug']):return err(400,'invalid')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(d['slug'],)).fetchone():return err(409,'duplicate')
        cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d.get('name',''),u['id']));wid=cur.lastrowid
        c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(wid,u['id'],'owner'));g=core.create_channel_row(c,wid,'general');core.add_channel_member(c,g['id'],u['id'])
        return out({'workspace':core.workspace_to_json(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':core.channel_to_json(g)},201)
@wrap
async def workspaces(r):
    u=need(r);c=core.connect();rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close();return out({'workspaces':[core.workspace_to_json(x) for x in rows]})
@wrap
async def ws_detail(r):
    u=need(r);w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect();chs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall();c.close();return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in chs]})
@wrap
async def members(r):
    u=need(r);w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect();rows=c.execute('SELECT user_id,role FROM workspace_members WHERE workspace_id=?',(w['id'],)).fetchall();c.close();return out({'members':[dict(x) for x in rows]})
@wrap
async def create_channel(r):
    u=need(r);w=core.get_workspace(r.match_info['slug']);d=await read_json(r)
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return err(403,'forbidden')
    if role=='guest' or (d.get('is_private',False) and role not in ('owner','admin')):return err(403,'forbidden')
    if not isinstance(d.get('name'),str) or not __import__('re').fullmatch(r'[a-z0-9-]{1,32}',d['name']):return err(400,'invalid')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return err(409,'duplicate')
        ch=core.create_channel_row(c,w['id'],d['name'],bool(d.get('is_private')),d.get('topic',''));core.add_channel_member(c,ch['id'],u['id'])
    return out({'channel':core.channel_to_json(ch)},201)
@wrap
async def join(r):
    u=need(r);ch=core.get_channel(r.match_info['id'])
    if not ch:return err(404,'not found')
    if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):
        w=core.get_workspace((core.connect().execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() or {'slug':''})['slug'])
        if w['join_mode']!='open':return err(403,'forbidden')
        with core.tx() as c:core.add_workspace_member(c,w['id'],u['id'])
    with core.tx() as c:core.add_channel_member(c,ch['id'],u['id'])
    return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
@wrap
async def leave(r):
    u=need(r);ch=core.get_channel(r.match_info['id'])
    if not ch:return err(404,'not found')
    with core.tx() as c:core.remove_channel_member(c,ch['id'],u['id'])
    return out({'channel':core.channel_to_json(ch)})
@wrap
async def channel_members(r):
    u=need(r);ch=core.get_channel(r.match_info['id'])
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    c=core.connect();rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close();return out({'members':[core.user_to_json(x) for x in rows]})
@wrap
async def patch_channel(r):
    u=need(r);ch=core.get_channel(r.match_info['id']);d=await read_json(r)
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),ch['id']))
    return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
@wrap
async def archive(r):
    u=need(r);ch=core.get_channel(r.match_info['id'])
    if not ch:return err(404,'not found')
    if core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(0 if r.path.endswith('unarchive') else 1,ch['id']))
    return out({'channel':core.channel_to_json(core.get_channel(ch['id']))})
@wrap
async def post_message(r):
    u=need(r);cid=int(r.match_info['id']);ch=core.get_channel(cid);d=await read_json(r)
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if not isinstance(d,dict) or not isinstance(d.get('body'),str):return err(400,'invalid')
    try:
        if d['body'] in ('/archive','/unarchive') and core.member_role(ch['workspace_id'],u['id'])!='owner': return err(403,'forbidden')
        s=core.run_slash(u,ch,d['body'])
        if s.get('side')=='channel':return out({'message':None,'channel':core.channel_to_json(s['channel'])},201)
        if s.get('side')=='invite':return out({'message':None,'invited_user':core.user_to_json(s['invited_user'])},201)
        msg=core.create_message(u,cid,s['body'],d.get('parent_id'),d.get('file_ids'))
        return out({'message':msg},201)
    except PermissionError as e:return err(423 if str(e)=='archived' else 403,'forbidden')
@wrap
async def get_messages(r):
    u=need(r);ch=core.get_channel(r.match_info['id'])
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    try:l,c=core.parse_paging(r.query,'messages')
    except:return err(400,'cursor')
    rows,n=core.list_messages(ch['id'],l,c);return out({'messages':[core.message_to_json(x) for x in rows],'next_cursor':n})
@wrap
async def replies(r):
    u=need(r)
    try:l,c=core.parse_paging(r.query,'replies')
    except:return err(400,'cursor')
    rows,n=core.list_replies(int(r.match_info['id']),l,c);return out({'replies':[core.message_to_json(x) for x in rows],'next_cursor':n})
@wrap
async def edit(r):
    u=need(r);d=await read_json(r)
    try:return out({'message':core.edit_message(u,int(r.match_info['id']),d.get('body'))})
    except RuntimeError:return err(409,'deleted')
@wrap
async def delete(r):
    u=need(r);core.delete_message(u,int(r.match_info['id']));return out({'deleted':True})
@wrap
async def reaction(r):
    u=need(r);d=await read_json(r)
    if not isinstance(d,dict) or not isinstance(d.get('emoji'),str) or not d['emoji']:return err(400,'invalid')
    return out({'reactions':core.react(u,int(r.match_info['id']),d['emoji'],True)})
@wrap
async def unreaction(r):
    u=need(r);return out({'reactions':core.react(u,int(r.match_info['id']),r.match_info['emoji'],False)})
@wrap
async def dm(r):
    u=need(r);d=await read_json(r)
    if not isinstance(d,dict) or not isinstance(d.get('user_id'),int):return err(400,'invalid')
    return out({'channel_id':core.open_dm(u,d['user_id'])})
@wrap
async def upload(r):
    u=need(r);rd=await r.multipart();part=await rd.next()
    if not part:return err(400,'file')
    data=await part.read()
    if len(data)>10485760:return err(413,'too large')
    f=core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)
    return out({'file':core.file_to_json(f)},201)
@wrap
async def file_meta(r):
    need(r);c=core.connect();f=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
    if not f:return err(404,'not found')
    return out({'file':core.file_to_json(f)})
@wrap
async def download(r):
    need(r);c=core.connect();f=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
    if not f:return err(404,'not found')
    return web.Response(body=open(f'/app/data/files/{f["id"]}','rb').read(),content_type=f['content_type'],headers={'Content-Disposition':f'attachment; filename="{f["filename"]}"'})
@wrap
async def groups(r):
    u=need(r);w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect();rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
    if r.method=='GET':return out({'groups':[core.group_to_json(x) for x in rows]})
    d=await read_json(r)
    if not isinstance(d,dict) or not isinstance(d.get('handle'),str) or not isinstance(d.get('name'),str) or not isinstance(d.get('user_ids'),list):return err(400,'invalid')
    try:g=core.create_group(u,w['id'],d['handle'],d['name'],d['user_ids']);return out({'group':core.group_to_json(g)},201)
    except FileExistsError:return err(409,'duplicate')
@wrap
async def group_item(r):
    u=need(r);w=core.get_workspace(r.match_info['slug']);h=r.match_info['handle']
    if not w:return err(404,'not found')
    try:
        if r.method=='DELETE':core.delete_group(u,w['id'],h);return out({'deleted':True})
        d=await read_json(r);g=core.update_group(u,w['id'],h,d.get('name'),d.get('user_ids'));return out({'group':core.group_to_json(g)})
    except LookupError:return err(404,'not found')
@wrap
async def profile(r):
    u=need(r)
    if r.match_info.get('id'):
        c=core.connect();x=c.execute('SELECT * FROM users WHERE id=?',(int(r.match_info['id']),)).fetchone();c.close()
        if not x:return err(404,'not found')
    else:
        d=await read_json(r)
        try:x=core.update_profile(u,d or {})
        except ValueError:return err(400,'invalid')
    return out({'user':core.user_to_json(x)})
async def ws_handler(r):
    u=core.user_for_token(r.query.get('token')) or auth(r)
    if not u:return err(401,'unauthorized')
    ws=web.WebSocketResponse();await ws.prepare(r);conn=str(uuid.uuid4());core.touch_presence(conn,u['id']);subs={};lastg=0
    async def pump():
        nonlocal lastg
        while not ws.closed:
            for cid,since in list(subs.items()):
                for frame in core.events_after(cid,since):
                    if frame['event_id']>subs[cid]:
                        await ws.send_json(frame);subs[cid]=frame['event_id']
            for gid,frame in core.globals_after(lastg):await ws.send_json(frame);lastg=gid
            core.touch_presence(conn,u['id']);await asyncio.sleep(.05)
    task=asyncio.create_task(pump())
    try:
        async for m in ws:
            if m.type==WSMsgType.TEXT:
                d=json.loads(m.data);cid=int(d.get('channel_id',0))
                if d.get('type')=='subscribe':
                    subs[cid]=core.head_event_id(cid)
                    await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subs[cid]})
                elif d.get('type')=='resume':
                    subs[cid]=int(d.get('since_event_id',0))
                    for frame in core.events_after(cid,subs[cid]):
                        if frame['event_id']>subs[cid]:
                            await ws.send_json(frame);subs[cid]=frame['event_id']
                    await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':subs[cid]})
    finally:task.cancel();core.drop_presence(conn)
    return ws
routes=[
('/api/health','GET',health),('/api/auth/register','POST',register),('/api/auth/login','POST',login),('/api/auth/me','GET',me),
('/api/workspaces','POST',create_ws),('/api/workspaces','GET',workspaces),('/api/workspaces/{slug}','GET',ws_detail),('/api/workspaces/{slug}/members','GET',members),
('/api/workspaces/{slug}/channels','POST',create_channel),('/api/channels/{id}/join','POST',join),('/api/channels/{id}/leave','POST',leave),('/api/channels/{id}/members','GET',channel_members),('/api/channels/{id}','PATCH',patch_channel),
('/api/channels/{id}/archive','POST',archive),('/api/channels/{id}/unarchive','POST',archive),('/api/channels/{id}/messages','POST',post_message),('/api/channels/{id}/messages','GET',get_messages),('/api/messages/{id}/replies','GET',replies),('/api/messages/{id}','PATCH',edit),('/api/messages/{id}','DELETE',delete),
('/api/messages/{id}/reactions','POST',reaction),('/api/messages/{id}/reactions/{emoji}','DELETE',unreaction),('/api/dms','POST',dm),('/api/files','POST',upload),('/api/files/{id}','GET',file_meta),('/api/files/{id}/download','GET',download),
('/api/users/me','PATCH',profile),('/api/users/{id}','GET',profile)]
app=web.Application()
for p,m,f in routes:app.router.add_route(m,p,f)
app.router.add_route('*','/api/workspaces/{slug}/groups',groups);app.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',group_item)
app.router.add_get('/api/ws',ws_handler)
app.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'))
if __name__=='__main__':web.run_app(app,host='127.0.0.1',port=8000+NODE)
