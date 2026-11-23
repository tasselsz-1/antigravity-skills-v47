---
name: character-scene-style
description: >-
  视频复刻人物/场景/画风设计与资产组织专项参考（V4.7）：人物四维重塑、美型分级、历史人物原型例外、原创角色 MJ 定脸、防同脸与干净度、扁平帧命名及正式集九项目录结构。仅由 P1/P2 对应工兵或明确做角色/场景设计时按需读取，不替代各棒调度流程。
metadata:
  version: "2.7.0"
  updated: "2026-11-22"
---

# 角色与场景风格创作（Character and Scene Style · V4.7 专项参考）

> **流程隶属**：第一棒四窗调度见 [`video-recreation-p1/SKILL.md`](../video-recreation-p1/SKILL.md)，第二棒声线母本与分波批次生图调度见 [`video-recreation-p2/SKILL.md`](../video-recreation-p2/SKILL.md)。本文件仅聚焦人物/场景设计铁律、资产命名与正式集九项结构规范。

---

## 一、人物四维重塑、美型分级与原创/历史分工铁律

1. **人物四维重新设计铁律**：所有复刻人物必须独立重塑 **① 发型、② 发色、③ 脸型（含五官骨相）、④ 服饰**，严禁直接照抄原片角色外貌与服装（分镜中 `Image 1 = 净源帧` 仅锁俯仰机位、构图与动作视线，严禁复制原片人脸/发型/发色/衣服）。
2. **美型分级标准**：
   - **常规/幻想/剧情类**：整体往**美型、幻想型审美**走（五官精致立体、比例修长协调、发丝与服饰具视觉美感），同时保持全库不同角色骨相差异化，严禁千篇一律网红同脸；
   - **历史人物/历史原型（明确例外）**：必须以**史料记载、传世画像/造像与公认历史原型**为基准考据还原，脱离原片演员/二创形象独立重塑新姓名与四维造型，**严禁过分美颜、网红脸或偶像滤镜化**，保留符合时代的真实骨相质感、年龄纹理与厚重气质（亦不套用原创虚构角色 MJ 盲抽逻辑）。
3. **原创/虚构新角色 MJ 定脸分工（仅限生新角色）**：设计全新原创/虚构角色的初始定妆脸时，不默认由内置生图盲抽或堆砌琐碎解剖参数硬捏脸，先完成四维人设、年龄梯度与防撞轮廓后，输出清爽克制的 **Midjourney（MJ）英文抽卡提示词**（附 `--ar`/`--c`/`--sref` 建议）交用户去 MJ 抽卡定颜；用户挑定回传后挂为 `Wave 1` `_肖像.png` 的 `image_refs[0]` 1:1 保持原颜转制（已定妆角色、单点返修与下游分镜直接由产线生图执行）。
4. **肖像/主体母图铁律**：`_肖像.png` 与 `_主体.png` 均须免冠露额锁死发际线、头骨轮廓与年龄特征，**默认 `16:9` 横屏（竖屏项目随 `--aspect-ratio 9:16` 切换）**、**正面正视镜头（`front-facing, looking directly at the camera`）**、100% `#FFFFFF` 纯白底（`FAIL_NON_WHITE_BG`）；`Wave 1 → Wave 2 → Wave 3/4` 严格经两阶 `prototype-sheet --stage wave1/wave2` 解锁（详见 [`video-recreation-p2/references/stage-voice-and-stage-a.md`](../video-recreation-p2/references/stage-voice-and-stage-a.md)）。

---

## 二、Stage A 去重、场景双铁律与 Stage B 扁平命名

1. **路人不建卡**：仅真实原型/历史名人或跨 `≥ 2` 镜复现角色建 `_肖像.png` + `_主体.png`；单镜一闪而过的无名路人/群演严禁独立建卡。
2. **场景跨时态物理拆建 + 净空三禁**：同空间同时态共享 1 张场景母图，**和平完好态 vs 战损废墟态必须物理拆分**（防 `SCENE_TEMPORAL_STATE_CONFLICT`）；强制 `STRICTLY 0 PERSON(S) IN FRAME, NO PEOPLE` 且严守**净空三禁**（旗杆留空禁挂旗帜、禁现代交通标志/玻璃幕墙、禁英文乱码与小字牌匾）。
3. **扁平唯一帧命名、三槽位垫图与多人无底横向合集图**：
   - 全集分镜帧统一扁平存放于 `分集/第NN集/首尾及关键帧/segment_XX_镜头YY_首帧.png` 与按需 `segment_XX_镜头YY_尾帧.png`（无子目录嵌套）；
   - `Wave 3` 严格执行三槽位：**`Image 1 = ref_clean_frames/seg_XX_shot_YY_first_净参考.jpg`**、**`Image 2 = 同状态场景母图`（严禁被人物母图挤占）**、**`Image 3 = 单人 #FFFFFF 纯白无底母图` 或多人同框由 `batch_ops.py build-char-sheets` 自动去白边横拼的 `角色合集缓存/合集_<角色1>_<角色2>_<肖像|主体>.png`**（默认 `#FFFFFF` 纯白无底防透明通道压黑边，按需支持 `--bg transparent` 真 RGBA 透明无底）；90% 常规同景镜头仅生 `_首帧.png`，保留的 `_尾帧.png`（`Wave 4`，仅无 Handles 余量大位移镜头）`Image 2` 强制绑定同段过审 `_首帧.png` 锁同景同色调。

---

## 三、项目目录、多人防同脸与干净度专业参考

1. **正式集九项交付结构**：`分集/第NN集/` 固定九项：① `人物/`（双 `#FFFFFF` 纯白底图）、② `声线/`（`2.00s–3.00s` `48000Hz` 说话+心声双母本）、③ `第NN集_剧本故事梗概与逐镜导演脚本.md`、④ `第NN集_视频提示词.md`（同步 `中间文件/提示词维护/视频提示词/第NN集/当前主稿.md` SHA-256 一致）、⑤ `首尾及关键帧/`、⑥ `第NN集_分镜头脚本.xlsx`、⑦ `原视频段落/`（`segment_XX.mp4`）、⑧ `第NN集_字幕时间轴.csv`（及 `.srt`）、⑨ `第NN集_台词生成表.xlsx`/`.csv`。详规见 [项目基本结构以及规则](references/项目基本结构以及规则.md)。
2. **按需专项子参考（工兵按本镜需求选读，严禁全量加载）**：
   - 多人同框骨相差异与连续性：[multi-person-face-consistency.md](references/multi-person-face-consistency.md)（人物 7 维参考库见 [`character-face-hair-reference/SKILL.md`](../character-face-hair-reference/SKILL.md)，仅作参考不可直用）
   - 生图朝向与空间关系锁定：[image-orientation-lock.md](references/image-orientation-lock.md)
   - 替换式剧情与镜头复刻方法：[replacement-recreation.md](references/replacement-recreation.md)
   - 场景/人物/头发干净度模块：[clean-image-prompts.md](references/clean-image-prompts.md)
   - 画风分析与画风库：[style-analysis-before-recreation.md](references/style-analysis-before-recreation.md) / [`image-style-reference/SKILL.md`](../image-style-reference/SKILL.md)
   - 即梦中文视频词与官方手册：[视频提示词编写规则.md](references/视频提示词编写规则.md) / [Seedance2.5官方手册核心全量参考.md](references/Seedance2.5官方手册核心全量参考.md)
