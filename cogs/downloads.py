# cogs/downloads.py | yt-dlp wrapper — video/audio downloads from yt/tiktok/instagram
import asyncio
import glob
import os
import modifyself_shim as discord
from uuid import uuid4
from . import state as S


class DownloadsCog:
    COMMANDS = {"yt", "youtube", "ytaudio", "tiktok", "tt", "instagram", "ig"}

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass
        if len(args) < 2:
            return await message.channel.send(S.ui_err(f"usage: {cmd} <url>"), delete_after=5)
        url = args[1]
        audio_only = cmd in ("ytaudio",)
        flags = (["--extract-audio", "--audio-format", "mp3"] if audio_only
                 else ["-f", "best[filesize<25M]"])
        try:
            await message.channel.send(S.ui_info(f"downloading {url}..."), delete_after=5)
            fname = f"/tmp/dl_{uuid4().hex[:8]}.%(ext)s"
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp", url, "-o", fname, *flags,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
            files = glob.glob("/tmp/dl_*")
            if files:
                latest = max(files, key=os.path.getctime)
                if os.path.getsize(latest) < 25 * 1024 * 1024:
                    await message.channel.send(file=discord.File(latest))
                    os.remove(latest)
                else:
                    await message.channel.send(S.ui_err("too large >25MB"), delete_after=8)
                    os.remove(latest)
            else:
                await message.channel.send(
                    S.ui_err(f"failed: {err.decode()[:200]}"), delete_after=8)
        except asyncio.TimeoutError:
            await message.channel.send(S.ui_err("timed out"), delete_after=8)
        except FileNotFoundError:
            await message.channel.send(S.ui_err("yt-dlp not installed"), delete_after=8)
        except Exception as e:
            await message.channel.send(S.ui_err(str(e)[:200]), delete_after=8)