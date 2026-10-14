# Roadmap

The goal: anyone who owns the game can join a server with one file, and anyone can host one with one download.

Measured on 14 October 2026 against the decompiled client:

- **Client calls:** 101 of the 108 the client can make have a handler. The other 7 are void calls that need no reply.
- **Server pushes:** this server sends 19 of the 43 kinds of message the client can receive. The missing ones are listed under step 4.

## 1. Joining a server: done

- [x] The game reads `servers.txt` next to `rcc.exe` before its built-in list. On the normal Steam branch it uses the entry named `live`. Tested in the real client on 14 October 2026: it connected to a port that only `servers.txt` named.
- [x] `client_config.py` writes that file. A player needs nothing else, and the game files stay untouched.
- [x] Removed the `resources.assets` patcher and UnityPy.
- [x] HTTP moved from port 80 to 8080, so no administrator shell is needed. The Steam client only uses HTTP for Facebook and VK.
- [x] `RidingclubOffline.dll` (an old debug mod that faked cards) is disabled. The game logs in and loads the paddock without it.

## 2. Hosting a server: one download

- [ ] A GitHub release zip: the server as one exe (PyInstaller), a prebuilt `IgorHost.exe`, `balance.json`, `gamedata/`, and `server.json.example`. No Python or compiler needed.
- [ ] One start script that finds the game's `rcc_Data/Managed` folder (Steam library folders, or a path in `server.json`), then starts the card host and the server.
- [ ] Run the card host from the server process, and stop it when the server stops, instead of using two windows.
- [ ] A hosting guide: open TCP 27130 on the router and firewall, set `bind_host` and `public_host`, then send players `servers.txt`.
- [ ] A Windows service or scheduled-task option for always-on servers.

## 3. Tools for server owners

- [ ] An admin command line or local web page: list riders, ban and unban, give coins, reset a rider, and rename one.
- [ ] Wire up the client's own moderation messages: `Chat.Banned`, `Chat.Unbanned`, `RemovePlayerMessage`, `RemoveRoomMessage`, and `Game.AdminUsers`.
- [ ] Automatic `users.db` backups, and rotation for `debug_logs/`.
- [ ] A server message of the day through the `news` card.

## 4. Gameplay gaps

- [ ] `Player.NewLevel`: the level-up popup.
- [ ] `Chat.GuildMessage`: club chat.
- [ ] `UpgradeInventoryItem` and `UpgradeEquipmentItem` are no-ops. Upgrading should cost coins and raise the item level, up to 6.
- [ ] `Game.DsQualification` and `Game.DsWeekFinished`: showdown notifications.
- [ ] Quests: accept and decline are stored, but `QuestUpdate` never offers one.
- [ ] The original 60-level XP table. Today every level is 100 XP.
- [ ] The 200-day login gifts (`Rewards for all the daily logins.txt`).
- [ ] Everything under "Still to test" in `STATUS.md`.
- Left out on purpose: Steam checkout, Pro Rider (`Premium*`), Kickstarter, and debug `Exec` / `Eval`.

## 5. Code health

- [ ] Split `game_features.py` (about 3,500 lines) by service: economy, courses, ranked, clubs, chat, showdown. Keep the `on_<Name>` dispatch.
- [ ] Move `UserDatabase` and the schema into one `db.py`, with numbered migrations.
- [ ] Remove or fix the legacy `/websocket` route (client ids collide after a disconnect).

## 6. Later: Linux and Docker hosting

- [ ] Run `IgorHost` under Mono with the owner's copy of `rcc_Data/Managed`, then publish a Docker image. The server still needs the owner's game files, because packets are encoded with the game's own types.
