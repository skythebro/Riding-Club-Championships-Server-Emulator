"""Ranked matches and friend challenges."""

from server_config import ride_is_plausible

import struct
import threading
import time


class RankedMixin:
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

    def _rival_allowed(self):
        """The saved Rival fills a ranked search by server.json ranked_rival: alone, always, or never."""
        mode = str((getattr(self.emulator, "join_config", None) or {}).get("ranked_rival", "always")).lower()
        if mode == "never":
            return False
        if mode == "alone":
            online = set((getattr(self.emulator, "client_players", {}) or {}).values())
            return len(online) <= 1
        return True

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
        club = self._player_guild(player_id) if paid and player_id and not mine["canceled"] else None
        if club:
            self._push_guild_news(club, self.NEWS_RANKED, player_id)
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
                        self._ap_cost(player_id, "ranked"),
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
            while True:
                with self.lock:
                    if client_id not in self.waiting:
                        return
                    if self._rival_allowed():
                        self.waiting.remove(client_id)
                        self.opponents[client_id] = self._rival_id()
                        break
                time.sleep(1)
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
        # The client calls this again to play: the sender right after sending, the receiver on accepting.
        # Only the first call opens the challenge and tells both riders.
        fresh = True
        if player_id and target != player_id:
            key = self._challenge_key(player_id, target)
            if key in self.challenges:
                fresh = False
            else:
                self.challenges[key] = {
                    "key": key,
                    "starter": int(player_id),
                    "course": 3023709559,
                    "results": {},
                }
        if fresh:
            self._push_challenge(client_id, target, 1)
            for other, other_player in list(self.emulator.client_players.items()):
                if other_player == target and other != client_id:
                    self._push_challenge(other, player_id or 1, 2)
        self.emulator.debug_log_tcp("CHALLENGE: %s target=%s fresh=%s" % (client_id, target, fresh))
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
        return payload + self._challenge_result_bytes(mine) + self._challenge_result_bytes(opp)

    def _challenge_notify_payload(self, friend, status, course, mine, opp, place):
        bits = (0x40 if mine else 0) | (0x20 if opp else 0)
        payload = bytes([2, bits]) + struct.pack("<IBI", int(friend), int(status), int(course))
        payload += self._challenge_result_bytes(mine) + self._challenge_result_bytes(opp)
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

    def push_open_challenges(self, client_id, player_id):
        """Show a rider who just logged in the challenges still waiting on them or their friend."""
        for challenge in list(self.challenges.values()):
            if player_id not in challenge["key"]:
                continue
            other = challenge["key"][0] if challenge["key"][1] == player_id else challenge["key"][1]
            results = challenge["results"]
            status = 1 if challenge["starter"] == player_id else 2
            update = self._challenge_update_payload(
                other, status, int(challenge["course"]), results.get(player_id), results.get(other)
            )
            self.emulator.send_to(client_id, self._push_frame(109, 13, update))

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
        club = self._player_guild(player_id) if not parsed["canceled"] else None
        if club:
            self._push_guild_news(club, self.NEWS_CHALLENGE, player_id)
        self.emulator.debug_log_tcp(
            "CHALLENGE RESULT: %s friend=%s time=%.2f penalty=%s canceled=%s"
            % (client_id, friend, parsed["time"], parsed["penalty"], parsed["canceled"])
        )
        return None

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

    def _rm_start(self, player_id, opponent_id):
        return self.emulator.igor.push_object(107, 5, "Protocol2.ReplyRmStart", {
            "CourseKey": 3023709559,
            "Opponent": {"Id": opponent_id or self._rival_id(), "Place": 1, "Level": 1},
        })
