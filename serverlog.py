"""The server's own log file, and one command that bundles everything needed to report a problem.

setup() writes logs/server.log next to the server (2 MB each, 5 kept). It records start-up, logins,
refusals, warnings, and any crash with its traceback. collect() makes rcc-logs-<time>.zip from that log,
the newest packet logs, the game's own log, the settings with private values removed, and a summary.
"""

import json
import logging
import platform
import sqlite3
import sys
import threading
import time
import zipfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "server.log"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
PRIVATE_KEYS = ("public_host", "allowed_steam_ids", "admin_steam_ids")
TAIL_BYTES = 1_000_000


def version(app_dir):
    """The release number written into the download, or "source" when run from the repository."""
    try:
        text = (Path(app_dir) / "version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "source"
    return text or "source"


def log_dir(app_dir):
    return Path(app_dir) / "logs"


def setup(app_dir):
    """Send every log line to console and logs/server.log. Uncaught errors are logged with a traceback."""
    folder = log_dir(app_dir)
    folder.mkdir(exist_ok=True)
    handler = RotatingFileHandler(folder / LOG_NAME, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, RotatingFileHandler) for item in root.handlers):
        root.addHandler(handler)
    log = logging.getLogger("rcc.crash")

    def on_error(kind, value, trace):
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, trace)
            return
        log.critical("Uncaught error", exc_info=(kind, value, trace))

    def on_thread_error(args):
        if args.exc_type is SystemExit:
            return
        log.critical("Uncaught error in thread %s", getattr(args.thread, "name", "?"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = on_error
    threading.excepthook = on_thread_error
    return folder / LOG_NAME


def describe_start(app_dir, config, game_dir):
    """The first lines of every run. They say which build, where, and with which settings."""
    log = logging.getLogger("rcc.start")
    log.info("Riding Club Championships server %s", version(app_dir))
    log.info("Python %s on %s", platform.python_version(), platform.platform())
    log.info("Game folder: %s", game_dir or "NOT FOUND")
    shown = {key: value for key, value in config.items() if key not in PRIVATE_KEYS}
    log.info("Settings: %s", json.dumps(shown, sort_keys=True))


def _tail(path, limit=TAIL_BYTES):
    size = path.stat().st_size
    with open(path, "rb") as handle:
        if size > limit:
            handle.seek(size - limit)
            handle.readline()
        return handle.read()


def redacted_config(config):
    clean = {}
    for key, value in config.items():
        if key in PRIVATE_KEYS:
            clean[key] = "<set>" if value else "<empty>"
        else:
            clean[key] = value
    return clean


def _database_summary(db_path):
    path = Path(db_path)
    if not path.exists():
        return "no save yet"
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=5)
        try:
            schema = conn.execute("PRAGMA user_version").fetchone()[0]
            riders = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            clubs = conn.execute("SELECT COUNT(*) FROM guilds").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as error:
        return "unreadable: %s" % error
    return "save version %s, %s riders, %s clubs" % (schema, riders, clubs)


def collect(app_dir, config, game_dir=None, out=None):
    """Write the report zip and return its path. users.db is never included."""
    app_dir = Path(app_dir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(out) if out else app_dir / ("rcc-logs-%s.zip" % stamp)
    notes = [
        "Riding Club Championships server report",
        "Made: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "Server: %s" % version(app_dir),
        "Python: %s" % platform.python_version(),
        "System: %s" % platform.platform(),
        "Game folder: %s" % (game_dir or "not found"),
        "Save: %s" % _database_summary(app_dir / "users.db"),
        "",
        "This zip holds the server log, the newest packet logs, the game's own log, and the settings",
        "with the public address and Steam id lists hidden. It does not hold users.db.",
    ]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.txt", "\n".join(notes) + "\n")
        archive.writestr("server.json (private values hidden).txt",
                         json.dumps(redacted_config(config), indent=2, sort_keys=True))
        for file in sorted(log_dir(app_dir).glob(LOG_NAME + "*")):
            archive.writestr("logs/" + file.name, _tail(file))
        packet_logs = sorted((app_dir / "debug_logs").glob("*.log"), key=lambda item: item.stat().st_mtime)
        for file in packet_logs[-3:]:
            archive.writestr("debug_logs/" + file.name, _tail(file))
        for label, source in game_logs(game_dir):
            archive.writestr(label, _tail(source))
    return target


def game_logs(game_dir):
    """(name in the zip, file) for each game log on this PC. A Mac game logs to ~/Library/Logs/Unity."""
    found = []
    candidates = [("game/Player.log (Mac)", Path.home() / "Library" / "Logs" / "Unity" / "Player.log")]
    if game_dir:
        candidates[:0] = [
            ("game/output_log.txt", Path(game_dir) / "rcc_Data" / "output_log.txt"),
            ("game/MelonLoader_Latest.log", Path(game_dir) / "MelonLoader" / "Latest.log"),
        ]
    for label, source in candidates:
        if source.is_file():
            found.append((label, source))
    return found
