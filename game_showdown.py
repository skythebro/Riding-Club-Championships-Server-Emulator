"""The daily showdown: boards, entry, and prizes."""

from server_config import ride_is_plausible

import datetime
import struct
import zlib


class ShowdownMixin:
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
                level_frame = self._level_up_frame(data["horse_level"], level)
                bonus_coins, bonus_tickets = self._level_bonus(data["horse_level"], level)
                self.save_profile(
                    player_id,
                    coins=data["coins"] + coins + bonus_coins,
                    skill_tickets=data["skill_tickets"] + bonus_tickets,
                    horse_level=level,
                    horse_xp=xp_left,
                    skill_points=points,
                    ds_claimed=today_key,
                )
                if item_id:
                    item_hash = zlib.crc32(str(item_id).encode("utf-8")) & 0xffffffff
                    self.add_item(player_id, item_hash, 1)
                self.emulator.send_to(client_id, self.session_push(player_id) + level_frame)
        self.emulator.debug_log_tcp(
            "DS REWARD: %s place=%s coins=%s xp=%s item=%s" % (client_id, place, coins, xp, item_id)
        )
        return None

    def _week_finished_frame(self, player_id):
        """Game.DsWeekFinished: where the rider finished last week, shown once at the next login.

        This server has no weekly final, so the popup carries the place and no reward. The
        daily prizes were already paid when each day's board was claimed.
        """
        if not self.bal("showdown", "week_result_popup", default=True):
            return b""
        this_week = self._showdown_week_start(datetime.datetime.now())
        last_week = this_week - datetime.timedelta(days=7)
        key = int(last_week.strftime("%Y%m%d"))
        with self._connect() as conn:
            row = conn.execute("SELECT ds_week_seen FROM player_data WHERE player_id = ?", (player_id,)).fetchone()
            if row is None or int(row[0] or 0) >= key:
                return b""
            conn.execute("UPDATE player_data SET ds_week_seen = ? WHERE player_id = ?", (key, player_id))
        place = next(
            (int(entry["Place"]) for entry in self._ds_board_span(last_week, this_week, None)
             if entry["PlayerId"] == player_id),
            0,
        )
        if not place:
            return b""
        self.emulator.debug_log_tcp("DS WEEK FINISHED: player=%s place=%s" % (player_id, place))
        return self.emulator.igor.push_object(108, 14, "Protocol2.ReplyDsFinished", {
            "Place": place,
            "Reward": {"Money": {"Coins": 0, "SkillTickets": 0}, "Xp": 0, "Ap": 0, "Items": []},
        })

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
