# cogs/developer.py | eval, restart, reconnect, proxy, plugins, saved sessions
import os
import sys
import asyncio
import importlib.util
import modifyself_shim as discord
from . import state as S


class DeveloperCog:
    COMMANDS = {"eval", "restart", "reconnect", "proxy", "plugin", "session"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "eval":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: eval <code>"))
            code = " ".join(args[1:])
            try:
                result = eval(code, globals(),
                              {"client": client, "message": message,
                               "discord": discord, "asyncio": asyncio})
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

    def _load(self, name):
        path = f"plugins/{name}.py"
        if not os.path.exists(path):
            return False
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        S._plugins[name] = mod
        if hasattr(mod, "setup"):
            try: mod.setup(S.CLIENT, globals())
            except Exception as e: print(f"[plugin] setup error: {e}")
        return True

    def _unload(self, name):
        if name in S._plugins:
            mod = S._plugins.pop(name)
            if hasattr(mod, "teardown"):
                try: mod.teardown()
                except Exception: pass
            return True
        return False