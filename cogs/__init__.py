# cogs/__init__.py | registry — selfbot.py imports ALL_COGS and loads in order
from . import (
    quests, host, voice, mass, nuke, scrape, webhooks,
    automod, monitor, backup, perms, scheduler, db,
    lastfm, social, status,
    agc, gc, triggers, tasks,
    guards, resilience, settings,
    general, fun, tools, utility, tracking, downloads,
    auto, profile, developer, server, information, interactions,
    spoofer, rpc,
)

ALL_COGS = [
    quests.QuestsCog,
    host.HostCog,
    voice.VoiceCog,
    mass.MassCog,
    nuke.NukeCog,
    scrape.ScrapeCog,
    webhooks.WebhooksCog,
    automod.AutomodCog,
    monitor.MonitorCog,
    backup.BackupCog,
    perms.PermsCog,
    scheduler.SchedulerCog,
    db.DbCog,
    lastfm.LastfmCog,
    social.SocialCog,
    status.StatusCog,
    agc.AgcCog,
    gc.GroupChatCog,
    triggers.TriggersCog,
    tasks.TasksCog,
    guards.GuardsCog,
    resilience.ResilienceCog,
    settings.SettingsCog,
    general.GeneralCog,
    fun.FunCog,
    tools.ToolsCog,
    utility.UtilityCog,
    tracking.TrackingCog,
    downloads.DownloadsCog,
    auto.AutoCog,
    profile.ProfileCog,
    developer.DeveloperCog,
    server.ServerCog,
    information.InformationCog,
    interactions.InteractionsCog,
    # ── adapters last so they win command-name collisions ──
    spoofer.SpooferCog,
    rpc.RPCCog,
]

def build_registry(client=None):
    """Build the command registry. `client` is optional — cogs that need it
    pull from cogs.state.CLIENT via their own __init__ fallback. Passing it
    here is the explicit path and takes priority when a cog accepts a bot."""
    registry = {}
    instances = []
    for cls in ALL_COGS:
        inst = None
        # try with client first (RPC-style cogs take an optional bot arg)
        if client is not None:
            try:
                inst = cls(client)
            except TypeError:
                inst = None
            except Exception as e:
                print(f"[cogs] {cls.__name__} init failed with client: {e}")
                inst = None
        # fall back to no-arg init (every other cog)
        if inst is None:
            try:
                inst = cls()
            except Exception as e:
                print(f"[cogs] {cls.__name__} init failed: {e}")
                continue
        instances.append(inst)
        for cmd in getattr(inst, "COMMANDS", set()):
            registry[cmd] = (inst, cmd)
    print(f"[cogs] registry: {len(instances)} cogs, {len(registry)} commands")
    return registry, instances

def register_events(client, instances):
    for inst in instances:
        if hasattr(inst, "register"):
            try:
                inst.register(client)
                print(f"[cogs] events registered — {inst.__class__.__name__}")
            except Exception as e:
                print(f"[cogs] event register failed — {inst.__class__.__name__}: {e}")
