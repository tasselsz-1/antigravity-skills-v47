# Antigravity Rules & Skills Pack (V4.7 按棒解耦版)

本仓库打包并开源分享 **Antigravity 工作区规则体系（V4.7 按棒解耦版）** 与全量 **17 个拆分/专项 Skill**，覆盖影视级视频复刻四棒流水线（P1–P4 + Fix）、多角色无底拼接与 `Image 2` 场景锁硬门禁、Colab GPU 开源声音模仿与视频配音（VoxCPM 2 / IndexTTS-2.5 / Fish Audio S2 Pro）、Colab CLI + SSH + rclone 算力通道、MiniMax-H3 (ref2va) 视听直出流水线，以及本地人物骨相/发型与商业画风参考资产库。

> 所有规则、脚本与参考数据均已完成本机路径脱敏（统一使用 `<WORKSPACE_ROOT>` 与 `<USER_HOME>` 占位符），通过 `install.ps1` 可一键部署并自动回填至你的本机工作区路径。

---

## 目录结构

```text
.
├── AGENTS.md                           # 工作区常驻规则与按棒单卡路由表（V4.7）
├── install.ps1                         # Windows 一键部署与占位符自动回填脚本
├── global/
│   └── GEMINI.md                       # 全局工作规范模板（部署至 <USER_HOME>/.gemini/config/GEMINI.md）
└── .agents/
    ├── rules/
    │   └── image-generation-quality.md # 工作区生图画质与三图垫图硬规范（-InstallGlobal 时同步至全局 rules）
    └── skills/                         # 全部 17 个拆分与专项 Skill
```

---

## 核心规则体系

| 文件 | 作用 |
| :--- | :--- |
| [`AGENTS.md`](AGENTS.md) | 工作区常驻总规范：顶层绝对红线、Root 主控轻量隔离、按棒按窗单卡加载防断流原则、全量技能路由表 |
| [`global/GEMINI.md`](global/GEMINI.md) | 全局工作规范模板：自然边界分批、多模态看图隔离（单子代理 `≤ 4–6` 张图）、`flash` 分级审图、内置 `image-generator` 单次禁读硬锁、人物去网红滤镜审美原则 |
| [`.agents/rules/image-generation-quality.md`](.agents/rules/image-generation-quality.md) | 生图画质与垫图规范：禁止水印/字标、人物无底干净度、多角色无底拼接图 (`build-char-sheets`) 与 `Image 2` 场景锁铁律 |

---

## 全部 17 个 Skill 清单

### 1. 视频复刻 V4.7 按棒解耦核心体系

| Skill | 职责与核心能力 | 入口 |
| :--- | :--- | :--- |
| `video-recreation` | 超薄总控路由器 + 公共 `scripts/` 宿主（含 `batch_ops.py`、`cloud_pipeline.py` 与 85 项自动化回归套件） | [SKILL.md](.agents/skills/video-recreation/SKILL.md) |
| `video-recreation-p1` | **第一棒（P1）**：按窗拆分 `w1`–`w4` + `flow-routing-rules`，覆盖切分/ASR、圣经与路由锁模、微场次双轨出词跨厂商盲审、合稿三表终检 | [SKILL.md](.agents/skills/video-recreation-p1/SKILL.md) |
| `video-recreation-p2` | **第二棒（P2）**：`VoxCPM 2` 48kHz 声线母本与批准清单、两阶 `prototype-sheet` 解锁、配额硬闸门机械生图、多角色拼接图与跨池持图双检归位 | [SKILL.md](.agents/skills/video-recreation-p2/SKILL.md) |
| `video-recreation-p3` | **第三棒（P3）**：匹配批准 SHA 三元组在 Colab 合成 48kHz 台词/心声（默认 `IndexTTS-2.5`）、防呆投喂手册与静态九项终检 | [SKILL.md](.agents/skills/video-recreation-p3/SKILL.md) |
| `video-recreation-p4` | **第四棒（P4A / P4B）**：默认 `1K@24fps` 四档矩阵、Colab 按需 LatentSync 1.6 口型与超分、FCPXML 1.9 交接与成片两步封板（可选 60fps 子卡） | [SKILL.md](.agents/skills/video-recreation-p4/SKILL.md) |
| `video-recreation-fix` | **按需返修**：定点改图、单帧返修、Stage A 角色/场景母图修改与达芬奇相邻镜头转场重对齐 | [SKILL.md](.agents/skills/video-recreation-fix/SKILL.md) |

### 2. 兼容入口（薄路由壳）

