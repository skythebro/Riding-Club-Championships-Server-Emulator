"""Course slots, listings, and likes."""

import json
import struct


class CoursesMixin:
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
            name, _offset = self._read_igor_string(raw, 7)
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
        club = self._player_guild(owner)
        if club:
            self._push_guild_news(club, self.NEWS_COURSE, owner, course_id=course_id)
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

    # A flat, clear strip on the paddock terrain, found in its height map. Ground is about -0.95 here.
    # The editor turns a jump half way round (y 180 degrees) when it faces +z, as the horse does.
    MEADOW_START = (-65.0, 50.0)
    MEADOW_JUMPS = (
        (2670118389, 70.0, -0.951),
        (2932793192, 90.0, -0.952),
        (1218636073, 110.0, -0.888),
    )

    def _meadow_map(self, min_time):
        x = self.MEADOW_START[0]
        obstacles = []
        for key, z, ground in self.MEADOW_JUMPS:
            obstacles.append({
                "Key": key,
                "Position": {
                    "Position": {"x": x, "y": ground, "z": z},
                    "Rotation": {"x": 0, "y": 1, "z": 0, "w": 0},
                },
            })
        return {
            "Terrain": 3684040698,
            "Horse": {
                "Position": {"x": x, "y": -0.6, "z": self.MEADOW_START[1]},
                "Rotation": {"x": 0, "y": 0, "z": 0, "w": 1},
            },
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
