# cogs/information.py | userinfo, avatar, whois, checkname, channelinfo
import aiohttp
import modifyself_shim as discord
from . import state as S


def _avatar_url(uid, u):
    if not u.get("avatar"):
        return "none"
    ext = "gif" if str(u["avatar"]).startswith("a_") else "webp"
    return f"https://cdn.discordapp.com/avatars/{uid}/{u['avatar']}.{ext}?size=512"


def _banner_url(uid, u):
    if not u.get("banner"):
        return "none"
    ext = "gif" if str(u["banner"]).startswith("a_") else "webp"
    return f"https://cdn.discordapp.com/banners/{uid}/{u['banner']}.{ext}?size=600"


class InformationCog:
    COMMANDS = {"userinfo", "avatar", "checkname", "whois", "channelinfo"}

    async def handle(self, message, cmd, args):
        client = S.CLIENT

        if cmd == "userinfo":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/users/{uid}",
                                     headers=h) as r:
                        if r.status != 200:
                            return await message.edit(content=S.ui_err(f"not found ({r.status})"))
                        u = await r.json()
                created = "?"
                if u.get("id"):
                    try:
                        created = discord.utils.snowflake_time(int(u["id"])).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass
                rows = [
                    f"  {S.DIM}username{S.RESET}  {u.get('username','?')}",
                    f"  {S.DIM}global{S.RESET}    {u.get('global_name') or '-'}",
                    f"  {S.DIM}id{S.RESET}        {uid}",
                    f"  {S.DIM}avatar{S.RESET}    {_avatar_url(uid, u)}",
                    f"  {S.DIM}banner{S.RESET}    {_banner_url(uid, u)}",
                    f"  {S.DIM}bot{S.RESET}       {'yes' if u.get('bot') else 'no'}",
                    f"  {S.DIM}flags{S.RESET}     {u.get('public_flags', 0)}",
                    f"  {S.DIM}created{S.RESET}   {created}",
                ]
                await message.edit(content=S.ui_box("user info", rows))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "avatar":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/users/{uid}",
                                     headers=h) as r:
                        if r.status != 200:
                            return await message.edit(content=S.ui_err(f"not found ({r.status})"))
                        u = await r.json()
                url = _avatar_url(uid, u)
                if url == "none":
                    return await message.edit(content=S.ui_info("no avatar"))
                await message.edit(content=url)
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "checkname":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: checkname <username>"))
            username = args[1].lower().strip()
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "Content-Type": "application/json",
                         "User-Agent": S.USER_AGENT}
                    async with s.post("https://discord.com/api/v9/users/@me/pomelo-attempt",
                                      headers=h, json={"username": username}) as r:
                        if r.status == 200:
                            taken = (await r.json()).get("taken", True)
                            await message.edit(content=(
                                S.ui_ok(f"`{username}` available") if not taken
                                else S.ui_err(f"`{username}` taken")))
                        else:
                            await message.edit(content=S.ui_err(f"check failed {r.status}"))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "whois":
            uid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.author.id
            try:
                async with aiohttp.ClientSession() as s:
                    h = {"Authorization": S.TOKEN, "User-Agent": S.USER_AGENT}
                    async with s.get(f"https://discord.com/api/v9/users/{uid}/profile",
                                     headers=h) as r:
                        if r.status != 200:
                            return await message.edit(content=S.ui_err(f"not found ({r.status})"))
                        p = await r.json()
                u = p.get("user", {}) or {}
                prof = p.get("user_profile", {}) or {}
                badges = [b.get("id", "") for b in p.get("badges", []) if isinstance(b, dict)]
                connected = p.get("connected_accounts", []) or []
                rows = [
                    f"  {S.DIM}username{S.RESET}       {u.get('username','?')}",
                    f"  {S.DIM}id{S.RESET}             {uid}",
                    f"  {S.DIM}bio{S.RESET}            {prof.get('bio','') or '-'}",
                    f"  {S.DIM}pronouns{S.RESET}       {prof.get('pronouns','') or '-'}",
                    f"  {S.DIM}badges{S.RESET}         {', '.join(badges) or 'none'}",
                    f"  {S.DIM}connected{S.RESET}      {len(connected)}",
                    f"  {S.DIM}nitro{S.RESET}          {'yes' if p.get('premium_since') else 'no'}",
                    f"  {S.DIM}mutual servers{S.RESET} {len(p.get('mutual_guilds', []) or [])}",
                    f"  {S.DIM}mutual friends{S.RESET} {len(p.get('mutual_friends', []) or [])}",
                ]
                await message.edit(content=S.ui_box("whois", rows))
            except Exception as e:
                await message.edit(content=S.ui_err(str(e)))

        elif cmd == "channelinfo":
            cid = int(args[1]) if len(args) > 1 and args[1].isdigit() else message.channel.id
            ch = client.get_channel(cid) if client else None
            if not ch:
                return await message.edit(content=S.ui_err("not in cache"))
            g = getattr(ch, "guild", None)
            ctype = getattr(ch, "type", None)
            try: ctype_str = str(ctype).replace("ChannelType.", "")
            except Exception: ctype_str = str(ctype)
            rows = [
                f"  {S.DIM}name{S.RESET}     #{getattr(ch, 'name', cid)}",
                f"  {S.DIM}id{S.RESET}       {ch.id}",
                f"  {S.DIM}type{S.RESET}     {ctype_str}",
            ]
            try:
                rows.append(f"  {S.DIM}created{S.RESET}  {ch.created_at.strftime('%Y-%m-%d %H:%M')}")
            except Exception:
                pass
            rows.append(f"  {S.DIM}guild{S.RESET}    {getattr(g, 'name', 'DM') if g else 'DM'}")
            await message.edit(content=S.ui_box("channel info", rows))