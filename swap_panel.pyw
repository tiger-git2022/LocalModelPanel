"""本地模型管理：llama-swap 的图形控制面板（PySide6-Fluent-Widgets，Win11 Fluent 风格）。

双击运行（无控制台）；点 × 缩小到托盘，托盘菜单“退出面板”才真正关闭（服务不受影响）。

命令行参数（任务栏右键菜单里的快捷项也是用这些参数启动的）：
  --autostart  只在后台启动服务然后退出（开机自启用）
  --show       打开面板
  --start      启动服务
  --stop       停止服务
  --unload     卸载所有已加载的模型
面板已在运行时，这些操作会转交给正在运行的面板执行。

设置保存在 %APPDATA%\\LocalModelPanel\\settings.json。
"""
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

APP_NAME = "本地模型管理"
APP_VERSION = "1.0.0"
APP_ID = "local.llama-swap.panel"  # Windows AppUserModelID：任务栏分组、跳转列表都靠它对应
INSTANCE = "llama-swap-panel"
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
RES_DIR = os.path.join(getattr(sys, "_MEIPASS", APP_DIR), "assets")
ICON_ICO = os.path.join(RES_DIR, "icon.ico")
ICON_PNG = os.path.join(RES_DIR, "icon.png")
DATA_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), "LocalModelPanel")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
PANEL_LOG = os.path.join(DATA_DIR, "panel.log")
STARTUP_LNK = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\llama-swap.lnk")
PROGRAMS_DIR = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW：子进程共用一个隐藏控制台，不会弹窗
NEW_GROUP = 0x00000200
GREEN, YELLOW, RED = "#0f7b0f", "#9d5d00", "#c42b1c"
COMMANDS = ("--show", "--start", "--stop", "--unload")
SELFTEST_GUI = []  # 自检时：界面打开后要执行的检查（执行完自动退出）


def log_error(where):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PANEL_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}\n{traceback.format_exc()}\n")
    except OSError:
        pass


# ---------- 设置 ----------

def _guess_swap_dir():
    """第一次运行时找 llama-swap.exe：面板所在文件夹、上一级、当前目录。"""
    for d in (APP_DIR, os.path.dirname(APP_DIR), os.getcwd()):
        if os.path.isfile(os.path.join(d, "llama-swap.exe")):
            return d
    return ""


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s = json.load(f)
        if isinstance(s, dict):
            return s
    except (OSError, ValueError):
        pass
    # 第一次运行：自动找 llama-swap 文件夹，并导入旧版放在那里的 panel_prefs.json
    s = {"swap_dir": _guess_swap_dir()}
    old = os.path.join(s["swap_dir"], "panel_prefs.json") if s["swap_dir"] else ""
    if old and os.path.isfile(old):
        try:
            with open(old, encoding="utf-8") as f:
                s.update({k: v for k, v in json.load(f).items() if k not in s or not s[k]})
        except (OSError, ValueError):
            pass
    save_settings(s)
    return s


def save_settings(s):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except OSError:
        log_error("保存设置失败")


S = load_settings()


def update_settings(**kw):
    S.update(kw)
    save_settings(S)


def swap_dir():
    return S.get("swap_dir") or ""


def swap_exe():
    return os.path.join(swap_dir(), "llama-swap.exe")


def config_path():
    return S.get("config") or os.path.join(swap_dir(), "config.yaml")


def log_path():
    return os.path.join(swap_dir(), "llama-swap.log")


def listen():
    return (S.get("listen") or "127.0.0.1:8080").strip()


def base_url():
    host = listen()
    if host.startswith(":"):
        host = "127.0.0.1" + host
    elif host.startswith("0.0.0.0:"):
        host = "127.0.0.1" + host[7:]
    return f"http://{host}"


