---
name: video-recreation-p4
description: >-
  视频复刻第四棒专属流程（V4.7）：执行 P4A（默认 1K@24fps 四档矩阵、Colab 按需 LatentSync 1.6 口型与单步神经超分、累计切点帧号表 FCPXML 1.9 DTD 与达芬奇 21 交接）与 P4B（技术规格自动门禁 + 人工回看确认两步封板）；备选 60fps 高刷模式仅按需加载。当用户说“第四棒”、“P4”、“第四棒A/P4A”、“第四棒B/P4B”、“超分刷口型”、“生成FCPXML”、“成片终检”、“封板”时触发。
---

# 视频复刻 · 第四棒专属总控（P4A / P4B · V4.7）

> **🛑 遇错即停铁律**：Colab 运行报错、`build-fcpxml` 缺超分段/缺帧/FCPXML 1.9 DTD 校验失败，或 `phase4-verify-master` / `phase4-confirm-master` 返回非零退出码时，**一律原地停机保留现场上报，严禁回退引用 `原视频段落`、伪造人工确认或跳步**。
>
> **定岗、步数与必读契约**：池 B **`claude-sonnet-5-5-medium`** 单席位微主管（目标 `12–18 步` · 硬熔断 **`≤ 28 步`**）；派单首行必须显式传入当前阶段单份子卡精确路径并要求开工首步先 `read_file` 后再执行。

## 一、起手第一步：查状态机，只读 P4A 或 P4B 单份子卡

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第NN集>"
```

| 当前状态 / 用户口令 | 唯一必读子卡（严禁一次全读） | 衔接断点 |
| :--- | :--- | :--- |
| **`P4A_VSR_AND_FCPXML`** / “第四棒” / “第四棒A” | [`references/p4a-colab-vsr-fcpxml.md`](./references/p4a-colab-vsr-fcpxml.md) | 完成后进入 **人工断点 ②**（达芬奇 21 导入 `.fcpxml` 渲染导出母带） |
| **`P4B_MASTER_VERIFY` / `WAIT_HUMAN_MASTER_REVIEW`** / “第四棒B” / “成片终检” / “封板” | [`references/p4b-master-verify-seal.md`](./references/p4b-master-verify-seal.md) | 两步封板：`phase4-verify-master` → 人工回看 → `phase4-confirm-master` → `ALL_DONE` |
| **按需备选**：仅用户明确指定 `--target-fps 60` / “60帧高刷” | [`references/optional-60fps-hfr-sop.md`](./references/optional-60fps-hfr-sop.md) | 常规默认 `24fps` **严禁读取**（0 Token） |

## 二、第四棒四档生产矩阵速查（`--p4-profile` + `--aspect-ratio 16:9|9:16` · 全档默认锁 `24fps`）

> **默认档位与画幅自动路由**：全链路画幅默认 `16:9` 横屏（竖屏项目随 `init --aspect-ratio 9:16` 全自动切换）；未显式传 `--p4-profile` 时**默认交付 `1K @ 24fps`**，按第一棒 `style_and_cast.json` 画风自动路由为 **`anime_1k`** 或 **`live_1k`**；**仅用户明确提出 `4K` 需求时**显式启用 `--p4-profile anime_4k`（或预留 `live_4k`）。未显式传 `--target-fps` 时一律硬锁 `24fps`（残留 `target_fps=60` 自动告警覆盖为 `24`）。

| 生产档位 | CLI 参数与规格（`16:9` / `9:16`） | 核心处理路径 | 目录 / FCPXML 格式 / 成片命名 |
| :--- | :--- | :--- | :--- |
| **`动漫 1K`** | `--p4-profile anime_1k`<br/>`1920×1080` / `1080×1920` @ `24fps` | 动漫日常主力档；`realesr-animevideov3.pth` GPU 单步 `antialias` 直出 `1K` | `已超分1K_24fps/` · `FFVideoFormat1920x1080p24` / `1080x1920p24` · `第NN集_1K24fps_成片.mp4` |
| **`动漫 4K`** ⭐ | `--p4-profile anime_4k`<br/>`3840×2160` / `2160×3840` @ `24fps` | 精品大屏主力档；`realesr-animevideov3.pth` + GPU FP32 `bicubic(antialias=True)` 单步直出 `4K` | `已超分4K_24fps/` · `FFVideoFormat3840x2160p24` / `2160x3840p24` · `第NN集_4K24fps_成片.mp4` |
| **`真人 1K`** | `--p4-profile live_1k`<br/>`1920×1080` / `1080×1920` @ `24fps` | 真人主力档；原生 `1080p` 按需口型后直通封装禁假 4K；`<1080p` 走 `RealESRGAN_x2plus.pth` 定标至 `1K` | `已超分1K_24fps/` · `FFVideoFormat1920x1080p24` / `1080x1920p24` · `第NN集_1K24fps_成片.mp4` |
| **`真人 4K`** ⚠️ | `--p4-profile live_4k`<br/>`3840×2160` / `2160×3840` @ `24fps` | 工程接口预留（暂不启用）；未来需高码率源 + `RealESRGAN_x2plus.pth` + 低权重 `CodeFormer`/`GFPGAN` | `已超分4K_24fps/` · `FFVideoFormat3840x2160p24` / `2160x3840p24` · `第NN集_4K24fps_成片.mp4` |
