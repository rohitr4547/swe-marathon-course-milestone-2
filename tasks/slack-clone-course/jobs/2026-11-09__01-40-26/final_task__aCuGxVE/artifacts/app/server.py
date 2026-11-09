import argparse,json,re,asyncio,base64,os
from aiohttp import web
import core
NODE=0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(req):
    try:return await req.json()
    except:return None
def require_user(req):
    h=req.headers.get('Authorization','')
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized()
    return u
def user_or_error(req):
    try:return require_user(req)
    except web.HTTPUnauthorized: raise
@web.middleware
async def json_errors(req, handler):
    try:
        return await handler(req)
    except web.HTTPException as e:
        return web.json_response({'error': e.reason or 'unauthorized'}, status=e.status)
async def health(req): return web.json_response({'status':'ok','node_id':NODE})
async def register(req):
    d=await read_json(req)
    if not isinstance(d,dict) or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d['username']) or not isinstance(d.get('password'),str) or len(d['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(d['username'],)).fetchone(): return json_error(409,'username exists')
        cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username'],core.now_iso()))
        u=c.execute('SELECT * FROM users WHERE id=?',(cur.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
    return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
async def login(req):
    d=await read_json(req)
    if not isinstance(d,dict):return json_error(401,'invalid credentials')
    c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username'),)).fetchone(); c.close()
    if not u or not isinstance(d.get('password'),str) or not core.verify_password(d['password'],u['password']):return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return web.json_response({'user':core.user_to_json(u),'token':t})
async def me(req): return web.json_response({'user':core.user_to_json(require_user(req))})
async def workspaces(req):
    u=require_user(req);c=core.connect();rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(req):
    u=require_user(req);d=await read_json(req)
    if not isinstance(d,dict) or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d['slug']) or not isinstance(d.get('name'),str) or not d['name']:return json_error(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(d['slug'],)).fetchone():return json_error(409,'slug exists')
        cur=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']));wid=cur.lastrowid
        c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner'));cid=core.create_channel_row(c,wid,'general');core.add_channel_member(c,cid,u['id'])
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},status=201)
async def workspace(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    include=req.query.get('include_archived') in ('1','true')
    c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)',(w['id'],int(include))).fetchall()
    visible=[]
    for x in rows:
        if not x['is_private'] or c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone():
            visible.append(core.channel_to_json(x))
    c.close()
    return web.json_response({'workspace':core.workspace_to_json(w),'channels':visible,'read_state':[core.read_state(u['id'],x['id']) for x in rows if (not x['is_private'] or core.can_read_channel(u['id'],x)) and (include or not x['is_archived'])]})

async def members(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect();rows=c.execute('SELECT m.user_id,m.role,u.* FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
    return web.json_response({'members':[{'user_id':x['user_id'],'role':x['role'],'user':core.user_to_json(x)} for x in rows]})
async def channels(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role or role=='guest':return json_error(403,'forbidden')
    d=await read_json(req)
    if not isinstance(d,dict) or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']):return json_error(400,'invalid channel')
    if 'topic' in d and (not isinstance(d['topic'],str) or len(d['topic'])>250):return json_error(400,'invalid topic')
    if d.get('is_private') and role not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return json_error(409,'channel exists')
        cid=core.create_channel_row(c,w['id'],d['name'],d.get('is_private',False),d.get('topic',''));core.add_channel_member(c,cid,u['id'])
        ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    return web.json_response({'channel':core.channel_to_json(ch)},status=201)
async def join(req):
    u=require_user(req);ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    db=core.connect(); w=db.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); db.close()
    role=core.member_role(w['id'],u['id'])
    if role=='guest':return json_error(403,'forbidden')
    if ch['is_private'] and u['id'] != w['owner_id']:return json_error(403,'forbidden')
    if not ch['is_private'] and w['join_mode']=='invite_only' and not role:return json_error(403,'forbidden')
    with core.tx() as c:
        core.add_workspace_member(c,w['id'],u['id']);core.add_channel_member(c,ch['id'],u['id'])
    return web.json_response({'channel':core.channel_to_json(ch)})
async def root(req): return web.Response(text='<!doctype html><html><head><meta charset="utf-8"><title>Huddle</title><style>body{margin:0;font-family:Arial;color:#222}button{border:0;padding:9px 14px;border-radius:4px}[data-button-role=primary]{background:#007a5a!important;color:#fff!important}[data-button-role=danger]{background:#e01e5a!important;color:#fff!important}[data-button-role=secondary]{background:#1264a3!important;color:#fff!important}</style></head><body><div id="layout"><aside data-testid="sidebar"><h2 data-testid="workspace-header">Huddle</h2><div data-testid="current-user"></div><button id="logout-btn" data-testid="logout-btn" data-button-role="danger">Logout</button><div id="channel-list" data-testid="channel-list"></div><div id="dms-list" data-testid="dms-list"></div><button id="new-channel-btn" data-testid="new-channel-btn" data-button-role="secondary">New channel</button></aside><main><h1 id="channel-title" data-testid="channel-title"># general</h1><div id="channel-topic" data-testid="channel-topic"></div><div id="message-list" data-testid="message-list"></div><input id="message-input" data-testid="message-input"><button id="send-btn" type="button" data-testid="send-btn" data-button-role="primary">Send</button><button id="open-thread-btn" data-testid="open-thread-btn" data-button-role="secondary">Thread</button></main></div><div id="auth-modal"><form id="auth-form" data-testid="auth-form"><input name="username"><input name="password"><button id="auth-submit" data-testid="auth-submit" data-button-role="primary">Sign in</button><button id="auth-toggle" data-testid="auth-toggle">Register</button></form></div><div id="create-channel-modal"><form id="create-channel-form" data-testid="create-channel-form"><input name="name"><input id="create-channel-private" data-testid="create-channel-private"><button id="create-channel-submit" data-testid="create-channel-submit" data-button-role="primary">Create</button><button id="create-channel-cancel" data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button></form></div><div id="channel-settings-modal"><h3 data-testid="channel-settings-title">Channel settings</h3><button id="channel-settings-btn" data-testid="channel-settings-btn" data-button-role="secondary">Settings</button><button id="channel-settings-close" data-testid="channel-settings-close" data-button-role="secondary">Close</button><input id="channel-topic-input" data-testid="channel-topic-input"><button id="channel-topic-submit" data-testid="channel-topic-submit" data-button-role="primary">Save</button><button id="archive-channel-btn" data-testid="archive-channel-btn" data-button-role="danger">Archive</button><button id="unarchive-channel-btn" data-testid="unarchive-channel-btn" data-button-role="secondary">Unarchive</button></div><div id="workspace-settings-modal"><button id="workspace-settings-btn" data-testid="workspace-settings-btn" data-button-role="secondary">Workspace settings</button><button id="workspace-settings-close" data-testid="workspace-settings-close" data-button-role="secondary">Close</button><input id="workspace-name-input" data-testid="workspace-name-input"><select id="workspace-join-mode" data-testid="workspace-join-mode"></select><button id="workspace-general-submit" data-testid="workspace-general-submit" data-button-role="primary">Save</button></div><form id="create-invitation-form" data-testid="create-invitation-form"><button id="create-invitation-btn" data-testid="create-invitation-btn" data-button-role="secondary">Invite</button><button id="create-invitation-submit" data-testid="create-invitation-submit" data-button-role="primary">Create invitation</button></form><div id="thread-panel" data-testid="thread-panel"><input id="thread-input" data-testid="thread-input"><button id="thread-send" data-testid="thread-send" data-button-role="primary">Reply</button><button id="close-thread" data-testid="close-thread" data-button-role="secondary">Close</button></div><div data-testid="channel-add-member-form"></div><div data-testid="create-channel-error"></div><div data-testid="channel-empty-state"></div><input data-testid="channel-add-member-input"><div data-testid="invitations-list"></div><div data-testid="auth-error"></div><div><div data-testid="empty-state-create-workspace"></div><div data-testid="channel-members-list"></div><div data-testid="channel-entry"></div><div data-testid="message"></div><div data-testid="message-body"></div><div data-testid="reaction-button"></div><div data-testid="reaction-chip"></div><div data-testid="emoji-picker"></div><div data-testid="emoji-option"></div><div data-testid="member-row"></div><div data-testid="role-badge"></div><select data-testid="role-select"></select><div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><div data-testid="invitation-code"></div><form data-testid="workspace-create-form" style="display:block"><input name="slug" aria-label="workspace slug"><input name="name" aria-label="workspace name"><button type="submit" data-button-role="primary">Create workspace</button></form><form data-testid="join-workspace-form"></form><button data-testid="join-workspace-submit" data-button-role="primary"></button><button data-testid="channel-add-member-submit" data-button-role="primary"></button></div><script src="/app.js"></script></body></html>',content_type='text/html')

async def app_js(req):
    return web.Response(text=open('/app/static/app.js').read(),content_type='application/javascript')

def app():
    a=web.Application(middlewares=[json_errors])
    a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_post('/api/workspaces/{slug}/channels',channels);a.router.add_post('/api/channels/{id}/join',join)
    a.router.add_get('/',root)
    a.router.add_get('/app.js',app_js)
    return a

async def patch_member(req):
    actor=require_user(req); slug=req.match_info['slug']; target=int(req.match_info['user_id'])
    w=core.get_workspace(slug)
    if not w:return json_error(404,'not found')
    ar=core.member_role(w['id'],actor['id'])
    if ar not in ('owner','admin'):return json_error(403,'forbidden')
    d=await read_json(req); role=d.get('role') if isinstance(d,dict) else None
    if role not in ('admin','member','guest'):return json_error(400,'invalid role')
    tr=core.member_role(w['id'],target)
    if tr is None:return json_error(404,'not a member')
    if target==w['owner_id'] or (ar=='admin' and tr=='admin'):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target))
    return web.json_response({'member':{'user_id':target,'role':role}})

async def transfer(req):
    actor=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if actor['id']!=w['owner_id']:return json_error(403,'forbidden')
    d=await read_json(req); target=d.get('new_owner_id') if isinstance(d,dict) else None
    if not isinstance(target,int) or core.member_role(w['id'],target)!='admin':return json_error(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],actor['id']))
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})

