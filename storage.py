"""One way to open the save, so every connection is committed and closed."""

import sqlite3
from contextlib import contextmanager

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
