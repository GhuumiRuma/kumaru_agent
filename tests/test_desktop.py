"""Protect the desktop app: a server it starts stops with its window, one it did
not start keeps running, it switches to gaming settings when the graphics card is
full, and its icon and shortcuts are well formed.

Offline: the echo backend, and a stand-in for the browser window.
"""

import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ghuumi import desktop
from ghuumi.core.config import GhuumiConfig, load_config
from ghuumi.core.errors import ConfigError


@pytest.fixture
def cfg(echo_config: GhuumiConfig) -> GhuumiConfig:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return echo_config.model_copy(update={"server": echo_config.server.model_copy(update={"port": port})})


class Window:
    """Stands in for Edge: the process it starts exits at once, and the browser it
    hands over to holds the profile's lock until the user closes the window."""

    def __init__(self, url: str, profile: Path) -> None:
        self.url, self.profile = url, profile
        self.commands: list[list[str]] = []
        self.served: list[bool] = []

    def __call__(self, command: list[str]) -> "Window":
        self.commands.append(command)
        lock = desktop.window_lock(self.profile)
        lock.touch()

        def close() -> None:  # half a second later, well after wait() returned
            self.served.append(desktop.is_serving(self.url))
            lock.unlink()

        threading.Timer(0.5, close).start()
        return self

    def wait(self) -> int:
        return 0


@pytest.fixture(autouse=True)
def quick_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "WINDOW_SETTLE_S", 0.2)


def test_the_server_it_starts_stops_when_the_window_closes(cfg: GhuumiConfig, tmp_path: Path) -> None:
    url = desktop.app_url(cfg)
    window = Window(url, tmp_path)
    desktop.run_app(cfg, browser="msedge.exe", opener=window, profile=tmp_path)

    [command] = window.commands
    assert command[:3] == ["msedge.exe", f"--app={url}", f"--user-data-dir={tmp_path}"]
    assert window.served == [True]  # up while the window was open, though its first process had exited
    assert not desktop.is_serving(url)  # the model was unloaded with the window


def test_a_server_it_did_not_start_keeps_running(cfg: GhuumiConfig, tmp_path: Path) -> None:
    other = desktop.OwnServer(cfg)  # as if from `ghuumi serve` or an earlier double-click
    try:
        url = desktop.app_url(cfg)
        assert desktop.wait_until_serving(url, other.thread.is_alive)
        desktop.run_app(cfg, browser="msedge.exe", opener=Window(url, tmp_path), profile=tmp_path)
        assert desktop.is_serving(url)
    finally:
        other.stop()


def test_it_switches_to_gaming_settings_only_when_asked_or_the_card_is_full() -> None:
    cfg = load_config("qwen36", use_env=False)  # gaming below 21 GiB free
    assert desktop.settings_for(cfg, gaming=False, free_gb=22.2)[1] is False  # idle card: full speed
    gamed, switched = desktop.settings_for(cfg, gaming=False, free_gb=17.8)  # a game holds 6 GB
    assert switched and gamed.backend.llama_server.n_cpu_moe == 18
    assert desktop.settings_for(cfg, gaming=False, free_gb=None)[1] is False  # no nvidia-smi: as configured
    assert desktop.settings_for(cfg, gaming=True, free_gb=None)[1] is True  # the gaming shortcut
    with pytest.raises(ConfigError):
        desktop.settings_for(GhuumiConfig(), gaming=True, free_gb=None)  # no gaming section


def test_the_icon_is_an_ico_of_pngs() -> None:
    ico = desktop.icon_bytes((16, 32))
    assert struct.unpack_from("<HHH", ico) == (0, 1, 2)
    for index, size in enumerate((16, 32)):
        width, _, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", ico, 6 + 16 * index)
        png = ico[offset : offset + length]
        assert width == size and png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")


def test_shortcut_paths_with_quotes_and_spaces_survive(tmp_path) -> None:
    script = desktop.shortcut_script(
        [("Ghuumi", "-m ghuumi app -c qwen36", "It’s Ghuumi")], r"C:\Kim's apps\pythonw.exe", tmp_path, tmp_path,
    )  # fmt: skip
    assert r"$l.TargetPath = 'C:\Kim''s apps\pythonw.exe';" in script
    assert "$l.Description = 'It’’s Ghuumi';" in script
    assert script.count("$l.Save()") == 2  # the desktop and the Start menu
    assert desktop.profile_argument("qwen36") == "qwen36"
    assert desktop.profile_argument("my profiles/qwen36.yaml").startswith('"')  # absolute, quoted for spaces


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell's own parser, Windows only")
def test_the_shortcut_script_parses_in_powershell(tmp_path) -> None:
    script = desktop.shortcut_script([("Ghuumi", "app", "x")], r"C:\it's\pythonw.exe", tmp_path, tmp_path)
    check = (
        "$errors = $null; [void][System.Management.Automation.Language.Parser]::ParseInput("
        f"{desktop._ps_quote(script)}, [ref]$null, [ref]$errors); $errors.Count"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", check],
                          capture_output=True, text=True, timeout=60)  # fmt: skip
    assert done.stdout.strip() == "0", done.stderr