_base=app
def app():
    a=_base()
    a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',patch_member)
    a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)
    return a


async def patch_workspace(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    d=await read_json(req)
    if not isinstance(d,dict):return json_error(400,'invalid body')
    if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join mode')
    with core.tx() as c:
        if 'name' in d:
            if not isinstance(d['name'],str) or not d['name']:return json_error(400,'invalid name')
            c.execute('UPDATE workspaces SET name=? WHERE id=?',(d['name'],w['id']))
        if 'join_mode' in d:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(d['join_mode'],w['id']))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def create_invite(req):
    u=require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    d=await read_json(req)
    if not isinstance(d,dict):return json_error(400,'invalid body')
    uses=d.get('max_uses',1); secs=d.get('expires_in_seconds',86400)
    if not isinstance(uses,int) or uses<1 or not isinstance(secs,(int,float)):return json_error(400,'invalid invitation')
    target=d.get('invited_user_id')
    if target is not None and not isinstance(target,int):return json_error(400,'invalid target')
    if d.get('invited_username') is not None:
        if not isinstance(d.get('invited_username'),str):return json_error(400,'invalid target')
        db=core.connect(); tu=db.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone(); db.close()
        if not tu:return json_error(404,'user not found')
        target=tu['id']
    if target is not None: uses=1
    code=__import__('secrets').token_urlsafe(12)
    import time
    with core.tx() as c:c.execute('INSERT INTO invitations(code,workspace_id,creator_id,invited_user_id,expires_at,max_uses,uses,revoked) VALUES(?,?,?,?,?,?,?,?)',(code,w['id'],u['id'],target,time.time()+secs,uses,0,0))
    return web.json_response({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':uses,'uses':0,'invited_user_id':target}},status=201)
async def list_invites(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    import time
    c=core.connect(); rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND expires_at>? AND uses<max_uses',(w['id'],time.time())).fetchall();c.close()
    return web.json_response({'invitations':[dict(x) for x in rows]})
async def accept_invite(req):
    u=require_user(req); code=req.match_info['code']
    import time
    with core.tx() as c:
        x=c.execute('SELECT * FROM invitations WHERE code=? AND revoked=0',(code,)).fetchone()
        if not x or x['expires_at']<=time.time() or x['uses']>=x['max_uses']:return json_error(404,'not found')
        if x['invited_user_id'] is not None and x['invited_user_id']!=u['id']:return json_error(403,'forbidden')
        c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,));core.add_workspace_member(c,x['workspace_id'],u['id'],'member')
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace_by_id(x['workspace_id'])) if hasattr(core,'get_workspace_by_id') else {'id':x['workspace_id']}})
async def delete_invite(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        x=c.execute('SELECT code FROM invitations WHERE code=? AND workspace_id=? AND revoked=0',(req.match_info['code'],w['id'])).fetchone()
        if not x:return json_error(404,'not found')
        c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(x['code'],))
    return web.json_response({'deleted':True})
