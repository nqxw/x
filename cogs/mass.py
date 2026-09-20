# cogs/mass.py | mass DM, mass friend, mass join/leave, mass role/ban/kick, mass ch/vc/cat, mass react/delete
import asyncio
import os
import aiohttp
from uuid import uuid4
from . import state as S


async def _mass_dm(guild, msg):
    done = 0
    client = S.CLIENT
    for m in list(guild.members):
        if m.bot or m.id == client.user.id: continue
        try: await m.send(msg); done += 1
        except Exception: pass
        await asyncio.sleep(1.2)
    return done


async def _mass_friend_ids(ids):
    done = 0
    h = {"Authorization": S.TOKEN, "Content-Type":"application/json", "User-Agent": S.USER_AGENT}
    async with aiohttp.ClientSession() as s:
        for uid in ids:
            try:
                async with s.put(f"https://discord.com/api/v9/users/@me/relationships/{uid}",
                                 headers=h, json={"type": 1}) as r:
                    if r.status in (200,201,204): done += 1
            except Exception: pass
            await asyncio.sleep(1.5)
    return done


async def _mass_role(guild, role_id, ids):
    role = guild.get_role(int(role_id))
    if not role: return 0
    done = 0
    for uid in ids:
        m = guild.get_member(int(uid))
        if not m: continue
        try: await m.add_roles(role); done += 1
        except Exception: pass
        await asyncio.sleep(0.6)
    return done


async def _mass_unrole(guild, role_id, ids):
    role = guild.get_role(int(role_id))
    if not role: return 0
    done = 0
    for uid in ids:
        m = guild.get_member(int(uid))
        if not m: continue
        try: await m.remove_roles(role); done += 1
        except Exception: pass
        await asyncio.sleep(0.6)
    return done


async def _mass_ban(guild, ids, reason="mass ban"):
    done = 0
    for uid in ids:
        try:
            user = await S.CLIENT.fetch_user(int(uid))
            await guild.ban(user, reason=reason); done += 1
        except Exception: pass
        await asyncio.sleep(0.8)
    return done


async def _mass_kick(guild, ids, reason="mass kick"):
    done = 0
    for uid in ids:
        m = guild.get_member(int(uid))
        if not m: continue
        try: await m.kick(reason=reason); done += 1
        except Exception: pass
        await asyncio.sleep(0.8)
    return done


async def _mass_join(invite, tokens):
    done = 0
    h_tmpl = {"Content-Type":"application/json","User-Agent": S.USER_AGENT}
    async with aiohttp.ClientSession() as s:
        for tk in tokens:
            try:
                async with s.post(f"https://discord.com/api/v9/invites/{invite}",
                    headers={**h_tmpl, "Authorization": tk.strip()},
                    json={"session_id": str(uuid4())[:12]}) as r:
                    if r.status in (200,204): done += 1
            except Exception: pass
            await asyncio.sleep(1.5)
    return done


async def _mass_leave(guild_id, tokens):
    done = 0
    h_tmpl = {"User-Agent": S.USER_AGENT}
    async with aiohttp.ClientSession() as s:
        for tk in tokens:
            try:
                async with s.delete(f"https://discord.com/api/v9/users/@me/guilds/{guild_id}",
                                    headers={**h_tmpl, "Authorization": tk.strip()}) as r:
                    if r.status in (200,204): done += 1
            except Exception: pass
            await asyncio.sleep(1.0)
    return done


