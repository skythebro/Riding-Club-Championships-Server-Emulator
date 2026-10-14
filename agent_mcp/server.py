"""Stdio MCP for the running game bridge and the local emulator."""

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

from mcp.server.mcpserver import MCPServer

BRIDGE = ("127.0.0.1", 27140)
try:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from server_config import load_config
    EMU_HEALTH = "http://127.0.0.1:%s/health" % load_config()["http_port"]
except Exception:
    EMU_HEALTH = "http://127.0.0.1:8080/health"
LOG_DIR = Path(__file__).resolve().parents[1] / "debug_logs"

server = MCPServer(
    "rcc-game",
    instructions=(
        "Talks to the Riding Club Championships client on 127.0.0.1:27140 and to ServerEmu. "
        "Layouts come from exports/decompiled. The bridge is only up while the game is running. "
        "Use game_buttons and game_click to press the visible interface, and game_scene to open a game screen "
        "or a course-editor terrain. Reload this MCP server after these tools change."
    ),
)


def bridge_call(payload: dict, timeout: float = 5) -> str:
    try:
        with socket.create_connection(BRIDGE, timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            reader = sock.makefile("r", encoding="utf-8", newline="\n")
            line = reader.readline()
    except (ConnectionRefusedError, TimeoutError, OSError) as exc:
        return json.dumps({"ok": False, "error": "bridge down: " + str(exc)})
    if not line:
        return json.dumps({"ok": False, "error": "bridge closed without a response"})
    return line.strip()


@server.tool()
def game_status() -> str:
    """Report whether the in-game bridge is listening, plus the current scene and hook count."""
    return bridge_call({"op": "status"})


@server.tool()
def game_describe(name: str) -> str:
    """List public methods and fields of a loaded game type, for example Protocol2.ServiceLogin."""
    return bridge_call({"op": "describe", "name": name})


@server.tool()
def game_search(query: str) -> str:
    """Find Assembly-CSharp type names that contain the query."""
    return bridge_call({"op": "search", "query": query})


@server.tool()
def game_hooks() -> str:
    """Return recent client service calls. Steam tickets and access tokens are redacted."""
    return bridge_call({"op": "hooks"})


@server.tool()
def game_serialize_card(category: int) -> str:
    """Create a card with Card.Create and return Card.Write() as hex. Category 15 is LogicHorse."""
    return bridge_call({"op": "serialize", "category": category}, timeout=8)


@server.tool()
def game_eval(code: str) -> str:
    """Evaluate a C# snippet on the Unity thread via the Mono.CSharp compiler embedded in UnityExplorer."""
    return bridge_call({"op": "eval", "code": code}, timeout=8)


BLANK_LABELS = r"""
System.Type textType = System.Type.GetType("UnityEngine.UI.Text, UnityEngine.UI");
UnityEngine.Object[] labels = UnityEngine.Object.FindObjectsOfType(textType);
System.Reflection.PropertyInfo textProp = textType.GetProperty("text");
string result = "";
int shown = 0;
foreach (UnityEngine.Object item in labels)
{
    UnityEngine.Component comp = item as UnityEngine.Component;
    if (comp == null || !comp.gameObject.activeInHierarchy)
        continue;
    if (comp.gameObject.name == "Buff")
        continue;
    string text = (string)textProp.GetValue(item, null);
    if (text == null)
        text = "";
    text = text.Trim();
    LabelLocalizer loc = comp.GetComponent<LabelLocalizer>();
    string tag = loc == null || loc.Tag == null ? "" : loc.Tag;
    bool blank = text.Length == 0;
    bool raw = text.IndexOf('_') >= 0;
    if (!blank && !raw)
        continue;
    shown++;
    if (result.Length < 1800)
    {
        string path = comp.gameObject.name;
        UnityEngine.Transform node = comp.transform.parent;
        int guard = 0;
        while (node != null && guard < 5)
        {
            path = node.name + "/" + path;
            node = node.parent;
            guard++;
        }
        result += path + " tag=" + tag + " [" + text + "]\n";
    }
}
shown.ToString() + "\n" + result
"""


@server.tool()
def game_blank_labels() -> str:
    """List visible labels that are blank or still look like a localization key.

    Stat bonus lines named Buff are skipped: those stay empty when the horse has no bonus.
    The game must be running. Reload this MCP server after the tool is added before calling it.
    """
    return bridge_call({"op": "eval", "code": BLANK_LABELS}, timeout=8)


def _csharp_string(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\r", "")
        .replace("\n", "")
    )


_BUTTONS = r"""
System.Type buttonType = System.Type.GetType("UnityEngine.UI.Button, UnityEngine.UI");
System.Type textType = System.Type.GetType("UnityEngine.UI.Text, UnityEngine.UI");
string query = "%(query)s";
string answer = "Unity UI is not loaded";
if (buttonType != null && textType != null)
{
    System.Reflection.PropertyInfo textProp = textType.GetProperty("text");
    System.Reflection.PropertyInfo interactProp = buttonType.GetProperty("interactable");
    UnityEngine.Object[] buttons = UnityEngine.Object.FindObjectsOfType(buttonType);
    string result = "";
    int shown = 0;
    int matched = 0;
    foreach (UnityEngine.Object item in buttons)
    {
        UnityEngine.Component comp = item as UnityEngine.Component;
        if (comp == null || !comp.gameObject.activeInHierarchy)
            continue;
        string label = "";
        UnityEngine.Component[] texts = comp.GetComponentsInChildren(textType, true);
        foreach (UnityEngine.Component textComp in texts)
        {
            string bit = (string)textProp.GetValue(textComp, null);
            if (bit == null)
                continue;
            bit = bit.Trim();
            if (bit.Length == 0)
                continue;
            if (label.Length > 0)
                label += " | ";
            label += bit;
        }
        string path = comp.gameObject.name;
        UnityEngine.Transform node = comp.transform.parent;
        int guard = 0;
        while (node != null && guard < 6)
        {
            path = node.name + "/" + path;
            node = node.parent;
            guard++;
        }
        bool interact = interactProp == null || (bool)interactProp.GetValue(item, null);
        string lowQuery = query.ToLower();
        string lowLabel = label.ToLower();
        string lowPath = path.ToLower();
        if (lowQuery.Length > 0 && lowLabel.IndexOf(lowQuery) < 0 && lowPath.IndexOf(lowQuery) < 0)
            continue;
        if (result.Length < 3500)
            result += shown.ToString() + " " + (interact ? "on " : "off ") + "[" + label + "] " + path + "\n";
        shown++;
        matched++;
    }
    answer = matched.ToString() + "\n" + result;
}
answer
"""


_CLICK = r"""
System.Type buttonType = System.Type.GetType("UnityEngine.UI.Button, UnityEngine.UI");
System.Type textType = System.Type.GetType("UnityEngine.UI.Text, UnityEngine.UI");
string query = "%(query)s";
int wantIndex = %(index)s;
string answer = "Unity UI is not loaded";
if (buttonType != null && textType != null)
{
    System.Reflection.PropertyInfo textProp = textType.GetProperty("text");
    System.Reflection.PropertyInfo interactProp = buttonType.GetProperty("interactable");
    System.Reflection.PropertyInfo onClickProp = buttonType.GetProperty("onClick");
    UnityEngine.Object[] buttons = UnityEngine.Object.FindObjectsOfType(buttonType);
    string clicked = "";
    int index = 0;
    foreach (UnityEngine.Object item in buttons)
    {
        UnityEngine.Component comp = item as UnityEngine.Component;
        if (comp == null || !comp.gameObject.activeInHierarchy)
            continue;
        string label = "";
        UnityEngine.Component[] texts = comp.GetComponentsInChildren(textType, true);
        foreach (UnityEngine.Component textComp in texts)
        {
            string bit = (string)textProp.GetValue(textComp, null);
            if (bit == null)
                continue;
            bit = bit.Trim();
            if (bit.Length == 0)
                continue;
            if (label.Length > 0)
                label += " | ";
            label += bit;
        }
        string path = comp.gameObject.name;
        UnityEngine.Transform node = comp.transform.parent;
        int guard = 0;
        while (node != null && guard < 6)
        {
            path = node.name + "/" + path;
            node = node.parent;
            guard++;
        }
        bool interact = interactProp == null || (bool)interactProp.GetValue(item, null);
        bool match = false;
        if (wantIndex >= 0)
            match = index == wantIndex;
        else
        {
            string lowQuery = query.ToLower();
            match = label.ToLower().IndexOf(lowQuery) >= 0 || path.ToLower().IndexOf(lowQuery) >= 0;
        }
        index++;
        if (!match || !interact)
            continue;
        object onClick = onClickProp.GetValue(item, null);
        onClick.GetType().GetMethod("Invoke").Invoke(onClick, null);
        clicked = "[" + label + "] " + path;
        break;
    }
    answer = clicked.Length == 0 ? "no matching button" : "clicked " + clicked;
}
answer
"""


_SCENE = r"""
System.Text.StringBuilder report = new System.Text.StringBuilder();
int count = UnityEngine.SceneManagement.SceneManager.sceneCount;
for (int i = 0; i < count; i++)
{
    UnityEngine.SceneManagement.Scene scene = UnityEngine.SceneManagement.SceneManager.GetSceneAt(i);
    report.Append(scene.name);
    if (scene == UnityEngine.SceneManagement.SceneManager.GetActiveScene())
        report.Append(" *");
    report.Append("\n");
}
string target = "%(target)s";
string arg = "%(arg)s";
if (target == "menu")
{
    Manager.LoadMainMenu();
    report.Append("loading menu");
}
else if (target == "shop")
{
    Manager.LoadShop();
    report.Append("loading shop");
}
else if (target == "paddock")
{
    Manager.LoadPaddock();
    report.Append("loading paddock");
}
else if (target == "tutorial")
{
    Manager.LoadTutorial();
    report.Append("loading tutorial");
}
else if (target == "showdown")
{
    Manager.LoadShowdown();
    report.Append("loading showdown");
}
else if (target == "grooming")
{
    Manager.LoadGrooming();
    report.Append("loading grooming");
}
else if (target == "daily")
{
    Manager.LoadDailyLogin();
    report.Append("loading daily");
}
else if (target == "avatar")
{
    Manager.LoadAvatarScene(false);
    report.Append("loading avatar");
}
else if (target == "editor")
{
    GuiCESlots slotsUi = UnityEngine.Object.FindObjectOfType<GuiCESlots>();
    System.Reflection.FieldInfo field = typeof(GuiCESlots).GetField("itemList", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
    System.Collections.IList list = slotsUi == null || field == null ? null : field.GetValue(slotsUi) as System.Collections.IList;
    Protocol2.CourseSlot chosen = null;
    if (list != null)
    {
        foreach (object entry in list)
        {
            Protocol2.CourseSlot slot = entry as Protocol2.CourseSlot;
            if (slot == null)
                continue;
            if (arg.Length == 0 || (slot.Name != null && slot.Name.ToLower().IndexOf(arg.ToLower()) >= 0))
            {
                chosen = slot;
                break;
            }
        }
    }
    if (chosen == null)
    {
        report.Append("no course slot is loaded");
    }
    else
    {
        chosen.ActivateCourse();
        report.Append("loading editor ");
        report.Append(chosen.Name);
    }
}
else if (target == "terrain")
{
    CourseEditorScene editor = UnityEngine.Object.FindObjectOfType<CourseEditorScene>();
    Protocol2.CardCourseItem card = Singleton<ICatalogue>.Instance.GetCard<Protocol2.CardCourseItem>(arg);
    if (editor == null)
        report.Append("course editor is not open");
    else if (card == null)
        report.Append("unknown terrain ");
    else
    {
        editor.OnSetTerrain(card, true);
        report.Append("loading terrain ");
        report.Append(card.Prefab);
    }
    if (card == null)
        report.Append(arg);
}
else if (target.Length > 0)
{
    report.Append("unknown target");
}
report.ToString()
"""


@server.tool()
def game_buttons(query: str = "") -> str:
    """List visible Unity UI buttons as index, on/off, label, and hierarchy path.

    query filters by label or path. The game must be running.
    """
    code = _BUTTONS % {"query": _csharp_string(query)}
    return bridge_call({"op": "eval", "code": code}, timeout=8)


@server.tool()
def game_click(query: str) -> str:
    """Click one visible, interactable Unity UI button.

    query matches the label or hierarchy path. A leading # uses the index from game_buttons,
    for example #3. The game must be running.
    """
    index = -1
    text = query.strip()
    if text.startswith("#") and text[1:].isdigit():
        index = int(text[1:])
        text = ""
    code = _CLICK % {"query": _csharp_string(text), "index": str(index)}
    return bridge_call({"op": "eval", "code": code}, timeout=8)


@server.tool()
def game_scene(target: str = "") -> str:
    """List loaded scenes, or open one through the game's own loader.

    Leave target empty to list scenes. The active scene is marked with a star.
    target is menu, shop, paddock, tutorial, showdown, grooming, daily, avatar,
    editor, editor:slot name, terrain:tutorial_terrain, terrain:desert_terrain,
    terrain:winter_terrain, terrain:stadium_terrain, or terrain:paddock_terrain.
    Terrain changes only work while the course editor is open. The game must be running.
    """
    raw = target.strip()
    kind = raw
    arg = ""
    if ":" in raw:
        kind, arg = raw.split(":", 1)
        kind = kind.strip().lower()
        arg = arg.strip()
    else:
        kind = kind.lower()
    allowed = {"", "menu", "shop", "paddock", "tutorial", "showdown", "grooming", "daily", "avatar", "editor", "terrain"}
    if kind not in allowed:
        return json.dumps({"ok": False, "error": "unknown scene target"})
    if kind == "terrain" and not arg.replace("_", "").isalnum():
        return json.dumps({"ok": False, "error": "terrain id must be a card id such as tutorial_terrain"})
    code = _SCENE % {"target": _csharp_string(kind), "arg": _csharp_string(arg)}
    return bridge_call({"op": "eval", "code": code}, timeout=8)


@server.tool()
def game_log() -> str:
    """Return recent Unity error and exception lines captured by the bridge."""
    return bridge_call({"op": "log"})


@server.tool()
def emu_health() -> str:
    """GET the emulator /health endpoint. Reports when ServerEmu is not running."""
    try:
        with urllib.request.urlopen(EMU_HEALTH, timeout=2) as response:
            return response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return json.dumps({"ok": False, "error": "emulator down: " + str(exc)})


@server.tool()
def emu_recent_packets() -> str:
    """Tail the newest TCP debug log, keeping Igor decode lines and connection events."""
    logs = sorted(LOG_DIR.glob("tcp_communication_*.log"))
    if not logs:
        return json.dumps({"ok": False, "error": "no tcp debug log yet"})
    path = logs[-1]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    kept = [
        line for line in lines
        if "IGOR:" in line or "NO REPLY" in line or "LOGIN" in line or "RECEIVED:" in line or "SENDING:" in line or "NO RESPONSE" in line
    ]
    return json.dumps({"ok": True, "file": path.name, "lines": kept[-40:]})


if __name__ == "__main__":
    server.run(transport="stdio")
