import datetime
import sqlite3
import struct
import unittest

from game_features import GameFeatures
from server_config import login_decision, ride_is_plausible


class Levels:
    balance = {"xp_per_level": 100, "max_level": 3, "skill_points_per_level": 1}
    bal = GameFeatures.bal


class LoginTests(unittest.TestCase):
    def test_local_defaults_accept_a_real_steam_id(self):
        allowed, reason = login_decision("76561198139908495", {"open_registration": True, "allowed_steam_ids": []}, False)
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_allow_list_blocks_everyone_else(self):
        config = {"open_registration": True, "allowed_steam_ids": ["76561198000000001"]}
        allowed, reason = login_decision("76561198139908495", config, True)
        self.assertFalse(allowed)
        self.assertIn("invited", reason)

    def test_closed_registration_keeps_saved_accounts(self):
        config = {"open_registration": False, "allowed_steam_ids": []}
        allowed, _reason = login_decision("76561198139908495", config, True)
        self.assertTrue(allowed)
        blocked, reason = login_decision("76561198000000001", config, False)
        self.assertFalse(blocked)
        self.assertIn("new riders", reason)

    def test_garbage_id_is_refused(self):
        allowed, reason = login_decision("steam_error_1337", {"open_registration": True, "allowed_steam_ids": []}, False)
        self.assertFalse(allowed)
        self.assertIn("Steam", reason)


class LevelTests(unittest.TestCase):
    def test_one_hundred_xp_is_a_level_and_a_skill_point(self):
        level, xp, points = GameFeatures._apply_xp(
            Levels(), {"horse_level": 1, "horse_xp": 90, "skill_points": 0}, 30
        )
        self.assertEqual((level, xp, points), (2, 20, 1))

    def test_level_stops_at_the_cap(self):
        level, xp, points = GameFeatures._apply_xp(
            Levels(), {"horse_level": 3, "horse_xp": 90, "skill_points": 4}, 50
        )
        self.assertEqual(level, 3)
        self.assertEqual(points, 4)
        self.assertLessEqual(xp, 100)


class PracticeRecordTests(unittest.TestCase):
    def test_practice_end_keeps_time_and_faults(self):
        body = bytes([0x70]) + struct.pack("<IfI", 7, 12.5, 4)
        parsed = GameFeatures._practice_result(None, body.hex())
        self.assertEqual(parsed["course_id"], 7)
        self.assertAlmostEqual(parsed["time"], 12.5)
        self.assertEqual(parsed["penalty"], 4)
        self.assertFalse(parsed["canceled"])

    def test_fewer_faults_beats_a_faster_time(self):
        self.assertTrue(GameFeatures._ride_beats(None, 0, 20.0, 1, 12.0))
        self.assertTrue(GameFeatures._ride_beats(None, 0, 10.0, 0, 12.0))
        self.assertFalse(GameFeatures._ride_beats(None, 0, 12.0, 0, 12.0))
        self.assertFalse(GameFeatures._ride_beats(None, 4, 9.0, 0, 12.0))


class ShowdownCalendarTests(unittest.TestCase):
    def test_saturday_is_day_six_and_sunday_is_day_seven(self):
        saturday = datetime.datetime(2026, 10, 3, 23, 0, 0)
        sunday = datetime.datetime(2026, 10, 4, 12, 0, 0)
        self.assertEqual(GameFeatures._showdown_day(None, saturday), 6)
        self.assertEqual(GameFeatures._showdown_day(None, sunday), 7)

    def test_the_timer_reaches_local_midnight(self):
        moment = datetime.datetime(2026, 10, 3, 23, 59, 50)
        self.assertEqual(GameFeatures._seconds_until_midnight(None, moment), 10)

    def test_sunday_does_not_charge_tickets(self):
        class Tickets:
            bal = staticmethod(lambda *keys, **kwargs: 150)
            _showdown_day = GameFeatures._showdown_day

        sunday = datetime.datetime(2026, 10, 4, 12, 0, 0)
        monday = datetime.datetime(2026, 10, 5, 12, 0, 0)
        self.assertEqual(GameFeatures._showdown_ticket_cost(Tickets(), sunday), 0)
        self.assertEqual(GameFeatures._showdown_ticket_cost(Tickets(), monday), 150)