- [`cinema-hybrid-workflow`](.agents/skills/cinema-hybrid-workflow/SKILL.md) — 旗舰全生态视频复刻兼容入口
- [`dual-drama-workflow`](.agents/skills/dual-drama-workflow/SKILL.md) — 双配额池异构兼 Codex/GPT 双端接力兼容入口
- [`dual-model-video-workflow`](.agents/skills/dual-model-video-workflow/SKILL.md) — Google Flow + 即梦 Seedance 2.0 双模混编兼容入口
- [`veo-cinema-workflow`](.agents/skills/veo-cinema-workflow/SKILL.md) — 纯 Google Flow (Veo 3.1 Lite) 兼容入口

### 3. 创作参考、资产库与云端算力专项 Skill

| Skill | 职责与核心能力 | 入口 |
| :--- | :--- | :--- |
| `video-prompt-writer` | 导演脚本、双轨视频提示词（Flow 英文闭嘴词 vs 即梦中文音画词）、分镜头/台词/字幕三表执笔规范 | [SKILL.md](.agents/skills/video-prompt-writer/SKILL.md) |
| `character-scene-style` | 人物四维重塑、防同脸与干净度、扁平帧命名、多角色无底拼接与正式集九项结构参考 | [SKILL.md](.agents/skills/character-scene-style/SKILL.md) |
| `character-face-hair-reference` | 人物骨相 + 发型参考资产库（7 维设计公式、A001–A007 解剖原图、B001–B024 骨相、H001–H042 发型 + 本地检索工作台） | [SKILL.md](.agents/skills/character-face-hair-reference/SKILL.md) |
| `image-style-reference` | 两层本地画风参考库（第一梯队 77 套成熟商业化画风 + 第二梯队 68 套基础参考画风，共 S001–S144） | [SKILL.md](.agents/skills/image-style-reference/SKILL.md) |
| `colab-voice-dubbing` | Colab 开源声音模仿与视频配音单一事实源（`VoxCPM 2` / `IndexTTS-2.5` / `Fish Audio S2 Pro` + H200 `.ipynb`） | [SKILL.md](.agents/skills/colab-voice-dubbing/SKILL.md) |
| `colab-ssh-rclone` | 基于 `google-colab-cli` 与 `rclone` 的 Colab 云端 GPU 操控与 Google Drive 内网直连规范 | [SKILL.md](.agents/skills/colab-ssh-rclone/SKILL.md) |
| `h3-chupian` | MiniMax-H3 (`ref2va`) 故事板与人物/场景/音色图转专用提示词、ComfyUI API 任务包构建与 Colab 无损 BF16 出片 | [SKILL.md](.agents/skills/h3-chupian/SKILL.md) |

---

## 快速安装与使用

### 方式一：使用 `install.ps1` 一键部署到你的 Antigravity 工作区（推荐）

```powershell
# 克隆仓库
git clone https://github.com/tasselsz-1/antigravity-skills-v47.git
cd antigravity-skills-v47

# 部署到指定工作区（自动备份已有 AGENTS.md / .agents，并将占位符回填为本机真实路径）
.\install.ps1 -WorkspaceRoot "D:\my-workspace"

# 若同时部署全局规则模板（写入 $HOME\.gemini\config\GEMINI.md 与 rules\image-generation-quality.md）
.\install.ps1 -WorkspaceRoot "D:\my-workspace" -InstallGlobal

# 先预览不落盘
.\install.ps1 -WorkspaceRoot "D:\my-workspace" -InstallGlobal -DryRun
```

### 方式二：直接下载 Release `.zip` 压缩包

从 GitHub Releases 下载 `antigravity-skills-v47.zip`，解压后按上述命令执行 `install.ps1` 或手动将 `AGENTS.md` 与 `.agents/` 拷贝至工作区根目录。

---

## 本地自检与自动化回归测试

依赖：`Python >= 3.11`、`pillow`、`numpy`、`lxml`（用于 FCPXML 1.9 DTD 严格校验）。

```powershell
# 运行视频复刻 V4.7 公共脚本全量自动化回归套件（85 项用例）
python .agents/skills/video-recreation/scripts/run_v42_regression_suite.py

# 查看 batch_ops.py 全部子命令（含 build-char-sheets / plan-batches / quota-reserve 等）
python .agents/skills/video-recreation/scripts/batch_ops.py --help
```

---

## 隐私与安全说明

- 本仓库**不包含**任何 `.env`、API Key、OAuth Token、SSH 私钥、`rclone.conf` 或个人项目素材。
- 云端算力与配音脚本依赖使用者在本地自行配置的环境变量、`.env`（位于 `<WORKSPACE_ROOT>/.env`）、`google-colab-cli` 与 `rclone` 凭据。
- `character-face-hair-reference` 与 `image-style-reference` 中的视觉样本均严格标注为“仅作审美与结构参考，不可直接商用”。
