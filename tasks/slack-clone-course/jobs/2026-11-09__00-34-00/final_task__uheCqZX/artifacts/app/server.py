import sys,re,json,os,time,uuid
from aiohttp import web
import core
NODE=int(sys.argv[sys.argv.index('--node')+1]) if '--node' in sys.argv else 0
def json_error(s,m): return web.json_response({'error':m},status=s)
async def read_json(q):
    try: x=await q.json(); return x if isinstance(x,dict) else None
    except: return None
def require_user(q):
    h=q.headers.get('Authorization','')
    if not h.startswith('Bearer '): raise web.HTTPUnauthorized()
    u=core.user_for_token(h[7:])
    if not u: raise web.HTTPUnauthorized()
    return u
def auth(q):
    try:return require_user(q)
    except:return None
async def health(q): return web.json_response({'status':'ok','node_id':NODE})
async def register(q):
    d=await read_json(q)
    if not d or not isinstance(d.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',d['username']) or not isinstance(d.get('password'),str) or len(d['password'])<8 or 'email' in d:return json_error(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(d['username'],)).fetchone(): return json_error(409,'username already exists')
        name=d.get('display_name',d['username'])
        if not isinstance(name,str): return json_error(400,'invalid display_name')
        c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(d['username'],core.hash_password(d['password']),name))
        u=c.execute('SELECT * FROM users WHERE username=?',(d['username'],)).fetchone(); t=core.issue_token(c,u['id'])
    return web.json_response({'user':core.user_to_json(u),'token':t},status=201)
async def login(q):
    d=await read_json(q); c=core.connect(); u=c.execute('SELECT * FROM users WHERE username=?',(d.get('username') if d else '',)).fetchone() if d else None
    if not u or not isinstance(d.get('password'),str) or not core.verify_password(d['password'],u['password']): c.close(); return json_error(401,'invalid credentials')
    t=core.issue_token(c,u['id']); c.close(); return web.json_response({'user':core.user_to_json(u),'token':t})
async def me(q):
    u=auth(q)
    return web.json_response({'user':core.user_to_json(u)}) if u else json_error(401,'unauthorized')