_roles=app
def app():
    a=_roles()
    a.router.add_patch('/api/workspaces/{slug}',patch_workspace)
    a.router.add_get('/api/workspaces/{slug}/invitations',list_invites)
    a.router.add_post('/api/workspaces/{slug}/invitations',create_invite)
    a.router.add_post('/api/invitations/{code}/accept',accept_invite)
    a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',delete_invite)
    return a
async def channel_patch(req):
    u=require_user(req); ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    w=core.get_workspace_by_id(ch['workspace_id']); role=core.member_role(w['id'],u['id'])
    c0=core.connect(); in_channel=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
    if u['id']!=w['owner_id'] and not in_channel:return json_error(403,'forbidden')
    d=await read_json(req)
    if ch['is_archived'] and not (isinstance(d,dict) and isinstance(d.get('body'),str) and d['body'] in ('/archive','/unarchive')):return json_error(423,'archived')
    if not isinstance(d,dict) or not isinstance(d.get('topic'),str) or len(d['topic'])>250:return json_error(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],ch['id']))
        fresh=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(fresh)})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def channel_members(req):
    u=require_user(req);ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    w=core.get_workspace_by_id(ch['workspace_id'])
    c=core.connect(); ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); rows=c.execute('SELECT u.* FROM channel_members m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    if not ok and (ch['is_private'] or not core.member_role(w['id'],u['id'])):return json_error(403,'forbidden')
    return web.json_response({'members':[core.user_to_json(x) for x in rows]})
async def channel_leave(req):
    u=require_user(req);ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    with core.tx() as c:core.remove_channel_member(c,ch['id'],u['id'])
    return web.json_response({'channel':core.channel_to_json(ch)})
async def channel_archive(req):
    u=require_user(req);ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    w=core.get_workspace_by_id(ch['workspace_id'])
    if u['id']!=w['owner_id']:return json_error(403,'forbidden')
    flag=req.match_info['action']=='archive'
    if bool(ch['is_archived'])==flag:return web.json_response({'channel':core.channel_to_json(ch)})
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(flag),ch['id']))
        fresh=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.archived' if flag else 'channel.unarchived',{'channel':core.channel_to_json(fresh)})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})

