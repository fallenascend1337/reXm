"""reXm - Sony XM headset app for Windows and Linux: pywebview window (ui/index.html) + RFCOMM link + tray.

Run:  python rexm.py      (--debug for devtools, --background to start in the tray)
Bluetooth lives in bt_windows.py (WinRT) / bt_linux.py (BlueZ sockets); everything else is shared.
"""
import asyncio
import collections
import json
import logging
import logging.handlers
import os
import webbrowser
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import webview
from PIL import Image, ImageDraw

from sony import ACK, CMD1, frame, parse

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import bt_windows as bt
else:
    import bt_linux as bt

FROZEN = getattr(sys, "frozen", False)  # running as the PyInstaller-built reXm.exe
HERE = Path(__file__).resolve().parent
RES = Path(getattr(sys, "_MEIPASS", HERE))  # bundled files (ui/)
if WINDOWS:
    DATA = Path(os.environ["APPDATA"]) / "reXm"  # writable: profiles, log, icon
    STARTUP_FILE = Path(os.environ["APPDATA"]) / r"Microsoft\Windows\Start Menu\Programs\Startup\reXm.lnk"
    ICON_FILE = DATA / "rexm.ico"
else:
    _config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    DATA = _config / "reXm"
    STARTUP_FILE = _config / "autostart" / "rexm.desktop"
    ICON_FILE = DATA / "rexm.png"
DATA.mkdir(parents=True, exist_ok=True)
PROFILES_FILE = DATA / "profiles.json"
LOG_FILE = DATA / "rexm.log"

# Debug log: rotating file + the last 500 lines in memory for the in-app viewer
log = logging.getLogger("rexm")
log.setLevel(logging.DEBUG)
LOG_LINES = collections.deque(maxlen=500)


class _MemoryHandler(logging.Handler):
    def emit(self, record):
        LOG_LINES.append(self.format(record))


for _h in (logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=1, encoding="utf-8"),
           _MemoryHandler()):
    _h.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(message)s", "%H:%M:%S"))
    log.addHandler(_h)

# Sony control services: v2 = XM5 generation (WH-1000XM5/XM6, WF-1000XM5), v1 = XM3/XM4 generation
SONY_V2 = uuid.UUID("956C7B26-D49A-4BA8-B03F-B17D393CB6E2")
SONY_V1 = uuid.UUID("96CC203E-5068-46AD-9B95-B9C2E2AB3A4A")
CMD2 = 0x0E

CODECS = {0x01: "SBC", 0x02: "AAC", 0x10: "LDAC", 0x20: "aptX", 0x21: "aptX HD"}
# auto power off (27 05 a b): the XM5 only accepts these two
POWER_OFF = {(0x10, 0x00): "taken_off", (0x11, 0x00): "never"}
# NC/AMB button cycle (fb 03 01 35 01 00 <mode>)
BUTTON_MODES = {0x01: "nc_amb_off", 0x02: "nc_amb", 0x03: "nc_off", 0x04: "amb_off"}

# Everything we ask for after connecting (type, payload)
QUERIES = [
    (CMD1, [0x04, 0x01]), (CMD1, [0x04, 0x02]),  # model, firmware
    (CMD1, [0x22, 0x00]), (CMD1, [0x12, 0x02]),  # battery, codec
    (CMD1, [0x66, 0x17]), (CMD1, [0x56, 0x00]),  # sound mode, EQ
    (CMD1, [0xF6, 0x0C]), (CMD1, [0xFA, 0x0C]),  # speak-to-chat + config
    (CMD1, [0xF6, 0x01]), (CMD1, [0xD6, 0xD1]),  # pause when taken off, touch panel
    (CMD1, [0xE6, 0x01]), (CMD1, [0xE6, 0x02]),  # DSEE, connection priority
    (CMD1, [0x26, 0x05]), (CMD2, [0x46, 0x01]),  # auto power off, voice prompts
    (CMD1, [0xFA, 0x03]),                        # NC/AMB button cycle
]