async def workspaces(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); rows=c.execute('SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall(); c.close()
    return web.json_response({'workspaces':[core.workspace_to_json(x) for x in rows]})
async def create_workspace(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    d=await read_json(q)
    if not d or not isinstance(d.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',d['slug']) or not isinstance(d.get('name'),str) or not d['name']:return json_error(400,'invalid workspace')
    try:
      with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(d['slug'],)).fetchone(): return json_error(409,'duplicate slug')
        c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(d['slug'],d['name'],u['id']))
        w=c.execute('SELECT * FROM workspaces WHERE slug=?',(d['slug'],)).fetchone()
        g=core.create_channel_row(c,w['id'],'general',0,'',u['id']); core.add_workspace_member(c,w['id'],u['id'],'owner')
    except Exception:return json_error(409,'duplicate slug')
    return web.json_response({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(g)},status=201)
async def workspace_detail(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT ch.* FROM channels ch WHERE ch.workspace_id=? AND (ch.is_archived=0 OR ?) AND (ch.is_private=0 OR EXISTS (SELECT 1 FROM channel_members cm WHERE cm.channel_id=ch.id AND cm.user_id=?))',(w['id'],q.query.get('include_archived') in ('1','true'),u['id'])).fetchall(); c.close()
    return web.json_response({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in rows],'read_state':[read_obj(u['id'],x['id']) for x in rows]})
async def members(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT m.*,u.* FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall(); c.close()
    return web.json_response({'members':[{'user_id':r['user_id'],'role':r['role'],'user':core.user_to_json(r)} for r in rs]})
async def create_channel(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id']); d=await read_json(q)
    if role not in ('owner','admin','member'):return json_error(403,'forbidden')
    if not d or not isinstance(d.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',d['name']):return json_error(400,'invalid name')
    private=d.get('is_private',False)
    if not isinstance(private,bool):return json_error(400,'invalid private')
    if private and role not in ('owner','admin'):return json_error(403,'forbidden')
    topic=d.get('topic','')
    if not isinstance(topic,str) or len(topic)>250:return json_error(400,'invalid topic')
    with core.tx() as c:
      if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],d['name'])).fetchone():return json_error(409,'duplicate channel')
      ch=core.create_channel_row(c,w['id'],d['name'],private,topic,u['id'])
    return web.json_response({'channel':core.channel_to_json(ch)},status=201)
async def join(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    try: ch=core.join_channel(u,int(q.match_info['id']))
    except KeyError:return json_error(404,'not found')
    except PermissionError:return json_error(403,'forbidden')
    return web.json_response({'channel':core.channel_to_json(ch)})
async def channel_patch(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(q.match_info['id']))
    if not ch:return json_error(404,'not found')
    w=core.get_workspace((core.connect().execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone() or {'slug':''})['slug']) if ch['workspace_id'] else None
    c0=core.connect(); cm=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
    if not cm and (not w or w['owner_id']!=u['id']):return json_error(403,'forbidden')
    if ch['is_archived']:return json_error(423,'archived')
    d=await read_json(q)
    if not isinstance(d,dict) or not isinstance(d.get('topic',''),str) or len(d.get('topic',''))>250:return json_error(400,'invalid topic')
    with core.tx() as c:
        c.execute('UPDATE channels SET topic=? WHERE id=?',(d.get('topic',''),ch['id'])); core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
async def channel_members(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(q.match_info['id']))
    if not ch:return json_error(404,'not found')
    c=core.connect(); ok=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); rs=c.execute('SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall(); c.close()
    if not ok:return json_error(403,'forbidden')
    return web.json_response({'members':[core.user_to_json(r) for r in rs]})
async def channel_leave(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(q.match_info['id']))
    if not ch:return json_error(404,'not found')
    with core.tx() as c:
        old=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
        if old: core.emit_event(c,ch['id'],'member.left',{'user_id':u['id']})
    return web.json_response({'channel':core.channel_to_json(ch)})
async def channel_archive(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(q.match_info['id']))
    if not ch:return json_error(404,'not found')
    c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c.close()
    if not w or w['owner_id']!=u['id']:return json_error(403,'forbidden')
    val=1 if q.path.endswith('/archive') else 0
    with core.tx() as c:
        old=ch['is_archived']; c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
        if old != val: core.emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{})
    return web.json_response({'channel':core.channel_to_json(core.get_channel(ch['id']))})
def reaction_list(rows):
    out=[]
    for x in rows:
        e=x['emoji']; z=next((q for q in out if q['emoji']==e),None)
        if not z: z={'emoji':e,'count':0,'user_ids':[]}; out.append(z)
        z['count']+=1; z['user_ids'].append(x['user_id'])
    return out

def msg_json(r):
    c=core.connect(); u=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    n=c.execute('SELECT COUNT(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']; rr=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY created_at',(r['id'],)).fetchall(); fs=c.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall(); c.close()
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':core.user_to_json(u),'body':r['body'],'parent_id':r['parent_id'],'reply_count':n,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[{'id':f['id'],'uploader_id':f['uploader_id'],'filename':f['filename'],'content_type':f['content_type'],'size':f['size'],'created_at':f['created_at']} for f in fs],'reactions':reaction_list(rr),'mentions':json.loads(r['mentions']) if 'mentions' in r.keys() and r['mentions'] else []}
def page_args(q,kind):
    if 'page' in q.query or 'offset' in q.query: raise ValueError()
    try: lim=int(q.query.get('limit','50'))
    except: raise ValueError()
    if lim<1 or lim>200: raise ValueError()
    cur=q.query.get('cursor')
    if not cur:return lim,None
    try:
        raw=__import__('base64').urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)); d=json.loads(raw)
        if d.get('k')!=kind or not isinstance(d.get('v'),int):raise ValueError()
        return lim,d['v']
    except: raise ValueError()
def enc(kind,v):
    return __import__('base64').urlsafe_b64encode(json.dumps({'k':kind,'v':v},separators=(',',':')).encode()).decode().rstrip('=')
def read_obj(uid,cid):
    c=core.connect(); x=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(uid,cid)).fetchone(); nv=x['last_read_event_id'] if x else 0
    rs=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,nv)).fetchall(); c.close()
    unread=mention=0
    for row in rs:
        try:
            d=json.loads(row['payload'])
            if d.get('type') not in ('message.created','message.reply'): continue
            m=d.get('message') or {}
            if m.get('author_id')==uid: continue
            unread+=1
            if uid in (m.get('mentions') or []): mention+=1
        except: pass
    return {'channel_id':cid,'last_read_event_id':nv,'unread_count':unread,'mention_count':mention}
def can_channel(u,ch):
    c=core.connect(); m=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
    role=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(ch['workspace_id'],u['id'])).fetchone() if ch['workspace_id'] else None; c.close()
    return bool(m or (not ch['is_private'] and role and role['role']!='guest'))
