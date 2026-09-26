# cogs/profile_ext.py | avatar/banner download, profile history, user notes, personal block/whitelist/ignore
import os
import json
import time
import aiohttp
from datetime import datetime
import modifyself_shim as discord
from . import state as S


def _av_url(uid, u):
    if not u.get("avatar"): return None
    ext = "gif" if str(u["avatar"]).startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{uid}/{u['avatar']}.{ext}?size=1024"


def _bn_url(uid, u):
    if not u.get("banner"): return None
    ext = "gif" if str(u["banner"]).startswith("a_") else "png"
    return f"https://cdn.discordapp.com/banners/{uid}/{u['banner']}.{ext}?size=1024"


async def _fetch_user(uid):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"https://discord.com/api/v9/users/{uid}",
                         headers={"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}) as r:
            if r.status != 200:
                return None
            return await r.json()


class ProfileExtCog:
    COMMANDS = {"avatardl", "bannerdl", "profilehistory", "usernote",
                "personalblock", "personalunblock", "personalignore",
                "personalunignore", "personalblocklist", "personallist"}

    async def handle(self, message, cmd, args):
        if cmd == "avatardl":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            u = await _fetch_user(uid)
            if not u: return await message.edit(content=S.ui_err("not found"))
            url = _av_url(uid, u)
            if not url: return await message.edit(content=S.ui_info("no avatar"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(url) as r:
                        data = await r.read()
                os.makedirs("exports/avatars", exist_ok=True)
                path = f"exports/avatars/{uid}.png"
                with open(path, "wb") as f: f.write(data)
                await message.edit(content=S.ui_ok(f"saved {path}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "bannerdl":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            u = await _fetch_user(uid)
            if not u: return await message.edit(content=S.ui_err("not found"))
            url = _bn_url(uid, u)
            if not url: return await message.edit(content=S.ui_info("no banner"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(url) as r:
                        data = await r.read()
                os.makedirs("exports/banners", exist_ok=True)
                path = f"exports/banners/{uid}.png"
                with open(path, "wb") as f: f.write(data)
                await message.edit(content=S.ui_ok(f"saved {path}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "profilehistory":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            hist = S.profile_history.get(uid, [])
            if not hist:
                # snapshot now
                u = await _fetch_user(uid)
                if u:
                    S.profile_history.setdefault(uid, []).append({
                        "ts": time.time(),
                        "username": u.get("username"),
                        "global_name": u.get("global_name"),
                        "avatar": u.get("avatar"),
                        "banner": u.get("banner"),
                    })
                hist = S.profile_history.get(uid, [])
            if not hist:
                return await message.edit(content=S.ui_info("no history"))
            rows = []
            for e in hist[-30:]:
                rows.append(f"  {S.GREY}•{S.RESET} {e['username']}  "
                            f"{S.DIM}av:{'y' if e.get('avatar') else 'n'} "
                            f"bn:{'y' if e.get('banner') else 'n'} "
                            f"{datetime.fromtimestamp(e['ts']).strftime('%m-%d %H:%M')}{S.RESET}")
            await message.edit(content=S._paginate("profile history", f"user {uid}", rows))

        elif cmd == "usernote":
            if len(args) < 3:
                return await message.edit(content=S.ui_err(
                    "usage: usernote add <uid> <text> | list <uid> | clear <uid>"))
            sub = args[1].lower()
            if sub == "add" and len(args) >= 4:
                S.db_note_add(args[2], " ".join(args[3:]))
                return await message.edit(content=S.ui_ok("saved"))
            if sub == "list" and len(args) >= 3:
                rows = [f"  {S.GREY}•{S.RESET} "
                        f"{datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')}  {n}"
                        for n, ts in S.db_note_list(args[2])]
                return await message.edit(content=S._paginate("notes", args[2], rows)
                                                    if rows else S.ui_info("none"))
            if sub == "clear" and len(args) >= 3:
                S.db_note_clear(args[2])
                return await message.edit(content=S.ui_ok("cleared"))
            await message.edit(content=S.ui_info("usage: usernote add/list/clear"))

        elif cmd in ("personalblock", "personalignore"):
            if len(args) < 2 or not args[1].isdigit():
                return await message.edit(content=S.ui_err(f"usage: {cmd} <uid>"))
            uid = int(args[1])
            if cmd == "personalblock":
                S.personal_blocklist.add(uid)
            else:
                S.personal_ignore.add(uid)
            await message.edit(content=S.ui_ok(f"{cmd} {uid}"))

        elif cmd in ("personalunblock", "personalunignore"):
            if len(args) < 2 or not args[1].isdigit():
                return await message.edit(content=S.ui_err(f"usage: {cmd} <uid>"))
            uid = int(args[1])
            if cmd == "personalunblock":
                S.personal_blocklist.discard(uid)
            else:
                S.personal_ignore.discard(uid)
            await message.edit(content=S.ui_ok("removed"))

        elif cmd == "personalblocklist":
            rows = [f"  {S.GREY}•{S.RESET} <@{u}>" for u in sorted(S.personal_blocklist)]
            await message.edit(content=S._paginate("personal blocklist", "", rows)
                                        if rows else S.ui_info("empty"))

        elif cmd == "personallist":
            rows = [
                f"  {S.DIM}block{S.RESET}  {len(S.personal_blocklist)}",
                f"  {S.DIM}ignore{S.RESET} {len(S.personal_ignore)}",
                "",
                *[f"  {S.GREY}•{S.RESET} <@{u}>  {S.DIM}block{S.RESET}" for u in sorted(S.personal_blocklist)],
                *[f"  {S.GREY}•{S.RESET} <@{u}>  {S.DIM}ignore{S.RESET}" for u in sorted(S.personal_ignore)],
            ]
            await message.edit(content=S._ansi_block(rows))