def read_config():
    try:
        with open(config_path(), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _unquote(v):
    v = v.split(" #")[0].strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v


def config_keys():
    """config.yaml 里 apiKeys: 下面列出的密钥。"""
    keys, inside = [], False
    for line in read_config().splitlines():
        if re.match(r"^apiKeys\s*:", line):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+-\s*(.+)$", line)
            if m:
                keys.append(_unquote(m.group(1)))
            elif line.strip() and not line.lstrip().startswith("#"):
                break
    return keys


def config_models():
    """从 config.yaml 的 models: 读出模型 ID、名称和说明（只做简单解析，不依赖 YAML 库）。"""
    models, inside, cur = [], False, None
    for line in read_config().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^models\s*:", line):
            inside = True
            continue
        if not inside:
            continue
        if not line.startswith(" "):
            break
        m = re.match(r"^( {1,4})([^\s#:][^:]*?)\s*:\s*$", line)
        if m and (not models or len(m.group(1)) == models[0]["indent"]):
            cur = {"id": _unquote(m.group(2)), "name": "", "desc": "", "indent": len(m.group(1))}
            models.append(cur)
            continue
        m = re.match(r"^\s+(name|description)\s*:\s*(.+)$", line)
        if cur and m:
            cur["name" if m.group(1) == "name" else "desc"] = _unquote(m.group(2))
    notes = S.get("model_notes") or {}
    titles = S.get("model_titles") or {}
    return [(m["id"], titles.get(m["id"]) or m["name"] or m["id"], notes.get(m["id"]) or m["desc"]) for m in models]


def key():
    if S.get("api_key"):
        return S["api_key"]
    kf = S.get("api_key_file")
    if kf:
        try:
            with open(kf, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    keys = config_keys()
    return keys[0] if keys else ""


def key_source():
    if S.get("api_key"):
        return "已在面板里设置"
    if S.get("api_key_file"):
        return f"读取自 {S['api_key_file']}"
    if config_keys():
        return "读取自 config.yaml 的 apiKeys"
    return "未设置（config.yaml 没有 apiKeys 时可以不填）"


# ---------- 服务控制（与界面无关） ----------

def api(path, method="GET", body=None, timeout=3):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if key():
        headers["Authorization"] = f"Bearer {key()}"
    req = urllib.request.Request(base_url() + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def run_hidden(args):
    return subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW,
                          encoding="utf-8", errors="replace").stdout or ""


def swap_pids():
    out = run_hidden(["tasklist", "/FI", "IMAGENAME eq llama-swap.exe", "/FO", "CSV", "/NH"])
    return [line.split('","')[1] for line in out.splitlines() if line.startswith('"llama-swap.exe"')]


def child_pids(pids):
    if not pids:
        return []
    out = run_hidden(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_Process -Filter \"%s\" | ForEach-Object { $_.ProcessId }"
                      % " OR ".join(f"ParentProcessId={int(p)}" for p in pids)])
    return [p.strip() for p in out.splitlines() if p.strip().isdigit()]


def start_swap():
    if swap_pids():
        return
    if not os.path.isfile(swap_exe()):
        raise RuntimeError("找不到 llama-swap.exe，请在“设置”里选择 llama-swap 所在的文件夹。")
    if not os.path.isfile(config_path()):
        raise RuntimeError(f"找不到配置文件 {config_path()}")
    log = open(log_path(), "w", encoding="utf-8")
    subprocess.Popen([swap_exe(), "-config", config_path(), "-listen", listen()], cwd=swap_dir(), stdout=log,
                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW | NEW_GROUP,
                     close_fds=True)


def stop_swap():
    pids = swap_pids()
    children = child_pids(pids)  # llama-server / whisper-server 等模型进程
    for pid in pids:
        run_hidden(["taskkill", "/F", "/T", "/PID", pid])
    for pid in children:  # 兜底：父进程被结束后可能留下的模型进程
        run_hidden(["taskkill", "/F", "/T", "/PID", pid])


def unload(model=None):
    api("/api/models/unload" + (f"/{model}" if model else ""), "POST", {})


def is_whisper(mid):
    return "whisper" in mid.lower()


def load_model(model):
    if is_whisper(model):
        # 语音识别模型没有对话接口，访问它的健康检查让 llama-swap 加载
        api(f"/upstream/{model}/health", timeout=600)
        return
    # 发一个只生成 1 个 token 的请求，让 llama-swap 把模型加载起来
    api("/v1/chat/completions", "POST",
        {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}, timeout=600)


def vram():
    try:
        out = run_hidden(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"])
        used, total = (int(x) for x in out.strip().splitlines()[0].split(","))
        return used / 1024, total / 1024
    except (OSError, ValueError, IndexError):
        return None


def launch_target(args):
    """返回 (程序, 参数)：打包版直接启动自己，源码版用 pythonw 运行脚本。"""
    if FROZEN:
        return sys.executable, args
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pyw, f'"{os.path.abspath(__file__)}" {args}'.strip()


# ---------- 快捷方式和任务栏右键菜单（直接用 ctypes 调 Windows COM 接口，不依赖 pywin32） ----------

class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p("{%s}" % text), ctypes.byref(self))


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_uint32)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("ptr", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


CLSID_ShellLink = "00021401-0000-0000-C000-000000000046"
IID_IShellLinkW = "000214F9-0000-0000-C000-000000000046"
IID_IPropertyStore = "886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"
IID_IPersistFile = "0000010b-0000-0000-C000-000000000046"
CLSID_DestinationList = "77f10cf0-3db5-4966-b520-b7c54fd35ed6"
IID_ICustomDestinationList = "6332debf-87b5-4670-90c0-5e57b408a49e"
CLSID_EnumerableObjectCollection = "2d3468c1-36a7-43b6-ac24-d3f02fd9607a"
IID_IObjectCollection = "5632b1a4-e38a-400a-928a-d4cd63230295"
IID_IObjectArray = "92ca9dcd-5622-4bba-a805-5e9f541bd8c9"
PKEY_TITLE = ("F29F85E0-4FF9-1068-AB91-08002B27B3D9", 2)
PKEY_APP_ID = ("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3", 5)
VT_LPWSTR = 31


def _com(ptr, index, *argtypes):
    """取 COM 对象虚表里第 index 个方法；返回值是 HRESULT，失败时 ctypes 会抛 OSError。"""
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[index])
    return lambda *a: fn(ptr, *a)


def _release(ptr):
    if ptr:
        vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])(ptr)


def _create(clsid, iid):
    ole32 = ctypes.oledll.ole32
    try:
        ole32.CoInitialize(None)
    except OSError:
        pass  # 已经初始化过（比如 Qt 用了另一种模式），不影响后面的调用
    ptr = ctypes.c_void_p()
    ole32.CoCreateInstance(ctypes.byref(_GUID(clsid)), None, 1, ctypes.byref(_GUID(iid)), ctypes.byref(ptr))
    return ptr.value


def _query(ptr, iid):
    out = ctypes.c_void_p()
    _com(ptr, 0, ctypes.c_void_p, ctypes.c_void_p)(ctypes.byref(_GUID(iid)), ctypes.byref(out))
    return out.value


def _set_string_prop(store, key_, text):
    pk = _PROPERTYKEY(_GUID(key_[0]), key_[1])
    buf = ctypes.create_unicode_buffer(text)
    pv = _PROPVARIANT(vt=VT_LPWSTR, ptr=ctypes.cast(buf, ctypes.c_void_p).value)
    _com(store, 6, ctypes.c_void_p, ctypes.c_void_p)(ctypes.byref(pk), ctypes.byref(pv))  # IPropertyStore::SetValue


