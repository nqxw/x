# cogs/guards.py | blacklist / whitelist / server-block / channel-block / role-restrict / status / reset
from . import state as S


class GuardsCog:
    COMMANDS = {"blacklist", "whitelist",
                "serverblacklist", "channelblacklist", "rolerestrict",
                "guards"}

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass

        if cmd == "blacklist":
            await self._blacklist(message, args)
        elif cmd == "whitelist":
            await self._whitelist(message, args)
        elif cmd == "serverblacklist":
            await self._serverblacklist(message, args)
        elif cmd == "channelblacklist":
            await self._channelblacklist(message, args)
        elif cmd == "rolerestrict":
            await self._rolerestrict(message, args)
        elif cmd == "guards":
            await self._guards(message, args)

    # ── user blacklist ──
    async def _blacklist(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 3:
            S._user_blacklist.add(int(args[2]))
            await message.channel.send(S.ui_ok("added"))
        elif sub == "remove" and len(args) >= 3:
            S._user_blacklist.discard(int(args[2]))
            await message.channel.send(S.ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} <@{u}>" for u in S._user_blacklist]
            await message.channel.send(S._paginate("user blacklist", "", rows)
                                        if rows else S.ui_info("empty"))
        elif sub == "clear":
            S._user_blacklist.clear()
            await message.channel.send(S.ui_ok("cleared"))
        else:
            await message.channel.send(S.ui_info("usage: blacklist add/remove/list/clear"))

    # ── user whitelist ──
    async def _whitelist(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 3:
            S._user_whitelist.add(int(args[2]))
            await message.channel.send(S.ui_ok(f"whitelisted {args[2]} (whitelist ON)"))
        elif sub == "remove" and len(args) >= 3:
            S._user_whitelist.discard(int(args[2]))
            await message.channel.send(S.ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} <@{u}>" for u in S._user_whitelist]
            await message.channel.send(S._paginate("user whitelist", "", rows)
                                        if rows else S.ui_info("empty"))
        elif sub == "clear":
            S._user_whitelist.clear()
            await message.channel.send(S.ui_ok("cleared"))
        else:
            await message.channel.send(S.ui_info("usage: whitelist add/remove/list/clear"))

    # ── server-scoped command blocks ──
    async def _serverblacklist(self, message, args):
        if not message.guild:
            return await message.channel.send(S.ui_err("server only"), delete_after=5)
        sub = args[1].lower() if len(args) > 1 else ""
        gid = str(message.guild.id)
        if sub == "add" and len(args) >= 3:
            S._cmd_blacklist_server.setdefault(gid, set()).add(args[2])
            await message.channel.send(S.ui_ok(f"blocked `{args[2]}`"))
        elif sub == "remove" and len(args) >= 3:
            S._cmd_blacklist_server.get(gid, set()).discard(args[2])
            await message.channel.send(S.ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} {c}" for c in S._cmd_blacklist_server.get(gid, set())]
            await message.channel.send(S._paginate("server blacklist", message.guild.name, rows)
                                        if rows else S.ui_info("empty"))
        elif sub == "clear":
            S._cmd_blacklist_server.pop(gid, None)
            await message.channel.send(S.ui_ok("cleared"))
        else:
            await message.channel.send(S.ui_info(
                "usage: serverblacklist add/remove/list/clear"))

    # ── channel-scoped command blocks ──
    async def _channelblacklist(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        cid = str(message.channel.id)
        if sub == "add" and len(args) >= 3:
            S._cmd_blacklist_channel.setdefault(cid, set()).add(args[2])
            await message.channel.send(S.ui_ok(f"blocked `{args[2]}`"))
        elif sub == "remove" and len(args) >= 3:
            S._cmd_blacklist_channel.get(cid, set()).discard(args[2])
            await message.channel.send(S.ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} {c}" for c in S._cmd_blacklist_channel.get(cid, set())]
            await message.channel.send(S._paginate("channel blacklist", "", rows)
                                        if rows else S.ui_info("empty"))
        elif sub == "clear":
            S._cmd_blacklist_channel.pop(cid, None)
            await message.channel.send(S.ui_ok("cleared"))
        else:
            await message.channel.send(S.ui_info(
                "usage: channelblacklist add/remove/list/clear"))

    # ── role restrictions ──
    async def _rolerestrict(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 4:
            S._role_restrict.setdefault(args[2], set()).add(int(args[3]))
            await message.channel.send(S.ui_ok(f"`{args[2]}` → role {args[3]}"))
        elif sub == "remove" and len(args) >= 4:
            S._role_restrict.get(args[2], set()).discard(int(args[3]))
            await message.channel.send(S.ui_ok("removed"))
        elif sub == "list":
            rows = []
            for c, ids in S._role_restrict.items():
                for rid in ids:
                    rows.append(f"  {S.GREY}•{S.RESET} {c} → role {rid}")
            await message.channel.send(S._paginate("role restrictions", "", rows)
                                        if rows else S.ui_info("empty"))
        elif sub == "clear" and len(args) >= 3:
            S._role_restrict.pop(args[2], None)
            await message.channel.send(S.ui_ok("cleared"))
        else:
            await message.channel.send(S.ui_info(
                "usage: rolerestrict add/remove/list/clear"))

    # ── guards status / reset ──
    async def _guards(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "status":
            await message.edit(content=S.ui_box("guards", [
                f"  {S.DIM}user blacklist{S.RESET}   {len(S._user_blacklist)}",
                f"  {S.DIM}user whitelist{S.RESET}   {len(S._user_whitelist)}",
                f"  {S.DIM}server blk sets{S.RESET} {len(S._cmd_blacklist_server)}",
                f"  {S.DIM}channel blk sets{S.RESET} {len(S._cmd_blacklist_channel)}",
                f"  {S.DIM}role restrictions{S.RESET} {len(S._role_restrict)}",
                f"  {S.DIM}disabled cmds{S.RESET}    {len(S._cmd_disabled)}",
            ]))
        elif sub == "reset":
            S._user_blacklist.clear()
            S._user_whitelist.clear()
            S._cmd_blacklist_server.clear()
            S._cmd_blacklist_channel.clear()
            S._role_restrict.clear()
            S._cmd_disabled.clear()
            await message.edit(content=S.ui_ok("guards reset"))
        else:
            await message.edit(content=S.ui_info("usage: guards status/reset"))