class ShowdownBoardTests(unittest.TestCase):
    def test_the_table_request_uses_dayofweek(self):
        sunday = datetime.datetime(2026, 10, 4, 12, 0, 0)
        monday = datetime.datetime(2026, 10, 5, 12, 0, 0)
        self.assertEqual(GameFeatures._csharp_weekday(None, sunday), 0)
        self.assertEqual(GameFeatures._csharp_weekday(None, monday), 1)
        self.assertEqual(GameFeatures._showdown_week_start(None, sunday), datetime.datetime(2026, 9, 28))
        self.assertEqual(GameFeatures._showdown_week_start(None, monday), datetime.datetime(2026, 10, 5))

    def test_monday_does_not_show_sunday_and_page_two_keeps_place(self):
        box = type("Box", (), {})()
        box.profile = lambda player_id: {"horse_level": 4}
        box._ds_table = lambda rows: GameFeatures._ds_table(box, rows)
        rows = [(index, 20.0 + index, 0) for index in range(1, 12)]
        rows.append((20, 9.0, 0))
        table = GameFeatures._ds_table(box, rows)
        visible, mine = GameFeatures._page_board(box, table, 20, 1)
        self.assertEqual([row["Place"] for row in visible], [11, 12])
        self.assertEqual(mine["PlayerId"], 20)
        self.assertEqual(mine["Place"], 1)
        self.assertEqual(GameFeatures._signed_ints(None, struct.pack("<ii", 0, 1).hex(), 2), [0, 1])


    def test_a_sunday_row_stays_off_saturday(self):
        box = type("Box", (), {})()
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """CREATE TABLE ds_results (
                player_id INTEGER, course_hash INTEGER, time REAL, penalty INTEGER, created_at TEXT
            )"""
        )
        box._connect = lambda: connection
        box.profile = lambda player_id: {"horse_level": 1}
        box._ds_table = lambda rows: GameFeatures._ds_table(box, rows)

        def stamp(local):
            return (local - local.astimezone().utcoffset()).strftime("%Y-%m-%d %H:%M:%S")

        saturday = datetime.datetime(2026, 10, 3, 15, 0, 0)
        sunday = datetime.datetime(2026, 10, 4, 15, 0, 0)
        connection.execute(
            "INSERT INTO ds_results (player_id, course_hash, time, penalty, created_at) VALUES (1, 1, 30, 0, ?)",
            (stamp(sunday),),
        )
        connection.execute(
            "INSERT INTO ds_results (player_id, course_hash, time, penalty, created_at) VALUES (2, 1, 28, 0, ?)",
            (stamp(saturday),),
        )
        start = datetime.datetime(2026, 9, 28)
        end = datetime.datetime(2026, 10, 5)
        sunday_board = GameFeatures._ds_board_span(box, start, end, 0)
        saturday_board = GameFeatures._ds_board_span(box, start, end, 6)
        self.assertEqual([row["PlayerId"] for row in sunday_board], [1])
        self.assertEqual([row["PlayerId"] for row in saturday_board], [2])
        connection.close()

    def test_qualifying_page_two_skips_the_first_ten(self):
        box = type("Box", (), {})()
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """CREATE TABLE ds_results (
                player_id INTEGER, course_hash INTEGER, time REAL, penalty INTEGER, created_at TEXT
            )"""
        )
        for rider in range(1, 13):
            connection.execute(
                "INSERT INTO ds_results (player_id, course_hash, time, penalty) VALUES (?, 1, ?, 0)",
                (rider, float(rider)),
            )
        box._connect = lambda: connection
        box._signed_ints = lambda rest, count: GameFeatures._signed_ints(box, rest, count)
        box._page_board = lambda table, player_id, page: GameFeatures._page_board(box, table, player_id, page)
        captured = {}

        class Igor:
            def reply_type(self, service, function, rpc, type_name, fields):
                captured["type"] = type_name
                captured["fields"] = fields
                return b"ok"

        box.emulator = type("Emu", (), {"igor": Igor()})()
        message = {"service": 107, "function": 15, "rpc": 1, "rest": struct.pack("<i", 1).hex()}
        GameFeatures.on_DsQualificationTable(box, message, "c", 1)
        self.assertEqual(captured["type"], "Protocol2.DsQualification")
        self.assertEqual([row["Place"] for row in captured["fields"]["Table"]], [11, 12])
        self.assertEqual(captured["fields"]["MyResult"]["PlayerId"], 1)
        self.assertEqual(captured["fields"]["MyResult"]["Place"], 1)
        connection.close()



