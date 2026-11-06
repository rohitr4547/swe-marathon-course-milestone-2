import argparse,asyncio,json,os,re,uuid,datetime
from aiohttp import web,WSMsgType
import core
NODE=0
def resp(x,status=200): return web.json_response(x,status=status)
def err(status,msg): return resp({'error':msg},status)
async def body(req):
    try:
        x=await req.json()
        return x if isinstance(x,dict) else None
    except: return None
def user(req):
    h=req.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else None)
def auth(req):
    u=user(req)
    if not u: raise web.HTTPUnauthorized()
    return u
def role(w,u): return core.member_role(w['id'],u['id'])
def channel_access(u,ch):
    return core.can_read_channel(u['id'],ch)
def jchan(r): return core.channel_to_json(r)
async def health(req): return resp({'status':'ok','node_id':NODE})
async def register(req):
    x=await body(req)
    if not x or not isinstance(x.get('username'),str) or not isinstance(x.get('password'),str): return err(400,'invalid input')
    if not re.fullmatch(r'[A-Za-z0-9_]+',x['username']) or len(x['password'])<8:return err(400,'invalid input')
    try:
        with core.tx() as c:
            cur=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(x['username'],core.hash_password(x['password']),x.get('display_name') or x['username'],core.now_iso()))
            uid=cur.lastrowid;t=core.issue_token(c,uid); u=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        return resp({'user':core.user_to_json(u),'token':t},201)
    except: return err(409,'username exists')
async def login(req):
    x=await body(req)
    if not x:return err(400,'invalid input')
    c=core.connect();u=c.execute('SELECT * FROM users WHERE username=?',(x.get('username'),)).fetchone();c.close()
    if not u or not isinstance(x.get('password'),str) or not core.verify_password(x['password'],u['password']):return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,u['id'])
    return resp({'user':core.user_to_json(u),'token':t})
