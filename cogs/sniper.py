# cogs/sniper.py | full delete/edit snipe suite — search, filters, stats, pagination
import asyncio
import time
from datetime import datetime
import modifyself_shim as discord
from . import state as S


# ── module state ──
_snipe_ignore_users:    set = set()
_snipe_ignore_channels: set = set()
_snipe_ignore_servers:  set = set()
_snipe_filter_users:    set = set()
_snipe_filter_channels: set = set()

# rolling global log — cross-channel queries, timestamps, search
_history_log: list = []
_HISTORY_CAP = 4000


def _record(entry: dict, kind: str = "delete"):
    entry = dict(entry)
    entry["kind"] = kind
    _history_log.append(entry)
    if len(_history_log) > _HISTORY_CAP:
        del _history_log[:-_HISTORY_CAP]


def _ignored(message) -> bool:
    if message.author.id in _snipe_ignore_users: return True
    if message.channel.id in _snipe_ignore_channels: return True
    if getattr(message, "guild", None) and message.guild.id in _snipe_ignore_servers:
        return True
    return False


def _entry_block(e: dict, idx: int = 0) -> list:
    head = f"  {S.DIM}#{idx}{S.RESET}  {S.WHITE}{e.get('author','?')}{S.RESET}  " \
           f"{S.DIM}{e.get('time','?')}{S.RESET}"
    body = f"  {S.WHITE}{e.get('content') or '(no content)'}{S.RESET}"
    lines = [head, body]
    atts = e.get("attachments") or []
    if atts:
        lines.append(f"  {S.DIM}attachments:{S.RESET}")
        for a in atts[:5]:
            lines.append(f"    {S.CYAN}•{S.RESET} {a}")
    embs = e.get("embeds") or []
    if embs:
        lines.append(f"  {S.DIM}embeds: {len(embs)}{S.RESET}")
        for eb in embs[:3]:
            t = eb.get("title") or eb.get("description") or eb.get("url") or "(embed)"
            lines.append(f"    {S.CYAN}•{S.RESET} {str(t)[:100]}")
    reacts = e.get("reactions") or []
    if reacts:
        lines.append(f"  {S.DIM}reactions:{S.RESET}")
        lines.append(f"    {S.CYAN}{' '.join(reacts[:8])}{S.RESET}")
    return lines


