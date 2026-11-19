"""Protect what overnight.ps1 closes: a program's own processes, not the programs it
started; nothing of a program it must leave alone; and no window of a process that exited
while it asked, even when Windows gave its id to another.

Windows only. Dot-sourcing the script loads its functions without running it; they are
checked on an invented process table, with stand-ins that only record what they would close.
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell, Windows only")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "overnight.ps1"

# Odd ids, which Windows never gives a process: VS Code (101), whose terminal runs the
# script (105), and its graphics process (103); Steam (201), its web helper (203, 205),
# and a game it started (207) with the game's own helper (209).
CHECKS = r"""
Add-Type -TypeDefinition @'
namespace GhuumiOvernight {
    public static class Native {
        public static System.Collections.Generic.List<int> Asked = new System.Collections.Generic.List<int>();
        public static int CloseWindows(int[] processIds) { Asked.AddRange(processIds); return 0; }
    }
}
'@
. '__SCRIPT__'
$stopped = New-Object System.Collections.Generic.List[int]
function Stop-Process { [CmdletBinding()] param([int]$Id, [switch]$Force) $stopped.Add($Id) }

$table = @{}
foreach ($row in @(@(101, 0, 'Code'), @(103, 101, 'Code'), @(105, 101, 'pwsh'),
        @(201, 0, 'steam'), @(203, 201, 'steamwebhelper'), @(205, 203, 'steamwebhelper'),
        @(207, 201, 'game'), @(209, 207, 'game'), @($PID, 0, 'x'))) {
    $table[$row[0]] = [pscustomobject]@{ Id = $row[0]; ParentId = $row[1]; Name = $row[2]; Path = "C:\Apps\$($row[2]).exe"; Session = 1; Started = $null }
}
$context = New-Context $table 105 @()
function TreeOf($id) { $ids = Get-ProcessTree $context $table[$id]; ($ids | Sort-Object) -join ',' }
$root = Get-AppRoot $context $table[103]

$app = [pscustomobject]@{ Name = 'x'; Ids = [int[]]@($PID) }
$table[$PID].Started = [datetime]'2001-01-01'   # the listed process exited; Windows gave its id to this one
$null = Close-Programs $context @($app) 0
$reused = @([GhuumiOvernight.Native]::Asked) + @($stopped)
$table[$PID].Started = (Get-Process -Id $PID).StartTime
$null = Close-Programs $context @($app) 0

[ordered]@{
    steam = TreeOf 201; game = TreeOf 207; helperRoot = "$($root.Id)"; reused = $reused -join ','
    asked = [GhuumiOvernight.Native]::Asked -join ','; stopped = $stopped -join ','; self = "$PID"
} | ConvertTo-Json -Compress
"""


@pytest.fixture(scope="module")
def checked() -> dict:
    script = CHECKS.replace("__SCRIPT__", str(SCRIPT).replace("'", "''"))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_closing_a_launcher_leaves_the_game_it_started(checked: dict) -> None:
    assert checked["steam"] == "201,203,205"  # Steam and its web helper
    assert checked["game"] == "207,209"  # closed only when it is listed itself


def test_a_helper_of_a_program_it_leaves_alone_is_left_alone(checked: dict) -> None:
    assert checked["helperRoot"] == ""  # VS Code's graphics process, the script running in VS Code


def test_no_close_request_reaches_a_process_that_took_a_listed_id(checked: dict) -> None:
    assert checked["reused"] == ""  # no close request, and not stopped
    assert checked["asked"] == checked["stopped"] == checked["self"]  # the listed one: both
