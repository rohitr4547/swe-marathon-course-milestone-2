import sqlite3, os, hashlib, secrets, base64, json, re
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True)
    c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA busy_timeout=10000')
    return c
class tx:
    def __enter__(self):
        self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '',created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER,created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open',created_at TEXT);
    CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0,UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0,mentions TEXT DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,mention_count INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS message_files(message_id INTEGER,file_id INTEGER,PRIMARY KEY(message_id,file_id));
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS groups_tbl(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,user_ids TEXT);
    CREATE TABLE IF NOT EXISTS invitations(code TEXT PRIMARY KEY,workspace_id INTEGER,invited_user_id INTEGER,expires_at TEXT,max_uses INTEGER,uses INTEGER DEFAULT 0,created_at TEXT);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    ''')
    mcols=[r['name'] for r in c.execute('PRAGMA table_info(messages)').fetchall()]
    if 'mentions' not in mcols: c.execute("ALTER TABLE messages ADD COLUMN mentions TEXT DEFAULT '[]'")
    cols=[r['name'] for r in c.execute('PRAGMA table_info(invitations)').fetchall()]
    if 'created_at' not in cols:
        c.execute('ALTER TABLE invitations ADD COLUMN created_at TEXT')
        c.execute("UPDATE invitations SET created_at=? WHERE created_at IS NULL",(now_iso(),))
    c.close()
def hash_password(pw):
    s=secrets.token_hex(16); return s+':'+hashlib.sha256((s+pw).encode()).hexdigest()
def verify_password(pw,stored):
    try:
        s,h=stored.split(':',1); return secrets.compare_digest(h,hashlib.sha256((s+pw).encode()).hexdigest())
    except: return False
def issue_token(c,user_id):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?,?)',(t,user_id,now_iso())); return t
def user_for_token(token):
    if not token:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(token,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
if __name__=='__main__':
    import sys
    if '--init-db' in sys.argv: init_db()
def member_role(workspace_id,user_id):
    c=connect(); r=c.execute('SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?',(workspace_id,user_id)).fetchone(); c.close()
    return r['role'] if r else None
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(cid):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(cid,)).fetchone(); c.close(); return r
def add_channel_member(c,cid,uid):
    exists=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone()
    if not exists:
        c.execute('INSERT INTO channel_members(channel_id,user_id) VALUES(?,?)',(cid,uid))
        emit_event(c,cid,'member.joined',{'user_id':uid})
def remove_channel_member(c,cid,uid):
    exists=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid)).fetchone()
    if exists:
        c.execute('DELETE FROM channel_members WHERE channel_id=? AND user_id=?',(cid,uid))
        emit_event(c,cid,'member.left',{'user_id':uid})
def add_workspace_member(c,wid,uid,role='member'):
    c.execute('INSERT OR IGNORE INTO workspace_members(workspace_id,user_id,role) VALUES(?,?,?)',(wid,uid,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(wid,)).fetchone()
    if g: add_channel_member(c,g['id'],uid)
def can_read_channel(uid,ch):
    c=connect(); m=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(ch['id'],uid)).fetchone()
    if m:return True
    if ch['is_private']:return False
    return bool(ch['workspace_id'] and member_role(ch['workspace_id'],uid) in ('owner','admin','member'))
import time
def invitation_to_json(r):
    return {k:(r[k] if k in r.keys() else None) for k in ('code','workspace_id','invited_user_id','max_uses','uses','expires_at','created_at')}
def create_invitation(actor,slug,b):
    w=get_workspace(slug)
    if not w:return None,404
    role=member_role(w['id'],actor['id'])
    if role not in ('owner','admin'):return None,403
    if not isinstance(b,dict):return None,400
    mx=b.get('max_uses',1); ex=b.get('expires_in_seconds')
    if not isinstance(mx,int) or isinstance(mx,bool) or mx<=0:return None,400
    if ex is not None and (not isinstance(ex,int) or isinstance(ex,bool) or ex<=0):return None,400
    target=b.get('invited_user_id')
    if 'invited_username' in b:
        if not isinstance(b['invited_username'],str):return None,400
        c=connect(); q=c.execute('SELECT id FROM users WHERE username=?',(b['invited_username'],)).fetchone();c.close()
        if not q:return None,404
        target=q['id']
    if target is not None:
        if not isinstance(target,int) or isinstance(target,bool):return None,400
        c=connect();q=c.execute('SELECT id FROM users WHERE id=?',(target,)).fetchone();c.close()
        if not q:return None,404
        mx=1
    code=secrets.token_urlsafe(18); expires=None if ex is None else str(time.time()+ex)
    with tx() as c:
        c.execute('INSERT INTO invitations(code,workspace_id,invited_user_id,expires_at,max_uses,uses,created_at) VALUES(?,?,?,?,?,?,?)',(code,w['id'],target,expires,mx,0,now_iso()))
        r=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
    return r,201
def accept_invitation(user,code):
    with tx() as c:
        r=c.execute('SELECT * FROM invitations WHERE code=?',(code,)).fetchone()
        if not r or r['uses']>=r['max_uses'] or (r['expires_at'] and time.time()>=float(r['expires_at'])):return None,404
        if r['invited_user_id'] and r['invited_user_id']!=user['id']:return None,403
        w=c.execute('SELECT * FROM workspaces WHERE id=?',(r['workspace_id'],)).fetchone()
        if c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],user['id'])).fetchone():return w,200
        add_workspace_member(c,w['id'],user['id'],'member');c.execute('UPDATE invitations SET uses=uses+1 WHERE code=?',(code,))
    return w,200
def emit_event(c,cid,kind,payload):
    c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(cid,))
    n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(cid,)).fetchone()['last_event_id']
    frame={'type':kind,'event_id':n,'channel_id':cid};frame.update(payload)
    c.execute('INSERT INTO events VALUES(?,?,?)',(cid,n,json.dumps(frame)));return n
def message_to_json(r):
    c=connect();a=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone()
    rc=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']
    try: stored_mentions=json.loads(r['mentions'] or '[]')
    except: stored_mentions=[]
    rr=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji,user_id',(r['id'],)).fetchall()
    attached=c.execute('SELECT f.* FROM files f JOIN message_files mf ON mf.file_id=f.id WHERE mf.message_id=?',(r['id'],)).fetchall()
    c.close()
    grouped={}
    for z in rr: grouped.setdefault(z['emoji'],[]).append(z['user_id'])
    reactions=[{'emoji':e,'count':len(ids),'user_ids':ids} for e,ids in grouped.items()]
    return {'id':r['id'],'channel_id':r['channel_id'],'author_id':r['author_id'],'author':user_to_json(a),'body':r['body'],'parent_id':r['parent_id'],'reply_count':rc,'created_at':r['created_at'],'edited_at':r['edited_at'],'files':[file_to_json(z) for z in attached],'reactions':reactions,'mentions':stored_mentions,'deleted':bool(r['deleted'])}
def create_message(user,cid,body,parent=None,file_ids=None):
    ch=get_channel(cid)
    if not ch:return None,404
    if ch['is_archived']:return None,423
    if not body or not isinstance(body,str) or not body.strip():return None,400
    if not can_read_channel(user['id'],ch):return None,403
    mentions=[]
    for name in re.findall(r'(?<![\w@])@([A-Za-z0-9_]+)',body):
        c0=connect(); z=c0.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone(); c0.close()
        if z and z['id']!=user['id'] and member_role(ch['workspace_id'],z['id']): mentions.append(z['id'])
    cc=connect()
    for gh in re.findall(r'(?<![\\w@])@([a-z0-9-]{1,32})',body):
        gr=cc.execute('SELECT user_ids FROM groups_tbl WHERE workspace_id=? AND handle=?',(ch['workspace_id'],gh)).fetchone()
        if gr:
            try: mentions.extend(int(x) for x in json.loads(gr['user_ids'] or '[]') if int(x)!=user['id'] and cc.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(cid,int(x))).fetchone())
            except: pass
    if '@here' in body:
        mentions.extend(x['user_id'] for x in cc.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(cid,user['id'])).fetchall() if x['user_id'] in present_user_ids())
    if '@channel' in body:
        mentions.extend(x['user_id'] for x in cc.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(cid,user['id'])).fetchall())
    cc.close()
    mentions=list(dict.fromkeys(mentions))
    file_ids=file_ids or []
    if not isinstance(file_ids,list) or any(not isinstance(x,int) or isinstance(x,bool) for x in file_ids):return None,400
    if file_ids:
        c0=connect()
        for fid in file_ids:
            f=c0.execute('SELECT * FROM files WHERE id=? AND uploader_id=? AND message_id IS NULL',(fid,user['id'])).fetchone()
            if not f:c0.close();return None,400
        c0.close()
    if parent:
        c=connect();p=c.execute('SELECT * FROM messages WHERE id=? AND channel_id=? AND parent_id IS NULL AND deleted=0',(parent,cid)).fetchone();c.close()
        if not p:return None,400
    with tx() as c:
        mid=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',(cid,user['id'],body,parent,now_iso(),json.dumps(mentions))).lastrowid
        for fid in file_ids:
            c.execute('INSERT INTO message_files(message_id,file_id) VALUES(?,?)',(mid,fid))
            c.execute('UPDATE files SET message_id=? WHERE id=?',(mid,fid))
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();emit_event(c,cid,'message.reply' if parent else 'message.created',{'message':message_to_json(r)})
    return r,201
def pin_message(user,mid):
    c=connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
    if not m:return None,404
    ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
    if not can_read_channel(user['id'],ch):return None,403
    r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    if not r:
        with tx() as x:
            x.execute('INSERT INTO pins(message_id,pinned_by,pinned_at) VALUES(?,?,?)',(mid,user['id'],now_iso()))
        c=connect();r=c.execute('SELECT * FROM pins WHERE message_id=?',(mid,)).fetchone()
    c.close();return {'message_id':r['message_id'],'pinned_by':r['pinned_by'],'pinned_at':r['pinned_at']},200
def unpin_message(user,mid):
    c=connect();m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
    if not m:return None,404
    ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
    if not can_read_channel(user['id'],ch):return None,403
    c.execute('DELETE FROM pins WHERE message_id=?',(mid,));c.commit();c.close();return True,200

def change_reaction(user,mid,emoji,add):
    if not isinstance(emoji,str) or not emoji or len(emoji)>32:return None,400
    c=connect();m=c.execute('SELECT * FROM messages WHERE id=? AND deleted=0',(mid,)).fetchone()
    if not m:c.close();return None,404
    ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
    if not can_read_channel(user['id'],ch):c.close();return None,403
    if ch['is_archived']:c.close();return None,423
    existing=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji)).fetchone()
    changed=(add and not existing) or ((not add) and bool(existing))
    if changed:
        with tx() as x:
            if add:x.execute('INSERT INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)',(mid,user['id'],emoji))
            else:x.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(mid,user['id'],emoji))
            emit_event(x,m['channel_id'],'reaction.added' if add else 'reaction.removed',{'message_id':mid,'emoji':emoji,'user_id':user['id']})
    c=connect();rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(mid,)).fetchall();c.close()
    g={}; order=[]
    for r in rows:
        if r['emoji'] not in g: g[r['emoji']]=[];order.append(r['emoji'])
        g[r['emoji']].append(r['user_id'])
    return [{'emoji':e,'count':len(g[e]),'user_ids':g[e]} for e in order],200

def edit_message(user,mid,body):
    if not isinstance(body,str) or not body.strip():return None,400
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m:return None,404
        if m['deleted']:return None,409
        ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
        if ch['is_archived']:return None,423
        if m['author_id']!=user['id']:return None,403
        mentions=[]
        for name in re.findall(r'(?<![\\w@])@([A-Za-z0-9_]+)',body):
            z=c.execute('SELECT id FROM users WHERE username=?',(name,)).fetchone()
            if z and z['id']!=user['id'] and c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],z['id'])).fetchone(): mentions.append(z['id'])
        if '@channel' in body:
            mentions.extend(x['user_id'] for x in c.execute('SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?',(m['channel_id'],user['id'])).fetchall())
        mentions=list(dict.fromkeys(mentions))
        c.execute('UPDATE messages SET body=?,mentions=?,edited_at=? WHERE id=?',(body,json.dumps(mentions),now_iso(),mid))
        r=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        emit_event(c,m['channel_id'],'message.updated',{'message':message_to_json(r)})
    return r,200
def delete_message(user,mid):
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
        if not m:return None,404
        ch=c.execute('SELECT * FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
        role=member_role(ch['workspace_id'],user['id']) if ch['workspace_id'] else None
        if m['author_id']!=user['id'] and role!='owner':return None,403
        if not m['deleted']:
            c.execute('UPDATE messages SET deleted=1 WHERE id=?',(mid,))
            emit_event(c,m['channel_id'],'message.deleted',{'message_id':mid})
    return True,200
def open_dm(user,other_id):
    c=connect(); other=c.execute('SELECT * FROM users WHERE id=?',(other_id,)).fetchone()
    if not other:return None,404
    if other_id==user['id']:return None,400
    a,b=sorted((user['id'],other_id));r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
    if r:c.close();return r['channel_id'],200
    with tx() as x:
        cid=x.execute('INSERT INTO channels(workspace_id,name,is_private,is_dm) VALUES(NULL,?,?,1)',('',1)).lastrowid
        x.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,cid))
        x.execute('INSERT INTO channel_members VALUES(?,?)',(cid,a));x.execute('INSERT INTO channel_members VALUES(?,?)',(cid,b))
    return cid,200
def save_file(user,filename,ctype,data):
    if len(data)>10*1024*1024:return None,413
    with tx() as c:
        fid=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(user['id'],filename,ctype,len(data),now_iso())).lastrowid
    os.makedirs('/app/data/files',exist_ok=True)
    with open('/app/data/files/'+str(fid),'wb') as f:f.write(data)
    c=connect();r=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone();c.close();return r,201
def file_to_json(r):
    return {k:r[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def run_slash(user,channel,body):
    if not isinstance(body,str) or not body.startswith('/') or body.startswith('//'):
        return None,None
    if body.startswith('/me'):
        if len(body)<=3 or not body[3:].strip(): return {'error':'text required'},400
        return {'body':body},None
    if body.startswith('/shrug'):
        text=body[6:].strip()
        return {'body':(text+' ' if text else '')+'¯\\_(ツ)_/¯'},None
    parts=body.split(None,1); cmd=parts[0]
    if cmd=='/topic':
        if len(parts)<2 or not parts[1].strip(): return {'error':'topic required'},400
        if len(parts[1])>250:return {'error':'topic too long'},400
        if channel['is_archived']:return {'error':'channel archived'},423
        role=member_role(channel['workspace_id'],user['id'])
        c=connect();m=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(channel['id'],user['id'])).fetchone();c.close()
        if role!='owner' and not m:return {'error':'forbidden'},403
        with tx() as c:
            c.execute('UPDATE channels SET topic=? WHERE id=?',(parts[1],channel['id']))
            r=c.execute('SELECT * FROM channels WHERE id=?',(channel['id'],)).fetchone()
            emit_event(c,channel['id'],'channel.updated',{'channel':channel_to_json(r) if 'channel_to_json' in globals() else {'id':r['id'],'topic':r['topic']}})
        return {'message':None,'channel':channel_to_json(r) if 'channel_to_json' in globals() else {'id':r['id'],'topic':r['topic']}},201
    if cmd=='/invite':
        if len(parts)!=2 or not re.fullmatch(r'@[A-Za-z0-9_]+',parts[1]):return {'error':'invalid user'},400
        c=connect();x=c.execute('SELECT * FROM users WHERE username=?',(parts[1][1:],)).fetchone();c.close()
        if not x:return {'error':'user not found'},404
        if not member_role(channel['workspace_id'],user['id']):return {'error':'forbidden'},403
        with tx() as c:add_channel_member(c,channel['id'],x['id'])
        return {'message':None,'invited_user':user_to_json(x)},201
    if cmd in ('/archive','/unarchive'):
        if member_role(channel['workspace_id'],user['id'])!='owner':return {'error':'forbidden'},403
        value=1 if cmd=='/archive' else 0
        with tx() as c:
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(value,channel['id']))
            r=c.execute('SELECT * FROM channels WHERE id=?',(channel['id'],)).fetchone()
            emit_event(c,channel['id'],'channel.archived' if value else 'channel.unarchived',{'channel':channel_to_json(r) if 'channel_to_json' in globals() else {'id':r['id']}})
        return {'message':None,'channel':channel_to_json(r) if 'channel_to_json' in globals() else {'id':r['id']}},201
    return {'error':'unknown command'},400
def group_to_json(r):
    try: ids=json.loads(r['user_ids'] or '[]')
    except: ids=[]
    return {'id':r['id'],'handle':r['handle'],'name':r['name'],'member_user_ids':ids}
def create_group(actor,slug,b):
    w=get_workspace(slug)
    if not w:return None,404
    if not member_role(w['id'],actor['id']):return None,403
    if not isinstance(b,dict) or not isinstance(b.get('handle'),str) or not re.fullmatch(r'[a-z0-9-]{1,32}',b['handle']) or not isinstance(b.get('name'),str) or not b['name'] or not isinstance(b.get('user_ids',[]),list):return None,400
    ids=b.get('user_ids',[])
    c=connect()
    for uid in ids:
        if not isinstance(uid,int) or isinstance(uid,bool) or not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone():c.close();return None,400
    if c.execute('SELECT 1 FROM groups_tbl WHERE workspace_id=? AND handle=?',(w['id'],b['handle'])).fetchone():c.close();return None,409
    with tx() as x:
        gid=x.execute('INSERT INTO groups_tbl(workspace_id,handle,name,creator_id,user_ids) VALUES(?,?,?,?,?)',(w['id'],b['handle'],b['name'],actor['id'],json.dumps(ids))).lastrowid
        r=x.execute('SELECT * FROM groups_tbl WHERE id=?',(gid,)).fetchone()
    c.close();return r,201
def update_group(actor,slug,handle,b,delete=False):
    w=get_workspace(slug)
    if not w:return None,404
    c=connect();r=c.execute('SELECT * FROM groups_tbl WHERE workspace_id=? AND handle=?',(w['id'],handle)).fetchone()
    if not r:
        if delete and member_role(w['id'],actor['id']) in ('owner','member','admin'): return True,200
        return None,404
    if member_role(w['id'],actor['id']) not in ('owner',) and r['creator_id']!=actor['id']:return None,403
    if delete:
        c.execute('DELETE FROM groups_tbl WHERE id=?',(r['id'],));c.commit();c.close();return True,200
    if not isinstance(b,dict):c.close();return None,400
    name=b.get('name',r['name']);ids=b.get('user_ids',json.loads(r['user_ids'] or '[]'))
    if not isinstance(name,str) or not name or not isinstance(ids,list):c.close();return None,400
    for uid in ids:
        if not isinstance(uid,int) or not c.execute('SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?',(w['id'],uid)).fetchone():c.close();return None,400
    c.execute('UPDATE groups_tbl SET name=?,user_ids=? WHERE id=?',(name,json.dumps(ids),r['id']));c.commit()
    r=c.execute('SELECT * FROM groups_tbl WHERE id=?',(r['id'],)).fetchone();c.close();return r,200
def touch_presence(conn_id,user_id):
    import time
    c=connect();c.execute('INSERT OR REPLACE INTO presence(conn_id,user_id,last_seen) VALUES(?,?,?)',(conn_id,user_id,time.time()));c.commit();c.close()
def drop_presence(conn_id):
    c=connect();c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,));c.commit();c.close()
def present_user_ids():
    import time
    c=connect();rows=c.execute('SELECT DISTINCT user_id FROM presence WHERE last_seen>?',(time.time()-3,)).fetchall();c.close();return {r['user_id'] for r in rows}
from zoneinfo import ZoneInfo
def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def globals_after(since):
    c=connect();r=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall();c.close();return r
def update_profile(user,fields):
    allowed={'display_name':(1,80),'status_text':(0,140),'status_emoji':(0,32),'avatar_url':(0,512),'timezone':(1,128)}
    if not isinstance(fields,dict):return None,400
    for k,v in fields.items():
        if k not in allowed or not isinstance(v,str):return None,400
        lo,hi=allowed[k]
        if len(v)<lo or len(v)>hi:return None,400
        if k=='timezone':
            try:ZoneInfo(v)
            except:return None,400
    with tx() as c:
        if fields:
            c.execute('UPDATE users SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',tuple(fields.values())+(user['id'],))
            r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
            emit_global(c,{'type':'user.updated','user':user_to_json(r)})
        else:r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
    return r,200
