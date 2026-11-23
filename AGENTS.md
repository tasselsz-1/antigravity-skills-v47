# Antigravity 工作区常驻规范与技能路由（V4.7 按棒解耦版）

默认使用中文回复用户，除非用户明确要求使用其他语言。

## 1. 顶层绝对红线（全员常驻）

- **Skill 即流程铁律（遇错即停、立即上报、严禁私自绕行）**：任意云端任务、脚本门禁（`verify-*` / `stamp-visual-audit` / `voice-audit` / `quota-reserve` / `quota-reconcile` / `finalize` / `check-delivery` / `build-fcpxml` / `phase4-verify-master` / `phase4-confirm-master`）返回非零退出码或双盲审未过时，**一律原地停机保留现场并上报，严禁 `--force-local`、篡改校验、伪造通过或跳步**。
- **全局画质与干净度红线**：严禁生成任何水印、“AI 生成”字样、平台/工具 Logo、签名、角标或二维码；Colab 任务完成后必须刷盘并 `runtime.unassign()` 释放运行时，**默认优先使用 Colab GPU 极速通道**（用户已授权，省时降本）：P1 `--gpu T4`、P2/P3 配音与 P4 `--gpu L4`（按需 `A100`/`H200`）；任何 GPU 作业必须 `try/finally` 强制 `rm -f /tmp/rclone.conf` + `colab stop`，并以 `colab sessions` / `colab usage` 核验 `Active assignments: 0`、`Usage rate: 0.00/hr`，严禁留孤儿 GPU 实例。
- **统一配音引擎栈（选型与环境规范唯一事实源见 `colab-voice-dubbing`）**：P2 声线母本（`--stage voice-ref`）默认 **`VoxCPM 2`**（`openbmb/VoxCPM2`，原生 `48000Hz`、`--gpu L4`）捏 `说话`+`心声` 双母本，经 `voice-audit` 签发批准清单；P3 台词配音（`--stage dialogue`）默认 **`IndexTTS-2.5`**（情绪解耦与精准卡时长主力），按需切 **`Fish Audio S2 Pro`**（长旁白/拟人）或 **`VoxCPM 2`**（方言/免费商用），一律硬守批准 SHA-256 三元组与 `48000Hz Mono pcm_s16le`。

## 2. 根主控轻量隔离与按棒单卡加载原则

- **根主控窗口（Root）**：只对话与派单调度，**严禁直接看图、严禁跨棒通读细则、严禁直接跑批处理脚本或越级直调一线工兵**；收到具体棒次指令后，直接路由对应棒次的独立 Skill 派发全新微主管，回收落盘摘要后释放席位。
- **按棒按窗单卡加载（防上下文膨胀断流）**：微主管与工兵**只读自己当前棒次（乃至当前单个窗口/环节）的唯一独立 Skill 与单份子卡**，严禁跨棒通读其他棒次规则。
- **派单必指定文档、开工必先读**：派单方必须在 Prompt 中显式指定本岗唯一必读路径；任何微主管与工兵开工前必须先 `read_file` 对应文档，严禁未读文档凭经验开工。

## 3. 按需技能路由表（按棒独立触发，零跨棒冗余）

| 触发口令 / 任务类型 | 按需直达的独立 Skill / 子卡 |
| :--- | :--- |
| 视频复刻 / 复刻主控 / 主控开跑（未指明具体棒次） | [`video-recreation/SKILL.md`](.agents/skills/video-recreation/SKILL.md)（超薄总控路由器 + 公共 `scripts/` 宿主） |
| **第一棒 / P1**（切分ASR / 圣经路由 / 微场次出词双盲审 / 合稿终检） | [`video-recreation-p1/SKILL.md`](.agents/skills/video-recreation-p1/SKILL.md)（按窗独立子卡 `w1`–`w4` + `flow-routing-rules`） |
| **第二棒 / P2**（48kHz声线母本 / 两阶 `prototype-sheet` / 配额闸门批次生图） | [`video-recreation-p2/SKILL.md`](.agents/skills/video-recreation-p2/SKILL.md)（按环节拆 `stage-voice-and-stage-a` / `stage-batch-gen-and-qc`） |
| **第三棒 / P3**（匹配批准 SHA 克隆台词 / 防呆投喂手册 / 静态九项终检） | [`video-recreation-p3/SKILL.md`](.agents/skills/video-recreation-p3/SKILL.md)（单卡自洽，零第四棒冗余） |
| **第四棒 / P4 / P4A / P4B**（四档默认 `1K@24fps` / 超分口型 / FCPXML 1.9 / 成片两步封板） | [`video-recreation-p4/SKILL.md`](.agents/skills/video-recreation-p4/SKILL.md)（按阶段读 `p4a-colab-vsr-fcpxml` 或 `p4b-master-verify-seal`） |
| **定点改图 / 单帧返修 / 转场重对齐**（默认不触发） | [`video-recreation-fix/SKILL.md`](.agents/skills/video-recreation-fix/SKILL.md) |
| **60fps 高刷插帧模式**（默认不触发，仅用户明确指定 `--target-fps 60`） | [`optional-60fps-hfr-sop.md`](.agents/skills/video-recreation-p4/references/optional-60fps-hfr-sop.md) |
| 导演脚本 / 分镜头表 / 台词表 / 字幕 / 双轨视频提示词（P1 执笔参考） | [`video-prompt-writer/SKILL.md`](.agents/skills/video-prompt-writer/SKILL.md) |
| 人物/场景设计 / 防同脸 / 干净度 / 正式集九项结构（P1/P2 参考） | [`character-scene-style/SKILL.md`](.agents/skills/character-scene-style/SKILL.md) |
| 人物骨相/发型参考库 / 7 维公式（A001–A007 / B001–B024 / H001–H042，仅作参考不可直用） | [`character-face-hair-reference/SKILL.md`](.agents/skills/character-face-hair-reference/SKILL.md) |
| 画风参考库查询与复用（S001–S144） | [`image-style-reference/SKILL.md`](.agents/skills/image-style-reference/SKILL.md) |
| Colab 开源声音模仿与视频配音（IndexTTS-2.5 / Fish S2 Pro / VoxCPM 2） | [`colab-voice-dubbing/SKILL.md`](.agents/skills/colab-voice-dubbing/SKILL.md)（独立配音工作台与 P2/P3 引擎选型唯一事实源，含 H200 `.ipynb`） |
| Colab 云端算力 / SSH 操控 Colab / Colab 内网直连 G 盘 | [`colab-ssh-rclone/SKILL.md`](.agents/skills/colab-ssh-rclone/SKILL.md)（默认 `colab` CLI + `rclone gdrive:`；P1 `T4` / P2–P4 `L4`，结束必删 `/tmp/rclone.conf` 并 `colab stop`） |
| **h3出片** / H3出片 / 故事板+人物图转 MiniMax-H3 Colab 出视频 | [`h3-chupian/SKILL.md`](.agents/skills/h3-chupian/SKILL.md)（直喂提示词+故事板+人物图或 `ai-anime-series` 目录 → 转 H3 专用词 → 默认 Colab G4 无损 BF16 出片与抽帧质检） |
