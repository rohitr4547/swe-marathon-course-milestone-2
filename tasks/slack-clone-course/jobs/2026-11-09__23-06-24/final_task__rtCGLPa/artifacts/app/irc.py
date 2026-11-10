import asyncio, core, re
clients=[]
nicks={}
def line(w,s):
    w.write((s+'\r\n').encode())
async def handle(reader,writer):
    conn={'reader':reader,'writer':writer,'token':None,'user':None,'nick':None,'registered':False,'channels':set(),'seen':{}}
    clients.append(conn)
    try:
      while True:
        raw=await reader.readline()
        if not raw: break
        text=raw.decode(errors='replace').rstrip('\r\n')
        if not text: continue
        parts=text.split(' ',2); cmd=parts[0].upper(); args=parts[1:]
        if cmd=='PASS' and args: conn['token']=args[0]
        elif cmd=='NICK' and args:
          nick=args[0]
          if nick in nicks and nicks[nick] is not conn:
            line(writer,f':huddle 433 * {nick} :Nickname is already in use'); continue
          conn['nick']=nick;nicks[nick]=conn
        elif cmd=='USER' and args:
          conn['user']=args[0]
        elif cmd=='QUIT':
          break
        if not conn['registered'] and conn['nick'] and conn['user']:
          u=core.user_for_token(conn['token'])
          if not u:
            line(writer,':huddle 464 * :Password incorrect');break
          conn['userrow']=u;conn['registered']=True;n=conn['nick']
          for code,text2 in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle oiwsz biklmnopstvrRcC'),('005','CHANTYPES=# PREFIX=(o)@ :are supported by this server'),('422','MOTD File is missing')]:
            line(writer,f':huddle {code} {n} :{text2}')
          continue
        if not conn['registered']: continue
        n=conn['nick'];u=conn['userrow']
        if cmd=='PING':
          tok=args[-1] if args else '';tok=tok[1:] if tok.startswith(':') else tok
          line(writer,f':huddle PONG huddle :{tok}')
        elif cmd=='JOIN' and args:
          name=args[0]
          if not re.fullmatch(r'#[^/]+/[^/]+',name):
            line(writer,f':huddle 403 {n} {name} :No such channel');continue
          slug,cname=name[1:].split('/',1);w=core.get_workspace(slug)
          c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone() if w else None;c.close()
          if not ch:
            line(writer,f':huddle 403 {n} {name} :No such channel');continue
          with core.tx() as tx: tx.execute('INSERT OR IGNORE INTO channel_members VALUES(?,?)',(ch['id'],u['id']))
          conn['channels'].add(ch['id']);conn['seen'][ch['id']]=core.head_event_id(ch['id'])
          line(writer,f':{n}!{u["username"]}@huddle JOIN {name}')
          c=core.connect();rows=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
          line(writer,f':huddle 353 {n} = {name} :{" ".join(x["username"] for x in rows)}')
          line(writer,f':huddle 366 {n} {name} :End of /NAMES list')
        elif cmd=='PRIVMSG' and len(args)>=2:
          target=args[0];body=args[1]
          if body.startswith(':'):body=body[1:]
          if target.startswith('#') and '/' in target:
            slug,cname=target[1:].split('/',1);w=core.get_workspace(slug);c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone() if w else None;c.close()
            if ch:
              with core.tx() as tx:
                cur=tx.execute('INSERT INTO messages(channel_id,author_id,body,created_at) VALUES(?,?,?,?)',(ch['id'],u['id'],body,core.now_iso()));m=tx.execute('SELECT * FROM messages WHERE id=?',(cur.lastrowid,)).fetchone();core.emit_event(tx,ch['id'],'message.created',{'message':{'id':m['id'],'channel_id':ch['id'],'author_id':u['id'],'body':body,'parent_id':None,'created_at':m['created_at'],'edited_at':None,'reply_count':0,'files':[],'reactions':[],'mentions':[]}})
        elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'): continue
        else: line(writer,f':huddle 421 {n} {cmd} :Unknown command')
    finally:
      if conn.get('nick') and nicks.get(conn['nick']) is conn:
        del nicks[conn['nick']]
      if conn in clients:
        clients.remove(conn)
      writer.close()
      await writer.wait_closed()
async def deliver():
 while True:
  for x in list(clients):
   for cid in list(x['channels']):
    last=x['seen'].get(cid,0)
    for e in core.events_after(cid,last):
     x['seen'][cid]=e['event_id']
     if e['type']=='message.created':
      c=core.connect();u=c.execute('SELECT username FROM users WHERE id=?',(e['message']['author_id'],)).fetchone();ch=c.execute('SELECT w.slug,c.name FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE c.id=?',(cid,)).fetchone();c.close()
      target=f'#{ch["slug"]}/{ch["name"]}'
      if u and x['userrow']['id']!=e['message']['author_id']: line(x['writer'],f':{u["username"]}!{u["username"]}@huddle PRIVMSG {target} :{e["message"]["body"]}')
  await asyncio.sleep(.05)
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667);asyncio.create_task(deliver())
 async with s: await s.serve_forever()
asyncio.run(main())