async def channel_messages(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(q.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not can_channel(u,ch):return json_error(403,'forbidden')
    if q.method=='GET':
        try: lim,cur=page_args(q,'messages')
        except: return json_error(400,'invalid paging')
        c=core.connect(); sql='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'
        args=[ch['id']]
        if cur is not None: sql+=' AND id<?'; args.append(cur)
        sql+=' ORDER BY id DESC LIMIT ?'; args.append(lim+1); rs=c.execute(sql,args).fetchall(); c.close()
        more=len(rs)>lim; rs=rs[:lim]
        return web.json_response({'messages':[msg_json(r) for r in rs],'next_cursor':enc('messages',rs[-1]['id']) if more and rs else None})
    d=await read_json(q)
    if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'invalid body')
    body=d['body']
    if body.startswith('/') and not body.startswith('//'):
        parts=body.split(' ',1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
        if cmd=='/me':
            if not arg.strip(): return json_error(400,'/me requires text')
        elif cmd=='/shrug':
            d['body']=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
        elif cmd=='/topic':
            if not arg.strip(): return json_error(400,'topic requires text')
            if len(arg)>250:return json_error(400,'invalid topic')
            with core.tx() as c:
                c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id'])); core.emit_event(c,ch['id'],'channel.updated',{'channel':core.channel_to_json(core.get_channel(ch['id']))})
            return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
        elif cmd=='/invite':
            import re
            m=re.fullmatch(r'@([A-Za-z0-9_]+)',arg.strip())
            if not m:return json_error(400,'invalid invite')
            c=core.connect(); target=c.execute('SELECT * FROM users WHERE username=?',(m.group(1),)).fetchone(); c.close()
            if not target:return json_error(404,'user not found')
            with core.tx() as c:
                if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],target['id'])).fetchone():
                    c.execute('INSERT INTO channel_members VALUES(?,?)',(ch['id'],target['id'])); core.emit_event(c,ch['id'],'member.joined',{'user_id':target['id']})
            return web.json_response({'message':None,'invited_user':core.user_to_json(target)},status=201)
        elif cmd in ('/archive','/unarchive'):
            c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c.close()
            if not w or w['owner_id']!=u['id']:return json_error(403,'forbidden')
            val=1 if cmd=='/archive' else 0
            with core.tx() as c:
                c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id']))
                core.emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{})
            return web.json_response({'message':None,'channel':core.channel_to_json(core.get_channel(ch['id']))},status=201)
        else:
            return json_error(400,'unknown command')
    if body.startswith('//'): d['body']=body[1:]
    if ch['is_archived']:return json_error(423,'archived')
    parent=d.get('parent_id')
    if parent is not None:
        try: parent=int(parent)
        except:return json_error(400,'invalid parent')
    file_ids=d.get('file_ids',[])
    if not isinstance(file_ids,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in file_ids):return json_error(400,'invalid file_ids')
    with core.tx() as c:
        for fid in file_ids:
            f=c.execute('SELECT uploader_id,message_id FROM files WHERE id=?',(fid,)).fetchone()
            if not f or f['uploader_id']!=u['id'] or f['message_id'] is not None:return json_error(400,'invalid file attachment')
        import re
        names=set(re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',d['body']))
        cusers=c.execute('SELECT u.id,u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall()
        ids={x['id'] for x in cusers if x['username'] in names and x['id']!=u['id']}
        for h in names:
            if h in ('channel','here'): continue
            for gr in c.execute('SELECT id FROM groups WHERE workspace_id=? AND handle=?',(ch['workspace_id'],h)).fetchall():
                ids.update(x['user_id'] for x in c.execute('SELECT gm.user_id FROM group_members gm JOIN channel_members cm ON cm.user_id=gm.user_id WHERE gm.group_id=? AND cm.channel_id=?',(gr['id'],ch['id'])).fetchall() if x['user_id']!=u['id'])
        if '@here' in d['body']:
            active={x['user_id'] for x in c.execute('SELECT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall()}
            ids.update(x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)).fetchall() if x['user_id']!=u['id'] and x['user_id'] in active)
        if '@channel' in d['body']:
            ids.update(x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(ch['id'],)).fetchall() if x['user_id']!=u['id'])
        c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(ch['id'],u['id'],d['body'],parent,core.now_iso(),json.dumps(list(ids))))
        r=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone()
        for fid in file_ids:c.execute('UPDATE files SET message_id=? WHERE id=?',(r['id'],fid))
        r=c.execute('SELECT * FROM messages WHERE id=?',(r['id'],)).fetchone()
        c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(ch['id'],))
        ev=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch['id'],)).fetchone()['last_event_id']
        kind='message.reply' if parent else 'message.created'
        c.execute('INSERT INTO events(channel_id,event_id,payload) VALUES(?,?,?)',(ch['id'],ev,json.dumps({'type':kind,'event_id':ev,'channel_id':ch['id'],'message':msg_json(r)})))
    return web.json_response({'message':msg_json(r)},status=201)
