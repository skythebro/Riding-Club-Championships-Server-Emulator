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

## 2. Hosting a server: one download (done, except an always-on option)

- [x] `build_release.py` makes `RCCServer-<version>.zip`: `RCCServer.exe` (PyInstaller, no Python needed), a prebuilt `IgorHost.exe`, `balance.json`, `gamedata/`, `server.json.example`, `Start Server.bat`, `Make servers.txt.bat`, and `QUICKSTART.txt`. No game files.
- [x] The server finds the game itself: `game_dir` in `server.json`, the folder above, then every Steam library. Tested from an unzipped copy outside the game folder on 14 October 2026; the real client logged in.
- [x] The server starts the card host and stops it with itself, even when the window is closed or the process is killed (a Windows job object).
- [x] The first start writes `server.json`. Packet logs are off in the exe unless `debug_logs` is true.
- [x] GitHub Actions: lint and tests on every push, and a release zip on every `v*` tag.
- [x] Hosting steps in the README and `QUICKSTART.txt`.
- [ ] A Windows service or scheduled-task option for always-on servers.

## 3. Tools for server owners: done

- [x] `RCCServer.exe admin`: list, ban and unban (by Steam account, optionally timed), mute and unmute, coins, rename, reset, kick, backup. It reaches the running server through local-only `/admin` routes, or edits `users.db` when the server is off. Tested against the real client on 14 October 2026.
- [x] The client's own moderation messages: `Chat.Banned` and `Chat.Unbanned` for mutes, `RemovePlayerMessage` to wipe a muted rider's lines, and `Game.AdminUsers` from `admin_steam_ids`.
- [x] Automatic `users.db` backups (online-safe SQLite backup), and cleanup of old packet logs.
- [x] Message of the day: `news` in `balance.json`.
- [x] Fixed on the way: chat timestamps were seconds where the client reads milliseconds (every line said 1970), and `new_player` in `balance.json` was never applied.

## 4. Gameplay gaps

- [x] `Player.NewLevel`: the level-up popup, seen in the real client on 14 October 2026. Each level pays `level_up_bonus` in `balance.json` (100 coins, 1 skill ticket), and the popup lists it.
- [x] `Chat.GuildMessage`: club chat room and club news lines.
- [x] `UpgradeInventoryItem` and `UpgradeEquipmentItem` stay no-ops on purpose. No item card in `wardrobe.json` has upgrade levels, and the client only shows the Upgrade button for items that do. The original upgrade table was never recorded, so inventing one would be guesswork.
- [x] `Game.DsWeekFinished`: the weekly result popup, seen in the real client. `Game.DsQualification` is skipped: this server has no weekly final.
- [x] Quests stay out: accept and decline are stored, but the original quest cards were never recorded, so there is nothing to offer.
- [ ] The original 60-level XP table. Today every level is 100 XP.
- [ ] The 200-day login gifts (`Rewards for all the daily logins.txt`).
- [ ] Everything under "Still to test" in `STATUS.md`.
- Left out on purpose: Steam checkout, Pro Rider (`Premium*`), Kickstarter, and debug `Exec` / `Eval`.

## 5. Code health

- [ ] Split `game_features.py` (about 3,500 lines) by service: economy, courses, ranked, clubs, chat, showdown. Keep the `on_<Name>` dispatch.
- [x] `UserDatabase` moved to `db.py`.
- [ ] Numbered migrations in place of the `_ensure_columns` lists.
- [x] Removed the legacy `/websocket` route. It was an echo toy, and the game plays over TCP.

## 6. Later: Linux and Docker hosting

- [ ] Run `IgorHost` under Mono with the owner's copy of `rcc_Data/Managed`, then publish a Docker image. The server still needs the owner's game files, because packets are encoded with the game's own types.
