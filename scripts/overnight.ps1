# Free the graphics card, keep the PC awake, and run one long job -- a
# training run, a benchmark -- while you sleep.
#
#   .\scripts\overnight.ps1 "kumaru train configs\train\lora-qwen.yaml"
#   .\scripts\overnight.ps1 -Preview "kumaru train configs\train\lora-qwen.yaml"
#   .\scripts\overnight.ps1 -Keep Discord "kumaru bench run gsm8k -c qwen36 --full"
#   .\scripts\overnight.ps1 -AllApps "kumaru train configs\train\lora-qwen.yaml"
#   .\scripts\overnight.ps1 -Preview          # only list what it would close
#
# Put the job in quotes, as you would type it in the Kumaru folder; it runs
# from there. Start it from Windows Terminal: closing the window it runs in
# stops the job.
#
# In order, it:
#   1. Checks the job can start. `kumaru train` gets a --dry-run first; if that
#      fails, nothing is closed.
#   2. Lists the programs holding graphics memory (Task Manager's "Dedicated
#      GPU memory"), with -AllApps also every other program with a window, and
#      asks once before closing them.
#   3. Closes them: politely first, as clicking X does, then by force after 20
#      seconds. Unsaved work in them is lost.
#   4. Keeps Windows from sleeping until the job ends. The screen can still
#      turn off, and no power setting is changed.
#   5. Runs the job, logging its output, and the graphics memory once a minute,
#      to runs\overnight\<date-time>\, and prints a summary when it ends.
#
# It never closes Windows itself (anything under C:\Windows, services, the
# desktop and taskbar), graphics drivers, security software, the window it
# runs in, or what you name with -Keep. It needs no administrator rights, and
# no "Developer Mode": that Windows setting frees no memory.

