# cogs/lastfm.py | last.fm now playing, recent, top, stats, compare
import aiohttp
from . import state as S


async def _lfm_get(method, params):
    p = {"method": method, "api_key": S._lfm.get("api_key",""), "format":"json", **params}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(S.LASTFM_BASE, params=p,
                                   timeout=aiohttp.ClientTimeout(total=8)) as r:
                return await r.json() if r.status == 200 else {}
    except Exception: return {}


async def _lfm_np(username):
    d = await _lfm_get("user.getRecentTracks", {"user": username, "limit": 1, "extended": 1})
    tracks = d.get("recenttracks", {}).get("track", [])
    if not tracks: return None
    t = tracks[0] if isinstance(tracks, list) else tracks
    artist = t.get("artist", {})
    return {"title": t.get("name","?"),
            "artist": artist.get("name","?") if isinstance(artist,dict) else str(artist),
            "album": (t.get("album",{}) or {}).get("#text",""),
            "playing": t.get("@attr",{}).get("nowplaying") == "true",
            "loved": t.get("loved","0") == "1",
            "total": d.get("recenttracks",{}).get("@attr",{}).get("total","?")}


def _lfm_load():
    cfg = S.load_config() or {}
    S._lfm.clear()
    S._lfm.update(cfg.get("lastfm", {}))


def _lfm_save():
    cfg = S.load_config() or {}
    cfg["lastfm"] = S._lfm
    S.save_config(cfg)


class LastfmCog:
    COMMANDS = {"lastfm"}

    async def handle(self, message, cmd, args):
        _lfm_load()
        sub = args[1].lower() if len(args) > 1 else ""
        try: await message.delete()
        except Exception: pass

        if not sub or sub == "help":
            await message.channel.send(S.ui_info(
                "usage: lastfm set/np/recent/topartists/toptracks/topalbums/stats/compare"))
            return

        if sub == "set":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: lastfm set <username> [api_key]"))
            S._lfm["username"] = args[2].strip()
            if len(args) > 3: S._lfm["api_key"] = args[3].strip()
            _lfm_save()
            await message.channel.send(S.ui_ok(f"linked {S._lfm['username']}"))
            return

        u = S._lfm.get("username","")
        if not u:
            return await message.channel.send(S.ui_err("set username first"), delete_after=8)

        if sub in ("np","nowplaying"):
            t = await _lfm_np(u)
            if not t:
                return await message.channel.send(S.ui_info("nothing"), delete_after=6)
            rows = [f"  {S.DIM}{'▶ now playing' if t['playing'] else '⏸ last played'}{S.RESET}", "",
                    f"  {S.WHITE}{t['title']}{S.RESET}", f"  {S.DIM}by{S.RESET} {t['artist']}"]
            if t["album"]: rows.append(f"  {S.DIM}album{S.RESET} {t['album']}")
            rows.append(f"  {S.DIM}scrobbles{S.RESET} {t['total']}")
            await message.channel.send(S._ansi_block(rows))

        elif sub == "recent":
            n = int(args[2]) if len(args) > 2 and args[2].isdigit() else 5
            d = await _lfm_get("user.getRecentTracks", {"user": u, "limit": min(n,15)})
            tracks = d.get("recenttracks", {}).get("track", [])
            if not tracks:
                return await message.channel.send(S.ui_info("no recent"), delete_after=6)
            rows = []
            for i, t in enumerate(tracks[:n], 1):
                title = t.get("name","?")
                artist = (t.get("artist",{}) or {}).get("#text","?") if isinstance(t.get("artist"),dict) else "?"
                now = " ▶" if t.get("@attr",{}).get("nowplaying") else ""
                rows.append(f"  {S.GREY}{i:2}.{S.RESET} {S.WHITE}{title}{S.RESET}  {S.DIM}— {artist}{now}{S.RESET}")
            await message.channel.send(S._paginate("recent", u, rows))

        elif sub in ("topartists","artists","toptracks","tracks","topalbums","albums"):
            period_raw = args[2].lower() if len(args) > 2 else "overall"
            period = S._PERIOD.get(period_raw, "overall"); label = S._PLABEL.get(period, "all time")
            mm = {"topartists":("user.getTopArtists","topartists","artist"),
                  "artists":("user.getTopArtists","topartists","artist"),
                  "toptracks":("user.getTopTracks","toptracks","track"),
                  "tracks":("user.getTopTracks","toptracks","track"),
                  "topalbums":("user.getTopAlbums","topalbums","album"),
                  "albums":("user.getTopAlbums","topalbums","album")}
            method, key, ik = mm[sub]
            d = await _lfm_get(method, {"user": u, "period": period, "limit": 10})
            items = d.get(key, {}).get(ik, [])
            if not items:
                return await message.channel.send(S.ui_info("no data"), delete_after=6)
            maxp = int(items[0].get("playcount", 1)) or 1
            rows = []
            for i, it in enumerate(items[:10], 1):
                name = it.get("name","?")
                plays = int(it.get("playcount", 0)); pct = int(plays / maxp * 100)
                bar = f"{S.GREEN}{'▓' * (pct // 10)}{S.GREY}{'░' * (10 - pct // 10)}{S.RESET}"
                extra = ""
                if "artist" in it and isinstance(it["artist"], dict):
                    extra = f"  {S.DIM}— {it['artist'].get('name','')}{S.RESET}"
                rows.append(f"  {S.GREY}{i:2}.{S.RESET} {bar} {S.DIM}{plays:>5}{S.RESET}  {S.WHITE}{name}{S.RESET}{extra}")
            await message.channel.send(S._paginate(sub, f"{u} — {label}", rows))

        elif sub == "stats":
            d = await _lfm_get("user.getInfo", {"user": u}); ud = d.get("user",{})
            if not ud:
                return await message.channel.send(S.ui_err("not found"), delete_after=6)
            await message.channel.send(S._ansi_block([f"  {S.WHITE}{u}{S.RESET}", "",
                f"  {S.DIM}scrobbles{S.RESET}  {ud.get('playcount','?')}",
                f"  {S.DIM}artists{S.RESET}    {ud.get('artist_count','?')}",
                f"  {S.DIM}albums{S.RESET}     {ud.get('album_count','?')}",
                f"  {S.DIM}tracks{S.RESET}     {ud.get('track_count','?')}",
                f"  {S.DIM}country{S.RESET}    {ud.get('country','?')}"]))

        elif sub == "compare":
            if len(args) < 3:
                return await message.channel.send(S.ui_err("usage: lastfm compare <user>"))
            other = args[2]
            d = await _lfm_get("tasteometer.compare",
                               {"type1":"user","type2":"user","value1":u,"value2":other,"limit":5})
            r = d.get("comparison",{}).get("result",{})
            score = float(r.get("score",0)) * 100
            artists = r.get("artists",{}).get("artist",[])
            if isinstance(artists, dict): artists = [artists]
            bar = f"{S.GREEN}{'▓'*int(score/10)}{S.GREY}{'░'*(10-int(score/10))}{S.RESET}"
            rows = [f"  {S.WHITE}{u}{S.RESET} vs {S.WHITE}{other}{S.RESET}",
                    f"  {bar}  {S.DIM}{score:.1f}% compatible{S.RESET}"]
            if artists:
                rows += ["", f"  {S.DIM}shared:{S.RESET}"] + [
                    f"    {S.GREY}•{S.RESET} {a.get('name','?') if isinstance(a,dict) else a}"
                    for a in artists[:5]
                ]
            await message.channel.send(S._ansi_block(rows))