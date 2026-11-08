import asyncio,re,core,json
clients=set()
class Conn:
 def __init__(self,r,w):self.r=r;self.w=w;self.nick=None;self.user=None;self.token=None;self.channels=set();clients.add(self)
 async def send(self,s):self.w.write((s+'\r\n').encode());await self.w.drain()
async def handle(r,w):
 c=Conn(r,w)
 try:
  while line:=await r.readline():
   p=line.decode(errors='ignore').strip().split(' ',2);cmd=p[0].upper()
   if cmd=='PASS':c.token=p[1] if len(p)>1 else ''
   elif cmd=='NICK':
    c.nick=p[1] if len(p)>1 else 'guest';c.user=core.user_for_token(c.token)
    if not c.user:await c.send(':huddle 464 '+c.nick+' :Password incorrect');continue
    await c.send(':huddle 001 '+c.nick+' :Welcome');await c.send(':huddle 002 '+c.nick+' :Huddle');await c.send(':huddle 003 '+c.nick+' :Ready');await c.send(':huddle 004 '+c.nick+' huddle');await c.send(':huddle 005 '+c.nick+' CHANTYPES=#');await c.send(':huddle 422 '+c.nick+' :No MOTD')
   elif cmd=='PING':await c.send('PONG '+(p[1] if len(p)>1 else ''))
   elif cmd=='JOIN' and len(p)>1:
    name=p[1];q=name[1:].split('/',1) if name.startswith('#') else [];ch=None
    if len(q)==2:
     w=core.get_workspace(q[0]);cc=core.connect();ch=cc.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],q[1])).fetchone() if w else None;cc.close()
    if not ch:await c.send(':huddle 403 '+c.nick+' '+name+' :No such channel');continue
    c.channels.add(ch['id']);await c.send(':'+c.nick+'!user@host JOIN '+name);await c.send(':huddle 353 '+c.nick+' = '+name+' :'+c.nick);await c.send(':huddle 366 '+c.nick+' '+name+' :End of NAMES')
   elif cmd=='PRIVMSG' and len(p)>2:
    name=p[1];body=p[2].lstrip(':');q=name[1:].split('/',1);w=core.get_workspace(q[0]);cc=core.connect();ch=cc.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],q[1])).fetchone();cc.close()
    if ch:
     try:core.create_message(c.user,ch['id'],body)
     except:pass
   elif cmd=='QUIT':break
   elif cmd not in ('USER','NAMES','WHO','LIST','TOPIC','MODE'):await c.send(':huddle 421 '+(c.nick or '*')+' '+cmd+' :Unknown command')
 finally:clients.discard(c);w.close();await w.wait_closed()
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with s:
  await s.serve_forever()
asyncio.run(main())
