# cogs/general.py | ping, info, say, spam, purge, snipe, editsnipe, copycat, status, platform, hypesquad, sniper, logger, readlog, ar
import asyncio
import os
import time
import random
import string
import re
import discord
from . import state as S


async def _spam_worker(channel, count, text):
    try:
        for _ in range(count):
            await channel.send(text)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[spam] {e}")


class GeneralCog:
    COMMANDS = {"ping", "info", "say", "spam", "spamstop", "purge", "purgeall",
                "clear", "snipe", "editsnipe", "esnipe", "copycat",
                "status", "platform", "hypesquad", "sniper", "logger",
                "readlog", "logs", "ar"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "ping":
            await message.edit(content=S.ui_ok(f"pong — `{round(client.latency*1000)}ms`"))

        elif cmd == "info":
            u = client.user
            await message.edit(content=S.ui_box("account", [
                f"  {S.DIM}user{S.RESET}     {S.WHITE}{u}{S.RESET}",
                f"  {S.DIM}id{S.RESET}       {u.id}",
                f"  {S.DIM}created{S.RESET}  {u.created_at.strftime('%Y-%m-%d')}",
                f"  {S.DIM}servers{S.RESET}  {len(client.guilds)}",
                f"  {S.DIM}prefix{S.RESET}   {S.PREFIX}",
                f"  {S.DIM}platform{S.RESET} {S._current_platform}",
            ]))

        elif cmd == "say":
            await message.edit(content=" ".join(args[1:]))

        elif cmd == "spam":
            if len(args) < 3:
                return await message.edit(content=S.ui_err("usage: spam <n> <text>"))
            try: count = int(args[1])
            except ValueError:
                return await message.edit(content=S.ui_err("n must be a number"))
            count = min(count, 200)
            text = " ".join(args[2:])
            cid = message.channel.id
            ex = S._spam_tasks.get(cid)
            if ex and not ex.done():
                ex.cancel()
                try: await ex
                except Exception: pass
            try: await message.delete()
            except Exception: pass
            S._spam_tasks[cid] = asyncio.create_task(
                _spam_worker(message.channel, count, text))

        elif cmd == "spamstop":
            cid = message.channel.id
            t = S._spam_tasks.get(cid)
            if not t or t.done():
                killed = 0
                for _cid, tt in list(S._spam_tasks.items()):
                    if tt and not tt.done():
                        tt.cancel(); killed += 1
                S._spam_tasks.clear()
                await message.edit(content=S.ui_ok(f"stopped {killed}")
                                            if killed else S.ui_info("no active spam"))
                return
            t.cancel()
            try: await t
            except Exception: pass
            S._spam_tasks.pop(cid, None)
            await message.edit(content=S.ui_ok("spam stopped"))

        elif cmd == "purge":
            limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
            try: await message.delete()
            except Exception: pass
            d = 0
            async for msg in message.channel.history(limit=500):
                if msg.author.id == client.user.id:
                    try: await msg.delete()
                    except Exception: pass
                    d += 1
                    await asyncio.sleep(0.3)
                    if d >= limit: break

        elif cmd == "purgeall":
            try: await message.delete()
            except Exception: pass
            async for msg in message.channel.history(limit=1000):
                if msg.author.id == client.user.id:
                    try: await msg.delete()
                    except Exception: pass
                    await asyncio.sleep(0.3)

        elif cmd == "clear":
            try: await message.delete()
            except Exception: pass

        elif cmd == "snipe":
            try: await message.delete()
            except Exception: pass
            sub = args[1].lower() if len(args) > 1 else ""
            cid = message.channel.id
            if sub == "clear":
                S._snipe_cache.pop(cid, None)
                return await message.channel.send(S.ui_ok("snipe cache cleared"), delete_after=4)
            entries = S._snipe_cache.get(cid, [])
            if not entries:
                return await message.channel.send(S.ui_info("nothing to snipe"), delete_after=5)
            try: idx = int(sub) if sub else 1
            except ValueError: idx = 1
            if idx < 1 or idx > len(entries):
                return await message.channel.send(S.ui_err(f"range 1–{len(entries)}"), delete_after=5)
            e = entries[-idx]
            atts = "\n".join(e.get("attachments", [])) or "none"
            await message.channel.send(S.ui_box(f"sniped #{idx}/{len(entries)}", [
                f"  {S.DIM}author{S.RESET}      {S.WHITE}{e['author']}{S.RESET}",
                f"  {S.DIM}deleted{S.RESET}     {e['time']}",
                f"  {S.DIM}attachments{S.RESET} {atts}", "",
                f"  {S.WHITE}{e['content'] or '(no content)'}{S.RESET}",
            ]))

        elif cmd in ("editsnipe", "esnipe"):
            try: await message.delete()
            except Exception: pass
            sub = args[1].lower() if len(args) > 1 else ""
            cid = message.channel.id
            if sub == "clear":
                S._editsnipe_cache.pop(cid, None)
                return await message.channel.send(S.ui_ok("editsnipe cache cleared"), delete_after=4)
            entries = S._editsnipe_cache.get(cid, [])
            if not entries:
                return await message.channel.send(S.ui_info("no edits"), delete_after=5)
            try: idx = int(sub) if sub else 1
            except ValueError: idx = 1
            if idx < 1 or idx > len(entries):
                return await message.channel.send(S.ui_err(f"range 1–{len(entries)}"), delete_after=5)
            e = entries[-idx]
            await message.channel.send(S.ui_box(f"sniped edit #{idx}/{len(entries)}", [
                f"  {S.DIM}author{S.RESET}  {S.WHITE}{e['author']}{S.RESET}",
                f"  {S.DIM}edited{S.RESET}  {e['time']}", "",
                f"  {S.DIM}before:{S.RESET}",
                f"  {S.WHITE}{e['before'] or '(empty)'}{S.RESET}", "",
                f"  {S.DIM}after:{S.RESET}",
                f"  {S.WHITE}{e['after'] or '(empty)'}{S.RESET}",
            ]))

        elif cmd == "copycat":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: copycat <user_id>"))
            try: uid = int(args[1])
            except ValueError:
                return await message.edit(content=S.ui_err("invalid user id"))
            try: await message.delete()
            except Exception: pass
            def check(m):
                return m.author.id == uid and m.channel.id == message.channel.id
            for _ in range(10):
                try:
                    m = await client.wait_for("message", check=check, timeout=60)
                    await message.channel.send(m.content)
                except asyncio.TimeoutError:
                    break

        elif cmd == "status":
            if len(args) < 2 or args[1].lower() == "clear":
                await client.change_presence(activity=None)
                await message.edit(content=S.ui_ok("status cleared"))
            else:
                text = " ".join(args[1:])
                await client.change_presence(activity=discord.CustomActivity(name=text))
                await message.edit(content=S.ui_ok(f"status set: {text}"))

        elif cmd == "platform":
            if len(args) < 2:
                return await message.edit(content=S.ui_info(
                    f"platform: {S._current_platform}\ntypes: {' '.join(S.PLATFORM_MAP)}"))
            plat = "desktop" if args[1].lower() == "off" else args[1].lower()
            if plat not in S.PLATFORM_MAP:
                return await message.edit(content=S.ui_err(f"unknown platform: {plat}"))
            S._current_platform = plat
            await message.edit(content=S.ui_ok(f"platform → {plat}"))
            try:
                ws = getattr(client, "ws", None)
                if ws:
                    await ws.close(code=4000)
            except Exception: pass

        elif cmd == "hypesquad":
            if len(args) < 2:
                return await message.edit(content=S.ui_err(
                    "usage: hypesquad bravery/brilliance/balance/off"))
            sub = args[1].lower()
            if sub == "off":
                ok = await S.clear_hypesquad() if S.clear_hypesquad else False
                await message.edit(content=S.ui_ok("removed") if ok else S.ui_err("failed"))
            elif sub in S.HOUSE_IDS:
                ok, _ = (await S.set_hypesquad(S.HOUSE_IDS[sub])
                         if S.set_hypesquad else (False, ""))
                await message.edit(content=S.ui_ok(
                    f"house {S.HOUSE_NAMES[S.HOUSE_IDS[sub]]}") if ok else S.ui_err("failed"))
            else:
                await message.edit(content=S.ui_err("unknown house"))

        elif cmd == "sniper":
            S.SNIPER_ENABLED = len(args) < 2 or args[1].lower() == "on"
            await message.edit(content=S.ui_ok(
                f"sniper → {'ON' if S.SNIPER_ENABLED else 'OFF'}"))

        elif cmd == "logger":
            S.LOGGER_ENABLED = len(args) < 2 or args[1].lower() == "on"
            await message.edit(content=S.ui_ok(
                f"logger → {'ON' if S.LOGGER_ENABLED else 'OFF'}"))

        elif cmd in ("readlog", "logs"):
            n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
            if not os.path.exists(S.LOG_FILE):
                return await message.edit(content=S.ui_err("no log"))
            with open(S.LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            tail = "".join(lines[-n:])
            if len(tail) > 1900: tail = tail[-1900:]
            await message.edit(content=f"```\n{tail}\n```")

        elif cmd == "ar":
            sub = args[1].lower() if len(args) > 1 else ""
            rest = " ".join(args[2:])
            if sub == "add":
                if "|" not in rest:
                    return await message.edit(content=S.ui_err(
                        "format: ar add trigger | response"))
                trig, resp = rest.split("|", 1)
                S.AUTO_RESPONSES[trig.strip()] = resp.strip()
                await message.edit(content=S.ui_ok(f"added: `{trig.strip()}`"))
            elif sub == "remove":
                S.AUTO_RESPONSES.pop(rest.strip(), None)
                await message.edit(content=S.ui_ok(f"removed: `{rest.strip()}`"))
            elif sub == "list":
                rows = [f"  {S.GREY}├{S.RESET} {k}  {S.DIM}→ {v}{S.RESET}"
                        for k, v in list(S.AUTO_RESPONSES.items())]
                await message.edit(content=S._paginate("ar", "auto-responder", rows)
                                            if rows else S.ui_info("none set"))
            else:
                await message.edit(content=S.ui_info("usage: ar add/remove/list"))