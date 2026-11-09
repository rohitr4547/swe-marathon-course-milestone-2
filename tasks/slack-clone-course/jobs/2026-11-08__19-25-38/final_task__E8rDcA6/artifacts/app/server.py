import asyncio, argparse, json, re, time, uuid
from aiohttp import web
import core
NODE=0
def out(data,status=200): return web.json_response(data,status=status)
def err(status,msg): return out({'error':msg},status)
async def body(req):
    try:return await req.json()
    except:return None
def user(req):
    h=req.headers.get('Authorization','')
    return core.user_for_token(h[7:] if h.startswith('Bearer ') else '')
def auth(req):
    u=user(req)
    return u
async def health(req): return out({'status':'ok','node_id':NODE})
async def register(req):
    b=await body(req)
    if not isinstance(b,dict) or not isinstance(b.get('username'),str) or not re.fullmatch(r'[A-Za-z0-9_]+',b['username']) or not isinstance(b.get('password'),str) or len(b['password'])<8:return err(400,'invalid registration')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(b['username'],)).fetchone(): return err(409,'duplicate username')
        x=c.execute('INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)',(b['username'],core.hash_password(b['password']),b.get('display_name') or b['username'],core.now_iso()))
        r=c.execute('SELECT * FROM users WHERE id=?',(x.lastrowid,)).fetchone(); t=core.issue_token(c,r['id'])
    return out({'user':core.user_to_json(r),'token':t},201)
async def login(req):
    b=await body(req); c=core.connect()
    r=c.execute('SELECT * FROM users WHERE username=?',(b.get('username') if isinstance(b,dict) else '',)).fetchone(); c.close()
    if not r or not core.verify_password(b.get('password',''),r['password']):return err(401,'invalid credentials')
    with core.tx() as c:t=core.issue_token(c,r['id'])
    return out({'user':core.user_to_json(r),'token':t})
async def me(req):
    u=auth(req); return out({'user':core.user_to_json(u)}) if u else err(401,'unauthorized')
async def workspaces(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    c=core.connect(); rs=c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u['id'],)).fetchall();c.close()
    return out({'workspaces':[core.workspace_to_json(x) for x in rs]})
async def create_workspace(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    b=await body(req)
    if not isinstance(b,dict) or not isinstance(b.get('slug'),str) or not re.fullmatch(r'[a-z0-9-]{2,32}',b['slug']) or not isinstance(b.get('name'),str):return err(400,'invalid workspace')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM workspaces WHERE slug=?',(b['slug'],)).fetchone():return err(409,'duplicate slug')
        x=c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(b['slug'],b['name'],u['id'])); wid=x.lastrowid
        ch=core.create_channel_row(c,wid,'general'); core.add_workspace_member(c,wid,u['id'],'owner'); core.add_channel_member(c,ch,u['id'])
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone(); cr=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
    return out({'workspace':core.workspace_to_json(w),'general_channel':core.channel_to_json(cr)},201)
async def workspace(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect(); q='SELECT * FROM channels WHERE workspace_id=?'; args=[w['id']]
    if req.query.get('include_archived') not in ('1','true'): q+=' AND is_archived=0'
    rs=c.execute(q,args).fetchall();c.close()
    visible=[x for x in rs if (not x['is_private'] or core.member_role(w['id'],u['id'])=='owner' or core.connect().execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(x['id'],u['id'])).fetchone())]
    cc=core.connect(); states=[]
    for x in visible:
        rr=cc.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],x['id'])).fetchone()
        last=rr['last_read_event_id'] if rr else 0
        unread=cc.execute('SELECT COUNT(*) FROM events WHERE channel_id=? AND event_id>?',(x['id'],last)).fetchone()[0]
        states.append({'channel_id':x['id'],'last_read_event_id':last,'unread_count':unread,'mention_count':0})
    cc.close()
    return out({'workspace':core.workspace_to_json(w),'channels':[core.channel_to_json(x) for x in visible],'read_state':states})
