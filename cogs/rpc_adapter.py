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
                "stopstatus", "stopemoji", "setpresencestatus", "roblox",
                "rpcwatchdog"}

    # aliases the inner cog doesn't know — rewritten before dispatch
    _ALIASES = {
        "listen": "listening",
        "watch":  "watching",
    }

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
        except Exception as e:
            print(f"[rpc-adapter] RPCCog init failed: {e}")
            traceback.print_exc()
            self._inner = None
            return

        # explicit register — idempotent, ensures on_ready hook + watchdog
        # land even if the auto-start inside __init__ couldn't reach the loop
        try:
            self._inner.register(client)
        except Exception as e:
            print(f"[rpc-adapter] inner register failed: {e}")
            traceback.print_exc()

        print("[rpc-adapter] RPCCog loaded")

    async def handle(self, message, cmd, args):
        if self._inner is None:
            try:
                await message.channel.send(S.ui_err("rpc cog not loaded — see console"))
            except Exception:
                pass
            return

        # bare `.rpc` — show the usage map instead of routing
        if cmd == "rpc":
            await self._usage(message)
            return

        # alias rewrite
        cmd = self._ALIASES.get(cmd, cmd)

        try:
            await self._inner.handle(message, cmd, args)
        except Exception as e:
            print(f"[rpc-adapter:{cmd}] {e}")
            traceback.print_exc()
            try:
                await message.channel.send(
                    S.ui_err(f"rpc `{cmd}` errored — check console"))
            except Exception:
                pass

    async def _usage(self, message):
        lines = [
            "  rpc <1-6> <field> <value>   set a slot",
            "  rpc status                    show all slots",
            "  rpc clearall                  wipe every slot",
            "",
            "  rpc1 name <text>              activity name",
            "  rpc1 details <text>           details line",
            "  rpc1 state <text>             state line",
            "  rpc1 type <type>              playing/streaming/listening/watching/competing",
            "  rpc1 platform <preset>        xbox/ps/ps4/crunchyroll/youtube/twitch/vrchat/meta/roblox",
            "  rpc1 large_image <url>        image url or discord cdn",
            "  rpc1 small_image <url>        image url",
            "  rpc1 timestamp <val>          3600 | 1:00:00 | clear",
            "  rpc1 btn1 <label> <url>       first button",
            "  rpc1 btn2 <label> <url>       second button",
            "  rpc1 clear                    wipe slot 1",
            "",
            "  spotify <song - artist> [slot]",
            "  youtube <video - channel> [slot]",
            "  roblox <game> [- details] [slot]",
            "  xbox / ps / ps4 / crunchy / vrchat / meta",
            "",
            "  rpc_status                    list every slot",
            "  clear_multi_rpc               wipe all six slots",
            "  stopactivity                  clear current activity",
            "  aoff                          shortcut for clear",
            "  rstatus <a, b, c>             rotate custom status text",
            "  remoji <a, b, c>              rotate custom status emoji",
            "  stopstatus / stopemoji        stop rotations",
            "  rpcwatchdog                   watchdog status / stop / start",
        ]
        try:
            await message.channel.send(S._ansi_block(lines))
        except Exception:
            pass