# cogs/rpc_adapter.py | bridges the existing RPCCog (commands.Cog style) into the selfbot dispatcher
import asyncio
import traceback
from . import state as S


def _unwrap(cmd_obj):
    """If cmd_obj is a discord.ext.commands.Command, return its raw callback.
    Otherwise return cmd_obj as-is. The callback is an unbound function, so
    callers must pass the cog instance as the first positional arg."""
    return getattr(cmd_obj, "callback", cmd_obj)


class RpcAdapterCog:
    COMMANDS = {"rpc", "rpc1", "rpc2", "rpc3", "rpc4", "rpc5", "rpc6",
                "playing", "listening", "listen", "watching", "watch",
                "competing", "stopactivity", "aoff", "clear_multi_rpc",
                "rpc_status", "spotify", "youtube", "xbox", "ps", "ps4",
                "crunchy", "vrchat", "meta", "rstatus", "remoji",
                "stopstatus", "stopemoji", "setpresencestatus"}

    def __init__(self):
        self._inner = None

    def register(self, client):
        """Instantiate the upstream RPCCog with a Bot-shaped shim around client."""
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

            class _BotShim:
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
        if cmd.startswith("rpc") and len(cmd) == 4 and cmd[3].isdigit():
            slot = int(cmd[3]) - 1
            if slot < 0 or slot > 5:
                return await message.channel.send(S.ui_err("slot must be 1–6"))
            sub = args[1].lower() if len(args) > 1 else ""
            rest = args[2:] if len(args) > 2 else []
            return await self._rpc_slot_sub(ctx, slot, sub, rest)

        # ── quick presence ──
        if cmd == "playing":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            c = getattr(inner, "playing_cmd", None)
            if c is None:
                return await message.channel.send(S.ui_err("playing handler missing in rpc cog"))
            return await _unwrap(c)(inner, ctx, message=txt)

        if cmd in ("listening", "listen"):
            txt = " ".join(args[1:]) if len(args) > 1 else None
            c = getattr(inner, "listening_cmd", None)
            if c is None:
                return await message.channel.send(S.ui_err("listening handler missing in rpc cog"))
            return await _unwrap(c)(inner, ctx, message=txt)

        if cmd in ("watching", "watch"):
            txt = " ".join(args[1:]) if len(args) > 1 else None
            c = getattr(inner, "watching_cmd", None)
            if c is None:
                return await message.channel.send(S.ui_err("watching handler missing in rpc cog"))
            return await _unwrap(c)(inner, ctx, message=txt)

        if cmd == "competing":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            c = getattr(inner, "competing_cmd", None)
            if c is None:
                return await message.channel.send(S.ui_err("competing handler missing in rpc cog"))
            return await _unwrap(c)(inner, ctx, message=txt)

        if cmd == "stopactivity":
            c = getattr(inner, "stopactivity_cmd", None)
            if c is None:
                return await message.channel.send(S.ui_err("stopactivity handler missing"))
            return await _unwrap(c)(inner, ctx)

        if cmd == "aoff":
            c = getattr(inner, "aoff_cmd", None)
            if c is None:
                return await message.channel.send(S.ui_err("aoff handler missing"))
            return await _unwrap(c)(inner, ctx)

        if cmd == "clear_multi_rpc":
            c = getattr(inner, "clear_multi_rpc", None)
            if c is None:
                return await message.channel.send(S.ui_err("clear_multi_rpc handler missing"))
            return await _unwrap(c)(inner, ctx)

        if cmd == "rpc_status":
            c = getattr(inner, "rpc_status", None)
            if c is None:
                return await message.channel.send(S.ui_err("rpc_status handler missing"))
            return await _unwrap(c)(inner, ctx)

        if cmd == "setpresencestatus":
            if len(args) < 2:
                return await message.channel.send(S.ui_err("usage: setpresencestatus <online|dnd|idle|invisible>"))
            c = getattr(inner, "setpresencestatus_cmd", None)
            if c is None:
                return await message.channel.send(S.ui_err("setpresencestatus handler missing"))
            return await _unwrap(c)(inner, ctx, args[1])

        if cmd in ("spotify", "youtube", "xbox", "ps", "ps4", "crunchy", "vrchat", "meta"):
            c = getattr(inner, f"cmd_{cmd}", None)
            if c is None:
                return await message.channel.send(S.ui_err(f"{cmd} handler missing in rpc cog"))
            rest = " ".join(args[1:]) if len(args) > 1 else None
            return await _unwrap(c)(inner, ctx, args=rest)

        if cmd == "rstatus":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            if not txt:
                return await message.channel.send(S.ui_err("usage: rstatus <a, b, c>"))
            c = getattr(inner, "rotate_status", None)
            if c is None:
                return await message.channel.send(S.ui_err("rotate_status handler missing"))
            return await _unwrap(c)(inner, ctx, statuses=txt)

        if cmd == "remoji":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            if not txt:
                return await message.channel.send(S.ui_err("usage: remoji <a, b, c>"))
            c = getattr(inner, "rotate_emoji", None)
            if c is None:
                return await message.channel.send(S.ui_err("rotate_emoji handler missing"))
            return await _unwrap(c)(inner, ctx, emojis=txt)

        if cmd == "stopstatus":
            c = getattr(inner, "stop_rotate_status", None)
            if c is None:
                return await message.channel.send(S.ui_err("stopstatus handler missing"))
            return await _unwrap(c)(inner, ctx)

        if cmd == "stopemoji":
            c = getattr(inner, "stop_rotate_emoji", None)
            if c is None:
                return await message.channel.send(S.ui_err("stopemoji handler missing"))
            return await _unwrap(c)(inner, ctx)

        # ── `$rpc <slot> <field> <value>` ──
        if cmd == "rpc":
            if len(args) < 2:
                return await message.channel.send(S.ui_info(
                    "usage: rpc <1-6> <field> <value> | rpc status | rpc clearall"))
            sub = args[1].lower()
            if sub == "status":
                c = getattr(inner, "rpc_status", None)
                if c is None:
                    return await message.channel.send(S.ui_err("rpc_status handler missing"))
                return await _unwrap(c)(inner, ctx)
            if sub in ("clearall", "clear"):
                c = getattr(inner, "clear_multi_rpc", None)
                if c is None:
                    return await message.channel.send(S.ui_err("clear_multi_rpc handler missing"))
                return await _unwrap(c)(inner, ctx)
            if sub.isdigit():
                slot = int(sub) - 1
                if slot < 0 or slot > 5:
                    return await message.channel.send(S.ui_err("slot must be 1–6"))
                field = args[2].lower() if len(args) > 2 else ""
                rest = args[3:] if len(args) > 3 else []
                return await self._rpc_slot_sub(ctx, slot, field, rest)
            return await message.channel.send(S.ui_err("unknown rpc subcommand"))

    _SUB_MAP = {
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

    _PARAM_MAP = {
        "name":        "name",
        "details":     "details",
        "state":       "state",
        "type":        "activity_type",
        "platform":    "preset",
        "large_image": "url",
        "small_image": "url",
        "timestamp":   "value",
    }

    async def _rpc_slot_sub(self, ctx, slot: int, sub: str, rest: list):
        """Route $rpcN <sub> <args...> into the upstream RPCCog methods."""
        inner = self._inner
        msg = " ".join(rest)
        suffix = self._SUB_MAP.get(sub)
        if suffix is None:
            return await ctx.send(S.ui_err(f"unknown rpc field: {sub}"))

        full = f"rpc{slot + 1}_{suffix}"
        cmd_obj = getattr(inner, full, None)
        if cmd_obj is None:
            return await ctx.send(S.ui_err(f"{full} not found in rpc cog"))

        # @commands.command / @group.command wraps the method into a Command object.
        # The raw coroutine function is on .callback, and it needs `self` passed
        # explicitly because it is unbound.
        fn = _unwrap(cmd_obj)

        try:
            if sub == "clear":
                return await fn(inner, ctx)
            if sub in ("btn1", "btn2"):
                if len(rest) < 2:
                    return await ctx.send(S.ui_err(f"usage: {sub} <label> <url>"))
                url = rest[-1]
                label = " ".join(rest[:-1])
                return await fn(inner, ctx, label, url)
            if sub in ("spotify", "youtube", "xbox", "ps", "ps4", "crunchy"):
                return await fn(inner, ctx, args=msg or None)
            param = self._PARAM_MAP.get(sub)
            if param is None:
                return await ctx.send(S.ui_err(f"unknown rpc field: {sub}"))
            return await fn(inner, ctx, **{param: msg})
        except TypeError as e:
            return await ctx.send(S.ui_err(f"arg mismatch for {full}: {e}"))


async def setup(bot):
    await bot.add_cog(RPCCog(bot))
