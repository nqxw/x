# cogs/auto.py | giveaway, nitrosniper, autoreact, multireact, vsniper, superreact
#
# v6 — superreact targets your own outbound messages (same as autoreact).
# no count arg — continuous toggle like autoreact.
import asyncio
import sys
import urllib.parse
import aiohttp
import modifyself_shim as discord
from . import state as S


# ─────────────────────────────────────────────────────────────
# STATE SYNC
# ─────────────────────────────────────────────────────────────

def _sync(**kw):
    main = sys.modules.get("__main__")
    if main is None:
        return
    for k, v in kw.items():
        try:
            setattr(main, k, v)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# RAW REACTION HELPERS
# ─────────────────────────────────────────────────────────────

def _emoji_to_str(emoji):
    if isinstance(emoji, str):
        return emoji
    name = getattr(emoji, "name", None)
    eid = getattr(emoji, "id", None)
    animated = getattr(emoji, "animated", False)
    if name and eid:
        prefix = "a" if animated else ""
        return f"<{prefix}:{name}:{eid}>"
    if name:
        return str(name)
    return str(emoji)


async def _react_once(session, channel_id, message_id, emoji):
    """Single PUT. Returns (status:int_or_str, retry_after:float_or_None)."""
    emoji_str = _emoji_to_str(emoji)
    emoji_enc = urllib.parse.quote(emoji_str, safe="")
    url = (f"https://discord.com/api/v9/channels/{channel_id}"
           f"/messages/{message_id}/reactions/{emoji_enc}/@me")
    headers = {
        "Authorization": S.TOKEN,
        "User-Agent": S.USER_AGENT,
        "Content-Length": "0",
    }
    try:
        async with session.put(url, headers=headers) as r:
            if r.status == 429:
                ra = 1.0
                try:
                    body = await r.json()
                    ra = float(body.get("retry_after", 1.0))
                except Exception:
                    pass
                return 429, ra
            return r.status, None
    except Exception as e:
        return f"err:{e}", None


async def _react(channel_id, message_id, emoji, session=None):
    """Fire-and-forget single reaction with built-in 429 retry."""
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        for _ in range(4):
            status, retry_after = await _react_once(
                session, channel_id, message_id, emoji)
            if status == 429:
                await asyncio.sleep(max(retry_after or 1.0, 0.5))
                continue
            return status
        return 429
    finally:
        if own_session:
            await session.close()


# ─────────────────────────────────────────────────────────────
# CLASS-LEVEL PATCH — shim add_reaction → raw REST fallback.
# covers autoreact + superreact in the dispatcher, plus any other
# cog that reacts to a message.
# ─────────────────────────────────────────────────────────────

def _install_react_patch():
    try:
        from modifyself.models.message import Message as _MSMessage
    except ImportError as e:
        print(f"[auto] cannot import Message for react patch: {e}")
        return False

    if getattr(_MSMessage, "_raw_react_patched", False):
        return True

    original = _MSMessage.add_reaction

    async def patched_add_reaction(self, emoji, *args, **kwargs):
        try:
            return await original(self, emoji, *args, **kwargs)
        except Exception as shim_err:
            ch_id = getattr(self, "channel_id", None)
            if ch_id is None:
                ch = getattr(self, "channel", None)
                ch_id = getattr(ch, "id", None) if ch is not None else None
            msg_id = getattr(self, "id", None)
            if ch_id is None or msg_id is None:
                raise shim_err
            status = await _react(ch_id, msg_id, emoji)
            if isinstance(status, int) and status in (200, 204):
                return None
            raise shim_err

    _MSMessage.add_reaction = patched_add_reaction
    _MSMessage._raw_react_patched = True
    print("[auto] Message.add_reaction patched (shim → raw REST fallback)")
    return True


# ─────────────────────────────────────────────────────────────
# VSNIPER LOOP
# ─────────────────────────────────────────────────────────────

async def _vsniper_loop():
    while True:
        for entry in list(S._vsniper_list):
            code = entry["code"]
            guild_id = entry["guild_id"]
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "Content-Type": "application/json",
                         "User-Agent": S.USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/invites/{code}", headers=h) as r:
                        if r.status == 404:
                            async with s.patch(
                                f"https://discord.com/api/v9/guilds/{guild_id}/vanity-url",
                                headers=h, json={"code": code}
                            ) as r2:
                                if r2.status in (200, 204) and S.log_msg:
                                    S.log_msg("VSNIPER", f"CLAIMED {code} for guild {guild_id}")
            except Exception:
                pass
        await asyncio.sleep(0.5)


