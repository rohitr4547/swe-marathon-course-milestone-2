import asyncio
async def handle(r,w):
    try:
        while True:
            line=await r.readline()
            if not line: break
            x=line.decode(errors='ignore').strip()
            if x.startswith('PING'): w.write(b':huddle PONG huddle\r\n')
            elif x.startswith('QUIT'): break
            await w.drain()
    finally:w.close()
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667)
    async with s: await s.serve_forever()
asyncio.run(main())
