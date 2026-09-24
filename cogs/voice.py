# cogs/voice.py | vc join/leave/move/self-controls + auto-reconnect watchdog
import asyncio
import json
import modifyself_shim as discord
from . import state as S


_vc_auto_state: dict = {}
_VC_RECONNECT_DELAY = 5
_VC_RECONNECT_MAX_TRIES = 12

_self_vc = {
    "guild_id":   None,
    "channel_id": None,
    "self_mute":  False,
    "self_deaf":  False,
    "self_video": False,
    "self_stream": False,
}


# ─────────────────────────────────────────────────────────────
# GUILD RESOLUTION
# ─────────────────────────────────────────────────────────────

def _resolve_guild_id(message, client):
    try:
        g = message.guild
        if g is not None:
            return int(g.id)
    except Exception:
        pass
    gid = getattr(message, "guild_id", None)
    if gid:
        try:
            return int(gid)
        except (TypeError, ValueError):
            pass
    ch = getattr(message, "channel", None)
    if ch is not None:
        cg = getattr(ch, "guild_id", None)
        if cg:
            try:
                return int(cg)
            except (TypeError, ValueError):
                pass
    try:
        ch_id = int(message.channel_id)
        for g in (getattr(client._state, "_guilds", None) or {}).values():
            try:
                if g.get_channel(ch_id) is not None:
                    return int(g.id)
            except Exception:
                continue
    except Exception:
        pass
    return None


def _guild_id_for_channel(client, channel_id):
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return None
    for g in (getattr(client._state, "_guilds", None) or {}).values():
        try:
            if g.get_channel(cid) is not None:
                return int(g.id)
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────
# RAW OP:4 SEND — modifyself's send_voice_state wrapper drops the
# frame on some builds (silent return, nothing on the wire).
# send the raw op:4 payload through send_json. string ids, all fields.
# ─────────────────────────────────────────────────────────────

async def _send_vc(client, guild_id, channel_id,
                   self_mute=False, self_deaf=False,
                   self_video=False, self_stream=False):
    gw = getattr(client, "_gateway", None)
    if gw is None:
        raise RuntimeError("gateway not attached")

    try:
        is_conn = bool(getattr(gw, "is_connected", True))
        is_closed = bool(getattr(gw, "is_closed", False))
        print(f"[voice] gw state: connected={is_conn} closed={is_closed}")
        if is_closed:
            raise RuntimeError("gateway is closed")
    except AttributeError:
        pass

    if guild_id is None:
        raise RuntimeError("guild id required")

    payload = {
        "op": 4,
        "d": {
            "guild_id": str(int(guild_id)),
            "channel_id": str(int(channel_id)) if channel_id is not None else None,
            "self_mute": bool(self_mute),
            "self_deaf": bool(self_deaf),
            "self_video": bool(self_video),
            "self_stream": bool(self_stream),
        },
    }

    send_json = getattr(gw, "send_json", None)
    if send_json is None:
        raise RuntimeError("gateway has no send_json method")

    print(f"[voice] op:4 → {json.dumps(payload['d'])}")
    try:
        result = await send_json(payload)
        print(f"[voice] op:4 returned: {result!r}")
        return result
    except Exception as e:
        print(f"[voice] op:4 raised: {type(e).__name__}: {e}")
        raise


