"""Numbered changes to the save. Add a new function and a line in MIGRATIONS; never edit an old one.

The save remembers its version in SQLite's user_version, so each step runs once.
"""

import sqlite3

PROFILE_COLUMNS = (
    ("horse_name", "TEXT"),
    ("horse_gender", "INTEGER DEFAULT 0"),
    ("player_gender", "INTEGER DEFAULT 0"),
    ("player_skin", "INTEGER DEFAULT 0"),
    ("horse_skin", "INTEGER DEFAULT 0"),
    ("tail_skin", "INTEGER DEFAULT 0"),
    ("coins", "INTEGER DEFAULT 500"),
    ("skill_tickets", "INTEGER DEFAULT 5"),
    ("action_points", "INTEGER DEFAULT 100"),
    ("horse_level", "INTEGER DEFAULT 1"),
    ("horse_xp", "INTEGER DEFAULT 0"),
    ("grooming", "REAL DEFAULT 1"),
    ("inventory", "TEXT DEFAULT '[]'"),
    ("rating", "INTEGER DEFAULT 1000"),
    ("guild_open", "INTEGER DEFAULT 1"),
    ("skill_points", "INTEGER DEFAULT 0"),
    ("skills", "TEXT DEFAULT '[]'"),
    ("ds_claimed", "INTEGER DEFAULT 0"),
    # YYYYMMDD of the Monday of the last week whose result popup this rider has been shown.
    ("ds_week_seen", "INTEGER DEFAULT 0"),
)


def _baseline(conn):
    """Every table and column the server had before numbered migrations. Safe to run on an old save."""
    for name, declaration in PROFILE_COLUMNS + (
        ("equipment", "TEXT"),
        ("rider_equipment", "TEXT"),
        ("daily_claim", "TEXT"),
        ("daily_day", "INTEGER DEFAULT 1"),
    ):
        try:
            conn.execute("ALTER TABLE player_data ADD COLUMN %s %s" % (name, declaration))
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """CREATE TABLE IF NOT EXISTS courses (
            course_id INTEGER PRIMARY KEY,
            owner_id INTEGER,
            name TEXT,
            body TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS course_slots (
            player_id INTEGER,
            slot_id INTEGER,
            body TEXT,
            PRIMARY KEY (player_id, slot_id)
        )"""
    )
    for name in ("min_time INTEGER DEFAULT 60", "played INTEGER DEFAULT 0", "completed INTEGER DEFAULT 0"):
        try:
            conn.execute("ALTER TABLE courses ADD COLUMN %s" % name)
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """CREATE TABLE IF NOT EXISTS course_reports (
            player_id INTEGER,
            course_id INTEGER,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS course_likes (
            player_id INTEGER,
            course_id INTEGER,
            PRIMARY KEY (player_id, course_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS guilds (
            guild_id INTEGER PRIMARY KEY,
            name TEXT,
            owner_id INTEGER,
            description TEXT,
            welcome TEXT
        )"""
    )
    for name, declaration in (("short_name", "TEXT"), ("icon", "TEXT"), ("invite", "INTEGER DEFAULT 1")):
        try:
            conn.execute("ALTER TABLE guilds ADD COLUMN %s %s" % (name, declaration))
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """CREATE TABLE IF NOT EXISTS guild_requests (
            guild_id INTEGER,
            player_id INTEGER,
            kind TEXT,
            PRIMARY KEY (guild_id, player_id, kind)
        )"""
    )
    for name, declaration in (("actor_id", "INTEGER"), ("created_at", "INTEGER")):
        try:
            conn.execute("ALTER TABLE guild_requests ADD COLUMN %s %s" % (name, declaration))
        except sqlite3.OperationalError:
            pass
    conn.execute(
        """CREATE TABLE IF NOT EXISTS guild_members (
            guild_id INTEGER,
            player_id INTEGER,
            role INTEGER,
            PRIMARY KEY (guild_id, player_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS player_reports (
            reporter_id INTEGER,
            target_id INTEGER,
            kind TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS quest_flags (
            player_id INTEGER,
            quest_id INTEGER,
            status TEXT,
            PRIMARY KEY (player_id, quest_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ignored_players (
            player_id INTEGER,
            target_id INTEGER,
            ignored INTEGER,
            PRIMARY KEY (player_id, target_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS ds_results (
            player_id INTEGER,
            course_hash INTEGER,
            time REAL,
            penalty INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS course_scores (
            player_id INTEGER,
            course_id INTEGER,
            time REAL,
            penalty INTEGER,
            PRIMARY KEY (player_id, course_id)
        )"""
    )


# Timed item effects. Each holds the Unix time the effect ends, 0 for none.
BOOST_COLUMNS = (
    ("xp_boost_until", "INTEGER DEFAULT 0"),
    ("loot_boost_until", "INTEGER DEFAULT 0"),
    ("stat_boost_until", "INTEGER DEFAULT 0"),
)


def _item_effects(conn):
    """Columns for the XP book, the lucky leaf, and the sugar cubes."""
    for name, declaration in BOOST_COLUMNS:
        try:
            conn.execute("ALTER TABLE player_data ADD COLUMN %s %s" % (name, declaration))
        except sqlite3.OperationalError:
            pass


def _pro_override(conn):
    """pro_override: NULL follows the server's everyone_pro setting, 1 is Pro, 0 is not."""
    try:
        conn.execute("ALTER TABLE player_data ADD COLUMN pro_override INTEGER")
    except sqlite3.OperationalError:
        pass


# (version, what it does, function(conn)). Versions count up from 1 with no gaps.
MIGRATIONS = (
    (1, "tables and columns up to the first numbered release", _baseline),
    (2, "timed item effects", _item_effects),
    (3, "per-rider Pro Rider override", _pro_override),
)


def migrate(conn):
    """Run every step the save has not seen. Returns the version it ends on."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, _what, step in MIGRATIONS:
        if version > current:
            step(conn)
            conn.execute("PRAGMA user_version = %d" % version)
            current = version
    return current
