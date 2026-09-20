# cogs/monitor.py | joins/leaves/roles/nicks/invites/keywords + log channel
from . import state as S


async def _monitor_log(guild, title, body):
    if not S._monitor["log_ch"]: return
    ch = S.CLIENT.get_channel(int(S._monitor["log_ch"]))
    if not ch: return
    try: await ch.send(S.ui_box(title, [body]))
    except Exception: pass


class MonitorCog:
    COMMANDS = {"monitor"}

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""

        if sub == "joins":
            S._monitor["joins"] = args[2].lower() in ("on","enable") if len(args) > 2 else not S._monitor["joins"]
            await message.channel.send(S.ui_ok(f"joins → {S._monitor['joins']}"))
        elif sub == "leaves":
            S._monitor["leaves"] = args[2].lower() in ("on","enable") if len(args) > 2 else not S._monitor["leaves"]
            await message.channel.send(S.ui_ok(f"leaves → {S._monitor['leaves']}"))
        elif sub == "roles":
            S._monitor["roles"] = args[2].lower() in ("on","enable") if len(args) > 2 else not S._monitor["roles"]
            await message.channel.send(S.ui_ok(f"roles → {S._monitor['roles']}"))
        elif sub == "nicks":
            S._monitor["nicks"] = args[2].lower() in ("on","enable") if len(args) > 2 else not S._monitor["nicks"]
            await message.channel.send(S.ui_ok(f"nicks → {S._monitor['nicks']}"))
        elif sub == "invites":
            S._monitor["invites"] = args[2].lower() in ("on","enable") if len(args) > 2 else not S._monitor["invites"]
            await message.channel.send(S.ui_ok(f"invites → {S._monitor['invites']}"))
        elif sub == "keywords" and len(args) >= 3:
            S._monitor["keywords"] = args[2:]
            await message.channel.send(S.ui_ok(f"keywords: {len(args)-2}"))
        elif sub == "keywordstop":
            S._monitor["keywords"] = []
            await message.channel.send(S.ui_ok("keywords cleared"))
        elif sub == "logch" and len(args) >= 3:
            S._monitor["log_ch"] = args[2]
            await message.channel.send(S.ui_ok("logch set"))
        elif sub == "status":
            await message.channel.send(S.ui_box("monitor", [
                f"  {S.DIM}joins{S.RESET}    {S._monitor['joins']}",
                f"  {S.DIM}leaves{S.RESET}   {S._monitor['leaves']}",
                f"  {S.DIM}roles{S.RESET}    {S._monitor['roles']}",
                f"  {S.DIM}nicks{S.RESET}    {S._monitor['nicks']}",
                f"  {S.DIM}invites{S.RESET}  {S._monitor['invites']}",
                f"  {S.DIM}logch{S.RESET}    {S._monitor['log_ch'] or '-'}",
                f"  {S.DIM}keywords{S.RESET} {len(S._monitor['keywords'])}",
            ]))
        else:
            await message.channel.send(S.ui_info(
                "usage: monitor joins/leaves/roles/nicks/invites/keywords/logch/status"))