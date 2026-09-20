# cogs/fun.py | neko roleplay, gayrate, meme, joke, mimic/unmimic/stopmimic
import asyncio
import random
import aiohttp
from . import state as S


NEKO_ACTIONS = {"feed", "tickle", "slap", "hug", "cuddle", "pat", "kiss",
                "poke", "wink", "smug", "boop", "nom"}


class FunCog:
    COMMANDS = ({"gayrate", "meme", "joke",
                 "mimic", "unmimic", "stopmimic"} | NEKO_ACTIONS)

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass

        if cmd == "gayrate":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            pct = 0 if uid == message.author.id else random.randint(0, 100)
            await message.channel.send(f"🏳️‍🌈 <@{uid}> is **{pct}%** gay")

        elif cmd in NEKO_ACTIONS:
            url = await S.neko_gif(cmd) if S.neko_gif else None
            if url:
                uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
                mention = f" <@{uid}>" if uid else ""
                await message.channel.send(f"{cmd}{mention}\n{url}")
            else:
                await message.channel.send(S.ui_err("could not fetch image"), delete_after=5)

        elif cmd == "meme":
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get("https://meme-api.com/gimme") as r:
                        if r.status == 200:
                            await message.channel.send((await r.json()).get("url", "no meme"))
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd == "joke":
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get("https://official-joke-api.appspot.com/random_joke") as r:
                        if r.status == 200:
                            j = await r.json()
                            await message.channel.send(
                                f"**{j['setup']}**\n||{j['punchline']}||")
            except Exception as e:
                await message.channel.send(S.ui_err(str(e)), delete_after=5)

        elif cmd == "mimic":
            if len(args) < 2:
                return await message.channel.send(S.ui_err("usage: mimic <user_id>"), delete_after=5)
            uid = int(args[1]); cid = message.channel.id
            S._mimic_dict.setdefault(cid, [])
            if uid not in S._mimic_dict[cid]:
                S._mimic_dict[cid].append(uid)
            await message.channel.send(S.ui_ok(f"mimicking <@{uid}>"), delete_after=5)

        elif cmd == "unmimic":
            if len(args) < 2:
                return await message.channel.send(S.ui_err("usage: unmimic <user_id>"), delete_after=5)
            uid = int(args[1]); cid = message.channel.id
            if cid in S._mimic_dict and uid in S._mimic_dict[cid]:
                S._mimic_dict[cid].remove(uid)
                if not S._mimic_dict[cid]:
                    del S._mimic_dict[cid]
            await message.channel.send(S.ui_ok("stopped"), delete_after=5)

        elif cmd == "stopmimic":
            S._mimic_dict.clear()
            await message.channel.send(S.ui_ok("all stopped"), delete_after=5)