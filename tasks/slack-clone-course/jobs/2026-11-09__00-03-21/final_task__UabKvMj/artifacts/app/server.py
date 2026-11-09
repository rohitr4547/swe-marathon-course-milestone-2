import asyncio,json,re,sys,os,secrets
from aiohttp import web,WSMsgType
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
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
def user(u): return core.user_to_json(u)


async def message_edit_delete(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    try: mid=int(req.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not m:c.close();return json_error(404,'not found')
    ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
    w=c.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch['workspace_id'] else None
    if req.method=='PATCH':
        if m['deleted']:c.close();return json_error(409,'deleted')
        if m['author_id']!=u['id']:c.close();return json_error(403,'forbidden')
        b=await read_json(req)
        if not b or not isinstance(b.get('body'),str) or not b['body'].strip():c.close();return json_error(400,'invalid body')
        with core.tx() as x:
            mentions=core.resolve_mentions(x,b['body'],ch,u['id'])
            x.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(b['body'],core.now_iso(),json.dumps(mentions),mid))
            m=x.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            core.emit_event(x,ch['id'],'message.updated',{'message':msg_json(m,x)})
        c.close();return web.json_response({'message':msg_json(m)})
    if m['deleted']:c.close();return web.json_response({'deleted':True})
    if m['author_id']!=u['id'] and (not w or w['owner_id']!=u['id']):c.close();return json_error(403,'forbidden')
    with core.tx() as x:
        x.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
        core.emit_event(x,ch['id'],'message.deleted',{'message_id':mid})
    c.close();return web.json_response({'deleted':True})
async def user_profile(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    if req.path == '/api/users/me' or req.match_info.get('id')=='me':
        target=u
    else:
        try: uid=int(req.match_info['id'])
        except:return json_error(404,'not found')
        c=core.connect();target=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close()
        if not target:return json_error(404,'not found')
    if req.method=='GET':return web.json_response({'user':core.user_to_json(target)})
    if target['id']!=u['id']:return json_error(403,'forbidden')
    b=await read_json(req)
    if b is None:return json_error(400,'invalid body')
    allowed=('display_name','timezone','avatar_url','status_text','status_emoji')
    if any(k not in allowed for k in b):return json_error(400,'invalid field')
    if 'timezone' in b:
        try: __import__('zoneinfo').ZoneInfo(b['timezone'])
        except: return json_error(400,'invalid timezone')
    limits={'display_name':80,'avatar_url':512,'status_text':140,'status_emoji':32,'timezone':100}
    for k,v in b.items():
        if not isinstance(v,str) or len(v)>limits[k] or (k=='display_name' and len(v)<1):return json_error(400,'invalid field')
    with core.tx() as c:
        if b:
            sets=','.join(k+'=?' for k in b);c.execute('UPDATE users SET '+sets+' WHERE id=?',list(b.values())+[u['id']])
        target=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        core.emit_global(c,{'type':'user.updated','user':core.user_to_json(target)})
    return web.json_response({'user':core.user_to_json(target)})
async def health(req): return web.json_response({'status':'ok','node_id':NODE})
async def register(req):
    b=await read_json(req)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return json_error(400,'invalid input')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone(): return json_error(409,'username exists')
        c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username']))
        u=c.execute('SELECT * FROM users WHERE id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
    return web.json_response({'user':user(u),'token':t},status=201)
async def login(req):
    b=await read_json(req); u=None
    if b and isinstance(b.get('username'),str):
        c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone();c.close()
    if not u or not isinstance(b.get('password'),str) or not core.verify_password(b['password'],u['password']): return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return web.json_response({'user':user(u),'token':t})
async def me(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    return web.json_response({'user':user(u)})
async def workspaces(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_workspace(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    b=await read_json(req)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str) or not b['name']:return json_error(400,'invalid input')
    try:
        with core.tx() as c:
            c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']))
            w=c.execute('SELECT * FROM workspaces WHERE id=last_insert_rowid()').fetchone()
            g=core.create_channel_row(c,w['id'],'general',False,'',u['id']);core.add_workspace_member(c,w['id'],u['id'],'owner')
    except Exception:return json_error(409,'workspace exists')
    return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(g)},status=201)
async def workspace(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT c.* FROM channels c WHERE c.workspace_id=? AND (c.is_archived=0 OR ?) AND (c.is_private=0 OR EXISTS (SELECT 1 FROM channel_members cm WHERE cm.channel_id=c.id AND cm.user_id=?))',(w['id'],req.query.get('include_archived') in ('1','true'),u['id'])).fetchall();c.close()
    return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rs],'read_state':[rs_json(core.connect(),u['id'],x['id']) for x in rs]})

async def workspace_members(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT m.*,u.* FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall(); c.close()
    return web.json_response({'members':[{'user_id':r['user_id'],'role':r['role'],'user':core.user_to_json(r)} for r in rows]})

async def change_role(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return json_error(403,'forbidden')
    target=int(req.match_info['user_id']); b=await read_json(req)
    c=core.connect(); old=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone();c.close()
    if not old:return json_error(404,'not found')
    new=b.get('role') if b else None
    if not isinstance(new,str) or new not in ('admin','member','guest'):return json_error(400,'invalid role')
    if target==w['owner_id'] or (role=='admin' and old['role'] in ('owner','admin')):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?',(new,w['id'],target))
    return web.json_response({'member':{'user_id':target,'role':new}})

async def transfer(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if u['id']!=w['owner_id']:return json_error(403,'forbidden')
    b=await read_json(req); target=b.get('new_owner_id') if b else None
    c=core.connect(); r=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(w['id'],target)).fetchone();c.close()
    if not r or r['role']!='admin':return json_error(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE memberships SET role=CASE WHEN user_id=? THEN ? ELSE role END WHERE workspace_id=?',(u['id'],'admin',w['id']))
        c.execute('UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return web.json_response({'workspace':core.workspace_to_json(w)})


async def workspace_patch(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    b=await read_json(req)
    if not b:return json_error(400,'invalid body')
    if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join mode')
    with core.tx() as c:
        if 'name' in b and (not isinstance(b['name'],str) or not b['name']):return json_error(400,'invalid name')
        if 'name' in b:c.execute('UPDATE workspaces SET name=? WHERE id=?',(b['name'],w['id']))
        if 'join_mode' in b:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(b['join_mode'],w['id']))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return web.json_response({'workspace':core.workspace_to_json(w)})

async def invitations(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    if req.method=='GET':
        c=core.connect(); rows=c.execute("SELECT * FROM invitations WHERE workspace_id=? AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)",(w['id'],core.now_iso())).fetchall();c.close()
        return web.json_response({'invitations':[dict(x) for x in rows]})
    b=await read_json(req)
    if b is None:return json_error(400,'invalid body')
    for k in ('expires_in_seconds','max_uses','invited_user_id'):
        if k in b and not isinstance(b[k],int):return json_error(400,'invalid input')
    uid=b.get('invited_user_id')
    if uid is None and isinstance(b.get('invited_username'),str):
        c=core.connect();x=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone();c.close();uid=x['id'] if x else None
    code=__import__('secrets').token_urlsafe(12)
    exp=None if not b.get('expires_in_seconds') else __import__('datetime').datetime.fromtimestamp(__import__('time').time()+b['expires_in_seconds'],__import__('datetime').timezone.utc).isoformat().replace('+00:00','Z')
    with core.tx() as c:c.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,expires_at,max_uses) VALUES(?,?,?,?,?)',(code,w['id'],uid,exp,1 if uid is not None else b.get('max_uses',1)))
    return web.json_response({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':1 if uid is not None else b.get('max_uses',1),'uses':0,'invited_user_id':uid,'expires_at':exp}},status=201)

async def accept_inv(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    code=req.match_info['code']; c=core.connect(); inv=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone();c.close()
    if not inv or inv['uses']>=inv['max_uses'] or (inv['expires_at'] and inv['expires_at']<=core.now_iso()):return json_error(404,'not found')
    if inv['invited_user_id'] and inv['invited_user_id']!=u['id']:return json_error(403,'forbidden')
    with core.tx() as c:
        core.add_workspace_member(c,inv['workspace_id'],u['id'],'member');c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(inv['workspace_id'],)).fetchone()
    return web.json_response({'workspace':core.workspace_to_json(w)})

async def revoke_inv(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        x=c.execute('DELETE FROM invitations WHERE workspace_id=? AND code=?',(w['id'],req.match_info['code'],))
    return web.json_response({'deleted':True}) if x.rowcount else json_error(404,'not found')
async def create_channel(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return json_error(403,'forbidden')
    b=await read_json(req)
    if role=='guest' or (bool((b or {}).get('is_private',False)) and role not in ('owner','admin')): return json_error(403,'forbidden')
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']) or ('topic' in b and (not isinstance(b['topic'],str) or len(b['topic'])>250)):return json_error(400,'invalid name')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return json_error(409,'exists')
        ch=core.create_channel_row(c,w['id'],b['name'],bool(b.get('is_private',False)),b.get('topic',''),u['id'])
    return web.json_response({'channel':core.channel_to_json(ch)},status=201)
async def join(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    existing=core.member_role(ch['workspace_id'],u['id'])
    c0=core.connect(); joined=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); w0=c0.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c0.close()
    if joined: return web.json_response({'channel':core.channel_to_json(ch)})
    if ch['is_private'] or not w0 or (w0['join_mode']=='invite_only' and not existing): return json_error(403,'forbidden')
    if existing=='guest': return json_error(403,'forbidden')
    with core.tx() as c:
        core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
        if core.add_channel_member(c,ch['id'],u['id']): core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
    return web.json_response({'channel':core.channel_to_json(ch)})

async def channel_members(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    c=core.connect(); ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); rows=c.execute('SELECT u.* FROM channel_members m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    if not ok and ch['is_private']:return json_error(403,'forbidden')
    return web.json_response({'members':[core.user_to_json(x) for x in rows]})
async def channel_leave(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    with core.tx() as c:
        x=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
        if x.rowcount: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return web.json_response({'channel':core.channel_to_json(ch)})
async def channel_patch(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    if ch['is_archived']: return json_error(423,'archived')
    b=await read_json(req)
    if not b or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return json_error(400,'invalid topic')
    role=core.member_role(ch['workspace_id'],u['id'])
    c0=core.connect(); member=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); w0=c0.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c0.close()
    if not member and (not w0 or w0['owner_id']!=u['id']):return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],ch['id']))
        core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return web.json_response({'channel':core.channel_to_json(ch)})
async def archive(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return json_error(404,'not found')
    c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
    if not w or w['owner_id']!=u['id']:return json_error(403,'forbidden')
    with core.tx() as c:
        val=0 if req.path.endswith('unarchive') else 1
        if bool(ch['is_archived']) != bool(val):
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
            core.emit_event(c,ch['id'],'channel.unarchived' if not val else 'channel.archived',{'channel':core.channel_to_json(c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone())})
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return web.json_response({'channel':core.channel_to_json(ch)})
def msg_json(r, conn=None):
    own=conn is None
    c=conn or core.connect()
    a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    replies=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    reaction_rows=c.execute('SELECT emoji,count(*) n,json_group_array(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY min(rowid)',(r['id'],)).fetchall()
    file_rows=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall()
    if own: c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':core.user_to_json(a) if a else None,
            'body':r['body'],'parent_id':r['parent_id'],'reply_count':replies,'created_at':r['created_at'],
            'edited_at':r['edited_at'],'files':[file_obj(z) for z in file_rows],'reactions':[{'emoji':z['emoji'],'count':z['n'],'user_ids':json.loads(z['ids'])} for z in reaction_rows],'mentions':json.loads(r['mentions'] or '[]') if 'mentions' in r.keys() else []}

def page_params(req, kind='messages'):
    if 'page' in req.query or 'offset' in req.query:return None,None,'offset pagination unsupported'
    try:n=int(req.query.get('limit','50'))
    except:return None,None,'invalid limit'
    if n<1 or n>200:return None,None,'invalid limit'
    cur=req.query.get('cursor')
    if not cur:return n,None,None
    try:
        raw=__import__('base64').urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)); x=json.loads(raw)
        if not isinstance(x,dict) or x.get('k')!=kind or not isinstance(x.get('v'),int):raise ValueError()
        return n,x['v'],None
    except:return None,None,'invalid cursor'

async def messages(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    try:cid=int(req.match_info['id'])
    except:return json_error(404,'not found')
    ch=core.get_channel(cid)
    if not ch:return json_error(404,'not found')
    c=core.connect()
    member=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
    role=c.execute('SELECT role FROM memberships WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone()
    allowed=bool(member) or (not ch['is_private'] and role and role['role']!='guest')
    if not allowed:c.close();return json_error(403,'forbidden')
    if req.method=='POST':
        b=await read_json(req)
        if not b or not isinstance(b.get('body'),str) or not b['body'].strip():c.close();return json_error(400,'empty body')
        body=b['body']
        if ch['is_archived'] and body not in ('/unarchive',): c.close();return json_error(423,'archived')
        if body.startswith('//'):
            b['body']=body[1:]
        elif body.startswith('/me'):
            if body == '/me' or not body.startswith('/me ') or not body[4:].strip(): c.close(); return json_error(400,'/me requires text')
        elif body.startswith('/shrug'):
            rest=body[6:].strip()
            b['body']=(rest+' ' if rest else '')+'¯\\_(ツ)_/¯'
        elif body.startswith('/invite '):
            target=body[8:].strip()
            if not target.startswith('@'): c.close();return json_error(400,'invalid invite')
            target=target[1:]
            uu=c.execute('SELECT * FROM users WHERE username=?',(target,)).fetchone()
            if not uu:c.close();return json_error(404,'user not found')
            with core.tx() as x: core.add_channel_member(x,cid,uu['id'])
            c.close();return web.json_response({'message':None,'invited_user':core.user_to_json(uu)},status=201)
        elif body.startswith('/topic '):
            topic=body[7:].strip()
            if not topic or len(topic)>250:c.close();return json_error(400,'invalid topic')
            with core.tx() as x:
                x.execute('UPDATE channels SET topic=? WHERE id=?',(topic,cid))
                ch2=x.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                core.emit_event(x,cid,'channel.updated',{'channel':core.channel_to_json(ch2)})
            c.close();return web.json_response({'message':None,'channel':core.channel_to_json(ch2)},status=201)
        elif body in ('/archive','/unarchive'):
            c0=core.connect();ww=c0.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c0.close()
            if not ww or ww['owner_id']!=u['id']:c.close();return json_error(403,'forbidden')
            val=1 if body=='/archive' else 0
            with core.tx() as x:
                x.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,cid))
                ch2=x.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
            c.close();return web.json_response({'message':None,'channel':core.channel_to_json(ch2)},status=201)
        elif body.startswith('/') and not body.startswith('//'):
            c.close();return json_error(400,'unknown command')
        parent=b.get('parent_id')
        file_ids=b.get('file_ids',[])
        if not isinstance(file_ids,list) or any(type(x) is not int for x in file_ids): c.close(); return json_error(400,'invalid files')
        if file_ids:
            marks=','.join('?'*len(file_ids))
            fr=c.execute(f'SELECT * FROM files WHERE id IN ({marks})',file_ids).fetchall()
            if len(fr)!=len(set(file_ids)) or any(x['uploader_id']!=u['id'] or x['message_id'] is not None for x in fr): c.close(); return json_error(400,'invalid files')
        if parent is not None and (not isinstance(parent,int) or not c.execute('SELECT 1 FROM messages WHERE id=? AND channel_id=? AND parent_id IS NULL AND deleted=0',(parent,cid)).fetchone()):
            c.close();return json_error(400,'invalid parent')
        with core.tx() as x:
            mentions=core.resolve_mentions(x,b['body'],ch,u['id'])
            x.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,u['id'],b['body'],parent,core.now_iso(),json.dumps(mentions)))
            m=x.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone()
            if file_ids:
                x.execute('UPDATE files SET message_id=? WHERE id IN ('+','.join('?'*len(file_ids))+')',[m['id']]+file_ids)
                m=x.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone()
            core.emit_event(x,cid,'message.reply' if parent else 'message.created',{'message':msg_json(m,x)})
        c.close();return web.json_response({'message':msg_json(m)},status=201)
    n,cur,err=page_params(req)
    if err:c.close();return json_error(400,err)
    q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'
    args=[cid]
    if cur is not None:q+=' AND id<?';args.append(cur)
    q+=' ORDER BY id DESC LIMIT ?';args.append(n+1)
    rs=c.execute(q,args).fetchall();c.close()
    more=len(rs)>n;rs=rs[:n]
    nxt=None
    if more:
        raw=json.dumps({'k':'messages','v':rs[-1]['id']}).encode()
        nxt=__import__('base64').urlsafe_b64encode(raw).decode().rstrip('=')
    return web.json_response({'messages':[msg_json(x) for x in rs],'next_cursor':nxt})

async def replies(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    mid=int(req.match_info['id']); c=core.connect(); parent=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not parent:c.close();return json_error(404,'not found')
    ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(parent['channel_id'],u['id'])).fetchone()
    if not ok:c.close();return json_error(403,'forbidden')
    n,cur,err=page_params(req)
    if err:c.close();return json_error(400,err)
    q='SELECT * FROM messages WHERE parent_id=? AND deleted=0'
    args=[mid]
    if cur is not None:
        q+=' AND id>?'
        args.append(cur)
    q+=' ORDER BY id ASC LIMIT ?'
    args.append(n+1)
    rs=c.execute(q,args).fetchall();c.close()
    more=len(rs)>n
    rs=rs[:n]
    nxt=None
    if more:
        raw=json.dumps({'k':'messages','v':rs[-1]['id']}).encode()
        nxt=__import__('base64').urlsafe_b64encode(raw).decode().rstrip('=')
    return web.json_response({'replies':[msg_json(x) for x in rs],'next_cursor':nxt})




def group_json(c,r):
    ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY rowid',(r['id'],)).fetchall()]
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
async def groups(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect()
    if req.method=='GET':
        rs=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall()
        out=[group_json(c,r) for r in rs];c.close();return web.json_response({'groups':out})
    b=await read_json(req)
    if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(b.get('user_ids',[]),list) or any(type(x) is not int for x in b.get('user_ids',[])):return json_error(400,'invalid group')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(w['id'],b['handle'])).fetchone():return json_error(409,'exists')
        ids=b.get('user_ids',[])
        if ids:
            q=','.join('?'*len(ids)); count=c.execute(f'SELECT count(*) n FROM memberships WHERE workspace_id=? AND user_id IN ({q})',[w['id']]+ids).fetchone()['n']
            if count!=len(set(ids)):return json_error(400,'invalid member')
        c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],b['handle'],b['name'],u['id']))
        r=c.execute('SELECT * FROM groups WHERE id=last_insert_rowid()').fetchone()
        c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],))
        for uid in ids:
            c.execute('INSERT OR IGNORE INTO group_members(group_id,user_id) VALUES(?,?)',(r['id'],uid))
        out=group_json(c,r)
    return web.json_response({'group':out},status=201)
