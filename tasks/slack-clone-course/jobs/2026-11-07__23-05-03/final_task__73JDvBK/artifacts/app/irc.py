import asyncio
async def handle(r,w):
    while not r.at_eof():
        x=await r.readline()
        if not x:break
        if x.upper().startswith(b"PING"):w.write(b":huddle PONG huddle\r\n");await w.drain()
    w.close()
async def main():
    s=await asyncio.start_server(handle,"0.0.0.0",6667)
    async with s:
        await s.serve_forever()
asyncio.run(main())