def make_shortcut(path, args, title, desc=""):
    """创建快捷方式（IShellLinkW），写入 AppUserModelID，让它和面板窗口在任务栏归为同一个程序。
    path 为 None 时不保存成文件，返回 COM 指针（给任务栏右键菜单用，调用方负责释放）。"""
    target, arguments = launch_target(args)
    w = ctypes.c_wchar_p
    link = _create(CLSID_ShellLink, IID_IShellLinkW)
    try:
        _com(link, 20, w)(target)  # SetPath
        _com(link, 11, w)(arguments)  # SetArguments
        _com(link, 9, w)(APP_DIR)  # SetWorkingDirectory
        _com(link, 17, w, ctypes.c_int)(sys.executable if FROZEN else ICON_ICO, 0)  # SetIconLocation
        _com(link, 7, w)(desc or title)  # SetDescription
        store = _query(link, IID_IPropertyStore)
        try:
            _set_string_prop(store, PKEY_APP_ID, APP_ID)
            _set_string_prop(store, PKEY_TITLE, title)
            _com(store, 7)()  # Commit
        finally:
            _release(store)
        if path:
            pf = _query(link, IID_IPersistFile)
            try:
                _com(pf, 6, w, ctypes.c_int)(path, 1)  # IPersistFile::Save
            finally:
                _release(pf)
    except Exception:
        _release(link)
        raise
    if path:
        _release(link)
        return None
    return link


JUMP_TASKS = [("显示面板", "--show"), ("启动服务", "--start"), ("停止服务", "--stop"), ("卸载模型", "--unload")]


def register_jump_list():
    """任务栏按钮右键菜单（跳转列表）里的“任务”：显示面板 / 启动服务 / 停止服务 / 卸载模型。"""
    dl = _create(CLSID_DestinationList, IID_ICustomDestinationList)
    coll = removed = None
    links = []
    try:
        _com(dl, 3, ctypes.c_wchar_p)(APP_ID)  # SetAppID
        slots, removed_p = ctypes.c_uint(), ctypes.c_void_p()
        _com(dl, 4, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(  # BeginList
            ctypes.byref(slots), ctypes.byref(_GUID(IID_IObjectArray)), ctypes.byref(removed_p))
        removed = removed_p.value
        coll = _create(CLSID_EnumerableObjectCollection, IID_IObjectCollection)
        for title, args in JUMP_TASKS:
            links.append(make_shortcut(None, args, title))
            _com(coll, 5, ctypes.c_void_p)(links[-1])  # IObjectCollection::AddObject
        _com(dl, 7, ctypes.c_void_p)(coll)  # AddUserTasks（IObjectCollection 继承自 IObjectArray）
        _com(dl, 8)()  # CommitList
    finally:
        for p in links + [coll, removed, dl]:
            _release(p)


def create_shortcuts():
    """在桌面和开始菜单创建快捷方式（带 AppUserModelID，固定到任务栏后右键菜单也有快捷项）。"""
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(0, 0x10, 0, 0, buf)  # CSIDL_DESKTOPDIRECTORY（OneDrive 桌面也能找对）
    paths = [os.path.join(PROGRAMS_DIR, APP_NAME + ".lnk")]
    if buf.value:
        paths.insert(0, os.path.join(buf.value, APP_NAME + ".lnk"))
    for p in paths:
        make_shortcut(p, "", APP_NAME, "llama-swap 控制面板")
    return paths


def set_autostart(enable):
    if enable:
        target, args = launch_target("--autostart")
        ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';"
              "$s.Arguments='%s';$s.WorkingDirectory='%s';$s.Save()"
              % (STARTUP_LNK, target, args.replace("'", "''"), APP_DIR))
        run_hidden(["powershell", "-NoProfile", "-Command", ps])
    elif os.path.exists(STARTUP_LNK):
        os.remove(STARTUP_LNK)


def autostart_mode():
    # 开机时网络（比如 Tailscale）可能还没就绪，监听地址不可用会导致启动失败，所以多试几次
    for _ in range(24):
        try:
            start_swap()
        except Exception:
            log_error("开机自启：启动服务失败")
            return
        time.sleep(5)
        try:
            if swap_pids() and api("/health") == "OK":
                return
        except Exception:
            pass
        time.sleep(5)


def collect_status():
    st = {"alive": bool(swap_pids()), "ok": False, "running": {}, "vram": vram()}
    if st["alive"]:
        try:
            st["ok"] = api("/health") == "OK"
            st["running"] = {r.get("model"): r.get("state") for r in api("/running").get("running", [])}
        except Exception:
            pass
    return st


HEADLESS = {"--start": start_swap, "--stop": stop_swap, "--unload": unload}


# ---------- 界面 ----------

