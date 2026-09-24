# cogs/webhooks.py | create/delete/list/spam/rename/clear, emoji list + steal
import asyncio
import re
import aiohttp
import modifyself_shim as discord
from . import state as S


def _h():
    return {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}


def _hj():
    return {"Authorization": S.TOKEN, "Content-Type": "application/json",
            "User-Agent": S.USER_AGENT}


class WebhooksCog:
    COMMANDS = {"webhook"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""

        if sub == "create":
            name = args[2] if len(args) > 2 else "lunar"
            try:
                wh = await message.channel.create_webhook(name=name)
                token = getattr(wh, "token", None) or getattr(wh, "_token", None)
                wid = getattr(wh, "id", None)
                await message.channel.send(
                    S.ui_ok(f"webhook id {wid}" + (f" / token {token}" if token else "")))
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif sub == "delete" and len(args) >= 3:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.delete(
                        f"https://discord.com/api/v9/webhooks/{args[2]}",
                        headers=_h()) as r:
                        if r.status in (200, 204):
                            await message.channel.send(S.ui_ok("deleted"))
                        else:
                            await message.channel.send(
                                S.ui_err(f"failed {r.status}"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif sub == "list":
            try:
                whs = await message.channel.webhooks()
                rows = [f"  {S.GREY}•{S.RESET} "
                        f"{getattr(w, 'name', '?')}  "
                        f"{S.DIM}({getattr(w, 'id', '?')}){S.RESET}"
                        for w in whs]
                await message.channel.send(
                    S._paginate("webhooks", "", rows) if rows else S.ui_info("none"))
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif sub == "spam" and len(args) >= 5:
            url = args[2]
            n = int(args[3]) if args[3].isdigit() else 10
            txt = " ".join(args[4:])
            done = 0
            async with aiohttp.ClientSession() as s:
                for _ in range(n):
                    try:
                        async with s.post(url, json={"content": txt}) as r:
                            if r.status in (200, 204):
                                done += 1
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
            await message.channel.send(S.ui_ok(f"spammed {done}/{n}"))

        elif sub == "rename" and len(args) >= 4:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.patch(
                        f"https://discord.com/api/v9/webhooks/{args[2]}",
                        headers=_hj(),
                        json={"name": " ".join(args[3:])}) as r:
                        if r.status == 200:
                            await message.channel.send(S.ui_ok("renamed"))
                        else:
                            await message.channel.send(
                                S.ui_err(f"failed {r.status}"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif sub == "emoji":
            if not message.guild:
                return await message.channel.send(
                    S.ui_err("server only"), delete_after=5)
            rows = [f"  {S.GREY}•{S.RESET} {e}  "
                    f"{S.DIM}{e.name} ({e.id}){S.RESET}"
                    for e in list(message.guild.emojis)[:80]]
            await message.channel.send(
                S._paginate("emojis", message.guild.name, rows)
                if rows else S.ui_info("none"))

        elif sub == "steal" and len(args) >= 3:
            e = args[2]
            m = re.match(r"<a?:([a-zA-Z0-9_]+):(\d+)>", e)
            if not m:
                return await message.channel.send(
                    S.ui_err("provide a custom emoji"), delete_after=5)
            name, eid = m.group(1), m.group(2)
            url = (f"https://cdn.discordapp.com/emojis/{eid}."
                   + ("gif" if e.startswith("<a:") else "png"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(url) as r:
                        img = await r.read()
                new = await message.guild.create_custom_emoji(name=name, image=img)
                await message.channel.send(S.ui_ok(f"stolen: {new}"))
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif sub == "clear":
            n = 0
            for ch in message.guild.channels:
                try:
                    for wh in await ch.webhooks():
                        try:
                            await wh.delete()
                            n += 1
                        except Exception:
                            pass
                except Exception:
                    pass
            await message.channel.send(S.ui_ok(f"deleted {n} webhooks"))

        else:
            await message.channel.send(S.ui_info(
                "usage: webhook create/delete/list/spam/rename/emoji/steal/clear"))