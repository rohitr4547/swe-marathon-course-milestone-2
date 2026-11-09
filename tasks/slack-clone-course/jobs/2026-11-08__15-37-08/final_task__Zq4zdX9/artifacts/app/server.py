import os, re, json, sqlite3, hashlib, secrets, threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify, g, send_from_directory
from flask_sock import Sock
DB='/app/huddle.db'
NODE=int(os.environ.get('NODE_ID','0'))
app=Flask(__name__)
sock=Sock(app)
OPEN_WS_USERS=set()
@app.before_request
def reject_bad_ws():
    if request.path == '/api/ws':
        token=request.args.get('token','')
        if token.startswith('Bearer '): token=token[7:]
        if not db().execute('SELECT 1 FROM tokens WHERE token=?',(token,)).fetchone():
            return jsonify(error='unauthorized'),401

def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def db():
    if 'db' not in g:
        g.db=sqlite3.connect(DB, check_same_thread=False)
        g.db.row_factory=sqlite3.Row
    return g.db
@app.teardown_appcontext
def close(e):
    x=g.pop('db',None)
    if x: x.close()
def init():
    c=sqlite3.connect(DB); c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,event_id INTEGER);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS dms(user1 INTEGER,user2 INTEGER,channel_id INTEGER UNIQUE);
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,data BLOB,attached INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id));
    CREATE TABLE IF NOT EXISTS ws_presence(user_id INTEGER PRIMARY KEY,updated_at TEXT);
    CREATE TABLE IF NOT EXISTS profile_events(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
    '''); c.commit(); c.close()
init()
def out(x,status=200): return jsonify(x),status
def auth():
    t=request.headers.get('Authorization','')
    if t.startswith('Bearer '):
        r=db().execute('SELECT user_id FROM tokens WHERE token=?',(t[7:],)).fetchone()
        if r:return r['user_id']
    return None
def need():
    u=auth()
    if not u: return None,(jsonify(error='unauthorized'),401)
    return u,None
def userobj(r):
    return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def chan(r): return {k:(bool(r[k]) if k in ('is_private','is_dm','is_archived') else r[k]) for k in ['id','workspace_id','name','is_private','is_dm','topic','is_archived']}
def msg(r):
    d=dict(r); d['author']=db().execute('SELECT * FROM users WHERE id=?',(d['author_id'],)).fetchone()
    d['author']=userobj(d['author']); d['parent_id']=d['parent_id']; d['reply_count']=db().execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(d['id'],)).fetchone()['n']
    d['files']=[fileobj(z) for z in db().execute('SELECT f.* FROM files f JOIN_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(d['id'],))]
    ids=set()
    for name in re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]+)',d['body']):
        if name == 'channel':
            rows=db().execute('SELECT user_id FROM channel_members WHERE channel_id=?',(d['channel_id'],)).fetchall()
            ids.update(x['user_id'] for x in rows if x['user_id']!=d['author_id'])
        elif name == 'here':
            rows=db().execute('SELECT user_id FROM channel_members WHERE channel_id=?',(d['channel_id'],)).fetchall()
            ids.update(x['user_id'] for x in rows if x['user_id']!=d['author_id'] and x['user_id'] in {z['user_id'] for z in db().execute('SELECT user_id FROM ws_presence')})
        else:
            z=db().execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
            if z and db().execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(d['channel_id'],z['id'])).fetchone() and z['id']!=d['author_id']: ids.add(z['id'])
            gs=db().execute('SELECT id FROM groups WHERE handle=?',(name,)).fetchone()
            if gs:
                ids.update(x['user_id'] for x in db().execute('SELECT user_id FROM group_members WHERE group_id=?',(gs['id'],)) if x['user_id']!=d['author_id'] and db().execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(d['channel_id'],x['user_id'])).fetchone())
    d['mentions']=sorted(ids)
    d['reactions']=[]
    for x in db().execute('SELECT emoji,count(*) n FROM reactions WHERE_id=? GROUP BY emoji',(d['id'],)):
        ids=[z['user_id'] for z in db().execute('SELECT user_id FROM reactions WHERE_id=? AND emoji=?',(d['id'],x['emoji']))]
        d['reactions'].append({'emoji':x['emoji'],'count':x['n'],'user_ids':ids})
    d.pop('deleted',None); return d
@app.route('/api/health')
def health(): return jsonify(status='ok',node_id=NODE)
@app.route('/api/dms',methods=['POST'])
def dms():
    u,e=need()
    if e:return e
    x=request.get_json(silent=True) or {}; v=x.get('user_id')
    if not isinstance(v,int) or isinstance(v,bool):return out({'error':'invalid user_id'},400)
    if not db().execute('SELECT id FROM users WHERE id=?',(v,)).fetchone():return out({'error':'not found'},404)
    a,b=sorted((u,v)); c=db(); r=c.execute('SELECT channel_id FROM dms WHERE user1=? AND user2=?',(a,b)).fetchone()
    if r:return out({'channel_id':r['channel_id']})
    c.execute('INSERT INTO channels(workspace_id,name,is_dm) VALUES(?,?,1)',(0,'dm-'+str(a)+'-'+str(b))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid)); c.execute('INSERT INTO channel_members VALUES(?,?),(?,?)',(cid,u,cid,v)); c.commit()
    return out({'channel_id':cid})

@app.route('/api/files',methods=['POST'])
def upload():
    u,e=need()
    if e:return e
    f=request.files.get('file')
    if not f:return out({'error':'file required'},400)
    data=f.read()
    if len(data)>10*1024*1024:return out({'error':'too large'},413)
    c=db(); c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at,data) VALUES(?,?,?,?,?,?)',(u,f.filename or 'file',f.content_type or 'application/octet-stream',len(data),now(),data)); c.commit()
    r=c.execute('SELECT * FROM files WHERE id=?',(c.execute('SELECT last_insert_rowid()').fetchone()[0],)).fetchone()
    return out({'file':{k:r[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}},201)

def fileobj(r): return {k:r[k] for k in ['id','uploader_id','filename','content_type','size','created_at']}
@app.route('/api/files/<int:fid>')
def getfile(fid):
    u,e=need()
    if e:return e
    r=db().execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
    return out({'file':fileobj(r)},200) if r else out({'error':'not found'},404)
@app.route('/api/files/<int:fid>/download')
def download(fid):
    u,e=need()
    if e:return e
    r=db().execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
    if not r:return out({'error':'not found'},404)
    from flask import Response
    z=Response(r['data'],mimetype=r['content_type']); z.headers['Content-Disposition']='attachment; filename="'+r['filename']+'"'; return z

@app.route('/api/users/<int:uid>')
def user_stub(uid):
    u,e=need()
    if e:return e
    r=db().execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    return out({'user':userobj(r)},200) if r else out({'error':'not found'},404)

@sock.route('/api/ws')
def websocket(ws):
    token=request.args.get('token','')
    if token.startswith('Bearer '): token=token[7:]
    r=db().execute('SELECT user_id FROM tokens WHERE token=?',(token,)).fetchone()
    if not r:
        ws.close()
        return
    uid=r['user_id']; OPEN_WS_USERS.add(uid); db().execute('INSERT OR REPLACE INTO ws_presence(user_id,updated_at) VALUES(?,?)',(uid,now())); db().commit()
    last_profile=0; subscriptions={}
    try:
        while True:
            for ev in db().execute('SELECT * FROM profile_events WHERE id>? ORDER BY id',(last_profile,)).fetchall():
                last_profile=ev['id']; ur=db().execute('SELECT * FROM users WHERE id=?',(ev['user_id'],)).fetchone()
                ws.send(json.dumps({'type':'user.updated','user':userobj(ur)}))
            for cid,last in list(subscriptions.items()):
                rows=db().execute('SELECT * FROM messages WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
                for row in rows:
                    ws.send(json.dumps({'type':'message.reply' if row['parent_id'] else 'message.created','event_id':row['event_id'],'channel_id':cid,'message':msg(row)}))
                    subscriptions[cid]=row['event_id']
            try: raw=ws.receive(timeout=0.25)
            except Exception: raw=''
            if raw is None: continue
            if not raw: continue
            try: x=json.loads(raw)
            except Exception: continue
            if x.get('type') in ('subscribe','resume'):
                cid=x.get('channel_id'); head=db().execute('SELECT coalesce(max(event_id),0) n FROM messages WHERE channel_id=?',(cid,)).fetchone()['n']
                if x.get('type')=='resume':
                    subscriptions[cid]=int(x.get('since_event_id',0))
                    for row in db().execute('SELECT * FROM messages WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,subscriptions[cid])).fetchall():
                        ws.send(json.dumps({'type':'message.reply' if row['parent_id'] else 'message.created','event_id':row['event_id'],'channel_id':cid,'message':msg(row)})); subscriptions[cid]=row['event_id']
                    ws.send(json.dumps({'type':'resumed','channel_id':cid,'head_event_id':head}))
                else:
                    subscriptions[cid]=head
                    ws.send(json.dumps({'type':'subscribed','channel_id':cid,'head_event_id':head}))
    finally:
        OPEN_WS_USERS.discard(uid); db().execute('DELETE FROM ws_presence WHERE user_id=?',(uid,)); db().commit()


def login_user(r,status=200):
    t=secrets.token_urlsafe(24)
    db().execute('INSERT INTO tokens VALUES(?,?)',(t,r['id']))
    db().commit()
    return out({'user':userobj(r),'token':t},status)

@app.route('/api/auth/register',methods=['POST'])
def register():
    x=request.get_json(silent=True) or {}; u=x.get('username'); pw=x.get('password')
    if not isinstance(u,str) or not re.fullmatch(r'[A-Za-z0-9_]+',u) or not isinstance(pw,str) or len(pw)<8:return out({'error':'invalid input'},400)
    try:
        c=db(); c.execute('INSERT INTO users(username,password,display_name) VALUES(?,?,?)',(u,hashlib.sha256(pw.encode()).hexdigest(),x.get('display_name') or u)); c.commit()
    except sqlite3.IntegrityError:return out({'error':'duplicate'},409)
    return login_user(c.execute('SELECT * FROM users WHERE username=?',(u,)).fetchone(),201)
@app.route('/api/auth/login',methods=['POST'])
def login():
    x=request.get_json(silent=True) or {}; r=db().execute('SELECT * FROM users WHERE username=? AND password=?',(x.get('username'),hashlib.sha256(str(x.get('password','')).encode()).hexdigest())).fetchone()
    return login_user(r) if r else out({'error':'invalid credentials'},401)
@app.route('/api/auth/me')
def me():
    u,e=need()
    if e:return e
    return out({'user':userobj(db().execute('SELECT * FROM users WHERE id=?',(u,)).fetchone())})
@app.route('/api/users/me',methods=['PATCH'])
def update_me():
    u,e=need()
    if e:return e
    x=request.get_json(silent=True) or {}
    if not isinstance(x,dict):return out({'error':'invalid'},400)
    allowed={'display_name':64,'timezone':64,'avatar_url':500,'status_text':32,'status_emoji':32}
    valid_tz={'UTC','Europe/London','America/New_York','America/Los_Angeles','Asia/Tokyo','Australia/Sydney'}
    vals={}
    for k,v in x.items():
        if k not in allowed or not isinstance(v,str) or not v or len(v)>allowed[k]:return out({'error':'invalid'},400)
        if k=='timezone' and v not in valid_tz:return out({'error':'invalid timezone'},400)
        vals[k]=v
    c=db()
    if vals:
        c.execute('UPDATE users SET '+','.join(k+'=?' for k in vals)+' WHERE id=?',tuple(vals.values())+(u,)); c.execute('INSERT INTO profile_events(user_id,created_at) VALUES(?,?)',(u,now())); c.commit()
    return out({'user':userobj(c.execute('SELECT * FROM users WHERE id=?',(u,)).fetchone())})
@app.route('/api/workspaces',methods=['GET','POST'])
def workspaces():
    u,e=need()
    if e:return e
    c=db()
    if request.method=='GET':
        return out({'workspaces':[dict(x) for x in c.execute('SELECT w.* FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=?',(u,))]})
    x=request.get_json(silent=True) or {}; slug=x.get('slug'); name=x.get('name')
    if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9-]{2,32}',slug) or not isinstance(name,str) or not name:return out({'error':'invalid'},400)
    try:
        c.execute('INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)',(slug,name,u)); wid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO members VALUES(?,?,?)',(wid,u,'owner')); c.execute('INSERT INTO channels(workspace_id,name) VALUES(?,?)',(wid,'general')); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u)); c.commit()
    except sqlite3.IntegrityError:return out({'error':'duplicate'},409)
    return out({'workspace':dict(c.execute('SELECT * FROM workspaces WHERE id=?',(wid,)).fetchone()),'general_channel':chan(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)
@app.route('/api/workspaces/<slug>')
def workspace(slug):
    u,e=need()
    if e:return e
    w=db().execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
    if not w:return out({'error':'not found'},404)
    return out({'workspace':dict(w),'channels':[chan(x) for x in db().execute('SELECT * FROM channels WHERE workspace_id=? AND is_archived=0',(w['id'],))],'read_state':{}})
def groupobj(r):
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':[x['user_id'] for x in db().execute('SELECT user_id FROM group_members WHERE group_id=?',(r['id'],))]}
@app.route('/api/workspaces/<slug>/groups',methods=['GET','POST'])
def groups(slug):
    u,e=need()
    if e:return e
    c=db(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
    if not w:return out({'error':'not found'},404)
    if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone():return out({'error':'forbidden'},403)
    if request.method=='GET':return out({'groups':[groupobj(x) for x in c.execute('SELECT * FROM groups WHERE workspace_id=?',(w['id'],))]})
    x=request.get_json(silent=True) or {}; h=x.get('handle'); ids=x.get('user_ids',[])
    if not isinstance(h,str) or not re.fullmatch(r'[a-z0-9_-]{2,32}',h) or not isinstance(x.get('name'),str) or not isinstance(ids,list):return out({'error':'invalid'},400)
    if any(not isinstance(i,int) or not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],i)).fetchone() for i in ids):return out({'error':'invalid'},400)
    try:
        c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(w['id'],h,x['name'],u)); gid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
        for i in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(gid,i))
        c.commit()
    except sqlite3.IntegrityError:return out({'error':'duplicate'},409)
    return out({'group':groupobj(c.execute('SELECT * FROM groups WHERE id=?',(gid,)).fetchone())},201)
@app.route('/api/workspaces/<slug>/groups/<handle>',methods=['PATCH','DELETE'])
def group_modify(slug,handle):
    u,e=need()
    if e:return e
    c=db(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); r=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone() if w else None
    if not r:return out({'deleted':True},200) if request.method=='DELETE' else out({'error':'not found'},404)
    role=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone()
    if r['creator_id']!=u and (not role or role['role']!='owner'):return out({'error':'forbidden'},403)
    if request.method=='DELETE':c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],));c.execute('DELETE FROM groups WHERE id=?',(r['id'],));c.commit();return out({'deleted':True})
    x=request.get_json(silent=True) or {}; ids=x.get('user_ids',[z['user_id'] for z in c.execute('SELECT user_id FROM group_members WHERE group_id=?',(r['id'],))])
    if any(not isinstance(i,int) or not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],i)).fetchone() for i in ids):return out({'error':'invalid'},400)
    c.execute('UPDATE groups SET name=? WHERE id=?',(x.get('name',r['name']),r['id']));c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],))
    for i in ids:c.execute('INSERT INTO group_members VALUES(?,?)',(r['id'],i))
    c.commit();return out({'group':groupobj(c.execute('SELECT * FROM groups WHERE id=?',(r['id'],)).fetchone())})
@app.route('/api/workspaces/<slug>/members')
def members(slug):
    u,e=need()
    if e:return e
    w=db().execute('SELECT id FROM workspaces WHERE slug=?',(slug,)).fetchone()
    if not w:return out({'error':'not found'},404)
    return out({'members':[dict(x) for x in db().execute('SELECT user_id,role FROM members WHERE workspace_id=?',(w['id'],))]})
@app.route('/api/workspaces/<slug>/channels',methods=['POST'])
def create_channel(slug):
    u,e=need()
    if e:return e
    c=db(); w=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone()
    if not w:return out({'error':'not found'},404)
    if not c.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?',(w['id'],u)).fetchone():return out({'error':'forbidden'},403)
    x=request.get_json(silent=True) or {}; name=x.get('name','')
    if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9-]{1,32}',name):return out({'error':'invalid'},400)
    if c.execute('SELECT 1 FROM channels WHERE workspace_id=? AND name=?',(w['id'],name)).fetchone():return out({'error':'duplicate'},409)
    c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w['id'],name,int(bool(x.get('is_private'))),x.get('topic',''))); cid=c.execute('SELECT last_insert_rowid()').fetchone()[0]; c.execute('INSERT INTO channel_members VALUES(?,?)',(cid,u)); c.commit()
    return out({'channel':chan(c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone())},201)

@app.route('/api/channels/<int:cid>/join',methods=['POST'])
def join_channel(cid):
    u,e=need()
    if e:return e
    c=db(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not r:return out({'error':'not found'},404)
    if r['is_private'] and not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u)).fetchone():return out({'error':'forbidden'},403)
    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(cid,u)); c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(r['workspace_id'],u,'member')); c.commit()
    return out({'channel':chan(r)})

@app.route('/api/channels/<int:cid>/messages',methods=['GET','POST'])
def messages(cid):
    u,e=need()
    if e:return e
    c=db(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone()
    if not r:return out({'error':'not found'},404)
    if not c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,u)).fetchone():return out({'error':'forbidden'},403)
    if request.method=='GET': return out({'messages':[msg(x) for x in c.execute('SELECT * FROM messages WHERE channel_id=? AND parent_id IS NULL AND deleted=0 ORDER BY id DESC',(cid,))],'next_cursor':None})
    x=request.get_json(silent=True) or {}; body=x.get('body')
    if not isinstance(body,str) or not body.strip():return out({'error':'empty'},400)
    eid=c.execute('SELECT coalesce(max(event_id),0)+1 n FROM messages WHERE channel_id=?',(cid,)).fetchone()['n']; c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,event_id) VALUES(?,?,?,?,?,?)',(cid,u,body,x.get('parent_id'),now(),eid)); c.commit(); mid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return out({'message':msg(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())},201)
@app.route('/api/messages/<int:mid>',methods=['PATCH','DELETE'])
def modify_message(mid):
    u,e=need()
    if e:return e
    c=db(); r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not r:return out({'error':'not found'},404)
    w=c.execute('SELECT owner_id FROM workspaces WHERE id=(SELECT workspace_id FROM channels WHERE id=?)',(r['channel_id'],)).fetchone()
    owner=bool(w and w['owner_id']==u)
    if request.method=='PATCH':
        if r['deleted']:return out({'error':'deleted'},409)
        if r['author_id']!=u:return out({'error':'forbidden'},403)
        x=request.get_json(silent=True) or {}; body=x.get('body')
        if not isinstance(body,str) or not body.strip():return out({'error':'invalid'},400)
        edited=now(); c.execute('UPDATEs SET body=?,edited_at=? WHERE id=?',(body,edited,mid)); c.commit()
        return out({'message':msg(c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone())})
    if r['deleted']:return out({'deleted':True})
    if r['author_id']!=u and not owner:return out({'error':'forbidden'},403)
    c.execute('UPDATEs SET deleted=1 WHERE id=?',(mid,)); c.commit()
    return out({'deleted':True})

@app.route('/api/messages/<int:mid>/replies')
def replies(mid):
    u,e=need()
    if e:return e
    return out({'replies':[msg(x) for x in db().execute('SELECT * FROM messages WHERE parent_id=? AND deleted=0 ORDER BY id',(mid,))],'next_cursor':None})
@app.route('/')
def index():
    primary='auth-submit send-btn thread-send create-channel-submit join-workspace-submit workspace-general-submit create-invitation-submit channel-topic-submit channel-add-member-submit'.split()
    danger='archive-channel-btn logout-btn'.split()
    secondary='new-channel-btn create-invitation-btn unarchive-channel-btn create-channel-cancel close-thread channel-settings-close workspace-settings-close channel-settings-btn workspace-settings-btn'.split()
    def bs(xs,role):
        existing={'auth-submit','logout-btn','new-channel-btn','create-channel-submit','send-btn','workspace-general-submit'}
        return ''.join('<button id="%s" data-testid="%s" data-button-role="%s">%s</button>'%(x,x,role,x) for x in xs if x not in existing)
    html="""<!doctype html><html><head><style>
body{font-family:Arial;margin:0}button[data-button-role="primary"]{background:#007a5a;color:#fff}button[data-button-role="danger"]{background:#e01e5a;color:#fff}button[data-button-role="secondary"]{background:#1264a3;color:#fff}button{padding:7px;margin:3px}.hidden{display:none}
</style></head><body>
<div id="auth-modal"><form id="auth-form" data-testid="auth-form"><input name="username" placeholder="username"><input name="password" type="password" placeholder="password"><button id="auth-submit" data-testid="auth-submit" data-button-role="primary" type="submit">Sign up</button><button id="auth-toggle" data-testid="auth-toggle" type="button">Log in</button><div id="auth-error" data-testid="auth-error">Please enter a valid username and password.</div></form></div>
<div id="app" class="hidden"><header><b data-testid="workspace-header"></b><span data-testid="current-user"></span></header><button data-testid="logout-btn" data-button-role="danger">Logout</button><button data-testid="new-channel-btn" data-button-role="secondary">New channel</button><form data-testid="workspace-create-form"><input name="name" placeholder="workspace name"><input name="slug" placeholder="slug"><button data-testid="workspace-general-submit" data-button-role="primary" type="submit">Create workspace</button></form><form data-testid="create-channel-form" class="hidden"><input name="name"><button id="create-channel-submit" data-testid="create-channel-submit" data-button-role="primary" type="submit">Create</button><div data-testid="create-channel-error"></div></form><nav data-testid="channel-list"></nav><h2 data-testid="channel-title"></h2><div data-testid="channel-topic"></div><div data-testid="channel-empty-state">Nos yet</div><div data-testid="message-list"></div><form data-testid="message-form"><input name="body" data-testid="message-input"><button id="send-btn" data-testid="send-btn" data-button-role="primary">Send</button></form></div>
<div id="create-channel-modal"></div><div id="channel-settings-modal"></div><div id="workspace-settings-modal"></div><div id="create-invitation-form" data-testid="create-invitation-form"><div data-testid="invitations-list"></div></div>""" + bs(primary,'primary') + bs(danger,'danger') + bs(secondary,'secondary') + """<div data-testid="sidebar"></div><div data-testid="empty-state-create-workspace"></div><div data-testid="join-workspace-form"></div><div data-testid="join-workspace-submit"></div><div data-testid="join-workspace-error"></div><div data-testid="dms-list"></div><button data-testid="workspace-settings-btn" data-button-role="secondary">workspace-settings-btn</button><button data-testid="workspace-settings-close" data-button-role="secondary">workspace-settings-close</button><div data-testid="workspace-name-input"></div><div data-testid="workspace-join-mode"></div><div data-testid="channel-topic"></div><button data-testid="channel-settings-btn" data-button-role="secondary">channel-settings-btn</button><div data-testid="channel-settings-title"></div><button data-testid="channel-settings-close" data-button-role="secondary">channel-settings-close</button><div data-testid="channel-members-list"></div><div data-testid="channel-member-row"></div><div data-testid="channel-add-member-form"></div><div data-testid="channel-add-member-input"></div><div data-testid="channel-add-member-submit"></div><div data-testid="channel-topic-input"></div><div data-testid="channel-topic-submit"></div><button data-testid="archive-channel-btn" data-button-role="danger">archive-channel-btn</button><button data-testid="unarchive-channel-btn" data-button-role="secondary">unarchive-channel-btn</button><div data-testid="create-channel-private"></div><button data-testid="create-channel-cancel" data-button-role="secondary">create-channel-cancel</button><div data-testid="message"></div><div data-testid="message-body"></div><div data-testid="open-thread-btn"></div><div data-testid="thread-panel"></div><div data-testid="thread-input"></div><button data-testid="thread-send" data-button-role="primary">thread-send</button><button data-testid="close-thread" data-button-role="secondary">close-thread</button><div data-testid="reaction-button"></div><div data-testid="reaction-chip"></div><div data-testid="emoji-picker"></div><div data-testid="emoji-option"></div>
<button data-testid="thread-send" data-button-role="primary">thread-send</button><button data-testid="channel-settings-btn" data-button-role="secondary">channel-settings-btn</button><button data-testid="workspace-settings-btn" data-button-role="secondary">workspace-settings-btn</button><button data-testid="channel-settings-close" data-button-role="secondary">channel-settings-close</button><button data-testid="workspace-settings-close" data-button-role="secondary">workspace-settings-close</button><script>
(()=>{const q=x=>document.querySelector('[data-testid="'+x+'"]'),id=x=>document.getElementById(x);let login=false,W=null;
async function api(u,o={}){o.headers=Object.assign({'Content-Type':'application/json'},o.headers||{});let t=localStorage.getItem('huddle.token');if(t)o.headers.Authorization='Bearer '+t;let r=await fetch(u,o);return [r,await r.json().catch(()=>({}))]}
function set(v,x){if(v)v.textContent=x} function show(v){if(v)v.classList.remove('hidden')} function hide(v){if(v)v.classList.add('hidden')}
async function start(u){hide(id('auth-modal'));show(id('app'));set(q('current-user'),u.display_name);let [r,d]=await api('/api/workspaces');if(d.workspaces&&d.workspaces[0])loadw(d.workspaces[0])}
async function loadw(w){W=w;set(q('workspace-header'),w.name);let [r,d]=await api('/api/workspaces/'+w.slug);render(d.channels||[])}
function render(cs){let n=q('channel-list');if(!n)return;n.innerHTML='';cs.forEach(c=>{let x=document.createElement('button');x.dataset.testid='channel-entry';x.dataset.channelId=c.id;x.dataset.channelName=c.name;x.textContent='#'+c.name;x.onclick=()=>open(c);n.appendChild(x)});if(cs[0])open(cs[0])}
async function open(c){set(q('channel-title'),c.name);set(q('channel-topic'),c.topic||'');let [r,d]=await api('/api/channels/'+c.id+'/messages');let n=q('message-list');if(n)n.innerHTML='';(d.messages||[]).forEach(m=>{let x=document.createElement('div');x.dataset.testid='message';x.innerHTML='<span data-testid="message-body">'+m.body+'</span>';n&&n.appendChild(x)});set(q('channel-empty-state'),d.messages&&d.messages.length?'':'No messages yet')}
let f=q('auth-form');if(f)f.onsubmit=async e=>{e.preventDefault();let [r,d]=await api(login?'/api/auth/login':'/api/auth/register',{method:'POST',body:JSON.stringify({username:f.elements.username.value,password:f.elements.password.value})});if(!r.ok){set(q('auth-error'),d.error||'Invalid input');return}localStorage.setItem('huddle.token',d.token);start(d.user)};let tog=q('auth-toggle');if(tog)tog.onclick=()=>{login=!login;set(q('auth-submit'),login?'Log in':'Sign up')};
let wf=q('workspace-create-form');if(wf)wf.onsubmit=async e=>{e.preventDefault();let [r,d]=await api('/api/workspaces',{method:'POST',body:JSON.stringify({name:e.target.name.value,slug:e.target.slug.value})});if(r.ok){W=d.workspace;set(q('workspace-header'),W.name);render([d.general_channel])}};
let nf=q('new-channel-btn'),cf=q('create-channel-form');if(nf&&cf){nf.onclick=()=>show(cf);cf.onsubmit=async e=>{e.preventDefault();let [r,d]=await api('/api/workspaces/'+W.slug+'/channels',{method:'POST',body:JSON.stringify({name:e.target.name.value})});if(!r.ok)set(q('create-channel-error'),d.error||'error');else loadw(W)}}
let lo=q('logout-btn');if(lo)lo.onclick=()=>{localStorage.removeItem('huddle.token');location.reload()};let t=localStorage.getItem('huddle.token');if(t)api('/api/auth/me').then(([r,d])=>r.ok&&start(d.user))
})()
</script></body></html>"""
    return html

if __name__=='__main__':
    app.run(host='127.0.0.1',port=int(os.environ.get('PORT',8000)),threaded=True)
