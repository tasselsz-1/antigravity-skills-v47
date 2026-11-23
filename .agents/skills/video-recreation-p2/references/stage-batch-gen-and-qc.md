# 第二棒 · 环节 4–5（`quota-reserve/reconcile` 机械生图 + 持图双检 + `finalize` 归位）

> **通道红线**：生图包工头（`model="flash"`，单工兵 `≤ 25 步`）必须经 **`invoke_subagent` 或 `run_workflow`** 拉起（严禁放入 `swarm` worker）；并发生图控制在 **`≤ 2` 路** 防晚高峰 `unexpected EOF` 白扣额度。

## 环节 4：`Wave 3 → Wave 4` 机械生图 + 原子预占/幂等核销 + 持图双检

### 1. `quota-reserve` 原子预占 + `image-generator` 单次硬锁 + `quota-reconcile` 幂等核销
- **调用前预占**：每次初图生成或 `remedy` 补救重绘前，必先运行 **`quota-reserve`** 写入 `api_call_ledger.json` 获取 `reservation_id`；若触及单窗 **`60` 次硬上限**或处于**跨窗口全局 429 冷却锁**，`quota-reserve` 非零退出阻断，微主管原地 `organize` 安全收口停机！
- **内置 `image-generator` 单次调用硬锁**：派发 `image-generator` 时，必须在给子代理的外层指令开头显式封死隐藏重绘循环：
  > `CRITICAL EXECUTION LIMIT: Call generate_image AT MOST ONCE (1 single call). Do NOT inspect, critique, edit, or regenerate after receiving an image, and do NOT auto-retry on EOF/empty/429 errors — immediately return either the saved image path or the exact error message.`
- **100% 照单机械执行（三槽位死锁）**：机械读取 `jobs.json` 的英文 `prompt_file`、`aspect_ratio` 与 `image_refs`——**`Image 1` 固定 `seg_XX_shot_YY_first_净参考.jpg`**、**`Image 2` 死锁同状态场景母图（`Wave 4` 尾帧为同段过审 `_首帧.png`，严禁被人物母图挤占）**、**`Image 3` 单人挂 `#FFFFFF` 纯白无底 `_肖像.png`/`_主体.png`，多人同框（`≥ 2` 人）挂 `build-char-sheets` 生成的 `角色合集缓存/合集_*.png` 去白边无底横向合集图**（`save-unit` 自动级联校验合集图及全部源单人母图已过审），严禁改词或翻转槽位顺序。
- **调用后幂等核销**：返回后必跑 **`quota-reconcile --reservation-id <预占单号> --outcome ok|429|timeout|error`**（按 `reservation_id` 幂等记账不重复扣额；遇 `429` 自动写入遵循 `Retry-After` 的跨窗口全局冷却锁）。
- **落盘与接触表**：每单元 `save-unit`，全批（`≤ 6` 张）跑 `contact`（`review-grid` 左列零空白）。

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py quota-reserve --work-dir "<中间文件/在制工作区/第NN集>" --unit "<unit名>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py quota-reconcile --work-dir "<中间文件/在制工作区/第NN集>" --reservation-id "<reservation_id>" --outcome ok
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py save-unit --jobs-file "<jobs.json路径>" --unit "<unit名>" --tool-png "<工具生成的JPG/PNG路径>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py contact --batch-dir "<批次目录>"
```

### 2. `Hydrogen High` 持图双检、显式 `run_id` 哈希防伪与最多补救 1 次
- 派发独立池 A **`gemini-4-hydrogen-high`** 审图工兵（高难度画风一致性疑难批次可派池 B **`claude-sonnet-5-5-high`** 跨池复核），单次审图 `≤ 6` 张，在会话内同时载入 **① Stage A 过审母图 + ② 本场原片源帧 + ③ 本批 1K 原图与《接触表.jpg》**。
- 逐镜核查：跨镜五官骨相/发际线/服饰连贯性、左右手握持刚体结构、非摆拍视线、俯仰构图、`#FFFFFF` 纯白底（角色母图）、干净度与无水印/无原片字幕七维红线。
- 写入 `<批次目录>/批次审核结论.json`：每单元 **`>= 12` 字独立具体审图理由**（防 `FINALIZE_BLOCKED_FAKE_BULK_PASS`）+ 实时 **`prompt_sha256`**、**`refs_sha256`** + 显式真实 **`inspector_run_id != author_run_id`**（严禁编造默认 `run_id`）。
- **补救上限与分类补救铁律（审图员必读 [`image-generation-quality.md`](../../../rules/image-generation-quality.md)）**：严格区分补救方式（场景问题禁局部重绘必整张重绘、纯噪点原词不变整张重绘、仅多人合集缩放掉精度允许传单人母图局部补精度 1 次）；不合格项经 `remedy-prep`（修改词/换图后旧哈希自动失效）+ `quota-reserve` + `image-generator`（单次硬锁）+ `quota-reconcile` **最多自动补救 1 次**并重新双检盖戳；**复检仍未通过立即原地停机上报**！

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py remedy-prep --batch-dir "<批次目录>" --review-json "<批次目录>/批次审核结论.json"
```

## 环节 5：`finalize` 反伪造归位 + `organize` 全集整理

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py finalize --batch-dir "<批次目录>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py organize --project-root "<项目根目录>" --episode "第NN集"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第NN集>"
```

- `finalize` 硬检接触表、`图片落盘SHA核验.json`、实时 `prompt_sha256`/`refs_sha256`、显式 `author_run_id != inspector_run_id` 及每单元 `>= 12` 字独立理由。
- 全部归位无缺口后（`phase-status` 推进至 `P3`），向根主控回传 `≤ 15 行` 落盘统计并销毁退出。
