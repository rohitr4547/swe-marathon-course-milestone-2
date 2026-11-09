import argparse, asyncio, json, re, time, uuid
from aiohttp import web
import core
NODE=0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(req):
    try:
        x=await req.json()
        return x if isinstance(x,dict) else None
    except: return None
def require_user(req):
    h=req.headers.get('Authorization','')
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized()
    return u
@web.middleware
async def auth_gate(req,handler):
    public=req.path in ('/api/health','/api/auth/register','/api/auth/login') or req.path=='/' or req.path.startswith('/static/')
    if req.path.startswith('/api/') and not public and req.path!='/api/ws':
        h=req.headers.get('Authorization','')
        if not h.startswith('Bearer ') or not core.user_for_token(h[7:]):
            return json_error(401,'unauthorized')
    return await handler(req)

@web.middleware
async def errors(req,handler):
    try: return await handler(req)
    except web.HTTPException as e:
        if e.status==401:return json_error(401,'unauthorized')
        raise
    except Exception as e:
        return json_error(500,str(e))
def user_obj(r): return core.user_to_json(r)
async def health(req): return web.json_response({'status':'ok','node_id':NODE})
async def index(req): return web.FileResponse('/app/static/index.html')
async def register(req):
    b=await read_json(req)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone(): return json_error(409,'username exists')
        c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
        u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
    return web.json_response({'user':user_obj(u),'token':t},status=201)
async def login(req):
    b=await read_json(req); u=None
    if b and isinstance(b.get('username'),str) and isinstance(b.get('password'),str):
        c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone()
        if u and core.verify_password(b['password'],u['password']): t=core.issue_token(c,u['id'])
        else:t=None
        c.close()
        if t:return web.json_response({'user':user_obj(u),'token':t})
    return json_error(401,'invalid credentials')
async def me(req): return web.json_response({'user':user_obj(require_user(req))})
async def workspaces(req):
    u=require_user(req); c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(req):
    u=require_user(req); b=await read_json(req)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str):return json_error(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return json_error(409,'exists')
        c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])); w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone()
        c.execute('INSERT INTO memberships VALUES(?,?,?)',(w['id'],u['id'],'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(w['id'],'general')); ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone(); c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
    return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},status=201)
async def workspace(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); arch='' if req.query.get('include_archived') in ('1','true') else ' AND is_archived=0'; rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_private=0 OR id IN (SELECT channel_id FROM channel_members WHERE user_id=?))'+arch,(w['id'],u['id'])).fetchall(); c.close()
    states=[]
    for x in rows:
        states.append(state_for(u,x['id']) if 'state_for' in globals() else {'channel_id':x['id'],'last_read_event_id':0,'unread_count':0,'mention_count':0})
    return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':states})
async def channels(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return json_error(403,'forbidden')
    b=await read_json(req)
    if role=='guest':return json_error(403,'forbidden')
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']) or ('topic' in b and (not isinstance(b['topic'],str) or len(b['topic'])>250)):return json_error(400,'invalid channel')
    with core.tx() as c:
        if bool(b.get('is_private',False)) and role not in ('owner','admin'):return json_error(403,'forbidden')
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return json_error(409,'exists')
        c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private',False))),b.get('topic','')))
        ch=c.execute('SELECT * FROM channels WHERE id=last_insert_rowid()').fetchone(); c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
    return web.json_response({'channel':core.channel_to_json(ch)},status=201)
async def join(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if ch['workspace_id'] and core.member_role(ch['workspace_id'],u['id'])=='guest': return json_error(403,'forbidden')
    if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):
        w=core.get_workspace(core.connect().execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()['slug'])
        if w['join_mode']!='open': return json_error(403,'forbidden')
    if ch['is_private']:
        cchk=core.connect(); cm=cchk.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); cchk.close()
        if not cm:return json_error(403,'forbidden')
    with core.tx() as c:
        existed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):
            c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
        c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
        if not existed:
            core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
        if ch['workspace_id']:
            g=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(ch['workspace_id'],)).fetchone()
            if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u['id']))
    return web.json_response({'channel':core.channel_to_json(ch)})
