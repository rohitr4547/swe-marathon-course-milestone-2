import asyncio
async def handle(r,w):
 try:
  while await r.readline(): pass
 finally:w.close()
async def main():
 s=await asyncio.start_server(handle,'0.0.0.0',6667); await s.serve_forever()
asyncio.run(main())
