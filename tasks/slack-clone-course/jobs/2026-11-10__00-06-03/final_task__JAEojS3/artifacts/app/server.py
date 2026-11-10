import asyncio, argparse, json, os, re, base64, urllib.parse, secrets
from aiohttp import web, WSMsgType
import core
def err(n,m): return web.json_response({'error':m},status=n)
async def body(r):
    try:
        x=await r.json()
        return x if isinstance(x,dict) else None
    except: return None
def tok(r):
    x=r.headers.get('Authorization','')
    if x.startswith('Bearer '): return x[7:]
    return r.query.get('token')
def user(r): return core.user_for_token(tok(r)) if tok(r) else None
def auth(r):
    u=user(r)
    return u
def js(x,status=200): return web.json_response(x,status=status)
def channel_access(uid,ch):
    return core.can_read_channel(uid,ch)
async def health(r): return js({'status':'ok','node_id':r.app['node']})
async def register(r):
    b=await body(r)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid')
    try:
        with core.tx() as c:
            c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
            u=c.execute('SELECT * FROM users WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone(); t=core.issue_token(c,u['id'])
        return js({'user':core.user_to_json(u),'token':t},201)
    except: return err(409,'duplicate')
async def login(r):
    b=await body(r); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if b else '',)).fetchone(); c.close()
    if not u or not core.verify_password(b.get('password',''),u['password']):return err(401,'invalid')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return js({'user':core.user_to_json(u),'token':t})
async def me(r):
    u=auth(r); return js({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspaces(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return js({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def make_ws(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    b=await body(r)
    if not b or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))):return err(400,'invalid')
    try:
        with core.tx() as c:
            c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b.get('name',''),u['id'])); w=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO memberships VALUES(?,?,?)',(w,u['id'],'owner')); ch=core.create_channel_row(c,w,'general')
            return js({'workspace':core.workspace_to_json(c.execute('SELECT * FROM workspaces WHERE id=?',(w,)).fetchone()),'general_channel':core.channel_to_json(ch)},201)
    except: return err(409,'duplicate')
async def ws_detail(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(r.match_info['slug'])
    if not w:return err(404,'not found')
    c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return js({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':{}})
async def make_channel(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(r.match_info['slug']); b=await body(r)
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) is None:return err(403,'forbidden')
    if not b or not re.fullmatch(r'[a-z0-9-]{1,32}',str(b.get('name',''))):return err(400,'invalid')
    try:
        with core.tx() as c:return js({'channel':core.channel_to_json(core.create_channel_row(c,w['id'],b['name'],b.get('is_private',False),b.get('topic','')))},201)
    except:return err(409,'duplicate')
async def join(r):
    u=auth(r); ch=core.get_channel(int(r.match_info['id']))
    if not u:return err(401,'unauthorized')
    if not ch:return err(404,'not found')
    with core.tx() as c:
        if ch['workspace_id']: core.add_workspace_member(c,ch['workspace_id'],u['id'])
        core.add_channel_member(c,ch['id'],u['id'])
    return js({'channel':core.channel_to_json(ch)})
async def channel_members(r):
    u=auth(r); ch=core.get_channel(int(r.match_info['id']))
    if not u:return err(401,'unauthorized')
    if not ch:return err(404,'not found')
    if not channel_access(u['id'],ch):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return js({'members':[core.user_to_json(x) for x in rows]})
async def post_msg(r):
    u=auth(r); ch=core.get_channel(int(r.match_info['id']))
    if not u:return err(401,'unauthorized')
    if not ch:return err(404,'not found')
    if not channel_access(u['id'],ch):return err(403,'forbidden')
    b=await body(r); text=b.get('body') if b else None
    if not isinstance(text,str) or not text.strip():return err(400,'empty')
    fids=b.get('file_ids',[]) if b else []
    if not isinstance(fids,list): return err(400,'invalid files')
    c=core.connect()
    for fid in fids:
        if not isinstance(fid,int) or not c.execute('SELECT 1 FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone(): c.close(); return err(400,'invalid file')
    c.close()
    if text.startswith('//'): text=text[1:]
    elif text == '/me' or text.startswith('/me   '): return err(400,'/me requires text')
    elif text.startswith('/me ') or text.startswith('/me\\t'): pass
    elif text == '/shrug' or text.startswith('/shrug '):
        rest=text[6:].strip(); text=(rest+' ' if rest else '')+'¯\\_(ツ)_/¯'
    elif text.startswith('/topic '):
        topic=text[7:].strip()
        if not topic or len(topic)>250:return err(400,'invalid topic')
        with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']))
        return js({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
    elif text.startswith('/invite @'):
        name=text[9:].strip(); c=core.connect(); target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone(); c.close()
        if not target:return err(400,'unknown user')
        with core.tx() as c: core.add_channel_member(c,ch['id'],target['id'])
        return js({'message':None,'invited_user':core.user_to_json(target)},201)
    elif text in ('/archive','/unarchive'):
        if ch['workspace_id'] is None or core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
        with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(text=='/archive'),ch['id']))
        return js({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
    elif text.startswith('/'): return err(400,'unknown command')
    try:
        m=core.create_message(u,ch['id'],text,b.get('parent_id') if b else None)
        if fids:
            with core.tx() as c:
                for fid in fids:c.execute('UPDATE files SET message_id=? WHERE id=?',(m['id'],fid))
            c=core.connect(); rows=c.execute('SELECT * FROM files WHERE message_id=?',(m['id'],)).fetchall(); c.close(); m['files']=[core.file_to_json(x) for x in rows]
        c=core.connect(); mids=set()
        for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)',text):
            if name=='channel':
                mids.update(x[0] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)))
            elif name=='here':
                mids.update(core.present_user_ids())
            else:
                x=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
                if x and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],x[0])).fetchone(): mids.add(x[0])
                g=c.execute('SELECT id FROM groups_ WHERE workspace_id=? AND handle=?',(ch['workspace_id'],name)).fetchone() if ch['workspace_id'] else None
                if g: mids.update(x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g[0],)) if c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],x[0])).fetchone())
        c.close(); mids.discard(u['id']); m['mentions']=sorted(mids)
        return js({'message':m},201)
    except ValueError:return err(423,'archived')
    except Exception as e:
        import traceback
        open('/tmp/post_trace.log','a').write(traceback.format_exc())
        return err(500,'internal')