DEFAULT_PROFILES = [
    {"name": "Music", "icon": "music", "mode": "nc", "chat": False, "dsee": True, "quality": True},
    {"name": "Gaming", "icon": "game", "mode": "nc", "chat": False, "dsee": False, "quality": False,
     "eq": {"preset": 0xA0, "bass": 3, "bands": [-2, 0, 4, 6, 3]}},
    {"name": "Silent", "icon": "moon", "mode": "nc", "chat": False, "voice_prompts": False},
    {"name": "Guests", "icon": "people", "mode": "ambient", "level": 20, "voice": True, "chat": True},
]


class Link:
    """Persistent connection. Every incoming message updates `state` and is pushed to the UI."""

    def __init__(self, push):
        self.push = push
        self.state = {"connected": False, "connection": "Bluetooth", "platform": "windows" if WINDOWS else "linux"}
        self.loop = asyncio.new_event_loop()
        self.transport = None
        self.seq = 0
        self.acked = asyncio.Event()
        self.lock = None

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self._run(), self.loop)

    def call(self, coro):
        asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def _run(self):
        self.lock = asyncio.Lock()
        while True:
            try:
                await self._connect()
                log.info("connected to %s (%s)", self.state.get("name"), self.state.get("mac"))
                self._set(connected=True, error=None)
                await self.send(CMD1, [0x00, 0x00])
                for t, p in QUERIES:
                    await self.send(t, p)
                # keep battery fresh; the read task raises when the headset goes away
                while not self._read_task.done():
                    await asyncio.wait([self._read_task], timeout=120)
                    if not self._read_task.done():
                        await self.send(CMD1, [0x22, 0x00])
                await self._read_task
            except Exception as e:
                err = str(e).split("\r")[0]
                if err != self.state.get("error"):  # don't repeat the same error every retry
                    log.warning("not connected: %s", err)
                self._set(connected=False, error=err)
            self._close()
            await asyncio.sleep(4)  # retry until the headphones come back

    async def _connect(self):
        """Pick a paired Sony headset that offers a control service (connected, newest protocol first)."""
        found = await bt.find([(2, SONY_V2), (1, SONY_V1)])
        if not found:
            raise OSError("No paired Sony headset found")
        found.sort(key=lambda f: (f[0], f[1].proto), reverse=True)
        connected, tr = found[0]
        self._set(name=tr.name, mac=tr.mac, protocol=tr.proto)
        if not connected:
            raise OSError(f"{tr.name} not connected")
        if tr.proto == 1:
            raise OSError(f"{tr.name} uses Sony's older protocol (XM3/XM4), not supported yet")
        await tr.connect(SONY_V2)
        self.transport = tr
        self.seq = 0
        self._read_task = asyncio.ensure_future(self._read_loop())

    def _close(self):
        if self.transport:
            self.transport.close()
            self.transport = None

    # ---- wire ------------------------------------------------------------
    async def _write(self, data):
        await self.transport.write(data)

    async def send(self, msg_type, payload):
        """Send one command and wait for its ACK (the headphones want one at a time)."""
        if not self.transport:
            return
        async with self.lock:
            self.acked.clear()
            log.debug("TX %02X %s", msg_type, bytes(payload).hex(" "))
            await self._write(frame(msg_type, self.seq, bytes(payload)))
            try:
                await asyncio.wait_for(self.acked.wait(), 2)
            except asyncio.TimeoutError:
                log.warning("no ACK for %s", bytes(payload).hex(" "))

    async def _read_loop(self):
        buf = b""
        while True:
            data = await self.transport.read()
            if not data:
                raise OSError("Headphones disconnected")
            buf += data
            end = buf.rfind(b"\x3c")
            if end < 0:
                continue
            chunk, buf = buf[:end + 1], buf[end + 1:]
            for t, seq, p in parse(chunk):
                if t == ACK:
                    self.seq = seq
                    self.acked.set()
                else:
                    log.debug("RX %02X %s", t, p.hex(" "))
                    await self._write(frame(ACK, 1 - seq, b""))
                    try:
                        self._handle(t, p)
                    except Exception:
                        log.exception("could not decode %s", p.hex(" "))

    # ---- decoding --------------------------------------------------------
    def _set(self, **kv):
        self.state.update(kv)
        self.push(self.state)

    def _handle(self, t, p):
        if len(p) < 2:
            return
        c, sub = p[0], p[1]
        if t == CMD2:
            if c in (0x47, 0x49) and sub == 0x01:
                self._set(voice_prompts=p[2] == 0)
            return
        if c == 0x05 and sub in (0x01, 0x02):
            text = p[3:3 + p[2]].decode(errors="replace")
            self._set(**{"model" if sub == 0x01 else "firmware": text})
        elif c in (0x23, 0x25) and sub == 0x00:
            self._set(battery=p[2], charging=bool(p[3]))
        elif c in (0x13, 0x15) and sub == 0x02:
            self._set(codec=CODECS.get(p[2], f"0x{p[2]:02X}"))
        elif c in (0x67, 0x69) and sub == 0x17:
            mode = "off" if not p[3] else ("ambient" if p[4] else "nc")
            self._set(mode=mode, voice=bool(p[5]), level=p[6])
        elif c in (0x57, 0x59) and sub == 0x00:
            n = p[3]
            vals = [v - 10 for v in p[4:4 + n]]
            self._set(eq_preset=p[2], eq_bass=vals[0] if vals else 0, eq_bands=vals[1:])
        elif c in (0xF7, 0xF9) and sub == 0x0C:
            self._set(chat=p[2] == 0)
        elif c in (0xF7, 0xF9) and sub == 0x01:
            self._set(pause_off=p[2] == 0)
        elif c in (0xFB, 0xFD) and sub == 0x0C:
            self._set(chat_sens=p[2], chat_timeout=p[3])
        elif c in (0xFB, 0xFD) and sub == 0x03 and len(p) >= 7:
            self._set(nc_button=BUTTON_MODES.get(p[6], "unknown"))
        elif c in (0xD7, 0xD9) and sub == 0xD1:
            self._set(touch=p[3] == 0)
        elif c in (0xE7, 0xE9) and sub == 0x01:
            self._set(dsee=p[2] == 1)
        elif c in (0xE7, 0xE9) and sub == 0x02:
            self._set(quality=p[2] == 0)
        elif c in (0x27, 0x29) and sub == 0x05:
            self._set(power_off=POWER_OFF.get((p[2], p[3]), "unknown"))


