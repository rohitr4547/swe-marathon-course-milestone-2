import argparse, base64, hashlib, json, os, re, secrets, sqlite3, time
from datetime import datetime, timezone
DB="/app/data/huddle.db"
def connect():
    c=sqlite3.connect(DB,timeout=10,isolation_level=None); c.row_factory=sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000"); c.execute("PRAGMA foreign_keys=ON"); return c
class tx:
    def __enter__(self):
        self.c=connect(); self.c.execute("BEGIN IMMEDIATE"); return self.c
    def __exit__(self,t,v,b):
        self.c.execute("ROLLBACK" if t else "COMMIT"); self.c.close()
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")
def init_db():
    os.makedirs("/app/data/files",exist_ok=True)
    c=connect(); c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE,password TEXT,display_name TEXT,timezone TEXT DEFAULT 'UTC',avatar_url TEXT DEFAULT '',status_text TEXT DEFAULT '',status_emoji TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY,user_id INTEGER);
CREATE TABLE IF NOT EXISTS workspaces(id INTEGER PRIMARY KEY,slug TEXT UNIQUE,name TEXT,owner_id INTEGER,join_mode TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS workspace_members(workspace_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(workspace_id,user_id));
CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY,workspace_id INTEGER,name TEXT,is_private INTEGER DEFAULT 0,is_dm INTEGER DEFAULT 0,topic TEXT DEFAULT '',is_archived INTEGER DEFAULT 0,last_event_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS channel_members(channel_id INTEGER,user_id INTEGER,PRIMARY KEY(channel_id,user_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,channel_id INTEGER,author_id INTEGER,body TEXT,parent_id INTEGER,created_at TEXT,edited_at TEXT,deleted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions(message_id INTEGER,user_id INTEGER,emoji TEXT,PRIMARY KEY(message_id,user_id,emoji));
CREATE TABLE IF NOT EXISTS pins(message_id INTEGER PRIMARY KEY,pinned_by INTEGER,pinned_at TEXT);
CREATE TABLE IF NOT EXISTS events(channel_id INTEGER,event_id INTEGER,payload TEXT,PRIMARY KEY(channel_id,event_id));
CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY,uploader_id INTEGER,filename TEXT,content_type TEXT,size INTEGER,created_at TEXT,message_id INTEGER);
CREATE TABLE IF NOT EXISTS dms(user_a INTEGER,user_b INTEGER,channel_id INTEGER,PRIMARY KEY(user_a,user_b));
CREATE TABLE IF NOT EXISTS read_state(user_id INTEGER,channel_id INTEGER,last_read_event_id INTEGER DEFAULT 0,PRIMARY KEY(user_id,channel_id));"""); c.close()
def hash_password(pw):
    s=secrets.token_bytes(16); return base64.urlsafe_b64encode(s).decode()+"$"+hashlib.sha256(s+pw.encode()).hexdigest()
def verify_password(pw,stored):
    try:
        a,b=stored.split("$",1); s=base64.urlsafe_b64decode(a.encode()); return secrets.compare_digest(hashlib.sha256(s+pw.encode()).hexdigest(),b)
    except: return False
def issue_token(c,uid):
    t=secrets.token_urlsafe(24); c.execute("INSERT INTO tokens VALUES(?,?)",(t,uid)); return t
def user_for_token(t):
    if not t:return None
    c=connect(); r=c.execute("SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?",(t,)).fetchone(); c.close(); return r
def user_to_json(r):
    return {k:r[k] for k in ("id","username","display_name","timezone","avatar_url","status_text","status_emoji")}
def workspace_to_json(r): return {k:r[k] for k in ("id","slug","name","owner_id","join_mode")}
def channel_to_json(r):
    return {k:(bool(r[k]) if k in ("is_private","is_dm","is_archived") else r[k]) for k in ("id","workspace_id","name","is_private","is_dm","topic","is_archived")}
def get_workspace(slug):
    c=connect(); r=c.execute("SELECT * FROM workspaces WHERE slug=?",(slug,)).fetchone(); c.close(); return r
def get_channel(i):
    c=connect(); r=c.execute("SELECT * FROM channels WHERE id=?",(i,)).fetchone(); c.close(); return r
def member_role(w,u):
    c=connect(); r=c.execute("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?",(w,u)).fetchone(); c.close(); return r["role"] if r else None
def add_workspace_member(c,w,u,role="member"):
    c.execute("INSERT OR IGNORE INTO workspace_members VALUES(?,?,?)",(w,u,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w,)).fetchone()
    if g:c.execute("INSERT OR IGNORE INTO channel_members VALUES(?,?)",(g["id"],u))
def add_channel_member(c,ch,u):
    c.execute("INSERT OR IGNORE INTO channel_members VALUES(?,?)",(ch,u))
def remove_channel_member(c,ch,u): c.execute("DELETE FROM channel_members WHERE channel_id=? AND user_id=?",(ch,u))
def create_channel_row(c,w,name,private=False,topic=""):
    cur=c.execute("INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)",(w,name,int(private),topic)); return cur.lastrowid
def can_read_channel(u,ch):
    c=connect(); r=c.execute("SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?",(ch["id"],u)).fetchone(); c.close(); return bool(r)
def require_writable(ch): return not ch["is_archived"]
def emit_event(c,ch,kind,payload):
    n=c.execute("UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id",(ch,)).fetchone()[0]
    p={"type":kind,"event_id":n,"channel_id":ch,**payload}; c.execute("INSERT INTO events VALUES(?,?,?)",(ch,n,json.dumps(p))); return p
def emit_global(c,p): c.execute("INSERT INTO global_events(payload) VALUES(?)",(json.dumps(p),))
def head_event_id(ch):
    c=connect(); r=c.execute("SELECT last_event_id FROM channels WHERE id=?",(ch,)).fetchone(); c.close(); return r[0] if r else 0
def events_after(ch,s):
    c=connect(); r=c.execute("SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id",(ch,s)); x=[json.loads(a[0]) for a in r]; c.close(); return x
def globals_after(s):
    c=connect(); r=c.execute("SELECT id,payload FROM global_events WHERE id>? ORDER BY id",(s,)); x=[(a[0],json.loads(a[1])) for a in r]; c.close(); return x
def encode_cursor(k,v): return base64.urlsafe_b64encode(json.dumps({"k":k,"v":int(v)}).encode()).decode().rstrip("=")
def decode_cursor(k,s):
    try:
        x=json.loads(base64.urlsafe_b64decode(s+"="*((4-len(s)%4)%4))); assert x["k"]==k and isinstance(x["v"],int); return x["v"]
    except: raise ValueError()
def parse_paging(q,k):
    if "page" in q or "offset" in q: raise ValueError()
    n=int(q.get("limit",50)); assert 1<=n<=200
    return n,decode_cursor(k,q["cursor"]) if q.get("cursor") else None
def message_to_json(r):
    c=connect(); a=c.execute("SELECT * FROM users WHERE id=?",(r["author_id"],)).fetchone()
    rs=c.execute("SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji",(r["id"],))
    reactions=[{"emoji":x["emoji"],"count":x["n"],"user_ids":[int(z) for z in x["ids"].split(",")]} for x in rs]
    rc=c.execute("SELECT COUNT(*) FROM messages WHERE parent_id=? AND deleted=0",(r["id"],)).fetchone()[0]; c.close()
    return {"id":r["id"],"channel_id":r["channel_id"],"author_id":r["author_id"],"author":user_to_json(a),"body":r["body"],"parent_id":r["parent_id"],"reply_count":rc,"created_at":r["created_at"],"edited_at":r["edited_at"],"files":[],"reactions":reactions,"mentions":[]}
def create_message(user,ch,body,parent_id=None):
    with tx() as c:
        r=c.execute("SELECT * FROM channels WHERE id=?",(ch,)).fetchone()
        if not r or not can_read_channel(user["id"],r): raise PermissionError()
        if r["is_archived"]: raise RuntimeError()
        cur=c.execute("INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)",(ch,user["id"],body,parent_id,now_iso())); mid=cur.lastrowid
        row=c.execute("SELECT * FROM messages WHERE id=?",(mid,)).fetchone(); emit_event(c,ch,"message.reply" if parent_id else "message.created",{"message":message_to_json(row)})
    return row
def list_messages(ch,limit=50,cursor=None,replies=False):
    c=connect(); q="SELECT * FROM messages WHERE channel_id=? AND deleted=0 AND parent_id IS "+("NOT NULL" if replies else "NULL")+" ORDER BY id "+("ASC" if replies else "DESC")+" LIMIT ?"; rows=list(c.execute(q,(ch,limit+1))); c.close(); return rows
def reactions_for(mid):
    c=connect(); r=c.execute("SELECT emoji,COUNT(*) n,GROUP_CONCAT(user_id) ids FROM reactions WHERE message_id=? GROUP BY emoji",(mid,)); x=[{"emoji":a["emoji"],"count":a["n"],"user_ids":[int(z) for z in a["ids"].split(",")]} for a in r]; c.close(); return x
def update_profile(user,fields):
    allowed={"display_name","timezone","avatar_url","status_text","status_emoji"}
    with tx() as c:
        vals={k:v for k,v in fields.items() if k in allowed}; 
        if vals:
            c.execute("UPDATE users SET "+",".join(k+"=?" for k in vals)+" WHERE id=?",(*vals.values(),user["id"]))
            emit_global(c,{"type":"user.updated","user":user_to_json(c.execute("SELECT * FROM users WHERE id=?",(user["id"],)).fetchone())})
    return user_for_token(None)
def touch_presence(*a): pass
def drop_presence(*a): pass
def present_user_ids(): return set()
if __name__=="__main__":
    if "--init-db" in os.sys.argv:init_db()
