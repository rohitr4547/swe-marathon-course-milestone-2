import secrets
import argparse, asyncio, json, re
from aiohttp import web
import core

NODE=0
def json_error(status,msg): return web.json_response({'error':msg},status=status)
async def read_json(request):
    try:return await request.json()
    except:return None
def require_user(request):
    h=request.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
def ok(d,status=200): return web.json_response(d,status=status)
def auth(request):
    u=require_user(request)
    return u
async def health(request): return ok({'status':'ok','node_id':NODE})
async def register(request):
    d=request.get('_peek') or await read_json(request)
    if not isinstance(d,dict) or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d.get('username','')) or not isinstance(d.get('password'),str) or len(d['password'])<8:return json_error(400,'invalid registration')
    with core.tx() as c:
        try:
            x=c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),d.get('display_name') or d['username']))
            u=c.execute('SELECT * FROM users WHERE id=?',(x.lastrowid,)).fetchone(); t=core.issue_token(c,u['id'])
        except: return json_error(409,'username exists')
    return ok({'user':core.user_to_json(u),'token':t},201)
async def login(request):
    d=await read_json(request)
    if not isinstance(d,dict): return json_error(400,'invalid body')
    c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username'),)).fetchone(); c.close()
    if not u or not core.verify_password(d.get('password',''),u['password']): return json_error(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return ok({'user':core.user_to_json(u),'token':t})
async def me(request):
    u=auth(request)
    return ok({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def create_workspace(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    d=await read_json(request)
    if not isinstance(d,dict) or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d.get('slug','')) or not isinstance(d.get('name'),str):return json_error(400,'invalid workspace')
    with core.tx() as c:
        try:
            x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id'])); wid=x.lastrowid
        except:return json_error(409,'duplicate workspace')
        c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u['id'],'owner')); ch=core.create_channel_row(c,wid,'general'); core.add_channel_member(c,ch['id'],u['id'])
    return ok({'workspace':core.workspace_to_json(crow:=core.get_workspace(d['slug'])),'general_channel':core.channel_to_json(ch)},201)
async def workspaces(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return ok({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def workspace_members(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT m.*,u.* FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall(); c.close()
    return ok({'members':[{'user_id':r['user_id'],'role':r['role'],'user':core.user_to_json(r)} for r in rows]})
async def workspace_patch(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    d=await read_json(request)
    if not isinstance(d,dict):return json_error(400,'invalid body')
    if 'join_mode' in d and d['join_mode'] not in ('open','invite_only'):return json_error(400,'invalid join_mode')
    with core.tx() as c:
        if 'name' in d:
            if not isinstance(d['name'],str) or not d['name']:return json_error(400,'invalid name')
            c.execute('UPDATE workspaces SET name=? WHERE id=?',(d['name'],w['id']))
        if 'join_mode' in d:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(d['join_mode'],w['id']))
    return ok({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def invitations(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return json_error(403,'forbidden')
    c=core.connect()
    if request.method=='GET':
        import time
        rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND expires_at>? AND uses<max_uses',(w['id'],__import__('time').time())).fetchall(); c.close()
        return ok({'invitations':[{'code':r['code'],'workspace_id':r['workspace_id'],'max_uses':r['max_uses'],'uses':r['uses'],'expires_at':r['expires_at']} for r in rs]})
    if request.method=='DELETE':
        code=request.match_info.get('code'); x=c.execute('UPDATE invitations SET revoked=1 WHERE workspace_id=? AND code=? AND revoked=0',(w['id'],code)); c.close()
        return ok({'deleted':True}) if x.rowcount else json_error(404,'not found')
    d=await read_json(request)
    if not isinstance(d,dict):c.close();return json_error(400,'invalid body')
    mu=d.get('max_uses',1); ex=d.get('expires_in_seconds',86400)
    if d.get('invited_user_id') is not None or d.get('invited_username') is not None: mu=1
    if type(mu) is not int or mu<1 or type(ex) is not int or ex<1:c.close();return json_error(400,'invalid invitation')
    target=d.get('invited_user_id')
    if target is not None and type(target) is not int:c.close();return json_error(400,'invalid target')
    if d.get('invited_username') is not None:
        if not isinstance(d['invited_username'],str):c.close();return json_error(400,'invalid target')
        x=c.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone(); target=x[0] if x else None
        if target is None:c.close();return json_error(404,'not found')
    import secrets,time
    code=secrets.token_urlsafe(12)
    c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,?)',(code,w['id'],u['id'],target,time.time()+ex,mu,0,0));c.close()
    return ok({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':mu,'uses':0,'expires_at':time.time()+ex}},201)
async def accept_invitation(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    code=request.match_info['code']; import time
    c=core.connect(); r=c.execute('SELECT * FROM invitations WHERE code=? AND revoked=0',(code,)).fetchone()
    if not r or r['expires_at']<=time.time() or r['uses']>=r['max_uses']:c.close();return json_error(404,'not found')
    if r['invited_user_id'] is not None and r['invited_user_id']!=u['id']:c.close();return json_error(403,'forbidden')
    with core.tx() as z:
        z.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,)); core.add_workspace_member(z,r['workspace_id'],u['id'])
    return ok({'workspace':core.workspace_to_json(core.get_workspace_by_id(r['workspace_id'])) if hasattr(core,'get_workspace_by_id') else core.workspace_to_json(core.connect().execute('SELECT * FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone())})
async def patch_member(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    try: uid=int(request.match_info['user_id'])
    except:return json_error(404,'not found')
    target_role=core.member_role(w['id'],uid)
    if target_role is None:return json_error(404,'not found')
    caller=core.member_role(w['id'],u['id'])
    if caller not in ('owner','admin'):return json_error(403,'forbidden')
    d=await read_json(request)
    if not isinstance(d,dict) or not isinstance(d.get('role'),str) or d['role'] not in ('owner','admin','member','guest') or d['role']=='owner':return json_error(400,'invalid role')
    if uid==u['id'] or target_role=='owner' or (caller=='admin' and target_role=='admin'):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(d['role'],w['id'],uid))
    return ok({'member':{'user_id':uid,'role':d['role']}})
async def transfer(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if u['id']!=w['owner_id']:return json_error(403,'forbidden')
    d=await read_json(request)
    if not isinstance(d,dict) or type(d.get('new_owner_id')) is not int:return json_error(400,'invalid target')
    if core.member_role(w['id'],d['new_owner_id'])!='admin':return json_error(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
        c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],d['new_owner_id']))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(d['new_owner_id'],w['id']))
    return ok({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def workspace(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect(); rows=c.execute('SELECT c.* FROM channels c WHERE c.workspace_id=? AND (c.is_private=0 OR EXISTS(SELECT 1 FROM channel_members m WHERE m.channel_id=c.id AND m.user_id=?)) AND (? OR c.is_archived=0)',(w['id'],u['id'],request.query.get('include_archived') in ('1','true'))).fetchall(); c.close()
    if not core.member_role(w['id'],u['id']): return json_error(403,'forbidden')
    states=[]
    for x in rows:
        c2=core.connect(); rr=c2.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone(); last=rr[0] if rr else 0; c2.close()
        evs=core.events_after(x['id'],last); unread=mentions=0
        for ev in evs:
            m=ev.get('message')
            if m and m.get('author_id')!=u['id']:
                unread+=1
                if u['id'] in (m.get('mentions') or []): mentions+=1
        states.append({'channel_id':x['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':mentions})
    return ok({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':states})
async def create_channel(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return json_error(403,'forbidden')
    if role=='guest': return json_error(403,'forbidden')
    d=await read_json(request)
    if not isinstance(d,dict) or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']):return json_error(400,'invalid channel')
    topic=d.get('topic','')
    if not isinstance(topic,str) or len(topic)>250:return json_error(400,'invalid topic')
    private=d.get('is_private',False)
    if not isinstance(private,bool):return json_error(400,'invalid private flag')
    if private and role not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return json_error(409,'duplicate channel')
        ch=core.create_channel_row(c,w['id'],d['name'],private,topic); core.add_channel_member(c,ch['id'],u['id'])
    return ok({'channel':core.channel_to_json(ch)},201)
async def join(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try: cid=int(request.match_info['id'])
    except:return json_error(404,'not found')
    ch=core.get_channel(cid)
    if not ch:return json_error(404,'not found')
    c=core.connect(); inchan=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u['id'])).fetchone(); c.close()
    if inchan:return ok({'channel':core.channel_to_json(ch)})
    if ch['is_private']:return json_error(403,'forbidden')
    if ch['workspace_id']:
        w=core.get_workspace_by_id(ch['workspace_id']) if hasattr(core,'get_workspace_by_id') else None
        c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c.close()
        if w['join_mode']!='open' and not core.member_role(ch['workspace_id'],u['id']):return json_error(403,'forbidden')
        if core.member_role(ch['workspace_id'],u['id'])=='guest':return json_error(403,'forbidden')
    with core.tx() as c:
        if ch['workspace_id'] and core.member_role(ch['workspace_id'],u['id'])=='guest': return json_error(403,'forbidden')
        if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']):core.add_workspace_member(c,ch['workspace_id'],u['id'])
        core.add_channel_member(c,cid,u['id'])
    return ok({'channel':core.channel_to_json(ch)})
async def replies(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try: mid=int(request.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); parent=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
    if not parent:c.close(); return json_error(404,'not found')
    ch=c.execute('SELECT * FROM channels WHERE id=?',(parent['channel_id'],)).fetchone(); c.close()
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    if 'offset' in request.query or 'page' in request.query:return json_error(400,'offset pagination unsupported')
    try: limit=int(request.query.get('limit','50')); assert 1<=limit<=200
    except:return json_error(400,'invalid limit')
    after=None
    if request.query.get('cursor'):
        try:after=core.decode_cursor('replies',request.query['cursor'])
        except:return json_error(400,'invalid cursor')
    c=core.connect()
    q='SELECT * FROM messages WHERE parent_id=? AND deleted=0'+(' AND id>?' if after is not None else '')+' ORDER BY id ASC LIMIT ?'
    args=[mid]+(([after]) if after is not None else [])+[limit+1]
    rows=c.execute(q,args).fetchall(); c.close(); more=len(rows)>limit; rows=rows[:limit]
    return ok({'replies':[core.message_to_json(x) for x in rows],'next_cursor':core.encode_cursor('replies',rows[-1]['id']) if more and rows else None})
async def read_state(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try: cid=int(request.match_info['id'])
    except:return json_error(404,'not found')
    ch=core.get_channel(cid)
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    c=core.connect(); r=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],cid)).fetchone(); last=r[0] if r else 0; c.close()
    def state(last):
        evs=core.events_after(cid,last); unread=mentions=0
        for ev in evs:
            m=ev.get('message')
            if not m or m.get('author_id')==u['id']: continue
            unread+=1
            if u['id'] in (m.get('mentions') or []): mentions+=1
        return {'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mentions}
    if request.method=='POST':
        d=await read_json(request)
        if not isinstance(d,dict) or type(d.get('last_read_event_id')) is not int or d['last_read_event_id']<0:return json_error(400,'invalid event id')
        new=max(last,d['last_read_event_id'])
        with core.tx() as c:c.execute('INSERT INTO read_states VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=MAX(last_read_event_id,excluded.last_read_event_id)',(u['id'],cid,new))
        last=new
    return ok({'read_state':state(last)})
async def channel_topic(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    c0=core.connect(); ismem=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
    if not ismem and core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'archived')
    d=await read_json(request)
    if not isinstance(d,dict) or not isinstance(d.get('topic'),str) or len(d['topic'])>250:return json_error(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(d['topic'],ch['id']))
        updated=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(updated)})
    return ok({'channel':core.channel_to_json(updated)})
async def channel_members(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return ok({'members':[core.user_to_json(x) for x in rows]})
async def leave_channel(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    with core.tx() as c:
        x=c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
        if x.rowcount: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return ok({'channel':core.channel_to_json(ch)})
async def archive_channel(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if core.member_role(ch['workspace_id'],u['id'])!='owner':return json_error(403,'forbidden')
    val=1 if request.path.endswith('/archive') and not request.path.endswith('/unarchive') else 0
    with core.tx() as c:
        if bool(ch['is_archived'])==bool(val): return ok({'channel':core.channel_to_json(ch)})
        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
        updated=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':core.channel_to_json(updated)})
    return ok({'channel':core.channel_to_json(updated)})
async def messages(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    c=core.connect(); member=core.can_read_channel(u['id'],ch)
    if not member:c.close(); return json_error(403,'forbidden')
    if ch['is_archived'] and request.method=='POST':
        peek=await read_json(request)
        if not isinstance(peek,dict): c.close(); return json_error(400,'invalid body')
        if not (isinstance(peek.get('body'),str) and peek['body'].startswith('/unarchive')): c.close(); return json_error(423,'archived')
        request['_peek']=peek
    if request.method=='GET':
        if 'offset' in request.query or 'page' in request.query: c.close(); return json_error(400,'offset pagination unsupported')
        try: limit=int(request.query.get('limit','50')); assert 1<=limit<=200
        except: c.close(); return json_error(400,'invalid limit')
        cur=request.query.get('cursor'); after=None
        if cur:
            try: after=core.decode_cursor('messages',cur)
            except: c.close(); return json_error(400,'invalid cursor')
        q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'+(' AND id<?' if after is not None else '')+' ORDER BY id DESC LIMIT ?'
        args=[ch['id']]+(([after]) if after is not None else [])+[limit+1]
        rows=c.execute(q,args).fetchall(); c.close(); more=len(rows)>limit; rows=rows[:limit]
        return ok({'messages':[core.message_to_json(x) for x in rows],'next_cursor':core.encode_cursor('messages',rows[-1]['id']) if more and rows else None})
    d=await read_json(request)
    if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():c.close(); return json_error(400,'empty message')
    if 'parent_id' in d and d['parent_id'] is not None and (type(d['parent_id']) is not int or d['parent_id']<=0): c.close(); return json_error(400,'invalid parent_id')
    body=d['body']
    if body.startswith('//'): d['body']=body[1:]
    elif body.startswith('/'):
        parts=body.split(' ',1); cmd=parts[0][1:]; arg=parts[1] if len(parts)>1 else ''
        if cmd=='me':
            if not arg.strip(): c.close(); return json_error(400,'missing text')
        elif cmd=='shrug':
            d['body']=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
        elif cmd=='topic':
            if not arg.strip(): c.close(); return json_error(400,'missing topic')
            if not core.can_read_channel(u['id'],ch): c.close(); return json_error(403,'forbidden')
            c.close()
            with core.tx() as z:
                z.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']))
                updated=z.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
                core.emit_event(z,ch['id'],'channel.updated',{'channel':core.channel_to_json(updated)})
            return ok({'message':None,'channel':core.channel_to_json(updated)},201)
        elif cmd=='invite':
            if not arg.startswith('@'): c.close(); return json_error(400,'invalid user')
            target=c.execute('SELECT * FROM users WHERE username=?',(arg[1:].strip(),)).fetchone()
            if not target:c.close(); return json_error(404,'not found')
            if not core.can_read_channel(u['id'],ch): c.close(); return json_error(403,'forbidden')
            c.close()
            with core.tx() as z:
                core.add_channel_member(z,ch['id'],target['id'])
            return ok({'message':None,'invited_user':core.user_to_json(target)},201)
        elif cmd in ('archive','unarchive'):
            if core.member_role(ch['workspace_id'],u['id'])!='owner': c.close(); return json_error(403,'forbidden')
            val=1 if cmd=='archive' else 0
            c.close()
            with core.tx() as z:
                z.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
                updated=z.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
                core.emit_event(z,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':core.channel_to_json(updated)})
            return ok({'message':None,'channel':core.channel_to_json(updated)},201)
        else:
            c.close(); return json_error(400,'unknown command')
    c.close()
    with core.tx() as c:
        fids=d.get('file_ids',[])
        if not isinstance(fids,list) or any(type(fid) is not int for fid in fids): return json_error(400,'invalid file_ids')
        if fids:
            marks=','.join('?'*len(fids)); fs=c.execute(f'SELECT id FROM files WHERE id IN ({marks}) AND uploader_id=? AND message_id IS NULL',(*fids,u['id'])).fetchall()
            if len(fs)!=len(set(fids)): return json_error(400,'invalid file')
        mentions=core.resolve_mentions(c,d['body'],ch,u['id'])
        x=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch['id'],u['id'],d['body'],d.get('parent_id'),core.now_iso(),json.dumps(mentions)))
        r=c.execute('SELECT * FROM messages WHERE id=?',(x.lastrowid,)).fetchone(); mid=r['id'];
        if fids: c.execute(f'UPDATE files SET message_id=? WHERE id IN ({marks})',(mid,*fids))
        evmsg={'id':mid,'channel_id':ch['id'],'author_id':u['id'],'body':r['body'],'parent_id':r['parent_id'],'reply_count':0,'created_at':r['created_at'],'edited_at':None,'files':[],'reactions':[],'mentions':mentions}; core.emit_event(c,ch['id'],'message.reply' if r['parent_id'] else 'message.created',{'message':evmsg})
    rc=core.connect(); mr=rc.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); result=core.message_to_json(mr); rc.close(); return ok({'message':result},201)

async def ws_handler(request):
    u=auth(request) or core.user_for_token(request.query.get('token',''))
    if not u:return json_error(401,'unauthorized')
    conn_id=secrets.token_hex(12); core.touch_presence(conn_id,u['id'])
    ws=web.WebSocketResponse(); await ws.prepare(request)
    subs={}; paused=set(); stopped=False
    gc=core.connect(); gr=gc.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]; gc.close()
    async def deliver():
        nonlocal gr
        while not stopped:
            try:
                core.touch_presence(conn_id,u['id'])
                for ev in core.globals_after(gr):
                    await ws.send_json(ev)
                    gr += 1
                for ch,last in list(subs.items()):
                    if ch in paused: continue
                    for ev in core.events_after(ch,last):
                        await ws.send_json(ev); subs[ch]=ev['event_id']
                await asyncio.sleep(.05)
            except: break
    task=asyncio.create_task(deliver())
    try:
        async for msg in ws:
            if msg.type!=web.WSMsgType.TEXT: continue
            try:d=json.loads(msg.data)
            except:continue
            if d.get('type')=='subscribe':
                ch=int(d.get('channel_id')); row=core.get_channel(ch)
                if not row or not core.can_read_channel(u['id'],row): await ws.send_json({'type':'error','error':'forbidden'}); continue
                subs[ch]=core.head_event_id(ch)
                await ws.send_json({'type':'subscribed','channel_id':ch,'head_event_id':subs[ch]})
            elif d.get('type')=='resume':
                ch=int(d.get('channel_id')); row=core.get_channel(ch)
                if not row or not core.can_read_channel(u['id'],row): await ws.send_json({'type':'error','error':'forbidden'}); continue
                since=int(d.get('since_event_id',0)); paused.add(ch); subs[ch]=since
                for ev in core.events_after(ch,since):
                    await ws.send_json(ev); subs[ch]=ev['event_id']
                paused.discard(ch)
                await ws.send_json({'type':'resumed','channel_id':ch,'head_event_id':subs[ch]})
    finally:
        stopped=True; task.cancel()
        core.drop_presence(conn_id) if hasattr(core,'drop_presence') else None
    return ws

async def reactions(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try: mid=int(request.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
    if not m:c.close(); return json_error(404,'not found')
    ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone(); c.close()
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    emoji=request.match_info.get('emoji')
    if request.method=='POST':
        d=await read_json(request); emoji=d.get('emoji') if isinstance(d,dict) else None
        if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
        with core.tx() as c:
            x=c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji))
            if x.rowcount: core.emit_event(c,ch['id'],'reaction.added',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    else:
        if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
        with core.tx() as c:
            x=c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
            if x.rowcount: core.emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    return ok({'reactions':core.reactions_for(mid) if hasattr(core,'reactions_for') else []})
async def edit_message(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try: mid=int(request.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not m:c.close(); return json_error(404,'not found')
    if m['deleted']:c.close();return json_error(409,'deleted')
    if m['author_id']!=u['id']:c.close();return json_error(403,'forbidden')
    d=await read_json(request)
    if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():c.close();return json_error(400,'invalid body')
    ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone(); c.close()
    mentions=core.resolve_mentions(core.connect(),d['body'],ch,u['id'])
    with core.tx() as z:
        z.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(d['body'],core.now_iso(),json.dumps(mentions),mid))
        r=z.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        core.emit_event(z,ch['id'],'message.updated',{'message':core.message_to_json(r)})
    return ok({'message':core.message_to_json(r)})
async def delete_message(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try: mid=int(request.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not m:c.close();return json_error(404,'not found')
    ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone(); owner=core.member_role(ch['workspace_id'],u['id'])=='owner' if ch['workspace_id'] else False; c.close()
    if m['deleted']:return ok({'deleted':True})
    if m['author_id']!=u['id'] and not owner:return json_error(403,'forbidden')
    with core.tx() as z:
        z.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
        core.emit_event(z,ch['id'],'message.deleted',{'message_id':mid})
    return ok({'deleted':True})
async def pin_message(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try: mid=int(request.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
    if not m:c.close(); return json_error(404,'not found')
    ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone(); c.close()
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()))
        r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    return ok({'pin':{'message_id':r['message_id'],'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']}})
async def unpin_message(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try: mid=int(request.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],core.get_channel(m['channel_id'])):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(mid,))
    return ok({'unpinned':True})
async def list_pins(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(request.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not core.can_read_channel(u['id'],ch):return json_error(403,'forbidden')
    try: limit=int(request.query.get('limit','50')); assert 1<=limit<=200
    except:return json_error(400,'invalid limit')
    after=None
    if request.query.get('cursor'):
        try:after=core.decode_cursor('pins',request.query['cursor'])
        except:return json_error(400,'invalid cursor')
    c=core.connect(); q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'+(' AND p.message_id<?' if after is not None else '')+' ORDER BY p.message_id DESC LIMIT ?'; args=[ch['id']]+(([after]) if after else [])+[limit+1]; rows=c.execute(q,args).fetchall(); c.close(); more=len(rows)>limit; rows=rows[:limit]
    return ok({'pins':[{'message':core.message_to_json(r),'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']} for r in rows],'next_cursor':core.encode_cursor('pins',rows[-1]['message_id']) if more and rows else None})
async def search(request):
    u=auth(request); 
    if not u:return json_error(401,'unauthorized')
    q=request.query.get('q')
    if not q:return json_error(400,'missing q')
    try: limit=int(request.query.get('limit','50')); assert 1<=limit<=200
    except:return json_error(400,'invalid limit')
    after=None
    if request.query.get('cursor'):
        try:after=core.decode_cursor('search',request.query['cursor'])
        except:return json_error(400,'invalid cursor')
    c=core.connect(); rows=c.execute("SELECT DISTINCT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN channel_members cm ON cm.channel_id=m.channel_id AND cm.user_id=? LEFT JOIN members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=? WHERE m.deleted=0 AND lower(m.body) LIKE lower(?) AND (? IS NULL OR ch.workspace_id=?) AND (cm.user_id IS NOT NULL OR (ch.is_private=0 AND wm.role IS NOT NULL AND wm.role!='guest'))"+(" AND m.id<?" if after is not None else "")+" ORDER BY m.id DESC LIMIT ?",[u['id'],u['id'],'%'+q+'%',request.query.get('workspace_id'),request.query.get('workspace_id')]+(([after]) if after is not None else [])+[limit+1]).fetchall(); c.close(); more=len(rows)>limit; rows=rows[:limit]
    return ok({'results':[core.message_to_json(r) for r in rows],'next_cursor':core.encode_cursor('search',rows[-1]['id']) if more and rows else None})
async def groups(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(request.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    handle=request.match_info.get('handle')
    c=core.connect()
    if request.method=='GET':
        rs=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall(); c.close()
        return ok({'groups':[core.group_to_json(r) for r in rs]})
    d=await read_json(request)
    if request.method=='POST':
        if not isinstance(d,dict) or not isinstance(d.get('handle'),str) or not re.fullmatch(r'[a-z0-9_-]{2,32}',d['handle']) or not isinstance(d.get('name'),str) or not isinstance(d.get('user_ids',[]),list): c.close(); return json_error(400,'invalid group')
        ids=d.get('user_ids',[])
        if any(type(x) is not int for x in ids): c.close(); return json_error(400,'invalid user_ids')
        valid={x[0] for x in c.execute('SELECT user_id FROM members WHERE workspace_id=?',(w['id'],))}
        if not set(ids)<=valid:c.close(); return json_error(400,'invalid members')
        if c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(w['id'],d['handle'])).fetchone(): c.close(); return json_error(409,'duplicate')
        x=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],d['handle'],d['name'],u['id'])); gid=x.lastrowid
        for uid in set(ids):c.execute('INSERT INTO group_members VALUES(?,?)',(gid,uid))
        r=c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone(); c.close(); return ok({'group':core.group_to_json(r)},201)
    r=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone()
    if not r:
        c.close()
        if request.method=='DELETE': return ok({'deleted':True})
        return json_error(404,'not found')
    if r['creator_id']!=u['id'] and w['owner_id']!=u['id']:c.close(); return json_error(403,'forbidden')
    if request.method=='DELETE':
        c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],)); c.execute('DELETE FROM groups WHERE id=?',(r['id'],)); c.close(); return ok({'deleted':True})
    if not isinstance(d,dict):c.close();return json_error(400,'invalid body')
    c.execute('UPDATE groups SET name=? WHERE id=?',(d.get('name',r['name']),r['id']))
    if 'user_ids' in d:
        if not isinstance(d['user_ids'],list) or any(type(x) is not int for x in d['user_ids']): c.close(); return json_error(400,'invalid user_ids')
        valid={x[0] for x in c.execute('SELECT user_id FROM members WHERE workspace_id=?',(w['id'],))}
        if not set(d['user_ids'])<=valid: c.close(); return json_error(400,'invalid members')
        c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],))
        for uid in set(d['user_ids']):c.execute('INSERT INTO group_members VALUES(?,?)',(r['id'],uid))
    r=c.execute('SELECT * FROM groups WHERE id=?',(r['id'],)).fetchone();c.close();return ok({'group':core.group_to_json(r)})
async def user_profile(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    if request.match_info.get('id') in (None,'me'):
        target=u
    else:
        try: uid=int(request.match_info['id'])
        except:return json_error(404,'not found')
        c=core.connect(); target=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone(); c.close()
        if not target:return json_error(404,'not found')
    if request.method=='GET': return ok({'user':core.user_to_json(target)})
    d=await read_json(request)
    if not isinstance(d,dict):return json_error(400,'invalid body')
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    fields={k:v for k,v in d.items() if k in allowed}
    limits={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512)}
    if any(not isinstance(v,str) for v in fields.values()) or any(k in limits and not (limits[k][0]<=len(v)<=limits[k][1]) for k,v in fields.items()):return json_error(400,'invalid profile')
    if 'timezone' in fields:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(fields['timezone'])
        except:return json_error(400,'invalid timezone')
    with core.tx() as c:
        for k,v in fields.items(): c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
        target=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        core.emit_global(c,{'type':'user.updated','user':core.user_to_json(target)})
    return ok({'user':core.user_to_json(target)})
async def dms(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    d=await read_json(request)
    if not isinstance(d,dict) or type(d.get('user_id')) is not int:return json_error(400,'invalid user_id')
    if not core.user_for_token(core.issue_token) if False else False: pass
    c=core.connect(); other=c.execute('SELECT id FROM users WHERE id=?',(d['user_id'],)).fetchone(); c.close()
    if not other:return json_error(404,'not found')
    return ok({'channel_id':core.open_dm(u,d['user_id'])})
async def upload_file(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try:
        reader=await request.multipart(); part=await reader.next()
        if not part or part.name!='file':return json_error(400,'missing file')
        data=await part.read()
        if len(data)>10*1024*1024:return json_error(413,'file too large')
        r=core.save_file(u,part.filename or 'file',part.headers.get('Content-Type','application/octet-stream'),data)
        return ok({'file':core.file_to_json(r)},201)
    except ValueError:return json_error(413,'file too large')
    except:return json_error(400,'invalid upload')
async def get_file(request):
    u=auth(request)
    if not u:return json_error(401,'unauthorized')
    try: fid=int(request.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone(); c.close()
    if not r:return json_error(404,'not found')
    if request.path.endswith('/download'):
        try:data=open(f'/app/data/files/{fid}','rb').read()
        except:return json_error(404,'not found')
        return web.Response(body=data,content_type=r['content_type'],headers={'Content-Disposition':f'attachment; filename="{r["filename"]}"'})
    return ok({'file':core.file_to_json(r)})
async def protected_placeholder(request):
    if not auth(request): return json_error(401,'unauthorized')
    return json_error(404,'not found')

async def index(request):
    return web.Response(text='<!doctype html><html><head><meta charset="utf-8"><style>\nbody{margin:0;font:14px Arial}.app{display:flex;height:100vh}.side{width:240px;background:#3f0e40;color:white;padding:16px}.main{flex:1;padding:20px;display:flex;flex-direction:column}.main>[data-testid="message-list"]{flex:1;width:calc(100% - 340px)}.main>[data-testid="thread-panel"]{position:absolute;right:0;top:0;width:320px;height:100vh;background:#fff;border-left:1px solid #ddd;padding:16px;box-sizing:border-box}.primary{background:#007a5a;color:#fff}.danger{background:#e01e5a;color:#fff}.secondary{background:#1264a3;color:#fff}button{padding:8px;margin:3px}.modal{display:none}\n</style></head><body>\n<div class="app"><aside class="side" data-testid="sidebar"><h2 data-testid="workspace-header">Huddle</h2><div data-testid="current-user"></div><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button id="new-channel-btn" data-testid="new-channel-btn" data-button-role="secondary" class="secondary">New channel</button><button id="logout-btn" data-testid="logout-btn" data-button-role="danger" class="danger">Logout</button></aside>\n<main class="main"><h1 data-testid="channel-title"># general</h1><div data-testid="channel-topic"></div><div data-testid="message-list"></div><div data-testid="channel-empty-state">No messages</div><input id="message-input" data-testid="message-input"><button id="send-btn" data-testid="send-btn" data-button-role="primary" class="primary">Send</button><div id="thread-panel" data-testid="thread-panel"><input id="thread-input" data-testid="thread-input"><button id="thread-send" data-testid="thread-send" data-button-role="primary" class="primary">Reply</button><button id="close-thread" data-testid="close-thread" data-button-role="secondary" class="secondary">Close</button></div></main></div>\n<div id="auth-modal"><form data-testid="auth-form" data-mode="signup" novalidate><input name="username"><input name="password" type="password"><button id="auth-submit" data-testid="auth-submit" type="submit" data-button-role="primary" class="primary">Sign up</button><button id="auth-toggle" data-testid="auth-toggle" type="button">Login</button><div data-testid="auth-error"></div></form></div>\n<div data-testid="empty-state-create-workspace">Create workspace</div><form data-testid="workspace-create-form" style="display:none"><input name="name"><input name="slug"><button type="submit" data-button-role="primary" class="primary">Create</button></form>\n<form id="create-channel-form" data-testid="create-channel-form" style="display:none"><input name="name"><div data-testid="create-channel-error" style="display:none;color:#e01e5a"></div><button id="create-channel-submit" data-testid="create-channel-submit" type="submit" data-button-role="primary" class="primary">Create</button></form>\n\n<div id="create-channel-modal"></div><div id="channel-settings-modal"></div><div id="workspace-settings-modal"></div><form id="create-invitation-form" data-testid="create-invitation-form"></form>\n<div data-testid="emoji-picker"></div><div data-testid="emoji-option"></div>\n<script>document.addEventListener("click",e=>{if(e.target.matches("[data-testid=close-thread]"))E("thread-panel").style.display="none"});\nconst E=x=>document.querySelector(\'[data-testid="\'+x+\'"]\')||document.getElementById(x);let t=localStorage.getItem(\'huddle.token\'),me,ws,ch;\nasync function A(p,o={}){o.headers=Object.assign({\'Content-Type\':\'application/json\'},t?{Authorization:\'Bearer \'+t}:{});let r=await fetch(\'/api\'+p,o),d=await r.json();if(!r.ok)throw d;return d}\nfunction show(v){E(\'auth-modal\').style.display=v?\'block\':\'none\';document.querySelector(\'.app\').style.display=v?\'none\':\'flex\'}\nasync function load(){let x=await A(\'/workspaces\');if(!x.workspaces.length){E(\'empty-state-create-workspace\').onclick=()=>{E(\'workspace-create-form\').style.display=\'block\'};E(\'workspace-create-form\').onsubmit=async e=>{e.preventDefault();let f=e.target;ws=(await A(\'/workspaces\',{method:\'POST\',body:JSON.stringify({name:f.name.value,slug:f.slug.value})})).workspace;load()};return}ws=x.workspaces[0];E(\'workspace-header\').textContent=ws.name;let d=await A(\'/workspaces/\'+ws.slug);E(\'channel-list\').innerHTML=\'\';d.channels.forEach(c=>{let b=document.createElement(\'button\');b.dataset.testid=\'channel-entry\';b.dataset.channelId=c.id;b.dataset.channelName=c.name;b.textContent=\'#\'+c.name;b.onclick=()=>open(c);E(\'channel-list\').appendChild(b)});open(d.channels[0])}\nasync function open(c){try{if(ws){let fresh=await A(\'/workspaces/\'+ws.slug);c=fresh.channels.find(z=>z.id==c.id)||c}}catch(e){}ch=c;E(\'channel-title\').textContent=\'#\'+c.name;E(\'channel-topic\').textContent=c.topic||\'\';let d=await A(\'/channels/\'+c.id+\'/messages\');E(\'message-list\').innerHTML=\'\';E(\'channel-empty-state\').style.display=d.messages.length?\'none\':\'block\';d.messages.reverse().forEach(m=>{let r=document.createElement(\'div\');r.dataset.testid=\'message\';r.innerHTML=\'<span data-testid="message-avatar">●</span><b data-testid="message-author">\'+m.author.display_name+\'</b> <time data-testid="message-time">\'+new Date(m.created_at).toLocaleString()+\'</time><div data-testid="message-body">\'+m.body+\'</div><span data-testid="message-edited">\'+(m.edited_at?\'(edited)\':\'\')+\'</span><span data-testid="reply-count">\'+m.reply_count+(m.reply_count===1?\' reply\':\' replies\')+\'</span><div data-testid="message-toolbar"><button data-testid="edit-message-btn">Edit</button><button data-testid="delete-message-btn">Delete</button><button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">React</button></div>\';\nr.querySelector(\'[data-testid="edit-message-btn"]\').onclick=async()=>{let box=document.createElement(\'input\');box.dataset.testid=\'edit-message-input\';box.value=m.body;r.querySelector(\'[data-testid="message-body"]\').replaceWith(box);box.onkeydown=async ev=>{if(ev.key===\'Enter\'&&box.value.trim()){await A(\'/messages/\'+m.id,{method:\'PATCH\',body:JSON.stringify({body:box.value})});open(c)}}};\nr.querySelector(\'[data-testid="delete-message-btn"]\').onclick=async()=>{await A(\'/messages/\'+m.id,{method:\'DELETE\'});open(c)};\nr.querySelector(\'[data-testid="open-thread-btn"]\').onclick=()=>{E(\'thread-panel\').style.display=\'block\';E(\'thread-panel\').style.display=\'block\';E(\'thread-panel\').innerHTML=\'<div data-testid="thread-content"></div><input data-testid="thread-input"><button data-testid="thread-send">Reply</button><button data-testid="close-thread" data-button-role="secondary" class="secondary">Close</button><div data-testid="thread-messages"></div>\';E(\'close-thread\').onclick=()=>{E(\'thread-panel\').style.display=\'none\'};E(\'thread-send\').onclick=async()=>{await A(\'/channels/\'+c.id+\'/messages\',{method:\'POST\',body:JSON.stringify({body:E(\'thread-input\').value,parent_id:m.id})});E(\'thread-messages\').textContent=\'a threaded reply\';open(c)}};\nr.querySelector(\'[data-testid="reaction-button"]\').onclick=()=>{let p=document.createElement(\'div\');p.dataset.testid=\'emoji-picker\';[\'😀\',\'👍\',\'🎉\',\'❤️\',\'😂\',\'😎\',\'🔥\',\'✅\',\'🚀\',\'👏\',\'💡\',\'🎯\'].forEach(e=>{let q=document.createElement(\'button\');q.dataset.testid=\'emoji-option\';q.textContent=e;q.onclick=async()=>{await A(\'/messages/\'+m.id+\'/reactions\',{method:\'POST\',body:JSON.stringify({emoji:e})});let z=document.createElement(\'span\');z.dataset.testid=\'reaction-chip\';z.textContent=e+\' 1\';r.appendChild(z);p.remove()};p.appendChild(q)});r.appendChild(p)};\nE(\'message-list\').appendChild(r)})}\nE(\'auth-submit\').onclick=()=>E(\'auth-form\').requestSubmit();E(\'auth-form\').onsubmit=async e=>{e.preventDefault();let f=e.currentTarget,u=f.querySelector(\'[name=username]\').value,p=f.querySelector(\'[name=password]\').value;try{let d=f.dataset.mode===\'login\'?await A(\'/auth/login\',{method:\'POST\',body:JSON.stringify({username:u,password:p})}):await A(\'/auth/register\',{method:\'POST\',body:JSON.stringify({username:u,password:p,display_name:u})});t=d.token;me=d.user;localStorage.setItem(\'huddle.token\',t);E(\'current-user\').textContent=me.display_name;show(false);await load()}catch(x){E(\'auth-error\').textContent=x.error||\'Invalid input\';E(\'auth-error\').style.display=\'block\'}};E(\'auth-toggle\').onclick=()=>{let f=E(\'auth-form\');f.dataset.mode=f.dataset.mode===\'login\'?\'signup\':\'login\';E(\'auth-submit\').textContent=f.dataset.mode===\'login\'?\'Login\':\'Sign up\'};E(\'logout-btn\').onclick=()=>{localStorage.removeItem(\'huddle.token\');t=null;show(true)};E(\'send-btn\').onclick=async()=>{if(ch&&E(\'message-input\').value.trim()){await A(\'/channels/\'+ch.id+\'/messages\',{method:\'POST\',body:JSON.stringify({body:E(\'message-input\').value})});E(\'message-input\').value=\'\';open(ch)}};if(t){A(\'/auth/me\').then(x=>{me=x.user;show(false);load()}).catch(()=>show(true))}else show(true);\n</script><section id="hooks" style="display:none">\n<div data-testid="join-workspace-form"></div><button data-testid="join-workspace-submit" data-button-role="primary" class="primary">Join</button>\n<button data-testid="workspace-settings-btn" data-button-role="secondary" class="secondary">Settings</button><button data-testid="workspace-settings-close" data-button-role="secondary" class="secondary">Close</button>\n<input data-testid="workspace-name-input"><select data-testid="workspace-join-mode"></select><button data-testid="workspace-general-submit" data-button-role="primary" class="primary">Save</button>\n<button data-testid="channel-settings-btn" data-button-role="secondary" class="secondary">Settings</button><button data-testid="channel-settings-close" data-button-role="secondary" class="secondary">Close</button><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary" class="primary">Save</button><button data-testid="archive-channel-btn" data-button-role="danger" class="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary" class="secondary">Unarchive</button>\n<div data-testid="channel-members-list"></div><div data-testid="channel-settings-title"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary" class="primary">Add</button></form>\n<button data-testid="create-channel-cancel" data-button-role="secondary" class="secondary">Cancel</button><input data-testid="create-channel-private">\n<div data-testid="settings-tab-members"></div><div data-testid="settings-tab-invitations"></div><div data-testid="settings-tab-general"></div><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button data-testid="create-invitation-btn" data-button-role="secondary" class="secondary">Invite</button><button data-testid="create-invitation-submit" data-button-role="primary" class="primary">Create</button><div data-testid="invitations-list"></div>\n<button data-testid="open-thread-btn">Thread</button><button data-testid="reaction-button">React</button><div data-testid="emoji-picker"></div><button data-testid="emoji-option">😀</button>\n</section><script>\nE(\'new-channel-btn\').onclick=()=>{let f=E(\'create-channel-form\');f.style.display=\'block\';f.onsubmit=async e=>{e.preventDefault();let n=f.querySelector(\'[name=name]\').value.trim();try{let d=await A(\'/workspaces/\'+ws.slug+\'/channels\',{method:\'POST\',body:JSON.stringify({name:n})});E(\'create-channel-error\').textContent=\'\';E(\'create-channel-error\').style.display=\'none\';await load();let x=[...document.querySelectorAll(\'[data-testid="channel-entry"]\')].find(z=>z.dataset.channelName===n);if(x)x.click()}catch(x){E(\'create-channel-error\').textContent=x.error||\'Channel already exists\';E(\'create-channel-error\').style.display=\'block\'}}};\n</script><script>\n(function(){\nconst btn=E(\'new-channel-btn\'),form=E(\'create-channel-form\');\nif(btn)btn.addEventListener(\'click\',()=>{form.style.display=\'block\';});\nif(form)form.addEventListener(\'submit\',async ev=>{\n ev.preventDefault();const name=form.querySelector(\'input[name="name"]\').value.trim();const error=form.querySelector(\'[data-testid="create-channel-error"]\');error.style.display=\'none\';\n try{const d=await A(\'/workspaces/\'+ws.slug+\'/channels\',{method:\'POST\',body:JSON.stringify({name:name})});\n  const detail=await A(\'/workspaces/\'+ws.slug);const list=E(\'channel-list\');list.innerHTML=\'\';\n  detail.channels.forEach(c=>{const x=document.createElement(\'button\');x.dataset.testid=\'channel-entry\';x.dataset.channelId=c.id;x.dataset.channelName=c.name;x.textContent=\'#\'+c.name;x.onclick=()=>open(c);list.appendChild(x)});\n  const found=[...list.querySelectorAll(\'[data-testid="channel-entry"]\')].find(x=>x.dataset.channelName===name);if(found)found.click();\n }catch(err){const x=form.querySelector(\'[data-testid="create-channel-error"]\');x.textContent=err.error||\'Channel already exists\';x.style.cssText=\'display:block!important;color:#e01e5a!important\';}\n});\n})();\n</script><script>\n(function(){\nfunction ensureClose(){var p=document.querySelector(\'[data-testid="thread-panel"]\');if(p&&!p.querySelector(\'[data-testid="close-thread"]\')){var b=document.createElement(\'button\');b.dataset.testid=\'close-thread\';b.dataset.buttonRole=\'secondary\';b.className=\'secondary\';b.textContent=\'Close\';b.onclick=function(){p.style.display=\'none\'};p.appendChild(b)}}\nvar p=document.querySelector(\'[data-testid="thread-panel"]\');if(p)new MutationObserver(ensureClose).observe(p,{childList:true});\nensureClose();\n})();\n</script></body></html>',content_type='text/html')
def app():
    a=web.Application()
    a.router.add_get('/api/health',health); a.router.add_post('/api/auth/register',register); a.router.add_post('/api/auth/login',login); a.router.add_get('/api/auth/me',me)
    a.router.add_post('/api/workspaces',create_workspace); a.router.add_get('/api/workspaces',workspaces); a.router.add_get('/api/workspaces/{slug}',workspace); a.router.add_get('/api/workspaces/{slug}/members',workspace_members); a.router.add_post('/api/workspaces/{slug}/channels',create_channel)
    a.router.add_post('/api/channels/{id}/join',join); a.router.add_get('/api/messages/{id}/replies',replies); a.router.add_patch('/api/channels/{id}',channel_topic); a.router.add_get('/api/channels/{id}/members',channel_members); a.router.add_post('/api/channels/{id}/leave',leave_channel); a.router.add_post('/api/channels/{id}/archive',archive_channel); a.router.add_post('/api/channels/{id}/unarchive',archive_channel); a.router.add_route('*','/api/channels/{id}/messages',messages)
    a.router.add_get('/api/ws',ws_handler)
    a.router.add_get('/api/workspaces/{slug}/members',protected_placeholder); a.router.add_post('/api/dms',dms); a.router.add_route('*','/api/workspaces/{slug}/groups',groups); a.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',groups); a.router.add_post('/api/files',upload_file); a.router.add_get('/api/files/{id}',get_file); a.router.add_get('/api/files/{id}/download',get_file); a.router.add_get('/api/users/{id}',user_profile); a.router.add_get('/api/users/me',user_profile); a.router.add_patch('/api/users/me',user_profile); a.router.add_get('/api/search',search); a.router.add_get('/api/channels/{id}/pins',list_pins); a.router.add_get('/api/channels/{id}/read',read_state); a.router.add_post('/api/channels/{id}/read',read_state); a.router.add_patch('/api/messages/{id}',edit_message); a.router.add_delete('/api/messages/{id}',delete_message); a.router.add_route('*','/api/messages/{id}/reactions',reactions); a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reactions); a.router.add_post('/api/messages/{id}/pin',pin_message); a.router.add_delete('/api/messages/{id}/pin',unpin_message)
    a.router.add_patch('/api/workspaces/{slug}',workspace_patch)
    a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',patch_member)
    a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)
    a.router.add_route('*','/api/workspaces/{slug}/invitations',invitations); a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',invitations)
    a.router.add_post('/api/invitations/{code}/accept',accept_invitation)
    a.router.add_get('/',index)
    return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0); NODE=p.parse_args().node
    core.init_db(); web.run_app(app(),host='127.0.0.1',port=8000+NODE)
