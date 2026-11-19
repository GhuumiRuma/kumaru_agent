"""Kumaru as a desktop app: its own window, opened and closed with one click.

``kumaru app -c qwen36`` starts Kumaru's server, unless one already answers on
the profile's port, and opens the chat in an app window: Microsoft Edge or
Google Chrome without tabs or an address bar, using a browser profile of its
own so it never mixes with your browsing. Closing the window stops the server
it started, which releases the graphics card; a server it did not start keeps
running. ``kumaru app -c qwen36 --install`` puts shortcuts on the desktop and
in the Start menu that do all of this with a double-click, no terminal:

* **Kumaru**: full speed when the graphics card is free. If a game already
  fills it (less free than the profile's ``gaming.below_free_gb``), it starts
  with the profile's ``gaming`` settings instead, as an overfull card makes
  Windows move memory to system RAM and slows the model and the game to a crawl.
* **Kumaru (gaming)**: always the ``gaming`` settings (``--gaming``), leaving
  room for a game started *after* Kumaru. More of the model then runs from
  system memory, so it answers more slowly.

The shortcuts run ``pythonw -m kumaru app``, which opens no console: output goes
to ``runs/logs/kumaru-app.log``, and an error also shows a message box.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kumaru.core.config import REPO_ROOT, KumaruConfig
from kumaru.core.errors import ConfigError, KumaruError

DATA_DIR = Path.home() / ".local" / "share" / "kumaru"
WINDOW_PROFILE = DATA_DIR / "app-window"  # the app window's own browser profile
ICON = DATA_DIR / "kumaru.ico"
LOG = REPO_ROOT / "runs" / "logs" / "kumaru-app.log"
# How long the window profile's lock must stay gone before the window counts as closed.
WINDOW_SETTLE_S = 3.0
# Requests to this machine's own server must never go through a proxy.
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def app_url(cfg: KumaruConfig) -> str:
    host = cfg.server.host
    if host in ("", "0.0.0.0", "::"):  # listening everywhere: open it locally
        host = "127.0.0.1"
    elif ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{cfg.server.port}"


def is_serving(url: str, timeout_s: float = 1.0) -> bool:
    """Whether a Kumaru server answers at `url`."""
    try:
        with _LOCAL.open(f"{url}/v1/info", timeout=timeout_s) as response:
            return "version" in json.loads(response.read())
    except (OSError, ValueError):
        return False


def wait_until_serving(url: str, alive: Callable[[], bool], timeout_s: float = 120.0) -> bool:
    """Poll until a server answers; False if `alive()` turns false or time runs out."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if is_serving(url):
            return True
        if not alive():
            return False
        time.sleep(0.2)
    return False


def free_graphics_gb() -> float | None:
    """Free memory of the first NVIDIA graphics card, in GiB; None without nvidia-smi."""
    try:
        done = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console flash from pythonw
        )  # fmt: skip
        return int(done.stdout.split()[0]) / 1024
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def settings_for(cfg: KumaruConfig, *, gaming: bool, free_gb: float | None) -> tuple[KumaruConfig, bool]:
    """The settings to start with, and whether they are the gaming ones: asked
    for, or the card has less free than the profile's `gaming.below_free_gb`."""
    threshold = cfg.gaming.below_free_gb
    full = threshold is not None and free_gb is not None and free_gb < threshold
    if gaming or (full and cfg.gaming.backend):
        return cfg.with_gaming(), True
    return cfg, False


class OwnServer:
    """A Kumaru server on a thread of this process, as `kumaru serve` runs it."""

    def __init__(self, cfg: KumaruConfig) -> None:
        import uvicorn

        from kumaru.server.app import create_app

        self.server = uvicorn.Server(uvicorn.Config(
            create_app(cfg), host=cfg.server.host, port=cfg.server.port,
            log_level=cfg.telemetry.log_level.lower(), access_log=False,
        ))  # fmt: skip
        self.thread = threading.Thread(target=self.server.run, name="kumaru-server", daemon=True)
        self.thread.start()

    def stop(self, timeout_s: float = 60.0) -> None:
        """Shut down as Ctrl+C does: the model is unloaded and its memory released."""
        self.server.should_exit = True
        self.thread.join(timeout_s)


