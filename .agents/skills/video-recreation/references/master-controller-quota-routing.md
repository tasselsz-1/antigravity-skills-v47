# V4.7 模型实力基线、双配额池四核定岗、双通道编排与哈希防伪契约

> 本文件仅在需要核查模型精确 ID、配额池分工、双通道编排边界或防伪工程死律时**按需查阅**；各棒具体作业步骤已独立拆入 `video-recreation-p1` / `p2` / `p3` / `p4` / `fix`。

## 1. 现有可用模型实力排列与双配额池机制（9 系列 / 27 ID 实测基线）

> **双独立配额池**：配额池 A（Google Gemini）与配额池 B（Anthropic / 第三方）互不挤占、独立重置。多模态看图/高速调度走池 A，美学写词/CLI 门禁合稿走池 B，实现配额翻倍与真跨厂商异构双盲审。

| 梯队 | 模型系列（精确 `swarm` ID 档位） | 配额池 | 实测定位 | 视觉 | V4.7 生产线角色 |
| :---: | :--- | :---: | :--- | :---: | :--- |
| **T0** | **`Gemini 4 Neon`** (`gemini-4-neon-{low,medium,high,xhigh}`) | 池 A | AA Index 61 @ xhigh，~110 tok/s，原生多模态 | ✅ | W2 全集导演圣经与锁模路由（`high`，复杂集升 `xhigh`） |
| **T0** | **`Claude Opus 5.5`** (`claude-opus-5-5-{low,medium,high}`) | 池 B | AA Index ~58 · GDPval 1846 Elo，美学与长细则极强 | ✅ | W2 Stage A 定调（`medium`）、W3 刀刃块（`high`） |
| **T0** | **`Gemini 4 Carbon`** (`gemini-4-carbon-{low,medium,high}`) | 池 A | AA Index 60 @ high，~55 tok/s，重型长程 | ✅ | **按需会诊兜底**（仅极端跨集冲突/连续打回卡死），不入常态 |
| **T1** | **`Claude Sonnet 5.5`** (`claude-sonnet-5-5-{low,medium,high}`) | 池 B | GDPval 1844 · Terminal-Bench 4.0 70.6%，CLI 门禁之王 | ✅ | W3 常规块 + W4 合稿 + P3/P4 微主管（`medium`）；P2 疑难跨池复核（`high`） |
| **T1** | **`Gemini 4 Hydrogen`** (`gemini-4-hydrogen-{low,medium,high,xhigh}`) | 池 A | AA Index 54 @ xhigh / 51 @ high，~198 tok/s，快 3.8 Flash 2.5x | ✅ | Root/W1/P2 微主管（`medium`）；W2/W3 盲审 + P2 持图双检 + 改图视觉（`high`） |
| **T2** | **`Orchestrator`** (`gemini-pro-orchestrator{-neon,,-argon}`) | 池 A | 单窗接力预设 | ✅ | 仅作单窗可选预设，**不作双盲审显式 `author/inspector_agent` ID** |
| **T3** | **`Gemini 4 Argon`** (`gemini-4-argon-{low,medium,high}`) | 池 A | 性价比全面倒挂 | ✅ | **正式淘汰（移出主生产线）** |
| **T3** | **`Gemini 3.8 Flash`** (`gemini-3.8-flash-{low,medium,high}`) | 池 A | 上代轻量快模 | ✅ | 仅退守 `invoke_subagent` / `run_workflow` 的 `model="flash"` 纯机械生图包工头 |
| **T4** | **`GPT-OSS 120B`** (`gpt-oss-120b-medium`) | 池 B | 纯文本 MoE | ❌ | **严禁分派看图/审图任务**，不入主线 |

## 2. 双通道编排硬边界

1. **精益单席位 / 分片 `swarm`**：需跨配额池精确指定模型 ID 时使用 `swarm(action="add", model="<精确ID>", tasks=[...])`；产物 100% 落盘，留言板 `≤ 3 行` 路径通知，验收即 `swarm(action="kill")` 释放，严禁群聊广播。
2. **原生 `invoke_subagent` / `run_workflow`（生图通道硬边界）**：`swarm` worker 工具集不含 `invoke_subagent` / `generate_image`，因此**第二棒及定点改图中调用 `image-generator` 的总管与生图包工头必须通过 `invoke_subagent` 或 `run_workflow` 拉起**（严禁放进 `swarm` worker）。

## 3. `stamp-visual-audit` 跨厂商哈希防伪契约

盲审记录与第二棒批次通过回执强制核验五项硬标准：
1. `author_agent != inspector_agent`（默认真跨厂商：`claude-opus/sonnet-5-5-*` vs `gemini-4-hydrogen-high`）；
2. **禁自动编造默认 `run_id`**：必须显式传入真实 `author_run_id != inspector_run_id`；
3. **100% 覆盖清单全部单元**（漏审任一单元立即非零退出阻断）；
4. 实时锁死 **`prompt_sha256`** 与 **`refs_sha256`**；
5. **自动失效铁律**：修改任一提示词或替换任一参考图后，旧回执哈希失配自动失效，必须重审盖戳。

## 4. 防上下文膨胀与并发限流四条工程死律

1. **一窗一新实例 + 按棒按窗单卡加载**：全新空白上下文启动，首步跑 `phase-status`，只读本棒/本窗单份独立 Skill/子卡，严禁跨棒通读与向旧主管续命。
2. **科学步数预算**：微主管目标 `12–18 步`（硬熔断 `≤ 28 步`）；一线工兵单次专注 1 微场次或 1 批次（`≤ 25 步`），回传 `≤ 5 行`。
3. **并发配额原子预占与幂等核销**：第二棒单窗弹性并窗 `≤ 54` 基础单元（预留 10% 缓冲 `≤ 60` 次），每次生图/重绘前必调 `quota-reserve`、返回后必按 `reservation_id` 调 `quota-reconcile`，触及 `60` 次或任一 429 触发跨窗全局冷却即硬阻断。
4. **全四棒 `phase-status` 与两步人工封板实时复验**：`P1_W1..W4` → `P2_VOICE_AND_WAVES` → `P3_DIALOGUE_AND_DELIVERY` → `P4A_VSR_AND_FCPXML` → `P4B_MASTER_VERIFY` → `WAIT_HUMAN_MASTER_REVIEW` → `ALL_DONE`（仅剪辑师跑完 `phase4-confirm-master` 且每次恢复实时复验母带物理存在、`confirmed_master_sha256` 一致才置 `ALL_DONE`）。
