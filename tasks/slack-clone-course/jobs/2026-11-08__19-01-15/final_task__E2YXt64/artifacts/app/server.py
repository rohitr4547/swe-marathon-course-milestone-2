import argparse, asyncio, json, re, secrets
from aiohttp import web
import core
NODE=0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(request):
    try:
        x=await request.json()
        return x if isinstance(x,dict) else None
    except: return None
async def require_user(request):
    h=request.headers.get('Authorization','')
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized()
    return u
def user_json(r): return core.user_to_json(r)
def workspace_json(r):
    return {k:r[k] for k in ('id','slug','name','owner_id','join_mode')}
def channel_json(r):
    return {'id':r['id'],'workspace_id':r['workspace_id'],'name':r['name'],'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'topic':r['topic'] or '','is_archived':bool(r['is_archived'])}
def auth_exc(e):
    return json_error(401,'authentication required')
async def health(request): return web.json_response({'status':'ok','node_id':NODE})
async def register(request):
    b=await read_json(request)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:
        return json_error(400,'invalid registration')
    with core.tx() as c:
        try:
            cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
            u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); token=core.issue_token(c,u['id'])
        except Exception as e: return json_error(409,'username already exists')
    return web.json_response({'user':user_json(u),'token':token},status=201)
async def login(request):
    b=await read_json(request); u=None
    if b and isinstance(b.get('username'),str) and isinstance(b.get('password'),str): u=core.user_for_token('') or None
    c=core.connect()
    if b and isinstance(b.get('username'),str): u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone()
    if not u or not isinstance(b.get('password'),str) or not core.verify_password(b['password'],u['password']): c.close(); return json_error(401,'invalid credentials')
    token=core.issue_token(c,u['id']); c.close()
    return web.json_response({'user':user_json(u),'token':token})
async def me(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    return web.json_response({'user':user_json(u)})
async def workspaces(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w LEFT JOIN workspace_members m ON m.workspace_id=w.id AND m.user_id=? WHERE w.owner_id=? OR m.user_id IS NOT NULL',(u['id'],u['id'])).fetchall(); c.close()
    return web.json_response({'workspaces':[workspace_json(x) for x in rows]})
async def create_workspace(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    b=await read_json(request)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str) or not b['name']: return json_error(400,'invalid workspace')
    with core.tx() as c:
        try:
            wid=c.execute('INSERT INTO workspaces(slug,name,owner_id,created_at) VALUES(?,?,?,?)',(b['slug'],b['name'],u['id'],core.now_iso())).lastrowid
            chid=c.execute("INSERT INTO channels(workspace_id,name) VALUES(?, 'general')",(wid,)).lastrowid
            c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(wid,u['id'],'owner')); c.execute('INSERT INTO channel_members VALUES(?,?)',(chid,u['id']))
        except: return json_error(409,'workspace already exists')
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(chid,)).fetchone()
    return web.json_response({'workspace':workspace_json(w),'general_channel':channel_json(ch)},status=201)
async def workspace_detail(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(request.match_info['slug'],)).fetchone()
    if not w:return json_error(404,'workspace not found')
    member=c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
    if not member:return json_error(403,'not a member')
    rows=c.execute("SELECT c.* FROM channels c WHERE c.workspace_id=? AND (c.is_archived=0 OR ?) AND (c.is_private=0 OR EXISTS(SELECT 1 FROM channel_members m WHERE m.channel_id=c.id AND m.user_id=?))",(w['id'],request.query.get('include_archived') in ('1','true'),u['id'])).fetchall()
    states=[]
    for x in rows:
        rr=c.execute('SELECT last_read_event_id,mention_count FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone()
        last=rr['last_read_event_id'] if rr else 0
        ev2=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>?',(x['id'],last)).fetchall()
        unread=0
        for ee in ev2:
            try:
                zz=json.loads(ee['payload']); mm=zz.get('message',{})
                if zz.get('type')=='message.created' and mm.get('author_id')!=u['id']: unread+=1
            except: pass
        states.append({'channel_id':x['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':rr['mention_count'] if rr else 0})
    c.close()
    return web.json_response({'workspace':workspace_json(w),'channels':[channel_json(x) for x in rows],'read_state':states})
async def create_channel(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    b=await read_json(request); c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(request.match_info['slug'],)).fetchone()
    if not w:return json_error(404,'workspace not found')
    role=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
    if not role:return json_error(403,'not a member')
    if role['role']=='guest':return json_error(403,'forbidden')
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']) or (b.get('topic','') and (not isinstance(b['topic'],str) or len(b['topic'])>250)): return json_error(400,'invalid channel')
    if b.get('is_private') and role['role'] not in ('owner','admin'): return json_error(403,'forbidden')
    try:
        cid=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],b['name'],int(bool(b.get('is_private'))),b.get('topic',''))).lastrowid
        c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u['id'])); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.commit()
    except: c.close(); return json_error(409,'channel already exists')
    c.close(); return web.json_response({'channel':channel_json(r)},status=201)


