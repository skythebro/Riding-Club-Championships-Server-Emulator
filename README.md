# Riding Club Championships Server Emulator

A private server for Riding Club Championships, so the game can be played after the official servers shut down on 26 July 2025. You need your own copy of the game from Steam. The game is no longer sold on the store, but anyone who owns it can still install it from their Steam library and press Play. This project is not affiliated with the original developers.

Pick your path:

- **I want to play on someone's server.** Read [Joining a server](#joining-a-server).
- **I want to run a server.** Read [Hosting a server](#hosting-a-server). It works on Windows, Linux, and macOS.
- **Something broke.** Read [Reporting a bug](#reporting-a-bug).
- **I want to help.** Read [what is not finished](#not-finished-and-where-help-is-wanted), then [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Reporting a bug

Send the logs while it is fresh. Logs let a bug be fixed without guessing, and they are the most useful thing you can attach to a [GitHub issue](https://github.com/skythebro/Riding-Club-Championships-Server-Emulator/issues).

**If you play on someone's server:** right after it happens, and before you restart the game, copy the game's log. On Windows it is `rcc_Data/output_log.txt` in the game folder. On a Mac it is `~/Library/Logs/Unity/Player.log` (in Finder: Go, Go to Folder, paste that path). The game replaces that file every time it starts. Say what you pressed and what you expected. Send both to the server's owner, or attach them to an issue.

**If you run the server:** double-click `Collect logs.bat` in the server folder (`RCCServer.exe logs`, or `./start.sh logs` on Linux). It makes `rcc-logs-<time>.zip` with:

- the server log (`logs/server.log`: the build, your settings, each login, refusals, warnings, and any crash with its traceback),
- the newest packet logs,
- the game's own log, if the game is on the same PC,
- your settings with the public address and Steam id lists hidden.

It never includes `users.db`, so no saves leave your PC. Attach the zip to the issue, and say who was playing and what they did.

When several people report the same thing, that is the signal to fix it.

## Not finished, and where help is wanted

The server runs the whole game loop: log in, ride, shop, chat, clubs, ranked, and the weekly showdown. These parts are missing or unproven. Most of them need people who played the original, because the original numbers were never published. [`CONTRIBUTING.md`](CONTRIBUTING.md) says how to help with each.

**Missing**

| What | Why it is missing |
| --- | --- |
| Quests | The rules are known (three slots, one decline a day). The quests and their rewards are not. |
| Item upgrades (up to level 6) | The costs and what each level gives are not known. The Upgrade button never shows. |
| The showdown's Sunday Grand Final | The top 100 qualifiers got free entry, and the winner got a purple blazer. This server has no final, only the daily boards. |
| The 60-level XP table | Every level is 100 XP here. The real numbers were never recovered. |
| The 200-day login gifts | The list exists in `Rewards for all the daily logins.txt`, but several prizes have no item id, so the server pays a 7-day sheet. |
| Steam checkout, Pro Rider, Kickstarter | Off on purpose. Coins and skill tickets come from the saved balance. |

**Guessed, so a real number would replace it**

- Level-ups give 100 action points. One fan site says so, and nothing else confirms it.
- Treat prices, apple energy (100 and 500), paddock prize odds, and every club and course-editor price marked "server choice" in [`balance.json`](balance.json).
- The ids of the showdown jacket, apple slice, and lucky leaf prizes.
- The wording of the first-time "Create your character" screen. The change-look screen has its original text.
- Any screen that shows a text key instead of a sentence. Only play-testing finds them.

**Written but not tried yet**

- More than two people on one server. Two real PCs have tried clubs, challenges, chat, and online status. Ranked between people and the showdown board with a crowd are not yet proven.
- `service install` on Windows from an administrator prompt. Only the generated task has been checked.
- The shop treats and the paddock prizes in the real client. The wire format is checked, the screens are not.
- A Linux host with players on other distributions. The test was Debian under WSL.
- macOS, as a player and as a host. The Mac game speaks the same protocol (checked against the Mac depot), but nobody has played or hosted on a real Mac yet.

The full list of what is checked by hand is [`STATUS.md`](STATUS.md). What is planned is [`ROADMAP.md`](ROADMAP.md).

## Joining a server

The server's owner sends you a file named `servers.txt`.

1. Open the game folder. In Steam: right-click the game, Manage, Browse local files.
2. Put `servers.txt` where the game reads it:
   - **Windows:** next to `rcc.exe`.
   - **Mac:** inside the game app, `rcc`. Right-click it, Show Package Contents, and put the file there, beside the `Contents` and `assetbundles` folders (not inside them).
3. Start the game from Steam.

Nothing inside the game files changes. Steam's "Verify integrity of game files" puts the original back, and you copy `servers.txt` in again. The game uses the entry named `live`, which is what the default Steam branch asks for.

## Hosting a server

You need your own copy of the game. The server reads some of the game's files from that install and does not ship them.

### Windows

1. Download `RCCServer-<version>.zip` from the [releases](../../releases) and unzip it anywhere.
2. Run `Start Server.bat`. The first start creates `server.json`, finds the game through Steam, and starts the card host. If it cannot find the game, set `game_dir` in `server.json` to the folder that holds `rcc.exe`.
3. To play on the same PC, run `Make servers.txt.bat` and leave the address blank. It writes `servers.txt` into the game folder and keeps the old one as `servers.txt.before-emu`.

Close the server window to stop it. The card host stops with it. Saves are in `users.db` next to `RCCServer.exe`.

### Letting other people join

1. In `server.json` set `"bind_host": "0.0.0.0"` and `"public_host"` to the address players will use.
2. Open **TCP port 27130** on your firewall, and forward it on your router.
3. Run `Make servers.txt.bat` and type that address, or run `RCCServer.exe join <address>`. It writes `servers.txt` next to the server. Send that file to your players.
4. Restart the server.

Players only need port 27130. The others are for the server itself:

| Port | Use |
| --- | --- |
| 27130 | Game connection. Open this one. |
| 27132 | Policy file. The Steam client did not use it in testing. |
| 8080 | HTTP: `/health` and the Facebook and VK features. |
| 27141 | Card host. Local only. |

The stock client does not send a password. It sends a Steam id, and this server cannot ask Valve whether that id is real. Anyone who can reach the port and knows an id can claim it. If that matters, fill `allowed_steam_ids` so everyone else is refused, and do not open the port to the whole internet.

### Always on

**Windows.** Open a command prompt as administrator in the server folder and run `RCCServer.exe service install`. This makes a scheduled task named `RCC Server`. It starts at boot without anyone logged in, and starts again a minute after the server stops. It runs as SYSTEM, so install first saves the game folder in `server.json`. Output goes to `server.log` and is replaced at each start. `service status` shows it and `service remove` deletes it. Do not also keep a server window open, because both would want the same ports.

**Linux.** `sudo ./start.sh service install` makes a systemd unit named `rcc-server`. Read its output with `journalctl -u rcc-server -f`.

### Linux

Download `RCCServer-linux-<version>.tar.gz`. It holds the Python source and the same card host, which runs under Mono. Copy the game's `rcc_Data/Managed` folder from a Windows install to the Linux machine, for example to `~/rcc-game/rcc_Data/Managed`.

```sh
sudo apt install python3 python3-venv mono-runtime libmono-system-web-extensions4.0-cil
tar xzf RCCServer-linux-<version>.tar.gz && cd RCCServer
./start.sh --game-dir ~/rcc-game
```

Set `game_dir` in `server.json` to `~/rcc-game` (the full path) so you do not need the flag. `./start.sh join <address>` and `./start.sh admin ...` work as on Windows. The card host ends with the server, even when the server is killed.

The game itself runs on Windows and macOS, so players use `servers.txt` there (see [Joining a server](#joining-a-server)). Tested on Debian 13 with Mono 6.12.

### macOS

Use the same download as Linux, `RCCServer-linux-<version>.tar.gz`. It is Python plus a card host that runs under Mono, and nothing in it is Linux-only.

```sh
brew install python mono            # or Python and Mono from python.org and mono-project.com
tar xzf RCCServer-linux-<version>.tar.gz && cd RCCServer
./start.sh
```

If the Mac version of the game is installed through Steam, the server finds it by itself (it reads `rcc.app/Contents/Resources/Data/Managed`). Otherwise copy `rcc_Data/Managed` from a Windows install, as on Linux, and pass `--game-dir`. `./start.sh join` with no address writes `servers.txt` straight into the game app, so you can play on the same Mac.

`./start.sh service install` makes a LaunchAgent. It starts the server when you log in and restarts it if it stops. Output goes to `server.log`. `service remove` takes it away.

The Mac game is the same build as the Windows one. Its game code differs only in the Steam bindings, and the card host run on the Mac game's files sends byte-identical frames. A real Mac has not run it yet, so if something is off, see [Reporting a bug](#reporting-a-bug).

### From source

Install Python 3.10 or newer, then run `start_server.bat` (Windows) or `./start.sh` from the Linux download. It creates the virtual environment and starts `Server.py`. On Windows it also builds the card host if `IgorHost.exe` is missing. To pass the game folder: `start_server.bat "D:\Games\RidingClubChampionships"`. `python Server.py join <address>` is the source version of `Make servers.txt.bat`.

## Settings

The first start writes `server.json`. All keys are optional.

| Key | Default | Meaning |
| --- | --- | --- |
| `bind_host` | `127.0.0.1` | `0.0.0.0` lets other computers reach the server. |
| `public_host` | empty | The address players use. `join` uses it when you give no address. |
| `game_dir` | found automatically | The game folder, if the server cannot find it. On a Mac, the folder that holds the game app. |
| `tcp_port`, `policy_port`, `http_port` | 27130, 27132, 8080 | Change them, then run `join` again. |
| `open_registration` | `true` | `false` keeps existing accounts and refuses new ones. |
| `allowed_steam_ids` | empty | Empty lets any Steam account create a rider. Fill it to allow only those ids. |
| `admin_steam_ids` | empty | These riders get the admin marker in chat. |
| `everyone_pro` | `true` | Every rider counts as a Pro Rider: rides cost half the action points, the daily gift is larger, the stable hand is free, and names show in the Pro colour in chat. `admin pro <rider> off` takes it from one rider. Nothing is sold, and Steam checkout stays off. |
| `ranked_rival` | `alone` | What fills a ranked search when nobody is waiting. `alone` uses the saved Rival only when no other rider is online, `always` uses it after two seconds, `never` keeps searching for a real rider. |
| `max_clients` | 64 | Open game connections. One that does not log in within a minute is closed. |
| `min_ride_seconds` | 8 | A ride shorter than this is not stored and does not win. |
| `backup_hours`, `backups_keep` | 6, 28 | How often `users.db` is copied into `backups/`, and how many copies stay. |
| `log_days` | 7 | Packet logs older than this are deleted. |
| `debug_logs` | off in the exe | Writes every packet to `debug_logs/`. |

### Prices and rewards

Edit [`balance.json`](balance.json), then restart the server. Each block has a `_note` that says whether the number is from the original game or a choice this server made, because the original was never published.

- New riders start with `new_player` (coins, action points, skill tickets, rating). An existing save is not changed.
- `level_up_bonus` is paid for every level gained, and the level-up popup lists it. It is 100 action points, as in the original. Coins and skill tickets are 0 until you set them.
- `horse_stats.start` is the number every horse stat starts at. The default is 0, as in the original.
- `showdown`, `club`, `ranked`, `treats`, and `avatar_change_coins` hold those prices and payouts.
- `treats` are the shop items for the horse: the golden apple (2000 action points), sugar cubes (+1 on every stat for an hour), the lucky four leaf clover (paddock prizes four times as often for 15 minutes), and the XP book (double XP for 24 hours). Those effects are from the original. The prices are this server's choice.
- `paddock` sets the prizes for collecting stars and presents in the paddock (2 or 6 coins, 20, 60, or 150 XP), and how many a minute count.
- `energy_buff` is the +1 on every stat while a horse has more than half its action points. Set `value` to 0 to turn it off.
- Course slots and course submits cost 0 until you set `course_editor`. Saving a course in the editor costs each piece you added, at its price under `items` (never below 1), and gives half back for pieces you remove.
- `news` is the message in the main menu's news panel.
- The daily gift is a 7-day sheet. The original ran 200 days. That list is in `Rewards for all the daily logins.txt` and is not in use.

## Running it day to day

Use `RCCServer.exe admin` (`python Server.py admin` from source, `./start.sh admin` on Linux) in a second window. While the server is up, changes reach players at once. With the server off, the same commands edit `users.db` directly.

```bat
RCCServer.exe admin list [search]
RCCServer.exe admin ban <rider> [--days N] [--reason "text"]
RCCServer.exe admin unban <rider>
RCCServer.exe admin mute <rider> [--minutes N] [--reason "text"]
RCCServer.exe admin unmute <rider>
RCCServer.exe admin coins <rider> <amount>
RCCServer.exe admin pro <rider> on|off|default
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

`users.db` is the save. It is copied into `backups/` at every start and every `backup_hours`. To restore, stop the server and copy a backup over `users.db`. A new version of the server upgrades an old save by itself.

`http://127.0.0.1:8080/health` answers while the server is up.

## What works

Login, the horse and its stats, inventory, action points, the tutorial, grooming, the paddock, practice, ranked matches, friend challenges, the weekly showdown, ladders, global and club chat, clubs (create, join, roles, news), the shop, and the course editor with course slots. Level-ups pay a bonus and show the popup. The [checklist](STATUS.md) says what has been tried in the real client.

## What does not work

See [Not finished](#not-finished-and-where-help-is-wanted) at the top.

## What a modified client cannot do

The server decides coins, rating, action points, and prizes from its own state. It never believes a number in a packet.

- A debug "give reward" packet is ignored.
- A ranked result pays only after that client was actually paired, and only once. When two riders meet, both results are compared. A rider who quits or drops loses.
- A showdown prize uses the place on today's board, and pays once per day's board.
- A ride shorter than `min_ride_seconds` is not stored and does not win.
- Grooming cannot jump to clean in one packet.
- Clothes and tack must be owned before they can be worn.
- Course edits are limited to the owner, only slots bought through the editor can be saved, and a saved course is capped at `course_editor.max_slot_bytes`.
- Only a club's owner can rename it, change the icon, or change roles. A manager can only kick plain members. A club holds `club.max_members`, and a rider is in one club at a time.
- A login that is not a real Steam id, or that `allowed_steam_ids` and `open_registration` do not let in, does not create an account. Game calls before a login are refused.

## Troubleshooting

- **"The game folder was not found."** Set `game_dir` in `server.json` to the folder with `rcc.exe` (on Linux, the folder that holds `rcc_Data/Managed`).
- **The game cannot connect.** Check that `servers.txt` is next to `rcc.exe`, that the server is running, and that its address matches `public_host`. For other players, check that port 27130 is open and forwarded.
- **"Mono is not installed" (Linux or Mac).** Run the `apt install` or `brew install` line above.
- **A port is already in use.** Another copy of the server is running. Stop it, or change the port in `server.json` and run `join` again.
- **The client drops.** Collect the logs (see [Reporting a bug](#reporting-a-bug)) and send them. The newest file in `debug_logs/` names the last call. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

### Reporting a problem

See [Reporting a bug](#reporting-a-bug). It covers the server log, `Collect logs.bat`, and the game's own log.

### Steam

The game checks Steam before it talks to any server. It needs Steam running, signed in, and online, and it has to be started from Steam (press Play in your library). Run from `rcc.exe` directly, it hands over to Steam and quits, which looks like the game closing at once.

"Steam did not start" or "Log in to Steam first" in the game means Steam is closed or offline. A Steam popup that says "Missing interface" comes from the game's old Steam files and has not been seen to stop play. This project does not replace or fake Steam. The game uses it to check you own the game, and that stays.

## Legal

The server code is under the [MIT licence](LICENSE). The game, its code, and its art belong to their original owners, and this repo contains none of them. Use it for education and preservation, and own a copy of the game.
