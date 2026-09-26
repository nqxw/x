# cogs/afk.py | full AFK system — whitelist, blacklist, cooldown, dm-only, per-server, expiry, emergency
import asyncio
import time
import modifyself_shim as discord
from . import state as S


async def _afk_pre_hook(client, message):
    """Called on every message before the command gate."""
    a = S.afk
    if not a["enabled"]:
        return
    # expiry check
    if a["expires_at"] and time.time() >= a["expires_at"]:
        a["enabled"] = False
        a["expires_at"] = 0.0
        return
    # emergency mode: exit on any message from user
    if a["emergency"] and message.author.id == client.user.id:
        return
    # only react to messages that mention us
    try:
        me = client.user
        mentioned = (me in (message.mentions or [])) if me else False
    except Exception:
        mentioned = False
    if not mentioned:
        return
    if message.author.id == client.user.id:
        return
    # ignore list
    if message.author.id in a["blacklist"]:
        return
    if a["whitelist"] and message.author.id not in a["whitelist"]:
        return
    # dm-only
    if a["dm_only"] and getattr(message, "guild", None) is not None:
        return
    # per-server override
    gid = str(getattr(getattr(message, "guild", None), "id", "")) or None
    if gid and gid in a["per_server"]:
        msg = a["per_server"][gid].get("message") or a["message"]
    else:
        msg = a["message"]
    # custom reply per user
    if message.author.id in a["custom_replies"]:
        msg = a["custom_replies"][message.author.id]
    # cooldown per user
    key = (message.author.id, getattr(message, "channel_id", 0))
    last = a["last_reply"].get(key, 0)
    if time.time() - last < a["cooldown"]:
        return
    a["last_reply"][key] = time.time()
    # ping counter
    a["ping_counter"][message.author.id] = a["ping_counter"].get(message.author.id, 0) + 1
    try:
        await message.reply(msg, mention_author=False)
    except Exception:
        pass


