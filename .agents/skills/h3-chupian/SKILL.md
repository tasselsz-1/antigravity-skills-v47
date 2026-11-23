---
name: h3-chupian
description: >-
  h3出片：接收用户提供的提示词（即梦/通用/自由文本）、4格或N格故事板、人物图、场景图与可选音色音频（或现有 ai-anime-series 目录），
  自动转换为 MiniMax-H3 (ref2va) 专用提示词（<Picture 1..N> / <Audio 1..M>、阅读顺序与防边框约束、口型音画同步）并构建 ComfyUI API 任务包，
  通过本机 colab CLI + SSH + rclone 直连 Google Colab G4 (96GB) / H100 / H200 Notebook 默认以无损 BF16 (h200-max) 视听一体直出视频并抽帧质检。
  触发口令：「h3出片」「H3出片」「用H3生视频」「转H3专用」「故事板H3出片」「MiniMax-H3出片」。
---

# h3出片（MiniMax-H3 Colab 视听一体出片工作流）

本 Skill 将用户零散提供的**提示词 + 故事板 + 人物/场景图 + 可选音频**（或已有 `ai-anime-series` 系列工程），一键转换为 **MiniMax-H3 (`ref2va`) 专用任务包**，并通过本机 `colab` CLI + `rclone` 调度 **Google Colab G4 (96GB Blackwell)** 默认无损 BF16 直出生视频，跑完自动关机核验归零并抽帧质检。

---

## 0. 绝对红线

1. **画质不妥协默认档**：正式出片默认 **`-Gpu G4 -Profile h200-max -Resolution 768p`**（纯 BF16 `ref2va_pruned_bf16` + BF16 `Qwen3-VL-32B` + `FP16` Video VAE + `FP32` Audio VAE + `ref_image_size=max`），零量化降质；仅当用户明确要求快速试节奏/省 CU 时才切 `-Resolution 480p -Turbo` 或 `-Profile h200-fast`。
2. **严禁空烧 GPU**：上云前必须确认所有参考图/故事板/音频本地真实存在、H3 提示词已通过 `validate_h3_prompt` 硬校验（无 `@图N` 残留、无 `<Picture K>` 越界）、时长符合 5–15 秒原生上限（超 15 秒必拆段）。
3. **强制闭环关机与凭据零泄露**（对齐 [`colab-ssh-rclone`](../colab-ssh-rclone/SKILL.md)）：严禁打印 `rclone.conf`、`colab_ed25519`、`HF_TOKEN`；严禁运行交互命令；所有云端任务经 [`run_colab_cli.ps1`](./scripts/run_colab_cli.ps1) 的 `try/finally` 自动删远端临时凭据、`colab stop` 并执行 `colab sessions` + `colab usage` 核验 `Active assignments: 0`、`Usage rate: 0.00/hr`。遇非零退出码立即停机上报，严禁伪造通过。
4. **看图隔离**：主窗口严禁连续读取多张素材图或抽帧拼图；如需看故事板/人物图提取外观描述，或出片后检查抽帧拼图，必须派发 **`flash` 子代理**（`Model: "flash"`，单批 ≤ 4–6 张）返回纯文本观察结论。

---

## 1. 两种输入入口识别

收到用户触发「h3出片」及素材后，先判定属于哪一类入口：

- **入口 A：直喂模式（最常用）** — 用户给出提示词文本（含即梦 `@图N` 或纯文字分镜）+ 本地故事板路径 + 人物/场景图路径 + 角色信息/时长/画幅要求（可能还带参考音频）。
- **入口 B：`ai-anime-series` 标准工程模式** — 用户直接给出已建好的 `series.json` + `refs/` + `epNN/episode.json` + `epNN/故事板.png` 目录。

---

## 2. 标准 5 步出片 SOP

