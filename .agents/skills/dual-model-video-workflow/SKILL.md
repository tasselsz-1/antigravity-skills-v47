---
name: dual-model-video-workflow
description: >-
  Google Flow（Omni Flash 1.2 + Veo 3.1 Lite）与即梦 Seedance 2.0 双模混编兼容入口（V4.7）。仅当用户说“双模工作流”、“双模复刻”、“Veo加即梦”、“混编视频制作”时触发；各棒执行直达独立棒次 Skill。
---

# Google Flow + 即梦 Seedance 2.0 双模混编入口（V4.7）

> **按棒独立路由**：全四棒细则已拆分至独立棒次 Skill（遇错即停、严禁绕行），本入口仅说明双模分流铁律，**只按当前棒次读取对应唯一 Skill**。

## 双模混编专属裁决规则

1. **80% / 20% 配比**：
   - **Google Flow 长期双主力（~80%）**：`Omni Flash 1.2` + `Veo 3.1 Lite` 写 7 要素英文词锁闭嘴留纯环境 SFX，后期按 [`colab-voice-dubbing`](../colab-voice-dubbing/SKILL.md) 配音（P2 `VoxCPM 2` `48000Hz` 母本 + P3 默认 `IndexTTS-2.5` / 按需 `Fish Audio S2 Pro` / `VoxCPM 2`）+ `LatentSync 1.6 @ d058a61`（`24↔25fps` 无损 Pull-up/Pull-down）刷口型；
   - **即梦 Seedance 2.0（~20%）**：正脸高爆发中文对口型特写，挂 `@图片1` 首帧 + `@图片2` 角色肖像 + `@音频1` 已批准 `48000Hz` 声线母本原生直出（标 `SKIP_JIMENG_NATIVE`，第四棒单步超分 + `--audio_codec copy` 保留原生音画）。
2. **铁律**：同场对话正反打严禁混用模型；`>= 2026-12-05` 硬阻断 `Veo 3.1 Quality` / `Veo 3.1 Fast` / `Omni Flash 1.1`。

## 按棒精准直达（严禁一次通读全部）

| 阶段 | 直达独立 Skill |
| :--- | :--- |
| 总控路由 | [`video-recreation/SKILL.md`](../video-recreation/SKILL.md) |
| 第一棒（切分/锁模/双轨出词） | [`video-recreation-p1/SKILL.md`](../video-recreation-p1/SKILL.md)（Flow 能力表见 [`flow-routing-rules.md`](../video-recreation-p1/references/flow-routing-rules.md)） |
| 第二棒（48kHz声线/批次生图） | [`video-recreation-p2/SKILL.md`](../video-recreation-p2/SKILL.md) |
| 第三棒（克隆台词/投喂/九项） | [`video-recreation-p3/SKILL.md`](../video-recreation-p3/SKILL.md) |
| 第四棒 P4A / P4B | [`video-recreation-p4/SKILL.md`](../video-recreation-p4/SKILL.md) |
