import asyncio, json, time
import core
clients=set()
class Client:
    def __init__(self,r,w):
        self.r,self.w=r,w;self.user=None;self.nick=None;self.joined={};self.last={};self.ready=False
    async def send(self,line):
        try:self.w.write((line+'\r\n').encode());await self.w.drain()
        except:pass
def chan_for(name):
    if not name.startswith('#') or '/' not in name[1:]: return None
    slug,n=name[1:].split('/',1);w=core.get_workspace(slug)
    if not w:return None
    c=core.connect();r=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],n)).fetchone();c.close();return r
async def registration(c):
    if c.ready or not c.user or not c.nick:return
    c.ready=True
    for n,text in [('001','Welcome to Huddle IRC'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle'),('005','CHANTYPES=# :are supported by this server')]:
        await c.send(f':huddle {n} {c.nick} :{text}')
    await c.send(f':huddle 422 {c.nick} :MOTD File is missing')
async def deliver(c):
    while not c.w.is_closing():
        try:
            for cid in list(c.joined):
                last=c.last.get(cid,0)
                for f in core.events_after(cid,last):
                    c.last[cid]=f['event_id']
                    if f['type'] in ('message.created','message.reply'):
                        m=f.get('message') or {}; author=m.get('author') or {}
                        name=author.get('username','user')
                        channel=c.joined[cid]
                        await c.send(f':{name}!{author.get("id","u")}@huddle PRIVMSG {channel} :{m.get("body","")}')
            await asyncio.sleep(.05)
        except: break
async def handle(r,w):
    c=Client(r,w);clients.add(c);task=asyncio.create_task(deliver(c))
    try:
        while True:
            raw=await r.readline()
            if not raw:break
            line=raw.decode(errors='replace').strip()
            if not line:continue
            parts=line.split(' ',2);cmd=parts[0].upper();arg=parts[1] if len(parts)>1 else '';trail=parts[2][1:] if len(parts)>2 and parts[2].startswith(':') else (parts[2] if len(parts)>2 else '')
            if cmd=='PASS':
                c.user=core.user_for_token(arg); 
                if not c.user: await c.send(':huddle 464 * :Password incorrect')
            elif cmd=='NICK':
                if any(x.nick==arg for x in clients if x is not c and x.nick==arg):await c.send(f':huddle 433 * {arg} :Nickname is already in use')
                else:c.nick=arg
            elif cmd=='USER':
                if c.user is None and len(parts)>2:c.user=core.user_for_token('')
            elif cmd=='PING':await c.send(f':huddle PONG huddle :{trail or arg}')
            elif cmd=='PONG':pass
            elif cmd=='QUIT':break
            elif cmd=='JOIN':
                if not c.ready:await registration(c)
                ch=chan_for(arg)
                if not ch:await c.send(f':huddle 403 {c.nick} {arg} :No such channel')
                elif not c.user or not core.can_read_channel(c.user['id'],ch):
                    await c.send(f':huddle 403 {c.nick} {arg} :Cannot join channel')
                else:
                    c.joined[ch['id']]=arg;c.last[ch['id']]=core.head_event_id(ch['id'])
                    await c.send(f':{c.nick}!{c.user["username"]}@huddle JOIN {arg}')
                    cdb=core.connect();names=[x[0] for x in cdb.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],))];cdb.close()
                    await c.send(f':huddle 353 {c.nick} = {arg} :{" ".join(names)}');await c.send(f':huddle 366 {c.nick} {arg} :End of /NAMES list')
            elif cmd=='NAMES':
                ch=chan_for(arg)
                if ch:
                    cdb=core.connect();names=[x[0] for x in cdb.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],))];cdb.close()
                    await c.send(f':huddle 353 {c.nick} = {arg} :{" ".join(names)}');await c.send(f':huddle 366 {c.nick} {arg} :End of /NAMES list')
            elif cmd=='PRIVMSG':
                ch=chan_for(arg)
                if ch and c.user and ch['id'] in c.joined and trail:
                    core.create_message(c.user,ch['id'],trail)
            elif cmd in ('WHO','LIST','TOPIC','MODE'): 
                if cmd=='TOPIC': await c.send(f':huddle 331 {c.nick} {arg} :No topic is set')
            else: await c.send(f':huddle 421 {c.nick or "*"} {cmd} :Unknown command')
            await registration(c)
    finally:
        task.cancel();clients.discard(c);w.close()
        try:await w.wait_closed()
        except:pass
async def main():
    core.init_db()
    srv=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with srv:
        await srv.serve_forever()
if __name__=='__main__':asyncio.run(main())
