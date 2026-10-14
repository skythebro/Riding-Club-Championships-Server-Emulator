# Riding Club Championships Server Emulator

A private server for Riding Club Championships, so the game can be played after the official servers went away. You need a legitimate copy of the game. This is not affiliated with the original developers.

The feature checklist is [`STATUS.md`](STATUS.md). Prices and payouts are [`balance.json`](balance.json).

## What answers today

Login, cards, the horse, inventory, action points, tutorial, grooming, the paddock, practice, course slots, the course editor, chat, clubs, challenges, ranked search, ladders, and this server's showdown week are answered from SQLite and from the clients connected here. Handlers live in `game_features.py`. Cards are written by `igor_host` with the game's own Igor types.

An empty account is given a helmet, jacket, breeches, boots, bridle, saddle, blanket, and ankle boots, and they are equipped. The list is `starter` in `balance.json`. Shop clothes and tack come from `gamedata/wardrobe.json`.

The course editor loads paddock, winter, desert, stadium, and the tutorial map. The terrain prefab has to be the scene name inside the bundle: `PaddockTerrain`, `WinterScene`, `Desertscene`, `StadiumScene`, `Tutorialscene`. The agility checkpoint card id is `agility_checkpoint`.

A reply is sent only when the client opened an RPC. Void calls stay silent. Sending bytes back to a void call makes the client drop.

Three calls stay failures, because a success would claim something this process cannot do:

- **Steam checkout** (`SteamMicroTxnInit`): a success would ask Steam to charge a real wallet. Coins and skill tickets still come from the saved balance.
- **Kickstarter**: the campaign is over and there is no pledge API.
- **Debug `Exec` and `Eval`**: they are not run.

## Changing prices

Edit [`balance.json`](balance.json), then restart the server and the card host. Each block has a `_note` that says whether the number is from the original game or a choice this server made because the original amount was never published.

Course-slot, course-submit, and per-piece prices default to **0**. Set `course_editor.slot_coins`, `submit_coins`, or `piece_coins` if you want those to cost coins. Individual shop and course-piece names and prices are under `items` in the same file. Shop clothes and tack use `gamedata/wardrobe.json` after you copy it into the game, not this file.

The original daily gift ran for 200 days. That table is in `Rewards for all the daily logins.txt`. This server still pays the 7-day coin sheet in `balance.json`, because several of those prize items have no id in the client.

An existing save is not rewritten when you change `new_player`. Those values apply to accounts created after the change.

## Hosting a server

You need Windows and your own copy of the game installed through Steam. The server reads the game's files from that install. It does not ship them.

1. Download `RCCServer-<version>.zip` from the GitHub releases and unzip it anywhere.
2. Run `Start Server.bat`. The first start creates `server.json`, finds the game through Steam, and starts the card host. If it cannot find the game, set `game_dir` in `server.json` to the folder that holds `rcc.exe`.
3. Run `Make servers.txt.bat` to make the file players need. See below.

Close the server window to stop it. The card host stops with it. Saves are in `users.db` next to `RCCServer.exe`.

### Running from source

Install Python 3.10 or newer, then run `start_server.bat`. It creates the virtual environment, builds the card host if `IgorHost.exe` is missing, and starts `Server.py`. If the game is not found, pass its folder:

```bat
start_server.bat "D:\Games\RidingClubChampionships"
```

`python Server.py join <address>` is the source version of `Make servers.txt.bat`. `python build_release.py` builds the release zip. Pushing a `v*` tag builds it on GitHub and attaches it to a release.

The card host reads `gamedata/wardrobe.json`, `gamedata/course_items.json`, and `gamedata/localization_extra.json` from the server folder. A copy in the game's `UserData` folder is used only when the server has none.

### Always on (Windows)

Open a command prompt as administrator in the server folder and run:

```bat
RCCServer.exe service install
```

This makes a scheduled task named `RCC Server`. It starts at boot without anyone logged in, and starts again a minute after the server stops. It runs as SYSTEM, so install first saves the game folder in `server.json`. Output goes to `server.log` and is replaced at each start. `service status` shows it and `service remove` deletes it. Do not also keep a server window open, because both would want the same ports.

### Hosting on Linux

Download `RCCServer-linux-<version>.tar.gz`. It holds the Python source and the same card host, which runs under Mono. You still need your own copy of the game: copy its `rcc_Data/Managed` folder to the Linux machine, for example to `~/rcc-game/rcc_Data/Managed`.

```sh
sudo apt install python3 python3-venv mono-runtime libmono-system-web-extensions4.0-cil
tar xzf RCCServer-linux-<version>.tar.gz && cd RCCServer
./start.sh --game-dir ~/rcc-game
```

Set `game_dir` in `server.json` to that folder, so you do not need the flag. `./start.sh join <address>` and `./start.sh admin ...` work as on Windows. `sudo ./start.sh service install` makes a systemd unit named `rcc-server` that starts at boot and restarts on failure. Read its output with `journalctl -u rcc-server -f`. The card host ends with the server, even when the server is killed.

The game itself only runs on Windows, so players still use a Windows `servers.txt`.

## Pointing the game at a server

The game reads `servers.txt`, next to `rcc.exe`, before its built-in list, and uses the entry named `live`. That file is all a player needs. Nothing inside the game files changes, and Steam's "Verify integrity of game files" puts the original back.

To play on this PC, run `Make servers.txt.bat` and leave the address blank. When the server sits next to the game, this writes `servers.txt` into the game folder and keeps the old one as `servers.txt.before-emu`.

For players on other machines, give the address they reach you at, and send them the `servers.txt` it writes next to the server:

```bat
RCCServer.exe join 203.0.113.7
```