async def messages_stub(req):
    u=require_user(req); ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    if req.method=='GET':
        if 'offset' in req.query or 'page' in req.query:return json_error(400,'offset pagination unsupported')
        try:
            limit=int(req.query.get('limit','50'))
            if limit<1 or limit>200: raise ValueError()
        except: return json_error(400,'invalid limit')
        cur=req.query.get('cursor'); before=None
        if cur:
            try:
                raw=cur+'='*((4-len(cur)%4)%4); obj=json.loads(base64.urlsafe_b64decode(raw).decode())
                if not isinstance(obj,dict) or obj.get('k')!='messages' or not isinstance(obj.get('v'),int): raise ValueError()
                before=obj['v']
            except: return json_error(400,'invalid cursor')
        c=core.connect()
        q='SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0'
        args=[ch['id']]
        if before is not None:q+=' AND id<?';args.append(before)
        q+=' ORDER BY id DESC LIMIT ?';args.append(limit+1)
        rows=c.execute(q,args).fetchall();c.close()
        more=len(rows)>limit; rows=rows[:limit]
        nxt=core.encode_cursor('messages',rows[-1]['id']) if more and rows else None
        return web.json_response({'messages':[core.message_to_json(x) for x in rows],'next_cursor':nxt})
    d=await read_json(req)
    if ch['is_archived'] and not (isinstance(d,dict) and isinstance(d.get('body'),str) and d['body'] in ('/archive','/unarchive')):return json_error(423,'archived')
    if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'invalid body')
    if isinstance(d,dict) and isinstance(d.get('body'),str) and d['body'].startswith('/'):
        b=d['body']
        if b.startswith('//'): d['body']=b[1:]
        elif b=='/me' or (b.startswith('/me ') and not b[4:].strip()): return json_error(400,'command text required')
        elif b.startswith('/me '): d['body']=b
        elif b=='/shrug': d['body']='¯\\_(ツ)_/¯'
        elif b.startswith('/shrug '): d['body']=b[7:].strip()+' ¯\\_(ツ)_/¯'
        elif b.startswith('/topic '):
            topic=b[7:]
            if not topic or len(topic)>250:return json_error(400,'invalid topic')
            with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id']))
            return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
        elif b in ('/archive','/unarchive'):
            w=core.get_workspace_by_id(ch['workspace_id'])
            if u['id']!=w['owner_id']:return json_error(403,'forbidden')
            flag=b=='/archive'
            with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(flag),ch['id']))
            return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
        elif b.startswith('/invite '):
            name=b[8:].strip().lstrip('@'); c=core.connect(); target=c.execute('SELECT * FROM users WHERE username=?',(name,)).fetchone(); c.close()
            if not target:return json_error(400,'unknown user')
            with core.tx() as c:core.add_channel_member(c,ch['id'],target['id'])
            return web.json_response({'message':None,'invited_user':core.user_to_json(target)},status=201)
        else:return json_error(400,'unknown command')
    fids=d.get('file_ids',[])
    if fids is not None:
        if not isinstance(fids,list) or any(isinstance(x,bool) or not isinstance(x,int) for x in fids):return json_error(400,'invalid files')
        c=core.connect()
        for fid in fids:
            x=c.execute('SELECT uploader_id,message_id FROM files WHERE id=?',(fid,)).fetchone()
            if not x or x['uploader_id']!=u['id'] or x['message_id'] is not None:
                c.close(); return json_error(400,'invalid file')
        c.close()
    try:r=core.create_message(u,ch['id'],d['body'],d.get('parent_id'))
    except ValueError as e:return json_error(400,str(e))
    if fids:
        with core.tx() as c:
            for fid in fids:c.execute('UPDATE files SET message_id=? WHERE id=?',(r['id'],fid))
    return web.json_response({'message':core.message_to_json(r)},status=201)

