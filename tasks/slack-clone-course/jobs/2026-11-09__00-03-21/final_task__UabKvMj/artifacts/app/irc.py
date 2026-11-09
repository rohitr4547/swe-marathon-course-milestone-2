import asyncio, core, json
clients={}
async def send(w,line):
    try:w.write((line+'\r\n').encode()); await w.drain()
    except: pass
def find_channel(name):
    if not name.startswith('#') or '/' not in name:return None
    slug,cname=name[1:].split('/',1); c=core.connect()
    r=c.execute('SELECT c.*,x.slug FROM channels c JOIN workspaces x ON x.id=c.workspace_id WHERE x.slug=? AND c.name=?',(slug,cname)).fetchone();c.close();return r
async def handle(r,w):
    nick=user=token=None; registered=False; joined={}; sent={}
    peer=w.get_extra_info('peername');host=str(peer[0] if peer else 'localhost')
    try:
      while True:
        try: line=await asyncio.wait_for(r.readline(),.05)
        except asyncio.TimeoutError: line=None
        if line:
          line=line.decode(errors='ignore').strip()
          if line:
            parts=line.split(' ',2);cmd=parts[0].upper();arg=parts[1] if len(parts)>1 else '';rest=parts[2] if len(parts)>2 else ''
            if cmd=='PASS': token=arg
            elif cmd=='NICK':
              if arg in clients and clients[arg] is not w: await send(w,f':huddle 433 * {arg} :Nickname is already in use');continue
              if token and not core.user_for_token(token): await send(w,':huddle 464 * :Password incorrect');break
              if nick and clients.get(nick) is w:del clients[nick]
              nick=arg;clients[nick]=w
              if user and token and core.user_for_token(token) and not registered:
                registered=True
                for n,msg in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created now'),('004','huddle huddle 1 0'),('005','CASEMAPPING=rfc1459')]:await send(w,f':huddle {n} {nick} :{msg}')
                await send(w,f':huddle 422 {nick} :MOTD File is missing')
            elif cmd=='USER':
              user=arg
              if nick and token and core.user_for_token(token) and not registered:
                registered=True
                for n,msg in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created now'),('004','huddle huddle 1 0'),('005','CASEMAPPING=rfc1459')]:await send(w,f':huddle {n} {nick} :{msg}')
                await send(w,f':huddle 422 {nick} :MOTD File is missing')
            elif cmd=='PING':await send(w,':huddle PONG huddle '+(rest.lstrip(':') if rest else arg.lstrip(':')))
            elif cmd in ('PONG','WHO','LIST','TOPIC','MODE'):pass
            elif cmd in ('JOIN','NAMES'):
              name=arg;ch=find_channel(name)
              if not ch:await send(w,f':huddle 403 {nick} {name} :No such channel');continue
              if cmd=='JOIN':joined[ch['id']]=name;sent.setdefault(ch['id'],0);await send(w,f':{nick}!{user}@{host} JOIN :{name}')
              c=core.connect();rows=c.execute('SELECT u.username FROM channel_members m JOIN users u ON u.id=m.user_id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
              await send(w,f':huddle 353 {nick} = {name} :{" ".join(x["username"] for x in rows)}');await send(w,f':huddle 366 {nick} {name} :End of /NAMES list')
            elif cmd=='PRIVMSG':
              ch=find_channel(arg); body=rest[1:] if rest.startswith(':') else rest
              if ch and ch['id'] in joined:
                uid=core.user_for_token(token)['id']
                with core.tx() as x:
                  x.execute('INSERT INTO messages(channel_id,author_id,body,created_at,mentions) VALUES(?,?,?,?,?)',(ch['id'],uid,body,core.now_iso(),'[]'))
                  m=x.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone();core.emit_event(x,ch['id'],'message.created',{'message':dict(m)})
            elif cmd=='QUIT':break
            else:await send(w,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
        for cid,name in list(joined.items()):
          c=core.connect();rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(cid,sent.get(cid,0))).fetchall();c.close()
          for row in rows:
            sent[cid]=row['event_id'];fr=json.loads(row['payload'])
            if fr.get('type')=='message.created':
              m=fr.get('message',{}); au=core.user_for_token(token); 
              if au and m.get('author_id')==au['id']:continue
              c=core.connect();a=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone();c.close()
              await send(w,f':{a["username"] if a else "user"}!user@host PRIVMSG {name} :{m.get("body","")}')
    finally:
      if nick and clients.get(nick) is w:del clients[nick]
      w.close()
async def main():
 server=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with server:await server.serve_forever()
if __name__=='__main__':asyncio.run(main())
