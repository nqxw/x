# cogs/filters.py | anti-spam, anti-dupe, anti-link/invite/attachment/nsfw, ping protection, scam filter
import re
import time
import modifyself_shim as discord
from . import state as S


URL_RE    = re.compile(r"https?://\S+")
INVITE_RE = re.compile(r"(discord\.gg|discord\.com/invite|discordapp\.com/invite)/\S+")
SCAM_RE   = re.compile(r"(free\s*nitro|steamcommunity\.(com|ru)|gift\s*claim|free\s*gift)", re.I)


async def _filters_pre_hook(client, message):
    f = S.filters
    if not message.guild or message.author.id == client.user.id:
        return
    content = message.content or ""

    # anti-bot
    if f["bot"]["enabled"] and getattr(message.author, "bot", False):
        return await _act(message, "bot message")

    # webhook
    if f["webhook"]["enabled"]:
        try:
            if getattr(message, "webhook_id", None):
                return await _act(message, "webhook message")
        except Exception:
            pass

    # spam (per-user rate)
    if f["spam"]["enabled"]:
        uid = message.author.id
        now = time.time()
        hist = f["spam"]["history"].setdefault(uid, [])
        hist[:] = [t for t in hist if now - t < f["spam"]["window"]]
        hist.append(now)
        if len(hist) > f["spam"]["threshold"]:
            return await _act(message, f"spam ({len(hist)} msgs/{f['spam']['window']}s)")

    # duplicate
    if f["duplicate"]["enabled"]:
        uid = message.author.id
        cid = getattr(message.channel, "id", 0)
        key = (uid, cid, content.strip().lower())
        now = time.time()
        last = f["duplicate"]["history"].get(key, 0)
        if content.strip() and now - last < f["duplicate"]["window"]:
            return await _act(message, "duplicate message")
        f["duplicate"]["history"][key] = now

    # link / invite
    if f["invite"]["enabled"] and INVITE_RE.search(content):
        return await _act(message, "invite link")
    if f["link"]["enabled"] and URL_RE.search(content):
        urls = URL_RE.findall(content)
        wl = f["link"]["whitelist"]
        if not all(any(w in u for w in wl) for u in urls) or not wl:
            if wl and all(any(w in u for w in wl) for u in urls):
                pass
            else:
                return await _act(message, "link")

    # attachment
    if f["attachment"]["enabled"] and (message.attachments or []):
        return await _act(message, "attachment")

    # nsfw
    if f["nsfw"]["enabled"]:
        low = content.lower()
        for kw in f["nsfw"]["keywords"]:
            if kw in low:
                return await _act(message, f"nsfw keyword `{kw}`")

    # mention spam
    if f["mention_spam"]["enabled"]:
        n = len(message.mentions or [])
        if n >= f["mention_spam"]["threshold"]:
            return await _act(message, f"mention spam ({n})")

    # mass ping (@everyone/@here)
    if f["mass_ping"]["enabled"]:
        if "@everyone" in content or "@here" in content:
            return await _act(message, "mass ping")

    # scam
    if f["scam"]["enabled"] and SCAM_RE.search(content):
        return await _act(message, "scam pattern")

    # auto purge
    if f["auto_purge"]["enabled"]:
        await _auto_purge(client, message)


async def _act(message, reason):
    f = S.filters
    try: await message.delete()
    except Exception: pass
    f["actions_log"].append({"ts": time.time(), "author": str(message.author),
                             "reason": reason, "content": (message.content or "")[:100]})
    if len(f["actions_log"]) > 200:
        del f["actions_log"][:-200]
    if f["log_channel"] and S.CLIENT:
        try:
            ch = S.CLIENT.get_channel(int(f["log_channel"]))
            if ch:
                await ch.send(S.ui_box("filter hit", [
                    f"  {S.DIM}author{S.RESET}  {message.author}",
                    f"  {S.DIM}reason{S.RESET}  {reason}",
                    f"  {S.DIM}content{S.RESET} {(message.content or '')[:200]}",
                ]))
        except Exception:
            pass


async def _auto_purge(client, message):
    keep = S.filters["auto_purge"].get("keep", 50)
    try:
        own = []
        async for m in message.channel.history(limit=keep * 3):
            if m.author.id == client.user.id:
                own.append(m)
                if len(own) >= keep + 10:
                    break
        for m in own[keep:]:
            try: await m.delete()
            except Exception: pass
    except Exception:
        pass


