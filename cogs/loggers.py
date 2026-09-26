# cogs/loggers.py | message / deleted / edited / reaction / mention / dm / join / leave loggers
import time
from datetime import datetime
import modifyself_shim as discord
from . import state as S


def _log_ch(client, kind):
    ch_id = S.loggers.get(kind, {}).get("channel")
    if not ch_id:
        return None
    try:
        return client.get_channel(int(ch_id))
    except Exception:
        return None


def _fmt_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def _emit(client, kind, title, rows):
    ch = _log_ch(client, kind)
    if not ch:
        return
    try:
        await ch.send(S.ui_box(title, rows))
    except Exception:
        pass


class LoggersCog:
    COMMANDS = {"logger", "logchannel", "logstatus",
                "logmessage", "logdeleted", "logedited", "logreaction",
                "logmention", "logdm", "logjoins", "logleaves"}

    def register(self, client):
        cog = self

        @client.event
        async def on_message(m):
            if not S.loggers["message"]["enabled"]: return
            if getattr(m.author, "id", None) == client.user.id: return
            await cog._message(m)

        @client.event
        async def on_message_delete(m):
            if not S.loggers["deleted"]["enabled"]: return
            if getattr(m.author, "id", None) == client.user.id: return
            await cog._deleted(m)

        @client.event
        async def on_message_edit(before, after):
            if not S.loggers["edited"]["enabled"]: return
            if getattr(before.author, "id", None) == client.user.id: return
            if (before.content or "") == (after.content or ""): return
            await cog._edited(before, after)

        @client.event
        async def on_reaction_add(reaction, user):
            if not S.loggers["reaction"]["enabled"]: return
            if getattr(user, "id", None) == client.user.id: return
            await cog._reaction(reaction, user, add=True)

        @client.event
        async def on_reaction_remove(reaction, user):
            if not S.loggers["reaction"]["enabled"]: return
            if getattr(user, "id", None) == client.user.id: return
            await cog._reaction(reaction, user, add=False)

        @client.event
        async def on_member_join(member):
            if not S.loggers["joins"]["enabled"]: return
            await _emit(client, "joins", "member join", [
                f"  {S.DIM}user{S.RESET}  {member}  ({getattr(member, 'id', '?')})",
                f"  {S.DIM}guild{S.RESET} {getattr(getattr(member, 'guild', None), 'name', '?')}",
                f"  {S.DIM}at{S.RESET}    {_fmt_time()}",
            ])

        @client.event
        async def on_member_remove(member):
            if not S.loggers["leaves"]["enabled"]: return
            await _emit(client, "leaves", "member leave", [
                f"  {S.DIM}user{S.RESET}  {member}  ({getattr(member, 'id', '?')})",
                f"  {S.DIM}guild{S.RESET} {getattr(getattr(member, 'guild', None), 'name', '?')}",
                f"  {S.DIM}at{S.RESET}    {_fmt_time()}",
            ])

    async def _message(self, m):
        g = getattr(m, "guild", None)
        rows = [
            f"  {S.DIM}guild{S.RESET}   {getattr(g, 'name', 'DM')}",
            f"  {S.DIM}channel{S.RESET} {getattr(m.channel, 'name', str(m.channel_id))}",
            f"  {S.DIM}author{S.RESET}  {m.author}  ({getattr(m.author,'id','?')})",
            f"  {S.DIM}at{S.RESET}      {_fmt_time()}",
            "",
            f"  {S.WHITE}{(m.content or '')[:400]}{S.RESET}",
        ]
        await _emit(m._state and m._state._get_client() if hasattr(m, "_state") else S.CLIENT,
                    "message", "message", rows)

    async def _deleted(self, m):
        atts = [a.get("url") for a in (m.attachments or [])]
        rows = [
            f"  {S.DIM}author{S.RESET}  {m.author}  ({getattr(m.author,'id','?')})",
            f"  {S.DIM}channel{S.RESET} {getattr(m.channel, 'name', str(m.channel_id))}",
            f"  {S.DIM}at{S.RESET}      {_fmt_time()}",
        ]
        if atts:
            rows.append(f"  {S.DIM}attach{S.RESET}  {', '.join(atts)}")
        rows += ["", f"  {S.WHITE}{(m.content or '')[:400]}{S.RESET}"]
        await _emit(S.CLIENT, "deleted", "message deleted", rows)

    async def _edited(self, before, after):
        rows = [
            f"  {S.DIM}author{S.RESET}  {before.author}  ({getattr(before.author,'id','?')})",
            f"  {S.DIM}channel{S.RESET} {getattr(before.channel, 'name', str(before.channel_id))}",
            f"  {S.DIM}at{S.RESET}      {_fmt_time()}",
            "",
            f"  {S.DIM}before{S.RESET}",
            f"  {S.WHITE}{(before.content or '')[:400]}{S.RESET}",
            "",
            f"  {S.DIM}after{S.RESET}",
            f"  {S.WHITE}{(after.content or '')[:400]}{S.RESET}",
        ]
        await _emit(S.CLIENT, "edited", "message edited", rows)

    async def _reaction(self, reaction, user, add=True):
        try:
            msg = reaction.message
        except Exception:
            return
        rows = [
            f"  {S.DIM}user{S.RESET}    {user}  ({getattr(user,'id','?')})",
            f"  {S.DIM}emoji{S.RESET}   {reaction.emoji}",
            f"  {S.DIM}action{S.RESET}  {'added' if add else 'removed'}",
            f"  {S.DIM}channel{S.RESET} {getattr(msg.channel, 'name', str(getattr(msg,'channel_id','?')))}",
            f"  {S.DIM}at{S.RESET}      {_fmt_time()}",
            "",
            f"  {S.WHITE}{(getattr(msg, 'content', '') or '')[:200]}{S.RESET}",
        ]
        await _emit(S.CLIENT, "reaction", "reaction", rows)

    async def handle(self, message, cmd, args):
        if cmd == "logger":
            sub = args[1].lower() if len(args) > 1 else ""
            kinds = list(S.loggers.keys())
            if sub in kinds:
                kind = sub
                on = (args[2].lower() in ("on", "enable")) if len(args) > 2 else not S.loggers[kind]["enabled"]
                S.loggers[kind]["enabled"] = on
                return await message.edit(content=S.ui_ok(f"{kind} logger → {on}"))
            if sub == "all":
                on = (args[2].lower() in ("on", "enable")) if len(args) > 2 else True
                for k in kinds:
                    S.loggers[k]["enabled"] = on
                return await message.edit(content=S.ui_ok(f"all loggers → {on}"))
            rows = [f"  {S.DIM}{k:<10}{S.RESET}  {'ON' if v['enabled'] else 'off'}  "
                    f"{S.DIM}{v['channel'] or '—'}{S.RESET}" for k, v in S.loggers.items()]
            return await message.edit(content=S.ui_box("loggers", rows))

        if cmd == "logchannel":
            if len(args) < 3:
                return await message.edit(content=S.ui_err("usage: logchannel <kind> <ch_id>"))
            kind = args[1].lower()
            if kind not in S.loggers:
                return await message.edit(content=S.ui_err(f"unknown kind: {kind}"))
            S.loggers[kind]["channel"] = args[2]
            await message.edit(content=S.ui_ok(f"{kind} → {args[2]}"))

        if cmd == "logstatus":
            rows = [f"  {S.DIM}{k:<10}{S.RESET}  {'ON ' if v['enabled'] else 'off'}  ch {v['channel'] or '—'}"
                    for k, v in S.loggers.items()]
            await message.edit(content=S.ui_box("loggers status", rows))

        elif cmd.startswith("log") and cmd[3:] in S.loggers:
            kind = cmd[3:]
            on = (args[1].lower() in ("on", "enable")) if len(args) > 1 else not S.loggers[kind]["enabled"]
            S.loggers[kind]["enabled"] = on
            await message.edit(content=S.ui_ok(f"{kind} → {on}"))