"""Build the server download: dist/RCCServer-<version>.zip.

    python build_release.py [version]

The zip holds RCCServer.exe (no Python needed), the card host, the editable
config and data, and two shortcuts. It holds no game files: the card host
loads them from the owner's own install at run time.

It also builds RCCServer-linux-<version>.tar.gz: the Python source and the same
card host, to run with Python 3 and Mono.
"""

import io
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP = DIST / "RCCServer"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")

START_BAT = r"""@echo off
cd /d "%~dp0"
RCCServer.exe %*
pause
"""

JOIN_BAT = r"""@echo off
cd /d "%~dp0"
set /p HOST=Address players use to reach this server (blank = this PC only):
RCCServer.exe join %HOST%
pause
"""

LOGS_BAT = r"""@echo off
cd /d "%~dp0"
RCCServer.exe logs
pause
"""

QUICKSTART = """Riding Club Championships server

1. Start Server.bat. The first start creates server.json and finds the game
   through Steam. If it cannot, set game_dir in server.json to the folder
   that holds rcc.exe.
2. Playing on this PC: run Make servers.txt.bat and leave the address blank.
3. Letting others join: in server.json set "bind_host": "0.0.0.0" and
   "public_host" to your address, open TCP port 27130 on your router and
   firewall, then run Make servers.txt.bat and send players servers.txt.
   They put it next to rcc.exe. Steam's "Verify integrity of game files"
   undoes it.

Always on: open a command prompt as administrator here and run
RCCServer.exe service install. It starts at boot and restarts if it stops.

Admin: open a command prompt here and run  RCCServer.exe admin --help
(list, ban, mute, coins, rename, reset, kick, backup).
Prices, rewards, and the news panel: balance.json.
Saves: users.db. Automatic copies go to backups/.
Full guide: README.md.
"""


LINUX_START = """#!/bin/sh
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q -r requirements.txt
exec .venv/bin/python Server.py "$@"
"""

LINUX_QUICKSTART = """Riding Club Championships server on Linux and macOS

You need Python 3.10 or newer and Mono, and the Managed folder of your own copy of the game.

1. Install:  sudo apt install python3 python3-venv mono-runtime libmono-system-web-extensions4.0-cil
   On a Mac:  brew install python mono
2. On a Mac with the game installed through Steam, skip this step: the server finds it.
   Otherwise copy the game's rcc_Data/Managed folder from a Windows install to a folder
   such as ~/rcc-game/rcc_Data/Managed. Then in server.json set "game_dir" to
   that folder (~/rcc-game). Until server.json exists, ./start.sh --game-dir ~/rcc-game.
3. Run ./start.sh. The first start creates server.json.
4. Letting others join: in server.json set "bind_host": "0.0.0.0" and
   "public_host" to your address. Open TCP port 27130 on the firewall.
   Then run ./start.sh join YOUR.ADDRESS and send players the servers.txt it writes.
   They put it next to rcc.exe, or on a Mac inside the game app beside Contents.
5. Always on: sudo ./start.sh service install   (a systemd unit; logs: journalctl -u rcc-server -f)
   On a Mac: ./start.sh service install          (a LaunchAgent; output in server.log)

Admin: ./start.sh admin --help
Prices, rewards, and the news panel: balance.json.
Saves: users.db. Automatic copies go to backups/.
Full guide: README.md.
"""


def run(*args, **kwargs):
    print(">", " ".join(str(item) for item in args))
    subprocess.run([str(item) for item in args], check=True, **kwargs)


def build_card_host():
    if not CSC.exists():
        sys.exit("csc.exe (.NET Framework 4) was not found at %s" % CSC)
    run(CSC, "/nologo", "/target:exe", "/main:Host", "/r:System.Web.Extensions.dll",
        "/out:IgorHost.exe", "Host.cs", cwd=ROOT / "igor_host")


def build_exe():
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "RCCServer", "--distpath", DIST, "--workpath", ROOT / "build",
        "--specpath", ROOT / "build",
        "--collect-submodules", "uvicorn", "--collect-submodules", "websockets",
        "Server.py", cwd=ROOT)


def copy_files(version):
    for name in ("balance.json", "server.json.example", "README.md", "STATUS.md", "LICENSE",
                 "Rewards for all the daily logins.txt"):
        shutil.copy2(ROOT / name, APP / name)
    shutil.copytree(ROOT / "gamedata", APP / "gamedata", dirs_exist_ok=True)
    host = APP / "igor_host"
    host.mkdir(exist_ok=True)
    for name in ("IgorHost.exe", "protocol_map.json"):
        shutil.copy2(ROOT / "igor_host" / name, host / name)
    (APP / "Start Server.bat").write_text(START_BAT.replace("\n", "\r\n"), encoding="ascii")
    (APP / "Make servers.txt.bat").write_text(JOIN_BAT.replace("\n", "\r\n"), encoding="ascii")
    (APP / "Collect logs.bat").write_text(LOGS_BAT.replace("\n", "\r\n"), encoding="ascii")
    (APP / "version.txt").write_text(version, encoding="ascii")
    (APP / "QUICKSTART.txt").write_text(QUICKSTART.replace("\n", "\r\n"), encoding="utf-8")


def make_linux(version):
    """A tar.gz of the source files the server runs, with the same prebuilt card host."""
    skip = ("test_", "build_", "analyze_")
    target = DIST / ("RCCServer-linux-%s.tar.gz" % version)
    with tarfile.open(target, "w:gz") as archive:
        def add_bytes(name, data, mode):
            info = tarfile.TarInfo("RCCServer/" + name)
            info.size, info.mode = len(data), mode
            archive.addfile(info, io.BytesIO(data))

        for file in sorted(ROOT.glob("*.py")):
            if not file.name.startswith(skip):
                archive.add(file, "RCCServer/" + file.name)
        for name in ("requirements.txt", "balance.json", "server.json.example", "README.md", "STATUS.md", "LICENSE"):
            archive.add(ROOT / name, "RCCServer/" + name)
        archive.add(ROOT / "gamedata", "RCCServer/gamedata")
        for name in ("IgorHost.exe", "protocol_map.json"):
            archive.add(ROOT / "igor_host" / name, "RCCServer/igor_host/" + name)
        add_bytes("version.txt", version.encode(), 0o644)
        add_bytes("start.sh", LINUX_START.encode(), 0o755)
        add_bytes("QUICKSTART.txt", LINUX_QUICKSTART.encode(), 0o644)
    print("Built", target, "%.1f MB" % (target.stat().st_size / 1e6))
    return target


def make_zip(version):
    target = DIST / ("RCCServer-%s.zip" % version)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(APP.rglob("*")):
            if file.is_file():
                archive.write(file, Path("RCCServer") / file.relative_to(APP))
    print("Built", target, "%.1f MB" % (target.stat().st_size / 1e6))
    return target


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if APP.exists():
        shutil.rmtree(APP)
    build_card_host()
    build_exe()
    copy_files(version)
    make_zip(version)
    make_linux(version)


if __name__ == "__main__":
    main()
