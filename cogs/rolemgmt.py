# cogs/rolemgmt.py | full role introspection — list, members, perms, hierarchy, colour, etc.
import modifyself_shim as discord
from . import state as S


def _find_role(guild, ref: str):
    if guild is None or not ref:
        return None
    ref = ref.strip().strip("<@&>")
    if ref.isdigit():
        rid = int(ref)
        for r in guild.roles:
            if r.id == rid:
                return r
        return None
    low = ref.lower()
    for r in guild.roles:
        if r.name.lower() == low:
            return r
    for r in guild.roles:
        if low in r.name.lower():
            return r
    return None


class RoleMgmtCog:
    COMMANDS = {
        "roleinfo", "rolelist", "roleperms", "rolemembers",
        "rolehierarchy", "roleposition", "rolecolor", "roleid",
        "rolementionable", "rolehoisted", "rolemanaged", "rolecreated",
    }

    async def handle(self, message, cmd, args):
        g = getattr(message, "guild", None)
        if g is None:
            return await message.edit(content=S.ui_err("server only"))
        if cmd == "rolelist":
            return await self._rolelist(message, g)
        if cmd == "rolehierarchy":
            return await self._hierarchy(message, g)
        if cmd == "roleid":
            return await self._roleid(message, g, args)
        if len(args) < 2:
            return await message.edit(content=S.ui_err(f"usage: {cmd} <role_id|name>"))
        role = _find_role(g, args[1])
        if role is None:
            return await message.edit(content=S.ui_err("role not found"))

        if cmd == "roleinfo":
            return await self._roleinfo(message, role)
        if cmd == "roleperms":
            return await self._perms(message, role)
        if cmd == "rolemembers":
            return await self._members(message, role)
        if cmd == "roleposition":
            return await message.edit(content=S.ui_ok(
                f"`{role.name}` position {role.position}"))
        if cmd == "rolecolor":
            return await self._color(message, role, args)
        if cmd == "rolementionable":
            return await message.edit(content=S.ui_ok(
                f"`{role.name}` mentionable: {bool(getattr(role, 'mentionable', False))}"))
        if cmd == "rolehoisted":
            return await message.edit(content=S.ui_ok(
                f"`{role.name}` hoisted: {bool(getattr(role, 'hoist', False))}"))
        if cmd == "rolemanaged":
            return await message.edit(content=S.ui_ok(
                f"`{role.name}` managed: {bool(getattr(role, 'managed', False))}"))
        if cmd == "rolecreated":
            try:
                ts = role.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                ts = "?"
            return await message.edit(content=S.ui_ok(f"`{role.name}` created {ts}"))

    async def _roleinfo(self, message, role):
        color = getattr(getattr(role, "color", None), "value", 0) or 0
        try:
            created = role.created_at.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            created = "?"
        rows = [
            f"  {S.DIM}name{S.RESET}         {role.name}",
            f"  {S.DIM}id{S.RESET}           {role.id}",
            f"  {S.DIM}color{S.RESET}        #{color:06x}",
            f"  {S.DIM}position{S.RESET}     {role.position}",
            f"  {S.DIM}mentionable{S.RESET}  {bool(getattr(role,'mentionable',False))}",
            f"  {S.DIM}hoisted{S.RESET}      {bool(getattr(role,'hoist',False))}",
            f"  {S.DIM}managed{S.RESET}      {bool(getattr(role,'managed',False))}",
            f"  {S.DIM}created{S.RESET}      {created}",
        ]
        await message.edit(content=S.ui_box("role info", rows))

    async def _perms(self, message, role):
        perms = getattr(role, "permissions", None)
        if perms is None:
            return await message.edit(content=S.ui_info("permissions unavailable"))
        try:
            names = [n for n, v in iter(perms) if v]
        except Exception:
            try:
                names = [n for n in dir(perms) if not n.startswith("_") and getattr(perms, n) is True]
            except Exception:
                names = []
        if not names:
            return await message.edit(content=S.ui_info(f"`{role.name}` has no explicit perms"))
        rows = [f"  {S.GREY}•{S.RESET} {n}" for n in names]
        await message.edit(content=S._paginate(f"perms — {role.name}", f"{len(names)}", rows))

    async def _members(self, message, role):
        members = []
        try:
            members = [m for m in role.members]
        except Exception:
            pass
        if not members:
            guild_members = list(getattr(message.guild, "members", []) or [])
            for m in guild_members:
                try:
                    if role in (m.roles or []):
                        members.append(m)
                except Exception:
                    pass
        if not members:
            return await message.edit(content=S.ui_info(f"no cached members with `{role.name}`"))
        rows = [f"  {S.GREY}•{S.RESET} {getattr(m,'display_name',str(m))}  "
                f"{S.DIM}({getattr(m,'id','?')}){S.RESET}" for m in members[:80]]
        await message.edit(content=S._paginate(f"members — {role.name}", f"{len(members)}", rows))

    async def _rolelist(self, message, guild):
        roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
        rows = []
        for r in roles:
            color = getattr(getattr(r, "color", None), "value", 0) or 0
            rows.append(f"  {S.GREY}{r.position:>3}.{S.RESET} "
                        f"{S.WHITE}{r.name}{S.RESET}  "
                        f"{S.DIM}#{color:06x}  {r.id}{S.RESET}")
        await message.edit(content=S._paginate("roles", guild.name, rows))

    async def _hierarchy(self, message, guild):
        roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
        rows = []
        for i, r in enumerate(roles):
            indent = "  " * min(i, 10)
            rows.append(f"  {indent}{S.GREY}└{S.RESET} {r.name} {S.DIM}({r.position}){S.RESET}")
        await message.edit(content=S._paginate("hierarchy", guild.name, rows))

    async def _roleid(self, message, guild, args):
        if len(args) < 2:
            return await message.edit(content=S.ui_err("usage: roleid <name>"))
        role = _find_role(guild, args[1])
        if not role:
            return await message.edit(content=S.ui_err("role not found"))
        await message.edit(content=S.ui_ok(f"{role.name} → {role.id}"))

    async def _color(self, message, role, args):
        if len(args) >= 3:
            hexv = args[2].lstrip("#")
            try:
                val = int(hexv, 16)
            except ValueError:
                return await message.edit(content=S.ui_err("hex like ff00aa"))
            try:
                await role.edit(color=discord.Color(val))
                return await message.edit(content=S.ui_ok(f"`{role.name}` → #{hexv:0>6}"))
            except Exception as e:
                return await message.edit(content=S.ui_err(str(e)))
        color = getattr(getattr(role, "color", None), "value", 0) or 0
        await message.edit(content=S.ui_ok(f"`{role.name}` colour #{color:06x}"))