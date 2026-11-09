import asyncio, core
NICKS=set()
CONNS=set()
LOCK=asyncio.Lock()
async def send(w,line):
 w.write((line+'\r\n').encode()); await w.drain()
async def handle(reader,writer):
 nick=None; token=None; registered=False; user_seen=False; joined={}; seen={}; CONNS.add((writer,lambda: nick))
 peer=writer.get_extra_info('peername'); host='huddle'
 try:
  while True:
   for cid,last in list(seen.items()):
    c=core.connect(); ev=c.execute('select payload from events where channel_id=? and event_id>? order by event_id',(cid,last)).fetchall(); c.close()
    for e in ev:
     frame=__import__('json').loads(e['payload']); seen[cid]=frame['event_id']
     if frame['type'] in ('message.created','message.reply'):
      mm=frame['message']
      db=core.connect(); author=db.execute('select username from users where id=?',(mm['author_id'],)).fetchone(); db.close()
      anick=author['username'] if author else 'huddle'
      for ow,getnick in list(CONNS):
       om=getattr(ow,'_huddle_joined',{})
       for oname,ocid in om.items():
        if ocid==cid:
         await send(ow,f':{anick}!{anick}@huddle PRIVMSG {oname} :{mm["body"]}')
   try: raw=await asyncio.wait_for(reader.readline(),0.1)
   except asyncio.TimeoutError: continue
   if not raw: break
   line=raw.decode(errors='replace').rstrip('\r\n')
   if not line: continue
   parts=line.split(' ',1); cmd=parts[0].upper(); rest=parts[1] if len(parts)>1 else ''
   args=rest.split()
   if cmd=='PASS':
    token=rest.strip().lstrip(':')
   elif cmd=='NICK':
    proposed=args[0] if args else ''
    async with LOCK:
     if proposed in NICKS and proposed!=nick:
      await send(writer,f':huddle 433 * {proposed} :Nickname is already in use'); continue
     if nick: NICKS.discard(nick)
     nick=proposed; NICKS.add(nick)
    if user_seen and not registered and nick:
     u=core.user_for_token(token)
     if not u:
      await send(writer,f':huddle 464 {nick} :Password incorrect'); return
     registered=True
     for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1.0 oi'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]:
      await send(writer,f':huddle {code} {nick} :{text}')
   elif cmd=='USER':
    user_seen=True
    if not registered and nick:
     u=core.user_for_token(token)
     if not u:
      await send(writer,f':huddle 464 {nick or "*"} :Password incorrect'); return
     registered=True
     for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1.0 oi'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]:
      await send(writer,f':huddle {code} {nick} :{text}')
   elif cmd=='PING':
    value=rest.strip()
    if value.startswith(':'): value=value[1:]
    await send(writer,f':huddle PONG huddle :{value}')
   elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
    if cmd=='NAMES' and args:
     name=args[0]; await send(writer,f':huddle 353 {nick} = {name} :{nick}'); await send(writer,f':huddle 366 {nick} {name} :End of /NAMES list')
   elif cmd=='JOIN':
    name=args[0] if args else ''
    ok=False; ch=None
    if name.startswith('#') and '/' in name:
     slug,cname=name[1:].split('/',1); w=core.get_workspace(slug)
     if w:
      c=core.connect(); ch=c.execute('select * from channels where workspace_id=? and name=?',(w['id'],cname)).fetchone()
      if ch:
       u=core.user_for_token(token)
       if u:
        c.execute('insert or ignore into members values(?,?,?)',(w['id'],u['id'],'member'))
        c.execute('insert or ignore into channel_members values(?,?)',(ch['id'],u['id'])); c.commit(); ok=True
      c.close()
    if not ok: await send(writer,f':huddle 403 {nick or "*"} {name} :No such channel')
    else:
     joined[name]=ch['id']; writer._huddle_joined=joined; seen[ch['id']]=ch['last_event_id'] if 'last_event_id' in ch.keys() else 0; await send(writer,f':{nick}!{nick}@{host} JOIN {name}')
     c=core.connect(); rows=c.execute('select u.username from users u join channel_members m on m.user_id=u.id where m.channel_id=?',(ch['id'],)).fetchall();c.close()
     names=' '.join(x['username'] for x in rows)
     await send(writer,f':huddle 353 {nick} = {name} :{names}'); await send(writer,f':huddle 366 {nick} {name} :End of /NAMES list')
   elif cmd=='PRIVMSG':
    if len(args)>=2:
     target=args[0]; text=rest.split(' :',1)[1] if ' :' in rest else ' '.join(args[1:])
     cid=joined.get(target)
     u=core.user_for_token(token)
     if cid and u:
      m=core.create_message(u['id'],cid,text)
      cc=core.get_channel(cid); seen[cid]=cc['last_event_id'] if cc else seen.get(cid,0)
      await asyncio.sleep(0)
      for ow,getnick in list(CONNS):
       if ow is not writer and target in getattr(ow,'_huddle_joined',{}):
        await send(ow,f':{nick}!{u["username"]}@huddle PRIVMSG {target} :{text}')

   elif cmd=='QUIT':
    return
   else:
    await send(writer,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
 finally:
  CONNS.difference_update({x for x in CONNS if x[0] is writer})
  if nick:
   async with LOCK:NICKS.discard(nick)
  writer.close()
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s: await s.serve_forever()
if __name__=='__main__': asyncio.run(main())
