import argparse, json, re, asyncio
from aiohttp import web, WSMsgType
import core
NODE=0
def jout(x,status=200): return web.json_response(x,status=status)
def err(s,m): return jout({'error':m},s)
async def body(r):
    try:
        x=await r.json()
        return x if isinstance(x,dict) else None
    except: return None
def user(r):
    h=r.headers.get('Authorization','')
    tok=h[7:] if h.startswith('Bearer ') else r.query.get('token','')
    return core.user_for_token(tok)
def need(r):
    u=user(r)
    return u
def msgjson(r):
    d=dict(r)
    d['author']=core.user_to_json(core.connect().execute('select * from users where id=?',(d['author_id'],)).fetchone())
    d['parent_id']=d.get('parent_id')
    try:
        qc=core.connect(); qr=qc.execute('select count(*) from messages where parent_id=? and deleted=0',(d['id'],)).fetchone(); qc.close(); d['reply_count']=qr[0]
    except: d['reply_count']=0
    d['files']=[]; d['reactions']=core.reactions_for(d['id'])
    try:
        cc=core.connect(); ch=cc.execute('select * from channels where id=?',(d['channel_id'],)).fetchone(); d['mentions']=core.resolve_mentions(cc,d.get('body',''),ch,d['author_id']); cc.close()
    except: d['mentions']=[]
    try:
        c=core.connect(); fs=c.execute('select * from files where message_id=?',(d['id'],)).fetchall(); c.close(); d['files']=[dict(x) for x in fs]
    except: pass
    return d