async def group_item(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone()
    if not r:
        c.close()
        return web.json_response({'deleted':True}) if req.method=='DELETE' else json_error(404,'not found')
    if req.method=='DELETE':
        if r['creator_id']!=u['id'] and w['owner_id']!=u['id']:c.close();return json_error(403,'forbidden')
        c.close()
        with core.tx() as x:x.execute('DELETE FROM groups WHERE id=?',(r['id'],))
        return web.json_response({'deleted':True})
    if r['creator_id']!=u['id'] and w['owner_id']!=u['id']:c.close();return json_error(403,'forbidden')
    b=await read_json(req)
    if not b:c.close();return json_error(400,'invalid body')
    with core.tx() as x:
        if 'name' in b and not isinstance(b['name'],str):return json_error(400,'invalid name')
        if 'name' in b:x.execute('UPDATE groups SET name=? WHERE id=?',(b['name'],r['id']))
        if 'user_ids' in b:
            if not isinstance(b['user_ids'],list) or any(type(z) is not int for z in b['user_ids']):return json_error(400,'invalid users')
            x.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],))
            for z in b['user_ids']:x.execute('INSERT INTO group_members VALUES(?,?)',(r['id'],z))
        r=x.execute('SELECT * FROM groups WHERE id=?',(r['id'],)).fetchone();out=group_json(x,r)
    return web.json_response({'group':out})