class FiltersCog:
    COMMANDS = {"filter", "filters", "filterlog", "filterclear",
                "antispam", "antidupe", "antilink", "antiinvite",
                "antiattachment", "antinsfw", "antimention", "antimassping",
                "antiscam", "antibot", "antiwebhook", "autopurge"}

    def register(self, client):
        S.register_pre_hook(_filters_pre_hook)

    async def handle(self, message, cmd, args):
        f = S.filters
        if cmd == "filter":
            sub = args[1].lower() if len(args) > 1 else ""
            kind = args[2].lower() if len(args) > 2 else ""
            if sub in f and isinstance(f[sub], dict) and "enabled" in f[sub]:
                on = (kind in ("on", "enable")) if kind else not f[sub]["enabled"]
                f[sub]["enabled"] = on
                return await message.edit(content=S.ui_ok(f"{sub} → {on}"))
            if sub == "log" and len(args) >= 3:
                f["log_channel"] = args[2]
                return await message.edit(content=S.ui_ok("filter log channel set"))
            if sub == "status":
                rows = []
                for k, v in f.items():
                    if isinstance(v, dict) and "enabled" in v:
                        rows.append(f"  {S.DIM}{k:<14}{S.RESET} {'ON ' if v['enabled'] else 'off'}")
                return await message.edit(content=S.ui_box("filters", rows))
            return await message.edit(content=S.ui_info(
                "usage: filter <kind> on/off | status | log <ch_id>"))

        if cmd == "filters":
            rows = []
            for k, v in f.items():
                if isinstance(v, dict) and "enabled" in v:
                    rows.append(f"  {S.DIM}{k:<14}{S.RESET} {'ON ' if v['enabled'] else 'off'}")
            await message.edit(content=S.ui_box("filters", rows))

        elif cmd == "filterlog":
            entries = f["actions_log"][-30:]
            rows = [f"  {S.GREY}•{S.RESET} {e['author'][:24]:<24}  {S.DIM}{e['reason']}{S.RESET}"
                    for e in entries]
            await message.edit(content=S._paginate("filter log", f"{len(f['actions_log'])} total", rows)
                                        if rows else S.ui_info("no hits"))

        elif cmd == "filterclear":
            f["actions_log"].clear()
            await message.edit(content=S.ui_ok("filter log cleared"))

        elif cmd == "antispam":
            if len(args) >= 2 and args[1].isdigit():
                f["spam"]["threshold"] = int(args[1])
                return await message.edit(content=S.ui_ok(f"threshold → {args[1]}"))
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["spam"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antispam → {on}"))

        elif cmd == "antidupe":
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["duplicate"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antidupe → {on}"))

        elif cmd == "antilink":
            if len(args) >= 3 and args[1].lower() == "add":
                f["link"]["whitelist"].add(args[2])
                return await message.edit(content=S.ui_ok(f"whitelisted {args[2]}"))
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["link"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antilink → {on}"))

        elif cmd == "antiinvite":
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["invite"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antiinvite → {on}"))

        elif cmd == "antiattachment":
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["attachment"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antiattachment → {on}"))

        elif cmd == "antinsfw":
            if len(args) >= 3 and args[1].lower() == "add":
                f["nsfw"]["keywords"].add(args[2].lower())
                return await message.edit(content=S.ui_ok("keyword added"))
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["nsfw"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antinsfw → {on}"))

        elif cmd == "antimention":
            if len(args) >= 2 and args[1].isdigit():
                f["mention_spam"]["threshold"] = int(args[1])
                return await message.edit(content=S.ui_ok(f"threshold → {args[1]}"))
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["mention_spam"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antimention → {on}"))

        elif cmd == "antimassping":
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["mass_ping"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antimassping → {on}"))

        elif cmd == "antiscam":
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["scam"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antiscam → {on}"))

        elif cmd == "antibot":
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["bot"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antibot → {on}"))

        elif cmd == "antiwebhook":
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["webhook"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"antiwebhook → {on}"))

        elif cmd == "autopurge":
            if len(args) >= 2 and args[1].isdigit():
                f["auto_purge"]["keep"] = int(args[1])
                f["auto_purge"]["enabled"] = True
                return await message.edit(content=S.ui_ok(f"keep last {args[1]}"))
            on = len(args) < 2 or args[1].lower() in ("on", "enable")
            f["auto_purge"]["enabled"] = on
            await message.edit(content=S.ui_ok(f"autopurge → {on}"))