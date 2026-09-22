# cogs/developer.py | eval, restart, reconnect, proxy, plugins, saved sessions
import os
import sys
import asyncio
import inspect
import pprint
import importlib.util
import traceback
import discord
from . import state as S


def _eval_namespace(client, message):
    """Build the namespace for eval:
    - selfbot's own state module (S) exposed as `S`
    - common libs already imported (discord, asyncio)
    - the live client and message
    - a few convenience shortcuts so `client`, `message` work without prefix
    """
    ns = {
        "client": client,
        "message": message,
        "discord": discord,
        "asyncio": asyncio,
        "S": S,
        "__builtins__": __builtins__,
    }
    # convenience: expose common S fields flat so `_aliases`, `_plugins`, etc work
    for name in dir(S):
        if name.startswith("_"):
            try:
                ns[name] = getattr(S, name)
            except Exception:
                pass
    return ns


def _format_eval_result(result):
    """Pretty-print dicts/lists, fall back to str for anything else."""
    try:
        if isinstance(result, (dict, list, tuple, set)):
            text = pprint.pformat(result, width=80)
        else:
            text = str(result)
    except Exception as e:
        text = f"<unrepresentable: {e}>"
    if len(text) > 1900:
        text = text[:1890] + "\n... (truncated)"
    return text


