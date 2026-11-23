<#
.SYNOPSIS
  h3出片本机一键云端调度脚本（colab CLI + Detached 断线免疫模式 + rclone Google 内网直连）。

.NOTES
  1) 默认硬件：--gpu G4（NVIDIA RTX PRO 6000 Blackwell 96GB + 176GB RAM）
  2) 默认画质：-Profile h200-max（无损满血 BF16 + FP16 Video VAE + FP32 Audio VAE + ref_image_size=max, 768p@24fps）
  3) 默认缓存：自动对接 Google Drive (gdrive:models/minimax_h3) 按 manifest 精确单文件内网缓存，零双份占盘。
  4) 断线免疫：远端 nohup setsid 后台独立运行 ComfyUI 与 MiniMax-H3，本地每 20 秒发 1 秒短连接拉增量日志，
     彻底免疫代理/WebSocket 长连接闪断，绝不中途误杀正在跑的 GPU 实例。
  5) 强制闭环关机：try/finally 必删 /tmp/rclone.conf /tmp/.hf_token，必跑 colab stop + colab sessions + colab usage 核验归零。

.EXAMPLE
  # 方式 1：直喂打包器 pack_direct_job.py 打包完成后直接上云（加 -SkipBuild 保留直喂任务单）
  .\run_colab_cli.ps1 -SeriesLocalDir "<WORKSPACE_ROOT>\h3_projects\my_ep01" -Episode "ep01" -SkipBuild

  # 方式 2：直接从 ai-anime-series 标准目录编译并上云（默认 G4 + 无损 h200-max）
  .\run_colab_cli.ps1 -SeriesLocalDir "<WORKSPACE_ROOT>\ai-anime-h3\template_kids_4grid" -Episode "ep01"