| Port | Use |
| --- | --- |
| 27130 | Game TCP. Open this one for other players. |
| 27132 | Policy file |
| 8080 | HTTP. The Steam client only uses it for Facebook and VK features. |
| 27141 | Card host, local only |

## Letting other people on

Copy [`server.json.example`](server.json.example) to `server.json`.

- `bind_host` is `127.0.0.1` until you set `0.0.0.0`. Only then can another computer reach the server.
- `public_host` is the address players use to reach you. `Make servers.txt.bat` uses it when you give no address.
- `game_dir` is the game folder, if the server cannot find it through Steam.
- `debug_logs` writes every packet to `debug_logs/`. It is on when run from source and off in `RCCServer.exe`. Set `true` or `false` to choose.
- `allowed_steam_ids` empty means any real Steam account can create a rider. Fill it with the Steam ids you want, and everyone else is refused.
- `open_registration` false keeps accounts that already exist and refuses new ones.
- `max_clients` is how many game connections may be open at once. 64 by default. A connection that does not log in within a minute is closed.

The stock client does not send a password. It sends a Steam id. This server cannot ask Valve whether that ticket is real, so a person who can reach the port and knows an id can claim it. Use the allow list, and do not leave `bind_host` open to the internet, if that matters to you.

## Running the server day to day

Use `RCCServer.exe admin` (or `python Server.py admin` from source) in a second window. While the server is up, changes reach players at once. With the server off, the same commands edit `users.db` directly.

```bat
RCCServer.exe admin list [search]
RCCServer.exe admin ban <rider> [--days N] [--reason "text"]
RCCServer.exe admin unban <rider>
RCCServer.exe admin mute <rider> [--minutes N] [--reason "text"]
RCCServer.exe admin unmute <rider>
RCCServer.exe admin coins <rider> <amount>
RCCServer.exe admin rename <rider> <name>
RCCServer.exe admin reset <rider> --yes
RCCServer.exe admin kick <rider>
RCCServer.exe admin backup
```

`<rider>` is the player id from `list`, a Steam id, or a rider name.

- A ban works on the Steam account. A banned player is disconnected and cannot log in until the ban ends.
- A mute stops that rider's chat. The game shows them how long and why, and their earlier lines disappear from everyone's chat.
- `coins` with a negative amount takes coins away, never below 0.
- `reset` gives that Steam account a new rider and horse. The old save is copied into `backups/` first.
- Riders whose Steam ids are in `admin_steam_ids` in `server.json` get the admin marker in chat.

`users.db` is copied into `backups/` at every start and every `backup_hours` (6 by default). The newest `backups_keep` (28) are kept. Packet logs older than `log_days` (7) are deleted. To restore, stop the server and copy a backup over `users.db`.

The news panel in the main menu reads `news` in `balance.json`.

## What a modified client cannot do

Coins, rating, action points, and showdown prizes are stored here. A debug "give reward" packet is ignored. A ranked result pays only after that client was actually paired, and only once. A showdown prize uses the place on today's board, not the place written in the packet. A ride shorter than `min_ride_seconds` (8 by default) is not stored and does not win. Grooming cannot jump to clean in one packet, and the Pro Rider groom packet still costs coins because this server does not sell Pro. Course edits are limited to the rider who owns the course, only slots bought through the editor can be saved, and a saved course is capped at `course_editor.max_slot_bytes`. Clothes and tack must be owned before they can be worn. Only a club's owner can rename it, change the icon, or change roles. Owners and managers invite, accept, and kick, and a manager can only kick plain members. A club holds `club.max_members`, and a rider is in one club at a time. When two riders here meet in ranked, both results are compared and each is paid once. A rider who quits or drops loses. The showdown prize pays once per day's board. A login that is not a real Steam id, or that `allowed_steam_ids` and `open_registration` do not let in, does not create an account. Game calls before a login are refused.

`http://127.0.0.1:8080/health` should answer while the server is up. The `/debug` pages list Steam ids and packet logs, so they only answer requests from this machine.

## Driving the client

`agent_mcp/server.py` is an optional Cursor MCP server. It talks to the MelonLoader bridge in `client_bridge/RccAgentBridge.cs` on `127.0.0.1:27140`. The bridge is only up while the game is running. Reload the MCP server in Cursor after `server.py` changes.

- `game_buttons` lists visible buttons
- `game_click` presses one, by label or by `#` index
- `game_scene` opens `menu`, `shop`, `paddock`, `tutorial`, `showdown`, `grooming`, `daily`, `avatar`, `editor`, `editor:slot name`, or `terrain:tutorial_terrain`

Build `RccAgentBridge.cs` as a MelonLoader mod and put the dll in the game's `Mods` folder. The MCP entry in Cursor runs this repo's `.venv` Python against `agent_mcp/server.py`.

## If the client drops

The TCP log in `debug_logs/` names the last call. `NO REPLY` on an RPC means that call still needs a handler. A void call with `NO REPLY` is correct. Logging is switched in `debug_config.py`. Set `DEBUG_ENABLED = False` to quiet it.

`users.db` is the save. It is not part of the git repo.

## Contributing

Fork, keep new replies in `game_features.py`, and write cards through the host rather than hand-packed bytes. Test with the Steam client.

Install the tools with `pip install -r requirements-dev.txt`. Run `python -m unittest test_server_rules test_game_rules test_igor_codec` and `ruff check .` before a pull request. The tests use a temporary database and never touch `users.db`. [`AGENTS.md`](AGENTS.md) explains how a packet flows through the code, for people and for coding agents. The open items are the unchecked boxes in [`STATUS.md`](STATUS.md).

## Legal

Educational and preservation use. Own a copy of the game. This software is provided as-is.