class DeveloperCog:
    COMMANDS = {"eval", "restart", "reconnect", "proxy", "plugin", "session"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        try:
            if cmd == "eval":
                await self._cmd_eval(message, args)
            elif cmd == "restart":
                await self._cmd_restart(message)
            elif cmd == "reconnect":
                await self._cmd_reconnect(message)
            elif cmd == "proxy":
                await self._cmd_proxy(message, args)
            elif cmd == "plugin":
                await self._cmd_plugin(message, args)
            elif cmd == "session":
                await self._cmd_session(message, args)
        except Exception as e:
            print(f"[developer:{cmd}] {e}")
            traceback.print_exc()
            try:
                await message.channel.send(S.ui_err(f"`{cmd}` errored: {e}"))
            except Exception:
                pass

    # ── eval ──

    async def _cmd_eval(self, message, args):
        if len(args) < 2:
            await message.edit(content=S.ui_err("usage: eval <code>"))
            return
        code = " ".join(args[1:])
        ns = _eval_namespace(S.CLIENT, message)
        try:
            result = eval(code, ns, ns)
            if inspect.isawaitable(result):
                result = await result
            text = _format_eval_result(result)
            await message.edit(content=f"```py\n{text}\n```")
        except Exception as e:
            tb = traceback.format_exc().splitlines()[-1]
            await message.edit(content=S.ui_err(f"{type(e).__name__}: {e}\n{tb}"))

    # ── restart ──

    async def _cmd_restart(self, message):
        await message.edit(content=S.ui_warn("restarting..."))
        await asyncio.sleep(0.5)
        # Railway / Docker path: close the client cleanly and let the
        # supervisor restart the container. the os.execv path below is a
        # fallback for bare-metal runs.
        try:
            client = S.CLIENT
            if client is not None:
                await client.close()
        except Exception:
            pass
        try:
            # bare-metal fallback
            if len(sys.argv) >= 1 and os.path.exists(sys.argv[0]):
                os.execv(sys.executable, [sys.executable, sys.argv[0]])
        except Exception:
            pass
        # final fallback: hard exit — Railway respawns on container restart
        os._exit(0)

    # ── reconnect ──

    async def _cmd_reconnect(self, message):
        try:
            client = S.CLIENT
            ws = getattr(client, "ws", None)
            if ws:
                await ws.close(code=4000)
            await message.edit(content=S.ui_ok("reconnect requested"))
        except Exception as e:
            await message.edit(content=S.ui_err(str(e)))

    # ── proxy ──

    async def _cmd_proxy(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "set" and len(args) >= 3:
            S._proxy = args[2]
            await message.edit(content=S.ui_ok(
                f"proxy → {args[2]}\nnote: takes effect on next reconnect"))
        elif sub == "clear":
            S._proxy = None
            await message.edit(content=S.ui_ok("proxy cleared"))
        else:
            await message.edit(content=S.ui_info("usage: proxy set <url> | clear"))

    # ── plugin ──

    async def _cmd_plugin(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if sub == "load" and len(args) >= 3:
            ok, err = self._load(args[2])
            if ok:
                await message.edit(content=S.ui_ok(f"loaded {args[2]}"))
            else:
                await message.edit(content=S.ui_err(f"load failed: {err}"))
        elif sub == "unload" and len(args) >= 3:
            ok, err = self._unload(args[2])
            if ok:
                await message.edit(content=S.ui_ok(f"unloaded {args[2]}"))
            else:
                await message.edit(content=S.ui_err(f"unload failed: {err}"))
        elif sub == "list":
            rows = [f"  {S.GREY}•{S.RESET} {n}" for n in S._plugins]
            if rows:
                await message.edit(content=S._paginate("plugins", "", rows))
            else:
                await message.edit(content=S.ui_info("none loaded"))
        else:
            await message.edit(content=S.ui_info("usage: plugin load/unload/list"))

    def _load(self, name):
        """Load a plugin from plugins/<name>.py.
        Supported shapes:
          1. setup(bot, ns) — called with the client and globals
          2. a class with COMMANDS + handle — auto-registered into the cog registry
          3. a module-level `PLUGIN` class attribute
        Returns (ok, error_string)."""
        name = name.replace(".py", "")
        path = f"plugins/{name}.py"
        if not os.path.exists(path):
            return False, f"no file at {path}"

        if name in S._plugins:
            return False, "already loaded"

        try:
            spec = importlib.util.spec_from_file_location(f"plugin_{name}", path)
            if spec is None or spec.loader is None:
                return False, "could not build spec"
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            tb = traceback.format_exc().splitlines()[-1]
            return False, f"{type(e).__name__}: {e} ({tb})"

        S._plugins[name] = mod
        registered = False

        # shape 1: class-based plugin with COMMANDS + handle
        cls = None
        if hasattr(mod, "PLUGIN"):
            cls = getattr(mod, "PLUGIN")
        else:
            # look for the first class defined in the module that has COMMANDS
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (isinstance(attr, type)
                        and attr.__module__ == mod.__name__
                        and hasattr(attr, "COMMANDS")):
                    cls = attr
                    break

        if cls is not None:
            try:
                inst = cls()
                for c in getattr(inst, "COMMANDS", set()):
                    _register_into_live_registry(c, inst)
                registered = True
            except Exception as e:
                return False, f"class init failed: {e}"

        # shape 2: setup(bot, ns) hook
        if hasattr(mod, "setup"):
            try:
                mod.setup(S.CLIENT, globals())
                registered = True
            except Exception as e:
                print(f"[plugin] setup error in {name}: {e}")

        if not registered:
            print(f"[plugin] {name}: module loaded but exposes no COMMANDS or setup()")

        return True, ""

    def _unload(self, name):
        name = name.replace(".py", "")
        if name not in S._plugins:
            return False, "not loaded"
        mod = S._plugins.pop(name)

        # unregister any commands the plugin registered
        for c in getattr(mod, "COMMANDS", set()):
            _unregister_from_live_registry(c)

        if hasattr(mod, "teardown"):
            try:
                mod.teardown()
            except Exception as e:
                print(f"[plugin] teardown error in {name}: {e}")
        return True, ""

    # ── session ──

    async def _cmd_session(self, message, args):
        sub = args[1].lower() if len(args) > 1 else ""
        sessions = getattr(S, "_sessions", []) or []
        if sub == "list":
            if not sessions:
                await message.edit(content=S.ui_info("no saved sessions"))
                return
            rows = []
            for i, s in enumerate(sessions):
                s_str = str(s)
                preview = s_str[:12] + "..." if len(s_str) > 12 else s_str
                marker = " *" if i == getattr(S, "_session_idx", 0) else "  "
                rows.append(f"  {S.GREY}[{i}]{S.RESET}{marker}{preview}")
            await message.edit(content=S._paginate("sessions", "", rows))
        elif sub == "switch" and len(args) >= 3 and args[2].isdigit():
            idx = int(args[2])
            if 0 <= idx < len(sessions):
                S._session_idx = idx
                await message.edit(content=S.ui_ok(
                    f"active → {str(sessions[idx])[:12]}...\n"
                    f"note: token swap is a no-op in this build"))
            else:
                await message.edit(content=S.ui_err(f"bad index — range 0-{len(sessions)-1}"))
        else:
            await message.edit(content=S.ui_info("usage: session list | switch <idx>"))


# ── live registry helpers ──
# developer.py runs as a cog with no direct handle on selfbot's _COG_REGISTRY.
# pull it from the main module if present, so loaded plugins can register
# their commands into the same dispatcher the cogs use.

def _get_live_registry():
    try:
        main = sys.modules.get("__main__")
        if main is not None:
            return getattr(main, "_COG_REGISTRY", None)
    except Exception:
        pass
    return None


def _register_into_live_registry(cmd_name, instance):
    registry = _get_live_registry()
    if registry is None:
        print(f"[plugin] no live registry — {cmd_name} not registered")
        return False
    registry[cmd_name] = (instance, cmd_name)
    print(f"[plugin] registered {cmd_name}")
    return True


def _unregister_from_live_registry(cmd_name):
    registry = _get_live_registry()
    if registry is None:
        return False
    registry.pop(cmd_name, None)
    return True
