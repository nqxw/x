# cogs/mass.py | mass DM, mass friend, mass join/leave, mass role/ban/kick, mass ch/vc/cat, mass react/delete
import asyncio
import os
import aiohttp
import discord
from uuid import uuid4
from . import state as S


# ── module-level helpers kept from the previous version ──

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
    COMMANDS = {"massdm", "stopdm", "dmstats",
                "massdmfile", "massfriend", "massjoin", "massleave",
                "massrole", "massunrole", "massban", "masskick", "massch", "massvc",
                "masscat", "massrolecreate", "massreact", "massdelete"}

    def __init__(self):
        # ── DM-all-open-channels state ──
        self.dm_running = False
        self.dm_stats = {"sent": 0, "failed": 0, "skipped": 0}
        self._orphan_tasks = set()

    # ── task tracking (fixes asyncio.create_task GC footgun) ──

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._orphan_tasks.add(t)
        t.add_done_callback(self._orphan_tasks.discard)
        return t

    async def cog_unload(self):
        self.dm_running = False
        for t in list(self._orphan_tasks):
            if not t.done():
                t.cancel()

    # ── formatting helpers ──

    def _format_response(self, content):
        """Wrap text in an ansi block. Content is expected to already be
        a bordered block (─── separators), so pass through as-is."""
        if isinstance(content, str):
            lines = content.split('\n')
        else:
            lines = content
        return S._ansi_block(lines)

    async def _send_and_delete(self, message, content, delete_after=2):
        """Send a message and schedule its deletion. Takes a discord.Message
        instead of a commands.Context so it fits the plain-class cog style."""
        try:
            await message.delete()
        except Exception:
            pass
        try:
            reply = await message.channel.send(content)
        except Exception:
            return None

        async def _del():
            await asyncio.sleep(delete_after)
            try:
                await reply.delete()
            except Exception:
                pass

        self._spawn(_del())
        return reply

    async def _delete_after_delay(self, message, delay=2):
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except Exception:
            pass

    # ── command dispatch ──

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        try: await message.delete()
        except Exception: pass

        # ── DM-all-open-channels commands ──

        if cmd == "massdm":
            return await self._cmd_massdm(message, args)

        if cmd == "stopdm":
            return await self._cmd_stopdm(message, args)

        if cmd == "dmstats":
            return await self._cmd_dmstats(message, args)

        # ── original mass-dm-file and friends ──

        if cmd == "massdmfile":
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

    # ── $massdm — DM every open DM channel ──

    async def _cmd_massdm(self, message, args):
        try:
            if len(args) < 2:
                await self._send_and_delete(message, S.ui_err("usage: massdm <message>"))
                return

            if self.dm_running:
                await self._send_and_delete(message, S.ui_warn("a mass DM is already running"))
                return

            msg_text = " ".join(args[1:])
            self.dm_running = True
            self.dm_stats = {"sent": 0, "failed": 0, "skipped": 0}

            # collect open DM channels
            dm_channels = [c for c in S.CLIENT.private_channels
                           if isinstance(c, discord.DMChannel)]

            if not dm_channels:
                self.dm_running = False
                await self._send_and_delete(message, S.ui_err("no DM channels found"))
                return

            start_msg = (
                f"Mass DM Started\n"
                f"━━━━━━━━━━━\n\n"
                f"• Total DMs: {len(dm_channels)}\n"
                f"• Message: {msg_text[:50]}{'...' if len(msg_text) > 50 else ''}\n"
                f"• Status: Running..."
            )
            try:
                await message.delete()
            except Exception:
                pass
            status_msg = await message.channel.send(self._format_response(start_msg))

            for i, channel in enumerate(dm_channels):
                if not self.dm_running:
                    break

                # skip channels with no messages
                try:
                    has_any = False
                    async for _m in channel.history(limit=1):
                        has_any = True
                        break
                    if not has_any:
                        self.dm_stats["skipped"] += 1
                        continue
                except Exception:
                    self.dm_stats["skipped"] += 1
                    continue

                try:
                    await channel.send(msg_text)
                    self.dm_stats["sent"] += 1
                except discord.Forbidden:
                    self.dm_stats["failed"] += 1
                except discord.HTTPException as e:
                    if getattr(e, "status", None) == 429:
                        retry_after = 5
                        try:
                            ra_hdr = e.response.headers.get("Retry-After")
                            if ra_hdr:
                                retry_after = float(ra_hdr)
                        except Exception:
                            pass
                        warn = (
                            f"Rate Limited\n"
                            f"━━━━━━━━━━━\n\n"
                            f"Waiting {retry_after}s before continuing..."
                        )
                        try:
                            warning_response = await message.channel.send(
                                self._format_response(warn))
                            self._spawn(self._delete_after_delay(warning_response, 2))
                        except Exception:
                            pass
                        await asyncio.sleep(retry_after)
                        try:
                            await channel.send(msg_text)
                            self.dm_stats["sent"] += 1
                        except Exception:
                            self.dm_stats["failed"] += 1
                    else:
                        self.dm_stats["failed"] += 1
                except Exception:
                    self.dm_stats["failed"] += 1

                # progress update every 5
                if (i + 1) % 5 == 0:
                    progress = (
                        f"Mass DM Progress\n"
                        f"━━━━━━━━━━━\n\n"
                        f"• Progress: {i+1}/{len(dm_channels)}\n"
                        f"• Sent: {self.dm_stats['sent']}\n"
                        f"• Failed: {self.dm_stats['failed']}\n"
                        f"• Skipped: {self.dm_stats['skipped']}"
                    )
                    try:
                        await status_msg.edit(content=self._format_response(progress))
                    except Exception:
                        pass

                await asyncio.sleep(3)

            self.dm_running = False

            final = (
                f"Mass DM Complete\n"
                f"━━━━━━━━━━━\n\n"
                f"• Total DMs: {len(dm_channels)}\n"
                f"• Sent: {self.dm_stats['sent']}\n"
                f"• Failed: {self.dm_stats['failed']}\n"
                f"• Skipped: {self.dm_stats['skipped']}"
            )
            try:
                await status_msg.edit(content=self._format_response(final))
                self._spawn(self._delete_after_delay(status_msg, 2))
            except Exception:
                pass

        except Exception as e:
            self.dm_running = False
            try:
                await self._send_and_delete(message, S.ui_err(f"error in mass DM: {e}"))
            except Exception:
                pass

    # ── $stopdm ──

    async def _cmd_stopdm(self, message, args):
        try:
            if self.dm_running:
                self.dm_running = False
                stop = (
                    f"Mass DM Stopped\n"
                    f"━━━━━━━━━━━\n\n"
                    f"• Sent: {self.dm_stats['sent']}\n"
                    f"• Failed: {self.dm_stats['failed']}\n"
                    f"• Skipped: {self.dm_stats['skipped']}"
                )
                await self._send_and_delete(message, self._format_response(stop))
            else:
                await self._send_and_delete(message, S.ui_err("no mass DM is currently running"))
        except Exception as e:
            await self._send_and_delete(message, S.ui_err(str(e)))

    # ── $dmstats ──

    async def _cmd_dmstats(self, message, args):
        try:
            dm_count = len([c for c in S.CLIENT.private_channels
                            if isinstance(c, discord.DMChannel)])
            stats = (
                f"DM Statistics\n"
                f"━━━━━━━━━━━\n\n"
                f"• Total DM Channels: {dm_count}\n"
                f"• DM Status: {'Running' if self.dm_running else 'Idle'}\n"
                f"• Messages Sent: {self.dm_stats['sent']}\n"
                f"• Messages Failed: {self.dm_stats['failed']}\n"
                f"• DMs Skipped: {self.dm_stats['skipped']}"
            )
            await self._send_and_delete(message, self._format_response(stats))
        except Exception as e:
            await self._send_and_delete(message, S.ui_err(str(e)))
