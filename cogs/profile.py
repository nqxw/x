# cogs/profile.py | setpfp, setbio, setbanner, myprofile, accountbackup
import base64
import json
from datetime import datetime
import aiohttp
from . import state as S


class ProfileCog:
    COMMANDS = {"setpfp", "setbio", "setbanner", "myprofile", "accountbackup"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "setpfp":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: setpfp <url>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(args[1]) as r:
                        img = await r.read()
                ext = args[1].split(".")[-1].split("?")[0].lower()
                mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                        "gif": "gif", "webp": "webp"}.get(ext, "png")
                b64 = base64.b64encode(img).decode()
                h = {"Authorization": S.TOKEN, "Content-Type": "application/json",
                     "User-Agent": S.USER_AGENT}
                async with aiohttp.ClientSession() as s:
                    async with s.patch("https://discord.com/api/v9/users/@me",
                                       headers=h,
                                       json={"avatar": f"data:image/{mime};base64,{b64}"}) as r2:
                        await message.edit(content=S.ui_ok("pfp updated")
                                                  if r2.status == 200
                                                  else S.ui_err(f"failed {r2.status}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "setbio":
            bio = " ".join(args[1:]) if len(args) > 1 else ""
            h = {"Authorization": S.TOKEN, "Content-Type": "application/json",
                 "User-Agent": S.USER_AGENT}
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.patch("https://discord.com/api/v9/users/@me/profile",
                                       headers=h, json={"bio": bio}) as r:
                        await message.edit(content=S.ui_ok("bio updated")
                                                  if r.status == 200
                                                  else S.ui_err(f"failed {r.status}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "setbanner":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: setbanner <url>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(args[1]) as r:
                        img = await r.read()
                ext = args[1].split(".")[-1].split("?")[0].lower()
                mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                        "gif": "gif", "webp": "webp"}.get(ext, "png")
                b64 = base64.b64encode(img).decode()
                h = {"Authorization": S.TOKEN, "Content-Type": "application/json",
                     "User-Agent": S.USER_AGENT}
                async with aiohttp.ClientSession() as s:
                    async with s.patch("https://discord.com/api/v9/users/@me",
                                       headers=h,
                                       json={"banner": f"data:image/{mime};base64,{b64}"}) as r2:
                        await message.edit(content=S.ui_ok("banner updated")
                                                  if r2.status == 200
                                                  else S.ui_err(f"failed {r2.status}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "myprofile":
            h = {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get("https://discord.com/api/v9/users/@me", headers=h) as r:
                        if r.status != 200:
                            return await message.edit(content=S.ui_err("failed"))
                        u = await r.json()
                uid = u.get("id")
                pfp = (f"https://cdn.discordapp.com/avatars/{uid}/{u.get('avatar')}.webp?size=256"
                       if u.get("avatar") else "none")
                banner = (f"https://cdn.discordapp.com/banners/{uid}/{u.get('banner')}.webp?size=512"
                          if u.get("banner") else "none")
                await message.edit(content=S.ui_box("my profile", [
                    f"  {S.DIM}username{S.RESET}  {u.get('username')}",
                    f"  {S.DIM}id{S.RESET}        {uid}",
                    f"  {S.DIM}avatar{S.RESET}    {pfp}",
                    f"  {S.DIM}banner{S.RESET}    {banner}",
                    f"  {S.DIM}email{S.RESET}     {u.get('email','?')}",
                    f"  {S.DIM}phone{S.RESET}     {u.get('phone','?')}",
                    f"  {S.DIM}nitro{S.RESET}     {'yes' if u.get('premium_type') else 'no'}",
                ]))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "accountbackup":
            try: await message.delete()
            except Exception: pass
            h = {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}
            async with aiohttp.ClientSession() as s:
                async with s.get("https://discord.com/api/v9/users/@me", headers=h) as r:
                    profile = await r.json() if r.status == 200 else {}
                async with s.get("https://discord.com/api/v9/users/@me/relationships",
                                 headers=h) as r:
                    rels = await r.json() if r.status == 200 else []
            backup = {"profile": profile, "relationships": rels,
                      "guilds": [{"id": str(g.id), "name": g.name} for g in client.guilds],
                      "timestamp": datetime.now().isoformat()}
            fname = f"backups/account_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(fname, "w") as f:
                json.dump(backup, f, indent=2)
            await message.channel.send(S.ui_ok(f"backed up → {fname}"), delete_after=8)