# cogs/perms.py | per-command user allow, block, channel/server restriction, reset
from . import state as S


class PermsCog:
    COMMANDS = {"perm"}

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""

        if sub == "add" and len(args) >= 4:
            S._perm_allow.setdefault(args[2], set()).add(int(args[3]))
            await message.channel.send(S.ui_ok(f"allowed {args[3]} on `{args[2]}`"))
        elif sub == "remove" and len(args) >= 4 and args[2] in S._perm_allow:
            S._perm_allow[args[2]].discard(int(args[3]))
            await message.channel.send(S.ui_ok("removed"))
        elif sub == "block" and len(args) >= 3:
            S._perm_block.add(args[2])
            await message.channel.send(S.ui_ok(f"blocked `{args[2]}`"))
        elif sub == "unblock" and len(args) >= 3:
            S._perm_block.discard(args[2])
            await message.channel.send(S.ui_ok("unblocked"))
        elif sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} {k} → {v}" for k, v in S._perm_allow.items()]
            rows += [f"  {S.GREY}•{S.RESET} BLOCKED: {c}" for c in S._perm_block]
            await message.channel.send(
                S._paginate("perms", "", rows) if rows else S.ui_info("no overrides"))
        elif sub == "channel" and len(args) >= 4:
            S._perm_channel[args[3]] = int(args[2])
            await message.channel.send(S.ui_ok(f"`{args[3]}` → channel {args[2]}"))
        elif sub == "server" and len(args) >= 4:
            S._perm_server[args[3]] = int(args[2])
            await message.channel.send(S.ui_ok(f"`{args[3]}` → server {args[2]}"))
        elif sub == "reset":
            S._perm_allow.clear(); S._perm_block.clear()
            S._perm_channel.clear(); S._perm_server.clear()
            await message.channel.send(S.ui_ok("perms wiped"))
        else:
            await message.channel.send(S.ui_info(
                "usage: perm add/remove/block/unblock/list/channel/server/reset"))