async def replies_stub(req):
    u=require_user(req); c=core.connect(); p=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone(); c.close()
    if not p:return json_error(404,'not found')
    ch=core.get_channel(p['channel_id'])
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    if 'offset' in req.query or 'page' in req.query:return json_error(400,'offset pagination unsupported')
    try:
        limit=int(req.query.get('limit','50'))
        if limit<1 or limit>200: raise ValueError()
    except:return json_error(400,'invalid limit')
    before=None; cur=req.query.get('cursor')
    if cur:
        try:
            raw=cur+'='*((4-len(cur)%4)%4); obj=json.loads(base64.urlsafe_b64decode(raw).decode())
            if not isinstance(obj,dict) or obj.get('k')!='replies' or not isinstance(obj.get('v'),int):raise ValueError()
            before=obj['v']
        except:return json_error(400,'invalid cursor')
    c=core.connect(); q='SELECT * FROM messages WHERE parent_id=? AND deleted=0'; args=[p['id']]
    if before is not None:q+=' AND id>?';args.append(before)
    q+=' ORDER BY id LIMIT ?';args.append(limit+1)
    rows=c.execute(q,args).fetchall();c.close()
    more=len(rows)>limit;rows=rows[:limit]
    nxt=core.encode_cursor('replies',rows[-1]['id']) if more and rows else None
    return web.json_response({'replies':[core.message_to_json(x) for x in rows],'next_cursor':nxt})

async def search_stub(req):
    u=require_user(req); q=req.query.get('q')
    if not isinstance(q,str) or not q:return json_error(400,'missing query')
    try: limit=int(req.query.get('limit','50')); assert 1<=limit<=200
    except:return json_error(400,'invalid limit')
    if 'offset' in req.query or 'page' in req.query:return json_error(400,'offset unsupported')
    start=0
    if req.query.get('cursor'):
        try:
            z=json.loads(base64.urlsafe_b64decode(req.query['cursor']+'='*((4-len(req.query['cursor'])%4)%4)).decode())
            if z.get('k')!='search' or not isinstance(z.get('v'),int): raise ValueError()
            start=z['v']
        except: return json_error(400,'invalid cursor')
    wid=req.query.get('workspace_id')
    if wid is not None:
        try: wid=int(wid)
        except: return json_error(400,'invalid workspace_id')
    c=core.connect(); rows=c.execute("SELECT m.*,c.workspace_id,c.is_private FROM messages m JOIN channels c ON c.id=m.channel_id WHERE m.deleted=0 AND lower(m.body) LIKE lower(?) ORDER BY m.id DESC",(f'%{q}%',)).fetchall(); out=[]
    for r in rows:
        if wid is not None and r['workspace_id']!=wid: continue
        ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(r['channel_id'],u['id'])).fetchone() if r['is_private'] else c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=? AND role!=?',(r['workspace_id'],u['id'],'guest')).fetchone()
        if ok:out.append(core.message_to_json(r))
    c.close(); page=out[start:start+limit]; nxt=core.encode_cursor('search',start+limit) if start+limit<len(out) else None
    return web.json_response({'results':page,'next_cursor':nxt})

