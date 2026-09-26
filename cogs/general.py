# cogs/general.py | ping, info, say, spam, purge, snipe (extended), editsnipe, copycat,
#                   status, platform, hypesquad, sniper, logger, readlog, ar
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


# snipe ignore — per-user filter, module-local because only this cog reads it
_snipe_ignore: set = set()


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
                    break
                batch = await r.json()
        except Exception:
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


# ---------- snipe helpers ----------
def _snipe_entries(cid):
    return S._snipe_cache.get(cid, [])


def _snipe_visible(entries):
    if not _snipe_ignore:
        return entries
    return [e for e in entries if int(e.get("author_id") or 0) not in _snipe_ignore]


def _filter_entries(entries, kind):
    if not kind or kind == "all":
        return entries
    if kind == "attachments":
        return [e for e in entries if e.get("attachments")]
    if kind == "embeds":
        return [e for e in entries if e.get("embeds")]
    if kind == "reactions":
        return [e for e in entries if e.get("reactions")]
    if kind == "text":
        return [e for e in entries if (e.get("content") or "").strip()]
    return entries


def _snipe_render(e, header):
    atts = "\n".join(e.get("attachments", [])) or "none"
    embeds = e.get("embeds") or []
    reacts = e.get("reactions") or []
    rows = [
        f"  {S.DIM}author{S.RESET}      {S.WHITE}{e.get('author','?')}{S.RESET}",
        f"  {S.DIM}deleted{S.RESET}     {e.get('time','?')}",
        f"  {S.DIM}id{S.RESET}          {e.get('message_id','?')}",
        f"  {S.DIM}attachments{S.RESET} {atts}",
    ]
    if embeds:
        rows.append(f"  {S.DIM}embeds{S.RESET}      {len(embeds)}")
    if reacts:
        rows.append(f"  {S.DIM}reactions{S.RESET}   {' '.join(str(r) for r in reacts)}")
    rows += ["", f"  {S.WHITE}{e.get('content') or '(no content)'}{S.RESET}"]
    return S.ui_box(header, rows)


def _clear_snipe_where(entries, *, author_id=None, channel_id=None):
    kept = []
    for e in entries:
        if author_id is not None and int(e.get("author_id") or 0) != int(author_id):
            kept.append(e); continue
        if channel_id is not None and int(e.get("channel_id") or 0) != int(channel_id):
            kept.append(e); continue
    return kept


