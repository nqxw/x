# cogs/host.py | admin + host (add/list/start/stop/say/broadcast) + admin owner
import asyncio
import time
import traceback
import aiohttp
import modifyself_shim as discord

from . import state as S


async def _run_hosted_client(hc, tok):
    try:
        await hc.start()
    except Exception as e:
        print(f"[hosted:{getattr(hc, '_bot_index', '?')}] CONNECT ERROR: {type(e).__name__}: {e}")


async def _spawn_one_hosted(token: str, prefix: str):
    try:
        hc = discord.Client(token=token)
        hc._host_prefix = prefix

        @hc.event
        async def _hc_on_ready(_hc=hc):
            print(f"[host] ✓ {_hc.user} online (prefix {prefix})")

        @hc.event
        async def _hc_on_message(_m, _hc=hc):
            if S.HOSTED_DISPATCH is not None:
                try: await S.HOSTED_DISPATCH(_hc, _m)
                except Exception as e:
                    print(f"[host] dispatch error: {e}")

        asyncio.create_task(_run_hosted_client(hc, token))
        for _ in range(30):
            if hc.user is not None: break
            await asyncio.sleep(0.5)

        return {
            "token": token, "client": hc, "user": hc.user,
            "started": time.time(), "status": "online", "prefix": prefix,
        }
    except Exception as e:
        print(f"[host] spawn failed: {e}")
        return None


