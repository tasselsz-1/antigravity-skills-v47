# 第一棒 · 窗 1（`P1-切分与ASR窗`）作业子卡

> **定岗与职责**：池 A `gemini-4-hydrogen-medium` 微主管（目标 `12–18 步` · 硬熔断 `≤ 28 步`）+ Colab `--gpu T4` 极速通道（CPU 兜底）。本窗严禁提前写导演脚本或生图词。

## 1. 静态薄卡、6 分钟正片边界与画幅锁定

1. **静态薄卡**：项目根目录仅维护 `< 1.5k Tokens` `全局设定/全剧世界观与角色注册表.json`，严禁扫描整部长视频。
2. **画幅透传（`--aspect-ratio 16:9|9:16`）**：默认 `16:9` 横屏；竖屏项目 `init` 时显式传 `--aspect-ratio 9:16` 写入 `分集索引.json` 并全链路透传至生图、提示词/投喂、第四棒超分与 FCPXML 1.9。
3. **360s 正片物理锁死**：单集转码、切分与 ASR 100% 物理锁死本集 `360s` 正片（通过 `--skip-intro-sec` / `--skip-outro-sec` 剔除片头片尾）；`.ts / .m2ts / .flv / .mkv` 等 VFR 流先重整为恒定帧率 `正片母本_CFR.mp4`。

## 2. Colab `--gpu T4` 执行 `TransNetV2` + `faster-whisper` 与强制关机

1. **云端执行通道**：优先通过 [`colab-ssh-rclone`](../../colab-ssh-rclone/SKILL.md) 或 `cloud_pipeline.py`（支持 G 盘 `.ipynb` 一键执行器 / `colab_mcp` 直连）在 **Colab `--gpu T4`**（用户已授权，省时 20x，`< 0.1 CU`；`faster-whisper` 自动探测 CUDA `float16` / CPU `int8`）运行：
   - **`TransNetV2` 神经网络切分**（精准识别硬切与慢叠化，抗闪光/爆闪误切）+ `0.15s` 安全内缩防串镜 + 首/中/尾三点抽帧，输出客观 `物理切点清单.json` / `.csv`；
   - **`faster-whisper` 语音识别**：生成带精确起止时间戳与逐句文本的 **`asr_transcript.json`**，供窗 2 与窗 3 零偏差对齐台词时间轴。
2. **遇错即停与强校验**：
   - 强校验 `phase1_colab_done.json` 中 **`colab_cpu_verified == true`**（兼容字段，GPU 运行时同样写入；另记 `colab_runtime_verified` + `colab_accelerator`）；失败立即原地停机上报，**严禁 `--force-local`**！
   - 作业无论成败必须经 **`try/finally` 强制 `rm -f /tmp/rclone.conf` + `os.sync()` → `drive.flush_and_unmount()` → `runtime.unassign()` / `colab stop`**，并核验 `Active assignments: 0`、`Usage rate: 0.00/hr`，严禁留孤儿 GPU 实例。

## 3. 净参考源帧、原型预处理与 `build-storyboards`

1. 运行 `clean-source-frames --mask-corner-logo` 裁除上下黑边并柔化角落台标，输出 `ref_clean_frames/*_净参考.jpg`（供分镜生图固定作为 `Image 1` 使用；`contact` 左列仍展示原片全貌）。
2. 若本集含真人原型/历史人物，运行 `prep-prototype-refs` 执行真水平镜像与胸像裁切，输出 `ref_prepared/*_镜像胸像参考.jpg`。
3. 运行 `build-storyboards` 生成 **`1–6` 张 `1600px` 全集故事板总览图**（及 `storyboard_index.json`），供窗 2 `Neon High/XHigh` 一次性通览全集结构。

## 4. 窗 1 标准执行命令

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第01集>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py init --project-root "<项目根目录>" --episode "第01集" --source-video "<原视频路径>" --source-range "0-360s" [--aspect-ratio 16:9|9:16]
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/cloud_pipeline.py --phase phase1 --project-root "<项目根目录>" --episode "第01集" --skip-intro-sec <片头秒数> --skip-outro-sec <片尾秒数>
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py clean-source-frames --input-dir "<中间文件/原片分析/第01集>" --mask-corner-logo
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py build-storyboards --ep-dir "<中间文件/在制工作区/第01集>"
```

完成后汇报 `≤ 5 行` 落盘路径与 `colab_cpu_verified=true` / `Active assignments: 0` 状态，交棒窗 2。