class AfkCog:
    COMMANDS = {"afk", "afkstop", "afkstatus", "afkignore", "afkignorelist",
                "afkwhitelist", "afkblacklist", "afkcooldown", "afkdmonly",
                "afkexpire", "afkemergency", "afkreturn", "afkcustom",
                "afkserver", "afkpingcount"}

    def register(self, client):
        S.register_pre_hook(_afk_pre_hook)

    async def handle(self, message, cmd, args):
        a = S.afk

        if cmd == "afk":
            msg = " ".join(args[1:]) if len(args) > 1 else None
            a["enabled"] = True
            if msg:
                a["message"] = msg
            tail = ""
            if a["expires_at"]:
                tail = f" (expires in {int(a['expires_at'] - time.time())}s)"
            await message.edit(content=S.ui_ok(
                f"AFK on — {a['message'][:60]}{tail}"))

        elif cmd == "afkstop":
            a["enabled"] = False
            a["expires_at"] = 0.0
            a["emergency"] = False
            await message.edit(content=S.ui_ok("AFK off"))

        elif cmd == "afkstatus":
            rows = [
                f"  {S.DIM}enabled{S.RESET}      {a['enabled']}",
                f"  {S.DIM}message{S.RESET}      {a['message'][:60]}",
                f"  {S.DIM}cooldown{S.RESET}     {a['cooldown']}s",
                f"  {S.DIM}dm only{S.RESET}      {a['dm_only']}",
                f"  {S.DIM}emergency{S.RESET}    {a['emergency']}",
                f"  {S.DIM}whitelist{S.RESET}    {len(a['whitelist'])}",
                f"  {S.DIM}blacklist{S.RESET}    {len(a['blacklist'])}",
                f"  {S.DIM}custom replies{S.RESET} {len(a['custom_replies'])}",
                f"  {S.DIM}per-server{S.RESET}   {len(a['per_server'])}",
                f"  {S.DIM}expires{S.RESET}      "
                f"{int(a['expires_at'] - time.time()) if a['expires_at'] else '—'}",
            ]
            await message.edit(content=S.ui_box("AFK status", rows))

        elif cmd == "afkignore":
            if len(args) < 3:
                return await message.edit(content=S.ui_err("usage: afkignore add/remove/list <uid>"))
            sub = args[1].lower()
            if sub == "add" and args[2].isdigit():
                a["blacklist"].add(int(args[2]))
                await message.edit(content=S.ui_ok(f"ignoring {args[2]}"))
            elif sub == "remove" and args[2].isdigit():
                a["blacklist"].discard(int(args[2]))
                await message.edit(content=S.ui_ok("removed"))
            elif sub == "list":
                rows = [f"  {S.GREY}•{S.RESET} <@{u}>" for u in sorted(a["blacklist"])]
                await message.edit(content=S._paginate("afk ignore", "", rows)
                                            if rows else S.ui_info("empty"))
            elif sub == "clear":
                a["blacklist"].clear()
                await message.edit(content=S.ui_ok("cleared"))
            else:
                await message.edit(content=S.ui_info("usage: afkignore add/remove/list/clear"))

        elif cmd == "afkwhitelist":
            if len(args) < 2:
                return await message.edit(content=S.ui_info(
                    "usage: afkwhitelist add/remove/list/clear <uid>"))
            sub = args[1].lower()
            if sub == "add" and len(args) >= 3 and args[2].isdigit():
                a["whitelist"].add(int(args[2]))
                await message.edit(content=S.ui_ok("added"))
            elif sub == "remove" and len(args) >= 3 and args[2].isdigit():
                a["whitelist"].discard(int(args[2]))
                await message.edit(content=S.ui_ok("removed"))
            elif sub == "list":
                rows = [f"  {S.GREY}•{S.RESET} <@{u}>" for u in sorted(a["whitelist"])]
                await message.edit(content=S._paginate("afk whitelist", "", rows)
                                            if rows else S.ui_info("empty"))
            elif sub == "clear":
                a["whitelist"].clear()
                await message.edit(content=S.ui_ok("cleared"))
            else:
                await message.edit(content=S.ui_info("usage: afkwhitelist add/remove/list/clear"))

        elif cmd == "afkblacklist":
            await self._mirror(message, args, "blacklist")

        elif cmd == "afkcooldown":
            if len(args) < 2 or not args[1].isdigit():
                return await message.edit(content=S.ui_err("usage: afkcooldown <seconds>"))
            a["cooldown"] = int(args[1])
            await message.edit(content=S.ui_ok(f"cooldown → {a['cooldown']}s"))

        elif cmd == "afkdmonly":
            val = (args[1].lower() in ("on", "enable")) if len(args) > 1 else not a["dm_only"]
            a["dm_only"] = val
            await message.edit(content=S.ui_ok(f"dm-only → {val}"))

        elif cmd == "afkexpire":
            if len(args) < 2:
                a["expires_at"] = 0.0
                return await message.edit(content=S.ui_ok("expiry cleared"))
            try: secs = int(args[1])
            except ValueError:
                return await message.edit(content=S.ui_err("seconds required"))
            a["expires_at"] = time.time() + secs
            await message.edit(content=S.ui_ok(f"expires in {secs}s"))

        elif cmd == "afkemergency":
            a["emergency"] = True
            a["enabled"] = True
            await message.edit(content=S.ui_ok("emergency AFK on"))

        elif cmd == "afkreturn":
            if a["enabled"]:
                a["enabled"] = False
                counts = a["ping_counter"]
                if counts:
                    lines = [f"  {S.DIM}you were pinged by {len(counts)} user(s){S.RESET}"]
                    for uid, n in sorted(counts.items(), key=lambda x: -x[1])[:10]:
                        lines.append(f"  {S.GREY}•{S.RESET} <@{uid}>  {n}x")
                    counts.clear()
                    return await message.edit(content=S.ui_box("welcome back", lines))
            await message.edit(content=S.ui_ok("AFK off"))

        elif cmd == "afkcustom":
            if len(args) < 4 or not args[2].isdigit():
                return await message.edit(content=S.ui_err(
                    "usage: afkcustom set <uid> <text> | afkcustom remove <uid>"))
            sub = args[1].lower()
            uid = int(args[2])
            if sub == "set":
                a["custom_replies"][uid] = " ".join(args[3:])
                await message.edit(content=S.ui_ok(f"reply for {uid} set"))
            elif sub == "remove":
                a["custom_replies"].pop(uid, None)
                await message.edit(content=S.ui_ok("removed"))
            else:
                await message.edit(content=S.ui_info("usage: afkcustom set/remove"))

        elif cmd == "afkserver":
            if not message.guild:
                return await message.edit(content=S.ui_err("server only"))
            gid = str(message.guild.id)
            if len(args) < 2:
                return await message.edit(content=S.ui_err(
                    f"usage: afkserver set <text> | afkserver clear"))
            sub = args[1].lower()
            if sub == "set":
                a["per_server"][gid] = {"message": " ".join(args[2:])}
                await message.edit(content=S.ui_ok("set"))
            elif sub == "clear":
                a["per_server"].pop(gid, None)
                await message.edit(content=S.ui_ok("cleared"))
            else:
                await message.edit(content=S.ui_info("usage: afkserver set/clear"))

        elif cmd == "afkpingcount":
            rows = [f"  {S.GREY}•{S.RESET} <@{u}>  {n}x"
                    for u, n in sorted(a["ping_counter"].items(), key=lambda x: -x[1])[:20]]
            await message.edit(content=S._paginate("ping counters", "", rows)
                                        if rows else S.ui_info("no pings yet"))

    async def _mirror(self, message, args, key):
        a = S.afk
        if len(args) < 2:
            return await message.edit(content=S.ui_info(f"usage: afk{key} add/remove/list/clear"))
        sub = args[1].lower()
        if sub == "add" and len(args) >= 3 and args[2].isdigit():
            a[key].add(int(args[2])); return await message.edit(content=S.ui_ok("added"))
        if sub == "remove" and len(args) >= 3 and args[2].isdigit():
            a[key].discard(int(args[2])); return await message.edit(content=S.ui_ok("removed"))
        if sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} <@{u}>" for u in sorted(a[key])]
            return await message.edit(content=S._paginate(f"afk {key}", "", rows)
                                                if rows else S.ui_info("empty"))
        if sub == "clear":
            a[key].clear(); return await message.edit(content=S.ui_ok("cleared"))
        await message.edit(content=S.ui_info(f"usage: afk{key} add/remove/list/clear"))