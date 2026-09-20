# cogs/information.py | userinfo, avatar, whois, checkname, channelinfo, roleinfo
import aiohttp
from . import state as S


class InformationCog:
    COMMANDS = {"userinfo", "avatar", "checkname", "whois",
                "channelinfo", "roleinfo"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "userinfo":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/users/{uid}",
                                     headers=h) as r:
                        if r.status != 200:
                            return await message.edit(content=S.ui_err("not found"))
                        u = await r.json()
                pfp = (f"https://cdn.discordapp.com/avatars/{uid}/{u.get('avatar')}.webp?size=256"
                       if u.get("avatar") else "none")
                await message.edit(content=S.ui_box("user info", [
                    f"  {S.DIM}username{S.RESET}   {u.get('username','?')}",
                    f"  {S.DIM}id{S.RESET}         {uid}",
                    f"  {S.DIM}avatar{S.RESET}     {pfp}",
                    f"  {S.DIM}bot{S.RESET}        {'yes' if u.get('bot') else 'no'}",
                    f"  {S.DIM}flags{S.RESET}      {u.get('public_flags',0)}",
                ]))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "avatar":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/users/{uid}",
                                     headers=h) as r:
                        u = await r.json()
                if u.get("avatar"):
                    await message.edit(content=(
                        f"https://cdn.discordapp.com/avatars/{uid}/{u['avatar']}.webp?size=2048"))
                else:
                    await message.edit(content=S.ui_info("no avatar"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "checkname":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: checkname <username>"))
            username = args[1].lower().strip()
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "Content-Type": "application/json",
                         "User-Agent": S.USER_AGENT}
                    async with s.post("https://discord.com/api/v9/users/@me/pomelo-attempt",
                                      headers=h, json={"username": username}) as r:
                        if r.status == 200:
                            taken = (await r.json()).get("taken", True)
                            await message.edit(content=(
                                S.ui_ok(f"`{username}` available") if not taken
                                else S.ui_err(f"`{username}` taken")))
                        else:
                            await message.edit(content=S.ui_err(f"check failed {r.status}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "whois":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/users/{uid}/profile",
                                     headers=h) as r:
                        if r.status != 200:
                            return await message.edit(content=S.ui_err("not found"))
                        p = await r.json()
                u = p.get("user", {})
                badges = [b.get("id", "") for b in p.get("badges", [])]
                await message.edit(content=S.ui_box("whois", [
                    f"  {S.DIM}username{S.RESET}       {u.get('username','?')}",
                    f"  {S.DIM}id{S.RESET}             {uid}",
                    f"  {S.DIM}bio{S.RESET}            {u.get('bio','') or '-'}",
                    f"  {S.DIM}badges{S.RESET}         {', '.join(badges) or 'none'}",
                    f"  {S.DIM}nitro{S.RESET}          {'yes' if p.get('premium_since') else 'no'}",
                    f"  {S.DIM}mutual servers{S.RESET} {len(p.get('mutual_guilds', []))}",
                    f"  {S.DIM}mutual friends{S.RESET} {len(p.get('mutual_friends', []))}",
                ]))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "channelinfo":
            cid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.channel.id
            ch = client.get_channel(cid)
            if not ch:
                return await message.edit(content=S.ui_err("not found"))
            rows = [f"  {S.DIM}name{S.RESET}    #{getattr(ch, 'name', cid)}",
                    f"  {S.DIM}id{S.RESET}      {ch.id}",
                    f"  {S.DIM}type{S.RESET}    {str(ch.type)}"]
            try:
                rows.append(f"  {S.DIM}created{S.RESET} {ch.created_at.strftime('%Y-%m-%d')}")
            except Exception: pass
            rows.append(f"  {S.DIM}guild{S.RESET}   "
                        f"{ch.guild.name if getattr(ch,'guild',None) else 'DM'}")
            await message.edit(content=S.ui_box("channel info", rows))

        elif cmd == "roleinfo":
            if not message.guild or len(args) < 2:
                return await message.edit(content=S.ui_err("usage: roleinfo <role_id>"))
            role = message.guild.get_role(int(args[1]))
            if not role:
                return await message.edit(content=S.ui_err("not found"))
            await message.edit(content=S.ui_box("role info", [
                f"  {S.DIM}name{S.RESET}        {role.name}",
                f"  {S.DIM}id{S.RESET}          {role.id}",
                f"  {S.DIM}color{S.RESET}       #{role.color.value:06x}",
                f"  {S.DIM}members{S.RESET}     {len(role.members)}",
                f"  {S.DIM}position{S.RESET}    {role.position}",
                f"  {S.DIM}mentionable{S.RESET} {'yes' if role.mentionable else 'no'}",
                f"  {S.DIM}hoisted{S.RESET}     {'yes' if role.hoist else 'no'}",
            ]))
