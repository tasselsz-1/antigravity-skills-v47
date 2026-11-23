# 第四棒 B（`P4B`）：技术规格自动门禁 + 剪辑师人工回看确认两步封板

> **触发时机**：DaVinci Resolve 21 渲染导出对应档位母带（默认 `分集/第NN集/第NN集_1K24fps_成片.mp4`，显式 `4K` 档位 `分集/第NN集/第NN集_4K24fps_成片.mp4`）后执行。

## 第一步：`phase4-verify-master`（技术规格自动门禁 · 仅置 `MASTER_TECH_SPEC_PASSED`）

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase4-verify-master --project-root "<项目根目录>" --episode "第01集"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第01集>"
```

对渲染导出的成片母带与 `.fcpxml` 执行独立技术规格实测（与 P4A 排布预检物理分离，**任一项不达标立即非零退出阻断**）：
1. **FCPXML 1.9 DTD 合规** + 成片分辨率严格匹配 `--p4-profile` 与 `aspect_ratio`（默认 `1K`：`16:9` 为 `1920×1080`、`9:16` 为 `1080×1920`；显式 `4K`：`16:9` 为 `3840×2160`、`9:16` 为 `2160×3840`），帧率锁定 `24fps`（`23.95–24.05fps`），视频流独立可读帧数与时间戳核验 **`missing_frames == 0`**；
2. **黑场/静止死帧拦截**：多点抽样实测画面亮度均值与像素标准差；
3. **对白时间窗非静音核验**：每个预期对白时间窗实测 **`rms_db >= -50 dBFS`**；
4. **无水印**且总时长与时间轴对齐。

> **状态边界**：自动门禁通过后**仅标记为 `MASTER_TECH_SPEC_PASSED`**，`phase-status` 停在 **`WAIT_HUMAN_MASTER_REVIEW`**，**严禁直接置 `ALL_DONE`**！

## 第二步：`phase4-confirm-master`（剪辑师人工回看确认 · 升为 `MASTER_SEALED_OK` 解锁 `ALL_DONE`）

由人工剪辑师在最终导出的成片母带上逐段回看确认：
1. **对白内容**准确无漏词错词；
2. **口型同步**自然吻合；
3. **画面烧录字幕**无错字、压脸或错位（且具 `2–3px` 黑描边清晰可读）。

确认无误后执行封板命令：

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase4-confirm-master `
  --project-root "<项目根目录>" `
  --episode "第01集" `
  --reviewer "<剪辑师姓名>" `
  --confirm-dialogue `
  --confirm-lipsync `
  --confirm-subtitles `
  --notes "<人工回看确认记录>"

python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第01集>"
```

- 命令强绑定母带文件哈希 **`confirmed_master_sha256`** 并升为 **`MASTER_SEALED_OK`**；
- `phase-status` 每次恢复时实时复验母带物理存在且当前 SHA-256 与 `confirmed_master_sha256` 100% 一致后方置 **`ALL_DONE`**（一旦后续替换或重导出成片导致 SHA 变化，自动打回重检与重新人工确认）；
- 全部封板后向根主控回传 `≤ 15 行` 封板报告并退出。

---

## 附：V4.6/V4.7 真机全链路实测封板基线（Google Colab `NVIDIA L4` + DaVinci Resolve 21）

- **环境**：Colab `NVIDIA L4 (23 GB)` / `Python 3.13.16` / `PyTorch 2.14.1+cu130` / `spandrel 0.4.2`；`uv venv --python 3.10` + `MPLBACKEND=Agg` 隔离解决 `mediapipe==0.10.11` 兼容，`python -m scripts.inference --help` 退出码 `0`。
- **无损帧锁**：`LatentSync 1.6 @ d058a61` `24↔25fps` Pull-up/Pull-down `120` 帧实测 1:1 零丢帧/零重复帧，帧差相关性 `r = 0.9921`。
- **单步超分**：`realesr-animevideov3.pth` + GPU FP32 `bicubic(antialias=True)` 单步直出 4K，去字幕区高频锐度方差 `+83.8%`（`19.79 → 36.37`），白边光晕与压缩噪点 100% 清除。
- **达芬奇 21**：GUI `Ctrl+Shift+I` 实测 `433` 帧 FCPXML 1.9 零误差导入，`2–3px` 字幕描边 + `H.264 Master High Profile` 导出，完整通过两步封板闭环。