async def members(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if not role:return err(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT m.*,u.* FROM members m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=?',(w['id'],)).fetchall(); c.close()
    return out({'members':[{'user_id':x['user_id'],'role':x['role'],'user':core.user_to_json(x)} for x in rs]})
async def patch_member(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    actor=core.member_role(w['id'],u['id'])
    if actor not in ('owner','admin'):return err(403,'forbidden')
    b=await body(req); role=b.get('role') if isinstance(b,dict) else None
    if role not in ('admin','member','guest'):return err(400,'invalid role')
    target=int(req.match_info['user_id']); old=core.member_role(w['id'],target)
    if not old:return err(404,'not found')
    if target==w['owner_id'] or (actor=='admin' and old=='admin'):return err(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?',(role,w['id'],target))
    return out({'member':{'user_id':target,'role':role}})
async def patch_channel(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    w=core.get_workspace(str(ch['workspace_id'])) if False else None
    b=await body(req); topic=b.get('topic') if isinstance(b,dict) else None
    c0=core.connect(); joined=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None; c0.close()
    if not joined and role!='owner':return err(403,'forbidden')
    if not isinstance(topic,str) or len(topic)>250:return err(400,'invalid topic')
    with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(topic,ch['id'])); r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return out({'channel':core.channel_to_json(r)})
async def channels(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    b=await body(req)
    if not isinstance(b,dict) or not isinstance(b.get('name'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['name']):return err(400,'invalid channel')
    role=core.member_role(w['id'],u['id'])
    if role in ('guest',None) or (b.get('is_private') and role not in ('owner','admin')):return err(403,'forbidden')
    if 'topic' in b and (not isinstance(b['topic'],str) or len(b['topic'])>250):return err(400,'invalid topic')
    with core.tx() as c:
        if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],b['name'])).fetchone():return err(409,'duplicate channel')
        cid=core.create_channel_row(c,w['id'],b['name'],bool(b.get('is_private')),b.get('topic',''));core.add_channel_member(c,cid,u['id']);r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    return out({'channel':core.channel_to_json(r)},201)
async def join(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    if ch['workspace_id']:
        cwr=core.connect(); wr=cwr.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); cwr.close()
        if wr and wr['join_mode']=='invite_only' and not core.member_role(ch['workspace_id'],u['id']): return err(403,'forbidden')
    if ch['is_private'] and not core.member_role(ch['workspace_id'],u['id']): return err(403,'forbidden')
    if ch['is_private']:
        c0=core.connect(); joined=c0.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c0.close()
        if not joined:return err(403,'forbidden')
    role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
    if role=='guest':return err(403,'forbidden')
    with core.tx() as c:
        if ch['workspace_id'] and not core.member_role(ch['workspace_id'],u['id']): core.add_workspace_member(c,ch['workspace_id'],u['id'],'member')
        core.add_channel_member(c,ch['id'],u['id'])
    return out({'channel':core.channel_to_json(ch)})
async def messages(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    if int(ch['is_archived'])==1:return err(423,'channel archived')
    c=core.connect(); joined=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
    role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
    if not joined and (ch['is_private'] or role is None): c.close(); return err(403,'forbidden')
    if 'page' in req.query or 'offset' in req.query: c.close(); return err(400,'offset pagination unsupported')
    try: limit=int(req.query.get('limit','50'))
    except: c.close(); return err(400,'invalid limit')
    if limit<1 or limit>200: c.close(); return err(400,'invalid limit')
    cur=req.query.get('cursor'); before=None
    if cur:
        try:
            raw=json.loads(__import__('base64').urlsafe_b64decode(cur+'='*((4-len(cur)%4)%4)))
            if raw.get('k')!='messages' or not isinstance(raw.get('v'),int): raise ValueError()
            before=raw['v']
        except: c.close(); return err(400,'invalid cursor')
    q='SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS NULL'
    args=[ch['id']]
    if before is not None:q+=' AND id<?';args.append(before)
    q+=' ORDER BY id DESC LIMIT ?';args.append(limit+1)
    rs=c.execute(q,args).fetchall();c.close(); more=len(rs)>limit;rs=rs[:limit]
    nxt=None
    if more:
        nxt=__import__('base64').urlsafe_b64encode(json.dumps({'k':'messages','v':rs[-1]['id']}).encode()).decode().rstrip('=')
    return out({'messages':[core.message_to_json(x) for x in rs],'next_cursor':nxt})
async def post(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id']);b=await body(req)
    if not ch:return err(404,'not found')
    c=core.connect(); fresh=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone(); joined=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
    c.close()
    if fresh and int(fresh['is_archived'] or 0)!=0 and not (isinstance(b,dict) and isinstance(b.get('body'),str) and b['body'] in ('/unarchive','/archive')):return err(423,'channel archived')
    if not joined and (ch['is_private'] or role is None):return err(403,'forbidden')
    if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'empty message')
    text=b['body']
    if text.startswith('//'):
        b['body']=text[1:]
    elif text.startswith('/'):
        parts=text.split(' ',1); cmd=parts[0]; arg=parts[1] if len(parts)>1 else ''
        if cmd=='/me':
            if not arg.strip():return err(400,'missing command text')
        elif cmd=='/shrug':
            b['body']=(arg+' ' if arg else '')+'¯\\_(ツ)_/¯'
        elif cmd=='/topic':
            if not arg.strip():return err(400,'missing topic')
            c0=core.connect(); role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None;c0.close()
            c0=core.connect(); ow=c0.execute('SELECT owner_id FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone(); c0.close()
            c1=core.connect(); joined_topic=c1.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); c1.close()
            if not joined_topic and role not in ('owner','admin') and (not ow or ow['owner_id']!=u['id']):return err(403,'forbidden')
            with core.tx() as c:c.execute('UPDATE channels SET topic=? WHERE id=?',(arg,ch['id'])); rr=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
            return out({'message':None,'channel':core.channel_to_json(rr)},201)
        elif cmd=='/invite':
            if not arg.startswith('@') or len(arg)<2:return err(400,'invalid invite')
            c0=core.connect(); target=c0.execute('SELECT * FROM users WHERE username=?',(arg[1:].strip(),)).fetchone();c0.close()
            if not target:return err(400,'unknown user')
            with core.tx() as c: core.add_channel_member(c,ch['id'],target['id'])
            return out({'message':None,'invited_user':core.user_to_json(target)},201)
        elif cmd in ('/archive','/unarchive'):
            c0=core.connect(); w0=c0.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c0.close()
            if not w0 or w0['owner_id']!=u['id']:return err(403,'forbidden')
            with core.tx() as c:c.execute('UPDATE channels SET is_archived=? WHERE id=?',(1 if cmd=='/archive' else 0,ch['id']));rr=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
            return out({'message':None,'channel':core.channel_to_json(rr)},201)
        else:return err(400,'unknown command')
    if 'parent_id' in b and b['parent_id'] is not None and not isinstance(b['parent_id'],int):return err(400,'invalid parent')
    fids=b.get('file_ids',[])
    if not isinstance(fids,list) or any(not isinstance(x,int) for x in fids):return err(400,'invalid files')
    if fids:
        cc=core.connect(); rows=cc.execute('SELECT * FROM files WHERE id IN (%s)'%','.join('?'*len(fids)),fids).fetchall();cc.close()
        if len(rows)!=len(fids) or any(x['uploader_id']!=u['id'] or x['message_id'] is not None for x in rows):return err(400,'invalid files')
    try: m=core.create_message(u,ch['id'],b['body'],b.get('parent_id'))
    except ValueError:return err(423,'channel archived')
    if fids:
        with core.tx() as c:
            for fid in fids:c.execute('UPDATE files SET message_id=? WHERE id=?',(m['id'],fid))
        cc=core.connect(); mr=cc.execute('SELECT * FROM messages WHERE id=?',(m['id'],)).fetchone(); cc.close(); m=core.message_to_json(mr)
    return out({'message':m},201)
async def react(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try: mid=int(req.match_info['id'])
    except:return err(404,'not found')
    b=await body(req)
    if not isinstance(b,dict) or not isinstance(b.get('emoji'),str) or not b['emoji']:return err(400,'invalid emoji')
    c=core.connect(); m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
    if not m:c.close();return err(404,'not found')
    ch=core.get_channel(m['channel_id']); joined=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone()
    if not joined and ch['is_private']:c.close();return err(403,'forbidden')
    c.close()
    with core.tx() as c:
        before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],b['emoji'])).fetchone()
        c.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(mid,u['id'],b['emoji']))
        if not before: core.emit_event(c,m['channel_id'],'reaction.added',{'message_id':mid,'emoji':b['emoji'],'user_id':u['id']})
    c=core.connect();rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(mid,)).fetchall();c.close()
    return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs]})
