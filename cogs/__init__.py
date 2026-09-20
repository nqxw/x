# cogs/__init__.py | registry — selfbot.py imports ALL_COGS and loads in order
from . import (
    quests, host, voice, mass, nuke, scrape, webhooks,
    automod, monitor, backup, perms, scheduler, db,
    lastfm, social, status,
    agc, gc, triggers, tasks,
    guards, resilience, settings,
    general, fun, tools, utility, tracking, downloads,
    auto, profile, developer, server, information, interactions,
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
]

def build_registry():
    registry = {}
    instances = []
    for cls in ALL_COGS:
        inst = cls()
        instances.append(inst)
        for cmd in getattr(inst, "COMMANDS", set()):
            registry[cmd] = (inst, cmd)
    return registry, instances

def register_events(client, instances):
    for inst in instances:
        if hasattr(inst, "register"):
            try:
                inst.register(client)
                print(f"[cogs] events registered — {inst.__class__.__name__}")
            except Exception as e:
                print(f"[cogs] event register failed — {inst.__class__.__name__}: {e}")