class VoiceCog:
    COMMANDS = {"vcjoin", "vcleave", "vcmute", "vcunmute", "vcdeafen", "vcundeafen",
                "vckick", "vcmove", "vcmoveall",
                "selfmute", "selfdeaf", "selfstream", "selfcamera",
                "vcreconnect", "vcdiag"}

    def register(self, client):
        cog = self

        @client.event
        async def on_voice_state_update(member, before, after):
            await cog._on_voice_state(member, before, after)

    async def _on_voice_state(self, member, before, after):
        client = S.CLIENT
        if client is None:
            return
        try:
            uid = getattr(member, "id", None) or getattr(member, "user_id", None)
            if uid is None or int(uid) != int(client.user.id):
                return
        except Exception:
            return

        gid = getattr(member, "guild_id", None)
        if gid is None:
            return
        gid = int(gid)

        after_ch = getattr(after, "channel_id", None)
        print(f"[voice] on_voice_state_update guild={gid} after_ch={after_ch}")
        if after_ch is not None:
            _self_vc["guild_id"] = gid
            _self_vc["channel_id"] = int(after_ch)
            for k in ("self_mute", "self_deaf", "self_video", "self_stream"):
                v = getattr(after, k, None)
                if v is not None:
                    _self_vc[k] = bool(v)
        else:
            _self_vc["channel_id"] = None

        state = _vc_auto_state.get(gid)
        if not state or not state.get("enabled"):
            return

        if after_ch is not None and int(after_ch) == int(state["channel_id"]):
            task = state.get("task")
            if task and not task.done():
                task.cancel()
            state["task"] = None
            return

        task = state.get("task")
        if task and not task.done():
            task.cancel()
        state["task"] = asyncio.create_task(self._reconnect_loop(gid))

    async def _reconnect_loop(self, guild_id):
        client = S.CLIENT
        state = _vc_auto_state.get(guild_id)
        if not state:
            return
        target_id = state["channel_id"]
        tries = 0
        while tries < _VC_RECONNECT_MAX_TRIES:
            await asyncio.sleep(_VC_RECONNECT_DELAY)
            state = _vc_auto_state.get(guild_id)
            if not state or not state.get("enabled"):
                return
            try:
                await _send_vc(client, guild_id, target_id)
                _self_vc.update({
                    "guild_id":   guild_id,
                    "channel_id": target_id,
                    "self_mute":  False,
                    "self_deaf":  False,
                })
                print(f"[vc-reconnect] rejoined {target_id}")
                return
            except Exception as e:
                tries += 1
                print(f"[vc-reconnect] attempt {tries}/{_VC_RECONNECT_MAX_TRIES}: {e}")
        print(f"[vc-reconnect] gave up on {guild_id}")

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        if client is None:
            return
        try:
            await message.delete()
        except Exception:
            pass

        if cmd == "vcdiag":
            gid = _resolve_guild_id(message, client)
            lines = [
                f"  message.guild:     {getattr(message, 'guild', None)}",
                f"  message.guild_id:  {getattr(message, 'guild_id', None)}",
                f"  message.channel:   {getattr(message, 'channel', None)}",
                f"  ch.guild_id:       {getattr(getattr(message, 'channel', None), 'guild_id', None)}",
                f"  resolved gid:      {gid}",
                f"  cached guilds:     {list((getattr(client._state, '_guilds', None) or {}).keys())}",
                f"  gateway:           {getattr(client, '_gateway', None)}",
                f"  gw.send_json:      {hasattr(getattr(client, '_gateway', None), 'send_json')}",
                f"  self_vc:           {_self_vc}",
            ]
            await message.channel.send(S._ansi_block(lines), delete_after=20)
            return

        if cmd == "vcjoin":
            if len(args) < 2:
                return await message.channel.send(
                    S.ui_err("usage: vcjoin <ch_id>"), delete_after=5)
            try:
                ch_id = int(args[1])
            except ValueError:
                return await message.channel.send(
                    S.ui_err("ch_id must be numeric"), delete_after=5)

            gid = _resolve_guild_id(message, client)
            if gid is None:
                gid = _guild_id_for_channel(client, ch_id)
            if gid is None:
                return await message.channel.send(
                    S.ui_err("can't resolve guild — run $vcdiag"), delete_after=6)

            print(f"[voice] vcjoin guild={gid} channel={ch_id}")
            try:
                await _send_vc(client, gid, ch_id, self_mute=False, self_deaf=False)
                _self_vc.update({
                    "guild_id":   gid,
                    "channel_id": ch_id,
                    "self_mute":  False,
                    "self_deaf":  False,
                })
                await message.channel.send(S.ui_ok("joined"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=6)

        elif cmd == "vcleave":
            gid = _resolve_guild_id(message, client) or _self_vc.get("guild_id")
            if gid is None:
                return await message.channel.send(
                    S.ui_err("can't resolve guild"), delete_after=5)
            try:
                await _send_vc(client, gid, None)
                _self_vc["channel_id"] = None
                await message.channel.send(S.ui_ok("left vc"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=6)

        elif cmd in ("vcmute", "vcunmute", "vcdeafen", "vcundeafen", "vckick"):
            if len(args) < 2:
                return await message.channel.send(
                    S.ui_err(f"usage: {cmd} <user_id>"), delete_after=5)
            gid = _resolve_guild_id(message, client)
            if gid is None:
                return await message.channel.send(
                    S.ui_err("server only"), delete_after=5)
            try:
                uid = int(args[1])
                if cmd == "vcmute":       payload = {"mute": True}
                elif cmd == "vcunmute":   payload = {"mute": False}
                elif cmd == "vcdeafen":   payload = {"deaf": True}
                elif cmd == "vcundeafen": payload = {"deaf": False}
                else:                     payload = {"channel_id": None}
                await client._http.request(
                    method="PATCH",
                    url=f"/guilds/{gid}/members/{uid}",
                    json=payload)
                await message.channel.send(S.ui_ok(f"{cmd} → {uid}"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=6)

        elif cmd == "vcmove":
            if len(args) < 3:
                return await message.channel.send(
                    S.ui_err("usage: vcmove <user_id> <ch_id>"), delete_after=5)
            gid = _resolve_guild_id(message, client)
            if gid is None:
                return await message.channel.send(
                    S.ui_err("server only"), delete_after=5)
            try:
                await client._http.request(
                    method="PATCH",
                    url=f"/guilds/{gid}/members/{int(args[1])}",
                    json={"channel_id": str(int(args[2]))})
                await message.channel.send(S.ui_ok("moved"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=6)

        elif cmd == "vcmoveall":
            await message.channel.send(
                S.ui_info("vcmoveall needs voice member cache — not available on modifyself yet"),
                delete_after=8)

        elif cmd in ("selfmute", "selfdeaf", "selfstream", "selfcamera"):
            gid = _resolve_guild_id(message, client) or _self_vc.get("guild_id")
            ch_id = _self_vc.get("channel_id")
            if gid is None or ch_id is None:
                return await message.channel.send(
                    S.ui_err("must be in a voice channel first"), delete_after=6)

            key_map = {
                "selfmute":   "self_mute",
                "selfdeaf":   "self_deaf",
                "selfstream": "self_stream",
                "selfcamera": "self_video",
            }
            key = key_map[cmd]
            new_val = not _self_vc.get(key, False)
            try:
                await _send_vc(client, gid, ch_id, **{key: new_val})
                _self_vc[key] = new_val
                await message.channel.send(
                    S.ui_ok(f"{cmd} → {'on' if new_val else 'off'}"),
                    delete_after=5)
            except Exception as e:
                await message.channel.send(
                    S.ui_err(f"{cmd} failed: {e}"), delete_after=6)

        elif cmd == "vcreconnect":
            gid = _resolve_guild_id(message, client) or _self_vc.get("guild_id")
            sub = args[1].lower() if len(args) > 1 else ""

            if sub in ("on", "enable"):
                if gid is None:
                    return await message.channel.send(
                        S.ui_err("server only (run from inside the server)"),
                        delete_after=6)
                target_id = None
                if len(args) > 2 and args[2].isdigit():
                    target_id = int(args[2])
                elif _self_vc.get("channel_id"):
                    target_id = _self_vc["channel_id"]
                if not target_id:
                    return await message.channel.send(
                        S.ui_err("usage: vcreconnect on <ch_id>"), delete_after=6)
                _vc_auto_state[gid] = {
                    "channel_id": target_id,
                    "enabled":    True,
                    "task":       None,
                }
                await message.channel.send(
                    S.ui_ok(f"auto-reconnect ON → ch {target_id}"), delete_after=6)

            elif sub in ("off", "disable"):
                if gid is None:
                    return await message.channel.send(
                        S.ui_err("can't resolve guild"), delete_after=5)
                st = _vc_auto_state.pop(gid, None)
                if st and st.get("task") and not st["task"].done():
                    st["task"].cancel()
                await message.channel.send(
                    S.ui_ok("auto-reconnect OFF"), delete_after=5)

            elif sub == "status" or not sub:
                if gid is None:
                    if not _vc_auto_state:
                        return await message.channel.send(
                            S.ui_info("auto-reconnect is OFF"), delete_after=5)
                    rows = [f"  guild {k} → ch {v['channel_id']} "
                            f"({'ON' if v.get('enabled') else 'OFF'})"
                            for k, v in _vc_auto_state.items()]
                    return await message.channel.send(
                        S.ui_box("vcreconnect", rows), delete_after=8)
                st = _vc_auto_state.get(gid)
                if not st or not st.get("enabled"):
                    return await message.channel.send(
                        S.ui_info("auto-reconnect is OFF"), delete_after=5)
                await message.channel.send(
                    S.ui_ok(f"auto-reconnect ON → ch {st['channel_id']}"),
                    delete_after=6)
            else:
                await message.channel.send(
                    S.ui_info("usage: vcreconnect on <ch_id> | off | status"),
                    delete_after=6)