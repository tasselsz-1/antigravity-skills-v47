---
name: video-recreation-p3
description: >-
  视频复刻第三棒专属流程（V4.7）：强制匹配声线母本批准清单 SHA 三元组在 Colab 合成 48000Hz 台词与心声（默认 IndexTTS-2.5，按需 Fish Audio S2 Pro / VoxCPM 2）、按 cuts.csv 生成防呆投喂手册与 MusicFX 配乐词、运行正式集静态九项终检交人工生视频。当用户说“第三棒”、“P3”、“克隆台词”、“生成投喂手册”、“九项终检”时触发。
---

# 视频复刻 · 第三棒专属流程（P3 · V4.7）

> **🛑 遇错即停铁律**：Colab 语音合成失败、`声线母本批准清单.json` 三元组失配、`pack-delivery` 命中分档越界或 `2026-12-05` 退役模型阻断，或 `check-delivery` 正式集九项终检返回非零退出码时，**一律原地停机保留现场上报，严禁绕过、篡改或跳步**。
>
> **定岗、前置与必读契约**：由第三棒微主管（**池 B `claude-sonnet-5-5-medium`**，目标 `12–18 步` · 硬熔断 `≤ 28 步`）独立执行，开工首步必先 `read_file` 本卡（涉配音引擎读 [`colab-voice-dubbing`](../colab-voice-dubbing/SKILL.md)，涉 Colab 算力读 [`colab-ssh-rclone`](../colab-ssh-rclone/SKILL.md)，核对 Flow 分档疑难附读 [`flow-routing-rules.md`](../video-recreation-p1/references/flow-routing-rules.md)）。前置要求第二棒已完成 `voice-audit` 签发 `声线母本批准清单.json` 且 `Wave 1..4` 全部图片已 `finalize`/`organize` 归位。

---

## 环节 1：Colab 零样本克隆全集台词与心声 OS（强制校验批准 SHA 三元组）

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第01集>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py build-voice-colab --project-root "<项目根目录>" --episode "第01集" --stage dialogue
```
*（按需切换引擎/算力：追加 `--tts-engine indextts25|fish_s2_pro|voxcpm2` 与 `--gpu L4|A100|H200`；引擎选型与独立 `uv` / Drive 权重规范唯一事实源见 [`colab-voice-dubbing`](../colab-voice-dubbing/SKILL.md)）*

1. **引擎自动路由与硬件**：`build-voice-colab --stage dialogue` 默认自动路由 **`indextts25`**（`IndexTeam/IndexTTS-2.5`，情绪解耦与精准卡时长主力，`--gpu L4`）；长旁白/解说切 **`fish_s2_pro`**（`fishaudio/s2-pro`，需 Colab Secrets `HF_TOKEN`），方言或免费商用切 **`voxcpm2`**（`openbmb/VoxCPM2`，原生 `48000Hz`）。Colab 作业清凭据与释卡按 [`colab-ssh-rclone`](../colab-ssh-rclone/SKILL.md) 执行。
2. **`48000Hz` 统一锁死 + 批准 SHA 三元组强制匹配（彻底剔除 `24kHz` 兼容口）**：
   - 逐条读取《`分集/第NN集/第NN集_台词生成表.csv`》（情绪列 `情绪导演指令`，自动兼容旧表 `VoxCPM2_情绪导演指令` / `CosyVoice2_情绪导演指令`）与第二棒签发的 **`声线母本批准清单.json`**，**强制匹配 `(角色, 说话/心声, approved_sha256)` 三元组**（未批准或 SHA 失配立即非零退出阻断）；
   - 以已批准 `48000Hz` 母本为音色底板 + 情绪导演指令合成并经 `[0.90, 1.15]` `atempo` 门禁（超限 `DIALOGUE_OVER_BUDGET` 阻断）统一导出 **`48000Hz` Mono `pcm_s16le`**：
     - **Flow 开口镜** ➔ `分集/第NN集/台词音频/segment_XX_镜头YY_角色新姓名_台词.wav`（供第四棒 `P4A` 驱动 `LatentSync` 刷嘴型，并独立挂载至达芬奇 `Lane 1 Dialogue_Track`）；
     - **全集心声 OS** ➔ `分集/第NN集/台词音频/segment_XX_镜头YY_角色新姓名_心声.wav`（免刷口型，独立挂载至 `Lane 2 OS_Track`）；
     - **即梦（Seedance 2.0）开口镜** ➔ 识别 **`SKIP_JIMENG_NATIVE`** 直接跳过单句合成（网页端直挂 `@音频1` 声线母本原生直出音画口型）。

---

## 环节 2：按 `cuts.csv` 生成防呆投喂手册与精准积分账单（`pack-delivery`）

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py pack-delivery --project-root "<项目根目录>" --episode "第01集"
```

