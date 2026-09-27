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

## Setup

1. Install Python 3.7 or newer.
2. From this folder:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install fastapi uvicorn websockets aiofiles python-multipart
```

3. Copy `gamedata/wardrobe.json`, `gamedata/course_items.json`, and `gamedata/localization_extra.json` into the game's `UserData` folder, next to `rcc_Data`. The card host reads them from there.

4. Build the card host from `igor_host` if `IgorHost.exe` is missing, then start the host before the server. It listens on `127.0.0.1:27141`.

```bat
cd igor_host
C:\WINDOWS\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /target:exe /main:Host /r:System.Web.Extensions.dll /out:IgorHost.exe Host.cs
IgorHost.exe "<game>\rcc_Data\Managed" 27141
```

Then, from this folder:

```bash
python Server.py
```

`start_server.bat` creates the virtual environment and starts `Server.py`. Start the card host first.

The client has to point at this machine. `AssetbundleEditServer.py` rewrites `resources.assets`. Replacing `servers.txt` alone is not enough, because the game reads the host from the asset bundle.

```json
[
    {
        "Name": "local_emulator",
        "WebSocketUrl": "ws://127.0.0.1:80/websocket",
        "Host": "127.0.0.1",
        "Port": 27130,
        "PolicyPort": 27132,
        "ServerURL": "http://127.0.0.1:80/mochiweb/",
        "ImageURL": "http://127.0.0.1:80/rcc/",
        "ProxyURL": "http://127.0.0.1:80/",
        "FacebookOGUrl": "http://127.0.0.1:80/rcc/open_graph/",
        "FacebookApp": "ridingclub"
    }
]
```

| Port | Use |
| --- | --- |
| 27130 | Game TCP |
| 27132 | Policy file |
| 80 | HTTP and the legacy websocket |
| 27141 | Card host, local only |

Port 80 needs an administrator shell on Windows, or change the HTTP port.

`http://127.0.0.1:80/health` should answer while the server is up.

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

Fork, keep new replies in `game_features.py`, and write cards through the host rather than hand-packed bytes. Test with the Steam client. The open items are the unchecked boxes in [`STATUS.md`](STATUS.md).

## Legal

Educational and preservation use. Own a copy of the game. This software is provided as-is.