class GuildTopTests(unittest.TestCase):
    def test_page_two_skips_the_first_count(self):
        rows = [1, 2, 3, 4, 5]
        self.assertEqual(GameFeatures._page_rows(None, rows, 1, 2), [3, 4])
        self.assertEqual(GameFeatures._page_rows(None, rows, 0, 100), rows)
        self.assertEqual(GameFeatures._page_rows(None, rows, 0, 0), rows)


class IgnoreListTests(unittest.TestCase):
    def test_ignore_present_is_a_count_then_ids(self):
        payload = GameFeatures._ignore_payload(None, [7])
        self.assertEqual(payload, bytes([1, 7, 0, 0, 0]))


class FriendsLadderTests(unittest.TestCase):
    def test_friends_keep_their_global_place(self):
        payload = GameFeatures._friends_ladder_payload(None, [(9,), (1,), (2,)], {1})
        self.assertEqual(payload, bytes([1]) + struct.pack("<II", 1, 2))

    def test_the_global_ladder_stops_at_the_asked_count(self):
        box = type("Box", (), {})()
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE player_data (player_id INTEGER, rating INTEGER)")
        for rider, rating in ((1, 900), (2, 1200), (3, 1100)):
            connection.execute(
                "INSERT INTO player_data (player_id, rating) VALUES (?, ?)", (rider, rating)
            )
        box._connect = lambda: connection
        box._varint = lambda value: GameFeatures._varint(box, value)
        payload = GameFeatures._ladder_payload(box, 2)
        self.assertEqual(payload, bytes([2]) + struct.pack("<II", 2, 1) + struct.pack("<II", 3, 2))
        full = GameFeatures._ladder_payload(box, 0)
        self.assertEqual(full[0], 3)
        connection.close()

    def test_a_named_rider_keeps_the_global_place(self):
        body = bytes([2]) + struct.pack("<II", 9, 2)
        self.assertEqual(GameFeatures._uint_list(None, body.hex()), [9, 2])
        payload = GameFeatures._friends_ladder_payload(None, [(9,), (1,), (2,)], {2})
        self.assertEqual(payload, bytes([1]) + struct.pack("<II", 2, 3))


class CourseSearchTests(unittest.TestCase):
    def test_course_search_matches_part_of_the_name(self):
        box = type("Box", (), {})()
        box.courses = {3: {"name": "Winter Cup"}, 1: {"name": "Meadow"}, 2: {"name": "Meadow 2"}}
        self.assertEqual(GameFeatures._courses_named(box, "mead"), [1, 2])
        self.assertEqual(GameFeatures._courses_named(box, "  "), [])


class MyCoursesTests(unittest.TestCase):
    def test_my_courses_is_only_that_riders(self):
        box = type("Box", (), {})()
        box.courses = {1: {"owner": 1}, 2: {"owner": 9}}
        box._course_ids = lambda owner=None: GameFeatures._course_ids(box, owner)
        self.assertEqual(GameFeatures._course_list_ids(box, 8, 1), [1])
        self.assertEqual(GameFeatures._course_list_ids(box, 1, 1), [1, 2])
        self.assertEqual(GameFeatures._course_list_ids(box, 8, 0), [])

    def test_friends_courses_skip_your_own(self):
        box = type("Box", (), {})()
        box.courses = {1: {"owner": 1}, 2: {"owner": 9}, 4: {"owner": 3}}
        box._steam_friend_ids = lambda player_id: [9]
        self.assertEqual(GameFeatures._course_list_ids(box, 3, 1), [2])
        box._steam_friend_ids = lambda player_id: []
        self.assertEqual(GameFeatures._course_list_ids(box, 3, 1), [])

    def test_newest_lists_the_highest_id_first(self):
        box = type("Box", (), {})()
        box.courses = {1: {}, 4: {}, 2: {}}
        self.assertEqual(GameFeatures._course_list_ids(box, 4, 1), [4, 2, 1])


