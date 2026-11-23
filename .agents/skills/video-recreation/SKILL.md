---
name: video-recreation
description: >-
  视频复刻 V4.7 轻量总控路由器（公共 scripts 宿主）。仅当用户说“视频复刻”、“复刻主控”、“主控开跑”或未指定棒次需按 phase-status 路由时触发；各棒细则已拆分为独立 Skill（video-recreation-p1/p2/p3/p4/fix），按棒直达，严禁一次通读全部规则。
---

# 视频复刻 V4.7 · 轻量总控路由器（Root）

> **🛑 顶层绝对红线（Skill 即流程 · 遇错即停）**：任意云端任务、脚本门禁（`verify-*` / `stamp-visual-audit` / `voice-audit` / `quota-reserve` / `quota-reconcile` / `finalize` / `check-delivery` / `build-fcpxml` / `phase4-verify-master` / `phase4-confirm-master`）返回非零退出码或双盲审打回 1 次仍未过时，**一律原地停机保留现场上报，严禁 `--force-local`、篡改校验、伪造通过或跳步**。

## 一、根主控窗口（Root）六不准与轻量铁律

- **推荐模型**：池 A `gemini-4-hydrogen-medium`（全集常驻上下文 `≤ 15k Tokens`）。
- **六不准**：严禁看图、严禁通读各棒子卡/细则、严禁直接跑批处理脚本、严禁写业务大文件、严禁越级直调一线工兵、严禁向旧主管续命（每棒/每窗全新微主管按 `phase-status` 无损接力）。
- **输出上限**：Plan Mode 调 `exit_plan_mode`，普通模式输出 `≤ 10 行` 派单计划；验收后向用户输出 `≤ 15 行` 摘要。
- **派单铁律**：派发微主管时，**Prompt 中必须只指定当前这一棒（乃至当前单个窗口/环节）的唯一独立 Skill 路径**，严禁让微主管跨棒通读其它棒次规则！

## 二、全四棒状态机 → 按棒独立 Skill 精准路由表

> 公共脚本目录固定保留于 `<WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/`（`batch_ops.py` / `cloud_pipeline.py` 等全棒共用，严禁挪动）。
> 查当前进度唯一命令：`python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第NN集>"`

| `phase-status` 状态 / 用户口令 | 直达独立 Skill（派单必指定） | 调度微主管与通道 | 内部按需子卡（只读当前单份） |
| :--- | :--- | :--- | :--- |
| **`INIT` / `P1_W1..W4`**<br/>“第一棒 / P1 / 切分 / 出脚本提示词” | [`video-recreation-p1`](../video-recreation-p1/SKILL.md) | W1：`Hydrogen Medium`<br/>W2–W3：`Neon`+`Opus/Sonnet`↔`Hydrogen High`（单席位/分片 `swarm`）<br/>W4：`Sonnet 5.5 Medium` | 按窗拆 4 份：`w1` / `w2` / `w3` / `w4` + `flow-routing-rules`（仅 W2/W3） |
| **`P2_VOICE_AND_WAVES`**<br/>“第二棒 / P2 / 声线母本 / 批次生图” | [`video-recreation-p2`](../video-recreation-p2/SKILL.md) | `Hydrogen Medium`<br/>（**生图总管与 `model="flash"` 包工头必经 `invoke_subagent`/`run_workflow`，禁进 `swarm`**） | 按环节拆 2 份：`stage-voice-and-stage-a` / `stage-batch-gen-and-qc` |
| **`P3_DIALOGUE_AND_DELIVERY`**<br/>“第三棒 / P3 / 克隆台词 / 投喂手册 / 九项终检” | [`video-recreation-p3`](../video-recreation-p3/SKILL.md) | `Sonnet 5.5 Medium`（单席位 `swarm`） | 本 Skill 完整自洽，零额外跳读（通过后进人工断点 ① 生视频） |
| **`P4A_VSR_AND_FCPXML`**<br/>“第四棒 / P4A / 超分刷口型 / FCPXML” | [`video-recreation-p4`](../video-recreation-p4/SKILL.md) | `Sonnet 5.5 Medium`（单席位 `swarm`） | 仅读 `p4a-colab-vsr-fcpxml`（通过后进人工断点 ② 达芬奇 21 导出）；仅 `--target-fps 60` 才读 `optional-60fps-hfr-sop` |
| **`P4B_MASTER_VERIFY` / `WAIT_HUMAN_MASTER_REVIEW`**<br/>“第四棒B / P4B / 成片终检 / 封板” | [`video-recreation-p4`](../video-recreation-p4/SKILL.md) | `Sonnet 5.5 Medium`（单席位 `swarm`） | 仅读 `p4b-master-verify-seal`（自动门禁 `MASTER_TECH_SPEC_PASSED` → 人工回看 `phase4-confirm-master` → `MASTER_SEALED_OK` → `ALL_DONE`） |
| **按需专项（默认不触发）**<br/>“定点改图 / 单帧返修 / 转场重对齐” | [`video-recreation-fix`](../video-recreation-fix/SKILL.md) | 派 `invoke_subagent(TypeName="self", Role="第NN集_定点改图_执行总管")` | 本 Skill 完整自洽，零额外跳读 |

## 三、双配额池四核异构定岗速查（完整基线按需查参考卡）

- **配额池 A（Google Gemini）**：`Gemini 4 Neon (High/XHigh)` 任 W2 导演圣经锁模；`Gemini 4 Hydrogen (Medium/High)` 任 Root/W1/P2 微主管与 W2/W3/P2 视觉盲审双检；`Gemini 4 Carbon (High)` 仅极端疑难会诊兜底；`3.8 Flash` 退守 `model="flash"` 机械生图包工头；**淘汰 `Argon`**。
- **配额池 B（Anthropic）**：`Claude Opus 5.5 (Medium/High)` 任 Stage A 定调与 W3 刀刃块；`Claude Sonnet 5.5 (Medium/High)` 任 W3 常规块、W4 合稿、P3/P4 微主管及 P2 疑难跨池复核；**禁用无视觉 `GPT-OSS 120B`**。
- **步数预算**：微主管目标 `12–18 步`（硬熔断 `≤ 28 步`）；一线工兵单次 `≤ 25 步`，回传 `≤ 5 行`。
- 完整 9 系列/27 ID 实力表、双通道边界与 `stamp-visual-audit` 哈希契约：[`references/master-controller-quota-routing.md`](./references/master-controller-quota-routing.md)（仅架构核验按需读取）。
