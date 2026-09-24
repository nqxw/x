# cogs/voice.py | vc join/leave/move/self-controls + auto-reconnect watchdog
import asyncio
import modifyself_shim as discord
from . import state as S


_vc_auto_state: dict = {}
_VC_RECONNECT_DELAY = 5
_VC_RECONNECT_MAX_TRIES = 12


class VoiceCog:
    COMMANDS = {"vcjoin", "vcleave", "vcmute", "vcunmute", "vcdeafen", "vcundeafen",
                "vckick", "vcmove", "vcmoveall",
                "selfmute", "selfdeaf", "selfstream", "selfcamera",
                "vcreconnect"}

    def register(self, client):
        cog = self

        @client.event
        async def on_voice_state_update(member, before, after):
            await cog._on_voice_state(member, before, after)

    async def _on_voice_state(self, member, before, after):
        client = S.CLIENT
        if member.id != client.user.id: return
        if before.channel == after.channel: return
        gid = member.guild_id
        state = _vc_auto_state.get(gid)
        if not state or not state.get("enabled"): return
        if after.channel and after.channel.id == state["channel_id"]: return
        task = state.get("task")
        if task and not task.done(): task.cancel()
        state["task"] = asyncio.create_task(self._reconnect_loop(gid))

    async def _reconnect_loop(self, guild_id: int):
        client = S.CLIENT
        state = _vc_auto_state.get(guild_id)
        if not state: return
        target_id = state["channel_id"]
        tries = 0
        while tries < _VC_RECONNECT_MAX_TRIES:
            await asyncio.sleep(_VC_RECONNECT_DELAY)
            state = _vc_auto_state.get(guild_id)
            if not state or not state.get("enabled"): return
            guild = client.get_guild(guild_id)
            if not guild: return
            # modifyself lacks a live voice client registry — this is best-effort
            try:
                # re-send op:4 to rejoin
                gw = getattr(client, "_gateway", None)
                if gw is not None:
                    await gw.send_voice_state(guild_id, target_id,
                                              self_mute=False, self_deaf=True)
                    print(f"[vc-reconnect] re-sent voice state for {target_id}")
                    return
            except Exception as e:
                tries += 1
                print(f"[vc-reconnect] attempt {tries}/{_VC_RECONNECT_MAX_TRIES}: {e}")
        print(f"[vc-reconnect] gave up on {guild_id}")

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        try: await message.delete()
        except Exception: pass

        if cmd == "vcjoin":
            if len(args) < 2:
                return await message.channel.send(S.ui_err("usage: vcjoin <ch_id>"), delete_after=5)
            try:
                gw = getattr(client, "_gateway", None)
                await gw.send_voice_state(message.guild.id if message.guild else None,
                                          int(args[1]), self_mute=False, self_deaf=True)
                await message.channel.send(S.ui_ok(f"joined"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd == "vcleave":
            try:
                gw = getattr(client, "_gateway", None)
                await gw.send_voice_state(message.guild.id if message.guild else None,
                                          None, self_mute=False, self_deaf=False)
                await message.channel.send(S.ui_ok("left vc"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd in ("vcmute", "vcunmute", "vcdeafen", "vcundeafen", "vckick"):
            if not message.guild or len(args) < 2:
                return await message.channel.send(S.ui_err(f"usage: {cmd} <user_id>"), delete_after=5)
            try:
                uid = int(args[1])
                method = "PATCH"
                payload = {}
                if cmd == "vcmute": payload = {"mute": True}
                elif cmd == "vcunmute": payload = {"mute": False}
                elif cmd == "vcdeafen": payload = {"deaf": True}
                elif cmd == "vcundeafen": payload = {"deaf": False}
                elif cmd == "vckick": payload = {"channel_id": None}
                await client._http.request(
                    method=method,
                    url=f"/guilds/{message.guild.id}/members/{uid}",
                    json=payload)
                await message.channel.send(S.ui_ok(f"{cmd} → {uid}"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd == "vcmove":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: vcmove <user_id> <ch_id>"), delete_after=5)
            try:
                await client._http.request(
                    method="PATCH",
                    url=f"/guilds/{message.guild.id}/members/{int(args[1])}",
                    json={"channel_id": str(int(args[2]))})
                await message.channel.send(S.ui_ok("moved"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd == "vcmoveall":
            if not message.guild or len(args) < 3:
                return await message.channel.send(S.ui_err("usage: vcmoveall <ch1> <ch2>"), delete_after=5)
            # modifyself doesn't expose live voice member lists — this would need
            # a raw guild members fetch + op:4 per member. leaving best-effort.
            await message.channel.send(S.ui_info("vcmoveall needs voice member cache — not available on modifyself yet"), delete_after=8)

        elif cmd in ("selfmute", "selfdeaf", "selfstream", "selfcamera"):
            if not message.guild:
                return await message.channel.send(S.ui_err("must be in a server"), delete_after=5)
            # toggle is best-effort — we don't track current state
            try:
                gw = getattr(client, "_gateway", None)
                if cmd == "selfmute":
                    await gw.send_voice_state(message.guild.id, None, self_mute=True)
                elif cmd == "selfdeaf":
                    await gw.send_voice_state(message.guild.id, None, self_deaf=True)
                elif cmd == "selfstream":
                    await gw.send_voice_state(message.guild.id, None, self_video=False, flags=2)
                elif cmd == "selfcamera":
                    await gw.send_voice_state(message.guild.id, None, self_video=True)
                await message.channel.send(S.ui_ok(f"{cmd} toggled"), delete_after=5)
            except Exception as e:
                await message.channel.send(S.ui_err(f"failed: {e}"), delete_after=6)

        elif cmd == "vcreconnect":
            if not message.guild:
                return await message.channel.send(S.ui_err("server only"), delete_after=5)
            gid = message.guild.id
            sub = args[1].lower() if len(args) > 1 else ""

            if sub in ("on", "enable"):
                target_id = None
                if len(args) > 2 and args[2].isdigit():
                    target_id = int(args[2])
                if not target_id:
                    return await message.channel.send(
                        S.ui_err("usage: vcreconnect on <ch_id>"), delete_after=6)
                _vc_auto_state[gid] = {"channel_id": target_id, "enabled": True, "task": None}
                await message.channel.send(
                    S.ui_ok(f"auto-reconnect ON → ch {target_id}"), delete_after=6)
            elif sub in ("off", "disable"):
                st = _vc_auto_state.pop(gid, None)
                if st and st.get("task") and not st["task"].done():
                    st["task"].cancel()
                await message.channel.send(S.ui_ok("auto-reconnect OFF"), delete_after=5)
            elif sub == "status" or not sub:
                st = _vc_auto_state.get(gid)
                if not st or not st.get("enabled"):
                    return await message.channel.send(S.ui_info("auto-reconnect is OFF"), delete_after=5)
                await message.channel.send(
                    S.ui_ok(f"auto-reconnect ON → ch {st['channel_id']}"), delete_after=6)
            else:
                await message.channel.send(
                    S.ui_info("usage: vcreconnect on <ch_id> | off | status"), delete_after=6)