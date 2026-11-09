import argparse, asyncio, json, re, secrets
from zoneinfo import ZoneInfo
from aiohttp import web
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
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized(text=json.dumps({'error':'authentication required'}),content_type='application/json')
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized(text=json.dumps({'error':'invalid token'}),content_type='application/json')
    return u
def user_required(f):
    async def w(request):
        try: request['user']=require_user(request)
        except web.HTTPException: raise
        return await f(request)
    return w
async def health(request): return web.json_response({'status':'ok','node_id':NODE})
async def index(request): return web.FileResponse('/app/static/index.html')
async def appjs(request): return web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'})
async def register(request):
    b=await read_json(request)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b.get('username','')): return json_error(400,'invalid username')
    if not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid password')
    if 'email' in b:return json_error(400,'email is not supported')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone(): return json_error(409,'username already exists')
        cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
        u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
    return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
async def login(request):
    b=await read_json(request); u=None
    if b and isinstance(b.get('username'),str):
        c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone();c.close()
    if not u or not isinstance(b.get('password'),str) or not core.verify_password(b['password'],u['password']): return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return web.json_response({'user':core.user_to_json(u),'token':t})
@user_required
async def me(request): return web.json_response({'user':core.user_to_json(request['user'])})
@user_required
async def workspaces(request):
    c=core.connect(); rows=c.execute('SELECT DISTINCT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(request['user']['id'],)).fetchall();c.close()
    return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rows]})
@user_required
async def create_workspace(request):
    b=await read_json(request)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b.get('slug','')) or not isinstance(b.get('name'),str) or not b['name']:return json_error(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return json_error(409,'duplicate slug')
        cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],request['user']['id']))
        wid=cur.lastrowid;core.add_workspace_member(c,wid,request['user']['id'],'owner'); ch=core.create_channel_row(c,wid,'general')
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()
    return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},status=201)
async def ws_handler(request):
    tok=request.query.get('token','')
    user=core.user_for_token(tok)
    if not user:return web.Response(status=401,text='{"error":"invalid token"}',content_type='application/json')
    ws=web.WebSocketResponse(); await ws.prepare(request)
    conn_id=secrets.token_urlsafe(12); core.touch_presence(conn_id,user['id'])
    subscriptions={}
    gc=core.connect(); gr=gc.execute('SELECT coalesce(max(id),0) FROM global_events').fetchone()[0]; gc.close()
    async def delivery():
        nonlocal gr
        while not ws.closed:
            for frame in core.globals_after(gr):
                await ws.send_json(frame); gr=frame.get('id',gr)
            for cid,last in list(subscriptions.items()):
                for frame in core.events_after(cid,last):
                    await ws.send_json(frame); subscriptions[cid]=frame.get('event_id',last)
            await asyncio.sleep(0.05)
    task=asyncio.create_task(delivery())
    try:
        async for msg in ws:
            if msg.type!=web.WSMsgType.TEXT: continue
            try: data=json.loads(msg.data)
            except: continue
            typ=data.get('type'); cid=data.get('channel_id')
            if typ=='subscribe' and isinstance(cid,int):
                subscriptions[cid]=core.head_event_id(cid)
                await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':subscriptions[cid]})
            elif typ=='resume' and isinstance(cid,int) and isinstance(data.get('since_event_id'),int):
                since=data['since_event_id']
                subscriptions[cid]=since
                for frame in core.events_after(cid,since):
                    await ws.send_json(frame); subscriptions[cid]=frame.get('event_id',subscriptions[cid])
                await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':core.head_event_id(cid)})
    finally:
        core.drop_presence(conn_id)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
    return ws

@user_required
async def workspace_get(request):
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if not core.member_role(w['id'],request['user']['id']):return json_error(403,'forbidden')
    return web.json_response(core.workspace_detail(request['user'],w['slug'],request.query.get('include_archived') in ('1','true')))
