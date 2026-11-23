# 第一棒 · 窗 4（`P1-合稿门禁窗`）作业子卡

> **定岗与职责**：池 B `claude-sonnet-5-5-medium` 单席位微主管（目标 `12–18 步` · 硬熔断 `≤ 28 步`）。负责组装 `Wave 1..4` 清单、落盘第一棒全套文稿与三表、同步主稿 SHA-256 并运行对齐终检。

## 1. 组装带 `Wave 1..4` 依赖锁的 `待生图帧清单.json`

写入 `中间文件/在制工作区/第NN集/待生图帧清单.json`，严格分波标注依赖：
- **`Wave 1`**：人物 `_肖像.png`（`character_portrait`）+ 场景 `_母图.png`（`scene`）
- **`Wave 2`**：人物 `_主体.png`（`character_subject`，强依赖 `Wave 1` 过审且经 `prototype-sheet --stage wave1` 放行的 `_肖像.png`）
- **`Wave 3`**：分镜 `_首帧.png`（`frame` / `首帧`，强依赖 `Wave 1/2` 经 `prototype-sheet --stage wave2` 放行的母图）
- **`Wave 4`**：分镜 `_尾帧.png`（`frame` / `尾帧`，仅 `need_tail_frame: true`，强依赖 `Wave 3` 同段 `_首帧.png`）

## 2. 落盘第一棒核心交付文稿与三表（强制主稿 SHA-256 同步）

1. `分集/第NN集/第NN集_剧本故事梗概与逐镜导演脚本.md`
2. `分集/第NN集/第NN集_视频提示词.md` —— **落盘后必须立即原样复制一份至 `中间文件/提示词维护/视频提示词/第NN集/当前主稿.md`，确保两处文件 SHA-256 100% 一致！**
3. `分集/第NN集/第NN集_分镜头脚本.xlsx`（标准 12 列；结构详见 [`video-prompt-writer/references/storyboard-script.md`](../../video-prompt-writer/references/storyboard-script.md)）
4. `分集/第NN集/第NN集_台词生成表.xlsx` 及同名 `.csv`（标准 9 列，情绪列用 `情绪导演指令`，兼容旧表列名；即梦开口镜标 `SKIP_JIMENG_NATIVE`）
5. `分集/第NN集/第NN集_字幕时间轴.csv`（UTF-8 BOM）与 `分集/第NN集/第NN集_字幕.srt`（基于 `asr_transcript.json` 与新姓名映射，去除片头片尾后从 `0` 起计）

## 3. 运行第一棒终检硬门禁（非零退出立即原地停机上报）

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py verify-cuts --cuts-csv "<中间文件/原片分析/第NN集/cuts.csv>" --shots-json "<中间文件/原片分析/第NN集/物理切点清单.json>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py verify-script-alignment --project-root "<项目根目录>" --episode "第NN集" --require-visual-audit
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py phase-status --ep-dir "<中间文件/在制工作区/第NN集>"
```

全部 `PASS` 后向根主控回传 `≤ 15 行` 落盘统计与文件链接并销毁退出。
