import argparse, asyncio, json, os, re, uuid
from aiohttp import web, WSMsgType
import core

NODE=0
WS_CLIENTS=set()

def json_error(status,msg):
    return web.json_response({"error":msg},status=status)

async def read_json(request):
    try:
        x=await request.json()
        return x if isinstance(x,dict) else None
    except Exception: return None

def require_user(request):
    h=request.headers.get("Authorization","")
    return core.user_for_token(h[7:] if h.startswith("Bearer ") else None)

def out(data,status=200): return web.json_response(data,status=status)

async def health(request): return out({"status":"ok","node_id":NODE})

async def register(request):
    b=await read_json(request)
    if not b or not isinstance(b.get("username"),str) or not re.fullmatch(r"[A-Za-z0-9_]+",b["username"]) or not isinstance(b.get("password"),str) or len(b["password"])<8: return json_error(400,"invalid registration")
    with core.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE username=?",(b["username"],)).fetchone(): return json_error(409,"username exists")
        name=b.get("display_name") if isinstance(b.get("display_name"),str) and b.get("display_name").strip() else b["username"]
        cur=c.execute("INSERT INTO users(username,password,display_name,created_at) VALUES(?,?,?,?)",(b["username"],core.hash_password(b["password"]),name,core.now_iso()))
        u=c.execute("SELECT * FROM users WHERE id=?",(cur.lastrowid,)).fetchone(); t=core.issue_token(c,u["id"])
    return out({"user":core.user_to_json(u),"token":t},201)

async def login(request):
    b=await read_json(request)
    if not b or not isinstance(b.get("username"),str) or not isinstance(b.get("password"),str): return json_error(400,"invalid login")
    c=core.connect(); u=c.execute("SELECT * FROM users WHERE username=?",(b["username"],)).fetchone()
    if not u or not core.verify_password(b["password"],u["password"]): c.close(); return json_error(401,"invalid credentials")
    t=core.issue_token(c,u["id"]); c.close(); return out({"user":core.user_to_json(u),"token":t})

async def me(request):
    u=require_user(request)
    return out({"user":core.user_to_json(u)}) if u else json_error(401,"unauthorized")

