# 第四棒 A（`P4A`）：Colab 按需口型超分 + FCPXML 1.9 DTD 排布预检 + 达芬奇 21 交接

> **触发时机与必读**：人工将网页端生成的 `segment_XX.mp4` 放入 `<在制工作区>/原始生成视频/` 后执行。硬件优先 Colab **`--gpu L4`**（按需 `A100`），Colab CLI / SSH / G 盘直连与 `try/finally` 清凭据关机规范详见 [`colab-ssh-rclone`](../../colab-ssh-rclone/SKILL.md)；任务无论成败必须强制 `rm -f /tmp/rclone.conf` + 刷盘 + `runtime.unassign()` / `colab stop`，核验 `Active assignments: 0`。

## 1. `build-colab`：回盘体检 + 生成实测可跑 Colab `.ipynb`（默认硬锁 `24fps`）

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py build-colab --project-root "<项目根目录>" --episode "第01集"
```

1. **硬锁 `24fps` 与视频流独立帧数核验**：
   - 未显式传 `--target-fps` 时一律硬锁 `24fps`（旧任务清单残留 `target_fps=60` 自动告警覆盖为 `24`）；全链路帧率容差 **`23.95–24.05fps`**，免跑 `RIFE` 光流插帧；
   - 按**视频流独立可读帧数与时间戳**核验 `missing_frames == 0`，严防长音轨掩盖短画面。
2. **Colab `.ipynb` 五大硬核标准**：
   1. **直读第三棒 `48000Hz` 台词 & 纯超分按需跳过口型加载**：砍掉重复配音空桩；若任务无开口对白，**跳过 `LatentSync` 仓库克隆、依赖安装与模型初始化**直接超分。
   2. **全相对云盘工程路径**：统一基于 `/content/drive/MyDrive/短剧复刻工作站/...` 相对寻址。
   3. **黄金前 15s 原生高分片段全量纳入刷口型**：有开口台词照常在原生 `24fps` 跑无损 Pull-up/Pull-down 口型；无开口台词按当前 `--p4-profile` 核验归入 `已超分1K_24fps/`（或显式 4K 档位 `已超分4K_24fps/`）。
   4. **固定 `LatentSync 1.6 @ d058a61` + 依赖隔离与 `--help` 前置自检**：存在开口对白时，用 `uv venv --python 3.10` + `MPLBACKEND=Agg` 隔离检出可检出提交 **`d058a61`**（`Release LatentSync 1.6`），安装 `requirements.txt` 并强制补齐 **`DeepCache==0.1.1`** 与 `huggingface_hub`，下载 **`ByteDance/LatentSync-1.6`** 权重（`checkpoints/latentsync_unet.pt` + `checkpoints/whisper/tiny.pt`），推理前先跑 `python -m scripts.inference --help` 自检；合并段含多句台词按段内精确时间戳拼临时驱动音轨，调用官方 CLI：
      `python -m scripts.inference --unet_config_path configs/unet/stage2_512.yaml --inference_ckpt_path checkpoints/latentsync_unet.pt --video_path <in.mp4> --audio_path <drv.wav> --video_out_path <out.mp4>`
   5. **声画轨物理分离 + `24↔25fps` 无损 Pull-up/Pull-down 帧锁 + 单步 GPU `antialias` 超分**：
      - **Flow 镜头**：进 `LatentSync` 前 `24→25fps` 无损 Pull-up（音频同比例对齐），推理后逆向 `25→24fps` 无损 Pull-down（1:1 零丢帧、零重复帧，**严禁 `-vf fps=24` 强转掉帧**）；**音频流 100% 仅保留原 Flow 纯环境 SFX，严禁把 `_台词.wav` 烧死进 `.mp4`**！
      - **即梦（`SKIP_JIMENG_NATIVE`）镜头**：不动口型，按当前 `--p4-profile` 单步 `spandrel` FP16 GPU `antialias` 直出目标分辨率，保持 `23.95–24.05fps` + `--audio_codec copy` 保留原生音画。
      - **Step 2 内嵌自举与四档单步直出**：开头执行 `ensure_vsr_pipeline_env()` 将内嵌 `vsr_pipeline/batch_processor.py` 写出至 `_runtime_modules/` 并注入 `PYTHONPATH`；严禁两步阶梯超分——动漫档（`anime_1k`/`anime_4k`）走 `realesr-animevideov3.pth` + `spandrel` FP16 GPU `antialias` 单步直出；真人主力档（`live_1k`）原生 `1080p` 直通封装、`<1080p` 走 `RealESRGAN_x2plus.pth` 定标至 `1K`；显式传 `--target_fps 24` 免跑 RIFE；结束后自动刷盘关机。

## 2. `build-fcpxml`：零回退 + 累计切点帧号表 + FCPXML 1.9 DTD 实时校验

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py build-fcpxml --project-root "<项目根目录>" --episode "第01集"
```

