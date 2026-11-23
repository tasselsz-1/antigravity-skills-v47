<#
.SYNOPSIS
  使用便宜的 Colab CPU 实例 + Detached 后台断线免疫模式，流式预热 MiniMax-H3 无损权重到 Google Drive。
.NOTES
  远端通过 Python start_new_session=True 独立运行，本地每 20 秒发 1 秒短连接拉取增量日志；
  任何情况下 try/finally 必删 /tmp/rclone.conf 并 colab stop 归零核验。
#>
param(
    [ValidateSet("h200-max", "h200-fast")][string]$Profile = "h200-max",
    [string]$RemoteRoot = "gdrive:models/minimax_h3",
    [string]$SessionName = "agy-h3-precache"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $scriptDir "precache_models.py"
$key    = "$env:USERPROFILE\.config\colab-cli\colab_ed25519"
$conf   = "$env:APPDATA\rclone\rclone.conf"

python -m py_compile $script
if ($LASTEXITCODE -ne 0) { throw "py_compile precache_models.py failed" }

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

$sess = "$SessionName-$(Get-Date -Format 'HHmmss')"
$created = $false
try {
    Write-Host "🚀 启动 Colab CPU 预热实例 (Detached 断线免疫模式): $sess ..."
    colab new -s $sess
    if ($LASTEXITCODE -ne 0) { throw "colab new failed" }
    $created = $true

    colab upload -s $sess $conf /tmp/rclone.conf
    if ($LASTEXITCODE -ne 0) { throw "upload rclone.conf failed" }
    colab upload -s $sess $script /content/precache_models.py
    if ($LASTEXITCODE -ne 0) { throw "upload precache_models.py failed" }

    $spawnSrc = @'
import json, os, subprocess, sys
from pathlib import Path

for p in ("/tmp/h3_job.log", "/tmp/h3_job.done", "/tmp/h3_job.pid"):
    Path(p).unlink(missing_ok=True)

runner = """
import os, subprocess, sys
from pathlib import Path
env = dict(os.environ)
env["PYTHONUNBUFFERED"] = "1"
cmd = [sys.executable, "/content/precache_models.py", "--profile", sys.argv[1], "--remote-root", sys.argv[2], "--rclone-config", "/tmp/rclone.conf"]
rc = 1
try:
    rc = subprocess.run(cmd, env=env).returncode
finally:
    Path("/tmp/h3_job.done").write_text(str(rc), encoding="utf-8")
sys.exit(rc)
"""
Path("/tmp/h3_runner.py").write_text(runner, encoding="utf-8")
log_f = open("/tmp/h3_job.log", "wb")
proc = subprocess.Popen(
    [sys.executable, "/tmp/h3_runner.py", sys.argv[1], sys.argv[2]],
    stdin=subprocess.DEVNULL,
    stdout=log_f,
    stderr=subprocess.STDOUT,
    start_new_session=True,
    close_fds=True,
)
Path("/tmp/h3_job.pid").write_text(str(proc.pid), encoding="utf-8")
print(f"SPAWNED_PID={proc.pid}")
'@
    $probeSrc = @'
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
    $spawnFile = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "h3_spawn.py")
    $probeFile = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "h3_probe.py")
    try {
        [System.IO.File]::WriteAllText($spawnFile, ($spawnSrc -replace "`r`n", "`n"), [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($probeFile, ($probeSrc -replace "`r`n", "`n"), [System.Text.UTF8Encoding]::new($false))
        colab upload -s $sess $spawnFile /tmp/h3_spawn.py
        if ($LASTEXITCODE -ne 0) { throw "upload spawn failed" }
        colab upload -s $sess $probeFile /tmp/h3_probe.py
        if ($LASTEXITCODE -ne 0) { throw "upload probe failed" }
    } finally {
        Remove-Item $spawnFile, $probeFile -Force -ErrorAction SilentlyContinue
    }

    $boot = 'chmod 600 /tmp/rclone.conf && (command -v rclone >/dev/null || (curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rc.zip && unzip -qjo /tmp/rc.zip "*/rclone" -d /usr/local/bin && chmod +x /usr/local/bin/rclone && rm -f /tmp/rc.zip))'
    Invoke-SshRetry -Sess $sess -KeyPath $key -Cmd $boot | Out-Null

    $pidOut = Invoke-SshRetry -Sess $sess -KeyPath $key -Cmd "python3 /tmp/h3_spawn.py '$Profile' '$RemoteRoot'"
    Write-Host "✅ 远端后台任务已启动 ($($pidOut -join ' '))，开始每 20 秒短连接拉取日志..."

    $startIdx = 0
    $consecFail = 0
    $maxFail = 8
    while ($true) {
        Start-Sleep -Seconds 20
        $res = Invoke-SshSafe -Sess $sess -KeyPath $key -Cmd "python3 /tmp/h3_probe.py $startIdx"
        if ($res.Code -ne 0) {
            $consecFail++
            Write-Host "⚠️ 短探活瞬时波动 ($consecFail/$maxFail)，远端预热不受影响，稍后重连..."
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
                Write-Host "🎉 远端模型预热全部成功完成！"
                break
            } else {
                throw "远端 precache_models.py 失败，退出码: $doneRc"
            }
        }
        if ($alive -eq 0) {
            throw "远端后台进程异常消失且未写出完成标记，请检查上方日志！"
        }
    }
}
finally {
    if ($created) {
        Invoke-SshSafe -Sess $sess -KeyPath $key -Cmd 'rm -f /tmp/rclone.conf /tmp/h3_spawn.py /tmp/h3_runner.py /tmp/h3_probe.py' | Out-Null
        colab stop -s $sess
    }
    colab sessions
    colab usage
}
