import asyncio, re, core, json

NICKS=set()
CLIENTS={}

def line(s): return (s+'\r\n').encode()
async def send(w,s): w.write(line(s)); await w.drain()
def numeric(code,nick,params='',text=''):
    return f':huddle {int(code):03d} {nick} {params} :{text}'
def channel_lookup(name):
    if not name.startswith('#') or '/' not in name[1:]: return None
    slug,cname=name[1:].split('/',1)
    c=core.connect()
    z=c.execute('SELECT c.* FROM channels c JOIN workspaces w ON c.workspace_id=w.id WHERE w.slug=? AND c.name=?',(slug,cname)).fetchone()
    c.close(); return z
async def handle(reader,writer):
    token=None; nick=None; username=None; user=None; registered=False; joined={}
    peer=writer.get_extra_info('peername')
    async def deliver():
      seen={}
      while not writer.is_closing():
        await asyncio.sleep(.05)
        if not joined: continue
        c=core.connect()
        for chid,cname in list(joined.items()):
          last=seen.get(chid,0)
          rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',(chid,last)).fetchall()
          for row in rows:
            seen[chid]=row['event_id']
            try: ev=json.loads(row['payload'])
            except: continue
            if ev.get('type') not in ('message.created','message.reply'): continue
            msg=ev.get('message') or {}
            if user and msg.get('author_id')==user['id']: continue
            au=msg.get('author') or {}
            an=au.get('username') or 'user'
            await send(writer,f':{an}!{an}@huddle PRIVMSG {cname} :{msg.get("body","")}')
        c.close()
    delivery=asyncio.create_task(deliver())
    try:
      while not reader.at_eof():
        raw=await reader.readline()
        if not raw: break
        text=raw.decode(errors='replace').strip('\r\n')
        if not text: continue
        parts=text.split(' ',1); cmd=parts[0].upper(); rest=parts[1] if len(parts)>1 else ''
        args=rest.split()
        if cmd=='PASS': token=rest.lstrip(':').strip(); continue
        if cmd=='NICK':
          new=args[0] if args else ''
          if new in NICKS and new != nick:
            await send(writer,numeric(433,nick or '*',new,'Nickname is already in use')); continue
          if nick:NICKS.discard(nick)
          nick=new;NICKS.add(nick)
        elif cmd=='USER':
          username=(args[0] if args else 'irc')
        elif cmd=='QUIT':
          break
        elif cmd=='PING':
          val=rest[1:] if rest.startswith(':') else rest
          await send(writer,f':huddle PONG huddle :{val}'); continue
        elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
          continue
        elif not registered:
          await send(writer,numeric(421,nick or '*',cmd,'Unknown command')); continue
        elif cmd=='JOIN':
          cname=(args[0] if args else '')
          ch=channel_lookup(cname)
          if not ch:
            await send(writer,numeric(403,nick,cname,'No such channel')); continue
          user=core.user_for_token(token)
          if not user:
            await send(writer,numeric(403,nick,cname,'No such channel')); continue
          with core.tx() as c:
            c.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],user['id']))
          joined[ch['id']]=cname
          c=core.connect(); names=[x['username'] for x in c.execute('SELECT u.username FROM users u JOIN channel_members m ON u.id=m.user_id WHERE m.channel_id=?',(ch['id'],)).fetchall()];c.close()
          await send(writer,f':{nick}!{username}@huddle JOIN {cname}')
          await send(writer,numeric(353,nick,f'= {cname}',' '.join(names)))
          await send(writer,numeric(366,nick,cname,'End of /NAMES list')); continue
        elif cmd=='PRIVMSG':
          if len(args)<2: continue
          cname=args[0]; body=rest.split(' :',1)[1] if ' :' in rest else ' '.join(args[1:])
          ch=channel_lookup(cname)
          if ch and user:
            with core.tx() as c:
              cur=c.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(ch['id'],user['id'],body,core.now_iso()));mid=cur.lastrowid
              z=c.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone();core.emit(c,ch['id'],'message.created',{'message':core.msg_json(c,z)})
          continue
        elif registered:
          await send(writer,numeric(421,nick,cmd,'Unknown command')); continue
        if not registered and nick and username:
          user=core.user_for_token(token)
          if not user:
            await send(writer,numeric(464,nick,'','Password incorrect')); break
          registered=True
          for code,textx in [('001','Welcome to huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle'),('005','CHANTYPES=#')]:
            await send(writer,numeric(code,nick,'',textx))
          await send(writer,numeric(422,nick,'','MOTD File is missing'))
    finally:
      delivery.cancel()
      if nick:NICKS.discard(nick)
      writer.close()
      try: await writer.wait_closed()
      except: pass
async def main():
    await asyncio.start_server(handle,'0.0.0.0',6667)
    await asyncio.Event().wait()
if __name__=='__main__': asyncio.run(main())
