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
        """Instantiate the upstream RPCCog with the live client."""
        try:
            from cogs.rpc import RPCCog
        except Exception as e:
            print(f"[rpc-adapter] cannot import cogs.rpc: {type(e).__name__}: {e}")
            return
        try:
            self._inner = RPCCog(client)
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

        # The upstream cog uses discord.ext.commands style invocation (ctx).
        # We can't call those directly, so we route through the inner methods.
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
            # args[0] is the subcommand, e.g. "name" / "details" / "state" / etc.
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
            method = getattr(inner, f"cmd_{cmd}", None)
            if method is None:
                # some use different names
                method = {
                    "spotify": inner.cmd_spotify if hasattr(inner, "cmd_spotify") else None,
                    "youtube": inner.cmd_youtube if hasattr(inner, "cmd_youtube") else None,
                    "xbox": inner.cmd_xbox if hasattr(inner, "cmd_xbox") else None,
                    "ps": inner.cmd_ps if hasattr(inner, "cmd_ps") else None,
                    "ps4": inner.cmd_ps4 if hasattr(inner, "cmd_ps4") else None,
                    "crunchy": inner.cmd_crunchy if hasattr(inner, "cmd_crunchy") else None,
                    "vrchat": inner.cmd_vrchat if hasattr(inner, "cmd_vrchat") else None,
                    "meta": inner.cmd_meta if hasattr(inner, "cmd_meta") else None,
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
        # upstream methods take ctx and explicit named args; map by name
        method_map = {
            "name": ("rpc_name", "name"),
            "details": ("rpc_details", "details"),
            "state": ("rpc_state", "state"),
            "type": ("rpc_type", "activity_type"),
            "platform": ("rpc_platform", "preset"),
            "large_image": ("rpc_large_image", "url"),
            "small_image": ("rpc_small_image", "url"),
            "timestamp": ("rpc_timestamp", "value"),
            "btn1": ("rpc_btn1", None),
            "btn2": ("rpc_btn2", None),
            "spotify": ("rpc_spotify", "args"),
            "youtube": ("rpc_youtube", "args"),
            "xbox": ("rpc_xbox", "args"),
            "ps": ("rpc_ps", "args"),
            "ps4": ("rpc_ps4", "args"),
            "crunchy": ("rpc_crunchy", "args"),
            "clear": ("rpc_clear", None),
        }
        entry = method_map.get(sub)
        if entry is None:
            return await ctx.send(S.ui_err(f"unknown rpc field: {sub}"))
        method_name, kw = entry
        # the slot-numbered methods are named like rpc1_name, rpc2_name, ...
        full = f"rpc{slot + 1}_{method_name.split('_', 1)[1]}"
        fn = getattr(inner, full, None)
        if fn is None:
            return await ctx.send(S.ui_err(f"{full} not found in rpc cog"))

        try:
            if sub in ("btn1", "btn2"):
                # expects label + url
                parts = rest
                if len(parts) < 2:
                    return await ctx.send(S.ui_err(f"usage: {sub} <label> <url>"))
                url = parts[-1]
                label = " ".join(parts[:-1])
                return await fn(ctx, label, url)
            if kw is None:
                return await fn(ctx)
            if sub == "type":
                return await fn(ctx, msg)
            if sub == "platform":
                return await fn(ctx, msg)
            if sub in ("large_image", "small_image"):
                return await fn(ctx, msg)
            if sub == "timestamp":
                return await fn(ctx, msg)
            if sub in ("spotify", "youtube", "xbox", "ps", "ps4", "crunchy"):
                return await fn(ctx, msg or None)
            # name/details/state
            return await fn(ctx, msg)
        except TypeError as e:
            return await ctx.send(S.ui_err(f"arg mismatch for {full}: {e}"))