async def dms(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    b=await read_json(req); other=b.get('user_id') if b else None
    if type(other) is not int:return json_error(400,'invalid user_id')
    if not core.user_for_token(next(iter([]),'')):
        pass
    c=core.connect(); x=c.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone()
    if not x:c.close();return json_error(404,'user not found')
    if other==u['id']:c.close();return json_error(400,'invalid user_id')
    a,z=sorted((u['id'],other)); old=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,z)).fetchone();c.close()
    if old:return web.json_response({'channel_id':old['channel_id']})
    with core.tx() as c:
        c.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',('',1))
        cid=c.execute('SELECT id FROM channels WHERE id=last_insert_rowid()').fetchone()['id']
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,z,cid));c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,z))
    return web.json_response({'channel_id':cid})

def file_obj(r):
    return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}

async def upload(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    try:
        part=await (await req.multipart()).next()
        data=await part.read()
    except:return json_error(400,'invalid file')
    if len(data)>10485760:return json_error(413,'file too large')
    with core.tx() as c:
        c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),len(data),core.now_iso()))
        r=c.execute('SELECT * FROM files WHERE id=last_insert_rowid()').fetchone()
    os.makedirs('/app/data/files',exist_ok=True);open('/app/data/files/'+str(r['id']),'wb').write(data)
    return web.json_response({'file':file_obj(r)},status=201)