@user_required
async def channel_create(request):
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    role=core.member_role(w['id'],request['user']['id'])
    if not role:return json_error(403,'forbidden')
    b=await read_json(request)
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return json_error(400,'invalid channel name')
    private=b.get('is_private',False)
    if not isinstance(private,bool):return json_error(400,'invalid is_private')
    if private and role not in ('owner','admin'):return json_error(403,'forbidden')
    if not private and role=='guest':return json_error(403,'forbidden')
    topic=b.get('topic','')
    if not core.validate_topic(topic):return json_error(400,'invalid topic')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return json_error(409,'duplicate channel')
        ch=core.create_channel_row(c,w['id'],b['name'],private,topic)
        core.add_channel_member(c,ch['id'],request['user']['id'])
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return web.json_response({'channel':core.channel_to_json(ch)},status=201)
@user_required
async def channel_join(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    ok=core.join_channel(request['user'],ch['id'])
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))}) if ok else json_error(403,'forbidden')
@user_required
async def channel_patch(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    c0=core.connect(); ismem=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],request['user']['id'])).fetchone(); c0.close()
    owner=core.connect().execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()[0]
    if not ismem and request['user']['id']!=owner:return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'channel archived')
    b=await read_json(request)
    if not b or not core.validate_topic(b.get('topic')):return json_error(400,'invalid topic')
    with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']));core.emit_event(c,ch['id'],'channel.updated',{})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
