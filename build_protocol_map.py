"""Build protocol_map.json from the dnSpy dump of the client services.

A call has an RPC when the client method calls _CreateRpc. Those need a reply.
Void calls must stay silent. emptyReply is true only when Recv's success path
reads no further payload, so a status-0 frame cannot desync the socket.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "exports" / "decompiled" / "Assembly-CSharp" / "Protocol2"
OUT = Path(__file__).resolve().parent / "igor_host" / "protocol_map.json"

SERVICES = {
    "ServiceLogin": 100,
    "ServiceCards": 101,
    "ServiceDebug": 102,
    "ServiceChat": 103,
    "ServicePaddock": 104,
    "ServiceSocial": 105,
    "ServiceCourseEditor": 106,
    "ServiceMatch": 107,
    "ServiceGame": 108,
    "ServicePlayer": 109,
}

# A success reply would claim something this process cannot do.
PLACEHOLDER_REASONS = {
    "SteamMicroTxnInit": "Steam checkout is not emulated. A success reply would ask Steam to charge a real wallet.",
    "SteamMicroTxn": "Steam checkout is not emulated. A success reply would ask Steam to charge a real wallet.",
    "KickstarterInfo": "The Kickstarter campaign is closed. There is no live pledge state to return.",
    "Exec": "Debug eval is not run on this server.",
    "Eval": "Debug eval is not run on this server.",
}


def method_body(text, signature_end):
    depth = 1
    i = signature_end
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    return text[signature_end:i - 1]


def reason_for(name):
    return PLACEHOLDER_REASONS.get(name, "")


def payload_of(success):
    listed = re.search(r"Read\.List<([^>]+)>", success)
    if listed:
        return "list", listed.group(1).split(".")[-1]
    if "Read.Dict<" in success:
        return "dict", ""
    optional = re.search(r"Read\.Optional(?:Value)?<([^>]+)>", success)
    if optional:
        inner = optional.group(1).split(".")[-1]
        if inner == "uint":
            return "optional_uint", ""
        return "optional", inner
    readable = re.search(r"Read\.Readable<([^>]+)>", success)
    if readable:
        return "readable", readable.group(1).split(".")[-1]
    for kind, pattern in (
        ("string", r"Read\.String\("),
        ("int", r"Read\.Int\("),
        ("uint", r"Read\.UInt\("),
        ("bool", r"Read\.Bool\("),
        ("float", r"Read\.Float\("),
    ):
        if re.search(pattern, success):
            return kind, ""
    return "none", ""


def parse_service(name, service_id):
    text = (ROOT / (name + ".cs")).read_text(encoding="utf-8")
    calls = {}
    for match in re.finditer(r"public [\w<>,\.\[\]\? ]+ (\w+)\([^\)]*\)\s*\{", text):
        method = match.group(1)
        if method in ("Recv",) or method.startswith("get_") or method.startswith("set_"):
            continue
        body = method_body(text, match.end())
        if "BeginSend()" not in body:
            continue
        after = body.split("BeginSend()", 1)[1]
        function = re.search(r"Write\((\d+)\)", after)
        if not function:
            continue
        calls[method] = {
            "service": service_id,
            "function": int(function.group(1)),
            "hasRpc": "_CreateRpc" in body,
            "emptyReply": False,
            "placeholder": reason_for(method),
            "payloadKind": "none",
            "payloadType": "",
        }
    for match in re.finditer(r"private void Recv_(\w+)\(BinaryReader reader\)\s*\{", text):
        method = match.group(1)
        if method not in calls:
            continue
        body = method_body(text, match.end())
        success = body.split("else")[-1] if "else" in body else body
        kind, payload_type = payload_of(success)
        calls[method]["payloadKind"] = kind
        calls[method]["payloadType"] = payload_type
        calls[method]["emptyReply"] = calls[method]["hasRpc"] and kind == "none"
    return calls


def main():
    catalog = {}
    for name, service_id in SERVICES.items():
        for method, info in parse_service(name, service_id).items():
            key = str((info["service"] << 8) | info["function"])
            info["name"] = method
            catalog[key] = info
    OUT.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")
    print("wrote", len(catalog), "calls to", OUT)


if __name__ == "__main__":
    main()
