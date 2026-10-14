"""Persisted solo play, courses, and multiplayer for clients connected to this server."""

import datetime
import json
import struct
import threading
import time

from game_clubs import ClubsMixin
from game_chat import ChatMixin
from game_ranked import RankedMixin
from game_showdown import ShowdownMixin
from game_courses import CoursesMixin
from paths import APP_DIR
from server_config import ride_is_plausible
from migrations import PROFILE_COLUMNS, migrate
from storage import connect_db

MAP_PATH = APP_DIR / "igor_host" / "protocol_map.json"


class GameFeatures(ClubsMixin, ChatMixin, RankedMixin, ShowdownMixin, CoursesMixin):
    # The login burst sends these before the Login reply has been read.
    BEFORE_LOGIN = frozenset({"Subscribe"})

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
            migrate(conn)
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
        if not player_id:
            return reply
        frame = reply + self.session_push(player_id)
        # The result popup opens the showdown window, so it waits until the daily gift is out of the way.
        if not need_accept:
            frame += self._week_finished_frame(player_id)
        return frame

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
        level_frame = b""
        if player_id and kept:
            data = self.profile(player_id)
            if int(data["action_points"] or 0) < ap_cost:
                reward_coins = 0
                reward_xp = 0
                ap_cost = 0
                wear = 0
            worn = max(0.0, float(data["grooming"] if data["grooming"] is not None else 1) - wear)
            level, xp, points = self._apply_xp(data, reward_xp)
            level_frame = self._level_up_frame(data["horse_level"], level)
            bonus_coins, bonus_tickets, bonus_ap = self._level_bonus(data["horse_level"], level)
            self.save_profile(
                player_id,
                coins=data["coins"] + reward_coins + bonus_coins,
                skill_tickets=data["skill_tickets"] + bonus_tickets,
                horse_level=level,
                horse_xp=xp,
                skill_points=points,
                action_points=max(0, data["action_points"] - ap_cost) + bonus_ap,
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
        ) + self.session_push(player_id or 1) + level_frame

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

    def _level_up_frame(self, old_level, new_level):
        """Player.NewLevel: opens the level-up popup. Nothing when the level did not rise."""
        if int(new_level) <= int(old_level or 1):
            return b""
        return self._push_frame(109, 4, struct.pack("<i", int(new_level)))

    def _level_bonus(self, old_level, new_level):
        """Coins, skill tickets, and action points for the levels just gained (balance.json level_up_bonus)."""
        gained = max(0, int(new_level) - int(old_level or 1))
        return (gained * int(self.bal("level_up_bonus", "coins", default=0)),
                gained * int(self.bal("level_up_bonus", "skill_tickets", default=0)),
                gained * int(self.bal("level_up_bonus", "ap", default=100)))

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
                level_frame = self._level_up_frame(data["horse_level"], level)
                bonus_coins, bonus_tickets, bonus_ap = self._level_bonus(data["horse_level"], level)
                self.save_profile(
                    player_id,
                    horse_level=level,
                    horse_xp=xp,
                    skill_points=points,
                    skill_tickets=data["skill_tickets"] + bonus_tickets,
                    action_points=data["action_points"] + bonus_ap,
                    coins=data["coins"] + coins_gain + bonus_coins,
                )
                self.emulator.debug_log_tcp("USE ITEM: %s hash=%s" % (client_id, item_hash))
                return self.session_push(player_id) + level_frame
        return None

    def on_UpgradeInventoryItem(self, message, client_id, player_id):
        return self.session_push(player_id) if player_id else None

    def on_UpgradeEquipmentItem(self, message, client_id, player_id):
        return self.session_push(player_id) if player_id else None

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

    def _player_exists(self, player_id):
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM users WHERE player_id = ?", (player_id,)).fetchone() is not None

    def _guild_exists(self, guild_id):
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM guilds WHERE guild_id = ?", (guild_id,)).fetchone() is not None

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
