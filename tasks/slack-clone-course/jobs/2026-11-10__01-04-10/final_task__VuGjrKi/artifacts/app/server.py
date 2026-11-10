import argparse, asyncio, base64, json, os, re, secrets, uuid
from aiohttp import web, WSMsgType
import core
def j(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return j({'error':msg},status)
async def body(req):
    try:
        x=await req.json(); return x if isinstance(x,dict) else None
    except: return None
def auth(req):
    h=req.headers.get('Authorization','')
    t=h[7:] if h.startswith('Bearer ') else req.query.get('token','')
    if t.startswith('Bearer '): t=t[7:]
    return core.user_for_token(t)
def need(req):
    u=auth(req)
    if not u: raise web.HTTPUnauthorized()
    return u
def cursor(kind,v): return base64.urlsafe_b64encode(json.dumps({'k':kind,'v':v}).encode()).decode().rstrip('=')
def page(q,kind):
    try:
        lim=int(q.get('limit','50')); 
        if lim<1 or lim>200: raise ValueError()
        c=q.get('cursor'); last=0
        if c:
            raw=base64.urlsafe_b64decode(c+'='*((4-len(c)%4)%4)); x=json.loads(raw)
            if x.get('k')!=kind or not isinstance(x.get('v'),int): raise ValueError()
            last=x['v']
        return lim,last
    except: raise web.HTTPBadRequest()
def userjson(r): return core.user_to_json(r)
def msgjson(r):
    c=core.connect(); u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    reps=c.execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
    rs=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(r['id'],)).fetchall(); c.close()
    groups={}
    for x in rs: groups.setdefault(x['emoji'],[]).append(x['user_id'])
    fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall() if False else []
    cc=core.connect(); fs=cc.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall(); cc.close()
    fj=[{'id':x['id'],'uploader_id':x['uploader_id'],'filename':x['filename'],'content_type':x['content_type'],'size':x['size'],'created_at':x['created_at']} for x in fs]
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':userjson(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':reps,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fj,'reactions':[{'emoji':e,'count':len(v),'user_ids':v} for e,v in groups.items()],'mentions':mentions_for(r['author_id'],core.get_channel(r['channel_id']),r['body'])}
def can(uid,ch):
    c=core.connect(); x=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone(); c.close()
    return bool(x)

async def dms(req):
    u=need(req); b=await body(req)
    if not b or not isinstance(b.get('user_id'),int): return err(400,'invalid user_id')
    c=core.connect(); other=c.execute('SELECT * FROM users WHERE id=?',(b['user_id'],)).fetchone(); c.close()
    if not other:return err(404,'not found')
    a,bid=sorted((u['id'],b['user_id']))
    with core.tx() as c:
        row=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bid)).fetchone()
        if row: cid=row['channel_id']
        else:
            cur=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)'); cid=cur.lastrowid
            c.execute('INSERT INTO dms VALUES(?,?,?)',(a,bid,cid))
            c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,bid))
    return j({'channel_id':cid})
async def upload(req):
    u=need(req)
    try:
        form=await req.multipart(); part=await form.next()
        if not part or part.name!='file': return err(400,'file required')
        data=await part.read()
    except: return err(400,'invalid upload')
    if len(data)>10*1024*1024:return err(413,'too large')
    fn=part.filename or 'file'; ct=part.headers.get('Content-Type','application/octet-stream')
    with core.tx() as c:
        cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],fn,ct,len(data),core.now_iso())); fid=cur.lastrowid
    os.makedirs('/app/data/files',exist_ok=True); open('/app/data/files/'+str(fid),'wb').write(data)
    return j({'file':{'id':fid,'uploader_id':u['id'],'filename':fn,'content_type':ct,'size':len(data),'created_at':core.connect().execute('select created_at from files where id=?',(fid,)).fetchone()[0]}},201)
def fjson(r): return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
async def getfile(req):
    u=need(req); c=core.connect(); r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    return err(404,'not found') if not r else j({'file':fjson(r)})
async def download(req):
    u=need(req); c=core.connect(); r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    if not r:return err(404,'not found')
    return web.Response(body=open('/app/data/files/'+str(r['id']),'rb').read(),content_type=r['content_type'],headers={'Content-Disposition':'attachment; filename="'+r['filename']+'"'})