class GeneralCog:
    COMMANDS = {"ping", "info", "say", "spam", "spamstop", "purge", "purgeall",
                "clear", "snipe", "editsnipe", "esnipe", "copycat",
                "status", "platform", "hypesquad", "sniper", "logger",
                "readlog", "logs", "ar"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "ping":
            lat = getattr(client, "latency", None)
            if lat is None:
                return await message.edit(content=S.ui_info("pong — gateway latency unavailable"))
            try:
                await message.edit(content=S.ui_ok(f"pong — `{round(lat*1000)}ms`"))
            except Exception as e:
                print(f"[ping] edit failed: {e}")

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
                        await message.channel.send(S.ui_ok(f"purged {d}"), delete_after=4)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[purge] {type(e).__name__}: {e}")

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
                        await message.channel.send(S.ui_ok(f"purged {d}"), delete_after=4)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[purgeall] {type(e).__name__}: {e}")

        elif cmd == "clear":
            try: await message.delete()
            except Exception: pass

        elif cmd == "snipe":
            await self._handle_snipe(message, args)

        elif cmd in ("editsnipe", "esnipe"):
            await self._handle_editsnipe(message, args)

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

    # ----------------------------------------------------------
    # snipe dispatcher
    # ----------------------------------------------------------
    async def _handle_snipe(self, message, args):
        try: await message.delete()
        except Exception: pass
        cid = message.channel.id
        sub = args[1].lower() if len(args) > 1 else ""
        rest = args[2:]

        # cache-wide ops
        if sub == "clear":
            S._snipe_cache.pop(cid, None)
            return await message.channel.send(S.ui_ok("snipe cache cleared"), delete_after=4)

        if sub == "cache":
            total = sum(len(v) for v in S._snipe_cache.values())
            rows = [f"  {S.DIM}channels cached{S.RESET}  {len(S._snipe_cache)}",
                    f"  {S.DIM}entries total{S.RESET}    {total}",
                    f"  {S.DIM}this channel{S.RESET}     {len(_snipe_entries(cid))}",
                    f"  {S.DIM}ignored users{S.RESET}    {len(_snipe_ignore)}"]
            return await message.channel.send(S.ui_box("snipe cache", rows), delete_after=8)

        # history
        if sub == "history":
            entries = _snipe_visible(_snipe_entries(cid))
            if not entries:
                return await message.channel.send(S.ui_info("nothing sniped here"), delete_after=5)
            rows = []
            for i, e in enumerate(reversed(entries[-50:]), 1):
                content = (e.get("content") or "")[:60]
                atts = "📎" if e.get("attachments") else "  "
                rows.append(f"  {S.GREY}{i:>3}.{S.RESET} {atts} {S.WHITE}{e.get('author','?')[:18]:<18}"
                            f"{S.RESET} {S.DIM}{e.get('time','?')}{S.RESET}  {content}")
            return await message.channel.send(S._paginate("snipe history", "recent 50", rows))

        # paging
        if sub == "page":
            entries = _snipe_visible(_snipe_entries(cid))
            if not entries:
                return await message.channel.send(S.ui_info("nothing sniped"), delete_after=5)
            try: page = int(rest[0]) if rest and rest[0].isdigit() else 1
            except ValueError: page = 1
            per = 10
            total = max(1, (len(entries) + per - 1) // per)
            page = max(1, min(page, total))
            chunk = entries[(page-1)*per : page*per]
            rows = []
            for i, e in enumerate(chunk, (page-1)*per + 1):
                atts = "📎" if e.get("attachments") else "  "
                rows.append(f"  {S.GREY}{i:>3}.{S.RESET} {atts} {S.WHITE}{e.get('author','?')[:18]:<18}"
                            f"{S.RESET} {S.DIM}{e.get('time','?')}{S.RESET}  "
                            f"{(e.get('content') or '')[:40]}")
            return await message.channel.send(S._paginate("snipe history", f"page {page}/{total}", rows))

        # search
        if sub == "search":
            if not rest:
                return await message.channel.send(S.ui_err("usage: snipe search <keyword>"), delete_after=5)
            kw = " ".join(rest).lower()
            entries = _snipe_visible(_snipe_entries(cid))
            hits = [e for e in entries if kw in (e.get("content") or "").lower()]
            if not hits:
                return await message.channel.send(S.ui_info("no match"), delete_after=5)
            rows = []
            for e in hits[-30:]:
                rows.append(f"  {S.GREY}•{S.RESET} {S.WHITE}{e.get('author','?')[:18]:<18}"
                            f"{S.RESET} {S.DIM}{e.get('time','?')}{S.RESET}  "
                            f"{(e.get('content') or '')[:60]}")
            return await message.channel.send(S._paginate("snipe search", f"`{kw}`", rows))

        # filters
        if sub == "filter":
            if not rest:
                return await message.channel.send(S.ui_info(
                    "usage: snipe filter <attachments|embeds|reactions|text|all|clear>"), delete_after=6)
            kind = rest[0].lower()
            if kind in ("clear", "all", "reset"):
                return await message.channel.send(S.ui_ok("filter reset → all"), delete_after=5)
            entries = _snipe_visible(_snipe_entries(cid))
            hits = _filter_entries(entries, kind)
            if not hits:
                return await message.channel.send(S.ui_info(f"no `{kind}` entries"), delete_after=5)
            rows = []
            for e in hits[-30:]:
                rows.append(f"  {S.GREY}•{S.RESET} {S.WHITE}{e.get('author','?')[:18]:<18}"
                            f"{S.RESET} {S.DIM}{e.get('time','?')}{S.RESET}  "
                            f"{(e.get('content') or '')[:50]}")
            return await message.channel.send(S._paginate("snipe filter", kind, rows))

        # attachments / embeds / reactions / time — shortcuts
        if sub in ("attachments", "embeds", "reactions"):
            entries = _snipe_visible(_snipe_entries(cid))
            hits = _filter_entries(entries, sub)
            if not hits:
                return await message.channel.send(S.ui_info(f"no `{sub}` entries"), delete_after=5)
            rows = []
            for e in hits[-30:]:
                extra = ""
                if sub == "attachments":
                    extra = "  " + ", ".join(e.get("attachments", [])[:1])
                rows.append(f"  {S.GREY}•{S.RESET} {S.WHITE}{e.get('author','?')[:18]:<18}"
                            f"{S.RESET} {S.DIM}{e.get('time','?')}{S.RESET}{extra}")
            return await message.channel.send(S._paginate(f"snipe {sub}", "", rows))

        if sub == "time":
            entries = _snipe_visible(_snipe_entries(cid))
            if not entries:
                return await message.channel.send(S.ui_info("nothing sniped"), delete_after=5)
            now = time.time()
            rows = []
            for e in entries[-30:]:
                ago = int(now - float(e.get("ts", now)))
                rows.append(f"  {S.GREY}•{S.RESET} {e.get('time','?')}  "
                            f"{S.DIM}{ago}s ago{S.RESET}  {e.get('author','?')}")
            return await message.channel.send(S._paginate("snipe timestamps", "", rows))

        # purge
        if sub == "purge":
            target = rest[0].lower() if rest else ""
            if target == "user":
                if len(rest) < 2 or not rest[1].strip("<@!>").isdigit():
                    return await message.channel.send(S.ui_err("usage: snipe purge user <uid>"), delete_after=5)
                uid = int(rest[1].strip("<@!>"))
                removed = 0
                for ch_id, lst in list(S._snipe_cache.items()):
                    kept = [e for e in lst if int(e.get("author_id") or 0) != uid]
                    removed += len(lst) - len(kept)
                    if kept: S._snipe_cache[ch_id] = kept
                    else: S._snipe_cache.pop(ch_id, None)
                return await message.channel.send(S.ui_ok(f"purged {removed} entries by {uid}"))
            if target == "channel":
                n = len(S._snipe_cache.pop(cid, []))
                return await message.channel.send(S.ui_ok(f"purged channel ({n} entries)"))
            if target == "server":
                total = sum(len(v) for v in S._snipe_cache.values())
                S._snipe_cache.clear()
                return await message.channel.send(S.ui_ok(f"purged all ({total} entries)"))
            return await message.channel.send(S.ui_info(
                "usage: snipe purge user <uid> | channel | server"), delete_after=6)

        # ignore list
        if sub == "ignore":
            if not rest:
                return await message.channel.send(S.ui_info(
                    "usage: snipe ignore add/remove/list/clear"), delete_after=6)
            action = rest[0].lower()
            if action == "add" and len(rest) >= 2 and rest[1].strip("<@!>").isdigit():
                _snipe_ignore.add(int(rest[1].strip("<@!>")))
                return await message.channel.send(S.ui_ok(f"ignoring {rest[1]}"))
            if action == "remove" and len(rest) >= 2 and rest[1].strip("<@!>").isdigit():
                _snipe_ignore.discard(int(rest[1].strip("<@!>")))
                return await message.channel.send(S.ui_ok("removed"))
            if action == "clear":
                _snipe_ignore.clear()
                return await message.channel.send(S.ui_ok("ignore list cleared"))
            if action == "list":
                if not _snipe_ignore:
                    return await message.channel.send(S.ui_info("ignore list empty"))
                rows = [f"  {S.GREY}•{S.RESET} <@{u}>" for u in sorted(_snipe_ignore)]
                return await message.channel.send(S._paginate("snipe ignore", "", rows))
            return await message.channel.send(S.ui_info(
                "usage: snipe ignore add/remove/list/clear"), delete_after=6)

        # default — nth from end, or plain view
        entries = _snipe_visible(_snipe_entries(cid))
        if not entries:
            return await message.channel.send(S.ui_info("nothing to snipe"), delete_after=5)
        try: idx = int(sub) if sub else 1
        except ValueError: idx = 1
        if idx < 1 or idx > len(entries):
            return await message.channel.send(S.ui_err(f"range 1–{len(entries)}"), delete_after=5)
        e = entries[-idx]
        await message.channel.send(_snipe_render(e, f"sniped #{idx}/{len(entries)}"))

    # ----------------------------------------------------------
    # editsnipe dispatcher
    # ----------------------------------------------------------
    async def _handle_editsnipe(self, message, args):
        try: await message.delete()
        except Exception: pass
        cid = message.channel.id
        sub = args[1].lower() if len(args) > 1 else ""

        if sub == "clear":
            S._editsnipe_cache.pop(cid, None)
            return await message.channel.send(S.ui_ok("editsnipe cache cleared"), delete_after=4)

        if sub == "history":
            entries = S._editsnipe_cache.get(cid, [])
            if not entries:
                return await message.channel.send(S.ui_info("no edits"), delete_after=5)
            rows = []
            for i, e in enumerate(reversed(entries[-50:]), 1):
                rows.append(f"  {S.GREY}{i:>3}.{S.RESET} {S.WHITE}{e.get('author','?')[:18]:<18}"
                            f"{S.RESET} {S.DIM}{e.get('time','?')}{S.RESET}  "
                            f"{(e.get('before') or '')[:30]} → {(e.get('after') or '')[:30]}")
            return await message.channel.send(S._paginate("editsnipe history", "", rows))

        if sub == "purge":
            target = args[2].lower() if len(args) > 2 else ""
            if target == "channel":
                n = len(S._editsnipe_cache.pop(cid, []))
                return await message.channel.send(S.ui_ok(f"purged ({n})"))
            if target == "server":
                total = sum(len(v) for v in S._editsnipe_cache.values())
                S._editsnipe_cache.clear()
                return await message.channel.send(S.ui_ok(f"purged all ({total})"))
            if target == "user" and len(args) >= 4 and args[3].strip("<@!>").isdigit():
                uid = int(args[3].strip("<@!>"))
                removed = 0
                for ch_id, lst in list(S._editsnipe_cache.items()):
                    kept = [e for e in lst if int(e.get("author_id") or 0) != uid]
                    removed += len(lst) - len(kept)
                    if kept: S._editsnipe_cache[ch_id] = kept
                    else: S._editsnipe_cache.pop(ch_id, None)
                return await message.channel.send(S.ui_ok(f"purged {removed}"))
            return await message.channel.send(S.ui_info(
                "usage: editsnipe purge user <uid> | channel | server"), delete_after=6)

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