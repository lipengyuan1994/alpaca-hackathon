# Section 9.3 reproduction wrapper for Windows PowerShell hosts.
$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PkgDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PkgDir)

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$PkgDir\src;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = "$PkgDir\src"
}

Set-Location $RepoRoot
uv run python -m opening_range_breakout_v1.reproduce @args
exit $LASTEXITCODE
