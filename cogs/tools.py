# cogs/tools.py | nitro gen, applybypass, tokeninfo, calculate, fact, fetchlyrics, robuxtax, archivechannel
import base64
import random
import re
import string
import aiohttp
from uuid import uuid4
from datetime import datetime
from . import state as S


class ToolsCog:
    COMMANDS = {"nitro", "applybypass", "tokeninfo", "calculate",
                "fact", "fetchlyrics", "robuxtax", "archivechannel"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "nitro":
            try: await message.delete()
            except Exception: pass
            code = "".join(random.choices(string.ascii_letters + string.digits, k=16))
            await message.channel.send(f"```\nhttps://discord.gift/{code}\n```")

        elif cmd == "applybypass":
            try: await message.delete()
            except Exception: pass
            if len(args) < 2:
                return await message.channel.send(S.ui_err("usage: applybypass <invite>"), delete_after=5)
            invite = args[1].replace("https://discord.gg/", "").replace("discord.gg/", "")
            h = {"Authorization": S.TOKEN, "Content-Type": "application/json",
                 "User-Agent": S.USER_AGENT}
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"https://discord.com/api/v9/invites/{invite}", headers=h) as r:
                        if r.status != 200:
                            return await message.channel.send(S.ui_err("invalid invite"), delete_after=6)
                        inv = await r.json()
                    guild_id = inv.get("guild", {}).get("id")
                    async with s.post(f"https://discord.com/api/v9/invites/{invite}",
                                      headers=h, json={"session_id": str(uuid4())[:8]}) as r2:
                        if r2.status in (200, 204):
                            await message.channel.send(S.ui_ok("joined"), delete_after=8)
                        elif r2.status == 403 and guild_id:
                            async with s.put(
                                f"https://discord.com/api/v9/guilds/{guild_id}/requests/@me",
                                headers=h, json={"form_fields": []}
                            ) as r3:
                                await message.channel.send(
                                    S.ui_ok("applied") if r3.status in (200, 201, 204)
                                    else S.ui_err(f"failed {r3.status}"),
                                    delete_after=8)
                        else:
                            await message.channel.send(S.ui_err(f"failed {r2.status}"),
                                                       delete_after=6)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=6)

        elif cmd == "tokeninfo":
            token = args[1] if len(args) > 1 else ""
            if not token:
                return await message.edit(content=S.ui_err("usage: tokeninfo <token>"))
            try:
                parts = token.split(".")
                uid_b64 = parts[0]
                uid = base64.b64decode(uid_b64 + "=" * (-len(uid_b64) % 4)).decode()
                ts_b64 = parts[1]
                pad = ts_b64 + "=" * (-len(ts_b64) % 4)
                ts_bytes = base64.b64decode(pad)
                epoch = int.from_bytes(ts_bytes[:4], "big")
                created = datetime.utcfromtimestamp(epoch + 1293840000).strftime("%Y-%m-%d %H:%M:%S")
                await message.edit(content=S.ui_box("token info", [
                    f"  {S.DIM}user_id{S.RESET}  {uid}",
                    f"  {S.DIM}created{S.RESET}  {created} UTC",
                ]))
            except Exception as e:
                await message.edit(content=S.ui_err(f"decode failed: {e}"))

        elif cmd == "calculate":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: calculate <expr>"))
            expr = " ".join(args[1:])
            try:
                result = eval(re.sub(r"[^0-9+\-*/(). ]", "", expr))
                await message.edit(content=S.ui_ok(f"{expr} = {result}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "fact":
            try: await message.delete()
            except Exception: pass
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        "https://uselessfacts.jsph.pl/api/v2/facts/random?language=en"
                    ) as r:
                        d = await r.json()
                        await message.channel.send(f"💡 {d.get('text','no fact')}")
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd == "fetchlyrics":
            if len(args) < 2:
                return await message.edit(content=S.ui_err(
                    "usage: fetchlyrics <artist - title>"))
            query = " ".join(args[1:])
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        f"https://lyrist.vercel.app/api/{query.replace(' - ','/')}"
                    ) as r:
                        if r.status == 200:
                            lyrics = (await r.json()).get("lyrics", "")[:1800]
                            await message.edit(content=f"```\n{lyrics}\n```")
                        else:
                            await message.edit(content=S.ui_err("not found"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "robuxtax":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: robuxtax <amount>"))
            try:
                amount = int(args[1])
                after = int(amount * 0.7)
                fee = amount - after
                await message.edit(content=S.ui_box("roblox fee", [
                    f"  {S.DIM}listed{S.RESET}  {amount:,} R$",
                    f"  {S.DIM}fee{S.RESET}     {fee:,} R$",
                    f"  {S.DIM}you get{S.RESET} {after:,} R$",
                ]))
            except ValueError:
                await message.edit(content=S.ui_err("invalid amount"))

        elif cmd == "archivechannel":
            try: await message.delete()
            except Exception: pass
            ch = (client.get_channel(int(args[1]))
                  if len(args) > 1 and args[1].isdigit() else message.channel)
            if not ch:
                return await message.channel.send(S.ui_err("not found"), delete_after=5)
            count = 0; out = []
            async for msg in ch.history(limit=2000):
                out.append(f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] "
                           f"{msg.author}: {msg.content}")
                count += 1
            fname = f"exports/archive_{ch.id}.txt"
            with open(fname, "w", encoding="utf-8") as f:
                f.write("\n".join(reversed(out)))
            await message.channel.send(S.ui_ok(f"archived {count} → {fname}"), delete_after=8)