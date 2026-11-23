# 按需备选：60fps 高刷插帧专项模式 SOP V4.7（默认不触发，仅显式 `--target-fps 60` 读取）

> **🛑 零冗余隔离与遇错即停铁律**：
> 1. **默认严禁读取**：常规第一～四棒未显式传 `--target-fps` 时一律硬锁原生 **`24fps`（`23.95–24.05fps`）**，全员严禁读取本文件（0 Token）。
> 2. **唯一触发条件**：仅当用户明确下达 **`“使用 60 帧高刷模式”`**、**`“开启 60fps 插帧”`** 或 **`“--target-fps 60”`** 时，第四棒微主管（池 B `claude-sonnet-5-5-medium`）按需读取并全程传 `--target-fps 60`。
> 3. **遇错即停**：触犯黑名单题材强插、合并段跨镜硬切未开启防融帧保护、口型与插帧时序倒置、视频流缺帧（`missing_frames != 0`）、FCPXML 1.9 DTD 失败或两步母带门禁非零退出，一律原地停机上报。

## 一、题材红黑榜（严防盲目插帧毁片）

- **🟢 白名单（推荐）**：① 3D CG 修仙/玄幻/机甲爽剧；② 现代都市高刷竖屏短剧/强节奏现代电子视觉流。
- **🔴 黑名单（严禁强插）**：① 院线历史/战争/悬疑胶片风真人剧（会毁掉 24fps 抽帧律动产生廉价肥皂剧效应，且火光/硝烟/暗部颗粒出果冻重影）；② 一拍二/一拍三（On Twos/Threes）2D 手绘番剧与水墨国风动画（线条撕裂、五官叠影）。

## 二、三大防翻车工程铁律

### 铁律 1：多镜合并段硬切防鬼畜融帧协议（二选一，严禁裸跑 `RIFE`）
- **方案 A（首推 · 零融帧）**：按物理切点将合并段拆为独立单镜子片段 → 分别跑 `RIFE v4.6`（`24→60fps`）→ 按精确帧边界无损 `concat` 拼回，并仅混流原纯净环境 SFX（绝不烧死人声）。
- **方案 B**：`RIFE v4.6` 显式开启转场突变检测保护 **`--scdet`**，回盘后抽检所有切镜点前后 `±2` 帧确认零半透明双脸残影。

### 铁律 2：口型与插帧先后时序锁（严禁先插 60fps 再刷嘴）
`原生 24fps 视频` → **Step 1：原生 `24fps` 按需运行 `LatentSync 1.6 @ d058a61`**（纯超分无开口跳过；防爆破音嘴唇粘连与算力空耗翻 `2.5` 倍）→ **Step 2：空间超分至目标分辨率（原生 4K 跳过）** → **Step 3：带硬切防融帧保护的 `RIFE v4.6` 升至 `60fps`** → **Step 4：仅混流原纯环境 SFX 封装 `.mp4`**（即梦镜头不动口型，超分 + 防融帧 `RIFE` 升 `60fps` 后 `--audio_codec copy` 保留原生音画）。

### 铁律 3：全链路 `--target-fps 60` 切换对照

| 维度 | 主线默认（`24fps`） | 备选高刷（显式 `--target-fps 60`） |
| :--- | :--- | :--- |
| **CLI 参数** | 默认 `24`（旧残留 `60` 告警覆盖为 `24`） | **全程必传 `--target-fps 60`** |
| **超分底片目录** | `已超分1K_24fps/` 或 `已超分4K_24fps/` | **`已超分1K_60fps/` 或 `已超分4K_60fps/`** |
| **FCPXML 1.9 `<format>`** | `FFVideoFormat*p24` (`100/2400s`) | **`FFVideoFormat*p60` (`100/6000s`)**，按 `60fps` 累计切点帧号表 `F_i - F_{i-1}` |
| **成片命名与帧率容差** | `*_24fps_成片.mp4` (`23.95–24.05fps`) | **`*_60fps_成片.mp4` (`59.90–60.10fps`、`missing_frames == 0`)** |

## 三、标准执行命令与 Colab / 达芬奇差异核对

```powershell
# P4A（带 --target-fps 60）
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py build-colab --project-root "<项目根目录>" --episode "第01集" --target-fps 60
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py build-fcpxml --project-root "<项目根目录>" --episode "第01集" --target-fps 60

# P4B 两步封板（达芬奇导出 *_60fps_成片.mp4 后）
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase4-verify-master --project-root "<项目根目录>" --episode "第01集" --target-fps 60
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase4-confirm-master --project-root "<项目根目录>" --episode "第01集" --reviewer "<剪辑师>" --confirm-dialogue --confirm-lipsync --confirm-subtitles --notes "<60fps高刷回看确认>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第01集>"
```

**Colab 内核范式**：
```bash
# Step 1: 原生 24fps 下按需跑 LatentSync 1.6 @ d058a61
python -m scripts.inference --unet_config_path configs/unet/stage2_512.yaml --inference_ckpt_path checkpoints/latentsync_unet.pt --video_path seg_in_24fps.mp4 --audio_path seg_dialogue_48k.wav --video_out_path seg_lipsynced_24fps.mp4
# Step 2: 空间超分后按物理切点拆镜或 --scdet 跑 RIFE v4.6 升 60fps
python inference_video.py --exp=2 --fps=60 --scdet --video=seg_24fps_subshot.mp4 --output=seg_60fps_subshot.mp4
# Step 3: 仅混流原 Flow 纯环境 SFX（禁烧死人声）
ffmpeg -y -i seg_60fps_video_only.mp4 -i seg_in_24fps.mp4 -map 0:v:0 -map 1:a:0? -c:v libx264 -crf 16 -r 60 -c:a aac -ar 48000 已超分4K_60fps/segment_XX.mp4
```

**达芬奇 60fps 专项核对**：① 项目与时间线帧率锁定 `60 fps`（禁 24fps 时间线混排）；② 重点抽检合并段硬切前后 `±2` 帧零融帧残影；③ `V2` 胶片颗粒不透明度下调 `30%–40%` 防高频闪烁；④ 导出恒定 `60fps` + `48000Hz` 母带并走完两步封板。
