import asyncio,core
NICKS=set()
CONNS=set()
def line(code,nick,text,params=''):
    return f':huddle {code} {nick} {params} :{text}\r\n'
async def deliver(conn):
    writer, joined, sent, me = conn
    while not writer.is_closing():
        try:
            for cid in list(joined):
                for ev in core.events_after(cid, sent.get(cid,0)):
                    sent[cid]=ev['event_id']
                    f=__import__('json').loads(ev['payload'])
                    if f.get('type') not in ('message.created','message.reply'): continue
                    mid=f.get('message',{}).get('id')
                    c=core.connect(); m=c.execute('SELECT m.*,u.username FROM messages m JOIN users u ON u.id=m.author_id WHERE m.id=?',(mid,)).fetchone(); c.close()
                    if not m or m['author_id']==conn[3]: continue
                    c=core.connect(); ch=c.execute('SELECT w.slug,c.name FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE c.id=?',(cid,)).fetchone(); c.close()
                    if ch:
                        writer.write(f":{m['username']}!{m['username']}@huddle PRIVMSG #{ch['slug']}/{ch['name']} :{m['body']}\r\n".encode())
                        await writer.drain()
            await asyncio.sleep(.05)
        except: break

async def handle(reader,writer):
    token=None;nick=None;user=None;registered=False; joined=set(); sent={}; me=None; task=None
    try:
        while True:
            raw=await reader.readline()
            if not raw:break
            t=raw.decode(errors='ignore').strip()
            if not t:continue
            parts=t.split(' ',1); cmd=parts[0].upper(); arg=parts[1] if len(parts)>1 else ''
            if cmd=='PASS':token=arg.lstrip(':')
            elif cmd=='NICK':
                n=arg.lstrip(':').strip()
                if n in NICKS and n != nick:
                    writer.write(f':huddle 433 * {n} :Nickname is already in use\r\n'.encode());await writer.drain();continue
                if nick:NICKS.discard(nick)
                nick=n;NICKS.add(nick)
                if user and not registered:
                    u=core.user_for_token(token)
                    if not u:
                        writer.write(f':huddle 464 {nick} :Password incorrect\r\n'.encode());await writer.drain();break
                    registered=True; me=u['id']; task=asyncio.create_task(deliver((writer,joined,sent,me)))
                    for code,text in [('001','Welcome'),('002','Your host'),('003','Created'),('004','huddle'),('005','CHANTYPES=#')]:
                        writer.write(line(code,nick,text).encode())
                    writer.write(line('422',nick,'No MOTD').encode());await writer.drain()
            elif cmd=='USER':
                user=arg
                if nick and not registered:
                    u=core.user_for_token(token)
                    if not u:
                        writer.write(f':huddle 464 {nick} :Password incorrect\r\n'.encode());await writer.drain();break
                    registered=True; me=u['id']; task=asyncio.create_task(deliver((writer,joined,sent,me)))
                    for code,text in [('001','Welcome'),('002','Your host'),('003','Created'),('004','huddle'),('005','CHANTYPES=#')]:
                        writer.write(line(code,nick,text).encode())
                    writer.write(line('422',nick,'No MOTD').encode());await writer.drain()
            elif cmd=='QUIT':break
            elif cmd=='PING':
                tok=arg.lstrip(':');writer.write(f':huddle PONG huddle :{tok}\r\n'.encode());await writer.drain()
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):continue
            elif cmd=='PRIVMSG' and registered:
                target,body=(arg.split(' :',1)+[''])[:2] if ' :' in arg else (arg,'')
                matched=None
                for cid in joined:
                    cc=core.connect(); mm=cc.execute('SELECT w.slug,c.name FROM channels c JOIN workspaces w ON w.id=c.workspace_id WHERE c.id=?',(cid,)).fetchone(); cc.close()
                    if mm and target==f'#{mm["slug"]}/{mm["name"]}': matched=cid; break
                if matched is not None:
                    cid=matched
                    m=core.create_message(me and core.user_for_token(token),cid,body)
                    sent[cid]=core.head_event_id(cid)
            elif cmd=='JOIN' and registered:
                name=arg.split()[0]; ok=False; ch=None
                if name.startswith('#') and '/' in name:
                    slug,cname=name[1:].split('/',1); w=core.get_workspace(slug)
                    if w:
                        c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone();c.close()
                        if ch:
                            u=core.user_for_token(token)
                            try:
                                joined.add(ch['id']); sent[ch['id']]=core.head_event_id(ch['id'])
                                role=core.member_role(w['id'],u['id'])
                                if ch['is_private'] and not role: raise ValueError()
                                if role=='guest': raise ValueError()
                                with core.tx() as tc:
                                    core.add_workspace_member(tc,w['id'],u['id'],'member')
                                    core.add_channel_member(tc,ch['id'],u['id'])
                                ok=True
                            except: ok=False
                if not ok:
                    writer.write(f':huddle 403 {nick} {name} :No such channel\r\n'.encode())
                else:
                    writer.write(f':{nick}!{user}@huddle JOIN {name}\r\n'.encode())
                    writer.write(f':huddle 353 {nick} = {name} :{nick}\r\n'.encode())
                    writer.write(f':huddle 366 {nick} {name} :End of /NAMES list\r\n'.encode())
                await writer.drain()
            else:
                if not registered and nick and user:
                    u=core.user_for_token(token)
                    if not u:
                        writer.write(f':huddle 464 {nick} :Password incorrect\r\n'.encode());await writer.drain();break
                    registered=True
                    for code,text in [('001','Welcome'),('002','Your host'),('003','Created'),('004','huddle'),('005','CHANTYPES=#')]:
                        writer.write(line(code,nick,text).encode())
                    writer.write(line('422',nick,'No MOTD').encode());await writer.drain()
                elif cmd not in ('PASS','NICK','USER'):
                    writer.write(f':huddle 421 {nick or "*"} {cmd} :Unknown command\r\n'.encode());await writer.drain()
    finally:
        if task: task.cancel()
        if nick:NICKS.discard(nick)
        writer.close()
        try:await writer.wait_closed()
        except:pass
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s:await s.serve_forever()
if __name__=='__main__':asyncio.run(main())
