$ErrorActionPreference = "Stop"

git config core.hooksPath .githooks
Write-Host "[progress-hook] core.hooksPath set to .githooks"
Write-Host "[progress-hook] pre-commit gate is now enabled for this repo."

