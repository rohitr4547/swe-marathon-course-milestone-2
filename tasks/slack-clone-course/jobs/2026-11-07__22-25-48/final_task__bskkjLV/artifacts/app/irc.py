import asyncio, re, core

clients=set()
NICKS=set()

async def send(w,line):
    try:w.write((line+"\r\n").encode()); await w.drain()
    except Exception: pass

def channel_by_name(name):
    if not name.startswith("#") or "/" not in name:return None
    slug,cn=name[1:].split("/",1); w=core.get_workspace(slug)
    if not w:return None
    c=core.connect(); r=c.execute("SELECT * FROM channels WHERE workspace_id=? AND name=?",(w["id"],cn)).fetchone(); c.close(); return r

async def deliver(client):
    w,user,subs,last,origin=client
    while not w.is_closing():
        for cid in list(subs):
            for ev in core.events_after(cid,last.get(cid,0)):
                last[cid]=ev["event_id"]
                if ev["type"] not in ("message.created","message.reply"):continue
                m=ev.get("message",{})
                if ev["event_id"] in origin: origin.discard(ev["event_id"]); continue
                ch=core.get_channel(cid)
                await send(w,f":{m.get('author',{}).get('username','user')}!{user['username']}@localhost PRIVMSG #{core.get_workspace_by_id(ch['workspace_id'])['slug']}/{ch['name']} :{m.get('body','')}")
        await asyncio.sleep(.1)

async def handle(reader,writer):
    user=None; nick=None; token=None; registered=False; subs=set(); last={}; origin=set(); task=None
    try:
        while True:
            line=await reader.readline()
            if not line:break
            s=line.decode(errors="ignore").strip(); parts=s.split(" ",2); cmd=parts[0].upper() if parts else ""
            if cmd=="PASS": token=parts[1] if len(parts)>1 else ""
            elif cmd=="NICK":
                newnick=parts[1] if len(parts)>1 else ""
                if newnick.lower() in {x.lower() for x in NICKS}:
                    await send(writer,f":huddle 433 {nick or '*'} {newnick} :Nickname is already in use"); continue
                if nick: NICKS.discard(nick)
                nick=newnick; NICKS.add(nick)
                if core.user_for_token(token) is None:
                    await send(writer,":huddle 464 * :Password incorrect"); continue
                user=core.user_for_token(token); registered=True
                await send(writer,f":huddle 001 {nick} :Welcome to Huddle")
                for n,text in [(2,"Your host is huddle"),(3,"This server was created locally"),(4,"huddle"),(5,"CHANTYPES=# PREFIX=(~ohv)~ohv")]:
                    await send(writer,f":huddle {n:03d} {nick} :{text}")
                await send(writer,f":huddle 422 {nick} :MOTD File is missing")
                task=asyncio.create_task(deliver((writer,user,subs,last,origin)))
            elif cmd=="PING": await send(writer,":"+ (parts[1] if len(parts)>1 else "ping")+" PONG")
            elif cmd=="JOIN" and registered:
                name=parts[1] if len(parts)>1 else ""; ch=channel_by_name(name)
                if not ch: await send(writer,f":huddle 403 {nick} {name} :No such channel"); continue
                core.join_channel(user,ch["id"]); subs.add(ch["id"]); last[ch["id"]]=core.head_event_id(ch["id"])
                await send(writer,f":{nick}!{user['username']}@localhost JOIN {name}")
                c=core.connect(); rows=c.execute("SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?",(ch["id"],)).fetchall(); c.close()
                await send(writer,f":huddle 353 {nick} = {name} :"+ " ".join(r["username"] for r in rows))
                await send(writer,f":huddle 366 {nick} {name} :End of /NAMES list")
            elif cmd=="NAMES" and registered:
                name=parts[1] if len(parts)>1 else ""; ch=channel_by_name(name)
                if ch:
                    c=core.connect(); rows=c.execute("SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?",(ch["id"],)).fetchall(); c.close()
                    await send(writer,f":huddle 353 {nick} = {name} :"+ " ".join(r["username"] for r in rows)); await send(writer,f":huddle 366 {nick} {name} :End of /NAMES list")
            elif cmd=="PRIVMSG" and registered and len(parts)>2:
                name=parts[1]; body=parts[2][1:] if parts[2].startswith(":") else parts[2]; ch=channel_by_name(name)
                if ch:
                    m=core.create_message(user,ch["id"],body)
                    if m:
                        origin.add(core.head_event_id(ch["id"]))
            elif cmd=="QUIT":break
            elif cmd and registered and cmd not in ("USER","PONG","WHO","LIST","TOPIC","MODE"):
                await send(writer,f":huddle 421 {nick} {cmd} :Unknown command")
    finally:
        if task: task.cancel()
        if nick: NICKS.discard(nick)
        writer.close(); await writer.wait_closed()

async def main():
    server=await asyncio.start_server(handle,"0.0.0.0",6667)
    async with server: await server.serve_forever()

if __name__=="__main__":
    try: asyncio.run(main())
    except OSError: pass
