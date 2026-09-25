# cogs/agc.py | anti gc-trap
import asyncio
import base64
import json
import os
import aiohttp
import modifyself_shim as discord
from . import state as S


def _agc_headers():
    return {"Authorization": S.TOKEN, "Content-Type": "application/json",
            "User-Agent": S.USER_AGENT}


class AgcCog:
    COMMANDS = {"agc"}

    def register(self, client):
        cog = self

        @client.event
        async def on_group_channel_create(channel):
            await cog._on_gc_create(channel)

    async def _on_gc_create(self, channel):
        if not S._agc_state["enabled"]:
            return
        channel_id = str(channel.id)
        owner_id = str(channel.owner_id) if hasattr(channel, "owner_id") and channel.owner_id else ""
        if owner_id == str(S.CLIENT.user.id):
            return
        if owner_id in S._agc_whitelist:
            return
        if S.log_msg:
            S.log_msg("AGC", f"trap detected — ch {channel_id}, owner {owner_id}")

        h = _agc_headers()
        async with aiohttp.ClientSession() as s:
            if S._agc_state["gc_name"]:
                try:
                    await s.patch(f"https://discord.com/api/v9/channels/{channel_id}",
                                  headers=h, json={"name": S._agc_state["gc_name"]})
                except Exception: pass

            if S._agc_state["gc_icon_url"]:
                try:
                    async with s.get(S._agc_state["gc_icon_url"]) as r:
                        img = await r.read()
                    mime = "image/gif" if img[:6] in (b"GIF87a", b"GIF89a") else "image/png"
                    b64 = base64.b64encode(img).decode()
                    await s.patch(f"https://discord.com/api/v9/channels/{channel_id}",
                                  headers=h, json={"icon": f"data:{mime};base64,{b64}"})
                except Exception: pass

            if S._agc_state["leave_msg"]:
                try:
                    await s.post(f"https://discord.com/api/v9/channels/{channel_id}/messages",
                                 headers=h, json={"content": S._agc_state["leave_msg"]})
                except Exception: pass

            if S._agc_state["block"] and owner_id:
                try:
                    await s.put(
                        f"https://discord.com/api/v9/users/@me/relationships/{owner_id}",
                        headers=h, json={"type": 2})
                except Exception: pass

            for _ in range(3):
                try:
                    async with s.delete(
                        f"https://discord.com/api/v9/channels/{channel_id}", headers=h) as r:
                        if r.status in (200, 204):
                            break
                except Exception: pass
                await asyncio.sleep(1)

            if S._agc_state["webhook_url"]:
                try:
                    members = [str(u.id) for u in (channel.recipients or [])]
                    await s.post(S._agc_state["webhook_url"],
                                 json={"content": f"**AGC** owner `{owner_id}` ch `{channel_id}` "
                                                  f"members `{', '.join(members)}`"})
                except Exception: pass

    async def handle(self, message, cmd, args):
        sub = args[1].lower() if len(args) > 1 else ""
        if not sub:
            state = "ON" if S._agc_state["enabled"] else "OFF"
            return await message.edit(content=S.ui_info(f"agc is {state}"))

        if sub in ("on", "enable"):
            S._agc_state["enabled"] = True
            await message.edit(content=S.ui_ok("agc on"))
        elif sub in ("off", "disable"):
            S._agc_state["enabled"] = False
            await message.edit(content=S.ui_ok("agc off"))
        elif sub == "block":
            opt = args[2].lower() if len(args) > 2 else ""
            S._agc_state["block"] = opt in ("on", "enable")
            await message.edit(content=S.ui_ok(f"block → {opt}"))
        elif sub == "msg":
            S._agc_state["leave_msg"] = " ".join(args[2:])
            await message.edit(content=S.ui_ok("leave msg set"))
        elif sub == "name":
            S._agc_state["gc_name"] = " ".join(args[2:])
            await message.edit(content=S.ui_ok("gc name set"))
        elif sub == "icon":
            S._agc_state["gc_icon_url"] = args[2] if len(args) > 2 else None
            await message.edit(content=S.ui_ok("icon set"))
        elif sub == "webhook":
            S._agc_state["webhook_url"] = args[2] if len(args) > 2 else None
            await message.edit(content=S.ui_ok("webhook set"))
        elif sub == "whitelist" and len(args) >= 3:
            S._agc_whitelist.add(args[2].strip("<@!>"))
            S.agc_save_wl()
            await message.edit(content=S.ui_ok("whitelisted"))
        elif sub == "unwhitelist" and len(args) >= 3:
            S._agc_whitelist.discard(args[2].strip("<@!>"))
            S.agc_save_wl()
            await message.edit(content=S.ui_ok("removed"))
        elif sub == "wllist":
            rows = [f"  {S.GREY}•{S.RESET} {u}" for u in S._agc_whitelist]
            await message.edit(content=S._paginate("agc whitelist", "", rows)
                                        if rows else S.ui_info("empty"))
        else:
            await message.edit(content=S.ui_info(
                "usage: agc on/off/block/msg/name/icon/webhook/whitelist/wllist"))