async def patch_workspace(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(request.match_info['slug'],)).fetchone()
    if not w:return json_error(404,'workspace not found')
    role=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
    if not role or role['role'] not in ('owner','admin'):return json_error(403,'forbidden')
    b=await read_json(request) or {}
    if 'name' in b:
        if not isinstance(b['name'],str) or not b['name']:return json_error(400,'invalid name')
        c.execute('UPDATE workspaces SET name=? WHERE id=?',(b['name'],w['id']))
    if 'join_mode' in b:
        if b['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join mode')
        c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(b['join_mode'],w['id']))
    c.commit(); r=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone();c.close()
    return web.json_response({'workspace':workspace_json(r)})
async def patch_member(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(request.match_info['slug'],)).fetchone()
    if not w:return json_error(404,'workspace not found')
    caller=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
    target_id=int(request.match_info['user_id']); target=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],target_id)).fetchone()
    if not target:return json_error(404,'member not found')
    b=await read_json(request) or {}; nr=b.get('role')
    if nr not in ('owner','admin','member','guest'):return json_error(400,'invalid role')
    if target_id==w['owner_id'] or nr=='owner':return json_error(403 if target_id==w['owner_id'] else 400,'invalid role change')
    if not caller or caller['role'] not in ('owner','admin'):return json_error(403,'forbidden')
    if caller['role']=='admin' and target['role']=='admin':return json_error(403,'forbidden')
    c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',(nr,w['id'],target_id));c.commit();c.close()
    return web.json_response({'member':{'user_id':target_id,'role':nr}})
async def transfer_owner(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE slug=?',(request.match_info['slug'],)).fetchone()
    if not w:return json_error(404,'workspace not found')
    if u['id']!=w['owner_id']:return json_error(403,'owner only')
    b=await read_json(request) or {}; nid=b.get('new_owner_id')
    t=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],nid)).fetchone()
    if not t or t['role']!='admin':return json_error(400,'target must be admin')
    c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
    c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],nid))
    c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(nid,w['id']));c.commit();r=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone();c.close()
    return web.json_response({'workspace':workspace_json(r)})