def load_profiles():
    try:
        return json.loads(PROFILES_FILE.read_text())
    except (OSError, ValueError):
        return [dict(p) for p in DEFAULT_PROFILES]


class Api:
    """Methods the web UI calls (window.pywebview.api.*). Attributes are _private so pywebview won't crawl them."""

    def __init__(self):
        self._window = None
        self._tray = None
        self._warned = 100
        self._link = Link(self._push)

    def _push(self, state):
        if "--debug" in sys.argv:
            print(json.dumps(state), flush=True)
        self._battery_alert(state)
        if self._tray:
            self._tray.update_menu()
        if self._window:
            self._window.evaluate_js(f"window.onState({json.dumps(state)})")

    def _battery_alert(self, state):
        b = state.get("battery")
        if b is None or not state.get("connected"):
            return
        if b > 25:
            self._warned = 100
        for limit in (10, 20):
            if b <= limit < self._warned:
                self._warned = limit
                self._notify(f"Battery at {b}%", f"Your {state.get('model', 'headset')} needs charging soon.")
                break

    def _notify(self, title, msg):
        if self._tray:
            try:
                self._tray.notify(msg, title)
            except Exception:
                pass

    def _cmd(self, *payload, t=CMD1):
        self._link.call(self._link.send(t, list(payload)))

    def get_state(self):
        return self._link.state

    def set_mode(self, mode, voice=False, level=None):
        lvl = max(1, min(20, int(level or self._link.state.get("level") or 20)))
        self._cmd(0x68, 0x17, 0x01, int(mode != "off"), int(mode == "ambient"), int(bool(voice)), lvl)

    def set_eq_preset(self, preset):
        self._cmd(0x58, 0x00, int(preset), 0x00)

    def set_eq_bands(self, bass, bands):
        self._cmd(0x58, 0x00, 0xA0, 0x06, int(bass) + 10, *[int(b) + 10 for b in bands])

    def set_toggle(self, name, on):
        on = bool(on)
        if name == "chat":
            self._cmd(0xF8, 0x0C, 0 if on else 1, 0x01)
        elif name == "pause_off":
            self._cmd(0xF8, 0x01, 0 if on else 1)
        elif name == "dsee":
            self._cmd(0xE8, 0x01, 1 if on else 0)
        elif name == "quality":
            self._cmd(0xE8, 0x02, 0 if on else 1)
        elif name == "voice_prompts":
            self._cmd(0x48, 0x01, 0 if on else 1, t=CMD2)

    def set_chat_config(self, sens, timeout):
        self._cmd(0xFC, 0x0C, int(sens), int(timeout))

    def set_power_off(self, key):
        code = {v: k for k, v in POWER_OFF.items()}.get(key)
        if code:
            self._cmd(0x28, 0x05, *code)

    def set_nc_button(self, key):
        code = {v: k for k, v in BUTTON_MODES.items()}.get(key)
        if code:
            self._cmd(0xFC, 0x03, 0x01, 0x35, 0x01, 0x00, code)

    def refresh(self):
        for t, p in QUERIES:
            self._link.call(self._link.send(t, p))

    # ---- profiles ----------------------------------------------------------
    def get_profiles(self):
        return load_profiles()

    def _save_profiles(self, profiles):
        PROFILES_FILE.write_text(json.dumps(profiles, indent=2))
        return profiles

    def apply_profile(self, name):
        prof = next((p for p in load_profiles() if p["name"] == name), None)
        if not prof:
            return
        st = self._link.state
        if "mode" in prof:
            self.set_mode(prof["mode"], prof.get("voice", st.get("voice")), prof.get("level", st.get("level")))
        if "eq" in prof:
            eq = prof["eq"]
            if eq["preset"] == 0xA0:
                self.set_eq_bands(eq["bass"], eq["bands"])
            else:
                self.set_eq_preset(eq["preset"])
        for key in ("chat", "dsee", "quality", "voice_prompts", "pause_off"):
            if key in prof:
                self.set_toggle(key, prof[key])
        self._link.state["profile"] = name
        self._notify(f"{name} profile", f"Applied to your {st.get('model', 'headset')}")

    def save_profile(self, name, icon="star"):
        st = self._link.state
        prof = {"name": name, "icon": icon}
        for key in ("mode", "voice", "level", "chat", "dsee", "quality", "voice_prompts"):
            if key in st:
                prof[key] = st[key]
        if "eq_preset" in st:
            prof["eq"] = {"preset": st["eq_preset"], "bass": st.get("eq_bass", 0), "bands": st.get("eq_bands", [0] * 5)}
        profiles = [p for p in load_profiles() if p["name"] != name] + [prof]
        return self._save_profiles(profiles)

    def delete_profile(self, name):
        return self._save_profiles([p for p in load_profiles() if p["name"] != name])

    # ---- app ---------------------------------------------------------------
    def get_log(self):
        return list(LOG_LINES)

    def open_log(self):
        if WINDOWS:
            os.startfile(LOG_FILE)
        else:
            subprocess.Popen(["xdg-open", str(LOG_FILE)])

    def open_url(self, url):
        if url.startswith(("https://", "mailto:")):
            webbrowser.open(url)

    def get_startup(self):
        return STARTUP_FILE.exists()

    def set_startup(self, on):
        if on:
            make_shortcut(STARTUP_FILE, "--background")
        elif STARTUP_FILE.exists():
            STARTUP_FILE.unlink()
        return STARTUP_FILE.exists()

    def start_resize(self, edge):
        """The window is frameless, so the UI's edge handles hand the resize to us."""
        if WINDOWS:
            threading.Thread(target=_win_resize_drag, args=(self._window, edge), daemon=True).start()
        else:
            from gi.repository import Gdk, GLib
            e = Gdk.WindowEdge
            edges = {"n": e.NORTH, "s": e.SOUTH, "e": e.EAST, "w": e.WEST,
                     "ne": e.NORTH_EAST, "nw": e.NORTH_WEST, "se": e.SOUTH_EAST, "sw": e.SOUTH_WEST}

            def go():
                _, x, y = Gdk.Display.get_default().get_default_seat().get_pointer().get_position()
                self._window.native.begin_resize_drag(edges[edge], 1, x, y, Gdk.CURRENT_TIME)
            GLib.idle_add(go)

    def toggle_maximize(self):
        if WINDOWS:
            _win_toggle_maximize(self._window)
        else:
            from gi.repository import GLib
            win = self._window.native
            GLib.idle_add(win.unmaximize if win.is_maximized() else win.maximize)

    def minimize(self):
        self._window.minimize()

    def close(self):
        self._window.hide()  # keep running in the tray

    def show(self):
        self._window.show()
        self._window.restore()

    def quit(self):
        if self._tray:
            self._tray.stop()
        self._window.destroy()