async def file_get(req):
    try:require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    if req.path.endswith('/download'):return web.Response(body=open('/app/data/files/'+str(r['id']),'rb').read(),content_type=r['content_type'],headers={'Content-Disposition':f'attachment; filename="{r["filename"]}"'})
    return web.json_response({'file':file_obj(r)})
async def reaction(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    mid=int(req.match_info['id']); c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not m:c.close();return json_error(404,'not found')
    ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone()
    if not ok:c.close();return json_error(403,'forbidden')
    emoji=req.match_info.get('emoji')
    if req.method=='POST':
        b=await read_json(req); emoji=b.get('emoji') if b else None
        if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
    if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
    with core.tx() as x:
        exists=x.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
        if (req.method=='POST' and not exists):
            x.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
            core.emit_event(x,m['channel_id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
        elif req.method=='DELETE' and exists:
            x.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
            core.emit_event(x,m['channel_id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
        rows=x.execute('SELECT emoji,count(*) n,json_group_array(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY min(rowid)',(mid,)).fetchall()
    return web.json_response({'reactions':[{'emoji':z['emoji'],'count':z['n'],'user_ids':json.loads(z['ids'])} for z in rows]})
async def pin(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    mid=int(req.match_info['id']); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not m:c.close();return json_error(404,'not found')
    ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],u['id'])).fetchone()
    if not ok:c.close();return json_error(403,'forbidden')
    with core.tx() as x:
        x.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(mid,u['id'],core.now_iso()))
        r=x.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    return web.json_response({'pin':{'message_id':r['message_id'],'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']}})
async def unpin(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    mid=int(req.match_info['id'])
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
    return web.json_response({'unpinned':True})
async def pins(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    cid=int(req.match_info['id']);c=core.connect();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not ch:c.close();return json_error(404,'not found')
    ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
    if ch['is_private'] and not ok:c.close();return json_error(403,'forbidden')
    n,cur,err=page_params(req,'pins')
    if err:c.close();return json_error(400,err)
    q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';a=[cid]
    if cur is not None:q+=' AND p.message_id<?';a.append(cur)
    q+=' ORDER BY p.message_id DESC LIMIT ?';a.append(n+1);rs=c.execute(q,a).fetchall();c.close()
    more=len(rs)>n;rs=rs[:n]; nxt=None
    if more:nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'pins','v':rs[-1]['message_id']}).encode()).decode().rstrip('=')
    return web.json_response({'pins':[{'message':msg_json(r),'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']} for r in rs],'next_cursor':nxt})
