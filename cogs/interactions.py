# cogs/interactions.py | buttons, modals, verify button handler, pending queue
import discord
from . import state as S


class InteractionsCog:
    COMMANDS = {"buttons", "modals", "interact"}

    def register(self, client):
        @client.event
        async def on_interaction(interaction):
            try:
                tname = getattr(interaction.type, "name", str(interaction.type))
                if tname == "component" and S._buttons_enabled:
                    cid = interaction.data.get("custom_id", "") if interaction.data else ""
                    if cid.startswith("verify_") and S._verify_cfg["role_id"]:
                        member = interaction.user
                        guild = interaction.guild
                        role = (guild.get_role(int(S._verify_cfg["role_id"]))
                                if guild else None)
                        if role and member:
                            try: await member.add_roles(role)
                            except Exception: pass
                            try:
                                await interaction.response.send_message(
                                    "verified.", ephemeral=True)
                            except Exception: pass
                elif tname == "modal_submit" and S._modals_enabled:
                    S._pending_interactions.append(interaction)
            except Exception as e:
                print(f"[interaction] {e}")

    async def handle(self, message, cmd, args):
        if cmd == "buttons":
            S._buttons_enabled = ((args[1].lower() in ("on", "enable"))
                                  if len(args) > 1 else not S._buttons_enabled)
            await message.edit(content=S.ui_ok(
                f"buttons → {'on' if S._buttons_enabled else 'off'}"))

        elif cmd == "modals":
            S._modals_enabled = ((args[1].lower() in ("on", "enable"))
                                 if len(args) > 1 else not S._modals_enabled)
            await message.edit(content=S.ui_ok(
                f"modals → {'on' if S._modals_enabled else 'off'}"))

        elif cmd == "interact":
            sub = args[1].lower() if len(args) > 1 else ""
            if sub == "list":
                rows = [f"  {S.GREY}•{S.RESET} {i}" for i in S._pending_interactions[-20:]]
                await message.edit(content=S._paginate("pending interactions", "", rows)
                                            if rows else S.ui_info("none"))
            elif sub == "clear":
                S._pending_interactions.clear()
                await message.edit(content=S.ui_ok("cleared"))
            else:
                await message.edit(content=S.ui_info("usage: interact list/clear"))