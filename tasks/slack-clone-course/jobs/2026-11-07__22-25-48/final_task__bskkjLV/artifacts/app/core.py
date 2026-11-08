import argparse, base64, hashlib, json, os, secrets, sqlite3
from datetime import datetime, timezone

DB_PATH = "/app/data/huddle.db"

def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    c.execute("PRAGMA foreign_keys=ON")
    return c

class tx:
    def __enter__(self):
        self.c = connect()
        self.c.execute("BEGIN IMMEDIATE")
        return self.c
    def __exit__(self, typ, val, tb):
        if typ:
            self.c.rollback()
        else:
            self.c.commit()
        self.c.close()

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def init_db():
    c = connect()
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
      display_name TEXT NOT NULL, timezone TEXT DEFAULT 'UTC', avatar_url TEXT DEFAULT '',
      status_text TEXT DEFAULT '', status_emoji TEXT DEFAULT '', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS tokens(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS workspaces(
      id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
      owner_id INTEGER NOT NULL, join_mode TEXT NOT NULL DEFAULT 'open');
    CREATE TABLE IF NOT EXISTS workspace_members(
      workspace_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL,
      PRIMARY KEY(workspace_id,user_id));
    CREATE TABLE IF NOT EXISTS channels(
      id INTEGER PRIMARY KEY, workspace_id INTEGER, name TEXT NOT NULL, is_private INTEGER DEFAULT 0,
      is_dm INTEGER DEFAULT 0, topic TEXT DEFAULT '', is_archived INTEGER DEFAULT 0,
      last_event_id INTEGER DEFAULT 0, UNIQUE(workspace_id,name));
    CREATE TABLE IF NOT EXISTS channel_members(
      channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(channel_id,user_id));
    CREATE TABLE IF NOT EXISTS messages(
      id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, author_id INTEGER NOT NULL,
      body TEXT NOT NULL, parent_id INTEGER, created_at TEXT NOT NULL, edited_at TEXT,
      deleted INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS reactions(
      message_id INTEGER NOT NULL, user_id INTEGER NOT NULL, emoji TEXT NOT NULL,
      PRIMARY KEY(message_id,user_id,emoji));
    CREATE TABLE IF NOT EXISTS events(
      channel_id INTEGER NOT NULL, event_id INTEGER NOT NULL, payload TEXT NOT NULL,
      PRIMARY KEY(channel_id,event_id));
    CREATE TABLE IF NOT EXISTS global_events(id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS presence(conn_id TEXT PRIMARY KEY, user_id INTEGER, last_seen REAL);
    CREATE TABLE IF NOT EXISTS dms(user_a INTEGER NOT NULL, user_b INTEGER NOT NULL, channel_id INTEGER NOT NULL,
      PRIMARY KEY(user_a,user_b));
    CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, uploader_id INTEGER NOT NULL, filename TEXT NOT NULL,
      content_type TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL, message_id INTEGER);
    """)
    c.close()

def hash_password(pw):
    salt = secrets.token_bytes(16)
    return salt.hex() + ":" + hashlib.sha256(salt + pw.encode()).hexdigest()

def verify_password(pw, stored):
    try:
        salt, digest = stored.split(":")
        return hashlib.sha256(bytes.fromhex(salt) + pw.encode()).hexdigest() == digest
    except Exception:
        return False

def issue_token(c, user_id):
    t = secrets.token_urlsafe(24)
    c.execute("INSERT INTO tokens(token,user_id,created_at) VALUES(?,?,?)", (t,user_id,now_iso()))
    return t

def user_for_token(token):
    if not token: return None
    c=connect()
    r=c.execute("SELECT u.* FROM users u JOIN tokens t ON t.user_id=u.id WHERE t.token=?", (token,)).fetchone()
    c.close()
    return r

def user_to_json(r):
    return {k:r[k] for k in ("id","username","display_name","timezone","avatar_url","status_text","status_emoji")}

if __name__ == "__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--init-db",action="store_true")
    if ap.parse_args().init_db: init_db()

def workspace_to_json(r):
    return {"id":r["id"],"slug":r["slug"],"name":r["name"],"owner_id":r["owner_id"],"join_mode":r["join_mode"]}

def get_workspace(slug):
    c=connect(); r=c.execute("SELECT * FROM workspaces WHERE slug=?", (slug,)).fetchone(); c.close(); return r

def member_role(workspace_id,user_id):
    c=connect(); r=c.execute("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?", (workspace_id,user_id)).fetchone(); c.close()
    return r["role"] if r else None

def channel_to_json(r):
    return {"id":r["id"],"workspace_id":r["workspace_id"],"name":r["name"],"is_private":bool(r["is_private"]),
            "is_dm":bool(r["is_dm"]),"topic":r["topic"] or "","is_archived":bool(r["is_archived"])}

def get_channel(cid):
    c=connect(); r=c.execute("SELECT * FROM channels WHERE id=?", (cid,)).fetchone(); c.close(); return r

def add_channel_member(c, channel_id, user_id):
    c.execute("INSERT OR IGNORE INTO channel_members(channel_id,user_id) VALUES(?,?)",(channel_id,user_id))

def remove_channel_member(c, channel_id, user_id):
    c.execute("DELETE FROM channel_members WHERE channel_id=? AND user_id=?",(channel_id,user_id))

def create_channel_row(c, workspace_id, name, is_private=False, topic=""):
    cur=c.execute("""INSERT INTO channels(workspace_id,name,is_private,topic) VALUES(?,?,?,?)""",
                  (workspace_id,name,int(is_private),topic))
    return c.execute("SELECT * FROM channels WHERE id=?",(cur.lastrowid,)).fetchone()

def add_workspace_member(c, workspace_id, user_id, role="member"):
    c.execute("INSERT OR IGNORE INTO workspace_members(workspace_id,user_id,role) VALUES(?,?,?)",
              (workspace_id,user_id,role))
    g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(workspace_id,)).fetchone()
    if g: add_channel_member(c,g["id"],user_id)

def can_read_channel(user_id, channel):
    if channel["is_dm"]:
        c=connect(); r=c.execute("SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?",(channel["id"],user_id)).fetchone(); c.close(); return bool(r)
    return bool(member_role(channel["workspace_id"],user_id)) and (
        not channel["is_private"] or _channel_member(channel["id"],user_id))

def _channel_member(cid,uid):
    c=connect(); r=c.execute("SELECT 1 FROM channel_members WHERE channel_id=? AND user_id=?",(cid,uid)).fetchone(); c.close(); return bool(r)

def require_writable(channel):
    return bool(channel and not channel["is_archived"])

def join_channel(user, channel_id):
    with tx() as c:
        ch=c.execute("SELECT * FROM channels WHERE id=?",(channel_id,)).fetchone()
        if not ch: return None
        if ch["workspace_id"]:
            ws=c.execute("SELECT * FROM workspaces WHERE id=?",(ch["workspace_id"],)).fetchone()
            if not c.execute("SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=?",(ws["id"],user["id"])).fetchone():
                if ws["join_mode"]!="open" or ch["is_private"]: return False
                add_workspace_member(c,ws["id"],user["id"])
        add_channel_member(c,channel_id,user["id"])
        return ch

def emit_event(c, channel_id, kind, payload):
    n=c.execute("UPDATE channels SET last_event_id=last_event_id+1 WHERE id=? RETURNING last_event_id",(channel_id,)).fetchone()["last_event_id"]
    frame={"type":kind,"event_id":n,"channel_id":channel_id}; frame.update(payload)
    c.execute("INSERT INTO events(channel_id,event_id,payload) VALUES(?,?,?)",(channel_id,n,json.dumps(frame)))
    return frame

def emit_global(c,payload):
    c.execute("INSERT INTO global_events(payload) VALUES(?)",(json.dumps(payload),))

def head_event_id(channel_id):
    c=connect(); r=c.execute("SELECT last_event_id FROM channels WHERE id=?",(channel_id,)).fetchone(); c.close(); return r["last_event_id"] if r else 0

def events_after(channel_id,since):
    c=connect(); rows=c.execute("SELECT payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id",(channel_id,since)).fetchall(); c.close()
    return [json.loads(x["payload"]) for x in rows]

def globals_after(since):
    c=connect(); rows=c.execute("SELECT id,payload FROM global_events WHERE id>? ORDER BY id",(since,)).fetchall(); c.close()
    return [(x["id"],json.loads(x["payload"])) for x in rows]

def encode_cursor(kind,v):
    return base64.urlsafe_b64encode(json.dumps({"k":kind,"v":int(v)}).encode()).decode().rstrip("=")

def decode_cursor(kind,s):
    try:
        raw=s + "="*((4-len(s)%4)%4); o=json.loads(base64.urlsafe_b64decode(raw))
        if not isinstance(o,dict) or o.get("k")!=kind or not isinstance(o.get("v"),int): raise ValueError()
        return o["v"]
    except Exception: raise ValueError("invalid cursor")

def parse_paging(query,kind):
    try:
        limit=int(query.get("limit","50"))
        if limit<1 or limit>200: raise ValueError()
        cur=decode_cursor(kind,query["cursor"]) if query.get("cursor") else None
        return limit,cur
    except Exception: raise ValueError("invalid paging")

def resolve_mentions(c,body,channel,author_id):
    names=set()
    if "@channel" in body:
        rows=c.execute("SELECT user_id FROM channel_members WHERE channel_id=? AND user_id!=?",(channel["id"],author_id)).fetchall()
        names.update(r["user_id"] for r in rows)
    import re
    for n in re.findall(r"(?<![\w@])@([A-Za-z0-9_]+)",body):
        if n in ("channel","here"): continue
        r=c.execute("SELECT u.id FROM users u JOIN workspace_members wm ON wm.user_id=u.id WHERE u.username=? AND wm.workspace_id=? AND u.id!=?",(n,channel["workspace_id"],author_id)).fetchone()
        if r and _channel_member(channel["id"],r["id"]): names.add(r["id"])
    return sorted(names)

def reactions_for(message_id):
    c=connect(); rows=c.execute("SELECT emoji,user_id FROM reactions WHERE message_id=? ORDER BY emoji",(message_id,)).fetchall(); c.close()
    d={}
    for r in rows: d.setdefault(r["emoji"],[]).append(r["user_id"])
    return [{"emoji":e,"count":len(u),"user_ids":u} for e,u in d.items()]

def message_to_json(r):
    c=connect(); author=c.execute("SELECT * FROM users WHERE id=?",(r["author_id"],)).fetchone()
    replies=c.execute("SELECT count(*) n FROM messages WHERE parent_id=? AND deleted=0",(r["id"],)).fetchone()["n"]; c.close()
    return {"id":r["id"],"channel_id":r["channel_id"],"author_id":r["author_id"],"author":user_to_json(author),
            "body":r["body"],"parent_id":r["parent_id"],"reply_count":replies,"created_at":r["created_at"],
            "edited_at":r["edited_at"],"files":[],"reactions":reactions_for(r["id"]),"mentions":[]}

def create_message(user, channel_id, body, parent_id=None):
    with tx() as c:
        ch=c.execute("SELECT * FROM channels WHERE id=?",(channel_id,)).fetchone()
        if not ch or not require_writable(ch): return None
        mentions=resolve_mentions(c,body,ch,user["id"])
        cur=c.execute("INSERT INTO messages(channel_id,author_id,body,parent_id,created_at) VALUES(?,?,?,?,?)",(channel_id,user["id"],body,parent_id,now_iso()))
        r=c.execute("SELECT * FROM messages WHERE id=?",(cur.lastrowid,)).fetchone()
        frame=emit_event(c,channel_id,"message.reply" if parent_id else "message.created",{"message":message_to_json(r)})
        return message_to_json(r)

def list_messages(channel_id,limit=50,cursor=None,replies=False):
    c=connect(); op="ASC" if replies else "DESC"; where="parent_id IS NOT NULL" if replies else "parent_id IS NULL"
    args=[channel_id]
    sql=f"SELECT * FROM messages WHERE channel_id=? AND {where} AND deleted=0"
    if cursor is not None: sql += " AND id " + (">" if replies else "<") + "?"; args.append(cursor)
    sql += " ORDER BY id "+op+" LIMIT ?"; args.append(limit+1)
    rows=c.execute(sql,args).fetchall(); c.close()
    more=len(rows)>limit; rows=rows[:limit]
    return [message_to_json(r) for r in rows], (encode_cursor("replies" if replies else "messages",rows[-1]["id"]) if more and rows else None)

def list_replies(parent_id,limit=50,cursor=None):
    c=connect(); args=[parent_id]; sql="SELECT * FROM messages WHERE parent_id=? AND deleted=0"
    if cursor is not None: sql+=" AND id>?"; args.append(cursor)
    sql+=" ORDER BY id ASC LIMIT ?"; args.append(limit+1); rows=c.execute(sql,args).fetchall(); c.close()
    more=len(rows)>limit; rows=rows[:limit]
    return [message_to_json(r) for r in rows], (encode_cursor("replies",rows[-1]["id"]) if more and rows else None)

def react(user,message_id,emoji,add=True):
    with tx() as c:
        if add: c.execute("INSERT OR IGNORE INTO reactions(message_id,user_id,emoji) VALUES(?,?,?)",(message_id,user["id"],emoji))
        else: c.execute("DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?",(message_id,user["id"],emoji))
        return reactions_for(message_id)

def edit_message(user,message_id,body):
    with tx() as c:
        r=c.execute("SELECT * FROM messages WHERE id=?",(message_id,)).fetchone()
        if not r: return ("notfound",None)
        if r["deleted"]: return ("deleted",None)
        ch=c.execute("SELECT * FROM channels WHERE id=?",(r["channel_id"],)).fetchone()
        if r["author_id"]!=user["id"]: return ("forbidden",None)
        mentions=resolve_mentions(c,body,ch,user["id"])
        c.execute("UPDATE messages SET body=?,edited_at=? WHERE id=?",(body,now_iso(),message_id))
        r=c.execute("SELECT * FROM messages WHERE id=?",(message_id,)).fetchone()
        obj=message_to_json(r); obj["mentions"]=mentions
        emit_event(c,r["channel_id"],"message.updated",{"message":obj})
        return ("ok",obj)

def delete_message(user,message_id):
    with tx() as c:
        r=c.execute("SELECT * FROM messages WHERE id=?",(message_id,)).fetchone()
        if not r: return ("notfound",False)
        if r["deleted"]: return ("ok",False)
        ch=c.execute("SELECT * FROM channels WHERE id=?",(r["channel_id"],)).fetchone()
        role=c.execute("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?",(ch["workspace_id"],user["id"])).fetchone() if ch["workspace_id"] else None
        if r["author_id"]!=user["id"] and (not role or role["role"]!="owner"): return ("forbidden",False)
        c.execute("UPDATE messages SET deleted=1 WHERE id=?",(message_id,))
        emit_event(c,r["channel_id"],"message.deleted",{"message_id":message_id})
        return ("ok",True)

def update_profile(user,fields):
    allowed={"display_name","timezone","avatar_url","status_text","status_emoji"}
    vals={k:v for k,v in fields.items() if k in allowed}
    with tx() as c:
        for k,v in vals.items(): c.execute(f"UPDATE users SET {k}=? WHERE id=?",(v,user["id"]))
        r=c.execute("SELECT * FROM users WHERE id=?",(user["id"],)).fetchone(); emit_global(c,{"type":"user.updated","user":user_to_json(r)}); return r

def get_workspace_by_id(wid):
    c=connect(); r=c.execute("SELECT * FROM workspaces WHERE id=?",(wid,)).fetchone(); c.close(); return r
