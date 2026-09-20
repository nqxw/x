# modifyself_compat.py | adapter — exposes modifyself through the discord.py-self surface
# Do NOT edit until modifyself's API is confirmed. Placeholder structure only.

"""
Expected shape after you paste the modifyself API:

    import modifyself as _ms

    # If modifyself ships a Client class with the same method names:
    Client          = _ms.Client
    GroupChannel    = _ms.GroupChannel
    DMChannel       = _ms.DMChannel
    TextChannel     = _ms.TextChannel
    Activity        = _ms.Activity
    ActivityType    = _ms.ActivityType
    Status          = _ms.Status
    CustomActivity  = _ms.CustomActivity
    Game            = _ms.Game
    File            = _ms.File
    Color           = _ms.Color
    Permissions     = _ms.Permissions
    LoginFailure    = _ms.LoginFailure
    ui              = _ms.ui
    utils           = _ms.utils

    # then in selfbot.py, replace:
    #   import discord
    # with:
    #   import modifyself_compat as discord

Any of those attributes that modifyself does NOT have would need a shim
written here once we see its actual surface.
"""
