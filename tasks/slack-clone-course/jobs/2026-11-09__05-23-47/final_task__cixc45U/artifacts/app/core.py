import sqlite3,os,sys,secrets,hashlib,base64,json,re,datetime
DB='/app/data/huddle.db'
def connect():
 c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=10000');return c
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
def init_db():
 os.makedirs('/app/data',exist_ok=True); c=connect();c.execute('PRAGMA journal_mode=WAL')
 c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT "UTC",avatar_url TEXT DEFAULT "",status_text TEXT DEFAULT "",status_emoji TEXT DEFAULT "");
 CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
 CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT "open");
 CREATE TABLE IF NOT EXISTS members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
 CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT "",is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
 CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
 CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,created_at TEXT,PRIMARY KEY(message_id,user_id,emoji)); CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER UNIQUE); CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER); CREATE TABLE IF NOT EXISTS groups(id INTEGER PRIMARY KEY,workspace_id INTEGER,handle TEXT,name TEXT,creator_id INTEGER,UNIQUE(workspace_id,handle)); CREATE TABLE IF NOT EXISTS group_members(group_id INTEGER,user_id INTEGER,PRIMARY KEY(group_id,user_id)); CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY,user_id INTEGER,last_seen REAL); CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);'''
)
 c.close()
def pw(p): return hashlib.sha256(p.encode()).hexdigest()
def user_json(r): return {k:r[k] for k in ['id','username','display_name','timezone','avatar_url','status_text','status_emoji']}
def chan_json(r): return {**dict(r),'is_private':bool(r['is_private']),'is_dm':bool(r['is_dm']),'is_archived':bool(r['is_archived'])}
def token_user(t):
 c=connect();r=c.execute('SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?',(t,)).fetchone();c.close();return r
def emit(c,ch,kind,payload):
 c.execute('UPDATE channels SET last_event_id=last_event_id+1 WHERE id=?',(ch,)); n=c.execute('SELECT last_event_id FROM channels WHERE id=?',(ch,)).fetchone()[0]
 frame={'type':kind,'event_id':n,'channel_id':ch,**payload};c.execute('INSERT INTO events VALUES(?,?,?)',(ch,n,json.dumps(frame)));return frame
def msg_json(r):
 c=connect(); rr=c.execute('SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji ORDER BY MIN(created_at)',(r['id'],)).fetchall();c.close()
 d=dict(r); ar=connect().execute('SELECT * FROM users WHERE id=?',(r['author_id'],)).fetchone(); d['author']=user_json(ar) if ar else None;d['parent_id']=d['parent_id'];d['reply_count']=connect().execute('SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0',(r['id'],)).fetchone()[0];fr=connect().execute('SELECT id,uploader_id,filename,content_type,size,created_at FROM files WHERE message_id=?',(r['id'],)).fetchall();d['files']=[dict(x) for x in fr];d['mentions']=[];d['reactions']=[{'emoji':x['emoji'],'count':x['n'],'user_ids':[int(i) for i in x['ids'].split(',')]} for x in rr];return d
if __name__=='__main__': init_db()
