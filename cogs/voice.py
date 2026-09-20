# cogs/voice.py | vc join/leave/move/self-controls + auto-reconnect watchdog
import asyncio
import discord
from . import state as S


# per-guild auto-reconnect state
# { guild_id: {"channel_id": int, "enabled": bool, "task": asyncio.Task | None} }
_vc_auto_state: dict = {}
_VC_RECONNECT_DELAY = 5      # seconds between reconnect attempts
_VC_RECONNECT_MAX_TRIES = 12 # give up after this many consecutive failures


class VoiceCog:
    COMMANDS = {"vcjoin", "vcleave", "vcmute", "vcunmute", "vcdeafen", "vcundeafen",
                "vckick", "vcmove", "vcmoveall",
                "selfmute", "selfdeaf", "selfstream", "selfcamera",
                "vcreconnect"}

    def register(self, client):
        """Hook voice state updates so we can detect when the client drops."""
        cog = self

        @client.event
        async def on_voice_state_update(member, before, after):
            await cog._on_voice_state(member, before, after)

    # ── event handler ──

    async def _on_voice_state(self, member, before, after):
        client = S.CLIENT
        if member.id != client.user.id:
            return
        # only care when we leave a channel (before has one, after doesn't) or move channels
        if before.channel == after.channel:
            return
        gid = member.guild.id
        state = _vc_auto_state.get(gid)
        if not state or not state.get("enabled"):
            return
        # if we're already where we should be, nothing to do
        if after.channel and after.channel.id == state["channel_id"]:
            return
        # cancel any existing watcher and start a fresh one
        task = state.get("task")
        if task and not task.done():
            task.cancel()
        state["task"] = asyncio.create_task(self._reconnect_loop(gid))

    async def _reconnect_loop(self, guild_id: int):
        """Repeatedly try to rejoin the target channel until we succeed or give up."""
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
            guild = client.get_guild(guild_id)
            if not guild:
                return
            # already back in the right channel?
            if guild.voice_client and guild.voice_client.channel and guild.voice_client.channel.id == target_id:
                return
            target = guild.get_channel(target_id)
            if not target:
                print(f"[vc-reconnect] target {target_id} not found in guild {guild_id}")
                return
            try:
                # tear down any stale vc client first
                if guild.voice_client:
                    try:
                        await guild.voice_client.disconnect(force=True)
                    except Exception:
                        pass
                await target.connect(self_deaf=True)
                print(f"[vc-reconnect] reconnected to {target.name} in {guild.name}")
                return
            except Exception as e:
                tries += 1
                print(f"[vc-reconnect] attempt {tries}/{_VC_RECONNECT_MAX_TRIES} failed: {e}")

        print(f"[vc-reconnect] giving up on guild {guild_id} after {tries} attempts")

    # ── command dispatch ──

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        try: await message.delete()
        except Exception: pass

        if cmd == "vcjoin":
            if len(args) < 2:
                return await message.channel.send(S.ui_err("usage: vcjoin <ch_id>"), delete_after=5)
            try:
                ch = client.get_channel(int(args[1]))
                if not ch:
                    return await message.channel.send(S.ui_err("channel not found"), delete_after=5)
                if message.guild and message.guild.voice_client:
                    await message.guild.voice_client.disconnect(force=True)
                await ch.connect(self_deaf=True)
                await message.channel.send(S.ui_ok(f"joined {ch.name}"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd == "vcleave":
            if message.guild and message.guild.voice_client:
                name = message.guild.voice_client.channel.name
                await message.guild.voice_client.disconnect(force=True)
                await message.channel.send(S.ui_ok(f"left {name}"), delete_after=5)
            else:
                await message.channel.send(S.ui_err("not in a vc"), delete_after=5)

        elif cmd in ("vcmute", "vcunmute", "vcdeafen", "vcundeafen", "vckick"):
            if not message.guild or len(args) < 2:
                return await message.channel.send(S.ui_err(f"usage: {cmd} <user_id>"), delete_after=5)
            try:
                member = message.guild.get_member(int(args[1]))
                if not member or not member.voice:
                    return await message.channel.send(S.ui_err("user not in vc"), delete_after=5)
                if cmd == "vcmute": await member.edit(mute=True)
                elif cmd == "vcunmute": await member.edit(mute=False)
                elif cmd == "vcdeafen": await member.edit(deafen=True)
                elif cmd == "vcundeafen": await member.edit(deafen=False)
                elif cmd == "vckick": await member.move_to(None)
                await message.channel.send(S.ui_ok(f"{cmd} → {member.name}"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd == "vcmove":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: vcmove <user_id> <ch_id>"), delete_after=5)
            try:
                member = message.guild.get_member(int(args[1]))
                ch = client.get_channel(int(args[2]))
                await member.move_to(ch)
                await message.channel.send(S.ui_ok(f"moved {member.name}"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd == "vcmoveall":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: vcmoveall <ch1> <ch2>"), delete_after=5)
            try:
                ch1 = client.get_channel(int(args[1])); ch2 = client.get_channel(int(args[2]))
                for m in list(ch1.members):
                    await m.move_to(ch2); await asyncio.sleep(0.2)
                await message.channel.send(S.ui_ok("moved all"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd in ("selfmute", "selfdeaf", "selfstream", "selfcamera"):
            if not message.guild:
                return await message.channel.send(S.ui_err("must be in a server"), delete_after=5)
            me = message.guild.me
            vs = me.voice if me else None
            if not vs or not vs.channel:
                return await message.channel.send(S.ui_err("not in a vc"), delete_after=5)
            cur_mute = vs.self_mute; cur_deaf = vs.self_deaf
            cur_stream = vs.self_stream; cur_video = vs.self_video
            new_mute = (not cur_mute) if cmd == "selfmute" else cur_mute
            new_deaf = (not cur_deaf) if cmd == "selfdeaf" else cur_deaf
            new_stream = (not cur_stream) if cmd == "selfstream" else cur_stream
            new_video = (not cur_video) if cmd == "selfcamera" else cur_video
            try:
                await client.ws.send_as_json({"op": 4, "d": {
                    "guild_id": str(message.guild.id), "channel_id": str(vs.channel.id),
                    "self_mute": new_mute, "self_deaf": new_deaf,
                    "self_stream": new_stream, "self_video": new_video,
                }})
                state_map = {
                    "selfmute": "muted" if new_mute else "unmuted",
                    "selfdeaf": "deafened" if new_deaf else "undeafened",
                    "selfstream": "streaming" if new_stream else "stopped",
                    "selfcamera": "camera on" if new_video else "camera off",
                }
                await message.channel.send(S.ui_ok(f"self {state_map[cmd]}"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(f"failed: {e}"), delete_after=6)

        elif cmd == "vcreconnect":
            if not message.guild:
                return await message.channel.send(S.ui_err("server only"), delete_after=5)
            gid = message.guild.id
            sub = args[1].lower() if len(args) > 1 else ""

            if sub in ("on", "enable"):
                # pick the target channel: current vc, or explicit ch_id, or error
                target_id = None
                if len(args) > 2 and args[2].isdigit():
                    target_id = int(args[2])
                elif message.guild.voice_client and message.guild.voice_client.channel:
                    target_id = message.guild.voice_client.channel.id
                if not target_id:
                    return await message.channel.send(
                        S.ui_err("not in a vc — usage: vcreconnect on [ch_id]"), delete_after=6)
                target = message.guild.get_channel(target_id)
                if not target:
                    return await message.channel.send(S.ui_err("channel not found"), delete_after=5)
                st = _vc_auto_state.get(gid)
                if st and st.get("task") and not st["task"].done():
                    st["task"].cancel()
                _vc_auto_state[gid] = {"channel_id": target_id, "enabled": True, "task": None}
                # if we're already out, kick the watcher now
                vc = message.guild.voice_client
                if not vc or not vc.channel or vc.channel.id != target_id:
                    _vc_auto_state[gid]["task"] = asyncio.create_task(self._reconnect_loop(gid))
                await message.channel.send(
                    S.ui_ok(f"auto-reconnect ON → {target.name}"), delete_after=6)

            elif sub in ("off", "disable"):
                st = _vc_auto_state.pop(gid, None)
                if st and st.get("task") and not st["task"].done():
                    st["task"].cancel()
                await message.channel.send(S.ui_ok("auto-reconnect OFF"), delete_after=5)

            elif sub == "status" or not sub:
                st = _vc_auto_state.get(gid)
                if not st or not st.get("enabled"):
                    return await message.channel.send(S.ui_info("auto-reconnect is OFF"), delete_after=5)
                ch = message.guild.get_channel(st["channel_id"])
                name = ch.name if ch else f"<deleted {st['channel_id']}>"
                await message.channel.send(
                    S.ui_ok(f"auto-reconnect ON → {name}"), delete_after=6)

            else:
                await message.channel.send(
                    S.ui_info("usage: vcreconnect on [ch_id] | off | status"), delete_after=6)
