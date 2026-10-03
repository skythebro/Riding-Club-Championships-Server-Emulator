"""Persisted solo play, courses, and multiplayer for clients connected to this server."""

import datetime
import json
import sqlite3
import struct
import threading
import time
import zlib
from pathlib import Path

from server_config import ride_is_plausible

MAP_PATH = Path(__file__).resolve().parent / "igor_host" / "protocol_map.json"

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
    def __init__(self, emulator):
        self.emulator = emulator
        self.waiting = []
        self.opponents = {}
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
        self.catalog = {}
        if MAP_PATH.exists():
            self.catalog = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        self.balance = self._load_balance()
        self._ensure_columns()

    def _load_balance(self):
        path = Path(__file__).resolve().parent / "balance.json"
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
        return sqlite3.connect(self.emulator.database.db_path)

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

    def _attributes(self, player_id):
        with self._connect() as conn:
            row = conn.execute("SELECT skills FROM player_data WHERE player_id = ?", (player_id,)).fetchone()
        if not row or not row[0]:
            return []
        try:
            saved = json.loads(row[0])
        except ValueError:
            return []
        spent = {}
        for item in saved:
            stat = int(item.get("type") or 0)
            value = int(item.get("value") or 0)
            if stat and value:
                spent[stat] = value
        grooming = float(self.profile(player_id).get("grooming") or 0)
        clean_at = float(self.bal("grooming", "clean_at", default=0.8))
        dirty_at = float(self.bal("grooming", "dirty_at", default=0.3))
        if grooming >= clean_at:
            buff = int(self.bal("grooming", "clean_buff", default=1))
        elif grooming <= dirty_at:
            buff = int(self.bal("grooming", "dirty_debuff", default=-1))
        else:
            buff = 0
        attributes = []
        for stat in range(1, 7):
            value = spent.get(stat, 0)
            if value or buff:
                attributes.append({"StatType": stat, "Value": value, "Buff": buff})
        return attributes

    def _gear_by_id(self):
        return {item.get("id"): item for item in self._gear_by_hash().values() if item.get("id")}

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

    def dispatch(self, message, client_id):
        name = message.get("name")
        player_id = self.player_id(client_id)
        try:
            handler = getattr(self, "on_" + name, None) if name else None
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

    def on_GetLikes(self, message, client_id, player_id):
        ids = self.liked_ids(player_id) if player_id else []
        day, _claimed = self._daily_state(player_id) if player_id else (1, False)
        return self.emulator.igor.reply_likes(message["rpc"], ids) + self.emulator.igor.push_daily_bonus(day=day) + self.session_push(player_id or 1)

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
        course_id = self._practice_course_id(message.get("rest") or "")
        started = player_id in self.practice_open
        if started and course_id and course_id in self.courses:
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
        if player_id and started:
            data = self.profile(player_id)
            if int(data["action_points"] or 0) < ap_cost:
                reward_coins = 0
                reward_xp = 0
                ap_cost = 0
                wear = 0
            worn = max(0.0, float(data["grooming"] if data["grooming"] is not None else 1) - wear)
            self.save_profile(
                player_id,
                coins=data["coins"] + reward_coins,
                horse_xp=data["horse_xp"] + reward_xp,
                action_points=max(0, data["action_points"] - ap_cost),
                grooming=worn,
            )
        else:
            reward_coins = 0
            reward_xp = 0
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.ReplyPracticeEnd",
            {
                "PersonalRecord": False,
                "Reward": {
                    "Money": {"Coins": reward_coins, "SkillTickets": 0},
                    "Xp": reward_xp,
                    "Ap": 0,
                    "Items": [],
                },
            },
        ) + self.session_push(player_id or 1)

    def on_StartTutorial(self, message, client_id, player_id):
        # The tutorial scene is local. Leaving the account in Tutorial makes the
        # next login skip the menu.
        if player_id:
            self.set_user_state(player_id, 3)
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

    def _min_ride(self):
        config = getattr(self.emulator, "join_config", None) or {}
        try:
            return float(config.get("min_ride_seconds") or 8)
        except (TypeError, ValueError):
            return 8

    def _data_file(self, name):
        root = Path(__file__).resolve().parent
        installed = root.parent / "UserData" / name
        if installed.exists():
            return installed
        return root / "gamedata" / name

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
                self.save_profile(
                    player_id,
                    horse_xp=data["horse_xp"] + xp_gain,
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
            body = self._empty_slot(slot_id, name)
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
            {"Id": slot_id, "Name": name, "Type": 1},
        )

    def _empty_slot(self, slot_id, name):
        # Show-jump slot with no maps and no screenshot.
        body = bytes([0x00]) + struct.pack("<iB?", int(slot_id), 1, False)
        return body + self._igor_string(name)

    def on_SaveSlot(self, message, client_id, player_id):
        owner = player_id or 0
        body = message.get("rest") or ""
        raw = bytes.fromhex(body) if body else b""
        slot_id = struct.unpack_from("<i", raw, 1)[0] if len(raw) >= 5 else len(self.slots.get(owner, [])) + 1
        with self._connect() as conn:
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

    def on_Submit(self, message, client_id, player_id):
        owner = player_id or 0
        stored = self.slots.get(owner) or [""]
        body = stored[-1]
        with self._connect() as conn:
            course_id = conn.execute("SELECT COALESCE(MAX(course_id), 0) + 1 FROM courses").fetchone()[0]
            name = "Meadow %s" % course_id
            conn.execute(
                "INSERT INTO courses (course_id, owner_id, name, body) VALUES (?, ?, ?, ?)",
                (course_id, owner, name, body),
            )
        self.courses[course_id] = {"owner": owner, "body": body, "name": name}
        return self._uint_reply(message, course_id)

    def _course_ids(self, owner=None):
        if owner is None:
            return sorted(self.courses)
        return sorted(
            course_id for course_id, course in self.courses.items() if course.get("owner") == owner
        )

    def on_GetCourseList(self, message, client_id, player_id):
        return self.emulator.igor.reply_uints(
            message["service"], message["function"], message["rpc"], self._course_ids()
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

    def _course_info(self, course_id, course):
        # Bit 0 is the ideal time, bit 1 is the jump height. Terrain is the paddock.
        name = course.get("name") or ("Meadow %s" % course_id)
        min_time = int(course.get("min_time") or 60)
        body = bytes([0xC0])
        body += struct.pack("<IB", int(course_id), 1)
        body += self._igor_string(name)
        body += struct.pack("<IiI", int(course.get("owner") or 0), min_time, 3684040698)
        body += struct.pack("<i", 110)
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

    def on_FindCourses(self, message, client_id, player_id):
        return self.emulator.igor.reply_uints(
            message["service"], message["function"], message["rpc"], self._course_ids()
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
        return None

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
            self.set_user_state(player_id, 3)
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
        raw = bytes.fromhex(rest) if rest else b""
        if len(raw) < 2:
            return 0
        flags = raw[0]
        offset = 1
        if flags & 0x80 and offset + 4 <= len(raw):
            offset += 4
        if flags & 0x40 and offset + 4 <= len(raw):
            return struct.unpack_from("<I", raw, offset)[0]
        return 0

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
        saved = self._slot_show_jump(course.get("body") or "")
        if saved and self._map_has_jumps(saved):
            payload = bytes([0x80])
            payload += struct.pack("<IBI", int(course_id), 1, int(course.get("owner") or 0))
            payload += self._igor_string(course.get("name") or "")
            payload += saved + bytes([0])
            payload += struct.pack(
                "<Iiii", 1790492400, int(course.get("played") or 0), int(course.get("completed") or 0), int(likes)
            )
            self.emulator.debug_log_tcp("COURSE MAP: %s id=%s bytes=%s" % (client_id, course_id, len(saved)))
            return self._status_payload(message, payload)
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
        count = len(self.liked_ids(player_id)) if player_id else 0
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
        count = len(self.liked_ids(player_id)) if player_id else 0
        self.emulator.debug_log_tcp("UNLIKE: %s course=%s count=%s" % (client_id, course_id, count))
        return self._uint_reply(message, count)

    def on_RmEnd(self, message, client_id, player_id):
        result = self._parse_rm_result(message.get("rest") or "")
        my_time = float(result.get("time") or 0)
        my_penalty = int(result.get("penalty") or 0)
        opp_time = my_time + 1.5
        opp_penalty = 0
        in_match = client_id in self.opponents
        if in_match:
            self.opponents.pop(client_id, None)
        plausible = ride_is_plausible(my_time, my_penalty, self._min_ride())
        # Fewer faults wins. Equal faults, then the faster time.
        won = bool(player_id) and in_match and plausible and not result.get("canceled") and (
            my_penalty < opp_penalty or (my_penalty == opp_penalty and my_time <= opp_time)
        )
        if player_id and in_match and not result.get("canceled"):
            with self._connect() as conn:
                conn.execute(
                    """UPDATE player_data
                       SET action_points = MAX(0, COALESCE(action_points, 0) - ?),
                           rating = COALESCE(rating, 1000) + ?,
                           coins = COALESCE(coins, 0) + ?
                       WHERE player_id = ?""",
                    (
                        int(self.bal("action_points", "ranked", default=20)),
                        int(self.bal("ranked", "win_rating" if won else "loss_rating", default=15 if won else 0)),
                        int(self.bal("ranked", "win_coins" if won else "loss_coins", default=25 if won else 0)),
                        player_id,
                    ),
                )
        self.emulator.debug_log_tcp(
            "RANKED END: %s time=%s penalty=%s won=%s" % (client_id, my_time, my_penalty, won)
        )
        try:
            finished = {
                "MyResult": 1 if won else 2,
                "PersonalRecord": won,
                "Me": {
                    "UserId": player_id or 1,
                    "Place": 1 if won else 2,
                    "Time": my_time,
                    "Penalty": my_penalty,
                },
                "Opponent": {
                    "UserId": self.opponents.get(client_id) or self._rival_id(),
                    "Place": 2 if won else 1,
                    "Time": opp_time,
                    "Penalty": opp_penalty,
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
            return None
        if player_id:
            frame += self.session_push(player_id)
        self.emulator.send_to(client_id, frame)
        return None

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
            self.emulator.debug_log_tcp("RANKED PAIR: %s %s" % (other, client_id))
            return None
        self.waiting.append(client_id)
        owner = player_id or 1

        def start_rival():
            time.sleep(2)
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
        self.waiting = [client for client in self.waiting if client != client_id]
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
        self._push_challenge(client_id, target, 1)
        for other, other_player in list(self.emulator.client_players.items()):
            if other_player == target and other != client_id:
                self._push_challenge(other, player_id or 1, 2)
        self.emulator.debug_log_tcp("CHALLENGE: %s target=%s" % (client_id, target))
        return self._status_payload(message, struct.pack("<I", 3023709559))

    def _push_challenge(self, client_id, other_player, status):
        payload = bytes([0x00]) + struct.pack("<IBQ", int(other_player), int(status), 0)
        payload += struct.pack("<I", 3023709559)
        self.emulator.send_to(client_id, self._push_frame(109, 13, payload))

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
        ticket_cost = int(self.bal("showdown", "skill_tickets", default=150))
        with self._connect() as conn:
            already = conn.execute(
                """SELECT COUNT(*) FROM ds_results
                   WHERE player_id = ? AND date(created_at) = date('now', 'localtime')""",
                (player_id,),
            ).fetchone()[0]
            if not already:
                if int(data.get("horse_level") or 1) < needed_level:
                    self.emulator.debug_log_tcp("DS RESULT REFUSED: %s level" % client_id)
                    return None
                if int(data.get("skill_tickets") or 0) < ticket_cost:
                    self.emulator.debug_log_tcp("DS RESULT REFUSED: %s tickets" % client_id)
                    return None
                self.save_profile(player_id, skill_tickets=int(data["skill_tickets"]) - ticket_cost)
            conn.execute(
                "INSERT INTO ds_results (player_id, course_hash, time, penalty) VALUES (?, ?, ?, ?)",
                (player_id, course_hash, time_s, penalty),
            )
        self.emulator.debug_log_tcp(
            "DS RESULT: %s time=%.2f penalty=%s" % (client_id, time_s, penalty)
        )
        return None

    def _ds_board(self, today):
        clause = "date(created_at) = date('now', 'localtime')" if today else "date(created_at) < date('now', 'localtime')"
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT player_id, time, penalty FROM ds_results WHERE %s" % clause
            ).fetchall()
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

    def on_DsDayTable(self, message, client_id, player_id):
        table = self._ds_board(True)
        fields = {"Table": table}
        for row in table:
            if row["PlayerId"] == (player_id or 0):
                fields["MyResult"] = row
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.DsResultsTable", fields
        )

    def on_DsLastWeekTable(self, message, client_id, player_id):
        table = self._ds_board(False)
        fields = {"Table": table}
        for row in table:
            if row["PlayerId"] == (player_id or 0):
                fields["MyResult"] = row
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.DsResultsTable", fields
        )

    def on_DsQualificationTable(self, message, client_id, player_id):
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT player_id, MIN(time) FROM ds_results
                   GROUP BY player_id ORDER BY MIN(time) ASC"""
            ).fetchall()
        table = []
        mine = None
        for place, (user_id, best) in enumerate(rows, start=1):
            record = {"PlayerId": user_id, "Scores": int(best * 100), "Place": place}
            table.append(record)
            if user_id == (player_id or 0):
                mine = record
        fields = {"Table": table}
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
        if player_id:
            with self._connect() as conn:
                claimed = conn.execute(
                    "SELECT ds_claimed FROM player_data WHERE player_id = ?", (player_id,)
                ).fetchone()
            if not claimed or not claimed[0]:
                data = self.profile(player_id)
                self.save_profile(
                    player_id,
                    coins=data["coins"] + coins,
                    horse_xp=data["horse_xp"] + xp,
                    ds_claimed=1,
                )
                if item_id:
                    item_hash = zlib.crc32(str(item_id).encode("utf-8")) & 0xffffffff
                    self.add_item(player_id, item_hash, 1)
                self.emulator.send_to(client_id, self.session_push(player_id))
        self.emulator.debug_log_tcp(
            "DS REWARD: %s place=%s coins=%s xp=%s item=%s" % (client_id, place, coins, xp, item_id)
        )
        return None

    def on_DsMenu(self, message, client_id, player_id):
        # TimeLeft of 0 makes GuiShowdownMenu request this again every frame.
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.ReplyDsMenu",
            {
                "CurrentDay": 6,
                "TimeLeft": 3600,
                "DsMinLevel": int(self.bal("showdown", "min_level", default=10)),
                "ApprovedCost": (self.profile(player_id).get("skill_tickets") or 0) >= int(self.bal("showdown", "skill_tickets", default=150)) if player_id else False,
                "ApprovedLevel": (self.profile(player_id).get("horse_level") or 1) >= int(self.bal("showdown", "min_level", default=10)) if player_id else False,
                "ApprovedQual": True,
                "ApprovedTry": True,
                "Course": 3023709559,
                "DsCost": {"Coins": 0, "SkillTickets": int(self.bal("showdown", "skill_tickets", default=150))},
            },
        )

    def on_GetCourseLeaderboard(self, message, client_id, player_id):
        rows = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT player_id, time, penalty FROM ds_results ORDER BY time ASC, penalty ASC"
            ).fetchall()
        scores = [
            {"UserId": row[0], "Time": row[1], "Penalty": row[2]}
            for row in rows
        ]
        mine = next((score for score in scores if score["UserId"] == (player_id or 0)), None)
        if mine is None and player_id:
            mine = {"UserId": player_id}
        return self.emulator.igor.reply_type(
            message["service"], message["function"], message["rpc"], "Protocol2.CourseScores",
            {"PlayerScore": mine or {}, "GlobalHighscores": scores, "FriendsHighscores": scores},
        )

    def on_GetLadder(self, message, client_id, player_id):
        return self._status_payload(message, self._ladder_payload())

    def on_GetFriendsLadder(self, message, client_id, player_id):
        return self._status_payload(message, self._ladder_payload())

    def on_GetGuildLadder(self, message, client_id, player_id):
        return self._status_payload(message, self._ladder_payload())

    def on_GetPlayersLadder(self, message, client_id, player_id):
        return self._status_payload(message, self._ladder_payload())

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
        horse = data["horse_name"] if data["horse_name"] != "Horse" else ""
        payload += (b"\x01" + self._igor_string(horse)) if horse else b"\x00"
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
                "FriendsCount": 0,
                "Rank": self._rank_place(asked),
                "PlayerEquipment": [],
                "PlayerGender": data["player_gender"],
                "PlayerSkinId": data["player_skin"],
                "HorseName": data["horse_name"],
                "HorseGender": data["horse_gender"],
                "HorseLevel": data["horse_level"],
                "HorseSkinId": data["horse_skin"],
                "HorseTailSkinId": data["tail_skin"],
                "HorseEquipment": [],
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
            remaining = max(0, data["skill_points"] - spent)
            totals = {}
            for item in self._attributes(player_id):
                totals[int(item["StatType"])] = int(item["Value"])
            for delta in deltas:
                stat = int(delta["type"])
                totals[stat] = totals.get(stat, 0) + int(delta["value"])
            saved = [{"type": stat, "value": value} for stat, value in totals.items() if value]
            self.save_profile(player_id, skill_points=remaining, skills=json.dumps(saved))
            self.emulator.debug_log_tcp("SKILL POINTS: %s spent=%s" % (client_id, spent))
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
        return self._guild_list_reply(message, None, False)

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

    def _guild_list_reply(self, message, needle, short):
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
        guild_id = self._player_guild(player_id)
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
        guild_id = self._player_guild(player_id)
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
        guild_id = self._player_guild(player_id)
        if guild_id:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE guilds SET %s = ? WHERE guild_id = ?" % column, (text, guild_id)
                )
            self._push_guild_members(guild_id)
            news_type = 6 if column == "description" else 7
            self._push_guild_news(guild_id, news_type, player_id, text)
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildLeave(self, message, client_id, player_id):
        if player_id:
            with self._connect() as conn:
                conn.execute("DELETE FROM guild_members WHERE player_id = ?", (player_id,))
                conn.execute("DELETE FROM guild_requests WHERE player_id = ?", (player_id,))
        self.emulator.send_to(client_id, self._push_frame(105, 18, b"\x00"))
        self.emulator.debug_log_tcp("GUILD LEAVE: %s" % client_id)
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildChangeInvite(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        allow = 1 if raw and raw[0] else 0
        guild_id = self._player_guild(player_id)
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
        guild_id = self._player_guild(player_id)
        if guild_id and target:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM guild_requests WHERE guild_id = ? AND player_id = ?",
                    (guild_id, target),
                )
        self.emulator.debug_log_tcp("GUILD CANCEL: %s target=%s" % (client_id, target))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildAcceptRequest(self, message, client_id, player_id):
        target = self._uint_from_rest(message.get("rest") or "")
        missing = self._missing_player(message, target)
        if missing:
            return missing
        guild_id = self._player_guild(player_id)
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
        guild_id = self._player_guild(player_id)
        if guild_id and target and target != (player_id or 0):
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
        self.emulator.debug_log_tcp("GUILD KICK: %s target=%s" % (client_id, target))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_GuildRoleUser(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        target = struct.unpack_from("<I", raw)[0] if len(raw) >= 4 else 0
        role = raw[4] if len(raw) > 4 else 3
        missing = self._missing_player(message, target)
        if missing:
            return missing
        guild_id = self._player_guild(player_id)
        if guild_id and target:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE guild_members SET role = ? WHERE guild_id = ? AND player_id = ?",
                    (role, guild_id, target),
                )
            self._push_guild_members(guild_id)
        self.emulator.debug_log_tcp("GUILD ROLE: %s target=%s role=%s" % (client_id, target, role))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_NoGuildCreateRequest(self, message, client_id, player_id):
        guild_id = self._uint_from_rest(message.get("rest") or "")
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
        self.emulator.debug_log_tcp("GUILD JOIN ACCEPT: %s guild=%s" % (client_id, guild_id))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

    def on_NoGuildCancelRequest(self, message, client_id, player_id):
        guild_id = self._uint_from_rest(message.get("rest") or "")
        if guild_id and player_id:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM guild_requests WHERE guild_id = ? AND player_id = ? AND kind = 'join'",
                    (guild_id, player_id),
                )
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
        guild_id = self._player_guild(player_id)
        if guild_id and target:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO guild_requests (guild_id, player_id, kind) VALUES (?, ?, 'invite')",
                    (guild_id, target),
                )
        self.emulator.debug_log_tcp("GUILD INVITE PLAYER: %s target=%s" % (client_id, target))
        return self.emulator.igor.reply_ok(message["service"], message["function"], message["rpc"])

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

    def _relay_room(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        room_id = struct.unpack_from("<I", raw)[0] if len(raw) >= 4 else 1
        text = ""
        if len(raw) > 4:
            try:
                text, _offset = self._read_igor_string(raw, 4)
            except (IndexError, UnicodeDecodeError):
                text = ""
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
        payload = bytes([0x60]) + struct.pack("<IB", int(room_id), 1)
        payload += self._igor_string("local")
        payload += self._igor_string("Welcome")
        self.emulator.send_to(client_id, self._push_frame(103, 1, payload))

    def _room_message_frame(self, room_id, player_id, text):
        self.chat_seq += 1
        payload = struct.pack("<III", int(room_id), self.chat_seq, int(player_id))
        payload += self._igor_string(text)
        payload += struct.pack("<Q", int(time.time()))
        return self._push_frame(103, 7, payload)

    def _private_message_frame(self, sender, target, text):
        payload = struct.pack("<II", int(sender), int(target))
        payload += self._igor_string(text)
        payload += struct.pack("<Q", int(time.time()))
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

    def _ladder_payload(self):
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT player_id FROM player_data
                   ORDER BY COALESCE(rating, 1000) DESC, player_id ASC"""
            ).fetchall()
        payload = self._varint(len(rows))
        for place, (user_id,) in enumerate(rows, start=1):
            payload += struct.pack("<II", int(user_id), place)
        return payload

    def _rm_start(self, player_id, opponent_id):
        name = "Rival" if not opponent_id else "Player%s" % opponent_id
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
