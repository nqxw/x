# cogs/developer.py | eval, restart, reconnect, proxy, plugins, saved sessions,
#                     owner-only access management (setdev/setadmin/setowner/...)
import os
import sys
import asyncio
import importlib.util
import modifyself_shim as discord
from . import state as S


class DeveloperCog:
    COMMANDS = {
        # developer tier
        "eval", "restart", "reconnect", "proxy", "plugin", "session",
        # owner-only access management
        "setdev", "devremove", "devlist",
        "setadmin", "adminremove", "adminlist",
        "setowner", "accesslist",
    }

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        # ═════════════════════════════════════════════════════
        # OWNER-ONLY ACCESS MANAGEMENT
        # every branch below checks message.author.id == S.OWNER_ID
        # ═════════════════════════════════════════════════════

        if cmd == "setdev":
            if message.author.id != S.OWNER_ID:
                return await message.edit(content=S.ui_err("owner only"))
            if len(args) < 2 or not args[1].isdigit():
                return await message.edit(content=S.ui_err("usage: setdev <user_id>"))
            uid = int(args[1])
            S._devs.add(uid)
            S.access_save()
            return await message.edit(content=S.ui_ok(f"dev added: {uid}"))

        if cmd == "devremove":
            if message.author.id != S.OWNER_ID:
                return await message.edit(content=S.ui_err("owner only"))
            if len(args) < 2 or not args[1].isdigit():
                return await message.edit(content=S.ui_err("usage: devremove <user_id>"))
            uid = int(args[1])
            if uid not in S._devs:
                return await message.edit(content=S.ui_err("not a dev"))
            S._devs.discard(uid)
            S.access_save()
            return await message.edit(content=S.ui_ok(f"dev removed: {uid}"))

        if cmd == "devlist":
            if message.author.id != S.OWNER_ID:
                return await message.edit(content=S.ui_err("owner only"))
            rows = [f"  {S.GREY}•{S.RESET} {uid}" for uid in sorted(S._devs)]
            return await message.edit(
                content=S._paginate("devs", f"{len(S._devs)} total", rows)
                if rows else S.ui_info("no devs"))

        if cmd == "setadmin":
            if message.author.id != S.OWNER_ID:
                return await message.edit(content=S.ui_err("owner only"))
            if len(args) < 2 or not args[1].isdigit():
                return await message.edit(content=S.ui_err("usage: setadmin <user_id>"))
            uid = int(args[1])
            S._admins.add(uid)
            S.access_save()
            return await message.edit(content=S.ui_ok(f"admin added: {uid}"))

        if cmd == "adminremove":
            if message.author.id != S.OWNER_ID:
                return await message.edit(content=S.ui_err("owner only"))
            if len(args) < 2 or not args[1].isdigit():
                return await message.edit(content=S.ui_err("usage: adminremove <user_id>"))
            uid = int(args[1])
            if uid not in S._admins:
                return await message.edit(content=S.ui_err("not an admin"))
            S._admins.discard(uid)
            S.access_save()
            return await message.edit(content=S.ui_ok(f"admin removed: {uid}"))

        if cmd == "adminlist":
            if message.author.id != S.OWNER_ID:
                return await message.edit(content=S.ui_err("owner only"))
            rows = [f"  {S.GREY}•{S.RESET} {uid}" for uid in sorted(S._admins)]
            return await message.edit(
                content=S._paginate("admins", f"{len(S._admins)} total", rows)
                if rows else S.ui_info("no admins"))

        if cmd == "setowner":
            if message.author.id != S.OWNER_ID:
                return await message.edit(content=S.ui_err("owner only"))
            if len(args) < 2 or not args[1].isdigit():
                return await message.edit(content=S.ui_err("usage: setowner <user_id>"))
            new_owner = int(args[1])
            old = S.OWNER_ID
            S.OWNER_ID = new_owner
            S.access_save()
            return await message.edit(content=S.ui_ok(f"owner {old} → {new_owner}"))

        if cmd == "accesslist":
            if message.author.id != S.OWNER_ID:
                return await message.edit(content=S.ui_err("owner only"))
            lines = [
                f"  owner:  {S.OWNER_ID}",
                f"  admins: {sorted(S._admins) if S._admins else '—'}",
                f"  devs:   {sorted(S._devs) if S._devs else '—'}",
            ]
            return await message.edit(content=S._ansi_block(lines))

        # ═════════════════════════════════════════════════════
        # DEVELOPER TIER
        # dispatcher gate rejects anyone below dev before we get here
        # ═════════════════════════════════════════════════════

        if cmd == "eval":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: eval <code>"))
            code = " ".join(args[1:])
            try:
                result = eval(code, globals(),
                              {"client": client, "message": message,
                               "discord": discord, "asyncio": asyncio,
                               "S": S})
                if asyncio.iscoroutine(result):
                    result = await result
                await message.edit(content=f"```py\n{str(result)[:1900]}\n```")
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "restart":
            await message.edit(content=S.ui_warn("restarting..."))
            os.execv(sys.executable, [sys.executable] + sys.argv)

        elif cmd == "reconnect":
            try:
                gw = getattr(client, "_gateway", None)
                if gw:
                    await gw.close()
                await message.edit(content=S.ui_ok("reconnect requested"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "proxy":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "set" and len(args) >= 3:
                S._proxy = args[2]
                await message.edit(content=S.ui_ok(f"proxy → {args[2]}"))
            elif sub == "clear":
                S._proxy = None
                await message.edit(content=S.ui_ok("proxy cleared"))
            else:
                await message.edit(content=S.ui_info("usage: proxy set <url> | clear"))

        elif cmd == "plugin":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "load" and len(args) >= 3:
                ok = self._load(args[2])
                await message.edit(content=S.ui_ok(f"loaded {args[2]}")
                                   if ok else S.ui_err("failed"))
            elif sub == "unload" and len(args) >= 3:
                ok = self._unload(args[2])
                await message.edit(content=S.ui_ok("unloaded")
                                   if ok else S.ui_err("not loaded"))
            elif sub == "list":
                rows = [f"  {S.GREY}•{S.RESET} {n}" for n in S._plugins]
                await message.edit(content=S._paginate("plugins", "", rows)
                                   if rows else S.ui_info("none"))
            else:
                await message.edit(content=S.ui_info("usage: plugin load/unload/list"))

        elif cmd == "session":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "list":
                rows = [f"  {S.GREY}[{i}]{S.RESET} {s[:12]}..."
                        for i, s in enumerate(S._sessions)]
                await message.edit(content=S._paginate("sessions", "", rows)
                                   if rows else S.ui_info("no saved sessions"))
            elif sub == "switch" and len(args) >= 3 and args[2].isdigit():
                idx = int(args[2])
                if 0 <= idx < len(S._sessions):
                    S._session_idx = idx
                    await message.edit(content=S.ui_ok(
                        f"active → {S._sessions[idx][:12]}..."))
                else:
                    await message.edit(content=S.ui_err("bad index"))
            else:
                await message.edit(content=S.ui_info("usage: session list | switch <idx>"))

    # ── plugin helpers ──

    def _load(self, name):
        path = f"plugins/{name}.py"
        if not os.path.exists(path):
            return False
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        S._plugins[name] = mod
        if hasattr(mod, "setup"):
            try:
                mod.setup(S.CLIENT, globals())
            except Exception as e:
                print(f"[plugin] setup error: {e}")
        return True

    def _unload(self, name):
        if name in S._plugins:
            mod = S._plugins.pop(name)
            if hasattr(mod, "teardown"):
                try:
                    mod.teardown()
                except Exception:
                    pass
            return True
        return False
