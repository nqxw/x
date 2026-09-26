# cogs/autoresponder.py | extended AR — keyword, regex, time-based, randomized, per-scope
import re
import time
import random
from datetime import datetime
from uuid import uuid4
import modifyself_shim as discord
from . import state as S


def _sub_vars(text, message):
    for var, fn in S.ar_variables.items():
        try:
            text = text.replace(var, str(fn(message)))
        except Exception:
            pass
    return text


def _scope_match(rule, message):
    sc = rule.get("scope", {})
    if not sc:
        return True
    if sc.get("users") and message.author.id not in sc["users"]:
        return False
    if sc.get("channels") and message.channel.id not in sc["channels"]:
        return False
    if sc.get("servers"):
        gid = getattr(getattr(message, "guild", None), "id", None)
        if gid not in sc["servers"]:
            return False
    if sc.get("time_range"):
        lo, hi = sc["time_range"]
        h = datetime.now().hour
        if not (lo <= h < hi):
            return False
    return True


async def _ar_pre_hook(client, message):
    if message.author.id == client.user.id:
        return
    if not message.content:
        return
    if message.content.startswith(S.PREFIX):
        return
    now = time.time()
    low = message.content.lower()
    for rule in list(S.ar_rules):
        if rule.get("cooldown") and now - rule.get("last", 0) < rule["cooldown"]:
            continue
        if not _scope_match(rule, message):
            continue
        hit = False
        kind = rule.get("kind", "keyword")
        if kind == "keyword":
            for kw in rule.get("patterns", []):
                if kw.lower() in low:
                    hit = True; break
        elif kind == "regex":
            for pat in rule.get("patterns", []):
                try:
                    if re.search(pat, message.content):
                        hit = True; break
                except re.error:
                    pass
        elif kind == "any":
            hit = True
        if not hit:
            continue
        rule["last"] = now
        responses = rule.get("responses") or [rule.get("reply", "")]
        reply = random.choice(responses) if responses else ""
        reply = _sub_vars(reply, message)
        try:
            await message.channel.send(reply)
        except Exception:
            pass
        break


class AutoResponderCog:
    COMMANDS = {"arule", "arules", "aruleclear", "arvars", "artest"}

    def register(self, client):
        S.register_pre_hook(_ar_pre_hook)

    async def handle(self, message, cmd, args):
        if cmd == "arvars":
            return await message.edit(content=S.ui_box("AR variables", [
                f"  {S.DIM}{k}{S.RESET}  →  dynamic" for k in S.ar_variables.keys()
            ]))

        if cmd == "artest":
            if len(args) < 2:
                return await message.edit(content=S.ui_err("usage: artest <text>"))
            text = " ".join(args[1:])
            subbed = _sub_vars(text, message)
            return await message.edit(content=S.ui_box("artest", [
                f"  {S.DIM}raw{S.RESET}  {text}",
                f"  {S.DIM}out{S.RESET}  {subbed}",
            ]))

        if cmd == "arule":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "add":
                if len(args) < 5:
                    return await message.edit(content=S.ui_err(
                        "usage: arule add <kind> <pattern> | <reply> [| reply2 ...]"))
                kind = args[2].lower()
                if kind not in ("keyword", "regex", "any"):
                    return await message.edit(content=S.ui_err("kind: keyword | regex | any"))
                rest = " ".join(args[3:])
                if "|" not in rest:
                    return await message.edit(content=S.ui_err("use | to separate patterns from replies"))
                pat_part, rep_part = rest.split("|", 1)
                patterns = [p.strip() for p in pat_part.split(",") if p.strip()]
                replies = [r.strip() for r in rep_part.split("|") if r.strip()]
                if not patterns or not replies:
                    return await message.edit(content=S.ui_err("missing patterns or replies"))
                rule = {
                    "id": str(uuid4())[:8],
                    "kind": kind,
                    "patterns": patterns,
                    "responses": replies,
                    "reply": replies[0],
                    "scope": {},
                    "cooldown": 2,
                    "last": 0.0,
                }
                S.ar_rules.append(rule)
                return await message.edit(content=S.ui_ok(
                    f"rule added `{rule['id']}` ({kind}, {len(patterns)} patterns, {len(replies)} replies)"))
            if sub == "remove" and len(args) >= 3:
                before = len(S.ar_rules)
                S.ar_rules[:] = [r for r in S.ar_rules if r["id"] != args[2]]
                return await message.edit(content=S.ui_ok("removed") if len(S.ar_rules) < before
                                                    else S.ui_err("id not found"))
            if sub == "scope" and len(args) >= 4:
                rule = next((r for r in S.ar_rules if r["id"] == args[2]), None)
                if not rule:
                    return await message.edit(content=S.ui_err("id not found"))
                kind = args[3].lower()
                if kind == "user" and len(args) >= 5:
                    rule["scope"].setdefault("users", set()).add(int(args[4]))
                elif kind == "channel" and len(args) >= 5:
                    rule["scope"].setdefault("channels", set()).add(int(args[4]))
                elif kind == "server" and len(args) >= 5:
                    rule["scope"].setdefault("servers", set()).add(int(args[4]))
                elif kind == "clear":
                    rule["scope"] = {}
                else:
                    return await message.edit(content=S.ui_err("scope: user/channel/server/clear"))
                return await message.edit(content=S.ui_ok("scope updated"))
            if sub == "cooldown" and len(args) >= 4 and args[3].isdigit():
                rule = next((r for r in S.ar_rules if r["id"] == args[2]), None)
                if not rule:
                    return await message.edit(content=S.ui_err("id not found"))
                rule["cooldown"] = int(args[3])
                return await message.edit(content=S.ui_ok(f"cooldown → {args[3]}s"))
            return await message.edit(content=S.ui_info(
                "usage: arule add/remove/scope/cooldown"))

        if cmd == "arules":
            if not S.ar_rules:
                return await message.edit(content=S.ui_info("no rules"))
            rows = [f"  {S.GREY}{r['id']}{S.RESET} {S.WHITE}{r['kind']:<8}{S.RESET} "
                    f"pat:{len(r.get('patterns',[]))}  rep:{len(r.get('responses',[]))}  "
                    f"cd:{r.get('cooldown',0)}s"
                    for r in S.ar_rules[:40]]
            return await message.edit(content=S._paginate("ar rules", f"{len(S.ar_rules)}", rows))

        if cmd == "aruleclear":
            n = len(S.ar_rules)
            S.ar_rules.clear()
            await message.edit(content=S.ui_ok(f"cleared {n} rules"))