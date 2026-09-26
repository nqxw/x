# cogs/pingtrack.py | ping counter + mention log
import time
from datetime import datetime
import modifyself_shim as discord
from . import state as S


async def _ping_pre_hook(client, message):
    if not S.ping_cfg.get("track"):
        return
    try:
        me = client.user
        if me is None: return
        mentioned = me in (message.mentions or [])
    except Exception:
        mentioned = False
    if not mentioned:
        return
    if message.author.id == client.user.id:
        return
    uid = message.author.id
    entry = S.ping_counts.setdefault(uid, {"count": 0, "last": 0, "messages": []})
    entry["count"] += 1
    entry["last"] = time.time()
    entry["messages"].append(message.id)
    if len(entry["messages"]) > 50:
        entry["messages"] = entry["messages"][-50:]
    S.mention_log.append({
        "ts": time.time(),
        "by": str(message.author),
        "by_id": uid,
        "in": getattr(message.channel, "name", str(getattr(message, "channel_id", "?"))),
        "content": (message.content or "")[:200],
    })
    if len(S.mention_log) > S.ping_cfg.get("max_store", 200):
        del S.mention_log[:-S.ping_cfg["max_store"]]
    if S.ping_cfg.get("log_channel") and S.CLIENT:
        try:
            ch = S.CLIENT.get_channel(int(S.ping_cfg["log_channel"]))
            if ch:
                await ch.send(S.ui_box("mention", [
                    f"  {S.DIM}by{S.RESET}  {message.author}",
                    f"  {S.DIM}in{S.RESET}  #{getattr(message.channel,'name','?')}",
                    "",
                    f"  {S.WHITE}{(message.content or '')[:200]}{S.RESET}",
                ]))
        except Exception:
            pass


class PingTrackCog:
    COMMANDS = {"pingcount", "pinglog", "pingreset", "pingtrack",
                "pingtop", "mentionlog"}

    def register(self, client):
        S.register_pre_hook(_ping_pre_hook)

    async def handle(self, message, cmd, args):
        if cmd == "pingcount":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
            if uid:
                e = S.ping_counts.get(uid)
                if not e:
                    return await message.edit(content=S.ui_info("no pings"))
                await message.edit(content=S.ui_box(f"pings by {uid}", [
                    f"  {S.DIM}count{S.RESET} {e['count']}",
                    f"  {S.DIM}last{S.RESET}  {datetime.fromtimestamp(e['last']).strftime('%H:%M:%S')}",
                ]))
            else:
                rows = [f"  {S.GREY}•{S.RESET} <@{u}>  {e['count']}"
                        for u, e in sorted(S.ping_counts.items(), key=lambda x: -x[1]['count'])[:20]]
                await message.edit(content=S._paginate("ping counts", "", rows)
                                            if rows else S.ui_info("no pings"))

        elif cmd == "pingtop":
            rows = [f"  {S.GREY}{i:2}.{S.RESET} <@{u}>  {e['count']}"
                    for i, (u, e) in enumerate(
                        sorted(S.ping_counts.items(), key=lambda x: -x[1]['count'])[:15], 1)]
            await message.edit(content=S._paginate("top pingers", "", rows)
                                        if rows else S.ui_info("none"))

        elif cmd in ("pinglog", "mentionlog"):
            if not S.mention_log:
                return await message.edit(content=S.ui_info("mention log empty"))
            rows = [f"  {S.GREY}{datetime.fromtimestamp(e['ts']).strftime('%H:%M')}{S.RESET}  "
                    f"{str(e['by'])[:18]:<18}  {S.DIM}#{e['in']}  {e['content'][:50]}{S.RESET}"
                    for e in S.mention_log[-30:]]
            await message.edit(content=S._paginate("mention log", "", rows))

        elif cmd == "pingreset":
            if len(args) > 1 and args[1].isdigit():
                S.ping_counts.pop(int(args[1]), None)
                await message.edit(content=S.ui_ok("cleared"))
            elif len(args) > 1 and args[1].lower() == "log":
                S.mention_log.clear()
                await message.edit(content=S.ui_ok("mention log cleared"))
            else:
                S.ping_counts.clear()
                await message.edit(content=S.ui_ok("all cleared"))

        elif cmd == "pingtrack":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "on":
                S.ping_cfg["track"] = True
            elif sub == "off":
                S.ping_cfg["track"] = False
            elif sub == "log" and len(args) >= 3:
                S.ping_cfg["log_channel"] = args[2]
            else:
                return await message.edit(content=S.ui_box("pingtrack", [
                    f"  {S.DIM}track{S.RESET}   {S.ping_cfg['track']}",
                    f"  {S.DIM}log ch{S.RESET}  {S.ping_cfg.get('log_channel') or '—'}",
                ]))
            await message.edit(content=S.ui_ok(f"track={S.ping_cfg['track']}"))