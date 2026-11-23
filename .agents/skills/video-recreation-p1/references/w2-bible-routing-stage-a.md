# 第一棒 · 窗 2（`P1-大纲路由与切片窗`）作业子卡

> **定岗与职责**：
> - **圣经路由导演**：池 A `gemini-4-neon-high`（超长复杂集升 `gemini-4-neon-xhigh`，单工兵 `≤ 25 步`）
> - **Stage A 母图执笔**：池 B `claude-opus-5-5-medium`（单工兵 `≤ 25 步`）
> - **Stage A 跨厂商盲审**：池 A `gemini-4-hydrogen-high`（单工兵 `≤ 25 步`）
> - **本窗按岗必读（开工首步先 `read_file`）**：锁模必读 [`flow-routing-rules.md`](./flow-routing-rules.md)；人设/画风/骨相按需读 [`character-scene-style`](../../character-scene-style/SKILL.md)、[`image-style-reference`](../../image-style-reference/SKILL.md)、[`character-face-hair-reference`](../../character-face-hair-reference/SKILL.md)；Stage A 写英文词与盲审必读 [`image-generation-quality.md`](../../../rules/image-generation-quality.md)（英文干净度/禁水印内核与七维红线）。

## 1. 全集死宪法 `episode_director_bible.json` 落盘（`Neon High / XHigh`）

仅读取跨集薄卡 `全局设定/全剧世界观与角色注册表.json` + 本集 `asr_transcript.json` + `1–6` 张 `1600px` 故事板总览图（`~20k Tokens`，零断流），一次性落盘 `episode_director_bible.json`、同步写入 `中间文件/索引与制作记录/角色改名与音色映射.json` 与 `<工作区>/style_and_cast.json`（锁定 `aspect_ratio: "16:9"|"9:16"`）：

1. **剧情与微场次边界**：
   - 写清时代背景、三幕剧梗概与核心戏剧冲突；
   - 划定 **`5–8 个同景连续 segment` 微场次边界**，显式打标 **`is_golden_15s`**（开局黄金 `0–15s`）与 **`is_climax`**（核心高潮），写死逐镜**图文同源本意锁**。
2. **人物四维重新设计铁律、美型分级、历史原型例外与原创 MJ 定脸分工**：
   - **唯一改名与四维重塑**：所有复刻角色必须分配唯一新姓名并重塑 **① 发型、② 发色、③ 脸型（含五官骨相）、④ 服饰**，严禁照抄原片角色外貌与服装样式；
   - **原创/虚构新角色首次定脸分工（仅限生新角色）**：不默认由内置生图盲抽或堆砌琐碎解剖参数硬捏脸；完成四维人设、年龄梯度与防撞轮廓后，同步输出清爽克制的 **Midjourney（MJ）英文抽卡提示词**（附 `--ar`/`--c`/`--sref` 建议）交用户去 MJ 抽卡定颜，用户挑定回传后再挂为 `Wave 1` `_肖像.png` 的 `image_refs[0]` 1:1 保持原颜转制；
   - **历史人物/历史原型明确除外**：必须以**史料记载、传世画像/造像与公认历史原型**为基准进行考据还原，并脱离原片演员/二创形象独立重塑新姓名与四维造型（不套用原创虚构角色 MJ 盲抽逻辑），**严禁过分美颜、严禁网红脸或过度偶像滤镜化**，保留符合时代的真实骨相质感、年龄纹理与厚重气质；
   - **美型分级标准**：常规/幻想/剧情类人物整体往**美型、幻想型审美**走且全库骨相各异，严禁千篇一律网红同脸；
   - **路人去重与声线映射**：单镜一闪而过的无名路人/群演**严禁独立建角色卡**（仅真实原型/历史名人或跨 `≥ 2` 镜复现角色建卡）；为每名建卡角色配置 **`VoxCPM 2`** `说话` + `心声` 双中文声线描述（统一 `48000Hz` 标准）。
3. **分时态场景母图与净空三禁**：
   - 同空间同时态共享 1 张场景母图；**和平完好态 vs 战损废墟态必须物理拆建**（防 `SCENE_TEMPORAL_STATE_CONFLICT`）；
   - 严守**净空三禁**：旗杆留空禁挂旗帜、禁现代交通标志/玻璃幕墙、禁英文乱码与小字牌匾。