def mentions_for(uid,ch,text):
    c=core.connect(); out=[]
    rows=c.execute('SELECT u.id,u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall()
    by={x['username']:x['id'] for x in rows}
    for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',text):
        if name in by and by[name]!=uid: out.append(by[name])
        if name=='channel':
            out.extend(x['id'] for x in rows if x['id']!=uid)
        if name=='here':
            active=core.present_user_ids()
            out.extend(x['id'] for x in rows if x['id']!=uid and x['id'] in active)
        g=c.execute('SELECT gm.user_id FROM groups_ g JOIN group_members gm ON gm.group_id=g.id WHERE g.workspace_id=? AND g.handle=?',(ch['workspace_id'],name)).fetchall() if ch['workspace_id'] else []
        chanids={x['id'] for x in rows}
        out.extend(x['user_id'] for x in g if x['user_id']!=uid and x['user_id'] in chanids)
    c.close(); return list(dict.fromkeys(out))
async def groups(req):
    u=need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall()
    out=[]
    for g in rows:
        ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]
        out.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids})
    c.close(); return j({'groups':out})
async def create_group(req):
    u=need(req); w=core.get_workspace(req.match_info['slug']); b=await body(req)
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(b.get('user_ids'),list):return err(400,'invalid')
    with core.tx() as c:
        for x in b['user_ids']:
            if not isinstance(x,int) or not c.execute('SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone():return err(400,'invalid member')
        if c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],b['handle'])).fetchone():return err(409,'duplicate')
        cur=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],b['handle'],b['name'],u['id'])); gid=cur.lastrowid
        for x in b['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,x))
    return j({'group':{'id':gid,'handle':b['handle'],'name':b['name'],'member_user_ids':b['user_ids']}},201)
async def group_modify(req):
    u=need(req); w=core.get_workspace(req.match_info['slug']); h=req.match_info['handle']; b=await body(req)
    if not w:return err(404,'not found')
    c=core.connect(); g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone(); c.close()
    if not g:
        return j({'deleted':True}) if req.method=='DELETE' else err(404,'not found')
    if g['creator_id']!=u['id'] and core.member_role(w['id'],u['id'])!='owner':return err(403,'forbidden')
    if req.method=='DELETE':
        with core.tx() as c:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],));c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
        return j({'deleted':True})
    with core.tx() as c:
        if b and 'name' in b:c.execute('UPDATE groups_ SET name=? WHERE id=?',(b['name'],g['id']))
        if b and 'user_ids' in b:
            c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
            for x in b['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],x))
    c=core.connect(); g=c.execute('SELECT * FROM groups_ WHERE id=?',(g['id'],)).fetchone(); ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],))]; c.close()
    return j({'group':{'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids}})
async def auth_placeholder(req):
    need(req)
    return err(404,'not found')
async def health(req): return j({'status':'ok','node_id':req.app['node']})
async def register(req):
    b=await body(req)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid')
    try:
        with core.tx() as c:
            cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
            uid=cur.lastrowid; t=core.issue_token(c,uid)
    except: return err(409,'duplicate')
    return j({'user':userjson(core.user_for_token(t)),'token':t},201)
async def login(req):
    b=await body(req); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if b else '',)).fetchone(); c.close()
    if not u or not core.verify_password(b.get('password',''),u['password']): return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return j({'user':userjson(u),'token':t})
async def me(req): return j({'user':userjson(need(req))})
async def workspaces(req):
    u=need(req); c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close(); return j({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_ws(req):
    u=need(req); b=await body(req)
    if not b or not re.fullmatch(r'[a-z0-9-]{2,32}',str(b.get('slug',''))) or not isinstance(b.get('name'),str): return err(400,'invalid')
    try:
        with core.tx() as c:
            cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])); wid=cur.lastrowid
            c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner')); cur=c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=cur.lastrowid; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
    except: return err(409,'duplicate')
    return j({'workspace':core.workspace_to_json(core.get_workspace(b['slug'])),'general_channel':core.channel_to_json(core.get_channel(cid))},201)
