# `scripts/overnight.ps1`

> Frees the graphics card, keeps the PC awake and runs one long job, such as a training run or a benchmark, while you sleep, then leaves a summary.

**Read this when:** you want to train or benchmark overnight on a PC you also use for games, browsers and chat apps; a long run was slow because something else held graphics memory; or you need to know exactly what the script will and won't close.

---

## What it does

1. **Checks the job can start.** `ghuumi train …` runs once with `--dry-run`. If that fails, the script stops before closing anything and shows why, for example `PyTorch is not installed`.
2. **Lists the programs holding graphics memory**: Task Manager's "Dedicated GPU memory", read per process from Windows' performance counters, with how much each holds. `-AllApps` adds every other program with a window. It asks once.
3. **Closes them.** Each of their windows gets a close request, as its X button sends; whatever is still running 20 seconds later is stopped by process id. It then prints the graphics memory in use before and after.
4. **Keeps Windows from sleeping** while the job runs (`SetThreadExecutionState`). No power setting changes: when the script ends, the normal sleep timer applies again. The screen can still turn off.
5. **Runs the job** from the Ghuumi folder, in the same window, showing how long it has run and the graphics memory in use. It writes `runs\overnight\<date-time>\`:

   | File | Holds |
   |---|---|
   | `log.txt` | The job's standard error: Ghuumi's log lines and training progress |
   | `output.txt` | The job's standard output |
   | `gpu.csv` | Graphics memory in use (dedicated, and shared with RAM) once a minute |
   | `summary.txt` | The summary below |

6. **Prints the summary** and exits with the job's exit code.

## Why it exists

Training needs graphics memory, not RAM ([TRAINING.md](../../TRAINING.md)), and everyday programs hold a lot of it. On the RTX 3090 used for this repo's measurements, a game held 4.8 GB, and a game launcher, a browser and a chat app 1.1 GB more. When a job needs more than is free, Windows doesn't stop it with an error: it lends system RAM, and training slows down sharply. Closing those programs by hand is easy to forget at bedtime, and Windows' Developer Mode doesn't help: it unlocks developer features and frees no memory.

## What it closes, and what it never closes

It closes **whole programs**. A browser's graphics process is traced up to the browser, and an embedded browser (WebView2, Steam's or Epic's web helper) up to the program that hosts it, because closing only the helper would get it restarted. A game is closed, not the launcher that started it; the launcher is closed only if it holds graphics memory itself.

| Never closed | How it's recognized |
|---|---|
| Windows itself: the desktop, taskbar, Search, Settings | Installed under `C:\Windows` |
| Services | They run in another Windows session |
| Programs it can't inspect | No readable path: system processes, and programs run as administrator |
| Graphics drivers and security software installed elsewhere | By name: `nvcontainer`, `NVDisplay.Container`, AMD's services, Microsoft Defender |
| The window it runs in | This PowerShell and every program above it: Windows Terminal, or an app whose built-in terminal you used |
| What you name | `-Keep`, matched against the program name (`Discord`) or its description (`GitHub Copilot`); wildcards work |

## Parameters

| Parameter | Type | Default | Effect |
|---|---|---|---|
| `-Run` (first, by position) | string | none | The job, in quotes, as you would type it in the Ghuumi folder. `ghuumi` means this folder's `.venv\Scripts\ghuumi.exe` when there is one, otherwise the first on your PATH. |
| `-Preview` | switch | off | Check the job and list what would be closed; close and start nothing. Without a job, only list. |
| `-AllApps` | switch | off | Also close every other program with a window |
| `-Keep` | string[] | none | Programs never to close: `-Keep Discord,Spotify` |
| `-MinGpuMB` | int | `100` | Programs holding less graphics memory than this are left alone |
| `-Yes` | switch | off | Don't ask before closing |

Exit codes: the job's own; `1` when the dry run fails or you answer no; `2` when the job is missing, unquoted or can't be found.

## How to use it

```powershell
# First, see what it would close, and check the job can start
.\scripts\overnight.ps1 -Preview "ghuumi train configs\train\lora-qwen.yaml"

# Then, before bed
.\scripts\overnight.ps1 "ghuumi train configs\train\lora-qwen.yaml"

