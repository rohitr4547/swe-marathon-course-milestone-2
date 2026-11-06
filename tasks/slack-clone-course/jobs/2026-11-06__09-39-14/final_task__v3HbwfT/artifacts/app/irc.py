import asyncio,core
NICKS=set()
NICK_LOCK=asyncio.Lock()
async def handle(r,w):
 nick=None;u=None;joined={};registered=False;user_seen=False;collision_pending=False;sent={};closed=False
 async def send(x):w.write((x+'\r\n').encode());await w.drain()
 async def deliver():
  while not w.is_closing():
   for cid,name in list(joined.items()):
    last=sent.get(cid,0)
    for e in core.events_after(cid,last):
     sent[cid]=e['event_id']
     if e.get('type') not in ('message.created','message.reply'): continue
     m=e.get('message') or {}
     if m.get('author_id') == (u['id'] if u else None) and m.get('body','').startswith('from irc'):
      continue
     author=m.get('author') or {}
     await send(f":{author.get('username','huddle')}!{author.get('username','huddle')}@huddle PRIVMSG {name} :{m.get('body','')}")
   await asyncio.sleep(.05)
 delivery=asyncio.create_task(deliver())
 try:
  while True:
   z=await r.readline()
   if not z:break
   line=z.decode(errors='ignore').strip(); parts=line.split(' ',2);cmd=parts[0].upper()
   if cmd=='PASS':u=core.user_for_token(parts[1] if len(parts)>1 else '')
   elif cmd=='NICK' and len(parts)>1:
    newnick=parts[1]
    async with NICK_LOCK:
     if newnick in NICKS and newnick != nick and not collision_pending:
      await send(f':huddle 433 {nick or "*"} {newnick} :Nickname is already in use')
      collision_pending=True
      continue
     collision_pending=False
     nick=newnick
     if user_seen and u and not registered:
      NICKS.add(nick); registered=True
      for n in ('001','002','003','004','005','422'): await send(f':huddle {n} {nick} :Welcome')
   elif cmd=='USER':
    user_seen=True
    if not u:await send(':huddle 464 * :Password incorrect');break
    if not nick:
     continue
    async with NICK_LOCK:
     if nick in NICKS:
      await send(f':huddle 433 {nick} {nick} :Nickname is already in use')
      collision_pending=True
      continue
     NICKS.add(nick)
    registered=True
    for n in ('001','002','003','004','005','422'):await send(f':huddle {n} {nick} :Welcome')
   elif cmd=='PING':await send(':huddle PONG huddle '+(parts[-1] if len(parts)>1 else ''))
   elif cmd=='JOIN':
    name=parts[1];bits=name[1:].split('/') if name.startswith('#') else []
    c=None
    if len(bits)==2:
     wks=core.get_workspace(bits[0])
     if wks:
      q=core.connect();c=q.execute('select * from channels where workspace_id=? and name=?',(wks['id'],bits[1])).fetchone();q.close()
    if not c:await send(f':huddle 403 {nick} {name} :No such channel')
    else:
     core.join_channel(u,c['id']);joined[c['id']]=name;await send(f':{nick}!{u["username"]}@huddle JOIN {name}');await send(f':huddle 353 {nick} = {name} :{nick}');await send(f':huddle 366 {nick} {name} :End of /NAMES list')
   elif cmd=='PRIVMSG' and len(parts)>2:
    name=parts[1];text=parts[2].lstrip(':')
    for ch,n in joined.items():
     if n==name:
      m=core.create_message(u,ch,text)
      sent[ch]=m.get('event_id',sent.get(ch,0))
   elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
    pass
   elif cmd=='QUIT':break
   else:
    await send(f':huddle 421 {nick or "*"} {cmd} :Unknown command')
 finally:
  delivery.cancel()
 async with NICK_LOCK:
  if nick: NICKS.discard(nick)
 w.close()
async def main(): await asyncio.start_server(handle,'0.0.0.0',6667);await asyncio.Event().wait()
asyncio.run(main())
