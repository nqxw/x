# cogs/rpc_adapter.py | bridges RPCCog into the selfbot dispatcher
import asyncio
import traceback
import modifyself_shim as discord
from . import state as S


class RpcAdapterCog:
    COMMANDS = {"rpc", "rpc1", "rpc2", "rpc3", "rpc4", "rpc5", "rpc6",
                "playing", "listening", "listen", "watching", "watch",
                "competing", "stopactivity", "aoff", "clear_multi_rpc",
                "rpc_status", "spotify", "youtube", "xbox", "ps", "ps4",
                "crunchy", "vrchat", "meta", "rstatus", "remoji",
                "stopstatus", "stopemoji", "setpresencestatus", "roblox"}

    def __init__(self):
        self._inner = None

    def register(self, client):
        try:
            from cogs.rpc import RPCCog
        except Exception as e:
            print(f"[rpc-adapter] cannot import cogs.rpc: {e}")
            traceback.print_exc()
            self._inner = None
            return
        try:
            self._inner = RPCCog(client)
            print("[rpc-adapter] RPCCog loaded")
        except Exception as e:
            print(f"[rpc-adapter] RPCCog init failed: {e}")
            traceback.print_exc()
            self._inner = None

    async def handle(self, message, cmd, args):
        if self._inner is None:
            try: await message.channel.send(S.ui_err("rpc cog not loaded — see console"))
            except Exception: pass
            return
        try:
            await self._inner.handle(message, cmd, args)
        except Exception as e:
            print(f"[rpc-adapter:{cmd}] {e}")
            traceback.print_exc()
            try: await message.channel.send(S.ui_err(f"rpc `{cmd}` errored — check console"))
            except Exception: pass