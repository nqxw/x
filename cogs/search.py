# cogs/search.py | message search + bulk delete helpers
import asyncio
import modifyself_shim as discord
from . import state as S


class SearchCog:
    COMMANDS = {"msearch", "bulkdel", "delby"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT
        if cmd == "msearch":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: msearch <keyword> [limit]"))
            kw = args[1].lower()
            limit = int(args[2]) if len(args) > 2 and args[2].isdigit() else 500
            hits = []
            try:
                async for m in message.channel.history(limit=limit):
                    if kw in (m.content or "").lower():
                        hits.append(m)
                        if len(hits) >= 30: break
            except Exception as e:
                return await message.edit(content=S.ui_err(str(e)))
            if not hits:
                return await message.edit(content=S.ui_info("no matches"))
            rows = [f"  {S.GREY}•{S.RESET} {str(m.author)[:18]:<18} "
                    f"{S.DIM}{(m.content or '')[:70]}{S.RESET}"
                    for m in hits]
            await message.edit(content=S._paginate("search", f"`{kw}`", rows))

        elif cmd == "bulkdel":
            n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
            own_only = "-a" not in args
            deleted = 0
            try: await message.delete()
            except Exception: pass
            try:
                async for m in message.channel.history(limit=n * 2):
                    if own_only and m.author.id != client.user.id:
                        continue
                    try:
                        await m.delete()
                        deleted += 1
                    except Exception:
                        pass
                    await asyncio.sleep(0.35)
                    if deleted >= n: break
            except Exception as e:
                print(f"[bulkdel] {e}")
            try:
                await message.channel.send(S.ui_ok(f"deleted {deleted}"), delete_after=4)
            except Exception:
                pass

        elif cmd == "delby":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: delby <uid> [n]"))
            uid = int(args[1])
            n = int(args[2]) if len(args) > 2 and args[2].isdigit() else 50
            try: await message.delete()
            except Exception: pass
            deleted = 0
            try:
                async for m in message.channel.history(limit=n * 3):
                    if m.author.id != uid:
                        continue
                    try:
                        await m.delete()
                        deleted += 1
                    except Exception:
                        pass
                    await asyncio.sleep(0.35)
                    if deleted >= n: break
            except Exception as e:
                print(f"[delby] {e}")
            try:
                await message.channel.send(S.ui_ok(f"deleted {deleted} from {uid}"), delete_after=4)
            except Exception:
                pass