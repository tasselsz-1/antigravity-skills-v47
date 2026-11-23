---
name: video-recreation-p2
description: >-
  视频复刻第二棒专属流程（V4.7）：执行 Colab VoxCPM 2 48000Hz 声线母本合成与批准清单签发、plan-batches 分波物化、两阶 prototype-sheet 解锁、quota-reserve/reconcile 配额硬闸门机械生图与跨池持图双检归位。当用户说“第二棒”、“P2”、“跑批次生图”、“做声线母本”、“Wave 1/2/3/4”时触发。
---

# 视频复刻 · 第二棒专属总控（P2 · V4.7）

> **🛑 遇错即停铁律**：前置 `verify-script-alignment` 未过、Colab `VoxCPM 2` 合成异常、`voice-audit` 报错、`quota-reserve` 触及 `60` 次上限或 `quota-reconcile` 写入跨窗口全局 429 冷却阻断、`prototype-sheet` / `check_white_background` 报错、持图双检打回 1 次后仍未过，或 `finalize` 哈希/反伪造拦截报错时，**一律原地停机保留现场上报，严禁野脚本、篡改校验、伪造批量通过或跳步**。

## 一、起手第一步：查状态机与平台通道硬边界

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第NN集>"
```

- **定岗与步数预算**：
  - **第二棒微主管**：池 A `gemini-4-hydrogen-medium`（目标 `12–18 步` · 硬熔断 **`≤ 28 步`**），严禁看图、严禁直接打开参考图或生成图；
  - **机械生图包工头**：`model="flash"` 轻量生图通道（单工兵 `≤ 25 步`），100% 照第一棒过审盖戳的 `jobs.json` 英文词与 `image_refs` 机械执行，严禁改词或翻转 `Image 1`；
  - **持图双检员**：池 A `gemini-4-hydrogen-high`（高难度画风一致性疑难批次可派池 B `claude-sonnet-5-5-high` 跨池复核，单工兵 `≤ 25 步`）。
- **🛑 平台调度实测硬边界（必守）**：`swarm` worker 工具集不含 `invoke_subagent` / `generate_image`，因此**第二棒需要调 `image-generator` 的总管与批次生图包工头必须通过 `invoke_subagent` 或 `run_workflow` 拉起**（严禁把生图包工头放进 `swarm` worker）；纯文本/脚本/独立持图双检员可走单席位 `swarm`。
- **工兵派单必读契约（防少读规则）**：派单首行必须显式传入本岗必读精确路径并要求开工首步先 `read_file`——环节 1–3 传 `stage-voice-and-stage-a.md`（执行 `Wave 1/2` 生图双检时同步复用 `stage-batch-gen-and-qc.md` 环节 4–5）；环节 4–5 生图包工头传 `stage-batch-gen-and-qc.md`；持图双检员加传 [`image-generation-quality.md`](../../rules/image-generation-quality.md)（分类补救铁律）。

## 二、按环节精准加载子卡（严禁一次全读）

| 当前进度 / 岗位 | 唯一必读子卡 | 核心门禁与交付 |
| :--- | :--- | :--- |
| **环节 1–3**：声线母本、`plan-batches`、`Wave 1`（肖像+场景）→ `Wave 2`（主体） | [`references/stage-voice-and-stage-a.md`](./references/stage-voice-and-stage-a.md) | Colab `--gpu L4` `VoxCPM 2` 原生 `48000Hz` → `voice-audit` 签发 `声线母本批准清单.json`；`plan-batches` 弹性并窗 `≤ 54` 单元；`prototype-sheet --stage wave1`（2列）与 `--stage wave2`（3列）两阶解锁 |
| **环节 4–5**：`Wave 3`（首帧）→ `Wave 4`（按需尾帧）机械生图、持图双检、`finalize` 与 `organize` | [`references/stage-batch-gen-and-qc.md`](./references/stage-batch-gen-and-qc.md) | `quota-reserve` 原子预占 + `quota-reconcile` 幂等核销（`≤ 60` 次 + 全局 429 冷却）；`image-generator` 单次调用硬锁 + `≤ 2` 路并发；固定 `Image 1 = 净源帧`；显式异源 `run_id` 双检 → `finalize` → `organize` |

> **按需辅卡（仅工兵核对人设/白底规范时读，微主管不读）**：[`character-scene-style/SKILL.md`](../character-scene-style/SKILL.md)

## 三、第二棒收口交接

全部 `Wave 1..4` 批次经 `finalize` 与 `organize` 归位无缺口，且 `声线母本批准清单.json` 已签发后，复跑 `phase-status` 确认推进至 `P3`，向根主控回传 `≤ 15 行` 落盘统计并销毁退出；下一阶段触发 [`video-recreation-p3`](../video-recreation-p3/SKILL.md)。
