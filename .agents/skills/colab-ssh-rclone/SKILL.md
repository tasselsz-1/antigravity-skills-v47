---
name: colab-ssh-rclone
description: 通过本地 google-colab-cli（colab new / exec / ssh / upload / download / run / stop）与 rclone（gdrive: 专属生产态 OAuth）操控 Google Colab 云端算力并内网直连 Google Drive（G 盘）素材，替代不稳定的 Colab MCP。凡涉及启动 Colab、云端跑脚本/模型、SSH 远程操作 Colab、读写 G 盘云端素材时优先使用本 Skill。支持口令：“用Colab”、“SSH连Colab”、“跑云端算力”、“Colab直连G盘”、“colab-ssh-rclone”。
---

# Colab CLI + SSH + rclone 直连算力操作规范（常驻 Skill）

## 1. 核心定位与优先级

- **默认首选通道**：凡需调用 Google Colab 云端算力或让 Colab 读写 Google 云盘（本地映射 `G:\`）素材，**一律优先使用本机已固化的 `colab` CLI + `colab ssh` + `rclone` 内网直连方案**，替代不稳定的 `colab_mcp` / 远程 Colab MCP。
- **数据流原则**：大文件/视频素材一律通过 **`rclone` 在 Google 内网直连 `gdrive:` ↔ Colab `/content/` 本地高速盘**，算完回写 `gdrive:`（本地 `G:\` 自动同步可见），避免绕行本地宽带，也避免 `colab drivemount` 每台新 VM 弹窗授权与 FUSE 缓存丢盘风险。

## 2. 本机已固化资产（仅记路径，严禁打印或泄露密钥原文）

| 组件 | 本机路径 / 说明 |
| :--- | :--- |
| `colab` CLI | `<USER_HOME>\.local\bin\colab.exe`（`google-colab-cli`，OAuth2 Refresh Token 已持久化至 Windows 系统凭据库，永久免登） |
| `colab ssh` 专用密钥 | `<USER_HOME>\.config\colab-cli\colab_ed25519`（Ed25519；Colab 拒绝 RSA；**每次 `colab ssh` 必须显式传 `-i`**） |
| `rclone` | `<USER_HOME>\.local\bin\rclone.exe` |
| `rclone` 配置 | `<USER_HOME>\AppData\Roaming\rclone\rclone.conf`（remote 名 `gdrive:`，已绑定用户专属生产态 Google Cloud OAuth `client_id`，零警告、永久自动续期） |
| 本地云盘映射 | `G:\`（Google Drive for Desktop；对应 `gdrive:` 根目录，如 `pilot_24fps`、`原视频`、`小样`、`项目-八百`） |
| 网络代理 | 本机开启 TUN 模式时直通，**无需**设置代理环境变量；若未开 TUN 仅开系统代理（如 `127.0.0.1:7897`），因 `websocket-client` 不读 Windows 注册表代理，调用前需设置 `$env:HTTP_PROXY="http://127.0.0.1:7897"; $env:HTTPS_PROXY="http://127.0.0.1:7897"` |

## 3. 绝对红线（计费、安全与非交互约束）

1. **默认优先 GPU 极速通道（用户已授权，省时降本）**：第一棒（`TransNetV2`+CFR+`faster-whisper`）默认 `--gpu T4`（`faster-whisper` 用 `device="cuda", compute_type="float16"`，保留 CPU `int8` 兜底）；第二/三棒配音（`VoxCPM 2` / `IndexTTS-2.5` / `Fish Audio S2 Pro`，选型见 [`colab-voice-dubbing`](../colab-voice-dubbing/SKILL.md)）与第四棒（`LatentSync 1.6 + Real-ESRGAN`）默认 `--gpu L4`（按需 `--gpu A100` / `H200`）；`--tpu` / `--high-mem` 仅按需。注意未知 `--gpu` 值会静默回退到 `A100`，传参前必须核对拼写；遇错即停，严禁 `--force-local`/伪造校验。
2. **强制闭环关机（防孤儿实例烧额度）**：所有 Colab 作业必须包裹在 `try { ... } finally { ... }` 中；无论成功或报错，退出前必须：
   - 删掉远端临时凭据 `rm -f /tmp/rclone.conf`；
   - 执行 `colab stop -s <name>` 销毁实例；
   - 执行 `colab sessions` 与 `colab usage` 核验活跃实例归零（`Active assignments: 0`、`Usage rate: 0.00/hr`）。
3. **凭据零泄露红线**：严禁查看、打印、回显 `rclone.conf` 或 `colab_ed25519` 内容到终端输出、日志文件或聊天记录；严禁将其复制到任何工作区目录或 Git 仓库；上传进容器一律放 `/tmp/rclone.conf` 并立即 `chmod 600`。
4. **Agent 非交互禁令**：严禁 Agent 运行会阻塞在 TTY 的交互命令——禁止裸 `colab ssh`（无 `-- CMD`）、`colab repl`、`colab auth`、`colab drivemount`。Shell 一律用 `colab ssh -s <name> -i <key> -- '<cmd>'`，Python 一律用 `colab exec -s <name> -f <script.py>`。
5. **长任务防超时**：`colab exec` 默认 `--timeout 30.0`（30 秒无输出即超时失败）；模型推理/转码/下载等耗时任务务必显式传 `--timeout 0`（或在 `colab ssh -- '<cmd>'` 下运行并定期刷新日志）。
6. **必须显式命名会话**：`colab new` 务必传 `-s <有意义的会话名>`；若多代理并发跑多个 Colab 任务，用全局 `--config <独立临时json路径>` 隔离 `sessions.json` 状态文件。

## 4. 标准全自动直连作业模板（PowerShell）

```powershell
$ErrorActionPreference = "Stop"
$sess = "agy-job-01"
$key  = "$env:USERPROFILE\.config\colab-cli\colab_ed25519"
$conf = "$env:APPDATA\rclone\rclone.conf"