## 2. `build-cuts` 锁模 + 强制独立可信终点 `verify-cuts` + 立即 `slice-segments`

1. **按官方能力表与 `2026-12-05` 退役红线锁死 `cuts.csv`**（细则见 [`flow-routing-rules.md`](./flow-routing-rules.md)）：
   - 长期双主力优先推荐：**`Omni Flash 1.2`**（无订阅 `≤10s`、付费 `≤15s`，`Extend` 仅付费 `+3–10s`）+ **`Veo 3.1 Lite`**（`Frames 4/6/8s` 且 `4/6s` 禁角色卡，`Ingredients ≤3图` 仅 `8s`）；
   - **`2026-12-05` 退役红线（`>= 2026-12-05` 硬阻断）**：`Veo 3.1 Quality`（禁 `Ingredients`）、`Veo 3.1 Fast`、`Omni Flash 1.1`（限离散 `4/6/8/10s`）；
   - **`即梦 Seedance 2.0`**：正脸高爆发中文对口型特写（同场对话正反打锁死同一模型）。
2. **`verify-cuts` 强制独立可信终点（严禁自证 100%）**：
   - 预期正片绝对区间 **`[expected_start_sec, expected_end_sec]`** 必须由源片/`正片母本_CFR.mp4`/`phase1_colab_done.json`/`分集索引.json`（含不足 `360s` 末集与剔除片头片尾区间）独立提供；
   - 硬门槛：**覆盖率 `≥ 99.5%`、首尾漏切 `≤ 0.50s`、内部最大无故空洞 `≤ 0.50s`**、严禁跨大场景合并。
3. **【关键补链】立即运行 `slice-segments`**：
   - 精确切出 `分集/第NN集/原视频段落/segment_XX.mp4`，并同步映射 `中间文件/原片分析/第NN集/ref_clean_frames/seg_XX_shot_YY_first_净参考.jpg`。

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py build-cuts --shots-json "<中间文件/原片分析/第01集/物理切点清单.json>" --routing-json "<episode_director_bible.json>" --output-csv "<中间文件/原片分析/第01集/cuts.csv>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py verify-cuts --cuts-csv "<中间文件/原片分析/第01集/cuts.csv>" --shots-json "<中间文件/原片分析/第01集/物理切点清单.json>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py slice-segments --project-root "<项目根目录>" --episode "第01集"
```

## 3. Stage A 母图出词（`Opus 5.5 Medium`）与跨厂商盲审盖戳（`Hydrogen High`）

1. **Stage A 出词规范（池 B `claude-opus-5-5-medium`）**：
   - 编写角色母图（`_肖像.png` 免冠露额锁脸 + `_主体.png` 全身立绘）与场景母图（`STRICTLY 0 PERSON(S) IN FRAME, NO PEOPLE` + 净空三禁）的 5 维 `visual_spec`、5 槽位英文 `image_prompt` 与 `image_refs`；
   - 角色母图严格锁死：**默认 `16:9` 横屏（竖屏项目随 `--aspect-ratio 9:16` 同步切 `9:16` 竖屏）**、**正面正视镜头（`front-facing, looking directly at the camera`）**、**`#FFFFFF` 纯白底**，并落实四维重塑、美型分级、历史人物原型考据或原创角色 MJ 定脸衔接（用户回传 MJ 定妆照挂 `image_refs[0]`）。
2. **独立跨厂商盲审与盖戳（池 A `gemini-4-hydrogen-high`）**：
   - 持原型胸像参考照（或新角色设定卡）与场景代表净源帧逐项盲审，不通过最多打回 `Opus 5.5` 精修 **1 次**，仍不通过立停上报；
   - 通过后落盘 `<工作区>/audit/stage_a_visual_audit.json` 并运行 `stamp-visual-audit`——**严禁自动编造默认 `run_id`**，显式传入真实 **`author_run_id != inspector_run_id`**、**`author_agent != inspector_agent`**，100% 覆盖 Stage A 全部单元并绑定实时 `prompt_sha256`、`refs_sha256`。