#>
param(
    [Parameter(Mandatory=$true)][string]$SeriesLocalDir,
    [string]$Episode = "ep01",
    [string]$DriveSeriesDir = "",
    [ValidateSet("G4", "H100", "A100")][string]$Gpu = "G4",
    [ValidateSet("h200-max", "bf16-raw", "h200-fast", "g4-balanced")][string]$Profile = "h200-max",
    [ValidateSet("1080p", "768p", "480p")][string]$Resolution = "768p",
    [ValidateSet("", "match", "max")][string]$RefSize = "",
    [switch]$Turbo,
    [int]$Seed = -1,
    [switch]$SkipBuild,
    [switch]$UseCustomPrompt,
    [string]$DriveModelCache = "models/minimax_h3",
    [switch]$NoModelCache,
    [switch]$NoCachePush,
    [switch]$SkipExisting,
    [string]$OnlyParts = "",
    [string]$HfToken = $env:HF_TOKEN,
    [string]$SessionName = "agy-h3-chupian",
    [switch]$SkipReview
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SeriesLocalDir = (Resolve-Path $SeriesLocalDir).Path
$key  = "$env:USERPROFILE\.config\colab-cli\colab_ed25519"
$conf = "$env:APPDATA\rclone\rclone.conf"
$reviewScript = "$env:USERPROFILE\.gemini\config\skills\ai-anime-series\scripts\review_clip.py"

if (-not (Test-Path $key))  { throw "缺少 Colab SSH 密钥: $key" }
if (-not (Test-Path $conf)) { throw "缺少 rclone 配置: $conf" }

function Invoke-SshSafe {
    param([string]$Sess, [string]$KeyPath, [string]$Cmd)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & colab ssh -s $Sess -i $KeyPath -- $Cmd 2>&1
        $rc = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    return @{ Code = $rc; Output = @($out | ForEach-Object { $_.ToString() }) }
}

function Invoke-SshRetry {
    param([string]$Sess, [string]$KeyPath, [string]$Cmd, [int]$MaxTry = 4)
    for ($i = 1; $i -le $MaxTry; $i++) {
        $res = Invoke-SshSafe -Sess $Sess -KeyPath $KeyPath -Cmd $Cmd
        if ($res.Code -eq 0) { return $res.Output }
        if ($i -lt $MaxTry) {
            Write-Host "⚠️ SSH 短命令第 $i/$MaxTry 次波动，10 秒后重试..."
            Start-Sleep -Seconds 10
        } else {
            throw "SSH 命令多次重试后仍失败 (rc=$($res.Code)): $($res.Output -join '; ')"
        }
    }
}

if (-not $DriveSeriesDir) {
    $leaf = Split-Path -Leaf $SeriesLocalDir
    $DriveSeriesDir = "ai_anime/$leaf"
}

# 1) 构建或校验 manifest.json
if (-not $SkipBuild) {
    $jobArgs = @("$scriptDir\make_h3_jobs.py", $SeriesLocalDir, $Episode, "--profile", $Profile, "--resolution", $Resolution)
    if ($RefSize) { $jobArgs += @("--ref-size", $RefSize) }
    if ($Turbo) { $jobArgs += "--turbo" }
    if ($Seed -ge 0) { $jobArgs += @("--seed", "$Seed") }
    if ($UseCustomPrompt) { $jobArgs += "--use-custom-prompt" }
    python @jobArgs
    if ($LASTEXITCODE -ne 0) { throw "make_h3_jobs.py 失败" }
}

$manifestPath = Join-Path $SeriesLocalDir "$Episode\h3_jobs\manifest.json"
if (-not (Test-Path $manifestPath)) {
    throw "未找到任务清单: $manifestPath（请先运行 pack_direct_job.py 或去掉 -SkipBuild）"
}
$manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$onlySet = @()
if ($OnlyParts) {
    $onlySet = @($OnlyParts.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" })
}
$selectedJobs = @($manifest.jobs | Where-Object { $onlySet.Count -eq 0 -or ($onlySet -contains $_.part) })
if ($selectedJobs.Count -eq 0) {
    throw "按 -OnlyParts '$OnlyParts' 未匹配到任何段落！可用段号: $(($manifest.jobs | ForEach-Object { $_.part }) -join ', ')"
}
foreach ($j in $selectedJobs) {
    if ($j.missing_files -and $j.missing_files.Count -gt 0) {
        throw "段 $($j.part) 缺少参考图/故事板素材，请先补齐再上云避免空烧 GPU: $($j.missing_files -join ', ')"
    }
    foreach ($ri in $j.ref_images) {
        $p = Join-Path $SeriesLocalDir ($ri.src_rel -replace '/', '\')
        if (-not (Test-Path $p)) { throw "本地缺少参考图 ($($ri.tag) $($ri.name)): $p" }
    }
    foreach ($ra in $j.ref_audios) {
        $p = Join-Path $SeriesLocalDir ($ra.src_rel -replace '/', '\')
        if (-not (Test-Path $p)) { throw "本地缺少参考音频 ($($ra.tag) $($ra.name)): $p" }
    }
}

$partSummary = if ($onlySet.Count -gt 0) { "筛选 $($selectedJobs.Count)/$($manifest.jobs.Count) 段 ($($onlySet -join ','))" } else { "共 $($manifest.jobs.Count) 段" }
Write-Host "📋 本次出片配置: Series=$SeriesLocalDir | Ep=$Episode | Gpu=$Gpu | Profile=$($manifest.profile_name) | $partSummary"
if (-not $HfToken) {
    Write-Host "ℹ️ 未设置 HF_TOKEN，将优先命中 Google Drive 缓存或匿名拉取公开仓库 Comfy-Org/MiniMax-H3。"
}

# 2) 推送工程素材与任务单到 Google Drive（排除本地旧 clips/review 与辅助草图 _refs）
Write-Host "⬆️ 同步素材与任务清单到 gdrive:$DriveSeriesDir ..."
rclone copy $SeriesLocalDir "gdrive:$DriveSeriesDir" --transfers 4 --checkers 8 --exclude "clips/**" --exclude "review/**" --exclude "**/storyboards/_refs/**"
if ($LASTEXITCODE -ne 0) { throw "本地 rclone 推送素材失败" }

$sess = "$SessionName-$(Get-Date -Format 'HHmmss')"
$created = $false
try {
    Write-Host "🚀 启动 Colab GPU 实例 (Detached 断线免疫模式): $sess (--gpu $Gpu) ..."
    colab new -s $sess --gpu $Gpu
    if ($LASTEXITCODE -ne 0) { throw "colab new 失败" }
    $created = $true

    # 上传 rclone 配置与云端 worker 脚本（绝不打印凭据内容）
    colab upload -s $sess $conf /tmp/rclone.conf
    if ($LASTEXITCODE -ne 0) { throw "上传 rclone.conf 失败" }
    colab upload -s $sess "$scriptDir\colab_h3_worker.py" /content/colab_h3_worker.py
    if ($LASTEXITCODE -ne 0) { throw "上传 colab_h3_worker.py 失败" }

    # 拼装 worker 参数 JSON + 远端包装器 + 探活器（彻底零引号嵌套与断线风险）
    $workerArgs = @("--series-dir", "/content/series", "--episode", $Episode, "--mode", "batch", "--rclone-config", "/tmp/rclone.conf", "--rclone-clips-dest", "gdrive:$DriveSeriesDir/$Episode/clips")
    if (-not $NoModelCache -and $DriveModelCache) {
        $workerArgs += @("--rclone-model-cache", "gdrive:$DriveModelCache")
        if ($NoCachePush) { $workerArgs += "--no-cache-push" }
    }
    if ($SkipExisting) { $workerArgs += "--skip-existing" }
    if ($OnlyParts) { $workerArgs += @("--only-parts", $OnlyParts) }

    $argsJson = ($workerArgs | ConvertTo-Json -Compress)
    $runnerPy = @'
import json, os, subprocess, sys
from pathlib import Path

args = json.loads(Path("/tmp/h3_args.json").read_text(encoding="utf-8"))
env = dict(os.environ)
env["PYTHONUNBUFFERED"] = "1"
tok_p = Path("/tmp/.hf_token")
rc = 1
try:
    if tok_p.exists():
        env["HF_TOKEN"] = tok_p.read_text(encoding="utf-8").strip()
    proc = subprocess.run([sys.executable, "/content/colab_h3_worker.py", *args], env=env)
    rc = proc.returncode
finally:
    if tok_p.exists():
        tok_p.unlink(missing_ok=True)
    Path("/tmp/h3_job.done").write_text(str(rc), encoding="utf-8")
sys.exit(rc)
'@
    $probePy = @'
import os, sys
from pathlib import Path

start_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
log_p = Path("/tmp/h3_job.log")
done_p = Path("/tmp/h3_job.done")
pid_p = Path("/tmp/h3_job.pid")

lines = log_p.read_text(encoding="utf-8", errors="replace").splitlines() if log_p.exists() else []
done_rc = done_p.read_text(encoding="utf-8").strip() if done_p.exists() else ""
alive = 0
if pid_p.exists():
    try:
        os.kill(int(pid_p.read_text(encoding="utf-8").strip()), 0)
        alive = 1
    except Exception:
        alive = 0

print(f"__META__|{len(lines)}|{alive}|{done_rc}")
for line in lines[start_idx:]:
    print(line)
'@

    $spawnPy = @'
import subprocess, sys
from pathlib import Path

for p in ("/tmp/h3_job.log", "/tmp/h3_job.done", "/tmp/h3_job.pid"):
    Path(p).unlink(missing_ok=True)
log_f = open("/tmp/h3_job.log", "wb")
proc = subprocess.Popen(
    [sys.executable, "/tmp/h3_runner.py"],
    stdin=subprocess.DEVNULL,
    stdout=log_f,
    stderr=subprocess.STDOUT,
    start_new_session=True,
    close_fds=True,
)
Path("/tmp/h3_job.pid").write_text(str(proc.pid), encoding="utf-8")
print(f"SPAWNED_PID={proc.pid}")
'@

    $tmpDir = [System.IO.Path]::GetTempPath()
    $stamp = Get-Date -Format 'HHmmssfff'
    $argsFile   = Join-Path $tmpDir "h3_args_$stamp.json"
    $runnerFile = Join-Path $tmpDir "h3_runner_$stamp.py"
    $spawnFile  = Join-Path $tmpDir "h3_spawn_$stamp.py"
    $probeFile  = Join-Path $tmpDir "h3_probe_$stamp.py"
    $tokFile    = Join-Path $tmpDir "h3_tok_$stamp.tmp"
    try {
        [System.IO.File]::WriteAllText($argsFile,   $argsJson, [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($runnerFile, ($runnerPy -replace "`r`n", "`n"), [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($spawnFile,  ($spawnPy  -replace "`r`n", "`n"), [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($probeFile,  ($probePy  -replace "`r`n", "`n"), [System.Text.UTF8Encoding]::new($false))
        colab upload -s $sess $argsFile   /tmp/h3_args.json
        colab upload -s $sess $runnerFile /tmp/h3_runner.py
        colab upload -s $sess $spawnFile  /tmp/h3_spawn.py
        colab upload -s $sess $probeFile  /tmp/h3_probe.py
        if ($HfToken) {
            [System.IO.File]::WriteAllText($tokFile, $HfToken.Trim(), [System.Text.UTF8Encoding]::new($false))
            colab upload -s $sess $tokFile /tmp/.hf_token
        }
    } finally {
        Remove-Item $argsFile, $runnerFile, $spawnFile, $probeFile, $tokFile -Force -ErrorAction SilentlyContinue
    }

    # 容器初始化 rclone（约 2 秒）
    $bootstrap = 'chmod 600 /tmp/rclone.conf && ([ ! -f /tmp/.hf_token ] || chmod 600 /tmp/.hf_token) && mkdir -p /content/series && (command -v rclone >/dev/null || (curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rc.zip && unzip -qjo /tmp/rc.zip "*/rclone" -d /usr/local/bin && chmod +x /usr/local/bin/rclone && rm -f /tmp/rc.zip))'
    Invoke-SshRetry -Sess $sess -KeyPath $key -Cmd $bootstrap | Out-Null

    # Google 内网拉取系列素材与任务清单
    $pullCmd = "rclone --config /tmp/rclone.conf copy `"gdrive:$DriveSeriesDir`" /content/series --transfers 4 --checkers 8"
    Invoke-SshRetry -Sess $sess -KeyPath $key -Cmd $pullCmd | Out-Null

    # Python start_new_session 脱离会话后台启动（零竞态、1 秒返回）
    $pidOut = Invoke-SshRetry -Sess $sess -KeyPath $key -Cmd "python3 /tmp/h3_spawn.py"
    Write-Host "🎬 Colab ($Gpu) 后台生成已启动 ($($pidOut -join ' '))，开始每 20 秒断线免疫轮询日志..."

    $startIdx = 0
    $consecFail = 0
    $maxFail = 8
    while ($true) {
        Start-Sleep -Seconds 20
        $res = Invoke-SshSafe -Sess $sess -KeyPath $key -Cmd "python3 /tmp/h3_probe.py $startIdx"
        if ($res.Code -ne 0) {
            $consecFail++
            Write-Host "⚠️ 短探活瞬时波动 ($consecFail/$maxFail)，云端 GPU 仍在正常运算，稍后自动重连..."
            if ($consecFail -ge $maxFail) {
                throw "连续 $maxFail 次探活失败，中止等待。"
            }
            continue
        }
        $consecFail = 0

        $lines = $res.Output
        $metaLine = $lines | Where-Object { $_ -like "__META__|*" } | Select-Object -First 1
        if (-not $metaLine) { continue }
        $parts = $metaLine.Split("|")
        $totalLines = [int]$parts[1]
        $alive = [int]$parts[2]
        $doneRc = $parts[3].Trim()

        $metaIdx = [Array]::IndexOf($lines, $metaLine)
        if ($metaIdx -ge 0 -and ($metaIdx + 1) -lt $lines.Count) {
            for ($k = $metaIdx + 1; $k -lt $lines.Count; $k++) {
                if ($lines[$k] -ne "") { Write-Host $lines[$k] }
            }
        }
        $startIdx = $totalLines

        if ($doneRc -ne "") {
            if ($doneRc -eq "0") {
                Write-Host "✅ 云端 MiniMax-H3 生成任务成功结束！"
                break
            } else {
                throw "云端 MiniMax-H3 生成失败，退出码: $doneRc"
            }
        }
        if ($alive -eq 0) {
            throw "云端后台生成进程异常退出且未写出完成标记，请检查上方日志！"
        }
    }

    # 3) 回传成品 clips 到 Google Drive，再同步到本地
    $pushOut = "rclone --config /tmp/rclone.conf copy `"/content/series/$Episode/clips`" `"gdrive:$DriveSeriesDir/$Episode/clips`" --transfers 4 --checkers 8"
    Invoke-SshRetry -Sess $sess -KeyPath $key -Cmd $pushOut | Out-Null

    Write-Host "✅ 云端成品已回传 gdrive:$DriveSeriesDir/$Episode/clips，同步回本地..."
    rclone copy "gdrive:$DriveSeriesDir/$Episode/clips" (Join-Path $SeriesLocalDir "$Episode\clips") --transfers 4 --checkers 8
    if ($LASTEXITCODE -ne 0) { throw "本地同步回 clips 失败" }
}
finally {
    if ($created) {
        Invoke-SshSafe -Sess $sess -KeyPath $key -Cmd 'rm -f /tmp/rclone.conf /tmp/.hf_token /tmp/h3_args.json /tmp/h3_runner.py /tmp/h3_spawn.py /tmp/h3_probe.py' | Out-Null
        colab stop -s $sess
    }
    colab sessions
    colab usage
}

# 4) 自动复用 ai-anime-series 的 review_clip.py 抽帧供快速质检
if (-not $SkipReview -and (Test-Path $reviewScript)) {
    Write-Host "🔍 运行 ai-anime-series/scripts/review_clip.py 生成抽帧拼图..."
    python $reviewScript $SeriesLocalDir $Episode
}
