"""Server owner tools: bans, chat mutes, coins, renames, resets, kicks, and backups.

Run from a shell while the server is up, and changes reach players at once:

    RCCServer.exe admin list [search]
    RCCServer.exe admin ban <rider> [--days N] [--reason TEXT]
    RCCServer.exe admin unban <rider>
    RCCServer.exe admin mute <rider> [--minutes N] [--reason TEXT]
    RCCServer.exe admin unmute <rider>
    RCCServer.exe admin coins <rider> <amount>          (negative takes coins away)
    RCCServer.exe admin pro <rider> on|off|default       (default follows everyone_pro in server.json)
    RCCServer.exe admin rename <rider> <name>
    RCCServer.exe admin reset <rider> --yes              (fresh rider and horse, same Steam account)
    RCCServer.exe admin kick <rider>
    RCCServer.exe admin backup

<rider> is a player id, a Steam id, or a rider name. With the server off, the same
commands edit users.db directly (kick needs the server).
"""

import json
import sqlite3
import struct
import time
from datetime import datetime

from storage import backup_db, connect_db


class AdminError(Exception):
    pass


def now_ms():
    return int(time.time() * 1000)


def ensure_tables(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS bans (
            steam_id TEXT PRIMARY KEY,
            player_id INTEGER,
            reason TEXT,
            until_ms INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mutes (
            player_id INTEGER PRIMARY KEY,
            reason TEXT,
            since_ms INTEGER,
            until_ms INTEGER
        )"""
    )


def ban_reason(db_path, steam_id):
    """The refusal text for a banned Steam id, or None. An expired ban is removed."""
    with connect_db(db_path) as conn:
        ensure_tables(conn)
        row = conn.execute("SELECT reason, until_ms FROM bans WHERE steam_id = ?", (str(steam_id),)).fetchone()
        if row is None:
            return None
        reason, until = row
        if until and until <= now_ms():
            conn.execute("DELETE FROM bans WHERE steam_id = ?", (str(steam_id),))
            return None
    text = "You are banned from this server."
    if reason:
        text += " Reason: %s." % reason
    if until:
        text += " Until %s." % datetime.fromtimestamp(until / 1000).strftime("%Y-%m-%d %H:%M")
    return text


def active_mute(db_path, player_id):
    """(since_ms, until_ms, reason) while a rider is muted, else None. An expired mute is removed."""
    if not player_id:
        return None
    with connect_db(db_path) as conn:
        ensure_tables(conn)
        row = conn.execute("SELECT since_ms, until_ms, reason FROM mutes WHERE player_id = ?", (player_id,)).fetchone()
        if row is None:
            return None
        if row[1] and row[1] <= now_ms():
            conn.execute("DELETE FROM mutes WHERE player_id = ?", (player_id,))
            return None
    return int(row[0] or 0), int(row[1] or 0), row[2] or ""


def admin_player_ids(db_path, steam_ids):
    if not steam_ids:
        return []
    marks = ",".join("?" for _item in steam_ids)
    with connect_db(db_path) as conn:
        rows = conn.execute(
            "SELECT player_id FROM users WHERE source_type = 'Steam' AND source_id IN (%s)" % marks,
            [str(item) for item in steam_ids],
        ).fetchall()
    return sorted(int(row[0]) for row in rows)


# Client frames. Chat is service 103, Game is 108.
def _varint(value):
    out = bytearray()
    while True:
        byte = value & 127
        value >>= 7
        out.append(byte | (128 if value else 0))
        if not value:
            return bytes(out)


def _frame(service, function, payload):
    body = bytes([service, function]) + payload
    return _varint(len(body)) + body


def _string(text):
    raw = (text or "").encode("utf-8")
    return _varint(len(raw)) + raw


def muted_frame(since_ms, until_ms, reason):
    """Chat.Banned: the client prints how long and why in every chat window."""
    # A permanent mute still needs an end the client can show. 10 years reads as "forever".
    until_ms = until_ms or since_ms + 10 * 365 * 86400 * 1000
    return _frame(103, 14, struct.pack("<QQ", since_ms, until_ms) + _string(reason or "muted by the server"))


def unmuted_frame():
    return _frame(103, 15, b"")


def remove_messages_frame(player_id):
    """Chat.RemovePlayerMessage: drop this rider's lines from every chat window."""
    return _frame(103, 10, struct.pack("<I", int(player_id)))


def admin_users_frame(player_ids):
    """Game.AdminUsers: these riders get the admin marker in chat."""
    return _frame(108, 19, _varint(len(player_ids)) + b"".join(struct.pack("<I", int(item)) for item in player_ids))


class Admin:
    """Owner commands on a save. Pass the running emulator to also reach connected players."""

    def __init__(self, db_path, emulator=None):
        self.db_path = str(db_path)
        self.emulator = emulator
        with connect_db(self.db_path) as conn:
            ensure_tables(conn)

    def find(self, who):
        who = str(who).strip()
        with connect_db(self.db_path) as conn:
            if who.isdigit() and len(who) >= 15:
                row = conn.execute(
                    "SELECT player_id, source_id FROM users WHERE source_type = 'Steam' AND source_id = ?", (who,)
                ).fetchone()
            elif who.isdigit():
                row = conn.execute("SELECT player_id, source_id FROM users WHERE player_id = ?", (int(who),)).fetchone()
            else:
                rows = conn.execute(
                    """SELECT u.player_id, u.source_id FROM users u JOIN player_data p ON p.player_id = u.player_id
                       WHERE LOWER(p.name) = LOWER(?)""",
                    (who,),
                ).fetchall()
                if len(rows) > 1:
                    raise AdminError("More than one rider is named %s. Use the player id." % who)
                row = rows[0] if rows else None
        if row is None:
            raise AdminError("No rider matches %s." % who)
        return int(row[0]), str(row[1])

    def _online_clients(self, player_id):
        if self.emulator is None:
            return []
        return [client for client, owner in list(self.emulator.client_players.items()) if owner == player_id]

    def _push_session(self, player_id):
        if self.emulator is None:
            return
        frame = self.emulator.features.session_push(player_id)
        for client in self._online_clients(player_id):
            self.emulator.send_to(client, frame)

    def list(self, search=""):
        online = set(self.emulator.client_players.values()) if self.emulator else set()
        with connect_db(self.db_path) as conn:
            ensure_tables(conn)
            rows = conn.execute(
                """SELECT u.player_id, u.source_type, u.source_id, p.name, p.horse_name, p.coins, p.horse_level,
                          u.last_login, b.reason IS NOT NULL, m.player_id IS NOT NULL
                   FROM users u
                   LEFT JOIN player_data p ON p.player_id = u.player_id
                   LEFT JOIN bans b ON b.steam_id = u.source_id
                   LEFT JOIN mutes m ON m.player_id = u.player_id
                   ORDER BY u.player_id"""
            ).fetchall()
        riders = []
        for row in rows:
            rider = {
                "id": row[0], "steam_id": row[2] if row[1] == "Steam" else "", "name": row[3] or "",
                "horse": row[4] or "", "coins": row[5] or 0, "level": row[6] or 1, "last_login": row[7] or "",
                "banned": bool(row[8]), "muted": bool(row[9]), "online": row[0] in online,
            }
            text = ("%s %s %s %s" % (rider["id"], rider["steam_id"], rider["name"], rider["horse"])).lower()
            if not search or search.lower() in text:
                riders.append(rider)
        return riders

    def ban(self, who, days=None, reason=""):
        player_id, steam_id = self.find(who)
        until = now_ms() + int(float(days) * 86400 * 1000) if days else None
        with connect_db(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO bans (steam_id, player_id, reason, until_ms) VALUES (?, ?, ?, ?)",
                (steam_id, player_id, reason or "", until),
            )
        kicked = self._close(player_id)
        return "Banned rider %s%s.%s" % (
            player_id, " for %s days" % days if days else "", " Disconnected." if kicked else ""
        )

    def unban(self, who):
        player_id, steam_id = self.find(who)
        with connect_db(self.db_path) as conn:
            gone = conn.execute("DELETE FROM bans WHERE steam_id = ?", (steam_id,)).rowcount
        return "Unbanned rider %s." % player_id if gone else "Rider %s was not banned." % player_id

    def mute(self, who, minutes=None, reason=""):
        player_id, _steam_id = self.find(who)
        since = now_ms()
        until = since + int(float(minutes) * 60 * 1000) if minutes else None
        with connect_db(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mutes (player_id, reason, since_ms, until_ms) VALUES (?, ?, ?, ?)",
                (player_id, reason or "", since, until),
            )
        if self.emulator is not None:
            for client in self._online_clients(player_id):
                self.emulator.send_to(client, muted_frame(since, until, reason))
            wipe = remove_messages_frame(player_id)
            for client in list(self.emulator.tcp_clients):
                self.emulator.send_to(client, wipe)
        return "Muted rider %s%s." % (player_id, " for %s minutes" % minutes if minutes else "")

    def unmute(self, who):
        player_id, _steam_id = self.find(who)
        with connect_db(self.db_path) as conn:
            gone = conn.execute("DELETE FROM mutes WHERE player_id = ?", (player_id,)).rowcount
        for client in self._online_clients(player_id):
            self.emulator.send_to(client, unmuted_frame())
        return "Unmuted rider %s." % player_id if gone else "Rider %s was not muted." % player_id

    def coins(self, who, amount):
        player_id, _steam_id = self.find(who)
        amount = int(amount)
        with connect_db(self.db_path) as conn:
            conn.execute(
                "UPDATE player_data SET coins = MAX(0, COALESCE(coins, 0) + ?) WHERE player_id = ?",
                (amount, player_id),
            )
            total = conn.execute("SELECT coins FROM player_data WHERE player_id = ?", (player_id,)).fetchone()
        self._push_session(player_id)
        return "Rider %s now has %s coins." % (player_id, total[0] if total else 0)

    def pro(self, who, mode):
        """on or off for one rider, or default to follow the everyone_pro setting again."""
        mode = str(mode).lower()
        if mode not in ("on", "off", "default"):
            raise AdminError("pro takes on, off, or default.")
        player_id, _steam_id = self.find(who)
        value = {"on": 1, "off": 0, "default": None}[mode]
        with connect_db(self.db_path) as conn:
            conn.execute("UPDATE player_data SET pro_override = ? WHERE player_id = ?", (value, player_id))
        self._push_session(player_id)
        if self.emulator is not None:
            self.emulator.features.broadcast_pro()
        return "Rider %s: Pro Rider %s." % (player_id, "follows the server setting" if value is None else mode)

    def rename(self, who, name):
        player_id, _steam_id = self.find(who)
        name = (name or "").strip()
        if not name or len(name) > 30:
            raise AdminError("A rider name is 1 to 30 characters.")
        with connect_db(self.db_path) as conn:
            conn.execute("UPDATE player_data SET name = ? WHERE player_id = ?", (name, player_id))
        self._push_session(player_id)
        return "Rider %s is now %s." % (player_id, name)

    def reset(self, who, confirmed=False):
        """A fresh start for one Steam account: the next login makes a new rider and horse."""
        if not confirmed:
            raise AdminError("reset deletes this rider's progress. Add --yes to do it.")
        player_id, _steam_id = self.find(who)
        self._close(player_id)
        backup = self.backup(note="before-reset-%s" % player_id)
        with connect_db(self.db_path) as conn:
            for table, column in (
                ("player_data", "player_id"), ("course_slots", "player_id"), ("guild_members", "player_id"),
                ("guild_requests", "player_id"), ("course_likes", "player_id"), ("mutes", "player_id"),
            ):
                try:
                    conn.execute("DELETE FROM %s WHERE %s = ?" % (table, column), (player_id,))
                except sqlite3.OperationalError:
                    pass
            conn.execute("UPDATE users SET user_state = 1 WHERE player_id = ?", (player_id,))
        return "Rider %s was reset. The old save is in %s." % (player_id, backup)

    def kick(self, who):
        player_id, _steam_id = self.find(who)
        if self.emulator is None:
            raise AdminError("kick needs the server to be running.")
        return "Kicked rider %s." % player_id if self._close(player_id) else "Rider %s is not online." % player_id

    def _close(self, player_id):
        closed = False
        for client in self._online_clients(player_id):
            sock = self.emulator.tcp_clients.get(client)
            if sock is not None:
                try:
                    sock.close()
                    closed = True
                except OSError:
                    pass
        return closed

    def backup(self, note="manual"):
        return str(backup_db(self.db_path, note=note))

    def run(self, command, args):
        """One entry point for the CLI and for /admin on the running server."""
        handlers = {
            "list": lambda: self.list(args.get("search") or ""),
            "ban": lambda: self.ban(args["rider"], args.get("days"), args.get("reason") or ""),
            "unban": lambda: self.unban(args["rider"]),
            "mute": lambda: self.mute(args["rider"], args.get("minutes"), args.get("reason") or ""),
            "unmute": lambda: self.unmute(args["rider"]),
            "coins": lambda: self.coins(args["rider"], args["amount"]),
            "pro": lambda: self.pro(args["rider"], args["mode"]),
            "rename": lambda: self.rename(args["rider"], args["name"]),
            "reset": lambda: self.reset(args["rider"], bool(args.get("yes"))),
            "kick": lambda: self.kick(args["rider"]),
            "backup": lambda: self.backup(),
        }
        if command not in handlers:
            raise AdminError("Unknown command %s." % command)
        return handlers[command]()


def format_riders(riders):
    lines = ["%-4s %-17s %-20s %-16s %7s %3s  %s" % ("id", "steam id", "rider", "horse", "coins", "lvl", "flags")]
    for rider in riders:
        flags = " ".join(flag for flag, on in (
            ("online", rider["online"]), ("BANNED", rider["banned"]), ("muted", rider["muted"])) if on)
        lines.append("%-4s %-17s %-20s %-16s %7s %3s  %s" % (
            rider["id"], rider["steam_id"], rider["name"][:20], rider["horse"][:16], rider["coins"], rider["level"], flags))
    return "\n".join(lines)


def _has_save(db_path):
    """True when users.db exists and the server has made its tables. Never creates the file."""
    from pathlib import Path
    if not Path(db_path).exists():
        return False
    conn = sqlite3.connect("file:%s?mode=ro" % Path(db_path).resolve().as_posix(), uri=True)
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone() is not None
    finally:
        conn.close()


def cli(argv, db_path, http_port):
    """RCCServer.exe admin ...: use the running server if it answers, else edit the save directly."""
    import argparse
    import urllib.error
    import urllib.request

    parser = argparse.ArgumentParser(prog="RCCServer.exe admin", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list").add_argument("search", nargs="?", default="")
    for name in ("ban", "mute"):
        command = sub.add_parser(name)
        command.add_argument("rider")
        command.add_argument("--days" if name == "ban" else "--minutes", type=float)
        command.add_argument("--reason", default="")
    for name in ("unban", "unmute", "kick"):
        sub.add_parser(name).add_argument("rider")
    coins = sub.add_parser("coins")
    coins.add_argument("rider")
    coins.add_argument("amount", type=int)
    pro = sub.add_parser("pro")
    pro.add_argument("rider")
    pro.add_argument("mode", choices=("on", "off", "default"))
    rename = sub.add_parser("rename")
    rename.add_argument("rider")
    rename.add_argument("name")
    reset = sub.add_parser("reset")
    reset.add_argument("rider")
    reset.add_argument("--yes", action="store_true")
    sub.add_parser("backup")
    args = vars(parser.parse_args(argv))
    command = args.pop("command")

    request = urllib.request.Request(
        "http://127.0.0.1:%s/admin/%s" % (http_port, command),
        data=json.dumps(args).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            reply = json.loads(response.read().decode("utf-8"))
        live = True
    except urllib.error.HTTPError as error:
        reply = json.loads(error.read().decode("utf-8") or "{}")
        live = True
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        live = False
        try:
            if not _has_save(db_path):
                raise AdminError("There is no save yet at %s. Start the server once first." % db_path)
            reply = {"ok": True, "result": Admin(db_path).run(command, args)}
        except (AdminError, sqlite3.Error) as error:
            reply = {"ok": False, "error": str(error)}
    if not reply.get("ok"):
        print("Error:", reply.get("error") or reply)
        return 1
    result = reply.get("result")
    print(format_riders(result) if command == "list" else result)
    if not live:
        print("(The server is not running, so this used users.db directly.)")
    return 0