async def search(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    qtext=req.query.get('q')
    if not qtext:return json_error(400,'missing q')
    n,cur,err=page_params(req,'search')
    if err:return json_error(400,err)
    c=core.connect(); q='SELECT DISTINCT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN channel_members cm ON cm.channel_id=m.channel_id AND cm.user_id=? LEFT JOIN memberships wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=? WHERE m.deleted=0 AND (cm.user_id IS NOT NULL OR (ch.is_private=0 AND wm.role IS NOT NULL AND wm.role!=?)) AND lower(m.body) LIKE lower(?)';a=[u['id'],u['id'],'guest','%'+qtext+'%']
    if req.query.get('workspace_id'):
        try:a.append(int(req.query['workspace_id']))
        except:return json_error(400,'invalid workspace')
        q+=' AND ch.workspace_id=?'
    if cur is not None:q+=' AND m.id<?';a.append(cur)
    q+=' ORDER BY m.id DESC LIMIT ?';a.append(n+1);rs=c.execute(q,a).fetchall();c.close()
    more=len(rs)>n;rs=rs[:n];nxt=None
    if more:nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'search','v':rs[-1]['id']}).encode()).decode().rstrip('=')
    return web.json_response({'results':[msg_json(x) for x in rs],'next_cursor':nxt})