async def ws_detail(req):
    u=need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],)).fetchall(); c.close()
    return j({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':{}})
async def members(req):
    u=need(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    c=core.connect(); rows=c.execute('SELECT m.user_id,m.role,u.username,u.display_name FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall(); c.close()
    return j({'members':[dict(x) for x in rows]})
async def create_channel(req):
    u=need(req); w=core.get_workspace(req.match_info['slug']); b=await body(req)
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin','member'):return err(403,'forbidden')
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return err(400,'invalid')
    try:
        with core.tx() as c:
            cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''))); cid=cur.lastrowid;c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id']))
    except:return err(409,'duplicate')
    return j({'channel':core.channel_to_json(core.get_channel(cid))},201)
async def join(req):
    u=need(req); ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    with core.tx() as c:
        if ch['workspace_id']: core.add_workspace_member(c,ch['workspace_id'],u['id'])
        c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
    return j({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def channel_members(req):
    u=need(req); ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    if not can(u['id'],ch):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return j({'members':[userjson(x) for x in rows]})
async def messages(req):
    u=need(req); ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    if not can(u['id'],ch):return err(403,'forbidden')
    lim,last=page(req.query,'messages'); c=core.connect(); rows=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND id>? ORDER BY id DESC LIMIT ?',(ch['id'],last,lim+1)).fetchall(); c.close(); more=len(rows)>lim; rows=rows[:lim]
    return j({'messages':[msgjson(x) for x in rows],'next_cursor':cursor('messages',rows[-1]['id']) if more else None})
async def postmsg(req):
    u=need(req); ch=core.get_channel(req.match_info['id']); b=await body(req)
    if not ch:return err(404,'not found')
    if not can(u['id'],ch):return err(403,'forbidden')
    text=b.get('body') if b else None
    if ch['is_archived'] and text != '/unarchive':return err(423,'archived')
    if not isinstance(text,str) or not text.strip():return err(400,'empty')
    file_ids=b.get('file_ids',[]) if isinstance(b,dict) else []
    if not isinstance(file_ids,list) or any(not isinstance(x,int) for x in file_ids): return err(400,'invalid files')
    with core.tx() as c:
        for fid in file_ids:
            f=c.execute('SELECT uploader_id,message_id FROM files WHERE id=?',(fid,)).fetchone()
            if not f or f['uploader_id']!=u['id'] or f['message_id'] is not None:return err(400,'invalid file')
    if text.startswith('//'):text=text[1:]
    elif text == '/me' or text.startswith('/me ') and not text[4:].strip(): return err(400,'missing text')
    elif text.startswith('/topic'):
        topic=text[6:].strip()
        if not topic or len(topic)>250:return err(400,'invalid topic')
        if not can(u['id'],ch):return err(403,'forbidden')
        with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']))
        return j({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
    elif text.startswith('/invite'):
        parts=text.split()
        if len(parts)!=2 or not parts[1].startswith('@') or not re.fullmatch(r'[A-Za-z0-9_]+',parts[1][1:]):return err(400,'invalid invite')
        c=core.connect(); target=c.execute('SELECT id,* FROM users WHERE username=?',(parts[1][1:],)).fetchone(); c.close()
        if not target:return err(404,'user not found')
        with core.tx() as c:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],target['id']))
        return j({'message':None,'invited_user':userjson(target)},201)
    elif text in ('/archive','/unarchive'):
        if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
        val=1 if text=='/archive' else 0
        with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
        return j({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},201)
    elif text.startswith('/shrug'):text=text[6:].strip()+(' ' if text[6:].strip() else '')+'¯\\_(ツ)_/¯'
    elif text.startswith('/') and not text.startswith('/me '): return err(400,'unknown command')
    with core.tx() as c:
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch['id'],u['id'],text,b.get('parent_id'),core.now_iso())); mid=cur.lastrowid
        for fid in file_ids:c.execute('UPDATE files SET message_id=? WHERE id=?',(mid,fid))
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); ev=core.emit_event(c,ch['id'],'message.reply' if b.get('parent_id') else 'message.created',{'message':msgjson(m)})
    return j({'message':msgjson(m)},201)
async def react(req):
    u=need(req); mid=int(req.match_info['id']); b=await body(req)
    emoji=b.get('emoji') if b else None
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
    ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone() if m else None
    member=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone() if m else None
    c.close()
    if not m or not ch:return err(404,'not found')
    if not member:return err(403,'forbidden')
    if not isinstance(emoji,str) or not emoji or len(emoji)>64:return err(400,'invalid emoji')
    with core.tx() as c:
        cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
        if cur.rowcount:
            core.emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    return j({'reactions':reaction_list(mid)})