try {
  # 1) 启动运行时（默认 GPU：P1 用 --gpu T4，P2–P4 用 --gpu L4/A100/H200；CPU 仅作兜底）
  colab new -s $sess
  if ($LASTEXITCODE -ne 0) { throw "colab new failed" }

  # 2) 上传 rclone 配置到容器 /tmp，并确保容器内已装好 rclone（约 2 秒）
  colab upload -s $sess $conf /tmp/rclone.conf
  if ($LASTEXITCODE -ne 0) { throw "colab upload rclone.conf failed" }

  colab ssh -s $sess -i $key -- 'chmod 600 /tmp/rclone.conf && mkdir -p /content/work /content/out && (command -v rclone >/dev/null || (curl -fsSL https://downloads.rclone.org/rclone-current-linux-amd64.zip -o /tmp/rc.zip && unzip -qjo /tmp/rc.zip "*/rclone" -d /usr/local/bin && chmod +x /usr/local/bin/rclone && rm -f /tmp/rc.zip))'
  if ($LASTEXITCODE -ne 0) { throw "remote bootstrap failed" }

  # 3) Google 内网从 G 盘拉取素材到容器本地 SSD
  colab ssh -s $sess -i $key -- 'rclone --config /tmp/rclone.conf copy "gdrive:原视频/目标文件.mp4" /content/work/ --transfers 4 --checkers 8'
  if ($LASTEXITCODE -ne 0) { throw "rclone pull failed" }

  # 4) 执行计算（Python 脚本用 colab exec --timeout 0；Shell 管道用 colab ssh --）
  colab exec -s $sess --timeout 0 -f .\local_worker.py
  if ($LASTEXITCODE -ne 0) { throw "colab exec failed" }

  # 5) Google 内网将成品回传 G 盘（带 MD5 校验，本地 G:\ 盘自动可见）
  colab ssh -s $sess -i $key -- 'rclone --config /tmp/rclone.conf copy /content/out/ "gdrive:项目输出/目标目录/" --transfers 4 --checkers 8'
  if ($LASTEXITCODE -ne 0) { throw "rclone push failed" }
}
finally {
  # 6) 强制清理远端临时凭据并销毁实例、验证归零
  try { colab ssh -s $sess -i $key -- 'rm -f /tmp/rclone.conf' 2>$null } catch {}
  colab stop -s $sess
  colab sessions
  colab usage
}
```

## 5. 常用命令速查与故障处理

- **状态与诊断**：
  - `colab whoami`：检查 OAuth2 登录态与到期时间
  - `colab sessions` / `colab status -s <name>` / `colab usage`：查看云端活跃实例、硬件型号、IDLE/BUSY 及计算单元余额
  - `colab log -s <name> -n 20`：排查失败任务事件日志；`colab log -s <name> -o out.ipynb` 可导出执行记录
  - `colab install -s <name> pkg1 pkg2`（或 `-r requirements.txt`）：在容器内优先通过 `uv` 极速装包
- **一次性轻量 Python 脚本（不涉及 G 盘素材）**：可用 `colab run --timeout 0 local_script.py [args...]`（自动 `new` → `exec` → `stop`，退出码直传；按环节加 `--gpu T4|L4`）。
- **常见异常恢复**：
  - **`colab ssh` 报 HTTP 429**：该 VM 的并发 SSH 连接数达上限，等待旧连接退出后重试；若仍受限，可临时回退 `colab exec -s <name>` 执行 Python `subprocess.run(...)`（严禁把不可信文本直接拼进 `!` 或 `%%bash`）。
  - **报 404 / 401 Session not found**：Colab 后端已回收实例，执行 `colab sessions` 清理陈旧本地记录后重新 `colab new`。
  - **内核卡死**：`colab restart-kernel -s <name>`（保留 VM 重置内核），仍无响应则直接 `colab stop` 重开。
  - **遇错即停**：云端任务或校验门禁返回非零退出码时，清理临时凭据并释放实例后立即如实上报失败原因，严禁私自改跑本地冒充云端通过。
