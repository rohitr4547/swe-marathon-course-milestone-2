import argparse, json, os, asyncio, re
from aiohttp import web, WSMsgType
import core

NODE=0
def j(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return j({'error':msg},status)
async def body(req):
    try:
        x=await req.json()
        return x if isinstance(x,dict) else None
    except: return None
def auth(req):
    h=req.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
def need(req):
    u=auth(req)
    if not u: raise web.HTTPUnauthorized()
    return u
def user_err(req):
    u=auth(req)
    if not u:return None
    return u
async def health(req): return j({'status':'ok','node_id':NODE})
async def register(req):
    b=await body(req)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone(): return err(409,'username exists')
        x=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
        u=c.execute('SELECT * FROM users WHERE id=?',(x.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
    return j({'user':core.user_to_json(u),'token':t},201)
async def login(req):
    b=await body(req)
    if not b or not isinstance(b.get('username'),str) or not isinstance(b.get('password'),str):return err(400,'invalid body')
    c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(b['username'],)).fetchone(); c.close()
    if not u or not core.verify_password(b['password'],u['password']):return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return j({'user':core.user_to_json(u),'token':t})
async def me(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    return j({'user':core.user_to_json(u)})
async def workspaces(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    c=core.connect();rs=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return j({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_workspace(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    b=await body(req)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str) or not b['name']:return err(400,'invalid workspace')
    try:
        with core.tx() as c:
            if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return err(409,'slug exists')
            x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id']));wid=x.lastrowid
            c.execute('INSERT INTO workspace_members VALUES(?,?,?)',(wid,u['id'],'owner'))
            ch=core.create_channel_row(c,wid,'general');core.add_channel_member(c,ch['id'],u['id'])
            return j({'workspace':core.workspace_to_json(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':core.channel_to_json(ch)},201)
    except: return err(409,'workspace exists')
async def patch_workspace(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    b=await body(req)
    if not b:return err(400,'invalid body')
    if 'name' in b and (not isinstance(b['name'],str) or not b['name']):return err(400,'invalid name')
    if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return err(400,'invalid join mode')
    vals=[]; args=[]
    if 'name' in b: vals.append('name=?');args.append(b['name'])
    if 'join_mode' in b: vals.append('join_mode=?');args.append(b['join_mode'])
    if vals:
        with core.tx() as c:
            args.append(w['id']);c.execute('UPDATE workspaces SET '+','.join(vals)+' WHERE id=?',args)
        w=core.get_workspace(req.match_info['slug'])
    return j({'workspace':core.workspace_to_json(w)})
async def transfer(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if u['id']!=w['owner_id']:return err(403,'forbidden')
    b=await body(req)
    if not b or type(b.get('new_owner_id')) is not int:return err(400,'invalid owner')
    target=core.member_role(w['id'],b['new_owner_id'])
    if target!='admin':return err(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
        c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],b['new_owner_id']))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(b['new_owner_id'],w['id']))
    return j({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def invitation_create(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    b=await body(req) or {}
    if any(k in b and type(b[k]) is not int for k in ('expires_in_seconds','max_uses','invited_user_id')) or ('invited_username' in b and not isinstance(b['invited_username'],str)):return err(400,'invalid invitation')
    invited=b.get('invited_user_id')
    if not invited and b.get('invited_username'):
        c=core.connect();q=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone();c.close()
        invited=q['id'] if q else None
    import time
    code=__import__('secrets').token_urlsafe(12)
    with core.tx() as c:c.execute('INSERT INTO invitations(code,workspace_id,created_by,expires_at,max_uses,invited_user_id) VALUES(?,?,?,?,?,?)',(code,w['id'],u['id'],time.time()+b.get('expires_in_seconds',86400),1 if invited else b.get('max_uses',1),invited))
    return j({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':1 if invited else b.get('max_uses',1),'uses':0}},201)
async def invitations(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    import time
    c=core.connect();rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND expires_at>? AND uses<max_uses',(w['id'],time.time())).fetchall();c.close()
    return j({'invitations':[dict(x) for x in rs]})
async def revoke_inv(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    c=core.connect();x=c.execute('SELECT 1 FROM invitations WHERE workspace_id=? AND code=?',(w['id'],req.match_info['code'])).fetchone();c.close()
    if not x:return err(404,'not found')
    with core.tx() as c:c.execute('DELETE FROM invitations WHERE workspace_id=? AND code=?',(w['id'],req.match_info['code']))
    return j({'deleted':True})
async def accept_inv(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    c=core.connect();x=c.execute('SELECT * FROM invitations WHERE code=?',(req.match_info['code'],)).fetchone();c.close()
    if not x:return err(404,'not found')
    import time
    if x['invited_user_id'] and x['invited_user_id']!=u['id']:return err(403,'invitation targeted to another user')
    if x['expires_at']<time.time() or x['uses']>=x['max_uses']:return err(404,'not found')
    with core.tx() as c:
        c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(x['code'],))
        c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(x['workspace_id'],u['id'],'member'))
    w=core.connect().execute('SELECT * FROM workspaces WHERE id=?',(x['workspace_id'],)).fetchone()
    return j({'workspace':core.workspace_to_json(w)})
async def workspace(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    include=req.query.get('include_archived','') in ('1','true')
    c=core.connect();chs=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (? OR is_archived=0)',(w['id'],int(include))).fetchall();chs=[x for x in chs if not x['is_private'] or core._cm(x['id'],u['id'])];c.close()
    states=[]
    for x in chs:
        q=await channel_read_state_value(u['id'],x['id'])
        states.append(q)
    return j({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in chs],'read_state':states})
async def channel_read_state_value(uid,cid):
    c=core.connect();r=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone();cur=r['last_read_event_id'] if r else 0
    rows=c.execute("SELECT e.event_id,json_extract(e.payload,'$.message.author_id') author_id,json_extract(e.payload,'$.message.mentions') mentions FROM events e WHERE e.channel_id=? AND e.event_id>? AND json_extract(e.payload,'$.message.id') IS NOT NULL",(cid,cur)).fetchall();c.close()
    return {'channel_id':cid,'last_read_event_id':cur,'unread_count':sum(x['author_id'] is not None and x['author_id']!=uid for x in rows),'mention_count':sum(x['author_id'] is not None and x['author_id']!=uid and str(uid) in (x['mentions'] or []) for x in rows)}
async def patch_member(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return err(403,'forbidden')
    try: target=int(req.match_info['user_id'])
    except: return err(404,'not found')
    current=core.member_role(w['id'],target)
    if not current:return err(404,'not found')
    b=await body(req)
    if not b or not isinstance(b.get('role'),str) or b['role'] not in ('member','admin','guest'):return err(400,'invalid role')
    if target==w['owner_id'] or (role=='admin' and current=='admin'):return err(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',(b['role'],w['id'],target))
    return j({'member':{'user_id':target,'role':b['role']}})
async def members(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT m.user_id,m.role,u.username,u.display_name FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall();c.close()
    return j({'members':[dict(x) for x in rs]})
async def create_channel(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role or role=='guest':return err(403,'forbidden')
    b=await body(req)
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']) or ('is_private' in b and not isinstance(b['is_private'],bool)) or ('topic' in b and (not isinstance(b['topic'],str) or len(b['topic'])>250)):return err(400,'invalid channel')
    if b.get('is_private') and role not in ('owner','admin'):return err(403,'forbidden')
    c=core.connect()
    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():c.close();return err(409,'channel exists')
    c.close()
    with core.tx() as c:
        ch=core.create_channel_row(c,w['id'],b['name'],b.get('is_private',False),b.get('topic',''));core.add_channel_member(c,ch['id'],u['id'])
    return j({'channel':core.channel_to_json(ch)},201)
async def join(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    if ch['is_private']:return err(403,'private channel')
    if ch['workspace_id']:
        role=core.member_role(ch['workspace_id'],u['id'])
        if role=='guest':return err(403,'guest cannot join')
        c0=core.connect();w=c0.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c0.close()
        if w and w['join_mode']=='invite_only' and not role:return err(403,'invite required')
    with core.tx() as c:
        existed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):
            c.execute('INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)',(ch['workspace_id'],u['id'],'member'))
            g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(ch['workspace_id'],)).fetchone()
            if g:c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(g['id'],u['id']))
        c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
        if not existed: core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
    return j({'channel':core.channel_to_json(ch)})
async def patch_channel(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: cid=int(req.match_info['id'])
    except: return err(404,'not found')
    ch=core.get_channel(cid)
    if not ch:return err(404,'not found')
    role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
    if not core._cm(cid,u['id']) and role!='owner':return err(403,'forbidden')
    if ch['is_archived']:
        probe=await body(req)
        if not isinstance(probe,dict) or not isinstance(probe.get('body'),str) or probe['body'].split(None,1)[0] not in ('/unarchive',):
            return err(423,'channel archived')
        b=probe
    else:
        b=await body(req)
    if not b or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return err(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(b['topic'],cid))
        updated=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        core.emit_event(c,cid,'channel.updated',{'channel':core.channel_to_json(updated)})
    return j({'channel':core.channel_to_json(updated)})
async def channel_members(req):
    u=auth(req); 
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT u.* FROM channel_members m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
    return j({'members':[core.user_to_json(x) for x in rs]})
async def leave_channel(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    with core.tx() as c:
        existed=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
        if existed: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return j({'channel':core.channel_to_json(ch)})
async def archive_channel(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    if core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
    action='unarchive' if req.path.endswith('/unarchive') else 'archive'
    with core.tx() as c:
        was=bool(ch['is_archived']); now=action=='archive'
        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(now),ch['id']))
        updated=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        if was!=now: core.emit_event(c,ch['id'],'channel.archived' if now else 'channel.unarchived',{'channel':core.channel_to_json(updated)})
    return j({'channel':core.channel_to_json(updated)})
async def messages(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: cid=int(req.match_info['id'])
    except: return err(404,'not found')
    ch=core.get_channel(cid)
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if 'offset' in req.query or 'page' in req.query:return err(400,'offset pagination unsupported')
    try:
        limit,cursor=core.parse_paging(req.query,'messages')
    except: return err(400,'invalid paging')
    c=core.connect()
    if cursor is None:
        rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL ORDER BY id DESC LIMIT ?', (cid,limit+1)).fetchall()
    else:
        rs=c.execute('SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL AND id<? ORDER BY id DESC LIMIT ?', (cid,cursor,limit+1)).fetchall()
    c.close()
    more=len(rs)>limit; rs=rs[:limit]
    return j({'messages':[core.message_to_json(x) for x in rs],'next_cursor':core.encode_cursor('messages',rs[-1]['id']) if more and rs else None})

async def post_message(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    cid=int(req.match_info['id']);ch=core.get_channel(cid)
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    b=await body(req)
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty body')
    if ch['is_archived'] and b['body'].split(None,1)[0] != '/unarchive':return err(423,'channel archived')
    parent=b.get('parent_id')
    if parent is not None and (type(parent) is not int or parent<1):return err(400,'invalid parent_id')
    text=b['body']
    if text.startswith('//'):
        text=text[1:]
    elif text.startswith('/'):
        parts=text.split(None,1); cmd=parts[0][1:]; arg=parts[1] if len(parts)>1 else ''
        role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
        if cmd=='me':
            if not arg.strip():return err(400,'/me requires text')
            text=text
        elif cmd=='shrug':
            text=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
        elif cmd=='topic':
            if not arg.strip():return err(400,'topic required')
            if len(arg)>250:return err(400,'topic too long')
            if not core._cm(cid,u['id']) and role!='owner':return err(403,'forbidden')
            with core.tx() as c:
                c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,cid))
                updated=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                core.emit_event(c,cid,'channel.updated',{'channel':core.channel_to_json(updated)})
            return j({'message':None,'channel':core.channel_to_json(updated)},201)
        elif cmd=='invite':
            if not arg.startswith('@') or not core._cm(cid,u['id']) and role not in ('owner','admin'):return err(403,'forbidden')
            username=arg.split()[0][1:]
            c=core.connect();target=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone();c.close()
            if not target:return err(400,'unknown user')
            with core.tx() as c:
                c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,target['id']))
                invited=c.execute('SELECT * FROM users WHERE id=?',(target['id'],)).fetchone()
            return j({'message':None,'invited_user':core.user_to_json(invited)},201)
        elif cmd in ('archive','unarchive'):
            if role!='owner':return err(403,'forbidden')
            want=cmd=='archive'
            with core.tx() as c:
                c.execute('UPDATE channels SET is_archived=? WHERE id=?',(int(want),cid))
                updated=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
                if bool(ch['is_archived'])!=want:core.emit_event(c,cid,'channel.archived' if want else 'channel.unarchived',{'channel':core.channel_to_json(updated)})
            return j({'message':None,'channel':core.channel_to_json(updated)},201)
        else:return err(400,'unknown command')
    file_ids=b.get('file_ids',[])
    if not isinstance(file_ids,list) or any(type(x) is not int for x in file_ids):return err(400,'invalid file_ids')
    if file_ids:
        c=core.connect(); fs=c.execute('SELECT * FROM files WHERE id IN (%s)'%','.join('?'*len(file_ids)),file_ids).fetchall();c.close()
        if len(fs)!=len(set(file_ids)) or any(f['uploader_id']!=u['id'] or f['message_id'] is not None for f in fs):return err(400,'invalid files')
    m=core.create_message(u,cid,text,parent)
    if file_ids:
        with core.tx() as c:
            c.execute('UPDATE files SET message_id=? WHERE id IN (%s)'%','.join('?'*len(file_ids)),[m['id']]+file_ids)
        row=core.connect().execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone();m=core.message_to_json(row)
    return j({'message':m},201)
async def edit_msg(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: mid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not r:return err(404,'not found')
    b=await body(req)
    if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'invalid body')
    status,msg=core.edit_message(u,mid,b['body'])
    if status=='403':return err(403,'forbidden')
    if status=='409':return err(409,'message deleted')
    return j({'message':msg})
async def delete_msg(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: mid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not r:return err(404,'not found')
    status,deleted=core.delete_message(u,mid)
    if status=='403':return err(403,'forbidden')
    return j({'deleted':True})
async def channel_read(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: cid=int(req.match_info['id'])
    except: return err(404,'not found')
    ch=core.get_channel(cid)
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    c=core.connect(); old=c.execute('SELECT last_read_event_id FROM read_state WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone(); cur=old['last_read_event_id'] if old else 0; c.close()
    if req.method=='POST':
        b=await body(req)
        if not b or type(b.get('last_read_event_id')) is not int or b['last_read_event_id']<0:return err(400,'invalid read state')
        cur=max(cur,b['last_read_event_id'])
        with core.tx() as c:c.execute('INSERT INTO read_state(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id WHERE excluded.last_read_event_id>read_state.last_read_event_id',(u['id'],cid,cur))
    c=core.connect()
    rows=c.execute("SELECT e.event_id,json_extract(e.payload,'$.message.author_id') author_id,json_extract(e.payload,'$.message.mentions') mentions FROM events e WHERE e.channel_id=? AND e.event_id>? AND json_extract(e.payload,'$.message.id') IS NOT NULL ORDER BY e.event_id",(cid,cur)).fetchall()
    c.close()
    unread=sum(1 for x in rows if x['author_id'] is not None and x['author_id']!=u['id'])
    mention=sum(1 for x in rows if x['author_id'] is not None and x['author_id']!=u['id'] and str(u['id']) in ((x['mentions'] or [])))
    return j({'read_state':{'channel_id':cid,'last_read_event_id':cur,'unread_count':unread,'mention_count':mention}})
async def pins(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: cid=int(req.match_info['id'])
    except: return err(404,'not found')
    ch=core.get_channel(cid)
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    try: limit,cursor=core.parse_paging(req.query,'pins')
    except: return err(400,'invalid paging')
    c=core.connect()
    if cursor is None:
        rs=c.execute('SELECT p.message_id,p.pinned_by,p.pinned_at,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND m.deleted=0 ORDER BY p.message_id DESC LIMIT ?', (cid,limit+1)).fetchall()
    else:
        rs=c.execute('SELECT p.message_id,p.pinned_by,p.pinned_at,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? AND m.deleted=0 AND p.message_id<? ORDER BY p.message_id DESC LIMIT ?', (cid,cursor,limit+1)).fetchall()
    c.close()
    more=len(rs)>limit; rs=rs[:limit]
    return j({'pins':[{'message':core.message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':core.encode_cursor('pins',rs[-1]['message_id']) if more and rs else None})

async def dms(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    b=await body(req)
    if not b or type(b.get('user_id')) is not int:return err(400,'invalid user_id')
    c=core.connect();x=c.execute('SELECT id FROM users WHERE id=?',(b['user_id'],)).fetchone();c.close()
    if not x:return err(404,'not found')
    cid=core.open_dm(u,b['user_id'])
    return j({'channel_id':cid})
async def upload_file(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:
        reader=await req.multipart(); part=await reader.next()
        if part is None or part.name!='file':return err(400,'file required')
        data=await part.read()
    except: return err(400,'invalid multipart')
    if len(data)>10*1024*1024:return err(413,'file too large')
    return j({'file':core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)},201)
async def get_file(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: fid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    if not f:return err(404,'not found')
    return j({'file':core.file_to_json(f)})
async def download_file(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: fid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    if not f:return err(404,'not found')
    try:
        with open('/app/data/files/'+str(fid),'rb') as fh: data=fh.read()
    except OSError: return err(404,'not found')
    return web.Response(body=data,content_type=f['content_type'],headers={'Content-Disposition':'attachment; filename="'+f['filename']+'"'})
async def groups(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return err(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT * FROM groups_ WHERE workspace_id=?',(w['id'],)).fetchall();c.close()
    if req.method=='GET':return j({'groups':[core.group_to_json(x) for x in rs]})
    b=await body(req)
    if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]+',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(b.get('user_ids',[]),list):return err(400,'invalid group')
    ids=b.get('user_ids',[])
    if any(type(x) is not int for x in ids):return err(400,'invalid user_ids')
    c=core.connect();members={x['user_id'] for x in c.execute('SELECT user_id FROM workspace_members WHERE workspace_id=?',(w['id'],)).fetchall()};exists=c.execute('SELECT 1 FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],b['handle'])).fetchone();c.close()
    if exists:return err(409,'handle exists')
    if any(x not in members for x in ids):return err(400,'non-member')
    return j({'group':core.create_group(u,w['id'],b['handle'],b['name'],ids)},201)
async def group_modify(req):
    u=auth(req); 
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return err(403,'forbidden')
    h=req.match_info['handle']; c=core.connect();g=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],h)).fetchone();c.close()
    if not g:
        return j({'deleted':True}) if req.method=='DELETE' and role in ('owner','admin','member','guest') else err(404,'not found')
    if role!='owner' and g['creator_id']!=u['id']:return err(403,'forbidden')
    if req.method=='DELETE':
        with core.tx() as c:c.execute('DELETE FROM groups_ WHERE id=?',(g['id'],))
        return j({'deleted':True})
    b=await body(req)
    b=await body(req) or {}
    if any(k in b and k not in ('name','user_ids') for k in b):return err(400,'invalid group')
    name=b.get('name',g['name']); ids=b.get('user_ids',json.loads(g['user_ids'] or '[]'))
    if not isinstance(name,str) or not isinstance(ids,list):return err(400,'invalid group')
    c=core.connect();members={x['user_id'] for x in c.execute('SELECT user_id FROM workspace_members WHERE workspace_id=?',(w['id'],)).fetchall()};c.close()
    if any(type(x) is not int or x not in members for x in ids):return err(400,'non-member')
    return j({'group':core.update_group(w['id'],h,name,ids)})
async def get_user(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: uid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();row=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close()
    if not row:return err(404,'not found')
    return j({'user':core.user_to_json(row)})
async def patch_profile(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    b=await body(req)
    if b is None:return err(400,'invalid body')
    row=core.update_profile(u,b)
    if row is None:return err(400,'invalid profile')
    return j({'user':core.user_to_json(row)})
async def c_placeholder(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    return err(404,'not found')
async def reaction_change(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: mid=int(req.match_info['id'])
    except: return err(404,'not found')
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone();c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if req.method=='POST':
        b=await body(req)
        if not b or not isinstance(b.get('emoji'),str) or not b['emoji']:return err(400,'invalid emoji')
        emoji=b['emoji']; add=True
    else:
        emoji=req.match_info.get('emoji','')
        if not isinstance(emoji,str) or not emoji:return err(400,'invalid emoji')
        add=False
    with core.tx() as c:
        exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
        changed=(add and not exists) or ((not add) and bool(exists))
        if add and not exists:c.execute('INSERT INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)',(mid,u['id'],emoji))
        if not add and exists:c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
        if changed:
            row=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            msg=core.message_to_json(row)
            core.emit_event(c,m['channel_id'],'reaction.added' if add else 'reaction.removed',{'message_id':mid,'message':msg,'emoji':emoji,'user_id':u['id']})
    row=core.connect().execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    return j({'reactions':core.message_to_json(row)['reactions']})
async def pin_message(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: mid=int(req.match_info['id'])
    except: return err(404,'not found')
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone();c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(mid,u['id'],core.now_iso()))
        x=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    return j({'pin':{'message_id':x['message_id'],'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']}})
async def unpin_message(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: mid=int(req.match_info['id'])
    except: return err(404,'not found')
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
    return j({'unpinned':True})
async def search(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    q=req.query.get('q')
    if not isinstance(q,str) or not q:return err(400,'query required')
    if 'offset' in req.query or 'page' in req.query:return err(400,'offset pagination unsupported')
    try: limit,cursor=core.parse_paging(req.query,'search')
    except: return err(400,'invalid paging')
    wid=req.query.get('workspace_id')
    if wid is not None:
        try: wid=int(wid)
        except: return err(400,'invalid workspace_id')
    c=core.connect()
    sql="""SELECT m.* FROM messages m
           JOIN channels ch ON ch.id=m.channel_id
           LEFT JOIN channel_members cm ON cm.channel_id=m.channel_id AND cm.user_id=?
           LEFT JOIN workspace_members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=?
           WHERE m.deleted=0 AND (ch.is_private=0 AND wm.user_id IS NOT NULL OR cm.user_id IS NOT NULL)
           AND LOWER(m.body) LIKE LOWER(?)"""
    args=[u['id'],u['id'],'%'+q+'%']
    if wid is not None: sql+=' AND ch.workspace_id=?';args.append(wid)
    if cursor is not None: sql+=' AND m.id<?';args.append(cursor)
    sql+=' ORDER BY m.id DESC LIMIT ?';args.append(limit+1)
    rs=c.execute(sql,args).fetchall();c.close()
    more=len(rs)>limit;rs=rs[:limit]
    return j({'results':[core.message_to_json(x) for x in rs],'next_cursor':core.encode_cursor('search',rs[-1]['id']) if more and rs else None})

async def replies(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: mid=int(req.match_info['id'])
    except: return err(404,'not found')
    c=core.connect(); parent=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
    if not parent:c.close();return err(404,'not found')
    ch=c.execute('SELECT * FROM channels WHERE id=?',(parent['channel_id'],)).fetchone()
    c.close()
    if not ch:return err(404,'not found')
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    try: limit,cursor=core.parse_paging(req.query,'replies')
    except: return err(400,'invalid paging')
    c=core.connect()
    if cursor is None:
        rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id ASC LIMIT ?', (mid,limit+1)).fetchall()
    else:
        rs=c.execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 AND id>? ORDER BY id ASC LIMIT ?', (mid,cursor,limit+1)).fetchall()
    c.close()
    more=len(rs)>limit; rs=rs[:limit]
    return j({'replies':[core.message_to_json(x) for x in rs],'next_cursor':core.encode_cursor('replies',rs[-1]['id']) if more and rs else None})

async def ws(req):
    u=core.user_for_token(req.query.get('token',''))
    if not u:u=auth(req)
    if not u:return web.Response(status=401)
    sock=web.WebSocketResponse();await sock.prepare(req); subscribed={}; conn_id=__import__('secrets').token_hex(12)
    c0=core.connect(); ge=c0.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]; c0.close()
    with core.tx() as c:c.execute('INSERT INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,u['id'],__import__('time').time()) )
    async def pump():
        nonlocal ge
        while not sock.closed:
            for cid,last in list(subscribed.items()):
                for e in core.events_after(cid,last):
                    await sock.send_json(e);subscribed[cid]=e['event_id']
            c1=core.connect()
            for row in c1.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(ge,)).fetchall():
                await sock.send_json(json.loads(row['payload'])); ge=row['id']
            c1.close()
            with core.tx() as c:c.execute('UPDATE presence SET last_seen=? WHERE conn_id=?',(__import__('time').time(),conn_id))
            await asyncio.sleep(.05)
    task=asyncio.create_task(pump())
    try:
        async for m in sock:
            if m.type==WSMsgType.TEXT:
                x=json.loads(m.data); typ=x.get('type')
                if typ=='subscribe':
                    cid=int(x['channel_id']); head=core.head_event_id(cid); subscribed[cid]=head
                    await sock.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
                elif typ=='resume':
                    cid=int(x['channel_id']); since=int(x.get('since_event_id',0))
                    for e in core.events_after(cid,since): await sock.send_json(e)
                    head=core.head_event_id(cid); subscribed[cid]=head
                    await sock.send_json({'type':'resumed','channel_id':cid,'head_event_id':head})
    finally:
        task.cancel()
        with core.tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
    return sock
async def root(req):
    p='/app/static/index.html'
    if os.path.exists(p):return web.FileResponse(p)
    return web.Response(text='<html><body>Huddle</body></html>',content_type='text/html')
def app():
    a=web.Application()
    a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me)
    a.router.add_patch('/api/messages/{id}',edit_msg);a.router.add_delete('/api/messages/{id}',delete_msg);a.router.add_get('/api/workspaces',workspaces);a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_patch('/api/workspaces/{slug}',patch_workspace);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',patch_member);a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer);a.router.add_post('/api/workspaces/{slug}/invitations',invitation_create);a.router.add_get('/api/workspaces/{slug}/invitations',invitations);a.router.add_post('/api/invitations/{code}/accept',accept_inv);a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_inv);a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/channels/{id}/join',join);a.router.add_get('/api/channels/{id}/read',channel_read);a.router.add_post('/api/channels/{id}/read',channel_read);a.router.add_get('/api/channels/{id}/pins',pins);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_get('/api/search',search);a.router.add_get('/api/workspaces/{slug}/groups',groups);a.router.add_post('/api/workspaces/{slug}/groups',groups);a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',group_modify);a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',group_modify);a.router.add_post('/api/messages/{id}/reactions',reaction_change);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction_change);a.router.add_post('/api/dms',dms);a.router.add_post('/api/files',upload_file);a.router.add_get('/api/files/{id}',get_file);a.router.add_get('/api/files/{id}/download',download_file);a.router.add_get('/api/workspaces/{slug}/groups',c_placeholder);a.router.add_post('/api/workspaces/{slug}/groups',c_placeholder);a.router.add_patch('/api/workspaces/{slug}/groups/{handle}',c_placeholder);a.router.add_delete('/api/workspaces/{slug}/groups/{handle}',c_placeholder);a.router.add_get('/api/users/{id}',get_user);a.router.add_patch('/api/users/me',patch_profile);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction_change);a.router.add_post('/api/messages/{id}/pin',pin_message);a.router.add_delete('/api/messages/{id}/pin',unpin_message);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_post('/api/channels/{id}/leave',leave_channel);a.router.add_post('/api/channels/{id}/archive',archive_channel);a.router.add_post('/api/channels/{id}/unarchive',archive_channel);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',post_message);a.router.add_get('/api/ws',ws);a.router.add_get('/',root)
    return a
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--node',type=int,default=0);NODE=ap.parse_args().node
    core.init_db();web.run_app(app(),host='127.0.0.1',port=8000+NODE)
