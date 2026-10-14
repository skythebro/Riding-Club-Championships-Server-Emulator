# Contributing

Thank you for helping. This project keeps a dead game playable, and most of what is left needs people who played it.

## Where help is most useful

**1. Memories and captures of the original game.** The server could not recover some numbers, so it uses a choice of its own. Anything real replaces a guess. Screenshots, videos, old notes, and packet captures all count.

- The XP needed for each of the 60 levels. Every level is 100 XP today.
- The level-up reward. The server pays 100 coins and 1 skill ticket. The original is unknown.
- The starting stats of a new horse. They start at 0.
- The upgrade table for items (levels 2 to 6) and what each level gave.
- The quest cards: what was offered, and the rewards.
- The ids of the showdown jacket, the apple slice, and the lucky leaf.
- Prices that `balance.json` marks as a server choice in its `_note`.

**2. Testing in the real client.** The unchecked lines in [`STATUS.md`](STATUS.md) are things nobody has tried. The most valuable test is two real PCs on one server: chat, challenges, ranked between humans, clubs, and the showdown board. Try a Linux host on other distributions, and on a Raspberry Pi if you have one.

**3. The Windows always-on option.** Run `RCCServer.exe service install` from an administrator prompt, reboot, and report whether the server came up. It is tested only as generated XML so far.

**4. Missing text.** Some screens show a text key instead of a sentence, such as `daily login claim reward message` before it was fixed. Walk through the game, note any, and add the key to `gamedata/localization_extra.json`. Keys in scene prefabs do not appear in the code, so only play finds them.

**5. Code.**

- A Docker image (left out of the first release).
- Moving the practice, shop, and profile handlers out of `game_features.py` into area files, as clubs and chat are.
- More tests for handlers that only the real client has exercised.
- A friendlier setup tool: a small window that writes `server.json`, forwards a port, and makes `servers.txt`.

**6. Words.** Screenshots or a short video of setup for the README, and a translation of the setup guide.

## Not wanted

- Anything that makes the server trust a number sent by the client. Coins, rating, XP, and prizes come from the server's own state.
- Steam checkout, Pro Rider, or Kickstarter support. They stay failures on purpose.
- Game files, decompiled code, or `users.db` in a commit.

## Set up

Python 3.10 or newer.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt     # Windows
.venv/bin/python -m pip install -r requirements-dev.txt         # Linux
```

Before a pull request:

```bash
python -m unittest test_server_rules test_game_rules test_igor_codec
ruff check .
```

`test_igor_codec` needs the game's own DLLs and `igor_host/IgorHost.exe`, and it uses TCP port 27130, so stop a running server first. The other two tests run anywhere. CI runs those two on Windows and Ubuntu. The tests use a temporary database and never touch `users.db`.

New behaviour gets a test, and a checklist line in `STATUS.md` until someone has tried it in the real client. Docs use short, plain sentences.

[`AGENTS.md`](AGENTS.md) explains how a packet flows through the code and which file holds what. It is written for people and for coding agents. [`ROADMAP.md`](ROADMAP.md) says what is done and what is next.

## Trying a change in the real client

Start the server, then the game, with `servers.txt` pointing at it. The newest file in `debug_logs/` names the last call when the client drops. `NO REPLY` on a call that opened an RPC means it needs a handler. `NO REPLY` on a void call is correct.

The optional `agent_mcp/server.py` is an MCP server that drives the running game through a MelonLoader mod (`client_bridge/RccAgentBridge.cs`, on `127.0.0.1:27140`). It gives an assistant three tools: `game_buttons` lists visible buttons, `game_click` presses one, and `game_scene` opens a scene such as `menu`, `shop`, `paddock`, `showdown`, or `editor`. Build the bridge as a MelonLoader mod and put the dll in the game's `Mods` folder.

## Releases

Pushing a tag that starts with `v` builds `RCCServer-<version>.zip` and `RCCServer-linux-<version>.tar.gz` on GitHub and attaches them to a release. `python build_release.py <version>` builds both locally.

## Licence

The repo has no licence file yet, so the code is all rights reserved by default. If you plan a large contribution, ask the owner to add one first.
