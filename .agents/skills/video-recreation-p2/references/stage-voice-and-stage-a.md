# 第二棒 · 环节 1–3（`48000Hz` 声线母本 + `plan-batches` + Stage A 两阶 `prototype-sheet` 解锁）

> **核心前置铁律**：第二棒严格照第一棒已过 `verify-script-alignment --require-visual-audit` 盖戳的 `待生图帧清单.json` 机械执行，**严禁现场拼改中文词、扩词或跳 `Wave` 生图**！

## 环节 1：Colab `VoxCPM 2`（`openbmb/VoxCPM2`）原生 `48000Hz` 声线母本与批准清单签发

1. **默认引擎与硬件**：`build-voice-colab --stage voice-ref` 自动路由 **`voxcpm2` + `--gpu L4`**（可显式传 `--gpu A100|H200`；权重与 `HF_HOME` 持久化至 Drive `tts_studio/`，模型选型与环境规范见 [`colab-voice-dubbing`](../../colab-voice-dubbing/SKILL.md)）。Colab 作业清理与关机按 [`colab-ssh-rclone`](../../colab-ssh-rclone/SKILL.md) 执行。
2. **原生 `48000Hz` 死红线（彻底剔除 `24kHz` 兼容口）**：
   - Voice Design 以 `(中文声线描述)台词` 不传参考直接捏声，原生 `48000Hz / Mono / pcm_s16le`（若 `sample_rate != 48000` 立即 `VOXCPM2_NOT_NATIVE_48K` 阻断，**严禁外部重采样**）；
   - 按 `角色改名与音色映射.json` 为每名建卡角色独立合成 **`角色编号_角色新姓名_说话_声线参考.wav`** 与 **`_心声_声线参考.wav`**（6–9 字完整短句，`2.05–2.95s` 自然时长重试 + 首尾轻量裁剪淡入淡出，**禁 `atempo`**，SHA-256 互异），同步存入根级 `声线参考/` 与 `分集/第NN集/声线/`。
3. **`voice-audit` 硬门禁签发批准清单**：
   - 强检 `2.00s <= duration <= 3.00s`、`48000Hz Mono pcm_s16le`、`silence_ratio <= 0.35`、SHA-256 互异；
   - 通过后正式签发 **`声线母本批准清单.json`**，锁死 `(角色, 说话/心声, approved_sha256)` 三元组供第三棒强制匹配。

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py build-voice-colab --project-root "<项目根目录>" --episode "第NN集" --stage voice-ref
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py voice-audit --project-root "<项目根目录>" --episode "第NN集"
```

## 环节 2：`plan-batches` 分波物化（严禁野脚本 + 弹性并窗 `≤ 54` 单元）

1. **严禁手写 `setup_batches.py` 等野脚本**！所有 `生成批次/批次XX/jobs.json` 必须 100% 由 `plan-batches` 原文物化第一棒 `待生图帧清单.json`，自动读取 `分集索引.json` / `style_and_cast.json` 中的 **`aspect_ratio`（默认 `16:9`，竖屏 `9:16`）** 逐单元透传写入 `jobs.json`。
2. **严格按 `Wave 1 → 2 → 3 → 4` 不混批物化**：
   - `Wave 1`：人物 `_肖像.png` + 场景 `_母图.png`
   - `Wave 2`：人物 `_主体.png`（强依赖 `Wave 1` 过审且经 `prototype-sheet --stage wave1` 放行的 `_肖像.png`）
   - `Wave 3`：分镜 `_首帧.png`（强依赖 `Wave 1/2` 经 `prototype-sheet --stage wave2` 放行的母图）
   - `Wave 4`：分镜 `_尾帧.png`（仅 `need_tail_frame: true`，强依赖 `Wave 3` 同段 `_首帧.png`）
3. **弹性并窗上限 `≤ 54` 基础单元**：默认单窗基准 `50` 单元；全集总基础单元处于 `51–54` 时自动并入 `window_01`（预留 10% 补救缓冲，确保总调用 `≤ 60` 次），超出 `54` 单元一律物理切入 `window_02`。

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py status --work-dir "<中间文件/在制工作区/第NN集>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py plan-batches --work-dir "<中间文件/在制工作区/第NN集>"
```

## 环节 3：`Wave 1 → Wave 2` Stage A 母资产与两阶 `prototype-sheet` 防死锁解锁