def rs_json(c,uid,cid):
    row=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone(); last=row['last_read_event_id'] if row else 0
    rows=c.execute('SELECT m.*,COALESCE((SELECT event_id FROM events e WHERE e.channel_id=m.channel_id AND json_extract(e.payload,"$.message.id")=m.id LIMIT 1),0) ev FROM messages m WHERE m.channel_id=? AND m.deleted=0 AND m.author_id!=?',(cid,uid)).fetchall()
    return {'channel_id':cid,'last_read_event_id':last,'unread_count':sum(x['ev']>last for x in rows),'mention_count':sum(x['ev']>last and uid in json.loads(x['mentions'] or '[]') for x in rows)}
async def read_route(req):
    try:u=require_user(req)
    except web.HTTPException:return json_error(401,'unauthorized')
    cid=int(req.match_info['id']);ch=core.get_channel(cid)
    if not ch:return json_error(404,'not found')
    c=core.connect(); ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone()
    if not ok and ch['is_private']:c.close();return json_error(403,'forbidden')
    if req.method=='POST':
        b=await read_json(req);v=b.get('last_read_event_id') if b else None
        if type(v) is not int or v<0:c.close();return json_error(400,'invalid event id')
        c.execute('INSERT INTO read_states(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=max(last_read_event_id,excluded.last_read_event_id)',(u['id'],cid,v));c.commit()
    out=rs_json(c,u['id'],cid);c.close();return web.json_response({'read_state':out})
