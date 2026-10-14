"""Talks to IgorHost, which calls the game's Igor.Read / Igor.Write."""

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

HOST_PORT = 27141
ROOT = Path(__file__).resolve().parent
EXE = ROOT / "igor_host" / "IgorHost.exe"
MANAGED = ROOT.parent / "rcc_Data" / "Managed"


class IgorClient:
    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.sock = None
        self.reader = None
        self.next_id = 1
        self._ensure()

    def _ensure(self):
        if self._try_ping():
            return
        self._start_process()
        self._connect()
        pong = self._request({"op": "ping"})
        if not pong.get("pong"):
            raise RuntimeError("igor host did not answer ping")

    def _try_ping(self):
        try:
            self._connect()
            return bool(self._request({"op": "ping"}).get("pong"))
        except Exception:
            self._close_socket()
            return False

    def _start_process(self):
        if not EXE.exists():
            raise FileNotFoundError(EXE)
        self.proc = subprocess.Popen(
            [str(EXE), str(MANAGED), str(HOST_PORT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(EXE.parent),
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            if self.proc.poll() is not None:
                output = self.proc.stdout.read().decode("utf-8", "replace")
                raise RuntimeError("igor host exited: " + output)
            try:
                self._connect()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("igor host did not open port %s" % HOST_PORT)

    def _connect(self):
        if self.sock is not None:
            return
        sock = socket.create_connection(("127.0.0.1", HOST_PORT), timeout=3)
        sock.settimeout(10)
        self.sock = sock
        self.reader = sock.makefile("r", encoding="utf-8", newline="\n")

    def _close_socket(self):
        try:
            if self.reader is not None:
                self.reader.close()
        except Exception:
            pass
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.reader = None

    def _request(self, payload, retry=True):
        with self.lock:
            try:
                return self._request_once(payload)
            except (OSError, RuntimeError):
                self._close_socket()
                if not retry:
                    raise
                return self._request_once(payload)

    def _request_once(self, payload):
        self._connect()
        body = dict(payload)
        body["id"] = self.next_id
        self.next_id += 1
        self.sock.sendall((json.dumps(body) + "\n").encode("utf-8"))
        line = self.reader.readline()
        if not line:
            raise RuntimeError("igor host closed the connection")
        response = json.loads(line)
        if response.get("id") != body["id"]:
            raise RuntimeError("igor host response id mismatch: %s" % line)
        if not response.get("ok"):
            raise RuntimeError(response.get("error") or "igor host error")
        return response

    def feed(self, client_id, data: bytes):
        # No retry: the host may have buffered these bytes before the error, and a
        # second feed would run the same call twice (a purchase, a reward).
        response = self._request({"op": "feed", "client": client_id, "hex": data.hex()}, retry=False)
        return response.get("messages") or []

    def drop(self, client_id):
        self._request({"op": "drop", "client": client_id})

    def reply_login(self, rpc, player_id, user_state, access):
        return bytes.fromhex(self._request({
            "op": "reply",
            "kind": "login",
            "rpc": rpc,
            "playerId": player_id,
            "userState": user_state,
            "access": access,
        })["hex"])

    def reply_special_offers(self, rpc):
        return bytes.fromhex(self._request({
            "op": "reply",
            "kind": "special_offers",
            "rpc": rpc,
        })["hex"])

    def push_daily_bonus(self, day=1, need_accept=False, time_left=86400):
        return bytes.fromhex(self._request({
            "op": "push",
            "kind": "daily_bonus",
            "day": day,
            "needAccept": need_accept,
            "timeLeft": time_left,
        })["hex"])

    def reply_ok(self, service, function, rpc):
        return bytes.fromhex(self._request({
            "op": "reply", "kind": "rpc_ok", "service": service, "function": function, "rpc": rpc,
        })["hex"])

    def reply_fail(self, service, function, rpc, text):
        return bytes.fromhex(self._request({
            "op": "reply", "kind": "rpc_fail", "service": service, "function": function, "rpc": rpc, "text": text,
        })["hex"])

    def reply_type(self, service, function, rpc, type_name, fields=None):
        return bytes.fromhex(self._request({
            "op": "reply", "kind": "rpc_type", "service": service, "function": function, "rpc": rpc,
            "type": type_name, "fields": fields or {},
        })["hex"])

    def reply_uints(self, service, function, rpc, ids):
        return bytes.fromhex(self._request({
            "op": "reply", "kind": "rpc_uints", "service": service, "function": function, "rpc": rpc, "ids": ids,
        })["hex"])

    def reply_blobs(self, service, function, rpc, items):
        return bytes.fromhex(self._request({
            "op": "reply", "kind": "rpc_list", "service": service, "function": function, "rpc": rpc, "items": items,
        })["hex"])

    def push_session(self, fields, inventory=None):
        body = {"op": "push", "kind": "session", "fields": fields}
        if inventory is not None:
            body["inventory"] = inventory
        return bytes.fromhex(self._request(body)["hex"])

    def push_cards(self):
        return bytes.fromhex(self._request({"op": "push", "kind": "cards"})["hex"])

    def push_object(self, service, function, type_name, fields=None):
        return bytes.fromhex(self._request({
            "op": "push", "kind": "object", "service": service, "function": function,
            "type": type_name, "fields": fields or {},
        })["hex"])

    def reply_likes(self, rpc, ids=None):
        body = {"op": "reply", "kind": "likes", "rpc": rpc}
        if ids is not None:
            body["ids"] = ids
        return bytes.fromhex(self._request(body)["hex"])
