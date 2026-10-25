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


def _managed_in(path):
    """The game's Managed folder inside this game folder, or None.

    Windows and a copied folder on Linux: rcc_Data/Managed. macOS: <name>.app/Contents/Resources/Data/Managed.
    """
    if path is None:
        return None
    path = Path(path)
    windows = path / "rcc_Data" / "Managed"
    if (windows / "Assembly-CSharp.dll").exists():
        return windows
    bundles = [path] if path.suffix == ".app" else sorted(path.glob("*.app"))
    for bundle in bundles:
        mac = bundle / "Contents" / "Resources" / "Data" / "Managed"
        if (mac / "Assembly-CSharp.dll").exists():
            return mac
    return None


def _is_game_dir(path):
    return _managed_in(path) is not None


def app_bundle(game_dir):
    """The macOS game app inside this game folder, or None on Windows."""
    managed = _managed_in(game_dir)
    if managed is None or managed.parent.name == "rcc_Data":
        return None
    return managed.parents[3]


def servers_txt_path(game_dir):
    """Where the game reads servers.txt: next to rcc.exe, or at the top of the macOS app (beside Contents)."""
    bundle = app_bundle(game_dir)
    return (bundle if bundle is not None else Path(game_dir)) / "servers.txt"


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
    roots.append(Path.home() / "Library" / "Application Support" / "Steam")
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
    """The game folder that holds rcc_Data/Managed (or a macOS app), or None.

    Order: server.json game_dir, the folder above this server, then every Steam library.
    """
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(APP_DIR.parent)
    candidates.extend(library / "steamapps" / "common" / GAME_FOLDER for library in _steam_libraries())
    for candidate in candidates:
        if _is_game_dir(candidate):
            candidate = candidate.resolve()
            # game_dir may name the .app itself. The game folder is the one that holds it.
            return candidate.parent if candidate.suffix == ".app" else candidate
    return None


def managed_dir(configured=None):
    game = find_game_dir(configured)
    return _managed_in(game) if game else None
