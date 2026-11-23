---
name: colab-voice-dubbing
description: >-
  Colab（L4/A100/H200）开源声音模仿与视频配音单一事实源：VoxCPM 2（48kHz 捏母本/方言）、IndexTTS-2.5（控情绪/卡时长主力）、Fish Audio S2 Pro（拟人旁白）选型、两阶段 SOP、独立 uv 隔离与 H200 Notebook 规范。触发口令：“声音模仿”、“视频配音”、“Colab配音”、“H200配音”、“启动IndexTTS”、“启动Fish Speech”、“跑开源TTS”。
---

# Colab 开源声音模仿与视频配音工作流（`colab-voice-dubbing`）

## 1. 定位与流水线衔接（单一事实源）

- **适用范围**：覆盖个人独立声音模仿/配音创作，并作为工作区 V4.7 视频复刻流水线的**配音引擎选型与 Colab 环境规范唯一事实源**。
- **两阶段范式**：**先出母本（克隆或原创捏定唯一声线）→ 再配台词（锁定母本控情绪、卡时间轴、48kHz 交付）**。
- **正式视频复刻流水线映射**（`batch_ops.py build-voice-colab` 自动路由）：
  - **第二棒声线母本**（[`video-recreation-p2`](../video-recreation-p2/SKILL.md)）：`--stage voice-ref` 默认 **`VoxCPM 2`**（`openbmb/VoxCPM2`，原生 `48000Hz`、`--gpu L4`）捏 `说话`+`心声` 双母本，经 `voice-audit` 签发 `声线母本批准清单.json`。
  - **第三棒全集配音**（[`video-recreation-p3`](../video-recreation-p3/SKILL.md)）：`--stage dialogue` 默认 **`IndexTTS-2.5`**（`--tts-engine indextts25`，情绪解耦 + 精准卡时长主力），按需切换 `fish_s2_pro`（长旁白）或 `voxcpm2`（方言/免费商用）；支持 `--gpu L4|A100|H200`，强制匹配批准清单 SHA-256 三元组并输出 `48000Hz Mono pcm_s16le`。

---

## 2. 开源 TTS 选型矩阵

### 2.1 默认黄金组合

| 定位 | 模型（仓库） | 核心优势 | 适用场景 |
| :--- | :--- | :--- | :--- |
| **配音主力（控戏 + 卡时长）** | **`IndexTTS-2.5`**（`IndexTeam/IndexTTS-2.5`） | 音色与情绪解耦（支持文本指令/滑块/原片情绪迁移）+ 精准时长控制 | 剧情对白、强情绪戏、严卡镜头时间窗与对口型（P3 默认） |
| **拟人天花板（自然松弛）** | **`Fish Audio S2 Pro`**（`fishaudio/s2-pro`） | 呼吸感、断句节奏与去 AI 味第一档，覆盖 80+ 语种（HF Gated Repo 需 `HF_TOKEN`） | 解说、长旁白、自然口语聊天型视频 |
| **起手捏声 / 方言 / 免费商用** | **`VoxCPM 2`**（`openbmb/VoxCPM2`） | Apache 2.0 免费商用、纯中文自然语言捏声线、原生 **48kHz**、9 种中国方言 | 无现成人声时原创捏母本（P2 默认）、方言配音、商用项目 |

### 2.2 细分场景按需补位

| 需求 | 模型 | 说明 |
| :--- | :--- | :--- |
| **1~5 分钟固定角色极致复刻** | **`GPT-SoVITS`**（MIT） | H200 上 1~2 分钟少样本微调，尾音/口癖还原上限最高 |
| **商用多语种 / 高并发流式** | **`Qwen3-TTS-1.7B`** / **`CosyVoice 3`**（Apache 2.0） | 3 秒克隆、支持 VoiceDesign 与中英混读，vLLM/SGLang 高吞吐 |
| **严格 MIT 协议多语种情感** | **`Chatterbox`**（MIT） | 英文及多语种情感张力控制 |
| **无 GPU / CPU 轻量朗读** | **`Kokoro-82M`**（Apache 2.0） | 82M 超小参数纯朗读兜底 |

---

## 3. 两阶段创作 SOP

### 阶段一：建立角色唯一声线母本
1. **有现成素材**：短素材（5~30s）先经 UVR5 / BS-RoFormer / Demucs 去伴奏去混响，截取 **8~12s 语速平稳、情绪中性、咬字清晰**干声作母本；1~5 分钟固定角色可在 H200 用 `GPT-SoVITS` 做少样本微调。
2. **无素材从零原创**：用 **`VoxCPM 2`** 按中文描述（年龄/质感/语速/方言）批量抽 5~10 条候选，盲听锁定唯一 `48kHz` 清晰母本（复刻流水线按 `voice-audit` 门禁锁定 `2.00–3.00s` `说话`+`心声` 双母本）。

### 阶段二：分镜头配音与交付
1. **锁定唯一母本**：全片同角色固定同一份中性母本，**严禁**因愤怒/哭腔更换底板防音色漂移。
2. **按镜路由引擎**：强情绪/卡时长/对口型 → `IndexTTS-2.5`；长旁白/解说 → `Fish Audio S2 Pro`；方言/免费商用 → `VoxCPM 2`。
3. **Best-of-N 与交付**：高算力卡（L4/A100/H200）关键句可抽 2~3 条择优（批处理可挂 `Qwen3-ASR-1.7B` 自动回检错漏字）；成品统一导出 **`48kHz Mono WAV (pcm_s16le)`** 导入剪映 / PR / 达芬奇，按需在同卡衔接 `LatentSync 1.6` 刷口型。

---

## 4. 两种 Colab 运行模式

- **模式 A（交互式 WebUI，个人创作首选）**：直接导入开箱即用 Notebook [`resources/colab_h200_voice_dubbing.ipynb`](./resources/colab_h200_voice_dubbing.ipynb)（唯一代码源）；前置配置（`HF_TOKEN` Secrets）与排错速查见 [`references/colab-notebook-cells.md`](./references/colab-notebook-cells.md)。
- **模式 B（无人值守批处理 / 复刻流水线）**：统一复用 [`colab-ssh-rclone/SKILL.md`](../colab-ssh-rclone/SKILL.md) 的 `colab` CLI + `rclone gdrive:` 内网通道与 `try/finally` 强清凭据关机规范，**严禁另起野通道**。

---

## 5. 绝对红线与验收

1. **存储动静分离 & 省算力**：权重与 `HF_HOME` 统一持久化至 Google Drive **`/content/drive/MyDrive/tts_studio/{models,hf_cache}`**（与复刻流水线共享缓存，首次下载大权重优先用免费 CPU 运行时，下完再切 GPU）；源码与独立 `uv` 虚拟环境放本机 SSD（`/content/`）；导完确认落盘即释放运行时，禁空烧 GPU。
2. **环境与端口隔离**：`IndexTTS-2.5`、`Fish Audio S2 Pro`、`VoxCPM 2` 各自使用独立 `uv` 虚拟环境，禁止混装系统 Python；同时间只启一个 `7860` WebUI Cell。
3. **凭据与合规**：严禁明文写 `HF_TOKEN`，一律通过 Colab **Secrets**（`userdata.get("HF_TOKEN")`）注入；公开发布视频严禁未经授权模仿在世明星或特定真人声音。
4. **交付自检**：权重与成品已落 Google Drive、WebUI 或批处理任务零报错、全片音色稳定无爆音且时长贴合画面、Colab GPU 运行时已释放归零。