async def message_replies2(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); parent=c.execute('SELECT * FROM messages WHERE id=?',(int(q.match_info['id']),)).fetchone(); c.close()
    if not parent:return json_error(404,'not found')
    ch=core.get_channel(parent['channel_id'])
    if not can_channel(u,ch):return json_error(403,'forbidden')
    try:lim,cur=page_args(q,'replies')
    except:return json_error(400,'invalid paging')
    c=core.connect(); sql='SELECT * FROM messages WHERE parent_id=? AND deleted=0'; args=[parent['id']]
    if cur is not None:sql+=' AND id>?';args.append(cur)
    sql+=' ORDER BY id ASC LIMIT ?';args.append(lim+1);rs=c.execute(sql,args).fetchall();c.close()
    more=len(rs)>lim;rs=rs[:lim]
    return web.json_response({'replies':[msg_json(r) for r in rs],'next_cursor':enc('replies',rs[-1]['id']) if more and rs else None})
async def reaction(q):
    u=auth(q); 
    if not u:return json_error(401,'unauthorized')
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(q.match_info['id']),)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not can_channel(u,ch):return json_error(403,'forbidden')
    d=await read_json(q); emoji=d.get('emoji') if d else None
    if not isinstance(emoji,str) or not emoji:return json_error(400,'invalid emoji')
    with core.tx() as c:
        old=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(m['id'],u['id'],emoji)).fetchone()
        if not old:c.execute('INSERT INTO reactions VALUES(?,?,?,?)',(m['id'],u['id'],emoji,core.now_iso())); core.emit_event(c,ch['id'],'reaction.added',{'message_id':m['id'],'emoji':emoji,'user_id':u['id']})
    cc=core.connect(); rr=cc.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY created_at',(m['id'],)).fetchall(); cc.close()
    return web.json_response({'reactions':reaction_list(rr)})