async def messages(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if 'page' in req.query or 'offset' in req.query:return json_error(400,'offset pagination unsupported')
    try:
        lim=int(req.query.get('limit','50'))
        if lim<1 or lim>200:return json_error(400,'invalid limit')
    except:return json_error(400,'invalid limit')
    cur=req.query.get('cursor')
    if cur:
        try:
            raw=__import__('base64').urlsafe_b64decode(cur+'===').decode(); obj=json.loads(raw)
            if not isinstance(obj,dict) or obj.get('k')!='messages' or not isinstance(obj.get('v'),int):raise ValueError()
        except:return json_error(400,'invalid cursor')
    c0=core.connect(); allowed=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
    if ch['is_dm'] and not allowed:return json_error(403,'forbidden')
    if not allowed and ch['workspace_id']:
        if not core.member_role(ch['workspace_id'],u['id']) or ch['is_private']: return json_error(403,'forbidden')
    fresh=core.get_channel(ch['id'])
    
    c=core.connect(); rows=c.execute('SELECT m.*,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.author_id WHERE m.channel_id=? AND m.deleted=0 AND m.parent_id IS NULL ORDER BY m.id DESC',(ch['id'],)).fetchall(); c.close()
    out=[]
    for r in rows:
        rc=core.connect(); rr=rc.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY min(rowid)',(r['id'],)).fetchall()
        ff=rc.execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE message_id=?',(r['id'],)).fetchall(); rc.close()
        rx=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(z) for z in x['ids'].split(',')]} for x in rr]
        fx=[dict(x) for x in ff]
        rcnt=core.connect().execute('SELECT count(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0]
        out.append({'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':{'username':r['username'],'display_name':r['display_name']},'body':r['body'],'parent_id':r['parent_id'],'reply_count':rcnt,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':fx,'reactions':rx,'mentions':json.loads(r['mentions'])})
    if req.method=='GET':
        start=int(obj['v']) if cur else 10**18
        page=[x for x in out if x['id']<start][:lim]
        nxt=None if len(page)<lim else __import__('base64').urlsafe_b64encode(json.dumps({'k':'messages','v':page[-1]['id']}).encode()).decode().rstrip('=')
        return web.json_response({'messages':page,'next_cursor':nxt})
    b=await read_json(req)
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return json_error(400,'empty body')
    if fresh['is_archived'] and b.get('body','').strip() not in ('/archive','/unarchive'): return json_error(423,'archived')
    if fresh['is_archived'] and b.get('body','').strip() not in ('/archive','/unarchive'): return json_error(423,'archived')
    body=b['body'].strip()
    if body.startswith('//'): body=body[1:]
    elif body.startswith('/me'):
        if body=='/me': return json_error(400,'/me requires text')
    elif body.startswith('/shrug'):
        tail=body[6:].strip(); body=(tail+' ' if tail else '')+'¯\\_(ツ)_/¯'
    elif body.startswith('/invite '):
        name=body[8:].strip()
        if not name.startswith('@'):return json_error(400,'invalid invite')
        cc=core.connect(); target=cc.execute('SELECT * FROM users WHERE username=?',(name[1:],)).fetchone(); cc.close()
        if not target:return json_error(404,'user not found')
        with core.tx() as c:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],target['id']))
        return web.json_response({'message':None,'invited_user':core.user_to_json(target)},status=201)
    elif body.startswith('/topic '):
        topic=body[7:].strip()
        if not topic or len(topic)>250:return json_error(400,'invalid topic')
        with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']))
        return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
    elif body in ('/archive','/unarchive'):
        if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
        with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if body=='/archive' else 0,ch['id']))
        return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
    elif body.startswith('/'):
        return json_error(400,'unknown command')
    b['body']=body
    file_ids=b.get('file_ids',[])
    if not isinstance(file_ids,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in file_ids):return json_error(400,'invalid file_ids')
    attached=[]
    if file_ids:
        cc=core.connect()
        for fid in file_ids:
            fr=cc.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,u['id'])).fetchone()
            if not fr: cc.close(); return json_error(400,'invalid file')
            attached.append(fr)
        cc.close()

    parent=b.get('parent_id')
    if parent is not None and (not isinstance(parent,int) or isinstance(parent,bool)): return json_error(400,'invalid parent_id')
    mentions=[]
    cmen=core.connect()
    members=cmen.execute('SELECT u.id,u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall()
    byname={x['username']:x['id'] for x in members}
    groups_by_handle={}
    if ch['workspace_id']:
        for gr in cmen.execute('SELECT handle,user_ids FROM groups WHERE workspace_id=?',(ch['workspace_id'],)).fetchall():
            groups_by_handle[gr['handle']]=json.loads(gr['user_ids'])
    cmen.close()
    channel_ids={x['id'] for x in members}
    for name in re.findall(r'(?<![\w@.])@(\w+)',b['body']):
        if name=='channel':
            mentions.extend(x['id'] for x in members if x['id']!=u['id'])
        elif name=='here':
            cp=core.connect(); online={x['user_id'] for x in cp.execute('SELECT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall()}; cp.close()
            mentions.extend(x['id'] for x in members if x['id']!=u['id'] and x['id'] in online)
        elif name in groups_by_handle:
            mentions.extend(x for x in groups_by_handle[name] if x in channel_ids and x!=u['id'])
        elif name in byname and byname[name]!=u['id']:
            mentions.append(byname[name])
    mentions=list(dict.fromkeys(mentions))
    with core.tx() as c:
        c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,mentions,created_at) VALUES(?,?,?,?,?,?)',(ch['id'],u['id'],b['body'],parent,json.dumps(mentions),core.now_iso()))
        r=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone()
        for fr in attached:c.execute('UPDATE files SET message_id=? WHERE id=?',(r['id'],fr['id']))
        frame=core.emit_event(c,ch['id'],'message.reply' if parent is not None else 'message.created',{'message':{'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'body':r['body'],'parent_id':r['parent_id'],'created_at':r['created_at'],'edited_at':None,'reactions':[],'mentions':mentions}})
        c.execute('UPDATE messages SET event_id=? WHERE id=?',(frame['event_id'],r['id']))
    return web.json_response({'message':{'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':core.user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':0,'created_at':r['created_at'],'edited_at':None,'files':[],'reactions':[],'mentions':mentions,'files':[dict(fr) for fr in attached]}},status=201)
async def ws(req):
    u=core.user_for_token(req.query.get('token',''))
    if not u:return json_error(401,'unauthorized')
    w=web.WebSocketResponse(); await w.prepare(req)
    conn_id=uuid.uuid4().hex
    with core.tx() as pc: pc.execute('INSERT INTO presence VALUES(?,?,?)',(conn_id,u['id'],time.time()))
    subscriptions={}
    gc=core.connect(); gr=gc.execute('SELECT coalesce(max(id),0) n FROM global_events').fetchone(); gc.close(); global_last=gr['n']
    async def poll():
        while not w.closed:
            try:
                nonlocal global_last
                gc=core.connect(); grows=gc.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_last,)).fetchall(); gc.close()
                for grow in grows:
                    await w.send_json(json.loads(grow['payload'])); global_last=grow['id']
                for cid,last in list(subscriptions.items()):
                    c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall(); c.close()
                    for row in rows:
                        frame=json.loads(row['payload']); await w.send_json(frame); subscriptions[cid]=frame['event_id']
                await asyncio.sleep(.05)
            except: break
    task=asyncio.create_task(poll())
    try:
        async for msg in w:
            if msg.type!=1: continue
            try:b=json.loads(msg.data)
            except:continue
            cid=b.get('channel_id')
            if not isinstance(cid,int):continue
            ch=core.get_channel(cid)
            if not ch:continue
            c=core.connect(); head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()[0]; c.close()
            if b.get('type')=='subscribe':
                subscriptions[cid]=head
                await w.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
            elif b.get('type')=='resume' and isinstance(b.get('since_event_id'),int):
                since=b['since_event_id']; subscriptions[cid]=since
                c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); c.close()
                last=since
                for row in rows:
                    frame=json.loads(row['payload']); await w.send_json(frame); last=frame['event_id']
                subscriptions[cid]=last
                await w.send_json({'type':'resumed','channel_id':cid,'head_event_id':last})
    finally:
        task.cancel()
        with core.tx() as pc: pc.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
    return w


async def replies(req):
    u=require_user(req); pid=int(req.match_info['id']); c=core.connect()
    parent=c.execute('SELECT * FROM messages WHERE id=?',(pid,)).fetchone()
    if not parent:c.close(); return json_error(404,'not found')
    try: lim=int(req.query.get('limit','50'))
    except: return json_error(400,'invalid limit')
    if lim<1 or lim>200:return json_error(400,'invalid limit')
    after=0
    if req.query.get('cursor'):
        try:
            z=json.loads(__import__('base64').urlsafe_b64decode(req.query['cursor']+'===').decode()); after=int(z['v'])
        except: return json_error(400,'invalid cursor')
    rows=c.execute('SELECT m.*,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.author_id WHERE m.parent_id=? AND m.deleted=0 AND m.id>? ORDER BY m.id LIMIT ?',(pid,after,lim+1)).fetchall(); c.close()
    out=[{'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':{'id':r['author_id'],'username':r['username'],'display_name':r['display_name'],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''},'body':r['body'],'parent_id':r['parent_id'],'reply_count':0,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[],'reactions':[],'mentions':json.loads(r['mentions'])} for r in rows]
    nxt=None
    if len(out)>lim:
        out=out[:lim]; nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'replies','v':int(out[-1]['id'])}).encode()).decode().rstrip('=')
    return web.json_response({'replies':out,'next_cursor':nxt})
async def pin(req):
    u=require_user(req); mid=int(req.match_info['id']); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    with core.tx() as c:c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()))
    c=core.connect(); p=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone(); c.close()
    return web.json_response({'pin':{'message_id':mid,'pinned_by':p['pinned_by'],'pinned_at':p['pinned_at']}})
