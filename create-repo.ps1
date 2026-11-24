param(
    [ValidateSet("private", "public")]
    [string]$Visibility = "private"
)

$ErrorActionPreference = "Stop"
$Repository = "aakashsiwach/cerepath-guardian"
$Description = "Independent CEREPATH reliability supervisor and bounded code-repair agent"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required. Install Git for Windows first."
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI is required. Install it from https://cli.github.com/"
}

gh auth status
if ($LASTEXITCODE -ne 0) {
    throw "Authenticate GitHub CLI first by running: gh auth login"
}

if (-not (Test-Path ".git")) {
    git init -b main
}
git branch -M main
if (-not (git config user.name)) { git config user.name "Aakash Siwach" }
if (-not (git config user.email)) { git config user.email "aakashsiwach@users.noreply.github.com" }
git add -A
$staged = git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "chore: initialize CEREPATH Guardian"
    if ($LASTEXITCODE -ne 0) { throw "Initial commit failed." }
}

$visibilityFlag = if ($Visibility -eq "public") { "--public" } else { "--private" }
gh repo create $Repository $visibilityFlag --source . --remote origin --push --description $Description
if ($LASTEXITCODE -ne 0) {
    throw "Repository creation/push failed. Check whether $Repository already exists and inspect git status."
}

Write-Host "Created and pushed https://github.com/$Repository"
