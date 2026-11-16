"""A switchable 'internet line' for outage drills: a TCP relay from the branch box to head office.

    python wan_relay.py --listen 127.0.0.1:8099 --target 127.0.0.1:8000 --flag C:/temp/wan-down.flag

While the flag file exists the line is down: new connections are refused and open ones are cut,
exactly like unplugging the branch router's internet cable. Delete the file to reconnect.
Development and drills only.
"""
from __future__ import annotations

import argparse
import asyncio
import os

active: set[asyncio.StreamWriter] = set()


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="127.0.0.1:8099")
    ap.add_argument("--target", default="127.0.0.1:8000")
    ap.add_argument("--flag", required=True)
    args = ap.parse_args()
    lhost, lport = args.listen.rsplit(":", 1)
    thost, tport = args.target.rsplit(":", 1)

    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        if os.path.exists(args.flag):
            client_w.close()
            return
        try:
            up_r, up_w = await asyncio.open_connection(thost, int(tport))
        except OSError:
            client_w.close()
            return
        active.update({client_w, up_w})
        try:
            await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))
        finally:
            active.difference_update({client_w, up_w})

    async def watchdog() -> None:
        while True:
            if os.path.exists(args.flag):
                for w in list(active):
                    w.close()
                active.clear()
            await asyncio.sleep(0.3)

    async def run() -> None:
        # Cut connections raise resets on Windows; that is the point of the relay, so stay quiet.
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, ctx: None if isinstance(ctx.get("exception"), ConnectionError) else loop.default_exception_handler(ctx))
        server = await asyncio.start_server(handle, lhost, int(lport))
        print(f"relay {args.listen} -> {args.target} (down while {args.flag} exists)", flush=True)
        async with server:
            await asyncio.gather(server.serve_forever(), watchdog())

    asyncio.run(run())


if __name__ == "__main__":
    main()
