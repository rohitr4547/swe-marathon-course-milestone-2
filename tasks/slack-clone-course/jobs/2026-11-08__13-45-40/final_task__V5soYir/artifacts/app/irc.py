import asyncio, core, time
clients=[]
nicks=set()
async def send(w,line):
 try: w.write((line+'\r\n').encode()); await w.drain()
 except Exception as e: print('IRC SEND',repr(e),flush=True)
async def handle(r,w):
 peer=w.get_extra_info('peername'); user=None; nick=None; passed=None; joined=set(); registered=False; user_seen=False
 rec={'w':w,'joined':joined,'nick':None,'user':None,'seen':{}}; clients.append(rec)
 async def numeric(code,text):
  await send(w,':huddle %s %s %s'%(code,nick or '*',text))
 async def deliver():
  while True:
   await asyncio.sleep(.05)
   for ch in list(joined):
    last=rec['seen'].get(ch,0)
    for ev in core.events_after(ch,last):
     rec['seen'][ch]=ev['event_id']
     if ev['payload'].find('message.created') >= 0:
      try:
       obj=__import__('json').loads(ev['payload']); m=obj.get('message') or {}
       author=(m.get('author') or {}).get('username','user')
       if author != nick:
        cc=core.get_channel(ch); ww=core.get_workspace(core.connect().execute('SELECT slug FROM workspaces WHERE id=?',(cc['workspace_id'],)).fetchone()['slug'])
        await send(w,':%s!%s@localhost PRIVMSG #%s/%s :%s'%(author,author,ww['slug'],cc['name'],m.get('body','')))
      except Exception: pass

 deliver_task=asyncio.create_task(deliver())
 try:
  while True:
   raw=await r.readline()
   if not raw: break
   line=raw.decode(errors='replace').rstrip('\r\n')
   if not line: continue
   parts=line.split(' ',2); cmd=parts[0].upper(); rest=parts[1:]
   if cmd=='PASS':
    token=rest[0] if rest else ''
    user=core.user_for_token(token)
    if not user: await numeric('464','Password incorrect'); continue
    passed=True
   elif cmd=='NICK':
    new=rest[0] if rest else ''
    collision=new in nicks and new != nick
    if collision: await numeric('433',new+' Nickname is already in use'); continue
    if nick: nicks.discard(nick)
    nick=new; rec['nick']=nick; rec['user']=user; nicks.add(nick)
    if passed and user_seen and not registered:
     registered=True
     for code,text in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle 1.0 oi'),('005','CHANTYPES=# :are supported'),('422','MOTD File is missing')]: await numeric(code,text)
   elif cmd=='USER':
    user_seen=True
    if passed and nick and not registered:
     registered=True
     for code,text in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle 1.0 oi'),('005','CHANTYPES=# :are supported'),('422','MOTD File is missing')]: await numeric(code,text)
   elif cmd=='PING':
    await send(w,':huddle PONG huddle '+(line.split(':',1)[1] if ':' in line else (rest[0] if rest else '')))
   elif cmd=='PONG': pass
   elif cmd=='QUIT': break
   elif cmd in ('JOIN','NAMES','WHO','TOPIC','MODE'):
    target=rest[0].lstrip(':') if rest else ''
    if cmd=='JOIN':
     if not target.startswith('#') or '/' not in target: await numeric('403',target+' No such channel'); continue
     slug,name=target[1:].split('/',1); ws=core.get_workspace(slug); ch=None
     if ws:
      c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],name)).fetchone(); c.close()
     if not ch: await numeric('403',target+' No such channel'); continue
     joined.add(ch['id']); await send(w,':%s!%s@localhost JOIN %s'%(nick,nick,target))
     await numeric('353','= '+target+' :'+(nick or '')); await numeric('366',target+' End of /NAMES list')
    elif cmd=='NAMES': await numeric('366',target+' End of /NAMES list')
   elif cmd=='LIST': await numeric('323','End of /LIST')
   elif cmd=='PRIVMSG':
    if len(rest)<2: continue
    target=rest[0]; body=rest[1].lstrip(':')
    if target.startswith('#') and '/' in target:
     slug,name=target[1:].split('/',1); ws=core.get_workspace(slug); ch=None
     if ws:
      c=core.connect(); ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ws['id'],name)).fetchone(); c.close()
     if ch and user:
      try: core.create_message(user,ch['id'],body)
      except: pass
   else:
    await numeric('421',cmd+' Unknown command')
 except Exception as e:
  print('IRC HANDLER',repr(e),flush=True)
 finally:
  deliver_task.cancel()
  clients_copy=[x for x in clients if x['w'] is w]
  for x in clients_copy: clients.remove(x)
  if nick: nicks.discard(nick)
  try:w.close(); await w.wait_closed()
  except:pass
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s: await s.serve_forever()
asyncio.run(main())
