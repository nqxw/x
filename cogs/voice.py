# cogs/voice.py | vc join/leave/move/self-controls
import asyncio
import discord
from . import state as S


class VoiceCog:
    COMMANDS = {"vcjoin", "vcleave", "vcmute", "vcunmute", "vcdeafen", "vcundeafen",
                "vckick", "vcmove", "vcmoveall",
                "selfmute", "selfdeaf", "selfstream", "selfcamera"}

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