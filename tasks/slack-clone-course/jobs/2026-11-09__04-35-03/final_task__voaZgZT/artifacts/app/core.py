import sqlite3, os, json, hashlib, secrets, re, base64
from datetime import datetime, timezone
DB='/app/data/huddle.db'
def connect():
    os.makedirs('/app/data',exist_ok=True); c=sqlite3.connect(DB,timeout=10,isolation_level=None)
    c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=10000'); return c
class Tx:
    def __enter__(self): self.c=connect(); self.c.execute('BEGIN IMMEDIATE'); return self.c
    def __exit__(self,t,v,b):
        self.c.execute('ROLLBACK' if t else 'COMMIT'); self.c.close()
def tx(): return Tx()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def init_db():
    c=connect(); c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "",created_at TEXT);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
    CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
    CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle));
    CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id));
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL);
    '''); c.close()
def hash_password(pw): return hashlib.sha256(pw.encode()).hexdigest()
def verify_password(pw,stored): return secrets.compare_digest(hash_password(pw),stored)
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute('INSERT INTO tokens VALUES(?,?)',(t,uid)); return t
def user_for_token(t):
    if not t:return None
    c=connect(); r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ('id','username','display_name','timezone','avatar_url','status_text','status_emoji')}
def workspace_to_json(r): return dict(r)
def channel_to_json(r):
    d=dict(r); d['is_private']=bool(d['is_private']); d['is_dm']=bool(d['is_dm']); d['is_archived']=bool(d['is_archived']); return d
def get_workspace(slug):
    c=connect(); r=c.execute('SELECT * FROM workspaces WHERE slug=?',(slug,)).fetchone(); c.close(); return r
def get_channel(i):
    c=connect(); r=c.execute('SELECT * FROM channels WHERE id=?',(i,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?',(w,u)).fetchone(); c.close(); return r['role'] if r else None
def emit_global(c,payload):
    c.execute('INSERT INTO global_events(payload) VALUES(?)',(json.dumps(payload),))
def globals_after(since):
    c=connect(); r=c.execute('SELECT id,payload FROM global_events WHERE id>? ORDER BY id',(since,)).fetchall(); c.close(); return r
def emit_event(c,ch,kind,payload):
    n=c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id',(ch,)).fetchone()[0]
    frame={'type':kind,'event_id':n,'channel_id':ch,**payload}; c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame))); return frame
def create_channel_row(c,w,name,private=False,topic=''):
    cur=c.execute('INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)',(w,name,int(private),topic)); return cur.lastrowid
def add_channel_member(c,ch,u): c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch,u))
def add_workspace_member(c,w,u,role='member'):
    c.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(w,u,role))
    r=c.execute('SELECT id FROM channels WHERE workspace_id=? AND name="general"',(w,)).fetchone()
    if r:add_channel_member(c,r[0],u)
def reactions_for(message_id):
    c=connect(); rows=c.execute('SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY rowid',(message_id,)).fetchall(); c.close()
    result=[]
    for r in rows:
        x=next((z for z in result if z['emoji']==r['emoji']),None)
        if not x: x={'emoji':r['emoji'],'count':0,'user_ids':[]}; result.append(x)
        x['count']+=1; x['user_ids'].append(r['user_id'])
    return result
def message_to_json(r):
    c=connect(); author=c.execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); replies=c.execute('SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()['n']; c.close()
    d=dict(r); d['author']=user_to_json(author); d['parent_id']=d.get('parent_id'); d['reply_count']=replies; c2=connect(); fs=c2.execute('SELECT * FROM files WHERE message_id=?',(r['id'],)).fetchall(); c2.close(); d['files']=[file_to_json(x) for x in fs]; d['reactions']=reactions_for(r['id']); d['mentions']=[]
    c3=connect(); members={x['user_id'] for x in c3.execute('SELECT user_id FROM channel_members WHERE channel_id=?',(r['channel_id'],)).fetchall()}
    words=set(re.findall(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)',r['body'] or ''))
    for w in words:
        if w=='channel': d['mentions'] += list(members)
        elif w=='here': d['mentions'] += list(members & set(present_user_ids()))
        else:
            u=c3.execute('SELECT id FROM users WHERE username=?',(w,)).fetchone()
            g=c3.execute('SELECT id FROM groups WHERE handle=? AND workspace_id=(SELECT workspace_id FROM channels WHERE id=?)',(w,r['channel_id'])).fetchone()
            if u:d['mentions'].append(u['id'])
            if g:d['mentions'] += [x['user_id'] for x in c3.execute('SELECT user_id FROM group_members WHERE group_id=?',(g['id'],)).fetchall()]
    c3.close(); d['mentions']=list(dict.fromkeys(x for x in d['mentions'] if x!=r['author_id'] and x in members)); return d
def react(user,message_id,emoji,add=True):
    if not isinstance(emoji,str) or not emoji:return None
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not m:return False
        member=c.execute('SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?',(m['channel_id'],user['id'])).fetchone()
        if not member:return 'FORBIDDEN'
        if add:
            prior=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user['id'],emoji)).fetchone()
            if not prior:
                c.execute('INSERT INTO reactions VALUES(?,?,?)',(message_id,user['id'],emoji))
                emit_event(c,m['channel_id'],'reaction.added',{'message_id':message_id,'emoji':emoji,'user_id':user['id']})
        else:
            prior=c.execute('SELECT 1 FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user['id'],emoji)).fetchone()
            if prior:
                c.execute('DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?',(message_id,user['id'],emoji))
                emit_event(c,m['channel_id'],'reaction.removed',{'message_id':message_id,'emoji':emoji,'user_id':user['id']})
    return reactions_for(message_id)
def open_dm(user, other_id):
    c=connect(); other=c.execute('SELECT id FROM users WHERE id=?',(other_id,)).fetchone(); c.close()
    if not other:return None
    a,b=sorted((user['id'],other_id))
    with tx() as c:
        r=c.execute('SELECT channel_id FROM dms WHERE user_a=? AND user_b=?',(a,b)).fetchone()
        if r:return r['channel_id']
        ch=c.execute('INSERT INTO channels(is_private,is_dm,last_event_id) VALUES(1,1,0)').lastrowid
        c.execute('INSERT INTO dms VALUES(?,?,?)',(a,b,ch))
        c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,a)); c.execute('INSERT INTO channel_members VALUES(?,?)',(ch,b))
        return ch
def update_profile(user,fields):
    allowed={'display_name','timezone','avatar_url','status_text','status_emoji'}
    if not isinstance(fields,dict) or not set(fields)<=allowed:return None
    from zoneinfo import ZoneInfo
    for k,v in fields.items():
        if not isinstance(v,str):return None
        if k=='timezone':
            try:ZoneInfo(v)
            except:return None
        if k=='display_name' and not 1<=len(v)<=80:return None
        if k=='status_text' and len(v)>140:return None
        if k=='status_emoji' and len(v)>32:return None
        if k=='avatar_url' and len(v)>512:return None
    with tx() as c:
        if fields:
            sets=', '.join(k+'=?' for k in fields); c.execute('UPDATE users SET '+sets+' WHERE id=?',tuple(fields.values())+(user['id'],))
        r=c.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone()
        emit_global(c,{'type':'user.updated','user':user_to_json(r)})
    return r
def group_to_json(row):
    c=connect(); ids=[x['user_id'] for x in c.execute('SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id',(row['id'],)).fetchall()]; c.close()
    return {'id':row['id'],'handle':row['handle'],'name':row['name'],'member_user_ids':ids}
def create_group(workspace_id,creator_id,handle,name,user_ids):
    if not re.fullmatch(r'[a-z0-9-]{1,32}',handle): raise ValueError('handle')
    with tx() as c:
        valid={x['user_id'] for x in c.execute('SELECT user_id FROM members WHERE workspace_id=?',(workspace_id,)).fetchall()}
        if not set(user_ids)<=valid: raise ValueError('members')
        i=c.execute('INSERT INTO groups(workspace_id,handle,name,creator_id) VALUES(?,?,?,?)',(workspace_id,handle,name,creator_id)).lastrowid
        for uid in user_ids:c.execute('INSERT INTO group_members VALUES(?,?)',(i,uid))
        return c.execute('SELECT * FROM groups WHERE id=?',(i,)).fetchone()
def update_group(workspace_id,handle,name=None,user_ids=None):
    with tx() as c:
        r=c.execute('SELECT * FROM groups WHERE workspace_id=? AND handle=?',(workspace_id,handle)).fetchone()
        if not r:return None
        if name is not None:c.execute('UPDATE groups SET name=? WHERE id=?',(name,r['id']))
        if user_ids is not None:
            valid={x['user_id'] for x in c.execute('SELECT user_id FROM members WHERE workspace_id=?',(workspace_id,)).fetchall()}
            if not set(user_ids)<=valid:raise ValueError('members')
            c.execute('DELETE FROM group_members WHERE group_id=?',(r['id'],))
            for uid in user_ids:c.execute('INSERT INTO group_members VALUES(?,?)',(r['id'],uid))
        return c.execute('SELECT * FROM groups WHERE id=?',(r['id'],)).fetchone()
def delete_group(workspace_id,handle):
    with tx() as c:
        r=c.execute('SELECT id FROM groups WHERE workspace_id=? AND handle=?',(workspace_id,handle)).fetchone()
        if r:c.execute('DELETE FROM groups WHERE id=?',(r['id'],))
        return True
def touch_presence(conn_id,user_id):
    with tx() as c:c.execute('INSERT OR REPLACE INTO presence VALUES(?,?,strftime("%s","now"))',(conn_id,user_id))
def drop_presence(conn_id):
    with tx() as c:c.execute('DELETE FROM presence WHERE conn_id=?',(conn_id,))
def present_user_ids():
    c=connect(); r=[x['user_id'] for x in c.execute("SELECT DISTINCT user_id FROM presence WHERE last_seen>strftime('%s','now')-3").fetchall()]; c.close(); return r
def file_to_json(row):
    return {k:row[k] for k in ('id','uploader_id','filename','content_type','size','created_at')}
def save_file(user,filename,content_type,data):
    os.makedirs('/app/data/files',exist_ok=True)
    with tx() as c:
        i=c.execute('INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)',(user['id'],filename,content_type or 'application/octet-stream',len(data),now_iso())).lastrowid
        open('/app/data/files/'+str(i),'wb').write(data)
        return c.execute('SELECT * FROM files WHERE id=?',(i,)).fetchone()
def run_slash(user, channel, body):
    if body.startswith('//'): return {'body':body[1:]}
    if not body.startswith('/'): return {'body':body}
    parts=body.split(None,1); cmd=parts[0].lower(); text=parts[1] if len(parts)>1 else ''
    if cmd=='/me':
        if not text.strip(): raise ValueError('empty')
        return {'body':body}
    if cmd=='/shrug': return {'body':(text+' ' if text else '')+'¯\\_(ツ)_/¯'}
    c=connect(); ch=c.execute('SELECT * FROM channels WHERE id=?',(channel['id'],)).fetchone(); c.close()
    if cmd=='/topic':
        if not text or len(text)>250: raise ValueError('topic')
        with tx() as c:
            c.execute('UPDATE channels SET topic=? WHERE id=?',(text,ch['id'])); r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
            emit_event(c,ch['id'],'channel.updated',{'channel':channel_to_json(r)})
        return {'side':{'message':None,'channel':channel_to_json(r)}}
    if cmd=='/invite':
        if not re.fullmatch(r'@[A-Za-z0-9_]+',text.strip()): raise ValueError('invite')
        c=connect(); u=c.execute('SELECT * FROM users WHERE username=?',(text.strip()[1:],)).fetchone(); c.close()
        if not u: raise ValueError('user')
        with tx() as c:add_channel_member(c,ch['id'],u['id'])
        return {'side':{'message':None,'invited_user':user_to_json(u)}}
    if cmd in ('/archive','/unarchive'):
        if member_role(ch['workspace_id'],user['id'])!='owner': return {'forbidden':True}
        val=1 if cmd=='/archive' else 0
        with tx() as c:
            c.execute('UPDATE channels SET is_archived=? WHERE id=?',(val,ch['id'])); r=c.execute('SELECT * FROM channels WHERE id=?',(ch['id'],)).fetchone()
            emit_event(c,ch['id'],'channel.archived' if val else 'channel.unarchived',{'channel':channel_to_json(r)})
        return {'side':{'message':None,'channel':channel_to_json(r)}}
    raise ValueError('unknown')
def create_message(user,ch,body,parent=None,file_ids=None):

    with tx() as c:
        r=c.execute('SELECT * FROM channels WHERE id=?',(ch,)).fetchone()
        m=c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)',(ch,user['id'],body,parent,now_iso())).lastrowid
        for fid in (file_ids or []):
            f=c.execute('SELECT * FROM files WHERE id=?',(fid,)).fetchone()
            if not f or f['uploader_id']!=user['id'] or f['message_id'] is not None: raise ValueError('invalid file')
            c.execute('UPDATE files SET message_id=? WHERE id=?',(m,fid))
        row=c.execute('SELECT * FROM messages WHERE id=?',(m,)).fetchone(); emit_event(c,ch,'message.reply' if parent else 'message.created',{'message':message_to_json(row)}); return row
def edit_message(user,message_id,body):
    if not isinstance(body,str) or not body.strip(): raise ValueError('body')
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not m:return None
        if m['deleted']:raise RuntimeError('deleted')
        if m['author_id']!=user['id']:raise PermissionError()
        c.execute('UPDATE messages SET body=?,edited_at=? WHERE id=?',(body,now_iso(),message_id))
        m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        emit_event(c,m['channel_id'],'message.updated',{'message':message_to_json(m)})
        return m
def delete_message(user,message_id):
    with tx() as c:
        m=c.execute('SELECT * FROM messages WHERE id=?',(message_id,)).fetchone()
        if not m:return None
        if m['deleted']:return m
        owner=False
        if m['channel_id']:
            ch=c.execute('SELECT workspace_id FROM channels WHERE id=?',(m['channel_id'],)).fetchone()
            if ch and ch['workspace_id']:
                owner=bool(c.execute('SELECT 1 FROM workspaces WHERE id=? AND owner_id=?',(ch['workspace_id'],user['id'])).fetchone())
        if m['author_id']!=user['id'] and not owner:raise PermissionError()
        c.execute('UPDATE messages SET deleted=1 WHERE id=?',(message_id,))
        c.execute('DELETE FROM reactions WHERE message_id=?',(message_id,))
        emit_event(c,m['channel_id'],'message.deleted',{'message_id':message_id})
        return m

if __name__=='__main__':
    import argparse; p=argparse.ArgumentParser(); p.add_argument('--init-db',action='store_true'); p.parse_args(); init_db()