async def messages(r):
    u=auth(r); ch=core.get_channel(int(r.match_info['id']))
    if not u:return err(401,'unauthorized')
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 ORDER BY id DESC LIMIT 200',(ch['id'],)).fetchall(); c.close()
    return js({'messages':[core.message_to_json(x) for x in rows],'next_cursor':None})
async def reaction(r):
    u=auth(r); mid=int(r.match_info['id'])
    if not u:return err(401,'unauthorized')
    if r.method=='POST':
        b=await body(r); emoji=b.get('emoji') if b else None
    else:
        emoji=urllib.parse.unquote(r.match_info.get('emoji',''))
    try:return js({'reactions':core.react(u,mid,emoji,r.method=='POST')})
    except LookupError:return err(404,'not found')
    except PermissionError:return err(403,'forbidden')
    except ValueError:return err(400,'invalid')
async def profile(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    if r.method=='GET':
        c=core.connect(); x=c.execute('SELECT * FROM users WHERE id=?',(int(r.match_info['id']),)).fetchone(); c.close()
        return js({'user':core.user_to_json(x)}) if x else err(404,'not found')
    try:return js({'user':core.user_to_json(core.update_profile(u,await body(r)))})
    except:return err(400,'invalid')
async def wsock(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    w=web.WebSocketResponse(); await w.prepare(r); subs={}; conn_id=secrets.token_urlsafe(12); core.touch_presence(conn_id,u['id'])
    gc=core.connect(); global_since=gc.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]; gc.close()
    async def pump():
        nonlocal global_since
        while not w.closed:
            await asyncio.sleep(.05)
            gc=core.connect(); grows=gc.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_since,)).fetchall(); gc.close()
            for grow in grows:
                try: await w.send_str(grow['payload']); global_since=grow['id']
                except: return
            for ch,since in list(subs.items()):
                c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall(); c.close()
                for row in rows:
                    try: await w.send_str(row['payload']); subs[ch]=row['event_id']
                    except: return
    task=asyncio.create_task(pump())
    try:
        async for m in w:
            if m.type!=WSMsgType.TEXT: continue
            try:x=json.loads(m.data)
            except:continue
            if x.get('type') in ('subscribe','resume'):
                ch=int(x.get('channel_id')); row=core.get_channel(ch)
                if not row or not core.can_read_channel(u['id'],row): continue
                since=int(x.get('since_event_id',0) or 0)
                if x.get('type')=='resume':
                    c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,since)).fetchall(); c.close()
                    for z in rows: await w.send_str(z['payload'])
                    subs[ch]=row['last_event_id']; await w.send_json({'type':'resumed','channel_id':ch,'head_event_id':row['last_event_id']})
                else:
                    subs[ch]=row['last_event_id']; await w.send_json({'type':'subscribed','channel_id':ch,'head_event_id':row['last_event_id']})
    finally:
        task.cancel(); core.drop_presence(conn_id); await w.close()
    return w

