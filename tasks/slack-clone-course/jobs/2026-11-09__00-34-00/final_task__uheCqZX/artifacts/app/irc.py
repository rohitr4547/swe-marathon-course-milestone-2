import asyncio, json, re, uuid
import core

NICKS=set()
CONNS=[]
LOCK=asyncio.Lock()

def sendline(w,s):
    w.write((s+'\r\n').encode())

def make_msg(c,cid,uid,body):
    c.execute('INSERT INTO messages(channel_id,author_id,body,parent_id,created_at,mentions) VALUES(?,?,?,?,?,?)',
              (cid,uid,body,None,core.now_iso(),'[]'))
    m=c.execute('SELECT * FROM messages WHERE id=last_insert_rowid()').fetchone()
    core.emit_event(c,cid,'message.created',{'message':{
        'id':m['id'],'channel_id':cid,'author_id':uid,'body':body,'parent_id':None,
        'reply_count':0,'created_at':m['created_at'],'edited_at':None,
        'files':[],'reactions':[],'mentions':[]}})
    return m

async def deliver(conn):
    while not conn['writer'].is_closing():
        await asyncio.sleep(.05)
        for cid in list(conn['channels']):
            c=core.connect()
            rows=c.execute('SELECT event_id,payload FROM events WHERE channel_id=? AND event_id>? ORDER BY event_id',
                           (cid,conn['last'].get(cid,0))).fetchall()
            c.close()
            for row in rows:
                d=json.loads(row['payload']); conn['last'][cid]=row['event_id']
                if d.get('type') not in ('message.created','message.reply'): continue
                m=d.get('message') or {}
                if m.get('author_id')==conn['uid']: continue
                c=core.connect(); u=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone(); c.close()
                if not u: continue
                name=conn['names'].get(cid)
                if name:
                    sendline(conn['writer'],f":{u['username']}!{u['username']}@huddle PRIVMSG {name} :{m.get('body','')}")
                    try: await conn['writer'].drain()
                    except: return

async def handle(reader,writer):
    token=nick=userline=None; registered=False; conn={'writer':writer,'uid':None,'channels':set(),'last':{},'names':{}}
    task=None
    try:
        while True:
            raw=await reader.readline()
            if not raw: break
            line=raw.decode(errors='ignore').rstrip('\r\n')
            if not line: continue
            p=line.split(' ',1); cmd=p[0].upper(); rest=p[1] if len(p)>1 else ''
            args=rest.split(' ') if rest else []
            if cmd=='PASS': token=rest.strip()
            elif cmd=='NICK':
                nn=rest.strip().lstrip(':')
                async with LOCK:
                    if nn in NICKS and nn!=nick:
                        sendline(writer,f':huddle 433 * {nn} :Nickname is already in use'); await writer.drain(); continue
                    if nick:NICKS.discard(nick)
                    nick=nn; NICKS.add(nn)
            elif cmd=='USER': userline=args[0] if args else 'irc'
            elif cmd=='QUIT': break
            elif cmd=='PING':
                v=rest[1:] if rest.startswith(':') else rest
                sendline(writer,f':huddle PONG huddle :{v}')
            elif not registered:
                if cmd not in ('PONG','CAP'): sendline(writer,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
            elif cmd=='JOIN':
                name=rest.strip().split()[0] if rest.strip() else ''
                u=core.user_for_token(token); target=name[1:] if name.startswith('#') else ''
                wslug,cname=target.split('/',1) if '/' in target else ('','')
                w=core.get_workspace(wslug); ch=None
                if w:
                    c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone();c.close()
                if not ch:
                    sendline(writer,f':huddle 403 {nick} {name} :No such channel')
                else:
                    try: core.join_channel(u,ch['id'])
                    except: sendline(writer,f':huddle 403 {nick} {name} :No such channel')
                    else:
                        conn['channels'].add(ch['id']);conn['last'][ch['id']]=ch['last_event_id'];conn['names'][ch['id']]=name
                        c=core.connect();ns=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=? ORDER BY u.username',(ch['id'],)).fetchall();c.close()
                        sendline(writer,f':{nick}!{userline}@huddle JOIN {name}')
                        sendline(writer,f':huddle 353 {nick} = {name} :{" ".join(x["username"] for x in ns)}')
                        sendline(writer,f':huddle 366 {nick} {name} :End of /NAMES list')
            elif cmd=='PRIVMSG':
                if not args: continue
                target=args[0]; body=rest.split(' :',1)[1] if ' :' in rest else (args[1] if len(args)>1 else '')
                cid=next((x for x,n in conn['names'].items() if n==target),None)
                if cid is not None:
                    with core.tx() as c: make_msg(c,cid,conn['uid'],body)
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'): pass
            else: sendline(writer,f':huddle 421 {nick} {cmd} :Unknown command')
            if not registered and nick and userline is not None:
                u=core.user_for_token(token)
                if not u:
                    sendline(writer,':huddle 464 * :Password incorrect'); await writer.drain(); break
                registered=True; conn['uid']=u['id']; CONNS.append(conn)
                for code,text in [('001','Welcome to huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle'),('005','CHANTYPES=#'),('422','MOTD File is missing')]:
                    sendline(writer,f':huddle {code} {nick} :{text}')
                task=asyncio.create_task(deliver(conn))
            await writer.drain()
    finally:
        if task: task.cancel()
        if conn in CONNS: CONNS.remove(conn)
        if nick:
            async with LOCK:NICKS.discard(nick)
        writer.close()
        try: await writer.wait_closed()
        except: pass

async def main():
    server=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with server: await server.serve_forever()

if __name__=='__main__': asyncio.run(main())
