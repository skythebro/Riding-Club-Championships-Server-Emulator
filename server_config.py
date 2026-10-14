"""Who may join, and which ride results are too absurd to store."""

import json
from pathlib import Path

DEFAULTS = {
    "bind_host": "127.0.0.1",
    "tcp_port": 27130,
    "policy_port": 27132,
    "http_port": 8080,
    "public_host": "",
    "open_registration": True,
    "allowed_steam_ids": [],
    "min_ride_seconds": 8,
    "max_clients": 64,
}

STEAM_ID_MIN = 76561197960265728
STEAM_ID_MAX = 76561297960265728


def load_config(path=None):
    config = dict(DEFAULTS)
    config["allowed_steam_ids"] = []
    file = Path(path) if path else Path(__file__).resolve().parent / "server.json"
    if not file.exists():
        return config
    loaded = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return config
    for key, default in DEFAULTS.items():
        if key not in loaded:
            continue
        value = loaded[key]
        if key == "allowed_steam_ids":
            config[key] = [str(item) for item in (value or [])]
        elif isinstance(default, bool):
            config[key] = bool(value)
        elif isinstance(default, int) and not isinstance(value, bool):
            config[key] = int(value)
        elif isinstance(default, float):
            config[key] = float(value)
        else:
            config[key] = value
    return config


def login_decision(steam_id, config, account_exists):
    """Return (allowed, reason). An empty allow list means every real Steam id."""
    text = str(steam_id)
    if not text.isdigit():
        return False, "That login did not include a Steam account."
    number = int(text)
    if number < STEAM_ID_MIN or number > STEAM_ID_MAX:
        return False, "That Steam account id is not valid."
    allowed = [str(item) for item in (config.get("allowed_steam_ids") or [])]
    if allowed and text not in allowed:
        return False, "This server has not invited that Steam account."
    if not config.get("open_registration", True) and not account_exists and text not in allowed:
        return False, "This server is not taking new riders."
    return True, ""


def ride_is_plausible(time_s, penalty, min_time):
    try:
        time_s = float(time_s)
        penalty = int(penalty)
        min_time = float(min_time)
    except (TypeError, ValueError):
        return False
    if time_s != time_s or time_s < min_time or time_s > 3600:
        return False
    if penalty < 0 or penalty > 400:
        return False
    return True
