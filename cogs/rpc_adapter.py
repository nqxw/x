# cogs/rpc_adapter.py | bridges the existing RPCCog (commands.Cog style) into the selfbot dispatcher
import asyncio
import traceback
from . import state as S


class RpcAdapterCog:
    COMMANDS = {"rpc", "rpc1", "rpc2", "rpc3", "rpc4", "rpc5", "rpc6",
                "playing", "listening", "listen", "watching", "watch",
                "competing", "stopactivity", "aoff", "clear_multi_rpc",
                "rpc_status", "spotify", "youtube", "xbox", "ps", "ps4",
                "crunchy", "vrchat", "meta", "rstatus", "remoji",
                "stopstatus", "stopemoji"}

    def __init__(self):
        self._inner = None  # RPCCog instance, created in register()

    def register(self, client):
        """Instantiate the upstream RPCCog with a Bot-shaped shim around client.

        RPCCog is written against discord.ext.commands.Bot, which exposes a
        `.loop` attribute for scheduling background tasks. discord.Client in
        discord.py 2.x has no `.loop`. We wrap the client in a thin proxy that
        provides `.loop` and delegates everything else through __getattr__.
        """
        try:
            from cogs.rpc import RPCCog
        except Exception as e:
            print(f"[rpc-adapter] cannot import cogs.rpc: {type(e).__name__}: {e}")
            traceback.print_exc()
            self._inner = None
            return

        try:
            loop = None
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                try:
                    loop = asyncio.get_event_loop()
                except Exception:
                    loop = None

            outer_client = client

            class _BotShim:
                """Minimal Bot-shaped wrapper. Delegates all attribute access
                to the underlying client, but exposes `loop` for RPCCog's
                background task creation in __init__."""
                def __init__(self, c, lp):
                    self._c = c
                    self.loop = lp

                def __getattr__(self, name):
                    return getattr(self._c, name)

            shim = _BotShim(client, loop)
            self._inner = RPCCog(shim)
            print("[rpc-adapter] RPCCog wrapped")
        except Exception as e:
            print(f"[rpc-adapter] RPCCog init failed: {type(e).__name__}: {e}")
            traceback.print_exc()
            self._inner = None

    async def handle(self, message, cmd, args):
        if self._inner is None:
            try:
                await message.channel.send(S.ui_err("rpc cog not loaded — see console"))
            except Exception:
                pass
            return

        try:
            await self._dispatch(message, cmd, args)
        except Exception as e:
            print(f"[rpc-adapter:{cmd}] {e}")
            traceback.print_exc()
            try:
                await message.channel.send(S.ui_err(f"rpc `{cmd}` errored — check console"))
            except Exception:
                pass

    async def _dispatch(self, message, cmd, args):
        inner = self._inner

        # minimal ctx shim so inner methods that use ctx.send / ctx.message work
        class _Ctx:
            def __init__(self, m):
                self.message = m
                self.channel = m.channel
                self.author = m.author
                self.guild = m.guild
                self.bot = None
            async def send(self, *a, **kw):
                return await self.channel.send(*a, **kw)

        ctx = _Ctx(message)

        # ── group-style commands (rpc1..rpc6) ──
        if cmd.startswith("rpc") and cmd[3:].isdigit():
            slot = int(cmd[3:]) - 1
            if slot < 0 or slot > 5:
                return await message.channel.send(S.ui_err("slot must be 1–6"))
            sub = args[1].lower() if len(args) > 1 else ""
            rest = args[2:] if len(args) > 2 else []
            return await self._rpc_slot_sub(ctx, slot, sub, rest)

        # ── quick presence commands ──
        if cmd == "playing":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            return await inner.playing_cmd(ctx, message=txt) if hasattr(inner, "playing_cmd") else None
        if cmd in ("listening", "listen"):
            txt = " ".join(args[1:]) if len(args) > 1 else None
            return await inner.listening_cmd(ctx, message=txt)
        if cmd in ("watching", "watch"):
            txt = " ".join(args[1:]) if len(args) > 1 else None
            return await inner.watching_cmd(ctx, message=txt)
        if cmd == "competing":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            return await inner.competing_cmd(ctx, message=txt)
        if cmd == "stopactivity":
            return await inner.stopactivity_cmd(ctx)
        if cmd == "aoff":
            return await inner.aoff_cmd(ctx)
        if cmd == "clear_multi_rpc":
            return await inner.clear_multi_rpc(ctx)
        if cmd == "rpc_status":
            return await inner.rpc_status(ctx)

        if cmd in ("spotify", "youtube", "xbox", "ps", "ps4", "crunchy", "vrchat", "meta"):
            method = {
                "spotify": getattr(inner, "cmd_spotify", None),
                "youtube": getattr(inner, "cmd_youtube", None),
                "xbox":    getattr(inner, "cmd_xbox", None),
                "ps":      getattr(inner, "cmd_ps", None),
                "ps4":     getattr(inner, "cmd_ps4", None),
                "crunchy": getattr(inner, "cmd_crunchy", None),
                "vrchat":  getattr(inner, "cmd_vrchat", None),
                "meta":    getattr(inner, "cmd_meta", None),
            }.get(cmd)
            if method is None:
                return await message.channel.send(S.ui_err(f"{cmd} handler missing in rpc cog"))
            rest = " ".join(args[1:]) if len(args) > 1 else None
            return await method(ctx, args=rest)

        if cmd == "rstatus":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            return await inner.rotate_status(ctx, statuses=txt) if txt else None
        if cmd == "remoji":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            return await inner.rotate_emoji(ctx, emojis=txt) if txt else None
        if cmd == "stopstatus":
            return await inner.stop_rotate_status(ctx)
        if cmd == "stopemoji":
            return await inner.stop_rotate_emoji(ctx)

        # ── `$rpc` top-level shim: `$rpc <slot> <field> <value>` ──
        if cmd == "rpc":
            if len(args) < 2:
                return await message.channel.send(S.ui_info(
                    "usage: rpc <1-6> <field> <value> | rpc status | rpc clearall"))
            sub = args[1].lower()
            if sub == "status":
                return await inner.rpc_status(ctx)
            if sub in ("clearall", "clear"):
                return await inner.clear_multi_rpc(ctx)
            if sub.isdigit():
                slot = int(sub) - 1
                if slot < 0 or slot > 5:
                    return await message.channel.send(S.ui_err("slot must be 1–6"))
                field = args[2].lower() if len(args) > 2 else ""
                value = " ".join(args[3:]) if len(args) > 3 else ""
                return await self._rpc_slot_sub(ctx, slot, field, value.split() if value else [])
            return await message.channel.send(S.ui_err("unknown rpc subcommand"))

    async def _rpc_slot_sub(self, ctx, slot: int, sub: str, rest: list):
        """Route $rpcN <sub> <args...> into the upstream RPCCog methods."""
        inner = self._inner
        msg = " ".join(rest)

        # maps user-facing sub → (method suffix, arg style)
        method_map = {
            "name":        "name",
            "details":     "details",
            "state":       "state",
            "type":        "type",
            "platform":    "platform",
            "large_image": "large_image",
            "small_image": "small_image",
            "timestamp":   "timestamp",
            "btn1":        "btn1",
            "btn2":        "btn2",
            "spotify":     "spotify",
            "youtube":     "youtube",
            "xbox":        "xbox",
            "ps":          "ps",
            "ps4":         "ps4",
            "crunchy":     "crunchy",
            "clear":       "clear",
        }
        suffix = method_map.get(sub)
        if suffix is None:
            return await ctx.send(S.ui_err(f"unknown rpc field: {sub}"))

        full = f"rpc{slot + 1}_{suffix}"
        fn = getattr(inner, full, None)
        if fn is None:
            return await ctx.send(S.ui_err(f"{full} not found in rpc cog"))

        try:
            if sub in ("btn1", "btn2"):
                if len(rest) < 2:
                    return await ctx.send(S.ui_err(f"usage: {sub} <label> <url>"))
                url = rest[-1]
                label = " ".join(rest[:-1])
                return await fn(ctx, label, url)
            if sub == "clear":
                return await fn(ctx)
            if sub in ("spotify", "youtube", "xbox", "ps", "ps4", "crunchy"):
                return await fn(ctx, msg or None)
            # name / details / state / type / platform / images / timestamp
            return await fn(ctx, msg)
        except TypeError as e:
            return await ctx.send(S.ui_err(f"arg mismatch for {full}: {e}"))