### 第 1 步：收料与核查
1. 盘点用户给出的所有本地文件路径（人物图、配角/场景图、故事板、可选音频）是否真实存在、数量是否 ≤ 9 张图且 ≤ 3 段音频。
2. 如需从图片核实人物外形细节或 4 格构图顺序，派 1 个轻量 `flash` 子代理（`Model: "flash"`）读取素材并输出简洁的文字锚点（角色外观特征、4 格画面内容、画幅比例）；若用户文字已给全，无需多余读图。
3. 确定输出工程目录（若用户未指定，默认放在 `<WORKSPACE_ROOT>\h3_projects\<项目拼音或简名>\ep01`）、目标画幅（默认 `16:9`）、目标时长（4 格推荐 `12` 秒；若剧情超过 15 秒，拆成 `part A` + `part B` 两段各 10–15 秒）。

### 第 2 步：转换为 MiniMax-H3 专用提示词
开工转换前务必先阅读 [`references/h3-prompt-conversion.md`](./references/h3-prompt-conversion.md)，完成以下转换：
- 所有 `@图1..N` / 角色名引用 → 严格对齐 `<Picture 1..N>`，音频引用 → `<Audio 1..M>`；
- 顶部强制写入故事板网格阅读顺序（如 `2行2列四宫格分镜故事板，按左上→右上→左下→右下顺序推进`）与**严禁渲染分镜黑框/白边/宫格分割线/序号**条款；
- 逐一锚定角色外观辨识点与音色绑定；
- 写清画风、场景一致性、有/无台词模式，以及逐镜时间轴（景别动作 + 精确说话人口型对白 + 音效配乐）；
- 末尾锁定 `全程全屏24fps流畅渲染，画面干净无任何文字、字幕、水印、Logo或分镜边框`。
- 将转换好的 H3 专用提示词写入本地文件（如 `<工程目录>\ep01\h3_prompt_src.txt`）。

### 第 3 步：打包生成 H3 任务单并硬校验
- **入口 A（直喂模式）** — 调用 [`scripts/pack_direct_job.py`](./scripts/pack_direct_job.py)：
  ```powershell
  python <WORKSPACE_ROOT>\.agents\skills\h3-chupian\scripts\pack_direct_job.py `
    --out-dir "<WORKSPACE_ROOT>\h3_projects\<项目名>" `
    --episode ep01 `
    --prompt-file "<WORKSPACE_ROOT>\h3_projects\<项目名>\ep01\h3_prompt_src.txt" `
    --ref "角色A=<角色A图片路径>" `
    --ref "角色B=<角色B图片路径>" `
    --storyboard "<4格故事板路径>" `
    --duration 12 --ratio 16:9 --resolution 768p --profile h200-max
  ```
  - 可选：`--audio "角色A=<童声参考.wav>"`（最多 3 段）、`--board-first`（故事板放 `<Picture 1>`）、`--seed <整数>`、`--turbo`（4 步速抽）。
  - 多段剧情（>15s）：第一段传 `--multi-part --part A`，第二段传 `--multi-part --part B --append`，自动合并进同一 `manifest.json` 一次上云连生。
- **入口 B（`ai-anime-series` 工程模式）** — 调用 [`scripts/make_h3_jobs.py`](./scripts/make_h3_jobs.py)：
  ```powershell
  python <WORKSPACE_ROOT>\.agents\skills\h3-chupian\scripts\make_h3_jobs.py "<SERIES目录>" ep01 --profile h200-max --resolution 768p
  ```
- **开跑前展示核对摘要**（若用户说“转换完直接开生”则打包校验通过后立即执行第 4 步；若用户要求先看词再跑则先给用户过目）：列出 `<Picture 1..N>` / `<Audio 1..M>` 映射表、帧数/分辨率/档位与转换后的 H3 提示词全文。

### 第 4 步：启动 Colab GPU 无损出片（含自动缓存与强关机）
- **默认主通道（本机全自动 CLI · Colab G4 96GB 无损 BF16）** — 调用 [`scripts/run_colab_cli.ps1`](./scripts/run_colab_cli.ps1)：
  ```powershell
  # 入口 A（直喂已打包好 manifest，必加 -SkipBuild 防止覆盖自定义 H3 提示词）
  powershell -ExecutionPolicy Bypass -File <WORKSPACE_ROOT>\.agents\skills\h3-chupian\scripts\run_colab_cli.ps1 `
    -SeriesLocalDir "<WORKSPACE_ROOT>\h3_projects\<项目名>" `
    -Episode "ep01" `
    -Gpu "G4" `
    -SkipBuild

  # 入口 B（直接从 ai-anime-series 编译并上云）
  powershell -ExecutionPolicy Bypass -File <WORKSPACE_ROOT>\.agents\skills\h3-chupian\scripts\run_colab_cli.ps1 `
    -SeriesLocalDir "<SERIES目录>" `
    -Episode "ep01" `
    -Gpu "G4" `
    -Profile "h200-max" `
    -Resolution "768p"
  ```
  - **免 Token & 云盘秒拉缓存**：`Comfy-Org/MiniMax-H3` 为公开仓库，无需强制 `HF_TOKEN`；脚本默认对接 `gdrive:models/minimax_h3`（已预热全套无损 `h200-max` 权重）按 `manifest` 精确单文件内网缓存（内网专线直拉原位、零双份占盘），并采用 Detached 后台执行 + 20s 短连接轮询，彻底免疫长连接中断。
  - **备选 H200 (141GB) 网页通道**：若需开 H200 或交互式 WebUI 精调，先用 `rclone copy` 将本地工程推到 `gdrive:ai_anime/<项目名>`，在 Colab 网页端打开 [`resources/MiniMax_H3_Colab_H200.ipynb`](./resources/MiniMax_H3_Colab_H200.ipynb) 选 H200 运行即可（`USE_EXISTING_MANIFEST = True`）。
