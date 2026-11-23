# 生图质量：噪点、独立子代理、5 槽位英文机械生图与一次重绘（V4）

## 触发范围与“根主控零膨胀 + 复刻项目机械生图”分工

本文件约束所有生图任务的提示词装配、无记忆并发执行与一次补救审核流程。默认使用内置生图工具（Antigravity 端 `image-generator` 子代理 / Codex 端 `ImageGen`），用户明确指定其他引擎时按其要求执行。

为防止根主控（Root）上下文膨胀并守住复刻项目单一事实源，严格执行以下**三层职责隔离**：

1. **提示词准备层（复刻项目第二棒 100% 照单机械生图，严禁现场拼改中文词！）**：
   - **【视频复刻项目硬门禁】**：第一棒已在 `待生图帧清单.json` 中落盘并经跨模型盲审通过了每单元的 **5 维 `visual_spec`、Gemini 官方 5 槽位全英文 `image_prompt` 与强命名三槽位 `image_refs`（`Image 1 = 净源帧`、`Image 2 = 同状态场景母图/尾帧同段首帧` 严禁被人物母图挤占、`Image 3 = 单人 `#FFFFFF` 纯白无底母图` 或多人同框由 `batch_ops.py build-char-sheets` 生成的 `角色合集缓存/合集_*.png` 去白边无底横向合集图）**。第二棒 `batch_ops.py plan-batches` 100% 原文物化为 `生成批次/批次XX/jobs.json` 与 `完整提示词.txt`——**第二棒严禁现场拼改中文词、严禁生图工兵做中译英或扩词、严禁翻转槽位或把角色卡塞进 `Image 2`**！
   - **非复刻独立生图任务**：若无上游清单，主代理派发独立准备子代理查看参考图、读取本文件及 `clean-image-prompts.md` 写出英文 `完整提示词.txt` 与 `jobs.json`；**根主控自身严禁打开源帧或参考图**。
2. **生图执行层（无记忆纯执行工兵，1 单元 1 工兵，生完即焚）**：
   - 微主管通过 `run_workflow` 或点对点 `invoke_subagent` 极简指针（仅传 `jobs.json` 路径 + `unit` 编号，不粘提示词正文）并发派发生图工兵。
   - 生图工兵原样读取 `prompt_file` 全英文原文与 `image_refs` 调用 1 次 `image-generator`，随后运行：
     `python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py save-unit --jobs-file "<jobs.json>" --unit "<unit>" --tool-png "<原始PNG/JPG>"`
   - 生图工兵**不重读本规则文件、不打开参考图或生成图做视觉自检**，仅返回 **1 行状态**（`unit | OK | png路径 | sha256_match=true`）即退出。
   - **封死内置 `image-generator` 读图与隐藏自检循环（防上下文膨胀与配额翻倍红线）**：Antigravity 内置 `image-generator` 子代理默认会在改图前 `read_file` 读入参考图（把整张图片塞进上下文增大上传体积），且在出图后自行看图挑刺、反复 `EDIT`/重绘或遇错重试。凡派发 `image-generator` 时，**必须在给子代理的外层指令开头显式写死**：`CRITICAL EXECUTION LIMIT: (1) Do NOT call read_file or view/inspect ANY reference image, input image, or generated image — pass reference image paths directly to generate_image's ImagePaths parameter as raw paths only. (2) Call generate_image AT MOST ONCE (1 single call). Do NOT critique, edit, or regenerate after receiving an image, and do NOT auto-retry on EOF/empty/429 errors — immediately return either the saved image path or the exact error message.`（该约束仅写给子代理执行器，不混入画面描述词本身）；同时控制并发生图 **`≤ 2` 路**，避免晚高峰 `unexpected EOF` 隐性扣额。