输出《`分集/第NN集/第NN集_多模态视频推流投喂手册.md`》（同步至 `05_台词配音与心声轨/`），核对四大区块合规：
1. **Step 0（Flow `Characters` 预建与画幅核对）**：`_肖像.png` 传【肖像】、`_主体.png` 传【创建主体】；置顶红线：**Google Flow 全程不传任何音频，并发锁死 `x1`，画幅按项目 `aspect_ratio` 锁定（默认 `16:9` 横屏；竖屏 `9:16` 同步在 Flow 与即梦选 `9:16`）**。
2. **Step 1（Flow 逐段投喂卡 · 对齐官方分档与 `2026-12-05` 退役线）**：
   - **`Omni Flash 1.2`（长期主力，`3 点/秒`）**：普通生成无订阅 **`≤ 10s`**（`4/6/8/10s`）、`Plus/Pro/Ultra` 付费 **`≤ 15s`**；**`Extend` 仅限付费订阅且单次 `+3–10s`**；支持 `Ingredients` 与 `Frames`；
   - **`Veo 3.1 Lite`（长期主力，`5 点/条`）**：`4s/6s` 仅 `Frames`（禁挂角色卡；含余量禁尾帧）；**`Ingredients ≤ 3 图` 仅限 `8s`**；
   - **`2026-12-05` 退役三模（`>= 2026-12-05` 硬阻断）**：`Veo 3.1 Quality`（禁 `Ingredients`，仅 `Text/Frames 4/6/8s`）、`Veo 3.1 Fast`、`Omni Flash 1.1`（仅离散 `4/6/8/10s`）；
   - **分辨率下载**：黄金前 15s 开站内 `4K Upscale (+50点)` 下载；15s 后一律 `0 点免费升 1080p` 下载；附零占位符 7 要素英文一键复制框。
3. **Step 2（即梦 Seedance 2.0 投喂卡）**：明确挂 `@图片1` 首帧 + `@图片2` 角色肖像 + `@音频1` 已批准 `48000Hz` `2–3s .wav` 声线母本 + 中文音画提示词框。
4. **Step 3（MusicFX 0 积分配乐词）**：附 3–4 条 Google Labs MusicFX 免费 `48000Hz` 纯器乐（`instrumental only, no vocals`）英文提示词。

---

## 环节 3：正式集静态九项齐套终检（`check-delivery` · 非零退出硬门禁）

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py check-delivery --project-root "<项目根目录>" --episode "第01集"
```

逐项硬检九项必交物（任一未达标非零退出立即停机上报）：
1. `人物/`（双 `#FFFFFF` 纯白底图）
2. `声线/`（`2.00s–3.00s` `48000Hz Mono pcm_s16le` 说话+心声母本，匹配 `声线母本批准清单.json` 三元组）
3. `第NN集_视频提示词.md`（零占位符，与 `中间文件/提示词维护/视频提示词/第NN集/当前主稿.md` SHA-256 完全一致）
4. `首尾及关键帧/`（扁平唯一命名 `segment_XX_镜头YY_首帧/尾帧.png`，豁免 `need_tail_frame: false`）
5. `第NN集_分镜头脚本.xlsx`（标准 12 列）
6. `原视频段落/`（`segment_XX.mp4`）
7. `第NN集_字幕时间轴.csv`（UTF-8 BOM）及 `第NN集_字幕.srt`
8. `第NN集_台词生成表.xlsx` / `.csv`（标准 9 列）+ `台词音频/*.wav`（`48000Hz` 齐全）
9. `第NN集_剧本故事梗概与逐镜导演脚本.md`

全部 `PASS` 后向根主控回传 `≤ 15 行` 摘要退出，进入**人工断点 ①（按投喂手册网页端生视频并回盘至 `<在制工作区>/原始生成视频/`）**；视频回盘后下一阶段触发 [`video-recreation-p4`](../video-recreation-p4/SKILL.md)。