# ---- Windows: resizing / maximising a borderless window -------------------
MIN_W, MIN_H = 860, 600
_restore_rect = {}


def _hwnd(window):
    return int(window.native.Handle.ToInt64())


def _win_resize_drag(window, edge):
    """Follow the mouse while the left button is held, moving the chosen edges (Win32, any thread)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    hwnd = _hwnd(window)
    r, p = wintypes.RECT(), wintypes.POINT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    u.GetCursorPos(ctypes.byref(p))
    left, top, right, bottom, sx, sy = r.left, r.top, r.right, r.bottom, p.x, p.y
    while u.GetAsyncKeyState(0x01) & 0x8000:  # VK_LBUTTON held
        u.GetCursorPos(ctypes.byref(p))
        dx, dy = p.x - sx, p.y - sy
        l, t, rr, b = left, top, right, bottom
        if "w" in edge: l = min(left + dx, right - MIN_W)
        if "e" in edge: rr = max(right + dx, left + MIN_W)
        if "n" in edge: t = min(top + dy, bottom - MIN_H)
        if "s" in edge: b = max(bottom + dy, top + MIN_H)
        u.SetWindowPos(hwnd, 0, l, t, rr - l, b - t, 0x0004 | 0x0010)  # NOZORDER | NOACTIVATE
        time.sleep(0.012)


def _win_toggle_maximize(window):
    """Fill the monitor's work area (not over the taskbar), or put the window back."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    hwnd = _hwnd(window)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    if hwnd in _restore_rect:
        l, t, r, b = _restore_rect.pop(hwnd)
    else:
        cur = wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(cur))
        _restore_rect[hwnd] = (cur.left, cur.top, cur.right, cur.bottom)
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        u.GetMonitorInfoW(u.MonitorFromWindow(hwnd, 2), ctypes.byref(mi))
        w = mi.rcWork
        l, t, r, b = w.left, w.top, w.right, w.bottom
    u.SetWindowPos(hwnd, 0, l, t, r - l, b - t, 0x0004)