async def invitations(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND uses<max_uses AND (expires_at IS NULL OR CAST(expires_at AS REAL)>?)',(w['id'],__import__('time').time())).fetchall();c.close()
    return web.json_response({'invitations':[core.invitation_to_json(x) for x in rows]})
async def create_invite(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    b=await read_json(request)
    r,st=core.create_invitation(u,request.match_info['slug'],b)
    if st!=201:return json_error(st,{400:'invalid invitation',403:'forbidden',404:'not found'}[st])
    return web.json_response({'invitation':core.invitation_to_json(r)},status=201)
async def accept_invite(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    w,st=core.accept_invitation(u,request.match_info['code'])
    if st!=200:return json_error(st,'not allowed' if st==403 else 'invitation not found')
    return web.json_response({'workspace':workspace_json(w)})
async def revoke_invite(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'workspace not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        r=c.execute('SELECT * FROM invitations WHERE code=? AND workspace_id=?',(request.match_info['code'],w['id'])).fetchone()
        if not r:return json_error(404,'invitation not found')
        c.execute('DELETE FROM invitations WHERE code=?',(r['code'],))
    return web.json_response({'deleted':True})

async def patch_channel(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    cid=int(request.match_info['id']); ch=core.get_channel(cid)
    if not ch:return json_error(404,'channel not found')
    if ch['is_archived']:return json_error(423,'channel archived')
    role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
    if not (role=='owner' or core.connect().execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()):return json_error(403,'forbidden')
    b=await read_json(request)
    if not b or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return json_error(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],cid))
        r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        if r['topic']!=ch['topic']: core.emit_event(c,cid,'channel.updated',{'channel':channel_json(r)})
    return web.json_response({'channel':channel_json(r)})
async def archive_channel(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    cid=int(request.match_info['id']);ch=core.get_channel(cid)
    if not ch:return json_error(404,'channel not found')
    if not ch['workspace_id'] or core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'owner only')
    archived=request.path.endswith('/archive')
    with core.tx() as c:
        if bool(ch['is_archived'])!=archived:
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(archived),cid))
            r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            core.emit_event(c,cid,'channel.archived' if archived else 'channel.unarchived',{'channel':channel_json(r)})
        else:r=ch
    return web.json_response({'channel':channel_json(r)})

async def workspace_members(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(request.match_info['slug'],)).fetchone()
    if not w:return json_error(404,'workspace not found')
    if not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone():return json_error(403,'not a member')
    rows=c.execute('SELECT wm.*,u.username,u.display_name FROM workspace_members wm JOIN users u ON u.id=wm.user_id WHERE wm.workspace_id=?',(w['id'],)).fetchall(); c.close()
    return web.json_response({'members':[{'user_id':x['user_id'],'role':x['role'],'username':x['username'],'display_name':x['display_name']} for x in rows]})
async def join_channel(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    cid=int(request.match_info['id']); c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch:return json_error(404,'channel not found')
    if ch['workspace_id'] and c.execute("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=? AND role='guest'",(ch['workspace_id'],u['id'])).fetchone(): c.close(); return json_error(403,'guest cannot join')
    if ch['is_private'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone(): c.close(); return json_error(403,'private channel')
    if ch['workspace_id']:
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); role=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],u['id'])).fetchone()
        if not role:
            if w['join_mode']!='open':c.close();return json_error(403,'invite required')
            core.add_workspace_member(c,w['id'],u['id'],'member')
    core.add_channel_member(c,cid,u['id']); c.commit(); c.close()
    return web.json_response({'channel':channel_json(ch)})
async def channel_members(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    return web.json_response({'members':[user_json(x) for x in rows]})
async def leave_channel(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not ch:return json_error(404,'channel not found')
    core.remove_channel_member(c,ch['id'],u['id']);c.commit();c.close()
    return web.json_response({'channel':channel_json(ch)})


async def messages(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    cid=int(request.match_info['id']);ch=core.get_channel(cid)
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    if request.method=='POST':
        b=await read_json(request)
        if not b or not isinstance(b.get('body'),str):return json_error(400,'invalid body')
        if b['body'].startswith('//'):
            r,st=core.create_message(u,cid,b['body'][1:],b.get('parent_id'),b.get('file_ids',[]))
        else:
            slash,slash_status=core.run_slash(u,ch,b['body'])
            if slash_status==400 or slash_status==403 or slash_status==404 or slash_status==423:
                return json_error(slash_status,slash.get('error','command failed'))
            if slash_status==201:
                if 'channel' in slash: slash['channel']=channel_json(core.get_channel(cid))
                return web.json_response(slash,status=201)
            if slash is not None:
                r,st=core.create_message(u,cid,slash['body'],b.get('parent_id'),b.get('file_ids',[]))
            else:
                r,st=core.create_message(u,cid,b['body'],b.get('parent_id'),b.get('file_ids',[]))
        if st!=201:return json_error(st,'invalid message')
        return web.json_response({'message':core.message_to_json(r)},status=201)
    if 'offset' in request.query or 'page' in request.query:return json_error(400,'offset pagination unsupported')
    try: limit=int(request.query.get('limit','50'))
    except: return json_error(400,'invalid limit')
    if limit<1 or limit>200:return json_error(400,'invalid limit')
    cur=request.query.get('cursor'); before=None
    if cur:
        try:
            raw=__import__('base64').urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)); obj=json.loads(raw)
            if obj.get('k')!='messages' or not isinstance(obj.get('v'),int):raise ValueError()
            before=obj['v']
        except: return json_error(400,'invalid cursor')
    c=core.connect()
    q='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0'
    args=[cid]
    if before is not None:q+=' AND id<?';args.append(before)
    q+=' ORDER BY id DESC LIMIT ?';args.append(limit+1)
    rows=c.execute(q,args).fetchall();c.close(); more=len(rows)>limit
    if more:rows=rows[:limit]
    nxt=None
    if more:nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'messages','v':rows[-1]['id']}).encode()).decode().rstrip('=')
    return web.json_response({'messages':[core.message_to_json(x) for x in rows],'next_cursor':nxt})
