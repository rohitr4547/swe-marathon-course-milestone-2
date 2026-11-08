import argparse, asyncio, json, os, re, urllib.parse
from aiohttp import web, WSMsgType
import core
NODE=0
def json_error(s,m): return web.json_response({"error":m},status=s)
async def read_json(r):
    try:return await r.json()
    except:return None
def require_user(r):
    h=r.headers.get("Authorization","")
    return core.user_for_token(h[7:] if h.startswith("Bearer ") else None)
def ok(x,status=200): return web.json_response(x,status=status)
def validbody(b): return isinstance(b,dict)
def role(w,u): return core.member_role(w,u)
def accessible(u,ch):
    return core.can_read_channel(u,ch)
async def health(r): return ok({"status":"ok","node_id":NODE})
async def register(r):
    b=await read_json(r)
    if not isinstance(b,dict) or not isinstance(b.get("username"),str) or not re.fullmatch(r"[A-Za-z0-9_]+",b["username"]) or not isinstance(b.get("password"),str) or len(b["password"])<8:return json_error(400,"invalid registration")
    try:
        with core.tx() as c:
            x=c.execute("INSERT INTO users(username,password,display_name) VALUES(?,?,?)",(b["username"],core.hash_password(b["password"]),b.get("display_name") or b["username"])); u=c.execute("SELECT * FROM users WHERE id=?",(x.lastrowid,)).fetchone(); t=core.issue_token(c,u["id"])
        return ok({"user":core.user_to_json(u),"token":t},201)
    except Exception:return json_error(409,"username exists")
async def login(r):
    b=await read_json(r); c=core.connect(); u=c.execute("SELECT * FROM users WHERE username=?",(b.get("username") if isinstance(b,dict) else None,)).fetchone(); c.close()
    if not u or not core.verify_password(b.get("password",""),u["password"]):return json_error(401,"invalid credentials")
    with core.tx() as c:t=core.issue_token(c,u["id"])
    return ok({"user":core.user_to_json(u),"token":t})
async def me(r):
    u=require_user(r); return ok({"user":core.user_to_json(u)}) if u else json_error(401,"unauthorized")
async def auth(r):
    u=require_user(r)
    return u
