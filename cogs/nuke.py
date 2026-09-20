# cogs/nuke.py | destructive server ops + backup/restore bridge
import os
import time
from . import state as S


async def _backup_server(guild):
    data = {"id": str(guild.id), "name": guild.name,
            "icon": str(guild.icon) if guild.icon else None,
            "roles": [{"id": str(r.id), "name": r.name, "color": r.color.value, "perms": r.permissions.value,
                       "position": r.position, "hoist": r.hoist, "mentionable": r.mentionable}
                      for r in guild.roles],
            "categories": [{"id": str(c.id), "name": c.name, "position": c.position} for c in guild.categories],
            "channels": [{"id": str(ch.id), "name": ch.name, "type": str(ch.type),
                          "position": ch.position,
                          "category": str(ch.category_id) if getattr(ch, "category_id", None) else None}
                         for ch in guild.channels]}
    p = f"backups/server_{guild.id}_{int(time.time())}.json"
    with open(p, "w") as f: json_dump(p, data)
    return p


def json_dump(path, data):
    import json
    with open(path, "w") as f: json.dump(data, f, indent=2)


async def _restore_server(guild, path):
    import json, discord
    with open(path) as f: data = json.load(f)
    for r in data.get("roles", []):
        if r["name"] == "@everyone": continue
        try:
            await guild.create_role(name=r["name"], color=discord.Color(r["color"]),
                                    permissions=discord.Permissions(r["perms"]),
                                    hoist=r["hoist"], mentionable=r["mentionable"])
        except Exception: pass
    for c in data.get("categories", []):
        try: await guild.create_category(c["name"])
        except Exception: pass
    for ch in data.get("channels", []):
        try:
            if "text" in ch["type"]: await guild.create_text_channel(ch["name"])
            elif "voice" in ch["type"]: await guild.create_voice_channel(ch["name"])
        except Exception: pass


class NukeCog:
    COMMANDS = {"nuke", "nukebackup"}

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass

        if cmd == "nukebackup":
            if not message.guild:
                return await message.channel.send(S.ui_err("server only"), delete_after=5)
            p = await _backup_server(message.guild)
            return await message.channel.send(S.ui_ok(f"backup saved: {p}"))

        # nuke
        if not message.guild:
            return await message.channel.send(S.ui_err("server only"), delete_after=5)
        sub = args[1].lower() if len(args) > 1 else ""
        g = message.guild

        if sub == "status":
            await message.channel.send(S.ui_info(
                f"nuke ready — ch:{len(g.channels)} r:{len(g.roles)} e:{len(g.emojis)}"))

        elif sub == "channels":
            for ch in list(g.channels):
                try: await ch.delete()
                except Exception: pass

        elif sub == "roles":
            for r in list(g.roles):
                if r.is_default(): continue
                try: await r.delete()
                except Exception: pass

        elif sub == "emojis":
            for e in list(g.emojis):
                try: await e.delete()
                except Exception: pass

        elif sub == "webhooks":
            for ch in list(g.text_channels):
                try:
                    for wh in await ch.webhooks():
                        try: await wh.delete()
                        except Exception: pass
                except Exception: pass

        elif sub == "everything":
            for ch in list(g.channels):
                try: await ch.delete()
                except Exception: pass
            for r in list(g.roles):
                if r.is_default(): continue
                try: await r.delete()
                except Exception: pass
            for e in list(g.emojis):
                try: await e.delete()
                except Exception: pass

        elif sub == "restore":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: nuke restore <file>"), delete_after=5)
            p = f"backups/{args[2]}" if not os.path.exists(args[2]) else args[2]
            if not os.path.exists(p):
                return await message.channel.send(S.ui_err("not found"), delete_after=5)
            await _restore_server(g, p)
            await message.channel.send(S.ui_ok("restored"))

        else:
            await message.channel.send(S.ui_info(
                "usage: nuke status/channels/roles/emojis/webhooks/everything/restore"))