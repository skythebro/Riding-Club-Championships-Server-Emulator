"""Write the servers.txt a player puts next to rcc.exe (or inside the Mac app) to join this server.

The game reads servers.txt before its built-in server list, and on the normal
Steam branch it picks the entry named "live". So this one file is all a player
needs. Nothing inside the game files is changed.

    python client_config.py                      # this PC, written into the game folder
    python client_config.py 203.0.113.7          # for players on other machines, written here
    python client_config.py myhost.example --out C:/somewhere/servers.txt
"""

import argparse
import json
from pathlib import Path

from paths import APP_DIR, find_game_dir, servers_txt_path
from server_config import load_config

ROOT = APP_DIR


def servers_entry(host, config):
    http = "http://%s:%s/" % (host, config["http_port"])
    return {
        # The client selects the server whose Name is in its Steam branch name, "live" by default.
        "Name": "live",
        "WebSocketUrl": "ws://%s:%s/websocket" % (host, config["http_port"]),
        "Host": host,
        "Port": config["tcp_port"],
        "PolicyPort": config["policy_port"],
        # The Steam client only uses these URLs for Facebook and VK features.
        "ServerURL": http + "mochiweb/",
        "ImageURL": http + "rcc/",
        "ProxyURL": http,
        "FacebookOGUrl": http + "rcc/open_graph/",
        "FacebookApp": "ridingclub",
    }


def servers_text(host, config):
    return json.dumps([servers_entry(host, config)], indent=4).replace("\n", "\r\n") + "\r\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", nargs="?", help="address players connect to (default: public_host in server.json, else 127.0.0.1)")
    parser.add_argument("--out", help="where to write servers.txt")
    args = parser.parse_args(argv)

    config = load_config()
    host = args.host or config.get("public_host") or "127.0.0.1"
    game_dir = find_game_dir(config.get("game_dir") or None)
    if args.out:
        out = Path(args.out)
    elif host == "127.0.0.1" and game_dir is not None:
        out = servers_txt_path(game_dir)
    else:
        out = ROOT / "servers.txt"
    if out.exists():
        backup = out.with_name(out.name + ".before-emu")
        if not backup.exists():
            backup.write_bytes(out.read_bytes())
            print("Kept the old file as %s" % backup)
    out.write_text(servers_text(host, config), encoding="utf-8", newline="")
    print("Wrote %s for %s:%s" % (out, host, config["tcp_port"]))
    if game_dir is None or out != servers_txt_path(game_dir):
        print("Give this file to players. On Windows it goes next to rcc.exe. On a Mac it goes inside the game app,")
        print("beside its Contents folder (right-click the app, Show Package Contents).")


if __name__ == "__main__":
    main()