@user_required
async def channel_members(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    return web.json_response({'members':[core.user_to_json(x) for x in rows]})
@user_required
async def channel_leave(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    with core.tx() as c:core.remove_channel_member(c,ch['id'],request['user']['id'])
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
@user_required
async def messages_list(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    try: limit,cur=core.parse_paging(request.query,'messages')
    except ValueError:return json_error(400,'invalid paging')
    rows,nxt=core.list_messages(ch['id'],limit,cur)
    return web.json_response({'messages':rows,'next_cursor':nxt})
@user_required
async def message_create(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    if ch['is_archived'] and not (isinstance((await read_json(request)) or {},dict) and str(((await read_json(request)) or {}).get('body','')).startswith('/unarchive')):return json_error(423,'channel archived')
    b=await read_json(request)
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty message')
    parent=b.get('parent_id')
    if parent is not None and (not isinstance(parent,int) or isinstance(parent,bool)):return json_error(400,'invalid parent')
    file_ids=b.get('file_ids',[])
    if not isinstance(file_ids,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in file_ids):return json_error(400,'invalid files')
    if len(set(file_ids))!=len(file_ids):return json_error(400,'invalid files')
    if file_ids:
        c=core.connect()
        for fid in file_ids:
            f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not f or f['uploader_id']!=request['user']['id'] or f['message_id'] is not None:
                c.close();return json_error(400,'invalid file')
        c.close()
    try: slash=core.run_slash(request['user'],ch,b['body'])
    except ValueError:return json_error(400,'invalid command')
    if slash.get('side'):
        side=slash['side']
        if side=='/topic':
            if not slash['arg'] or not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
            with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(slash['arg'],ch['id']))
            return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
        if side in ('/archive','/unarchive'):
            if core.member_role(ch['workspace_id'],request['user']['id'])!='owner':return json_error(403,'forbidden')
            x=core.set_archived(request['user'],ch['id'],side=='/archive');return web.json_response({'message':None,'channel':core.channel_to_json(x)},status=201)
        if side=='/invite':
            name=slash['arg'].strip().lstrip('@'); c=core.connect(); target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone(); c.close()
            if not target:return json_error(404,'user not found')
            with core.tx() as c: core.add_channel_member(c,ch['id'],target['id'])
            return web.json_response({'message':None,'invited_user':core.user_to_json(target)},status=201)
        return web.json_response({'message':None},status=201)
    b['body']=slash['body']
    msg=core.create_message(request['user'],ch['id'],b['body'],parent)
    if file_ids:
        with core.tx() as c:
            for fid in file_ids:c.execute('UPDATE files SET message_id=? WHERE id=?',(msg['id'],fid))
        msg=core.connect().execute('SELECT * FROM messages WHERE id=?',(msg['id'],)).fetchone()
        msg=core.message_to_json(msg)
    return web.json_response({'message':msg},status=201)
@user_required
async def replies_list(request):
    parent=core.connect().execute('SELECT * FROM messages WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not parent:return json_error(404,'message not found')
    ch=core.get_channel(parent['channel_id'])
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    try: limit,cur=core.parse_paging(request.query,'replies')
    except ValueError:return json_error(400,'invalid paging')
    rows,nxt=core.list_replies(parent['id'],limit,cur)
    return web.json_response({'replies':rows,'next_cursor':nxt})
@user_required
async def channel_archive(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    oc=core.connect(); owner=oc.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()[0]; oc.close()
    if request['user']['id']!=owner:return json_error(403,'forbidden')
    x=core.set_archived(request['user'],ch['id'],request.path.endswith('/archive'))
    return web.json_response({'channel':core.channel_to_json(x)})

@user_required
async def pin_post(request):
    m=core.connect().execute('SELECT * FROM messages WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not m:return json_error(404,'message not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    p=core.pin_message(request['user']['id'],m['id'])
    return web.json_response({'pin':dict(p)})
@user_required
async def pin_delete(request):
    m=core.connect().execute('SELECT * FROM messages WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not m:return json_error(404,'message not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    core.unpin_message(m['id']);return web.json_response({'unpinned':True})
@user_required
async def pins_get(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    try:limit,cur=core.parse_paging(request.query,'pins')
    except ValueError:return json_error(400,'invalid paging')
    rows,n=core.list_pins(ch['id'],limit,cur);return web.json_response({'pins':rows,'next_cursor':n})
@user_required
async def search_get(request):
    q=request.query.get('q')
    if not q:return json_error(400,'q required')
    try:limit,cur=core.parse_paging(request.query,'search')
    except ValueError:return json_error(400,'invalid paging')
    c=core.connect(); rows=c.execute('SELECT m.* FROM messages m WHERE m.deleted=0 AND lower(m.body) LIKE ? ORDER BY m.id DESC',('%'+q.lower()+'%',)).fetchall();c.close()
    wid=request.query.get('workspace_id')
    filtered=[]
    for x in rows:
        ch=core.get_channel(x['channel_id'])
        if wid is not None and (not ch or str(ch['workspace_id'])!=str(wid)): continue
        if ch and core.can_read_channel(request['user']['id'],ch): filtered.append(x)
    if cur is not None: filtered=[x for x in filtered if x['id']<cur]
    out=filtered[:limit]
    return web.json_response({'results':[core.message_to_json(x) for x in out],'next_cursor':core.encode_cursor('search',out[-1]['id']) if len(filtered)>limit else None})

@user_required
async def read_get(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    c=core.connect(); r=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(request['user']['id'],ch['id'])).fetchone(); last=r[0] if r else 0
    evs=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch['id'],last)).fetchall()
    unread=mention=0
    for e in evs:
        try:
            mid=json.loads(e['payload']).get('message',{}).get('id')
            m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if m and m['author_id']!=request['user']['id']:
                unread+=1
                if request['user']['id'] in core.resolve_mentions(c,m['body'],ch,m['author_id']): mention+=1
        except: pass
    c.close()
    return web.json_response({'read_state':{'channel_id':ch['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':mention}})

@user_required
async def read_post(request):
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    b=await read_json(request)
    if not b or not isinstance(b.get('last_read_event_id'),int) or isinstance(b.get('last_read_event_id'),bool):return json_error(400,'invalid read state')
    c=core.connect(); old=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(request['user']['id'],ch['id'])).fetchone(); last=max(old[0] if old else 0,b['last_read_event_id']); c.execute('INSERT INTO read_states VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(request['user']['id'],ch['id'],last));c.commit();c.close()
    return await read_get(request)

@user_required
async def reaction_post(request):
    m=core.connect().execute('SELECT * FROM messages WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not m:return json_error(404,'message not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    b=await read_json(request)
    if not b or not isinstance(b.get('emoji'),str) or not b['emoji']:return json_error(400,'invalid emoji')
    return web.json_response({'reactions':core.react(request['user'],m['id'],b['emoji'],True)})
@user_required
async def reaction_delete(request):
    m=core.connect().execute('SELECT * FROM messages WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not m:return json_error(404,'message not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(request['user']['id'],ch):return json_error(403,'forbidden')
    return web.json_response({'reactions':core.react(request['user'],m['id'],request.match_info['emoji'],False)})

@user_required
async def dms_placeholder(request):
    b=await read_json(request)
    if not b or not isinstance(b.get('user_id'),int) or isinstance(b.get('user_id'),bool): return json_error(400,'invalid user_id')
    c=core.connect(); other=c.execute('SELECT * FROM users WHERE id=?',(b['user_id'],)).fetchone(); c.close()
    if not other:return json_error(404,'user not found')
    if other['id']==request['user']['id']:return json_error(400,'invalid user_id')
    a,bid=sorted((request['user']['id'],other['id']))
    with core.tx() as c:
        r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bid)).fetchone()
        if r:return web.json_response({'channel_id':r[0]})
        cur=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)',())
        cid=cur.lastrowid
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,bid,cid))
        c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,bid))
    return web.json_response({'channel_id':cid})
@user_required
async def files_placeholder(request):
    if request.method=='POST':
        try:
            rd=await request.multipart(); part=await rd.next()
            if not part or part.name!='file': return json_error(400,'file required')
            data=await part.read(decode=False)
        except: return json_error(400,'invalid upload')
        if len(data)>10*1024*1024:return json_error(413,'file too large')
        with core.tx() as c:
            cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(request['user']['id'],part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),len(data),core.now_iso()))
            fid=cur.lastrowid
        open('/app/data/files/'+str(fid),'wb').write(data)
        row=core.connect().execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
        return web.json_response({'file':core.file_to_json(row) if hasattr(core,'file_to_json') else {'id':fid,'uploader_id':row['uploader_id'],'filename':row['filename'],'content_type':row['content_type'],'size':row['size'],'created_at':row['created_at']}},status=201)
    fid=int(request.match_info['id']); c=core.connect();row=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    if not row:return json_error(404,'file not found')
    f={'id':row['id'],'uploader_id':row['uploader_id'],'filename':row['filename'],'content_type':row['content_type'],'size':row['size'],'created_at':row['created_at']}
    if request.path.endswith('/download'):
        return web.Response(body=open('/app/data/files/'+str(fid),'rb').read(),content_type=row['content_type'],headers={'Content-Disposition':'attachment; filename="'+row['filename']+'"'})
    return web.json_response({'file':f})

