"""One way to open the save, so every connection is committed and closed."""

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# TCP clients run on their own threads. Wait for a writer instead of failing.
BUSY_TIMEOUT_SECONDS = 10


@contextmanager
def connect_db(path):
    """Commit on success, roll back on error, and always close.

    ``with sqlite3.connect(...)`` alone commits but leaves the handle open,
    which leaks one file handle per call and keeps the file locked on Windows.
    """
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def backup_db(path, note="auto", keep=None):
    """Copy the save into backups/ while the server keeps running. Returns the new file.

    keep: how many "auto" backups to leave; older ones are deleted. Manual and
    pre-reset backups are never deleted.
    """
    source = Path(path).resolve()
    folder = source.parent / "backups"
    folder.mkdir(exist_ok=True)
    target = folder / ("%s-%s-%s.db" % (source.stem, datetime.now().strftime("%Y%m%d-%H%M%S"), note))
    src = sqlite3.connect(str(source), timeout=BUSY_TIMEOUT_SECONDS)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if keep:
        autos = sorted(folder.glob("%s-*-auto.db" % source.stem))
        for old in autos[:-int(keep)]:
            old.unlink(missing_ok=True)
    return target


def prune_logs(folder, days):
    """Delete packet logs older than this many days."""
    folder = Path(folder)
    if not days or not folder.is_dir():
        return 0
    cutoff = time.time() - float(days) * 86400
    removed = 0
    for file in folder.glob("*.log"):
        if file.stat().st_mtime < cutoff:
            file.unlink(missing_ok=True)
            removed += 1
    return removed