- 确认结束日志显示 `Active assignments: 0` 与 `Usage rate: 0.00/hr`，且成片已同步回本地 `<工程目录>\epNN\clips\*_h3.mp4`。

### 第 5 步：抽帧视觉质检与交付/迭代
1. `run_colab_cli.ps1` 会自动运行 `ai-anime-series/scripts/review_clip.py` 在 `epNN/review/` 生成抽帧拼图（`*_帧.jpg`）。
2. 派发 **`flash` 审图子代理**（`Model: "flash"`）读取抽帧拼图对照 H3 提示词做 6 项质检（无边框水印、角色锁脸、4 格顺序节奏、口型表情、场景连贯、有效音轨）。
3. 向用户汇报：本地成片可点击路径、G 盘云端路径、视频参数（分辨率/帧率/时长/音轨）、质检结论与 Colab 剩余计算单元余额。
4. 若多段出片且质检全过，可调用 `ai-anime-series/scripts/compile.py` 拼合完整一集；若需微调重抽，按 [`references/h3-prompt-conversion.md`](./references/h3-prompt-conversion.md) 定向修改 `custom_h3_prompt.txt` 或换 `--seed` 重跑。

---

## 3. 附带资源一览

- [`scripts/pack_direct_job.py`](./scripts/pack_direct_job.py)：直喂模式打包 + 提示词硬校验器
- [`scripts/make_h3_jobs.py`](./scripts/make_h3_jobs.py)：`ai-anime-series` 标准格式 H3 编译器
- [`scripts/colab_h3_worker.py`](./scripts/colab_h3_worker.py)：Colab 云端 ComfyUI + MiniMax-H3 执行引擎
- [`scripts/run_colab_cli.ps1`](./scripts/run_colab_cli.ps1)：本机 `colab` CLI + `rclone` 一键调度器（Detached 断线免疫 + 强制关机归零）
- [`scripts/precache_colab_cli.ps1`](./scripts/precache_colab_cli.ps1) / [`scripts/precache_models.py`](./scripts/precache_models.py)：低成本 Colab CPU 权重预热器（流式单文件校验写入 `gdrive:models/minimax_h3`，支持断点续传）
- [`scripts/build_notebook.py`](./scripts/build_notebook.py)：重生成同步版 H200 Notebook
- [`resources/MiniMax_H3_Colab_H200.ipynb`](./resources/MiniMax_H3_Colab_H200.ipynb)：Colab Pro+ H200 网页端自包含工作台
- [`resources/template_kids_4grid/`](./resources/template_kids_4grid/series.json)：4 格 12 秒中文对白少儿动漫示例模板
- [`references/h3-prompt-conversion.md`](./references/h3-prompt-conversion.md)：H3 提示词转换模板、档位对比与返工手册
