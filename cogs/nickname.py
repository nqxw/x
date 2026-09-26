# cogs/nickname.py | auto-nickname + nickname history
import time
import modifyself_shim as discord
from . import state as S


class NicknameCog:
    COMMANDS = {"autonick", "nickhistory", "nickclear", "nickset"}

    def register(self, client):
        cog = self
        @client.event
        async def on_member_update(before, after):
            if (getattr(before, "nick", None) != getattr(after, "nick", None)):
                uid = getattr(after, "id", None)
                if uid is None: return
                S.nickname["history"].setdefault(uid, []).append({
                    "ts": time.time(),
                    "old": before.nick,
                    "new": after.nick,
                    "by": "self" if before.nick == after.nick else "mod",
                })
                hist = S.nickname["history"][uid]
                if len(hist) > 50:
                    S.nickname["history"][uid] = hist[-50:]

        @client.event
        async def on_ready():
            await cog._auto_nick_once(client)

    async def _auto_nick_once(self, client):
        n = S.nickname
        if not n["auto"] or not n["pattern"]:
            return
        try:
            me = client.user
        except Exception:
            return
        new = n["pattern"].replace("{user}", getattr(me, "name", "")).replace(
            "{discrim}", getattr(me, "discriminator", "0")[:4])
        for g in getattr(client, "guilds", []) or []:
            try:
                member = g.get_member(me.id)
                if member and getattr(member, "nick", None) != new:
                    await member.edit(nick=new)
            except Exception:
                pass
        n["last_run"] = time.time()

    async def handle(self, message, cmd, args):
        n = S.nickname
        if cmd == "autonick":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "on":
                n["auto"] = True
            elif sub == "off":
                n["auto"] = False
            elif sub == "pattern" and len(args) >= 3:
                n["pattern"] = " ".join(args[2:])
            elif sub == "interval" and len(args) >= 3 and args[2].isdigit():
                n["interval"] = int(args[2])
            elif sub == "run":
                await self._auto_nick_once(S.CLIENT)
                return await message.edit(content=S.ui_ok("applied"))
            else:
                return await message.edit(content=S.ui_box("autonick", [
                    f"  {S.DIM}enabled{S.RESET}  {n['auto']}",
                    f"  {S.DIM}pattern{S.RESET}  {n['pattern']}",
                    f"  {S.DIM}interval{S.RESET} {n['interval']}s",
                ]))
            await message.edit(content=S.ui_ok(
                f"autonick: auto={n['auto']} pattern={n['pattern']} interval={n['interval']}s"))

        elif cmd == "nickset":
            if not message.guild or len(args) < 3:
                return await message.edit(content=S.ui_err("usage: nickset <uid> <nick>"))
            try:
                m = message.guild.get_member(int(args[1]))
                await m.edit(nick=" ".join(args[2:]))
                await message.edit(content=S.ui_ok("set"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "nickhistory":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            hist = n["history"].get(uid, [])
            if not hist:
                return await message.edit(content=S.ui_info("no history"))
            rows = []
            for e in hist[-30:]:
                rows.append(f"  {S.GREY}•{S.RESET} {e['old'] or '—'} → {e['new'] or '—'}  "
                            f"{S.DIM}{int(time.time() - e['ts'])}s ago{S.RESET}")
            await message.edit(content=S._paginate("nick history", f"user {uid}", rows))

        elif cmd == "nickclear":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            n["history"].pop(uid, None)
            await message.edit(content=S.ui_ok("cleared"))