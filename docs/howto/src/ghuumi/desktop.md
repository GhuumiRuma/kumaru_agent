# `src\ghuumi\desktop.py`

> Ghuumi as a desktop app: a double-click opens the chat in its own window and starts the model; closing the window stops the model and frees the graphics card.

**Read this when:** you install the desktop shortcuts, a shortcut does nothing or shows an error, or you want to know which settings the app started with.

---

## What it does

```powershell
ghuumi app -c qwen36 --install   # once: "Ghuumi" and "Ghuumi (gaming)" on the desktop and in the Start menu
ghuumi app -c qwen36             # what the "Ghuumi" shortcut runs
ghuumi app -c qwen36 --gaming    # what "Ghuumi (gaming)" runs
```

1. **Settings.** With `--gaming`, the profile's `gaming` settings ([config](core/config.md)). Without it, `free_graphics_gb()` asks `nvidia-smi` how much graphics memory is free; below the profile's `gaming.below_free_gb` (qwen36: 21 GiB, a game is running), the gaming settings too. The log says which, and why.
2. **Server.** If a Ghuumi server already answers on the profile's port, it is used as it is. Otherwise `OwnServer` runs one on a thread of this process, as `ghuumi serve` would; the model loads in the background while the page shows its progress.
3. **Window.** `find_browser()` takes Microsoft Edge (on every Windows PC), else Google Chrome, and opens the page with `--app`: a window without tabs or address bar. It has its own browser profile, `~/.local/share/ghuumi/app-window`, with extensions and sync off, so it never mixes with your browsing and password managers cannot pop up in it. With neither browser, the default browser opens the page, and a message box (Windows) or Ctrl+C stops Ghuumi.
4. **Closing.** `wait_for_window()` returns when the browser on that profile exits. Then `OwnServer.stop()` shuts down as Ctrl+C does: the model is unloaded and its memory released, in about 5 seconds. A server the app did not start keeps running.

The shortcuts run `pythonw.exe -m ghuumi app`, which opens no console window. `log_if_windowless()` then sends all output to `runs\logs\ghuumi-app.log`, and an error also shows a message box naming that log. The icon (`~/.local/share/ghuumi/ghuumi.ico`) is drawn by `icon_bytes()`: the chat page's ◈ in the UI's colours.

## Why it exists

- **No terminal.** Starting Ghuumi needed a PowerShell command and a browser tab, and stopping it needed Ctrl+C in the right window. Now it is an icon and a window's close button.
- **The graphics card comes back.** The model holds about 20 GB of graphics memory while it runs. Tying it to the window means closing Ghuumi gives that memory back to games.
- **It never overfills the card.** Starting the full model beside a running game made Windows spill memory into system RAM, and llama-server stalled while warming up (measured). The automatic switch to the gaming settings prevents that.

## Mental model

- **Which icon?** "Ghuumi" for full speed (about 110 tokens a second on an RTX 3090), slowing down by itself if a game already holds the card. "Ghuumi (gaming)" when you will start a game *after* Ghuumi: it always leaves about 10 GB free and answers at about 15-27 tokens a second.
- **Why not wait on the browser process it starts?** Edge hands the launch to another process of its own and exits at once (measured: the first version stopped the model a second after opening). The browser running a profile holds the file `lockfile` in it (`SingletonLock` on Linux and macOS) and Windows deletes it when that browser exits, even when it is killed. So the app waits for that file to appear, then to stay gone for `WINDOW_SETTLE_S` (3 s), in case the browser relaunches itself.
- **Two double-clicks** open two windows on one server: the second finds the server running and only opens a window. If both start at the same moment, the one that loses the port waits for the other's server.
- The shortcuts point at this folder's Python environment. After moving Ghuumi, or to use another copy, run `--install` again from there.

## Public API

| Name | What it's for |
|---|---|
| `run_app(cfg, *, browser=None, opener=subprocess.Popen, profile=WINDOW_PROFILE)` | Steps 2-4. `browser`, `opener` and `profile` let tests stand in for Edge. |
| `settings_for(cfg, *, gaming, free_gb) -> (cfg, switched)` | Step 1, without the measuring. |
| `free_graphics_gb() -> float \| None` | Free memory of the first NVIDIA card in GiB, via `nvidia-smi` without a console flash; `None` without one. |
| `OwnServer(cfg)`, `.stop()` | A uvicorn server on a daemon thread. |
| `is_serving(url)`, `wait_until_serving(url, alive)`, `app_url(cfg)` | Is Ghuumi up at this address (`/v1/info`, never through a proxy)? |
| `find_browser()`, `window_command(browser, url, profile)`, `window_lock(profile)`, `wait_for_window(process, profile)` | The app window. |
| `install_shortcuts(profile, *, gaming) -> list[str]`, `shortcut_script(...)`, `profile_argument(config)` | The shortcuts, written by PowerShell's `WScript.Shell`. A profile file is stored by its absolute path. |
| `icon_bytes(sizes)`, `log_if_windowless()`, `notify(message, *, error)` | Icon, log and message box. |

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| A message box: "Ghuumi's server did not start" | Another program uses the profile's port (8080 for qwen36). | Close it, or change `server.port`. |
| A message box: "This profile has no gaming settings" | `--gaming` with a profile without a `gaming:` section. | Add one, as `configs\qwen36.yaml` has. |
| The window says the page cannot be reached | The model failed to load, so the server stopped; or the window outlived a crashed app. | Read `runs\logs\ghuumi-app.log` and the llama-server log it names; close the window and open Ghuumi again. |
| Slow answers and a stuttering game | The game started after "Ghuumi" (full settings) and the card overflowed. | Close Ghuumi and use "Ghuumi (gaming)". |
| `--install`: "Desktop shortcuts are made for Windows only" | Another operating system. | Start `ghuumi app -c PROFILE` from a launcher of your own. |

## Related files

- [`cli.md`](cli.md): the `ghuumi app` command.
- [`__main__.md`](__main__.md): `python -m ghuumi`, which the shortcuts run.
- [`core/config.md`](core/config.md): the `gaming` section.
- [`../../configs/qwen36.yaml.md`](../../configs/qwen36.yaml.md#share-the-gpu-with-a-game-or-use-a-smaller-card): the measured gaming recipe.
- [`server/app.md`](server/app.md): the server it runs.
