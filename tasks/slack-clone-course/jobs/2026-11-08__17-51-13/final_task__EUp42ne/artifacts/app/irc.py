import asyncio,core,json
NICKS=set(); CLIENTS=[]; SENT=set(); SEEN={}
def parse_chan(name):
    if not name.startswith('#') or '/' not in name[1:]: return None,None
    slug,cname=name[1:].split('/',1); w=core.get_workspace(slug)
    if not w:return None,None
    c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone(); c.close()
    return w,ch
async def handle(r,w):
    token=None; nick=None; user=None; registered=False; claimed=False; joined={}
    async def send(x): w.write((x+'\r\n').encode()); await w.drain()
    CLIENTS.append((w,joined))
    try:
        while not r.at_eof():
            line=(await r.readline()).decode(errors='ignore').strip()
            if not line: continue
            p=line.split(); cmd=p[0].upper()
            if cmd=='PASS' and len(p)>1: token=p[1]
            elif cmd=='NICK' and len(p)>1:
                if p[1] in NICKS and p[1]!=nick: await send(':huddle 433 * '+p[1]+' :Nickname in use')
                else: nick=p[1]
            elif cmd=='USER' and len(p)>1:
                user=p[1]; u=core.user_for_token(token)
                if not u: await send(':huddle 464 * :Password incorrect')
                else:
                    registered=True; NICKS.add(nick)
                    for n in range(1,6): await send(f':huddle 00{n} {nick} :Huddle')
                    await send(':huddle 422 '+nick+' :MOTD File is missing')
            elif cmd=='PING': await send('PONG :'+(p[1] if len(p)>1 else 'huddle'))
            elif cmd=='QUIT': break
            elif cmd=='JOIN' and len(p)>1:
                name=p[1]; ws,ch=parse_chan(name)
                if not ch: await send(':huddle 403 '+(nick or '*')+' '+name+' :No such channel')
                else:
                    joined[ch['id']]=name; SEEN[(id(w),ch['id'])]=core.head_event_id(ch['id'])
                    await send(':'+nick+'!irc@localhost JOIN '+name); await send(':huddle 353 '+nick+' = '+name+' :'+nick); await send(':huddle 366 '+nick+' '+name+' :End of /NAMES list.')
            elif cmd=='PRIVMSG' and len(p)>2:
                name=p[1]; body=line.split(' :',1)[1] if ' :' in line else ' '.join(p[2:])
                ws,ch=parse_chan(name)
                if ch:
                    u=core.user_for_token(token)
                    with core.tx() as c:
                        x=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(ch['id'],u['id'],body,core.now_iso()))
                        rmsg=c.execute('SELECT * FROM messages WHERE id=?',(x.lastrowid,)).fetchone()
                        SENT.add((id(w),rmsg['id']))
                        core.emit_event(c,ch['id'],'message.created',{'message':{'id':rmsg['id'],'channel_id':ch['id'],'author_id':u['id'],'body':body,'parent_id':None,'reply_count':0,'created_at':rmsg['created_at'],'edited_at':None,'files':[],'reactions':[],'mentions':[]}})
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'): pass
            elif cmd not in ('PASS','USER','NICK'): await send(':huddle 421 '+(nick or '*')+' '+cmd+' :Unknown command')
    finally:
        NICKS.discard(nick); SENT_copy=[x for x in SENT if x[0]==id(w)]; [SENT.discard(x) for x in SENT_copy]; CLIENTS[:]=[x for x in CLIENTS if x[0] is not w]; w.close(); await w.wait_closed()
async def broadcast():
    seen={}
    while True:
        for w,joined in list(CLIENTS):
            try:
                for cid,name in joined.items():
                    for ev in core.events_after(cid,SEEN.get((id(w),cid),0)):
                        SEEN[(id(w),cid)]=ev['event_id']
                        if ev['type']=='message.created':
                            m=ev.get('message',{});
                            if (id(w),m.get('id')) in SENT:
                                SENT.discard((id(w),m.get('id')))
                                continue
                            c=core.connect(); u=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone(); c.close()
                            if u and w in [x[0] for x in CLIENTS]: w.write((f":{u['username']}!{u['username']}@localhost PRIVMSG {name} :{m.get('body','')}\r\n").encode()); await w.drain()
            except: pass
        await asyncio.sleep(.05)
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667); asyncio.create_task(broadcast())
    async with s: await s.serve_forever()
asyncio.run(main())