def find_browser() -> str | None:
    """A browser that opens app windows: Edge first (every Windows PC has it), then Chrome."""
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        for exe in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            for root in filter(None, roots):
                if (path := Path(root) / exe).is_file():
                    return str(path)
        return None
    for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        if found := shutil.which(name):
            return found
    for name in ("Microsoft Edge", "Google Chrome"):
        if (path := Path(f"/Applications/{name}.app/Contents/MacOS/{name}")).is_file():
            return str(path)
    return None


def window_command(browser: str, url: str, profile: Path = WINDOW_PROFILE) -> list[str]:
    return [
        browser, f"--app={url}", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check",
        # A fresh profile still gets extensions that other programs register for
        # every profile (password managers open their own windows), and sync.
        "--disable-extensions", "--disable-sync",
        # So the browser process ends with its window.
        "--disable-background-mode",
        "--window-size=1280,880",
    ]  # fmt: skip


def window_lock(profile: Path) -> Path:
    """The file a Chromium browser holds while it runs on `profile`; gone once it exits."""
    return profile / ("lockfile" if sys.platform == "win32" else "SingletonLock")


def wait_for_window(process: Any, profile: Path, start_timeout_s: float = 30.0) -> None:
    """Return once the browser showing the app window has exited.

    Not the process started: Edge and Chrome may hand the launch to another
    process of theirs and exit at once. Whichever browser runs the profile
    holds its lock, so wait for the lock to appear, then to stay gone for
    WINDOW_SETTLE_S (a browser relaunching itself drops it for a moment).
    """
    lock = window_lock(profile)
    deadline = time.monotonic() + start_timeout_s
    while not os.path.lexists(lock):
        if time.monotonic() > deadline:  # no lock to watch: wait on the process instead
            process.wait()
            return
        time.sleep(0.25)
    gone_since = None
    while True:
        if os.path.lexists(lock):
            gone_since = None
        elif gone_since is None:
            gone_since = time.monotonic()
        elif time.monotonic() - gone_since >= WINDOW_SETTLE_S:
            return
        time.sleep(0.25)


def notify(message: str, *, error: bool = False) -> None:
    """A message box on Windows, waiting for OK; a line on stderr elsewhere."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Kumaru", 0x10 if error else 0x40)
    else:
        print(message, file=sys.stderr)


def run_app(
    cfg: KumaruConfig, *, browser: str | None = None, opener: Callable[[list[str]], Any] = subprocess.Popen,
    profile: Path = WINDOW_PROFILE,
) -> None:
    """Open the app window; return when it closes, stopping the server if this started it."""
    url = app_url(cfg)
    own = None
    if not is_serving(url):
        own = OwnServer(cfg)
        if not wait_until_serving(url, own.thread.is_alive):
            own.stop()
            own = None
            # A second double-click can lose the race for the port to the first.
            if not wait_until_serving(url, lambda: True, timeout_s=10):
                raise KumaruError(
                    f"Kumaru's server did not start at {url}.",
                    hint=f"Is another program using port {cfg.server.port}? Details: {LOG}",
                )
    try:
        browser = browser or find_browser()
        if browser:
            wait_for_window(opener(window_command(browser, url, profile)), profile)
            return
        webbrowser.open(url)
        if own is None:
            return
        if sys.platform == "win32":
            notify(f"Kumaru is open in your browser at {url}.\n\nClick OK to stop Kumaru.")
        else:
            print(f"Kumaru is open in your browser at {url}. Press Ctrl+C to stop it.", file=sys.stderr)
            with contextlib.suppress(KeyboardInterrupt):
                threading.Event().wait()
    finally:
        if own is not None:
            own.stop()


def log_if_windowless() -> Path | None:
    """pythonw, which the shortcuts use, has no console: send output to LOG.

    Returns LOG when it did; None when there is a console to write to.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return None
    LOG.parent.mkdir(parents=True, exist_ok=True)
    fresh = LOG.exists() and LOG.stat().st_size > 5_000_000
    stream = open(LOG, "w" if fresh else "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - open until exit
    stream.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} kumaru {' '.join(sys.argv[1:])}\n")
    sys.stdout = sys.stderr = stream
    return LOG


