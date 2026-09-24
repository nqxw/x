# cogs/__init__.py | package marker.
#
# cog modules are loaded by selfbot.py::_boot_cogs() via importlib.import_module
# against the COG_MODULES list. this file must NOT import any cog module at
# package-load time — a single failing cog would take down the whole package,
# _boot_cogs would catch the failure, and _COG_REGISTRY would be left empty,
# silencing every cog command.