class ChallengeResultTests(unittest.TestCase):
    def test_fewer_faults_wins_a_slower_time(self):
        mine = {"time": 20.0, "penalty": 0, "canceled": False}
        opp = {"time": 12.0, "penalty": 4, "canceled": False}
        self.assertEqual(GameFeatures._challenge_place(None, mine, opp), 1)
        tied = {"time": 12.0, "penalty": 0, "canceled": False}
        self.assertEqual(GameFeatures._challenge_place(None, tied, dict(tied)), 3)

    def test_challenge_result_reads_friend_time_and_faults(self):
        body = struct.pack("<I", 7) + bytes([0]) + struct.pack("<fI", 12.5, 2)
        parsed = GameFeatures._parse_challenge_result(None, body.hex())
        self.assertEqual(parsed["friend"], 7)
        self.assertAlmostEqual(parsed["time"], 12.5)
        self.assertEqual(parsed["penalty"], 2)
        self.assertFalse(parsed["canceled"])

    def test_both_results_clear_the_pair(self):
        sent = []

        class Emu:
            client_players = {"a": 1, "b": 2}

            def send_to(self, client_id, frame):
                sent.append((client_id, frame))

        class Box:
            emulator = Emu()
            challenges = {}

        for name in (
            "_varint",
            "_push_frame",
            "_challenge_key_ids",
            "_challenge_update_payload",
            "_challenge_notify_payload",
            "_challenge_place",
            "_challenge_result_bytes",
        ):
            setattr(Box, name, getattr(GameFeatures, name))
        challenge = {
            "key": (1, 2),
            "starter": 1,
            "course": 3023709559,
            "results": {
                1: {"time": 20.0, "penalty": 0, "canceled": False},
                2: {"time": 12.0, "penalty": 4, "canceled": False},
            },
        }
        Box.challenges[(1, 2)] = challenge
        GameFeatures._push_challenge_state(Box(), challenge)
        self.assertNotIn((1, 2), Box.challenges)
        by_rider = {client: frame for client, frame in sent}
        self.assertIn(bytes([6, 109, 14]) + struct.pack("<I", 2), by_rider["a"])
        self.assertIn(bytes([6, 109, 14]) + struct.pack("<I", 1), by_rider["b"])


class FriendBoardTests(unittest.TestCase):
    def test_friends_board_keeps_you_and_drops_everyone_else(self):
        scores = [{"UserId": 1}, {"UserId": 9}, {"UserId": 3}]
        kept = GameFeatures._friend_board(None, scores, 1, [9])
        self.assertEqual([score["UserId"] for score in kept], [1, 9])


class SlotNameTests(unittest.TestCase):
    def test_a_saved_slot_keeps_its_name(self):
        class Slot:
            _varint = GameFeatures._varint
            _igor_string = lambda self, text: GameFeatures._igor_string(self, text)
            _read_varint = GameFeatures._read_varint
            _read_igor_string = GameFeatures._read_igor_string

        body = GameFeatures._empty_slot(Slot(), 2, "Meadow draft")
        self.assertEqual(GameFeatures._slot_name(Slot(), body.hex()), "Meadow draft")


class CourseFactsTests(unittest.TestCase):
    def test_list_row_copies_the_saved_map(self):
        class Box:
            _read_igor_string = GameFeatures._read_igor_string
            _read_varint = GameFeatures._read_varint
            _skip_map_items = GameFeatures._skip_map_items
            _skip_show_jump = GameFeatures._skip_show_jump
            _slot_show_jump = GameFeatures._slot_show_jump

        game_map = struct.pack("<I", 99) + bytes(1 + 28 + 1 + 1) + struct.pack("<ii", 45, 130)
        slot = bytes([0x80]) + struct.pack("<iB?", 2, 1, False) + bytes([4]) + b"Park" + game_map
        facts = GameFeatures._saved_course_facts(Box(), slot.hex())
        self.assertEqual(facts, (1, 45, 99, 130))