def icon_bytes(sizes: tuple[int, ...] = (16, 24, 32, 48, 256)) -> bytes:
    """Kumaru's mark, the chat page's ◈, as a Windows .ico of PNG images."""
    images = [_png(size, _mark(size)) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = data = b""
    for size, png in zip(sizes, images, strict=True):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    return header + entries + data


def _mark(size: int, samples: int = 4) -> list[bytes]:
    """RGBA rows: an outlined diamond around a small solid one, in the UI's
    accent, on its dark rounded tile; edges antialiased by supersampling."""
    tile_rgb, mark_rgb = (0x17, 0x1B, 0x21), (0x6E, 0xA8, 0xFE)
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            tile = mark = 0
            for sy in range(samples):
                for sx in range(samples):
                    x = (px + (sx + 0.5) / samples) / size - 0.5
                    y = (py + (sy + 0.5) / samples) / size - 0.5
                    dx, dy = max(abs(x) - 0.28, 0.0), max(abs(y) - 0.28, 0.0)
                    if dx * dx + dy * dy <= 0.22**2:  # a square with corners of radius 0.22
                        tile += 1
                        d = abs(x) + abs(y)
                        mark += 0.25 <= d <= 0.34 or d <= 0.11
            share = mark / tile if tile else 0.0
            rgb = (round(t + (m - t) * share) for t, m in zip(tile_rgb, mark_rgb, strict=True))
            row += bytes((*rgb, round(255 * tile / samples**2)))
        rows.append(bytes(row))
    return rows


def _png(size: int, rows: list[bytes]) -> bytes:
    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    raw = b"".join(b"\x00" + row for row in rows)  # filter type 0 on every row
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _ps_quote(text: str) -> str:
    """A PowerShell single-quoted string; PowerShell also reads curly quotes as quotes."""
    return "'" + re.sub("['\u2018\u2019\u201a\u201b]", lambda m: m.group() * 2, text) + "'"


def shortcut_script(shortcuts: list[tuple[str, str, str]], target: str, icon: Path, cwd: Path) -> str:
    """PowerShell writing each (name, arguments, description) to the desktop and the
    Start menu, then printing each shortcut's path."""
    lines = ["$shell = New-Object -ComObject WScript.Shell"]
    for name, arguments, description in shortcuts:
        for folder in ("Desktop", "Programs"):
            lines.append(
                f"$l = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('{folder}'))"
                f" {_ps_quote(name + '.lnk')})); $l.TargetPath = {_ps_quote(target)};"
                f" $l.Arguments = {_ps_quote(arguments)}; $l.WorkingDirectory = {_ps_quote(str(cwd))};"
                f" $l.IconLocation = {_ps_quote(str(icon))}; $l.Description = {_ps_quote(description)};"
                " $l.Save(); $l.FullName"
            )
    return "\n".join(lines)


def profile_argument(config: str) -> str:
    """How a shortcut names the profile: a name as given, a file by absolute path."""
    path = Path(config)
    named = path.suffix.lower() not in (".yaml", ".yml") and len(path.parts) == 1
    text = config if named else str(path.resolve())
    return f'"{text}"' if " " in text else text


def install_shortcuts(profile: str, *, gaming: bool) -> list[str]:
    """Write the icon and the shortcuts; returns the shortcuts' paths."""
    if sys.platform != "win32":
        raise ConfigError(
            "Desktop shortcuts are made for Windows only.",
            hint="Elsewhere, start `kumaru app -c PROFILE` from a launcher of your own.",
        )
    ICON.parent.mkdir(parents=True, exist_ok=True)
    ICON.write_bytes(icon_bytes())
    python = Path(sys.executable)
    target = python.with_name("pythonw.exe") if python.with_name("pythonw.exe").is_file() else python
    arguments = f"-m kumaru app -c {profile}"
    shortcuts = [("Kumaru", arguments, "Kumaru, your local AI, in its own window.")]
    if gaming:
        shortcuts.append((
            "Kumaru (gaming)", f"{arguments} --gaming",
            "Kumaru, leaving room on the graphics card for a game started afterwards. Answers more slowly.",
        ))  # fmt: skip
    script = shortcut_script(shortcuts, str(target), ICON, REPO_ROOT)
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    if done.returncode != 0:
        raise KumaruError("Could not create the shortcuts.", hint=done.stderr.strip()[-500:])
    return done.stdout.splitlines()
