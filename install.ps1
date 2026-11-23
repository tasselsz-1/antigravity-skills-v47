<#
.SYNOPSIS
    将 Antigravity V4.7 拆分规则与全量 17 个 Skill 一键部署到目标工作区，并自动将占位符回填为本机路径。
.PARAMETER WorkspaceRoot
    目标工作区根目录（默认当前工作目录）。
.PARAMETER InstallGlobal
    可选开关：同时安装 global/GEMINI.md 与 .agents/rules/image-generation-quality.md 至 $HOME\.gemini\config\。
.PARAMETER KeepPlaceholders
    可选开关：保持 <WORKSPACE_ROOT> / <USER_HOME> 占位符不做本机路径回填。
.PARAMETER DryRun
    仅预览将要执行的复制与替换操作，不修改任何文件。
.EXAMPLE
    .\install.ps1 -WorkspaceRoot "D:\my-workspace" -InstallGlobal
#>
[CmdletBinding()]
param(
    [string]$WorkspaceRoot = (Get-Location).Path,
    [switch]$InstallGlobal,
    [switch]$KeepPlaceholders,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetWs = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$TargetWsSlash = $TargetWs.Replace('\', '/')
$UserHome = [System.IO.Path]::GetFullPath($HOME)
$UserHomeSlash = $UserHome.Replace('\', '/')
$Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'

Write-Host "=== Antigravity V4.7 Rules & Skills Installer ===" -ForegroundColor Cyan
Write-Host "Source Repo   : $RepoRoot"
Write-Host "Target Root   : $TargetWs"
Write-Host "User Home     : $UserHome"
Write-Host "InstallGlobal : $InstallGlobal"
Write-Host "DryRun        : $DryRun"

function Backup-IfExists {
    param([string]$PathToCheck, [string]$BackupBase)
    if (Test-Path -LiteralPath $PathToCheck) {
        $leaf = Split-Path -Leaf $PathToCheck
        $dst = Join-Path $BackupBase $leaf
        if ($DryRun) {
            Write-Host "[DryRun] Backup $PathToCheck -> $dst"
        } else {
            New-Item -ItemType Directory -Force -Path $BackupBase | Out-Null
            Copy-Item -LiteralPath $PathToCheck -Destination $dst -Recurse -Force
            Write-Host "[Backup] $PathToCheck -> $dst" -ForegroundColor Yellow
        }
    }
}

function Render-Placeholders {
    param([string]$FilePath)
    if ($KeepPlaceholders) { return }
    $ext = [System.IO.Path]::GetExtension($FilePath).ToLowerInvariant()
    if ($ext -notin @('.md', '.ps1', '.yaml', '.yml', '.txt')) { return }
    $content = Get-Content -LiteralPath $FilePath -Raw -Encoding UTF8
    $updated = $content.Replace('<WORKSPACE_ROOT>', $TargetWsSlash).Replace('<USER_HOME>', $UserHomeSlash)
    if ($updated -ne $content) {
        if ($DryRun) {
            Write-Host "[DryRun] Render placeholders in $FilePath"
        } else {
            [System.IO.File]::WriteAllText($FilePath, $updated, [System.Text.UTF8Encoding]::new($false))
        }
    }
}

$BackupRoot = Join-Path $TargetWs "_skill_backups\install-$Timestamp"

# 1. Backup existing AGENTS.md and .agents
Backup-IfExists -PathToCheck (Join-Path $TargetWs 'AGENTS.md') -BackupBase $BackupRoot
Backup-IfExists -PathToCheck (Join-Path $TargetWs '.agents\rules') -BackupBase (Join-Path $BackupRoot '.agents')
Backup-IfExists -PathToCheck (Join-Path $TargetWs '.agents\skills') -BackupBase (Join-Path $BackupRoot '.agents')

# 2. Deploy AGENTS.md and .agents/
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $TargetWs | Out-Null
    Copy-Item -LiteralPath (Join-Path $RepoRoot 'AGENTS.md') -Destination (Join-Path $TargetWs 'AGENTS.md') -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $TargetWs '.agents\rules') | Out-Null
    Copy-Item -Path (Join-Path $RepoRoot '.agents\rules\*') -Destination (Join-Path $TargetWs '.agents\rules') -Recurse -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $TargetWs '.agents\skills') | Out-Null
    Copy-Item -Path (Join-Path $RepoRoot '.agents\skills\*') -Destination (Join-Path $TargetWs '.agents\skills') -Recurse -Force
} else {
    Write-Host "[DryRun] Copy AGENTS.md, .agents\rules, and 17 skills into $TargetWs"
}

