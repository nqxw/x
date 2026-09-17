# selfbot

discord selfbot with quest completer, nitro sniper, rich presence, message logger, auto-responder.

## setup

1. open `config.json`
2. paste your user token into `"token"`
3. run `start.bat` (windows) or `start.sh` (linux/mac)

### get your token
open discord in browser → F12 → console → paste:
```
webpackChunkdiscord_app.push([[Math.random()],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]);m.find(m=>m?.exports?.default?.getToken!==void 0).exports.default.getToken()
```

---

## commands

### core
| command | description |
|---------|-------------|
| `.ping` | latency check |
| `.purge <n>` | delete your last n messages |
| `.say <text>` | replace command with text |
| `.spam <n> <text>` | send n messages |
| `.status <text>` | set custom status |
| `.clear` | delete command message |
| `.info` | account snapshot |
| `.copycat <user_id>` | mirror next 10 messages from user |
| `.sniper on/off` | toggle nitro sniper |
| `.logger on/off` | toggle message logger |
| `.readlog <n>` | read last n lines of log |
| `.ar add trigger \| response` | add auto-response |
| `.ar remove trigger` | remove auto-response |
| `.ar list` | list auto-responses |

### quests
| command | description |
|---------|-------------|
| `.quest` | list active quests with progress |
| `.questrun <index>` | solve a specific quest |
| `.questall` | solve all active quests simultaneously |
| `.autoquest on/off` | background autoquest on startup |
| `.orbbadge` | claim orb badge |

### rpc
| command | description |
|---------|-------------|
| `.rpc help` | full rpc command list |
| `.rpc enable/disable` | toggle rich presence |
| `.rpc type` | set activity type |
| `.rpc name/details/state` | set display fields |
| `.rpc start/end` | set timestamps |
| `.rpc large_image/large_text` | set large asset |
| `.rpc small_image/small_text` | set small asset |
| `.rpc button1_name/button1_url` | set button 1 |
| `.rpc button2_name/button2_url` | set button 2 |
| `.rpc party enable/disable/current/max` | party config |
| `.rpc status` | current rpc snapshot |

---

## files
```
selfbot/
├── selfbot.py          ← main bot
├── config.json         ← token + settings
├── requirements.txt    ← dependencies
├── start.bat           ← windows launcher
├── start.sh            ← linux/mac launcher
├── message_log.txt     ← auto-created, message log
├── config/
│   └── rpc_config.json ← rich presence settings
└── database/           ← reserved for future data
```

## notes
- edit `config/rpc_config.json` directly for bulk rpc changes
- `message_log.txt` is created automatically when logger is on
- prefix is `.` by default, change `PREFIX` in `selfbot.py` line 16