@user_required
async def groups_placeholder(request):
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    role=core.member_role(w['id'],request['user']['id'])
    if not role:return json_error(403,'forbidden')
    handle=request.match_info.get('handle')
    if request.method=='GET':
        c=core.connect();rs=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
        return web.json_response({'groups':[core.group_to_json(r) for r in rs]})
    if request.method=='POST':
        b=await read_json(request)
        if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9_-]+',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(b.get('user_ids',[]),list):return json_error(400,'invalid group')
        try:r=core.create_group(w['id'],b['handle'],b['name'],b.get('user_ids',[]),request['user']['id'])
        except ValueError as e:return json_error(409 if str(e)=='duplicate' else 400,'invalid group')
        return web.json_response({'group':core.group_to_json(r)},status=201)
    c=core.connect();r=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone();c.close()
    if not r:
        if request.method=='DELETE': return web.json_response({'deleted':True})
        return json_error(404,'group not found')
    if request.method=='DELETE':
        if r['creator_id']!=request['user']['id'] and role!='owner':return json_error(403,'forbidden')
        core.delete_group(w['id'],handle);return web.json_response({'deleted':True})
    if r['creator_id']!=request['user']['id'] and role!='owner':return json_error(403,'forbidden')
    b=await read_json(request)
    if not b:return json_error(400,'invalid group')
    try:r=core.update_group(w['id'],handle,b.get('name'),b.get('user_ids'))
    except:return json_error(400,'invalid group')
    return web.json_response({'group':core.group_to_json(r)}) if r else json_error(404,'group not found')

@user_required
async def user_placeholder(request):
    if request.method=='GET':
        try: uid=int(request.match_info['id'])
        except: return json_error(404,'user not found')
        c=core.connect();r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close()
        return web.json_response({'user':core.user_to_json(r)}) if r else json_error(404,'user not found')
    b=await read_json(request)
    if not b:return json_error(400,'invalid profile')
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if any(k not in allowed for k in b):return json_error(400,'invalid profile')
    if 'timezone' in b:
        try: ZoneInfo(b['timezone'])
        except: return json_error(400,'invalid timezone')
    limits={'display_name':80,'avatar_url':512,'status_text':140,'status_emoji':32}
    for k,v in b.items():
        if not isinstance(v,str) or len(v)>limits.get(k,1000) or (k=='display_name' and not v):return json_error(400,'invalid profile')
    with core.tx() as c:
        sets=', '.join(k+'=?' for k in b);c.execute('UPDATE users SET '+sets+' WHERE id=?',tuple(b.values())+(request['user']['id'],))
        r=c.execute('SELECT * FROM users WHERE id=?',(request['user']['id'],)).fetchone();core.emit_global(c,{'type':'user.updated','user':core.user_to_json(r)})
    return web.json_response({'user':core.user_to_json(r)})

