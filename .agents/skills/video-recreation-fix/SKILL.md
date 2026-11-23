---
name: video-recreation-fix
description: >-
  视频复刻按需专项：定点改图、单帧返修、Stage A 角色/场景母图修改与达芬奇相邻镜头转场重对齐（V4.7）。常规第一至四棒不触发；仅当用户明确提出“改图”、“单帧返修”、“某张图重画”、“转场重对齐”时触发。
---

# 视频复刻 · 定点改图与单帧返修专属流程（V4.7）

> **零冗余隔离与遇错即停铁律**：
> 1. 常规“第一棒～第四棒”流水线中不触发本 Skill；仅当用户提出明确改图/返修/转场重对齐意见时，根主控调用 1 次 `invoke_subagent(TypeName="self", Role="第NN集_定点改图_执行总管", ...)`（目标 `12–18 步` · 硬熔断 `≤ 28 步`）按本 SOP 闭环执行。
> 2. **根主控与改图总管严守“绝对零看图” + 派单必读契约**：派单首行必须显式传入本卡路径并要求工兵开工首步先 `read_file`；看错图、源帧、参考图及新生成图一律交给视觉工兵（池 A `gemini-4-hydrogen-high`，高难度画风复核派池 B `claude-sonnet-5-5-high`，单工兵 `≤ 25 步`，必附读 [`image-generation-quality.md`](../../rules/image-generation-quality.md) 分类补救铁律；改 Stage A 母图或朝向锁定按需附读 [`character-scene-style`](../character-scene-style/SKILL.md)）；调 `image-generator` 生图必须经 `invoke_subagent` / `run_workflow`（`model="flash"` 通道，严禁放进 `swarm` worker）且加单次调用硬锁。
> 3. 遇 `quota-reserve` / `quota-reconcile` 429 冷却 / `prototype-sheet` / `stamp-visual-audit` / `finalize` 非零退出一律原地停机上报，严禁私自绕行。

---

## 一、三大高频场景与级联门禁提醒

1. **单帧或多帧分镜图返修**：如“某段首帧眼神不对”“某尾帧手部多指”。
2. **相邻镜头转场重对齐（上镜尾帧 ↔ 下镜首帧）**：先固定或修好上镜尾帧，再将其作为参考锚点锁定下镜首帧的机位角度、站位、动势终点与光影方向。
3. **Stage A 角色母图（`_肖像.png` / `_主体.png`）或场景母图返修**：
   - 修改 `Wave 1` `_肖像.png` 必重过 `#FFFFFF` 纯白底像素门禁与 **`prototype-sheet --stage wave1`**（2 列核验表）后方解锁 `Wave 2` `_主体.png`；
   - 修改 `_肖像.png` 或 `_主体.png` 归位后，必须重过 **`prototype-sheet --stage wave2`**（3 列一致性总表）并立即运行 **`batch_ops.py build-char-sheets --work-dir "<中间文件/在制工作区/第NN集>"`** 按最新母图 SHA 自动重拼刷新 `角色合集缓存/合集_*.png`，方放行下游引用分镜；是否联动重跑下游分镜严格遵照用户指定范围。

---

## 二、定点改图 5 步手术闭环

### 步骤 1：纯文本精准定位目标单元（不读大图）
检索 `<在制工作区/第NN集>/生成批次/批次XX/jobs.json`、`单元XX_<unit_id>/完整提示词.txt`（备选 `待生图帧清单.json` / `文件归位清单.json`），提取 4 项关键路径：`<batch_dir>` 与 `<unit_id>`、净参考源帧 `ref_clean_frames/seg_XX_shot_YY_first_净参考.jpg`（及原片源帧）、`image_refs` 列表、当前待修错图路径与原 5 槽位英文词。

### 步骤 2：派发 `Hydrogen High` 视觉工兵诊断并覆写 5 槽位英文词
- 视觉工兵对比【源帧 + Stage A 母图 + 当前错图 + 用户意见】诊断缺陷（俯仰角偏移、盯镜头摆拍、手部穿模、非 `#FFFFFF` 白底、场景时态冲突或净空三禁等）；
- 严守分镜帧 **Gemini 5 槽位英文格式 + 三槽位硬锁**（`Image 1 = 净源帧`、`Image 2 = 同状态场景母图/尾帧同段首帧` 严禁被人物挤占、`Image 3 = 单人无底母图或角色合集缓存/合集_*.png`），覆写 `<batch_dir>/单元XX_<unit_id>/完整提示词.txt` 并同步更新 `jobs.json`（及 `待生图帧清单.json` 对应单元）。
- **哈希自动失效**：改词或换图后 `prompt_sha256` / `refs_sha256` 变更，旧审核回执自动失效，步骤 4 必须重新双检盖戳。

### 步骤 3：`quota-reserve` 预占 + `image-generator` 单次硬锁生图 + `quota-reconcile` 幂等核销 + `save-unit`
1. 原子预占额度获取 `reservation_id`（触及 `60` 次上限或跨窗全局 429 冷却即停机）：
   ```powershell
   python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py quota-reserve --work-dir "<中间文件/在制工作区/第NN集>" --unit "<unit_id>" --kind remedy
   ```
2. 经 `invoke_subagent` 派生图工兵原样传 `image_refs`、原画幅与更新后的英文词调 `image-generator`——**外层指令开头必加单次硬锁**：
   > `CRITICAL EXECUTION LIMIT: Call generate_image AT MOST ONCE (1 single call). Do NOT inspect, critique, edit, or regenerate after receiving an image, and do NOT auto-retry on EOF/empty/429 errors — immediately return either the saved image path or the exact error message.`
3. 返回后按 `reservation_id` 幂等核销并落盘 `save-unit`（Stage A 返修紧接重跑对应 `prototype-sheet --stage wave1` 或 `--stage wave2`）：
   ```powershell
   python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py quota-reconcile --work-dir "<中间文件/在制工作区/第NN集>" --reservation-id "<reservation_id>" --outcome ok
   python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py save-unit --jobs-file "<所属批次目录/jobs.json>" --unit "<unit_id>" --tool-png "<image-generator生成的临时图片路径>"
   ```

### 步骤 4：独立异源视觉工兵 1K 复检与显式 `run_id` 重绑哈希
- 派独立异源视觉工兵（池 A `gemini-4-hydrogen-high` 或高难度画风复核池 B `claude-sonnet-5-5-high`，保证真实 `inspector_run_id != author_run_id` 且 `inspector_agent != author_agent`）核验修复结果且无新缺陷；
- 首轮仍有微瑕允许经 `quota-reserve` + `quota-reconcile` **最多再补救 1 次**（仍不合格立即停机上报）；合格后写入 `>= 12` 字独立具体审图理由、最新 `prompt_sha256`、`refs_sha256` 与显式 `inspector_run_id` 至 `<batch_dir>/result.json`（及 `批次审核结论.json`），同步运行 `stamp-visual-audit` 更新清单哈希戳。

### 步骤 5：刷新接触表 + `finalize` 原地覆盖归位 + 极简回传

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py contact --batch-dir "<所属批次目录>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py finalize --batch-dir "<所属批次目录>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py organize --project-root "<项目根目录>" --episode "第NN集"
```

向根主控回传 **`≤ 5 行`** 极简摘要（已修复单元、核心改词点、正式交付图与接触表链接）后退出销毁。