async def reaction_delete(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    try: mid=int(q.match_info['id'])
    except:return json_error(404,'not found')
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not can_channel(u,ch):return json_error(403,'forbidden')
    from urllib.parse import unquote
    emoji=unquote(q.match_info.get('emoji',''))
    with core.tx() as c:
        row=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
        if row:
            c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
            core.emit_event(c,ch['id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    c=core.connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY created_at',(mid,)).fetchall(); c.close()
    return web.json_response({'reactions':reaction_list(rows)})

async def message_edit(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    mid=int(q.match_info['id']); c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    if m['deleted']:return json_error(409,'deleted')
    if m['author_id']!=u['id']:return json_error(403,'forbidden')
    d=await read_json(q)
    if not isinstance(d,dict) or not isinstance(d.get('body'),str) or not d['body'].strip():return json_error(400,'invalid body')
    body=d['body']
    c=core.connect(); ch=core.get_channel(m['channel_id'])
    import re
    names=set(re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',body))
    ids=set()
    for x in c.execute('SELECT u.id,u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall():
        if x['username'] in names and x['id']!=u['id']:ids.add(x['id'])
    c.close()
    edited=core.now_iso()
    with core.tx() as c:
        c.execute('UPDATE messages SET body=?,edited_at=?,mentions=? WHERE id=?',(body,edited,json.dumps(list(ids)),mid))
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        core.emit_event(c,ch['id'],'message.updated',{'message':msg_json(r)})
    return web.json_response({'message':msg_json(r)})
async def message_delete(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    mid=int(q.match_info['id']); c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE id=?',(core.get_channel(m['channel_id'])['workspace_id'],)).fetchone();c.close()
    if m['deleted']:return web.json_response({'deleted':True})
    if m['author_id']!=u['id'] and (not w or w['owner_id']!=u['id']):return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
        core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
    return web.json_response({'deleted':True})
async def pin(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(int(q.match_info['id']),)).fetchone(); c.close()
    if not m:return json_error(404,'not found')
    ch=core.get_channel(m['channel_id'])
    if not can_channel(u,ch):return json_error(403,'forbidden')
    with core.tx() as c:
        c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(m['id'],u['id'],core.now_iso()))
        r=c.execute('SELECT * FROM pins WHERE message_id=?',(m['id'],)).fetchone()
    return web.json_response({'pin':{'message_id':r['message_id'],'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']}})
async def unpin(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(int(q.match_info['id']),)).fetchone();c.close()
    if not m:return json_error(404,'not found')
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(m['id'],))
    return web.json_response({'unpinned':True})
async def pins(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(q.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not can_channel(u,ch):return json_error(403,'forbidden')
    try:lim,cur=page_args(q,'pins')
    except:return json_error(400,'invalid paging')
    c=core.connect();sql='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?';args=[ch['id']]
    if cur is not None:sql+=' AND p.message_id<?';args.append(cur)
    sql+=' ORDER BY p.message_id DESC LIMIT ?';args.append(lim+1);rs=c.execute(sql,args).fetchall();c.close()
    more=len(rs)>lim;rs=rs[:lim]
    return web.json_response({'pins':[{'message':msg_json(r),'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']} for r in rs],'next_cursor':enc('pins',rs[-1]['message_id']) if more and rs else None})
async def search(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    text=q.query.get('q')
    if not text:return json_error(400,'missing q')
    try:lim,cur=page_args(q,'search')
    except:return json_error(400,'invalid paging')
    c=core.connect();rows=c.execute("SELECT m.* FROM messages m WHERE lower(m.body) LIKE lower(?) AND m.deleted=0 ORDER BY m.id DESC",('%'+text+'%',)).fetchall();c.close()
    allowed=[]
    wid=q.query.get('workspace_id')
    for r in rows:
        ch=core.get_channel(r['channel_id'])
        if wid is not None and (not ch or str(ch['workspace_id'])!=str(wid)): continue
        if ch and can_channel(u,ch): allowed.append(r)
    rows=allowed
    if cur is not None:rows=[r for r in rows if r['id']<cur]
    more=len(rows)>lim;rows=rows[:lim]
    return web.json_response({'results':[msg_json(r) for r in rows],'next_cursor':enc('search',rows[-1]['id']) if more and rows else None})
async def read_state(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(q.match_info['id']))
    if not ch:return json_error(404,'not found')
    if not can_channel(u,ch):return json_error(403,'forbidden')
    if q.method=='POST':
        d=await read_json(q); v=d.get('last_read_event_id') if d else None
        if not isinstance(v,int) or isinstance(v,bool):return json_error(400,'invalid event id')
        with core.tx() as c:
            old=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()
            c.execute('INSERT OR REPLACE INTO read_states VALUES(?,?,?)',(u['id'],ch['id'],max(v,old['last_read_event_id'] if old else 0)))
    return web.json_response({'read_state':read_obj(u['id'],ch['id'])})


    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    ch=core.get_channel(int(q.match_info['id']))
    if not ch:return json_error(404,'not found')
    return web.json_response({'read_state':{'channel_id':ch['id'],'last_read_event_id':0,'unread_count':0,'mention_count':0}})
async def message_replies(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    return json_error(404,'not found')
async def message_pin(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    return json_error(404,'not found')
async def dms(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    d=await read_json(q); oid=d.get('user_id') if d else None
    if not isinstance(oid,int) or isinstance(oid,bool):return json_error(400,'invalid user_id')
    c=core.connect(); other=c.execute('SELECT id FROM users WHERE id=?',(oid,)).fetchone(); c.close()
    if not other:return json_error(404,'not found')
    a,b=sorted((u['id'],oid))
    with core.tx() as c:
        r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if r: cid=r['channel_id']
        else:
            c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)'); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid)); c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,b))
    return web.json_response({'channel_id':cid})
async def file_upload(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    try:
        rd=await q.multipart(); part=await rd.next()
        if not part or part.name!='file':return json_error(400,'missing file')
        data=await part.read()
        if len(data)>10*1024*1024:return json_error(413,'file too large')
        fn=part.filename or 'file'; ct=part.headers.get('Content-Type','application/octet-stream')
    except:return json_error(400,'invalid upload')
    os.makedirs('/app/data/files',exist_ok=True)
    with core.tx() as c:
        c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],fn,ct,len(data),core.now_iso()))
        fid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    open('/app/data/files/'+str(fid),'wb').write(data)
    return web.json_response({'file':{'id':fid,'uploader_id':u['id'],'filename':fn,'content_type':ct,'size':len(data),'created_at':core.now_iso()}},status=201)
async def file_get(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(int(q.match_info['id']),)).fetchone();c.close()
    if not r:return json_error(404,'not found')
    f={'id':r['id'],'uploader_id':r['uploader_id'],'filename':r['filename'],'content_type':r['content_type'],'size':r['size'],'created_at':r['created_at']}
    if q.path.endswith('/download'):
        try:data=open('/app/data/files/'+str(r['id']),'rb').read()
        except:return json_error(404,'not found')
        return web.Response(body=data,content_type=r['content_type'],headers={'Content-Disposition':'attachment; filename="'+r['filename']+'"'})
    return web.json_response({'file':f})
async def groups(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    if not core.member_role(w['id'],u['id']):return json_error(403,'forbidden')
    if q.method=='GET':
        c=core.connect(); gs=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall(); out=[]
        for g in gs:
            ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall()]
            out.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids})
        c.close(); return web.json_response({'groups':out})
    d=await read_json(q)
    if not isinstance(d,dict) or not isinstance(d.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]+',d['handle']) or not isinstance(d.get('name'),str) or not d['name'] or not isinstance(d.get('user_ids',[]),list):return json_error(400,'invalid group')
    ids=d.get('user_ids',[])
    if any(not isinstance(x,int) or isinstance(x,bool) for x in ids):return json_error(400,'invalid user_ids')
    c=core.connect()
    if c.execute('SELECT 1 FROM groups WHERE workspace_id=? AND handle=?',(w['id'],d['handle'])).fetchone():c.close();return json_error(409,'duplicate handle')
    if any(not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],x)).fetchone() for x in ids):c.close();return json_error(400,'non-member')
    c.close()
    with core.tx() as c:
        c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],d['handle'],d['name'],u['id'])); gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        for x in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,x))
    return web.json_response({'group':{'id':gid,'handle':d['handle'],'name':d['name'],'member_user_ids':ids}},status=201)
