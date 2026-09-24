# cogs/status.py | setstatus, clearstatus, stealstatus, statushistory, speaklanguage
import sys
import re
import aiohttp
from datetime import datetime
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


def _settings_headers():
    return {"Authorization": S.TOKEN, "Content-Type": "application/json",
            "User-Agent": S.USER_AGENT}


class StatusCog:
    COMMANDS = {"setstatus", "customstatus", "clearstatus", "stealstatus", "copystatus",
                "statushistory", "speaklanguage", "speaklanguagestop"}

    async def handle(self, message, cmd, args):
        if cmd in ("setstatus", "customstatus"): await self._setstatus(message, args)
        elif cmd == "clearstatus": await self._clearstatus(message)
        elif cmd in ("stealstatus", "copystatus"): await self._stealstatus(message, args)
        elif cmd == "statushistory": await self._history(message)
        elif cmd == "speaklanguage":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: speaklanguage <lang>"))
            S._speak_lang = args[1]
            _sync(_speak_lang=args[1])
            await message.edit(content=S.ui_ok(f"auto → {S._speak_lang}"))
        elif cmd == "speaklanguagestop":
            S._speak_lang = None
            _sync(_speak_lang=None)
            await message.edit(content=S.ui_ok("stopped"))

    async def _setstatus(self, message, args):
        if len(args) < 2:
            return await message.edit(content=S.ui_box("setstatus", [
                f"  {S.DIM}usage:{S.RESET}",
                f"  {S.PREFIX}setstatus <text>",
                f"  {S.PREFIX}setstatus <emoji>, <text>",
                f"  {S.PREFIX}setstatus <:name:id>, <text>",
            ]))
        full_text = " ".join(args[1:])
        emoji_name = None; emoji_id = None
        text = full_text.strip()
        if "," in text:
            parts = text.split(",", 1)
            emoji_part = parts[0].strip()
            text_part = parts[1].strip() if len(parts) > 1 else ""
            if not text_part:
                return await message.edit(content=S.ui_err("provide status text after the comma"))
            m = re.match(r"<:([a-zA-Z0-9_]+):([0-9]+)>", emoji_part)
            if m:
                emoji_name = m.group(1); emoji_id = m.group(2)
            elif len(emoji_part) >= 1 and (len(emoji_part) == 1 or any(ord(c) > 127 for c in emoji_part)):
                emoji_name = emoji_part
            else:
                return await message.edit(content=S.ui_err("invalid emoji"))
            text = text_part
        if not text:
            return await message.edit(content=S.ui_err("provide status text"))
        payload = {"custom_status": {"text": text, "emoji_name": emoji_name, "emoji_id": emoji_id}}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me/settings",
                                   headers=_settings_headers(), json=payload) as r:
                    if r.status == 200:
                        cfg = S.load_config() or {}
                        hist = cfg.get("status_history", [])
                        hist.insert(0, {"text": text, "emoji": emoji_name,
                                        "time": datetime.now().strftime("%H:%M %d/%m")})
                        cfg["status_history"] = hist[:20]
                        S.save_config(cfg)
                        await message.edit(content=S.ui_ok(f"status set: {emoji_name or ''} {text}".strip()))
                    elif r.status == 429:
                        retry = (await r.json()).get("retry_after", 1)
                        await message.edit(content=S.ui_warn(f"rate limited — retry in {retry}s"))
                    else:
                        await message.edit(content=S.ui_err(f"failed {r.status}"))
        except Exception as e:
            await message.edit(content=S.ui_err(str(e)))

    async def _clearstatus(self, message):
        payload = {"custom_status": {"text": "", "emoji_name": None, "emoji_id": None}}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.patch("https://discord.com/api/v9/users/@me/settings",
                                   headers=_settings_headers(), json=payload) as r:
                    await message.edit(content=S.ui_ok("cleared") if r.status == 200
                                              else S.ui_err(f"failed {r.status}"))
        except Exception as e:
            await message.edit(content=S.ui_err(str(e)))

    async def _stealstatus(self, message, args):
        if len(args) < 2:
            return await message.edit(content=S.ui_err("usage: stealstatus <user_id>"))
        uid = args[1].strip("<@!>")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://discord.com/api/v9/users/{uid}/profile",
                                 headers={"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}) as r:
                    if r.status != 200:
                        return await message.edit(content=S.ui_err("cannot fetch profile"))
                    profile = await r.json()
                username = profile.get("user", {}).get("username", "?")
                custom_text = profile.get("user_profile", {}).get("bio", "") or ""
                if not custom_text:
                    return await message.edit(content=S.ui_err(f"{username} has no visible status"))
                payload = {"custom_status": {"text": custom_text,
                                              "emoji_name": None, "emoji_id": None}}
                async with s.patch("https://discord.com/api/v9/users/@me/settings",
                                   headers=_settings_headers(), json=payload) as r2:
                    if r2.status == 200:
                        cfg = S.load_config() or {}
                        hist = cfg.get("status_history", [])
                        hist.insert(0, {"text": custom_text, "emoji": None,
                                        "time": datetime.now().strftime("%H:%M %d/%m"),
                                        "stolen_from": username})
                        cfg["status_history"] = hist[:20]
                        S.save_config(cfg)
                        await message.edit(content=S.ui_ok(f"stole from {username}"))
                    else:
                        await message.edit(content=S.ui_err(f"failed {r2.status}"))
        except Exception as e:
            await message.edit(content=S.ui_err(str(e)))

    async def _history(self, message):
        cfg = S.load_config() or {}
        hist = cfg.get("status_history", [])
        if not hist:
            return await message.edit(content=S.ui_info("no status history yet"))
        rows = []
        for i, entry in enumerate(hist[:20], 1):
            emoji = f"{entry['emoji']} " if entry.get("emoji") else ""
            stolen = f"  {S.DIM}(from {entry['stolen_from']}){S.RESET}" if entry.get("stolen_from") else ""
            rows.append(f"  {S.GREY}{i:2}.{S.RESET} "
                        f"{S.WHITE}{emoji}{entry['text']}{S.RESET}  "
                        f"{S.DIM}{entry['time']}{S.RESET}{stolen}")
        await message.edit(content=S._paginate("status history", "recent", rows))