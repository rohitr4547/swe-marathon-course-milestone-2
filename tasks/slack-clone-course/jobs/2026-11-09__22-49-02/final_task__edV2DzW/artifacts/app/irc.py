import asyncio,re
import core
clients={}
reserved=set()
joined={}
origins={}
async def broadcaster():
 while True:
  await asyncio.sleep(.05)
  for w, chans in list(joined.items()):
   for cid in list(chans):
    last=chans[cid]
    c=core.connect();rows=c.execute("SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id",(cid,last)).fetchall();ch.close() if False else None
    c.close()
    for row in rows:
     chans[cid]=row['event_id']
     try:
      d=__import__('json').loads(row['payload'])
      if d.get('type') in ('message.created','message.reply'):
       m=d.get('message',{}); u=core.user_for_token('')
       cc=core.connect();ch=cc.execute('SELECT c.*,w.slug FROM channels c JOIN workspaces w ON c.workspace_id=w.id WHERE c.id=?',(cid,)).fetchone();au=cc.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone();cc.close()
       if ch and au:
        target='#'+ch['slug']+'/'+ch['name']
        for ow,ocs in list(joined.items()):
         if cid in ocs and origins.get((cid,d.get('event_id'))) is not ow:
          line(ow,f":{au['username']}!{au['username']}@huddle PRIVMSG {target} :{m.get('body','')}");await ow.drain()
     except Exception: pass

def line(w,s): w.write((s+'\r\n').encode())
async def handle(r,w):
 nick=None; username='user'; token=None; registered=False; user=None
 joined[w]={}
 try:
  while True:
   raw=await r.readline()
   if not raw: break
   p=raw.decode(errors='ignore').strip().split(' ',2)
   if not p: continue
   cmd=p[0].upper(); arg=p[1] if len(p)>1 else ''; rest=p[2] if len(p)>2 else ''
   if cmd=='PASS': token=arg
   elif cmd=='NICK':
    new=arg
    if new in reserved and clients.get(new) is not w:
     line(w,f':huddle 433 {nick or "*"} {new} :Nickname is already in use');await w.drain();continue
    nick=new
    if username and token and not registered:
     user=core.user_for_token(token)
     if not user:
      line(w,f':huddle 464 {nick} :Password incorrect');await w.drain();break
     clients[nick]=w;reserved.add(nick);registered=True
     for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle 1.0 oi'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]:
      line(w,f':huddle {code} {nick} :{text}')
   elif cmd=='USER':
    username=arg or username
    if nick and username and not registered:
     user=core.user_for_token(token)
     if not user:
      line(w,f':huddle 464 {nick} :Password incorrect');await w.drain();break
     if nick in reserved and clients.get(nick) is not w:
      line(w,f':huddle 433 {nick} {nick} :Nickname is already in use');await w.drain();continue
     clients[nick]=w;reserved.add(nick);registered=True
     for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle 1.0 oi'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]:
      line(w,f':huddle {code} {nick} :{text}')
   elif cmd=='PING':
    line(w,':huddle PONG '+(rest[1:] if rest.startswith(':') else arg))
   elif cmd=='PONG': pass
   elif cmd=='FOOBAR': line(w,f':huddle 421 {nick or "*"} FOOBAR :Unknown command')
   elif cmd=='QUIT': break
   elif cmd in ('NAMES','WHO','LIST','TOPIC','MODE'):
    if cmd=='NAMES':
     target=arg;line(w,f':huddle 353 {nick} = {target} :{username}')
     line(w,f':huddle 366 {nick} {target} :End of /NAMES list')
    elif cmd=='TOPIC': line(w,f':huddle 331 {nick} {arg} :No topic is set')
   elif cmd=='JOIN':
    if not registered:
     user=core.user_for_token(token); 
     if not user: line(w,f':huddle 464 {nick or "*"} :Password incorrect');await w.drain();break
     if nick in reserved and clients.get(nick) is not w:
      line(w,f':huddle 433 {nick} {nick} :Nickname is already in use');await w.drain();continue
     clients[nick]=w;reserved.add(nick);registered=True
     for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle 1.0 oi'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]:
      line(w,f':huddle {code} {nick} :{text}')
    target=arg
    ok=None
    if target.startswith('#') and '/' in target:
     slug,name=target[1:].split('/',1);ww=core.get_workspace(slug)
     if ww:
      c=core.connect();ok=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ww['id'],name)).fetchone();c.close()
    if not ok:
     line(w,f':huddle 403 {nick} {target} :No such channel')
    else:
     line(w,f':{nick}!{username}@huddle JOIN {target}')
     line(w,f':huddle 353 {nick} = {target} :{username}')
     line(w,f':huddle 366 {nick} {target} :End of /NAMES list')
     joined[w][ok['id']]=core.head_event_id(ok['id']) if hasattr(core,'head_event_id') else ok['last_event_id']
   elif cmd=='PRIVMSG':
    target=arg; body=rest[1:] if rest.startswith(':') else rest
    if registered and target.startswith('#') and '/' in target:
     slug,name=target[1:].split('/',1);ww=core.get_workspace(slug);ch=None
     if ww:
      cc=core.connect();ch=cc.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(ww['id'],name)).fetchone();cc.close()
     if ch:
      with core.tx() as cc:
       before=cc.execute('SELECT last_event_id FROM channels WHERE id=?',(ch['id'],)).fetchone()[0]
       core.addmsg(cc,ch['id'],user['id'],body)
       origins[(ch['id'],before+1)]=w
    else:
     line(w,f':huddle 404 {nick} {target} :No such channel')
    if not registered:
     user=core.user_for_token(token)
     if not user: line(w,f':huddle 464 {nick or "*"} :Password incorrect');await w.drain();break
     clients[nick]=w;reserved.add(nick);registered=True
     for code,text in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle 1.0 oi'),('005','CHANTYPES=# :are supported by this server'),('422','MOTD File is missing')]:line(w,f':huddle {code} {nick} :{text}')
   else:
    line(w,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
   await w.drain()
 finally:
  if nick and clients.get(nick) is w: del clients[nick]
  joined.pop(w,None)
  w.close();await w.wait_closed()
async def main():
 core.init_db()
 server=await asyncio.start_server(handle,'0.0.0.0',6667)
 asyncio.create_task(broadcaster())
 async with server: await server.serve_forever()
asyncio.run(main())