async def unpin(req):
    u=require_user(req); mid=int(req.match_info['id'])
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
    return web.json_response({'unpinned':True})
async def pins(req):
    u=require_user(req); cid=int(req.match_info['id']); ch=core.get_channel(cid)
    if not ch:return json_error(404,'not found')
    c=core.connect(); ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone(); c.close()
    if not ok:return json_error(403,'forbidden')
    try:
        lim=int(req.query.get('limit','50'))
        if lim<1 or lim>200:return json_error(400,'invalid limit')
    except:return json_error(400,'invalid limit')
    after=10**18; cur=req.query.get('cursor')
    if cur:
        try:
            z=json.loads(__import__('base64').urlsafe_b64decode(cur+'===').decode())
            if z.get('k')!='pins' or not isinstance(z.get('v'),int):raise ValueError()
            after=z['v']
        except:return json_error(400,'invalid cursor')
    c=core.connect(); rows=c.execute('SELECT p.*,m.body,m.channel_id,m.author_id,m.created_at FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND p.message_id<? ORDER BY p.message_id DESC LIMIT ?',(cid,after,lim+1)).fetchall(); c.close()
    vals=[{'message':{'id':r['message_id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'body':r['body'],'created_at':r['created_at']},'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']} for r in rows[:lim]]
    nxt=None
    if len(rows)>lim:nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'pins','v':rows[lim-1]['message_id']}).encode()).decode().rstrip('=')
    return web.json_response({'pins':vals,'next_cursor':nxt})

async def search(req):
    u=require_user(req); q=req.query.get('q')
    if not isinstance(q,str) or not q:return json_error(400,'missing q')
    cursor=req.query.get('cursor'); after=10**18
    workspace_id=req.query.get('workspace_id')
    if workspace_id is not None:
        try: workspace_id=int(workspace_id)
        except: return json_error(400,'invalid workspace_id')
    if cursor:
        try:
            z=json.loads(__import__('base64').urlsafe_b64decode(cursor+'===').decode())
            if z.get('k')!='search' or not isinstance(z.get('v'),int): raise ValueError()
            after=z['v']
        except: return json_error(400,'invalid cursor')
    try:
        lim=int(req.query.get('limit','50'))
        if lim<1 or lim>200:return json_error(400,'invalid limit')
    except:return json_error(400,'invalid limit')
    c=core.connect()
    sql="SELECT m.*,u.username,u.display_name FROM messages m JOIN users u ON u.id=m.author_id JOIN channels sc ON sc.id=m.channel_id LEFT JOIN channel_members cm ON cm.channel_id=m.channel_id AND cm.user_id=? LEFT JOIN memberships sm ON sm.workspace_id=sc.workspace_id AND sm.user_id=? WHERE m.deleted=0 AND lower(m.body) LIKE lower(?) AND m.id<? AND ((sc.is_private=0 AND sm.user_id IS NOT NULL) OR cm.user_id IS NOT NULL)"
    args=[u['id'],u['id'],'%'+q+'%',after]
    if workspace_id is not None: sql+=' AND sc.workspace_id=?'; args.append(workspace_id)
    sql+=' ORDER BY m.id DESC LIMIT ?'; args.append(lim+1)
    rows=c.execute(sql,args).fetchall(); c.close()
    vals=[{'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':{'id':r['author_id'],'username':r['username'],'display_name':r['display_name'],'timezone':'UTC','avatar_url':'','status_text':'','status_emoji':''},'body':r['body'],'created_at':r['created_at']} for r in rows[:lim]]
    nxt=None
    if len(rows)>lim:nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'search','v':vals[-1]['id']}).encode()).decode().rstrip('=')
    return web.json_response({'results':vals,'next_cursor':nxt})

