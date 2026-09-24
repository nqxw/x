# cogs/backup.py | server backup, restore, list, sync, autosave flag, config backup
import os
import json
import time
import modifyself_shim as discord
from . import state as S
from .nuke import _backup_server, _restore_server


async def _sync_servers(src, dst):
    p = await _backup_server(src)
    await _restore_server(dst, p)


class BackupCog:
    COMMANDS = {"backup"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""

        if sub == "server" and message.guild:
            p = await _backup_server(message.guild)
            await message.channel.send(S.ui_ok(f"backed up: {p}"))

        elif sub == "restore" and len(args) >= 3 and message.guild:
            p = f"backups/{args[2]}" if not os.path.exists(args[2]) else args[2]
            if not os.path.exists(p):
                return await message.channel.send(S.ui_err("backup not found"), delete_after=5)
            await _restore_server(message.guild, p)
            await message.channel.send(S.ui_ok("restored"))

        elif sub == "list":
            files = sorted(os.listdir("backups")) if os.path.isdir("backups") else []
            rows = [f"  {S.GREY}•{S.RESET} {f}" for f in files]
            await message.channel.send(
                S._paginate("backups", "", rows) if rows else S.ui_info("no backups"))

        elif sub == "sync" and len(args) >= 4:
            src = client.get_guild(int(args[2])); dst = client.get_guild(int(args[3]))
            if not src or not dst:
                return await message.channel.send(S.ui_err("guild not found"), delete_after=5)
            await _sync_servers(src, dst)
            await message.channel.send(S.ui_ok("synced"))

        elif sub == "autosave":
            cfg = S.load_config() or {}
            cfg["autosave"] = (args[2].lower() in ("on","enable")) if len(args) > 2 else not cfg.get("autosave", False)
            S.save_config(cfg)
            await message.channel.send(S.ui_ok(f"autosave → {cfg['autosave']}"))

        elif sub == "config":
            p = f"backups/config_{int(time.time())}.json"
            with open(p, "w") as f: json.dump(S.load_config() or {}, f, indent=2)
            await message.channel.send(S.ui_ok(f"config backed up → {p}"))

        else:
            await message.channel.send(S.ui_info(
                "usage: backup server/restore/list/sync/autosave/config"))