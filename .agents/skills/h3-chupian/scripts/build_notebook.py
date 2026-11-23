# -*- coding: utf-8 -*-
"""生成自包含的 Colab Pro+ H200 网页端一键 Notebook: ../resources/MiniMax_H3_Colab_H200.ipynb。"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES_DIR = HERE.parent / "resources"
RES_DIR.mkdir(parents=True, exist_ok=True)

MAKE_JOBS_SRC = (HERE / "make_h3_jobs.py").read_text(encoding="utf-8")
WORKER_SRC = (HERE / "colab_h3_worker.py").read_text(encoding="utf-8")


def md_cell(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(keepends=True)}


def code_cell(code: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": code.splitlines(keepends=True),
    }


nb = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {
        "colab": {"provenance": [], "gpuType": "H200"},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
        "accelerator": "GPU",
    },
    "cells": [
        md_cell(
            "# 🎬 h3出片工作台（MiniMax-H3 `ref2va` · Colab Pro+ H200 141GB 无损 BF16）\n\n"
            "已预填当前工程 **`xiaoxing_wenhao_ep01` / `ep01` / `ONLY_PARTS = 'S01'`**，切到 **H200 (141GB)** 后点击菜单 **「代码执行程序 → 全部运行」** 即可一键出片：\n"
            "- **默认无损配置**：`h200-max`（纯 BF16 `pruned` DiT 40.2GB + BF16 Qwen3-VL-32B 51.5GB + FP16 Video VAE + FP32 Audio VAE + `ref_image_size=max`），零画质折损。\n"
            "- **秒拉云盘缓存**：自动从 `/content/drive/MyDrive/models/minimax_h3` 流式拉取已预热的 97.5 GB 权重（全程实时打印进度），免公网下载。\n"
            "- **续跑 S02–S12 极简切法**：S01 验片满意后，只需把 `ONLY_PARTS` 改为空字符串 `''`（保持 `SKIP_EXISTING = True`），再点「全部运行」即可自动跳过 S01 连跑 S02–S12，且每出完 1 段立即写回 Google Drive 防丢。\n"
            "- **防烧 CU 铁律**：`RUN_MODE = 'batch'` 跑完并刷盘回写 Google Drive 后，自动等待 10 秒落盘再执行 `runtime.unassign()` 销毁 GPU 停止计费。\n"
        ),
        code_cell(
            "#@title 1. 硬件检查 + 挂载 Google Drive + 任务配置\n"
            "import os, subprocess\n"
            "from pathlib import Path\n"
            "from google.colab import drive\n\n"
            "print('=== GPU 与内存/磁盘检查 ===')\n"
            "for cmd in (\n"
            "    ['nvidia-smi', '--query-gpu=name,memory.total,memory.free', '--format=csv,noheader'],\n"
            "    ['free', '-h'],\n"
            "    ['df', '-h', '/content'],\n"
            "):\n"
            "    r = subprocess.run(cmd, capture_output=True, text=True)\n"
            "    print(r.stdout.strip())\n"
            "drive.mount('/content/drive')\n\n"
            "# ===== 任务配置（已预填 EP01 首段 S01）=====\n"
            "SERIES_DRIVE_DIR = '/content/drive/MyDrive/ai_anime/xiaoxing_wenhao_ep01'  #@param {type:'string'}\n"
            "EPISODE = 'ep01'  #@param {type:'string'}\n"
            "ONLY_PARTS = 'S01'  #@param {type:'string'}  # 只跑 S01；留空 '' 则跑全部段（配合 SKIP_EXISTING 自动跳过已出段）\n"
            "SKIP_EXISTING = True  #@param {type:'boolean'}  # 已存在的成片自动跳过（断点续跑）\n"
            "USE_EXISTING_MANIFEST = True  #@param {type:'boolean'}  # 沿用本地编译好的 12 段 H3 manifest.json\n"
            "PROFILE = 'h200-max'  #@param ['h200-max', 'bf16-raw', 'h200-fast', 'g4-balanced']\n"
            "RESOLUTION = '768p'  #@param ['768p', '1080p', '480p']\n"
            "REF_IMAGE_SIZE = 'max'  #@param ['max', 'match']\n"
            "ENABLE_TURBO_4STEP = False  #@param {type:'boolean'}\n"
            "RUN_MODE = 'batch'  #@param ['batch', 'webui']\n"
            "MODEL_CACHE_DRIVE_DIR = '/content/drive/MyDrive/models/minimax_h3'  #@param {type:'string'}\n"
            "HF_TOKEN = ''  #@param {type:'string'}  # 可选（公开仓库且已命中 G 盘缓存，留空即可）\n"
            "AUTO_UNASSIGN_AFTER_BATCH = True  #@param {type:'boolean'}\n\n"
            "assert Path(SERIES_DRIVE_DIR).exists(), f'云盘系列目录不存在: {SERIES_DRIVE_DIR}'\n"
            "if MODEL_CACHE_DRIVE_DIR.strip():\n"
            "    assert Path(MODEL_CACHE_DRIVE_DIR).exists(), f'云盘权重缓存目录不存在: {MODEL_CACHE_DRIVE_DIR}'\n"
            "if HF_TOKEN.strip():\n"
            "    os.environ['HF_TOKEN'] = HF_TOKEN.strip()\n"
            "print(f'✅ 已就绪: {SERIES_DRIVE_DIR} / {EPISODE} | ONLY_PARTS={ONLY_PARTS or \"ALL\"} | SKIP_EXISTING={SKIP_EXISTING} | profile={PROFILE} | res={RESOLUTION}')\n"
        ),
        code_cell(
            "#@title 2. 写入内置脚本并拷贝当前系列到本地高速 SSD\n"
            "from pathlib import Path\n"
            "import shutil\n\n"
            f"Path('/content/make_h3_jobs.py').write_text({MAKE_JOBS_SRC!r}, encoding='utf-8')\n"
            f"Path('/content/colab_h3_worker.py').write_text({WORKER_SRC!r}, encoding='utf-8')\n\n"
            "LOCAL_SERIES = Path('/content/series_work')\n"
            "if LOCAL_SERIES.exists():\n"
            "    shutil.rmtree(LOCAL_SERIES)\n"
            "shutil.copytree(SERIES_DRIVE_DIR, LOCAL_SERIES)\n"
            "print('✅ 已同步系列目录至本地高速盘:', LOCAL_SERIES)\n"
        ),
        code_cell(
            "#@title 3. 检查本次待跑段落的 H3 任务清单与提示词\n"
            "import subprocess, sys, json\n"
            "from pathlib import Path\n\n"
            "manifest_path = Path('/content/series_work') / EPISODE / 'h3_jobs' / 'manifest.json'\n"
            "if not (USE_EXISTING_MANIFEST and manifest_path.exists()):\n"
            "    cmd = [\n"
            "        sys.executable, '/content/make_h3_jobs.py',\n"
            "        '/content/series_work', EPISODE,\n"
            "        '--profile', PROFILE,\n"
            "        '--resolution', RESOLUTION,\n"
            "        '--ref-size', REF_IMAGE_SIZE,\n"
            "        '--use-custom-prompt',\n"
            "    ]\n"
            "    if ENABLE_TURBO_4STEP:\n"
            "        cmd.append('--turbo')\n"
            "    subprocess.run(cmd, check=True)\n"
            "else:\n"
            "    print('✅ 直接沿用本地编译好的 manifest.json:', manifest_path)\n\n"
            "manifest = json.loads(manifest_path.read_text(encoding='utf-8'))\n"
            "only_set = {p.strip() for p in ONLY_PARTS.split(',') if p.strip()}\n"
            "selected = [j for j in manifest['jobs'] if (not only_set or j['part'] in only_set)]\n"
            "assert selected, f'按 ONLY_PARTS={ONLY_PARTS!r} 未匹配到段落！可选: {[j[\"part\"] for j in manifest[\"jobs\"]]}'\n"
            "print(f'📋 本次将执行 {len(selected)}/{len(manifest[\"jobs\"])} 段: {[j[\"part\"] for j in selected]}')\n"
            "for j in selected:\n"
            "    tags = ' / '.join(f\"{r['tag']}:{r['name']}\" for r in j['ref_images'])\n"
            "    print(f\"  - 段 {j['part']}: {j['width']}x{j['height']} @ {j['fps']}fps, {j['frame_length']}帧 (~{j['actual_duration_sec']}s) | {tags}\")\n"
            "    if RUN_MODE == 'batch' and j.get('missing_files'):\n"
            "        raise FileNotFoundError(f'缺少参考素材，请补齐后重试: {j[\"missing_files\"]}')\n"
            "if len(selected) <= 2:\n"
            "    for j in selected:\n"
            "        p_txt = (manifest_path.parent / j['prompt_file']).read_text(encoding='utf-8')\n"
            "        print(f'\\n--- 段 {j[\"part\"]} H3 提示词全文 ---\\n{p_txt}')\n"
        ),
        code_cell(
            "#@title 4. 启动 MiniMax-H3 无损生成（实时日志流 + 每段即刻落盘 G 盘 + 自动关机）\n"
            "import os, subprocess, sys, shutil, time\n"
            "from pathlib import Path\n"
            "from google.colab import runtime\n\n"
            "drive_clips_dir = Path(SERIES_DRIVE_DIR) / EPISODE / 'clips'\n"
            "drive_clips_dir.mkdir(parents=True, exist_ok=True)\n\n"
            "worker_cmd = [\n"
            "    sys.executable, '-u', '/content/colab_h3_worker.py',\n"
            "    '--series-dir', '/content/series_work',\n"
            "    '--episode', EPISODE,\n"
            "    '--mode', RUN_MODE,\n"
            "    '--local-clips-dest', str(drive_clips_dir),\n"
            "]\n"
            "if MODEL_CACHE_DRIVE_DIR.strip():\n"
            "    worker_cmd += ['--model-cache-dir', MODEL_CACHE_DRIVE_DIR.strip()]\n"
            "if ONLY_PARTS.strip():\n"
            "    worker_cmd += ['--only-parts', ONLY_PARTS.strip()]\n"
            "if SKIP_EXISTING:\n"
            "    worker_cmd.append('--skip-existing')\n\n"
            "env = dict(os.environ)\n"
            "env['PYTHONUNBUFFERED'] = '1'\n"
            "print('🚀 启动命令:', ' '.join(worker_cmd), flush=True)\n\n"
            "ok = False\n"
            "try:\n"
            "    proc = subprocess.Popen(\n"
            "        worker_cmd,\n"
            "        stdout=subprocess.PIPE,\n"
            "        stderr=subprocess.STDOUT,\n"
            "        text=True,\n"
            "        bufsize=1,\n"
            "        env=env,\n"
            "    )\n"
            "    assert proc.stdout is not None\n"
            "    for line in proc.stdout:\n"
            "        print(line, end='', flush=True)\n"
            "    rc = proc.wait()\n"
            "    if rc != 0:\n"
            "        raise RuntimeError(f'colab_h3_worker.py 失败 (rc={rc})')\n"
            "    ok = True\n"
            "    if RUN_MODE == 'batch':\n"
            "        src_clips = Path('/content/series_work') / EPISODE / 'clips'\n"
            "        for mp4 in sorted(src_clips.glob('*.mp4')):\n"
            "            dst_mp4 = drive_clips_dir / mp4.name\n"
            "            if not dst_mp4.exists() or dst_mp4.stat().st_size != mp4.stat().st_size:\n"
            "                shutil.copy2(mp4, dst_mp4)\n"
            "            print(f'💾 已核验写入 Google Drive: {dst_mp4} ({dst_mp4.stat().st_size / 1e6:.2f} MB)', flush=True)\n"
            "        os.sync()\n"
            "        print('⏳ 等待 10 秒确保 Google Drive 挂载缓存彻底刷盘...', flush=True)\n"
            "        time.sleep(10)\n"
            "        print('🎉 本批成片已安全落盘 Google Drive！', flush=True)\n"
            "finally:\n"
            "    if RUN_MODE == 'batch' and AUTO_UNASSIGN_AFTER_BATCH:\n"
            "        if not ok:\n"
            "            print('⚠️ 任务报错，为防持续扣费仍将在 5 秒后自动释放 GPU（请先截图或查看上方报错日志）...', flush=True)\n"
            "            time.sleep(5)\n"
            "        print('🛑 正在自动释放 GPU 运行时以停止计费...', flush=True)\n"
            "        runtime.unassign()\n"
        ),
        code_cell(
            "#@title 5. 手动立刻释放 GPU（仅调试或 WebUI 模式需要时点此停止计费）\n"
            "from google.colab import runtime\n"
            "runtime.unassign()\n"
        ),
    ],
}

out = RES_DIR / "MiniMax_H3_Colab_H200.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"✅ 已生成自包含 H200 Notebook: {out} ({out.stat().st_size} bytes)")
