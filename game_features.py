"""Persisted solo play, courses, and multiplayer for clients connected to this server."""

import datetime
import json
import sqlite3
import struct
import threading
import time
import zlib

from paths import APP_DIR
from server_config import ride_is_plausible
from storage import connect_db

MAP_PATH = APP_DIR / "igor_host" / "protocol_map.json"

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
)


class GameFeatures:
    # The login burst sends these before the Login reply has been read.
    BEFORE_LOGIN = frozenset({"Subscribe"})

    # Protocol2.GuildMemberStatus
    GUILD_OWNER, GUILD_MANAGER, GUILD_MEMBER = 1, 2, 3
    # Protocol2.GuildErrorType, sent as the one-byte EGuildError body.
    GUILD_ERR_UNKNOWN = 1
    GUILD_ERR_NAME_EXIST = 2
    GUILD_ERR_SHORT_NAME_EXIST = 3
    GUILD_ERR_NOT_IN_GUILD = 4
    GUILD_ERR_IN_GUILD = 5
    GUILD_ERR_FULL = 7
    GUILD_ERR_SELF = 9
    GUILD_ERR_ALREADY_MEMBER = 10
    GUILD_ERR_DENIED = 11

    def __init__(self, emulator):
        self.emulator = emulator
        self.lock = threading.RLock()
        self.waiting = []
        self.opponents = {}
        # client_id -> the human ranked match it is in. Both riders share one dict.
        self.rm_matches = {}
        self.practice_open = set()
        self.groom_at = {}
        self.slots = {}
        self.courses = {}
        self.likes = {}
        self.guilds = {}
        self.members = {}
        self.next_course = 1
        self.next_guild = 1
        self.chat_seq = 1
        self.chat_rooms = set()
        self.global_chat = set()
        self.challenges = {}
        self.catalog = {}
        if MAP_PATH.exists():
            self.catalog = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        self.balance = self._load_balance()
        self._ensure_columns()

    def _load_balance(self):
        path = APP_DIR / "balance.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def bal(self, *keys, default=None):
        node = self.balance
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def _connect(self):
        return connect_db(self.emulator.database.db_path)

    def _ensure_columns(self):
        with self._connect() as conn:
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
            for row in conn.execute(
                "SELECT course_id, owner_id, name, body, min_time, played, completed FROM courses"
            ):
                self.courses[row[0]] = {
                    "owner": row[1],
                    "name": row[2] or "",
                    "body": row[3] or "",
                    "min_time": row[4] if row[4] is not None else 60,
                    "played": row[5] or 0,
                    "completed": row[6] or 0,
                }
            self._ensure_rival(conn)
            for row in conn.execute("SELECT player_id, body FROM course_slots ORDER BY slot_id"):
                self.slots.setdefault(row[0], []).append(row[1] or "")
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

    def profile(self, player_id):
        with self._connect() as conn:
            row = conn.execute(
                """SELECT horse_name, horse_gender, player_gender, player_skin, horse_skin, tail_skin,
                          coins, skill_tickets, action_points, horse_level, horse_xp, grooming, name, rating, skill_points
                   FROM player_data WHERE player_id = ?""",
                (player_id,),
            ).fetchone()
        if not row:
            return self._blank(player_id)
        return {
            "horse_name": row[0] or "Horse",
            "horse_gender": row[1] or 0,
            "player_gender": row[2] or 0,
            "player_skin": row[3] or 0,
            "horse_skin": row[4] or 0,
            "tail_skin": row[5] or 0,
            "coins": row[6] if row[6] is not None else 500,
            "skill_tickets": row[7] if row[7] is not None else 5,
            "action_points": row[8] if row[8] is not None else 100,
            "horse_level": row[9] or 1,
            "horse_xp": row[10] or 0,
            "grooming": row[11] if row[11] is not None else 1,
            "name": row[12] or "Player",
            "rating": row[13] if row[13] is not None else 1000,
            "skill_points": row[14] if row[14] is not None else 0,
        }

    def _blank(self, player_id):
        return {
            "horse_name": "Horse", "horse_gender": 0, "player_gender": 0, "player_skin": 0,
            "horse_skin": 0, "tail_skin": 0, "coins": 500, "skill_tickets": 5, "action_points": 100,
            "horse_level": 1, "horse_xp": 0,             "grooming": 1.0, "name": "Player", "rating": 1000, "skill_points": 0,
        }

    def save_profile(self, player_id, **values):
        allowed = {name for name, _declaration in PROFILE_COLUMNS} | {"name"}
        pairs = [(key, value) for key, value in values.items() if key in allowed]
        if not pairs:
            return
        with self._connect() as conn:
            assignments = ", ".join("%s = ?" % key for key, _value in pairs)
            conn.execute(
                "UPDATE player_data SET %s WHERE player_id = ?" % assignments,
                [value for _key, value in pairs] + [player_id],
            )

    def set_user_state(self, player_id, state):
        with self._connect() as conn:
            conn.execute("UPDATE users SET user_state = ? WHERE player_id = ?", (state, player_id))

    def _user_state(self, player_id):
        with self._connect() as conn:
            row = conn.execute("SELECT user_state FROM users WHERE player_id = ?", (player_id,)).fetchone()
        if row is None or row[0] is None:
            return 0
        return int(row[0])

    def _next_tutorial_state(self, current, finished):
        # Avatar is 1, Tutorial is 2, Menu is 3. A finished account is not pulled backwards.
        current = int(current or 0)
        if current >= 3:
            return current
        return 1 if finished else 2

    def session_fields(self, player_id):
        data = self.profile(player_id)
        fields = {
            "Name": data["horse_name"],
            "Gender": data["horse_gender"],
            "SkinId": data["player_skin"],
            "TailSkinId": data["tail_skin"],
            "GroomingState": data["grooming"],
            "SkillPoints": data["skill_points"],
            "ActionPoints": data["action_points"],
            "Money": {"Coins": data["coins"], "SkillTickets": data["skill_tickets"]},
            "XpStats": {
                "Level": data["horse_level"],
                "XpCurrent": data["horse_xp"],
                "XpNextLevel": int(self.bal("xp_per_level", default=100)),
                "Progress": min(1.0, float(data["horse_xp"] or 0) / float(self.bal("xp_per_level", default=100))),
            },
        }
        attributes = self._attributes(player_id)
        if attributes:
            fields["Attributes"] = attributes
        bonuses = self._gear_bonuses(player_id)
        if bonuses:
            fields["Bonuses"] = bonuses
        return fields

    def _gear_bonuses(self, player_id):
        # Boots and breeches add action points. Helmet and jacket add restore rate.
        # Each starts at 10 and stops at 50, matching the clothes help page.
        ap_max = 0
        restore = 0.0
        ap_step = int(self.bal("gear", "ap_per_level", default=10))
        ap_cap = int(self.bal("gear", "ap_cap", default=50))
        restore_step = float(self.bal("gear", "restore_per_level", default=0.1))
        restore_cap = float(self.bal("gear", "restore_cap", default=0.5))
        for item in self.rider_equipment(player_id):
            slot = int(item.get("slot") or 0)
            level = int(item.get("level") or 1)
            if level < 1:
                level = 1
            if slot in (3, 4):
                ap_max += min(ap_cap, ap_step * level)
            elif slot in (1, 2):
                restore += min(restore_cap, restore_step * level)
        bonuses = {}
        if ap_max:
            bonuses["ApMax"] = ap_max
        if restore:
            bonuses["ApRestoreRate"] = restore
        return bonuses

    def equipment(self, player_id):
        with self._connect() as conn:
            row = conn.execute("SELECT equipment FROM player_data WHERE player_id = ?", (player_id,)).fetchone()
        if not row or not row[0]:
            return []
        try:
            return json.loads(row[0])
        except ValueError:
            return []

    def save_equipment(self, player_id, items):
        with self._connect() as conn:
            conn.execute("UPDATE player_data SET equipment = ? WHERE player_id = ?", (json.dumps(items), player_id))

    def _equipment_fields(self, player_id):
        worn = []
        for item in self.equipment(player_id):
            worn.append({
                "Slot": int(item.get("slot") or 0),
                "SlotItem": {"ItemKey": int(item.get("hash") or 0), "Level": 1, "Progress": 0},
            })
        return worn

    def rider_equipment(self, player_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT rider_equipment FROM player_data WHERE player_id = ?", (player_id,)
            ).fetchone()
        if not row or not row[0]:
            return []
        try:
            return json.loads(row[0])
        except ValueError:
            return []

    def save_rider_equipment(self, player_id, items):
        with self._connect() as conn:
            conn.execute(
                "UPDATE player_data SET rider_equipment = ? WHERE player_id = ?",
                (json.dumps(items), player_id),
            )

    def _rider_fields(self, player_id):
        worn = []
        for item in self.rider_equipment(player_id):
            worn.append({
                "Slot": int(item.get("slot") or 0),
                "SlotItem": {"ItemKey": int(item.get("hash") or 0), "Level": 1, "Progress": 0},
            })
        return worn

    # Protocol2.StatType, by the names balance.json uses.
    STAT_NAMES = {"strength": 1, "timing": 2, "speed": 3, "acceleration": 4, "stamina": 5, "obedience": 6}

    def _start_stats(self):
        """Stats every horse has before any skill point. horse_stats.start is one number or one per stat."""
        start = self.bal("horse_stats", "start", default=0)
        if isinstance(start, dict):
            values = {self.STAT_NAMES.get(str(name).lower()): value for name, value in start.items()}
        else:
            values = {stat: start for stat in range(1, 7)}
        cap = int(self.bal("stat_cap", default=10))
        result = {}
        for stat in range(1, 7):
            try:
                result[stat] = max(0, min(cap, int(values.get(stat) or 0)))
            except (TypeError, ValueError):
                result[stat] = 0
        return result

    def _spent_skills(self, player_id):
        """Skill points the rider has put into each stat."""
        with self._connect() as conn:
            row = conn.execute("SELECT skills FROM player_data WHERE player_id = ?", (player_id,)).fetchone()
        try:
            saved = json.loads((row[0] if row else None) or "[]")
        except ValueError:
            saved = []
        spent = {}
        for item in saved:
            stat = int(item.get("type") or 0)
            value = int(item.get("value") or 0)
            if stat and value:
                spent[stat] = value
        return spent

    def _attributes(self, player_id):
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM player_data WHERE player_id = ?", (player_id,)).fetchone()
        if exists is None:
            return []
        cap = int(self.bal("stat_cap", default=10))
        start = self._start_stats()
        values = {stat: min(cap, start[stat] + value) for stat, value in self._spent_skills(player_id).items() if stat in start}
        for stat, value in start.items():
            values.setdefault(stat, value)
        grooming = float(self.profile(player_id).get("grooming") or 0)
        clean_at = float(self.bal("grooming", "clean_at", default=0.8))
        dirty_at = float(self.bal("grooming", "dirty_at", default=0.3))
        if grooming >= clean_at:
            buff = int(self.bal("grooming", "clean_buff", default=1))
        elif grooming <= dirty_at:
            buff = int(self.bal("grooming", "dirty_debuff", default=-1))
        else:
            buff = 0
        # Send all six every time. Horse.SetHorseStats only overwrites the stats it receives,
        # so a stat left out keeps its old value and buff on screen.
        return [{"StatType": stat, "Value": values.get(stat, 0), "Buff": buff} for stat in range(1, 7)]

    def _gear_by_id(self):
        return {item.get("id"): item for item in self._gear_by_hash().values() if item.get("id")}

    def apply_new_player(self, player_id):
        """Starting coins, action points, skill tickets, and rating from balance.json new_player."""
        values = {
            key: int(value) for key, value in (self.bal("new_player", default={}) or {}).items()
            if key in ("coins", "action_points", "skill_tickets", "rating") and not str(key).startswith("_")
        }
        if values:
            self.save_profile(player_id, **values)

    def ensure_starter(self, player_id):
        """Dress an account that has never been given clothes or tack."""
        if not player_id:
            return
        by_id = self._gear_by_id()
        if not by_id:
            return
        rider_ids = self.bal(
            "starter", "rider",
            default=["rider_helmet_01", "rider_blazer_01", "rider_pants_01", "rider_boots_01"],
        )
        horse_ids = self.bal(
            "starter", "horse",
            default=["horse_headgear_01", "horse_saddle_01", "horse_blanket_01", "horse_ankleboots_01"],
        )
        owned = {int(item.get("hash") or 0) for item in self.inventory(player_id)}
        if not self.rider_equipment(player_id):
            worn = []
            for item_id in rider_ids:
                item = by_id.get(item_id)
                if not item or not int(item.get("player") or 0):
                    continue
                worn.append({"slot": int(item["player"]), "hash": int(item["hash"])})
                if int(item["hash"]) not in owned:
                    self.add_item(player_id, int(item["hash"]), 1)
                    owned.add(int(item["hash"]))
            if worn:
                self.save_rider_equipment(player_id, worn)
        if not self.equipment(player_id):
            worn = []
            for item_id in horse_ids:
                item = by_id.get(item_id)
                if not item or not int(item.get("gear") or 0):
                    continue
                worn.append({"slot": int(item["gear"]), "hash": int(item["hash"])})
                if int(item["hash"]) not in owned:
                    self.add_item(player_id, int(item["hash"]), 1)
                    owned.add(int(item["hash"]))
            if worn:
                self.save_equipment(player_id, worn)

    def session_push(self, player_id):
        self.ensure_starter(player_id)
        fields = self.session_fields(player_id)
        worn = self._equipment_fields(player_id)
        if worn:
            fields["Equipment"] = worn
        rider = self._rider_fields(player_id)
        if rider:
            fields["RiderEquipment"] = rider
        frame = self.emulator.igor.push_session(fields, self.inventory(player_id))
        place = self._rank_place(player_id)
        if place:
            frame += self._push_frame(109, 5, struct.pack("<i", place))
        frame += self._ignore_frame(player_id)
        frame += self._no_guild_update(player_id)
        frame += self._online_count_frame()
        frame += self._admin_users_frame()
        return self._with_global_room(player_id, frame)

    def _admin_users_frame(self):
        from admin import admin_player_ids, admin_users_frame
        steam_ids = (getattr(self.emulator, "join_config", None) or {}).get("admin_steam_ids") or []
        return admin_users_frame(admin_player_ids(self.emulator.database.db_path, steam_ids))

    def _global_room_frame(self):
        # Room 2 is the global chat. No local name and no welcome line.
        payload = bytes([0x00]) + struct.pack("<IB", 2, 1)
        return self._push_frame(103, 1, payload)

    def _with_global_room(self, player_id, frame):
        if not player_id or player_id in self.global_chat:
            return frame
        self.global_chat.add(player_id)
        from admin import active_mute, muted_frame
        mute = active_mute(self.emulator.database.db_path, player_id)
        if mute:
            frame += muted_frame(*mute)
        for client_id, owner in list((getattr(self.emulator, "client_players", {}) or {}).items()):
            if owner == player_id:
                self.chat_rooms.add((client_id, 2))
        return frame + self._global_room_frame()

    def _online_count_frame(self):
        players = getattr(self.emulator, "client_players", {}) or {}
        count = len(set(players.values()))
        return self._push_frame(108, 17, struct.pack("<i", count))

    def _broadcast_online_count(self):
        frame = self._online_count_frame()
        for other in list(getattr(self.emulator, "tcp_clients", {}) or {}):
            self.emulator.send_to(other, frame)
        return frame

    def on_Subscribe(self, message, client_id, player_id):
        frame = self._online_count_frame()
        for other in list(getattr(self.emulator, "tcp_clients", {}) or {}):
            if other != client_id:
                self.emulator.send_to(other, frame)
        self.emulator.debug_log_tcp("ONLINE COUNT: %s" % client_id)
        return frame

    def inventory(self, player_id):
        with self._connect() as conn:
            row = conn.execute("SELECT inventory FROM player_data WHERE player_id = ?", (player_id,)).fetchone()
        if not row or not row[0]:
            return []
        try:
            return json.loads(row[0])
        except ValueError:
            return []

    def save_inventory(self, player_id, items):
        with self._connect() as conn:
            conn.execute("UPDATE player_data SET inventory = ? WHERE player_id = ?", (json.dumps(items), player_id))

    def add_item(self, player_id, item_hash, count):
        items = self.inventory(player_id)
        for item in items:
            if item.get("hash") == item_hash:
                item["count"] = int(item.get("count") or 0) + int(count)
                self.save_inventory(player_id, items)
                return
        items.append({"hash": item_hash, "count": int(count)})
        self.save_inventory(player_id, items)

    def player_id(self, client_id):
        return self.emulator.client_players.get(client_id)

    def push_known_friends(self, client_id, steam_ids):
        """Tell the client which subscribed Steam ids are accounts on this server."""
        ids = []
        wanted = [str(value) for value in (steam_ids or []) if value]
        if wanted:
            marks = ",".join("?" for _value in wanted)
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT player_id FROM users WHERE source_id IN (%s)" % marks, wanted
                ).fetchall()
            ids = [row[0] for row in rows]
        payload = self._varint(len(ids))
        for friend_id in ids:
            payload += struct.pack("<I", int(friend_id))
        self.emulator.send_to(client_id, self._push_frame(109, 12, payload))
        self.emulator.debug_log_tcp("FRIENDS: %s count=%s" % (client_id, len(ids)))

    def liked_ids(self, player_id):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT course_id FROM course_likes WHERE player_id = ?", (player_id,)
            ).fetchall()
        return [row[0] for row in rows]

    def _course_fan_count(self, course_id):
        if not course_id:
            return 0
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM course_likes WHERE course_id = ?", (course_id,)
            ).fetchone()
        return int(row[0]) if row else 0

    def dispatch(self, message, client_id):
        name = message.get("name")
        player_id = self.player_id(client_id)
        if not player_id and name not in self.BEFORE_LOGIN:
            # Without a login there is no account to charge, pay, or post as.
            self.emulator.debug_log_tcp("REFUSED BEFORE LOGIN: %s %s" % (client_id, name))
            if message.get("rpc") is None:
                return None
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"], "Log in first."
            )
        try:
            handler = getattr(self, "on_" + name, None) if name else None
            # Client threads share the matchmaking queue, courses, clubs, and coin balances.
            with self.lock:
                if handler is not None:
                    return handler(message, client_id, player_id)
                return self.generic(message, client_id)
        except Exception as exc:
            self.emulator.debug_log_tcp("HANDLER ERROR: %s %s" % (name, exc))
            if message.get("rpc") is None:
                return None
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"], "That reply could not be built."
            )

    def on_disconnect(self, client_id):
        """Forget a closed connection so nobody is paired with it or sent its room traffic."""
        with self.lock:
            self.waiting[:] = [client for client in self.waiting if client != client_id]
            self.opponents.pop(client_id, None)
            match = self.rm_matches.pop(client_id, None)
            if match is not None and not match["settled"]:
                match["gone"].add(client_id)
                if all(client in match["results"] or client in match["gone"] for client in match["clients"]):
                    self._rm_settle(match)
            self.chat_rooms = {key for key in self.chat_rooms if key[0] != client_id}

    def on_GetLikes(self, message, client_id, player_id):
        ids = self.liked_ids(player_id) if player_id else []
        if player_id:
            day, claimed = self._daily_state(player_id)
            need_accept = not claimed
        else:
            day, need_accept = 1, False
        bonus = self.emulator.igor.push_daily_bonus(
            day=day,
            need_accept=need_accept,
            time_left=self._seconds_until_midnight(datetime.datetime.now()),
        )
        reply = self.emulator.igor.reply_likes(message["rpc"], ids) + bonus
        return reply + self.session_push(player_id) if player_id else reply

    def on_ApplyAvatar(self, message, client_id, player_id):
        if not player_id:
            return None
        # The first save is free. A later change costs 4000 coins, matching the help page.
        with self._connect() as conn:
            row = conn.execute("SELECT user_state FROM users WHERE player_id = ?", (player_id,)).fetchone()
        changing = row is not None and int(row[0] or 0) == 3
        cost = int(self.bal("avatar_change_coins", default=4000)) if changing else 0
        if cost:
            coins = int(self.profile(player_id)["coins"] or 0)
            if coins < cost:
                self.emulator.debug_log_tcp(
                    "APPLY AVATAR REFUSED: %s coins=%s need=%s" % (client_id, coins, cost)
                )
                return None
            self.save_profile(player_id, coins=coins - cost)
        self.save_profile(
            player_id,
            horse_name=message.get("horseName") or "Horse",
            horse_gender=message.get("horseGender") or 0,
            player_gender=message.get("playerGender") or 0,
            player_skin=message.get("playerSkin") or 0,
            horse_skin=message.get("horseSkin") or 0,
            tail_skin=message.get("tailSkin") or 0,
        )
        self.set_user_state(player_id, 3)
        self.emulator.debug_log_tcp("APPLY AVATAR: %s horse=%s cost=%s" % (client_id, message.get("horseName"), cost))
        return self.session_push(player_id)

    def _daily_table(self):
        table = self.bal("daily", "coins", default=None)
        if not isinstance(table, list) or not table:
            return [10, 15, 20, 25, 30, 35, 40]
        return [int(value) for value in table]

    def _daily_coins(self, day):
        table = self._daily_table()
        index = (int(day or 1) - 1) % len(table)
        return table[index]

    def _daily_state(self, player_id):
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT daily_claim, daily_day FROM player_data WHERE player_id = ?", (player_id,)
            ).fetchone()
        claimed = None
        day = 1
        if row:
            if row[0]:
                claimed = datetime.datetime.strptime(row[0], "%Y-%m-%d").date()
            if row[1]:
                day = int(row[1])
        if claimed is None or claimed < yesterday:
            day = 1
        return day, claimed == today

    def on_AcceptDailyBonus(self, message, client_id, player_id):
        # Opening the daily screen sends this. A missed day starts the sheet over.
        paid = 0
        if player_id:
            day, already = self._daily_state(player_id)
            if not already:
                paid = self._daily_coins(day)
                next_day = 1 if day >= len(self._daily_table()) else day + 1
                today = datetime.date.today().isoformat()
                with self._connect() as conn:
                    conn.execute(
                        """UPDATE player_data
                           SET coins = COALESCE(coins, 0) + ?, daily_claim = ?, daily_day = ?
                           WHERE player_id = ?""",
                        (paid, today, next_day, player_id),
                    )
                self.emulator.send_to(client_id, self.session_push(player_id))
        self.emulator.debug_log_tcp("DAILY BONUS ACCEPTED: %s paid=%s" % (client_id, paid))
        return None

    def on_StartPaddock(self, message, client_id, player_id):
        # The scene looks up spawn places after it sends this call. An immediate
        # reply starts presents before those places exist.
        frame = self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])
        def send_later():
            time.sleep(2)
            self.emulator.send_to(client_id, frame)
        threading.Thread(target=send_later, daemon=True).start()
        return None

    def on_PickupItem(self, message, client_id, player_id):
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.LootItem",
            {"Chance": 1},
        )

    def on_StartPractice(self, message, client_id, player_id):
        if player_id:
            self.practice_open.add(player_id)
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_EndPractice(self, message, client_id, player_id):
        result = self._practice_result(message.get("rest") or "")
        course_id = result["course_id"]
        started = player_id in self.practice_open
        kept = started and not result.get("canceled")
        if kept and course_id and course_id in self.courses:
            course = self.courses[course_id]
            course["played"] = int(course.get("played") or 0) + 1
            course["completed"] = int(course.get("completed") or 0) + 1
            with self._connect() as conn:
                conn.execute(
                    "UPDATE courses SET played = ?, completed = ? WHERE course_id = ?",
                    (course["played"], course["completed"], course_id),
                )
        reward_coins = int(self.bal("practice", "coins", default=25))
        reward_xp = int(self.bal("practice", "horse_xp", default=10))
        ap_cost = int(self.bal("action_points", "practice", default=20))
        wear = float(self.bal("grooming", "wear_per_ride", default=0.05))
        if player_id:
            self.practice_open.discard(player_id)
        if player_id and kept:
            data = self.profile(player_id)
            if int(data["action_points"] or 0) < ap_cost:
                reward_coins = 0
                reward_xp = 0
                ap_cost = 0
                wear = 0
            worn = max(0.0, float(data["grooming"] if data["grooming"] is not None else 1) - wear)
            level, xp, points = self._apply_xp(data, reward_xp)
            self.save_profile(
                player_id,
                coins=data["coins"] + reward_coins,
                horse_level=level,
                horse_xp=xp,
                skill_points=points,
                action_points=max(0, data["action_points"] - ap_cost),
                grooming=worn,
            )
        else:
            reward_coins = 0
            reward_xp = 0
        record = self._save_practice_best(player_id, result) if player_id and kept else False
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.ReplyPracticeEnd",
            {
                "PersonalRecord": record,
                "Reward": {
                    "Money": {"Coins": reward_coins, "SkillTickets": 0},
                    "Xp": reward_xp,
                    "Ap": 0,
                    "Items": [],
                },
            },
        ) + self.session_push(player_id or 1)

    def on_StartTutorial(self, message, client_id, player_id):
        # A disconnect during the paddock tutorial logs back into it.
        if player_id:
            self.set_user_state(player_id, self._next_tutorial_state(self._user_state(player_id), False))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_StartGrooming(self, message, client_id, player_id):
        return None

    def on_UpdateGrooming(self, message, client_id, player_id):
        if not player_id:
            return None
        raw = bytes.fromhex(message.get("rest") or "")
        if len(raw) < 4:
            return None
        reported = max(0.0, min(1.0, struct.unpack_from("<f", raw)[0]))
        value = self._groom_value(player_id, reported)
        self.save_profile(player_id, grooming=value)
        self.emulator.debug_log_tcp("GROOM: %s value=%.2f" % (client_id, value))
        return None

    def on_BuyGrooming(self, message, client_id, player_id):
        return self._paid_groom(client_id, player_id, "BUY GROOM")

    def on_PremiumGrooming(self, message, client_id, player_id):
        # The real client sends this only for a Pro Rider. Anyone can send the
        # packet, and this server does not sell Pro, so the groom still costs coins.
        return self._paid_groom(client_id, player_id, "PREMIUM GROOM", free=False)

    def _paid_groom(self, client_id, player_id, label, free=False):
        if not player_id:
            return None
        data = self.profile(player_id)
        missing = max(0.0, 1.0 - float(data["grooming"] if data["grooming"] is not None else 1))
        full = int(self.bal("grooming", "full_groom_coins", default=20))
        cost = 0 if free else int(round(full * missing))
        if cost > data["coins"]:
            self.emulator.debug_log_tcp("%s: %s refused cost=%s" % (label, client_id, cost))
            return None
        self.save_profile(player_id, grooming=1.0, coins=data["coins"] - cost)
        self.emulator.debug_log_tcp("%s: %s cost=%s" % (label, client_id, cost))
        return self.session_push(player_id)

    def on_FinishGrooming(self, message, client_id, player_id):
        if not player_id:
            return None
        raw = bytes.fromhex(message.get("rest") or "")
        reported = 1.0
        if len(raw) >= 4:
            reported = max(0.0, min(1.0, struct.unpack_from("<f", raw)[0]))
        self.save_profile(player_id, grooming=self._groom_value(player_id, reported))
        return self.session_push(player_id)

    def _groom_value(self, player_id, reported):
        """A brush report can raise cleanliness a little, and not twice in a few seconds."""
        data = self.profile(player_id)
        current = float(data["grooming"] if data["grooming"] is not None else 1)
        if reported <= current:
            return reported
        now = time.time()
        last = self.groom_at.get(player_id, 0)
        if now - last < 20:
            return current
        self.groom_at[player_id] = now
        return min(reported, current + 0.35)

    def _apply_xp(self, data, gain):
        """Turn horse XP into levels. Each level grants the skill points on the main card."""
        threshold = int(self.bal("xp_per_level", default=100)) or 100
        cap = int(self.bal("max_level", default=60))
        per_level = int(self.bal("skill_points_per_level", default=1))
        level = int(data.get("horse_level") or 1)
        xp = int(data.get("horse_xp") or 0) + int(gain or 0)
        points = int(data.get("skill_points") or 0)
        while level < cap and xp >= threshold:
            xp -= threshold
            level += 1
            points += per_level
        if level >= cap:
            xp = min(xp, threshold)
        return level, xp, points

    def _min_ride(self):
        config = getattr(self.emulator, "join_config", None) or {}
        try:
            return float(config.get("min_ride_seconds") or 8)
        except (TypeError, ValueError):
            return 8

    def _data_file(self, name):
        # Same order as the card host: this server's copy first, then the game's UserData copy.
        shipped = APP_DIR / "gamedata" / name
        if shipped.exists():
            return shipped
        installed = APP_DIR.parent / "UserData" / name
        return installed if installed.exists() else shipped

    def _gear_by_hash(self):
        cached = getattr(self, "_gear_index", None)
        if cached is not None:
            return cached
        path = self._data_file("wardrobe.json")
        index = {}
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
            for item in document.get("items") or []:
                index[int(item["hash"])] = item
        self._gear_index = index
        return index

    def on_EquipItem(self, message, client_id, player_id):
        if not player_id:
            return None
        item_hash = self._uint_from_rest(message.get("rest") or "")
        if not self._may_equip(player_id, item_hash):
            # Boots and breeches raise the action-point bar, so wearing one must mean owning it.
            self.emulator.debug_log_tcp("EQUIP REFUSED: %s hash=%s not owned" % (client_id, item_hash))
            return self.session_push(player_id)
        gear = self._gear_by_hash().get(item_hash) or {}
        rider_slot = int(gear.get("player") or 0) or {
            273916496: 1,    # helmet
            3227336666: 2,   # jacket
            697550404: 3,    # breeches
            2356041962: 4,   # boots
        }.get(item_hash)
        if rider_slot:
            worn = [item for item in self.rider_equipment(player_id) if int(item.get("slot") or 0) != rider_slot]
            worn.append({"slot": rider_slot, "hash": item_hash})
            self.save_rider_equipment(player_id, worn)
            self.emulator.debug_log_tcp("EQUIP RIDER: %s slot=%s" % (client_id, rider_slot))
            return self.session_push(player_id)
        slot = int(gear.get("gear") or 0) or {
            3590476992: 1,  # bridle
            3766252702: 2,  # saddle
            1906234608: 3,  # blanket
            3648995946: 4,  # ankle boots
        }.get(item_hash)
        if slot:
            worn = [item for item in self.equipment(player_id) if int(item.get("slot") or 0) != slot]
            worn.append({"slot": slot, "hash": item_hash})
            self.save_equipment(player_id, worn)
            self.emulator.debug_log_tcp("EQUIP: %s slot=%s" % (client_id, slot))
        return self.session_push(player_id)

    # The plain pieces every rider starts with. Older saves wear them without an inventory row.
    STARTER_HASHES = frozenset({
        273916496, 3227336666, 697550404, 2356041962,
        3590476992, 3766252702, 1906234608, 3648995946,
    })

    def _may_equip(self, player_id, item_hash):
        if not item_hash:
            return False
        if item_hash in self.STARTER_HASHES:
            return True
        by_id = self._gear_by_id() or {}
        for item_id in list(self.bal("starter", "rider", default=[])) + list(self.bal("starter", "horse", default=[])):
            item = by_id.get(item_id)
            if item and int(item.get("hash") or 0) == item_hash:
                return True
        if any(int(item.get("hash") or 0) == item_hash and int(item.get("count", 1) or 0) > 0
               for item in self.inventory(player_id)):
            return True
        worn = self.rider_equipment(player_id) + self.equipment(player_id)
        return any(int(item.get("hash") or 0) == item_hash for item in worn)

    def on_BuyItem(self, message, client_id, player_id):
        if not player_id:
            return None
        item_hash, count = self._hash_and_count(message.get("rest") or "")
        if not item_hash:
            return None
        treats = {
            3099497678: ("carrot", 10),
            4141365854: ("clover", 15),
            1965560022: ("coinbag", 25),
        }
        gear = self._gear_by_hash().get(item_hash)
        item_id = gear.get("id") if gear else None
        fallback = int(gear.get("price") or 20) if gear else int(self.bal("treats", "unknown_item_coins", default=10))
        if item_hash in treats:
            item_id, treat_fallback = treats[item_hash]
            fallback = int(self.bal("treats", item_id, "coins", default=treat_fallback))
        listed = self.bal("items", item_id, "price", default=None) if item_id else None
        price = int(listed if listed is not None else fallback) * max(1, count)
        data = self.profile(player_id)
        if data["coins"] < price:
            self.emulator.debug_log_tcp("BUY REFUSED: %s not enough coins" % client_id)
            return None
        self.save_profile(player_id, coins=data["coins"] - price)
        self.add_item(player_id, item_hash, count)
        self.emulator.debug_log_tcp("BUY ITEM: %s hash=%s count=%s" % (client_id, item_hash, count))
        return self.session_push(player_id)

    def on_UseItem(self, message, client_id, player_id):
        if not player_id:
            return None
        item_hash, _count = self._hash_and_count(message.get("rest") or "")
        items = self.inventory(player_id)
        for item in items:
            if item.get("hash") == item_hash and int(item.get("count") or 0) > 0:
                item["count"] = int(item["count"]) - 1
                self.save_inventory(player_id, [row for row in items if int(row.get("count") or 0) > 0])
                data = self.profile(player_id)
                xp_gain = {
                    3099497678: int(self.bal("treats", "carrot", "xp", default=5)),
                    4141365854: int(self.bal("treats", "clover", "xp", default=8)),
                    1965560022: int(self.bal("treats", "coinbag", "xp", default=0)),
                }.get(item_hash, 0)
                coins_gain = int(self.bal("treats", "coinbag", "coins_on_use", default=50)) if item_hash == 1965560022 else 0
                level, xp, points = self._apply_xp(data, xp_gain)
                self.save_profile(
                    player_id,
                    horse_level=level,
                    horse_xp=xp,
                    skill_points=points,
                    coins=data["coins"] + coins_gain,
                )
                self.emulator.debug_log_tcp("USE ITEM: %s hash=%s" % (client_id, item_hash))
                return self.session_push(player_id)
        return None

    def on_UpgradeInventoryItem(self, message, client_id, player_id):
        return self.session_push(player_id) if player_id else None

    def on_UpgradeEquipmentItem(self, message, client_id, player_id):
        return self.session_push(player_id) if player_id else None

    def on_BuySlot(self, message, client_id, player_id):
        owner = player_id or 0
        raw = bytes.fromhex(message.get("rest") or "")
        course_type = raw[0] if raw else 1
        if course_type != 2:
            course_type = 1
        slot_cost = int(self.bal("course_editor", "slot_coins", default=0))
        # The original game included one free show-jumping slot. Later slots use slot_coins.
        with self._connect() as conn:
            owned_slots = conn.execute(
                "SELECT COUNT(*) FROM course_slots WHERE player_id = ?", (owner,)
            ).fetchone()[0]
        if not owned_slots:
            slot_cost = 0
        if slot_cost and owner:
            coins = int(self.profile(owner)["coins"] or 0)
            if coins < slot_cost:
                self.emulator.debug_log_tcp("SLOT REFUSED: %s coins=%s need=%s" % (client_id, coins, slot_cost))
                return self.emulator.igor.reply_fail(
                    message["service"], message["function"], message["rpc"],
                    "Not enough coins for another course slot.",
                )
            self.save_profile(owner, coins=coins - slot_cost)
        with self._connect() as conn:
            slot_id = conn.execute(
                "SELECT COALESCE(MAX(slot_id), 0) + 1 FROM course_slots WHERE player_id = ?",
                (owner,),
            ).fetchone()[0]
            name = "Slot %s" % slot_id
            body = self._empty_slot(slot_id, name, course_type)
            conn.execute(
                "INSERT OR REPLACE INTO course_slots (player_id, slot_id, body) VALUES (?, ?, ?)",
                (owner, slot_id, body.hex()),
            )
            rows = conn.execute(
                "SELECT body FROM course_slots WHERE player_id = ? ORDER BY slot_id", (owner,)
            ).fetchall()
        self.slots[owner] = [row[0] or "" for row in rows]
        self.emulator.debug_log_tcp("SLOT BOUGHT: %s id=%s" % (client_id, slot_id))
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.CourseSlot",
            {"Id": slot_id, "Name": name, "Type": course_type},
        )

    def _empty_slot(self, slot_id, name, course_type=1):
        # Show-jump is 1. Agility is 2. Anything else stays show-jump.
        course_type = 2 if int(course_type or 1) == 2 else 1
        body = bytes([0x00]) + struct.pack("<iB?", int(slot_id), course_type, False)
        return body + self._igor_string(name)

    def on_SaveSlot(self, message, client_id, player_id):
        owner = player_id or 0
        body = message.get("rest") or ""
        raw = bytes.fromhex(body) if body else b""
        slot_id = struct.unpack_from("<i", raw, 1)[0] if len(raw) >= 5 else len(self.slots.get(owner, [])) + 1
        limit = int(self.bal("course_editor", "max_slot_bytes", default=524288))
        if limit and len(raw) > limit:
            self.emulator.debug_log_tcp("SLOT REFUSED: %s slot=%s %s bytes" % (client_id, slot_id, len(raw)))
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"], "That course is too large to save."
            )
        with self._connect() as conn:
            owned = {row[0] for row in conn.execute(
                "SELECT slot_id FROM course_slots WHERE player_id = ?", (owner,)
            )}
            # Only slots bought through BuySlot can be saved. The first one is free there.
            # A rider with no slot yet may save slot 1, which is that free slot.
            if slot_id not in owned and not (not owned and slot_id == 1):
                self.emulator.debug_log_tcp("SLOT REFUSED: %s slot=%s not owned" % (client_id, slot_id))
                return self.emulator.igor.reply_fail(
                    message["service"], message["function"], message["rpc"], "Buy that course slot first."
                )
            conn.execute(
                "INSERT OR REPLACE INTO course_slots (player_id, slot_id, body) VALUES (?, ?, ?)",
                (owner, slot_id, body),
            )
            rows = conn.execute(
                "SELECT body FROM course_slots WHERE player_id = ? ORDER BY slot_id", (owner,)
            ).fetchall()
        self.slots[owner] = [row[0] or "" for row in rows]
        self.emulator.debug_log_tcp("COURSE SLOT SAVED: %s slot=%s" % (client_id, slot_id))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def _slot_name(self, body_hex):
        try:
            raw = bytes.fromhex(body_hex) if body_hex else b""
        except ValueError:
            return ""
        if len(raw) < 8:
            return ""
        try:
            name, _offset = GameFeatures._read_igor_string(self, raw, 7)
        except (IndexError, UnicodeDecodeError):
            return ""
        return name

    def on_Submit(self, message, client_id, player_id):
        owner = player_id or 0
        raw = bytes.fromhex(message.get("rest") or "")
        slot_id = struct.unpack_from("<i", raw)[0] if len(raw) >= 4 else 0
        body = ""
        if owner and slot_id:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT body FROM course_slots WHERE player_id = ? AND slot_id = ?",
                    (owner, slot_id),
                ).fetchone()
            if row:
                body = row[0] or ""
        if not body:
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"],
                "That course slot is not saved.",
            )
        name = self._slot_name(body) or ("Slot %s" % slot_id)
        with self._connect() as conn:
            course_id = conn.execute("SELECT COALESCE(MAX(course_id), 0) + 1 FROM courses").fetchone()[0]
            conn.execute(
                "INSERT INTO courses (course_id, owner_id, name, body) VALUES (?, ?, ?, ?)",
                (course_id, owner, name, body),
            )
        self.courses[course_id] = {"owner": owner, "body": body, "name": name}
        self.emulator.debug_log_tcp("COURSE SUBMIT: %s slot=%s name=%s" % (client_id, slot_id, name))
        return self._uint_reply(message, course_id)

    def _course_ids(self, owner=None):
        if owner is None:
            return sorted(self.courses)
        return sorted(
            course_id for course_id, course in self.courses.items() if course.get("owner") == owner
        )

    def _steam_friend_ids(self, player_id):
        """Player ids of Steam friends who already have accounts here. Not the caller."""
        if not player_id:
            return []
        with self._connect() as conn:
            row = conn.execute(
                "SELECT friend_ids FROM player_data WHERE player_id = ?", (player_id,)
            ).fetchone()
            steam_ids = []
            if row and row[0]:
                try:
                    steam_ids = json.loads(row[0])
                except ValueError:
                    steam_ids = []
            wanted = [str(value) for value in steam_ids if value]
            if not wanted:
                return []
            marks = ",".join("?" for _value in wanted)
            friends = conn.execute(
                "SELECT player_id FROM users WHERE source_id IN (%s)" % marks, wanted
            ).fetchall()
        return [int(item[0]) for item in friends]

    def _course_list_ids(self, sort_byte, player_id):
        # 3 is Friends, 4 is Newest, 8 is MyCourses. Other tabs stay the full list.
        sort_byte = int(sort_byte or 0)
        if sort_byte == 4:
            return sorted(self.courses, reverse=True)
        if sort_byte == 8:
            return self._course_ids(player_id) if player_id else []
        if sort_byte == 3:
            owners = set(self._steam_friend_ids(player_id))
            return sorted(
                course_id
                for course_id, course in self.courses.items()
                if course.get("owner") in owners
            )
        return self._course_ids()

    def on_GetCourseList(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        sort_byte = raw[0] if raw else 0
        return self.emulator.igor.reply_uints(
            message["service"], message["function"], message["rpc"],
            self._course_list_ids(sort_byte, player_id),
        )

    def on_GetCourseInfo(self, message, client_id, player_id):
        requested = self._read_uint_list(bytes.fromhex(message.get("rest") or ""))
        if not requested:
            requested = self._course_ids()
        payload = self._varint(len(requested))
        for course_id in requested:
            course = self.courses.get(int(course_id)) or {"name": "course-%s" % course_id, "owner": 0}
            payload += self._course_info(int(course_id), course)
        return self._status_payload(message, payload)

    def _saved_course_facts(self, body_hex):
        """Terrain, ideal time, and jump height from the saved show-jump map."""
        saved = self._slot_show_jump(body_hex or "")
        if not saved:
            return None
        try:
            end = self._skip_show_jump(saved, 0)
        except (IndexError, UnicodeDecodeError, ValueError):
            return None
        if end < 12 or end > len(saved):
            return None
        terrain, = struct.unpack_from("<I", saved, 0)
        min_time, height = struct.unpack_from("<ii", saved, end - 8)
        course_type = 1
        try:
            raw = bytes.fromhex(body_hex or "")
            if len(raw) >= 6 and raw[5]:
                course_type = raw[5]
        except ValueError:
            pass
        return course_type, int(min_time), int(terrain), int(height)

    def _course_info(self, course_id, course):
        # Bit 0 is the ideal time, bit 1 is the jump height.
        name = course.get("name") or ("Meadow %s" % course_id)
        facts = self._saved_course_facts(course.get("body") or "")
        if facts:
            course_type, min_time, terrain, height = facts
        else:
            course_type = 1
            min_time = int(course.get("min_time") or 60)
            terrain = 3684040698
            height = 110
        body = bytes([0xC0])
        body += struct.pack("<IB", int(course_id), int(course_type))
        body += self._igor_string(name)
        body += struct.pack("<IiI", int(course.get("owner") or 0), int(min_time), int(terrain))
        body += struct.pack("<i", int(height))
        return body

    def _read_uint_list(self, raw):
        if not raw:
            return []
        count = 0
        shift = 0
        offset = 0
        while offset < len(raw):
            byte = raw[offset]
            offset += 1
            count |= (byte & 127) << shift
            if byte < 128:
                break
            shift += 7
        ids = []
        for _index in range(count):
            if offset + 4 > len(raw):
                break
            ids.append(struct.unpack_from("<I", raw, offset)[0])
            offset += 4
        return ids

    def on_GetUserCourses(self, message, client_id, player_id):
        asked = self._uint_from_rest(message.get("rest") or "") or (player_id or 0)
        return self.emulator.igor.reply_uints(
            message["service"], message["function"], message["rpc"], self._course_ids(asked)
        )

    def _courses_named(self, pattern):
        needle = (pattern or "").strip().lower()
        if not needle:
            return []
        return [
            course_id
            for course_id in sorted(self.courses)
            if needle in (self.courses[course_id].get("name") or "").lower()
        ]

    def on_FindCourses(self, message, client_id, player_id):
        pattern = ""
        try:
            pattern, _offset = self._read_igor_string(bytes.fromhex(message.get("rest") or ""), 0)
        except (IndexError, UnicodeDecodeError):
            pattern = ""
        ids = self._courses_named(pattern)
        self.emulator.debug_log_tcp("FIND COURSES: %s pattern=%s count=%s" % (client_id, pattern, len(ids)))
        return self.emulator.igor.reply_uints(
            message["service"], message["function"], message["rpc"], ids
        )

    def on_GetCourses(self, message, client_id, player_id):
        payload = self._varint(len(self.courses))
        for course_id in sorted(self.courses):
            course = self.courses[course_id]
            payload += struct.pack("<II", int(course_id), int(course.get("owner") or 0))
            payload += self._igor_string(course.get("name") or "")
        self.emulator.debug_log_tcp("DEBUG COURSES: %s count=%s" % (client_id, len(self.courses)))
        return self._status_payload(message, payload)

    def on_SetCourseName(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        course_id = self._uint_from_rest(message.get("rest") or "")
        name = ""
        if len(raw) > 4:
            try:
                name, _offset = self._read_igor_string(raw, 4)
            except (IndexError, UnicodeDecodeError):
                name = ""
        return self._update_course(message, client_id, player_id, course_id, name=name)

    def on_SetCourseOwner(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        course_id = self._uint_from_rest(message.get("rest") or "")
        owner = struct.unpack_from("<I", raw, 4)[0] if len(raw) >= 8 else 0
        if owner and not self._player_exists(owner):
            return self._missing_player(message, owner)
        return self._update_course(message, client_id, player_id, course_id, owner=owner)

    def on_SetCourseMinTime(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        course_id = self._uint_from_rest(message.get("rest") or "")
        min_time = struct.unpack_from("<i", raw, 4)[0] if len(raw) >= 8 else 60
        return self._update_course(message, client_id, player_id, course_id, min_time=min_time)

    def _update_course(self, message, client_id, player_id, course_id, **values):
        course = self.courses.get(course_id)
        if course is None:
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"], "That course is not on this server."
            )
        owner = int(course.get("owner") or 0)
        if not player_id or (owner and owner != player_id):
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"],
                "Only the rider who owns this course can change it.",
            )
        course.update(values)
        with self._connect() as conn:
            conn.execute(
                "UPDATE courses SET owner_id = ?, name = ?, min_time = ? WHERE course_id = ?",
                (course.get("owner") or 0, course.get("name") or "", course.get("min_time") or 60, course_id),
            )
        self.emulator.debug_log_tcp("COURSE UPDATE: %s id=%s %s" % (client_id, course_id, values))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_QuestConfirmed(self, message, client_id, player_id):
        return self._quest_flag(message, client_id, player_id, "confirmed")

    def on_QuestDeclined(self, message, client_id, player_id):
        return self._quest_flag(message, client_id, player_id, "declined")

    def _quest_flag(self, message, client_id, player_id, status):
        quest_id = self._uint_from_rest(message.get("rest") or "")
        if player_id:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO quest_flags (player_id, quest_id, status) VALUES (?, ?, ?)",
                    (player_id, quest_id, status),
                )
        self.emulator.debug_log_tcp("QUEST: %s id=%s %s" % (client_id, quest_id, status))
        return None

    def on_ComplainSpam(self, message, client_id, player_id):
        return self._report_player(message, client_id, player_id, "spam")

    def on_ComplainAbuse(self, message, client_id, player_id):
        return self._report_player(message, client_id, player_id, "abuse")

    def _report_player(self, message, client_id, player_id, kind):
        target = self._uint_from_rest(message.get("rest") or "")
        if player_id and target:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO player_reports (reporter_id, target_id, kind) VALUES (?, ?, ?)",
                    (player_id, target, kind),
                )
        self.emulator.debug_log_tcp("REPORT: %s kind=%s target=%s" % (client_id, kind, target))
        return None

    def _ignore_payload(self, ids):
        payload = GameFeatures._varint(self, len(ids))
        for ignored_id in ids:
            payload += struct.pack("<I", int(ignored_id))
        return payload

    def _ignored_ids(self, player_id):
        if not player_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT target_id FROM ignored_players WHERE player_id = ? AND ignored = 1",
                (player_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def _ignore_frame(self, player_id):
        return self._push_frame(103, 0, self._ignore_payload(self._ignored_ids(player_id)))

    def on_Ignore(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        target = self._uint_from_rest(message.get("rest") or "")
        ignored = 1 if len(raw) > 4 and raw[4] else 0
        if player_id and target:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO ignored_players (player_id, target_id, ignored) VALUES (?, ?, ?)",
                    (player_id, target, ignored),
                )
        self.emulator.debug_log_tcp("IGNORE: %s target=%s ignored=%s" % (client_id, target, ignored))
        if not player_id:
            return None
        return self._ignore_frame(player_id)

    def on_LogError(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        name = ""
        try:
            name, _offset = self._read_igor_string(raw, 0)
        except (IndexError, UnicodeDecodeError):
            name = ""
        self.emulator.debug_log_tcp("CLIENT ERROR: %s %s" % (client_id, name))
        return None

    def on_TutorialFinished(self, message, client_id, player_id):
        if player_id:
            self.set_user_state(player_id, self._next_tutorial_state(self._user_state(player_id), True))
        self.emulator.debug_log_tcp("TUTORIAL DONE: %s" % client_id)
        return None

    def on_GiveReward(self, message, client_id, player_id):
        # ServiceDebug.GiveReward. The amount in the packet is whatever the sender typed.
        self.emulator.debug_log_tcp("REWARD IGNORED: %s" % client_id)
        return None

    def on_GetCourseChanges(self, message, client_id, player_id):
        course_id = self._uint_from_rest(message.get("rest") or "")
        course = self.courses.get(course_id) or {}
        likes = 0
        with self._connect() as conn:
            likes = conn.execute(
                "SELECT COUNT(*) FROM course_likes WHERE course_id = ?", (course_id,)
            ).fetchone()[0]
        return self._status_payload(
            message,
            struct.pack("<iii", int(course.get("played") or 0), int(course.get("completed") or 0), int(likes)),
        )

    def _practice_course_id(self, rest):
        return self._practice_result(rest)["course_id"]

    def _practice_result(self, rest):
        """RequestPracticeEnd: CourseKey, CourseId, Time, Penalty, IsCanceled."""
        raw = bytes.fromhex(rest) if rest else b""
        parsed = {"course_key": 0, "course_id": 0, "time": 0.0, "penalty": 0, "canceled": False}
        if not raw:
            return parsed
        flags = raw[0]
        offset = 1
        if flags & 0x80 and offset + 4 <= len(raw):
            parsed["course_key"], = struct.unpack_from("<I", raw, offset)
            offset += 4
        if flags & 0x40 and offset + 4 <= len(raw):
            parsed["course_id"], = struct.unpack_from("<I", raw, offset)
            offset += 4
        if flags & 0x20 and offset + 4 <= len(raw):
            parsed["time"], = struct.unpack_from("<f", raw, offset)
            offset += 4
        if flags & 0x10 and offset + 4 <= len(raw):
            parsed["penalty"], = struct.unpack_from("<I", raw, offset)
            offset += 4
        if flags & 0x08 and offset < len(raw):
            parsed["canceled"] = raw[offset] != 0
        return parsed

    def _ride_beats(self, penalty, time_s, best_penalty, best_time):
        if best_penalty is None:
            return True
        if penalty < best_penalty:
            return True
        return penalty == best_penalty and time_s < best_time

    def _save_practice_best(self, player_id, result):
        """Keep the fewest faults, then the fastest time. A canceled or absurd ride is not a record."""
        if result.get("canceled"):
            return False
        if not ride_is_plausible(result.get("time"), result.get("penalty"), self._min_ride()):
            return False
        keys = []
        if result.get("course_id"):
            keys.append(int(result["course_id"]))
        if result.get("course_key"):
            keys.append(int(result["course_key"]))
        if not keys:
            return False
        penalty = int(result["penalty"])
        time_s = float(result["time"])
        primary = keys[0]
        with self._connect() as conn:
            row = conn.execute(
                "SELECT penalty, time FROM course_scores WHERE player_id = ? AND course_id = ?",
                (player_id, primary),
            ).fetchone()
            if not self._ride_beats(penalty, time_s, None if row is None else row[0], None if row is None else row[1]):
                return False
            for course_id in keys:
                conn.execute(
                    """INSERT INTO course_scores (player_id, course_id, time, penalty)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(player_id, course_id) DO UPDATE SET time = excluded.time, penalty = excluded.penalty""",
                    (player_id, course_id, time_s, penalty),
                )
        return True

    def on_GetSlots(self, message, client_id, player_id):
        items = [item for item in self.slots.get(player_id or 0, []) if item]
        return self.emulator.igor.reply_blobs(message["service"], message["function"], message["rpc"], items)

    def on_GetCourse(self, message, client_id, player_id):
        course_id = self._uint_from_rest(message.get("rest") or "")
        course = self.courses.get(course_id)
        if course is None and self.courses and not course_id:
            course = self.courses[sorted(self.courses)[-1]]
        if course is None:
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"], "That course is not on this server."
            )
        likes = 0
        if player_id:
            with self._connect() as conn:
                likes = conn.execute(
                    "SELECT COUNT(*) FROM course_likes WHERE course_id = ?", (course_id,)
                ).fetchone()[0]
        agility = self._slot_agility(course.get("body") or "")
        if agility:
            return self._course_with_map(message, course_id, course, likes, 2, 0x40, agility)
        saved = self._slot_show_jump(course.get("body") or "")
        if saved and self._map_has_jumps(saved):
            return self._course_with_map(message, course_id, course, likes, 1, 0x80, saved)
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.Course",
            {
                "Id": course_id,
                "Type": 1,
                "OwnerId": course.get("owner") or 0,
                "Name": course.get("name") or "",
                "Likes": likes,
                "SubmitTime": 1790492400,
                "ShowJumpMap": self._meadow_map(course.get("min_time") or 60),
            },
        )

    def _map_has_jumps(self, saved):
        if not saved or len(saved) < 6:
            return False
        _items, offset = self._read_varint(saved, 4)
        if _items:
            return True
        if offset + 28 >= len(saved):
            return False
        jumps, _offset = self._read_varint(saved, offset + 28)
        return jumps > 0

    def _meadow_map(self, min_time):
        jumps = (
            (2670118389, 15),
            (2932793192, 35),
            (1218636073, 55),
        )
        obstacles = []
        for key, distance in jumps:
            obstacles.append({
                "Key": key,
                "Position": {
                    "Position": {"x": 0, "y": 0, "z": distance},
                    "Rotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                },
            })
        return {
            "Terrain": 3684040698,
            "MinTime": int(min_time),
            "ObstacleHeight": 110,
            "Obstacles": obstacles,
            "ObstacleSequence": [0, 1, 2],
        }

    def _slot_show_jump(self, body_hex):
        # A saved slot is a CourseSlot. Copy its show-jump map into GetCourse.
        if not body_hex:
            return None
        try:
            raw = bytes.fromhex(body_hex)
        except ValueError:
            return None
        if len(raw) < 8 or not (raw[0] & 0x80):
            return None
        offset = 7
        try:
            _name, offset = self._read_igor_string(raw, offset)
            end = self._skip_show_jump(raw, offset)
        except (IndexError, UnicodeDecodeError, ValueError):
            return None
        if end <= offset or end > len(raw):
            return None
        return raw[offset:end]

    def _slot_agility(self, body_hex):
        # CourseSlot bit 1 is the agility map. It follows the show-jump map when both are set.
        if not body_hex:
            return None
        try:
            raw = bytes.fromhex(body_hex)
        except ValueError:
            return None
        if len(raw) < 8 or not (raw[0] & 0x40):
            return None
        offset = 7
        try:
            _name, offset = self._read_igor_string(raw, offset)
            if raw[0] & 0x80:
                offset = self._skip_show_jump(raw, offset)
            end = self._skip_agility(raw, offset)
        except (IndexError, UnicodeDecodeError, ValueError):
            return None
        if end <= offset or end > len(raw):
            return None
        return raw[offset:end]

    def _skip_agility(self, raw, offset):
        offset += 4
        offset = self._skip_map_items(raw, offset)
        offset += 28
        count, offset = self._read_varint(raw, offset)
        offset += count * 12
        return offset

    def _course_with_map(self, message, course_id, course, likes, course_type, header, saved):
        payload = bytes([header])
        payload += struct.pack("<IBI", int(course_id), int(course_type), int(course.get("owner") or 0))
        payload += self._igor_string(course.get("name") or "")
        payload += saved + bytes([0])
        payload += struct.pack(
            "<Iiii", 1790492400, int(course.get("played") or 0), int(course.get("completed") or 0), int(likes)
        )
        self.emulator.debug_log_tcp("COURSE MAP: %s id=%s bytes=%s" % (message.get("rpc"), course_id, len(saved)))
        return self._status_payload(message, payload)

    def _skip_show_jump(self, raw, offset):
        offset += 4
        offset = self._skip_map_items(raw, offset)
        offset += 28
        offset = self._skip_map_items(raw, offset)
        count, offset = self._read_varint(raw, offset)
        offset += count * 2 + 8
        return offset

    def _skip_map_items(self, raw, offset):
        count, offset = self._read_varint(raw, offset)
        return offset + count * 32

    def on_ReportCourse(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        course_id = self._uint_from_rest(message.get("rest") or "")
        comment = ""
        if len(raw) > 4:
            try:
                comment, _offset = self._read_igor_string(raw, 4)
            except (IndexError, UnicodeDecodeError):
                comment = ""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO course_reports (player_id, course_id, comment) VALUES (?, ?, ?)",
                (player_id or 0, course_id, comment),
            )
        self.emulator.debug_log_tcp("REPORT: %s course=%s" % (client_id, course_id))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_Like(self, message, client_id, player_id):
        course_id = self._uint_from_rest(message.get("rest") or "")
        if player_id and course_id:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO course_likes (player_id, course_id) VALUES (?, ?)",
                    (player_id, course_id),
                )
        count = self._course_fan_count(course_id)
        self.emulator.debug_log_tcp("LIKE: %s course=%s count=%s" % (client_id, course_id, count))
        return self._uint_reply(message, count)

    def on_UnLike(self, message, client_id, player_id):
        course_id = self._uint_from_rest(message.get("rest") or "")
        if player_id and course_id:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM course_likes WHERE player_id = ? AND course_id = ?",
                    (player_id, course_id),
                )
        count = self._course_fan_count(course_id)
        self.emulator.debug_log_tcp("UNLIKE: %s course=%s count=%s" % (client_id, course_id, count))
        return self._uint_reply(message, count)

    def on_RmEnd(self, message, client_id, player_id):
        result = self._parse_rm_result(message.get("rest") or "")
        mine = {
            "client": client_id,
            "player": player_id,
            "time": float(result.get("time") or 0),
            "penalty": int(result.get("penalty") or 0),
            "canceled": bool(result.get("canceled")),
        }
        mine["valid"] = bool(player_id) and not mine["canceled"] and ride_is_plausible(
            mine["time"], mine["penalty"], self._min_ride()
        )
        in_match = client_id in self.opponents
        opponent_id = self.opponents.pop(client_id, None) if in_match else None
        match = self.rm_matches.pop(client_id, None)
        if match is not None:
            # Two riders on this server. Settle once both results are in, or the other one has gone.
            match["results"][client_id] = mine
            if all(client in match["results"] or client in match["gone"] for client in match["clients"]):
                self._rm_settle(match)
            else:
                self._rm_settle_later(match)
            return None
        # The saved local rival always finishes 1.5 s behind you with no faults.
        rival = {
            "client": None, "player": opponent_id or self._rival_id(),
            "time": mine["time"] + 1.5, "penalty": 0, "canceled": False, "valid": True,
        }
        won = in_match and mine["valid"] and self._rm_beats(mine, rival, ties_win=True)
        self._rm_finish(mine, rival, won, paid=in_match)
        return None

    @staticmethod
    def _rm_beats(mine, theirs, ties_win=False):
        """Fewer faults wins. Equal faults, then the faster time."""
        if mine["penalty"] != theirs["penalty"]:
            return mine["penalty"] < theirs["penalty"]
        if ties_win:
            return mine["time"] <= theirs["time"]
        return mine["time"] < theirs["time"]

    def _rm_settle_later(self, match):
        wait = float(self.bal("ranked", "settle_wait_seconds", default=120))

        def settle():
            time.sleep(wait)
            self._rm_settle(match)

        threading.Thread(target=settle, daemon=True).start()

    def _rm_settle(self, match):
        """Pay both riders of a human match once. A rider with no result forfeits."""
        with self.lock:
            if match["settled"]:
                return
            match["settled"] = True
            for client in match["clients"]:
                self.rm_matches.pop(client, None)
                self.opponents.pop(client, None)
            sides = []
            for client in match["clients"]:
                sides.append(match["results"].get(client) or {
                    "client": None, "player": match["players"].get(client), "time": 0.0,
                    "penalty": 0, "canceled": True, "valid": False,
                })
            first, second = sides
            for mine, theirs in ((first, second), (second, first)):
                if mine["client"] is None:
                    continue
                won = mine["valid"] and (not theirs["valid"] or self._rm_beats(mine, theirs))
                self._rm_finish(mine, theirs, won, paid=True)

    def _rm_finish(self, mine, theirs, won, paid):
        player_id = mine["player"]
        if paid and player_id and not mine["canceled"]:
            wear = float(self.bal("grooming", "wear_per_ride", default=0.05))
            with self._connect() as conn:
                conn.execute(
                    """UPDATE player_data
                       SET action_points = MAX(0, COALESCE(action_points, 0) - ?),
                           rating = COALESCE(rating, 1000) + ?,
                           coins = COALESCE(coins, 0) + ?,
                           grooming = MAX(0, COALESCE(grooming, 1) - ?)
                       WHERE player_id = ?""",
                    (
                        int(self.bal("action_points", "ranked", default=20)),
                        int(self.bal("ranked", "win_rating" if won else "loss_rating", default=15 if won else 0)),
                        int(self.bal("ranked", "win_coins" if won else "loss_coins", default=25 if won else 0)),
                        wear,
                        player_id,
                    ),
                )
        self.emulator.debug_log_tcp(
            "RANKED END: %s time=%s penalty=%s won=%s" % (mine["client"], mine["time"], mine["penalty"], won)
        )
        try:
            finished = {
                "MyResult": 1 if won else 2,
                "PersonalRecord": won,
                "Me": {
                    "UserId": player_id or 1,
                    "Place": 1 if won else 2,
                    "Time": mine["time"],
                    "Penalty": mine["penalty"],
                },
                "Opponent": {
                    "UserId": theirs["player"] or self._rival_id(),
                    "Place": 2 if won else 1,
                    "Time": theirs["time"],
                    "Penalty": theirs["penalty"],
                },
            }
            if won:
                finished["Reward"] = {
                    "Money": {"Coins": int(self.bal("ranked", "win_coins", default=25)), "SkillTickets": 0},
                    "Xp": 0,
                    "Ap": 0,
                    "Items": [],
                }
            frame = self.emulator.igor.push_object(107, 6, "Protocol2.ReplyRmFinished", finished)
        except Exception as exc:
            self.emulator.debug_log_tcp("RANKED FINISH SKIPPED: %s" % exc)
            return
        if player_id:
            frame += self.session_push(player_id)
        self.emulator.send_to(mine["client"], frame)

    def _parse_rm_result(self, rest):
        raw = bytes.fromhex(rest) if rest else b""
        parsed = {"time": 0.0, "penalty": 0, "canceled": False}
        if not raw:
            return parsed
        flags = raw[0]
        offset = 1
        if offset + 4 <= len(raw):
            parsed["user"], = struct.unpack_from("<I", raw, offset)
            offset += 4
        if flags & 0x80 and offset + 4 <= len(raw):
            offset += 4
        if flags & 0x40 and offset + 4 <= len(raw):
            offset += 4
        if flags & 0x20 and offset + 4 <= len(raw):
            parsed["time"], = struct.unpack_from("<f", raw, offset)
            offset += 4
        if flags & 0x10 and offset + 4 <= len(raw):
            parsed["penalty"], = struct.unpack_from("<I", raw, offset)
            offset += 4
        if flags & 0x08 and offset < len(raw):
            parsed["canceled"] = raw[offset] != 0
        return parsed

    def on_SubscribeRmSearch(self, message, client_id, player_id):
        if client_id in self.waiting:
            return None
        if self.waiting:
            other = self.waiting.pop(0)
            mine = self._rm_start(player_id or 1, self.player_id(other) or 2)
            theirs = self._rm_start(self.player_id(other) or 2, player_id or 1)
            self.emulator.send_to(other, theirs)
            self.emulator.send_to(client_id, mine)
            self.opponents[client_id] = self.player_id(other) or self._rival_id()
            self.opponents[other] = player_id or 1
            match = {
                "clients": (other, client_id),
                "players": {other: self.player_id(other), client_id: player_id},
                "results": {},
                "gone": set(),
                "settled": False,
            }
            self.rm_matches[other] = match
            self.rm_matches[client_id] = match
            self.emulator.debug_log_tcp("RANKED PAIR: %s %s" % (other, client_id))
            return None
        self.waiting.append(client_id)
        owner = player_id or 1

        def start_rival():
            time.sleep(2)
            with self.lock:
                if client_id not in self.waiting:
                    return
                self.waiting.remove(client_id)
                self.opponents[client_id] = self._rival_id()
            self.emulator.send_to(client_id, self._rm_start(owner, 0))
            self.emulator.debug_log_tcp("RANKED RIVAL: %s" % client_id)

        threading.Thread(target=start_rival, daemon=True).start()
        self.emulator.debug_log_tcp("RANKED QUEUE: %s" % client_id)
        return None

    def on_UnSubscribeRmSearch(self, message, client_id, player_id):
        self.waiting[:] = [client for client in self.waiting if client != client_id]
        self.emulator.debug_log_tcp("RANKED LEAVE: %s" % client_id)
        return None

    def on_StartChallenge(self, message, client_id, player_id):
        friend = self._uint_from_rest(message.get("rest") or "")
        with self._connect() as conn:
            row = conn.execute("SELECT player_id FROM users WHERE player_id = ?", (friend,)).fetchone()
        if friend and row is None:
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"], "That player is not on this server."
            )
        target = friend or (player_id or 1)
        if player_id and target != player_id:
            key = self._challenge_key(player_id, target)
            self.challenges[key] = {
                "key": key,
                "starter": int(player_id),
                "course": 3023709559,
                "results": {},
            }
        self._push_challenge(client_id, target, 1)
        for other, other_player in list(self.emulator.client_players.items()):
            if other_player == target and other != client_id:
                self._push_challenge(other, player_id or 1, 2)
        self.emulator.debug_log_tcp("CHALLENGE: %s target=%s" % (client_id, target))
        return self._status_payload(message, struct.pack("<I", 3023709559))

    def _challenge_key(self, left, right):
        pair = (int(left), int(right))
        return tuple(sorted(pair))

    def _parse_challenge_result(self, rest):
        raw = bytes.fromhex(rest) if rest else b""
        parsed = {"friend": 0, "time": 0.0, "penalty": 0, "canceled": False}
        if len(raw) < 13:
            return parsed
        parsed["friend"], = struct.unpack_from("<I", raw)
        flags = raw[4]
        parsed["time"], parsed["penalty"] = struct.unpack_from("<fI", raw, 5)
        if flags & 0x80 and len(raw) > 13:
            parsed["canceled"] = raw[13] != 0
        return parsed

    def _challenge_place(self, mine, opp):
        """1 win, 2 lose, 3 draw. Fewer faults, then the faster time. A quit loses."""
        if mine.get("canceled") and opp.get("canceled"):
            return 3
        if mine.get("canceled"):
            return 2
        if opp.get("canceled"):
            return 1
        if int(mine["penalty"]) != int(opp["penalty"]):
            return 1 if int(mine["penalty"]) < int(opp["penalty"]) else 2
        if float(mine["time"]) != float(opp["time"]):
            return 1 if float(mine["time"]) < float(opp["time"]) else 2
        return 3

    def _challenge_result_bytes(self, result):
        if not result:
            return b""
        return bytes([0x80]) + struct.pack("<fI", float(result["time"]), int(result["penalty"])) + bytes([1 if result.get("canceled") else 0])

    def _challenge_update_payload(self, other, status, course, mine, opp):
        bits = (0x80 if mine else 0) | (0x40 if opp else 0)
        payload = bytes([bits]) + struct.pack("<IBQI", int(other), int(status), 0, int(course))
        return payload + GameFeatures._challenge_result_bytes(self, mine) + GameFeatures._challenge_result_bytes(self, opp)

    def _challenge_notify_payload(self, friend, status, course, mine, opp, place):
        bits = (0x40 if mine else 0) | (0x20 if opp else 0)
        payload = bytes([2, bits]) + struct.pack("<IBI", int(friend), int(status), int(course))
        payload += GameFeatures._challenge_result_bytes(self, mine) + GameFeatures._challenge_result_bytes(self, opp)
        return payload + bytes([int(place)])

    def _push_challenge_state(self, challenge):
        left, right = challenge_ids = self._challenge_key_ids(challenge)
        results = challenge["results"]
        course = int(challenge["course"])
        both = left in results and right in results
        for viewer, other in ((left, right), (right, left)):
            mine = results.get(viewer)
            opp = results.get(other)
            status = 1 if viewer == challenge["starter"] else 2
            update = self._challenge_update_payload(other, status, course, mine, opp)
            frames = self._push_frame(109, 13, update)
            if both and mine and opp:
                place = self._challenge_place(mine, opp)
                frames += self._push_frame(109, 9, self._challenge_notify_payload(other, status, course, mine, opp, place))
                frames += self._push_frame(109, 14, struct.pack("<I", int(other)))
            for client_id, player in list(self.emulator.client_players.items()):
                if player == viewer:
                    self.emulator.send_to(client_id, frames)
        if both:
            self.challenges.pop(challenge["key"], None)
        return challenge_ids

    def _challenge_key_ids(self, challenge):
        return challenge["key"]

    def _push_challenge(self, client_id, other_player, status):
        payload = bytes([0x00]) + struct.pack("<IBQ", int(other_player), int(status), 0)
        payload += struct.pack("<I", 3023709559)
        self.emulator.send_to(client_id, self._push_frame(109, 13, payload))

    def on_ChallengeResult(self, message, client_id, player_id):
        parsed = self._parse_challenge_result(message.get("rest") or "")
        friend = int(parsed["friend"])
        if not player_id or not friend:
            return None
        challenge = self.challenges.get(self._challenge_key(player_id, friend))
        if not challenge or friend == player_id:
            self.emulator.debug_log_tcp("CHALLENGE RESULT IGNORED: %s no open pair" % client_id)
            return None
        if not parsed["canceled"] and not ride_is_plausible(parsed["time"], parsed["penalty"], self._min_ride()):
            self.emulator.debug_log_tcp(
                "CHALLENGE RESULT IGNORED: %s time=%.2f penalty=%s" % (client_id, parsed["time"], parsed["penalty"])
            )
            return None
        challenge["results"][int(player_id)] = {
            "time": float(parsed["time"]),
            "penalty": int(parsed["penalty"]),
            "canceled": bool(parsed["canceled"]),
        }
        if not parsed["canceled"]:
            wear = float(self.bal("grooming", "wear_per_ride", default=0.05))
            with self._connect() as conn:
                conn.execute(
                    "UPDATE player_data SET grooming = MAX(0, COALESCE(grooming, 1) - ?) WHERE player_id = ?",
                    (wear, player_id),
                )
            self.emulator.send_to(client_id, self.session_push(player_id))
        self._push_challenge_state(challenge)
        self.emulator.debug_log_tcp(
            "CHALLENGE RESULT: %s friend=%s time=%.2f penalty=%s canceled=%s"
            % (client_id, friend, parsed["time"], parsed["penalty"], parsed["canceled"])
        )
        return None

    def on_SubmitDsResult(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        if len(raw) < 12 or not player_id:
            return None
        course_hash, time_s, penalty = struct.unpack_from("<IfI", raw)
        if not ride_is_plausible(time_s, penalty, self._min_ride()):
            self.emulator.debug_log_tcp(
                "DS RESULT IGNORED: %s time=%.2f penalty=%s" % (client_id, time_s, penalty)
            )
            return None
        data = self.profile(player_id)
        needed_level = int(self.bal("showdown", "min_level", default=10))
        ticket_cost = self._showdown_ticket_cost()
        with self._connect() as conn:
            already = conn.execute(
                """SELECT COUNT(*) FROM ds_results
                   WHERE player_id = ? AND date(created_at, 'localtime') = date('now', 'localtime')""",
                (player_id,),
            ).fetchone()[0]
            if already:
                self.emulator.debug_log_tcp("DS RESULT REFUSED: %s already rode today" % client_id)
                return None
            if int(data.get("horse_level") or 1) < needed_level:
                self.emulator.debug_log_tcp("DS RESULT REFUSED: %s level" % client_id)
                return None
            if ticket_cost and int(data.get("skill_tickets") or 0) < ticket_cost:
                self.emulator.debug_log_tcp("DS RESULT REFUSED: %s tickets" % client_id)
                return None
            wear = float(self.bal("grooming", "wear_per_ride", default=0.05))
            assignments = ["grooming = MAX(0, COALESCE(grooming, 1) - ?)"]
            params = [wear]
            if ticket_cost:
                assignments.append("skill_tickets = MAX(0, COALESCE(skill_tickets, 0) - ?)")
                params.append(ticket_cost)
            params.append(player_id)
            conn.execute(
                "UPDATE player_data SET %s WHERE player_id = ?" % ", ".join(assignments),
                params,
            )
            conn.execute(
                "INSERT INTO ds_results (player_id, course_hash, time, penalty) VALUES (?, ?, ?, ?)",
                (player_id, course_hash, time_s, penalty),
            )
        self.emulator.send_to(client_id, self.session_push(player_id))
        self.emulator.debug_log_tcp(
            "DS RESULT: %s time=%.2f penalty=%s" % (client_id, time_s, penalty)
        )
        return None

    def _ds_board(self, today):
        clause = "date(created_at, 'localtime') = date('now', 'localtime')" if today else "date(created_at, 'localtime') < date('now', 'localtime')"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT player_id, time, penalty FROM ds_results WHERE %s" % clause
            ).fetchall()
        return self._ds_table(rows)

    def _ds_table(self, rows):
        best = {}
        for user_id, time_s, penalty in rows:
            current = best.get(user_id)
            candidate = (int(penalty or 0), float(time_s or 0))
            if current is None or candidate < current:
                best[user_id] = candidate
        ordered = sorted(best.items(), key=lambda item: item[1])
        table = []
        for place, (user_id, (penalty, time_s)) in enumerate(ordered, start=1):
            table.append({
                "PlayerId": user_id,
                "PlayerLevel": int(self.profile(user_id).get("horse_level") or 1),
                "Time": time_s,
                "Penalty": penalty,
                "Place": place,
            })
        return table

    def _csharp_weekday(self, moment):
        # DayOfWeek is Sunday 0 through Saturday 6. The menu's CurrentDay still uses 7 for Sunday.
        return (moment.weekday() + 1) % 7

    def _showdown_week_start(self, moment):
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        return start - datetime.timedelta(days=start.weekday())

    def _signed_ints(self, rest, count):
        raw = bytes.fromhex(rest or "")
        values = []
        for index in range(count):
            offset = index * 4
            if len(raw) < offset + 4:
                values.append(0)
            else:
                values.append(struct.unpack_from("<i", raw, offset)[0])
        return values

    def _ds_board_span(self, start, end, csharp_day):
        params = [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]
        weekday = ""
        if csharp_day is not None:
            weekday = " AND CAST(strftime('%w', created_at, 'localtime') AS INTEGER) = ?"
            params.append(int(csharp_day) % 7)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT player_id, time, penalty FROM ds_results
                   WHERE date(created_at, 'localtime') >= ?
                     AND date(created_at, 'localtime') < ?""" + weekday,
                params,
            ).fetchall()
        return self._ds_table(rows)

    def _page_board(self, table, player_id, page):
        # GuiShowdownMenu draws ten lines. The rider's own row stays under the page.
        mine = next((row for row in table if row["PlayerId"] == (player_id or 0)), None)
        start = max(0, int(page or 0)) * 10
        return table[start:start + 10], mine

    def _ds_table_reply(self, message, table, player_id, page):
        visible, mine = self._page_board(table, player_id, page)
        fields = {"Table": visible}
        if mine:
            fields["MyResult"] = mine
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.DsResultsTable", fields
        )

    def on_DsDayTable(self, message, client_id, player_id):
        day, page = self._signed_ints(message.get("rest") or "", 2)
        if len(bytes.fromhex(message.get("rest") or "")) < 4:
            day = self._csharp_weekday(datetime.datetime.now())
        start = self._showdown_week_start(datetime.datetime.now())
        table = self._ds_board_span(start, start + datetime.timedelta(days=7), day)
        return self._ds_table_reply(message, table, player_id, page)

    def on_DsLastWeekTable(self, message, client_id, player_id):
        # The last-week tab always asks for week 0. No other week id is published.
        week_id, page = self._signed_ints(message.get("rest") or "", 2)
        table = []
        if week_id == 0:
            this_week = self._showdown_week_start(datetime.datetime.now())
            table = self._ds_board_span(this_week - datetime.timedelta(days=7), this_week, None)
        return self._ds_table_reply(message, table, player_id, page)

    def on_DsQualificationTable(self, message, client_id, player_id):
        page = self._signed_ints(message.get("rest") or "", 1)[0]
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT player_id, MIN(time) FROM ds_results
                   GROUP BY player_id ORDER BY MIN(time) ASC"""
            ).fetchall()
        table = [
            {"PlayerId": user_id, "Scores": int(best * 100), "Place": place}
            for place, (user_id, best) in enumerate(rows, start=1)
        ]
        visible, mine = self._page_board(table, player_id, page)
        fields = {"Table": visible}
        if mine:
            fields["MyResult"] = mine
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.DsQualification", fields
        )

    def _showdown_prize(self, place):
        prizes = self.bal("showdown", "prizes", default=None)
        if not isinstance(prizes, list):
            prizes = [
                {"from": 1, "to": 1, "coins": 2000, "xp": 0, "item": ""},
                {"from": 2, "to": 2, "coins": 500, "xp": 200, "item": "item_apple_big"},
                {"from": 3, "to": 3, "coins": 500, "xp": 200, "item": "item_apple"},
                {"from": 4, "to": 10, "coins": 250, "xp": 0, "item": ""},
                {"from": 11, "to": 100, "coins": 100, "xp": 0, "item": ""},
            ]
        for band in prizes:
            if int(band.get("from") or 0) <= place <= int(band.get("to") or 0):
                return band
        return {"coins": 0, "xp": 0, "item": ""}

    def on_DsGetFinalReward(self, message, client_id, player_id):
        place = 0
        for row in self._ds_board(True):
            if row["PlayerId"] == (player_id or 0):
                place = int(row["Place"])
        if not place:
            self.emulator.debug_log_tcp("DS REWARD REFUSED: %s no ride today" % client_id)
            return None
        prize = self._showdown_prize(place)
        coins = int(prize.get("coins") or 0)
        xp = int(prize.get("xp") or 0)
        item_id = prize.get("item") or ""
        # ds_claimed holds the day (YYYYMMDD) of the board that was paid, so each day's board pays once.
        today_key = int(datetime.date.today().strftime("%Y%m%d"))
        if player_id:
            with self._connect() as conn:
                claimed = conn.execute(
                    "SELECT ds_claimed FROM player_data WHERE player_id = ?", (player_id,)
                ).fetchone()
            if not claimed or int(claimed[0] or 0) != today_key:
                data = self.profile(player_id)
                level, xp_left, points = self._apply_xp(data, xp)
                self.save_profile(
                    player_id,
                    coins=data["coins"] + coins,
                    horse_level=level,
                    horse_xp=xp_left,
                    skill_points=points,
                    ds_claimed=today_key,
                )
                if item_id:
                    item_hash = zlib.crc32(str(item_id).encode("utf-8")) & 0xffffffff
                    self.add_item(player_id, item_hash, 1)
                self.emulator.send_to(client_id, self.session_push(player_id))
        self.emulator.debug_log_tcp(
            "DS REWARD: %s place=%s coins=%s xp=%s item=%s" % (client_id, place, coins, xp, item_id)
        )
        return None

    def _showdown_day(self, moment):
        """Client days are Monday 1 through Saturday 6. Sunday is 7, not DayOfWeek.Sunday."""
        return moment.weekday() + 1

    def _seconds_until_midnight(self, moment):
        tomorrow = (moment + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1, int((tomorrow - moment).total_seconds()))

    def _showdown_ticket_cost(self, moment=None):
        moment = moment or datetime.datetime.now()
        if self._showdown_day(moment) == 7:
            return 0
        return int(self.bal("showdown", "skill_tickets", default=150))

    def _ds_rode_today(self, player_id):
        if not player_id:
            return False
        with self._connect() as conn:
            count = conn.execute(
                """SELECT COUNT(*) FROM ds_results
                   WHERE player_id = ? AND date(created_at, 'localtime') = date('now', 'localtime')""",
                (player_id,),
            ).fetchone()[0]
        return bool(count)

    def on_DsMenu(self, message, client_id, player_id):
        # TimeLeft of 0 makes GuiShowdownMenu request this again every frame.
        now = datetime.datetime.now()
        ticket_cost = self._showdown_ticket_cost(now)
        profile = self.profile(player_id) if player_id else {}
        can_pay = True if ticket_cost == 0 else int(profile.get("skill_tickets") or 0) >= ticket_cost
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.ReplyDsMenu",
            {
                "CurrentDay": self._showdown_day(now),
                "TimeLeft": self._seconds_until_midnight(now),
                "DsMinLevel": int(self.bal("showdown", "min_level", default=10)),
                "ApprovedCost": can_pay if player_id else False,
                "ApprovedLevel": int(profile.get("horse_level") or 1) >= int(self.bal("showdown", "min_level", default=10)) if player_id else False,
                "ApprovedQual": True,
                "ApprovedTry": not self._ds_rode_today(player_id),
                "Course": 3023709559,
                "DsCost": {"Coins": 0, "SkillTickets": ticket_cost},
            },
        )

    def on_GetCourseLeaderboard(self, message, client_id, player_id):
        course_id = self._uint_from_rest(message.get("rest") or "")
        rows = []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT player_id, time, penalty FROM course_scores
                   WHERE course_id = ? ORDER BY penalty ASC, time ASC""",
                (course_id,),
            ).fetchall()
        scores = [
            {"UserId": row[0], "Time": row[1], "Penalty": row[2]}
            for row in rows
        ]
        mine = next((score for score in scores if score["UserId"] == (player_id or 0)), None)
        if mine is None and player_id:
            mine = {"UserId": player_id}
        friends = self._friend_board(scores, player_id, self._steam_friend_ids(player_id))
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.CourseScores",
            {"PlayerScore": mine or {}, "GlobalHighscores": scores, "FriendsHighscores": friends},
        )

    def _friend_board(self, scores, player_id, friend_ids):
        allowed = {int(item) for item in friend_ids}
        if player_id:
            allowed.add(int(player_id))
        return [score for score in scores if int(score["UserId"]) in allowed]

    def on_GetLadder(self, message, client_id, player_id):
        count = self._signed_ints(message.get("rest") or "", 1)[0]
        return self._status_payload(message, self._ladder_payload(count))

    def _friends_ladder_payload(self, rows, allowed):
        chosen = [
            (int(user_id), place)
            for place, (user_id,) in enumerate(rows, start=1)
            if int(user_id) in allowed
        ]
        payload = GameFeatures._varint(self, len(chosen))
        for user_id, place in chosen:
            payload += struct.pack("<II", user_id, place)
        return payload

    def on_GetFriendsLadder(self, message, client_id, player_id):
        allowed = set()
        rows = []
        if player_id:
            allowed.add(int(player_id))
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT friend_ids FROM player_data WHERE player_id = ?", (player_id,)
                ).fetchone()
                steam_ids = []
                if row and row[0]:
                    try:
                        steam_ids = json.loads(row[0])
                    except ValueError:
                        steam_ids = []
                wanted = [str(value) for value in steam_ids if value]
                if wanted:
                    marks = ",".join("?" for _value in wanted)
                    friends = conn.execute(
                        "SELECT player_id FROM users WHERE source_id IN (%s)" % marks, wanted
                    ).fetchall()
                    allowed.update(int(item[0]) for item in friends)
                rows = conn.execute(
                    """SELECT player_id FROM player_data
                       ORDER BY COALESCE(rating, 1000) DESC, player_id ASC"""
                ).fetchall()
        return self._status_payload(message, self._friends_ladder_payload(rows, allowed))

    def on_GetGuildLadder(self, message, client_id, player_id):
        allowed = set()
        rows = []
        guild_id = self._player_guild(player_id) if player_id else None
        if guild_id:
            with self._connect() as conn:
                members = conn.execute(
                    "SELECT player_id FROM guild_members WHERE guild_id = ?", (guild_id,)
                ).fetchall()
                allowed.update(int(item[0]) for item in members)
                rows = conn.execute(
                    """SELECT player_id FROM player_data
                       ORDER BY COALESCE(rating, 1000) DESC, player_id ASC"""
                ).fetchall()
        return self._status_payload(message, self._friends_ladder_payload(rows, allowed))

    def on_GetPlayersLadder(self, message, client_id, player_id):
        wanted = set(self._uint_list(message.get("rest") or ""))
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT player_id FROM player_data
                   ORDER BY COALESCE(rating, 1000) DESC, player_id ASC"""
            ).fetchall()
        return self._status_payload(message, self._friends_ladder_payload(rows, wanted))

    def on_GetClassicLeaderboard(self, message, client_id, player_id):
        return self.on_GetCourseLeaderboard(message, client_id, player_id)

    def on_SteamMicroTxnInit(self, message, client_id, player_id):
        text = message.get("placeholder") or "Steam checkout is not emulated. A success reply would ask Steam to charge a real wallet."
        self.emulator.debug_log_tcp("PLACEHOLDER: %s %s" % (message.get("name"), text))
        return self.emulator.igor.reply_fail(message["service"], message["function"], message["rpc"], text)

    def on_Exec(self, message, client_id, player_id):
        return self._placeholder_fail(message)

    def on_Eval(self, message, client_id, player_id):
        return self._placeholder_fail(message)

    def on_KickstarterInfo(self, message, client_id, player_id):
        return self._placeholder_fail(message)

    def on_SendPrivateMessage(self, message, client_id, player_id):
        return self._relay_private(message, client_id, player_id)

    def on_PrivateMessage(self, message, client_id, player_id):
        return self._relay_private(message, client_id, player_id)

    def on_SendRoomMessage(self, message, client_id, player_id):
        return self._relay_room(message, client_id, player_id)

    def on_RoomMessage(self, message, client_id, player_id):
        return self._relay_room(message, client_id, player_id)

    def on_GetPlayerInfo(self, message, client_id, player_id):
        asked = self._uint_from_rest(message.get("rest") or "") or (player_id or 0)
        data = self.profile(asked) if asked else self._blank(0)
        payload = self._igor_string(data["name"])
        payload += b"\x00"
        gender = data["player_gender"] or 0
        payload += (b"\x01" + bytes([gender & 255])) if gender else b"\x00"
        return self._status_payload(message, payload)

    def on_GetPlayerProfile(self, message, client_id, player_id):
        asked = self._uint_from_rest(message.get("rest") or "") or (player_id or 0)
        if asked and not self._player_exists(asked):
            return self._missing_player(message, asked)
        data = self.profile(asked) if asked else self._blank(0)
        state = 3
        if asked:
            with self._connect() as conn:
                row = conn.execute("SELECT user_state FROM users WHERE player_id = ?", (asked,)).fetchone()
            if row and row[0] is not None:
                state = row[0]
        online = asked in set(getattr(self.emulator, "client_players", {}).values())
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.PlayerProfile",
            {
                "IsOnline": online,
                "State": state,
                "FriendsCount": len(self._steam_friend_ids(asked)) if asked else 0,
                "Rank": self._rank_place(asked),
                "PlayerEquipment": self._rider_fields(asked) if asked else [],
                "PlayerGender": data["player_gender"],
                "PlayerSkinId": data["player_skin"],
                "HorseName": data["horse_name"],
                "HorseGender": data["horse_gender"],
                "HorseLevel": data["horse_level"],
                "HorseSkinId": data["horse_skin"],
                "HorseTailSkinId": data["tail_skin"],
                "HorseEquipment": self._equipment_fields(asked) if asked else [],
                "CourseIds": self._course_ids(asked),
            },
        )

    def on_IsOnline(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        count, offset = self._read_varint(raw, 0)
        asked = []
        for _index in range(count):
            if offset + 4 > len(raw):
                break
            asked.append(struct.unpack_from("<I", raw, offset)[0])
            offset += 4
        online = set(getattr(self.emulator, "client_players", {}).values())
        payload = self._varint(len(asked))
        for other in asked:
            payload += struct.pack("<I", int(other)) + bytes([1 if other in online else 0])
        self.emulator.debug_log_tcp("ONLINE: %s asked=%s" % (client_id, len(asked)))
        return self._status_payload(message, payload)

    def on_FindPlayer(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        pattern = ""
        try:
            pattern, _offset = self._read_igor_string(raw, 0)
        except (IndexError, UnicodeDecodeError):
            pattern = ""
        ids = []
        if pattern:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT player_id FROM player_data WHERE name LIKE ? ORDER BY player_id",
                    ("%%%s%%" % pattern,),
                ).fetchall()
            ids = [row[0] for row in rows]
        self.emulator.debug_log_tcp("FIND PLAYER: %s pattern=%s count=%s" % (client_id, pattern, len(ids)))
        return self.emulator.igor.reply_uints(message["service"], message["function"], message["rpc"], ids)

    def on_GetAccount(self, message, client_id, player_id):
        asked = self._uint_from_rest(message.get("rest") or "")
        missing = self._missing_player(message, asked)
        if missing:
            return missing
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_type, source_id FROM users WHERE player_id = ?", (asked,)
            ).fetchone()
        source = 5 if row and row[0] == "Steam" else 0
        try:
            source_id = int(row[1]) if row else 0
        except (TypeError, ValueError):
            source_id = 0
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.Account",
            {"Source": source, "SourceId": source_id},
        )

    def on_GetPlayerId(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        source = raw[0] if raw else 0
        source_id = struct.unpack_from("<Q", raw, 1)[0] if len(raw) >= 9 else 0
        source_name = "Steam" if source == 5 else "source_%s" % source
        with self._connect() as conn:
            row = conn.execute(
                "SELECT player_id FROM users WHERE source_type = ? AND source_id = ?",
                (source_name, str(source_id)),
            ).fetchone()
        if row is None:
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"], "That player is not on this server."
            )
        return self._uint_reply(message, row[0])

    def on_ApplySkillPoints(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        count, offset = self._read_varint(raw, 0)
        spent = 0
        deltas = []
        for _index in range(count):
            if offset >= len(raw):
                break
            stat = raw[offset]
            offset += 1
            value = struct.unpack_from("<I", raw, offset)[0] if offset + 4 <= len(raw) else 0
            offset += 4
            deltas.append({"type": stat, "value": value})
            spent += value
        if player_id:
            data = self.profile(player_id)
            cap = int(self.bal("stat_cap", default=10))
            if spent > int(data["skill_points"] or 0):
                self.emulator.debug_log_tcp("SKILL POINTS REFUSED: %s spent=%s have=%s" % (client_id, spent, data["skill_points"]))
                return None
            # Only spent points are saved. Starting stats come from balance.json and count toward the cap.
            spent_by_stat = self._spent_skills(player_id)
            start = self._start_stats()
            used = 0
            for delta in deltas:
                stat = int(delta["type"])
                if stat < 1 or stat > 6:
                    continue
                room = max(0, cap - start.get(stat, 0) - spent_by_stat.get(stat, 0))
                applied = min(int(delta["value"]), room)
                spent_by_stat[stat] = spent_by_stat.get(stat, 0) + applied
                used += applied
            saved = [{"type": stat, "value": value} for stat, value in spent_by_stat.items() if value]
            self.save_profile(
                player_id,
                skill_points=int(data["skill_points"]) - used,
                skills=json.dumps(saved),
            )
            self.emulator.debug_log_tcp("SKILL POINTS: %s spent=%s" % (client_id, used))
            self.emulator.send_to(client_id, self.session_push(player_id))
        return None

    def on_GetPlayerRank(self, message, client_id, player_id):
        asked = self._uint_from_rest(message.get("rest") or "") or (player_id or 0)
        return self._status_payload(message, struct.pack("<i", self._rank_place(asked)))

    def on_GetPlayerGuild(self, message, client_id, player_id):
        asked = self._uint_from_rest(message.get("rest") or "") or (player_id or 0)
        guild_id = None
        if asked:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT guild_id FROM guild_members WHERE player_id = ?", (asked,)
                ).fetchone()
            if row:
                guild_id = row[0]
        if guild_id is None:
            return self._status_payload(message, b"\x00")
        return self._status_payload(message, b"\x01" + struct.pack("<I", int(guild_id)))

    def on_FindGuildsTop(self, message, client_id, player_id):
        page, count = self._signed_ints(message.get("rest") or "", 2)
        return self._guild_list_reply(message, None, False, page, count)

    def on_FindGuildsByName(self, message, client_id, player_id):
        text = ""
        try:
            text, _offset = self._read_igor_string(bytes.fromhex(message.get("rest") or ""), 0)
        except (IndexError, UnicodeDecodeError):
            text = ""
        return self._guild_list_reply(message, text, False)

    def on_FindGuildsByShortName(self, message, client_id, player_id):
        text = ""
        try:
            text, _offset = self._read_igor_string(bytes.fromhex(message.get("rest") or ""), 0)
        except (IndexError, UnicodeDecodeError):
            text = ""
        return self._guild_list_reply(message, text, True)

    def _page_rows(self, rows, page, count):
        if int(count or 0) <= 0:
            return rows
        start = max(0, int(page or 0)) * int(count)
        return rows[start:start + int(count)]

    def _guild_list_reply(self, message, needle, short, page=0, count=0):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT guild_id, name, short_name, icon FROM guilds ORDER BY guild_id"
            ).fetchall()
        matched = []
        for row in rows:
            label = (row[2] if short else row[1]) or ""
            if needle and needle.lower() not in label.lower():
                continue
            matched.append(row)
        matched = self._page_rows(matched, page, count)
        payload = self._varint(len(matched))
        for row in matched:
            with self._connect() as conn:
                members = conn.execute(
                    "SELECT COUNT(*) FROM guild_members WHERE guild_id = ?", (row[0],)
                ).fetchone()[0]
            payload += self._guild_bytes(row, members)
        return self._status_payload(message, payload)

    def _guild_bytes(self, row, members):
        icon = row[3] or "GUI/unknown"
        body = bytes([0x80]) + struct.pack("<I", int(row[0]))
        body += self._igor_string(row[1] or "")
        body += self._igor_string(row[2] or "")
        body += self._igor_string(icon)
        body += struct.pack("<iiQ", int(members), 1, 0)
        return body

    def on_FindGuild(self, message, client_id, player_id):
        return self._guild_reply(message, "Protocol2.Guild")

    def on_FindGuildFull(self, message, client_id, player_id):
        return self._guild_reply(message, "Protocol2.GuildFull")

    def _guild_reply(self, message, type_name):
        guild_id = self._uint_from_rest(message.get("rest") or "")
        row = None
        members = 0
        member_rows = []
        if guild_id:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT name, short_name, icon, description, invite FROM guilds WHERE guild_id = ?",
                    (guild_id,),
                ).fetchone()
                member_rows = conn.execute(
                    "SELECT player_id, role FROM guild_members WHERE guild_id = ? ORDER BY role, player_id",
                    (guild_id,),
                ).fetchall()
                members = len(member_rows)
        if row is None:
            return self._status_payload(message, b"\x00")
        fields = {
            "GuildId": guild_id,
            "Name": row[0] or "",
            "NameShort": row[1] or "",
            "Icon": row[2] or "GUI/unknown",
            "MembersCount": members,
            "Rank": 1,
        }
        if type_name.endswith("GuildFull"):
            fields["Description"] = row[3] or ""
            fields["AllowInvite"] = bool(row[4])
            fields["Members"] = [
                {"PlayerId": player, "Status": role or 3, "CreatedTime": 0}
                for player, role in member_rows
            ]
        frame = self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], type_name, fields,
        )
        return self._mark_present(frame)

    def on_GuildCreate(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        name, short_name = "Guild", "G"
        try:
            name, offset = self._read_igor_string(raw, 0)
            short_name, _offset = self._read_igor_string(raw, offset)
        except (IndexError, UnicodeDecodeError):
            pass
        if self._player_guild(player_id):
            return self._guild_error(message, self.GUILD_ERR_IN_GUILD)
        refused = self._guild_name_error(message, name, short_name, None)
        if refused:
            return refused
        with self._connect() as conn:
            guild_id = conn.execute("SELECT COALESCE(MAX(guild_id), 0) + 1 FROM guilds").fetchone()[0]
            if not name:
                name = "Guild%s" % guild_id
            conn.execute(
                "INSERT INTO guilds (guild_id, name, owner_id, description, welcome, short_name, icon) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guild_id, name, player_id or 0, "", "", short_name or name[:4], "GUI/unknown"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO guild_members (guild_id, player_id, role) VALUES (?, ?, 1)",
                (guild_id, player_id or 0),
            )
        self.emulator.debug_log_tcp("GUILD CREATE: %s id=%s name=%s" % (client_id, guild_id, name))
        reply = self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])
        push = self._push_present(105, 18, "Protocol2.GuildUpdate", {
            "GuildId": guild_id,
            "Name": name,
            "NameShort": short_name or name[:4],
            "Icon": "GUI/unknown",
            "Description": "",
            "Welcome": "",
        })
        return reply + push

    def _player_guild(self, player_id):
        if not player_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT guild_id FROM guild_members WHERE player_id = ?", (player_id,)
            ).fetchone()
        return row[0] if row else None

    def _push_guild_to(self, client_id, guild_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, short_name, icon, description, welcome, invite FROM guilds WHERE guild_id = ?",
                (guild_id,),
            ).fetchone()
            member_rows = conn.execute(
                "SELECT player_id, role FROM guild_members WHERE guild_id = ? ORDER BY role, player_id",
                (guild_id,),
            ).fetchall()
        if row is None:
            self.emulator.send_to(client_id, self._push_frame(105, 18, b"\x00"))
            return
        frame = self._push_present(105, 18, "Protocol2.GuildUpdate", {
            "GuildId": guild_id,
            "Name": row[0] or "",
            "NameShort": row[1] or "",
            "Icon": row[2] or "GUI/unknown",
            "Description": row[3] or "",
            "Welcome": row[4] or "",
            "Rank": 1,
            "AllowInvite": bool(row[5]),
            "Members": [
                {"PlayerId": player, "Status": role or 3, "CreatedTime": 0}
                for player, role in member_rows
            ],
        })
        self.emulator.send_to(client_id, frame)

    def on_GuildChangeName(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        name, short_name = "", ""
        try:
            name, offset = self._read_igor_string(raw, 0)
            short_name, _offset = self._read_igor_string(raw, offset)
        except (IndexError, UnicodeDecodeError):
            pass
        guild_id, _role, refused = self._guild_allowed(message, player_id, self.GUILD_OWNER)
        if refused:
            return refused
        refused = self._guild_name_error(message, name, short_name, guild_id)
        if refused:
            return refused
        price = int(self.bal("club", "rename_coins", default=0))
        if name and price:
            coins = int(self.profile(player_id)["coins"] or 0)
            if coins < price:
                return self._guild_error(message, self.GUILD_ERR_UNKNOWN)
            self.save_profile(player_id, coins=coins - price)
        if guild_id and name:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE guilds SET name = ?, short_name = ? WHERE guild_id = ?",
                    (name, short_name, guild_id),
                )
            self._push_guild_members(guild_id)
            self._push_guild_news(guild_id, 5, player_id, name)
        self.emulator.debug_log_tcp("GUILD RENAME: %s %s" % (client_id, name))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildChangeIcon(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        icon = ""
        if raw and raw[0]:
            try:
                icon, _offset = self._read_igor_string(raw, 1)
            except (IndexError, UnicodeDecodeError):
                icon = ""
        guild_id, _role, refused = self._guild_allowed(message, player_id, self.GUILD_OWNER)
        if refused:
            return refused
        if guild_id:
            with self._connect() as conn:
                conn.execute("UPDATE guilds SET icon = ? WHERE guild_id = ?", (icon, guild_id))
            self._push_guild_members(guild_id)
        self.emulator.debug_log_tcp("GUILD ICON: %s" % client_id)
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildChangeDescription(self, message, client_id, player_id):
        return self._guild_text(message, client_id, player_id, "description")

    def on_GuildChangeWelcome(self, message, client_id, player_id):
        return self._guild_text(message, client_id, player_id, "welcome")

    def _guild_text(self, message, client_id, player_id, column):
        raw = bytes.fromhex(message.get("rest") or "")
        text = ""
        try:
            text, _offset = self._read_igor_string(raw, 0)
        except (IndexError, UnicodeDecodeError):
            text = ""
        guild_id, _role, refused = self._guild_allowed(message, player_id, self.GUILD_OWNER, self.GUILD_MANAGER)
        if refused:
            return refused
        if self._too_long(text, column + "_length"):
            return self._guild_error(message, self.GUILD_ERR_UNKNOWN)
        if guild_id:
            # column is "description" or "welcome", never client text.
            with self._connect() as conn:
                conn.execute(
                    "UPDATE guilds SET %s = ? WHERE guild_id = ?" % column, (text, guild_id)
                )
            self._push_guild_members(guild_id)
            news_type = 6 if column == "description" else 7
            self._push_guild_news(guild_id, news_type, player_id, text)
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildLeave(self, message, client_id, player_id):
        guild_id = self._player_guild(player_id) if player_id else None
        if player_id:
            with self._connect() as conn:
                conn.execute("DELETE FROM guild_members WHERE player_id = ?", (player_id,))
                conn.execute("DELETE FROM guild_requests WHERE player_id = ?", (player_id,))
                if guild_id:
                    self._guild_after_leave(conn, guild_id)
            self._push_no_guild_update(player_id)
        self.emulator.send_to(client_id, self._push_frame(105, 18, b"\x00"))
        if guild_id:
            self._push_guild_members(guild_id)
            self._push_guild_news(guild_id, 4, player_id, "")
        self.emulator.debug_log_tcp("GUILD LEAVE: %s" % client_id)
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildChangeInvite(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        allow = 1 if raw and raw[0] else 0
        guild_id, _role, refused = self._guild_allowed(message, player_id, self.GUILD_OWNER, self.GUILD_MANAGER)
        if refused:
            return refused
        if guild_id:
            with self._connect() as conn:
                conn.execute("UPDATE guilds SET invite = ? WHERE guild_id = ?", (allow, guild_id))
        self.emulator.debug_log_tcp("GUILD INVITE: %s open=%s" % (client_id, allow))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildCreateRequest(self, message, client_id, player_id):
        return self._guild_invite(message, client_id, player_id)

    def on_GuildCancelRequest(self, message, client_id, player_id):
        target = self._uint_from_rest(message.get("rest") or "")
        missing = self._missing_player(message, target)
        if missing:
            return missing
        guild_id, _role, refused = self._guild_allowed(message, player_id, self.GUILD_OWNER, self.GUILD_MANAGER)
        if refused:
            return refused
        if guild_id and target:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM guild_requests WHERE guild_id = ? AND player_id = ?",
                    (guild_id, target),
                )
            self._push_no_guild_update(target)
        self.emulator.debug_log_tcp("GUILD CANCEL: %s target=%s" % (client_id, target))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildAcceptRequest(self, message, client_id, player_id):
        target = self._uint_from_rest(message.get("rest") or "")
        missing = self._missing_player(message, target)
        if missing:
            return missing
        guild_id, _role, refused = self._guild_allowed(message, player_id, self.GUILD_OWNER, self.GUILD_MANAGER)
        if refused:
            return refused
        if target and self._player_guild(target):
            return self._guild_error(message, self.GUILD_ERR_ALREADY_MEMBER)
        if self._guild_full(guild_id):
            return self._guild_error(message, self.GUILD_ERR_FULL)
        if guild_id and target:
            with self._connect() as conn:
                pending = conn.execute(
                    "SELECT 1 FROM guild_requests WHERE guild_id = ? AND player_id = ? AND kind = 'join'",
                    (guild_id, target),
                ).fetchone()
                if pending:
                    conn.execute(
                        "INSERT OR REPLACE INTO guild_members (guild_id, player_id, role) VALUES (?, ?, 3)",
                        (guild_id, target),
                    )
                    conn.execute(
                        "DELETE FROM guild_requests WHERE guild_id = ? AND player_id = ?",
                        (guild_id, target),
                    )
            self._push_guild_members(guild_id)
        self.emulator.debug_log_tcp("GUILD ACCEPT: %s target=%s" % (client_id, target))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildKickUser(self, message, client_id, player_id):
        target = self._uint_from_rest(message.get("rest") or "")
        missing = self._missing_player(message, target)
        if missing:
            return missing
        guild_id, role, refused = self._guild_allowed(message, player_id, self.GUILD_OWNER, self.GUILD_MANAGER)
        if refused:
            return refused
        if target == player_id:
            return self._guild_error(message, self.GUILD_ERR_SELF)
        target_guild, target_role = self._guild_role(target)
        if target_guild != guild_id:
            return self._guild_error(message, self.GUILD_ERR_UNKNOWN)
        if role != self.GUILD_OWNER and target_role != self.GUILD_MEMBER:
            return self._guild_error(message, self.GUILD_ERR_DENIED)
        if guild_id and target:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM guild_members WHERE guild_id = ? AND player_id = ?",
                    (guild_id, target),
                )
                conn.execute(
                    "DELETE FROM guild_requests WHERE guild_id = ? AND player_id = ?",
                    (guild_id, target),
                )
            for other in self._clients_for(target):
                self.emulator.send_to(other, self._push_frame(105, 18, b"\x00"))
            self._push_guild_members(guild_id)
            self._push_guild_news(guild_id, 3, target, "")
        self.emulator.debug_log_tcp("GUILD KICK: %s target=%s" % (client_id, target))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildRoleUser(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        target = struct.unpack_from("<I", raw)[0] if len(raw) >= 4 else 0
        role = raw[4] if len(raw) > 4 else 3
        missing = self._missing_player(message, target)
        if missing:
            return missing
        guild_id, _role, refused = self._guild_allowed(message, player_id, self.GUILD_OWNER)
        if refused:
            return refused
        if target == player_id:
            return self._guild_error(message, self.GUILD_ERR_SELF)
        if role not in (self.GUILD_OWNER, self.GUILD_MANAGER, self.GUILD_MEMBER):
            return self._guild_error(message, self.GUILD_ERR_UNKNOWN)
        if self._guild_role(target)[0] != guild_id:
            return self._guild_error(message, self.GUILD_ERR_UNKNOWN)
        if guild_id and target:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE guild_members SET role = ? WHERE guild_id = ? AND player_id = ?",
                    (role, guild_id, target),
                )
                if role == self.GUILD_OWNER:
                    # One owner per club. The old owner stays on as a manager.
                    conn.execute(
                        "UPDATE guild_members SET role = ? WHERE guild_id = ? AND player_id = ?",
                        (self.GUILD_MANAGER, guild_id, player_id),
                    )
                    conn.execute("UPDATE guilds SET owner_id = ? WHERE guild_id = ?", (target, guild_id))
            self._push_guild_members(guild_id)
            self._push_guild_news(guild_id, 1, target, "")
        self.emulator.debug_log_tcp("GUILD ROLE: %s target=%s role=%s" % (client_id, target, role))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_NoGuildCreateRequest(self, message, client_id, player_id):
        guild_id = self._uint_from_rest(message.get("rest") or "")
        if self._player_guild(player_id):
            return self._guild_error(message, self.GUILD_ERR_IN_GUILD)
        if guild_id and player_id and self._guild_exists(guild_id):
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO guild_requests (guild_id, player_id, kind) VALUES (?, ?, 'join')",
                    (guild_id, player_id),
                )
        self.emulator.debug_log_tcp("GUILD JOIN: %s guild=%s" % (client_id, guild_id))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_NoGuildAcceptRequest(self, message, client_id, player_id):
        guild_id = self._uint_from_rest(message.get("rest") or "")
        invited = None
        if self._player_guild(player_id):
            return self._guild_error(message, self.GUILD_ERR_IN_GUILD)
        if guild_id and self._guild_full(guild_id):
            return self._guild_error(message, self.GUILD_ERR_FULL)
        if guild_id and player_id:
            with self._connect() as conn:
                invited = conn.execute(
                    "SELECT 1 FROM guild_requests WHERE guild_id = ? AND player_id = ? AND kind = 'invite'",
                    (guild_id, player_id),
                ).fetchone()
                if invited:
                    conn.execute(
                        "INSERT OR REPLACE INTO guild_members (guild_id, player_id, role) VALUES (?, ?, 3)",
                        (guild_id, player_id),
                    )
                    conn.execute(
                        "DELETE FROM guild_requests WHERE guild_id = ? AND player_id = ?",
                        (guild_id, player_id),
                    )
            if invited:
                self._push_guild_members(guild_id)
                self._push_no_guild_update(player_id)
        self.emulator.debug_log_tcp("GUILD JOIN ACCEPT: %s guild=%s" % (client_id, guild_id))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_NoGuildCancelRequest(self, message, client_id, player_id):
        guild_id = self._uint_from_rest(message.get("rest") or "")
        if guild_id and player_id:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM guild_requests WHERE guild_id = ? AND player_id = ?",
                    (guild_id, player_id),
                )
            self._push_no_guild_update(player_id)
        self.emulator.debug_log_tcp("GUILD JOIN CANCEL: %s guild=%s" % (client_id, guild_id))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_NoGuildChangeRequest(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        allow = 1 if raw and raw[0] else 0
        if player_id:
            with self._connect() as conn:
                conn.execute("UPDATE player_data SET guild_open = ? WHERE player_id = ?", (allow, player_id))
        self.emulator.debug_log_tcp("GUILD OPEN: %s %s" % (client_id, allow))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def _guild_invite(self, message, client_id, player_id):
        target = self._uint_from_rest(message.get("rest") or "")
        missing = self._missing_player(message, target)
        if missing:
            return missing
        guild_id, _role, refused = self._guild_allowed(message, player_id, self.GUILD_OWNER, self.GUILD_MANAGER)
        if refused:
            return refused
        if target == player_id:
            return self._guild_error(message, self.GUILD_ERR_SELF)
        if target and self._player_guild(target):
            return self._guild_error(message, self.GUILD_ERR_ALREADY_MEMBER)
        if self._guild_full(guild_id):
            return self._guild_error(message, self.GUILD_ERR_FULL)
        if guild_id and target:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO guild_requests
                       (guild_id, player_id, kind, actor_id, created_at)
                       VALUES (?, ?, 'invite', ?, ?)""",
                    (guild_id, target, player_id, int(time.time())),
                )
            self._push_no_guild_update(target)
        self.emulator.debug_log_tcp("GUILD INVITE PLAYER: %s target=%s" % (client_id, target))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def _guild_error(self, message, error):
        """Status 1 with a one-byte EGuildError. Every guild call reads a refusal this way."""
        body = bytes([int(message["service"]) & 255, int(message["function"]) & 255])
        body += struct.pack("<HB", int(message["rpc"]), 1) + bytes([error & 255])
        return self._varint(len(body)) + body

    def _guild_role(self, player_id):
        """(guild_id, role) for this rider, or (None, None)."""
        if not player_id:
            return None, None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT guild_id, role FROM guild_members WHERE player_id = ?", (player_id,)
            ).fetchone()
        if not row:
            return None, None
        return row[0], int(row[1] or self.GUILD_MEMBER)

    def _guild_allowed(self, message, player_id, *roles):
        """(guild_id, role, refusal). refusal is an error frame when the caller may not do this."""
        guild_id, role = self._guild_role(player_id)
        if not guild_id:
            return None, None, self._guild_error(message, self.GUILD_ERR_NOT_IN_GUILD)
        if role not in roles:
            return guild_id, role, self._guild_error(message, self.GUILD_ERR_DENIED)
        return guild_id, role, None

    def _guild_full(self, guild_id):
        cap = int(self.bal("club", "max_members", default=20))
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM guild_members WHERE guild_id = ?", (guild_id,)
            ).fetchone()[0]
        return cap > 0 and count >= cap

    def _too_long(self, text, key):
        limit = int(self.bal("club", key, default=0))
        return limit > 0 and len(text or "") > limit

    def _guild_name_error(self, message, name, short_name, own_guild):
        """A refusal frame when a name is too long or another club already has it."""
        if self._too_long(name, "name_length") or self._too_long(short_name, "short_name_length"):
            return self._guild_error(message, self.GUILD_ERR_UNKNOWN)
        with self._connect() as conn:
            if name and conn.execute(
                "SELECT 1 FROM guilds WHERE LOWER(name) = LOWER(?) AND guild_id IS NOT ?", (name, own_guild)
            ).fetchone():
                return self._guild_error(message, self.GUILD_ERR_NAME_EXIST)
            if short_name and conn.execute(
                "SELECT 1 FROM guilds WHERE LOWER(short_name) = LOWER(?) AND guild_id IS NOT ?",
                (short_name, own_guild),
            ).fetchone():
                return self._guild_error(message, self.GUILD_ERR_SHORT_NAME_EXIST)
        return None

    def _guild_after_leave(self, conn, guild_id):
        """Keep one owner: the first manager, then the first member. Close the club when it is empty."""
        left = conn.execute(
            "SELECT player_id, role FROM guild_members WHERE guild_id = ? ORDER BY role, rowid", (guild_id,)
        ).fetchall()
        if not left:
            conn.execute("DELETE FROM guilds WHERE guild_id = ?", (guild_id,))
            conn.execute("DELETE FROM guild_requests WHERE guild_id = ?", (guild_id,))
            return
        if any(int(role or self.GUILD_MEMBER) == self.GUILD_OWNER for _player, role in left):
            return
        heir = left[0][0]
        conn.execute(
            "UPDATE guild_members SET role = ? WHERE guild_id = ? AND player_id = ?",
            (self.GUILD_OWNER, guild_id, heir),
        )
        conn.execute("UPDATE guilds SET owner_id = ? WHERE guild_id = ?", (heir, guild_id))

    def _missing_player(self, message, player_id):
        if not player_id or self._player_exists(player_id):
            return None
        return self.emulator.igor.reply_fail(
            message["service"], message["function"], message["rpc"], "That player is not on this server."
        )

    def _ensure_rival(self, conn):
        row = conn.execute(
            "SELECT player_id FROM users WHERE source_type = 'Local' AND source_id = 'rival'"
        ).fetchone()
        if row:
            return
        conn.execute(
            "INSERT INTO users (source_type, source_id, user_state, access_level) VALUES ('Local', 'rival', 3, 0)"
        )
        rival_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT OR IGNORE INTO player_data
               (player_id, name, horse_name, horse_gender, player_gender, coins, action_points, horse_level, rating, grooming)
               VALUES (?, 'Rival', 'Rival', 1, 2, 500, 100, 1, 1000, 1)""",
            (rival_id,),
        )

    def _rival_id(self):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT player_id FROM users WHERE source_type = 'Local' AND source_id = 'rival'"
            ).fetchone()
        return row[0] if row else 2

    def _player_exists(self, player_id):
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM users WHERE player_id = ?", (player_id,)).fetchone() is not None

    def _guild_exists(self, guild_id):
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM guilds WHERE guild_id = ?", (guild_id,)).fetchone() is not None

    def _guild_request_bytes(self, guild_id, actor_id, created):
        # Bit 0 set means ActorId is present. That is what the club badge counts.
        created = int(created or 0)
        guild_id = int(guild_id or 0)
        if actor_id:
            return bytes([0x80]) + struct.pack("<IIQ", guild_id, int(actor_id), created)
        return bytes([0x00]) + struct.pack("<IQ", guild_id, created)

    def _no_guild_update(self, player_id):
        rows = []
        if player_id:
            with self._connect() as conn:
                rows = conn.execute(
                    """SELECT guild_id, kind, actor_id, created_at
                       FROM guild_requests WHERE player_id = ?
                       ORDER BY guild_id""",
                    (player_id,),
                ).fetchall()
        payload = self._varint(len(rows))
        for guild_id, kind, actor_id, created in rows:
            actor = actor_id if kind == "invite" else None
            payload += self._guild_request_bytes(guild_id, actor, created)
        return self._push_frame(105, 13, payload)

    def _push_no_guild_update(self, player_id):
        if not player_id:
            return b""
        frame = self._no_guild_update(player_id)
        for other in self._clients_for(player_id):
            self.emulator.send_to(other, frame)
        return frame

    def _clients_for(self, player_id):
        owners = getattr(self.emulator, "client_players", {})
        return [client_id for client_id, owner in list(owners.items()) if owner == player_id]

    def _push_guild_news(self, guild_id, news_type, actor_id, text):
        # Actor and text bits are set. Welcome is 7, description is 6, name is 5.
        payload = self._varint(1) + bytes([0x88, news_type & 255])
        payload += struct.pack("<QI", int(time.time()), int(actor_id or 0))
        payload += self._igor_string(text or "")
        frame = self._push_frame(105, 19, payload)
        with self._connect() as conn:
            members = [row[0] for row in conn.execute(
                "SELECT player_id FROM guild_members WHERE guild_id = ?", (guild_id,)
            )]
        for member in members:
            for other in self._clients_for(member):
                self.emulator.send_to(other, frame)

    def _push_guild_members(self, guild_id):
        with self._connect() as conn:
            members = [row[0] for row in conn.execute(
                "SELECT player_id FROM guild_members WHERE guild_id = ?", (guild_id,)
            )]
        for member in members:
            for client_id in self._clients_for(member):
                self._push_guild_to(client_id, guild_id)

    def generic(self, message, client_id):
        name = message.get("name")
        if message.get("rpc") is None:
            if message.get("placeholder"):
                self.emulator.debug_log_tcp("PLACEHOLDER: %s %s" % (name, message.get("placeholder")))
            return None
        if message.get("placeholder"):
            self.emulator.debug_log_tcp("PLACEHOLDER: %s %s" % (name, message.get("placeholder")))
            return self.emulator.igor.reply_fail(
                message["service"], message["function"], message["rpc"], message["placeholder"]
            )
        if message.get("emptyReply"):
            return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])
        kind = message.get("payloadKind")
        if kind == "list":
            return self.emulator.igor.reply_uints(message["service"], message["function"], message["rpc"], [])
        if kind == "readable" and message.get("payloadType"):
            return self.emulator.igor.reply_type(
                message["service"], message["function"], message["rpc"],
                "Protocol2." + message["payloadType"], {},
            )
        if kind == "uint":
            return self._uint_reply(message, 0)
        if kind == "int":
            return self._status_payload(message, struct.pack("<i", 0))
        if kind == "bool":
            return self._status_payload(message, b"\x00")
        if kind == "float":
            return self._status_payload(message, struct.pack("<f", 0.0))
        if kind == "string":
            return self._status_payload(message, self._igor_string(""))
        if kind in ("optional_uint", "optional", "dict"):
            return self._status_payload(message, b"\x00")
        return None

    def _placeholder_fail(self, message):
        text = message.get("placeholder") or "Debug eval is not run on this server."
        self.emulator.debug_log_tcp("PLACEHOLDER: %s %s" % (message.get("name"), text))
        if message.get("rpc") is None:
            return None
        return self.emulator.igor.reply_fail(message["service"], message["function"], message["rpc"], text)

    CHAT_MAX_CHARS = 500

    def _chat_refused(self, client_id, player_id, text):
        """True when this line must not be relayed: a muted rider, an empty line, or one too long."""
        from admin import active_mute, muted_frame
        mute = active_mute(self.emulator.database.db_path, player_id)
        if mute:
            self.emulator.send_to(client_id, muted_frame(*mute))
            self.emulator.debug_log_tcp("CHAT MUTED: %s" % client_id)
            return True
        return not text.strip() or len(text) > self.CHAT_MAX_CHARS

    def _relay_room(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        room_id = struct.unpack_from("<I", raw)[0] if len(raw) >= 4 else 1
        text = ""
        if len(raw) > 4:
            try:
                text, _offset = self._read_igor_string(raw, 4)
            except (IndexError, UnicodeDecodeError):
                text = ""
        refused = self._chat_refused(client_id, player_id, text)
        if refused:
            return None
        frame = self._room_message_frame(room_id, player_id or 1, text)
        for other in list(self.emulator.tcp_clients):
            self._announce_room(other, room_id)
            self.emulator.send_to(other, frame)
        self.emulator.debug_log_tcp("CHAT ROOM: %s room=%s" % (client_id, room_id))
        return None

    def _relay_private(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        target = struct.unpack_from("<I", raw)[0] if len(raw) >= 4 else 0
        text = ""
        if len(raw) > 4:
            try:
                text, _offset = self._read_igor_string(raw, 4)
            except (IndexError, UnicodeDecodeError):
                text = ""
        refused = self._chat_refused(client_id, player_id, text)
        if refused:
            return None
        frame = self._private_message_frame(player_id or 1, target, text)
        self.emulator.send_to(client_id, frame)
        for other, other_player in list(self.emulator.client_players.items()):
            if other_player == target and other != client_id:
                self.emulator.send_to(other, frame)
        self.emulator.debug_log_tcp("CHAT PRIVATE: %s to=%s" % (client_id, target))
        return None

    def _announce_room(self, client_id, room_id):
        key = (client_id, room_id)
        if key in self.chat_rooms:
            return
        self.chat_rooms.add(key)
        if int(room_id) == 2:
            self.emulator.send_to(client_id, self._global_room_frame())
            return
        payload = bytes([0x60]) + struct.pack("<IB", int(room_id), 1)
        payload += self._igor_string("local")
        payload += self._igor_string("Welcome")
        self.emulator.send_to(client_id, self._push_frame(103, 1, payload))

    def _room_message_frame(self, room_id, player_id, text):
        self.chat_seq += 1
        payload = struct.pack("<III", int(room_id), self.chat_seq, int(player_id))
        payload += self._igor_string(text)
        payload += struct.pack("<Q", int(time.time() * 1000))  # client reads ms since 1970
        return self._push_frame(103, 7, payload)

    def _private_message_frame(self, sender, target, text):
        payload = struct.pack("<II", int(sender), int(target))
        payload += self._igor_string(text)
        payload += struct.pack("<Q", int(time.time() * 1000))  # client reads ms since 1970
        return self._push_frame(103, 4, payload)

    def _push_frame(self, service, function, payload):
        body = bytes([service & 255, function & 255]) + payload
        return self._varint(len(body)) + body

    def _rank_place(self, player_id):
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT player_id FROM player_data
                   ORDER BY COALESCE(rating, 1000) DESC, player_id ASC"""
            ).fetchall()
        for place, (user_id,) in enumerate(rows, start=1):
            if user_id == player_id:
                return place
        return 0

    def _ladder_payload(self, count=0):
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT player_id FROM player_data
                   ORDER BY COALESCE(rating, 1000) DESC, player_id ASC"""
            ).fetchall()
        if count > 0:
            rows = rows[:count]
        payload = self._varint(len(rows))
        for place, (user_id,) in enumerate(rows, start=1):
            payload += struct.pack("<II", int(user_id), place)
        return payload

    def _rm_start(self, player_id, opponent_id):
        return self.emulator.igor.push_object(107, 5, "Protocol2.ReplyRmStart", {
            "CourseKey": 3023709559,
            "Opponent": {"Id": opponent_id or self._rival_id(), "Place": 1, "Level": 1},
        })

    def _status_payload(self, message, payload):
        body = bytes([int(message["service"]) & 255, int(message["function"]) & 255])
        body += struct.pack("<HB", int(message["rpc"]), 0) + payload
        return self._varint(len(body)) + body

    def _igor_string(self, text):
        raw = (text or "").encode("utf-8")
        return self._varint(len(raw)) + raw

    def _uint_list(self, rest):
        raw = bytes.fromhex(rest or "")
        count, offset = GameFeatures._read_varint(self, raw, 0)
        ids = []
        for _index in range(int(count or 0)):
            if offset + 4 > len(raw):
                break
            ids.append(struct.unpack_from("<I", raw, offset)[0])
            offset += 4
        return ids

    def _read_varint(self, raw, offset):
        value = 0
        shift = 0
        while offset < len(raw):
            byte = raw[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, offset
            shift += 7
        return value, offset

    def _read_igor_string(self, raw, offset):
        value = 0
        shift = 0
        while True:
            byte = raw[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                break
            shift += 7
        text = raw[offset:offset + value].decode("utf-8")
        return text, offset + value

    def _push_present(self, service, function, type_name, fields):
        frame = self.emulator.igor.push_object(service, function, type_name, fields)
        index = 0
        while frame[index] & 128:
            index += 1
        index += 1
        body = frame[index:]
        body = body[:2] + b"\x01" + body[2:]
        return self._varint(len(body)) + body

    def _mark_present(self, frame):
        index = 0
        while frame[index] & 128:
            index += 1
        index += 1
        body = frame[index:]
        body = body[:5] + b"\x01" + body[5:]
        return self._varint(len(body)) + body

    def _uint_reply(self, message, value):
        return self._frame_uint(message, value)

    def _frame_uint(self, message, value):
        body = bytes([message["service"], message["function"]]) + struct.pack("<HB", message["rpc"], 0) + struct.pack("<I", int(value))
        return self._varint(len(body)) + body

    def _hash_and_count(self, rest):
        raw = bytes.fromhex(rest) if rest else b""
        if len(raw) < 4:
            return 0, 0
        item_hash = struct.unpack_from("<I", raw)[0]
        count = struct.unpack_from("<I", raw, 4)[0] if len(raw) >= 8 else 1
        return item_hash, count

    def _uint_from_rest(self, rest):
        raw = bytes.fromhex(rest) if rest else b""
        if len(raw) < 4:
            return 0
        return struct.unpack_from("<I", raw)[0]

    def _size_prefix(self, count):
        return bytes([count])

    def _varint(self, value):
        data = bytearray()
        while value > 127:
            data.append((value & 127) | 128)
            value >>= 7
        data.append(value)
        return bytes(data)
