---
name: cinema-hybrid-workflow
description: >-
  旗舰全生态视频复刻兼容入口（V4.7，含跨集 NotebookLM 防穿帮与 Flow/即梦双路线切换）。仅当用户说“旗舰工作流”、“超级复刻工作流”、“cinema-hybrid-workflow”时触发；各棒执行直达独立棒次 Skill。
---

# 旗舰级短剧与视频复刻全生态入口（Cinema Hybrid · V4.7）

> **按棒独立路由**：全四棒规则与门禁已拆分为独立棒次 Skill（遇错即停、严禁绕行），本入口仅保留全生态增配说明，**只按当前棒次读取对应唯一 Skill**。

## 全生态增配定位

1. **跨集防穿帮智囊层**：除 `< 1.5k Tokens` 静态薄卡 `全局设定/全剧世界观与角色注册表.json` 外，可选联动 **NotebookLM** 管理跨集服化道与场景时序态（和平完好态 vs 战损废墟态，防 `SCENE_TEMPORAL_STATE_CONFLICT`）。
2. **双视频路线切换**：
   - **纯 Google Flow 路线**：长期双主力 `Omni Flash 1.2` + `Veo 3.1 Lite`，配乐用 MusicFX 0 积分 `48000Hz` 纯器乐；
   - **Flow 80% + 即梦 Seedance 2.0 20% 混编路线**：正脸高爆发中文对白走即梦（同场正反打锁死同一模型）。

## 按棒精准直达（严禁一次通读全部）

| 阶段 | 直达独立 Skill |
| :--- | :--- |
| 总控路由（未指定棒次） | [`video-recreation/SKILL.md`](../video-recreation/SKILL.md) |
| 第一棒（切分/路由/出词/盲审） | [`video-recreation-p1/SKILL.md`](../video-recreation-p1/SKILL.md) |
| 第二棒（48kHz声线/批次生图） | [`video-recreation-p2/SKILL.md`](../video-recreation-p2/SKILL.md) |
| 第三棒（克隆台词/投喂手册/九项） | [`video-recreation-p3/SKILL.md`](../video-recreation-p3/SKILL.md) |
| 第四棒 P4A / P4B（含按需 60fps） | [`video-recreation-p4/SKILL.md`](../video-recreation-p4/SKILL.md) |
| 定点改图（默认不触发） | [`video-recreation-fix/SKILL.md`](../video-recreation-fix/SKILL.md) |