async def reaction(req):
    u=require_user(req); mid=int(req.match_info['id']); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if ch['is_private']:
        cc=core.connect(); ok=cc.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone(); cc.close()
        if not ok:return json_error(403,'forbidden')
    if req.method=='POST':
        b=await read_json(req)
        if not b or not isinstance(b.get('emoji'),str) or not b['emoji']:return json_error(400,'invalid emoji')
        emoji=b['emoji']; add=True
    else:
        emoji=__import__('urllib.parse').parse.unquote(req.match_info['emoji']); add=False
    with core.tx() as c:
        if add:
            cur=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji)); changed=cur.rowcount>0
        else:
            cur=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)); changed=cur.rowcount>0
        rs=c.execute('SELECT emoji,count(*) n,group_concat(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY min(rowid)',(mid,)).fetchall()
        vals=[{'emoji':r['emoji'],'count':r['n'],'user_ids':[int(x) for x in r['ids'].split(',')]} for r in rs]
        if changed: core.emit_event(c,m['channel_id'],'reaction.added' if add else 'reaction.removed',{'message_id':mid,'user_id':u['id'],'emoji':emoji,'reactions':vals})
    return web.json_response({'reactions':vals})

async def dms(req):
    u=require_user(req); b=await read_json(req)
    if not b or not isinstance(b.get('user_id'),int) or isinstance(b.get('user_id'),bool):return json_error(400,'invalid user_id')
    other=b['user_id']
    c=core.connect(); target=c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone(); c.close()
    if not target:return json_error(404,'user not found')
    if other==u['id']:return json_error(400,'invalid user_id')
    a,z=sorted((u['id'],other))
    c=core.connect(); existing=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,z)).fetchone(); c.close()
    if existing:return web.json_response({'channel_id':existing['channel_id']})
    with core.tx() as c:
        c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',('',1))
        cid=c.execute('SELECT id FROM channels WHERE id=last_insert_rowid()').fetchone()['id']
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,z,cid))
        c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a)); c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,z))
    return web.json_response({'channel_id':cid})

