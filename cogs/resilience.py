# cogs/resilience.py | auto-reconnect, session log, rate-limit tracking, cache, queue
import asyncio
import time
from datetime import datetime
import modifyself_shim as discord
from . import state as S


async def _queue_worker(name):
    while True:
        try:
            item = await S._cmd_queue.get()
            if item is None:
                await asyncio.sleep(0.05); continue
            try: await item
            except Exception as e: print(f"[queue:{name}] {e}")
            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[queue worker] {e}")
            await asyncio.sleep(1)


async def _cache_cleanup_loop():
    while True:
        try:
            if S._cache_auto:
                now = time.time()
                for cache in (S._snipe_cache, S._editsnipe_cache):
                    for cid in list(cache.keys()):
                        cache[cid] = [e for e in cache[cid]
                                      if now - float(e.get("ts", now)) < 3600]
                        if not cache[cid]:
                            cache.pop(cid, None)
                for cid in list(S._typing_tasks.keys()):
                    if S._typing_tasks[cid].done():
                        S._typing_tasks.pop(cid, None)
                for uid in list(S._tracking.keys()):
                    if len(S._tracking[uid]) > 200:
                        S._tracking[uid] = S._tracking[uid][-200:]
                if len(S._session_events) > 200:
                    del S._session_events[:-200]
                if len(S._rate_limit_events) > 200:
                    del S._rate_limit_events[:-200]
            await asyncio.sleep(600)
        except Exception as e:
            print(f"[cache] {e}")
            await asyncio.sleep(60)


class ResilienceCog:
    COMMANDS = {"autoreconnect", "autorestart", "sessionmon", "sessions",
                "ratelimit", "ratelimits",
                "cache", "queue"}

    def register(self, client):
        @client.event
        async def on_disconnect():
            S._reconnect_count += 1
            S._session_events.append({"ts": time.time(), "event": "disconnect",
                                       "count": S._reconnect_count})
            if S._auto_reconnect:
                print(f"[ws] disconnected — auto-reconnect attempt #{S._reconnect_count}")

        @client.event
        async def on_resumed():
            S._session_events.append({"ts": time.time(), "event": "resumed"})

    async def handle(self, message, cmd, args):
        if cmd == "autoreconnect":
            S._auto_reconnect = ((args[1].lower() in ("on", "enable"))
                                 if len(args) > 1 else not S._auto_reconnect)
            await message.edit(content=S.ui_ok(
                f"autoreconnect → {'on' if S._auto_reconnect else 'off'}"))

        elif cmd == "autorestart":
            S._auto_restart = ((args[1].lower() in ("on", "enable"))
                               if len(args) > 1 else not S._auto_restart)
            await message.edit(content=S.ui_ok(
                f"autorestart → {'on' if S._auto_restart else 'off'}"))

        elif cmd == "sessionmon":
            enabled = (args[1].lower() in ("on", "enable")) if len(args) > 1 else True
            await message.edit(content=S.ui_ok(
                f"session monitor → {'on' if enabled else 'off'}"))

        elif cmd == "sessions":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "clear":
                S._session_events.clear()
                await message.edit(content=S.ui_ok("cleared"))
            else:
                rows = [f"  {S.DIM}{datetime.fromtimestamp(e['ts']).strftime('%H:%M:%S')}{S.RESET}  "
                        f"{e['event']}  "
                        f"{S.DIM}{e.get('user','') or ''}{e.get('count','') or ''}{S.RESET}"
                        for e in S._session_events[-30:]]
                await message.edit(content=S._paginate("sessions", "recent events", rows)
                                            if rows else S.ui_info("none"))

        elif cmd == "ratelimit":
            S._rate_limit_tracking = ((args[1].lower() in ("on", "enable"))
                                      if len(args) > 1 else not S._rate_limit_tracking)
            await message.edit(content=S.ui_ok(
                f"ratelimit tracking → {'on' if S._rate_limit_tracking else 'off'}"))

        elif cmd == "ratelimits":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "clear":
                S._rate_limit_events.clear()
                await message.edit(content=S.ui_ok("cleared"))
            else:
                rows = [f"  {S.DIM}{datetime.fromtimestamp(e['ts']).strftime('%H:%M:%S')}{S.RESET}  "
                        f"wait={e.get('wait')}s  {e.get('url','')[:40]}"
                        for e in S._rate_limit_events[-30:]]
                await message.edit(content=S._paginate("rate-limits", "recent", rows)
                                            if rows else S.ui_info("none"))

        elif cmd == "cache":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "stats":
                await message.edit(content=S.ui_box("cache", [
                    f"  {S.DIM}snipe{S.RESET}     {sum(len(v) for v in S._snipe_cache.values())}",
                    f"  {S.DIM}editsnipe{S.RESET} {sum(len(v) for v in S._editsnipe_cache.values())}",
                    f"  {S.DIM}typing{S.RESET}    {len(S._typing_tasks)}",
                    f"  {S.DIM}tracking{S.RESET}  {sum(len(v) for v in S._tracking.values())}",
                    f"  {S.DIM}sessions{S.RESET}  {len(S._session_events)}",
                    f"  {S.DIM}ratelimits{S.RESET} {len(S._rate_limit_events)}",
                    f"  {S.DIM}auto-clean{S.RESET} {S._cache_auto}",
                ]))
            elif sub == "clean":
                S._snipe_cache.clear()
                S._editsnipe_cache.clear()
                for cid in list(S._typing_tasks.keys()):
                    try: S._typing_tasks[cid].cancel()
                    except Exception: pass
                S._typing_tasks.clear()
                S._tracking.clear()
                S._session_events.clear()
                S._rate_limit_events.clear()
                await message.edit(content=S.ui_ok("caches cleared"))
            elif sub == "auto":
                S._cache_auto = ((args[2].lower() in ("on", "enable"))
                                 if len(args) > 2 else not S._cache_auto)
                await message.edit(content=S.ui_ok(
                    f"cache auto → {'on' if S._cache_auto else 'off'}"))
            else:
                await message.edit(content=S.ui_info("usage: cache stats/clean/auto"))

        elif cmd == "queue":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "on":
                S._queue_enabled = True
                await message.edit(content=S.ui_ok("queue on"))
            elif sub == "off":
                S._queue_enabled = False
                await message.edit(content=S.ui_ok("queue off"))
            elif sub == "workers" and len(args) >= 3 and args[2].isdigit():
                S._queue_workers = max(1, int(args[2]))
                await message.edit(content=S.ui_ok(f"workers → {S._queue_workers}"))
            elif sub == "status":
                await message.edit(content=S.ui_box("queue", [
                    f"  {S.DIM}enabled{S.RESET}  {S._queue_enabled}",
                    f"  {S.DIM}workers{S.RESET}  {S._queue_workers}",
                    f"  {S.DIM}pending{S.RESET}  {S._cmd_queue.qsize() if S._cmd_queue else 0}",
                ]))
            else:
                await message.edit(content=S.ui_info("usage: queue on/off/workers/status"))