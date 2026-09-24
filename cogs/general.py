# cogs/general.py | ping, info, say, spam, purge, snipe, editsnipe, copycat, status, platform, hypesquad, sniper, logger, readlog, ar
import asyncio
import sys
import os
import time
import random
import string
import re
import aiohttp
import modifyself_shim as discord
from . import state as S


def _sync(**kw):
    main = sys.modules.get("__main__")
    if main is None:
        return
    for k, v in kw.items():
        try:
            setattr(main, k, v)
        except Exception:
            pass


def _auth_headers():
    return {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}


async def _purge_own_messages(session, channel_id, own_uid, limit):
    """
    Raw REST purge. Paginates GET /channels/{ch}/messages?limit=100&before=ID
    and deletes every message whose author.id == own_uid.
    Bypasses channel.history() entirely — that path depends on the shim's
    HTTPClient patch landing, which isn't guaranteed on every build.
    """
    h = _auth_headers()
    deleted = 0
    before = None
    guard = 0
    while deleted < limit and guard < 20:
        guard += 1
        qs = "?limit=100"
        if before:
            qs += f"&before={before}"
        try:
            async with session.get(
                f"https://discord.com/api/v9/channels/{channel_id}/messages{qs}",
                headers=h,
            ) as r:
                if r.status != 200:
                    print(f"[purge] history HTTP {r.status}")
                    break
                batch = await r.json()
        except Exception as e:
            print(f"[purge] fetch error: {e}")
            break
        if not batch:
            break
        for m in batch:
            if deleted >= limit:
                break
            author = m.get("author") or {}
            if str(author.get("id")) != str(own_uid):
                continue
            try:
                async with session.delete(
                    f"https://discord.com/api/v9/channels/{channel_id}/messages/{m['id']}",
                    headers=h,
                ) as dr:
                    if dr.status in (200, 204):
                        deleted += 1
                    elif dr.status == 429:
                        try:
                            info = await dr.json()
                            await asyncio.sleep(float(info.get("retry_after", 2.0)))
                        except Exception:
                            await asyncio.sleep(2.0)
            except Exception:
                pass
            await asyncio.sleep(0.35)
        before = batch[-1]["id"]
        if len(batch) < 100:
            break
    return deleted


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
                f"  {S.DIM}servers{S.RESET}  {len(client.guilds)}",
                f"  {S.DIM}prefix{S.RESET}   {S.PREFIX}",
                f"  {S.DIM}platform{S.RESET} {S._current_platform}",
            ]))

        elif cmd == "say":
            await message.edit(content=" ".join(args[1:]))

        elif cmd == "spam":
            if len(args) < 3:
                return await message.edit(content=S.ui_err("usage: spam <n> <text>"))
            try:
                count = int(args[1])
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
            ch_id = message.channel.id
            own_uid = client.user.id
            try: await message.delete()
            except Exception: pass
            try:
                async with aiohttp.ClientSession() as session:
                    d = await _purge_own_messages(session, ch_id, own_uid, limit)
                if d:
                    try:
                        await message.channel.send(
                            S.ui_ok(f"purged {d}"), delete_after=4)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[purge] {type(e).__name__}: {e}")
                try:
                    await message.channel.send(
                        S.ui_err(f"purge: {e}"), delete_after=6)
                except Exception:
                    pass

        elif cmd == "purgeall":
            ch_id = message.channel.id
            own_uid = client.user.id
            try: await message.delete()
            except Exception: pass
            try:
                async with aiohttp.ClientSession() as session:
                    d = await _purge_own_messages(session, ch_id, own_uid, 1000)
                if d:
                    try:
                        await message.channel.send(
                            S.ui_ok(f"purged {d}"), delete_after=4)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[purgeall] {type(e).__name__}: {e}")

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
                return m.author.id == uid and m.channel_id == message.channel.id
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
            _sync(_current_platform=plat)
            await message.edit(content=S.ui_ok(f"platform → {plat}"))
            try:
                gw = getattr(client, "_gateway", None)
                if gw:
                    await gw.close()
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
            _sync(SNIPER_ENABLED=S.SNIPER_ENABLED)
            await message.edit(content=S.ui_ok(
                f"sniper → {'ON' if S.SNIPER_ENABLED else 'OFF'}"))

        elif cmd == "logger":
            S.LOGGER_ENABLED = len(args) < 2 or args[1].lower() == "on"
            _sync(LOGGER_ENABLED=S.LOGGER_ENABLED)
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