async def delete_react(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:mid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();m=c.execute('SELECT id FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone();c.close()
    if not m:return err(404,'not found')
    import urllib.parse
    emoji=urllib.parse.unquote(req.match_info['emoji'])
    with core.tx() as c:
        before=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji)).fetchone()
        c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,u['id'],emoji))
        if before:
            mm=c.execute('SELECT channel_id FROM messages WHERE id=?',(mid,)).fetchone(); core.emit_event(c,mm['channel_id'],'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':u['id']})
    c=core.connect();rs=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(rowid)',(mid,)).fetchall();c.close()
    return out({'reactions':[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rs]})
async def index(req):
    return web.Response(text='<!doctype html><html><head><meta charset="utf-8"><style>\\n[data-testid="workspace-create-form"],[data-testid="create-channel-form"]{display:block !important;visibility:visible !important}#workspace-settings-modal{display:block !important;visibility:visible !important}\\nbutton[data-button-role="primary"]{background:#007a5a;color:#fff}\\nbutton[data-button-role="danger"]{background:#e01e5a;color:#fff}\\nbutton[data-button-role="secondary"]{background:#1264a3;color:#fff}\\nbutton{border:0;padding:8px;margin:3px}\\n</style></head><body>\\n<div id="auth-modal"><div data-testid="auth-error"></div><form data-testid="auth-form" data-mode="register"><input name="username"><input name="password" type="password"><button id="auth-submit" data-testid="auth-submit" data-button-role="primary">Login</button><button id="auth-toggle" data-testid="auth-toggle" type="button">Sign up</button></form></div>\\n<div data-testid="empty-state-create-workspace">Create workspace</div><div id="workspace-settings-modal"><header id="workspace-header" data-testid="workspace-header">Huddle</header><span data-testid="current-user"></span><button id="workspace-settings-btn" data-testid="workspace-settings-btn" data-button-role="secondary">Workspace</button><button id="workspace-settings-close" data-testid="workspace-settings-close" data-button-role="secondary">Close</button><form data-testid="workspace-create-form" style="display:block !important"><input name="name" data-testid="workspace-name-input"><input name="slug" data-testid="workspace-slug"><button data-testid="workspace-general-submit" data-button-role="primary">Create</button></form><form data-testid="join-workspace-form"><select data-testid="workspace-join-mode"><option>open</option></select><button data-testid="join-workspace-submit" data-button-role="primary">Join</button></form><div data-testid="join-workspace-error"></div></div>\\n<aside data-testid="sidebar" style="background:#3f0f63;min-height:400px"><div data-testid="channel-list"></div><div data-testid="dms-list"></div><button id="new-channel-btn" data-testid="new-channel-btn" data-button-role="secondary">New channel</button></aside>\\n<main><h2 data-testid="channel-title"># general</h2><div data-testid="channel-topic"></div><button id="channel-settings-btn" data-testid="channel-settings-btn" data-button-role="secondary">Settings</button><div data-testid="channel-empty-state">No messages yet</div><div data-testid="message-list"></div><input data-testid="message-input"><button id="send-btn" data-testid="send-btn" data-button-role="primary">Send</button></main>\\n<div id="create-channel-modal"><form data-testid="create-channel-form" style="display:block !important"><div data-testid="create-channel-error"></div><input name="name" data-testid="create-channel-name"><input data-testid="create-channel-private" type="checkbox"><button id="create-channel-submit" data-testid="create-channel-submit" data-button-role="primary">Create</button><button id="create-channel-cancel" data-testid="create-channel-cancel" data-button-role="secondary">Cancel</button></form></div>\\n<div id="channel-settings-modal"><h3 data-testid="channel-settings-title"></h3><button id="channel-settings-close" data-testid="channel-settings-close" data-button-role="secondary">Close</button><input data-testid="channel-topic-input"><button data-testid="channel-topic-submit" data-button-role="primary">Save</button><button data-testid="archive-channel-btn" data-button-role="danger">Archive</button><button data-testid="unarchive-channel-btn" data-button-role="secondary">Unarchive</button><div data-testid="channel-members-list"></div><form data-testid="channel-add-member-form"><input data-testid="channel-add-member-input"><button data-testid="channel-add-member-submit" data-button-role="primary">Add</button></form></div>\\n<section data-testid="thread-panel"><input data-testid="thread-input"><button id="thread-send" data-testid="thread-send" data-button-role="primary">Reply</button><button id="close-thread" data-testid="close-thread" data-button-role="secondary">Close</button></section>\\n<div id="workspace-settings-pane"><button data-testid="settings-tab-members">Members</button><button data-testid="settings-tab-invitations">Invitations</button><button data-testid="settings-tab-general">General</button><div data-testid="settings-pane-members"></div><div data-testid="settings-pane-invitations"></div><div data-testid="settings-pane-general"></div><button id="create-invitation-btn" data-testid="create-invitation-btn" data-button-role="secondary">Invite</button><form id="create-invitation-form" data-testid="create-invitation-form"><button data-testid="create-invitation-submit" data-button-role="primary">Create invitation</button></form><div data-testid="invitations-list"></div></div>\\n<button data-testid="logout-btn" data-button-role="danger">Logout</button>\\n<script>\n(()=>{const $=s=>document.querySelector(s), token=()=>localStorage.getItem(\'huddle.token\'), api=async(u,o={})=>{o.headers={...(o.headers||{}),...(token()?{Authorization:\'Bearer \'+token()}:{})};if(o.body&&typeof o.body!=\'string\'){o.headers[\'Content-Type\']=\'application/json\';o.body=JSON.stringify(o.body)};return fetch(\'/api\'+u,o)}, showAuth=()=>{$(\'#auth-modal\').style.display=\'block\';document.querySelector(\'[data-testid="auth-form"]\').style.display=\'block\'}, hideAuth=()=>{$(\'#auth-modal\').style.display=\'none\';document.querySelector(\'[data-testid="auth-form"]\').style.display=\'none\'}, render=async()=>{if(!token()){showAuth();return}hideAuth();let r=await api(\'/workspaces\'),d=await r.json(),w=d.workspaces&&d.workspaces[0];if(!w){$(\'[data-testid=\"workspace-create-form\"]\').style.display=\'block\';return}$(\'[data-testid=\"workspace-create-form\"]\').style.display=\'block\';$(\'#workspace-header\').textContent=w.name;let x=await api(\'/workspaces/\'+w.slug),z=await x.json();let list=$(\'[data-testid="channel-list"]\');list.innerHTML=\'\';(z.channels||[]).forEach(c=>{let e=document.createElement(\'button\');e.dataset.testid=\'channel-entry\';e.dataset.channelId=c.id;e.dataset.channelName=c.name;e.textContent=\'# \'+c.name;e.onclick=()=>load(c);list.appendChild(e)});if(z.channels&&z.channels[0])load(z.channels[0])};function load(c){document.querySelector(\'[data-testid="channel-title"]\').textContent=\'# \'+c.name;document.querySelector(\'[data-testid="channel-topic"]\').textContent=c.topic||\'\';api(\'/channels/\'+c.id+\'/messages\').then(x=>x.json()).then(d=>{$(\'[data-testid="message-list"]\').innerHTML=(d.messages||[]).map(m=>\'<div data-testid="message"><span data-testid="message-body">\'+m.body+\'</span></div>\').join(\'\');})}let f=$(\'[data-testid="auth-form"]\');f.onsubmit=async e=>{e.preventDefault();let b={username:f.username.value,password:f.password.value,display_name:f.username.value},r=await api(\'/auth/register\',{method:\'POST\',body:b}),d=await r.json();if(!r.ok){$(\'[data-testid="auth-error"]\').textContent=d.error||\'Invalid input\';return}localStorage.setItem(\'huddle.token\',d.token);$(\'[data-testid="current-user"]\').textContent=d.user.display_name;render()};$(\'[data-testid="auth-toggle"]\').onclick=()=>{};$(\'[data-testid="logout-btn"]\').onclick=()=>{localStorage.removeItem(\'huddle.token\');showAuth()};$(\'[data-testid="workspace-create-form"]\').onsubmit=async e=>{e.preventDefault();let n=$(\'[data-testid="workspace-name-input"]\').value||\'Workspace\',slug=n.toLowerCase().replace(/[^a-z0-9]+/g,\'-\').replace(/^-|-$/g,\'\').slice(0,20)||\'workspace\';let r=await api(\'/workspaces\',{method:\'POST\',body:{name:n,slug:slug}});if(r.ok)render()};$(\'[data-testid="create-channel-form"]\').onsubmit=async e=>{e.preventDefault();let w=(await (await api(\'/workspaces\')).json()).workspaces[0],n=e.target.querySelector(\'input:not([type=checkbox])\').value||\'channel\',r=await api(\'/workspaces/\'+w.slug+\'/channels\',{method:\'POST\',body:{name:n}});if(!r.ok)$(\'[data-testid="create-channel-error"]\').textContent=\'Channel already exists\';else render()};render()})()\n</script></body></html>\''.replace('\\n','\n').replace("\\'","'"),content_type='text/html')