class CourseLikeTests(unittest.TestCase):
    def test_like_replies_with_that_courses_fans(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE course_likes (
                player_id INTEGER,
                course_id INTEGER,
                PRIMARY KEY (player_id, course_id)
            )"""
        )

        class Emu:
            def debug_log_tcp(self, text):
                pass

        class Box:
            emulator = Emu()

            def _connect(self):
                return conn

        for name in (
            "_uint_from_rest",
            "_course_fan_count",
            "_uint_reply",
            "_frame_uint",
            "_varint",
        ):
            setattr(Box, name, getattr(GameFeatures, name))
        message = {"service": 106, "function": 12, "rpc": 1, "rest": struct.pack("<I", 9).hex()}
        GameFeatures.on_Like(Box(), message, "a", 1)
        second = GameFeatures.on_Like(Box(), message, "b", 2)
        self.assertEqual(struct.unpack_from("<I", second, len(second) - 4)[0], 2)
        message["function"] = 13
        after = GameFeatures.on_UnLike(Box(), message, "a", 1)
        self.assertEqual(struct.unpack_from("<I", after, len(after) - 4)[0], 1)


class TutorialStateTests(unittest.TestCase):
    def test_finishing_the_tutorial_opens_avatar_creation(self):
        self.assertEqual(GameFeatures._next_tutorial_state(None, 2, True), 1)
        self.assertEqual(GameFeatures._next_tutorial_state(None, 1, False), 2)

    def test_a_menu_account_is_not_pulled_backwards(self):
        self.assertEqual(GameFeatures._next_tutorial_state(None, 3, True), 3)
        self.assertEqual(GameFeatures._next_tutorial_state(None, 3, False), 3)


class GuildInviteTests(unittest.TestCase):
    def test_an_invite_names_the_inviter(self):
        raw = GameFeatures._guild_request_bytes(None, 4, 9, 100)
        self.assertEqual(raw[0], 0x80)
        self.assertEqual(struct.unpack_from("<IIQ", raw, 1), (4, 9, 100))

    def test_a_join_request_has_no_inviter(self):
        raw = GameFeatures._guild_request_bytes(None, 4, None, 100)
        self.assertEqual(raw[0], 0x00)
        self.assertEqual(struct.unpack_from("<IQ", raw, 1), (4, 100))

    def test_the_push_lists_every_pending_request(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE guild_requests (
                guild_id INTEGER,
                player_id INTEGER,
                kind TEXT,
                actor_id INTEGER,
                created_at INTEGER
            )"""
        )
        conn.execute("INSERT INTO guild_requests VALUES (4, 2, 'invite', 1, 100)")
        conn.execute("INSERT INTO guild_requests VALUES (8, 2, 'join', NULL, 50)")

        class Box:
            def _connect(self):
                return conn

        for name in ("_varint", "_push_frame", "_guild_request_bytes"):
            setattr(Box, name, getattr(GameFeatures, name))
        frame = GameFeatures._no_guild_update(Box(), 2)
        body = frame[1:]
        self.assertEqual(body[:3], bytes([105, 13, 2]))
        self.assertEqual(struct.unpack_from("<IIQ", body, 4), (4, 1, 100))
        self.assertEqual(body[20], 0x00)
        self.assertEqual(struct.unpack_from("<IQ", body, 21), (8, 50))

    def test_declining_removes_the_invite(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE guild_requests (
                guild_id INTEGER,
                player_id INTEGER,
                kind TEXT,
                actor_id INTEGER,
                created_at INTEGER
            )"""
        )
        conn.execute("INSERT INTO guild_requests VALUES (4, 2, 'invite', 1, 100)")
        conn.execute("INSERT INTO guild_requests VALUES (8, 2, 'join', NULL, 50)")
        sent = []

        class Emu:
            igor = None

            def debug_log_tcp(self, text):
                pass

            def send_to(self, client_id, frame):
                sent.append(frame)

            class Igor:
                @staticmethod
                def reply_ok(service, function, rpc):
                    return b"ok"

            igor = Igor()

        class Box:
            emulator = Emu()

            def _connect(self):
                return conn

            def _clients_for(self, player_id):
                return ["invitee"]

        for name in ("_uint_from_rest", "_varint", "_push_frame", "_guild_request_bytes", "_no_guild_update", "_push_no_guild_update"):
            setattr(Box, name, getattr(GameFeatures, name))
        message = {"service": 105, "function": 16, "rpc": 3, "rest": struct.pack("<I", 4).hex()}
        GameFeatures.on_NoGuildCancelRequest(Box(), message, "invitee", 2)
        left = conn.execute("SELECT guild_id, kind FROM guild_requests ORDER BY guild_id").fetchall()
        self.assertEqual(left, [(8, "join")])
        self.assertEqual(sent[0][1:4], bytes([105, 13, 1]))





class ProfileFriendTests(unittest.TestCase):
    def test_a_profile_counts_friends_on_this_server(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE users (player_id INTEGER, user_state INTEGER)")
        conn.execute("INSERT INTO users VALUES (1, 3)")
        seen = {}

        class Igor:
            @staticmethod
            def reply_type(service, function, rpc, type_name, fields):
                seen.update(fields)
                return b"ok"

        class Emu:
            igor = Igor()
            client_players = {}

        class Box:
            emulator = Emu()

            def _connect(self):
                return conn

            def _player_exists(self, player_id):
                return True

            def profile(self, player_id):
                return {
                    "player_gender": 0,
                    "player_skin": 0,
                    "horse_name": "Mist",
                    "horse_gender": 0,
                    "horse_level": 1,
                    "horse_skin": 0,
                    "tail_skin": 0,
                }

            def _rank_place(self, player_id):
                return 1

            def _rider_fields(self, player_id):
                return []

            def _equipment_fields(self, player_id):
                return []

            def _course_ids(self, player_id):
                return []

            def _steam_friend_ids(self, player_id):
                return [2, 3]

        Box._uint_from_rest = GameFeatures._uint_from_rest
        message = {"service": 108, "function": 11, "rpc": 1, "rest": struct.pack("<I", 1).hex()}
        GameFeatures.on_GetPlayerProfile(Box(), message, "viewer", 9)
        self.assertEqual(seen["FriendsCount"], 2)


class OnlineCountTests(unittest.TestCase):
    def test_two_windows_for_one_rider_count_once(self):
        class Emu:
            client_players = {"a": 1, "b": 1, "c": 2}

        class Box:
            emulator = Emu()

        for name in ("_varint", "_push_frame"):
            setattr(Box, name, getattr(GameFeatures, name))
        frame = GameFeatures._online_count_frame(Box())
        self.assertEqual(frame[1:3], bytes([108, 17]))
        self.assertEqual(struct.unpack_from("<i", frame, len(frame) - 4)[0], 2)

    def test_subscribe_tells_the_other_riders(self):
        sent = []

        class Emu:
            client_players = {"a": 1, "b": 2}
            tcp_clients = {"a": object(), "b": object()}

            def send_to(self, client_id, frame):
                sent.append(client_id)

            def debug_log_tcp(self, text):
                pass

        class Box:
            emulator = Emu()

        for name in ("_varint", "_push_frame", "_online_count_frame"):
            setattr(Box, name, getattr(GameFeatures, name))
        frame = GameFeatures.on_Subscribe(Box(), {"service": 108, "function": 0}, "a", 1)
        self.assertEqual(sent, ["b"])
        self.assertEqual(struct.unpack_from("<i", frame, len(frame) - 4)[0], 2)


class BuySlotTests(unittest.TestCase):
    def test_an_agility_slot_stays_agility(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE course_slots (player_id INTEGER, slot_id INTEGER, body TEXT)"
        )

        class Emu:
            def debug_log_tcp(self, text):
                pass

            class Igor:
                @staticmethod
                def reply_type(service, function, rpc, type_name, fields):
                    return fields

            igor = Igor()

        class Box:
            emulator = Emu()
            balance = {}
            slots = {}

            def _connect(self):
                return conn

        for name in ("bal", "_empty_slot", "_igor_string", "_varint"):
            setattr(Box, name, getattr(GameFeatures, name))
        message = {"service": 106, "function": 0, "rpc": 1, "rest": bytes([2]).hex()}
        fields = GameFeatures.on_BuySlot(Box(), message, "rider", 1)
        self.assertEqual(fields["Type"], 2)
        stored = bytes.fromhex(conn.execute("SELECT body FROM course_slots").fetchone()[0])
        self.assertEqual(stored[5], 2)

    def test_an_unknown_slot_type_stays_show_jump(self):
        class Box:
            pass

        for name in ("_empty_slot", "_igor_string", "_varint"):
            setattr(Box, name, getattr(GameFeatures, name))
        body = GameFeatures._empty_slot(Box(), 1, "Slot 1", 9)
        self.assertEqual(body[5], 1)


class PlayerInfoTests(unittest.TestCase):
    def test_the_horse_name_is_not_an_avatar_address(self):
        class Box:
            def profile(self, player_id):
                return {"name": "Skye", "horse_name": "Mist", "player_gender": 1}

        for name in ("_uint_from_rest", "_status_payload", "_igor_string", "_varint"):
            setattr(Box, name, getattr(GameFeatures, name))
        message = {"service": 105, "function": 3, "rpc": 1, "rest": struct.pack("<I", 1).hex()}
        frame = GameFeatures.on_GetPlayerInfo(Box(), message, "viewer", 9)
        payload = frame[1:][5:]
        self.assertEqual(payload[:5], bytes([4]) + b"Skye")
        self.assertEqual(payload[5:], bytes([0, 1, 1]))
        self.assertNotIn(b"Mist", payload)


class AgilityCourseTests(unittest.TestCase):
    def test_opening_an_agility_course_keeps_that_map(self):
        game_map = struct.pack("<I", 99) + bytes([0]) + bytes(28) + bytes([1]) + bytes(12)
        slot = bytes([0x40]) + struct.pack("<iB?", 2, 2, False) + bytes([4]) + b"Park" + game_map

        class Emu:
            def debug_log_tcp(self, text):
                pass

        class Box:
            emulator = Emu()
            courses = {4: {"name": "Park", "owner": 9, "body": slot.hex(), "played": 1, "completed": 0}}

        for name in (
            "_uint_from_rest",
            "_slot_agility",
            "_read_igor_string",
            "_skip_show_jump",
            "_skip_agility",
            "_skip_map_items",
            "_read_varint",
            "_course_with_map",
            "_igor_string",
            "_varint",
            "_status_payload",
        ):
            setattr(Box, name, getattr(GameFeatures, name))
        message = {"service": 106, "function": 4, "rpc": 1, "rest": struct.pack("<I", 4).hex()}
        frame = GameFeatures.on_GetCourse(Box(), message, "rider", None)
        payload = frame[1:][5:]
        self.assertEqual(payload[0], 0x40)
        self.assertEqual(struct.unpack_from("<I", payload, 1)[0], 4)
        self.assertEqual(payload[5], 2)
        self.assertIn(game_map, payload)


class GlobalChatTests(unittest.TestCase):
    def test_the_global_room_has_no_welcome_line(self):
        class Box:
            pass

        for name in ("_varint", "_push_frame"):
            setattr(Box, name, getattr(GameFeatures, name))
        body = GameFeatures._global_room_frame(Box())[1:]
        self.assertEqual(body, bytes([103, 1, 0]) + struct.pack("<IB", 2, 1))

    def test_the_room_is_opened_once_per_login(self):
        class Emu:
            client_players = {"a": 1}
            database = type("Db", (), {"db_path": ":memory:"})()

        class Box:
            global_chat = set()
            chat_rooms = set()
            emulator = Emu()

        for name in ("_varint", "_push_frame", "_global_room_frame"):
            setattr(Box, name, getattr(GameFeatures, name))
        first = GameFeatures._with_global_room(Box(), 1, b"session")
        second = GameFeatures._with_global_room(Box(), 1, b"session")
        self.assertTrue(first.endswith(bytes([103, 1, 0]) + struct.pack("<IB", 2, 1)))
        self.assertEqual(second, b"session")
        self.assertIn(("a", 2), Box.chat_rooms)


class RideTests(unittest.TestCase):
    def test_a_normal_round_is_kept(self):
        self.assertTrue(ride_is_plausible(48.2, 4, 8))

    def test_instant_time_is_dropped(self):
        self.assertFalse(ride_is_plausible(0.01, 0, 8))

    def test_absurd_faults_are_dropped(self):
        self.assertFalse(ride_is_plausible(40, 9999, 8))


if __name__ == "__main__":
    unittest.main()
