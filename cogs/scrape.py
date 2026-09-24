# cogs/scrape.py | export members/messages/invites, server structure dumps, import
import os
import json
import time
import csv
import modifyself_shim as discord
from . import state as S


async def _dump_server(guild):
    from .nuke import _backup_server
    p = f"exports/server_{guild.id}_{int(time.time())}.json"
    data_path = await _backup_server(guild)
    with open(data_path) as f: data = json.load(f)
    with open(p, "w") as f: json.dump(data, f, indent=2)
    return p


class ScrapeCog:
    COMMANDS = {"scrape", "export", "import"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        try: await message.delete()
        except Exception: pass

        if cmd == "scrape":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "members" and message.guild:
                rows = [f"  {S.GREY}•{S.RESET} {m}  {S.DIM}({m.id}){S.RESET}"
                        for m in list(message.guild.members)[:100]]
                await message.channel.send(S._paginate("members", message.guild.name, rows))
            elif sub == "invites" and message.guild:
                try:
                    invs = await message.guild.invites()
                    rows = [f"  {S.GREY}•{S.RESET} {i.get('code')}  {S.DIM}uses={i.get('uses')}{S.RESET}"
                            for i in invs]
                    await message.channel.send(
                        S._paginate("invites", message.guild.name, rows) if rows else S.ui_info("none"))
                except Exception as e:
                    await message.channel.send(S.ui_err(str(e)), delete_after=5)
            elif sub == "channel" and len(args) >= 3:
                ch = client.get_channel(int(args[2]))
                if not ch:
                    return await message.channel.send(S.ui_err("not found"), delete_after=5)
                n = int(args[3]) if len(args) > 3 and args[3].isdigit() else 500
                out = []
                async for m in ch.history(limit=n):
                    out.append({"author": str(m.author), "id": m.id, "content": m.content,
                                "ts": m.timestamp.isoformat() if hasattr(m.timestamp, "isoformat") else str(m.timestamp)})
                p = f"exports/channel_{ch.id}_{int(time.time())}.json"
                with open(p, "w") as f: json.dump(out, f, indent=2)
                await message.channel.send(S.ui_ok(f"exported {len(out)} → {p}"))
            elif sub == "server" and message.guild:
                p = await _dump_server(message.guild)
                await message.channel.send(S.ui_ok(f"server dump: {p}"))
            else:
                await message.channel.send(S.ui_info("usage: scrape members/invites/channel/server"))

        elif cmd == "export":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "members" and len(args) >= 3:
                g = client.get_guild(int(args[2]))
                if not g:
                    return await message.channel.send(S.ui_err("guild not found"), delete_after=5)
                p = f"exports/members_{g.id}_{int(time.time())}.csv"
                with open(p, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f); w.writerow(["id","name","nick","joined","bot"])
                    for m in g.members:
                        w.writerow([m.id, str(m), m.nick or "",
                                    m.joined_at.isoformat() if m.joined_at else "", False])
                await message.channel.send(S.ui_ok(f"exported {len(g.members)} → {p}"))
            elif sub == "messages" and len(args) >= 3:
                ch = client.get_channel(int(args[2]))
                if not ch:
                    return await message.channel.send(S.ui_err("not found"), delete_after=5)
                n = int(args[3]) if len(args) > 3 and args[3].isdigit() else 1000
                out = []
                async for m in ch.history(limit=n):
                    out.append({"author": str(m.author), "content": m.content,
                                "ts": m.timestamp.isoformat() if hasattr(m.timestamp, "isoformat") else str(m.timestamp)})
                p = f"exports/messages_{ch.id}_{int(time.time())}.json"
                with open(p, "w") as f: json.dump(out, f, indent=2)
                await message.channel.send(S.ui_ok(f"exported {len(out)} → {p}"))
            elif sub == "invites" and len(args) >= 3:
                g = client.get_guild(int(args[2]))
                if not g:
                    return await message.channel.send(S.ui_err("guild not found"), delete_after=5)
                invs = await g.invites()
                p = f"exports/invites_{g.id}_{int(time.time())}.json"
                with open(p, "w") as f:
                    json.dump(invs, f, indent=2, default=str)
                await message.channel.send(S.ui_ok(f"exported {len(invs)} → {p}"))
            else:
                await message.channel.send(S.ui_info("usage: export members/messages/invites"))

        elif cmd == "import":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "members" and len(args) >= 3 and os.path.exists(args[2]):
                with open(args[2]) as f:
                    data = json.load(f) if args[2].endswith(".json") else [l.strip() for l in f]
                await message.channel.send(S.ui_ok(f"loaded {len(data)} ids from {args[2]}"))
            else:
                await message.channel.send(S.ui_info("usage: import members <path>"))