async def ws(req):
    tok=req.query.get('token',''); u=core.user_for_token(tok)
    if not u:return json_error(401,'unauthorized')
    socket=web.WebSocketResponse(); await socket.prepare(req)
    conn_id=secrets.token_urlsafe(12)
    with core.tx() as pc: pc.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?)',(conn_id,u['id'],__import__('time').time()))
    subs={}; gc=core.connect(); grow=gc.execute('SELECT coalesce(max(id),0) n FROM global_events').fetchone()['n']; gc.close()
    try:
        while not socket.closed:
            try:
                incoming=await asyncio.wait_for(socket.receive(),0.05)
                if incoming.type==WSMsgType.CLOSE: break
                if incoming.type==WSMsgType.TEXT:
                    try: data=json.loads(incoming.data)
                    except: continue
                    typ=data.get('type'); cid=data.get('channel_id')
                    if not isinstance(cid,int): continue
                    if typ=='subscribe':
                        head=core.head_event_id(cid); subs[cid]=head
                        await socket.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
                    elif typ=='resume':
                        since=data.get('since_event_id',0)
                        if not isinstance(since,int): since=0
                        c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); c.close()
                        last=since
                        for row in rows:
                            frame=json.loads(row['payload']); await socket.send_json(frame); last=frame['event_id']
                        subs[cid]=last
                        await socket.send_json({'type':'resumed','channel_id':cid,'head_event_id':core.head_event_id(cid)})
            except asyncio.TimeoutError:
                pass
            gc=core.connect(); grows=gc.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(grow,)).fetchall(); gc.close()
            for gr in grows:
                await socket.send_json(json.loads(gr['payload'])); grow=gr['id']
            for cid,last in list(subs.items()):
                c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall(); c.close()
                for row in rows:
                    frame=json.loads(row['payload']); await socket.send_json(frame); subs[cid]=frame['event_id']
    except (ConnectionResetError,RuntimeError): pass
    with core.tx() as pc: pc.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
    return socket