async def health(r): return jout({'status':'ok','node_id':NODE})
async def register(r):
    b=await body(r)
    if not b or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
    with core.tx() as c:
        if c.execute('select 1 from users where username=?',(b['username'],)).fetchone(): return err(409,'duplicate username')
        c.execute('insert into users(username,password,display_name,created_at) values(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
        u=c.execute('select * from users where id=last_insert_rowid()').fetchone(); t=core.issue_token(c,u['id'])
    return jout({'user':core.user_to_json(u),'token':t},201)
async def login(r):
    b=await body(r); c=core.connect(); u=c.execute('select * from users where username=?',(b.get('username') if b else '',)).fetchone(); c.close()
    if not u or not core.verify_password(b.get('password',''),u['password']): return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return jout({'user':core.user_to_json(u),'token':t})
async def me(r):
    u=need(r); return jout({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspaces(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect(); rows=c.execute('select w.* from workspaces w left join members m on m.workspace_id=w.id and m.user_id=? where w.owner_id=? or m.user_id=?',(u['id'],u['id'],u['id'])).fetchall();c.close()
    return jout({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    b=await body(r)
    if not b or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str):return err(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('select 1 from workspaces where slug=?',(b['slug'],)).fetchone():return err(409,'duplicate')
        c.execute('insert into workspaces(slug,name,owner_id) values(?,?,?)',(b['slug'],b['name'],u['id'])); w=c.execute('select * from workspaces where id=last_insert_rowid()').fetchone()
        add=core.add_workspace_member(c,w['id'],u['id'],'owner'); c.execute('insert into channels(workspace_id,name) values(?,?)',(w['id'],'general')); ch=c.execute('select * from channels where id=last_insert_rowid()').fetchone(); c.execute('insert into channel_members values(?,?)',(ch['id'],u['id']))
    return jout({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(ch)},201)
async def workspace(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect();w=c.execute('select * from workspaces where slug=?',(r.match_info['slug'],)).fetchone()
    if not w:return err(404,'not found')
    rows=c.execute('select * from channels where workspace_id=? and is_archived=0',(w['id'],)).fetchall()
    states=[]
    for ch in rows:
        rr=c.execute('select last_read_event_id from read_states where user_id=? and channel_id=?',(u['id'],ch['id'])).fetchone()
        last=rr[0] if rr else 0
        evrows=c.execute('select payload from events where channel_id=? and event_id>? order by event_id',(ch['id'],last)).fetchall()
        unread=mentions_n=0
        for er in evrows:
            try:
                ep=json.loads(er[0]); mm=ep.get('message') or {}
                if ep.get('type') in ('message.created','message.reply') and mm.get('author_id')!=u['id'] and mm.get('parent_id') is None:
                    unread+=1
                    if u['id'] in (mm.get('mentions') or []) or re.search(r'(?<![\\w@])@'+re.escape(u['username'])+r'(?=\\W|$)',mm.get('body','')): mentions_n+=1
            except: pass
        states.append({'channel_id':ch['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':mentions_n})
    c.close()
    return jout({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':states})
async def channels(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    b=await body(r); c=core.connect(); w=c.execute('select * from workspaces where slug=?',(r.match_info['slug'],)).fetchone(); c.close()
    if not w:return err(404,'not found')
    if not b or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return err(400,'invalid channel')
    role=core.member_role(w['id'],u['id'])
    private=bool(b.get('is_private',False))
    if role=='guest' or (private and role not in ('owner','admin')):return err(403,'forbidden')
    if not isinstance(b.get('topic',''),str) or len(b.get('topic',''))>250:return err(400,'invalid topic')
    with core.tx() as c:
        if c.execute('select 1 from channels where workspace_id=? and name=?',(w['id'],b['name'])).fetchone():return err(409,'duplicate')
        c.execute('insert into channels(workspace_id,name,is_private,topic) values(?,?,?,?)',(w['id'],b['name'],int(private),b.get('topic','')))
        ch=c.execute('select * from channels where id=last_insert_rowid()').fetchone()
        c.execute('insert or ignore into channel_members values(?,?)',(ch['id'],u['id']))
    return jout({'channel':core.channel_to_json(ch)},201)
async def join(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    with core.tx() as c:
        ch=c.execute('select * from channels where id=?',(r.match_info['id'],)).fetchone()
        if not ch:return err(404,'not found')
        if ch['workspace_id'] and c.execute('select role from members where workspace_id=? and user_id=?',(ch['workspace_id'],u['id'])).fetchone() and c.execute('select role from members where workspace_id=? and user_id=?',(ch['workspace_id'],u['id'])).fetchone()['role']=='guest':return err(403,'forbidden')
        if ch['is_private'] and not c.execute('select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone():return err(403,'forbidden')
        if ch['workspace_id']:
            w=c.execute('select * from workspaces where id=?',(ch['workspace_id'],)).fetchone()
            if not c.execute('select 1 from members where workspace_id=? and user_id=?',(w['id'],u['id'])).fetchone():
                if w['join_mode']!='open':return err(403,'forbidden')
                core.add_workspace_member(c,w['id'],u['id'],'member')
                g=c.execute('select id from channels where workspace_id=? and name="general"',(w['id'],)).fetchone()
                if g:c.execute('insert or ignore into channel_members values(?,?)',(g['id'],u['id']))
        existed=c.execute('select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone()
        if not existed:
            c.execute('insert into channel_members values(?,?)',(ch['id'],u['id']))
            if ch['workspace_id']:
                c.execute('insert or ignore into members(workspace_id,user_id,role) values(?,?,?)',(ch['workspace_id'],u['id'],'member'))
            core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
    return jout({'channel':core.channel_to_json(ch)})
async def messages(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    cid=int(r.match_info['id']); b=await body(r)
    check=core.connect(); ch=check.execute('select * from channels where id=?',(cid,)).fetchone(); member=check.execute('select 1 from channel_members where channel_id=? and user_id=?',(cid,u['id'])).fetchone(); check.close()
    if not ch:return err(404,'not found')
    if not member and ch['workspace_id'] and not ch['is_private']:
        cc=core.connect(); wm=cc.execute('select 1 from members where workspace_id=? and user_id=?',(ch['workspace_id'],u['id'])).fetchone(); cc.close()
        if wm:
            with core.tx() as xx: xx.execute('insert or ignore into channel_members values(?,?)',(cid,u['id']))
            member=True
    if not member:return err(403,'forbidden')
    if r.method=='POST':
        if not b or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty message')
        text=b['body']
        if ch['is_archived'] and not text.startswith('/unarchive'): return err(423,'archived')
        if text.startswith('//'): b['body']=text[1:]
        elif text.startswith('/'):
            parts=text.split(' ',1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
            if cmd=='/me':
                if not arg.strip(): return err(400,'text required')
            elif cmd=='/shrug':
                b['body']=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
            elif cmd=='/topic':
                if not isinstance(arg,str) or len(arg)>250:return err(400,'invalid topic')
                with core.tx() as c:
                    c.execute('update channels set topic=? where id=?',(arg,cid)); ch=c.execute('select * from channels where id=?',(cid,)).fetchone()
                    core.emit_event(c,cid,'channel.updated',{'channel':dict(ch)})
                return jout({'message':None,'channel':core.channel_to_json(ch)},201)
            elif cmd=='/invite':
                if not re.fullmatch(r'@[A-Za-z0-9_]+',arg.strip()):return err(400,'invalid invite')
                c=core.connect(); target=c.execute('select * from users where username=?',(arg.strip()[1:],)).fetchone();c.close()
                if not target:return err(404,'user not found')
                with core.tx() as c:
                    c.execute('insert or ignore into channel_members values(?,?)',(cid,target['id']))
                return jout({'message':None,'invited_user':core.user_to_json(target)},201)
            elif cmd in ('/archive','/unarchive'):
                c=core.connect(); w=c.execute('select owner_id from workspaces where id=?',(ch['workspace_id'],)).fetchone();c.close()
                if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
                archived=cmd=='/archive'
                with core.tx() as c:
                    c.execute('update channels set is_archived=? where id=?',(int(archived),cid));ch=c.execute('select * from channels where id=?',(cid,)).fetchone()
                    core.emit_event(c,cid,'channel.archived' if archived else 'channel.unarchived',{'channel':dict(ch)})
                return jout({'message':None,'channel':core.channel_to_json(ch)},201)
            else:return err(400,'unknown command')
        ids=b.get('file_ids',[])
        if not isinstance(ids,list):return err(400,'invalid files')
        if 'parent_id' in b and b['parent_id'] is not None and (isinstance(b['parent_id'],bool) or not isinstance(b['parent_id'],int)):return err(400,'invalid parent_id')
        with core.tx() as c:
            for fid in ids:
                f=c.execute('select * from files where id=? and uploader_id=? and message_id is null',(fid,u['id'])).fetchone()
                if not f:return err(400,'invalid file')
            c.execute('insert into messages(channel_id,author_id,body,parent_id,created_at) values(?,?,?,?,?)',(cid,u['id'],b['body'],b.get('parent_id'),core.now_iso())); m=c.execute('select * from messages where id=last_insert_rowid()').fetchone()
            for fid in ids:c.execute('update files set message_id=? where id=?',(m['id'],fid))
            core.emit_event(c,cid,'message.reply' if m['parent_id'] is not None else 'message.created',{'message':dict(m)})
        return jout({'message':msgjson(m)},201)
    c=core.connect(); rows=c.execute('select * from messages where channel_id=? and parent_id is null and deleted=0 order by id desc limit 200',(cid,)).fetchall();c.close()
    return jout({'messages':[msgjson(x) for x in rows],'next_cursor':None})
async def replies(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect(); parent=c.execute('select * from messages where id=?',(r.match_info['id'],)).fetchone()
    if not parent:return err(404,'not found')
    member=c.execute('select 1 from channel_members where channel_id=? and user_id=?',(parent['channel_id'],u['id'])).fetchone()
    if not member:return err(403,'forbidden')
    rows=c.execute('select * from messages where parent_id=? and deleted=0 order by id asc',(parent['id'],)).fetchall();c.close()
    return jout({'replies':[msgjson(x) for x in rows],'next_cursor':None})

async def edit(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    b=await body(r)
    if b is None or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'invalid body')
    with core.tx() as c:
        m=c.execute('select * from messages where id=?',(r.match_info['id'],)).fetchone()
        if not m:return err(404,'not found')
        if m['deleted']:return err(409,'deleted')
        if m['author_id']!=u['id']:return err(403,'forbidden')
        c.execute('update messages set body=?,edited_at=? where id=?',(b['body'],core.now_iso(),m['id']))
        m=c.execute('select * from messages where id=?',(m['id'],)).fetchone()
        core.emit_event(c,m['channel_id'],'message.updated',{'message':dict(m)})
    return jout({'message':msgjson(m)})
async def delete(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    with core.tx() as c:
        m=c.execute('select * from messages where id=?',(r.match_info['id'],)).fetchone()
        if not m:return err(404,'not found')
        allowed=m['author_id']==u['id']
        if not allowed:
            ch=c.execute('select workspace_id from channels where id=?',(m['channel_id'],)).fetchone()
            w=c.execute('select owner_id from workspaces where id=?',(ch['workspace_id'],)).fetchone() if ch and ch['workspace_id'] else None
            allowed=bool(w and w['owner_id']==u['id'])
        if not allowed:return err(403,'forbidden')
        if not m['deleted']:
            c.execute('update messages set deleted=1 where id=?',(m['id'],))
            c.execute('delete from reactions where message_id=?',(m['id'],))
            core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':m['id']})
    return jout({'deleted':True})

async def react(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    b=await body(r); emoji=b.get('emoji') if b else None
    if not isinstance(emoji,str) or not emoji:return err(400,'invalid emoji')
    with core.tx() as c:
        m=c.execute('select * from messages where id=?',(r.match_info['id'],)).fetchone()
        if not m:return err(404,'not found')
        ch=c.execute('select * from channels where id=?',(m['channel_id'],)).fetchone()
        if ch['is_private'] and not c.execute('select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone():return err(403,'forbidden')
        before=c.execute('select 1 from reactions where message_id=? and user_id=? and emoji=?',(m['id'],u['id'],emoji)).fetchone()
        c.execute('insert or ignore into reactions values(?,?,?)',(m['id'],u['id'],emoji))
        if not before: core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
    return jout({'reactions':core.reactions_for(m['id'])})
async def react_delete(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    with core.tx() as c:
        m=c.execute('select * from messages where id=?',(r.match_info['id'],)).fetchone()
        if not m:return err(404,'not found')
        ch=c.execute('select * from channels where id=?',(m['channel_id'],)).fetchone()
        if ch['is_private'] and not c.execute('select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone():return err(403,'forbidden')
        emoji=r.match_info['emoji']
        existed=c.execute('select 1 from reactions where message_id=? and user_id=? and emoji=?',(m['id'],u['id'],emoji)).fetchone()
        c.execute('delete from reactions where message_id=? and user_id=? and emoji=?',(m['id'],u['id'],emoji))
        if existed: core.emit_event(c,m['channel_id'],'reaction.removed',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
    return jout({'reactions':core.reactions_for(m['id'])})


async def ws_handler(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    ws=web.WebSocketResponse(); await ws.prepare(r); subs={}; last={}; gc=core.connect(); gc.execute('create table if not exists global_events(id integer primary key,payload text)'); gr=gc.execute('select coalesce(max(id),0) from global_events').fetchone(); global_last=gr[0]; gc.close()
    try:
        c=core.connect(); c.execute('create table if not exists presence(conn_id text primary key,user_id integer,last_seen real)'); c.execute('insert or replace into presence values(?,?,strftime("%s","now"))',(str(id(ws)),u['id'])); c.close()
    except: pass
    send_lock=asyncio.Lock()
    async def send_events():
        nonlocal global_last
        while not ws.closed:
            async with send_lock:
                for ge in core.globals_after(global_last):
                    await ws.send_json(json.loads(ge['payload'])); global_last=ge['id']
                for cid,since in list(last.items()):
                    rows=core.events_after(cid,since) if hasattr(core,'events_after') else []
                    for x in rows:
                        await ws.send_json(json.loads(x['payload'])); last[cid]=x['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(send_events())
    try:
        async for m in ws:
            if m.type==WSMsgType.TEXT:
                try:x=json.loads(m.data)
                except:continue
                cid=x.get('channel_id')
                if x.get('type')=='subscribe' and cid:
                    h=core.head_event_id(cid) if hasattr(core,'head_event_id') else 0
                    last[cid]=h; await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':h})
                elif x.get('type')=='resume' and cid:
                    since=int(x.get('since_event_id',0))
                    async with send_lock:
                        last.pop(cid,None)
                        replay=core.events_after(cid,since)
                        for ev in replay:
                            await ws.send_json(json.loads(ev['payload']))
                        head=core.head_event_id(cid)
                        last[cid]=head
                        await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':head})
    finally:
        task.cancel()
        try:
            c=core.connect(); c.execute('delete from presence where conn_id=?',(str(id(ws)),)); c.close()
        except: pass
    return ws

async def members(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect(); w=c.execute('select * from workspaces where slug=?',(r.match_info['slug'],)).fetchone()
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    rows=c.execute('select m.user_id,m.role,u.* from members m join users u on u.id=m.user_id where m.workspace_id=?',(w['id'],)).fetchall();c.close()
    return jout({'members':[{'user_id':x['user_id'],'role':x['role'],**core.user_to_json(x)} for x in rows]})
async def role_patch(r):
    u=need(r); b=await body(r)
    if not u:return err(401,'unauthorized')
    c=core.connect();w=c.execute('select * from workspaces where slug=?',(r.match_info['slug'],)).fetchone();c.close()
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return err(403,'forbidden')
    target=int(r.match_info['user_id'])
    if not core.member_role(w['id'],target):return err(404,'not found')
    new=b.get('role') if b else None
    if new not in ('admin','member','guest'):return err(400,'invalid role')
    if target==w['owner_id'] or (role=='admin' and core.member_role(w['id'],target)=='admin'):return err(403,'forbidden')
    with core.tx() as c:c.execute('update members set role=? where workspace_id=? and user_id=?',(new,w['id'],target))
    return jout({'member':{'user_id':target,'role':new}})
async def transfer(r):
    u=need(r);b=await body(r)
    if not u:return err(401,'unauthorized')
    c=core.connect();w=c.execute('select * from workspaces where slug=?',(r.match_info['slug'],)).fetchone();c.close()
    if not w:return err(404,'not found')
    if u['id']!=w['owner_id']:return err(403,'forbidden')
    target=b.get('new_owner_id') if b else None
    if core.member_role(w['id'],target)!='admin':return err(400,'target must be admin')
    with core.tx() as c:
        c.execute('update members set role="admin" where workspace_id=? and user_id=?',(w['id'],u['id']))
        c.execute('update members set role="owner" where workspace_id=? and user_id=?',(w['id'],target))
        c.execute('update workspaces set owner_id=? where id=?',(target,w['id']))
    return jout({'workspace':core.workspace_to_json(core.connect().execute('select * from workspaces where id=?',(w['id'],)).fetchone())})
async def archive_channel(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    cid=int(r.match_info['id']); c=core.connect(); ch=c.execute('select * from channels where id=?',(cid,)).fetchone(); c.close()
    if not ch:return err(404,'not found')
    c=core.connect(); w=c.execute('select owner_id from workspaces where id=?',(ch['workspace_id'],)).fetchone(); c.close()
    if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as x:
        x.execute('update channels set is_archived=? where id=?',(1 if r.path.endswith('/archive') else 0,cid))
        ch=x.execute('select * from channels where id=?',(cid,)).fetchone()
        core.emit_event(x,cid,'channel.archived' if r.path.endswith('/archive') else 'channel.unarchived',{'channel':dict(ch)})
    return jout({'channel':core.channel_to_json(ch)})

async def channel_patch(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    b=await body(r); c=core.connect(); ch=c.execute('select * from channels where id=?',(r.match_info['id'],)).fetchone(); c.close()
    if not ch:return err(404,'not found')
    if not b or not isinstance(b.get('topic'),str) or len(b['topic'])>250:return err(400,'invalid topic')
    if not c: pass
    with core.tx() as x:
        member=x.execute('select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone()
        owner=x.execute('select 1 from workspaces where id=? and owner_id=?',(ch['workspace_id'],u['id'])).fetchone() if ch['workspace_id'] else None
        if not member and not owner:return err(403,'forbidden')
        x.execute('update channels set topic=? where id=?',(b['topic'],ch['id']))
        ch=x.execute('select * from channels where id=?',(ch['id'],)).fetchone()
    return jout({'channel':core.channel_to_json(ch)})


async def read_state(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    cid=int(r.match_info['id']); c=core.connect(); ch=c.execute('select * from channels where id=?',(cid,)).fetchone()
    if not ch:return err(404,'not found')
    if not c.execute('select 1 from channel_members where channel_id=? and user_id=?',(cid,u['id'])).fetchone():return err(403,'forbidden')
    row=c.execute('select last_read_event_id from read_states where user_id=? and channel_id=?',(u['id'],cid)).fetchone(); last=row[0] if row else 0
    evrows=c.execute('select payload from events where channel_id=? and event_id>? order by event_id',(cid,last)).fetchall()
    unread=mentions_n=0
    for er in evrows:
        try:
            ep=json.loads(er[0]); mm=ep.get('message') or {}
            if ep.get('type') in ('message.created','message.reply') and mm.get('author_id')!=u['id'] and mm.get('parent_id') is None:
                unread+=1
                if u['id'] in (mm.get('mentions') or []) or re.search(r'(?<![\\w@])@'+re.escape(u['username'])+r'(?=\\W|$)',mm.get('body','')): mentions_n+=1
        except: pass
    obj={'channel_id':cid,'last_read_event_id':last,'unread_count':unread,'mention_count':mentions_n};c.close()
    if r.method=='GET':return jout({'read_state':obj})
    b=await body(r)
    if not b or isinstance(b.get('last_read_event_id'),bool) or not isinstance(b.get('last_read_event_id'),int):return err(400,'invalid read state')
    with core.tx() as x:
        x.execute('insert into read_states(user_id,channel_id,last_read_event_id) values(?,?,?) on conflict(user_id,channel_id) do update set last_read_event_id=max(last_read_event_id,excluded.last_read_event_id)',(u['id'],cid,b['last_read_event_id']))
    newlast=max(last,b['last_read_event_id'])
    cc=core.connect(); rows2=cc.execute('select payload from events where channel_id=? and event_id>? order by event_id',(cid,newlast)).fetchall(); cc.close()
    unread2=mentions2=0
    for er in rows2:
        try:
            ep=json.loads(er[0]); mm=ep.get('message') or {}
            if ep.get('type') in ('message.created','message.reply') and mm.get('author_id')!=u['id'] and mm.get('parent_id') is None:
                unread2+=1
                if u['id'] in (mm.get('mentions') or []) or re.search(r'(?<![\\w@])@'+re.escape(u['username'])+r'(?=\\W|$)',mm.get('body','')): mentions2+=1
        except: pass
    return jout({'read_state':{'channel_id':cid,'last_read_event_id':newlast,'unread_count':unread2,'mention_count':mentions2}})

async def channel_members(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect();ch=c.execute('select * from channels where id=?',(r.match_info['id'],)).fetchone()
    if not ch:return err(404,'not found')
    rows=c.execute('select u.* from channel_members cm join users u on u.id=cm.user_id where cm.channel_id=?',(ch['id'],)).fetchall();c.close()
    return jout({'members':[core.user_to_json(x) for x in rows]})
async def leave(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    with core.tx() as c:
        ch=c.execute('select * from channels where id=?',(r.match_info['id'],)).fetchone()
        if not ch:return err(404,'not found')
        existed=c.execute('select 1 from channel_members where channel_id=? and user_id=?',(ch['id'],u['id'])).fetchone()
        if existed:
            c.execute('delete from channel_members where channel_id=? and user_id=?',(ch['id'],u['id']))
            core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return jout({'channel':core.channel_to_json(ch)})



async def profile(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect(); x=c.execute('select * from users where id=?',(r.match_info['id'],)).fetchone(); c.close()
    if not x:return err(404,'not found')
    return jout({'user':core.user_to_json(x)})
async def profile_me(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    b=await body(r)
    if b is None:return err(400,'invalid body')
    try:x=core.update_profile(u,b)
    except ValueError as e:return err(400,str(e))
    return jout({'user':core.user_to_json(x)})

async def groups(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect();w=c.execute('select * from workspaces where slug=?',(r.match_info['slug'],)).fetchone()
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    if r.method=='GET':
        rows=c.execute('select * from groups where workspace_id=? order by id',(w['id'],)).fetchall();c.close()
        return jout({'groups':[core.group_to_json(x) for x in rows]})
    b=await body(r)
    if not b or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['handle']) or not isinstance(b.get('name'),str) or not isinstance(b.get('user_ids',[]),list):return err(400,'invalid group')
    ids=b.get('user_ids',[]); valid={x[0] for x in c.execute('select user_id from members where workspace_id=?',(w['id'],)).fetchall()};c.close()
    if any(not isinstance(x,int) or x not in valid for x in ids):return err(400,'invalid members')
    with core.tx() as c:
        if c.execute('select 1 from groups where workspace_id=? and handle=?',(w['id'],b['handle'])).fetchone():return err(409,'duplicate')
        c.execute('insert into groups(workspace_id,handle,name,creator_id) values(?,?,?,?)',(w['id'],b['handle'],b['name'],u['id']));g=c.execute('select * from groups where id=last_insert_rowid()').fetchone()
        c.execute('delete from group_members where group_id=?',(g['id'],))
        for x in ids:c.execute('insert into group_members values(?,?)',(g['id'],x))
    return jout({'group':core.group_to_json(g)},201)
async def group_item(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect();w=c.execute('select * from workspaces where slug=?',(r.match_info['slug'],)).fetchone()
    if not w:return err(404,'not found')
    g=c.execute('select * from groups where workspace_id=? and handle=?',(w['id'],r.match_info['handle'])).fetchone()
    if not g:
        if r.method=='DELETE' and core.member_role(w['id'],u['id']): return jout({'deleted':True})
        return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    if core.member_role(w['id'],u['id'])!='owner' and g['creator_id']!=u['id']:return err(403,'forbidden')
    if r.method=='DELETE':
        with core.tx() as x:
            x.execute('delete from group_members where group_id=?',(g['id'],))
            x.execute('delete from groups where id=?',(g['id'],))
        return jout({'deleted':True})
    b=await body(r)
    if not b:return err(400,'invalid body')
    with core.tx() as x:
        if 'name' in b:x.execute('update groups set name=? where id=?',(b['name'],g['id']))
        if 'user_ids' in b:
            x.execute('delete from group_members where group_id=?',(g['id'],))
            for uid in b['user_ids']:x.execute('insert into group_members values(?,?)',(g['id'],uid))
        g=x.execute('select * from groups where id=?',(g['id'],)).fetchone()
    return jout({'group':core.group_to_json(g)})

async def missing_feature(r):
    if not need(r): return err(401,'unauthorized')
    return err(404,'not found')
async def dms(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    b=await body(r); oid=b.get('user_id') if b else None
    if not isinstance(oid,int):return err(400,'invalid user_id')
    c=core.connect(); other=c.execute('select id from users where id=?',(oid,)).fetchone();c.close()
    if not other:return err(404,'not found')
    a,z=sorted((u['id'],oid))
    with core.tx() as c:
        c.execute('create table if not exists dms(user_a integer,user_b integer,channel_id integer,primary key(user_a,user_b))')
        row=c.execute('select channel_id from dms where user_a=? and user_b=?',(a,z)).fetchone()
        if row: cid=row[0]
        else:
            c.execute('insert into channels(is_private,is_dm,name) values(1,1,?)',(f'dm-{a}-{z}',));cid=c.execute('select last_insert_rowid()').fetchone()[0]
            c.execute('insert into dms values(?,?,?)',(a,z,cid));c.execute('insert into channel_members values(?,?),(?,?)',(cid,a,cid,z))
    return jout({'channel_id':cid})
async def files(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    if not r.content_type.startswith('multipart/'):return err(400,'file required')
    rd=await r.multipart(); part=await rd.next()
    if not part or part.name!='file':return err(400,'file required')
    data=await part.read()
    if len(data)>10*1024*1024:return err(413,'too large')
    fn=part.filename or 'file'; ct=part.headers.get('Content-Type','application/octet-stream')
    with core.tx() as c:
        c.execute('create table if not exists files(id integer primary key,uploader_id integer,filename text,content_type text,size integer,created_at text,message_id integer)')
        c.execute('insert into files(uploader_id,filename,content_type,size,created_at) values(?,?,?,?,?)',(u['id'],fn,ct,len(data),core.now_iso()));fid=c.execute('select last_insert_rowid()').fetchone()[0]
    import os
    os.makedirs('/app/data/files',exist_ok=True);open(f'/app/data/files/'+str(fid),'wb').write(data)
    return jout({'file':{'id':fid,'uploader_id':u['id'],'filename':fn,'content_type':ct,'size':len(data),'created_at':core.now_iso()}},201)
async def getfile(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect();c.execute('create table if not exists files(id integer primary key,uploader_id integer,filename text,content_type text,size integer,created_at text,message_id integer)')
    f=c.execute('select * from files where id=?',(r.match_info['id'],)).fetchone();c.close()
    if not f:return err(404,'not found')
    return jout({'file':dict(f)})
async def download(r):
    u=need(r)
    if not u:return err(401,'unauthorized')
    c=core.connect();f=c.execute('select * from files where id=?',(r.match_info['id'],)).fetchone();c.close()
    if not f:return err(404,'not found')
    return web.FileResponse('/app/data/files/'+str(f['id']),headers={'Content-Type':f['content_type'],'Content-Disposition':f'attachment; filename="{f["filename"]}"'})


async def root(r):
    return web.FileResponse('/app/static/index.html')
async def appjs(r): return web.FileResponse('/app/static/app.js',headers={'Content-Type':'application/javascript'})
def main():
    a=argparse.ArgumentParser();a.add_argument('--node',type=int,default=0);global NODE;NODE=a.parse_args().node
    app=web.Application();app.router.add_get('/api/health',health);app.router.add_get('/api/users/{id}',profile);app.router.add_patch('/api/users/me',profile_me);app.router.add_get('/api/ws',ws_handler);app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me);app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_workspace);app.router.add_get('/api/workspaces/{slug}',workspace);app.router.add_get('/api/workspaces/{slug}/members',members);app.router.add_patch('/api/workspaces/{slug}/members/{user_id}',role_patch);app.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer);app.router.add_post('/api/workspaces/{slug}/channels',channels);app.router.add_post('/api/channels/{id}/join',join);app.router.add_get('/api/channels/{id}/members',channel_members);app.router.add_route('*','/api/channels/{id}/read',read_state);app.router.add_patch('/api/channels/{id}',channel_patch);app.router.add_post('/api/channels/{id}/archive',archive_channel);app.router.add_post('/api/channels/{id}/unarchive',archive_channel);app.router.add_post('/api/channels/{id}/leave',leave);app.router.add_route('*','/api/channels/{id}/messages',messages);app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_patch('/api/messages/{id}',edit);app.router.add_delete('/api/messages/{id}',delete);app.router.add_post('/api/messages/{id}/reactions',react);app.router.add_delete('/api/messages/{id}/reactions/{emoji}',react_delete);app.router.add_route('*','/api/workspaces/{slug}/groups',groups);app.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',group_item);app.router.add_post('/api/dms',dms);app.router.add_post('/api/files',files);app.router.add_get('/api/files/{id}',getfile);app.router.add_get('/api/files/{id}/download',download);app.router.add_get('/api/users/{id}',missing_feature);app.router.add_get('/app.js',appjs);app.router.add_get('/',root);web.run_app(app,host='127.0.0.1',port=8000+NODE)
main()
