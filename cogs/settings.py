# cogs/settings.py | prefix, aliases, cooldowns, profiles, config import/export, encrypt, enable/disable
import os
import sys
import json
import time
import modifyself_shim as discord
from . import state as S


class SettingsCog:
    COMMANDS = {"prefix", "version", "reload", "serverprefix", "serverprefixclear",
                "alias", "cooldown", "profile", "config", "encrypt",
                "enable", "disable", "disabled"}

    async def handle(self, message, cmd, args):
        if cmd == "prefix": await self._prefix(message, args)
        elif cmd == "version": await message.edit(content=S.ui_info(f"wilt v{S.VERSION}"))
        elif cmd == "reload": await message.edit(content=S.ui_ok("config reloaded"))
        elif cmd == "serverprefix": await self._serverprefix(message, args)
        elif cmd == "serverprefixclear": await self._serverprefixclear(message)
        elif cmd == "alias": await self._alias(message, args)
        elif cmd == "cooldown": await self._cooldown(message, args)
        elif cmd == "profile": await self._profile(message, args)
        elif cmd == "config": await self._config(message, args)
        elif cmd == "encrypt": await self._encrypt(message, args)
        elif cmd == "enable":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: enable <cmd>"))
            S._cmd_disabled.discard(args[1].lower())
            await message.edit(content=S.ui_ok(f"enabled `{args[1]}`"))
        elif cmd == "disable":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: disable <cmd>"))
            S._cmd_disabled.add(args[1].lower())
            await message.edit(content=S.ui_ok(f"disabled `{args[1]}`"))
        elif cmd == "disabled":
            rows = [f"  {S.GREY}•{S.RESET} {c}" for c in sorted(S._cmd_disabled)]
            await message.edit(content=S._paginate("disabled commands", "", rows)
                                        if rows else S.ui_info("none"))

    async def _prefix(self, message, args):
        if len(args) < 2:
            return await message.edit(content=S.ui_info(f"current prefix: {S.PREFIX}"))
        new_prefix = args[1]
        S.PREFIX = new_prefix
        try:
            cfg = S.load_config() or {}
            cfg["prefix"] = new_prefix
            S.save_config(cfg)
        except Exception as e:
            print(f"[settings] prefix save failed: {e}")
        try:
            main = sys.modules.get("__main__")
            if main is not None and hasattr(main, "PREFIX"):
                main.PREFIX = new_prefix
        except Exception:
            pass
        await message.edit(content=S.ui_ok(f"prefix changed to `{new_prefix}`"))

    async def _serverprefix(self, message, args):
        try: await message.delete()
        except Exception: pass
        if not message.guild:
            return await message.channel.send(S.ui_err("server only"), delete_after=5)
        if len(args) < 2:
            cur = S._server_prefixes.get(str(message.guild.id), S.PREFIX)
            return await message.channel.send(S.ui_info(f"server prefix: `{cur}`"), delete_after=6)
        S._server_prefixes[str(message.guild.id)] = args[1]
        try:
            cfg = S.load_config() or {}
            cfg.setdefault("server_prefixes", {})[str(message.guild.id)] = args[1]
            S.save_config(cfg)
        except Exception as e:
            print(f"[settings] serverprefix save failed: {e}")
        await message.channel.send(S.ui_ok(f"server prefix → `{args[1]}`"))

    async def _serverprefixclear(self, message):
        try: await message.delete()
        except Exception: pass
        if not message.guild:
            return await message.channel.send(S.ui_err("server only"), delete_after=5)
        S._server_prefixes.pop(str(message.guild.id), None)
        try:
            cfg = S.load_config() or {}
            if "server_prefixes" in cfg:
                cfg["server_prefixes"].pop(str(message.guild.id), None)
            S.save_config(cfg)
        except Exception as e:
            print(f"[settings] serverprefixclear save failed: {e}")
        await message.channel.send(S.ui_ok("cleared"))

    async def _alias(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "add" and len(args) >= 4:
            S._aliases[args[3].lower()] = args[2].lower()
            await message.edit(content=S.ui_ok(f"alias `{args[3]}` → `{args[2]}`"))
        elif sub == "remove" and len(args) >= 3:
            S._aliases.pop(args[2].lower(), None)
            await message.edit(content=S.ui_ok("removed"))
        elif sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} {k} → {v}" for k, v in S._aliases.items()]
            await message.edit(content=S._paginate("aliases", "", rows)
                                        if rows else S.ui_info("no aliases"))
        else:
            await message.edit(content=S.ui_info("usage: alias add/remove/list"))

    async def _cooldown(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "set" and len(args) >= 4 and args[3].isdigit():
            S._cooldowns[args[2].lower()] = int(args[3])
            await message.edit(content=S.ui_ok("set"))
        elif sub == "clear" and len(args) >= 3:
            S._cooldowns.pop(args[2].lower(), None)
            await message.edit(content=S.ui_ok("cleared"))
        elif sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} {k}: {v}s" for k, v in S._cooldowns.items()]
            await message.edit(content=S._paginate("cooldowns", "", rows)
                                        if rows else S.ui_info("none"))
        else:
            await message.edit(content=S.ui_info("usage: cooldown set/clear/list"))

    async def _profile(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "save" and len(args) >= 3:
            os.makedirs("database/profiles", exist_ok=True)
            with open(f"database/profiles/{args[2]}.json", "w") as f:
                json.dump(S.load_config() or {}, f, indent=2)
            await message.edit(content=S.ui_ok("saved"))
        elif sub == "load" and len(args) >= 3:
            p = f"database/profiles/{args[2]}.json"
            if os.path.exists(p):
                with open(p) as f: S.save_config(json.load(f))
                await message.edit(content=S.ui_ok("loaded (restart to fully apply)"))
            else:
                await message.edit(content=S.ui_err("not found"))
        elif sub == "list":
            if not os.path.isdir("database/profiles"):
                return await message.edit(content=S.ui_info("no profiles"))
            rows = [f"  {S.GREY}•{S.RESET} {n[:-5]}"
                    for n in os.listdir("database/profiles") if n.endswith(".json")]
            await message.edit(content=S._paginate("profiles", "", rows)
                                        if rows else S.ui_info("none"))
        elif sub == "delete" and len(args) >= 3:
            p = f"database/profiles/{args[2]}.json"
            if os.path.exists(p):
                os.remove(p)
                await message.edit(content=S.ui_ok("deleted"))
            else:
                await message.edit(content=S.ui_err("not found"))
        else:
            await message.edit(content=S.ui_info("usage: profile save/load/list/delete"))

    async def _config(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "export":
            p = f"exports/config_{int(time.time())}.json"
            with open(p, "w") as f: json.dump(S.load_config() or {}, f, indent=2)
            await message.edit(content=S.ui_ok(f"→ {p}"))
        elif sub == "import" and len(args) >= 3 and os.path.exists(args[2]):
            with open(args[2]) as f: S.save_config(json.load(f))
            await message.edit(content=S.ui_ok("imported"))
        else:
            await message.edit(content=S.ui_info("usage: config export | import <path>"))

    async def _encrypt(self, message, args):
        if not S._has_crypto:
            return await message.edit(content=S.ui_err("cryptography not installed"))
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "on":
            try:
                S.encrypt_file("config.json")
                await message.edit(content=S.ui_ok("encrypted"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))
        elif sub == "off":
            try:
                S.decrypt_file("config.json")
                await message.edit(content=S.ui_ok("decrypted"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))
        else:
            await message.edit(content=S.ui_info("usage: encrypt on/off"))