def run_gui(command=None):
    from PySide6.QtCore import QObject, QPointF, Qt, QTimer, Signal
    from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLabel, QSystemTrayIcon, QVBoxLayout, QWidget
    from qfluentwidgets import (Action, BodyLabel, CaptionLabel, CardWidget, ComboBox, FluentIcon as FIF,
                                FluentWindow, IconWidget, InfoBar, InfoBarPosition, LineEdit, MessageBoxBase,
                                NavigationItemPosition, PasswordLineEdit, PrimaryPushButton, ProgressBar, PushButton,
                                PushSettingCard, ScrollArea, SettingCard, SettingCardGroup, Slider, StrongBodyLabel,
                                SubtitleLabel, SwitchButton, SystemTrayMenu, Theme, TitleLabel, isDarkTheme, setTheme,
                                setThemeColor)

    THEMES = [("浅色", Theme.LIGHT), ("深色", Theme.DARK), ("跟随系统", Theme.AUTO)]
    # Win11 DWMWA_SYSTEMBACKDROP_TYPE：2=云母 3=亚克力（模糊透出窗口后面的内容） 4=云母 Alt；0 表示不用特效
    BACKDROPS = [("云母", 2), ("亚克力（半透明）", 3), ("云母 Alt", 4), ("纯色", 0)]

    # 独立的 AppUserModelID：任务栏显示本面板图标，跳转列表也挂在这个 ID 下
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 单实例：已有面板在运行就把命令（默认“显示”）交给它，然后退出
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE)
    if sock.waitForConnected(300):
        sock.write((command or "--show").encode())
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return
    if command in HEADLESS:  # 面板没开：直接在后台执行，不打开窗口
        try:
            HEADLESS[command]()
        except Exception:
            log_error(f"命令 {command} 执行失败")
        return
    QLocalServer.removeServer(INSTANCE)
    server = QLocalServer()
    server.listen(INSTANCE)

    def idx(name, n, default=0):
        try:
            v = int(S.get(name, default))
        except (TypeError, ValueError):
            v = default
        return v if 0 <= v < n else default

    theme_idx = idx("theme", len(THEMES))
    backdrop_idx = idx("backdrop", len(BACKDROPS))
    tint = min(max(idx("tint", 96, 55), 0), 95)  # 亚克力上方遮罩的不透明度（%），越高越易读
    setTheme(THEMES[theme_idx][1])
    setThemeColor("#0067c0")

    APP_ICON = QIcon(ICON_ICO)
    try:  # 图标里天线小球的位置（比例）
        with open(os.path.join(RES_DIR, "icon_ball.json"), encoding="utf-8") as f:
            BALL = json.load(f)
    except (OSError, ValueError):
        BALL = None
    BALL_COLORS = {YELLOW: "#f7b500", RED: "#e5534b"}  # 运行中保持原图的绿色

    def status_icon(color):
        """托盘用：机器人天线小球随服务状态变色（绿=运行中，黄=启动中，红=已停止）。"""
        size = 256
        pm = QPixmap(ICON_PNG).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if BALL and color in BALL_COLORS:
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(BALL_COLORS[color]))
            r = BALL["r"] * size * 1.06  # 略大一点盖住原来绿色的抗锯齿边
            p.drawEllipse(QPointF(BALL["cx"] * size, BALL["cy"] * size), r, r)
            p.end()
        return QIcon(pm)

    class Bridge(QObject):
        status = Signal(dict)
        done = Signal(str, str)  # (成功提示, 错误信息)

    bridge = Bridge()

    def page(name):
        area = ScrollArea()
        area.setObjectName(name)
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.enableTransparentBackground()
        view = QWidget()
        view.setObjectName(name + "View")
        view.setStyleSheet(f"#{name}View{{background:transparent}}")
        area.setWidget(view)
        lay = QVBoxLayout(view)
        lay.setContentsMargins(36, 28, 36, 28)
        lay.setSpacing(12)
        return area, lay

    class TextDialog(MessageBoxBase):
        def __init__(self, parent, title, tip, text, password=False):
            super().__init__(parent)
            self.viewLayout.addWidget(SubtitleLabel(title))
            hint = CaptionLabel(tip)
            hint.setWordWrap(True)
            self.viewLayout.addWidget(hint)
            self.edit = PasswordLineEdit() if password else LineEdit()
            self.edit.setText(text)
            self.edit.setClearButtonEnabled(True)
            self.viewLayout.addWidget(self.edit)
            self.yesButton.setText("保存")
            self.cancelButton.setText("取消")
            self.widget.setMinimumWidth(420)

    class ModelCard(CardWidget):
        def __init__(self, mid, title, desc, panel):
            super().__init__()
            self.mid, self.panel, self.loaded = mid, panel, False
            low = mid.lower()
            icon = IconWidget(FIF.MICROPHONE if is_whisper(mid) else
                              FIF.LANGUAGE if ("mt" in low or "trans" in low) else FIF.ROBOT)
            icon.setFixedSize(22, 22)
            self.title = StrongBodyLabel(title)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(self.title)
            if desc:
                d = CaptionLabel(desc)
                d.setWordWrap(True)
                d.setTextColor(QColor("#606060"), QColor("#a0a0a0"))
                text.addWidget(d)
            self.state = CaptionLabel("未加载")
            self.btn = PushButton("加载")
            self.btn.setFixedWidth(96)
            self.btn.clicked.connect(self.toggle)
            row = QHBoxLayout(self)
            row.setContentsMargins(20, 14, 20, 14)
            row.setSpacing(16)
            row.addWidget(icon)
            row.addLayout(text, 1)
            row.addWidget(self.state)
            row.addWidget(self.btn)

        def toggle(self):
            if self.loaded:
                self.panel.run(lambda: unload(self.mid), f"已卸载 {self.mid}")
            else:
                self.panel.run(lambda: load_model(self.mid), f"{self.mid} 已加载", f"正在加载 {self.mid}…")

        def set_state(self, state, enabled):
            self.loaded = state is not None
            if state == "ready":
                self.state.setText("● 已加载")
                self.state.setTextColor(QColor(GREEN), QColor(GREEN))
            elif state:
                self.state.setText(f"● {state}")
                self.state.setTextColor(QColor(YELLOW), QColor(YELLOW))
            else:
                self.state.setText("未加载")
                self.state.setTextColor(QColor("#808080"), QColor("#808080"))
            self.btn.setText("卸载" if self.loaded else "加载")
            self.btn.setEnabled(enabled)

    class Panel(FluentWindow):
        def __init__(self):
            super().__init__()
            self.busy = False
            self.alive = self.ok = False
            self.quitting = False
            self.notified = False
            self.running = {}
            self.cards = []
            self.setWindowTitle(APP_NAME)
            self.setWindowIcon(APP_ICON)
            # 按屏幕可用区域（扣掉任务栏）限制大小并居中，避免底部导航按钮被任务栏挡住
            avail = QApplication.primaryScreen().availableGeometry()
            w, h = min(880, avail.width() - 40), min(780, avail.height() - 40)
            self.resize(w, h)
            self.move(avail.x() + (avail.width() - w) // 2, avail.y() + (avail.height() - h) // 2)
            self.setMicaEffectEnabled(True)
            self.navigationInterface.setExpandWidth(200)
            self.navigationInterface.setMinimumExpandWidth(700)
            self.navigationInterface.setAcrylicEnabled(True)
            self.tint = tint

            self._build_home()
            self._build_settings()
            self.addSubInterface(self.home, FIF.HOME, "主页")
            self.addSubInterface(self.settings, FIF.SETTING, "设置")
            self.navigationInterface.addItem("theme", FIF.CONSTRACT, "切换深浅色", onClick=self.toggle_theme,
                                             selectable=False, position=NavigationItemPosition.BOTTOM,
                                             tooltip="切换深色 / 浅色")

            self.tray = QSystemTrayIcon(status_icon(YELLOW), self)
            self.tray.setToolTip(APP_NAME)
            menu = SystemTrayMenu(parent=self)
            self.act_start = Action(FIF.PLAY, "启动服务", triggered=lambda: self.run(start_swap, "服务已启动"))
            self.act_stop = Action(FIF.PAUSE, "停止服务", triggered=lambda: self.run(stop_swap, "服务已停止"))
            self.act_unload = Action(FIF.DELETE, "卸载模型", triggered=lambda: self.run(unload, "已卸载模型"))
            menu.addActions([Action(FIF.HOME, "显示面板", triggered=self.show_window)])
            menu.addSeparator()
            menu.addActions([self.act_start, self.act_stop, self.act_unload])
            menu.addSeparator()
            menu.addActions([Action(FIF.CLOSE, "退出面板（服务继续运行）", triggered=self.quit_panel)])
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(
                lambda r: self.show_window() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
            self.tray.show()

            server.newConnection.connect(self._on_instance)
            bridge.status.connect(self.render)
            bridge.done.connect(self._finish)
            self.timer = QTimer(self, interval=2000, timeout=self.poll)
            self.timer.start()
            self.poll()

        # --- 页面 ---
        def _build_home(self):
            self.home, lay = page("home")
            lay.addWidget(TitleLabel(APP_NAME))
            head = QHBoxLayout()
            head.setSpacing(8)
            self.dot = QLabel()
            self.dot.setFixedSize(10, 10)
            self.status = BodyLabel("检查中…")
            head.addWidget(self.dot)
            head.addWidget(self.status)
            head.addStretch()
            lay.addLayout(head)
            lay.addSpacing(8)

            card = CardWidget()
            c = QVBoxLayout(card)
            c.setContentsMargins(24, 20, 24, 20)
            c.setSpacing(10)
            c.addWidget(SubtitleLabel("服务"))
            self.info = {}
            for k, label in [("url", "接口地址"), ("model", "当前模型"), ("vram", "显存占用")]:
                row = QHBoxLayout()
                name = BodyLabel(label)
                name.setFixedWidth(96)
                name.setTextColor(QColor("#606060"), QColor("#a0a0a0"))
                val = BodyLabel("-")
                val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                row.addWidget(name)
                row.addWidget(val, 1)
                c.addLayout(row)
                self.info[k] = val
            self.bar = ProgressBar()
            self.bar.setRange(0, 100)
            c.addWidget(self.bar)
            c.addSpacing(6)
            btns = QHBoxLayout()
            btns.setSpacing(10)
            self.start_btn = PrimaryPushButton(FIF.PLAY, "启动服务")
            self.stop_btn = PushButton(FIF.PAUSE, "停止服务")
            self.start_btn.clicked.connect(lambda: self.run(start_swap, "服务已启动", "正在启动…"))
            self.stop_btn.clicked.connect(lambda: self.run(stop_swap, "服务已停止", "正在停止…"))
            btns.addWidget(self.start_btn)
            btns.addWidget(self.stop_btn)
            btns.addStretch()
            c.addLayout(btns)
            lay.addWidget(card)

            lay.addSpacing(12)
            lay.addWidget(SubtitleLabel("模型"))
            tip = CaptionLabel("模型列表来自 llama-swap 的 config.yaml。调用接口时会自动加载对应模型，也可以在这里手动加载或卸载；"
                               "能否同时加载、空闲多久自动卸载，由 config.yaml 决定。")
            tip.setTextColor(QColor("#606060"), QColor("#a0a0a0"))
            tip.setWordWrap(True)
            lay.addWidget(tip)
            self.model_lay = QVBoxLayout()
            self.model_lay.setSpacing(12)
            lay.addLayout(self.model_lay)
            self.empty_tip = CaptionLabel("")
            self.empty_tip.setWordWrap(True)
            lay.addWidget(self.empty_tip)
            lay.addStretch()
            self.reload_models()

        def reload_models(self):
            for cd in self.cards:
                cd.setParent(None)
                cd.deleteLater()
            self.cards = [ModelCard(mid, t, d, self) for mid, t, d in config_models()]
            for cd in self.cards:
                self.model_lay.addWidget(cd)
            self.empty_tip.setText("" if self.cards else
                                   f"没有读到模型：请确认 {config_path()} 存在，并且里面有 models: 配置。")
            self.empty_tip.setVisible(not self.cards)
            self.info["url"].setText(f"{base_url()}/v1")
            self._apply_enabled()

        def _build_settings(self):
            self.settings, lay = page("settings")
            lay.addWidget(TitleLabel("设置"))
            lay.addSpacing(8)

            g1 = SettingCardGroup("服务")
            self.dir_card = PushSettingCard("选择", FIF.FOLDER, "llama-swap 文件夹", swap_dir() or "未设置：请选择 llama-swap.exe 所在的文件夹")
            self.dir_card.clicked.connect(self.pick_swap_dir)
            self.listen_card = PushSettingCard("修改", FIF.GLOBE, "监听地址", listen())
            self.listen_card.clicked.connect(self.edit_listen)
            self.url_card = PushSettingCard("复制", FIF.LINK, "接口地址", f"{base_url()}/v1（OpenAI 兼容）")
            self.url_card.clicked.connect(lambda: self.copy(f"{base_url()}/v1", "已复制接口地址"))
            self.key_card = PushSettingCard("修改", FIF.FINGERPRINT, "API 密钥", key_source())
            self.key_card.clicked.connect(self.edit_key)
            copy_key = PushButton("复制")
            copy_key.clicked.connect(lambda: self.copy(key(), "已复制 API 密钥") if key() else self.toast(False, "没有设置 API 密钥"))
            self.key_card.hBoxLayout.insertWidget(self.key_card.hBoxLayout.count() - 2, copy_key, 0, Qt.AlignRight)
            self.key_card.hBoxLayout.insertSpacing(self.key_card.hBoxLayout.count() - 2, 8)
            auto = SettingCard(FIF.POWER_BUTTON, "开机自动启动服务", "登录 Windows 后在后台启动 llama-swap（不打开面板）")
            self.auto_sw = SwitchButton()
            self.auto_sw.setOnText("开")
            self.auto_sw.setOffText("关")
            self.auto_sw.setChecked(os.path.exists(STARTUP_LNK))
            self.auto_sw.checkedChanged.connect(set_autostart)
            auto.hBoxLayout.addWidget(self.auto_sw, 0, Qt.AlignRight)
            auto.hBoxLayout.addSpacing(16)
            for w in (self.dir_card, self.listen_card, self.url_card, self.key_card, auto):
                g1.addSettingCard(w)
            lay.addWidget(g1)

            g0 = SettingCardGroup("外观")
            tc = SettingCard(FIF.BRUSH, "主题", "浅色 / 深色 / 跟随 Windows 设置")
            self.theme_box = ComboBox()
            self.theme_box.addItems([name for name, _ in THEMES])
            self.theme_box.setCurrentIndex(theme_idx)
            self.theme_box.setMinimumWidth(120)
            self.theme_box.currentIndexChanged.connect(self.apply_theme)
            tc.hBoxLayout.addWidget(self.theme_box, 0, Qt.AlignRight)
            tc.hBoxLayout.addSpacing(16)
            g0.addSettingCard(tc)
            bc = SettingCard(FIF.TRANSPARENT, "背景材质", "Win11 窗口特效；窗口失去焦点时系统会自动显示为纯色")
            self.backdrop_box = ComboBox()
            self.backdrop_box.addItems([name for name, _ in BACKDROPS])
            self.backdrop_box.setCurrentIndex(backdrop_idx)
            self.backdrop_box.setMinimumWidth(160)
            self.backdrop_box.currentIndexChanged.connect(self.set_backdrop)
            bc.hBoxLayout.addWidget(self.backdrop_box, 0, Qt.AlignRight)
            bc.hBoxLayout.addSpacing(16)
            g0.addSettingCard(bc)
            self.tint_card = SettingCard(FIF.BRIGHTNESS, "亚克力遮罩", "越高文字越清晰，越低越通透")
            self.tint_slider = Slider(Qt.Horizontal)
            self.tint_slider.setRange(0, 95)
            self.tint_slider.setValue(tint)
            self.tint_slider.setFixedWidth(220)
            self.tint_value = BodyLabel(f"{tint}%")
            self.tint_value.setFixedWidth(44)
            self.tint_slider.valueChanged.connect(self.set_tint)
            self.tint_card.hBoxLayout.addWidget(self.tint_slider, 0, Qt.AlignRight)
            self.tint_card.hBoxLayout.addSpacing(12)
            self.tint_card.hBoxLayout.addWidget(self.tint_value, 0, Qt.AlignRight)
            self.tint_card.hBoxLayout.addSpacing(16)
            g0.addSettingCard(self.tint_card)
            sc = PushSettingCard("创建", FIF.PIN, "桌面和开始菜单快捷方式",
                                 "创建“本地模型管理”快捷方式；固定到任务栏后，右键菜单里有启动服务、卸载模型等快捷项")
            sc.clicked.connect(self.make_shortcuts)
            g0.addSettingCard(sc)
            lay.addWidget(g0)

            g2 = SettingCardGroup("文件")
            self.file_cards = []
            for icon, title, fn in [(FIF.DOCUMENT, "运行日志", log_path), (FIF.EDIT, "配置文件", config_path),
                                    (FIF.FOLDER, "模型目录", lambda: S.get("model_dir") or "")]:
                cd = PushSettingCard("打开", icon, title, fn() or "未设置")
                cd.clicked.connect(lambda f=fn, t=title: self.open_path(f(), t))
                self.file_cards.append((cd, fn))
                g2.addSettingCard(cd)
            md = PushSettingCard("选择", FIF.FOLDER_ADD, "设置模型目录", "“打开模型目录”时打开哪个文件夹（只用于快速打开，不影响加载）")
            md.clicked.connect(self.pick_model_dir)
            g2.addSettingCard(md)
            lay.addWidget(g2)
            ver = CaptionLabel(f"版本 {APP_VERSION} · 设置文件 {SETTINGS_FILE}")
            ver.setTextColor(QColor("#808080"), QColor("#909090"))
            ver.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(ver)
            lay.addStretch()

        def refresh_settings_text(self):
            self.dir_card.setContent(swap_dir() or "未设置：请选择 llama-swap.exe 所在的文件夹")
            self.listen_card.setContent(listen())
            self.url_card.setContent(f"{base_url()}/v1（OpenAI 兼容）")
            self.key_card.setContent(key_source())
            for cd, fn in self.file_cards:
                cd.setContent(fn() or "未设置")
            self.reload_models()

        # --- 设置操作 ---
        def pick_swap_dir(self):
            d = QFileDialog.getExistingDirectory(self, "选择 llama-swap.exe 所在的文件夹", swap_dir())
            if not d:
                return
            d = os.path.normpath(d)
            if not os.path.isfile(os.path.join(d, "llama-swap.exe")):
                self.toast(False, "这个文件夹里没有 llama-swap.exe")
                return
            update_settings(swap_dir=d)
            self.refresh_settings_text()
            self.toast(True, "已保存", "重新启动服务后生效。")

        def pick_model_dir(self):
            d = QFileDialog.getExistingDirectory(self, "选择模型文件夹", S.get("model_dir") or "")
            if d:
                update_settings(model_dir=os.path.normpath(d))
                self.refresh_settings_text()

        def edit_listen(self):
            dlg = TextDialog(self, "监听地址",
                             "llama-swap 监听的“IP:端口”。只在本机用填 127.0.0.1:8080；想让局域网或 Tailscale 里的其他设备访问，"
                             "填本机对应网卡的 IP（比如 Tailscale 的 100.x.x.x:8080），这时务必在 config.yaml 里设置 apiKeys。",
                             listen())
            if dlg.exec():
                v = dlg.edit.text().strip()
                if not re.match(r"^[\w.\-\[\]:]*:\d+$", v):
                    self.toast(False, "格式不对", "应该是 IP:端口，例如 127.0.0.1:8080")
                    return
                update_settings(listen=v)
                self.refresh_settings_text()
                self.toast(True, "已保存", "重新启动服务后生效。")

        def edit_key(self):
            dlg = TextDialog(self, "API 密钥",
                             "面板调用 llama-swap 时用的密钥。留空则自动读取 config.yaml 里 apiKeys 的第一个。",
                             S.get("api_key", ""), password=True)
            if dlg.exec():
                update_settings(api_key=dlg.edit.text().strip())
                self.refresh_settings_text()
                self.toast(True, "已保存")

        def make_shortcuts(self):
            try:
                create_shortcuts()
                register_jump_list()
                self.toast(True, "已创建快捷方式", "桌面和开始菜单里都有“本地模型管理”。")
            except Exception as e:  # noqa: BLE001
                log_error("创建快捷方式失败")
                self.toast(False, "创建失败", str(e))

        # --- 主题 ---
        def apply_theme(self, i):
            setTheme(THEMES[i][1])
            update_settings(theme=i)

        def toggle_theme(self):
            self.theme_box.setCurrentIndex(0 if isDarkTheme() else 1)

        def set_backdrop(self, i):
            update_settings(backdrop=i)
            self.apply_backdrop()

        def apply_backdrop(self):
            kind = BACKDROPS[self.backdrop_box.currentIndex()][1]
            self.tint_card.setEnabled(kind == 3)
            if kind == 0:
                if self.isMicaEffectEnabled():
                    self.setMicaEffectEnabled(False)
                return
            if not self.isMicaEffectEnabled():
                self.setMicaEffectEnabled(True)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(self.winId()), 38, ctypes.byref(ctypes.c_int(kind)), 4)
            self.setBackgroundColor(self._normalBackgroundColor())

        def set_tint(self, v):
            self.tint = v
            self.tint_value.setText(f"{v}%")
            update_settings(tint=v)
            self.setBackgroundColor(self._normalBackgroundColor())

        def _normalBackgroundColor(self):
            # 亚克力时在窗口底层画一层半透明底色，压低背后内容的对比度，提升可读性
            if self.isMicaEffectEnabled() and hasattr(self, "backdrop_box") \
                    and BACKDROPS[self.backdrop_box.currentIndex()][1] == 3:
                c = QColor(32, 32, 32) if isDarkTheme() else QColor(243, 243, 243)
                c.setAlpha(int(self.tint / 100 * 255))
                return c
            return super()._normalBackgroundColor()

        def showEvent(self, e):
            super().showEvent(e)
            QTimer.singleShot(0, self.apply_backdrop)
            if not getattr(self, "_nav_expanded", False) and self.width() >= 700:
                self._nav_expanded = True
                QTimer.singleShot(0, lambda: self.navigationInterface.expand(useAni=False))

        def _onThemeChangedFinished(self):
            super()._onThemeChangedFinished()
            QTimer.singleShot(0, self.apply_backdrop)

        # --- 动作 ---
        def toast(self, ok, title, text=""):
            (InfoBar.success if ok else InfoBar.error)(
                title, text, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=2500 if ok else 6000)

        def copy(self, text, msg):
            QGuiApplication.clipboard().setText(text)
            self.toast(True, msg)

        def open_path(self, path, title):
            if path and os.path.exists(path):
                os.startfile(path)
            elif title == "运行日志":
                self.toast(False, "日志还不存在", "日志要由本面板启动服务后才会生成。")
            else:
                self.toast(False, f"{title}不存在或未设置", path)

        def run(self, fn, ok_msg, busy_msg=None):
            if self.busy:
                return
            self.busy = True
            self._apply_enabled()
            if busy_msg:
                self.status.setText(busy_msg)

            def work():
                err = ""
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    err = str(e)
                bridge.done.emit(ok_msg, err)

            threading.Thread(target=work, daemon=True).start()

        def _finish(self, ok_msg, err):
            self.busy = False
            if self.isVisible():
                self.toast(not err, "操作失败" if err else ok_msg, err)
            else:  # 从任务栏快捷项或托盘操作、窗口没打开时，用托盘通知
                self.tray.showMessage(APP_NAME, err or ok_msg,
                                      QSystemTrayIcon.Warning if err else QSystemTrayIcon.Information, 3000)
            self.poll()

        # --- 刷新 ---
        def poll(self):
            threading.Thread(target=lambda: bridge.status.emit(collect_status()), daemon=True).start()

        def render(self, st):
            self.alive, self.ok = st["alive"], st["ok"]
            if st["ok"]:
                text, color = "运行中", GREEN
            elif st["alive"]:
                text, color = "启动中 / 无响应", YELLOW
            else:
                text, color = "已停止", RED
            if not self.busy:
                self.status.setText(text)
            self.dot.setStyleSheet(f"background:{color};border-radius:5px;")
            if getattr(self, "_color", None) != color:
                self._color = color
                self.tray.setIcon(status_icon(color))
            self.tray.setToolTip(f"{APP_NAME}：{text}")
            titles = {cd.mid: cd.title.text() for cd in self.cards}
            self.info["model"].setText("，".join(titles.get(m, m) for m in st["running"]) or "无")
            if st["vram"]:
                used, total = st["vram"]
                self.info["vram"].setText(f"{used:.1f} / {total:.1f} GB")
                self.bar.setValue(int(used / total * 100))
            else:
                self.info["vram"].setText("（未检测到 NVIDIA 显卡）")
            self.running = st["running"]
            self._apply_enabled()

        def _apply_enabled(self):
            if not hasattr(self, "start_btn") or not hasattr(self, "act_start"):
                return
            ok = self.ok and not self.busy
            for cd in self.cards:
                cd.set_state(self.running.get(cd.mid), ok)
            self.start_btn.setEnabled(not self.alive and not self.busy)
            self.stop_btn.setEnabled(self.alive and not self.busy)
            self.act_start.setEnabled(not self.alive and not self.busy)
            self.act_stop.setEnabled(self.alive and not self.busy)
            self.act_unload.setEnabled(ok and bool(self.running))

        # --- 窗口 / 托盘 / 其他实例转来的命令 ---
        def _on_instance(self):
            c = server.nextPendingConnection()
            if not c:
                return
            c.waitForReadyRead(300)
            self.handle_command(bytes(c.readAll()).decode(errors="ignore").strip() or "--show")
            c.close()

        def handle_command(self, cmd):
            if cmd == "--start":
                self.run(start_swap, "服务已启动", "正在启动…")
            elif cmd == "--stop":
                self.run(stop_swap, "服务已停止", "正在停止…")
            elif cmd == "--unload":
                self.run(unload, "已卸载模型")
            else:
                self.show_window()

        def show_window(self):
            self.showNormal()
            self.raise_()
            self.activateWindow()

        def closeEvent(self, e):
            if self.quitting:
                e.accept()
                return
            e.ignore()
            self.hide()
            if not self.notified:
                self.notified = True
                self.tray.showMessage(APP_NAME, "面板已缩小到托盘，服务继续运行。右键托盘图标可退出。",
                                      QSystemTrayIcon.Information, 3000)

        def quit_panel(self):
            self.quitting = True
            self.tray.hide()
            self.close()
            app.quit()

    w = Panel()
    if command in (None, "--show"):
        w.show()
    else:
        w.handle_command(command)
    if SELFTEST_GUI:
        def check():
            try:
                for fn in SELFTEST_GUI:
                    fn(w)
            finally:
                w.quit_panel()
        QTimer.singleShot(3000, check)
    # 每次启动都重新登记任务栏右键菜单的快捷项（程序换了位置也能自动更新）
    try:
        register_jump_list()
    except Exception:
        log_error("登记任务栏快捷项失败")
    app.exec()


def selftest():
    out = os.environ.get("SELFTEST_LOG")
    lines = []

    def say(msg):
        lines.append(msg)
        if sys.stdout:  # 打包成无控制台程序时 stdout 是 None
            print(msg, flush=True)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

    steps = [("图标文件", lambda: os.path.isfile(ICON_ICO) and os.path.isfile(ICON_PNG) or 1 / 0),
             ("导入 PySide6", lambda: __import__("PySide6.QtWidgets")),
             ("导入 qfluentwidgets", lambda: __import__("qfluentwidgets")),
             ("创建快捷方式对象", lambda: _release(make_shortcut(None, "--start", "启动服务"))),
             ("登记任务栏快捷项", register_jump_list),
             ("读取 config 示例", lambda: config_models)]
    errors = []
    sys.excepthook = lambda *e: errors.append("".join(traceback.format_exception(*e)))

    def gui_check(w):
        w.refresh_settings_text()
        w.switchTo(w.settings)
        w.switchTo(w.home)
        w.handle_command("--show")
        w.render({"alive": True, "ok": True, "running": {"x": "ready"}, "vram": (1.0, 8.0)})
        w.render({"alive": False, "ok": False, "running": {}, "vram": None})

    def gui():
        SELFTEST_GUI.append(gui_check)
        run_gui()
        if errors:
            raise RuntimeError("界面运行时出错：\n" + "\n".join(errors))
    steps.append(("打开界面并切换页面", gui))
    ok = True
    for name, fn in steps:
        try:
            fn()
            say(f"[通过] {name}")
        except Exception:  # noqa: BLE001
            ok = False
            say(f"[失败] {name}\n{traceback.format_exc()}")
    say(f"selftest {'ok' if ok else 'FAILED'} {APP_VERSION}")
    if not ok:
        sys.exit(1)


def main():
    args = sys.argv[1:]
    if "--autostart" in args:
        autostart_mode()
        return
    if "--selftest" in args:  # 打包检查用：不打开窗口，只检查依赖和快捷方式相关接口
        selftest()
        return
    cmd = next((a for a in args if a in COMMANDS), None)
    try:
        run_gui(cmd)
    except Exception:
        log_error("面板运行出错")
        raise


if __name__ == "__main__":
    main()