def app(node):
    a=web.Application(client_max_size=12*1024*1024); a['node']=node
    a.router.add_get('/api/health',health); a.router.add_post('/api/auth/register',register); a.router.add_post('/api/auth/login',login); a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces); a.router.add_post('/api/workspaces',make_ws); a.router.add_get('/api/workspaces/{slug}',ws_detail); a.router.add_post('/api/workspaces/{slug}/channels',make_channel)
    a.router.add_post('/api/channels/{id}/join',join); a.router.add_get('/api/channels/{id}/members',channel_members); a.router.add_post('/api/channels/{id}/messages',post_msg); a.router.add_get('/api/channels/{id}/messages',messages)
    a.router.add_post('/api/messages/{id}/reactions',reaction); a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction)
    a.router.add_route('*','/api/users/{id}',profile); a.router.add_get('/api/ws',wsock)
    a.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html') if os.path.exists('/app/static/index.html') else js({'status':'ok'}))
    return a
async def dms(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    b=await body(r)
    if not b or not isinstance(b.get('user_id'),int):return err(400,'invalid')
    c=core.connect(); other=c.execute('SELECT * FROM users WHERE id=?',(b['user_id'],)).fetchone()
    if not other:c.close(); return err(404,'not found')
    a,bid=sorted((u['id'],other['id'])); row=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bid)).fetchone()
    if row:c.close(); return js({'channel_id':row[0]})
    c.close()
    with core.tx() as c:
        c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)')
        ch=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,bid,ch)); c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(ch,a,ch,bid))
    return js({'channel_id':ch})

async def upload_file(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    try:
        form=await r.multipart(); part=await form.next()
        if not part or part.name!='file':return err(400,'file required')
        data=await part.read(decode=False)
        if len(data)>10*1024*1024:return err(413,'too large')
        with core.tx() as c:
            c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],part.filename,part.headers.get('Content-Type','application/octet-stream'),len(data),core.now_iso()))
            fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        open('/app/data/files/'+str(fid),'wb').write(data)
        c=core.connect(); row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone(); c.close()
        return js({'file':core.file_to_json(row)},201)
    except Exception:return err(400,'invalid')

async def file_get(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    c=core.connect(); row=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone(); c.close()
    return js({'file':core.file_to_json(row)}) if row else err(404,'not found')

async def file_download(r):
    u=auth(r)
    if not u:return err(401,'unauthorized')
    c=core.connect(); row=c.execute('SELECT * FROM files WHERE id=?',(int(r.match_info['id']),)).fetchone(); c.close()
    if not row:return err(404,'not found')
    return web.Response(body=open('/app/data/files/'+str(row['id']),'rb').read(),content_type=row['content_type'],headers={'Content-Disposition':'attachment; filename="'+row['filename']+'"'})

_old_app=app
def app(node):
    a=_old_app(node)
    a.router.add_post('/api/dms',dms)
    a.router.add_post('/api/files',upload_file)
    a.router.add_get('/api/files/{id}',file_get)
    a.router.add_get('/api/files/{id}/download',file_download)
    return a


async def groups(r):
    u=auth(r); w=core.get_workspace(r.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    if r.method=='GET': return js({'groups':[core.group_to_json(x) for x in rows]})
    b=await body(r)
    if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['handle']) or not isinstance(b.get('user_ids',[]),list):return err(400,'invalid')
    for x in b['user_ids']:
        if not isinstance(x,int) or not core.member_role(w['id'],x):return err(400,'invalid member')
    try:
        with core.tx() as c: gid=core.create_group(c,w['id'],u['id'],b['handle'],b.get('name',''),b['user_ids'])
        c=core.connect(); row=c.execute('SELECT * FROM groups_ WHERE id=?',(gid,)).fetchone(); c.close()
        return js({'group':core.group_to_json(row)},201)
    except:return err(409,'duplicate')
async def group_item(r):
    u=auth(r); w=core.get_workspace(r.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    c=core.connect(); g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],r.match_info['handle'])).fetchone(); c.close()
    if not g:
        if r.method=='DELETE' and core.member_role(w['id'],u['id']): return js({'deleted':True})
        return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    if g['creator_id'] != u['id'] and w['owner_id'] != u['id']: return err(403,'forbidden')
    if r.method=='DELETE':
        with core.tx() as c:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],)); c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
        return js({'deleted':True})
    b=await body(r)
    with core.tx() as c:
        if 'name' in b:c.execute('UPDATE groups_ SET name=? WHERE id=?',(b['name'],g['id']))
        if 'user_ids' in b:
            c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
            for x in b['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],x))
    c=core.connect(); g=c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone(); c.close()
    return js({'group':core.group_to_json(g)})
_old_app2=app
def app(node):
    a=_old_app2(node)
    a.router.add_route('*','/api/workspaces/{slug}/groups',groups)
    a.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',group_item)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0); n=p.parse_args().node
    core.init_db(); web.run_app(app(n),host='127.0.0.1',port=8000+n)

