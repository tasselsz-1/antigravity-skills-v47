# 第一棒 · 窗 3（`P1-微场次出词与跨厂商双盲审窗`）作业子卡

> **定岗与并发范式**：
> - **刀刃块执笔（`is_golden_15s` / `is_climax`）**：池 B `claude-opus-5-5-high`（`swarm(action="add", name="w3-edge", model="claude-opus-5-5-high", tasks=[...])`）
> - **常规块执笔（~75% 微场次）**：池 B `claude-sonnet-5-5-medium`（分片 `swarm(tasks=[...])` 或由 `Sonnet 5.5` 微主管 `run_workflow` `model="inherit"` 并发）
> - **1 对 1 跨厂商盲审督导**：池 A `gemini-4-hydrogen-high`（分片 `swarm(name="w3-audit", model="gemini-4-hydrogen-high", tasks=[...])`）
> - 单工兵专注 **1 个微场次（5–8 段）**，步数预算 **`≤ 25 步`**，验收即 `swarm(action="kill")` 释放。执笔与盲审开工首步必须先 `read_file` 本卡、[`flow-routing-rules.md`](./flow-routing-rules.md) 及 [`image-generation-quality.md`](../../../rules/image-generation-quality.md)（英文干净度/禁水印内核原文与七维红线）。

## 1. 图片端出词硬规范（5 维 `visual_spec` + 5 槽位英文词 + 固定 `Image 1 = 净源帧`）

1. **先填 5 维实体清点 `visual_spec`，再编译 Gemini 官方多参考图 5 槽位英文 `image_prompt`**（画幅严格遵循 `style_and_cast.json` 的 `aspect_ratio`：默认 `16:9`，竖屏 `9:16`）：
   - `ref_roles`：**所有景别统一固定 `Image 1 = seg_XX_shot_YY_first_净参考.jpg`**，明确声明 `Image 1` 仅锁俯仰机位、九宫格构图、肢体姿态与视线向量，**严禁复制原片人物的脸、发型、发色与衣服**及噪点；**严禁翻转调换 `Image 1` 顺序或把 `Image 1` 指认为人脸卡**！
   - `camera_and_horizon`：精确景别、俯仰角、地平线占比与焦段；
   - `spatial_layers`：前/中/远景空间层次与遮挡，严守净空三禁；
   - `subjects_inventory`：准确人数 `exact_person_count`、九宫格站位、躯干朝向夹角、**逐人左右手动作与刚体持物**、**非摆拍视线落点**；
   - `lighting_vector`：主光源方向、冷暖色温与阴影投射面；`_尾帧.png` 必锁同段 `_首帧.png` 光场色调。
2. **强命名三槽位垫图数组 `image_refs`（`verify-script-alignment` 代码硬锁，严禁人物挤占场景槽！）**：
   - **`Image 1`**：固定净参考源帧 `ref_clean_frames/seg_XX_shot_YY_first_净参考.jpg`（严禁指认为人脸卡）；
   - **`Image 2`**：**死锁同状态场景母图**（生成 `_尾帧.png` 时为同段过审 `_首帧.png`）——**严禁把人物母图塞进 `Image 2` 挤掉场景母图**（代码硬拦截 `IMAGE_REF_SLOT2_MISSING_SCENE`）；
   - **`Image 3`**：单人镜按景别挂 `#FFFFFF` 纯白无底 `_肖像.png`（特写/近景）或 `_主体.png`（中/全景）；**多人同框（`≥ 2` 人）在 AGY `≤ 3` 张上限内严禁硬塞多张单人卡**（代码硬拦截 `IMAGE_REF_MULTI_CHAR_NEEDS_COMPOSITE`），统一填写确定性合集缓存路径 `中间文件/在制工作区/第NN集/角色合集缓存/合集_<角色1>_<角色2>_<肖像|主体>.png`（左→右顺序严格一致于 `visible_characters`，由第二棒 `batch_ops.py build-char-sheets` 自动去白边无底横拼并在提示词中注明自左向右角色对应）。
3. **`need_tail_frame` 量化裁决**：
   - 90% 常规同景镜头设 `false`；仅单镜内大跨度位移且**无 Handles 尾部裁切余量**的 `Frames` 镜头设 `true`（强绑同段 `_首帧.png`）。
4. **内嵌干净度与禁水印内核**：在 `[5. Lighting & Style]` 直接写入无脏噪/无色斑与无水印/无原片字幕约束（详规见 [`character-scene-style/references/clean-image-prompts.md`](../../character-scene-style/references/clean-image-prompts.md)）。

