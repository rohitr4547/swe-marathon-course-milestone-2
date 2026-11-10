import asyncio
async def handle(r,w):
    try:
        while True:
            x=await r.readline()
            if not x:break
            if x.startswith(b'PING'):w.write(b'PONG'+x[4:]);await w.drain()
    finally:w.close()
async def main():
    s=await asyncio.start_server(handle,'0.0.0.0',6667);await s.serve_forever()
if __name__=='__main__':asyncio.run(main())