async def upload_file(req):
    u=require_user(req)
    try:
        rd=await req.multipart(); part=await rd.next()
        if not part or part.name!='file':return json_error(400,'missing file')
        data=await part.read()
    except:return json_error(400,'invalid upload')
    if len(data)>10*1024*1024:return json_error(413,'file too large')
    name=part.filename or 'file'; typ=part.headers.get('Content-Type','application/octet-stream')
    with core.tx() as c:
        c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],name,typ,len(data),core.now_iso()))
        fid=c.execute('SELECT id FROM files WHERE id=last_insert_rowid()').fetchone()['id']
    import os
    os.makedirs('/app/data/files',exist_ok=True); open('/app/data/files/'+str(fid),'wb').write(data)
    return web.json_response({'file':{'id':fid,'uploader_id':u['id'],'filename':name,'content_type':typ,'size':len(data),'created_at':core.now_iso()}},status=201)
async def get_file(req):
    u=require_user(req); fid=int(req.match_info['id']); c=core.connect(); r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    return web.json_response({'file':dict(r)})
async def download_file(req):
    u=require_user(req); fid=int(req.match_info['id']); c=core.connect(); r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    return web.FileResponse('/app/data/files/'+str(fid),headers={'Content-Type':r['content_type'],'Content-Disposition':'attachment; filename="'+r['filename']+'"'})