async def profile(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    b=await read_json(request)
    if not b:return json_error(400,"invalid body")
    try:r=core.update_profile(u,b)
    except Exception:return json_error(400,"invalid profile")
    return out({"user":core.user_to_json(r)})

async def create_workspace(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    b=await read_json(request)
    if not b or not isinstance(b.get("slug"),str) or not re.fullmatch(r"[a-z0-9-]{2,32}",b["slug"]) or not isinstance(b.get("name"),str):return json_error(400,"invalid workspace")
    with core.tx() as c:
        if c.execute("SELECT 1 FROM workspaces WHERE slug=?",(b["slug"],)).fetchone():return json_error(409,"workspace exists")
        cur=c.execute("INSERT INTO workspaces(slug,name,owner_id) VALUES(?,?,?)",(b["slug"],b["name"],u["id"]))
        wid=cur.lastrowid; c.execute("INSERT INTO workspace_members VALUES(?,?,?)",(wid,u["id"],"owner"))
        ch=core.create_channel_row(c,wid,"general"); core.add_channel_member(c,ch["id"],u["id"])
        w=c.execute("SELECT * FROM workspaces WHERE id=?",(wid,)).fetchone()
    return out({"workspace":core.workspace_to_json(w),"general_channel":core.channel_to_json(ch)},201)

async def workspaces(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    c=core.connect(); rows=c.execute("SELECT w.* FROM workspaces w JOIN workspace_members m ON m.workspace_id=w.id WHERE m.user_id=?",(u["id"],)).fetchall(); c.close()
    return out({"workspaces":[core.workspace_to_json(x) for x in rows]})

async def workspace(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    w=core.get_workspace(request.match_info["slug"])
    if not w:return json_error(404,"not found")
    if not core.member_role(w["id"],u["id"]):return json_error(403,"forbidden")
    c=core.connect(); chans=c.execute("SELECT * FROM channels WHERE workspace_id=? AND (is_archived=0 OR ?)",(w["id"],request.query.get("include_archived") in ("1","true"))).fetchall(); c.close()
    return out({"workspace":core.workspace_to_json(w),"channels":[core.channel_to_json(x) for x in chans],"read_state":{}})

async def members(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    w=core.get_workspace(request.match_info["slug"])
    if not w:return json_error(404,"not found")
    if not core.member_role(w["id"],u["id"]):return json_error(403,"forbidden")
    c=core.connect(); rows=c.execute("SELECT user_id,role FROM workspace_members WHERE workspace_id=?",(w["id"],)).fetchall(); c.close()
    return out({"members":[dict(x) for x in rows]})

async def create_channel(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    w=core.get_workspace(request.match_info["slug"])
    if not w:return json_error(404,"not found")
    role=core.member_role(w["id"],u["id"])
    if not role:return json_error(403,"forbidden")
    b=await read_json(request)
    if not b or not isinstance(b.get("name"),str) or not re.fullmatch(r"[a-z0-9-]{1,32}",b["name"]):return json_error(400,"invalid channel")
    if b.get("is_private") and role not in ("owner","admin"):return json_error(403,"forbidden")
    with core.tx() as c:
        if c.execute("SELECT 1 FROM channels WHERE workspace_id=? AND name=?",(w["id"],b["name"])).fetchone():return json_error(409,"channel exists")
        ch=core.create_channel_row(c,w["id"],b["name"],bool(b.get("is_private")),b.get("topic","") if isinstance(b.get("topic",""),str) else "")
        core.add_channel_member(c,ch["id"],u["id"])
    return out({"channel":core.channel_to_json(ch)},201)

async def channel_join(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    ch=core.get_channel(int(request.match_info["id"]))
    if not ch:return json_error(404,"not found")
    r=core.join_channel(u,ch["id"])
    if r is False:return json_error(403,"forbidden")
    return out({"channel":core.channel_to_json(ch)})

async def channel_leave(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    ch=core.get_channel(int(request.match_info["id"]))
    if not ch:return json_error(404,"not found")
    with core.tx() as c:core.remove_channel_member(c,ch["id"],u["id"])
    return out({"channel":core.channel_to_json(ch)})

async def channel_members(request):
    u=require_user(request); ch=core.get_channel(int(request.match_info["id"]))
    if not u:return json_error(401,"unauthorized")
    if not ch:return json_error(404,"not found")
    if not core.can_read_channel(u["id"],ch):return json_error(403,"forbidden")
    c=core.connect(); rows=c.execute("SELECT u.* FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?",(ch["id"],)).fetchall(); c.close()
    return out({"members":[core.user_to_json(x) for x in rows]})

async def patch_channel(request):
    u=require_user(request); ch=core.get_channel(int(request.match_info["id"]))
    if not u:return json_error(401,"unauthorized")
    if not ch:return json_error(404,"not found")
    if not core.can_read_channel(u["id"],ch):return json_error(403,"forbidden")
    b=await read_json(request)
    if not b or not isinstance(b.get("topic",""),str) or len(b.get("topic",""))>250:return json_error(400,"invalid topic")
    with core.tx() as c:c.execute("UPDATE channels SET topic=? WHERE id=?",(b.get("topic",""),ch["id"]))
    return out({"channel":core.channel_to_json(core.get_channel(ch["id"]))})

async def archive(request):
    u=require_user(request); ch=core.get_channel(int(request.match_info["id"]))
    if not u:return json_error(401,"unauthorized")
    if not ch:return json_error(404,"not found")
    if core.member_role(ch["workspace_id"],u["id"])!="owner":return json_error(403,"forbidden")
    with core.tx() as c:c.execute("UPDATE channels SET is_archived=? WHERE id=?",(1 if request.path.endswith("archive") else 0,ch["id"]))
    return out({"channel":core.channel_to_json(core.get_channel(ch["id"]))})

async def messages(request):
    u=require_user(request); ch=core.get_channel(int(request.match_info["id"]))
    if not u:return json_error(401,"unauthorized")
    if not ch:return json_error(404,"not found")
    if not core.can_read_channel(u["id"],ch):return json_error(403,"forbidden")
    try:limit,cursor=core.parse_paging(request.query,"messages")
    except ValueError:return json_error(400,"invalid paging")
    rows,nxt=core.list_messages(ch["id"],limit,cursor)
    return out({"messages":rows,"next_cursor":nxt})

async def post_message(request):
    u=require_user(request); ch=core.get_channel(int(request.match_info["id"]))
    if not u:return json_error(401,"unauthorized")
    if not ch:return json_error(404,"not found")
    if not core.can_read_channel(u["id"],ch):return json_error(403,"forbidden")
    if not core.require_writable(ch):return json_error(423,"archived")
    b=await read_json(request)
    if not b or not isinstance(b.get("body"),str) or not b["body"].strip():return json_error(400,"empty message")
    r=core.create_message(u,ch["id"],b["body"],b.get("parent_id"))
    return out({"message":r},201)

async def replies(request):
    u=require_user(request); pid=int(request.match_info["id"])
    if not u:return json_error(401,"unauthorized")
    c=core.connect(); p=c.execute("SELECT * FROM messages WHERE id=?",(pid,)).fetchone(); c.close()
    if not p:return json_error(404,"not found")
    ch=core.get_channel(p["channel_id"])
    if not core.can_read_channel(u["id"],ch):return json_error(403,"forbidden")
    try:limit,cursor=core.parse_paging(request.query,"replies")
    except ValueError:return json_error(400,"invalid paging")
    rows,nxt=core.list_replies(pid,limit,cursor); return out({"replies":rows,"next_cursor":nxt})

async def edit(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    b=await read_json(request)
    if not b or not isinstance(b.get("body"),str) or not b["body"].strip():return json_error(400,"invalid body")
    status,r=core.edit_message(u,int(request.match_info["id"]),b["body"])
    if status=="notfound": return json_error(404,"not found")
    if status=="deleted": return json_error(409,"deleted")
    if status!="ok": return json_error(403,"forbidden")
    return out({"message":r})

async def delete(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    status,deleted=core.delete_message(u,int(request.match_info["id"]))
    if status=="notfound": return json_error(404,"not found")
    if status!="ok": return json_error(403,"forbidden")
    return out({"deleted":True})

async def reaction(request):
    u=require_user(request)
    if not u:return json_error(401,"unauthorized")
    b=await read_json(request); emoji=b.get("emoji") if b else None
    if not isinstance(emoji,str) or not emoji:return json_error(400,"invalid emoji")
    return out({"reactions":core.react(u,int(request.match_info["id"]),emoji,request.method=="POST")})

async def ws_handler(request):
    u=core.user_for_token(request.query.get("token"))
    if not u:return json_error(401,"unauthorized")
    ws=web.WebSocketResponse(); await ws.prepare(request); subs={}; alive=True
    async def pump():
        while alive:
            for cid,last in list(subs.items()):
                for ev in core.events_after(cid,last):
                    await ws.send_json(ev); subs[cid]=ev["event_id"]
            await asyncio.sleep(.05)
    task=asyncio.create_task(pump())
    try:
        async for msg in ws:
            if msg.type==WSMsgType.TEXT:
                try:b=json.loads(msg.data)
                except:continue
                if b.get("type")=="subscribe":
                    cid=int(b["channel_id"]); subs[cid]=core.head_event_id(cid); await ws.send_json({"type":"subscribed","channel_id":cid,"head_event_id":subs[cid]})
                elif b.get("type")=="resume":
                    cid=int(b["channel_id"]); subs[cid]=int(b.get("since_event_id",0))
                    for ev in core.events_after(cid,subs[cid]): await ws.send_json(ev); subs[cid]=ev["event_id"]
                    await ws.send_json({"type":"resumed","channel_id":cid,"head_event_id":subs[cid]})
            elif msg.type==WSMsgType.ERROR:break
    finally:
        alive=False; task.cancel()
    return ws

async def index(request):
    try:data=open("/app/static/index.html").read()
    except:data="<html><body><h1>Huddle</h1></body></html>"
    return web.Response(text=data,content_type="text/html")

def app():
    a=web.Application()
    a.router.add_get("/",index); a.router.add_get("/api/health",health)
    a.router.add_post("/api/auth/register",register); a.router.add_post("/api/auth/login",login); a.router.add_get("/api/auth/me",me)
    a.router.add_patch("/api/users/me",profile)
    a.router.add_post("/api/workspaces",create_workspace); a.router.add_get("/api/workspaces",workspaces)
    a.router.add_get("/api/workspaces/{slug}",workspace); a.router.add_get("/api/workspaces/{slug}/members",members)
    a.router.add_post("/api/workspaces/{slug}/channels",create_channel)
    a.router.add_post("/api/channels/{id}/join",channel_join); a.router.add_post("/api/channels/{id}/leave",channel_leave)
    a.router.add_get("/api/channels/{id}/members",channel_members); a.router.add_patch("/api/channels/{id}",patch_channel)
    a.router.add_post("/api/channels/{id}/archive",archive); a.router.add_post("/api/channels/{id}/unarchive",archive)
    a.router.add_get("/api/channels/{id}/messages",messages); a.router.add_post("/api/channels/{id}/messages",post_message)
    a.router.add_get("/api/messages/{id}/replies",replies); a.router.add_patch("/api/messages/{id}",edit); a.router.add_delete("/api/messages/{id}",delete)
    a.router.add_post("/api/messages/{id}/reactions",reaction); a.router.add_delete("/api/messages/{id}/reactions/{emoji}",reaction)
    a.router.add_get("/api/ws",ws_handler)
    return a

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--node",type=int,default=0); NODE=ap.parse_args().node
    web.run_app(app(),host="127.0.0.1",port=8000+NODE)