# 3. Render placeholders in deployed workspace markdown/config docs
if (-not $DryRun -and -not $KeepPlaceholders) {
    Render-Placeholders -FilePath (Join-Path $TargetWs 'AGENTS.md')
    Get-ChildItem -Path (Join-Path $TargetWs '.agents') -Recurse -File | ForEach-Object {
        Render-Placeholders -FilePath $_.FullName
    }
}

# 4. Optional global rules deployment
if ($InstallGlobal) {
    $GlobalDir = Join-Path $UserHome '.gemini\config'
    $GlobalRulesDir = Join-Path $GlobalDir 'rules'
    $GlobalBackup = Join-Path $GlobalDir "backup-$Timestamp"
    Backup-IfExists -PathToCheck (Join-Path $GlobalDir 'GEMINI.md') -BackupBase $GlobalBackup
    Backup-IfExists -PathToCheck (Join-Path $GlobalRulesDir 'image-generation-quality.md') -BackupBase (Join-Path $GlobalBackup 'rules')

    if (-not $DryRun) {
        New-Item -ItemType Directory -Force -Path $GlobalRulesDir | Out-Null
        $dstGemini = Join-Path $GlobalDir 'GEMINI.md'
        $dstGlobalRule = Join-Path $GlobalRulesDir 'image-generation-quality.md'
        Copy-Item -LiteralPath (Join-Path $RepoRoot 'global\GEMINI.md') -Destination $dstGemini -Force
        Copy-Item -LiteralPath (Join-Path $RepoRoot '.agents\rules\image-generation-quality.md') -Destination $dstGlobalRule -Force
        if (-not $KeepPlaceholders) {
            # Update relative links so global copies resolve to the target workspace
            $gTxt = Get-Content -LiteralPath $dstGemini -Raw -Encoding UTF8
            $gTxt = $gTxt.Replace('](../AGENTS.md)', "](file:///$TargetWsSlash/AGENTS.md)")
            $gTxt = $gTxt.Replace('<WORKSPACE_ROOT>', $TargetWsSlash).Replace('<USER_HOME>', $UserHomeSlash)
            [System.IO.File]::WriteAllText($dstGemini, $gTxt, [System.Text.UTF8Encoding]::new($false))

            $rTxt = Get-Content -LiteralPath $dstGlobalRule -Raw -Encoding UTF8
            $rTxt = $rTxt.Replace('](../skills/', "](file:///$TargetWsSlash/.agents/skills/")
            $rTxt = $rTxt.Replace('<WORKSPACE_ROOT>', $TargetWsSlash).Replace('<USER_HOME>', $UserHomeSlash)
            [System.IO.File]::WriteAllText($dstGlobalRule, $rTxt, [System.Text.UTF8Encoding]::new($false))
        }
    } else {
        Write-Host "[DryRun] Copy global\GEMINI.md and .agents\rules\image-generation-quality.md into $GlobalDir"
    }
}

Write-Host "`nDone! Deployment completed." -ForegroundColor Green
if (-not $DryRun) {
    Write-Host "Tip: Run regression test to verify scripts:"
    Write-Host "  python `"$TargetWs\.agents\skills\video-recreation\scripts\run_v42_regression_suite.py`""
}