async def group_update(q):
    u=auth(q); 
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    c=core.connect();g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],q.match_info['handle'])).fetchone();c.close()
    if not g:
        if q.method=='DELETE': return web.json_response({'deleted':True})
        return json_error(404,'not found')
    if u['id'] not in (g['creator_id'],w['owner_id']):return json_error(403,'forbidden')
    if q.method=='DELETE':
        with core.tx() as c:c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],));c.execute('DELETE FROM groups WHERE id=?',(g['id'],))
        return web.json_response({'deleted':True})
    d=await read_json(q)
    with core.tx() as c:
        if isinstance(d,dict) and isinstance(d.get('name'),str):c.execute('UPDATE groups SET name=? WHERE id=?',(d['name'],g['id']))
        if isinstance(d,dict) and isinstance(d.get('user_ids'),list):
            c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
            for x in d['user_ids']:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],x))
    c=core.connect();ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall()];ng=c.execute('SELECT * FROM groups WHERE id=?',(g['id'],)).fetchone();c.close()
    return web.json_response({'group':{'id':ng['id'],'handle':ng['handle'],'name':ng['name'],'member_user_ids':ids}})
async def user_get(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    c=core.connect(); r=c.execute('SELECT * FROM users WHERE id=?',(int(q.match_info['id']),)).fetchone(); c.close()
    return json_error(404,'not found') if not r else web.json_response({'user':core.user_to_json(r)})
async def user_patch(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    d=await read_json(q)
    if not isinstance(d,dict):return json_error(400,'invalid profile')
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if any(k not in allowed for k in d):return json_error(400,'invalid field')
    if 'timezone' in d:
        try:
            from zoneinfo import ZoneInfo
            if not isinstance(d['timezone'],str):raise ValueError()
            ZoneInfo(d['timezone'])
        except: return json_error(400,'invalid timezone')
    limits={'display_name':80,'avatar_url':512,'status_text':140,'status_emoji':32}
    for k,v in d.items():
        if not isinstance(v,str) or (k in limits and len(v)>limits[k]) or (k=='display_name' and not v):return json_error(400,'invalid field')
    with core.tx() as c:
        if d:
            c.execute('UPDATE users SET '+','.join(k+'=?' for k in d)+' WHERE id=?',tuple(d.values())+(u['id'],))
            r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
            c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps({'type':'user.updated','user':core.user_to_json(r)}),))
    return web.json_response({'user':core.user_to_json(r if d else u)})