1. **零回退与独立帧数门禁**：**严禁回退引用 `原视频段落/*.mp4`**！仅引用对应 `--p4-profile` 与 `aspect_ratio` 复测达标的 `已超分1K_24fps/*.mp4`（或 `已超分4K_24fps/*.mp4`），要求 `23.95–24.05fps`、`missing_frames == 0` 且视频流时长 `>= cuts.csv` 目标 `real_dur`，缺段缺帧立即非零退出。
2. **累计切点 `24fps` 帧号表（消除累积漂移）**：先将每个累计切点映射为 `24fps` 帧号 `F_i`，再用相邻帧差 `F_i - F_{i-1}` 计算段长，使 V1 画面、对白轨与字幕时间点共用同一张切点帧号表。
3. **FCPXML 1.9 DTD 严格合规（内置 `lxml.etree.DTD` 实时校验）**：
   - 时间基准：`1K` 用 `FFVideoFormat1920x1080p24`（竖屏 `FFVideoFormat1080x1920p24`），`4K` 用 `FFVideoFormat3840x2160p24`（竖屏 `FFVideoFormat2160x3840p24`），统一 `frameDuration="100/2400s"`；
   - `<asset>` 统一使用子元素 **`<media-rep kind="original-media" src="..."/>`**（禁 `<asset src="...">`）；
   - 对白/心声/配乐 `<asset-clip>` 统一使用 **`audioRole`** 属性（禁 `role`）；未过 DTD 校验立即非零退出。
4. **排布预检落盘**：自动写入 `V1`（裁除尾部 Handles 余量）、`Lane 1 (audioRole="dialogue")`、`Lane 2 (audioRole="effects")` 及配乐轨，输出 **《`口型与字幕时间点核验记录.json`》**（仅作时间线排布预检，**严禁代替 P4B 母带实测**）与 **《`时间线与达芬奇交接单.md`》**。

## 3. 人工断点 ②：DaVinci Resolve 21 导出与交接硬规范

DaVinci Resolve 21 免费版限制控制台 `dofile`/外部脚本，标准交付统一走 GUI **`文件 → 导入 → 时间线… (Ctrl+Shift+I)`** 导入 `.fcpxml`，对照《`时间线与达芬奇交接单.md`》完成 **4 项人工确认** 后导出对应档位母带（`分集/第NN集/第NN集_1K24fps_成片.mp4` 或 `第NN集_4K24fps_成片.mp4`）：
1. **三节点破塑调色树（Color 页面 Timeline 全局节点）**：`Node 1`（曝光/色温一级校色）➔ `Node 2`（`Midtone Detail = +20` 中频破塑消蜡感）➔ `Node 3`（`Kodak 2383` 胶片 LUT）；
2. **`V2` 胶片颗粒层**：挂载 `35mm Film Grain Overlay`（混合模式 `Overlay`）；
3. **切镜处 `0.25s` L-Cut / J-Cut 微调**：抽听对话切镜声音桥过渡；
4. **字幕描边与编码规格**：导入/确认 `第NN集_字幕.srt`，**字幕轨统一开启 `2–3 px` 纯黑描边（Stroke）防亮部吃字**；交付页选 **`H.264 Master`**（格式 `MP4`、网络优化）时**必须显式选择 `High Profile`**（或改用 `H.265 Main10`）渲染导出。

导出完成后汇报并交由 **`P4B`（[`p4b-master-verify-seal.md`](./p4b-master-verify-seal.md)）** 执行两步封板。