@web.middleware
async def auth_first(req, handler):
    path=req.path
    public=(path=='/api/health' or path in ('/api/auth/register','/api/auth/login') or path=='/api/ws')
    if path.startswith('/api/') and not public:
        h=req.headers.get('Authorization','')
        if not h.startswith('Bearer ') or not core.user_for_token(h[7:]):
            return json_error(401,'unauthorized')
    return await handler(req)

app=web.Application(middlewares=[auth_first],client_max_size=11*1024*1024)
app.router.add_get('/',lambda req:web.FileResponse('/app/static/index.html')); app.router.add_get('/app.js',lambda req:web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'}))
app.router.add_get('/api/channels/{id}/read',read_route); app.router.add_post('/api/channels/{id}/read',read_route); app.router.add_post('/api/dms',dms); app.router.add_route('*','/api/workspaces/{slug}/groups',groups); app.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',group_item); app.router.add_post('/api/files',upload); app.router.add_get('/api/files/{id}',file_get); app.router.add_get('/api/files/{id}/download',file_get); app.router.add_patch('/api/users/me',user_profile); app.router.add_get('/api/users/{id}',user_profile); app.router.add_get('/api/health',health);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_workspace);app.router.add_get('/api/workspaces/{slug}',workspace); app.router.add_patch('/api/workspaces/{slug}',workspace_patch); app.router.add_route('*','/api/workspaces/{slug}/invitations',invitations); app.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_inv); app.router.add_post('/api/invitations/{code}/accept',accept_inv);app.router.add_get('/api/workspaces/{slug}/members',workspace_members);app.router.add_post('/api/workspaces/{slug}/channels',create_channel); app.router.add_patch('/api/workspaces/{slug}/members/{user_id}',change_role); app.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)
app.router.add_post('/api/channels/{id}/join',join); app.router.add_get('/api/channels/{id}/members',channel_members); app.router.add_post('/api/channels/{id}/leave',channel_leave); app.router.add_patch('/api/channels/{id}',channel_patch); app.router.add_post('/api/channels/{id}/archive',archive); app.router.add_post('/api/channels/{id}/unarchive',archive);app.router.add_route('*','/api/channels/{id}/messages',messages); app.router.add_get('/api/messages/{id}/replies',replies); app.router.add_patch('/api/messages/{id}',message_edit_delete); app.router.add_delete('/api/messages/{id}',message_edit_delete); app.router.add_post('/api/messages/{id}/pin',pin); app.router.add_post('/api/messages/{id}/reactions',reaction); app.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction); app.router.add_delete('/api/messages/{id}/pin',unpin); app.router.add_get('/api/channels/{id}/pins',pins); app.router.add_get('/api/search',search);app.router.add_get('/api/ws',ws)
if __name__=='__main__': web.run_app(app,host='127.0.0.1',port=8000+NODE)
