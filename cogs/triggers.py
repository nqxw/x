# cogs/triggers.py | message / reaction / voice / member triggers + fired counts
import json
import os
import modifyself_shim as discord
from . import state as S


def triggers_save():
    try:
        with open(S._TRIGGER_STORE, "w") as f:
            json.dump(S._triggers, f, indent=2)
    except Exception: pass


def triggers_load():
    if os.path.exists(S._TRIGGER_STORE):
        try:
            with open(S._TRIGGER_STORE) as f:
                loaded = json.load(f)
            S._triggers.clear()
            S._triggers.update(loaded)
        except Exception: pass


class TriggersCog:
    COMMANDS = {"trigger", "triggers"}

    def register(self, client):
        cog = self

        @client.event
        async def on_reaction_add(reaction, user):
            await cog._on_reaction(reaction, user)

        @client.event
        async def on_voice_state_update(member, before, after):
            await cog._on_voice(member, before, after)

        @client.event
        async def on_member_join(member):
            await cog._on_member(member, "join")

        @client.event
        async def on_member_remove(member):
            await cog._on_member(member, "leave")

    async def _on_reaction(self, reaction, user):
        client = S.CLIENT
        if user.id == client.user.id: return
        for t in S._triggers.get("reaction", []):
            try:
                if (str(reaction.emoji) == str(t.get("emoji"))
                        or (t.get("emoji") and t["emoji"] in str(reaction.emoji))):
                    S._trigger_fired_counts[t["name"]] = S._trigger_fired_counts.get(t["name"], 0) + 1
                    mode = t.get("mode", "dm")
                    if mode == "reply":
                        try: await reaction.message.reply(t.get("reply") or "")
                        except Exception: pass
                    elif mode == "channel":
                        try: await reaction.message.channel.send(t.get("reply") or "")
                        except Exception: pass
                    elif mode == "dm":
                        try: await user.send(t.get("reply") or "")
                        except Exception: pass
            except Exception: pass

    async def _on_voice(self, member, before, after):
        for t in S._triggers.get("voice", []):
            try:
                ev = t.get("event", "")
                ch_filter = t.get("channel_id")
                fire = False
                if ev == "join" and before.channel is None and after.channel is not None: fire = True
                elif ev == "leave" and before.channel is not None and after.channel is None: fire = True
                elif (ev == "move" and before.channel and after.channel
                      and before.channel != after.channel): fire = True
                if fire:
                    if ch_filter and str((after.channel or before.channel).id) != str(ch_filter):
                        continue
                    S._trigger_fired_counts[t["name"]] = S._trigger_fired_counts.get(t["name"], 0) + 1
                    if S._monitor["log_ch"]:
                        ch = S.CLIENT.get_channel(int(S._monitor["log_ch"]))
                        if ch:
                            try:
                                await ch.send(S.ui_box(f"voice trigger: {t['name']}",
                                                       [f"{member} {ev}"]))
                            except Exception: pass
            except Exception: pass

    async def _on_member(self, member, event):
        for t in S._triggers.get("member", []):
            try:
                if t.get("event") == event:
                    S._trigger_fired_counts[t["name"]] = S._trigger_fired_counts.get(t["name"], 0) + 1
                    if S._monitor["log_ch"]:
                        ch = S.CLIENT.get_channel(int(S._monitor["log_ch"]))
                        if ch:
                            try:
                                await ch.send(S.ui_box(f"member trigger: {t['name']}",
                                                       [f"{member} {event}"]))
                            except Exception: pass
            except Exception: pass

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass

        if cmd == "trigger":
            if len(args) < 3:
                return await message.channel.send(S.ui_info(
                    "usage: trigger message/reaction/voice/member add/remove/list"))
            kind = args[1].lower(); action = args[2].lower()
            if kind == "message": await self._message_trigger(message, action, args)
            elif kind == "reaction": await self._reaction_trigger(message, action, args)
            elif kind == "voice": await self._voice_trigger(message, action, args)
            elif kind == "member": await self._member_trigger(message, action, args)
            else: await message.channel.send(S.ui_info("kind: message/reaction/voice/member"))

        elif cmd == "triggers":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "status":
                rows = [f"  {S.GREY}•{S.RESET} {n}: {c}"
                        for n, c in S._trigger_fired_counts.items()]
                await message.channel.send(S._paginate("trigger counts", "", rows)
                                            if rows else S.ui_info("nothing fired yet"))
            elif sub == "clear":
                S._triggers["message"].clear()
                S._triggers["reaction"].clear()
                S._triggers["voice"].clear()
                S._triggers["member"].clear()
                S._trigger_fired_counts.clear()
                triggers_save()
                await message.channel.send(S.ui_ok("cleared"))
            else:
                await message.channel.send(S.ui_info("usage: triggers status/clear"))

    async def _message_trigger(self, message, action, args):
        if action == "add" and len(args) >= 5:
            rest = " ".join(args[3:])
            if "|" not in rest:
                return await message.channel.send(S.ui_err(
                    "format: trigger message add <name> <contains> | <reply>"), delete_after=6)
            pre, reply = rest.split("|", 1)
            parts = pre.strip().split(" ", 1)
            if len(parts) < 2:
                return await message.channel.send(S.ui_err("missing contains"), delete_after=6)
            name, contains = parts[0], parts[1].strip()
            S._triggers["message"].append({"name": name, "contains": contains,
                                            "reply": reply.strip()})
            triggers_save()
            await message.channel.send(S.ui_ok(f"message trigger `{name}` added"))
        elif action == "remove" and len(args) >= 4:
            name = args[3]
            S._triggers["message"] = [t for t in S._triggers["message"] if t.get("name") != name]
            triggers_save()
            await message.channel.send(S.ui_ok("removed"))
        elif action == "list":
            rows = [f"  {S.GREY}•{S.RESET} {t['name']}  "
                    f"{S.DIM}contains:{t.get('contains','')}{S.RESET}"
                    for t in S._triggers["message"]]
            await message.channel.send(S._paginate("message triggers", "", rows)
                                        if rows else S.ui_info("none"))
        else:
            await message.channel.send(S.ui_info("usage: trigger message add/remove/list"))

    async def _reaction_trigger(self, message, action, args):
        if action == "add" and len(args) >= 6:
            rest = " ".join(args[3:])
            if "|" not in rest:
                return await message.channel.send(S.ui_err(
                    "format: trigger reaction add <name> <emoji> <mode> | <reply>"), delete_after=6)
            pre, reply = rest.split("|", 1)
            parts = pre.strip().split()
            if len(parts) < 3:
                return await message.channel.send(S.ui_err("missing args"), delete_after=6)
            name, emoji, mode = parts[0], parts[1], parts[2]
            S._triggers["reaction"].append({"name": name, "emoji": emoji,
                                             "mode": mode, "reply": reply.strip()})
            triggers_save()
            await message.channel.send(S.ui_ok(f"reaction trigger `{name}` added"))
        elif action == "remove" and len(args) >= 4:
            name = args[3]
            S._triggers["reaction"] = [t for t in S._triggers["reaction"] if t.get("name") != name]
            triggers_save()
            await message.channel.send(S.ui_ok("removed"))
        elif action == "list":
            rows = [f"  {S.GREY}•{S.RESET} {t['name']}  "
                    f"{S.DIM}{t.get('emoji')} → {t.get('mode')}{S.RESET}"
                    for t in S._triggers["reaction"]]
            await message.channel.send(S._paginate("reaction triggers", "", rows)
                                        if rows else S.ui_info("none"))
        else:
            await message.channel.send(S.ui_info("usage: trigger reaction add/remove/list"))

    async def _voice_trigger(self, message, action, args):
        if action == "add" and len(args) >= 6:
            name = args[3]; event = args[4]; ch_id = args[5]
            S._triggers["voice"].append({"name": name, "event": event, "channel_id": ch_id})
            triggers_save()
            await message.channel.send(S.ui_ok(f"voice trigger `{name}` added"))
        elif action == "remove" and len(args) >= 4:
            name = args[3]
            S._triggers["voice"] = [t for t in S._triggers["voice"] if t.get("name") != name]
            triggers_save()
            await message.channel.send(S.ui_ok("removed"))
        elif action == "list":
            rows = [f"  {S.GREY}•{S.RESET} {t['name']}  "
                    f"{S.DIM}{t.get('event')} → {t.get('channel_id')}{S.RESET}"
                    for t in S._triggers["voice"]]
            await message.channel.send(S._paginate("voice triggers", "", rows)
                                        if rows else S.ui_info("none"))
        else:
            await message.channel.send(S.ui_info("usage: trigger voice add/remove/list"))

    async def _member_trigger(self, message, action, args):
        if action == "add" and len(args) >= 5:
            name = args[3]; event = args[4]
            S._triggers["member"].append({"name": name, "event": event})
            triggers_save()
            await message.channel.send(S.ui_ok(f"member trigger `{name}` added"))
        elif action == "remove" and len(args) >= 4:
            name = args[3]
            S._triggers["member"] = [t for t in S._triggers["member"] if t.get("name") != name]
            triggers_save()
            await message.channel.send(S.ui_ok("removed"))
        elif action == "list":
            rows = [f"  {S.GREY}•{S.RESET} {t['name']}  {S.DIM}{t.get('event')}{S.RESET}"
                    for t in S._triggers["member"]]
            await message.channel.send(S._paginate("member triggers", "", rows)
                                        if rows else S.ui_info("none"))
        else:
            await message.channel.send(S.ui_info("usage: trigger member add/remove/list"))