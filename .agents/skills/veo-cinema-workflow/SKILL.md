---
name: veo-cinema-workflow
description: >-
  纯 Google Flow（Omni Flash 1.2 + Veo 3.1 Lite）影视级视频复刻兼容入口（V4.7）。仅当用户说“Veo视频复刻”、“Veo工作流”、“写Veo提示词”、“跑Veo生成”时触发；各棒执行直达独立棒次 Skill。
---

# Google Flow (`Omni Flash 1.2` / `Veo 3.1 Lite`) 专项复刻入口（V4.7）

> **按棒独立路由**：全四棒细则已拆分至独立棒次 Skill（遇错即停、严禁绕行），本入口仅说明纯 Flow 路线核心约束，**只按当前棒次读取对应唯一 Skill**。

## 纯 Google Flow 核心约束

1. **长期双主力与 `2026-12-05` 退役线**：优先 `Omni Flash 1.2`（无订阅 `≤10s` / 付费 `≤15s`，`Extend` 仅付费 `+3–10s`）+ `Veo 3.1 Lite`（`4/6s` 仅 `Frames` 禁角色卡，`Ingredients ≤3图` 仅 `8s`）；`>= 2026-12-05` 硬阻断 `Veo 3.1 Quality` / `Veo 3.1 Fast` / `Omni Flash 1.1`（详见 [`flow-routing-rules.md`](../video-recreation-p1/references/flow-routing-rules.md)）。
2. **官方 7 要素英文词铁律**：第 7 项锁死 `[Silent Emotional Acting — Lips Naturally Closed, No Spoken Dialogue, Pure Environment SFX Only]`，Handles 余量写自然物理余势延续，严禁 `Hold posture` 急停与套话。

## 按棒精准直达（严禁一次通读全部）

| 阶段 | 直达独立 Skill |
| :--- | :--- |
| 总控路由 | [`video-recreation/SKILL.md`](../video-recreation/SKILL.md) |
| 第一棒（切分/锁模/7要素出词） | [`video-recreation-p1/SKILL.md`](../video-recreation-p1/SKILL.md) |
| 第二棒（48kHz声线/批次生图） | [`video-recreation-p2/SKILL.md`](../video-recreation-p2/SKILL.md) |
| 第三棒（克隆台词/投喂/九项） | [`video-recreation-p3/SKILL.md`](../video-recreation-p3/SKILL.md) |
| 第四棒 P4A / P4B | [`video-recreation-p4/SKILL.md`](../video-recreation-p4/SKILL.md) |