class HostCog:
    COMMANDS = {"admin", "host"}

    async def handle(self, message, cmd, args):
        if cmd == "admin":
            await self._admin(message, args)
        elif cmd == "host":
            await self._host(message, args)

    async def _admin(self, message, args):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""

        if sub == "setowner":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: admin setowner <uid>"), delete_after=6)
            uid = S._parse_uid_str(args[2])
            if not uid:
                return await message.channel.send(S.ui_err("invalid uid"), delete_after=6)
            current_owner = S._owner_id()
            if current_owner and not S._is_owner(message.author.id):
                return await message.channel.send(S.ui_err("owner already set — owner only"), delete_after=6)
            S._set_owner(uid)
            await message.channel.send(S.ui_ok(f"owner set to {uid}"), delete_after=8)

        elif sub == "add":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: admin add <uid>"), delete_after=6)
            if not S._is_owner(message.author.id):
                return await message.channel.send(S.ui_err("owner only"), delete_after=6)
            uid = S._parse_uid_str(args[2])
            if not uid:
                return await message.channel.send(S.ui_err("invalid uid"), delete_after=6)
            admins = S._load_admins()
            if uid in admins:
                return await message.channel.send(S.ui_warn("already admin"), delete_after=6)
            admins.append(uid); S._save_admins(admins)
            await message.channel.send(S.ui_ok(f"added {uid}"), delete_after=6)

        elif sub == "remove":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: admin remove <uid>"), delete_after=6)
            if not S._is_owner(message.author.id):
                return await message.channel.send(S.ui_err("owner only"), delete_after=6)
            uid = S._parse_uid_str(args[2])
            if not uid:
                return await message.channel.send(S.ui_err("invalid uid"), delete_after=6)
            admins = S._load_admins()
            if uid not in admins:
                return await message.channel.send(S.ui_warn("not admin"), delete_after=6)
            admins.remove(uid); S._save_admins(admins)
            await message.channel.send(S.ui_ok(f"removed {uid}"), delete_after=6)

        elif sub == "list" or not sub:
            o = S._owner_id()
            admins = S._load_admins()
            rows = []
            if o: rows.append(f"  {S.DIM}owner{S.RESET}  <@{o}>")
            for a in admins:
                if a != o: rows.append(f"  {S.DIM}admin{S.RESET}  <@{a}>")
            await message.channel.send(
                S.ui_box("admins", rows) if rows else S.ui_info("no admins configured"),
                delete_after=15)

    async def _host(self, message, args):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""

        if sub == "add":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: host add <token>"), delete_after=6)
            t = args[2].strip().strip('"').strip("'")
            if t in S.HOSTED_TOKENS:
                return await message.channel.send(S.ui_warn("already in hosted list"), delete_after=6)
            uname = await S.hosted_username(t)
            if not uname or uname == "?":
                return await message.channel.send(S.ui_err("invalid or dead token"), delete_after=6)
            try:
                add = S.async_hosted_token_add
                await add(t, username=uname)
                S.HOSTED_TOKENS.append(t)
                await message.channel.send(S.ui_ok(f"hosted {uname}"), delete_after=8)
            except Exception as e:
                await message.channel.send(S.ui_err(f"write failed: {e}"), delete_after=8)

        elif sub in ("remove", "rem"):
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: host remove <idx_or_token>"), delete_after=6)
            ref = args[2].strip().strip('"').strip("'")
            target = None
            if ref.isdigit():
                i = int(ref)
                if 0 <= i < len(S.HOSTED_TOKENS): target = S.HOSTED_TOKENS[i]
            elif ref in S.HOSTED_TOKENS:
                target = ref
            if not target:
                return await message.channel.send(S.ui_err("not in list"), delete_after=6)
            try:
                await S.async_hosted_token_remove(target)
                if target in S.HOSTED_TOKENS: S.HOSTED_TOKENS.remove(target)
                await message.channel.send(S.ui_ok("removed"), delete_after=6)
            except Exception as e:
                await message.channel.send(S.ui_err(f"delete failed: {e}"), delete_after=6)

        elif sub == "list" or (not sub and len(args) == 1):
            if not S.HOSTED_TOKENS:
                return await message.channel.send(S.ui_info("no hosted tokens"), delete_after=6)
            rows = []
            for i, t in enumerate(S.HOSTED_TOKENS):
                uname = await S.hosted_username(t)
                rows.append(f"  {S.GREY}[{i}]{S.RESET} {S.WHITE}{uname}{S.RESET}  {S.DIM}{t[:20]}...{S.RESET}")
            await message.channel.send(S._paginate("host", "hosted accounts", rows), delete_after=20)

        elif sub == "sessions":
            if not S._host_sessions:
                return await message.channel.send(S.ui_info("no live sessions"), delete_after=6)
            rows = []
            for i, s in enumerate(S._host_sessions, 1):
                u = s.get("user")
                name = getattr(u, "display_name", None) or getattr(u, "name", "?") if u else "?"
                up = int(time.time() - s.get("started", time.time()))
                rows.append(f"  {S.GREY}[{i}]{S.RESET} {S.WHITE}{name}{S.RESET}  {S.DIM}{s.get('status','?')} {up}s{S.RESET}")
            await message.channel.send(S._paginate("sessions", "live", rows), delete_after=20)

        elif sub == "info":
            if len(args) < 3 or not args[2].isdigit():
                return await message.channel.send(S.ui_err("usage: host info <idx>"), delete_after=6)
            i = int(args[2])
            if i >= len(S.HOSTED_TOKENS):
                return await message.channel.send(S.ui_err("index out of range"), delete_after=6)
            t = S.HOSTED_TOKENS[i]
            info = await S.hosted_info(t)
            await message.channel.send(S.ui_box("hosted account", [
                f"  {S.DIM}index{S.RESET}     [{i}]",
                f"  {S.DIM}username{S.RESET}  {info['username']}",
                f"  {S.DIM}id{S.RESET}        {info['id']}",
                f"  {S.DIM}valid{S.RESET}     {'yes' if info['valid'] else 'no'}",
                f"  {S.DIM}token{S.RESET}     {t[:12]}...{t[-6:]}",
            ]), delete_after=20)

        elif sub == "start":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: host start <idx_or_token>"), delete_after=6)
            ref = args[2].strip().strip('"').strip("'")
            token = None
            if ref.isdigit():
                i = int(ref)
                if 0 <= i < len(S.HOSTED_TOKENS): token = S.HOSTED_TOKENS[i]
            elif len(ref) > 50:
                token = ref
            if not token:
                return await message.channel.send(S.ui_err("no token found"), delete_after=6)
            if any(s.get("token") == token for s in S._host_sessions):
                return await message.channel.send(S.ui_warn("already running"), delete_after=6)
            st = await _spawn_one_hosted(token, S.PREFIX)
            if not st:
                return await message.channel.send(S.ui_err("spawn failed"), delete_after=6)
            S._host_sessions.append(st)
            u = st.get("user")
            name = getattr(u, "display_name", None) or getattr(u, "name", "?") if u else "?"
            await message.channel.send(S.ui_ok(f"started {name} as session #{len(S._host_sessions)}"), delete_after=8)

        elif sub == "stop":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: host stop <idx_or_uid>"), delete_after=6)
            s = S._get_host_session(args[2])
            if not s:
                return await message.channel.send(S.ui_err("no such session"), delete_after=6)
            cl = s.get("client")
            if cl:
                try: await cl.close()
                except Exception: pass
            S._host_sessions.remove(s)
            u = s.get("user")
            name = getattr(u, "display_name", None) or getattr(u, "name", "?") if u else "?"
            await message.channel.send(S.ui_ok(f"stopped {name}"), delete_after=6)

        elif sub == "stopall":
            if not S._host_sessions:
                return await message.channel.send(S.ui_info("nothing to stop"), delete_after=6)
            n = len(S._host_sessions)
            for s in list(S._host_sessions):
                cl = s.get("client")
                if cl:
                    try: await cl.close()
                    except Exception: pass
            S._host_sessions.clear()
            await message.channel.send(S.ui_ok(f"stopped {n} session(s)"), delete_after=6)

        elif sub == "all":
            if not S.HOSTED_TOKENS:
                return await message.channel.send(S.ui_warn("no tokens"), delete_after=6)
            await message.channel.send(S.ui_info(f"hosting {len(S.HOSTED_TOKENS)} token(s)..."), delete_after=6)
            hosted = 0; failed = 0
            for t in S.HOSTED_TOKENS:
                if any(s.get("token") == t for s in S._host_sessions): continue
                st = await _spawn_one_hosted(t, S.PREFIX)
                if not st: failed += 1; continue
                S._host_sessions.append(st)
                hosted += 1
                await asyncio.sleep(0.5)
            await message.channel.send(S.ui_ok(f"hosted {hosted}, failed {failed}"), delete_after=10)

        elif sub == "say":
            if len(args) < 4:
                return await message.channel.send(S.ui_err("usage: host say <idx> <msg>"), delete_after=6)
            s = S._get_host_session(args[2])
            if not s:
                return await message.channel.send(S.ui_err("no such session"), delete_after=6)
            ok = await S.hosted_send_via_client(s, message.channel.id, " ".join(args[3:]))
            if not ok:
                ok = await S.hosted_send(s["token"], message.channel.id, " ".join(args[3:]))
            await message.channel.send(S.ui_ok("sent") if ok else S.ui_err("failed"), delete_after=6)

        elif sub == "broadcast":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: host broadcast <msg>"), delete_after=6)
            text = " ".join(args[2:])
            if not S._host_sessions and not S.HOSTED_TOKENS:
                return await message.channel.send(S.ui_warn("no hosted accounts"), delete_after=6)
            ok = 0; total = 0; failed = 0
            for s in S._host_sessions:
                total += 1
                if await S.hosted_send_via_client(s, message.channel.id, text): ok += 1
                else: failed += 1
                await asyncio.sleep(0.5)
            await message.channel.send(S.ui_ok(f"sent from {ok}/{total}" + (f" ({failed} failed)" if failed else "")), delete_after=10)

        elif sub == "clear":
            n = len(S.HOSTED_TOKENS)
            for t in list(S.HOSTED_TOKENS):
                try: await S.async_hosted_token_remove(t)
                except Exception: pass
            S.HOSTED_TOKENS.clear()
            await message.channel.send(S.ui_ok(f"cleared {n} token(s)"), delete_after=6)

        else:
            await message.channel.send(S.ui_info("usage: host add/list/start/stop/say/broadcast/clear"))