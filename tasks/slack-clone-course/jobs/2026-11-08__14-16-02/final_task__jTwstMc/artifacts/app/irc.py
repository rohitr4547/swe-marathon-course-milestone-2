import asyncio, core, json
clients=set()
async def send(w,line):
    w.write((line+'\r\n').encode()); await w.drain()
async def handle(r,w):
    nick=None; user=None; token=None; registered=False; joined={}; last={}
    clients.add(w)
    async def deliver():
        while True:
            await asyncio.sleep(.05)
            if not registered: continue
            c=core.connect()
            for ch,target in list(joined.items()):
                n=last.get(ch,0)
                rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(ch,n)).fetchall()
                for row in rows:
                    last[ch]=row['event_id']
                    frame=json.loads(row['payload'])
                    if frame.get('type') not in ('message.created','message.reply'): continue
                    msg=frame.get('message') or {}; author=msg.get('author') or {}
                    if msg.get('author_id')==core.user_for_token(token)['id'] and frame.get('_irc_origin'): continue
                    await send(w,f":{author.get('username','user')}!{author.get('username','user')}@huddle PRIVMSG {target} :{msg.get('body','')}")
            c.close()
    task=asyncio.create_task(deliver())
    try:
        while True:
            raw=await r.readline()
            if not raw: break
            line=raw.decode(errors='ignore').rstrip('\r\n')
            if not line: continue
            parts=line.split(' ',1); cmd=parts[0].upper(); arg=parts[1] if len(parts)>1 else ''
            if cmd=='PASS': token=arg.lstrip(':')
            elif cmd=='NICK':
                candidate=arg.split()[0] if arg else ''
                if any(candidate==getattr(x,'_huddle_nick',None) for x in clients if x is not w):
                    await send(w,f':huddle 433 * {candidate} :Nickname is already in use'); continue
                nick=candidate; setattr(w,'_huddle_nick',nick)
            elif cmd=='USER': user=arg.split()[0] if arg else ''
            elif cmd=='PING': await send(w,':huddle PONG huddle '+arg)
            elif cmd=='QUIT': break
            elif cmd=='JOIN':
                target=arg.split()[0] if arg else ''; ok=False
                if registered and target.startswith('#') and '/' in target[1:]:
                    slug,name=target[1:].split('/',1); ws=core.get_workspace(slug)
                    if ws:
                        cc=core.connect(); ch=cc.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],name)).fetchone();cc.close()
                        if ch:
                            u=core.user_for_token(token)
                            with core.tx() as c:
                                core.add_workspace_member(c,ws['id'],u['id'],'member');core.add_channel_member(c,ch['id'],u['id'])
                            joined[ch['id']]=target;last[ch['id']]=ch['last_event_id']
                            cc=core.connect(); names=[x['username'] for x in cc.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall()];cc.close()
                            await send(w,f':{nick}!{user}@huddle JOIN {target}');await send(w,f':huddle 353 {nick} = {target} :{" ".join(names)}');await send(w,f':huddle 366 {nick} {target} :End of /NAMES list');ok=True
                if not ok: await send(w,f':huddle 403 {nick or "*"} {target} :No such channel')
            elif cmd=='PRIVMSG' and registered:
                target,_,text=arg.partition(' :'); text=text or target.partition(' ')[2]; target=target.split()[0]
                ch=next((x for x,t in joined.items() if t==target),None)
                if ch and text:
                    u=core.user_for_token(token)
                    with core.tx() as c:
                        m=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at,mentions) VALUES(?,?,?,?,?)',(ch,u['id'],text,core.now_iso(),'[]'))
                        row=c.execute('SELECT * FROM messages WHERE id=?',(m.lastrowid,)).fetchone()
                        core.emit_event(c,ch,'message.created',{'message':core.message_json(c,row),'_irc_origin':True})
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'): pass
            else: await send(w,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
            if not registered and nick and user:
                u=core.user_for_token(token)
                if not u: await send(w,f':huddle 464 {nick} :Password incorrect');break
                registered=True
                for code,text in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle'),('005','CASEMAPPING=rfc1459')]: await send(w,f':huddle {code} {nick} :{text}')
                await send(w,f':huddle 422 {nick} :MOTD File is missing')
    finally:
        task.cancel();clients.discard(w);w.close()
        try: await w.wait_closed()
        except: pass
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s: await s.serve_forever()
if __name__=='__main__': asyncio.run(main())
