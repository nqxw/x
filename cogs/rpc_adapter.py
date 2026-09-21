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
            fn = getattr(inner, "playing_cmd", None)
            if fn is None:
                return await message.channel.send(S.ui_err("playing handler missing in rpc cog"))
            return await fn(ctx, message=txt)
        if cmd in ("listening", "listen"):
            txt = " ".join(args[1:]) if len(args) > 1 else None
            fn = getattr(inner, "listening_cmd", None)
            if fn is None:
                return await message.channel.send(S.ui_err("listening handler missing in rpc cog"))
            return await fn(ctx, message=txt)
        if cmd in ("watching", "watch"):
            txt = " ".join(args[1:]) if len(args) > 1 else None
            fn = getattr(inner, "watching_cmd", None)
            if fn is None:
                return await message.channel.send(S.ui_err("watching handler missing in rpc cog"))
            return await fn(ctx, message=txt)
        if cmd == "competing":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            fn = getattr(inner, "competing_cmd", None)
            if fn is None:
                return await message.channel.send(S.ui_err("competing handler missing in rpc cog"))
            return await fn(ctx, message=txt)
        if cmd == "stopactivity":
            return await inner.stopactivity_cmd(ctx)
        if cmd == "aoff":
            return await inner.aoff_cmd(ctx)
        if cmd == "clear_multi_rpc":
            return await inner.clear_multi_rpc(ctx)
        if cmd == "rpc_status":
            return await inner.rpc_status(ctx)

        if cmd in ("spotify", "youtube", "xbox", "ps", "ps4", "crunchy", "vrchat", "meta"):
            method = getattr(inner, f"cmd_{cmd}", None)
            if method is None:
                return await message.channel.send(S.ui_err(f"{cmd} handler missing in rpc cog"))
            rest = " ".join(args[1:]) if len(args) > 1 else None
            return await method(ctx, args=rest)

        if cmd == "rstatus":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            if not txt:
                return await message.channel.send(S.ui_err("usage: rstatus <a, b, c>"))
            return await inner.rotate_status(ctx, statuses=txt)
        if cmd == "remoji":
            txt = " ".join(args[1:]) if len(args) > 1 else None
            if not txt:
                return await message.channel.send(S.ui_err("usage: remoji <a, b, c>"))
            return await inner.rotate_emoji(ctx, emojis=txt)
        if cmd == "stopstatus":
            return await inner.stop_rotate_status(ctx)
        if cmd == "stopemoji":
            return await inner.stop_rotate_emoji(ctx)

        # ── `$rpc <slot> <field> <value>` ──
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
                rest = args[3:] if len(args) > 3 else []
                return await self._rpc_slot_sub(ctx, slot, field, rest)
            return await message.channel.send(S.ui_err("unknown rpc subcommand"))

    # sub → (method suffix, param name | None for no-arg | "buttons" for label+url)
    _SUB_MAP = {
        "name":        ("name", "name"),
        "details":     ("details", "details"),
        "state":       ("state", "state"),
        "type":        ("type", "activity_type"),
        "platform":    ("platform", "preset"),
        "large_image": ("large_image", "url"),
        "small_image": ("small_image", "url"),
        "timestamp":   ("timestamp", "value"),
        "btn1":        ("btn1", "buttons"),
        "btn2":        ("btn2", "buttons"),
        "spotify":     ("spotify", "args"),
        "youtube":     ("youtube", "args"),
        "xbox":        ("xbox", "args"),
        "ps":          ("ps", "args"),
        "ps4":         ("ps4", "args"),
        "crunchy":     ("crunchy", "args"),
        "clear":       ("clear", None),
    }

    async def _rpc_slot_sub(self, ctx, slot: int, sub: str, rest: list):
        """Route $rpcN <sub> <args...> into the upstream RPCCog methods."""
        inner = self._inner
        msg = " ".join(rest)
        entry = self._SUB_MAP.get(sub)
        if entry is None:
            return await ctx.send(S.ui_err(f"unknown rpc field: {sub}"))
        suffix, param = entry

        full = f"rpc{slot + 1}_{suffix}"
        fn = getattr(inner, full, None)
        if fn is None:
            return await ctx.send(S.ui_err(f"{full} not found in rpc cog"))

        try:
            if param is None:
                # clear — no args
                return await fn(ctx)
            if param == "buttons":
                # btn1/btn2 — takes (ctx, label, url) positionally
                if len(rest) < 2:
                    return await ctx.send(S.ui_err(f"usage: {sub} <label> <url>"))
                url = rest[-1]
                label = " ".join(rest[:-1])
                return await fn(ctx, label, url)
            # everything else is keyword-bound
            return await fn(ctx, **{param: msg})
        except TypeError as e:
            return await ctx.send(S.ui_err(f"arg mismatch for {full}: {e}"))