async def replies(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect();p=c.execute('SELECT * FROM messages WHERE id=?',(int(request.match_info['id']),)).fetchone()
    if not p:return json_error(404,'message not found')
    ch=core.get_channel(p['channel_id'])
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    try:n,last=page_args(request.query,'replies')
    except:return json_error(400,'invalid paging')
    q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';args=[p['id']]
    if last is not None:q+=' AND id>?';args.append(last)
    q+=' ORDER BY id LIMIT ?';args.append(n+1)
    rows=c.execute(q,args).fetchall();c.close();more=len(rows)>n;rows=rows[:n]
    nxt=None
    if more:nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'replies','v':rows[-1]['id']}).encode()).decode().rstrip('=')
    return web.json_response({'replies':[core.message_to_json(x) for x in rows],'next_cursor':nxt})


def page_args(q,kind):
    if 'offset' in q or 'page' in q: raise ValueError()
    try:n=int(q.get('limit','50'))
    except: raise ValueError()
    if n<1 or n>200:raise ValueError()
    cur=q.get('cursor'); last=None
    if cur:
        try:
            raw=__import__('base64').urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4));o=json.loads(raw)
            if o.get('k')!=kind or not isinstance(o.get('v'),int):raise ValueError()
            last=o['v']
        except:raise ValueError()
    return n,last
async def pin_route(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    r,st=core.pin_message(u,int(request.match_info['id']))
    return json_error(st,'not allowed') if st!=200 else web.json_response({'pin':r})
async def unpin_route(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    r,st=core.unpin_message(u,int(request.match_info['id']))
    return json_error(st,'not allowed') if st!=200 else web.json_response({'unpinned':True})
async def pins_route(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    cid=int(request.match_info['id']);ch=core.get_channel(cid)
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    try:n,last=page_args(request.query,'pin')
    except:return json_error(400,'invalid paging')
    c=core.connect();q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';a=[cid]
    if last is not None:q+=' AND p.message_id<?';a.append(last)
    q+=' ORDER BY p.message_id DESC LIMIT ?';a.append(n+1);rows=c.execute(q,a).fetchall();c.close();more=len(rows)>n;rows=rows[:n]
    nxt=None
    if more:nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'pin','v':rows[-1]['message_id']}).encode()).decode().rstrip('=')
    return web.json_response({'pins':[{'message':core.message_to_json(r),'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']} for r in rows],'next_cursor':nxt})
async def search_route(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    q=request.query.get('q')
    if not isinstance(q,str) or not q:return json_error(400,'q required')
    workspace_filter=request.query.get('workspace_id')
    try:n,last=page_args(request.query,'srch')
    except:return json_error(400,'invalid paging')
    c=core.connect();rows=c.execute("SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.deleted=0 AND lower(m.body) LIKE lower(?) ORDER BY m.id DESC",(f'%{q}%',)).fetchall();out=[]
    for r in rows:
        ch=c.execute('SELECT * FROM channels WHERE id=?',(r['channel_id'],)).fetchone()
        if workspace_filter is not None and str(ch['workspace_id'])!=workspace_filter: continue
        if (last is None or r['id']<last) and core.can_read_channel(u['id'],ch):out.append(r)
    more=len(out)>n;out=out[:n];c.close();nxt=None
    if more:nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'srch','v':out[-1]['id']}).encode()).decode().rstrip('=')
    return web.json_response({'results':[core.message_to_json(r) for r in out],'next_cursor':nxt})