async def groups(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect()
    if req.method=='GET':
        rs=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
        return web.json_response({'groups':[{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'])} for r in rs]})
    b=await read_json(req)
    if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9_-]+',b['handle']) or not isinstance(b.get('name'),str):c.close();return json_error(400,'invalid group')
    ids=b.get('user_ids',[])
    if not isinstance(ids,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in ids):c.close();return json_error(400,'invalid users')
    for x in ids:
        if not core.member_role(w['id'],x):c.close();return json_error(400,'non-member')
    if c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(w['id'],b['handle'])).fetchone():c.close();return json_error(409,'exists')
    c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],b['handle'],b['name'],u['id'],json.dumps(ids))); r=c.execute('SELECT * FROM groups WHERE id=last_insert_rowid()').fetchone();c.close()
    return web.json_response({'group':{'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}},status=201)
async def group_item(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect(); r=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone();c.close()
    if not r:
        cc=core.connect(); dg=cc.execute('SELECT creator_id FROM deleted_groups WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone(); cc.close()
        if req.method=='DELETE' and dg and dg['creator_id']==u['id']: return web.json_response({'deleted':True})
        return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin') and r['creator_id']!=u['id']:return json_error(403,'forbidden')
    if req.method=='DELETE':
        with core.tx() as c:
            c.execute('DELETE FROM groups WHERE id=?',(r['id'],))
            c.execute('INSERT OR REPLACE INTO deleted_groups VALUES(?,?,?)',(w['id'],r['handle'],r['creator_id']))
        return web.json_response({'deleted':True})
    b=await read_json(req)
    if not b:return json_error(400,'invalid json')
    ids=b.get('user_ids',json.loads(r['user_ids']))
    with core.tx() as c:c.execute('UPDATE groups SET name=?,user_ids=? WHERE id=?',(b.get('name',r['name']),json.dumps(ids),r['id']))
    return web.json_response({'group':{'id':r['id'],'handle':r['handle'],'name':b.get('name',r['name']),'member_user_ids':ids}})

async def get_user(req):
    require_user(req); c=core.connect(); r=c.execute('SELECT * FROM users WHERE id=?',(int(req.match_info['id']),)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    return web.json_response({'user':core.user_to_json(r)})
async def patch_me(req):
    u=require_user(req); b=await read_json(req)
    if not b:return json_error(400,'invalid json')
    allowed=('display_name','timezone','avatar_url','status_text','status_emoji')
    if 'timezone' in b and (not isinstance(b['timezone'],str) or b['timezone'] not in ('UTC','America/New_York','Europe/London','Asia/Tokyo')):return json_error(400,'invalid timezone')
    limits={'display_name':80,'status_text':140,'status_emoji':32,'avatar_url':512,'timezone':64}
    for k in allowed:
        if k in b and not isinstance(b[k],str):return json_error(400,'invalid field')
        if k in b and (len(b[k])<1 or len(b[k])>limits[k]):return json_error(400,'field too long')
    with core.tx() as c:
        for k in allowed:
            if k in b:c.execute('UPDATE users SET '+k+'=? WHERE id=?',(b[k],u['id']))
        r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps({'type':'user.updated','user':core.user_to_json(r)}),))
    return web.json_response({'user':core.user_to_json(r)})

def app():
    a=web.Application(middlewares=[auth_gate,errors]); a.add_routes([web.get('/api/health',health),web.get('/',index),web.get('/api/search',search),web.post('/api/dms',dms),web.route('*','/api/workspaces/{slug}/groups',groups),web.route('*','/api/workspaces/{slug}/groups/{handle}',group_item),web.post('/api/files',upload_file),web.get('/api/files/{id}',get_file),web.get('/api/files/{id}/download',download_file),web.post('/api/auth/register',register),web.post('/api/auth/login',login),web.get('/api/auth/me',me),web.get('/api/users/{id}',get_user),web.patch('/api/users/me',patch_me),web.get('/api/workspaces',workspaces),web.post('/api/workspaces',create_workspace),web.get('/api/workspaces/{slug}',workspace),web.patch('/api/workspaces/{slug}',patch_workspace),web.post('/api/workspaces/{slug}/channels',channels),web.route('*','/api/workspaces/{slug}/invitations',invitations),web.delete('/api/workspaces/{slug}/invitations/{code}',revoke_invite),web.post('/api/invitations/{code}/accept',accept_invite),web.get('/api/workspaces/{slug}/members',workspace_members),web.patch('/api/workspaces/{slug}/members/{user_id}',patch_member),web.post('/api/workspaces/{slug}/transfer_ownership',transfer),web.post('/api/channels/{id}/archive',archive_channel),web.post('/api/channels/{id}/unarchive',unarchive_channel),web.route('*','/api/channels/{id}/messages',messages),web.post('/api/channels/{id}/join',join),web.route('*','/api/channels/{id}/read',read_state),web.patch('/api/channels/{id}',patch_channel),web.post('/api/channels/{id}/leave',leave),web.get('/api/channels/{id}/members',channel_members),web.get('/api/messages/{id}/replies',replies),web.patch('/api/messages/{id}',edit_message),web.delete('/api/messages/{id}',delete_message),web.post('/api/messages/{id}/pin',pin),web.post('/api/messages/{id}/reactions',reaction),web.delete('/api/messages/{id}/reactions/{emoji}',reaction),web.delete('/api/messages/{id}/pin',unpin),web.get('/api/channels/{id}/pins',pins),web.get('/api/ws',ws)]); return a

async def workspace_members(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return json_error(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT user_id,role FROM memberships WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
    return web.json_response({'members':[{'user_id':r['user_id'],'role':r['role']} for r in rs]})
async def patch_member(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    caller=core.member_role(w['id'],u['id']); target=int(req.match_info['user_id'])
    if not caller:return json_error(403,'forbidden')
    c=core.connect(); old=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone(); c.close()
    if not old:return json_error(404,'not found')
    b=await read_json(req); role=b.get('role') if b else None
    if role not in ('admin','member','guest'):return json_error(400,'invalid role')
    if target==w['owner_id'] or target==u['id']:return json_error(403,'forbidden')
    if caller not in ('owner','admin') or (caller=='admin' and old['role']=='admin'):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target))
    return web.json_response({'member':{'user_id':target,'role':role}})
async def transfer(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if w['owner_id']!=u['id']:return json_error(403,'forbidden')
    b=await read_json(req); nid=b.get('new_owner_id') if b else None
    if not isinstance(nid,int):return json_error(400,'invalid target')
    c=core.connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],nid)).fetchone(); c.close()
    if not r or r['role']!='admin':return json_error(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE memberships SET role="admin" WHERE workspace_id=? AND user_id=?',(w['id'],u['id']))
        c.execute('UPDATE memberships SET role="owner" WHERE workspace_id=? AND user_id=?',(w['id'],nid))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(nid,w['id']))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def channel_members(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if ch['workspace_id'] is not None and not core.member_role(ch['workspace_id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return web.json_response({'members':[core.user_to_json(r) for r in rs]})
async def leave(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    with core.tx() as c:
        existed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
        if existed: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return web.json_response({'channel':core.channel_to_json(ch)})


async def patch_workspace(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return json_error(403,'forbidden')
    b=await read_json(req)
    if not b:return json_error(400,'invalid json')
    if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join_mode')
    if 'name' in b and not isinstance(b['name'],str):return json_error(400,'invalid name')
    with core.tx() as c:
        if 'name' in b:c.execute('UPDATE workspaces SET name=? WHERE id=?',(b['name'],w['id']))
        if 'join_mode' in b:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(b['join_mode'],w['id']))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def patch_channel(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    cchk=core.connect(); cm=cchk.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); cchk.close()
    if ch['workspace_id'] is None or (not cm and core.member_role(ch['workspace_id'],u['id'])!='owner'):return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'archived')
    b=await read_json(req)
    if not b or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return json_error(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']))
        core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})


async def invitations(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return json_error(403,'forbidden')
    if req.method=='GET':
        c=core.connect(); rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND expires_at>?',(w['id'],core.now_iso())).fetchall(); c.close()
        return web.json_response({'invitations':[dict(r) for r in rows]})
    b=await read_json(req)
    if b is None:return json_error(400,'invalid json')
    if 'max_uses' in b and (not isinstance(b['max_uses'],int) or isinstance(b['max_uses'],bool) or b['max_uses']<1):return json_error(400,'invalid max_uses')
    if 'expires_in_seconds' in b and (not isinstance(b['expires_in_seconds'],int) or isinstance(b['expires_in_seconds'],bool) or b['expires_in_seconds']<1):return json_error(400,'invalid expiry')
    if 'invited_user_id' in b and not isinstance(b['invited_user_id'],int):return json_error(400,'invalid user')
    invited_id=b.get('invited_user_id')
    if 'invited_username' in b:
        if not isinstance(b['invited_username'],str):return json_error(400,'invalid username')
        cc=core.connect(); uu=cc.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone(); cc.close()
        if not uu:return json_error(400,'unknown user')
        invited_id=uu['id']
    import time
    code=__import__('secrets').token_urlsafe(12); exp=(__import__('datetime').datetime.now(__import__('datetime').timezone.utc)+__import__('datetime').timedelta(days=3650)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    if b.get('expires_in_seconds'): exp=(__import__('datetime').datetime.now(__import__('datetime').timezone.utc)+__import__('datetime').timedelta(seconds=b['expires_in_seconds'])).isoformat(timespec='milliseconds').replace('+00:00','Z')
    with core.tx() as c:
        c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,?,?)',(code,w['id'],u['id'],exp,1 if invited_id is not None else b.get('max_uses',1),0,invited_id,0,core.now_iso()))
    return web.json_response({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':(1 if invited_id is not None else b.get('max_uses',1)),'uses':0,'expires_at':exp}},status=201)
async def revoke_invite(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    code=req.match_info['code']; c=core.connect(); r=c.execute('SELECT 1 FROM invitations WHERE code=? AND workspace_id=? AND revoked=0',(code,w['id'])).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    with core.tx() as c:c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(code,))
    return web.json_response({'deleted':True})
async def accept_invite(req):
    u=require_user(req); code=req.match_info['code']; c=core.connect(); r=c.execute('SELECT * FROM invitations WHERE code=? AND revoked=0',(code,)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    if r['invited_user_id'] is not None and r['invited_user_id']!=u['id']:return json_error(403,'not invited')
    if r['uses']>=r['max_uses'] or r['expires_at']<core.now_iso():return json_error(404,'expired')
    with core.tx() as c:
        c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,))
        c.execute('INSERT OR IGNORE INTO memberships VALUES(?,?,?)',(r['workspace_id'],u['id'],'member'))
        g=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(r['workspace_id'],)).fetchone()
        if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u['id']))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(core.connect().execute('SELECT slug FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone()['slug']))})


async def archive_channel(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=1 WHERE id=?',(ch['id'],))
        core.emit_event(c,ch['id'],'channel.archived',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def unarchive_channel(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=0 WHERE id=?',(ch['id'],))
        core.emit_event(c,ch['id'],'channel.unarchived',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
def state_for(u,cid):
    c=core.connect()
    r=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
    last=r['last_read_event_id'] if r else 0
    rows=c.execute('SELECT author_id,mentions FROM messages WHERE channel_id=? AND deleted=0 AND author_id!=? AND COALESCE(event_id,0)>?',(cid,u['id'],last)).fetchall()
    mentions=0
    for row in rows:
        try:
            if u['id'] in json.loads(row['mentions'] or '[]'): mentions+=1
        except: pass
    c.close()
    return {'channel_id':cid,'last_read_event_id':last,'unread_count':len(rows),'mention_count':mentions}
async def read_state(req):
    u=require_user(req); ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    c=core.connect(); ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.close()
    if not ok:return json_error(403,'forbidden')
    if req.method=='POST':
        b=await read_json(req); n=b.get('last_read_event_id') if b else None
        if not isinstance(n,int) or isinstance(n,bool) or n<0:return json_error(400,'invalid event id')
        with core.tx() as c:c.execute('INSERT INTO read_states VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=max(last_read_event_id,excluded.last_read_event_id)',(u['id'],ch['id'],n))
    return web.json_response({'read_state':state_for(u,ch['id'])})









async def edit_message(req):
    u=require_user(req); mid=int(req.match_info['id']); c0=core.connect()
    m=c0.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c0.close()
    if not m:return json_error(404,'not found')
    if m['deleted']:return json_error(409,'deleted')
    if m['author_id']!=u['id']:return json_error(403,'forbidden')
    body=await read_json(req)
    if not body or not isinstance(body.get('body'),str) or not body['body'].strip():return json_error(400,'invalid body')
    cc=core.connect(); mems=cc.execute('SELECT u.id,u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(m['channel_id'],)).fetchall(); cc.close()
    names={x['username']:x['id'] for x in mems}; mentions=[]
    for name in re.findall(r'(?<![\w@.])@(\w+)',body['body']):
        if name=='channel': mentions.extend(x['id'] for x in mems if x['id']!=u['id'])
        elif name in names and names[name]!=u['id']: mentions.append(names[name])
    mentions=list(dict.fromkeys(mentions)); t=core.now_iso()
    with core.tx() as c:
        c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(body['body'],t,json.dumps(mentions),mid))
        core.emit_event(c,m['channel_id'],'message.updated',{'message':{'id':mid,'channel_id':m['channel_id'],'author_id':m['author_id'],'body':body['body'],'edited_at':t,'mentions':mentions}})
    return web.json_response({'message':{'id':mid,'channel_id':m['channel_id'],'author_id':m['author_id'],'body':body['body'],'edited_at':t,'mentions':mentions}})
async def delete_message(req):
    u=require_user(req); mid=int(req.match_info['id']); c=core.connect(); m=c.execute('SELECT m.*,w.owner_id FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN workspaces w ON w.id=ch.workspace_id WHERE m.id=?',(mid,)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    if m['author_id']!=u['id'] and m['owner_id']!=u['id']:return json_error(403,'forbidden')
    if not m['deleted']:
        with core.tx() as c:
            c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
            core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
    return web.json_response({'deleted':True})

if __name__=='__main__':
    core.init_db()
    ap=argparse.ArgumentParser(); ap.add_argument('--node',type=int,default=0); args=ap.parse_args(); NODE=args.node
    web.run_app(app(),host='127.0.0.1',port=8000+NODE)