## 2. 视频端双轨出词硬规范（对照首/中/尾三帧与 `episode_director_bible.json`）

严格按 `cuts.csv` 锁死的模型、模式与秒数编写，零占位符直接写入完整画风母词与人物造型：

1. **Google Flow 模型（长期双主力 `Omni Flash 1.2` / `Veo 3.1 Lite`）👉 英文官方 7 要素 + `video_assets`**：
   - 7 要素齐全：`[1. Subject & Identity]` ~ `[7. Audio & SFX & Constraints]`（完整模板见 [`flow-routing-rules.md`](./flow-routing-rules.md)）；
   - **锁闭嘴演眉眼**：第 7 项强制写 `[Silent Emotional Acting — Lips Naturally Closed, No Spoken Dialogue, Pure Environment SFX Only]`，100% 禁写人声台词与张嘴动作；
   - **Handles 自然余势延续（禁急停与套话）**：尾部余量写呼吸起伏、烟尘飘散、衣角/发丝微拂、眼神余势等自然延续，**严禁 `Hold posture` / `Hold final pose` 急停**，**严禁“按首帧画面人物造型自然继承”套话**，**清除原片片名/角标/字幕字样**；
   - **`video_assets` 严守分档与退役线**：`Omni Flash 1.2` 无订阅 `≤ 10s` / 付费 `≤ 15s`、`Extend` 仅付费 `+3–10s`；`Veo 3.1 Quality` 禁 `Ingredients`；`Veo 3.1 Lite/Fast` `8s Ingredients ≤ 3` 图、`4s/6s Frames` 禁角色卡且含余量仅挂 `Start Frame`；`Omni Flash 1.1` 限 `{4, 6, 8, 10}`；`>= 2026-12-05` 禁排三款退役模型。
2. **即梦 `Seedance 2.0` 👉 中文音画词 + `video_assets`**：
   - 编写 `【全局设定】` 六项 + 逐镜 `动作/物理指令`、`台词`、`音效`（参考 [`video-prompt-writer/SKILL.md`](../../video-prompt-writer/SKILL.md)）；
   - 显式绑定 `video_assets`（`@图片1` 首帧 + `@图片2` 角色肖像 + `@音频1` 已批准 `48000Hz` `2–3s .wav` 声线母本），《台词生成表》标 `SKIP_JIMENG_NATIVE`。

## 3. `Hydrogen High` 1 对 1 跨厂商盲审与 `stamp-visual-audit` 哈希防伪盖戳

1. **逐槽核验清单（每微场次 1 名独立池 A `gemini-4-hydrogen-high` 持高清源帧与首中尾帧审校）**：
   - `visual_spec` 与源帧俯仰角、站位、人数、左右手动作持物、非摆拍视线是否一致；
   - `image_refs` 是否严格满足三槽位：`Image 1 = 净源帧`（禁指认人脸卡）、`Image 2 = 同状态场景母图/尾帧同段首帧`（禁被人物卡挤占）、多人同框 `Image 3 = 角色合集缓存/合集_*.png` 且左→右映射与正文一致；
   - 视频词是否契合大纲本意、有无残留 `Hold posture` / 套话 / 原片字幕片名、`video_assets` 与型号/订阅分档/模式/退役日期是否 100% 合规。
2. **打回上限**：初审 `FAIL` 仅允许打回原执笔（`Opus 5.5` / `Sonnet 5.5`）精修 **1 次**并复审；**复审仍不通过立即原地停机上报**（极端复杂冲突可按需召唤 `gemini-4-carbon-high` 会诊）。
3. **合并盖戳与自动失效铁律**：
   - 全部微场次 `PASS` 后，合并 Stage A 与各微场次记录写出 `<工作区>/prompt_visual_audit.json`，运行 `stamp-visual-audit`：
   - **五项硬标准**：`author_agent != inspector_agent`（真跨厂商）、**禁自动编造默认 `run_id`**（必须显式传入真实 `author_run_id != inspector_run_id`）、**覆盖清单 100% 全部单元**、实时锁死 `prompt_sha256` 与 `refs_sha256`。一旦后续改词或换图，旧回执自动失效重审！

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py stamp-visual-audit `
  --audit-json "<prompt_visual_audit.json>" `
  --manifest "<待生图帧清单.json>" `
  --author-agent "claude-sonnet-5-5-medium" `
  --inspector-agent "gemini-4-hydrogen-high" `
  --author-run-id "<真实author_run_id>" `
  --inspector-run-id "<真实inspector_run_id>"
```