async def read_state(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    cid=int(request.match_info['id']);ch=core.get_channel(cid)
    if not ch:return json_error(404,'channel not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    c=core.connect()
    if request.method=='POST':
        b=await read_json(request)
        if not b or not isinstance(b.get('last_read_event_id'),int) or isinstance(b.get('last_read_event_id'),bool):c.close();return json_error(400,'invalid event id')
        old=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone()
        n=max(old['last_read_event_id'] if old else 0,b['last_read_event_id'])
        c.execute('INSERT INTO read_state(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=?',(u['id'],cid,n,n));c.commit()
    r=c.execute('SELECT last_read_event_id,mention_count FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone();last=r['last_read_event_id'] if r else 0
    ev=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>?',(cid,last)).fetchall()
    total=0; mentions=0
    for e in ev:
        try:
            z=json.loads(e['payload']); m=z.get('message',{})
            if z.get('type') != 'message.created': continue
            if m.get('author_id')==u['id']: continue
            total+=1
            if u['id'] in (m.get('mentions') or []): mentions+=1
        except: continue
    c.close()
    return web.json_response({'read_state':{'channel_id':cid,'last_read_event_id':last,'unread_count':total,'mention_count':mentions}})


async def reaction_add(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    b=await read_json(request)
    if not b or not isinstance(b.get('emoji'),str):return json_error(400,'invalid emoji')
    r,st=core.change_reaction(u,int(request.match_info['id']),b['emoji'],True)
    return json_error(st,'reaction failed') if st!=200 else web.json_response({'reactions':r})
async def reaction_del(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    r,st=core.change_reaction(u,int(request.match_info['id']),request.match_info['emoji'],False)
    return json_error(st,'reaction failed') if st!=200 else web.json_response({'reactions':r})


async def message_update(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    b=await read_json(request)
    if not b or not isinstance(b.get('body'),str):return json_error(400,'invalid body')
    r,st=core.edit_message(u,int(request.match_info['id']),b['body'])
    return json_error(st,'message update failed') if st!=200 else web.json_response({'message':core.message_to_json(r)})
async def message_delete(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    r,st=core.delete_message(u,int(request.match_info['id']))
    return json_error(st,'message delete failed') if st!=200 else web.json_response({'deleted':True})



async def dms_route(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    b=await read_json(request)
    if not b or not isinstance(b.get('user_id'),int) or isinstance(b.get('user_id'),bool):return json_error(400,'invalid user_id')
    cid,st=core.open_dm(u,b['user_id'])
    return json_error(st,'user not found' if st==404 else 'invalid') if st!=200 else web.json_response({'channel_id':cid})
async def files_route(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    try:
        rd=await request.multipart(); part=await rd.next()
        if not part or part.name!='file':return json_error(400,'file required')
        data=await part.read()
    except:return json_error(400,'invalid upload')
    r,st=core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)
    return json_error(st,'file too large' if st==413 else 'upload failed') if st!=201 else web.json_response({'file':core.file_to_json(r)},status=201)
async def file_get(request):
    try:await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(request.match_info['id']),)).fetchone();c.close()
    if not r:return json_error(404,'file not found')
    if request.path.endswith('/download'):
        return web.Response(body=open('/app/data/files/'+str(r['id']),'rb').read(),content_type=r['content_type'],headers={'Content-Disposition':'attachment; filename="'+r['filename']+'"'})
    return web.json_response({'file':core.file_to_json(r)})


async def groups_route(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    slug=request.match_info['slug'];w=core.get_workspace(slug)
    if not w:return json_error(404,'workspace not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    if request.method=='GET':
        c=core.connect();rs=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
        return web.json_response({'groups':[core.group_to_json(r) for r in rs]})
    b=await read_json(request);r,st=core.create_group(u,slug,b)
    return json_error(st,'group error') if st!=201 else web.json_response({'group':core.group_to_json(r)},status=201)
async def group_item(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    b=await read_json(request) if request.method=='PATCH' else None
    r,st=core.update_group(u,request.match_info['slug'],request.match_info['handle'],b,request.method=='DELETE')
    if st!=200:return json_error(st,'group error')
    return web.json_response({'deleted':True} if request.method=='DELETE' else {'group':core.group_to_json(r)})


async def user_get(request):
    try:await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    c=core.connect();r=c.execute('SELECT * FROM users WHERE id=?',(int(request.match_info['id']),)).fetchone();c.close()
    return json_error(404,'user not found') if not r else web.json_response({'user':user_json(r)})
async def profile_patch(request):
    try:u=await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    b=await read_json(request);r,st=core.update_profile(u,b)
    return json_error(st,'invalid profile') if st!=200 else web.json_response({'user':user_json(r)})

async def c_missing(request):
    try: await require_user(request)
    except web.HTTPException:return json_error(401,'authentication required')
    return json_error(404,'not found')

async def ws_handler(request):
    token=request.query.get('token','')
    u=core.user_for_token(token)
    if not u:return json_error(401,'authentication required')
    ws=web.WebSocketResponse()
    conn_id=secrets.token_urlsafe(12)
    core.touch_presence(conn_id,u['id'])
    await ws.prepare(request)
    subs={}; global_since=0
    async def poll():
        try:
            while not ws.closed:
                nonlocal global_since
                for ge in core.globals_after(global_since):
                    await ws.send_str(ge['payload']); global_since=ge['id']
                for cid,last in list(subs.items()):
                    c=core.connect()
                    rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
                    head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()
                    c.close()
                    for r in rows:
                        await ws.send_str(r['payload']); subs[cid]=r['event_id']
                await asyncio.sleep(.05)
        except (asyncio.CancelledError,ConnectionResetError): pass
    task=asyncio.create_task(poll())
    try:
        async for msg in ws:
            if msg.type!=web.WSMsgType.TEXT: continue
            try:b=json.loads(msg.data)
            except: continue
            if b.get('type')=='subscribe':
                cid=b.get('channel_id'); ch=core.get_channel(cid)
                if not ch or not core.can_read_channel(u['id'],ch): continue
                c=core.connect(); head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()['last_event_id'];c.close()
                subs[cid]=head
                await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
            elif b.get('type')=='resume':
                cid=b.get('channel_id'); since=b.get('since_event_id',0); ch=core.get_channel(cid)
                if not ch or not core.can_read_channel(u['id'],ch): continue
                c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); head=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()['last_event_id'];c.close()
                for r in rows: await ws.send_str(r['payload'])
                subs[cid]=head
                await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':head})
    finally:
        task.cancel(); core.drop_presence(conn_id)
    return ws

def app():
    a=web.Application(); a.router.add_get('/',lambda request:web.FileResponse('/app/static/index.html'))
    a.router.add_get('/api/health',health); a.router.add_post('/api/dms',dms_route); a.router.add_post('/api/files',files_route); a.router.add_get('/api/files/{id}',file_get); a.router.add_get('/api/files/{id}/download',file_get); a.router.add_get('/api/users/{id}',user_get); a.router.add_patch('/api/users/me',profile_patch); a.router.add_route('*','/api/workspaces/{slug}/groups',groups_route); a.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',group_item);  a.router.add_post('/api/auth/register',register); a.router.add_post('/api/auth/login',login); a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces); a.router.add_post('/api/workspaces',create_workspace); a.router.add_get('/api/workspaces/{slug}',workspace_detail); a.router.add_patch('/api/channels/{id}',patch_channel); a.router.add_post('/api/channels/{id}/archive',archive_channel); a.router.add_post('/api/channels/{id}/unarchive',archive_channel); a.router.add_post('/api/workspaces/{slug}/channels',create_channel); a.router.add_patch('/api/workspaces/{slug}',patch_workspace); a.router.add_post('/api/workspaces/{slug}/invitations',create_invite); a.router.add_get('/api/workspaces/{slug}/invitations',invitations); a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_invite); a.router.add_post('/api/invitations/{code}/accept',accept_invite); a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',patch_member); a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer_owner); a.router.add_get('/api/workspaces/{slug}/members',workspace_members); a.router.add_post('/api/channels/{id}/join',join_channel); a.router.add_get('/api/channels/{id}/members',channel_members); a.router.add_post('/api/channels/{id}/leave',leave_channel)
    a.router.add_route('*','/api/channels/{id}/messages',messages); a.router.add_get('/api/messages/{id}/replies',replies); a.router.add_post('/api/messages/{id}/pin',pin_route); a.router.add_patch('/api/messages/{id}',message_update); a.router.add_delete('/api/messages/{id}',message_delete); a.router.add_delete('/api/messages/{id}/pin',unpin_route); a.router.add_get('/api/channels/{id}/pins',pins_route); a.router.add_get('/api/search',search_route); a.router.add_route('*','/api/channels/{id}/read',read_state); a.router.add_post('/api/messages/{id}/reactions',reaction_add); a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction_del); a.router.add_get('/api/ws',ws_handler)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--node',type=int,default=0); NODE=p.parse_args().node
    core.init_db(); web.run_app(app(),host='127.0.0.1',port=8000+NODE)
