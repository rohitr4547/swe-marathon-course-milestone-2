import asyncio, core, json, re
clients=set()
async def send(w,line):
 try:w.write((line+'\r\n').encode());await w.drain()
 except:pass
async def handle(reader,writer):
 clients.add(writer); token=None; user=None; nick=None; registered=False; joined={}; sent={}; closing=False
 async def numeric(code,text): await send(writer,f':huddle {code} {nick or "*"} {text}')
 async def deliver():
  while not writer.is_closing():
   for cid,name in list(joined.items()):
    since=sent.get(cid,0)
    for e in core.events_after(cid,since):
     sent[cid]=e['event_id']
     if e.get('type')=='message.created' and e.get('message',{}).get('author_id') != (user['id'] if user else -1):
      m=e.get('message',{})
      await send(writer,f':{m.get("author",{}).get("username","user")}!user@localhost PRIVMSG {name} :{m.get("body","")}')
   await asyncio.sleep(.05)
 task=asyncio.create_task(deliver())
 try:
  while True:
   raw=await reader.readline()
   if not raw:break
   line=raw.decode(errors='ignore').strip()
   if not line:continue
   parts=line.split(' ',2); cmd=parts[0].upper(); args=parts[1:]
   if cmd=='PASS':
    token=parts[1] if len(parts)>1 else ''
    user=core.user_for_token(token)
    if not user:await numeric('464','Password incorrect')
   elif cmd=='NICK':
    nn=parts[1] if len(parts)>1 else ''
    c=core.connect(); taken=c.execute('SELECT 1 FROM users WHERE username=?',(nn,)).fetchone();c.close()
    if nick and nn==nick:continue
    if taken and (not user or nn!=user['username']):await numeric('433',f'{nn} Nickname in use');continue
    nick=nn
    if user and not registered and nick: registered=True
   elif cmd=='USER':
    if not user:continue
    if not registered:registered=True
    for code,text in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1.0 oi'),('005','CHANTYPES=# PREFIX=(ov)@+'),('422','MOTD File is missing')]:await numeric(code,text)
   elif cmd=='PING':
    await send(writer,':huddle PONG '+(parts[2] if len(parts)>2 else (parts[1] if len(parts)>1 else '')))
   elif cmd=='PONG':pass
   elif cmd=='QUIT':break
   elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'): 
    if cmd=='NAMES' and len(parts)>1: await numeric('353',f'= {parts[1]} :{nick}')
   elif cmd=='JOIN':
    if not user or len(parts)<2:continue
    name=parts[1]; m=re.fullmatch(r'#([^/]+)/([a-z0-9-]+)',name)
    c=core.connect(); ch=None
    if m: ch=c.execute('SELECT c.*,w.slug FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE w.slug=? AND c.name=?',(m.group(1),m.group(2))).fetchone()
    c.close()
    if not ch:await numeric('403',f'{name} No such channel');continue
    joined[ch['id']]=name;sent[ch['id']]=core.head_event_id(ch['id']);await send(writer,f':{nick}!{user["username"]}@localhost JOIN {name}')
    await numeric('353',f'= {name} :{nick}');await numeric('366',f'{name} End of /NAMES list.')
   elif cmd=='PRIVMSG' and len(parts)>2:
    name=parts[1]; text=parts[2][1:] if parts[2].startswith(':') else parts[2]
    cid=None
    for k,v in joined.items():
     if v==name: cid=k
    if cid is not None and text:
     core.create_message(user,cid,text)
   elif cmd=='PRIVMSG': await numeric('412','No text to send')
   else:
    await numeric('421',f'{cmd} Unknown command')
 finally:
  task.cancel();clients.discard(writer);writer.close();await writer.wait_closed()
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s:await s.serve_forever()
asyncio.run(main())