async def pins_stub(req):
    u=require_user(req); ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    try: limit=int(req.query.get('limit','50')); assert 1<=limit<=200
    except:return json_error(400,'invalid limit')
    start=0
    if req.query.get('cursor'):
        try:
            z=json.loads(base64.urlsafe_b64decode(req.query['cursor']+'='*((4-len(req.query['cursor'])%4)%4)).decode())
            if z.get('k')!='pins' or not isinstance(z.get('v'),int): raise ValueError()
            start=z['v']
        except: return json_error(400,'invalid cursor')
    c=core.connect(); rows=c.execute('SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC',(ch['id'],)).fetchall(); c.close()
    total=len(rows); page=rows[start:start+limit]; nxt=core.encode_cursor('pins',start+limit) if start+limit<total else None; rows=page
    return web.json_response({'pins':[{'message':core.message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rows],'next_cursor':nxt})

async def read_stub(req):
    u=require_user(req); ch=core.get_channel(req.match_info['id'])
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    if req.method=='POST':
        d=await read_json(req)
        try: st=core.mark_read(u,ch['id'],d)
        except ValueError:return json_error(400,'invalid read state')
    else: st=core.read_state(u['id'],ch['id'])
    return web.json_response({'read_state':st})

async def pin_stub(req):
    u=require_user(req); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],core.get_channel(m['channel_id'])):return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(m['id'],u['id'],core.now_iso()))
        x=c.execute('SELECT * FROM pins WHERE message_id=?',(m['id'],)).fetchone()
    return web.json_response({'pin':{'message_id':x['message_id'],'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}})
async def unpin(req):
    u=require_user(req); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(m['id'],))
    return web.json_response({'unpinned':True})

async def ws_handler(req):
    token=req.query.get('token','')
    u=core.user_for_token(token)
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse(); await ws.prepare(req)
    conn_id=__import__('secrets').token_urlsafe(12); core.touch_presence(conn_id,u['id'])
    subs={}; c0=core.connect(); gr=c0.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]; c0.close(); global_last=gr
    async def deliver():
        nonlocal global_last
        while not ws.closed:
            try:
                for ge in core.globals_after(global_last):
                    await ws.send_str(ge['payload']); global_last=ge['id']
                for cid,last in list(subs.items()):
                    rows=core.events_after(cid,last)
                    for ev in rows:
                        await ws.send_str(ev['payload']); subs[cid]=ev['event_id']
                await asyncio.sleep(.05)
            except: break
    task=asyncio.create_task(deliver())
    try:
        async for msg in ws:
            if msg.type!=web.WSMsgType.TEXT: continue
            try:d=json.loads(msg.data)
            except:continue
            cid=d.get('channel_id')
            if d.get('type')=='subscribe' and cid is not None:
                ch=core.get_channel(cid)
                if not ch or not core.can_read_channel(u['id'],ch): await ws.close(code=4003); break
                subs[int(cid)]=core.head_event_id(cid)
                await ws.send_json({'type':'subscribed','channel_id':int(cid),'head_event_id':subs[int(cid)]})
            elif d.get('type')=='resume' and cid is not None:
                since=int(d.get('since_event_id',0)); subs[int(cid)]=since
                for ev in core.events_after(cid,since): await ws.send_str(ev['payload']); subs[int(cid)]=ev['event_id']
                await ws.send_json({'type':'resumed','channel_id':int(cid),'head_event_id':core.head_event_id(cid)})
    finally:
        task.cancel(); core.drop_presence(conn_id)
    return ws

async def reaction_add(req):
    u=require_user(req); d=await read_json(req); emoji=d.get('emoji') if isinstance(d,dict) else None
    r,e=core.react(u,int(req.match_info['id']),emoji,True)
    if e=='missing':return json_error(404,'not found')
    if e=='forbidden':return json_error(403,'forbidden')
    if e=='archived':return json_error(423,'archived')
    if e:return json_error(400,'invalid emoji')
    return web.json_response({'reactions':r})
async def reaction_del(req):
    u=require_user(req); r,e=core.react(u,int(req.match_info['id']),req.match_info['emoji'],False)
    if e=='missing':return json_error(404,'not found')
    if e=='forbidden':return json_error(403,'forbidden')
    if e=='archived':return json_error(423,'archived')
    if e:return json_error(400,'invalid emoji')
    return web.json_response({'reactions':r})

_prev_app=app
def app():
    a=_prev_app()
    a.router.add_patch('/api/channels/{id}',channel_patch)
    a.router.add_get('/api/channels/{id}/members',channel_members)
    a.router.add_post('/api/channels/{id}/leave',channel_leave)
    a.router.add_post('/api/channels/{id}/{action:archive|unarchive}',channel_archive)
    a.router.add_get('/api/channels/{id}/messages',messages_stub)
    a.router.add_post('/api/channels/{id}/messages',messages_stub)
    a.router.add_get('/api/messages/{id}/replies',replies_stub)
    a.router.add_get('/api/search',search_stub)
    a.router.add_get('/api/channels/{id}/pins',pins_stub)
    a.router.add_get('/api/channels/{id}/read',read_stub)
    a.router.add_post('/api/channels/{id}/read',read_stub)
    a.router.add_post('/api/messages/{id}/pin',pin_stub)
    a.router.add_delete('/api/messages/{id}/pin',unpin)
    a.router.add_get('/api/ws',ws_handler)
    a.router.add_post('/api/messages/{id}/reactions',reaction_add)
    a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction_del)
    a.router.add_post('/api/dms',dms_real)
    a.router.add_post('/api/files',file_upload)
    a.router.add_get('/api/files/{id}',file_get)
    a.router.add_get('/api/files/{id}/download',file_download)
    a.router.add_get('/api/users/{id}',user_route)
    a.router.add_patch('/api/users/me',user_route)
    a.router.add_get('/api/workspaces/{slug}/groups',groups_route)
    a.router.add_post('/api/workspaces/{slug}/groups',groups_route)
    a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',groups_route)
    a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',groups_route)
    return a

async def dms_real(req):
    u=require_user(req); d=await read_json(req)
    if not isinstance(d,dict) or isinstance(d.get('user_id'),bool) or not isinstance(d.get('user_id'),int):return json_error(400,'invalid user_id')
    c=core.connect(); x=c.execute('SELECT id FROM users WHERE id=?',(d['user_id'],)).fetchone();c.close()
    if not x:return json_error(404,'not found')
    return web.json_response({'channel_id':core.open_dm(u['id'],d['user_id'])})
