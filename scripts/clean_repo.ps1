param(
  [switch]$SkipRemoveFromGitIndex
)

# luego:
if (-not $SkipRemoveFromGitIndex) {
  # git rm --cached ...
}

Write-Host "Cleaning local build artifacts..." -ForegroundColor Cyan

$pathsToDelete = @(
  ".venv",
  "src\aegisforge_agent.egg-info",
  "**\__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  "build",
  "dist"
)

foreach ($p in $pathsToDelete) {
  Get-ChildItem -Path $p -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

if ($AlsoRemoveFromGitIndex) {
  Write-Host "Removing artifacts from git index (cached)..." -ForegroundColor Yellow
  git rm -r --cached .venv 2>$null
  git rm -r --cached src\aegisforge_agent.egg-info 2>$null
  git rm -r --cached **\__pycache__ 2>$null
  git rm -r --cached .pytest_cache 2>$null
  git rm -r --cached .mypy_cache 2>$null
  git rm -r --cached .ruff_cache 2>$null
  git rm -r --cached build 2>$null
  git rm -r --cached dist 2>$null
}

Write-Host "Done. Now make sure .gitignore ignores venv/egg-info/caches." -ForegroundColor Green
