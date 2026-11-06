import asyncio
import core

NICKS=set()
CONNS=set()

def line(w,s): w.write((s+'\r\n').encode())
def numeric(w,code,nick,text,params=''): line(w,f':huddle {code} {nick} {params} :{text}')

def parse_channel(name):
    if not isinstance(name,str) or not name.startswith('#') or '/' not in name[1:]: return None
    slug,cname=name[1:].split('/',1)
    w=core.get_workspace(slug)
    if not w:return None
    c=core.connect();ch=c.execute('SELECT * FROM channels WHERE workspace_id=? AND name=?',(w['id'],cname)).fetchone();c.close()
    return (w,ch) if ch else None

async def deliver(writer, joined, user_box):
    while not writer.is_closing():
        for name,(cid,last) in list(joined.items()):
            for ev in core.events_after(cid,last):
                joined[name]=(cid,ev['event_id'])
                if ev.get('type') not in ('message.created','message.reply'): continue
                m=ev.get('message') or {}
                if m.get('author_id')==user_box[0]: continue
                c=core.connect();u=c.execute('SELECT username FROM users WHERE id=?',(m.get('author_id'),)).fetchone();c.close()
                if u:
                    try: line(writer,f':{u["username"]}!{u["username"]}@huddle PRIVMSG {name} :{m.get("body","")}')
                    except: return
        await asyncio.sleep(.05)

async def handle(reader,writer):
    CONNS.add(writer);token=None;nick=None;username=None;registered=False;joined={};user_box=[None]
    task=asyncio.create_task(deliver(writer,joined,user_box))
    try:
        while not reader.at_eof():
            raw=await reader.readline()
            if not raw:break
            text=raw.decode(errors='replace').strip('\r\n')
            if not text:continue
            bits=text.split();cmd=bits[0].upper();args=bits[1:]
            if cmd=='PASS': token=args[0] if args else None
            elif cmd=='NICK':
                new=args[0] if args else ''
                if any(x.lower()==new.lower() for x in NICKS) and (not nick or new.lower()!=nick.lower()):
                    numeric(writer,'433',nick or '*','Nickname is already in use',new);continue
                if nick:NICKS.discard(nick)
                nick=new;NICKS.add(nick)
            elif cmd=='USER': username=args[0] if args else (nick or 'irc')
            elif cmd=='QUIT': break
            elif cmd=='PING':
                tok=text.split(':',1)[1] if ':' in text else (args[0] if args else '')
                line(writer,f':huddle PONG huddle :{tok}');continue
            if not registered and nick and username and cmd in ('NICK','USER'):
                u=core.user_for_token(token)
                if not u:numeric(writer,'464',nick or '*','Password incorrect');break
                registered=True;user_box[0]=u['id']
                for code,msg in [('001','Welcome to Huddle'),('002','Your host is huddle'),('003','This server was created today'),('004','huddle huddle 1.0 oiwsz biklmnopqstvr'),('005','CHANTYPES=# PREFIX=(ov)@+')] : numeric(writer,code,nick,msg)
                numeric(writer,'422',nick,'MOTD File is missing');continue
            if not registered:continue
            u=core.user_for_token(token)
            if not u:break
            if cmd=='JOIN':
                name=args[0] if args else '';pair=parse_channel(name)
                if not pair:numeric(writer,'403',nick,'No such channel',name);continue
                w,ch=pair
                if ch['is_private'] or (not core.can_read_channel(u['id'],ch) and w['join_mode']!='open') or core.member_role(w['id'],u['id'])=='guest':
                    numeric(writer,'403',nick,'No such channel',name);continue
                if not core.member_role(w['id'],u['id']):
                    with core.tx() as c:core.add_workspace_member(c,w['id'],u['id'])
                with core.tx() as c:core.add_channel_member(c,ch['id'],u['id'])
                joined[name]=(ch['id'],core.head_event_id(ch['id']))
                line(writer,f':{nick}!{u["username"]}@huddle JOIN {name}')
                c=core.connect();rows=c.execute('SELECT u.username FROM users u JOIN channel_members m ON m.user_id=u.id WHERE m.channel_id=?',(ch['id'],)).fetchall();c.close()
                numeric(writer,'353',nick,' '.join(x['username'] for x in rows),'= '+name);numeric(writer,'366',nick,'End of /NAMES list',name)
            elif cmd=='PRIVMSG':
                name=args[0] if args else '';body=text.split(':',1)[1] if ':' in text else ''
                pair=parse_channel(name)
                if not pair or name not in joined: numeric(writer,'404',nick,'Cannot send to channel',name);continue
                try: core.create_message(u,pair[1]['id'],body)
                except: numeric(writer,'404',nick,'Cannot send to channel',name)
            elif cmd in ('PONG','NAMES','WHO','LIST','TOPIC','MODE'):
                if cmd=='NAMES' and args:numeric(writer,'366',nick,'End of /NAMES list',args[0])
            else:numeric(writer,'421',nick,'Unknown command',cmd)
    finally:
        task.cancel()
        if nick:NICKS.discard(nick)
        CONNS.discard(writer);writer.close()
        try:await writer.wait_closed()
        except:pass

async def main():
    server=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with server:await server.serve_forever()
if __name__=='__main__':
    core.init_db()
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
