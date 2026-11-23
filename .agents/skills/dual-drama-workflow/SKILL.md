---
name: dual-drama-workflow
description: >-
  Antigravity 双配额池异构兼 Codex/GPT 双端接力短剧流程兼容入口（V4.7）。仅当用户说“双端短剧流程”、“Codex双端接力”时触发；日常各棒直接触发 video-recreation-p1/p2/p3/p4。
---

# 双配额池异构兼双端接力工作流入口（V4.7）

> **按棒独立路由**：全四棒细则已拆分至独立棒次 Skill（遇错即停、严禁绕行），本入口仅说明双端接力要点，**只按当前棒次读取对应唯一 Skill**。

## 双端接力专属要点

1. **Antigravity 双池四核异构**：详见 [`video-recreation/references/master-controller-quota-routing.md`](../video-recreation/references/master-controller-quota-routing.md)。
2. **GPT / Codex 第二棒双端接力**：若将第二棒生图交由 Codex/GPT 端执行，仍严格复用 `batch_ops.py plan-batches` 物化的 `jobs.json`（弹性并窗 `≤ 54` 基础单元），每次生图/重绘前经 `quota-reserve` 原子预占、返回后经 `quota-reconcile --reservation-id` 幂等核销，并经两阶 `prototype-sheet --stage wave1/wave2` 与显式异源 `run_id` 双检后执行 `finalize` / `organize`。

## 按棒精准直达（严禁一次通读全部）

| 阶段 | 直达独立 Skill |
| :--- | :--- |
| 总控路由 | [`video-recreation/SKILL.md`](../video-recreation/SKILL.md) |
| 第一棒（脚本/出词/盲审） | [`video-recreation-p1/SKILL.md`](../video-recreation-p1/SKILL.md) |
| 第二棒（48kHz声线/批次生图） | [`video-recreation-p2/SKILL.md`](../video-recreation-p2/SKILL.md) |
| 第三棒（克隆台词/投喂/九项） | [`video-recreation-p3/SKILL.md`](../video-recreation-p3/SKILL.md) |
| 第四棒 P4A / P4B | [`video-recreation-p4/SKILL.md`](../video-recreation-p4/SKILL.md) |
