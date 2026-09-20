# cogs/tracking.py | track/untrack/tracklist — message + profile history per user
from datetime import datetime
from . import state as S


class TrackingCog:
    COMMANDS = {"track", "untrack", "tracklist"}

    async def handle(self, message, cmd, args):
        if cmd == "track":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: track <user_id>"))
            S._tracked_users.add(int(args[1]))
            await message.edit(content=S.ui_ok(f"tracking <@{args[1]}>"))

        elif cmd == "untrack":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: untrack <user_id>"))
            uid = int(args[1])
            S._tracked_users.discard(uid)
            S._tracking.pop(uid, None)
            await message.edit(content=S.ui_ok("stopped"))

        elif cmd == "tracklist":
            if not S._tracked_users:
                return await message.edit(content=S.ui_info("not tracking anyone"))
            rows = [f"  {S.GREY}•{S.RESET} <@{uid}>  "
                    f"{S.DIM}({len(S._tracking.get(uid,[]))} msgs){S.RESET}"
                    for uid in S._tracked_users]
            await message.edit(content=S._paginate("tracking", "", rows))