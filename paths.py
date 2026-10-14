"""Where the server's files are, and where the game is.

Run from source, files sit next to these scripts. Run as RCCServer.exe, they sit
next to the exe, so an owner can edit balance.json and server.json in place.
"""

import os
import re
import sys
from pathlib import Path

GAME_FOLDER = "RidingClubChampionships"


def _app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()


def _is_game_dir(path):
    return path is not None and (Path(path) / "rcc_Data" / "Managed" / "Assembly-CSharp.dll").exists()


def _steam_roots():
    roots = []
    try:
        import winreg

        for hive, key in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        ):
            try:
                with winreg.OpenKey(hive, key) as handle:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            roots.append(Path(winreg.QueryValueEx(handle, name)[0]))
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]) / "Steam")
    roots.append(Path.home() / ".steam" / "steam")
    roots.append(Path.home() / ".local" / "share" / "Steam")
    return roots


def _steam_libraries():
    libraries = []
    for root in _steam_roots():
        vdf = root / "steamapps" / "libraryfolders.vdf"
        libraries.append(root)
        if vdf.exists():
            text = vdf.read_text(encoding="utf-8", errors="replace")
            for found in re.findall(r'"path"\s+"([^"]+)"', text):
                libraries.append(Path(found.replace("\\\\", "\\")))
    return libraries


def find_game_dir(configured=None):
    """The game folder that holds rcc_Data/Managed, or None.

    Order: server.json game_dir, the folder above this server, then every Steam library.
    """
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(APP_DIR.parent)
    candidates.extend(library / "steamapps" / "common" / GAME_FOLDER for library in _steam_libraries())
    for candidate in candidates:
        if _is_game_dir(candidate):
            return candidate.resolve()
    return None


def managed_dir(configured=None):
    game = find_game_dir(configured)
    return game / "rcc_Data" / "Managed" if game else None
