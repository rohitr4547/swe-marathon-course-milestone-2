import asyncio, core, re
NICKS=set(); CONNS=set(); INFO={}; ORIGIN={}
def line(w,s): w.write((s+'\r\n').encode())
async def handle(r,w):
    nick=None; user=None; token=None; registered=False; joined={}
    CONNS.add(w); INFO[w]={'joined':joined,'last':{}}
    try:
        while not r.at_eof():
            raw=await r.readline()
            if not raw: break
            text=raw.decode(errors='ignore').strip('\r\n')
            if not text: continue
            parts=text.split(' ',1); cmd=parts[0].upper(); rest=parts[1] if len(parts)>1 else ''
            if cmd=='PASS': token=rest.strip()
            elif cmd=='NICK':
                n=rest.split()[0] if rest else ''
                if n in NICKS and n!=nick:
                    line(w,f':huddle 433 * {n} :Nickname is already in use'); continue
                if nick:NICKS.discard(nick)
                nick=n; NICKS.add(nick)
            elif cmd=='USER': user=(rest.split()[0] if rest else nick)
            elif cmd=='QUIT': break
            elif cmd=='PRIVMSG':
                z=rest.split(' :',1)
                target=z[0].strip() if z else ''
                body=z[1] if len(z)>1 else ''
                m=re.fullmatch(r'#([^/]+)/([A-Za-z0-9-]+)',target)
                u=core.user_for_token(token); ws=core.get_workspace(m.group(1)) if m else None
                ch=None
                if ws:
                    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],m.group(2))).fetchone(); c.close()
                if ch and u and ch['id'] in joined:
                    msg=core.create_message(u,ch['id'],body); ORIGIN[(ch['id'],msg['id'])]=w
                else: line(w,f':huddle 403 {nick} {target} :No such channel')
            elif cmd=='PING':
                tok=rest[1:] if rest.startswith(':') else rest
                line(w,f':huddle PONG huddle :{tok}')
            elif cmd=='PONG': pass
            elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'): pass
            elif cmd=='JOIN':
                name=rest.split(',')[0].strip()
                m=re.fullmatch(r'#([^/]+)/([A-Za-z0-9-]+)',name)
                if not m: line(w,f':huddle 403 {nick} {name} :No such channel'); continue
                ws=core.get_workspace(m.group(1)); ch=None
                if ws:
                    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],m.group(2))).fetchone(); c.close()
                u=core.user_for_token(token)
                if not ws or not ch or not u:
                    line(w,f':huddle 403 {nick} {name} :No such channel'); continue
                try:
                    with core.tx() as c:
                        if core.member_role(ws['id'],u['id']) is None:core.add_workspace_member(c,ws['id'],u['id'])
                        core.add_channel_member(c,ch['id'],u['id'])
                except: line(w,f':huddle 403 {nick} {name} :No such channel'); continue
                joined[ch['id']]=name
                c=core.connect(); names=[x[0] for x in c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],))]; c.close()
                line(w,f':{nick}!{user}@huddle JOIN {name}')
                line(w,f':huddle 353 {nick} = {name} :{" ".join(names)}')
                line(w,f':huddle 366 {nick} {name} :End of /NAMES list')
            else:
                line(w,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
            if not registered and nick and user:
                if not core.user_for_token(token):
                    line(w,f':huddle 464 {nick} :Password incorrect'); break
                registered=True
                for n,t in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle'),('005','CHANTYPES=#')]:
                    line(w,f':huddle {n} {nick} :{t}')
                line(w,f':huddle 422 {nick} :MOTD File is missing')
    finally:
        if nick:NICKS.discard(nick)
        CONNS.discard(w); w.close()
        try: await w.wait_closed()
        except: pass
async def deliver():
    seen={}
    while True:
        await asyncio.sleep(.05)
        ids=set()
        for info in INFO.values():
            ids.update(info.get('joined',{}).keys())
        for cid in ids:
            last=seen.get(cid,0)
            events=core.events_after(cid,last)
            for ev in events:
                seen[cid]=ev['event_id']
                if ev.get('type')!='message.created': continue
                m=ev.get('message') or {}
                c=core.connect(); au=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone(); c.close()
                for other,info in list(INFO.items()):
                    name=info.get('joined',{}).get(cid)
                    if name and ORIGIN.get((cid,m.get('id'))) is not other:
                        try:
                            line(other,f':{au[0]}!{au[0]}@huddle PRIVMSG {name} :{m.get("body","")}')
                            await other.drain()
                        except: pass

async def main():
    core.init_db(); srv=await asyncio.start_server(handle,'0.0.0.0',6667)
    asyncio.create_task(deliver()); await srv.serve_forever()
if __name__=='__main__': asyncio.run(main())