# ---- icon, shortcuts, tray -------------------------------------------------
def make_icon(size=256):
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(14 * s), fill=(20, 23, 31, 255))
    orange = (255, 122, 47, 255)
    d.arc([12 * s, 10 * s, 52 * s, 50 * s], 180, 360, fill=orange, width=int(5 * s))
    d.rounded_rectangle([9 * s, 28 * s, 21 * s, 50 * s], radius=int(6 * s), fill=orange)
    d.rounded_rectangle([43 * s, 28 * s, 55 * s, 50 * s], radius=int(6 * s), fill=orange)
    return img


def ensure_icon():
    if ICON_FILE.exists():
        return
    if WINDOWS:
        make_icon().save(ICON_FILE, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    else:
        make_icon().save(ICON_FILE)


def make_shortcut(lnk, args=""):
    """Create a launcher for reXm: a .lnk on Windows (no console window), a .desktop entry on Linux."""
    ensure_icon()
    if not WINDOWS:
        cmd = f'"{sys.executable}"' if FROZEN else f'"{sys.executable}" "{HERE / "rexm.py"}"'
        lnk.parent.mkdir(parents=True, exist_ok=True)
        lnk.write_text("[Desktop Entry]\nType=Application\nName=reXm\nComment=Sony XM headset control\n"
                       f"Exec={cmd} {args}\nIcon={ICON_FILE}\nTerminal=false\nCategories=AudioVideo;Settings;\n")
        lnk.chmod(0o755)
        return
    if FROZEN:
        target, arguments, icon = Path(sys.executable), args, Path(sys.executable)
    else:
        target, arguments, icon = Path(sys.executable).with_name("pythonw.exe"), f'"{HERE / "rexm.py"}" {args}', ICON_FILE
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{target}';$s.Arguments='{arguments}';"
          f"$s.WorkingDirectory='{target.parent}';$s.IconLocation='{icon}';$s.Description='reXm';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)


def make_tray(api):
    import pystray  # needs a display, so only load it when the tray is actually created

    st = api._link.state

    def battery_text(_):
        name = st.get("model") or st.get("name") or "Headset"
        if not st.get("connected"):
            return f"{name}: not connected"
        return f"{name}: {st.get('battery', '?')}%" + (" (charging)" if st.get("charging") else "")

    def mode_item(label, mode):
        return pystray.MenuItem(label, lambda: api.set_mode(mode, st.get("voice"), st.get("level")),
                                checked=lambda _: st.get("mode") == mode, radio=True,
                                enabled=lambda _: st.get("connected", False))

    def profile_menu():
        return [pystray.MenuItem(p["name"], (lambda n: lambda: api.apply_profile(n))(p["name"]),
                                 checked=(lambda n: lambda _: st.get("profile") == n)(p["name"]), radio=True)
                for p in load_profiles()]

    menu = pystray.Menu(
        pystray.MenuItem(battery_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        mode_item("Noise cancelling", "nc"),
        mode_item("Ambient sound", "ambient"),
        mode_item("Off", "off"),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Profiles", pystray.Menu(lambda: profile_menu())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open reXm", lambda: api.show(), default=True),
        pystray.MenuItem("Start with Windows" if WINDOWS else "Start on login",
                         lambda: api.set_startup(not api.get_startup()),
                         checked=lambda _: api.get_startup()),
        pystray.MenuItem("Quit", lambda: api.quit()),
    )
    return pystray.Icon("reXm", make_icon(64), "reXm", menu)


def main():
    log.info("reXm starting (Python %s)", sys.version.split()[0])
    ensure_icon()
    api = Api()
    api._window = webview.create_window(
        "reXm", str(RES / "ui" / "index.html"), js_api=api,
        width=1060, height=720, min_size=(860, 600), frameless=True, easy_drag=False,
        background_color="#0B0D12", hidden="--background" in sys.argv)
    api._tray = make_tray(api)
    api._tray.run_detached()
    webview.start(api._link.start, debug="--debug" in sys.argv, icon=str(ICON_FILE))


if __name__ == "__main__":
    main()
