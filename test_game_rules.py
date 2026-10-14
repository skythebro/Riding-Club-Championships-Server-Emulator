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
