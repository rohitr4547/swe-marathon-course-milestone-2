import asyncio, re
import core

clients={}
channels={}

def line(code,nick,text):
 return f":huddle {code} {nick} {text}\r\n"

async def handle(reader,writer):
 nick=None; user=None; token=None; registered=False; joined=set(); sent={}; delivery=None
 async def send(x):
  writer.write(x.encode()); await writer.drain()
 async def register():
  nonlocal registered
  if registered:return
  if not nick or user is None:return
  if not core.token_user(token or ''):
   await send(line('464',nick or '*',':Password incorrect'))
   writer.close(); await writer.wait_closed(); return
  registered=True
  for code,text in [('001',':Welcome to Huddle'),('002',':Your host is huddle'),('003',':This server was created today'),('004','huddle 1.0 aiohttp'),('005',':CHANTYPES=#') ,('422',':MOTD File is missing')]:
   await send(line(code,nick,text))
 async def deliver():
  while True:
   await asyncio.sleep(0.05)
   if not registered: continue
   try:
    c=core.connect()
    for cid in list(joined):
     last=sent.get(cid,0)
     rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,last)).fetchall()
     for row in rows:
      sent[cid]=row['event_id']
      frame=__import__('json').loads(row['payload'])
      if frame.get('type')!='message.created': continue
      msg=frame.get('message') or {}
      if msg.get('author_id')==core.token_user(token)['id']: continue
      au=c.execute('SELECT username FROM users WHERE id=?',(msg.get('author_id'),)).fetchone()
      ch=c.execute('SELECT ch.name,w.slug FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE ch.id=?',(cid,)).fetchone()
      if au and ch:
       target=f"#{ch['slug']}/{ch['name']}"
       for peer in list(channels.get(cid,set())):
        if peer is writer:
         peer.write(f":{au['username']}!{au['username']}@huddle PRIVMSG {target} :{msg.get('body','')}\r\n".encode())
         await peer.drain()
    c.close()
   except Exception:
    pass
 try:
  while True:
   if delivery is None and registered: delivery=asyncio.create_task(deliver())
   raw=await reader.readline()
   if not raw:break
   text=raw.decode(errors='replace').strip('\r\n')
   if not text:continue
   parts=text.split(' ',2); cmd=parts[0].upper(); rest=parts[1:]
   if cmd=='PASS' and rest: token=rest[0]
   elif cmd=='NICK' and rest:
    new=rest[0]
    if new in clients and clients[new] is not writer:
     await send(f":huddle 433 * {new} :Nickname is already in use\r\n");continue
    if nick in clients: clients.pop(nick,None)
    nick=new;clients[nick]=writer;await register()
   elif cmd=='USER' and len(rest)>=1:
    user=rest[0];await register()
   elif cmd=='QUIT':
    break
   elif cmd=='PING':
    payload=text.split(':',1)[1] if ':' in text else (rest[0] if rest else '')
    await send(f":huddle PONG huddle :{payload}\r\n")
   elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
    continue
   elif cmd=='JOIN' and rest and registered:
    name=rest[0]
    if not re.fullmatch(r'#[a-z0-9-]+/[a-z0-9-]+',name):
     await send(line('403',nick,f'{name} :No such channel'));continue
    slug,cname=name[1:].split('/',1);c=core.connect()
    ch=c.execute('SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(slug,cname)).fetchone()
    u=core.token_user(token)
    if not ch or not u:
     await send(line('403',nick,f'{name} :No such channel'));continue
    c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
    joined.add(ch['id']); channels.setdefault(ch['id'],set()).add(writer)
    await send(f":{nick}!{user}@huddle JOIN {name}\r\n")
    rows=c.execute('SELECT u.username FROM users u JOIN channel_members cm ON cm.user_id=u.id WHERE cm.channel_id=?',(ch['id'],)).fetchall()
    await send(line('353',nick,f'= {name} :{" ".join(x[0] for x in rows)}'))
    await send(line('366',nick,f'{name} :End of /NAMES list'))
   elif cmd=='PRIVMSG' and len(rest)>=2 and registered:
    target=rest[0];body=rest[1]
    if body.startswith(':'):body=body[1:]
    if not target.startswith('#'):continue
    slug,cname=target[1:].split('/',1);c=core.connect();ch=c.execute('SELECT ch.* FROM channels ch JOIN workspaces w ON w.id=ch.workspace_id WHERE w.slug=? AND ch.name=?',(slug,cname)).fetchone();u=core.token_user(token)
    if ch and u:
     c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(ch['id'],u['id'],body,core.now()));m=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone();core.emit(c,ch['id'],'message.created',{'message':dict(m)})
   elif registered:
    await send(line('421',nick,f'{cmd} :Unknown command'))
 finally:
  if delivery: delivery.cancel()
  if nick and clients.get(nick) is writer:clients.pop(nick,None)
  for s in channels.values():s.discard(writer)
  writer.close()
  try: await writer.wait_closed()
  except: pass

async def main():
 server=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with server:await server.serve_forever()
if __name__=='__main__':asyncio.run(main())
