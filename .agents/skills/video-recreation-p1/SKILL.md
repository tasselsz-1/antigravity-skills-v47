---
name: video-recreation-p1
description: >-
  视频复刻第一棒专属流程（V4.7）：执行切分与ASR、导演圣经与路由锁模、微场次双轨出词与跨厂商盲审、合稿三表与对齐终检。当用户说“第一棒”、“P1”、“视频切分”、“出脚本和视频提示词”、“P1-窗1/窗2/窗3/窗4”时触发。
---

# 视频复刻 · 第一棒专属总控（P1 · V4.7）

> **🛑 遇错即停铁律**：Colab 异常、`TransNetV2` 失败、`colab_cpu_verified != true`、双盲审打回 1 次仍未过，或 `verify-cuts` / `stamp-visual-audit` / `verify-script-alignment` 返回非零退出码时，**一律原地停机保留现场上报，严禁 `--force-local`、篡改校验、伪造审图或跳步**。

## 一、起手第一步：查状态机，只读当前窗口单份子卡

微主管启动后首步仅运行状态检查，**严禁一次性通读全部 4 个窗口文档**：

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第NN集>"
```

- **微主管步数预算**：目标 `12–18 步`，硬熔断 **`≤ 28 步`**；单名一线工兵 **`≤ 25 步`**，产物落盘后回传 `≤ 5 行` 路径与状态。
- **工兵派单必读契约（防少读规则）**：`swarm`/`invoke_subagent` 默认空白上下文启动，微主管派单首行必须显式传入本窗子卡与对应辅卡的精确路径，要求工兵**开工第一步先 `read_file` 后再执行**，严禁凭记忆盲干。
- **跨厂商调度通道**：精准指定模型 ID（`Neon / Hydrogen / Opus 5.5 / Sonnet 5.5 / Carbon`）使用精益单席位或分片 `swarm(action="add", model="<ID>", tasks=[...])`（验收即 `kill`）；常规高扇出并发可用 `run_workflow`。

## 二、按窗精准路由与双配额池定岗（严格按需单卡加载）

| 当前检查点 / 窗口 | 责任角色与模型定岗（配额池） | 本窗唯一必读子卡（严禁跨窗通读） | 核心落盘与硬门禁 |
| :--- | :--- | :--- | :--- |
| **`P1_W1` 窗 1**<br/>切分与 ASR | 微主管：池 A `gemini-4-hydrogen-medium` + Colab `--gpu T4` | [`references/w1-cut-asr-storyboard.md`](./references/w1-cut-asr-storyboard.md) | `正片母本_CFR.mp4`、`物理切点清单.json`、`asr_transcript.json`、`*_净参考.jpg`、`1–6` 张 `1600px` 故事板；`colab_cpu_verified == true` 并 `colab stop` |
| **`P1_W2` 窗 2**<br/>圣经、路由、切片与 Stage A | 圣经路由：池 A `gemini-4-neon-high`（超长复杂升 `xhigh`）<br/>Stage A 词：池 B `claude-opus-5-5-medium`<br/>Stage A 盲审：池 A `gemini-4-hydrogen-high` | [`references/w2-bible-routing-stage-a.md`](./references/w2-bible-routing-stage-a.md)<br/>按需附读：[`references/flow-routing-rules.md`](./references/flow-routing-rules.md) | `episode_director_bible.json`、`style_and_cast.json`、`cuts.csv`（`verify-cuts ≥ 99.5%` 独立可信终点）、`segment_XX.mp4`、`stage_a_visual_audit.json` |
| **`P1_W3` 窗 3**<br/>微场次出词与跨厂商双盲审 | 刀刃块（`is_golden_15s`/`is_climax`）：池 B `claude-opus-5-5-high`<br/>常规块（~75%）：池 B `claude-sonnet-5-5-medium`<br/>1对1盲审：池 A `gemini-4-hydrogen-high` | [`references/w3-scene-prompts-audit.md`](./references/w3-scene-prompts-audit.md)<br/>执笔/盲审按需附读：[`references/flow-routing-rules.md`](./references/flow-routing-rules.md) | 5 维 `visual_spec` + 5 槽位英文词（固定 `Image 1 = 净源帧`）+ 双轨视频词 + `prompt_visual_audit.json`（显式异源 `run_id` 全单元盖戳） |
| **`P1_W4` 窗 4**<br/>合稿、三表与对齐终检 | 微主管：池 B `claude-sonnet-5-5-medium` | [`references/w4-merge-tables-gate.md`](./references/w4-merge-tables-gate.md) | `Wave 1..4` `待生图帧清单.json`、《导演脚本.md》、《视频提示词.md》（同步 `当前主稿.md` SHA-256）、三表、`verify-script-alignment --require-visual-audit` |

> **按需专业辅卡（仅对应工兵读取，微主管不读）**：
> - 生图英文干净度/禁水印内核与分类补救标准（W2/W3 写词与盲审必读）：[`image-generation-quality.md`](../../rules/image-generation-quality.md)
> - 人物四维设计/防同脸/干净度细则：[`character-scene-style/SKILL.md`](../character-scene-style/SKILL.md)（按需查阅 [`character-face-hair-reference/SKILL.md`](../character-face-hair-reference/SKILL.md)、[`image-style-reference/SKILL.md`](../image-style-reference/SKILL.md)）
> - 导演脚本/分镜头表/即梦中文词格式：[`video-prompt-writer/SKILL.md`](../video-prompt-writer/SKILL.md)
> - 极端跨集冲突或连续打回卡死会诊：按需单席位召唤池 A `gemini-4-carbon-high`（不入常态主线）。

## 三、第一棒收口交接

窗 4 通过 `verify-cuts`（独立可信终点）与 `verify-script-alignment --require-visual-audit` 后，向根主控回传 `≤ 15 行` 落盘统计与文件链接并退出；下一阶段直接触发 [`video-recreation-p2`](../video-recreation-p2/SKILL.md)。
