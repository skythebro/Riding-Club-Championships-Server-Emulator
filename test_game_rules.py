"""Game rules against a real GameFeatures and a throwaway save. No IgorHost needed."""

import datetime
import struct
import tempfile
import unittest
from pathlib import Path

from game_features import GameFeatures
from Server import UserDatabase


class FakeIgor:
    """Frames are short tags, so tests can see which reply a handler chose."""

    def __init__(self):
        self.pushed = []

    def reply_ok(self, service, function, rpc):
        return b"ok"

    def reply_fail(self, service, function, rpc, text):
        return b"fail:" + text.encode("utf-8")

    def reply_type(self, service, function, rpc, type_name, fields=None):
        return b"type"

    def reply_uints(self, service, function, rpc, ids):
        return b"uints"

    def reply_blobs(self, service, function, rpc, items):
        return b"blobs"

    def push_object(self, service, function, type_name, fields=None):
        self.pushed.append((type_name, fields))
        return bytes([2, service, function])

    def push_session(self, fields, inventory=None):
        return b"session"

    def push_daily_bonus(self, **kwargs):
        return b"daily"

    def reply_likes(self, rpc, ids=None):
        return b"likes"


class FakeEmulator:
    def __init__(self, db_path):
        self.database = UserDatabase(db_path)
        self.igor = FakeIgor()
        self.client_players = {}
        self.tcp_clients = {}
        self.join_config = {}
        self.sent = []

    def send_to(self, client_id, data):
        self.sent.append((client_id, data))

    def debug_log_tcp(self, text):
        pass


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.emu = FakeEmulator(str(Path(self.tmp.name) / "users.db"))
        self.features = GameFeatures(self.emu)
        self.emu.features = self.features
        self.features.balance = {
            "club": {"max_members": 3, "name_length": 30, "short_name_length": 10,
                     "description_length": 400, "welcome_length": 100, "rename_coins": 0},
            "ranked": {"win_coins": 25, "win_rating": 15, "loss_coins": 0, "loss_rating": 0,
                       "settle_wait_seconds": 120},
            "course_editor": {"max_slot_bytes": 64},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def rider(self, number, client=None):
        player_id, _data = self.emu.database.get_or_create_user("Steam", str(76561198000000000 + number), "")
        if client:
            self.emu.client_players[client] = player_id
        return player_id

    def sql(self, query, args=()):
        with self.features._connect() as conn:
            return conn.execute(query, args).fetchall()

    @staticmethod
    def message(function, rest=b"", service=105, rpc=1):
        return {"service": service, "function": function, "rpc": rpc, "rest": rest.hex()}

    def guild_error(self, frame):
        """The GuildErrorType byte of a status-1 reply, or None for any other reply."""
        if len(frame) >= 7 and frame[5] == 1:
            return frame[6]
        return None

    def make_club(self, owner, *members, name="Meadow Riders"):
        raw = self.features._igor_string(name) + self.features._igor_string(name[:4])
        self.features.on_GuildCreate(self.message(10, raw), "c%s" % owner, owner)
        guild_id = self.features._player_guild(owner)
        for member in members:
            with self.features._connect() as conn:
                conn.execute(
                    "INSERT INTO guild_members (guild_id, player_id, role) VALUES (?, ?, 3)", (guild_id, member)
                )
        return guild_id


class ClubRuleTests(Harness):
    def test_a_member_cannot_rename_kick_or_promote(self):
        owner, member, other = self.rider(1), self.rider(2), self.rider(3)
        self.make_club(owner, member, other)
        rename = self.features.on_GuildChangeName(
            self.message(21, self.features._igor_string("Mine") + self.features._igor_string("M")), "c", member
        )
        self.assertEqual(self.guild_error(rename), GameFeatures.GUILD_ERR_DENIED)
        kick = self.features.on_GuildKickUser(self.message(29, struct.pack("<I", owner)), "c", member)
        self.assertEqual(self.guild_error(kick), GameFeatures.GUILD_ERR_DENIED)
        promote = self.features.on_GuildRoleUser(self.message(30, struct.pack("<IB", member, 1)), "c", member)
        self.assertEqual(self.guild_error(promote), GameFeatures.GUILD_ERR_DENIED)
        self.assertEqual(self.sql("SELECT role FROM guild_members WHERE player_id = ?", (owner,)), [(1,)])

    def test_kicking_posts_that_the_rider_was_removed(self):
        owner, member = self.rider(1, "owner"), self.rider(2)
        self.make_club(owner, member)
        self.assertEqual(self.features.on_GuildKickUser(self.message(29, struct.pack("<I", member)), "c", owner), b"ok")
        self.assertEqual(self.sql("SELECT player_id FROM guild_members"), [(owner,)])
        # Guild news push (105, 19), one item, news type 3 is "removed".
        news = [frame for client, frame in self.emu.sent if client == "owner" and frame[1:3] == bytes([105, 19])]
        self.assertEqual(news[-1][5], 3)

    def test_a_manager_cannot_kick_another_manager(self):
        owner, first, second = self.rider(1), self.rider(2), self.rider(3)
        self.make_club(owner, first, second)
        self.sql("UPDATE guild_members SET role = 2 WHERE player_id IN (?, ?)", (first, second))
        kick = self.features.on_GuildKickUser(self.message(29, struct.pack("<I", second)), "c", first)
        self.assertEqual(self.guild_error(kick), GameFeatures.GUILD_ERR_DENIED)

    def test_naming_a_new_owner_hands_the_club_over(self):
        owner, member = self.rider(1), self.rider(2)
        guild_id = self.make_club(owner, member)
        reply = self.features.on_GuildRoleUser(self.message(30, struct.pack("<IB", member, 1)), "c", owner)
        self.assertEqual(reply, b"ok")
        roles = dict(self.sql("SELECT player_id, role FROM guild_members"))
        self.assertEqual(roles, {owner: 2, member: 1})
        self.assertEqual(self.sql("SELECT owner_id FROM guilds WHERE guild_id = ?", (guild_id,)), [(member,)])

    def test_the_club_is_full_at_max_members(self):
        owner, a, b, newcomer = self.rider(1), self.rider(2), self.rider(3), self.rider(4)
        self.make_club(owner, a, b)
        invite = self.features._guild_invite(self.message(25, struct.pack("<I", newcomer)), "c", owner)
        self.assertEqual(self.guild_error(invite), GameFeatures.GUILD_ERR_FULL)

    def test_one_club_per_rider_and_unique_names(self):
        owner, other = self.rider(1), self.rider(2)
        self.make_club(owner)
        raw = self.features._igor_string("Second") + self.features._igor_string("Sec")
        again = self.features.on_GuildCreate(self.message(10, raw), "c", owner)
        self.assertEqual(self.guild_error(again), GameFeatures.GUILD_ERR_IN_GUILD)
        raw = self.features._igor_string("meadow riders") + self.features._igor_string("X")
        taken = self.features.on_GuildCreate(self.message(10, raw), "c", other)
        self.assertEqual(self.guild_error(taken), GameFeatures.GUILD_ERR_NAME_EXIST)

    def test_a_long_welcome_is_refused(self):
        owner = self.rider(1)
        self.make_club(owner)
        reply = self.features.on_GuildChangeWelcome(self.message(24, self.features._igor_string("x" * 101)), "c", owner)
        self.assertEqual(self.guild_error(reply), GameFeatures.GUILD_ERR_UNKNOWN)

    def test_when_the_owner_leaves_a_manager_takes_over(self):
        owner, member, manager = self.rider(1), self.rider(2), self.rider(3)
        guild_id = self.make_club(owner, member, manager)
        self.sql("UPDATE guild_members SET role = 2 WHERE player_id = ?", (manager,))
        self.emu.client_players["stays"] = member
        self.features.on_GuildLeave(self.message(31), "c1", owner)
        self.assertEqual(dict(self.sql("SELECT player_id, role FROM guild_members")), {member: 3, manager: 1})
        # The riders who stay get news type 4, "left".
        news = [frame for client, frame in self.emu.sent if client == "stays" and frame[1:3] == bytes([105, 19])]
        self.assertEqual(news[-1][5], 4)
        self.assertEqual(self.sql("SELECT owner_id FROM guilds WHERE guild_id = ?", (guild_id,)), [(manager,)])

    def test_the_last_rider_out_closes_the_club(self):
        owner = self.rider(1)
        self.make_club(owner)
        self.features.on_GuildLeave(self.message(31), "c1", owner)
        self.assertEqual(self.sql("SELECT COUNT(*) FROM guilds"), [(0,)])


class EquipTests(Harness):
    def test_gear_you_do_not_own_is_not_worn(self):
        player = self.rider(1)
        self.assertFalse(self.features._may_equip(player, 123456789))
        self.features.add_item(player, 123456789, 1)
        self.assertTrue(self.features._may_equip(player, 123456789))

    def test_the_starter_boots_are_always_allowed(self):
        player = self.rider(1)
        self.assertTrue(self.features._may_equip(player, 2356041962))


class SlotTests(Harness):
    def save(self, player, slot_id, extra=b""):
        body = bytes([0]) + struct.pack("<i", slot_id) + extra
        return self.features.on_SaveSlot(self.message(2, body, service=106), "c", player)

    def test_only_a_bought_slot_saves(self):
        player = self.rider(1)
        self.assertEqual(self.save(player, 1), b"ok")
        self.assertTrue(self.save(player, 7).startswith(b"fail:"))
        self.assertEqual(self.sql("SELECT slot_id FROM course_slots WHERE player_id = ?", (player,)), [(1,)])

    def test_a_huge_course_is_refused(self):
        player = self.rider(1)
        self.assertTrue(self.save(player, 1, b"x" * 100).startswith(b"fail:"))


class ShowdownClaimTests(Harness):
    def test_the_prize_pays_once_a_day_and_again_tomorrow(self):
        player = self.rider(1, "c")
        self.features._ds_board = lambda today: [{"PlayerId": player, "Place": 1}]
        self.features._showdown_prize = lambda place: {"coins": 100}
        start = int(self.features.profile(player)["coins"] or 0)
        message = self.message(5, service=110)
        self.features.on_DsGetFinalReward(message, "c", player)
        self.features.on_DsGetFinalReward(message, "c", player)
        self.assertEqual(int(self.features.profile(player)["coins"]), start + 100)
        yesterday = int((datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y%m%d"))
        self.features.save_profile(player, ds_claimed=yesterday)
        self.features.on_DsGetFinalReward(message, "c", player)
        self.assertEqual(int(self.features.profile(player)["coins"]), start + 200)


class RankedTests(Harness):
    def finish(self, client, player, seconds, faults=0):
        rest = bytes([0x30]) + struct.pack("<IfI", player, seconds, faults)
        self.features.on_RmEnd(self.message(4, rest, service=107), client, player)

    def pair(self):
        a, b = self.rider(1, "a"), self.rider(2, "b")
        self.features.waiting.append("a")
        self.features.on_SubscribeRmSearch(self.message(2, service=107), "b", b)
        return a, b

    def results(self):
        return {fields["Me"]["UserId"]: fields for name, fields in self.emu.igor.pushed
                if name == "Protocol2.ReplyRmFinished"}

    def test_two_riders_settle_once_both_finish(self):
        a, b = self.pair()
        self.finish("a", a, 30.0)
        self.assertEqual(self.results(), {})
        self.finish("b", b, 25.0)
        results = self.results()
        self.assertEqual(results[b]["MyResult"], 1)
        self.assertEqual(results[a]["MyResult"], 2)
        self.assertEqual(results[a]["Opponent"]["UserId"], b)
        self.assertEqual(results[a]["Opponent"]["Time"], 25.0)
        rating = dict(self.sql("SELECT player_id, rating FROM player_data WHERE player_id IN (?, ?)", (a, b)))
        self.assertEqual(rating[b] - rating[a], 15)

    def test_fewer_faults_beats_a_faster_time(self):
        a, b = self.pair()
        self.finish("a", a, 40.0, faults=0)
        self.finish("b", b, 20.0, faults=4)
        self.assertEqual(self.results()[a]["MyResult"], 1)

    def test_a_rider_who_drops_forfeits(self):
        a, b = self.pair()
        self.finish("a", a, 30.0)
        self.features.on_disconnect("b")
        self.assertEqual(self.results()[a]["MyResult"], 1)

    def test_the_result_names_the_saved_rival(self):
        a = self.rider(1, "a")
        self.features.opponents["a"] = 9
        self.finish("a", a, 12.0)
        self.assertEqual(self.results()[a]["Opponent"]["UserId"], 9)
        self.assertNotIn("a", self.features.opponents)


class HorseStatTests(Harness):
    def apply(self, player, stat, points):
        rest = self.features._varint(1) + bytes([stat]) + struct.pack("<I", points)
        self.features.on_ApplySkillPoints(self.message(9, rest, service=109), "c", player)

    def test_all_six_stats_are_sent_with_the_coat_buff(self):
        player = self.rider(1)
        self.features.save_profile(player, grooming=1.0)
        stats = self.features._attributes(player)
        self.assertEqual([item["StatType"] for item in stats], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(item["Value"] == 0 and item["Buff"] == 1 for item in stats))
        # A coat between clean and dirty clears the buff, and the client must be told.
        self.features.save_profile(player, grooming=0.5)
        self.assertTrue(all(item["Buff"] == 0 for item in self.features._attributes(player)))

    def test_starting_stats_count_toward_the_cap_but_are_not_saved_as_spent(self):
        player = self.rider(1)
        self.features.balance["stat_cap"] = 10
        self.features.balance["horse_stats"] = {"start": {"strength": 9, "speed": 3}}
        self.features.save_profile(player, skill_points=5)
        self.apply(player, 1, 5)
        stats = {item["StatType"]: item["Value"] for item in self.features._attributes(player)}
        self.assertEqual((stats[1], stats[3], stats[2]), (10, 3, 0))
        self.assertEqual(int(self.features.profile(player)["skill_points"]), 4)
        self.assertEqual(self.features._spent_skills(player), {1: 1})

    def test_one_number_sets_every_stat(self):
        player = self.rider(1)
        self.features.balance["horse_stats"] = {"start": 2}
        self.assertTrue(all(item["Value"] == 2 for item in self.features._attributes(player)))


class AdminTests(Harness):
    def admin(self):
        from admin import Admin
        return Admin(self.emu.database.db_path, self.emu)

    def test_a_rider_is_found_by_id_steam_id_or_name(self):
        player = self.rider(1)
        self.features.save_profile(player, name="Skye")
        admin = self.admin()
        steam = str(76561198000000001)
        self.assertEqual(admin.find(str(player))[0], player)
        self.assertEqual(admin.find(steam)[0], player)
        self.assertEqual(admin.find("skye")[0], player)
        from admin import AdminError
        with self.assertRaises(AdminError):
            admin.find("nobody")

    def test_a_ban_blocks_login_until_it_ends(self):
        from admin import ban_reason
        player = self.rider(1)
        steam = str(76561198000000001)
        admin = self.admin()
        admin.ban(str(player), reason="cheating")
        self.assertIn("cheating", ban_reason(self.emu.database.db_path, steam))
        admin.unban(str(player))
        self.assertIsNone(ban_reason(self.emu.database.db_path, steam))
        admin.ban(str(player), days=-1)
        self.assertIsNone(ban_reason(self.emu.database.db_path, steam))

    def test_a_muted_rider_is_told_and_not_relayed(self):
        player = self.rider(1, "me")
        self.emu.tcp_clients = {"me": None, "other": None}
        self.admin().mute(str(player), minutes=10, reason="spam")
        frames = [frame for client, frame in self.emu.sent]
        self.assertTrue(any(frame[1:3] == bytes([103, 14]) for client, frame in self.emu.sent if client == "me"))
        self.assertTrue(any(frame[1:3] == bytes([103, 10]) for client, frame in self.emu.sent if client == "other"))
        self.emu.sent.clear()
        rest = struct.pack("<I", 2) + self.features._igor_string("hello")
        self.features._relay_room(self.message(1, rest, service=103), "me", player)
        self.assertFalse(any(frame[1:3] == bytes([103, 7]) for _client, frame in self.emu.sent))
        self.assertTrue(len(frames) >= 2)

    def test_coins_never_go_below_zero(self):
        player = self.rider(1)
        self.features.save_profile(player, coins=100)
        self.admin().coins(str(player), -500)
        self.assertEqual(int(self.features.profile(player)["coins"]), 0)
        self.admin().coins(str(player), 250)
        self.assertEqual(int(self.features.profile(player)["coins"]), 250)

    def test_reset_starts_the_rider_over_with_new_player_values(self):
        player = self.rider(1)
        self.features.save_profile(player, coins=9999, name="Skye")
        self.features.balance["new_player"] = {"coins": 321, "rating": 1000}
        result = self.admin().reset(str(player), confirmed=True)
        self.assertIn("backups", result)
        again, data = self.emu.database.get_or_create_user("Steam", str(76561198000000001), "")
        self.assertEqual(again, player)
        self.assertTrue(data["new"])
        self.features.apply_new_player(again)
        self.assertEqual(int(self.features.profile(again)["coins"]), 321)

    def test_reset_needs_yes(self):
        from admin import AdminError
        player = self.rider(1)
        with self.assertRaises(AdminError):
            self.admin().reset(str(player))

    def test_admin_marker_frame(self):
        from admin import admin_users_frame
        frame = admin_users_frame([3, 7])
        self.assertEqual(frame[1:4], bytes([108, 19, 2]))
        self.assertEqual(struct.unpack_from("<II", frame, 4), (3, 7))


class AdminCliTests(unittest.TestCase):
    def test_offline_without_a_save_refuses_and_creates_nothing(self):
        import admin
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "users.db"
            # Port 9 is not the server, so the command falls back to the save on disk.
            self.assertEqual(admin.cli(["list"], str(db), 9), 1)
            self.assertFalse(db.exists())


class BackupTests(unittest.TestCase):
    def test_auto_backups_keep_the_newest(self):
        import sqlite3
        import time as clock
        from storage import backup_db
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "users.db"
            sqlite3.connect(str(db)).close()
            for _index in range(3):
                backup_db(db, keep=2)
                clock.sleep(1.1)
            backup_db(db, note="manual")
            files = sorted(item.name for item in (Path(folder) / "backups").iterdir())
            self.assertEqual(sum(name.endswith("-auto.db") for name in files), 2)
            self.assertEqual(sum(name.endswith("-manual.db") for name in files), 1)


class LevelUpTests(Harness):
    def test_a_level_gain_sends_the_popup_frame_and_no_gain_sends_nothing(self):
        frame = self.features._level_up_frame(2, 4)
        self.assertEqual(frame[1:3], bytes([109, 4]))
        self.assertEqual(struct.unpack_from("<i", frame, 3)[0], 4)
        self.assertEqual(self.features._level_up_frame(4, 4), b"")

    def test_xp_that_crosses_a_level_reaches_the_rider(self):
        player = self.rider(1, "me")
        self.features.save_profile(player, horse_xp=95, horse_level=1)
        self.features.balance["xp_per_level"] = 100
        data = self.features.profile(player)
        level, _xp, _points = self.features._apply_xp(data, 10)
        self.assertEqual(level, 2)
        self.assertTrue(self.features._level_up_frame(data["horse_level"], level))

    def test_each_level_gained_pays_the_bonus(self):
        self.features.balance["level_up_bonus"] = {"coins": 100, "skill_tickets": 1}
        self.assertEqual(self.features._level_bonus(1, 4), (300, 3))
        self.assertEqual(self.features._level_bonus(4, 4), (0, 0))
        self.features.balance["level_up_bonus"] = {"coins": 0, "skill_tickets": 0}
        self.assertEqual(self.features._level_bonus(1, 4), (0, 0))


class ClubChatTests(Harness):
    def room_frames(self, client):
        return [frame for target, frame in self.emu.sent if target == client and frame[1:3] == bytes([103, 1])]

    def test_a_club_member_gets_the_club_room_and_leaving_removes_it(self):
        owner = self.rider(1, "owner")
        guild_id = self.make_club(owner)
        self.features._sync_guild_room(owner)
        room = self.features.GUILD_ROOM_BASE + guild_id
        frames = self.room_frames("owner")
        self.assertEqual(len(frames), 1)
        self.assertEqual(struct.unpack_from("<IB", frames[0], 4), (room, 3))
        # A second sync does not open it twice.
        self.features._sync_guild_room(owner)
        self.assertEqual(len(self.room_frames("owner")), 1)
        self.emu.sent.clear()
        self.features.on_GuildLeave(self.message(31), "owner", owner)
        removed = [frame for target, frame in self.emu.sent if target == "owner" and frame[1:3] == bytes([103, 2])]
        self.assertEqual(struct.unpack_from("<I", removed[0], 3)[0], room)

    def test_a_club_line_reaches_only_that_club(self):
        owner, mate, outsider = self.rider(1, "owner"), self.rider(2, "mate"), self.rider(3, "outsider")
        guild_id = self.make_club(owner, mate)
        room = self.features.GUILD_ROOM_BASE + guild_id
        rest = struct.pack("<I", room) + self.features._igor_string("hello club")
        self.features._relay_room(self.message(1, rest, service=103), "owner", owner)
        got = {target for target, frame in self.emu.sent if frame[1:3] == bytes([103, 7])}
        self.assertEqual(got, {"owner", "mate"})
        self.emu.sent.clear()
        self.features._relay_room(self.message(1, rest, service=103), "outsider", outsider)
        self.assertFalse(self.emu.sent)

    def test_club_news_is_also_a_line_in_the_club_chat(self):
        owner = self.rider(1, "owner")
        guild_id = self.make_club(owner)
        self.features._sync_guild_room(owner)
        self.emu.sent.clear()
        self.features._push_guild_news(guild_id, self.features.NEWS_JOIN, owner)
        kinds = [frame[1:3] for target, frame in self.emu.sent if target == "owner"]
        self.assertIn(bytes([105, 19]), kinds)
        self.assertIn(bytes([103, 11]), kinds)
        chat = next(frame for target, frame in self.emu.sent if frame[1:3] == bytes([103, 11]))
        self.assertEqual(struct.unpack_from("<I", chat, 3)[0], self.features.GUILD_ROOM_BASE + guild_id)

    def test_creating_a_club_returns_the_room_and_the_created_news(self):
        owner = self.rider(1, "owner")
        raw = self.features._igor_string("Meadow Riders") + self.features._igor_string("MR")
        # The fake Igor returns tags, so look for the frames this handler builds itself.
        reply = self.features.on_GuildCreate(self.message(10, raw), "owner", owner)
        self.assertIn(bytes([103, 1]), reply)
        self.assertIn(bytes([103, 11]), reply)
        self.assertIn(bytes([105, 19]), reply)


class WeekResultTests(Harness):
    def ride(self, player, when, seconds):
        stamp = when.strftime("%Y-%m-%d %H:%M:%S")
        self.sql("INSERT INTO ds_results (player_id, course_hash, time, penalty, created_at) VALUES (?, 1, ?, 0, ?)",
                 (player, seconds, stamp))

    def test_last_weeks_place_is_sent_once(self):
        a, b = self.rider(1, "a"), self.rider(2, "b")
        monday = self.features._showdown_week_start(datetime.datetime.now())
        last = monday - datetime.timedelta(days=5)
        self.ride(a, last, 40.0)
        self.ride(b, last, 35.0)
        first = self.features._week_finished_frame(a)
        self.assertEqual(first[1:3], bytes([108, 14]))
        self.assertEqual(self.emu.igor.pushed[-1][1]["Place"], 2)
        self.assertEqual(self.features._week_finished_frame(a), b"")

    def test_no_ride_last_week_means_no_popup(self):
        player = self.rider(1, "a")
        self.assertEqual(self.features._week_finished_frame(player), b"")

    def test_it_can_be_turned_off(self):
        player = self.rider(1, "a")
        monday = self.features._showdown_week_start(datetime.datetime.now())
        self.ride(player, monday - datetime.timedelta(days=3), 40.0)
        self.features.balance["showdown"] = {"week_result_popup": False}
        self.assertEqual(self.features._week_finished_frame(player), b"")


class ClientConfigTests(unittest.TestCase):
    def test_the_entry_is_named_live_and_uses_the_configured_ports(self):
        import json
        from client_config import servers_text
        config = {"tcp_port": 27130, "policy_port": 27132, "http_port": 8080}
        entries = json.loads(servers_text("203.0.113.7", config))
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        # SteamRcc selects the entry whose Name the branch name contains. The public branch is "live".
        self.assertEqual(entry["Name"], "live")
        self.assertEqual((entry["Host"], entry["Port"], entry["PolicyPort"]), ("203.0.113.7", 27130, 27132))
        self.assertTrue(entry["ServerURL"].startswith("http://203.0.113.7:8080/"))


if __name__ == "__main__":
    unittest.main()


class MigrationTests(Harness):
    def test_a_new_save_ends_on_the_latest_version(self):
        import migrations
        with self.features._connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, migrations.MIGRATIONS[-1][0])

    def test_an_old_save_without_a_version_is_brought_up_to_date(self):
        import migrations
        with self.features._connect() as conn:
            conn.execute("PRAGMA user_version = 0")
            conn.execute("DROP TABLE course_likes")
        self.features._ensure_columns()
        with self.features._connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], migrations.MIGRATIONS[-1][0])
            conn.execute("SELECT 1 FROM course_likes")

    def test_versions_count_up_without_gaps(self):
        import migrations
        self.assertEqual([m[0] for m in migrations.MIGRATIONS], list(range(1, len(migrations.MIGRATIONS) + 1)))