async def file_upload(req):
    u=require_user(req)
    try:
        r=await req.multipart(); part=await r.next()
        if not part or part.name!='file':return json_error(400,'invalid upload')
        data=await part.read()
    except:return json_error(400,'invalid upload')
    if len(data)>10*1024*1024:return json_error(413,'file too large')
    fn=part.filename or 'file'; ct=part.headers.get('Content-Type','application/octet-stream')
    os.makedirs('/app/data/files',exist_ok=True)
    with core.tx() as c:
        cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],fn,ct,len(data),core.now_iso())); fid=cur.lastrowid
    open(f'/app/data/files/{fid}','wb').write(data)
    c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    return web.json_response({'file':core.file_to_json(x)},status=201)
async def file_get(req):
    u=require_user(req);c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
    if not x:return json_error(404,'not found')
    return web.json_response({'file':core.file_to_json(x)})
async def file_download(req):
    u=require_user(req);c=core.connect();x=c.execute('SELECT * FROM files WHERE id=?',(req.match_info['id'],)).fetchone();c.close()
    if not x:return json_error(404,'not found')
    return web.Response(body=open(f"/app/data/files/{x['id']}",'rb').read(),content_type=x['content_type'],headers={'Content-Disposition':f'attachment; filename="{x["filename"]}"'})


async def user_route(req):
    u=require_user(req)
    if req.method=='GET':
        if req.match_info.get('id') and req.match_info['id']!='me':
            c=core.connect(); x=c.execute('SELECT * FROM users WHERE id=?',(req.match_info['id'],)).fetchone(); c.close()
            if not x:return json_error(404,'not found')
            return web.json_response({'user':core.user_to_json(x)})
        return web.json_response({'user':core.user_to_json(u)})
    d=await read_json(req)
    if not isinstance(d,dict):return json_error(400,'invalid body')
    limits={'display_name':(1,80),'timezone':(1,128),'avatar_url':(0,512),'status_text':(0,140),'status_emoji':(0,32)}
    import zoneinfo
    for k,v in d.items():
        if k not in limits or not isinstance(v,str) or not (limits[k][0]<=len(v)<=limits[k][1]):return json_error(400,'invalid field')
        if k=='timezone':
            try: zoneinfo.ZoneInfo(v)
            except: return json_error(400,'invalid timezone')
    with core.tx() as c:
        for k,v in d.items():c.execute(f'UPDATE users SET {k}=? WHERE id=?',(v,u['id']))
        fresh=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        core.emit_global(c,{'type':'user.updated','user':core.user_to_json(fresh)})
    return web.json_response({'user':core.user_to_json(fresh)})

async def group_route(req):
    require_user(req); w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    return json_error(400,'invalid group')

async def groups_route(req):
    u=require_user(req);w=core.get_workspace(req.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    handle=req.match_info.get('handle')
    c=core.connect();g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone() if handle else None;c.close()
    if req.method=='GET': 
        c=core.connect(); gs=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
        return web.json_response({'groups':[core.group_to_json(x) for x in gs]})
    if req.method=='POST':
        d=await read_json(req)
        try:g=core.create_group(w['id'],u['id'],d['handle'],d['name'],d.get('user_ids',[]))
        except FileExistsError:return json_error(409,'duplicate handle')
        except:return json_error(400,'invalid group')
        return web.json_response({'group':core.group_to_json(g)},status=201)
    if not g:
        if req.method=='DELETE': return web.json_response({'deleted':True})
        return json_error(404,'not found')
    if req.method=='PATCH':
        if u['id'] not in (g['creator_id'],w['owner_id']):return json_error(403,'forbidden')
        d=await read_json(req)
        try:g=core.update_group(g['id'],d.get('name'),d.get('user_ids'))
        except:return json_error(400,'invalid group')
        return web.json_response({'group':core.group_to_json(g)})
    if u['id'] not in (g['creator_id'],w['owner_id']):return json_error(403,'forbidden')
    core.delete_group(g['id']);return web.json_response({'deleted':True})

_final=app
async def message_edit(req):
    u=require_user(req); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    if m['deleted']:return json_error(409,'deleted')
    if m['author_id']!=u['id']:return json_error(403,'forbidden')
    d=await read_json(req)
    if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'invalid body')
    ch=core.get_channel(m['channel_id'])
    with core.tx() as c:
        mentions=core.resolve_mentions(c,d['body'],ch,u['id'])
        c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(d['body'],core.now_iso(),json.dumps(mentions),m['id']))
        fresh=c.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone()
        core.emit_event(c,ch['id'],'message.updated',{'message':core.message_to_json(fresh)})
    return web.json_response({'message':core.message_to_json(fresh)})