class AutoCog:
    COMMANDS = {"giveaway", "nitrosniper", "autoreact", "autoreactstop",
                "multireact", "multiautoreact", "vsniper",
                "superreact", "superreactstop", "reactdiag"}

    def __init__(self):
        _install_react_patch()

    async def handle(self, message, cmd, args):
        if cmd == "giveaway":
            S._giveaway_enabled = len(args) < 2 or args[1].lower() in ("on", "enable")
            _sync(_giveaway_enabled=S._giveaway_enabled)
            await message.edit(content=S.ui_ok(
                f"giveaway → {'on' if S._giveaway_enabled else 'off'}"))

        elif cmd == "nitrosniper":
            S._nitrosniper_enabled = len(args) < 2 or args[1].lower() in ("on", "enable")
            _sync(_nitrosniper_enabled=S._nitrosniper_enabled)
            await message.edit(content=S.ui_ok(
                f"nitrosniper → {'on' if S._nitrosniper_enabled else 'off'}"))

        elif cmd == "autoreact":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: autoreact <emoji>"))
            S._autoreact_emoji = args[1]
            _sync(_autoreact_emoji=S._autoreact_emoji)
            await message.edit(content=S.ui_ok(f"reacting with {S._autoreact_emoji}"))

        elif cmd == "autoreactstop":
            S._autoreact_emoji = None
            _sync(_autoreact_emoji=None)
            await message.edit(content=S.ui_ok("stopped"))

        # ── SUPERREACT — continuous, reacts to YOUR OWN messages ──
        elif cmd == "superreact":
            if len(args) < 2:
                return await message.edit(
                    content=S.ui_err("usage: superreact <emoji>  |  superreact stop"))
            sub = args[1].lower()
            if sub in ("stop", "off", "disable"):
                S._superreact_emoji = None
                _sync(_superreact_emoji=None)
                return await message.edit(
                    content=S.ui_ok("superreact → off"))

            S._superreact_emoji = args[1]
            _sync(_superreact_emoji=S._superreact_emoji)
            await message.edit(content=S.ui_ok(
                f"superreact → {S._superreact_emoji}  (reacting to your msgs)"))

        elif cmd == "superreactstop":
            S._superreact_emoji = None
            _sync(_superreact_emoji=None)
            await message.edit(content=S.ui_ok("superreact → off"))

        elif cmd == "reactdiag":
            try:
                from modifyself.models.message import Message as _MSMessage
                patched = bool(getattr(_MSMessage, "_raw_react_patched", False))
            except ImportError:
                patched = "import-failed"
            main = sys.modules.get("__main__")
            main_ar = getattr(main, "_autoreact_emoji", None) if main else None
            main_sr = getattr(main, "_superreact_emoji", None) if main else None
            main_multi = getattr(main, "_multireact_enabled", None) if main else None
            lines = [
                f"  patch installed:     {patched}",
                f"  S._autoreact_emoji:  {S._autoreact_emoji!r}",
                f"  main._autoreact:     {main_ar!r}",
                f"  S._superreact_emoji: {S._superreact_emoji!r}",
                f"  main._superreact:    {main_sr!r}",
                f"  S._multireact_en:    {S._multireact_enabled}",
                f"  main._multireact:    {main_multi}",
                f"  pool (S):            {S._multireact_pool}",
                f"  __main__ present:    {main is not None}",
            ]
            await message.edit(content=S._ansi_block(lines))

        elif cmd in ("multireact", "multiautoreact"):
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "add" and len(args) >= 3:
                if args[2] in S._multireact_pool:
                    return await message.edit(content=S.ui_info("already in pool"))
                S._multireact_pool.append(args[2])
                await message.edit(content=S.ui_ok(f"added ({len(S._multireact_pool)})"))
            elif sub in ("remove", "rem", "del") and len(args) >= 3:
                if args[2] not in S._multireact_pool:
                    return await message.edit(content=S.ui_err("not in pool"))
                S._multireact_pool.remove(args[2])
                await message.edit(content=S.ui_ok(f"removed ({len(S._multireact_pool)})"))
            elif sub == "list":
                rows = [f"  {S.GREY}{i:2}.{S.RESET}  {e}"
                        for i, e in enumerate(S._multireact_pool, 1)]
                await message.edit(content=S.ui_box(
                    f"multi pool — {'ON' if S._multireact_enabled else 'OFF'}", rows)
                    if rows else S.ui_info("empty"))
            elif sub in ("on", "enable"):
                if not S._multireact_pool:
                    return await message.edit(content=S.ui_err("pool is empty"))
                S._multireact_enabled = True
                _sync(_multireact_enabled=True)
                await message.edit(content=S.ui_ok("enabled"))
            elif sub in ("off", "disable"):
                S._multireact_enabled = False
                _sync(_multireact_enabled=False)
                await message.edit(content=S.ui_ok("disabled"))
            elif sub == "clear":
                S._multireact_pool.clear()
                S._multireact_enabled = False
                _sync(_multireact_enabled=False)
                await message.edit(content=S.ui_ok("cleared"))
            else:
                await message.edit(content=S.ui_info(
                    "usage: multireact add/remove/list/on/off/clear"))

        elif cmd == "vsniper":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "add" and len(args) >= 4:
                S._vsniper_list.append({"code": args[2], "guild_id": args[3]})
                await message.edit(content=S.ui_ok(f"watching {args[2]}"))
            elif sub == "start":
                if S._vsniper_task and not S._vsniper_task.done():
                    return await message.edit(content=S.ui_info("already running"))
                S._vsniper_task = asyncio.create_task(_vsniper_loop())
                _sync(_vsniper_task=S._vsniper_task)
                await message.edit(content=S.ui_ok("started"))
            elif sub == "stop":
                if S._vsniper_task:
                    S._vsniper_task.cancel()
                    S._vsniper_task = None
                    _sync(_vsniper_task=None)
                await message.edit(content=S.ui_ok("stopped"))
            elif sub == "list":
                rows = [f"  {S.GREY}•{S.RESET} {e['code']}  "
                        f"{S.DIM}guild {e['guild_id']}{S.RESET}"
                        for e in S._vsniper_list]
                await message.edit(content=S._paginate("vsniper", "watch list", rows)
                                            if rows else S.ui_info("empty"))
            else:
                await message.edit(content=S.ui_info("usage: vsniper add/start/stop/list"))