@user_required
async def message_patch(request):
    m=core.connect().execute('SELECT * FROM messages WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not m:return json_error(404,'message not found')
    b=await read_json(request)
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'invalid body')
    status,out=core.edit_message(request['user'],m['id'],b['body'])
    if status=='deleted':return json_error(409,'message deleted')
    if status=='forbidden':return json_error(403,'forbidden')
    return web.json_response({'message':out})
@user_required
async def message_delete(request):
    m=core.connect().execute('SELECT * FROM messages WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not m:return json_error(404,'message not found')
    status,out=core.delete_message(request['user'],m['id'])
    if status=='forbidden':return json_error(403,'forbidden')
    return web.json_response({'deleted':True})

@user_required
async def ws_members(request):
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if not core.member_role(w['id'],request['user']['id']):return json_error(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
    return web.json_response({'members':[{'user_id':r['user_id'],'role':r['role']} for r in rs]})
@user_required
async def member_patch(request):
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    uid=int(request.match_info['user_id']); role=core.member_role(w['id'],request['user']['id']); target=core.member_role(w['id'],uid)
    if not target:return json_error(404,'member not found')
    b=await read_json(request); new=b.get('role') if b else None
    if new not in ('owner','admin','member','guest'):return json_error(400,'invalid role')
    if uid==w['owner_id'] or uid==request['user']['id'] or role not in ('owner','admin'):return json_error(403,'forbidden')
    if new=='owner' or (role=='admin' and target=='admin'):return json_error(403 if new!='owner' else 400,'forbidden')
    with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(new,w['id'],uid))
    return web.json_response({'member':{'user_id':uid,'role':new}})
@user_required
async def transfer_owner(request):
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if request['user']['id']!=w['owner_id']:return json_error(403,'forbidden')
    b=await read_json(request); uid=b.get('new_owner_id') if b else None
    if not isinstance(uid,int) or core.member_role(w['id'],uid)!='admin':return json_error(400,'invalid target')
    with core.tx() as c:
        c.execute('UPDATE members SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],w['owner_id']))
        c.execute('UPDATE members SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],uid))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(uid,w['id']))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})

@user_required
async def workspace_patch(request):
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    role=core.member_role(w['id'],request['user']['id'])
    if role not in ('owner','admin'):return json_error(403,'forbidden')
    b=await read_json(request)
    if not b:return json_error(400,'invalid body')
    if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join_mode')
    if 'name' in b and (not isinstance(b['name'],str) or not b['name']):return json_error(400,'invalid name')
    with core.tx() as c:
        if 'name' in b:c.execute('UPDATE workspaces SET name=? WHERE id=?',(b['name'],w['id']))
        if 'join_mode' in b:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(b['join_mode'],w['id']))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
@user_required
async def invitations_placeholder(request):
    import time,secrets
    code=request.match_info.get('code')
    if request.path.startswith('/api/invitations/'):
        c=core.connect(); inv=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone(); c.close()
        if not inv:return json_error(404,'invitation not found')
        if (inv['expires_at'] and inv['expires_at']<time.time()) or inv['uses']>=inv['max_uses']:return json_error(404,'invitation not found')
        if inv['invited_user_id'] and inv['invited_user_id']!=request['user']['id']:return json_error(403,'forbidden')
        with core.tx() as c:
            c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,))
            core.add_workspace_member(c,inv['workspace_id'],request['user']['id'],'member')
        return web.json_response({'accepted':True})
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    role=core.member_role(w['id'],request['user']['id'])
    if request.method=='GET':
        if role not in ('owner','admin'):return json_error(403,'forbidden')
        c=core.connect();rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND (expires_at IS NULL OR expires_at>?) AND uses<max_uses',(w['id'],time.time())).fetchall();c.close()
        return web.json_response({'invitations':[dict(r) for r in rs]})
    if role not in ('owner','admin'):return json_error(403,'forbidden')
    if request.method=='DELETE':
        with core.tx() as c:c.execute('DELETE FROM invitations WHERE workspace_id=? AND code=?',(w['id'],code))
        return web.json_response({'deleted':True})
    b=await read_json(request)
    if b is None or any(k in b and (not isinstance(b[k],int) or isinstance(b[k],bool)) for k in ('expires_in_seconds','max_uses')):return json_error(400,'invalid invitation')
    target=b.get('invited_user_id')
    if target is not None and (not isinstance(target,int) or isinstance(target,bool)):return json_error(400,'invalid target')
    if target is None and b.get('invited_username') is not None:
        if not isinstance(b['invited_username'],str):return json_error(400,'invalid target')
        q=core.connect(); u=q.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone(); q.close()
        if not u:return json_error(404,'user not found')
        target=u['id']
    uses=1 if target is not None else b.get('max_uses',1)
    if uses<1:return json_error(400,'invalid max_uses')
    code=secrets.token_urlsafe(12)
    with core.tx() as c:c.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,expires_at,max_uses) VALUES(?,?,?,?,?)',(code,w['id'],target,time.time()+b['expires_in_seconds'] if b.get('expires_in_seconds') is not None else None,uses))
    return web.json_response({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':uses,'uses':0}},status=201)

def app():
    a=web.Application();a.add_routes([web.get('/',index),web.get('/app.js',appjs),web.get('/api/health',health),web.post('/api/auth/register',register),web.post('/api/auth/login',login),web.get('/api/auth/me',me),web.get('/api/workspaces',workspaces),web.post('/api/workspaces',create_workspace),web.get('/api/ws',ws_handler),web.get('/api/workspaces/{slug}',workspace_get),web.patch('/api/workspaces/{slug}',workspace_patch),web.get('/api/workspaces/{slug}/invitations',invitations_placeholder),web.post('/api/workspaces/{slug}/invitations',invitations_placeholder),web.post('/api/invitations/{code}/accept',invitations_placeholder),web.delete('/api/workspaces/{slug}/invitations/{code}',invitations_placeholder),web.get('/api/workspaces/{slug}/members',ws_members),web.patch('/api/workspaces/{slug}/members/{user_id}',member_patch),web.post('/api/workspaces/{slug}/transfer_ownership',transfer_owner),web.post('/api/workspaces/{slug}/channels',channel_create),web.post('/api/channels/{id}/join',channel_join),web.patch('/api/channels/{id}',channel_patch),web.get('/api/channels/{id}/members',channel_members),web.post('/api/channels/{id}/leave',channel_leave),web.post('/api/channels/{id}/archive',channel_archive),web.post('/api/channels/{id}/unarchive',channel_archive),web.get('/api/channels/{id}/messages',messages_list),web.get('/api/channels/{id}/read',read_get),web.post('/api/channels/{id}/read',read_post),web.get('/api/messages/{id}/replies',replies_list),web.patch('/api/messages/{id}',message_patch),web.delete('/api/messages/{id}',message_delete),web.post('/api/messages/{id}/pin',pin_post),web.post('/api/messages/{id}/reactions',reaction_post),web.delete('/api/messages/{id}/reactions/{emoji}',reaction_delete),web.delete('/api/messages/{id}/pin',pin_delete),web.get('/api/channels/{id}/pins',pins_get),web.get('/api/search',search_get),web.post('/api/channels/{id}/messages',message_create),web.post('/api/dms',dms_placeholder),web.post('/api/files',files_placeholder),web.get('/api/files/{id}',files_placeholder),web.get('/api/files/{id}/download',files_placeholder),web.get('/api/workspaces/{slug}/groups',groups_placeholder),web.post('/api/workspaces/{slug}/groups',groups_placeholder),web.patch('/api/workspaces/{slug}/groups/{handle}',groups_placeholder),web.delete('/api/workspaces/{slug}/groups/{handle}',groups_placeholder),web.get('/api/users/{id}',user_placeholder),web.patch('/api/users/me',user_placeholder)])
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node
    web.run_app(app(),host='127.0.0.1',port=8000+NODE)


