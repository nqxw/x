# cogs/auto.py | giveaway, nitrosniper, autoreact, multireact, vsniper, superreact
#
# v3 fixes:
#   - class-level patch on Message.add_reaction so every reaction call —
#     dispatcher autoreact, multireact, massreact — falls back to raw REST
#     when the shim's internal HTTPClient path explodes (the same route bug
#     that killed superreact)
#   - superreact uses raw REST for enumeration instead of channel.history()
#   - _sync re-resolves __main__ every call (survives module reloads)
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
# the shim's add_reaction drops the positional route arg in
# HTTPClient.request for any message that isn't in its live cache.
# hit the endpoint directly.
# PUT /channels/{ch}/messages/{msg}/reactions/{emoji}/@me
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


async def _react(channel_id, message_id, emoji, session=None):
    emoji_str = _emoji_to_str(emoji)
    emoji_enc = urllib.parse.quote(emoji_str, safe="")
    url = (f"https://discord.com/api/v9/channels/{channel_id}"
           f"/messages/{message_id}/reactions/{emoji_enc}/@me")
    headers = {
        "Authorization": S.TOKEN,
        "User-Agent": S.USER_AGENT,
        "Content-Length": "0",
    }
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    try:
        async with session.put(url, headers=headers) as r:
            return r.status
    except Exception as e:
        return f"err:{e}"
    finally:
        if own_session:
            await session.close()


async def _fetch_recent(channel_id, limit, session, skip_id=None):
    """Raw REST message ID fetch — bypasses channel.history()."""
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
    params = {"limit": min(limit + 5, 100)}
    h = {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}
    async with session.get(url, headers=h, params=params) as r:
        if r.status != 200:
            body = await r.text()
            raise RuntimeError(f"history HTTP {r.status}: {body[:120]}")
        data = await r.json()
    ids = []
    for m in data:
        if skip_id is not None and str(m.get("id")) == str(skip_id):
            continue
        ids.append(m["id"])
        if len(ids) >= limit:
            break
    return ids


# ─────────────────────────────────────────────────────────────
# CLASS-LEVEL PATCH
# wrap Message.add_reaction so any shim failure falls back to raw REST.
# covers autoreact in the dispatcher, multireact, massreact, and any
# other cog that touches a message reaction.
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
        # try the shim first
        try:
            return await original(self, emoji, *args, **kwargs)
        except Exception as shim_err:
            # resolve channel + message ids
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
            # REST also failed — surface the original error
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
                "multireact", "multiautoreact", "vsniper", "superreact",
                "reactdiag"}

    def __init__(self):
        # install the shim → raw REST fallback once, class-level
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

        elif cmd == "reactdiag":
            try:
                from modifyself.models.message import Message as _MSMessage
                patched = bool(getattr(_MSMessage, "_raw_react_patched", False))
            except ImportError:
                patched = "import-failed"
            main = sys.modules.get("__main__")
            main_emoji = getattr(main, "_autoreact_emoji", None) if main else None
            main_multi = getattr(main, "_multireact_enabled", None) if main else None
            main_pool = getattr(main, "_multireact_pool", None) if main else None
            lines = [
                f"  patch installed:     {patched}",
                f"  S._autoreact_emoji:  {S._autoreact_emoji!r}",
                f"  main._autoreact:     {main_emoji!r}",
                f"  S._multireact_en:    {S._multireact_enabled}",
                f"  main._multireact:    {main_multi}",
                f"  pool (S):            {S._multireact_pool}",
                f"  pool (main):         {main_pool}",
                f"  __main__ present:    {main is not None}",
            ]
            await message.edit(content=S._ansi_block(lines))

        elif cmd == "superreact":
            # superreact <emoji> [count] — react to the last N messages
            if len(args) < 2:
                return await message.edit(
                    content=S.ui_err("usage: superreact <emoji> [count]"))
            emoji = args[1]
            count = int(args[2]) if len(args) > 2 and args[2].isdigit() else 10
            count = max(1, min(count, 50))

            ch_id = message.channel.id
            own_id = message.id

            try:
                async with aiohttp.ClientSession() as session:
                    try:
                        ids = await _fetch_recent(ch_id, count, session, skip_id=own_id)
                    except Exception as e:
                        return await message.edit(
                            content=S.ui_err(f"superreact fetch: {e}"))

                    if not ids:
                        return await message.edit(
                            content=S.ui_info("nothing to react to"))

                    results = await asyncio.gather(*(
                        _react(ch_id, mid, emoji, session=session) for mid in ids
                    ), return_exceptions=True)

                ok = sum(1 for r in results if r in (200, 204))
                fails = len(ids) - ok

                if ok == 0:
                    sample = next((r for r in results if isinstance(r, (int, str))), "?")
                    return await message.edit(content=S.ui_err(
                        f"superreact: all {len(ids)} failed (last={sample})"))

                msg = f"superreact → {emoji} × {ok}/{len(ids)} msgs"
                if fails:
                    msg += f"  ({fails} failed)"
                await message.edit(content=S.ui_ok(msg))
            except Exception as e:
                await message.edit(content=S.ui_err(f"superreact: {e}"))

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
