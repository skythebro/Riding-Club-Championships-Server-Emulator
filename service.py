"""Keep the server running without a window: starts at boot, restarts if it stops.

    RCCServer.exe service install     start at boot and restart if it stops
    RCCServer.exe service remove
    RCCServer.exe service status

Windows: a scheduled task, so run it from a command prompt opened as administrator.
Linux: a systemd unit, so run it as root (otherwise it writes the file and tells you what to run).
macOS: a LaunchAgent for your user. It starts when you log in and restarts the server if it stops.

The task runs as SYSTEM, so install writes the game folder into server.json first. SYSTEM does not
see your Steam settings. Output goes to server.log next to the server and is replaced on each start.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from paths import APP_DIR, find_game_dir

TASK_NAME = "RCC Server"
UNIT_NAME = "rcc-server.service"
UNIT_DIR = Path("/etc/systemd/system")
AGENT_LABEL = "com.rcc.server"
# launchd starts with a bare PATH. Homebrew puts mono in one of these.
MAC_PATH = "/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Mono.framework/Versions/Current/Commands:/usr/bin:/bin:/usr/sbin:/sbin"


def server_command():
    """The command that starts this server, as a list."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(APP_DIR / "Server.py")]


def remember_game_dir():
    """Write game_dir into server.json when it is not there, so a service with no Steam settings finds it."""
    config_file = APP_DIR / "server.json"
    config = json.loads(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
    if config.get("game_dir"):
        return config["game_dir"]
    game = find_game_dir(None)
    if game is None:
        raise SystemExit("The game folder was not found. Set game_dir in server.json, then run this again.")
    config["game_dir"] = str(game)
    config_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print("Saved game_dir %s in server.json" % game)
    return str(game)


def windows_task_xml():
    command = " ".join('"%s"' % part for part in server_command())
    log = str(APP_DIR / "server.log")
    # cmd /c keeps the output so a problem can be read later. The outer quotes are cmd's.
    arguments = '/c "%s > "%s" 2>&1"' % (command, log)
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Riding Club Championships server</Description></RegistrationInfo>
  <Triggers><BootTrigger><Enabled>true</Enabled></BootTrigger></Triggers>
  <Principals>
    <Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>%s</Arguments>
      <WorkingDirectory>%s</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
""" % (escape(arguments), escape(str(APP_DIR)))


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def windows(action):
    if action == "status":
        result = _schtasks("/Query", "/TN", TASK_NAME, "/FO", "LIST")
        print(result.stdout.strip() if result.returncode == 0 else "Not installed.")
        return 0
    if action == "remove":
        _schtasks("/End", "/TN", TASK_NAME)
        result = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
        print("Removed." if result.returncode == 0 else result.stderr.strip() or "Not installed.")
        return 0 if result.returncode == 0 else 1
    remember_game_dir()
    handle, name = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(handle, "w", encoding="utf-16") as out:
            out.write(windows_task_xml())
        result = _schtasks("/Create", "/TN", TASK_NAME, "/XML", name, "/F")
    finally:
        os.unlink(name)
    if result.returncode != 0:
        print((result.stderr or result.stdout).strip())
        print("Run this from a command prompt opened as administrator.")
        return 1
    _schtasks("/Run", "/TN", TASK_NAME)
    print("Installed and started. The server now starts at boot and restarts if it stops.")
    print("Output: %s" % (APP_DIR / "server.log"))
    return 0


def systemd_unit():
    command = " ".join('"%s"' % part for part in server_command())
    return """[Unit]
Description=Riding Club Championships server
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=%s
ExecStart=%s
Restart=always
RestartSec=5
# Stops the card host with the server, even when it ignores the stop signal.
KillMode=control-group

[Install]
WantedBy=multi-user.target
""" % (APP_DIR, command)


def launch_agent():
    """The launchd plist for macOS. Output goes to server.log, like the other systems."""
    program = "".join("        <string>%s</string>\n" % escape(part) for part in server_command())
    log = escape(str(APP_DIR / "server.log"))
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>%s</string>
    <key>ProgramArguments</key>
    <array>
%s    </array>
    <key>WorkingDirectory</key>
    <string>%s</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>%s</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>%s</string>
    <key>StandardErrorPath</key>
    <string>%s</string>
</dict>
</plist>
""" % (AGENT_LABEL, program, escape(str(APP_DIR)), MAC_PATH, log, log)


def mac(action):
    agent = Path.home() / "Library" / "LaunchAgents" / (AGENT_LABEL + ".plist")
    domain = "gui/%d" % os.getuid()
    if action == "status":
        return subprocess.call(["launchctl", "print", "%s/%s" % (domain, AGENT_LABEL)])
    if action == "remove":
        subprocess.call(["launchctl", "bootout", domain, str(agent)])
        agent.unlink(missing_ok=True)
        print("Removed.")
        return 0
    remember_game_dir()
    agent.parent.mkdir(parents=True, exist_ok=True)
    subprocess.call(["launchctl", "bootout", domain, str(agent)], stderr=subprocess.DEVNULL)
    agent.write_text(launch_agent(), encoding="utf-8")
    code = subprocess.call(["launchctl", "bootstrap", domain, str(agent)])
    print("Installed %s. Output: %s" % (agent, APP_DIR / "server.log") if code == 0 else "launchctl failed.")
    return code


def linux(action):
    unit = UNIT_DIR / UNIT_NAME
    if action == "status":
        return subprocess.call(["systemctl", "status", UNIT_NAME, "--no-pager"])
    if action == "remove":
        subprocess.call(["systemctl", "disable", "--now", UNIT_NAME])
        unit.unlink(missing_ok=True)
        subprocess.call(["systemctl", "daemon-reload"])
        print("Removed.")
        return 0
    remember_game_dir()
    try:
        unit.write_text(systemd_unit(), encoding="utf-8")
    except PermissionError:
        local = APP_DIR / UNIT_NAME
        local.write_text(systemd_unit(), encoding="utf-8")
        print("Not root. Wrote %s. As root, run:" % local)
        print("  cp %s %s && systemctl daemon-reload && systemctl enable --now %s" % (local, unit, UNIT_NAME))
        return 1
    subprocess.call(["systemctl", "daemon-reload"])
    code = subprocess.call(["systemctl", "enable", "--now", UNIT_NAME])
    print("Installed. Logs: journalctl -u %s -f" % UNIT_NAME if code == 0 else "systemctl failed.")
    return code


def cli(argv):
    if not argv or argv[0] not in ("install", "remove", "status"):
        print(__doc__)
        return 2
    if sys.platform == "win32":
        return windows(argv[0])
    if sys.platform == "darwin":
        return mac(argv[0])
    return linux(argv[0])
