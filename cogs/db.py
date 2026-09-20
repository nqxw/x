# cogs/db.py | notes, history, command stats, db info/vacuum
import os
from datetime import datetime
from . import state as S


class DbCog:
    COMMANDS = {"note", "history", "stats", "db"}

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass

        if cmd == "note":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "add" and len(args) >= 4:
                S.db_note_add(args[2], " ".join(args[3:]))
                await message.channel.send(S.ui_ok("saved"))
            elif sub == "list" and len(args) >= 3:
                rows = [f"  {S.GREY}•{S.RESET} "
                        f"{datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')}  {n}"
                        for n, ts in S.db_note_list(args[2])]
                await message.channel.send(
                    S._paginate("notes", args[2], rows) if rows else S.ui_info("none"))
            elif sub == "clear" and len(args) >= 3:
                S.db_note_clear(args[2])
                await message.channel.send(S.ui_ok("cleared"))
            else:
                await message.channel.send(S.ui_info("usage: note add/list/clear"))

        elif cmd == "history":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "add" and len(args) >= 4:
                S.db_hist_add(args[2], " ".join(args[3:]))
                await message.channel.send(S.ui_ok("saved"))
            elif sub == "list" and len(args) >= 3:
                rows = [f"  {S.GREY}•{S.RESET} "
                        f"{datetime.fromtimestamp(ts).strftime('%H:%M')}  {e}"
                        for e, ts in S.db_hist_list(args[2])]
                await message.channel.send(
                    S._paginate("history", args[2], rows) if rows else S.ui_info("none"))
            else:
                await message.channel.send(S.ui_info("usage: history add/list"))

        elif cmd == "stats":
            if len(args) > 1 and args[1].lower() == "clear":
                S.db_stats_clear()
                return await message.channel.send(S.ui_ok("cleared"))
            rows = [f"  {S.GREY}•{S.RESET} {c}  {S.DIM}{n}{S.RESET}"
                    for c, n in S.db_stats_all()[:100]]
            await message.channel.send(
                S._paginate("stats", "top commands", rows) if rows else S.ui_info("no stats yet"))

        elif cmd == "db":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "info":
                size = os.path.getsize(S._db_path) if os.path.exists(S._db_path) else 0
                await message.channel.send(S.ui_box("db", [
                    f"  {S.DIM}path{S.RESET}  {S._db_path}",
                    f"  {S.DIM}size{S.RESET}  {size} bytes"]))
            elif sub == "vacuum":
                S._db.execute("VACUUM"); S._db.commit()
                await message.channel.send(S.ui_ok("vacuumed"))
            else:
                await message.channel.send(S.ui_info("usage: db info/vacuum"))