[CmdletBinding()]
param(
    # The job, in quotes: "kumaru train configs\train\lora-qwen.yaml".
    [Parameter(Position = 0)]
    [string]$Run,
    # Check the job and list what would be closed, then stop. Closes nothing.
    [switch]$Preview,
    # Also close every other program that has a window.
    [switch]$AllApps,
    # Programs never to close, by name, wildcards allowed: -Keep Discord,Spotify
    [string[]]$Keep = @(),
    # Programs holding less graphics memory than this are left alone.
    [ValidateRange(1, 1048576)]
    [int]$MinGpuMB = 100,
    # Close without asking first.
    [switch]$Yes,
    # Catches a job given without quotes, to explain them.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$GraceSeconds = 20
$SampleSeconds = 60

# Embedded browsers belong to the program that started them: closing only the
# helper frees nothing for long, because its program starts a new one.
$HelperNames = @("msedgewebview2", "steamwebhelper", "EpicWebHelper", "CefSharp.BrowserSubprocess", "QtWebEngineProcess")
# Driver and security programs installed outside C:\Windows.
$ProtectedNames = @("nvcontainer", "NVDisplay.Container", "AMDRSServ", "amdfendrsr", "atieclxx", "atiesrxx", "MsMpEng", "NisSrv", "SecurityHealthSystray")

if (-not ("KumaruOvernight.Native" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace KumaruOvernight {
    public static class Native {
        const uint ES_CONTINUOUS = 0x80000000;
        const uint ES_SYSTEM_REQUIRED = 0x00000001;
        const uint WM_CLOSE = 0x0010;
        const uint GW_OWNER = 4;

        delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

        [DllImport("kernel32.dll")] static extern uint SetThreadExecutionState(uint flags);
        [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
        [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr hWnd, uint command);
        [DllImport("user32.dll")] static extern bool PostMessage(IntPtr hWnd, uint message, IntPtr wParam, IntPtr lParam);

        // Until AllowSleep, or until this process ends, Windows does not sleep
        // on its own. The screen can still turn off. Both return the previous
        // state; 0 means the call failed.
        public static uint KeepAwake() { return SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED); }
        public static uint AllowSleep() { return SetThreadExecutionState(ES_CONTINUOUS); }

        // Asks every visible main window of these processes to close, as its X
        // button does. Returns how many windows were asked.
        public static int CloseWindows(int[] processIds) {
            Dictionary<uint, bool> wanted = new Dictionary<uint, bool>();
            foreach (int id in processIds) { wanted[(uint)id] = true; }
            int asked = 0;
            EnumWindows(delegate (IntPtr hWnd, IntPtr lParam) {
                uint owner;
                GetWindowThreadProcessId(hWnd, out owner);
                if (wanted.ContainsKey(owner) && IsWindowVisible(hWnd) && GetWindow(hWnd, GW_OWNER) == IntPtr.Zero) {
                    PostMessage(hWnd, WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
                    asked++;
                }
                return true;
            }, IntPtr.Zero);
            return asked;
        }
    }
}
'@
}

function Write-Step($text) { Write-Host "`n>> $text" -ForegroundColor Cyan }

function Format-Clock([TimeSpan]$span) {
    "{0}:{1:D2}:{2:D2}" -f [int][math]::Floor($span.TotalHours), $span.Minutes, $span.Seconds
}

function Format-Duration([TimeSpan]$span) {
    "{0}h {1:D2}m" -f [int][math]::Floor($span.TotalHours), $span.Minutes
}

# -- processes ---------------------------------------------------------------

function Get-ProcessTable {
    $table = @{}
    foreach ($p in Get-CimInstance Win32_Process) {
        $table[[int]$p.ProcessId] = [pscustomobject]@{
            Id       = [int]$p.ProcessId
            ParentId = [int]$p.ParentProcessId
            Name     = [IO.Path]::GetFileNameWithoutExtension([string]$p.Name)
            Path     = [string]$p.ExecutablePath
            Session  = [int]$p.SessionId
            Started  = $p.CreationDate
        }
    }
    $table
}

function Get-Parent($table, $proc) {
    $parent = $table[$proc.ParentId]
    if ($null -eq $parent -or $parent.Id -eq $proc.Id) { return $null }
    # Windows reuses process ids: a "parent" that started after its child is
    # an unrelated process.
    if ($parent.Started -and $proc.Started -and $parent.Started -gt $proc.Started) { return $null }
    $parent
}

function Get-Ancestors($table, [int]$id) {
    $ids = @{}
    $proc = $table[$id]
    while ($proc -and -not $ids.ContainsKey($proc.Id)) {
        $ids[$proc.Id] = $true
        $proc = Get-Parent $table $proc
    }
    $ids
}

function New-Context($table, [int]$selfId, [string[]]$keep) {
    $children = @{}
    foreach ($proc in $table.Values) {
        $parent = Get-Parent $table $proc
        if ($parent) {
            if (-not $children.ContainsKey($parent.Id)) { $children[$parent.Id] = New-Object System.Collections.Generic.List[object] }
            $children[$parent.Id].Add($proc)
        }
    }
    [pscustomobject]@{
        Table    = $table
        Children = $children
        Session  = $table[$selfId].Session
        Own      = Get-Ancestors $table $selfId
        # "-Keep a,b" arrives as one string when the script is started with -File.
        Keep     = @($keep | ForEach-Object { "$_" -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        WinDir   = [Environment]::GetFolderPath("Windows").TrimEnd("\") + "\"
        Names    = @{}
    }
}

function Get-DisplayName($context, $proc) {
    if (-not $context.Names.ContainsKey($proc.Id)) {
        $name = $null
        if ($proc.Path) {
            try { $name = (Get-Item -LiteralPath $proc.Path).VersionInfo.FileDescription } catch { $name = $null }
        }
        if (-not $name -or -not $name.Trim()) { $name = $proc.Name }
        $context.Names[$proc.Id] = $name.Trim()
    }
    $context.Names[$proc.Id]
}

function Test-Protected($context, $proc) {
    if ($proc.Session -ne $context.Session) { return $true }   # services and other users
    if ($context.Own.ContainsKey($proc.Id)) { return $true }   # this window and what opened it
    if (-not $proc.Path) { return $true }                      # unreadable: Windows, or run as administrator
    if ($proc.Path.StartsWith($context.WinDir, [StringComparison]::OrdinalIgnoreCase)) { return $true }
    if ($ProtectedNames -contains $proc.Name) { return $true }
    foreach ($pattern in $context.Keep) {
        if ($proc.Name -like $pattern -or (Get-DisplayName $context $proc) -like $pattern) { return $true }
    }
    $false
}

# The program a process belongs to: up through its own helper processes
# (brave.exe under brave.exe), and from an embedded browser to the program
# hosting it. $null when that program must not be closed.
function Get-AppRoot($context, $proc) {
    $root = $proc
    $seen = @{}
    while (-not $seen.ContainsKey($root.Id)) {
        $seen[$root.Id] = $true
        $parent = Get-Parent $context.Table $root
        if ($null -eq $parent) { break }
        $same = $parent.Name -eq $root.Name
        $hosted = $HelperNames -contains $root.Name
        if (-not ($same -or $hosted)) { break }
        if (Test-Protected $context $parent) {
            if ($hosted -and -not $same) { return $null }
            break
        }
        $root = $parent
    }
    if (Test-Protected $context $root) { return $null }
    $root
}

# The program and everything it started, except what is protected.
function Get-ProcessTree($context, $root) {
    $ids = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Generic.Queue[object]
    $seen = @{}
    $queue.Enqueue($root)
    while ($queue.Count -gt 0) {
        $proc = $queue.Dequeue()
        if ($seen.ContainsKey($proc.Id)) { continue }
        $seen[$proc.Id] = $true
        if ($proc.Id -ne $root.Id -and (Test-Protected $context $proc)) { continue }
        $ids.Add($proc.Id)
        if ($context.Children.ContainsKey($proc.Id)) {
            foreach ($child in $context.Children[$proc.Id]) { $queue.Enqueue($child) }
        }
    }
    , $ids.ToArray()
}

# -- graphics memory -----------------------------------------------------------

# Dedicated graphics memory per process id, in bytes; $null where Windows does
# not report it (Windows 10 before version 1709).
function Get-GpuUsage {
    try { $rows = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUProcessMemory -ErrorAction Stop }
    catch { return $null }
    $usage = @{}
    foreach ($row in $rows) {
        if ($row.Name -match '^pid_(\d+)_') {
            $id = [int]$Matches[1]
            $usage[$id] = [double]$usage[$id] + [double]$row.DedicatedUsage
        }
    }
    $usage
}

# Memory in use on one graphics adapter (by default the busiest), in GB.
function Get-GpuMemory([string]$adapter) {
    try { $rows = @(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory -ErrorAction Stop) }
    catch { return $null }
    $row = $null
    if ($adapter) { $row = $rows | Where-Object { $_.Name -eq $adapter } | Select-Object -First 1 }
    if (-not $row) { $row = $rows | Sort-Object { [double]$_.DedicatedUsage } -Descending | Select-Object -First 1 }
    if (-not $row) { return $null }
    [pscustomobject]@{
        Adapter     = $row.Name
        DedicatedGB = [double]$row.DedicatedUsage / 1GB
        SharedGB    = [double]$row.SharedUsage / 1GB
    }
}

# The card's own memory in MB, from nvidia-smi; $null without an NVIDIA driver.
function Get-TotalGpuMB {
    $smi = Get-Command nvidia-smi -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $smi) { return $null }
    try { $lines = & $smi.Path --query-gpu=memory.total --format=csv,noheader,nounits 2>$null } catch { return $null }
    $max = 0
    foreach ($line in @($lines)) {
        $mb = 0
        if ([int]::TryParse(([string]$line).Trim(), [ref]$mb) -and $mb -gt $max) { $max = $mb }
    }
    if ($max -gt 0) { return $max }
    $null
}

function Find-Programs($context, [bool]$allApps, [int]$minGpuMB, $totalGpuMB) {
    $apps = @{}
    $usage = Get-GpuUsage
    if ($null -ne $usage) {
        foreach ($id in @($usage.Keys)) {
            $proc = $context.Table[$id]
            if (-not $proc -or $usage[$id] -le 0) { continue }
            $root = Get-AppRoot $context $proc
            if (-not $root) { continue }
            if (-not $apps.ContainsKey($root.Id)) { $apps[$root.Id] = [pscustomobject]@{ Root = $root; GpuBytes = 0.0; Windowed = $false } }
            $apps[$root.Id].GpuBytes += $usage[$id]
        }
    }
    if ($allApps) {
        foreach ($p in Get-Process) {
            $hasWindow = $false
            try { $hasWindow = $p.MainWindowHandle -ne [IntPtr]::Zero } catch { $hasWindow = $false }
            if (-not $hasWindow) { continue }
            $proc = $context.Table[$p.Id]
            if (-not $proc) { continue }
            $root = Get-AppRoot $context $proc
            if (-not $root) { continue }
            if (-not $apps.ContainsKey($root.Id)) { $apps[$root.Id] = [pscustomobject]@{ Root = $root; GpuBytes = 0.0; Windowed = $false } }
            $apps[$root.Id].Windowed = $true
        }
    }
    $limit = [double]$minGpuMB * 1MB
    $chosen = @($apps.Values | Where-Object { $_.GpuBytes -ge $limit -or $_.Windowed } | Sort-Object GpuBytes -Descending)
    foreach ($app in $chosen) {
        $mb = $app.GpuBytes / 1MB
        # Windows sometimes reports more than the card has; show that as unknown.
        if ($totalGpuMB -and $mb -gt $totalGpuMB) { $text = "        ? MB" }
        elseif ($mb -ge 1) { $text = "{0,9:N0} MB" -f $mb }
        else { $text = "           -" }
        $app | Add-Member -NotePropertyName Name -NotePropertyValue (Get-DisplayName $context $app.Root)
        $app | Add-Member -NotePropertyName GpuText -NotePropertyValue $text
        $app | Add-Member -NotePropertyName Ids -NotePropertyValue (Get-ProcessTree $context $app.Root)
    }
    , $chosen
}

# -- closing -----------------------------------------------------------------

# True while the process with this id is the one that started at $started.
function Test-Running([int]$id, $started) {
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if (-not $p) { return $false }
    if ($started) {
        try { if ([math]::Abs(($p.StartTime - $started).TotalSeconds) -gt 2) { return $false } } catch { }
    }
    $true
}

function Get-StillRunning($context, $app) {
    , @($app.Ids | Where-Object { Test-Running $_ $context.Table[$_].Started })
}

function Close-Programs($context, $apps, [int]$graceSeconds) {
    $waiting = @()
    foreach ($app in $apps) {
        if ([KumaruOvernight.Native]::CloseWindows([int[]]$app.Ids) -gt 0) { $waiting += $app }
    }
    $deadline = (Get-Date).AddSeconds($graceSeconds)
    while ($waiting.Count -gt 0 -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $waiting = @($waiting | Where-Object { (Get-StillRunning $context $_).Count -gt 0 })
    }
    foreach ($app in $apps) {
        $left = Get-StillRunning $context $app
        if ($left.Count -eq 0) {
            [pscustomobject]@{ Name = $app.Name; Outcome = "closed" }
            continue
        }
        foreach ($id in $left) { try { Stop-Process -Id $id -Force -ErrorAction Stop } catch { } }
        Start-Sleep -Milliseconds 500
        if ((Get-StillRunning $context $app).Count -eq 0) { $outcome = "closed by force" }
        else { $outcome = "could not be closed (it may run as administrator)" }
        [pscustomobject]@{ Name = $app.Name; Outcome = $outcome }
    }
}

function Test-RestartPending {
    (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired") -or
    (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending")
}

# -- the job -------------------------------------------------------------------

function Resolve-Job([string]$line) {
    $line = $line.Trim()
    if ($line.StartsWith('"')) {
        $end = $line.IndexOf('"', 1)
        if ($end -lt 0) { throw "The job has an opening quote but no closing one: $line" }
        $program = $line.Substring(1, $end - 1)
        $arguments = $line.Substring($end + 1).Trim()
    }
    else {
        $space = $line.IndexOfAny([char[]]@(" ", "`t"))
        if ($space -lt 0) { $program = $line; $arguments = "" }
        else { $program = $line.Substring(0, $space); $arguments = $line.Substring($space + 1).Trim() }
    }
    $exe = $null
    if ($program -eq "kumaru") {
        $venv = Join-Path $Root ".venv\Scripts\kumaru.exe"
        if (Test-Path -LiteralPath $venv) { $exe = $venv }
    }
    if (-not $exe) {
        Push-Location $Root
        try { $command = Get-Command $program -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 }
        finally { Pop-Location }
        if ($command) { $exe = $command.Path }
    }
    if (-not $exe) { throw "Can't find the program '$program'. For kumaru, run .\scripts\setup.ps1 first." }
    $isKumaru = (Split-Path $exe -Leaf) -match '^kumaru(\.exe)?$'
    [pscustomobject]@{
        Line       = $line
        Exe        = $exe
        Arguments  = $arguments
        IsTraining = $isKumaru -and $arguments -match '^train(\s|$)' -and $arguments -notmatch '(^|\s)--dry-run(\s|$)'
    }
}

function Start-Program([string]$exe, [string]$arguments, [string]$stdout, [string]$stderr) {
    $options = @{
        FilePath               = $exe
        NoNewWindow            = $true
        PassThru               = $true
        WorkingDirectory       = $Root
        RedirectStandardOutput = $stdout
        RedirectStandardError  = $stderr
    }
    if ($arguments) { $options.ArgumentList = $arguments }
    $proc = Start-Process @options
    $null = $proc.Handle   # without this, Windows PowerShell loses the exit code
    $proc
}

function Invoke-Preflight($job) {
    $folder = Join-Path ([IO.Path]::GetTempPath()) ("kumaru-overnight-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $folder | Out-Null
    try {
        $stdout = Join-Path $folder "output.txt"
        $stderr = Join-Path $folder "log.txt"
        $proc = Start-Program $job.Exe ($job.Arguments + " --dry-run") $stdout $stderr
        $proc.WaitForExit()
        $tail = @()
        if ($proc.ExitCode -ne 0) { $tail = @(Get-LogTail $stderr 12) + @(Get-LogTail $stdout 5) }
        [pscustomobject]@{ ExitCode = $proc.ExitCode; Tail = $tail }
    }
    finally { Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue }
}

# The last lines of a log. Progress bars redraw one line with carriage
# returns, thousands of times; only each line's final state is kept.
function Get-LogTail([string]$path, [int]$count) {
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    try {
        $size = [int][math]::Min($stream.Length, 65536)
        [void]$stream.Seek(-$size, [IO.SeekOrigin]::End)
        $buffer = New-Object byte[] $size
        $read = $stream.Read($buffer, 0, $size)
        $text = [Text.Encoding]::UTF8.GetString($buffer, 0, $read)
    }
    finally { $stream.Dispose() }
    $lines = @($text -split "`n" | ForEach-Object { $_.Split("`r") | Where-Object { $_.Trim() } | Select-Object -Last 1 } | Where-Object { $_ })
    @($lines | Select-Object -Last $count)
}

# Appends one line to gpu.csv and keeps the peaks. Shared memory growing past
# its starting level means the card ran out and Windows lent it RAM. Never
# throws: a missed sample must not stop the watch over the job.
function Add-GpuSample($state) {
    try {
        $mem = Get-GpuMemory $state.Adapter
        if (-not $mem) { return }
        if ($null -eq $state.SharedStart) { $state.SharedStart = $mem.SharedGB }
        if ($mem.DedicatedGB -gt $state.Peak) { $state.Peak = $mem.DedicatedGB }
        if ($mem.SharedGB -gt $state.SharedPeak) { $state.SharedPeak = $mem.SharedGB }
        $line = [string]::Format([Globalization.CultureInfo]::InvariantCulture, "{0:yyyy-MM-dd HH:mm:ss},{1:F2},{2:F2}", (Get-Date), $mem.DedicatedGB, $mem.SharedGB)
        Add-Content -LiteralPath $state.Csv -Value $line -Encoding ASCII
        $state.Text = "graphics memory {0:N1} GB" -f $mem.DedicatedGB
    }
    catch { }
}

function Invoke-Job($job, [string]$folder, [string]$adapter) {
    $stdout = Join-Path $folder "output.txt"
    $stderr = Join-Path $folder "log.txt"
    $state = @{ Adapter = $adapter; Csv = (Join-Path $folder "gpu.csv"); Peak = 0.0; SharedStart = $null; SharedPeak = 0.0; Text = "" }
    Set-Content -LiteralPath $state.Csv -Value "time,dedicated_gb,shared_gb" -Encoding ASCII
    Add-GpuSample $state
    $started = Get-Date
    $proc = Start-Program $job.Exe $job.Arguments $stdout $stderr
    $nextSample = $started.AddSeconds($SampleSeconds)
    while (-not $proc.WaitForExit(1000)) {
        if ((Get-Date) -ge $nextSample) {
            Add-GpuSample $state
            $nextSample = (Get-Date).AddSeconds($SampleSeconds)
        }
        Write-Host -NoNewline ("`r   running for {0}   {1}   " -f (Format-Clock ((Get-Date) - $started)), $state.Text)
    }
    $proc.WaitForExit()
    $finished = Get-Date
    Add-GpuSample $state
    Write-Host ""
    $borrowed = $null
    if ($null -ne $state.SharedStart) { $borrowed = [math]::Max(0.0, $state.SharedPeak - $state.SharedStart) }
    [pscustomobject]@{
        ExitCode   = $proc.ExitCode
        Started    = $started
        Finished   = $finished
        PeakGB     = $state.Peak
        BorrowedGB = $borrowed
        Log        = $stderr
    }
}

function Write-Summary($job, $result, [string[]]$closed, [string]$folder) {
    $ok = $result.ExitCode -eq 0
    $graphics = "not measured"
    if ($result.PeakGB -gt 0) {
        $graphics = "peak {0:N1} GB in use" -f $result.PeakGB
        if ($result.BorrowedGB -ge 1) {
            $graphics += "; it ran out and borrowed {0:N1} GB of RAM, which slows training" -f $result.BorrowedGB
        }
    }
    if ($ok) { $outcome = "finished" } else { $outcome = "FAILED" }
    if ($closed) { $closedText = $closed -join ", " } else { $closedText = "nothing" }
    $lines = @(
        "================ Overnight run ================",
        ("Job       " + $job.Line),
        ("Result    {0} (exit code {1})" -f $outcome, $result.ExitCode),
        ("Time      {0:ddd HH:mm} to {1:ddd HH:mm}, {2}" -f $result.Started, $result.Finished, (Format-Duration ($result.Finished - $result.Started))),
        ("Graphics  " + $graphics),
        ("Closed    " + $closedText),
        ("Logs      " + $folder),
        "==============================================="
    )
    if (-not $ok) {
        $lines += "Last lines of log.txt:"
        $lines += @(Get-LogTail $result.Log 20 | ForEach-Object { "   " + $_ })
    }
    Set-Content -LiteralPath (Join-Path $folder "summary.txt") -Value $lines -Encoding UTF8
    Write-Host ""
    foreach ($line in $lines) {
        if ($line.StartsWith("Result")) { Write-Host $line -ForegroundColor $(if ($ok) { "Green" } else { "Red" }) }
        else { Write-Host $line }
    }
}

# -- main ----------------------------------------------------------------------

function Invoke-Overnight {
    if ($Rest -or (-not $Run -and -not $Preview)) {
        if ($Rest) { Write-Host "Put the whole job in quotes, for example:" -ForegroundColor Yellow }
        else { Write-Host "Say what to run, in quotes, for example:" -ForegroundColor Yellow }
        Write-Host '   .\scripts\overnight.ps1 "kumaru train configs\train\lora-qwen.yaml"'
        exit 2
    }

    $job = $null
    if ($Run) {
        try { $job = Resolve-Job $Run }
        catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 2 }
    }

    if ($job -and $job.IsTraining) {
        Write-Step "Checking the job can start: $($job.Line) --dry-run"
        $check = Invoke-Preflight $job
        if ($check.ExitCode -ne 0) {
            Write-Host "   The dry run failed (exit code $($check.ExitCode)), so nothing was closed. Its last lines:" -ForegroundColor Red
            foreach ($line in $check.Tail) { Write-Host "   $line" }
            exit 1
        }
        Write-Host "   OK" -ForegroundColor Green
    }

    Write-Step "Programs to close"
    $table = Get-ProcessTable
    $context = New-Context $table $PID $Keep
    $gpu = Get-GpuMemory ""
    $adapter = $null
    if ($gpu) { $adapter = $gpu.Adapter }
    if ($null -eq (Get-GpuUsage) -and -not $AllApps) {
        Write-Host "   Windows doesn't report graphics memory per program here; -AllApps closes every program with a window instead." -ForegroundColor Yellow
    }
    $apps = Find-Programs $context ([bool]$AllApps) $MinGpuMB (Get-TotalGpuMB)
    if ($apps.Count -eq 0) { Write-Host "   Nothing to close." }
    foreach ($app in $apps) {
        Write-Host ("   {0}  {1}  ({2})" -f $app.GpuText, $app.Name, (Split-Path $app.Root.Path -Leaf))
    }
    if ($apps.Count -gt 0) { Write-Host "   Unsaved work in these programs will be lost." -ForegroundColor Yellow }
    if (Test-RestartPending) {
        Write-Host "   Windows is waiting to restart for an update and may do it tonight, which would stop the job. Restart first, or pause updates in Settings > Windows Update." -ForegroundColor Yellow
    }

    if ($Preview) {
        Write-Host "`nPreview only: nothing was closed or started."
        exit 0
    }

    if ($apps.Count -gt 0 -and -not $Yes) {
        $answer = Read-Host "`nClose them and start the job? [y/N]"
        if ($answer -notmatch '^\s*(y|yes)\s*$') {
            Write-Host "Cancelled. Nothing was closed."
            exit 1
        }
    }

    $closed = @()
    if ($apps.Count -gt 0) {
        Write-Step "Closing"
        $before = Get-GpuMemory $adapter
        foreach ($result in Close-Programs $context $apps $GraceSeconds) {
            Write-Host ("   {0,-34} {1}" -f $result.Name, $result.Outcome)
            if ($result.Outcome -like "closed*") { $closed += $result.Name }
        }
        Start-Sleep -Seconds 3
        $after = Get-GpuMemory $adapter
        if ($before -and $after) {
            Write-Host ("   Graphics memory in use: {0:N1} GB before, {1:N1} GB now." -f $before.DedicatedGB, $after.DedicatedGB)
        }
    }

    $folder = Join-Path $Root ("runs\overnight\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    Write-Step "Running: $($job.Line)"
    Write-Host "   The PC stays awake until it ends; the screen can still turn off. Keep this window open."
    Write-Host "   Logs: $folder"
    $unbuffered = $env:PYTHONUNBUFFERED
    $env:PYTHONUNBUFFERED = "1"   # so log.txt fills as it goes
    [void][KumaruOvernight.Native]::KeepAwake()
    try { $result = Invoke-Job $job $folder $adapter }
    finally {
        [void][KumaruOvernight.Native]::AllowSleep()
        $env:PYTHONUNBUFFERED = $unbuffered
    }
    Write-Summary $job $result $closed $folder
    exit $result.ExitCode
}

# Dot-sourcing (". .\scripts\overnight.ps1") only loads the functions above.
if ($MyInvocation.InvocationName -ne ".") { Invoke-Overnight }
