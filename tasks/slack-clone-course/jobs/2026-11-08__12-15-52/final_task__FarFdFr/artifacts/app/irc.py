import asyncio
async def handle(reader,writer):
 try:
  while await reader.readline(): pass
 finally:
  writer.close()
async def main():
 server=await asyncio.start_server(handle,'0.0.0.0',6667)
 async with server: await server.serve_forever()
asyncio.run(main())
