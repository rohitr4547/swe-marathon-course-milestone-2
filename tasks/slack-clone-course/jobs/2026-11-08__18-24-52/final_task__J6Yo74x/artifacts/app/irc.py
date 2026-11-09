import asyncio, core, json
clients=set()
async def send(w,line): w.write((line+'\r\n').encode()); await w.drain()
async def deliver(w,joined,lasts,nick,uid):
    while not w.is_closing():
        try:
            for cid, name in list(joined.items()):
                c=core.connect(); rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,lasts.get(cid,0))).fetchall(); c.close()
                for r in rows:
                    lasts[cid]=r['event_id']; f=json.loads(r['payload'])
                    if f.get('type')=='message.created':
                        m=f.get('message',{}) or {}; aid=m.get('author_id')
                        if aid==uid: continue
                        cc=core.connect(); ar=cc.execute('SELECT username FROM users WHERE id=?',(aid,)).fetchone(); cc.close()
                        an=ar['username'] if ar else nick
                        await send(w,f':{an}!{an}@localhost PRIVMSG {name} :{m.get("body","")}')
            await asyncio.sleep(.05)
        except: return
async def handle(r,w):
    clients.add(w); token=None; nick=None; user=None; registered=False; joined={}; lasts={}; task=None; uid=None
    try:
        while True:
            raw=await r.readline()
            if not raw: break
            line=raw.decode(errors='ignore').rstrip('\r\n'); parts=line.split(' ',1); cmd=parts[0].upper(); arg=parts[1] if len(parts)>1 else ''
            if cmd=='PASS': token=arg.strip()
            elif cmd=='NICK':
                n=arg.strip()
                if any(getattr(x,'_irc_nick',None)==n and x is not w for x in clients): await send(w,':huddle 433 * '+n+' :Nickname is already in use'); continue
                nick=n; w._irc_nick=n
            elif cmd=='USER': user=arg
            elif cmd=='PING': await send(w,':huddle PONG huddle '+arg.lstrip(':'))
            elif cmd=='PONG': pass
            elif cmd=='QUIT': break
            if not registered and token and nick and user:
                u=core.user_for_token(token)
                if not u: await send(w,':huddle 464 * :Password incorrect'); token=None; continue
                registered=True; uid=u['id']
                for n in ('001','002','003','004','005'): await send(w,f':huddle {n} {nick} :huddle')
                await send(w,f':huddle 422 {nick} :MOTD File is missing'); task=asyncio.create_task(deliver(w,joined,lasts,nick,uid))
            elif registered and cmd=='JOIN':
                name=arg.strip().split(',')[0]
                if not name.startswith('#') or '/' not in name: await send(w,f':huddle 403 {nick} {name} :No such channel'); continue
                slug,chname=name[1:].split('/',1); ws=core.get_workspace(slug); c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],chname)).fetchone() if ws else None; c.close()
                if not ch: await send(w,f':huddle 403 {nick} {name} :No such channel'); continue
                joined[ch['id']]=name; lasts[ch['id']]=ch['last_event_id']; await send(w,f':{nick}!{nick}@localhost JOIN {name}'); await send(w,f':huddle 353 {nick} = {name} :{nick}'); await send(w,f':huddle 366 {nick} {name} :End of NAMES list')
            elif registered and cmd=='PRIVMSG':
                try:
                    target,body=arg.split(' :',1); cid=next(k for k,v in joined.items() if v==target)
                    with core.tx() as c:
                        c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(cid,core.user_for_token(token)['id'],body,core.now_iso()))
                        m=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone()
                        core.emit_event(c,cid,'message.created',{'message':{'id':m['id'],'channel_id':cid,'author_id':m['author_id'],'body':body,'parent_id':None,'created_at':m['created_at'],'mentions':[]}})
                except: pass
            elif registered and cmd in ('NAMES','WHO','LIST','TOPIC','MODE'): pass
            elif registered and cmd not in ('PASS','NICK','USER','PING','PONG','QUIT','JOIN'): await send(w,f':huddle 421 {nick} {cmd} :Unknown command')
    finally:
        if task: task.cancel()
        clients.discard(w); w.close()
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s: await s.serve_forever()
if __name__=='__main__': asyncio.run(main())
