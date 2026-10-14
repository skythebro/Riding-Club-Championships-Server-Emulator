"""Club calls: create, join, roles, news, and the club chat room."""

import struct
import time


class ClubsMixin:
    # Protocol2.GuildNewsType values this server posts.
    NEWS_ROLE, NEWS_JOIN, NEWS_KICK, NEWS_LEAVE = 1, 2, 3, 4

    NEWS_COURSE, NEWS_RANKED, NEWS_CHALLENGE, NEWS_CREATED = 8, 9, 10, 11

    # Chat room id of a club's chat is this plus the club id. Plain rooms stay far below it.
    GUILD_ROOM_BASE = 1000000

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
        self.chat_rooms.add((client_id, self.GUILD_ROOM_BASE + guild_id))
        room = self._guild_room_frame(guild_id)
        news, chat = self._guild_news_frames(guild_id, self.NEWS_CREATED, player_id)
        push = self._push_present(105, 18, "Protocol2.GuildUpdate", {
            "GuildId": guild_id,
            "Name": name,
            "NameShort": short_name or name[:4],
            "Icon": "GUI/unknown",
            "Description": "",
            "Welcome": "",
        })
        return reply + push + room + news + chat

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
            request_rows = conn.execute(
                "SELECT player_id, kind, actor_id, created_at FROM guild_requests WHERE guild_id = ? ORDER BY created_at",
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
            # A join request has no ActorId (the rider asked). An invite carries the rider who sent it.
            # Always sent, so an answered request disappears from the owner's list.
            "Requests": [
                {"PlayerId": player, "ActorId": actor if kind == "invite" else None, "CreatedTime": int(created or 0)}
                for player, kind, actor, created in request_rows
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
            self._sync_guild_room(player_id)
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
            self._push_guild_members(guild_id)
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
            self._sync_guild_room(target)
            self._push_guild_members(guild_id)
            if self._player_guild(target) == guild_id:
                self._push_guild_news(guild_id, self.NEWS_JOIN, target)
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
            self._sync_guild_room(target)
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
                    "INSERT OR REPLACE INTO guild_requests (guild_id, player_id, kind, created_at) VALUES (?, ?, 'join', ?)",
                    (guild_id, player_id, int(time.time())),
                )
            # The rider's Join button turns into a pending request, and the club's owner sees it in Requests.
            self._push_no_guild_update(player_id)
            self._push_guild_members(guild_id)
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
                self._sync_guild_room(player_id)
                self._push_guild_members(guild_id)
                self._push_guild_news(guild_id, self.NEWS_JOIN, player_id)
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
            self._push_guild_members(guild_id)
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
            self._push_guild_members(guild_id)
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

    def _guild_news_item(self, news_type, actor_id, text="", target_id=None, course_id=None):
        """One Protocol2.GuildNews. Optional fields in wire order: actor, status, target, course, text, rank."""
        bits = 0x08  # text is always sent, empty when unused
        body = b""
        if actor_id:
            bits |= 0x80
            body += struct.pack("<I", int(actor_id))
        if target_id:
            bits |= 0x20
            body += struct.pack("<I", int(target_id))
        if course_id:
            bits |= 0x10
            body += struct.pack("<I", int(course_id))
        body += self._igor_string(text or "")
        return bytes([bits, news_type & 255]) + struct.pack("<Q", int(time.time())) + body

    def _guild_news_frames(self, guild_id, news_type, actor_id, text="", target_id=None, course_id=None):
        item = self._guild_news_item(news_type, actor_id, text, target_id, course_id)
        news = self._push_frame(105, 19, self._varint(1) + item)
        chat = self._push_frame(103, 11, struct.pack("<I", self.GUILD_ROOM_BASE + int(guild_id)) + item)
        return news, chat

    def _guild_member_ids(self, guild_id):
        with self._connect() as conn:
            return [row[0] for row in conn.execute(
                "SELECT player_id FROM guild_members WHERE guild_id = ?", (guild_id,)
            )]

    def _push_guild_news(self, guild_id, news_type, actor_id, text="", target_id=None, course_id=None):
        news, chat = self._guild_news_frames(guild_id, news_type, actor_id, text, target_id, course_id)
        for member in self._guild_member_ids(guild_id):
            for other in self._clients_for(member):
                self.emulator.send_to(other, news)
                # The chat line only has a room once the rider has the club chat open.
                if (other, self.GUILD_ROOM_BASE + int(guild_id)) in self.chat_rooms:
                    self.emulator.send_to(other, chat)

    def _guild_room_frame(self, guild_id):
        """Chat.RoomAdd for a club's chat: type Guild (3) with the club id."""
        payload = bytes([0x80]) + struct.pack("<IB", self.GUILD_ROOM_BASE + int(guild_id), 3)
        payload += struct.pack("<I", int(guild_id))
        return self._push_frame(103, 1, payload)

    def _sync_guild_room(self, player_id):
        """Give a rider's open games the chat of the club they are in now, and take away any old one."""
        guild_id = self._player_guild(player_id)
        wanted = self.GUILD_ROOM_BASE + int(guild_id) if guild_id else None
        for client_id in self._clients_for(player_id):
            stale = [key for key in self.chat_rooms
                     if key[0] == client_id and key[1] >= self.GUILD_ROOM_BASE and key[1] != wanted]
            for key in stale:
                self.chat_rooms.discard(key)
                self.emulator.send_to(client_id, self._push_frame(103, 2, struct.pack("<I", key[1])))
            if wanted and (client_id, wanted) not in self.chat_rooms:
                self.chat_rooms.add((client_id, wanted))
                self.emulator.send_to(client_id, self._guild_room_frame(guild_id))

    def _push_guild_members(self, guild_id):
        with self._connect() as conn:
            members = [row[0] for row in conn.execute(
                "SELECT player_id FROM guild_members WHERE guild_id = ?", (guild_id,)
            )]
        for member in members:
            for client_id in self._clients_for(member):
                self._push_guild_to(client_id, guild_id)
