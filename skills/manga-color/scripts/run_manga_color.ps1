$ErrorActionPreference = "Stop"

$candidates = [System.Collections.Generic.List[string]]::new()
if ($env:MANGA_COLOR_PYTHON) {
    $candidates.Add($env:MANGA_COLOR_PYTHON)
}
$candidates.Add((Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"))
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $candidates.Add($pythonCommand.Source)
}

$selected = $null
foreach ($candidate in $candidates) {
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        continue
    }
    & $candidate -c "import sys, PIL; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) {
        $selected = $candidate
        break
    }
}

if (-not $selected) {
    $payload = @{
        ok = $false
        status = "NEEDS_INPUT"
        task_dir = $null
        error = @{
            code = "python_runtime_unavailable"
            message = "No compatible Python runtime with Pillow is available. Configure MANGA_COLOR_PYTHON or install requirements with a stable Python 3.10+. OpenAI SDK is only required for provider=openai."
        }
        next_action = "configure_python_runtime"
    }
    $payload | ConvertTo-Json -Compress -Depth 4
    exit 2
}

& $selected (Join-Path $PSScriptRoot "manga_color.py") @args
exit $LASTEXITCODE