async def users_me(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    b=await read_json(r)
    if not isinstance(b,dict):return json_error(400,"invalid body")
    with core.tx() as c:
        fields={k:v for k,v in b.items() if k in ("display_name","timezone","avatar_url","status_text","status_emoji")}
        if fields:c.execute("UPDATE users SET "+",".join(k+"=?" for k in fields)+" WHERE id=?",(*fields.values(),u["id"]))
        x=c.execute("SELECT * FROM users WHERE id=?",(u["id"],)).fetchone()
    return ok({"user":core.user_to_json(x)})
async def workspaces(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    if r.method=="GET":
        c=core.connect(); rows=c.execute("SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?",(u["id"],)).fetchall(); c.close(); return ok({"workspaces":[core.workspace_to_json(x) for x in rows]})
    b=await read_json(r)
    if not isinstance(b,dict) or not isinstance(b.get("slug"),str) or not re.fullmatch(r"[a-z0-9-]{2,32}",b["slug"]) or not isinstance(b.get("name"),str):return json_error(400,"invalid workspace")
    try:
        with core.tx() as c:
            x=c.execute("INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)",(b["slug"],b["name"],u["id"])); wid=x.lastrowid
            core.add_workspace_member(c,wid,u["id"],"owner"); cid=core.create_channel_row(c,wid,"general"); core.add_channel_member(c,cid,u["id"])
            w=c.execute("SELECT * FROM workspaces WHERE id=?",(wid,)).fetchone(); ch=c.execute("SELECT * FROM channels WHERE id=?",(cid,)).fetchone()
        return ok({"workspace":core.workspace_to_json(w),"general_channel":core.channel_to_json(ch)},201)
    except:return json_error(409,"duplicate workspace")
async def workspace(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    slug=r.match_info["slug"]; w=core.get_workspace(slug)
    if not w:return json_error(404,"not found")
    if r.method=="PATCH":
        if role(w["id"],u["id"]) not in ("owner","admin"):return json_error(403,"forbidden")
        b=await read_json(r)
        if not isinstance(b,dict):return json_error(400,"invalid body")
        with core.tx() as c:c.execute("UPDATE workspaces SET name=COALESCE(?,name),join_mode=COALESCE(?,join_mode) WHERE id=?",(b.get("name"),b.get("join_mode"),w["id"]))
        return ok({"workspace":core.workspace_to_json(core.get_workspace(slug))})
    if not role(w["id"],u["id"]):return json_error(403,"forbidden")
    c=core.connect(); cs=c.execute("SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)",(w["id"],r.query.get("include_archived") in ("1","true"))).fetchall(); c.close()
    return ok({"workspace":core.workspace_to_json(w),"channels":[core.channel_to_json(x) for x in cs],"read_state":[]})
async def channels(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    w=core.get_workspace(r.match_info["slug"])
    if not w:return json_error(404,"not found")
    rr=role(w["id"],u["id"])
    if rr not in ("owner","admin","member") and rr is not None:return json_error(403,"forbidden")
    b=await read_json(r)
    if not isinstance(b,dict) or not isinstance(b.get("name"),str) or not re.fullmatch(r"[a-z0-9-]{1,32}",b["name"]):return json_error(400,"invalid channel")
    private=bool(b.get("is_private",False))
    if private and rr not in ("owner","admin"):return json_error(403,"forbidden")
    try:
        with core.tx() as c:
            cid=core.create_channel_row(c,w["id"],b["name"],private,b.get("topic","")); core.add_channel_member(c,cid,u["id"])
            if not private:
                for x in c.execute("SELECT user_id FROM workspace_members WHERE workspace_id=?",(w["id"],)):core.add_channel_member(c,cid,x[0])
            ch=c.execute("SELECT * FROM channels WHERE id=?",(cid,)).fetchone()
        return ok({"channel":core.channel_to_json(ch)},201)
    except:return json_error(409,"duplicate channel")

async def member_role_route(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    w=core.get_workspace(r.match_info["slug"])
    if not w:return json_error(404,"not found")
    target=int(r.match_info["uid"]); rr=role(w["id"],u["id"]); old=role(w["id"],target)
    if not old:
        c=core.connect(); known=c.execute("SELECT id FROM users WHERE id=?",(target,)).fetchone(); c.close()
        if not known:return json_error(404,"not found")
        return json_error(404,"not found")
    if rr not in ("owner","admin") or target==w["owner_id"]:return json_error(403,"forbidden")
    b=await read_json(r); nr=b.get("role") if isinstance(b,dict) else None
    if nr not in ("admin","member","guest"):return json_error(400,"invalid role")
    if rr=="admin" and old=="admin":return json_error(403,"forbidden")
    with core.tx() as c:c.execute("UPDATE workspace_members SET role=? WHERE workspace_id=? AND user_id=?",(nr,w["id"],target))
    return ok({"member":{"user_id":target,"role":nr}})
async def transfer(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    w=core.get_workspace(r.match_info["slug"])
    if not w:return json_error(404,"not found")
    if w["owner_id"]!=u["id"]:return json_error(403,"forbidden")
    b=await read_json(r); target=b.get("new_owner_id") if isinstance(b,dict) else None
    if role(w["id"],target)!="admin":return json_error(400,"target must be admin")
    with core.tx() as c:
        c.execute("UPDATE workspace_members SET role='admin' WHERE workspace_id=? AND user_id=?",(w["id"],u["id"]))
        c.execute("UPDATE workspace_members SET role='owner' WHERE workspace_id=? AND user_id=?",(w["id"],target));c.execute("UPDATE workspaces SET owner_id=? WHERE id=?",(target,w["id"]))
    return ok({"workspace":core.workspace_to_json(core.get_workspace(w["slug"]))})
async def channel_members(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    ch=core.get_channel(int(r.match_info["id"]))
    if not ch:return json_error(404,"not found")
    if not accessible(u["id"],ch):return json_error(403,"forbidden")
    c=core.connect(); rows=c.execute("SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?",(ch["id"],)).fetchall();c.close()
    return ok({"members":[core.user_to_json(x) for x in rows]})
async def pins(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    ch=core.get_channel(int(r.match_info["id"]))
    if not ch:return json_error(404,"not found")
    if not accessible(u["id"],ch):return json_error(403,"forbidden")
    c=core.connect(); rows=c.execute("SELECT p.*,m.* FROM pins p JOIN messages m ON m.id=p.message_id WHERE m.channel_id=? ORDER BY p.pinned_at DESC",(ch["id"],)).fetchall();c.close()
    return ok({"pins":[{"message":core.message_to_json(x),"pinned_by":x["pinned_by"],"pinned_at":x["pinned_at"]} for x in rows],"next_cursor":None})

async def workspace_members(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    w=core.get_workspace(r.match_info["slug"])
    if not w:return json_error(404,"not found")
    if not role(w["id"],u["id"]):return json_error(403,"forbidden")
    c=core.connect()
    rows=c.execute("SELECT user_id,role FROM workspace_members WHERE workspace_id=?",(w["id"],)).fetchall()
    c.close()
    return ok({"members":[{"user_id":x["user_id"],"role":x["role"]} for x in rows]})
async def channel(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    ch=core.get_channel(int(r.match_info["id"]))
    if not ch:return json_error(404,"not found")
    w=core.get_workspace(ch["workspace_id"]) if ch["workspace_id"] else None
    if r.method=="POST" and r.path.endswith("/join"):
        if ch["is_private"]:return json_error(403,"private")
        wr=role(w["id"],u["id"]) if w else None
        if w:
            cc=core.connect(); exists=cc.execute("SELECT role FROM workspace_members WHERE workspace_id=? AND user_id=?",(w["id"],u["id"])).fetchone(); cc.close(); wr=exists["role"] if exists else None
        if wr=="guest":return json_error(403,"forbidden")
        if w and not wr:
            if w["join_mode"]!="open":return json_error(403,"invite required")
            with core.tx() as c:
                c.execute("INSERT OR IGNORE INTO workspace_members(workspace_id,user_id,role) VALUES(?,?,?)",(w["id"],u["id"],"member"))
                g=c.execute("SELECT id FROM channels WHERE workspace_id=? AND name='general'",(w["id"],)).fetchone()
                if g:c.execute("INSERT OR IGNORE INTO channel_members(channel_id,user_id) VALUES(?,?)",(g["id"],u["id"]))
                core.add_channel_member(c,ch["id"],u["id"])
        else:
            with core.tx() as c: core.add_channel_member(c,ch["id"],u["id"])
        return ok({"channel":core.channel_to_json(core.get_channel(ch["id"]))})
    if not role(w["id"],u["id"]) and u["id"]!=w["owner_id"]:return json_error(403,"forbidden")
    if r.method=="PATCH":
        b=await read_json(r)
        with core.tx() as c:c.execute("UPDATE channels SET topic=? WHERE id=?",(b.get("topic",""),ch["id"]))
        return ok({"channel":core.channel_to_json(core.get_channel(ch["id"]))})
    if r.method=="POST" and (r.path.endswith("/archive") or r.path.endswith("/unarchive")):
        if not w or w["owner_id"]!=u["id"]:return json_error(403,"forbidden")
        with core.tx() as c:c.execute("UPDATE channels SET is_archived=? WHERE id=?",(r.path.endswith("/archive"),ch["id"]))
        return ok({"channel":core.channel_to_json(core.get_channel(ch["id"]))})
    if r.method=="POST" and r.path.endswith("/leave"):
        with core.tx() as c:core.remove_channel_member(c,ch["id"],u["id"])
        return ok({"channel":core.channel_to_json(ch)})
    c=core.connect(); ms=c.execute("SELECT * FROM channel_members WHERE channel_id=?",(ch["id"],)).fetchall(); c.close()
    return ok({"members":[core.user_to_json(core.user_for_token("")) for _ in []]})
async def messages(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    ch=core.get_channel(int(r.match_info["id"]))
    if not ch:return json_error(404,"not found")
    if r.method=="POST" and r.path.endswith("/join"):
        if ch["is_private"]:return json_error(403,"private")
        if w and role(w["id"],u["id"])=="guest":return json_error(403,"forbidden")
        if w and not role(w["id"],u["id"]):
            if w["join_mode"]!="open":return json_error(403,"invite required")
            with core.tx() as c: core.add_workspace_member(c,w["id"],u["id"])
        with core.tx() as c: core.add_channel_member(c,ch["id"],u["id"])
        return ok({"channel":core.channel_to_json(core.get_channel(ch["id"]))})
    if not accessible(u["id"],ch):return json_error(403,"forbidden")
    if r.method=="GET":
        try:n,cur=core.parse_paging(r.query,"messages")
        except:return json_error(400,"invalid paging")
        rows=core.list_messages(ch["id"],n,cur); return ok({"messages":[core.message_to_json(x) for x in rows[:n]],"next_cursor":None})
    if ch["is_archived"]:return json_error(423,"archived")
    b=await read_json(r)
    if not isinstance(b,dict) or not isinstance(b.get("body"),str) or not b["body"].strip():return json_error(400,"empty message")
    try:x=core.create_message(u,ch["id"],b["body"],b.get("parent_id"))
    except PermissionError:return json_error(403,"forbidden")
    except RuntimeError:return json_error(423,"archived")
    return ok({"message":core.message_to_json(x)},201)
async def search(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    q=r.query.get("q",""); c=core.connect(); rows=c.execute("SELECT m.* FROM messages m JOIN channel_members cm ON cm.channel_id=m.channel_id WHERE cm.user_id=? AND m.body LIKE ? AND m.deleted=0 ORDER BY m.id DESC",(u["id"],"%"+q+"%")).fetchall(); c.close()
    return ok({"results":[core.message_to_json(x) for x in rows],"next_cursor":None})
async def pin(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    mid=int(r.match_info["id"]); c=core.connect(); m=c.execute("SELECT * FROM messages WHERE id=?",(mid,)).fetchone(); c.close()
    if not m:return json_error(404,"not found")
    if not accessible(u["id"],core.get_channel(m["channel_id"])):return json_error(403,"forbidden")
    with core.tx() as c:
        if r.method=="POST":c.execute("INSERT OR IGNORE INTO pins VALUES(?,?,?)",(mid,u["id"],core.now_iso()))
        else:c.execute("DELETE FROM pins WHERE message_id=?",(mid,))
    return ok({"unpinned":True}) if r.method=="DELETE" else ok({"pin":{"message_id":mid,"pinned_by":u["id"],"pinned_at":core.now_iso()}})
async def reactions(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    mid=int(r.match_info["id"]); b=await read_json(r); emoji=b.get("emoji") if isinstance(b,dict) else None
    if not isinstance(emoji,str):return json_error(400,"invalid emoji")
    with core.tx() as c:
        if r.method=="POST":c.execute("INSERT OR IGNORE INTO reactions VALUES(?,?,?)",(mid,u["id"],emoji))
        else:c.execute("DELETE FROM reactions WHERE message_id=? AND user_id=? AND emoji=?",(mid,u["id"],emoji))
    return ok({"reactions":core.reactions_for(mid)})
async def dms(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    b=await read_json(r); other=b.get("user_id") if isinstance(b,dict) else None
    if not isinstance(other,int):return json_error(400,"invalid user")
    a,z=sorted((u["id"],other))
    with core.tx() as c:
        x=c.execute("SELECT channel_id FROM dms WHERE user_a=? AND user_b=?",(a,z)).fetchone()
        if x:cid=x[0]
        else:
            cid=core.create_channel_row(c,None,f"dm-{a}-{z}",True); c.execute("INSERT INTO dms VALUES(?,?,?)",(a,z,cid)); core.add_channel_member(c,cid,a);core.add_channel_member(c,cid,z)
    return ok({"channel_id":cid})
async def files(r):
    u=await auth(r)
    if not u:return json_error(401,"unauthorized")
    data=await r.read(); fn=r.headers.get("X-Filename","upload"); ct=r.headers.get("Content-Type","application/octet-stream")
    with core.tx() as c:
        x=c.execute("INSERT INTO files(uploader_id,filename,content_type,size,created_at) VALUES(?,?,?,?,?)",(u["id"],fn,ct,len(data),core.now_iso())); fid=x.lastrowid
    open(f"/app/data/files/{fid}","wb").write(data); return ok({"file":{"id":fid,"uploader_id":u["id"],"filename":fn,"content_type":ct,"size":len(data),"created_at":core.now_iso()}},201)
async def ws(r):
    u=core.user_for_token(r.query.get("token")); 
    if not u:return web.Response(status=401)
    w=web.WebSocketResponse(); await w.prepare(r); last={}
    async for m in w:
        if m.type==WSMsgType.TEXT:
            x=json.loads(m.data)
            if x.get("type")=="subscribe":
                ch=int(x["channel_id"]); last[ch]=core.head_event_id(ch); await w.send_json({"type":"subscribed","channel_id":ch,"head_event_id":last[ch]})
            elif x.get("type")=="resume":
                ch=int(x["channel_id"]); last[ch]=int(x.get("since_event_id",0))
        if w.closed:break
    return w
def app():
    a=web.Application()
    a.router.add_get("/",lambda r:web.FileResponse("/app/static/index.html"))
    a.router.add_get("/api/health",health);a.router.add_post("/api/auth/register",register);a.router.add_post("/api/auth/login",login);a.router.add_get("/api/auth/me",me);a.router.add_patch("/api/users/me",users_me)
    a.router.add_route("*","/api/workspaces",workspaces);a.router.add_get("/api/workspaces/{slug}/members",workspace_members);a.router.add_patch("/api/workspaces/{slug}/members/{uid}",member_role_route);a.router.add_post("/api/workspaces/{slug}/transfer_ownership",transfer);a.router.add_post("/api/workspaces/{slug}/channels",channels);a.router.add_route("*","/api/workspaces/{slug}",workspace)
    for act in ("join","leave","archive","unarchive"):a.router.add_route("*",f"/api/channels/{{id}}/{act}",channel)
    a.router.add_route("*","/api/channels/{id}/members",channel_members);a.router.add_get("/api/channels/{id}/pins",pins);a.router.add_route("*","/api/channels/{id}/messages",messages);a.router.add_route("*","/api/channels/{id}",channel);a.router.add_route("*","/api/messages/{id}/pin",pin);a.router.add_route("*","/api/messages/{id}/reactions",reactions);a.router.add_get("/api/search",search);a.router.add_post("/api/dms",dms);a.router.add_post("/api/files",files);a.router.add_get("/api/ws",ws)
    return a
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--node",type=int,default=0);NODE=p.parse_args().node;core.init_db();web.run_app(app(),host="127.0.0.1",port=8000+NODE)