> **🛑 `Wave 1/2` 生图与双检统一执行链（严禁因身处环节 1–3 而漏步）**：`Wave 1/2` 的调用生图、配额原子预占与幂等核销（`quota-reserve` / `quota-reconcile`）、`image-generator` 英文单次调用硬锁（`CRITICAL EXECUTION LIMIT...`）、`save-unit` / `contact`、独立持图双检（含 [`image-generation-quality.md`](../../../rules/image-generation-quality.md) 分类补救铁律）与 `finalize` **100% 复用 [`stage-batch-gen-and-qc.md`](./stage-batch-gen-and-qc.md) 环节 4–5**；人设/白底细则参考 [`character-scene-style`](../../character-scene-style/SKILL.md)。

1. **去重与场景双铁律**：
   - 单镜路人/群演不建角色卡；仅真实原型/历史名人或跨 `≥ 2` 镜复现角色建 `_肖像.png` + `_主体.png`。
   - 场景同空间同时态共享 1 张母图，和平完好态 vs 战损废墟态物理拆分（防 `SCENE_TEMPORAL_STATE_CONFLICT`），强制 `STRICTLY 0 PERSON(S) IN FRAME, NO PEOPLE` + 净空三禁（旗杆留空、禁现代交通标志、禁英文乱码与小字牌匾）。
2. **`Wave 1` 肖像锁脸、画幅朝向与纯白底门禁**：
   - **历史人物/真人原型**：按史料与公认历史原型考据，由 `prep-prototype-refs` 胸像裁切作 `_肖像.png` 的 `image_refs[0]`，**严禁过分美颜/网红脸/偶像滤镜化**；
   - **原创/虚构新角色（仅限生新角色）**：用户按第一棒 MJ 英文词抽卡定颜回传的定妆照挂为 `_肖像.png` 的 `image_refs[0]`，**1:1 保持原颜转制**（常规/幻想类走美型、幻想型审美且全库骨相各异）；
   - `_肖像.png` 与 `_主体.png` 均须免冠露额锁骨相发际线、**默认 `16:9` 横屏（竖屏项目随 `jobs.json` 切 `9:16`）**、**正面正视镜头（`front-facing, looking directly at the camera`）**、100% `#FFFFFF` 纯白底像素门禁（`FAIL_NON_WHITE_BG`）。
3. **两阶对照表解锁（根治新角色无原型照/无主体图死锁）**：
   - **第一阶（`Wave 1` → 解锁 `Wave 2`）**：`Wave 1` 生图落盘后运行 **`prototype-sheet --stage wave1`**（支持 `finalize` 前自动回退扫描工作区待审图），生成 **2 列肖像核验表**（`[原型照 或 自动绘制深色角色设定规格卡 | 生成_肖像.png]`，无真人/历史原型照的新原创角色不阻塞）；经 `Hydrogen High`（疑难 `Sonnet 5.5 High`）过审并 `finalize` 后，方放行 `Wave 2` 生成 `_主体.png`（强制以过审 `_肖像.png` 为 `Image 1`）。
   - **第二阶（`Wave 2` → 解锁 `Wave 3/4`）**：`Wave 2` 完成后运行 **`prototype-sheet --stage wave2`**（同样支持 `finalize` 前自动回退扫描），生成 **3 列一致性总表**（`[原型照/深色设定规格卡 | _肖像.png | _主体.png]`）；过审并 `finalize` 后，运行 **`batch_ops.py build-char-sheets`** 自动将多人同框角色母图裁去白边并等高横拼为 `角色合集缓存/合集_<角色1>_<角色2>_<肖像|主体>.png`（`Wave 1/2` `finalize` 也会自动预拼已齐套组合；默认 `#FFFFFF` 纯白无底防视觉模型把透明通道压成黑边，按需可加 `--bg transparent` 输出真 RGBA 透明无底），随后解锁下游 `Wave 3 / Wave 4`！

```powershell
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py prototype-sheet --stage wave1 --refs-dir "<公共资产库/原型参考照片/ref_prepared>" --chars-dir "<人物参考>" --output "<中间文件/在制工作区/第NN集/人物肖像二列核验表_wave1.jpg>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py prototype-sheet --stage wave2 --refs-dir "<公共资产库/原型参考照片/ref_prepared>" --chars-dir "<人物参考>" --output "<中间文件/在制工作区/第NN集/人物原型三列对照表_wave2.jpg>"
python <WORKSPACE_ROOT>/.agents/skills/video-recreation/scripts/batch_ops.py build-char-sheets --work-dir "<中间文件/在制工作区/第NN集>"
```
