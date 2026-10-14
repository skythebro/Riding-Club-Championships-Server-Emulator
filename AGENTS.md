# Agent guide: RCC server emulator

Private server for Riding Club Championships, a Unity (Mono) game whose official servers shut down on 26 July 2025. This repo is `ServerEmu/`, inside the game's install folder. The game itself (`../rcc.exe`, `../rcc_Data/`) is not in git.

Read [`README.md`](README.md) for hosting, [`STATUS.md`](STATUS.md) for what is tested, and [`ROADMAP.md`](ROADMAP.md) for what comes next. The unchecked items in `STATUS.md` are the open work.

## Commands

All commands run from this folder. Use the virtual environment (Python 3.10+, built on 3.12).

```bash
python -m venv .venv && .venv\Scripts\python -m pip install -r requirements-dev.txt   # first time
.venv\Scripts\python -m unittest test_server_rules test_game_rules test_igor_codec    # all tests
.venv\Scripts\python -m unittest test_server_rules test_game_rules                    # fast, no IgorHost
.venv\Scripts\ruff check .                                                            # lint, must pass
.venv\Scripts\python Server.py                                                        # run the server
```

`test_igor_codec` needs `igor_host/IgorHost.exe` and the game's `../rcc_Data/Managed` folder. `IgorClient` starts the host if it is not already listening on 27141. The test also binds the real TCP port 27130, so stop a running server first.

Build the card host after you change `igor_host/Host.cs`. Stop any running `IgorHost.exe` first, because the exe is locked while it runs:

```bat
cd igor_host && C:\WINDOWS\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /target:exe /main:Host /r:System.Web.Extensions.dll /out:IgorHost.exe Host.cs
```

## How a packet flows

1. `Server.py` `handle_tcp_client`: one thread per TCP client on 27130. Raw bytes go to `IgorClient.feed`.
2. `igor_client.py` → `IgorHost.exe` (C#, `igor_host/Host.cs`): splits frames and decodes them with the game's own `Protocol2` Igor types from `Assembly-CSharp.dll`. It returns message dicts such as `{"name": "BuyItem", "service": 105, "function": 3, "rpc": 7, "rest": "<hex>"}`.
3. `Server.handle_igor_message`: handles `Login`, `UpdateAccountInfo`, `SubscribeFriends`, `GetSpecialOffers`, and `GetLikes`. Everything else goes to `GameFeatures.dispatch`.
4. `game_features.py` `GameFeatures.dispatch` → `on_<MessageName>(message, client_id, player_id)`. It holds `self.lock` (an RLock) and refuses calls before login, except those in `BEFORE_LOGIN`.

Rules that keep the client connected:

- **Reply only when `message["rpc"]` is not None.** A void call must return `None`. Any bytes sent back to a void call make the client drop.
- Build every frame through `self.emulator.igor`: `reply_ok`, `reply_fail`, `reply_type`, `reply_uints`, `reply_blobs`, `push_object`, `push_session`. Do not hand-pack bytes when an Igor type exists.
- Send unsolicited pushes with `self.emulator.send_to(client_id, frame)`. It locks per socket and uses `sendall`. Never call `sock.send` directly.
- `igor_host/protocol_map.json` lists every service, function, name, and whether it has an RPC. Regenerate it with `build_protocol_map.py`, which reads the decompiled client.

## Where things live

| Path | What |
| --- | --- |
| `Server.py` | FastAPI HTTP on 8080, TCP game server on 27130, policy server on 27132, `UserDatabase` |
| `game_features.py` | Every game handler (`on_*`). This is where new behaviour goes. |
| `storage.py` | `connect_db(path)`: the only way to open SQLite. It commits, closes, and waits on a busy lock. |
| `server_config.py` | `server.json` loading, login allow list, `ride_is_plausible` |
| `paths.py` | `APP_DIR` (next to the scripts, or next to `RCCServer.exe`) and `find_game_dir()`. Use these, never `Path(__file__)`, so the packaged exe finds its files. |
| `build_release.py` | Builds `dist/RCCServer-<version>.zip`. CI runs it on `v*` tags (`.github/workflows/`). |
| `client_config.py` | Writes the `servers.txt` that points a game at this server (entry `live`, read before the built-in list) |
| `balance.json` | All prices and payouts. Read them with `self.bal("section", "key", default=...)`. Never hard-code a number. |
| `gamedata/*.json` | Wardrobe, course pieces, and extra localization served by the card host |
| `igor_host/` | C# card and codec host on 127.0.0.1:27141 |
| `agent_mcp/server.py` + `client_bridge/RccAgentBridge.cs` | MCP tools that drive the running game (MelonLoader bridge on 27140) |
| `../exports/decompiled/Assembly-CSharp/` | The decompiled client, outside git. This is the source of truth for wire formats: see `Protocol2/` and the `Service*` classes. |
| `debug_logs/` | Per-run TCP and binary logs, ignored by git. `NO REPLY` on an RPC means a handler is missing. |

## Rules

- **Never touch `users.db` in tests.** It is the real save. For handler rules, use the `Harness` in `test_game_rules.py`: a real `GameFeatures`, a temporary save, and a fake Igor. Prefer it over hand-built stubs, which break when a handler calls a new helper. For wire bytes, build `RCCServerEmulator(db_path=<temp file>, debug_logging=False)` and set `capture_outbox = True` to see pushes for clients without a socket (`test_igor_codec.py`).
- Club calls refuse with `_guild_error(message, GUILD_ERR_*)`, the client's own `EGuildError`. Check roles with `_guild_allowed`.
- The server decides coins, rating, XP, and prizes from its own state. It never takes them from the packet. Check affordability, then deduct, inside the handler. Keep `ride_is_plausible` and the anti-cheat rules in the README true.
- `SteamMicroTxnInit`, Kickstarter, and debug `Exec` / `Eval` stay failures on purpose.
- Schema changes go in `GameFeatures._ensure_columns` (`ALTER TABLE ... ADD COLUMN` inside try) so existing saves migrate.
- New behaviour gets a test, and a checklist line in `STATUS.md` until someone has tried it in the real client.
- Docs use short, plain sentences, like README and STATUS.

## Trying it in the real client

Start `IgorHost.exe`, then `Server.py`, then the game. With MelonLoader and `Mods/RccAgentBridge.dll` installed, `agent_mcp` exposes `game_buttons`, `game_click`, and `game_scene`, plus emulator health and log tools. `.mcp.json` registers it for Claude Code, and `../.cursor/mcp.json` for Cursor.