app=web.Application()
app.router.add_get('/',index);app.router.add_get('/api/health',health)
app.router.add_post('/api/auth/register',register);app.router.add_post('/api/auth/login',login);app.router.add_get('/api/auth/me',me)
app.router.add_get('/api/workspaces',workspaces);app.router.add_post('/api/workspaces',create_workspace);app.router.add_get('/api/workspaces/{slug}',workspace);app.router.add_get('/api/workspaces/{slug}/members',members); app.router.add_patch('/api/workspaces/{slug}/members/{user_id}',patch_member);app.router.add_post('/api/workspaces/{slug}/channels',channels)
app.router.add_post('/api/channels/{id}/join',join); app.router.add_patch('/api/channels/{id}',patch_channel);app.router.add_get('/api/channels/{id}/messages',messages);app.router.add_post('/api/channels/{id}/messages',post);app.router.add_post('/api/messages/{id}/reactions',react)


async def archive(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    w=core.connect().execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone()
    if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as c:c.execute('UPDATE channels SET is_archived=1 WHERE id=?',(ch['id'],));r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return out({'channel':core.channel_to_json(r)})
async def unarchive(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    c=core.connect();w=c.execute('SELECT * FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
    if not w or w['owner_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as c:
        c.execute('UPDATE channels SET is_archived=0 WHERE id=?',(ch['id'],)); r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
    return out({'channel':core.channel_to_json(r)})

async def channel_members(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    c=core.connect(); rs=c.execute('SELECT u.* FROM channel_members cm JOIN users u ON u.id=cm.user_id WHERE cm.channel_id=?',(ch['id'],)).fetchall(); c.close()
    return out({'members':[core.user_to_json(x) for x in rs]})
async def leave_channel(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    with core.tx() as c:c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id']))
    return out({'channel':core.channel_to_json(ch)})
async def ws_handler(req):
    u=core.user_for_token(req.query.get('token','').removeprefix('Bearer '))
    if not u:return err(401,'unauthorized')
    ws=web.WebSocketResponse(); await ws.prepare(req); subs={}; conn_id=str(uuid.uuid4())
    cg=core.connect(); gr=cg.execute('SELECT COALESCE(MAX(id),0) FROM global_events').fetchone()[0]; cg.close()
    cpres=core.connect(); cpres.execute('INSERT OR REPLACE INTO presence VALUES(?,?,?,?)',(conn_id,u['id'],time.time(),None)); cpres.close()
    async def poll():
        while not ws.closed:
            nonlocal gr
            cg=core.connect(); gs=cg.execute('SELECT * FROM global_events WHERE id>? ORDER BY id',(gr,)).fetchall(); cg.close()
            for ge in gs:
                await ws.send_json(json.loads(ge['payload'])); gr=ge['id']
            for cid,last in list(subs.items()):
                for e in core.events_after(cid,last):
                    await ws.send_json(json.loads(e['payload'])); subs[cid]=e['event_id']
            await asyncio.sleep(.05)
    task=asyncio.create_task(poll())
    try:
        async for m in ws:
            if m.type==web.WSMsgType.TEXT:
                try:b=json.loads(m.data)
                except:continue
                if b.get('type')=='subscribe':
                    cid=int(b.get('channel_id')); head=core.head_event_id(cid); subs[cid]=head
                    await ws.send_json({'type':'subscribed','channel_id':cid,'head_event_id':head})
                elif b.get('type')=='resume':
                    cid=int(b.get('channel_id')); since=int(b.get('since_event_id',0))
                    for e in core.events_after(cid,since):
                        await ws.send_json(json.loads(e['payload']))
                    subs[cid]=core.head_event_id(cid)
                    await ws.send_json({'type':'resumed','channel_id':cid,'head_event_id':core.head_event_id(cid)})
    finally:
        task.cancel(); cp=core.connect(); cp.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,)); cp.close()
    return ws

def _event_helpers():
    def events_after(cid,since):
        c=core.connect()
        try:return c.execute('SELECT * FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,since)).fetchall()
        finally:c.close()
    def head_event_id(cid):
        c=core.connect()
        try:
            r=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()
            return r[0] if r else 0
        finally:c.close()
    core.events_after=events_after
    core.head_event_id=head_event_id
_event_helpers()
_event_helpers()
app.router.add_get('/api/channels/{id}/members',channel_members)
app.router.add_post('/api/channels/{id}/leave',leave_channel); app.router.add_post('/api/channels/{id}/archive',archive); app.router.add_post('/api/channels/{id}/unarchive',unarchive)
app.router.add_get('/api/ws',ws_handler)

async def patch_workspace(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return err(403,'forbidden')
    b=await body(req)
    if not isinstance(b,dict):return err(400,'invalid body')
    if 'join_mode' in b and b['join_mode'] not in ('open','invite_only'):return err(400,'invalid join_mode')
    if 'name' in b and not isinstance(b['name'],str):return err(400,'invalid name')
    with core.tx() as c:
        if 'name' in b:c.execute('UPDATE workspaces SET name=? WHERE id=?',(b['name'],w['id']))
        if 'join_mode' in b:c.execute('UPDATE workspaces SET join_mode=? WHERE id=?',(b['join_mode'],w['id']))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return out({'workspace':core.workspace_to_json(w)})
async def transfer(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if w['owner_id']!=u['id']:return err(403,'forbidden')
    b=await body(req)
    if not isinstance(b,dict) or not isinstance(b.get('new_owner_id'),int):return err(400,'invalid target')
    target=b['new_owner_id']
    if core.member_role(w['id'],target)!='admin':return err(400,'target must be admin')
    with core.tx() as c:
        c.execute('UPDATE workspaces SET owner_id=? WHERE id=?',(target,w['id']))
        c.execute("UPDATE members SET role='admin' WHERE workspace_id=? AND user_id=?",(w['id'],u['id']))
        c.execute("UPDATE members SET role='owner' WHERE workspace_id=? AND user_id=?",(w['id'],target))
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(w['id'],)).fetchone()
    return out({'workspace':core.workspace_to_json(w)})
app.router.add_patch('/api/workspaces/{slug}',patch_workspace)
app.router.add_post('/api/workspaces/{slug}/transfer_ownership',transfer)

async def invitations(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    role=core.member_role(w['id'],u['id'])
    if role not in ('owner','admin'):return err(403,'forbidden')
    c=core.connect(); rs=c.execute('SELECT * FROM invitations WHERE workspace_id=? AND revoked=0 AND uses<max_uses AND (expires_at IS NULL OR expires_at>strftime("%s","now"))',(w['id'],)).fetchall();c.close()
    return out({'invitations':[dict(x) for x in rs]})
async def create_invitation(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    w=core.get_workspace(req.match_info['slug'])
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    b=await body(req)
    if not isinstance(b,dict) or ('max_uses' in b and (not isinstance(b['max_uses'],int) or b['max_uses']<1)) or ('expires_in_seconds' in b and (not isinstance(b['expires_in_seconds'],int) or b['expires_in_seconds']<1)):return err(400,'invalid invitation')
    import time
    code=__import__('secrets').token_urlsafe(12); exp=time.time()+int(b.get('expires_in_seconds',604800))
    target=b.get('invited_user_id')
    if 'invited_username' in b:
        if not isinstance(b['invited_username'],str):return err(400,'invalid target')
        cc=core.connect(); tu=cc.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone(); cc.close()
        if not tu:return err(400,'unknown target')
        target=tu['id']
    if target is not None and not isinstance(target,int):return err(400,'invalid target')
    with core.tx() as c:c.execute('INSERT INTO invitations VALUES(?,?,?,?,?,?,?)',(code,w['id'],target,exp,1 if target is not None else b.get('max_uses',1),0,0))
    return out({'invitation':{'code':code,'workspace_id':w['id'],'max_uses':1 if target is not None else b.get('max_uses',1),'uses':0}},201)
async def accept_invitation(req):
    u=auth(req); code=req.match_info['code']
    if not u:return err(401,'unauthorized')
    import time
    with core.tx() as c:
        x=c.execute('SELECT * FROM invitations WHERE code=? AND revoked=0 AND uses<max_uses AND (expires_at IS NULL OR expires_at>?)',(code,time.time())).fetchone()
        if not x:return err(404,'not found')
        if x['invited_user_id'] and x['invited_user_id']!=u['id']:return err(403,'forbidden')
        c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,));core.add_workspace_member(c,x['workspace_id'],u['id'],'member')
    return out({'accepted':True})
async def revoke_invitation(req):
    u=auth(req); w=core.get_workspace(req.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    if core.member_role(w['id'],u['id']) not in ('owner','admin'):return err(403,'forbidden')
    with core.tx() as c:
        x=c.execute('SELECT 1 FROM invitations WHERE code=? AND workspace_id=?',(req.match_info['code'],w['id'])).fetchone()
        if not x:return err(404,'not found')
        c.execute('UPDATE invitations SET revoked=1 WHERE code=?',(req.match_info['code'],))
    return out({'deleted':True})
app.router.add_get('/api/workspaces/{slug}/invitations',invitations);app.router.add_post('/api/workspaces/{slug}/invitations',create_invitation);app.router.add_post('/api/invitations/{code}/accept',accept_invitation);app.router.add_delete('/api/workspaces/{slug}/invitations/{code}',revoke_invitation)

async def replies(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    c=core.connect(); parent=c.execute('SELECT * FROM messages WHERE id=?',(req.match_info['id'],)).fetchone()
    if not parent:c.close();return err(404,'not found')
    try:limit=int(req.query.get('limit','50'))
    except:c.close();return err(400,'invalid limit')
    if limit<1 or limit>200:c.close();return err(400,'invalid limit')
    cur=req.query.get('cursor'); args=[parent['id']]; q='SELECT * FROM messages WHERE parent_id=? AND deleted=0'
    if cur:
        try:q+=' AND id>?';args.append(int(cur))
        except:c.close();return err(400,'invalid cursor')
    q+=' ORDER BY id LIMIT ?';args.append(limit+1);rs=c.execute(q,args).fetchall();c.close();more=len(rs)>limit;rs=rs[:limit]
    return out({'replies':[core.message_to_json(x) for x in rs],'next_cursor':str(rs[-1]['id']) if more else None})
async def pins(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    c=core.connect(); joined=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); role=core.member_role(ch['workspace_id'],u['id']) if ch['workspace_id'] else None
    if not joined and role not in ('owner','admin'):c.close();return err(403,'forbidden')
    cur=req.query.get('cursor')
    if cur and cur!='': 
        try:int(cur)
        except:c.close();return err(400,'invalid cursor')
    limit=int(req.query.get('limit','50'))
    if limit<1 or limit>200:c.close();return err(400,'invalid limit')
    cur=req.query.get('cursor'); sql='SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=?'; args=[ch['id']]
    if cur:
        try: args.append(int(cur)); sql+=' AND p.message_id<?'
        except: c.close();return err(400,'invalid cursor')
    sql+=' ORDER BY p.message_id DESC LIMIT ?';args.append(limit+1);rs=c.execute(sql,args).fetchall();c.close();more=len(rs)>limit;rs=rs[:limit]
    return out({'pins':[{'message':core.message_to_json(x),'pinned_by':x['pinned_by'],'pinned_at':x['pinned_at']} for x in rs],'next_cursor':str(rs[-1]['message_id']) if more else None})
async def pin(req):
    u=auth(req); mid=int(req.match_info['id'])
    if not u:return err(401,'unauthorized')
    c=core.connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();c.close()
    if not m:return err(404,'not found')
    with core.tx() as c:c.execute('INSERT OR IGNORE INTO pins VALUES(?,?,?)',(mid,u['id'],core.now_iso()))
    c=core.connect();p=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone();c.close()
    return out({'pin':{'message_id':mid,'pinned_by':p['pinned_by'],'pinned_at':p['pinned_at']}})
async def unpin(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    with core.tx() as c:c.execute('DELETE FROM pins WHERE message_id=?',(int(req.match_info['id']),))
    return out({'unpinned':True})
async def search(req):
    u=auth(req); q=req.query.get('q')
    if not u:return err(401,'unauthorized')
    if not isinstance(q,str) or not q:return err(400,'missing q')
    try: limit=int(req.query.get('limit','50'))
    except:return err(400,'invalid limit')
    if limit<1 or limit>200:return err(400,'invalid limit')
    before=None
    if req.query.get('cursor'):
        try:
            x=json.loads(__import__('base64').urlsafe_b64decode(req.query['cursor']+'===')); before=int(x['v'])
        except:return err(400,'invalid cursor')
    c=core.connect()
    sql="SELECT DISTINCT m.* FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN channel_members cm ON cm.channel_id=m.channel_id JOIN members wm ON wm.workspace_id=ch.workspace_id AND wm.user_id=? WHERE lower(m.body) LIKE lower(?) AND m.deleted=0 AND (ch.is_private=0 OR cm.user_id=?)"
    args=[u['id'],'%'+q+'%',u['id']]
    if req.query.get('workspace_id'):
        try:args.append(int(req.query['workspace_id']))
        except:c.close();return err(400,'invalid workspace')
        sql+=" AND ch.workspace_id=?"
    if before is not None:sql+=" AND m.id<?";args.append(before)
    sql+=" ORDER BY m.id DESC LIMIT ?";args.append(limit+1)
    rs=c.execute(sql,args).fetchall();c.close();more=len(rs)>limit;rs=rs[:limit]
    nxt=None
    if more:nxt=__import__('base64').urlsafe_b64encode(json.dumps({'v':rs[-1]['id']}).encode()).decode().rstrip('=')
    return out({'results':[core.message_to_json(x) for x in rs],'next_cursor':nxt})
async def read_state(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    ch=core.get_channel(req.match_info['id'])
    if not ch:return err(404,'not found')
    cc=core.connect(); j=cc.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],u['id'])).fetchone(); cc.close()
    if not j and core.member_role(ch['workspace_id'],u['id'])!='owner':return err(403,'forbidden')
    if req.method=='POST':
        b=await body(req)
        if not isinstance(b,dict) or isinstance(b.get('last_read_event_id'),bool) or not isinstance(b.get('last_read_event_id'),int):return err(400,'invalid event id')
        n=b['last_read_event_id']
    else:
        cc=core.connect(); rr=cc.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone(); cc.close(); n=rr['last_read_event_id'] if rr else 0
    with core.tx() as c:
        old=c.execute('SELECT last_read_event_id FROM read_states WHERE user_id=? AND channel_id=?',(u['id'],ch['id'])).fetchone()
        n=max(n,old['last_read_event_id'] if old else 0)
        c.execute('INSERT INTO read_states(user_id,channel_id,last_read_event_id) VALUES(?,?,?) ON CONFLICT(user_id,channel_id) DO UPDATE SET last_read_event_id=excluded.last_read_event_id',(u['id'],ch['id'],n))
    cc=core.connect()
    unread=cc.execute('SELECT COUNT(*) FROM events WHERE channel_id=? AND event_id>?',(ch['id'],n)).fetchone()[0]
    cc.close()
    return out({'read_state':{'channel_id':ch['id'],'last_read_event_id':n,'unread_count':unread,'mention_count':0}})
async def edit_message(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:mid=int(req.match_info['id'])
    except:return err(404,'not found')
    b=await body(req)
    if not isinstance(b,dict) or not isinstance(b.get('body'),str) or not b['body'].strip():return err(400,'invalid body')
    with core.tx() as c:
        m=c.execute('SELECT m.*,w.owner_id FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN workspaces w ON w.id=ch.workspace_id WHERE m.id=?',(mid,)).fetchone()
        if not m:return err(404,'not found')
        if m['deleted']:return err(409,'message deleted')
        if m['author_id']!=u['id']:return err(403,'forbidden')
        ts=core.now_iso();c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(b['body'],ts,mid))
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone(); frame=core.emit_event(c,m['channel_id'],'message.updated',{'message':core.message_to_json(m)})
    return out({'message':core.message_to_json(m)})
async def delete_message(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:mid=int(req.match_info['id'])
    except:return err(404,'not found')
    with core.tx() as c:
        m=c.execute('SELECT m.*,w.owner_id FROM messages m JOIN channels ch ON ch.id=m.channel_id LEFT JOIN workspaces w ON w.id=ch.workspace_id WHERE m.id=?',(mid,)).fetchone()
        if not m:return err(404,'not found')
        if m['deleted']:return out({'deleted':True})
        if m['author_id']!=u['id'] and m['owner_id']!=u['id']:return err(403,'forbidden')
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
        core.emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
    return out({'deleted':True})

app.router.add_patch('/api/messages/{id}',edit_message); app.router.add_delete('/api/messages/{id}',delete_message); app.router.add_get('/api/search',search);app.router.add_get('/api/messages/{id}/replies',replies);app.router.add_get('/api/channels/{id}/pins',pins);app.router.add_post('/api/messages/{id}/pin',pin);app.router.add_delete('/api/messages/{id}/pin',unpin);app.router.add_get('/api/channels/{id}/read',read_state);app.router.add_post('/api/channels/{id}/read',read_state)
app.router.add_delete('/api/messages/{id}/reactions/{emoji}',delete_react)

async def dms(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    b=await body(req)
    if not isinstance(b,dict) or isinstance(b.get('user_id'),bool) or not isinstance(b.get('user_id'),int):return err(400,'invalid user_id')
    other=b['user_id']
    if not core.user_for_token(next(iter([]),'')):
        c0=core.connect(); exists=c0.execute('SELECT id FROM users WHERE id=?',(other,)).fetchone();c0.close()
    if not exists:return err(404,'not found')
    a,bid=sorted((u['id'],other))
    with core.tx() as c:
        x=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,bid)).fetchone()
        if x:cid=x['channel_id']
        else:
            c.execute('INSERT INTO channels(is_private,is_dm) VALUES(1,1)');cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO dms VALUES(?,?,?)',(a,bid,cid));c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,a,cid,bid))
    return out({'channel_id':cid})
async def get_user(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:uid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();c.close()
    return out({'user':core.user_to_json(r)}) if r else err(404,'not found')
async def patch_me(req):
    u=auth(req); 
    if not u:return err(401,'unauthorized')
    b=await body(req)
    if not isinstance(b,dict):return err(400,'invalid body')
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    limits={'display_name':80,'status_text':140,'status_emoji':32,'avatar_url':512,'timezone':64}
    if any(k not in allowed or not isinstance(v,str) or len(v)==0 or len(v)>limits[k] for k,v in b.items()):return err(400,'invalid profile')
    if 'timezone' in b and b['timezone'] not in ('UTC','America/New_York','Europe/London','Asia/Tokyo'):return err(400,'invalid timezone')
    with core.tx() as c:
        for k,v in b.items():c.execute(f'UPDATE users SET {k}=? WHERE id=?',(v,u['id']))
        r=c.execute('SELECT * FROM users WHERE id=?',(u['id'],)).fetchone()
        core.emit_global(c,{'type':'user.updated','user':core.user_to_json(r)})
    return out({'user':core.user_to_json(r)})

async def upload_file(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:
        form=await req.multipart(); part=await form.next()
        if not part or part.name!='file':return err(400,'missing file')
        data=await part.read()
        if len(data)>10*1024*1024:return err(413,'file too large')
        filename=part.filename or 'file'; ctype=part.headers.get('Content-Type','application/octet-stream')
    except:return err(400,'invalid upload')
    with core.tx() as c:
        x=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(u['id'],filename,ctype,len(data),core.now_iso()));fid=x.lastrowid
    import os
    os.makedirs('/app/data/files',exist_ok=True)
    open(f'/app/data/files/{fid}','wb').write(data)
    return out({'file':{'id':fid,'uploader_id':u['id'],'filename':filename,'content_type':ctype,'size':len(data),'created_at':core.now_iso()}},201)
async def file_info(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:fid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    if not r:return err(404,'not found')
    return out({'file':dict(r)})
async def download_file(req):
    u=auth(req)
    if not u:return err(401,'unauthorized')
    try:fid=int(req.match_info['id'])
    except:return err(404,'not found')
    c=core.connect();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close()
    if not r:return err(404,'not found')
    try:data=open(f'/app/data/files/{fid}','rb').read()
    except:return err(404,'not found')
    return web.Response(body=data,content_type=r['content_type'],headers={'Content-Disposition':f'attachment; filename="{r["filename"]}"'})

async def groups(req):
    u=auth(req); w=core.get_workspace(req.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    c=core.connect();rs=c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],)).fetchall();outg=[]
    for g in rs:
        ids=[x[0] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall()]
        outg.append({'id':g['id'],'handle':g['handle'],'name':g['name'],'member_user_ids':ids})
    c.close(); return out({'groups':outg})
async def create_group(req):
    u=auth(req);w=core.get_workspace(req.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    if not core.member_role(w['id'],u['id']):return err(403,'forbidden')
    b=await body(req)
    if not isinstance(b,dict) or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9_-]+',b['handle']) or not isinstance(b.get('name'),str):return err(400,'invalid group')
    ids=b.get('user_ids',[])
    if not isinstance(ids,list) or any(not isinstance(x,int) for x in ids):return err(400,'invalid members')
    c=core.connect(); valid={x[0] for x in c.execute('SELECT user_id FROM members WHERE workspace_id=?',(w['id'],)).fetchall()};dup=c.execute('SELECT 1 FROM groups WHERE handle=?',(b['handle'],)).fetchone();c.close()
    if dup:return err(409,'duplicate handle')
    if any(x not in valid for x in ids):return err(400,'non-member')
    with core.tx() as c:
        x=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],b['handle'],b['name'],u['id']));gid=x.lastrowid
        for uid in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,uid))
    return out({'group':{'id':gid,'handle':b['handle'],'name':b['name'],'member_user_ids':ids}},201)

async def patch_group(req):
    u=auth(req);w=core.get_workspace(req.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    c=core.connect();g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone();c.close()
    if not g:return err(404,'not found')
    if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
    b=await body(req)
    if not isinstance(b,dict):return err(400,'invalid body')
    cpre=core.connect(); existing=[x[0] for x in cpre.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall()];cpre.close()
    ids=b.get('user_ids',existing)
    if not isinstance(ids,list) or any(not isinstance(x,int) for x in ids):return err(400,'invalid members')
    with core.tx() as c:
        if 'name' in b:c.execute('UPDATE groups SET name=? WHERE id=?',(b['name'],g['id']))
        c.execute('DELETE FROM group_members WHERE group_id=?',(g['id'],))
        for uid in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(g['id'],uid))
    return out({'group':{'id':g['id'],'handle':g['handle'],'name':b.get('name',g['name']),'member_user_ids':ids}})
async def delete_group(req):
    u=auth(req);w=core.get_workspace(req.match_info['slug'])
    if not u:return err(401,'unauthorized')
    if not w:return err(404,'not found')
    c=core.connect();g=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],req.match_info['handle'])).fetchone();c.close()
    if not g:return out({'deleted':True})
    if g['creator_id']!=u['id'] and w['owner_id']!=u['id']:return err(403,'forbidden')
    with core.tx() as c:c.execute('DELETE FROM groups WHERE id=?',(g['id'],))
    return out({'deleted':True})

async def missing_auth_route(req):
    if not auth(req):return err(401,'unauthorized')
    return err(404,'not found')
app.router.add_get('/api/users/{id}',get_user); app.router.add_get('/api/workspaces/{slug}/groups',groups); app.router.add_post('/api/workspaces/{slug}/groups',create_group); app.router.add_patch('/api/workspaces/{slug}/groups/{handle}',patch_group); app.router.add_delete('/api/workspaces/{slug}/groups/{handle}',delete_group); app.router.add_patch('/api/users/me',patch_me)
app.router.add_get('/api/workspaces/{slug}/groups',missing_auth_route); app.router.add_post('/api/workspaces/{slug}/groups',missing_auth_route); app.router.add_patch('/api/workspaces/{slug}/groups/{handle}',missing_auth_route); app.router.add_delete('/api/workspaces/{slug}/groups/{handle}',missing_auth_route)
app.router.add_post('/api/dms',dms)
app.router.add_post('/api/files',upload_file)
app.router.add_get('/api/files/{id}',file_info)
app.router.add_get('/api/files/{id}/download',download_file)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--node',type=int,default=0);a=p.parse_args();NODE=a.node;web.run_app(app,host='127.0.0.1',port=8000+NODE)