class MassCog:
    COMMANDS = {"massdm", "massdmfile", "massfriend", "massjoin", "massleave",
                "massrole", "massunrole", "massban", "masskick", "massch", "massvc",
                "masscat", "massrolecreate", "massreact", "massdelete"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        try: await message.delete()
        except Exception: pass

        if cmd == "massdm":
            if not message.guild:
                return await message.channel.send(S.ui_err("server only"), delete_after=5)
            if len(args) < 2:
                return await message.channel.send(S.ui_err("usage: massdm <msg>"), delete_after=5)
            txt = " ".join(args[1:])
            await message.channel.send(S.ui_info("mass dm started"))
            done = await _mass_dm(message.guild, txt)
            await message.channel.send(S.ui_ok(f"sent to {done} members"))

        elif cmd == "massdmfile":
            if len(args) < 3 or not os.path.exists(args[1]):
                return await message.channel.send(S.ui_err("usage: massdmfile <path> <msg>"), delete_after=5)
            txt = " ".join(args[2:])
            with open(args[1]) as f: ids = [l.strip() for l in f if l.strip()]
            done = 0
            for uid in ids:
                try:
                    u = await client.fetch_user(int(uid))
                    await u.send(txt); done += 1
                except Exception: pass
                await asyncio.sleep(1.2)
            await message.channel.send(S.ui_ok(f"sent to {done}/{len(ids)}"))

        elif cmd == "massfriend":
            if len(args) < 2 or not os.path.exists(args[1]):
                return await message.channel.send(S.ui_err("usage: massfriend <file>"), delete_after=5)
            with open(args[1]) as f: ids = [l.strip() for l in f if l.strip()]
            done = await _mass_friend_ids(ids)
            await message.channel.send(S.ui_ok(f"sent {done}/{len(ids)}"))

        elif cmd == "massjoin":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: massjoin <invite> <count>"), delete_after=5)
            invite = args[1].replace("https://discord.gg/","").replace("discord.gg/","")
            count = int(args[2]) if args[2].isdigit() else 1
            tokens = S.HOSTED_TOKENS[:count]
            done = await _mass_join(invite, tokens)
            await message.channel.send(S.ui_ok(f"joined {done}/{len(tokens)}"))

        elif cmd == "massleave":
            if len(args) < 2:
                return await message.channel.send(S.ui_err("usage: massleave <guild_id>"), delete_after=5)
            done = await _mass_leave(args[1], S.HOSTED_TOKENS)
            await message.channel.send(S.ui_ok(f"left {done}/{len(S.HOSTED_TOKENS)}"))

        elif cmd == "massrole":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: massrole <role_id> <uids...>"), delete_after=5)
            done = await _mass_role(message.guild, args[1], args[2:])
            await message.channel.send(S.ui_ok(f"role assigned to {done}"))

        elif cmd == "massunrole":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: massunrole <role_id> <uids...>"), delete_after=5)
            done = await _mass_unrole(message.guild, args[1], args[2:])
            await message.channel.send(S.ui_ok(f"removed from {done}"))

        elif cmd == "massban":
            if not message.guild or len(args) < 2:
                return await message.channel.send(S.ui_err("usage: massban <uids...>"), delete_after=5)
            done = await _mass_ban(message.guild, args[1:])
            await message.channel.send(S.ui_ok(f"banned {done}"))

        elif cmd == "masskick":
            if not message.guild or len(args) < 2:
                return await message.channel.send(S.ui_err("usage: masskick <uids...>"), delete_after=5)
            done = await _mass_kick(message.guild, args[1:])
            await message.channel.send(S.ui_ok(f"kicked {done}"))

        elif cmd == "massch":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: massch <name> <n>"), delete_after=5)
            n = int(args[2]) if args[2].isdigit() else 5
            for _ in range(min(n, 50)):
                try: await message.guild.create_text_channel(args[1])
                except Exception: pass
            await message.channel.send(S.ui_ok(f"created {min(n,50)}"))

        elif cmd == "massvc":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: massvc <name> <n>"), delete_after=5)
            n = int(args[2]) if args[2].isdigit() else 5
            for _ in range(min(n, 50)):
                try: await message.guild.create_voice_channel(args[1])
                except Exception: pass
            await message.channel.send(S.ui_ok(f"created {min(n,50)}"))

        elif cmd == "masscat":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: masscat <name> <n>"), delete_after=5)
            n = int(args[2]) if args[2].isdigit() else 5
            for _ in range(min(n, 20)):
                try: await message.guild.create_category(args[1])
                except Exception: pass
            await message.channel.send(S.ui_ok(f"created {min(n,20)}"))

        elif cmd == "massrolecreate":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: massrolecreate <name> <n>"), delete_after=5)
            n = int(args[2]) if args[2].isdigit() else 5
            for _ in range(min(n, 50)):
                try: await message.guild.create_role(name=args[1])
                except Exception: pass
            await message.channel.send(S.ui_ok(f"created {min(n,50)}"))

        elif cmd == "massreact":
            if len(args) < 2:
                return await message.channel.send(S.ui_err("usage: massreact <emoji>"), delete_after=5)
            emoji = args[1]; done = 0
            async for msg in message.channel.history(limit=10):
                try: await msg.add_reaction(emoji); done += 1
                except Exception: pass
                await asyncio.sleep(0.4)
            await message.channel.send(S.ui_ok(f"reacted to {done}"))

        elif cmd == "massdelete":
            n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
            d = 0
            async for msg in message.channel.history(limit=n*2):
                if msg.author.id == client.user.id:
                    try: await msg.delete()
                    except Exception: pass
                    d += 1; await asyncio.sleep(0.3)
                    if d >= n: break
            await message.channel.send(S.ui_ok(f"deleted {d}"), delete_after=5)