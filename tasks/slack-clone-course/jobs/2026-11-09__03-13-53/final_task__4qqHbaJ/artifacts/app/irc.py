import asyncio,core,re
nicks=set()
async def send(w,line):
 try:w.write((line+'\r\n').encode());await w.drain()
 except:pass
def channel_for(name):
 m=re.match(r'^#([^/]+)/([^/]+)$',name or '')
 if not m:return None
 ws=core.get_workspace(m.group(1))
 if not ws:return None
 c=core.connect();x=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],m.group(2))).fetchone();c.close()
 return x
async def register(w,nick):
 for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1 2'),('005','CHANTYPES=# PREFIX=(o)@')]:
  await send(w,f':huddle {code} {nick} :{text}')
 await send(w,f':huddle 422 {nick} :MOTD File is missing')
async def deliver(w,nick,joined,last_sent,skip):
 while not w.is_closing():
  for name,ch in list(joined.items()):
   for e in core.events_after(ch['id'],last_sent.get(ch['id'],0)):
    last_sent[ch['id']]=e['event_id']
    if e.get('type') not in ('message.created','message.reply'):continue
    m=e.get('message',{})
    if m.get('id')==skip.get(ch['id']):continue
    a=m.get('author') or {}; un=a.get('username','huddle')
    await send(w,f":{un}!{un}@huddle PRIVMSG {name} :{m.get('body','')}")
  await asyncio.sleep(.05)
async def handle(r,w):
 nick=None;token=None;user=None;registered=False;user_seen=False;joined={};last_sent={};skip={}
 task=None
 try:
  while True:
   raw=await r.readline()
   if not raw:break
   line=raw.decode(errors='ignore').rstrip('\r\n')
   if not line:continue
   p=line.split(' ',2);cmd=p[0].upper()
   if cmd=='PASS':
    token=p[1] if len(p)>1 else '';user=core.user_for_token(token)
    if not user:await send(w,':huddle 464 * :Password incorrect');token=None
   elif cmd=='NICK':
    want=p[1] if len(p)>1 else ''
    if want in nicks and want!=nick:await send(w,f':huddle 433 * {want} :Nickname is already in use');continue
    if nick:nicks.discard(nick)
    nick=want;nicks.add(nick)
    if token and user_seen and not registered:registered=True;await register(w,nick);task=asyncio.create_task(deliver(w,nick,joined,last_sent,skip))
   elif cmd=='USER':
    user_seen=True
    if token and nick and not registered:registered=True;await register(w,nick);task=asyncio.create_task(deliver(w,nick,joined,last_sent,skip))
   elif cmd=='PING':await send(w,':huddle PONG huddle '+(p[1] if len(p)>1 else ''))
   elif cmd in ('PONG','WHO','LIST','TOPIC','MODE'):pass
   elif cmd=='NAMES':
    if len(p)>1:await send(w,f':huddle 353 {nick} = {p[1]} :{nick}');await send(w,f':huddle 366 {nick} {p[1]} :End of /NAMES list.')
   elif cmd=='JOIN':
    name=p[1] if len(p)>1 else '';ch=channel_for(name)
    if not ch:await send(w,f':huddle 403 {nick} {name} :No such channel');continue
    joined[name]=ch;last_sent[ch['id']]=core.head_event_id(ch['id'])
    await send(w,f':{nick}!{user["username"] if user else "user"}@huddle JOIN {name}')
    await send(w,f':huddle 353 {nick} = {name} :{nick}');await send(w,f':huddle 366 {nick} {name} :End of /NAMES list')
   elif cmd=='PRIVMSG':
    name=p[1] if len(p)>1 else '';body=p[2] if len(p)>2 else ''
    if body.startswith(':'):body=body[1:]
    ch=joined.get(name)
    if ch and user and body:
     m=core.create_message(user,ch['id'],body);skip[ch['id']]=m['id']
   elif cmd=='QUIT':break
   else:await send(w,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
 finally:
  if task:task.cancel()
  if nick:nicks.discard(nick)
  w.close();await w.wait_closed()
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s:await s.serve_forever()
asyncio.run(main())
