# cogs/automod.py | automod words/actions, raidmode, quarantine, ticket, verify
from . import state as S


class AutomodCog:
    COMMANDS = {"automod", "raidmode", "quarantine", "ticket", "verify"}

    async def handle(self, message, cmd, args):
        try: await message.delete()
        except Exception: pass
        sub = args[1].lower() if len(args) > 1 else ""

        if cmd == "automod":
            if sub == "on":
                S._automod["enabled"] = True
                await message.channel.send(S.ui_ok("automod on"))
            elif sub == "off":
                S._automod["enabled"] = False
                await message.channel.send(S.ui_ok("automod off"))
            elif sub == "add" and len(args) >= 3:
                S._automod["words"].append(args[2])
                await message.channel.send(S.ui_ok(f"added `{args[2]}`"))
            elif sub == "remove" and len(args) >= 3:
                S._automod["words"] = [w for w in S._automod["words"]
                                       if w.lower() != args[2].lower()]
                await message.channel.send(S.ui_ok(f"removed `{args[2]}`"))
            elif sub == "list":
                rows = [f"  {S.GREY}•{S.RESET} {w}" for w in S._automod["words"]]
                await message.channel.send(
                    S._paginate("automod words", "", rows) if rows else S.ui_info("none"))
            elif sub == "action" and len(args) >= 3:
                if args[2] not in ("delete","kick","ban","timeout"):
                    return await message.channel.send(
                        S.ui_err("action must be delete/kick/ban/timeout"), delete_after=5)
                S._automod["action"] = args[2]
                await message.channel.send(S.ui_ok(f"action → {args[2]}"))
            elif sub == "logs" and len(args) >= 3:
                S._automod["log_ch"] = args[2]
                await message.channel.send(S.ui_ok("logs set"))
            else:
                await message.channel.send(S.ui_info(
                    "usage: automod on/off/add/remove/list/action/logs"))

        elif cmd == "raidmode":
            if sub == "on":
                S._raidmode["enabled"] = True
                await message.channel.send(S.ui_ok("raidmode on"))
            elif sub == "off":
                S._raidmode["enabled"] = False
                await message.channel.send(S.ui_ok("raidmode off"))
            elif sub == "threshold" and len(args) >= 3 and args[2].isdigit():
                S._raidmode["threshold"] = int(args[2])
                await message.channel.send(S.ui_ok(f"threshold → {args[2]}/sec"))
            else:
                await message.channel.send(S.ui_info("usage: raidmode on/off/threshold <n>"))

        elif cmd == "quarantine":
            if sub == "on":
                S._quarantine["enabled"] = True
                await message.channel.send(S.ui_ok("quarantine on"))
            elif sub == "off":
                S._quarantine["enabled"] = False
                await message.channel.send(S.ui_ok("quarantine off"))
            elif sub == "role" and len(args) >= 3:
                S._quarantine["role_id"] = args[2]
                await message.channel.send(S.ui_ok("role set"))
            elif sub == "age" and len(args) >= 3 and args[2].isdigit():
                S._quarantine["age_days"] = int(args[2])
                await message.channel.send(S.ui_ok(f"age → {args[2]}d"))
            else:
                await message.channel.send(S.ui_info("usage: quarantine on/off/role <id>/age <days>"))

        elif cmd == "ticket":
            if sub == "setup" and len(args) >= 3:
                S._ticket_cfg["category_id"] = args[2]
                await message.channel.send(S.ui_ok("ticket category set"))
            elif sub == "close":
                import discord
                if isinstance(message.channel, discord.TextChannel):
                    try: await message.channel.delete()
                    except Exception as e: await message.channel.send(S.ui_err(str(e)))
            else:
                await message.channel.send(S.ui_info("usage: ticket setup <cat_id> | close"))

        elif cmd == "verify":
            if sub == "setup" and len(args) >= 3:
                S._verify_cfg["role_id"] = args[2]
                await message.channel.send(S.ui_ok("verified role set"))
            elif sub == "button":
                import discord
                label = " ".join(args[2:]) if len(args) > 2 else "Verify"
                try:
                    view = discord.ui.View()
                    view.add_item(discord.ui.Button(label=label, custom_id="verify_button",
                                                    style=discord.ButtonStyle.success))
                    await message.channel.send("click to verify", view=view)
                except Exception as e:
                    await message.channel.send(S.ui_err(f"button send failed: {e}"), delete_after=6)
            else:
                await message.channel.send(S.ui_info("usage: verify setup <role_id> | button [label]"))