3. **视觉审核与改词补救层（`Gemini 3.8 Flash (Medium)` · `model="flash"`，单工兵 `≤ 25 步`）**：
   - 全批生图落盘后，运行 `python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py contact --batch-dir "<批次目录>"` 生成接触表（左列零空白）与 SHA256 核验文件。
   - 派发独立 **`Gemini 3.8 Flash`（`model="flash"`）持图审图子代理**（严禁主控或微主管自己看图！），在会话内载入【Stage A 过审母图 + 本场原片源帧 + 接触表与 1K 原图】执行两步完整双检：
     ① 横向比对“源帧 vs 生成图”的俯仰机位、九宫格构图、非摆拍视线、人数、服装与跨镜连贯性；
     ② 打开 **1K 原始生成图（`初始.png` / `补救.png`）**，核查 `#FFFFFF` 纯白底、五官骨相、手部刚体结构、暗部噪点与发丝边缘干净度。
   - 审图子代理将每单元判定结果、**`>= 12` 字独立具体审图理由**（严禁复制雷同套话触发 `FINALIZE_BLOCKED_FAKE_BULK_PASS`）及问题图的 5 槽位全英文补救提示词写入 `<批次目录>/批次审核结论.json`，运行 `batch_ops.py remedy-prep`（初审轮）或写入最终 `result.json` 后运行 `batch_ops.py finalize`（归位轮），仅向微主管回复 `≤ 5 行` 统计结果后销毁。

## 提示词必填内核：干净度与禁止水印

每次提交的完整生图提示词必须涵盖以下两项质量约束（复刻项目在第一棒编写 5 槽位英文词 `[5. Lighting & Style]` 时直接内嵌英文表达）：

1. **基础干净度要求**：
> Clean crisp rendering, smooth color gradients and natural contours, orderly material details at true physical scale; preserve intentional surface textures while avoiding dirty speckles, random grain, color noise, shadow blotches, compression artifacts, oversharpening halos, or waxy smearing; keep pure white (#FFFFFF) or solid areas spotless.

2. **禁止水印与标识要求**：
> Strictly no watermarks, no "AI generated" text, no platform/tool logos, no signatures, no corner badges, no QR codes, and no source subtitle/title-card remnants (in-world story signage or letters allowed when specified with renamed identities).

- 不以磨皮塑料质感换取干净度；用户明确要求胶片颗粒风格时保留指定纹理并抑制杂色噪点。

## 场景、人物与头发的视觉脏感（第一棒写词阶段按需融入）

在第一棒准备生图提示词（或独立生图写词）阶段读取 [场景、人物与头发干净度模块](../skills/character-scene-style/references/clean-image-prompts.md)，按本镜可见内容将适用标准融入英文提示词：
- 场景的体块/纹理/连贯明暗与净空三禁（旗杆留空、禁现代交通牌、禁英文乱码小字牌匾）；
- 人物的肤色与材质分区（无人物空镜不加，强制 `STRICTLY 0 PERSON(S) IN FRAME`）；
- 头发的主次发束、发流与干净暗部（`_肖像.png` 强制免冠露额展示自然发际线）。

## 批次审图判定标准与最多补救一次

1. **按表达影响与七维红线判定**：
   - **必须处理（一票否决）**：非 `#FFFFFF` 纯白底或三视图拼贴；人数不符；俯仰机位跑偏或正面盯镜头罚站摆拍；场景跨时态冲突（`SCENE_TEMPORAL_STATE_CONFLICT`）或违背净空三禁；五官/手部畸变、多人同脸或跨镜骨相穿帮；偏离原片本意、明显脏噪或残留水印/原片字幕。
   - **允许通过**：不影响画面表达、跨镜一致性与达芬奇切镜衔接的细微笔触差别直接通过并记录具体独立理由，不为微观完美滥用生图配额。
2. **问题图分类补救（每图总计最多自动补救 1 次）**：
   - **场景问题 → 严禁局部重绘，必须修改提示词整张重绘 1 次**。
   - **朝向、身份、构图、动作、手部或画风错误 → 修改 5 槽位英文提示词整张重绘 1 次**（若因多人合集图缩放导致单人面部精度下降，允许传入 `[初始.png, 场景/画风参考, 该角色单人高清母图]` 局部补回精度 1 次）。
   - **纯噪点/脏污渲染问题 → 原词与合格参考不变，整张重绘 1 次**。
   - 局部重绘与整张重绘共用每张问题图 **最多 1 次** 自动补救上限。
3. **遇错即停**：若 1 次补救重绘复检后仍未通过，**立即原地停机保留现场并向主控/用户上报裁决，严禁私自放行或伪造通过**！
