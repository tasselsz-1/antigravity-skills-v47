---
name: video-prompt-writer
description: >-
  视频复刻第一棒执笔专项参考（V4.7）：编写《剧本故事梗概与逐镜导演脚本.md》、双轨视频提示词（Flow 英文 7 要素锁闭嘴 vs 即梦 Seedance 2.0 中文音画词）、分镜头/台词/字幕三表。仅由第一棒 W2–W4 执笔工兵或明确要求写复刻脚本/提示词时按需读取，不替代各棒调度流程。
metadata:
  version: "2.0.0"
  updated: "2026-11-22"
---

# Video Prompt Writer（V4.7 第一棒执笔专项规范）

> **流程隶属**：第一棒全四窗调度与门禁见 [`video-recreation-p1/SKILL.md`](../video-recreation-p1/SKILL.md)；Google Flow 官方能力分档与 `2026-12-05` 退役红线见 [`flow-routing-rules.md`](../video-recreation-p1/references/flow-routing-rules.md)。本文件仅聚焦导演脚本、双轨视频提示词与三表格式规范。历史版本见 [版本更新说明](references/version-history.md)。

---

## 一、第一棒核心文稿与双轨视频提示词硬规范

### 1. 必出《`分集/第NN集/第NN集_剧本故事梗概与逐镜导演脚本.md`》（纳入正式集九项）
1. **全集时代背景、核心主题与三幕剧剧情梗概**（分时态场景物理拆分防 `SCENE_TEMPORAL_STATE_CONFLICT`，严守净空三禁：旗杆留空禁挂旗帜、禁现代交通标志、禁英文乱码与小字牌匾）；
2. **出场人物档案与原型锁脸映射摘要**（角色ID、原型照或深色设定规格卡、`prep-prototype-refs` 免冠露额骨相、服化道、默认 `16:9` 横屏［竖屏随 `--aspect-ratio 9:16` 切换］与**正面正视镜头 `front-facing, looking directly at the camera`**、`VoxCPM 2` `48000Hz` 双音色设定；单镜路人不建卡）；
3. **出场场景档案与色调锚点摘要**；
4. **逐段/逐镜导演脚本与生图要素详表（`segment_01` ~ `segment_NN`）**：含【前后文上下文与图文同源本意锁】、【原片俯仰机位与非摆拍视线还原】、【生图帧去重决策（90% 仅生 `Wave 3` `_首帧.png`；仅无余量大位移镜头补 `Wave 4` `_尾帧.png`）】、【5 维 `visual_spec` + 5 槽位英文词 + 固定 `Image 1 = 净源帧` 的 `image_refs`】。

### 2. 双轨视频提示词强制分流制（零占位符一步写死画风母词与人物造型）
严格按 `cuts.csv` 锁死的模型执行双轨制（写入 `分集/第NN集/第NN集_视频提示词.md`）：
1. **轨道 A —— Google Flow 模型（长期双主力 `Omni Flash 1.2` + `Veo 3.1 Lite`）👉【官方 7 要素英文词】+ `video_assets`**：
   - 7 要素结构：`[1. Subject & Identity]` ~ `[7. Audio & SFX & Constraints]`（完整模板与订阅分档/`2026-12-05` 退役线见 [`flow-routing-rules.md`](../video-recreation-p1/references/flow-routing-rules.md)）；
   - **禁人声、锁闭嘴演眉眼**：第 7 项强制写 `[Silent Emotional Acting — Lips Naturally Closed, No Spoken Dialogue, Pure Environment SFX Only]`，台词情绪转为上半张脸眉眼微表情；
   - **Handles 自然余势延续**：尾部余量写呼吸起伏、烟尘飘散、衣角/发丝微拂等自然延续，**严禁 `Hold posture` / `Hold final pose` 急停**，**严禁“按首帧画面人物造型自然继承”套话**，**清除原片片名/字幕字样**。
2. **轨道 B —— 即梦 `Seedance 2.0` 镜头 👉【中文音画多模态提示词】+ `video_assets`**：
   - 遵循 [视频提示词编写规则](../character-scene-style/references/视频提示词编写规则.md)，含【全局设定】六项与逐镜【动作/物理指令】【台词（`起始秒-结束秒s人物 [情绪标签]：内容`）】【音效】；
   - 显式绑定 `video_assets`：`@图片1` 首帧 + `@图片2` 角色肖像 + `@音频1` 已批准 `48000Hz` `2–3s .wav` 声线母本（《台词生成表》标 `SKIP_JIMENG_NATIVE`）。

### 3. 三表标准导出（分镜头脚本 + 台词生成表 + 字幕时间轴）
- **`第NN集_分镜头脚本.xlsx`**：按 [分镜头脚本规则](references/storyboard-script.md) 填写标准 12 列并自查（看渲染页派独立视觉子代理 `gemini-4-hydrogen-high` 或 `model="flash"`）。
- **`第NN集_台词生成表.xlsx`（及同名 `.csv`）**：标准 9 列（情绪列用 `情绪导演指令`，兼容旧表列名；即梦开口镜填 `SKIP_JIMENG_NATIVE`）。
- **`第NN集_字幕时间轴.csv`（UTF-8 BOM）与 `第NN集_字幕.srt`**：基于 `asr_transcript.json` 与新姓名映射，去除片头片尾后从 `0` 起计。

---

## 二、执行准则：改名、转场运镜、原片双层解构与交付同步

1. **人物唯一改名与 360s 正片边界**：原名→新名映射锁死于 `中间文件/索引与制作记录/角色改名与音色映射.json`，全链路同步；单集锁死 `360s` 正片。
2. **扁平唯一帧命名**：全集分镜帧扁平存放于 `分集/第NN集/首尾及关键帧/segment_XX_镜头YY_首帧.png` 与按需 `segment_XX_镜头YY_尾帧.png`，无子目录嵌套。
3. **转场运镜与防纸片人/防僵死准则**：
   - 同空间连续动作写明可行推拉摇移路径；原片硬切用瞬时硬切（Flow 写 `[AT X.Xs — INSTANT HARD CUT]`，即梦写 `硬切至[目标构图/机位]`）配合 `0.25s` L-Cut/J-Cut 声音桥；
   - **生图画死光场，视频守住恒定**：不在禁止项滥用“如铸铁/石雕般静止”等绝对僵死词；穿透原片 2D 省帧定格（止め絵），还原剧本克制的微物理动作。
4. **交付双写与对齐终检**：写完 `分集/第NN集/第NN集_视频提示词.md` 后**必须立即原样复制至 `中间文件/提示词维护/视频提示词/第NN集/当前主稿.md`（SHA-256 一致）**，并交由窗 4 运行 `verify-cuts` 与 `verify-script-alignment --require-visual-audit`。