async def me(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    return resp({'user':core.user_to_json(u)})
async def list_workspaces(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    c=core.connect();a=[core.workspace_to_json(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],))];c.close();return resp({'workspaces':a})
async def create_workspace(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    x=await body(req)
    if not x or not isinstance(x.get('slug'),str) or not isinstance(x.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',x['slug']):return err(400,'invalid input')
    try:
        with core.tx() as c:
            cur=c.execute('INSERT INTO workspaces(slug,name,owner_id,created_at) VALUES(?,?,?,?)',(x['slug'],x['name'],u['id'],core.now_iso()));wid=cur.lastrowid
            c.execute('INSERT INTO memberships VALUES(?,?,?)',(wid,u['id'],'owner'));cid=core.create_channel_row(c,wid,'general');core.add_channel_member(c,cid,u['id'])
            w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone();ch=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        return resp({'workspace':core.workspace_to_json(w),'general_channel':jchan(ch)},201)
    except:return err(409,'duplicate slug')
async def workspace(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?) AND (is_private=0 OR EXISTS (SELECT 1 FROM channel_members cm WHERE cm.channel_id=channels.id AND cm.user_id=?))',(w['id'],req.query.get('include_archived') in ('1','true'),u['id'])).fetchall(); chs=[jchan(x) for x in rows]
    states=[]
    for ch in rows:
        rr=c.execute('SELECT COALESCE(last_read_event_id,0) v FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()
        last=rr['v'] if rr else 0
        unread=c.execute('SELECT COUNT(*) n FROM messages WHERE channel_id=? AND deleted=0 AND COALESCE(event_id,0)>? AND author_id!=?',(ch['id'],last,u['id'])).fetchone()['n']
        states.append({'channel_id':ch['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':0})
    c.close()
    return resp({'workspace':core.workspace_to_json(w),'channels':chs,'read_state':states})
async def members(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect();a=[dict(x) for x in c.execute('SELECT user_id,role FROM memberships WHERE workspace_id=?',(w['id'],))];c.close();return resp({'members':a})
async def create_channel(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug']);x=await body(req)
    if not w:return err(404,'not found')
    if role(w,u) not in ('owner','admin','member'):return err(403,'forbidden')
    if x and x.get('is_private') is True and role(w,u) not in ('owner','admin'):return err(403,'forbidden')
    if not x or not isinstance(x.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',x['name']) or ('is_private' in x and not isinstance(x['is_private'],bool)) or ('topic' in x and (not isinstance(x['topic'],str) or len(x['topic'])>250)) or (x.get('is_private',False) and role(w,u) not in ('owner','admin')):return err(400,'invalid input')
    try:
        with core.tx() as c:
            cid=core.create_channel_row(c,w['id'],x['name'],x.get('is_private',False),x.get('topic',''));core.add_channel_member(c,cid,u['id']);core.emit_event(c,cid,'member.joined',{'user_id':u['id']});r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
        return resp({'channel':jchan(r)},201)
    except:return err(409,'duplicate channel')
async def join(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    if bool(ch['is_archived']):return err(423,'archived')
    w=None
    if ch['workspace_id']:
        cc=core.connect();w=cc.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();cc.close()
    if ch['is_private'] and not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if w and core.member_role(w['id'],u['id'])=='guest':return err(403,'forbidden')
    if w and not core.member_role(w['id'],u['id']):
        if w['join_mode']!='open':return err(403,'invite required')
        with core.tx() as c:core.add_workspace_member(c,w['id'],u['id'])
    with core.tx() as c:
        existing=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        if not existing:
            core.add_channel_member(c,ch['id'],u['id']);core.emit_event(c,ch['id'],'member.joined',{'user_id':u['id']})
        r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return resp({'channel':jchan(r)})
async def messages(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    if not channel_access(u,ch):return err(403,'forbidden')
    if 'offset' in req.query or 'page' in req.query:return err(400,'offset pagination unsupported')
    try:lim,cur=core.parse_paging(req.query,'messages')
    except:return err(400,'invalid cursor')
    c=core.connect();q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL';args=[ch['id']]
    if cur:q+=' AND id<?';args.append(cur)
    q+=' ORDER BY id DESC LIMIT ?';args.append(lim+1);a=list(c.execute(q,args));c.close();more=len(a)>lim;a=a[:lim]
    return resp({'messages':[core.message_to_json(x) for x in a],'next_cursor':core.encode_cursor('messages',a[-1]['id']) if more else None})
async def post_message(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']));x=await body(req)
    if not ch:return err(404,'not found')
    if not channel_access(u,ch):return err(403,'forbidden')
    if not x or not isinstance(x.get('body'),str) or not x['body'].strip() or ('parent_id' in x and (not isinstance(x['parent_id'],int) or isinstance(x['parent_id'],bool))):return err(400,'invalid body')
    file_ids=x.get('file_ids',[])
    if not isinstance(file_ids,list) or any(not isinstance(fid,int) or isinstance(fid,bool) for fid in file_ids) or len(set(file_ids))!=len(file_ids):return err(400,'invalid files')
    text=x['body']
    if text.startswith('/') and not text.startswith('//'):
        parts=text.split(' ',1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
        cc=core.connect(); w=cc.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() if ch['workspace_id'] else None; cc.close()
        if cmd=='/me':
            if not arg.strip(): return err(400,'missing text')
        elif cmd=='/shrug':
            text=(arg.strip()+' ' if arg.strip() else '')+'¯\\_(ツ)_/¯'
        elif cmd=='/topic':
            if not arg.strip(): return err(400,'missing topic')
            if ch['is_archived']: return err(423,'archived')
            with core.tx() as c:
                c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id']))
                ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
                core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(ch)})
            return resp({'message':None,'channel':core.channel_to_json(ch)},201)
        elif cmd=='/invite':
            if not w or not arg.startswith('@') or not re.fullmatch(r'@[A-Za-z0-9_]+',arg.strip()): return err(400,'invalid invite')
            c=core.connect(); invited=c.execute('SELECT * FROM users WHERE username=?',(arg.strip()[1:],)).fetchone();c.close()
            if not invited:return err(404,'user not found')
            if ch['is_archived']:return err(423,'archived')
            with core.tx() as c:
                core.add_channel_member(c,ch['id'],invited['id'])
                core.emit_event(c,ch['id'],'member.joined',{'user_id':invited['id']})
            return resp({'message':None,'invited_user':core.user_to_json(invited)},201)
        elif cmd in ('/archive','/unarchive'):
            if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
            flag=1 if cmd=='/archive' else 0
            if bool(ch['is_archived'])==bool(flag):return resp({'message':None,'channel':core.channel_to_json(ch)},201)
            with core.tx() as c:
                c.execute('UPDATE channels SET is_archived=? WHERE id=?',(flag,ch['id']))
                ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
                core.emit_event(c,ch['id'],'channel.archived' if flag else 'channel.unarchived',{'channel':core.channel_to_json(ch)})
            return resp({'message':None,'channel':core.channel_to_json(ch)},201)
        else:
            return err(400,'unknown command')
    elif text.startswith('//'):
        text=text[1:]
    if bool(ch['is_archived']):return err(423,'archived')
    if file_ids:
        fc=core.connect()
        valid=fc.execute('SELECT id FROM files WHERE uploader_id=? AND message_id IS NULL AND id IN (%s)' % ','.join('?'*len(file_ids)),[u['id'],*file_ids]).fetchall()
        fc.close()
        if len(valid)!=len(file_ids):return err(400,'invalid file')
    with core.tx() as c:
        mentions=core.resolve_mentions(c,text,ch,u['id'])
        for fid in file_ids:
            fr=c.execute('SELECT uploader_id,message_id FROM files WHERE id=?',(fid,)).fetchone()
            if not fr or fr['uploader_id']!=u['id'] or fr['message_id'] is not None:
                raise ValueError('invalid file')
        cur=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch['id'],u['id'],text,x.get('parent_id'),core.now_iso(),json.dumps(mentions)));mid=cur.lastrowid
        if file_ids:c.executemany('UPDATE files SET message_id=? WHERE id=?',[(mid,fid) for fid in file_ids])
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); frame=core.emit_event(c,ch['id'],'message.reply' if x.get('parent_id') else 'message.created',{'message':core.message_to_json(r)}); c.execute('UPDATE messages SET event_id=? WHERE id=?',(frame['event_id'],mid))
    return resp({'message':core.message_to_json(r)},201)
async def channel_members(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    if not channel_access(u,ch):return err(403,'forbidden')
    c=core.connect();a=[]
    for r in c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)):a.append(core.user_to_json(r))
    c.close();return resp({'members':a})
async def edit_message(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    try:mid=int(req.match_info['id'])
    except:return err(404,'not found')
    x=await body(req)
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not r:return err(404,'not found')
    if r['deleted']:return err(409,'deleted')
    if r['author_id']!=u['id']:return err(403,'forbidden')
    if not x or not isinstance(x.get('body'),str) or not x['body'].strip():return err(400,'invalid body')
    ch=core.get_channel(r['channel_id'])
    with core.tx() as c:
        mentions=core.resolve_mentions(c,x['body'],ch,u['id'])
        c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(x['body'],core.now_iso(),json.dumps(mentions),mid))
        nr=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        core.emit_event(c,r['channel_id'],'message.updated',{'message':core.message_to_json(nr)})
    return resp({'message':core.message_to_json(nr)})
async def delete_message(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    try:mid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not r:return err(404,'not found')
    ch=core.get_channel(r['channel_id']);w=None
    if ch['workspace_id']:
        cc=core.connect();w=cc.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();cc.close()
    if r['author_id']!=u['id'] and (not w or w['owner_id']!=u['id']):return err(403,'forbidden')
    if r['deleted']:return resp({'deleted':True})
    with core.tx() as c:
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
        core.emit_event(c,r['channel_id'],'message.deleted',{'message_id':mid})
    return resp({'deleted':True})
async def replies(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    c=core.connect();p=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not p:return err(404,'not found')
    ch=core.get_channel(p['channel_id'])
    if not channel_access(u,ch):return err(403,'forbidden')
    try:lim,cur=core.parse_paging(req.query,'replies')
    except:return err(400,'invalid cursor')
    c=core.connect();q='SELECT * FROM messages WHERE parent_id=? AND deleted=0';args=[p['id']]
    if cur:q+=' AND id>?';args.append(cur)
    q+=' ORDER BY id ASC LIMIT ?';args.append(lim+1);a=list(c.execute(q,args));c.close()
    more=len(a)>lim;a=a[:lim]
    return resp({'replies':[core.message_to_json(x) for x in a],'next_cursor':core.encode_cursor('replies',a[-1]['id']) if more else None})
async def reaction(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    try:mid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return err(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not core.can_read_channel(u['id'],ch):return err(403,'forbidden')
    if ch['is_archived']:return err(423,'archived')
    if req.method=='POST':
        x=await body(req);emoji=x.get('emoji') if x else None
    else:
        emoji=req.match_info.get('emoji','')
    if not isinstance(emoji,str) or not emoji:return err(400,'invalid emoji')
    changed=False
    with core.tx() as c:
        exists=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
        if req.method=='POST' and not exists:
            c.execute('INSERT INTO reactions VALUES(?,?,?)',(mid,u['id'],emoji));changed=True;kind='reaction.added'
        elif req.method=='DELETE' and exists:
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji));changed=True;kind='reaction.removed'
        if changed: core.emit_event(c,m['channel_id'],kind,{'message_id':mid,'emoji':emoji,'user_id':u['id']})
        rr=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    return resp({'reactions':core.message_to_json(rr)['reactions']})
async def pin(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(req.match_info['id']),)).fetchone();c.close()
    if not m:return err(404,'not found')
    if req.method=='POST':
        with core.tx() as c:
            existing=c.execute('SELECT * FROM pins WHERE message_id=?',(m['id'],)).fetchone()
            if existing:
                pinrow=existing
            else:
                stamp=core.now_iso()
                c.execute('INSERT INTO pins VALUES(?,?,?)',(m['id'],u['id'],stamp))
                pinrow=c.execute('SELECT * FROM pins WHERE message_id=?',(m['id'],)).fetchone()
        return resp({'pin':{'message_id':pinrow['message_id'],'pinned_by':pinrow['pinned_by'],'pinned_at':pinrow['pinned_at']}})
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(m['id'],))
    return resp({'unpinned':True})
async def pins(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    if not channel_access(u,ch):return err(403,'forbidden')
    try:lim,cur=core.parse_paging(req.query,'pins')
    except:return err(400,'invalid cursor')
    c=core.connect();q='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';args=[ch['id']]
    if cur:q+=' AND p.message_id<?';args.append(cur)
    q+=' ORDER BY p.message_id DESC LIMIT ?';args.append(lim+1);rows=list(c.execute(q,args));c.close()
    more=len(rows)>lim;rows=rows[:lim]
    return resp({'pins':[{'message':core.message_to_json(r),'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']} for r in rows],'next_cursor':core.encode_cursor('pins',rows[-1]['message_id']) if more else None})
async def dms(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    x=await body(req);oid=x.get('user_id') if x else None
    if not isinstance(oid,int) or isinstance(oid,bool):return err(400,'invalid user')
    c0=core.connect(); target=c0.execute('SELECT id FROM users WHERE id=?',(oid,)).fetchone(); c0.close()
    if not target:return err(404,'user not found')
    with core.tx() as c:
        a,b=sorted((u['id'],oid));r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if r:return resp({'channel_id':r['channel_id']})
        cur=c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)');cid=cur.lastrowid
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid));core.add_channel_member(c,cid,a);core.add_channel_member(c,cid,b)
    return resp({'channel_id':cid})
async def search(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    qtext=req.query.get('q')
    if not qtext:return err(400,'missing q')
    try:lim,cur=core.parse_paging(req.query,'search')
    except:return err(400,'invalid paging')
    c=core.connect();q='SELECT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id WHERE m.deleted=0 AND (ch.is_private=0 AND EXISTS (SELECT 1 FROM memberships wm WHERE wm.workspace_id=ch.workspace_id AND wm.user_id=?) OR EXISTS (SELECT 1 FROM channel_members cm WHERE cm.channel_id=m.channel_id AND cm.user_id=?)) AND lower(m.body) LIKE lower(?)';args=[u['id'],u['id'],'%'+qtext+'%']
    if req.query.get('workspace_id'):
        try:int(req.query['workspace_id'])
        except:return err(400,'invalid workspace')
        q+=' AND m.channel_id IN (SELECT id FROM channels WHERE workspace_id=?)';args.append(int(req.query['workspace_id']))
    if cur:q+=' AND m.id<?';args.append(cur)
    q+=' ORDER BY m.id DESC LIMIT ?';args.append(lim+1);rows=list(c.execute(q,args));c.close()
    more=len(rows)>lim;rows=rows[:lim]
    return resp({'results':[core.message_to_json(x) for x in rows],'next_cursor':core.encode_cursor('search',rows[-1]['id']) if more else None})
async def read_state(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    if not channel_access(u,ch):return err(403,'forbidden')
    if req.method=='POST':
        x=await body(req);v=x.get('last_read_event_id') if x else None
        if not isinstance(v,int) or isinstance(v,bool) or v<0:return err(400,'invalid read state')
        with core.tx() as c:c.execute('INSERT INTO read_states VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=max(last_read_event_id,excluded.last_read_event_id)',(u['id'],ch['id'],v))
    c=core.connect();r=c.execute('SELECT COALESCE(last_read_event_id,0) v FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone();last=r['v'] if r else 0
    n=c.execute('SELECT COUNT(*) n FROM messages WHERE channel_id=? AND deleted=0 AND COALESCE(event_id,0)>? AND author_id!=?',(ch['id'],last,u['id'])).fetchone()['n'];mn=c.execute("SELECT COUNT(*) n FROM messages WHERE channel_id=? AND deleted=0 AND COALESCE(event_id,0)>? AND author_id!=? AND mentions LIKE ?",(ch['id'],last,u['id'],'%'+str(u['id'])+'%')).fetchone()['n'];c.close()
    return resp({'read_state':{'channel_id':ch['id'],'last_read_event_id':last,'unread_count':n,'mention_count':mn}})
async def ws(req):
    token=req.query.get('token','');u=core.user_for_token(token)
    if not u:return err(401,'unauthorized')
    w=web.WebSocketResponse();await w.prepare(req);subs={}; conn_id=str(uuid.uuid4()); core.touch_presence(conn_id,u['id']); global_last=core.latest_global_id()
    try:
        while not w.closed:
            try:
                msg=await w.receive(timeout=0.05)
                if msg.type==WSMsgType.CLOSE:break
                if msg.type==WSMsgType.TEXT:
                    try:x=json.loads(msg.data)
                    except: x={}
                    if x.get('type') in ('subscribe','resume'):
                        cid=int(x['channel_id']);since=int(x.get('since_event_id',0));subs[cid]=since
                        if x['type']=='subscribe':
                            head=core.head_event_id(cid);subs[cid]=head
                            await w.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
                        else:
                            for e in core.events_after(cid,since): await w.send_json(e)
                            subs[cid]=core.head_event_id(cid)
                            await w.send_json({'type':'resumed','channel_id':cid,'head_event_id':subs[cid]})
            except asyncio.TimeoutError:
                pass
            core.touch_presence(conn_id,u['id'])
            for ge in core.globals_after(global_last):
                await w.send_json(ge); global_last=ge.get('_global_id',global_last+1)
            for cid,last in list(subs.items()):
                for e in core.events_after(cid,last):
                    await w.send_json(e);subs[cid]=e['event_id']
    finally:
        core.drop_presence(conn_id)
        if not w.closed: await w.close()
    return w
async def index(req):
    return web.FileResponse('/app/static/index.html')

async def patch_workspace(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if role(w,u) not in ('owner','admin'):return err(403,'forbidden')
    x=await body(req)
    if not x:return err(400,'invalid input')
    if 'name' in x and not isinstance(x['name'],str):return err(400,'invalid input')
    if 'join_mode' in x and x['join_mode'] not in ('open','invite_only'):return err(400,'invalid input')
    with core.tx() as c:
        if 'name' in x:c.execute('UPDATE workspaces SET name=? WHERE id=?',(x['name'],w['id']))
        if 'join_mode' in x:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(x['join_mode'],w['id']))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return resp({'workspace':core.workspace_to_json(w)})
async def patch_member(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    target=int(req.match_info['user_id']); old=core.member_role(w['id'],target)
    if old is None:return err(404,'not found')
    x=await body(req); new=x.get('role') if x else None
    if not isinstance(new,str) or new not in ('owner','admin','member','guest'):return err(400,'invalid role')
    caller=role(w,u)
    if target==w['owner_id']:return err(403,'forbidden')
    if caller not in ('owner','admin'):return err(403,'forbidden')
    if caller=='admin' and old=='admin':return err(403,'forbidden')
    if new=='owner':return err(400,'use transfer')
    with core.tx() as c:c.execute('UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?',(new,w['id'],target))
    return resp({'member':{'user_id':target,'role':new}})
async def transfer(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if u['id']!=w['owner_id']:return err(403,'forbidden')
    x=await body(req);target=x.get('new_owner_id') if x else None
    if not isinstance(target,int) or core.member_role(w['id'],target)!='admin':return err(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?',('admin',w['id'],u['id']))
        c.execute('UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?',('owner',w['id'],target))
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return resp({'workspace':core.workspace_to_json(w)})
async def patch_channel(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    if bool(ch['is_archived']):return err(423,'archived')
    w=None
    if ch['workspace_id']:
        cc=core.connect();w=cc.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();cc.close()
    c0=core.connect(); direct=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
    if not direct and (not w or w['owner_id']!=u['id']):return err(403,'forbidden')
    x=await body(req)
    if not x or ('topic' in x and (not isinstance(x['topic'],str) or len(x['topic'])>250)):return err(400,'invalid topic')
    with core.tx() as c:
        if 'topic' in x:c.execute('UPDATE channels SET topic=? WHERE id=?',(x['topic'],ch['id']))
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(ch)})
    return resp({'channel':core.channel_to_json(ch)})

async def invitations(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if role(w,u) not in ('owner','admin'):return err(403,'forbidden')
    if req.method=='GET':
        c=core.connect();a=[dict(x) for x in c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND (expires_at IS NULL OR expires_at>?) AND uses<max_uses',(w['id'],__import__('time').time()))];c.close()
        return resp({'invitations':a})
    x=await body(req)
    if x is None:return err(400,'invalid json')
    for k in ('expires_in_seconds','max_uses','invited_user_id'):
        if k in x and (not isinstance(x[k],int) or isinstance(x[k],bool)):return err(400,'invalid input')
    target=x.get('invited_user_id')
    if 'invited_username' in x:
        if not isinstance(x['invited_username'],str):return err(400,'invalid input')
        c=core.connect();r=c.execute('SELECT id FROM users WHERE username=?',(x['invited_username'],)).fetchone();c.close()
        if not r:return err(404,'user not found')
        target=r['id']
    code=__import__('secrets').token_urlsafe(12); exp=__import__('time').time()+x.get('expires_in_seconds',86400)
    max_uses=1 if target is not None else x.get('max_uses',1)
    with core.tx() as c:
        cur=c.execute('INSERT INTO invitations(workspace_id,code,expires_at,max_uses,invited_user_id) VALUES(?,?,?,?,?)',(w['id'],code,exp,max_uses,target))
        r=c.execute('SELECT * FROM invitations WHERE id=?',(cur.lastrowid,)).fetchone()
    return resp({'invitation':dict(r)},201)
async def accept_invite(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM invitations WHERE code=?',(req.match_info['code'],)).fetchone();c.close()
    import time
    if not r or r['revoked'] or r['uses']>=r['max_uses'] or (r['expires_at'] and r['expires_at']<=time.time()):return err(404,'not found')
    if r['invited_user_id'] and r['invited_user_id']!=u['id']:return err(403,'not invited')
    with core.tx() as c:
        c.execute('UPDATE invitations SET uses=uses+1 WHERE id=?',(r['id'],));core.add_workspace_member(c,r['workspace_id'],u['id'],'member')
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone()
    return resp({'workspace':core.workspace_to_json(w)})
async def revoke_invite(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if role(w,u) not in ('owner','admin'):return err(403,'forbidden')
    with core.tx() as c:
        r=c.execute('SELECT id FROM invitations WHERE workspace_id=? AND code=? AND revoked=0',(w['id'],req.match_info['code'])).fetchone()
        if not r:return err(404,'not found')
        c.execute('UPDATE invitations SET revoked=1 WHERE id=?',(r['id'],))
    return resp({'deleted':True})
async def leave(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    with core.tx() as c:
        existing=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
        if existing:
            core.remove_channel_member(c,ch['id'],u['id']);core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return resp({'channel':core.channel_to_json(ch)})
async def archive(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    ch=core.get_channel(int(req.match_info['id']))
    if not ch:return err(404,'not found')
    cc=core.connect();w=cc.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();cc.close()
    if not w:return err(404,'not found')
    if w['owner_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as c:
        flag=0 if req.path.endswith('/unarchive') else 1
        if bool(ch['is_archived'])==bool(flag): return resp({'channel':core.channel_to_json(ch)})
        c.execute('UPDATE channels SET is_archived=? WHERE id=?',(flag,ch['id']))
        ch=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
        core.emit_event(c,ch['id'],'channel.unarchived' if flag==0 else 'channel.archived',{'channel':core.channel_to_json(ch)})
    return resp({'channel':core.channel_to_json(ch)})


def file_json(r):
    return {'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}
async def upload_file(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    try:
        reader=await req.multipart()
        part=await reader.next()
        if not part or part.name!='file':return err(400,'file required')
        filename=part.filename or 'file'
        data=await part.read(decode=False)
    except:return err(400,'invalid multipart')
    if len(data)>10485760:return err(413,'file too large')
    with core.tx() as c:
        cur=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],filename,part.headers.get('Content-Type','application/octet-stream'),len(data),core.now_iso()))
        fid=cur.lastrowid
        with open('/app/data/files/'+str(fid),'wb') as f:f.write(data)
        r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
    return resp({'file':file_json(r)},201)
async def get_file(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    try:fid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    if not r:return err(404,'not found')
    if req.path.endswith('/download'):
        try:data=open('/app/data/files/'+str(fid),'rb').read()
        except:return err(404,'not found')
        return web.Response(body=data,content_type=r['content_type'],headers={'Content-Disposition':'attachment; filename="'+r['filename']+'"'})
    return resp({'file':file_json(r)})
async def get_user(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    try:uid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close()
    if not r:return err(404,'not found')
    return resp({'user':core.user_to_json(r)})


def group_json(r):
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':json.loads(r['user_ids'] or '[]')}
async def groups(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect()
    if req.method=='GET':
        rows=c.execute('SELECT * FROM groups_ WHERE workspace_id=? ORDER BY id',(w['id'],)).fetchall();c.close()
        return resp({'groups':[group_json(r) for r in rows]})
    x=await body(req)
    if not x or not isinstance(x.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',x['handle']) or not isinstance(x.get('name'),str) or not isinstance(x.get('user_ids'),list) or any(not isinstance(i,int) or isinstance(i,bool) for i in x['user_ids']):return err(400,'invalid input')
    c=core.connect();valid={r['user_id'] for r in c.execute('SELECT user_id FROM memberships WHERE workspace_id=?',(w['id'],))};c.close()
    if any(i not in valid for i in x['user_ids']):return err(400,'user is not a workspace member')
    try:
        with core.tx() as c:
            cur=c.execute('INSERT INTO groups_(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],x['handle'],x['name'],u['id'],json.dumps(x['user_ids'])))
            r=c.execute('SELECT * FROM groups_ WHERE id=?',(cur.lastrowid,)).fetchone()
        return resp({'group':group_json(r)},201)
    except:return err(409,'duplicate handle')
async def group_item(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect();r=c.execute('SELECT * FROM groups_ WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone();c.close()
    if not r:return resp({'deleted':True}) if req.method=='DELETE' else err(404,'not found')
    if req.method=='DELETE':
        if r['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
        with core.tx() as c:c.execute('DELETE FROM groups_ WHERE id=?',(r['id'],))
        return resp({'deleted':True})
    if r['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
    x=await body(req)
    if not x:return err(400,'invalid input')
    vals=[];args=[]
    if 'name' in x:
        if not isinstance(x['name'],str):return err(400,'invalid input')
        vals.append('name=?');args.append(x['name'])
    if 'user_ids' in x:
        if not isinstance(x['user_ids'],list):return err(400,'invalid input')
        c=core.connect();valid={z['user_id'] for z in c.execute('SELECT user_id FROM memberships WHERE workspace_id=?',(w['id'],))};c.close()
        if any(not isinstance(i,int) or i not in valid for i in x['user_ids']):return err(400,'invalid input')
        vals.append('user_ids=?');args.append(json.dumps(x['user_ids']))
    if vals:
        args.append(r['id'])
        with core.tx() as c:c.execute('UPDATE groups_ SET '+','.join(vals)+' WHERE id=?',args)
    c=core.connect();r=c.execute('SELECT * FROM groups_ WHERE id=?',(r['id'],)).fetchone();c.close()
    return resp({'group':group_json(r)})


async def patch_me(req):
    try:u=auth(req)
    except web.HTTPUnauthorized:return err(401,'unauthorized')
    x=await body(req)
    if x is None:return err(400,'invalid json')
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if any(k not in allowed for k in x):return err(400,'invalid field')
    limits={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512)}
    for k,v in x.items():
        if not isinstance(v,str):return err(400,'invalid field')
        if k in limits and not (limits[k][0] <= len(v) <= limits[k][1]):return err(400,'invalid field')
    if 'timezone' in x:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(x['timezone'])
        except Exception:return err(400,'invalid timezone')
    with core.tx() as c:
        for k,v in x.items():c.execute('UPDATE users SET '+k+'=? WHERE id=?',(v,u['id']))
        r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        core.emit_global(c,{'type':'user.updated','user':core.user_to_json(r)})
    return resp({'user':core.user_to_json(r)})

def app():
    a=web.Application()
    a.router.add_get('/api/health',health);a.router.add_post('/api/auth/register',register);a.router.add_post('/api/auth/login',login);a.router.add_get('/api/auth/me',me);a.router.add_patch('/api/users/me',patch_me)
    a.router.add_get('/api/workspaces',list_workspaces);a.router.add_post('/api/workspaces',create_workspace);a.router.add_get('/api/workspaces/{slug}',workspace);a.router.add_get('/api/workspaces/{slug}/members',members);a.router.add_patch('/api/workspaces/{slug}',patch_workspace);a.router.add_patch('/api/workspaces/{slug}/members/{user_id}',patch_member);a.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer);a.router.add_post('/api/workspaces/{slug}/channels',create_channel);a.router.add_route('*','/api/workspaces/{slug}/groups',groups);a.router.add_route('*','/api/workspaces/{slug}/groups/{handle}',group_item);a.router.add_route('*','/api/workspaces/{slug}/invitations',invitations);a.router.add_post('/api/invitations/{code}/accept',accept_invite);a.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_invite)
    a.router.add_post('/api/channels/{id}/join',join);a.router.add_post('/api/channels/{id}/leave',leave);a.router.add_post('/api/channels/{id}/archive',archive);a.router.add_post('/api/channels/{id}/unarchive',archive);a.router.add_patch('/api/channels/{id}',patch_channel);a.router.add_get('/api/channels/{id}/members',channel_members);a.router.add_get('/api/channels/{id}/messages',messages);a.router.add_post('/api/channels/{id}/messages',post_message);a.router.add_get('/api/channels/{id}/pins',pins);a.router.add_route('*','/api/channels/{id}/read',read_state)
    a.router.add_get('/api/messages/{id}/replies',replies);a.router.add_patch('/api/messages/{id}',edit_message);a.router.add_delete('/api/messages/{id}',delete_message);a.router.add_post('/api/messages/{id}/reactions',reaction);a.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction);a.router.add_post('/api/messages/{id}/pin',pin);a.router.add_delete('/api/messages/{id}/pin',pin)
    a.router.add_post('/api/dms',dms);a.router.add_post('/api/files',upload_file);a.router.add_get('/api/files/{id}/download',get_file);a.router.add_get('/api/files/{id}',get_file);a.router.add_get('/api/users/{id}',get_user);a.router.add_get('/api/search',search);a.router.add_get('/api/ws',ws);a.router.add_get('/',index);return a
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);NODE=p.parse_args().node
    web.run_app(app(),host='127.0.0.1',port=8000+NODE)

