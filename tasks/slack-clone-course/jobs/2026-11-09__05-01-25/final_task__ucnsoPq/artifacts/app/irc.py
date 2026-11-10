import asyncio, core, re, json
NICKS=set()
def line(w,s): w.write((s+'\r\n').encode())
async def deliver(conn):
    w=conn['w']; seen=dict(conn['seen'])
    try:
        while not w.is_closing():
            for cid,name in list(conn['joined'].items()):
                for ev in core.events_after(cid,seen.get(cid,0)):
                    seen[cid]=ev['event_id']
                    p=json.loads(ev['payload'])
                    if p.get('type') not in ('message.created','message.reply'): continue
                    m=p.get('message') or {}
                    if m.get('author_id')==conn['user']['id']: continue
                    c=core.connect(); a=c.execute('select username from users where id=?',(m.get('author_id'),)).fetchone();c.close()
                    author=a['username'] if a else 'user'
                    line(w,f":{author}!{author}@huddle PRIVMSG {name} :{m.get('body','')}")
            await w.drain(); await asyncio.sleep(.05)
    except (asyncio.CancelledError,ConnectionError): pass
async def handle(r,w):
    token=None; nick=None; username=None; registered=False; conn={'w':w,'user':None,'joined':{},'seen':{}}
    task=None
    try:
        while True:
            raw=await r.readline()
            if not raw: break
            text=raw.decode(errors='ignore').strip('\r\n')
            if not text: continue
            bits=text.split(' ',1); cmd=bits[0].upper(); arg=bits[1] if len(bits)>1 else ''
            if cmd=='PASS': token=arg[1:] if arg.startswith(':') else arg
            elif cmd=='NICK':
                n=arg.lstrip(':').strip()
                if n in NICKS and n!=nick:
                    line(w,f':huddle 433 * {n} :Nickname is already in use'); await w.drain(); continue
                if nick:NICKS.discard(nick)
                nick=n;NICKS.add(nick)
            elif cmd=='USER': username=(arg.split(' ',1)[0] if arg else nick) or nick
            elif cmd=='QUIT': break
            elif cmd=='PING': line(w,f':huddle PONG huddle :{arg[1:] if arg.startswith(":") else arg}')
            elif cmd=='JOIN':
                u=core.user_for_token(token); name=arg.strip(); m=re.fullmatch(r'#([^/]+)/([^/]+)',name)
                c=core.connect();ch=c.execute('select c.* from channels c join workspaces w on w.id=c.workspace_id where w.slug=? and c.name=?',(m.group(1),m.group(2))).fetchone() if m else None;c.close()
                ok=False
                if u and ch:
                    try:
                        with core.tx() as x:
                            x.execute('insert or ignore into members(workspace_id,user_id,role) values(?,?,?)',(ch['workspace_id'],u['id'],'member'))
                            x.execute('insert or ignore into channel_members values(?,?)',(ch['id'],u['id']))
                        ok=True
                    except: pass
                if not ok: line(w,f':huddle 403 {nick} {name} :No such channel')
                else:
                    conn['joined'][ch['id']]=name;conn['seen'][ch['id']]=core.head_event_id(ch['id'])
                    c=core.connect();names=[x[0] for x in c.execute('select u.username from channel_members cm join users u on u.id=cm.user_id where cm.channel_id=?',(ch['id'],)).fetchall()];c.close()
                    line(w,f':{nick}!{username}@huddle JOIN {name}');line(w,f':huddle 353 {nick} = {name} :{" ".join(names)}');line(w,f':huddle 366 {nick} {name} :End of /NAMES list')
            elif cmd=='PRIVMSG':
                u=core.user_for_token(token); parts=arg.split(' :',1)
                if u and len(parts)==2:
                    name,body=parts; cid=next((c for c,n in conn['joined'].items() if n==name),None)
                    if cid:
                        with core.tx() as c:
                            c.execute('insert into messages(channel_id,author_id,body,created_at) values(?,?,?,?)',(cid,u['id'],body,core.now_iso()))
                            m=c.execute('select * from messages where id=last_insert_rowid()').fetchone()
                            core.emit_event(c,cid,'message.created',{'message':dict(m)})
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'): pass
            elif cmd not in ('PASS','NICK','USER'): line(w,f':huddle 421 {nick or "*"} {cmd} :Unknown command')
            if not registered and nick and username:
                if not core.user_for_token(token):
                    line(w,f':huddle 464 {nick} :Password incorrect'); await w.drain(); break
                registered=True;conn['user']=core.user_for_token(token);task=asyncio.create_task(deliver(conn))
                for code,msg in [('001','Welcome'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle 1.0'),('005','CHANTYPES=# :are supported'),('422','MOTD File is missing')]:line(w,f':huddle {code} {nick} :{msg}')
            await w.drain()
    finally:
        if task:task.cancel()
        if nick:NICKS.discard(nick)
        w.close(); await w.wait_closed()
async def main():
    core.init_db(); server=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with server: await server.serve_forever()
if __name__=='__main__':asyncio.run(main())