# Keep Discord running, and run a long benchmark instead
.\scripts\overnight.ps1 -Keep Discord "ghuumi bench run gsm8k -c qwen36 --full"
```

- **Quote the job.** Unquoted, PowerShell would read the job's options, such as `-c`, as the script's own; the script refuses and shows the quoted form.
- **Start it from Windows Terminal or a PowerShell window**, not from a terminal inside an app you might close. Closing the window stops the job.
- **Leave the PC on.** The screen can turn off and the PC can lock; sleeping, shutting down or restarting it interrupts the job.

## Reading the output

A run looks like this. The programs are the ones measured on the RTX 3090 above; the times and the peak are an example.

```
>> Checking the job can start: ghuumi train configs\train\lora-qwen.yaml --dry-run
   OK

>> Programs to close
           ? MB  NVIDIA App  (NVIDIA Overlay.exe)
       4,779 MB  World of Warcraft  (Wow.exe)
         478 MB  Battle.net  (Battle.net.exe)
         422 MB  Brave Browser  (brave.exe)
         281 MB  Discord  (Discord.exe)
   Unsaved work in these programs will be lost.

Close them and start the job? [y/N]: y
...
================ Overnight run ================
Job       ghuumi train configs\train\lora-qwen.yaml
Result    finished (exit code 0)
Time      Tue 23:05 to Wed 00:41, 1h 36m
Graphics  peak 11.9 GB in use
Closed    NVIDIA App, World of Warcraft, Battle.net, Brave Browser, Discord
Logs      C:\...\runs\overnight\20261117-230512
===============================================
```

- **`? MB`**: Windows reported more memory for that program than the card has. The NVIDIA app's overlay reported 188 GB on a 24 GB card. It is still closed.
- **`Graphics peak`** is everything on the card, including Windows' own share of 1–2 GB, not just the job.
- **`it ran out and borrowed … GB of RAM`** means shared graphics memory grew by more than 1 GB during the job: the card was full, and training ran slower. Use the [out-of-memory list](../../TRAINING.md#out-of-memory-change-these-in-order), or `-AllApps`.
- **`FAILED`** prints the last 20 lines of `log.txt`; the full log is in the run folder.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `Put the whole job in quotes` | The job was given without quotes | `.\scripts\overnight.ps1 "ghuumi train …"` |
| `The dry run failed … PyTorch is not installed` | This folder's `.venv` has no training stack | `.\scripts\setup.ps1 -Gpu -Quant` |
| `Can't find the program 'ghuumi'` | No `.venv` in this folder | `.\scripts\setup.ps1` |
| `could not be closed (it may run as administrator)` | A program started as administrator can't be stopped without administrator rights | Close it yourself, or start the script as administrator |
| A closed program comes back | Something restarts it, such as NVIDIA's container restarting its overlay | `-Keep` it, or turn off its overlay or start-up setting |
| `Windows is waiting to restart for an update…` | An update is installed and Windows may restart overnight | Restart first, or pause updates in Settings > Windows Update |
| The job stopped in the night with no summary | The PC restarted, or the window was closed | A LoRA run continues from its last checkpoint: `ghuumi train <recipe> --resume <output_dir>\checkpoint-N`. A benchmark resumes when you repeat its command. |
| `Windows doesn't report graphics memory per program here` | Windows 10 before version 1709 has no per-process GPU counters | `-AllApps` |
| `log.txt` stays empty until a non-Python job ends | That program buffers its output when it writes to a file | Nothing to fix; Python jobs get `PYTHONUNBUFFERED=1` |

## Extending it

- **Another embedded browser that comes back after closing**: add its process name to `$HelperNames`, so the program hosting it is closed instead.
- **A driver or security program that must never close**: add its process name to `$ProtectedNames`.
- **Testing**: dot-sourcing the script (`. .\scripts\overnight.ps1`) loads its functions without running anything. `Get-AppRoot`, `Get-ProcessTree` and `Find-Programs` take a process table, so they can be checked against an invented one; `Close-Programs` can be checked on dummy windows you start yourself.

## Related files

- `docs/TRAINING.md` — what to run, and what it costs
- `configs/train/` — the training recipes
- `scripts/setup.ps1` — installs the training stack (`-Gpu -Quant`)
- `src/ghuumi/train/finetune_lora.py` — `--dry-run` and `--resume`
