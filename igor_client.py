"""Talks to IgorHost, which calls the game's Igor.Read / Igor.Write."""

import atexit
import json
import shutil
import socket
import subprocess
import sys
import threading
import time

from paths import APP_DIR, managed_dir

HOST_PORT = 27141
EXE = APP_DIR / "igor_host" / "IgorHost.exe"
HOST_LOG = APP_DIR / "igor_host" / "host.log"


def _host_command(managed):
    """The card host is a .NET exe. Linux runs it with Mono (apt install mono-runtime)."""
    command = [str(EXE), str(managed), str(HOST_PORT), str(APP_DIR)]
    return command if sys.platform == "win32" else ["mono", *command]


def _die_with_parent():
    """Linux: the kernel ends the card host when the server ends, however the server ends."""
    import ctypes
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, 9)  # PR_SET_PDEATHSIG, SIGKILL


def _tie_to_this_process(proc):
    """Windows: put the card host in a job that closes with this process, so it never outlives the server.

    Closing the console window or killing the server skips atexit. The job handle is kept for the
    life of this process; when the process ends, Windows closes it and ends the host.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(limits), ctypes.sizeof(limits))
    handle = kernel32.OpenProcess(0x0101, False, proc.pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
    if handle:
        kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle))
        kernel32.CloseHandle(wintypes.HANDLE(handle))
    return job


class IgorClient:
    def __init__(self, game_dir=None):
        self.game_dir = game_dir
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
            raise FileNotFoundError("The card host is missing: %s" % EXE)
        if sys.platform != "win32" and shutil.which("mono") is None:
            raise FileNotFoundError("Mono is not installed. On Debian or Ubuntu: sudo apt install mono-runtime libmono-system-web-extensions4.0-cil")
        managed = managed_dir(self.game_dir)
        if managed is None:
            raise FileNotFoundError(
                "Riding Club Championships was not found. Set game_dir in server.json to the game folder "
                "(the one with rcc.exe and rcc_Data)."
            )
        # A log file, not a pipe: nobody reads a pipe, and a full one would freeze the host.
        log = open(HOST_LOG, "ab")
        self.proc = subprocess.Popen(
            _host_command(managed),
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(EXE.parent),
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32"
               else {"preexec_fn": _die_with_parent}),
        )
        log.close()
        self._job = _tie_to_this_process(self.proc)
        atexit.register(self.stop)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.proc.poll() is not None:
                output = HOST_LOG.read_text(encoding="utf-8", errors="replace")[-2000:]
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

    def stop(self):
        """Stop the card host if this process started it."""
        self._close_socket()
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

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
