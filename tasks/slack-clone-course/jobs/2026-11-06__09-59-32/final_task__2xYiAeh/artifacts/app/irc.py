import asyncio, core, re
clients=[]
def line(writer,s): writer.write((s+'\r\n').encode())
def chan_name(ch):
    c=core.connect();w=c.execute('SELECT slug FROM workspaces WHERE id=?',(ch['workspace_id'],)).fetchone();c.close()
    return '#%s/%s'%(w['slug'],ch['name']) if w else ''
def find_channel(name):
    if not name.startswith('#') or '/' not in name[1:]:return None
    slug,n=name[1:].split('/',1);w=core.get_workspace(slug)
    if not w:return None
    c=core.connect();r=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],n)).fetchone();c.close();return r
async def deliver(conn):
    while conn['alive']:
        for cid in list(conn['joined']):
            for ev in core.events_after(cid,conn['last'].get(cid,0)):
                conn['last'][cid]=ev['event_id']
                if ev['type'] not in ('message.created','message.reply'):continue
                m=ev.get('message') or {}; 
                if m.get('author_id')==conn['user']['id']:continue
                ch=core.get_channel(cid); line(conn['writer'],':%s!%s@huddle PRIVMSG %s :%s'%(m.get('author',{}).get('username','user'),m.get('author',{}).get('username','user'),chan_name(ch),m.get('body','')))
        try: await conn['writer'].drain()
        except: break
        await asyncio.sleep(.05)
async def handle(reader,writer):
    conn={'writer':writer,'alive':True,'nick':None,'user':None,'token':None,'joined':set(),'last':{}}
    clients.append(conn)
    task=asyncio.create_task(deliver(conn))
    try:
        while True:
            raw=await reader.readline()
            if not raw:break
            s=raw.decode(errors='ignore').strip('\r\n'); parts=s.split(' ',2); cmd=parts[0].upper() if parts else ''
            arg=parts[1] if len(parts)>1 else ''; rest=parts[2] if len(parts)>2 else ''
            if cmd=='PASS':conn['token']=arg
            elif cmd=='NICK':
                if any(x.get('nick')==arg and x is not conn for x in clients):
                    line(writer,':huddle 433 * %s :Nickname is already in use'%arg);continue
                conn['nick']=arg
                if conn.get('userline') and not conn['user']:
                    conn['user']=core.user_for_token(conn['token'])
                    if not conn['user']:
                        line(writer,':huddle 464 %s :Password incorrect'%conn['nick']);await writer.drain();break
                    for n,t in [('001','Welcome'),('002','Your host'),('003','Created'),('004','huddle'),('005','CHANTYPES=#'),('422','No MOTD')]:
                        line(writer,':huddle %s %s :%s'%(n,conn['nick'],t))
                    await writer.drain()
            elif cmd=='USER':
                conn['userline']=arg
                if conn['nick'] and not conn['user']:
                    conn['user']=core.user_for_token(conn['token'])
                    if not conn['user']:
                        line(writer,':huddle 464 %s :Password incorrect'%conn['nick']);await writer.drain();break
                    for n,t in [('001','Welcome'),('002','Your host'),('003','Created'),('004','huddle'),('005','CHANTYPES=#'),('422','No MOTD')]:
                        line(writer,':huddle %s %s :%s'%(n,conn['nick'],t))
                    await writer.drain()
            elif cmd=='PING':line(writer,':huddle PONG huddle :'+(rest.lstrip(':') if rest else arg.lstrip(':')))
            elif cmd=='JOIN':
                ch=find_channel(arg)
                if not ch or not (await asyncio.to_thread(core.join_channel,conn['user'],ch['id']))[0]:
                    line(writer,':huddle 403 %s %s :No such channel'%(conn['nick'],arg));continue
                conn['joined'].add(ch['id']);conn['last'][ch['id']]=core.head_event_id(ch['id'])
                line(writer,':%s!%s@huddle JOIN %s'%(conn['nick'],conn['user']['username'],arg))
                c=core.connect();rs=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
                line(writer,':huddle 353 %s = %s :%s'%(conn['nick'],arg,' '.join(x['username'] for x in rs)));line(writer,':huddle 366 %s %s :End of /NAMES list'%(conn['nick'],arg))
            elif cmd=='PRIVMSG':
                ch=find_channel(arg);body=rest[1:] if rest.startswith(':') else rest
                if ch and body:
                    try: await asyncio.to_thread(core.create_message,conn['user'],ch['id'],body)
                    except: pass
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):pass
            elif cmd=='QUIT':break
            elif conn['user']:line(writer,':huddle 421 %s %s :Unknown command'%(conn['nick'],cmd))
            await writer.drain()
    finally:
        conn['alive']=False
        task.cancel()
        if conn in clients: clients.remove(conn)
        writer.close()
        await writer.wait_closed()
async def main():
    core.init_db()
    srv=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with srv:
        await srv.serve_forever()
if __name__=='__main__':asyncio.run(main())
