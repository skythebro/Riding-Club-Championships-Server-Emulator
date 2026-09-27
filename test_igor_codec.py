"""Decode the captured login burst with the real Igor types."""

import socket
import struct
import time
import unittest
from pathlib import Path

from igor_client import IgorClient

ROOT = Path(__file__).resolve().parent


def login_request(rpc, token):
    token_bytes = token.encode("utf-8")
    return bytes([100, 0]) + struct.pack("<H", rpc) + struct.pack("<i", 34) + bytes([5]) + struct.pack("<Q", 76561198139908495) + bytes([len(token_bytes)]) + token_bytes


class IgorCodecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.igor = IgorClient()

    def test_update_account_info_and_split_frames(self):
        raw = bytes.fromhex("0c690904536b7965000202656e")
        first = self.igor.feed("split", raw[:2])
        self.assertEqual(first, [])
        messages = self.igor.feed("split", raw[2:])
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message["name"], "UpdateAccountInfo")
        self.assertEqual(message["playerName"], "Skye")
        self.assertIsNone(message["avatar"])
        self.assertEqual(message["gender"], 2)
        self.assertEqual(message["locale"], "en")
        self.igor.drop("split")

    def test_subscribe_friends_count(self):
        notes = (ROOT / "services stuff.txt").read_text(encoding="utf-8")
        hex_line = [line.strip() for line in notes.splitlines() if line.startswith("Hex:")][1]
        raw = bytes.fromhex(hex_line.split("Hex:", 1)[1].strip())
        messages = self.igor.feed("friends", raw)
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message["name"], "SubscribeFriends")
        self.assertEqual(len(message["friendIds"]), 76)
        self.assertTrue(all(item.isdigit() for item in message["friendIds"]))
        self.igor.drop("friends")

    def test_batched_game_and_editor_calls(self):
        raw = bytes.fromhex("026c00046c090100046a0b0200")
        messages = self.igor.feed("batch", raw)
        self.assertEqual([item["name"] for item in messages], ["Subscribe", "GetSpecialOffers", "GetLikes"])
        self.assertEqual(messages[1]["rpc"], 1)
        self.assertEqual(messages[2]["rpc"], 2)
        offers = self.igor.reply_special_offers(messages[1]["rpc"])
        likes = self.igor.reply_likes(messages[2]["rpc"])
        self.assertEqual(offers.hex(), "066c0901000000")
        self.assertEqual(likes.hex(), "066a0b02000000")
        self.igor.drop("batch")

    def test_login_roundtrip_uses_reply_login_write(self):
        raw = login_request(7, "ticket")
        # VarInt length is not in login_request; the client frames it. Wrap it.
        framed = bytes([len(raw)]) + raw
        messages = self.igor.feed("login", framed)
        self.assertEqual(messages[0]["name"], "Login")
        self.assertEqual(messages[0]["rpc"], 7)
        self.assertEqual(messages[0]["version"], 34)
        self.assertEqual(messages[0]["source"], 5)
        self.assertEqual(messages[0]["sourceId"], "76561198139908495")
        self.assertEqual(messages[0]["token"], "ticket")
        reply = self.igor.reply_login(7, 1, 1, 0)
        self.assertEqual(reply.hex(), "0b6400070000010000000100")
        seen = self.igor._request({
            "op": "roundtrip",
            "type": "Protocol2.PlayerStats",
            "fields": {"ActionPoints": 100, "Money": {"Coins": 500, "SkillTickets": 5}},
        })
        self.assertEqual(seen["values"]["Coins"], "500")
        self.assertEqual(seen["values"]["ActionPoints"], "100")
        horse = self.igor._request({
            "op": "roundtrip",
            "type": "Protocol2.HorseStats",
            "fields": {"Name": "Mist"},
        })
        self.assertEqual(horse["values"]["Name"], "Mist")
        self.igor.drop("login")


class LoginBurstServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from Server import RCCServerEmulator
        cls.emu = RCCServerEmulator()

    def test_burst_replies_only_to_rpcs(self):
        client = "burst-test"
        login = bytes([24]) + login_request(3, "ticket")
        social = bytes.fromhex("0c690904536b7965000202656e")
        batch = bytes.fromhex("026c00046c090100046a0b0200")
        replies = bytearray()
        for chunk in (login, social, batch):
            for message in self.emu.igor.feed(client, chunk):
                reply = self.emu.handle_igor_message(message, client)
                if reply:
                    replies.extend(reply)
        self.emu.igor.drop(client)
        self.assertTrue(replies.hex().startswith(
            "0b6400030000" + "010000000100" + "066c0901000000" + "066a0b02000000" + "106d000001000000000000000000c0a847"
        ))
        self.assertIn("6d02", replies.hex())
        stored = self.emu.database.get_player_data(self.emu.client_players[client])
        self.assertEqual(stored["name"], "Skye")
        self.assertEqual(stored["locale"], "en")

    def test_live_socket_stays_open_after_likes(self):
        notes = (ROOT / "services stuff.txt").read_text(encoding="utf-8")
        friends = bytes.fromhex([line.strip() for line in notes.splitlines() if line.startswith("Hex:")][1].split("Hex:", 1)[1].strip())
        burst = b"".join([
            bytes([24]) + login_request(4, "ticket"),
            bytes.fromhex("0c690904536b7965000202656e"),
            friends,
            bytes.fromhex("026c00046c090100046a0b0200"),
        ])
        self.emu.start_tcp_server()
        time.sleep(0.3)
        try:
            sock = socket.create_connection(("127.0.0.1", 27130), timeout=3)
            sock.settimeout(2)
            sock.sendall(burst)
            received = b""
            try:
                while True:
                    chunk = sock.recv(8192)
                    if not chunk:
                        self.fail("server closed the socket")
                    received += chunk
            except socket.timeout:
                pass
            self.assertIn(bytes.fromhex("066c0901000000"), received)
            self.assertIn(bytes.fromhex("066a0b02000000"), received)
            sock.settimeout(0.5)
            try:
                extra = sock.recv(1)
            except socket.timeout:
                extra = None
            self.assertNotEqual(extra, b"")
            sock.getpeername()
            sock.close()
        finally:
            self.emu.stop_tcp_server()

    def _frame(self, body):
        return bytes([len(body)]) + body

    def test_paddock_practice_course_ranked_and_steam(self):
        paddock = self._frame(bytes([104, 0]) + struct.pack("<H", 1))
        messages = self.emu.igor.feed("pad", paddock)
        self.assertEqual(messages[0]["name"], "StartPaddock")
        self.assertEqual(self.emu.handle_igor_message(messages[0], "pad").hex(), "056800010000")

        practice = self._frame(bytes([107, 0]) + struct.pack("<H", 3))
        messages = self.emu.igor.feed("prac", practice)
        self.assertEqual(messages[0]["name"], "StartPractice")
        self.assertTrue(self.emu.handle_igor_message(messages[0], "prac"))

        avatar = self._frame(bytes.fromhex("6d14") + bytes.fromhex(self.emu.igor._request({
            "op": "roundtrip", "type": "Protocol2.RequestApplyAvatar", "fields": {"HorseName": "Mist"},
        })["hex"]))
        messages = self.emu.igor.feed("ava", avatar)
        self.assertEqual(messages[0]["name"], "ApplyAvatar")
        self.assertEqual(messages[0]["horseName"], "Mist")

        save = self._frame(bytes([106, 2]) + struct.pack("<H", 4) + b"slot-body")
        messages = self.emu.igor.feed("slot", save)
        self.assertEqual(messages[0]["name"], "SaveSlot")
        self.emu.client_players["slot"] = 1
        self.assertTrue(self.emu.handle_igor_message(messages[0], "slot"))
        submit = self._frame(bytes([106, 3]) + struct.pack("<H", 5) + struct.pack("<i", 1))
        messages = self.emu.igor.feed("slot", submit)
        self.assertEqual(messages[0]["name"], "Submit")
        self.assertTrue(self.emu.handle_igor_message(messages[0], "slot"))
        get_course = self._frame(bytes([106, 4]) + struct.pack("<H", 6))
        messages = self.emu.igor.feed("slot", get_course)
        self.assertEqual(messages[0]["name"], "GetCourse")
        course = self.emu.handle_igor_message(messages[0], "slot")
        self.assertTrue(course)
        self.assertNotIn(bytes([255]), course[3:6])

        left = self._frame(bytes([107, 2]))
        right = self._frame(bytes([107, 2]))
        first = self.emu.igor.feed("rank-a", left)[0]
        second = self.emu.igor.feed("rank-b", right)[0]
        self.assertEqual(first["name"], "SubscribeRmSearch")
        self.assertEqual(second["name"], "SubscribeRmSearch")
        # Queue the first rider, then pair when the second subscribes.
        self.emu.features.waiting.append("rank-a")
        paired = self.emu.handle_igor_message(second, "rank-b")
        self.assertTrue(paired)
        self.assertTrue(self.emu.outbox.get("rank-a"))

        steam = self._frame(bytes([105, 10]) + struct.pack("<H", 9))
        messages = self.emu.igor.feed("steam", steam)
        self.assertEqual(messages[0]["name"], "SteamMicroTxnInit")
        failed = self.emu.handle_igor_message(messages[0], "steam")
        self.assertIn(b"Steam checkout is not emulated", failed)
        self.emu.igor.drop("pad")
        self.emu.igor.drop("prac")
        self.emu.igor.drop("ava")
        self.emu.igor.drop("slot")
        self.emu.igor.drop("rank-a")
        self.emu.igor.drop("rank-b")
        self.emu.igor.drop("steam")


if __name__ == "__main__":
    unittest.main()