async def file_missing(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    return json_error(404,'not found')
async def role_patch(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    d=await read_json(q); role=d.get('role') if d else None
    if role not in ('admin','member','guest'):return json_error(400,'invalid role')
    target=int(q.match_info['user_id']); tr=core.member_role(w['id'],target)
    if not tr:return json_error(404,'not member')
    if tr=='owner' or (core.member_role(w['id'],u['id'])=='admin' and tr=='admin'):return json_error(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target))
    return web.json_response({'member':{'user_id':target,'role':role}})
async def invitations(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return json_error(403,'forbidden')
    c=core.connect(); rows=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)',(w['id'],__import__('time').time())).fetchall(); c.close()
    return web.json_response({'invitations':[dict(r) for r in rows]})
async def create_invitation(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    d=await read_json(q)
    if d is None:return json_error(400,'invalid json')
    try:
      import time
      maxu=d.get('max_uses',1); exp=d.get('expires_in_seconds')
      if d.get('invited_user_id') is not None: maxu=1
      if not isinstance(maxu,int) or maxu<1 or (exp is not None and (not isinstance(exp,int) or exp<1)): raise ValueError()
      target=d.get('invited_user_id')
      if target is not None and not isinstance(target,int): raise ValueError()
      if target is None and d.get('invited_username') is not None:
       if not isinstance(d['invited_username'],str): raise ValueError()
       cc=core.connect(); ur=cc.execute('SELECT id FROM users WHERE username=?',(d['invited_username'],)).fetchone(); cc.close()
       if not ur: raise ValueError()
       target=ur['id']
      code=__import__('secrets').token_urlsafe(12)
      with core.tx() as c:
       c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?,?)',(code,w['id'],u['id'],target,time.time()+exp if exp else None,maxu,0,0))
      return web.json_response({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':maxu,'uses':0,'invited_user_id':target}},status=201)
    except ValueError:return json_error(400,'invalid invitation')
async def accept_invitation(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    import time
    c=core.connect(); r=c.execute('SELECT * FROM invitations WHERE code=?',(q.match_info['code'],)).fetchone(); c.close()
    if not r or r['revoked'] or r['uses']>=r['max_uses'] or (r['expires_at'] and r['expires_at']<=time.time()):return json_error(404,'not found')
    if r['invited_user_id'] is not None and r['invited_user_id']!=u['id']:return json_error(403,'forbidden')
    with core.tx() as c:
      c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(r['code'],)); core.add_workspace_member(c,r['workspace_id'],u['id'],'member')
    c=core.connect(); w=c.execute('SELECT * FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone(); c.close()
    return web.json_response({'workspace':core.workspace_to_json(w)})
async def revoke_invitation(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    with core.tx() as c:
      r=c.execute('SELECT code FROM invitations WHERE code=? AND workspace_id=? AND revoked=0',(q.match_info['code'],w['id'])).fetchone()
      if not r:return json_error(404,'not found')
      c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(r['code'],))
    return web.json_response({'deleted':True})
async def workspace_patch(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    d=await read_json(q)
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return json_error(403,'forbidden')
    if not isinstance(d,dict) or ('join_mode' in d and d['join_mode'] not in ('open','invite_only')):return json_error(400,'invalid join_mode')
    with core.tx() as c:
      if 'name' in d:
       if not isinstance(d['name'],str) or not d['name']:return json_error(400,'invalid name')
       c.execute('UPDATE workspaces SET name=? WHERE id=?',(d['name'],w['id']))
      if 'join_mode' in d:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(d['join_mode'],w['id']))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def auth_placeholder(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    return json_error(404,'not found')
async def transfer(q):
    u=auth(q)
    if not u:return json_error(401,'unauthorized')
    w=core.get_workspace(q.match_info['slug'])
    if not w:return json_error(404,'not found')
    if w['owner_id']!=u['id']:return json_error(403,'forbidden')
    d=await read_json(q); target=d.get('new_owner_id') if d else None
    if core.member_role(w['id'],target)!='admin':return json_error(400,'target must be admin')
    with core.tx() as c:
      c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id'])); c.execute("UPDATE workspace_members SET role='admin' WHERE workspace_id=? AND user_id=?",(w['id'],u['id'])); c.execute("UPDATE workspace_members SET role='owner' WHERE workspace_id=? AND user_id=?",(w['id'],target))
    return web.json_response({'workspace':core.workspace_to_json(core.get_workspace(w['slug']))})
async def index(q):
    return web.FileResponse('/app/static/index.html')
async def appjs(q):
    return web.FileResponse('/app/app.js',headers={'Content-Type':'application/javascript'})
app=web.Application()
app.router.add_get('/',index); app.router.add_get('/app.js',appjs); app.router.add_get('/api/users/{id}',user_get); app.router.add_patch('/api/users/me',user_patch); app.router.add_get('/api/health',health); app.router.add_post('/api/auth/register',register); app.router.add_post('/api/auth/login',login); app.router.add_get('/api/auth/me',me)
app.router.add_patch('/api/workspaces/{slug}',workspace_patch)
app.router.add_get('/api/workspaces/{slug}/invitations',invitations); app.router.add_post('/api/workspaces/{slug}/invitations',create_invitation); app.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_invitation)
app.router.add_post('/api/invitations/{code}/accept',accept_invitation)
app.router.add_get('/api/channels/{id}/join',auth_placeholder); app.router.add_patch('/api/messages/{id}',message_edit); app.router.add_delete('/api/messages/{id}',message_delete); app.router.add_get('/api/channels/{id}/messages',channel_messages); app.router.add_post('/api/channels/{id}/messages',channel_messages); app.router.add_get('/api/messages/{id}/replies',message_replies2); app.router.add_post('/api/messages/{id}/reactions',reaction); app.router.add_delete('/api/messages/{id}/reactions/{emoji}',reaction_delete); app.router.add_post('/api/messages/{id}/pin',pin); app.router.add_delete('/api/messages/{id}/pin',unpin); app.router.add_get('/api/channels/{id}/pins',pins); app.router.add_get('/api/channels/{id}/read',read_state); app.router.add_post('/api/channels/{id}/read',read_state); app.router.add_get('/api/search',search); app.router.add_patch('/api/channels/{id}',channel_patch); app.router.add_get('/api/channels/{id}/members',channel_members); app.router.add_post('/api/channels/{id}/leave',channel_leave); app.router.add_post('/api/channels/{id}/archive',channel_archive); app.router.add_post('/api/channels/{id}/unarchive',channel_archive)
app.router.add_get('/api/workspaces/{slug}/groups',groups); app.router.add_post('/api/workspaces/{slug}/groups',groups); app.router.add_patch('/api/workspaces/{slug}/groups/{handle}',group_update); app.router.add_delete('/api/workspaces/{slug}/groups/{handle}',group_update); app.router.add_post('/api/dms',dms); app.router.add_post('/api/files',file_upload); app.router.add_get('/api/files/{id}',file_get); app.router.add_get('/api/files/{id}/download',file_get)
app.router.add_get('/api/workspaces',workspaces); app.router.add_post('/api/workspaces',create_workspace); app.router.add_get('/api/workspaces/{slug}',workspace_detail); app.router.add_get('/api/workspaces/{slug}/members',members); app.router.add_post('/api/workspaces/{slug}/channels',create_channel); app.router.add_post('/api/channels/{id}/join',join); app.router.add_patch('/api/workspaces/{slug}/members/{user_id}',role_patch); app.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)
async def ws_handler(q):
    u=core.user_for_token(q.query.get('token',''))
    if not u:return json_error(401,'unauthorized')
    ws=web.WebSocketResponse(); await ws.prepare(q); subs={}; conn_id=uuid.uuid4().hex
    gc=core.connect(); grow=gc.execute('SELECT COALESCE(MAX(id),0) n FROM global_events').fetchone(); global_last=grow['n']; gc.close()
    with core.tx() as c: c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,u['id'],time.time()))
    async def deliver():
        nonlocal global_last
        while not ws.closed:
            await __import__('asyncio').sleep(.05)
            gc=core.connect(); gs=gc.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(global_last,)).fetchall(); gc.close()
            for g in gs:
                await ws.send_json(json.loads(g['payload'])); global_last=g['id']
            for cid,last in list(subs.items()):
                c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall(); c.close()
                for r in rows:
                    d=json.loads(r['payload']); await ws.send_json(d); subs[cid]=d['event_id']
    task=__import__('asyncio').create_task(deliver())
    try:
      async for msg in ws:
        if msg.type==web.WSMsgType.TEXT:
          try:d=json.loads(msg.data); cid=int(d.get('channel_id'))
          except:continue
          ch=core.get_channel(cid)
          if not ch:continue
          if d.get('type')=='subscribe':
            subs[cid]=ch['last_event_id']
            await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':ch['last_event_id']})
          elif d.get('type')=='resume':
            since=int(d.get('since_event_id',0)); c=core.connect(); rows=c.execute('SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall(); c.close()
            for r in rows: await ws.send_json(json.loads(r['payload']))
            subs[cid]=ch['last_event_id']; await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':ch['last_event_id']})
    finally:
      task.cancel()
      with core.tx() as c: c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
    return ws
app.router.add_get('/api/ws',ws_handler)
if __name__=='__main__': web.run_app(app,host='127.0.0.1',port=8000+NODE)