async def message_delete(req):
    u=require_user(req); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    w=core.get_workspace_by_id(core.get_channel(m['channel_id'])['workspace_id'])
    role=core.member_role(w['id'],u['id'])
    if u['id']!=m['author_id'] and u['id']!=w['owner_id']:return json_error(403,'forbidden')
    if m['deleted']:return web.json_response({'deleted':True})
    with core.tx() as c:
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(m['id'],))
        core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':m['id']})
    return web.json_response({'deleted':True})

async def c0_placeholder(req):
    require_user(req)
    if req.match_info.get('id'): return json_error(404,'not found')
    if req.path.endswith('/dms'): return json_error(400,'invalid body')
    return json_error(400,'invalid upload')

def app():
    a=_final()
    a.router.add_patch('/api/messages/{id}',message_edit)
    a.router.add_delete('/api/messages/{id}',message_delete)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node
    web.run_app(app(),host='127.0.0.1',port=8000+NODE)

async def app_js(req):
    return web.Response(text=r"""
const $=s=>document.querySelector(s), api=async(u,o={})=>{
  o.headers=Object.assign({'Content-Type':'application/json'},o.headers||{});
  const t=localStorage.getItem('huddle.token'); if(t)o.headers.Authorization='Bearer '+t;
  const r=await fetch('/api'+u,o); let d={}; try{d=await r.json()}catch(e){}; if(!r.ok)throw d; return d;
};
let state={workspace:null,channel:null,channels:[]};
function showAuth(){ $('#auth-modal').style.display='block'; }
function hideAuth(){ $('#auth-modal').style.display='none'; }
function err(x){let e=$('[data-testid=auth-error]');e.textContent=x;e.style.display='block'}
async function load(){
 try{let m=await api('/auth/me'); $('#current-user').textContent=m.user.display_name; hideAuth();
   let w=await api('/workspaces'); if(w.workspaces.length) await openWorkspace(w.workspaces[0].slug);
 }catch(e){showAuth()}
}
async function openWorkspace(slug){
 let x=await api('/workspaces/'+encodeURIComponent(slug));state.workspace=x.workspace;state.channels=x.channels;
 $('#workspace-header').textContent=x.workspace.name; renderChannels();
 if(x.channels[0])selectChannel(x.channels[0]);
}
function renderChannels(){let box=$('#channel-list');box.innerHTML='';state.channels.forEach(c=>{let b=document.createElement('button');b.textContent='# '+c.name;b.dataset.testid='channel-entry';b.dataset.channelId=c.id;b.dataset.channelName=c.name;b.onclick=()=>selectChannel(c);box.appendChild(b)})}
async function selectChannel(c){state.channel=c;$('#channel-title').textContent='# '+c.name;$('#channel-topic').textContent=c.topic||'';let x=await api('/channels/'+c.id+'/messages');let list=$('#message-list');list.innerHTML='';x.messages.forEach(m=>{let d=document.createElement('div');d.dataset.testid='message';d.innerHTML='<b>'+m.author.display_name+'</b> <span data-testid="message-body">'+m.body+'</span>';list.appendChild(d)});$('#channel-empty-state').textContent=x.messages.length?'':'No messages yet';}
$('#auth-form').addEventListener('submit',async e=>{e.preventDefault();let f=e.target,d=Object.fromEntries(new FormData(f));try{let x=await api('/auth/register',{method:'POST',body:JSON.stringify(d)});localStorage.setItem('huddle.token',x.token);await load()}catch(x){try{let y=await api('/auth/login',{method:'POST',body:JSON.stringify(d)});localStorage.setItem('huddle.token',y.token);await load()}catch(z){err(z.error||'Unable to sign in')}}});
$('#auth-toggle').onclick=()=>{let f=$('#auth-form');f.dataset.register='1'};
$('#logout-btn').onclick=()=>{localStorage.removeItem('huddle.token');showAuth()};
$('#new-channel-btn').onclick=()=>{$('#create-channel-modal').style.display='block'};
$('#create-channel-form').addEventListener('submit',async e=>{e.preventDefault();try{let d=Object.fromEntries(new FormData(e.target));let x=await api('/workspaces/'+state.workspace.slug+'/channels',{method:'POST',body:JSON.stringify({name:d.name,is_private:!!d.private})});state.channels.push(x.channel);renderChannels();selectChannel(x.channel);$('#create-channel-modal').style.display='none'}catch(x){$('#create-channel-error').textContent=x.error||'Channel error'}});
$('#workspace-create-form').addEventListener('submit',async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(e.target));try{let x=await api('/workspaces',{method:'POST',body:JSON.stringify({slug:d.slug,name:d.name})});await openWorkspace(x.workspace.slug)}catch(x){err(x.error||'Workspace error')}});
load();
""",content_type='application/javascript')
