"""Chat rooms and private messages."""

import struct
import time


class ChatMixin:
    def on_SendPrivateMessage(self, message, client_id, player_id):
        return self._relay_private(message, client_id, player_id)

    def on_PrivateMessage(self, message, client_id, player_id):
        return self._relay_private(message, client_id, player_id)

    def on_SendRoomMessage(self, message, client_id, player_id):
        return self._relay_room(message, client_id, player_id)

    def on_RoomMessage(self, message, client_id, player_id):
        return self._relay_room(message, client_id, player_id)

    CHAT_MAX_CHARS = 500

    def _chat_refused(self, client_id, player_id, text):
        """True when this line must not be relayed: a muted rider, an empty line, or one too long."""
        from admin import active_mute, muted_frame
        mute = active_mute(self.emulator.database.db_path, player_id)
        if mute:
            self.emulator.send_to(client_id, muted_frame(*mute))
            self.emulator.debug_log_tcp("CHAT MUTED: %s" % client_id)
            return True
        return not text.strip() or len(text) > self.CHAT_MAX_CHARS

    def _relay_room(self, message, client_id, player_id):
        raw = bytes.fromhex(message.get("rest") or "")
        room_id = struct.unpack_from("<I", raw)[0] if len(raw) >= 4 else 1
        text = ""
        if len(raw) > 4:
            try:
                text, _offset = self._read_igor_string(raw, 4)
            except (IndexError, UnicodeDecodeError):
                text = ""
        refused = self._chat_refused(client_id, player_id, text)
        if refused:
            return None
        frame = self._room_message_frame(room_id, player_id or 1, text)
        if int(room_id) >= self.GUILD_ROOM_BASE:
            guild_id = int(room_id) - self.GUILD_ROOM_BASE
            if self._player_guild(player_id) != guild_id:
                self.emulator.debug_log_tcp("CHAT CLUB REFUSED: %s not in club %s" % (client_id, guild_id))
                return None
            for member in self._guild_member_ids(guild_id):
                for other in self._clients_for(member):
                    self.emulator.send_to(other, frame)
            self.emulator.debug_log_tcp("CHAT CLUB: %s club=%s" % (client_id, guild_id))
            return None
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
        refused = self._chat_refused(client_id, player_id, text)
        if refused:
            return None
        frame = self._private_message_frame(player_id or 1, target, text)
        self.emulator.send_to(client_id, frame)
        for other, other_player in list(self.emulator.client_players.items()):
            if other_player == target and other != client_id:
                self.emulator.send_to(other, frame)
        self.emulator.debug_log_tcp("CHAT PRIVATE: %s to=%s" % (client_id, target))
        return None

    def _announce_room(self, client_id, room_id):
        key = (client_id, room_id)
        if key in self.chat_rooms or int(room_id) >= self.GUILD_ROOM_BASE:
            return
        self.chat_rooms.add(key)
        if int(room_id) == 2:
            self.emulator.send_to(client_id, self._global_room_frame())
            return
        payload = bytes([0x60]) + struct.pack("<IB", int(room_id), 1)
        payload += self._igor_string("local")
        payload += self._igor_string("Welcome")
        self.emulator.send_to(client_id, self._push_frame(103, 1, payload))

    def _room_message_frame(self, room_id, player_id, text):
        self.chat_seq += 1
        payload = struct.pack("<III", int(room_id), self.chat_seq, int(player_id))
        payload += self._igor_string(text)
        payload += struct.pack("<Q", int(time.time() * 1000))  # client reads ms since 1970
        return self._push_frame(103, 7, payload)

    def _private_message_frame(self, sender, target, text):
        payload = struct.pack("<II", int(sender), int(target))
        payload += self._igor_string(text)
        payload += struct.pack("<Q", int(time.time() * 1000))  # client reads ms since 1970
        return self._push_frame(103, 4, payload)
