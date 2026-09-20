# cogs/server.py | serverinfo, members, channels, roles, ban/kick/mute, setnick, topic, slowmode, icon/banner/name
import aiohttp
from datetime import timedelta
import discord
from . import state as S


class ServerCog:
    COMMANDS = {"serverinfo", "members", "channels", "roles",
                "ban", "kick", "mute", "unmute",
                "setnick", "topic", "slowmode",
                "servericon", "serverbanner", "servername"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "serverinfo":
            g = message.guild
            if not g:
                return await message.edit(content=S.ui_err("not in a server"))
            await message.edit(content=S.ui_box(g.name, [
                f"  {S.DIM}id{S.RESET}          {g.id}",
                f"  {S.DIM}owner{S.RESET}       {g.owner}",
                f"  {S.DIM}members{S.RESET}     {g.member_count}",
                f"  {S.DIM}channels{S.RESET}    {len(g.channels)}",
                f"  {S.DIM}roles{S.RESET}       {len(g.roles)}",
                f"  {S.DIM}created{S.RESET}     {g.created_at.strftime('%Y-%m-%d')}",
                f"  {S.DIM}boost level{S.RESET} {g.premium_tier}",
            ]))

        elif cmd == "members":
            g = message.guild
            if not g:
                return await message.edit(content=S.ui_err("not in a server"))
            n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
            rows = [f"  {S.GREY}•{S.RESET} {m.display_name}  {S.DIM}({m.id}){S.RESET}"
                    for m in list(g.members)[:n]]
            await message.edit(content=S._paginate("members", g.name, rows))

        elif cmd == "channels":
            g = message.guild
            if not g:
                return await message.edit(content=S.ui_err("not in a server"))
            rows = [f"  {S.GREY}•{S.RESET} #{ch.name}  {S.DIM}({ch.id}){S.RESET}"
                    for ch in g.channels]
            await message.edit(content=S._paginate("channels", g.name, rows))

        elif cmd == "roles":
            g = message.guild
            if not g:
                return await message.edit(content=S.ui_err("not in a server"))
            rows = [f"  {S.GREY}•{S.RESET} {r.name}  {S.DIM}({r.id}){S.RESET}"
                    for r in g.roles]
            await message.edit(content=S._paginate("roles", g.name, rows))

        elif cmd in ("ban", "kick", "mute", "unmute"):
            g = message.guild
            if not g or len(args) < 2:
                return await message.edit(content=S.ui_err(f"usage: {cmd} <user_id>"))
            try:
                member = g.get_member(int(args[1]))
                if cmd == "ban":
                    await g.ban(member, reason=" ".join(args[2:]) or "no reason")
                elif cmd == "kick":
                    await g.kick(member, reason=" ".join(args[2:]) or "no reason")
                elif cmd == "mute":
                    until = discord.utils.utcnow() + timedelta(minutes=10)
                    await member.timeout(until)
                elif cmd == "unmute":
                    await member.timeout(None)
                await message.edit(content=S.ui_ok(f"{cmd} → {member}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "setnick":
            g = message.guild
            if not g or len(args) < 3:
                return await message.edit(content=S.ui_err("usage: setnick <user_id> <nick>"))
            try:
                m = g.get_member(int(args[1]))
                await m.edit(nick=" ".join(args[2:]))
                await message.edit(content=S.ui_ok("nick set"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "topic":
            if not message.guild or len(args) < 2:
                return await message.edit(content=S.ui_err("usage: topic <text>"))
            try:
                await message.channel.edit(topic=" ".join(args[1:]))
                await message.edit(content=S.ui_ok("topic set"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "slowmode":
            if not message.guild or len(args) < 2:
                return await message.edit(content=S.ui_err("usage: slowmode <seconds>"))
            try:
                await message.channel.edit(slowmode_delay=int(args[1]))
                await message.edit(content=S.ui_ok("slowmode set"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "servericon":
            if not message.guild or len(args) < 2:
                return await message.edit(content=S.ui_err("usage: servericon <url>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(args[1]) as r:
                        img = await r.read()
                await message.guild.edit(icon=img)
                await message.edit(content=S.ui_ok("icon set"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "serverbanner":
            if not message.guild or len(args) < 2:
                return await message.edit(content=S.ui_err("usage: serverbanner <url>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(args[1]) as r:
                        img = await r.read()
                await message.guild.edit(banner=img)
                await message.edit(content=S.ui_ok("banner set"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "servername":
            if not message.guild or len(args) < 2:
                return await message.edit(content=S.ui_err("usage: servername <name>"))
            try:
                await message.guild.edit(name=" ".join(args[1:]))
                await message.edit(content=S.ui_ok("name set"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))