def reaction_list(mid):
    c=core.connect(); rs=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall(); c.close(); g={}
    for x in rs:g.setdefault(x['emoji'],[]).append(x['user_id'])
    return [{'emoji':e,'count':len(v),'user_ids':v} for e,v in g.items()]
async def unreact(req):
    u=need(req); mid=int(req.match_info['id']); emoji=req.match_info['emoji']
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone() if m else None; member=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone() if m else None; c.close()
    if not m or not ch:return err(404,'not found')
    if not member:return err(403,'forbidden')
    with core.tx() as c:
        cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
        if cur.rowcount: core.emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    return j({'reactions':reaction_list(mid)})
async def profile(req):
    u=need(req); b=await body(req)
    if not b:return err(400,'invalid')
    allowed=('display_name','timezone','avatar_url','status_text','status_emoji')
    if any(k not in allowed for k in b):return err(400,'invalid field')
    limits={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512)}
    try:
        for k,v in b.items():
            if not isinstance(v,str):return err(400,'invalid field')
            if k=='timezone': __import__('zoneinfo').ZoneInfo(v)
            elif not (limits[k][0]<=len(v)<=limits[k][1]):return err(400,'invalid length')
    except:return err(400,'invalid value')
    with core.tx() as c:
        for k,v in b.items():c.execute('UPDATE users SET %s=? WHERE id=?'%(k,),(v,u['id']))
        row=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        core.emit_global(c,{'type':'user.updated','user':userjson(row)})
    return j({'user':userjson(row)})
async def get_user(req):
    need(req)
    c=core.connect(); row=c.execute('SELECT * FROM users WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    return err(404,'not found') if not row else j({'user':userjson(row)})
async def ws(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    conn_id=str(uuid.uuid4()); core.touch_presence(conn_id,u['id'])
    sock=web.WebSocketResponse();await sock.prepare(req); subs={}; alive=True
    initial=core.globals_after(0)
    global_seen=initial[-1][0] if initial else 0
    async def pump():
        nonlocal global_seen
        while alive:
            core.touch_presence(conn_id,u['id'])
            for _,ev in core.globals_after(global_seen):
                await sock.send_json(ev)
            vals=core.globals_after(global_seen)
            if vals: global_seen=vals[-1][0]
            for cid,last in list(subs.items()):
                for e in core.events_after(cid,last): await sock.send_json(e);subs[cid]=e['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(pump())
    try:
        async for m in sock:
            if m.type==WSMsgType.TEXT:
                x=json.loads(m.data)
                if x.get('type') in ('subscribe','resume'):
                    cid=int(x['channel_id']); head=core.head_event_id(cid); since=int(x.get('since_event_id',0))
                    subs[cid]=head if x['type']=='subscribe' else since
                    await sock.send_json({'type':'subscribed' if x['type']=='subscribe' else 'resumed','channel_id':cid,'head_event_id':head})
    finally:
        alive=False;task.cancel()
        if hasattr(core,'drop_presence'): core.drop_presence(conn_id)
    return sock
def app(node):
    a=web.Application();a['node']=node
    a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_ws);a.router.add_get('/api/workspaces/{slug}',ws_detail);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/channels/{id}/join',join); a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',postmsg)
    a.router.add_post('/api/messages/{id}/reactions',react);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',unreact);a.router.add_patch('/api/users/me',profile);a.router.add_get('/api/ws',ws); a.router.add_post('/api/dms',dms); a.router.add_post('/api/files',upload); a.router.add_get('/api/files/{id}',getfile); a.router.add_get('/api/files/{id}/download',download); a.router.add_get('/api/workspaces/{slug}/groups',groups); a.router.add_post('/api/workspaces/{slug}/groups',create_group); a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',group_modify); a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',group_modify); a.router.add_get('/api/users/{id}',get_user)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);n=p.parse_args().node;web.run_app(app(n),host='127.0.0.1',port=8000+n)
# static routes are attached after app construction
_old_app=app
def app(node):
    a=_old_app(node)
    a.router.add_get('/',lambda r:web.FileResponse('/app/static/index.html'))
    a.router.add_get('/app.js',lambda r:web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'}))
    return a