class SniperCog:
    COMMANDS = {
        "snipe", "snipehistory", "snipesearch", "snipepage",
        "snipeattachments", "snipeembeds", "snipereactions", "snipetimestamp",
        "snipeclearcache", "snipeclearuser", "snipeclearchannel", "snipeclearserver",
        "snipeignore", "snipefilter", "snipestats",
        "editsnipe", "esnipe", "editsnipehistory",
    }

    # ── event hooks are handled by selfbot's on_message_delete / on_message_edit
    #    this cog only reads; the two global handlers call _record().
    def register(self, client):
        return

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        try:
            await message.delete()
        except Exception:
            pass
        if client is None:
            return

        if cmd == "snipe":               return await self._snipe(message, args)
        if cmd == "snipehistory":        return await self._history(message, args)
        if cmd == "snipesearch":         return await self._search(message, args)
        if cmd == "snipepage":           return await self._page(message, args)
        if cmd == "snipeattachments":    return await self._attachments(message, args)
        if cmd == "snipeembeds":         return await self._embeds(message, args)
        if cmd == "snipereactions":      return await self._reactions(message, args)
        if cmd == "snipetimestamp":      return await self._timestamp(message, args)
        if cmd == "snipeclearcache":
            S._snipe_cache.clear(); S._editsnipe_cache.clear(); _history_log.clear()
            return await message.channel.send(S.ui_ok("all snipe caches cleared"))
        if cmd == "snipeclearuser":      return await self._clear_user(message, args)
        if cmd == "snipeclearchannel":   return await self._clear_channel(message, args)
        if cmd == "snipeclearserver":    return await self._clear_server(message)
        if cmd == "snipeignore":         return await self._ignore(message, args)
        if cmd == "snipefilter":         return await self._filter(message, args)
        if cmd == "snipestats":          return await self._stats(message)
        if cmd in ("editsnipe", "esnipe"): return await self._editsnipe(message, args)
        if cmd == "editsnipehistory":    return await self._editsnipe_history(message, args)

    # ── core snipe ──
    async def _snipe(self, message, args):
        cid = message.channel.id
        entries = S._snipe_cache.get(cid, [])
        if not entries:
            return await message.channel.send(S.ui_info("nothing to snipe"), delete_after=6)
        sub = args[0].lower() if args else ""

        if sub == "clear":
            S._snipe_cache.pop(cid, None)
            return await message.channel.send(S.ui_ok("channel cache cleared"), delete_after=5)
        if sub == "history":
            return await self._history(message, args[1:])

        try: n = int(sub) if sub else 1
        except ValueError: n = 1
        if n < 1 or n > len(entries):
            return await message.channel.send(
                S.ui_err(f"range 1–{len(entries)}"), delete_after=5)

        e = entries[-n]
        lines = [f"  {S.WHITE}sniped{S.RESET}  {S.DIM}{n}/{len(entries)}{S.RESET}", ""]
        lines += _entry_block(e, idx=n)
        await message.channel.send(S._ansi_block(lines))

    async def _history(self, message, args):
        cid = message.channel.id
        entries = S._snipe_cache.get(cid, [])
        if not entries:
            return await message.channel.send(S.ui_info("no history"), delete_after=6)
        per = 5
        page = int(args[0]) if args and args[0].isdigit() else 1
        total = max(1, (len(entries) + per - 1) // per)
        page = max(1, min(page, total))
        chunk = entries[::-1][(page-1)*per:page*per]
        lines = [f"  {S.WHITE}snipe history{S.RESET}  {S.DIM}page {page}/{total}{S.RESET}", ""]
        for i, e in enumerate(chunk):
            lines += _entry_block(e, idx=(page-1)*per + i + 1)
            lines.append("")
        lines.append(f"  {S.DIM}next: snipe history {page+1}{S.RESET}")
        await message.channel.send(S._ansi_block(lines))

    async def _search(self, message, args):
        if not args:
            return await message.channel.send(S.ui_err("usage: snipesearch <keyword>"), delete_after=5)
        kw = " ".join(args).lower()
        hits = [e for e in _history_log if kw in (e.get("content") or "").lower()]
        if not hits:
            return await message.channel.send(S.ui_info(f"no match for `{kw}`"), delete_after=6)
        lines = [f"  {S.WHITE}search{S.RESET}  {S.DIM}\"{kw}\" — {len(hits)} hits{S.RESET}", ""]
        for i, e in enumerate(hits[-8:][::-1], 1):
            lines += _entry_block(e, idx=i)
            lines.append("")
        await message.channel.send(S._ansi_block(lines))

    async def _page(self, message, args):
        n = int(args[0]) if args and args[0].isdigit() else 1
        await self._history(message, [str(n)])

    async def _attachments(self, message, args):
        hits = [e for e in _history_log if e.get("attachments")]
        if not hits:
            return await message.channel.send(S.ui_info("no sniped attachments"), delete_after=6)
        lines = [f"  {S.WHITE}attachment snipes{S.RESET}  {S.DIM}{len(hits)}{S.RESET}", ""]
        for i, e in enumerate(hits[-5:][::-1], 1):
            lines += _entry_block(e, idx=i)
            lines.append("")
        await message.channel.send(S._ansi_block(lines))

    async def _embeds(self, message, args):
        hits = [e for e in _history_log if e.get("embeds")]
        if not hits:
            return await message.channel.send(S.ui_info("no sniped embeds"), delete_after=6)
        lines = [f"  {S.WHITE}embed snipes{S.RESET}  {S.DIM}{len(hits)}{S.RESET}", ""]
        for i, e in enumerate(hits[-5:][::-1], 1):
            lines += _entry_block(e, idx=i)
            lines.append("")
        await message.channel.send(S._ansi_block(lines))

    async def _reactions(self, message, args):
        hits = [e for e in _history_log if e.get("reactions")]
        if not hits:
            return await message.channel.send(S.ui_info("no sniped reactions"), delete_after=6)
        lines = [f"  {S.WHITE}reaction snipes{S.RESET}  {S.DIM}{len(hits)}{S.RESET}", ""]
        for i, e in enumerate(hits[-5:][::-1], 1):
            lines += _entry_block(e, idx=i)
            lines.append("")
        await message.channel.send(S._ansi_block(lines))

    async def _timestamp(self, message, args):
        if not args:
            return await message.channel.send(
                S.ui_err("usage: snipetimestamp <unix_or_HH:MM:SS>"), delete_after=6)
        raw = args[0]
        try:
            if ":" in raw:
                h, m, s = (raw.split(":") + ["0","0"])[:3]
                target = datetime.now().replace(hour=int(h), minute=int(m),
                                                second=int(s), microsecond=0).timestamp()
            else:
                target = float(raw)
        except Exception:
            return await message.channel.send(S.ui_err("bad timestamp"), delete_after=5)
        window = 60
        hits = [e for e in _history_log if abs(e.get("ts", 0) - target) <= window]
        if not hits:
            return await message.channel.send(S.ui_info(f"nothing within {window}s"), delete_after=6)
        lines = [f"  {S.WHITE}around {raw}{S.RESET}  {S.DIM}{len(hits)} hits ±{window}s{S.RESET}", ""]
        for i, e in enumerate(sorted(hits, key=lambda x: x["ts"]), 1):
            lines += _entry_block(e, idx=i)
            lines.append("")
        await message.channel.send(S._ansi_block(lines))

    # ── cache clearing ──
    async def _clear_user(self, message, args):
        if not args or not args[0].isdigit():
            return await message.channel.send(S.ui_err("usage: snipeclearuser <uid>"), delete_after=5)
        uid = int(args[0])
        n = 0
        for cid, lst in list(S._snipe_cache.items()):
            before = len(lst)
            S._snipe_cache[cid] = [e for e in lst if e.get("author_id") != uid]
            n += before - len(S._snipe_cache[cid])
            if not S._snipe_cache[cid]: S._snipe_cache.pop(cid, None)
        for cid, lst in list(S._editsnipe_cache.items()):
            before = len(lst)
            S._editsnipe_cache[cid] = [e for e in lst if e.get("author_id") != uid]
            n += before - len(S._editsnipe_cache[cid])
            if not S._editsnipe_cache[cid]: S._editsnipe_cache.pop(cid, None)
        await message.channel.send(S.ui_ok(f"removed {n} entries by {uid}"))

    async def _clear_channel(self, message, args):
        cid = int(args[0]) if args and args[0].isdigit() else message.channel.id
        n = len(S._snipe_cache.pop(cid, [])) + len(S._editsnipe_cache.pop(cid, []))
        await message.channel.send(S.ui_ok(f"cleared {n} entries from channel {cid}"))

    async def _clear_server(self, message):
        if not message.guild:
            return await message.channel.send(S.ui_err("server only"), delete_after=5)
        ch_ids = {ch.id for ch in message.guild.channels}
        n = 0
        for cid in list(S._snipe_cache.keys()):
            if cid in ch_ids:
                n += len(S._snipe_cache.pop(cid, []))
        for cid in list(S._editsnipe_cache.keys()):
            if cid in ch_ids:
                n += len(S._editsnipe_cache.pop(cid, []))
        await message.channel.send(S.ui_ok(f"cleared {n} entries in this server"))

    # ── ignore / filter ──
    async def _ignore(self, message, args):
        if len(args) < 2:
            return await message.channel.send(S.ui_info(
                "usage: snipeignore add/remove/list <user|channel|server> <id>"))
        sub = args[0].lower(); kind = args[1].lower()
        ref = args[2] if len(args) > 2 else ""
        target = None
        try: target = int(ref.strip("<#@!>"))
        except Exception: pass
        if sub == "add" and target:
            bucket = {"user": _snipe_ignore_users,
                      "channel": _snipe_ignore_channels,
                      "server": _snipe_ignore_servers}.get(kind)
            if bucket is None:
                return await message.channel.send(S.ui_err("kind: user/channel/server"), delete_after=5)
            bucket.add(target)
            return await message.channel.send(S.ui_ok(f"ignoring {kind} {target}"))
        if sub == "remove" and target:
            bucket = {"user": _snipe_ignore_users,
                      "channel": _snipe_ignore_channels,
                      "server": _snipe_ignore_servers}.get(kind)
            if bucket: bucket.discard(target)
            return await message.channel.send(S.ui_ok("removed"))
        if sub == "list":
            lines = [
                f"  {S.DIM}users:{S.RESET}    {sorted(_snipe_ignore_users)}",
                f"  {S.DIM}channels:{S.RESET} {sorted(_snipe_ignore_channels)}",
                f"  {S.DIM}servers:{S.RESET}  {sorted(_snipe_ignore_servers)}",
            ]
            return await message.channel.send(S._ansi_block(lines))
        await message.channel.send(S.ui_info("usage: snipeignore add/remove/list <kind> <id>"))

    async def _filter(self, message, args):
        if len(args) < 2:
            return await message.channel.send(S.ui_info(
                "usage: snipefilter add/remove/list <user|channel> <id>"))
        sub = args[0].lower(); kind = args[1].lower()
        ref = args[2] if len(args) > 2 else ""
        try: target = int(ref.strip("<#@!>"))
        except Exception: target = None
        if sub == "add" and target:
            bucket = {"user": _snipe_filter_users, "channel": _snipe_filter_channels}.get(kind)
            if bucket is None:
                return await message.channel.send(S.ui_err("kind: user/channel"), delete_after=5)
            bucket.add(target)
            return await message.channel.send(S.ui_ok(f"filter {kind} {target}"))
        if sub == "remove" and target:
            bucket = {"user": _snipe_filter_users, "channel": _snipe_filter_channels}.get(kind)
            if bucket: bucket.discard(target)
            return await message.channel.send(S.ui_ok("removed"))
        if sub == "list":
            return await message.channel.send(S._ansi_block([
                f"  {S.DIM}users:{S.RESET}    {sorted(_snipe_filter_users)}",
                f"  {S.DIM}channels:{S.RESET} {sorted(_snipe_filter_channels)}",
            ]))
        await message.channel.send(S.ui_info("usage: snipefilter add/remove/list <kind> <id>"))

    async def _stats(self, message):
        del_count = sum(len(v) for v in S._snipe_cache.values())
        edit_count = sum(len(v) for v in S._editsnipe_cache.values())
        lines = [
            f"  {S.DIM}channels cached (del):{S.RESET}  {len(S._snipe_cache)}",
            f"  {S.DIM}entries (del):{S.RESET}           {del_count}",
            f"  {S.DIM}channels cached (edit):{S.RESET} {len(S._editsnipe_cache)}",
            f"  {S.DIM}entries (edit):{S.RESET}          {edit_count}",
            f"  {S.DIM}global history:{S.RESET}          {len(_history_log)}",
            f"  {S.DIM}ignore users/ch/servers:{S.RESET} "
            f"{len(_snipe_ignore_users)}/{len(_snipe_ignore_channels)}/{len(_snipe_ignore_servers)}",
        ]
        await message.channel.send(S._ansi_block(lines))

    # ── edit snipe ──
    async def _editsnipe(self, message, args):
        cid = message.channel.id
        entries = S._editsnipe_cache.get(cid, [])
        if not entries:
            return await message.channel.send(S.ui_info("no edits"), delete_after=6)
        sub = args[0].lower() if args else ""
        if sub == "clear":
            S._editsnipe_cache.pop(cid, None)
            return await message.channel.send(S.ui_ok("edit cache cleared"), delete_after=5)
        try: n = int(sub) if sub else 1
        except ValueError: n = 1
        if n < 1 or n > len(entries):
            return await message.channel.send(S.ui_err(f"range 1–{len(entries)}"), delete_after=5)
        e = entries[-n]
        lines = [
            f"  {S.WHITE}edit sniped{S.RESET}  {S.DIM}{n}/{len(entries)}{S.RESET}",
            f"  {S.DIM}author{S.RESET}  {S.WHITE}{e['author']}{S.RESET}",
            f"  {S.DIM}time{S.RESET}    {e['time']}", "",
            f"  {S.DIM}before:{S.RESET}",
            f"  {S.WHITE}{e['before'] or '(empty)'}{S.RESET}", "",
            f"  {S.DIM}after:{S.RESET}",
            f"  {S.WHITE}{e['after'] or '(empty)'}{S.RESET}",
        ]
        await message.channel.send(S._ansi_block(lines))

    async def _editsnipe_history(self, message, args):
        cid = message.channel.id
        entries = S._editsnipe_cache.get(cid, [])
        if not entries:
            return await message.channel.send(S.ui_info("no edit history"), delete_after=6)
        per = 5
        page = int(args[0]) if args and args[0].isdigit() else 1
        total = max(1, (len(entries) + per - 1) // per)
        page = max(1, min(page, total))
        chunk = entries[::-1][(page-1)*per:page*per]
        lines = [f"  {S.WHITE}edit history{S.RESET}  {S.DIM}page {page}/{total}{S.RESET}", ""]
        for i, e in enumerate(chunk, 1):
            lines += [
                f"  {S.DIM}#{i}{S.RESET}  {S.WHITE}{e['author']}{S.RESET}  {S.DIM}{e['time']}{S.RESET}",
                f"  {S.DIM}before:{S.RESET} {e['before'][:120]}",
                f"  {S.DIM}after:{S.RESET}  {e['after'][:120]}",
                "",
            ]
        await message.channel.send(S._ansi_block(lines))


# ── exports for selfbot's on_message_delete / on_message_edit ──
def record_delete(message):
    if _ignored(message): return
    cid = message.channel_id
    entry = {
        "author": str(message.author), "author_id": message.author.id,
        "content": message.content or "",
        "attachments": [a.get("url") for a in (message.attachments or [])],
        "embeds": [dict(e.to_dict()) if hasattr(e, "to_dict") else {} for e in (message.embeds or [])],
        "reactions": [str(r.emoji) for r in (getattr(message, "reactions", []) or [])],
        "time": datetime.now().strftime("%H:%M:%S"),
        "ts": time.time(),
        "channel_id": cid,
        "guild_id": getattr(getattr(message, "guild", None), "id", None),
    }
    S._snipe_cache.setdefault(cid, [])
    S._snipe_cache[cid].append(entry)
    if len(S._snipe_cache[cid]) > 20:
        S._snipe_cache[cid] = S._snipe_cache[cid][-20:]
    _record(entry, "delete")


def record_edit(before, after):
    if _ignored(before): return
    cid = before.channel_id
    entry = {
        "author": str(before.author), "author_id": before.author.id,
        "before": before.content or "", "after": after.content or "",
        "time": datetime.now().strftime("%H:%M:%S"),
        "ts": time.time(),
        "channel_id": cid,
        "guild_id": getattr(getattr(before, "guild", None), "id", None),
    }
    S._editsnipe_cache.setdefault(cid, [])
    S._editsnipe_cache[cid].append(entry)
    if len(S._editsnipe_cache[cid]) > 20:
        S._editsnipe_cache[cid] = S._editsnipe_cache[cid][-20:]
    _record(entry, "edit")