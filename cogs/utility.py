# cogs/utility.py | text transforms, AFK, translate, typing, ghostping, pins, autodelete, firstmessage
import asyncio
import sys
import random
import aiohttp
import modifyself_shim as discord
from . import state as S


def _sync(**kw):
    main = sys.modules.get("__main__")
    if main is None:
        return
    for k, v in kw.items():
        try:
            setattr(main, k, v)
        except Exception:
            pass


def _uwuify(text):
    import re
    text = re.sub(r'[rRlL]', 'w', text)
    text = re.sub(r'n([aeiou])', r'ny\1', text)
    text = re.sub(r'N([aeiou])', r'Ny\1', text)
    faces = [" >w<", " uwu", " owo", " >.<", " ^w^"]
    for p in ".!?":
        text = text.replace(p, p + random.choice(faces))
    return text


def _owoify(text):
    for a, b in [("r", "w"), ("l", "w"), ("R", "W"), ("L", "W"),
                 ("n", "ny"), ("N", "NY")]:
        text = text.replace(a, b)
    return f"owo {text} owo"


def _mock(text):
    return "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))


def _aesthetic(text):
    n = "abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    w = ("ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ　"
         "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
         "０１２３４５６７８９")
    return text.translate(str.maketrans(n, w))


def _clap(text):
    return " 👏 ".join(text.split())


async def _typing_loop(channel):
    while True:
        try:
            async with channel.typing():
                await asyncio.sleep(9)
        except Exception:
            await asyncio.sleep(5)


def _h():
    return {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}


class UtilityCog:
    COMMANDS = {"uwuify", "owoify", "mock", "reverse", "aesthetic", "clap",
                "animatetype", "typing", "typingstop",
                "afk", "afkstop", "translate",
                "ghostping", "pin", "unpin",
                "therapy", "ragebait", "firstmessage",
                "autodelete"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "uwuify":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: uwuify <text>"))
            await message.edit(content=_uwuify(" ".join(args[1:])))

        elif cmd == "owoify":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: owoify <text>"))
            await message.edit(content=_owoify(" ".join(args[1:])))

        elif cmd == "mock":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: mock <text>"))
            await message.edit(content=_mock(" ".join(args[1:])))

        elif cmd == "reverse":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: reverse <text>"))
            await message.edit(content=" ".join(args[1:])[::-1])

        elif cmd == "aesthetic":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: aesthetic <text>"))
            await message.edit(content=_aesthetic(" ".join(args[1:])))

        elif cmd == "clap":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: clap <text>"))
            await message.edit(content=_clap(" ".join(args[1:])))

        elif cmd == "animatetype":
            if len(args) < 2: return await message.edit(content=S.ui_err("usage: animatetype <text>"))
            text = " ".join(args[1:])
            built = ""
            for ch in text:
                built += ch
                try: await message.edit(content=built)
                except Exception: pass
                await asyncio.sleep(0.1)

        elif cmd == "typing":
            cid = message.channel.id
            if cid in S._typing_tasks and not S._typing_tasks[cid].done():
                return await message.edit(content=S.ui_info("already typing here"))
            try: await message.delete()
            except Exception: pass
            S._typing_tasks[cid] = asyncio.create_task(_typing_loop(message.channel))

        elif cmd == "typingstop":
            cid = message.channel.id
            if cid in S._typing_tasks:
                S._typing_tasks[cid].cancel()
                del S._typing_tasks[cid]
            try: await message.delete()
            except Exception: pass

        elif cmd == "afk":
            S._afk_enabled = True
            S._afk_msg = " ".join(args[1:]) if len(args) > 1 else "I'm AFK right now."
            _sync(_afk_enabled=True, _afk_msg=S._afk_msg)
            await message.edit(content=S.ui_ok(f"AFK set: {S._afk_msg}"))

        elif cmd == "afkstop":
            S._afk_enabled = False
            S._afk_msg = None
            _sync(_afk_enabled=False, _afk_msg=None)
            await message.edit(content=S.ui_ok("AFK disabled"))

        elif cmd == "translate":
            if len(args) < 3:
                return await message.edit(content=S.ui_err("usage: translate <lang> <text>"))
            translated = (await S.translate_text(" ".join(args[2:]), args[1])
                          if S.translate_text else "")
            await message.edit(content=f"```\n{translated}\n```")

        elif cmd == "ghostping":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: ghostping <user_id>"))
            try: await message.delete()
            except Exception: pass
            m = await message.channel.send(f"<@{args[1]}>")
            await asyncio.sleep(0.3)
            await m.delete()

        elif cmd == "pin":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: pin <msg_id>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.put(
                        f"https://discord.com/api/v9/channels/{message.channel.id}/pins/{args[1]}",
                        headers=_h()) as r:
                        if r.status in (200, 204):
                            await message.edit(content=S.ui_ok("pinned"))
                        else:
                            await message.edit(content=S.ui_err(f"failed {r.status}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "unpin":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: unpin <msg_id>"))
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.delete(
                        f"https://discord.com/api/v9/channels/{message.channel.id}/pins/{args[1]}",
                        headers=_h()) as r:
                        if r.status in (200, 204):
                            await message.edit(content=S.ui_ok("unpinned"))
                        else:
                            await message.edit(content=S.ui_err(f"failed {r.status}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "therapy":
            try: await message.delete()
            except Exception: pass
            await message.channel.send(random.choice([
                "I hear you. That sounds really difficult.",
                "Your feelings are valid. Take things one step at a time.",
                "It's okay to not have everything figured out.",
                "You're doing better than you think.",
                "Remember to be kind to yourself today.",
            ]))

        elif cmd == "ragebait":
            try: await message.delete()
            except Exception: pass
            await message.channel.send(random.choice([
                "pineapple on pizza is literally the best topping change my mind",
                "anime is just cartoons for people who couldn't make friends in high school",
                "dogs are overrated. cats are objectively superior",
                "morning people are just people who go to bed early. you're not special",
            ]))

        elif cmd == "firstmessage":
            try: await message.delete()
            except Exception: pass
            async for msg in message.channel.history(limit=1, oldest_first=True):
                await message.channel.send(S.ui_box("first message", [
                    f"  {S.DIM}author{S.RESET}  {msg.author}",
                    f"  {S.DIM}content{S.RESET} {msg.content[:200] or '(empty)'}",
                    f"  {S.DIM}url{S.RESET}     {getattr(msg, 'jump_url', '(n/a)')}",
                ]))

        elif cmd == "autodelete":
            if len(args) > 1 and args[1].lower() == "off":
                S._autodelete_secs = 0
                _sync(_autodelete_secs=0)
                await message.edit(content=S.ui_ok("autodelete off"))
            elif len(args) > 1 and args[1].isdigit():
                S._autodelete_secs = int(args[1])
                _sync(_autodelete_secs=S._autodelete_secs)
                await message.edit(content=S.ui_ok(f"autodelete {args[1]}s"))
            else:
                await message.edit(content=S.ui_err("usage: autodelete <secs> | off"))