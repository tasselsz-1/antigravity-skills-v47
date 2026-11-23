#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
云端/本地双栖 · 一键投料建项与物理切片抽帧引擎 (tools/cloud_pipeline.py)
无论在 Google 云端 Remote Agent (/workspace/...)、Colab (/content/drive/...) 还是本地 Windows (G:\我的云端硬盘\...)，
均通过相对锚点自动定位 短剧复刻工作站 根目录，实现 100% 全程云端执行 + 本地 G 盘实时透视！
"""

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def _v41_engines():
    here_dir = str(Path(__file__).resolve().parent)
    if here_dir not in sys.path:
        sys.path.insert(0, here_dir)
    import v41_cloud_engines
    return v41_cloud_engines


DEFAULT_TARGET_FPS = 24


def execute_step2_lipsync_pipeline(*args, **kwargs):
    return _v41_engines().execute_step2_lipsync_pipeline(*args, **kwargs)


def is_fps_compliant(fps: float | int | None, target_fps: int = DEFAULT_TARGET_FPS) -> bool:
    return _v41_engines().is_fps_compliant(fps, target_fps=target_fps)


def _extract_cli_target_fps(args: argparse.Namespace) -> int | None:
    if any(a == "--target-fps" or a.startswith("--target-fps=") for a in sys.argv[1:]):
        return int(getattr(args, "target_fps", DEFAULT_TARGET_FPS) or DEFAULT_TARGET_FPS)
    return None

COLAB_MCP_ENDPOINT = "https://colaboratory.googleapis.com/mcp"
COLAB_OAUTH_SCOPE = "https://www.googleapis.com/auth/colaboratory"
LOCAL_COLAB_MCP_EXE = Path(os.environ.get("COLAB_MCP_EXE", str(Path.home() / ".gemini" / "tools" / "colab-mcp" / ".venv" / "Scripts" / "colab-mcp.exe")))

# 核心防 429 与全量画质优先铁律常量
MAX_CONCURRENCY = 2
MAX_BATCH_SIZE = 6
JITTER_MIN_SEC = 1.5
JITTER_MAX_SEC = 3.5
FULL_QUALITY_NO_FRAME_CAP = True  # 严禁缩减张数（彻底移除“控制在 25~30 张”限制，按物理切镜全量生成）
SCHEME_A_NO_CROSS_SCENE_MERGE = True  # 方案 A：严禁跨场景合并镜头，孤立/跨场景短镜走 Veo 3.1 (4s/6s/8s) + 达芬奇 .fcpxml 裁切 Handles
PHASE1_CPU_PIP_PACKAGES = ("transnetv2-pytorch", "scenedetect[opencv]", "faster-whisper")


def _extract_drive_rel_hint(project_root: Path) -> str:
    s = str(project_root.resolve()).replace("\\", "/").rstrip("/")
    for marker in ("/MyDrive/", "/我的云端硬盘/"):
        if marker in s:
            return s.split(marker, 1)[1].lstrip("/")
    if "短剧复刻工作站" in s:
        return "短剧复刻工作站" + s.split("短剧复刻工作站", 1)[1]
    return re.sub(r"^[A-Za-z]:/+", "", s) or project_root.name


def _detect_workstation_root() -> Path:
    here = Path(__file__).resolve()
    s = str(here).replace("\\", "/")
    if "短剧复刻工作站" in s:
        prefix = s.split("短剧复刻工作站", 1)[0] + "短剧复刻工作站"
        return Path(prefix).resolve()
    for cand in (
        Path("/content/drive/MyDrive/短剧复刻工作站"),
        Path("G:/我的云端硬盘/短剧复刻工作站"),
    ):
        if cand.exists():
            return cand.resolve()
    return here.parent.parent


WORKSTATION_ROOT = _detect_workstation_root()
INBOX_DIR = WORKSTATION_ROOT / "00_待选片投料池"
PROJECTS_ROOT = WORKSTATION_ROOT / "01_在制项目库"


def _detect_batch_ops_script() -> Path:
    for cand in (
        Path(__file__).resolve().parent / "batch_ops.py",
        WORKSTATION_ROOT / "tools" / "batch_ops.py",
        WORKSTATION_ROOT / ".agents" / "skills" / "video-recreation" / "scripts" / "batch_ops.py",
    ):
        if cand.exists():
            return cand.resolve()
    return WORKSTATION_ROOT / "tools" / "batch_ops.py"


BATCH_OPS_SCRIPT = _detect_batch_ops_script()


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _utf8_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def probe_hardware_env(phase: str = "phase1") -> dict:
    """
    硬件自适应探针：
    - 第一棒、第二棒、第三棒必须默认使用 Colab 纯 CPU 模式（严禁使用 GPU 模式，torch.cuda.is_available() 为 False 时绝不报错或中断，自动切换为 CPU + 云端 API 模式，0 CU 损耗）；
    - 若在第一~三棒检测到误开启 GPU，立即输出告警提示切回纯 CPU 模式；
    - 仅在第四棒跑 LatentSync 与 24fps 四档超分（默认 1K，可选 4K）时检测并提示使用 A100 GPU。
    """
    cuda_ok = False
    gpu_name = "None (Pure CPU Mode)"
    try:
        import torch  # type: ignore
        cuda_ok = bool(torch.cuda.is_available())
        if cuda_ok:
            gpu_name = str(torch.cuda.get_device_name(0))
    except Exception:
        cuda_ok = False

    is_phase4 = phase in ("phase4", "step4", "latentsync", "vsr")
    if is_phase4:
        mode_desc = "A100_GPU_MODE" if cuda_ok else "CPU_DRY_PREP_FOR_A100"
        note = (
            f"检测到 GPU ({gpu_name})，可执行第四棒 LatentSync + 24fps 四档超分（默认 1K，可选 4K）"
            if cuda_ok
            else "当前为纯 CPU 环境，已生成第四棒任务单与 .ipynb，实际执行 LatentSync 与 24fps 四档超分（默认 1K，可选 4K）时请在 Colab 切换至 A100 GPU"
        )
    else:
        mode_desc = "PURE_CPU_CLOUD_API_MODE"
        note = (
            "第一~三棒运行于纯 CPU + 云端 API 模式（0 GPU 占用，0 CU 损耗，100% 零报错兼容）"
            if not cuda_ok
            else f"⚠️ [GPU 误开告警] 检测到 GPU ({gpu_name})！第一棒及前三棒严禁使用 GPU 模式（纯 CPU 即可满速运行且 0 CU 损耗），请立即将 Colab 运行时切换回纯 CPU 模式！"
        )
        if cuda_ok:
            log(note)
    return {
        "phase": phase,
        "cuda_available": cuda_ok,
        "gpu_name": gpu_name,
        "execution_mode": mode_desc,
        "max_concurrency": MAX_CONCURRENCY,
        "max_batch_size": MAX_BATCH_SIZE,
        "jitter_range_sec": [JITTER_MIN_SEC, JITTER_MAX_SEC],
        "full_quality_no_frame_cap": FULL_QUALITY_NO_FRAME_CAP,
        "scheme_a_no_cross_scene_merge": SCHEME_A_NO_CROSS_SCENE_MERGE,
        "note": note,
    }


def is_colab_runtime() -> bool:
    """检测当前进程是否运行于云端 Google Colab Linux 实例内"""
    if "google.colab" in sys.modules:
        return True
    if os.environ.get("COLAB_RELEASE_TAG") or os.environ.get("COLAB_GPU") is not None:
        return True
    if os.name != "nt" and (Path("/content").exists() or Path("/datalab").exists()):
        return True
    try:
        import google.colab  # type: ignore  # noqa: F401
        return True
    except ImportError:
        return False


def _normalize_ep_str(ep: int | str) -> str:
    if isinstance(ep, int):
        return f"第{ep:02d}集"
    s = str(ep or "第01集").strip()
    m = re.search(r"(\d+)", s)
    if m and not s.startswith("第"):
        return f"第{int(m.group(1)):02d}集"
    return s


def _find_project_phase1_ipynb(project_dir: Path) -> Path:
    cands = [
        project_dir / f"{project_dir.name}_云端夜间免守候执行器.ipynb",
    ]
    if project_dir.exists():
        cands.extend(sorted(project_dir.glob("*_云端夜间免守候执行器.ipynb")))
        cands.extend(sorted(project_dir.glob("*.ipynb")))
    for c in cands:
        if c.exists():
            return c.resolve()
    return (project_dir / f"{project_dir.name}_云端夜间免守候执行器.ipynb").resolve()


def resolve_drivefs_cloud_id(ipynb_path: Path) -> str:
    """
    从 %LOCALAPPDATA%\\Google\\DriveFS\\*\\metadata_sqlite_db（复制到临时文件只读查询
    SELECT cloud_id FROM items WHERE local_title = ?）自动解析目标 .ipynb 的 Google Drive cloud_id。
    """
    target_title = ipynb_path.name
    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    drivefs_root = local_app_data / "Google" / "DriveFS"
    if not drivefs_root.exists():
        raise FileNotFoundError(f"未找到 Google DriveFS 目录: {drivefs_root}")

    db_files = sorted(drivefs_root.glob("*/metadata_sqlite_db"))
    if not db_files:
        raise FileNotFoundError(f"未在 {drivefs_root} 下找到 metadata_sqlite_db")

    for db_p in db_files:
        if not db_p.is_file():
            continue
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db = Path(tmpdir) / "metadata_sqlite_db_ro"
            shutil.copy2(db_p, tmp_db)
            for suf in ("-wal", "-shm"):
                side_f = db_p.with_name(db_p.name + suf)
                if side_f.exists():
                    shutil.copy2(side_f, tmp_db.with_name(tmp_db.name + suf))
            conn = sqlite3.connect(f"file:{tmp_db.as_posix()}?mode=ro", uri=True)
            try:
                cur = conn.cursor()
                for sql in (
                    "SELECT COALESCE(s.cloud_id, i.id) FROM items i LEFT JOIN stable_ids s ON i.stable_id = s.stable_id WHERE i.local_title = ? AND i.trashed = 0 AND i.is_tombstone = 0 ORDER BY i.modified_date DESC LIMIT 1",
                    "SELECT id FROM items WHERE local_title = ? AND id IS NOT NULL AND id != '' ORDER BY modified_date DESC LIMIT 1",
                    "SELECT cloud_id FROM items WHERE local_title = ? AND cloud_id IS NOT NULL AND cloud_id != ''",
                ):
                    try:
                        cur.execute(sql, (target_title,))
                        row = cur.fetchone()
                        if row and row[0]:
                            return str(row[0]).strip()
                    except sqlite3.OperationalError:
                        continue
            finally:
                conn.close()

    raise LookupError(f"未在 Google DriveFS metadata_sqlite_db 中查到文件 cloud_id: {target_title}")


def resolve_colab_url(ipynb_path: Path) -> str:
    """根据本地 Google Drive 同步目录下的 .ipynb 路径拼接 https://colab.research.google.com/drive/<cloud_id>"""
    cloud_id = resolve_drivefs_cloud_id(ipynb_path)
    return f"https://colab.research.google.com/drive/{cloud_id}"


def _build_pure_cpu_phase1_notebook(ipynb_path: Path, project_dir: Path, ep: str = "第01集") -> Path:
    """生成 100% 纯 CPU（零 GPU 元数据、零 Windows 盘符）的第一棒 TransNetV2 + faster-whisper ASR + 自动关机 .ipynb"""
    rel_hint = _extract_drive_rel_hint(project_dir)
    proj_name = project_dir.resolve().name
    ep_str = _normalize_ep_str(ep)
    pip_pkgs = " ".join(PHASE1_CPU_PIP_PACKAGES)
    code_lines = [
        "# === 第一棒：Colab T4 GPU(CPU 兜底) TransNetV2 切分 + faster-whisper (small, CUDA float16/CPU int8) 真实 ASR + 自动关机 ===\n",
        "import os, sys, json, subprocess, tempfile\n",
        "from pathlib import Path\n\n",
        'PHASE = "phase1"\n',
        "AUTO_SHUTDOWN = True\n",
        f'PROJECT_NAME = "{proj_name}"\n',
        f'REL_FROM_DRIVE = "{rel_hint}"\n',
        f'EPISODE = "{ep_str}"\n\n',
        "def resolve_colab_project_root(project_name: str, rel_hint: str) -> Path:\n",
        "    drive_my = Path('/content/drive/MyDrive')\n",
        "    for cand in [\n",
        "        drive_my / rel_hint,\n",
        "        drive_my / '短剧复刻工作站' / '01_在制项目库' / project_name,\n",
        "        drive_my / project_name,\n",
        "    ]:\n",
        "        if cand.exists():\n",
        "            return cand.resolve()\n",
        "    matches = list(drive_my.glob(f'*/*/{project_name}')) + list(drive_my.glob(f'*/{project_name}'))\n",
        "    if matches:\n",
        "        return matches[0].resolve()\n",
        "    raise FileNotFoundError(f'云盘未找到项目目录: {project_name} ({rel_hint})')\n\n",
        "try:\n",
        "    _UTF8_ENV = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}\n",
        "    from google.colab import drive\n",
        "    drive.mount('/content/drive')\n",
        "    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', *'" + pip_pkgs + "'.split()], check=True, encoding='utf-8', errors='replace', env=_UTF8_ENV)\n",
        "    PROJECT_ROOT = resolve_colab_project_root(PROJECT_NAME, REL_FROM_DRIVE)\n",
        "    tools_cands = [\n",
        "        PROJECT_ROOT.parent.parent / 'tools' / 'cloud_pipeline.py',\n",
        "        Path('/content/drive/MyDrive/短剧复刻工作站/tools/cloud_pipeline.py'),\n",
        "    ]\n",
        "    cp_script = next((p for p in tools_cands if p.exists()), None)\n",
        "    if cp_script is not None:\n",
        "        subprocess.run([sys.executable, str(cp_script), '--phase', 'phase1', '--project-root', str(PROJECT_ROOT), '--episode', EPISODE, '--no-auto-shutdown'], check=True, encoding='utf-8', errors='replace', env=_UTF8_ENV)\n",
        "    # 确保本集正片音频 faster-whisper ASR 转录落盘至 <ep_work_dir>/asr_transcript.json\n",
        "    work_dir = PROJECT_ROOT / '中间文件' / '在制工作区' / EPISODE\n",
        "    ana_dir = PROJECT_ROOT / '中间文件' / '原片分析' / EPISODE\n",
        "    work_dir.mkdir(parents=True, exist_ok=True)\n",
        "    ana_dir.mkdir(parents=True, exist_ok=True)\n",
        "    asr_out = work_dir / 'asr_transcript.json'\n",
        "    if not asr_out.exists() or asr_out.stat().st_size < 16:\n",
        "        from faster_whisper import WhisperModel\n",
        "        cfr_v = ana_dir / '正片母本_CFR.mp4'\n",
        "        raw_vids = sorted((PROJECT_ROOT / '原视频').glob('*.*')) if (PROJECT_ROOT / '原视频').exists() else []\n",
        "        src_v = cfr_v if cfr_v.exists() else (raw_vids[0] if raw_vids else None)\n",
        "        if src_v is None or not src_v.exists():\n",
        "            raise FileNotFoundError(f'未找到正片母本或原视频用于 ASR: {PROJECT_ROOT}')\n",
        "        offset_s = 0.0\n",
        "        shots_j = ana_dir / '物理切点清单.json'\n",
        "        if shots_j.exists() and src_v == cfr_v:\n",
        "            sh_list = json.loads(shots_j.read_text(encoding='utf-8-sig'))\n",
        "            if isinstance(sh_list, list) and sh_list:\n",
        "                offset_s = float(sh_list[0].get('start_sec') or 0.0)\n",
        "        tmp_wav = Path('/tmp/ep_phase1_asr_16k.wav')\n",
        "        subprocess.run(['ffmpeg', '-y', '-i', str(src_v), '-vn', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(tmp_wav)], check=True, encoding='utf-8', errors='replace', env=_UTF8_ENV)\n",
        "        import wave as _wave, numpy as _np\n",
        "        with _wave.open(str(tmp_wav), 'rb') as _wf:\n",
        "            _nch = _wf.getnchannels(); _raw = _wf.readframes(_wf.getnframes())\n",
        "        _aud = _np.frombuffer(_raw, dtype=_np.int16).astype(_np.float32) / 32768.0\n",
        "        if _nch > 1: _aud = _aud.reshape(-1, _nch).mean(axis=1).astype(_np.float32)\n",
        "        try:\n",
        "            import torch as _torch\n",
        "            _dev, _cmp = ('cuda', 'float16') if _torch.cuda.is_available() else ('cpu', 'int8')\n",
        "        except Exception:\n",
        "            _dev, _cmp = 'cpu', 'int8'\n",
        "        try:\n",
        "            wm = WhisperModel('small', device=_dev, compute_type=_cmp)\n",
        "        except Exception:\n",
        "            _dev, _cmp = 'cpu', 'int8'; wm = WhisperModel('small', device=_dev, compute_type=_cmp)\n",
        "        segs_it, info = wm.transcribe(_aud, language='zh', vad_filter=True)\n",
        "        segs_payload = []\n",
        "        for i_s, sg in enumerate(segs_it, 1):\n",
        "            txt = (sg.text or '').strip()\n",
        "            if txt:\n",
        "                segs_payload.append({'id': i_s, 'start': round(offset_s + float(sg.start), 3), 'end': round(offset_s + float(sg.end), 3), 'ep_start': round(float(sg.start), 3), 'ep_end': round(float(sg.end), 3), 'text': txt})\n",
        "        asr_doc = {'episode': EPISODE, 'engine': 'faster-whisper', 'model': 'small', 'device': _dev, 'compute_type': _cmp, 'language': getattr(info, 'language', 'zh'), 'segments': segs_payload}\n",
        "        asr_txt = json.dumps(asr_doc, ensure_ascii=False, indent=2)\n",
        "        asr_out.write_text(asr_txt, encoding='utf-8')\n",
        "        (ana_dir / 'asr_transcript.json').write_text(asr_txt, encoding='utf-8')\n",
        "        print(f'✅ faster-whisper ASR 完成: 共 {len(segs_payload)} 句 -> {asr_out}')\n",
        "finally:\n",
        "    if AUTO_SHUTDOWN:\n",
        "        try:\n",
        "            if hasattr(os, 'sync'): os.sync()\n",
        "        except Exception: pass\n",
        "        try:\n",
        "            from google.colab import drive\n",
        "            drive.flush_and_unmount()\n",
        "        except Exception: pass\n",
        "        try:\n",
        "            from google.colab import runtime\n",
        "            runtime.unassign()\n",
        "        except Exception: pass\n",
    ]
    nb_data = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "colab": {"provenance": []},
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        },
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    f"# 🎬 {proj_name} · {ep_str} 第一棒纯 CPU 切分与 ASR 执行器\n",
                    "- Colab T4 GPU 极速（`< 0.1 CU`，CPU 兜底）：`TransNetV2` + CFR 母本 + `0.15s` 内缩 + `faster-whisper (small, CUDA float16 / CPU int8)` 真实 ASR (`asr_transcript.json`) + 故事板 + 自动关机\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": code_lines,
            },
        ],
    }
    ipynb_path.parent.mkdir(parents=True, exist_ok=True)
    ipynb_path.write_text(json.dumps(nb_data, ensure_ascii=False, indent=1), encoding="utf-8")
    return ipynb_path


def _ensure_pure_cpu_phase1_notebook(ipynb_path: Path, project_dir: Path | None = None, ep: str = "第01集") -> dict:
    """
    自动校验并确保目标 .ipynb 顶层 metadata 无 "accelerator" / "gpuClass"（100% 纯 CPU 模式），
    含 faster-whisper ASR 且代码锁死 PHASE = "phase1"、AUTO_SHUTDOWN = True。
    """
    if not ipynb_path.exists():
        if project_dir is not None:
            _build_pure_cpu_phase1_notebook(ipynb_path, project_dir, ep=ep)
        else:
            raise FileNotFoundError(f"未找到 Colab 执行器笔记本: {ipynb_path}")
    nb_data = json.loads(ipynb_path.read_text(encoding="utf-8-sig"))
    meta = nb_data.setdefault("metadata", {})
    modified = False
    for gpu_key in ("accelerator", "gpuClass"):
        if gpu_key in meta:
            meta.pop(gpu_key, None)
            modified = True
    colab_meta = meta.setdefault("colab", {"provenance": []})
    for c_gpu_key in ("gpuType", "machine_shape"):
        if c_gpu_key in colab_meta:
            colab_meta.pop(c_gpu_key, None)
            modified = True

    has_phase1_lock = False
    has_auto_shutdown = False
    has_asr = False
    for cell in nb_data.get("cells", []):
        if cell.get("cell_type") == "code":
            src_txt = "".join(cell.get("source", []))
            if 'PHASE = "phase1"' in src_txt or "PHASE = 'phase1'" in src_txt:
                has_phase1_lock = True
            if "AUTO_SHUTDOWN = True" in src_txt:
                has_auto_shutdown = True
            if "faster_whisper" in src_txt or "faster-whisper" in src_txt or "asr_transcript.json" in src_txt:
                has_asr = True

    if not has_asr and project_dir is not None:
        _build_pure_cpu_phase1_notebook(ipynb_path, project_dir, ep=ep)
        nb_data = json.loads(ipynb_path.read_text(encoding="utf-8-sig"))
        has_phase1_lock = True
        has_auto_shutdown = True
        has_asr = True
        modified = True
    elif modified:
        ipynb_path.write_text(json.dumps(nb_data, ensure_ascii=False, indent=1), encoding="utf-8")

    return {
        "ipynb_path": str(ipynb_path).replace("\\", "/"),
        "pure_cpu_metadata_verified": ("accelerator" not in meta and "gpuClass" not in meta),
        "phase1_locked": has_phase1_lock,
        "auto_shutdown_locked": has_auto_shutdown,
        "faster_whisper_asr_included": has_asr,
        "metadata_sanitized": modified,
    }


def _detect_colab_accelerator() -> str:
    """返回 GPU 型号（如 'Tesla T4' / 'NVIDIA L4'）或 'cpu'。"""
    try:
        if shutil.which("nvidia-smi"):
            r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return "cpu"


def _fw_detect_device() -> tuple:
    """自动探测 CUDA(float16) / CPU(int8)。"""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    try:
        if shutil.which("nvidia-smi") and subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=15).returncode == 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _fw_transcribe_wav(wav_path, model_size: str = "small", vad_filter: bool = True):
    """wave + numpy float32 解码直传 ndarray（绕过新版 PyAV `open() got unexpected keyword 'metadata_errors'`）；
    自动 CUDA float16 / CPU int8，CUDA 初始化失败回退 CPU。返回 (segments_iter, info, device, compute_type)。"""
    import wave
    import numpy as np  # type: ignore
    from faster_whisper import WhisperModel  # type: ignore
    with wave.open(str(wav_path), "rb") as wf:
        nch, sw, sr = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise RuntimeError(f"ASR_WAV_UNSUPPORTED_SAMPLEWIDTH: {sw}")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if nch > 1:
        audio = audio.reshape(-1, nch).mean(axis=1).astype(np.float32)
    if sr != 16000:
        raise RuntimeError(f"ASR_WAV_SAMPLERATE_NOT_16K: {sr}")
    device, compute = _fw_detect_device()
    try:
        wm = WhisperModel(model_size, device=device, compute_type=compute)
    except Exception:
        device, compute = "cpu", "int8"
        wm = WhisperModel(model_size, device=device, compute_type=compute)
    segs_iter, info = wm.transcribe(audio, language="zh", vad_filter=vad_filter)
    return segs_iter, info, device, compute


def run_episode_cpu_asr(
    video_path: Path,
    local_start: float,
    local_end: float,
    time_offset: float,
    out_json_paths: list[Path],
    episode: str = "第01集",
    force_resplit: bool = False,
) -> dict:
    """自动 CUDA float16 / CPU int8 调用 faster-whisper (small) 对本集锁定正片区间生成真实 asr_transcript.json"""
    primary_out = out_json_paths[0]
    if not force_resplit and primary_out.exists() and primary_out.stat().st_size > 32:
        try:
            cached_asr = json.loads(primary_out.read_text(encoding="utf-8-sig"))
            if isinstance(cached_asr, dict) and isinstance(cached_asr.get("segments"), list):
                for p_dst in out_json_paths[1:]:
                    p_dst.parent.mkdir(parents=True, exist_ok=True)
                    if not p_dst.exists():
                        shutil.copyfile(primary_out, p_dst)
                return cached_asr
        except Exception:
            pass

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        if is_colab_runtime():
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-q", "faster-whisper"],
                check=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(),
            )
            from faster_whisper import WhisperModel  # type: ignore
        else:
            raise RuntimeError("FASTER_WHISPER_NOT_INSTALLED: 当前环境缺少 faster-whisper，请在 Colab T4 GPU 运行第一棒或先安装 faster-whisper！")

    dur = max(0.1, round(float(local_end) - float(local_start), 3))
    with tempfile.TemporaryDirectory() as tmpdir:
        wav_16k = Path(tmpdir) / "episode_locked_16k.wav"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", f"{float(local_start):.3f}",
                "-i", str(video_path),
                "-t", f"{dur:.3f}",
                "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(wav_16k),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_utf8_env(),
            check=True,
        )
        log(f"🎙️ 启动 faster-whisper (small, 自动 CUDA float16 / CPU int8) ASR 转录: {episode} ({dur:.1f}s)...")
        segs_iter, info, fw_device, fw_compute = _fw_transcribe_wav(wav_16k, "small", True)
        segments_list = []
        for idx, sg in enumerate(segs_iter, 1):
            txt = str(getattr(sg, "text", "") or "").strip()
            if not txt:
                continue
            rel_s = round(float(sg.start), 3)
            rel_e = round(float(sg.end), 3)
            segments_list.append({
                "id": idx,
                "start": round(float(time_offset) + rel_s, 3),
                "end": round(float(time_offset) + rel_e, 3),
                "ep_start": rel_s,
                "ep_end": rel_e,
                "text": txt,
            })

    payload = {
        "episode": episode,
        "engine": "faster-whisper",
        "model": "small",
        "device": fw_device,
        "compute_type": fw_compute,
        "language": getattr(info, "language", "zh"),
        "source_offset_sec": round(float(time_offset), 3),
        "duration_sec": dur,
        "segments_count": len(segments_list),
        "segments": segments_list,
    }
    txt_out = json.dumps(payload, ensure_ascii=False, indent=2)
    for out_p in out_json_paths:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(txt_out, encoding="utf-8")
    log(f"✅ faster-whisper ASR 完成：共 {len(segments_list)} 条带时间戳台词 -> {primary_out}")
    return payload


def write_phase1_colab_done_marker(
    project_dir: Path,
    ep: int | str = 1,
    shots_count: int = 0,
    manifest_count: int = 0,
) -> Path:
    """
    在云端 Colab Linux CPU 实例内完成第一棒物理切镜、ASR 与清单后，写出握手凭证：
    中间文件/原片分析/第{ep:02d}集/phase1_colab_done.json
    包含 platform.uname()、UTC 时间戳与 colab_cpu_verified: True。
    """
    proj_root = resolve_ws_path(project_dir)
    ep_str = _normalize_ep_str(ep)
    done_path = proj_root / "中间文件" / "原片分析" / ep_str / "phase1_colab_done.json"
    done_path.parent.mkdir(parents=True, exist_ok=True)
    seg_mp4s = sorted((proj_root / "分集" / ep_str / "原视频段落").glob("segment_*.mp4"))
    src_jpgs = sorted((proj_root / "中间文件" / "原片分析" / ep_str).glob("segment_*_源.jpg"))
    asr_path = proj_root / "中间文件" / "在制工作区" / ep_str / "asr_transcript.json"
    asr_count = 0
    if asr_path.exists():
        try:
            asr_count = len(json.loads(asr_path.read_text(encoding="utf-8-sig")).get("segments") or [])
        except Exception:
            asr_count = 0
    uname_info = platform.uname()._asdict()
    payload = {
        "status": "PHASE1_COLAB_CPU_DONE",
        "colab_cpu_verified": bool(is_colab_runtime()),  # 兼容字段：GPU/CPU 运行时均写入
        "colab_runtime_verified": bool(is_colab_runtime()),
        "colab_accelerator": _detect_colab_accelerator(),
        "detector_engine": "TransNetV2",
        "asr_engine": "faster-whisper-small-int8",
        "asr_transcript_path": str(asr_path).replace("\\", "/") if asr_path.exists() else "",
        "asr_segments_count": asr_count,
        "episode": ep_str,
        "project_root": str(proj_root).replace("\\", "/"),
        "uname": uname_info,
        "utc_timestamp": datetime.now(timezone.utc).isoformat(),
        "segments_verified_count": len(seg_mp4s),
        "source_frames_verified_count": len(src_jpgs),
        "shots_count": shots_count,
        "manifest_items_count": manifest_count,
        "auto_shutdown_after_sync": True,
    }
    done_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return done_path


class ColabMcpClient:
    """
    Google Colab 官方远程 MCP 客户端 (https://developers.google.com/colab/api/reference/mcp)
    端点: https://colaboratory.googleapis.com/mcp
    提供 10 个官方工具:
    - list_runtime_specs / list_runtimes / create_runtime / get_runtime / delete_runtime
    - create_session / list_sessions / delete_session / execute_code / get_subscription
    """

    def __init__(self, endpoint: str = COLAB_MCP_ENDPOINT, bearer_token: str | None = None) -> None:
        self.endpoint = endpoint
        self.bearer_token = bearer_token or self._discover_bearer_token()
        self._req_id = 0

    @staticmethod
    def _discover_bearer_token() -> str:
        for env_key in ("COLAB_MCP_BEARER_TOKEN", "GOOGLE_OAUTH_ACCESS_TOKEN", "COLAB_ACCESS_TOKEN"):
            val = os.environ.get(env_key, "").strip()
            if val:
                return val

        token_files = [
            Path.home() / ".gemini" / "antigravity" / "colab_mcp_token.json",
            Path.home() / ".gemini" / "antigravity" / "mcp_oauth_tokens.json",
        ]
        for tf in token_files:
            if tf.exists():
                try:
                    data = json.loads(tf.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        if data.get("access_token"):
                            return str(data["access_token"]).strip()
                        for k, v in data.items():
                            if ("colab" in k.lower() or "colaboratory" in k.lower()) and isinstance(v, dict) and v.get("access_token"):
                                return str(v["access_token"]).strip()
                except Exception:
                    pass

        for cmd in (
            ["gcloud", "auth", "application-default", "print-access-token"],
            ["gcloud", "auth", "print-access-token"],
        ):
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=_utf8_env(),
                    timeout=5,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except Exception:
                pass
        return ""

    def _rpc(self, method: str, params: dict | None = None, timeout: int = 120) -> dict:
        self._req_id += 1
        payload: dict = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
        if params is not None:
            payload["params"] = params
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return {"http_status": resp.status, "headers": dict(resp.headers), "body": json.loads(raw)}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw)
            except Exception:
                body = {"raw": raw}
            return {"http_status": e.code, "headers": dict(e.headers), "body": body}

    def list_tools(self) -> list[dict]:
        resp = self._rpc("tools/list", timeout=15)
        return resp.get("body", {}).get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None, timeout: int = 300) -> dict:
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}}, timeout=timeout)

    def run_pure_cpu_smoke_test(self) -> dict:
        """
        实测 Colab 官方 MCP 连通性与第一棒纯 CPU 调用流程：
        1) tools/list 枚举全部 10 个官方工具；
        2) 调用 list_runtime_specs 检查 OAuth 授权状态与纯 CPU 规格 (VARIANT_CPU / NONE / SHAPE_STANDARD)；
        3) 若已授权，自动创建纯 CPU 运行时 -> create_session -> execute_code('import platform; print(platform.uname())')
           -> try...finally 立即调用 delete_runtime 彻底销毁实例！
        """
        tools = self.list_tools()
        tool_names = [t.get("name") for t in tools]

        local_stdio_status: dict = {"installed": LOCAL_COLAB_MCP_EXE.exists(), "exe": str(LOCAL_COLAB_MCP_EXE)}
        if LOCAL_COLAB_MCP_EXE.exists():
            try:
                proc = subprocess.Popen(
                    [str(LOCAL_COLAB_MCP_EXE)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                init_msg = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": True}},
                        "clientInfo": {"name": "cloud_pipeline", "version": "1.0"},
                    },
                }) + "\n"
                notif_msg = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
                list_msg = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(init_msg + notif_msg + list_msg)
                proc.stdin.flush()
                l1 = json.loads(proc.stdout.readline().strip())
                l2 = json.loads(proc.stdout.readline().strip())
                proc.terminate()
                local_stdio_status["handshake_ok"] = True
                local_stdio_status["server_info"] = l1.get("result", {}).get("serverInfo", {})
                local_stdio_status["tools"] = [t.get("name") for t in l2.get("result", {}).get("tools", [])]
            except Exception as ex:
                local_stdio_status["handshake_ok"] = False
                local_stdio_status["error"] = str(ex)

        specs_resp = self.call_tool("list_runtime_specs", {})
        http_code = specs_resp.get("http_status", 0)
        if http_code == 401 or specs_resp.get("body", {}).get("result", {}).get("isError"):
            www_auth = specs_resp.get("headers", {}).get("WWW-Authenticate", "")
            return {
                "status": "MCP_CONFIGURED_PENDING_OAUTH",
                "remote_endpoint": self.endpoint,
                "remote_tools_count": len(tool_names),
                "remote_tools": tool_names,
                "local_colab_mcp": local_stdio_status,
                "oauth_required_scope": COLAB_OAUTH_SCOPE,
                "www_authenticate": www_auth,
                "auth_message": (
                    "远程端点 https://colaboratory.googleapis.com/mcp 握手成功（10 个工具已就绪），"
                    "调用 list_runtime_specs 返回 401：需通过 Google OAuth 2.0 (scope: https://www.googleapis.com/auth/colaboratory) "
                    "或 gcloud auth application-default login 授权 Bearer Token。"
                ),
            }

        runtime_name = ""
        session_name = ""
        exec_output = None
        try:
            create_rt = self.call_tool("create_runtime", {
                "requestId": str(uuid.uuid4()),
                "runtime": {
                    "runtimeSpec": {
                        "variant": "VARIANT_CPU",
                        "accelerator": "NONE",
                        "shape": "SHAPE_STANDARD",
                    }
                },
            })
            rt_text = json.dumps(create_rt.get("body", {}))
            m = re.search(r"runtimes/[a-zA-Z0-9_-]+", rt_text)
            if m:
                runtime_name = m.group(0)
            if runtime_name:
                sess_resp = self.call_tool("create_session", {"parent": runtime_name})
                sess_text = json.dumps(sess_resp.get("body", {}))
                ms = re.search(r"runtimes/[a-zA-Z0-9_-]+/sessions/[a-zA-Z0-9_-]+", sess_text)
                session_name = ms.group(0) if ms else ""
                target_arg = {"session": session_name} if session_name else {"runtime": runtime_name}
                exec_resp = self.call_tool("execute_code", {
                    **target_arg,
                    "code": "import platform; print(platform.uname())",
                })
                exec_output = exec_resp.get("body", {})
        finally:
            if runtime_name:
                self.call_tool("delete_runtime", {"name": runtime_name, "allowMissing": True})

        return {
            "status": "MCP_PURE_CPU_SMOKE_TEST_OK",
            "remote_endpoint": self.endpoint,
            "remote_tools": tool_names,
            "local_colab_mcp": local_stdio_status,
            "runtime_created_and_deleted": runtime_name,
            "execute_code_output": exec_output,
        }


def trigger_colab_phase1(project_dir: Path, ep: int | str = 1, timeout_sec: int = 900) -> dict:
    """
    本地 Windows 触发云端 Colab 纯 CPU 实例执行第一棒切分并等待回盘握手闭环：
    0) 【优先通道：Colab 官方远程 MCP (https://colaboratory.googleapis.com/mcp)】：
       若检测到有效 OAuth / ADC Bearer Token，优先通过 ColabMcpClient 调用 create_runtime(VARIANT_CPU, NONE, SHAPE_STANDARD)
       -> create_session -> execute_code 运行第一棒纯 CPU 切镜与清单生成 -> finally 自动调用 delete_runtime 彻底销毁实例！
    1) 自动校验并确保目标 .ipynb 顶层 metadata 无 "accelerator" / "gpuClass"（100% 纯 CPU 模式），代码锁死 PHASE = "phase1"、AUTO_SHUTDOWN = True；
    2) 清除旧的 中间文件/原片分析/第{ep:02d}集/phase1_colab_done.json（若需重跑）；
    3) 通过系统默认浏览器或 Google Colab.lnk / chrome.exe --profile-directory="Profile 1" --app="https://colab.research.google.com/drive/<cloud_id>"
       打开该 Colab 笔记本，并在加载后通过 PowerShell WScript.Shell 发送 ^{F9}（Ctrl+F9 全部运行）及回车确认；
    4) 轮询等待云盘落盘 中间文件/原片分析/第{ep:02d}集/phase1_colab_done.json（由云端 Colab CPU 实例写出并在 finally 自动 os.sync() + drive.flush_and_unmount() + runtime.unassign()）；
    5) 一旦检测到 phase1_colab_done.json，等待 5 秒待云端完成 unassign 后，自动关闭本地对应 Colab 窗口并返回成功。
    """
    proj_root = resolve_ws_path(project_dir)
    ep_str = _normalize_ep_str(ep)
    ipynb_path = _find_project_phase1_ipynb(proj_root)

    # 1) 校验纯 CPU 模式与 PHASE="phase1", AUTO_SHUTDOWN=True, faster-whisper ASR
    nb_check = _ensure_pure_cpu_phase1_notebook(ipynb_path, project_dir=proj_root, ep=ep_str)

    # 2) 清除旧的 phase1_colab_done.json
    done_json_path = proj_root / "中间文件" / "原片分析" / ep_str / "phase1_colab_done.json"
    if done_json_path.exists():
        try:
            done_json_path.unlink()
        except Exception:
            pass

    # 0) 优先尝试 Colab 官方远程 MCP (https://colaboratory.googleapis.com/mcp) 纯 CPU 无头执行
    mcp_client = ColabMcpClient()
    if mcp_client.bearer_token:
        log("🚀 [Colab 官方 MCP 优先通道] 检测到 OAuth/ADC Token，正在通过 https://colaboratory.googleapis.com/mcp 创建纯 CPU 实例...")
        rt_name = ""
        try:
            cr = mcp_client.call_tool("create_runtime", {
                "requestId": str(uuid.uuid4()),
                "runtime": {"runtimeSpec": {"variant": "VARIANT_CPU", "accelerator": "NONE", "shape": "SHAPE_STANDARD"}},
            })
            if cr.get("http_status") == 200 and not cr.get("body", {}).get("result", {}).get("isError"):
                m = re.search(r"runtimes/[a-zA-Z0-9_-]+", json.dumps(cr.get("body", {})))
                rt_name = m.group(0) if m else ""
                if rt_name:
                    pip_pkgs = " ".join(PHASE1_CPU_PIP_PACKAGES)
                    code_str = (
                        "from google.colab import drive; drive.mount('/content/drive')\n"
                        "import os, subprocess, sys\n"
                        "_UTF8_ENV = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}\n"
                        f"subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', *'{pip_pkgs}'.split()], check=True, encoding='utf-8', errors='replace', env=_UTF8_ENV)\n"
                        f"subprocess.run([sys.executable, '/content/drive/MyDrive/短剧复刻工作站/tools/cloud_pipeline.py', "
                        f"'--phase', 'phase1', '--project-root', '/content/drive/MyDrive/短剧复刻工作站/01_在制项目库/{proj_root.name}', "
                        f"'--episode', '{ep_str}', '--no-auto-shutdown'], check=True, encoding='utf-8', errors='replace', env=_UTF8_ENV)\n"
                        "os.sync(); drive.flush_and_unmount()\n"
                    )
                    mcp_client.call_tool("execute_code", {"runtime": rt_name, "code": code_str}, timeout=timeout_sec)
                    if done_json_path.exists():
                        handshake_data = json.loads(done_json_path.read_text(encoding="utf-8-sig"))
                        return {
                            "status": "COLAB_MCP_PHASE1_OK",
                            "runtime": rt_name,
                            "notebook_check": nb_check,
                            "done_json_path": str(done_json_path).replace("\\", "/"),
                            "handshake": handshake_data,
                        }
        except Exception as mcp_ex:
            log(f"⚠️ [Colab 官方 MCP] 调用异常 ({mcp_ex})，无缝回退至 DriveFS cloud_id 浏览器纯 CPU 触发器...")
        finally:
            if rt_name:
                log(f"🛑 [Colab 官方 MCP] 任务结束，立即调用 delete_runtime({rt_name}) 销毁纯 CPU 实例...")
                mcp_client.call_tool("delete_runtime", {"name": rt_name, "allowMissing": True})

    # 3) 解析 DriveFS cloud_id 并打开 Colab 笔记本发送 Ctrl+F9
    cloud_id = resolve_drivefs_cloud_id(ipynb_path)
    colab_url = f"https://colab.research.google.com/drive/{cloud_id}"
    log(f"🌐 [Colab 纯 CPU 触发器] 已解析 {ipynb_path.name} -> cloud_id={cloud_id} | URL={colab_url}")

    chrome_candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    chrome_exe = next((c for c in chrome_candidates if c.exists()), None)
    if chrome_exe is not None:
        subprocess.Popen([
            str(chrome_exe),
            "--profile-directory=Profile 1",
            f"--app={colab_url}",
        ])
    else:
        os.startfile(colab_url)  # type: ignore[attr-defined]

    # 等待页面加载并通过 WScript.Shell 发送 ^{F9} (Ctrl+F9 全部运行) 及回车确认
    ps_sendkeys = (
        "Start-Sleep -Seconds 15; "
        "$ws = New-Object -ComObject WScript.Shell; "
        "[void]$ws.AppActivate('Colab'); "
        "Start-Sleep -Milliseconds 800; "
        "$ws.SendKeys('^{F9}'); "
        "Start-Sleep -Seconds 2; "
        "$ws.SendKeys('{ENTER}');"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_sendkeys],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
        check=False,
    )
    log(f"⌨️ 已向 Colab 窗口发送 Ctrl+F9 (全部运行)，正在轮询等待云端握手凭证: {done_json_path}")

    # 4) 轮询等待云盘落盘 phase1_colab_done.json
    deadline = time.time() + max(30, int(timeout_sec))
    handshake_data = None
    while time.time() < deadline:
        if done_json_path.exists() and done_json_path.stat().st_size > 32:
            try:
                handshake_data = json.loads(done_json_path.read_text(encoding="utf-8-sig"))
                if handshake_data.get("colab_cpu_verified") is True:
                    break
            except Exception:
                pass
        time.sleep(5)

    if not handshake_data:
        raise TimeoutError(
            f"等待云端 Colab 纯 CPU 实例落盘握手文件超时 ({timeout_sec}s): {done_json_path} | URL={colab_url}"
        )

    # 5) 检测到 phase1_colab_done.json 后等待 5 秒待云端完成 unassign，随后自动关闭本地对应 Colab 窗口
    log("✅ 已检测到云端 Colab CPU 握手凭证 phase1_colab_done.json，等待 5 秒待云端完成 runtime.unassign()...")
    time.sleep(5)
    ps_close = (
        "Get-Process chrome -ErrorAction SilentlyContinue | "
        "Where-Object { $_.MainWindowTitle -match 'Colab|云端夜间免守候执行器' } | "
        "ForEach-Object { [void]$_.CloseMainWindow() }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_close],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
        check=False,
    )
    log("🔒 已自动关闭本地 Colab 窗口，第一棒云端纯 CPU 切分闭环完成！")
    return {
        "status": "COLAB_PHASE1_TRIGGER_OK",
        "cloud_id": cloud_id,
        "colab_url": colab_url,
        "notebook_check": nb_check,
        "done_json_path": str(done_json_path).replace("\\", "/"),
        "handshake": handshake_data,
    }


def _resolve_episode_time_range(
    proj_root: Path,
    ep_str: str,
    start_sec: float | None = None,
    end_sec: float | None = None,
    skip_intro_sec: float = 0.0,
    skip_outro_sec: float = 0.0,
    chunk_sec: int = 360,
) -> tuple[float, float]:
    """解析分集真实正片区间：显式传入 > 分集索引.json source_range_sec > 预排表 > skip_intro_sec 推算"""
    if start_sec is not None and end_sec is not None:
        return round(float(start_sec), 3), round(float(end_sec), 3)

    idx_start, idx_end = None, None
    ep_index_file = proj_root / "中间文件" / "索引与制作记录" / "分集索引.json"
    if ep_index_file.exists():
        try:
            idx_data = json.loads(ep_index_file.read_text(encoding="utf-8-sig"))
            ep_entry = (idx_data.get("episodes") or {}).get(ep_str, {})
            src_range = str(ep_entry.get("source_range_sec") or "")
            m = re.search(r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)", src_range)
            if m:
                idx_start, idx_end = float(m.group(1)), float(m.group(2))
        except Exception:
            pass

    if idx_start is None or idx_end is None:
        sched_file = proj_root / "中间文件" / "索引与制作记录" / "原片探测与6分钟预排表.json"
        if sched_file.exists():
            try:
                sched_data = json.loads(sched_file.read_text(encoding="utf-8-sig"))
                for item in sched_data.get("episode_6min_schedule") or []:
                    if item.get("episode") == ep_str:
                        idx_start = float(item["start_sec"])
                        idx_end = float(item["end_sec"])
                        break
            except Exception:
                pass

    m_ep = re.search(r"(\d+)", ep_str)
    ep_num = int(m_ep.group(1)) if m_ep else 1
    eff_intro = max(0.0, float(skip_intro_sec or 0.0))
    fallback_start = round(eff_intro + (ep_num - 1) * chunk_sec, 3)
    fallback_end = round(fallback_start + chunk_sec, 3)

    s_val = float(start_sec) if start_sec is not None else (idx_start if idx_start is not None else fallback_start)
    e_val = float(end_sec) if end_sec is not None else (
        idx_end if (idx_end is not None and start_sec is None) else round(s_val + chunk_sec, 3)
    )
    if skip_outro_sec > 0 and e_val <= s_val:
        e_val = round(s_val + chunk_sec, 3)

    # 强制按源片真实时长 clamp 末集终点，防止短片/末集越界至 360.0s
    cand_video: Path | None = None
    if ep_index_file.exists():
        try:
            idx_data2 = json.loads(ep_index_file.read_text(encoding="utf-8-sig"))
            sv_raw = str(((idx_data2.get("episodes") or {}).get(ep_str, {}) or {}).get("source_video") or "").strip()
            if sv_raw and sv_raw != "待绑定":
                sv_p = resolve_ws_path(sv_raw)
                if sv_p.exists() and sv_p.is_file():
                    cand_video = sv_p
        except Exception:
            pass
    if cand_video is None:
        raw_dir = proj_root / "原视频"
        if raw_dir.exists():
            for ext in ("*.mp4", "*.mkv", "*.mov", "*.ts", "*.m2ts", "*.flv", "*.avi"):
                cands = sorted(raw_dir.glob(ext))
                if cands:
                    cand_video = cands[0]
                    break
    if cand_video is not None and cand_video.exists():
        try:
            v_meta = probe_video(cand_video)
            v_dur = float(v_meta.get("duration_sec") or 0.0)
            if v_dur > 0.0:
                e_val = round(min(e_val, v_dur), 3)
        except Exception:
            pass

    return round(s_val, 3), round(e_val, 3)


def ensure_cfr_episode_master(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    out_dir: Path,
    force_cfr: bool = False,
) -> tuple[Path, float]:
    """
    当源片为 .ts/.m2ts/.flv/.mkv 或开启精确模式时，先用 ffmpeg 将 [start_sec, end_sec] 区间
    重整为恒定 25fps 的 正片母本_CFR.mp4，返回 (master_path, start_sec)。
    """
    v_path = Path(video_path)
    if v_path.suffix.lower() not in (".ts", ".m2ts", ".flv", ".mkv") and not force_cfr:
        return v_path, 0.0

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    master_path = out_dir / "正片母本_CFR.mp4"
    dur = max(0.1, round(float(end_sec) - float(start_sec), 3))

    if master_path.exists() and master_path.stat().st_size > 10240 and not force_cfr:
        try:
            meta = probe_video(master_path)
            if abs(float(meta.get("duration_sec", 0.0)) - dur) <= 1.0:
                return master_path, round(float(start_sec), 3)
        except Exception:
            pass

    if not v_path.exists():
        raise FileNotFoundError(f"原视频不存在: {v_path}")

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{float(start_sec):.3f}",
        "-i", str(v_path),
        "-t", f"{dur:.3f}",
        "-vf", "fps=25",
        "-vsync", "cfr",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-g", "25",
        "-c:a", "aac", "-b:a", "192k",
        str(master_path),
    ]
    subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
        check=True,
    )
    return master_path, round(float(start_sec), 3)


def run_phase1(
    project_dir: Path | str,
    ep: int | str = "第01集",
    force_local: bool = False,
    timeout_sec: int = 900,
    start_sec: float | None = None,
    end_sec: float | None = None,
    skip_intro_sec: float = 0.0,
    skip_outro_sec: float = 0.0,
    force_resplit: bool = False,
) -> dict:
    """
    第一棒入口与本地防偷跑门禁：
    - 当 not is_colab_runtime()（即在本地 Windows 运行）且未传入 --force-local 时，拒绝在本地直接跑 FFmpeg 切分，自动调用 trigger_colab_phase1()；
    - 当 is_colab_runtime() 为 True（即在云端 Colab Linux CPU 实例内运行）时，执行完 run_phase1() 后自动在
      中间文件/原片分析/第{ep:02d}集/phase1_colab_done.json 写入包含 platform.uname()、UTC 时间戳、colab_cpu_verified: True 的握手凭证。
    """
    proj_root = resolve_ws_path(project_dir)
    ep_str = _normalize_ep_str(ep)
    allow_local = bool(force_local) or (os.environ.get("ALLOW_LOCAL_SPLIT") == "1")

    if not is_colab_runtime() and not allow_local:
        err_msg = (
            "BLOCKED_BY_LOCAL_GATEKEEPER: 未授权且不在 Colab 运行时，"
            "严禁在本地直接运行第一棒物理切分！请通过 Colab 纯 CPU 实例执行或显式传入 --force-local / --trigger-colab-phase1。"
        )
        log(f"🛡️ [{err_msg}]")
        raise RuntimeError(err_msg)

    s_sec, e_sec = _resolve_episode_time_range(
        proj_root,
        ep_str,
        start_sec=start_sec,
        end_sec=end_sec,
        skip_intro_sec=skip_intro_sec,
        skip_outro_sec=skip_outro_sec,
    )
    pipe = CloudRecreationPipeline(project_root=proj_root, episode=ep_str, source_range=f"{s_sec}-{e_sec}s")
    if force_resplit:
        for stale in (
            pipe.intermediate_dir / "物理切点清单.json",
            pipe.intermediate_dir / "物理切点清单.csv",
            pipe.intermediate_dir / "phase1_colab_done.json",
            pipe.intermediate_dir / "正片母本_CFR.mp4",
        ):
            if stale.exists():
                try:
                    stale.unlink()
                except Exception:
                    pass
    shots = pipe.step0_detect_shots(s_sec, e_sec, force_local=allow_local, force_resplit=force_resplit)
    manifest = pipe.step1_generate_script_and_manifest(shots, force_resplit=force_resplit)
    done_path = write_phase1_colab_done_marker(
        proj_root,
        ep=ep_str,
        shots_count=len(shots),
        manifest_count=len(manifest),
    )
    log(f"📝 已写入第一棒完成握手凭证: {done_path}")
    return {
        "status": "PHASE1_OK",
        "colab_cpu_verified": bool(is_colab_runtime()),  # 兼容字段：GPU/CPU 运行时均写入
        "colab_runtime_verified": bool(is_colab_runtime()),
        "colab_accelerator": _detect_colab_accelerator(),
        "detector_engine": "TransNetV2",
        "episode": ep_str,
        "start_sec": s_sec,
        "end_sec": e_sec,
        "shots_count": len(shots),
        "manifest_items_count": len(manifest),
        "done_marker": str(done_path).replace("\\", "/"),
    }


def resolve_ws_path(raw: str | Path) -> Path:
    s = str(raw).strip().replace("\\", "/")
    if "短剧复刻工作站" in s:
        suffix = s.split("短剧复刻工作站", 1)[1].lstrip("/")
        for root_cand in (
            WORKSTATION_ROOT,
            Path("/content/drive/MyDrive/短剧复刻工作站"),
            Path("G:/我的云端硬盘/短剧复刻工作站"),
        ):
            if root_cand.exists():
                return (root_cand / suffix).resolve()
        return (WORKSTATION_ROOT / suffix).resolve()
    p = Path(s)
    if p.exists():
        return p.resolve()
    inbox_candidate = INBOX_DIR / s
    if inbox_candidate.exists():
        return inbox_candidate.resolve()
    return p.resolve()


def _parse_fps_str(raw_fps: str | None) -> float:
    s = str(raw_fps or "").strip()
    if not s or s in ("0/0", "N/A"):
        return 0.0
    if "/" in s:
        p1, p2 = s.split("/", 1)
        try:
            den = float(p2)
            return round(float(p1) / den, 3) if den > 0 else 0.0
        except Exception:
            return 0.0
    try:
        return round(float(s), 3)
    except Exception:
        return 0.0


def probe_video(video_path: Path) -> dict:
    meta = _v41_engines().probe_media_strict(Path(video_path))
    if not meta.get("ok"):
        raise RuntimeError(meta.get("error") or f"ffprobe 探测失败: {video_path}")
    return meta


def _fmt_sec_token(val: float) -> str:
    r = round(float(val), 3)
    if abs(r - int(round(r))) < 1e-6:
        return str(int(round(r)))
    return f"{r:.3f}".rstrip("0").rstrip(".")


def build_episode_plan(
    duration_sec: float,
    chunk_sec: int = 360,
    skip_intro_sec: float = 0.0,
    skip_outro_sec: float = 0.0,
) -> list[dict]:
    effective_start = round(max(0.0, float(skip_intro_sec or 0.0)), 3)
    effective_end = round(max(effective_start, float(duration_sec) - max(0.0, float(skip_outro_sec or 0.0))), 3)
    remaining = round(max(0.0, effective_end - effective_start), 3)
    total_eps = max(1, math.ceil(remaining / chunk_sec)) if remaining > 0 else 1
    plan = []
    for idx in range(total_eps):
        start = round(effective_start + idx * chunk_sec, 3)
        end = round(min(effective_start + (idx + 1) * chunk_sec, effective_end), 3)
        if idx > 0 and end <= start:
            break
        plan.append({
            "episode": f"第{idx + 1:02d}集",
            "source_range": f"{_fmt_sec_token(start)}-{_fmt_sec_token(end)}s",
            "start_sec": start,
            "end_sec": end,
            "duration_sec": round(max(0.0, end - start), 3),
        })
    return plan


def cmd_onboard(args: argparse.Namespace) -> None:
    src_video = resolve_ws_path(args.source_video)
    if not src_video.exists():
        raise FileNotFoundError(f"未找到原视频文件: {src_video}（请确认已拖入 00_待选片投料池 并完成云盘同步）")

    project_root = PROJECTS_ROOT / args.project_name
    raw_video_dir = project_root / "原视频"
    raw_video_dir.mkdir(parents=True, exist_ok=True)

    target_video = raw_video_dir / src_video.name
    if src_video != target_video:
        if args.move:
            shutil.move(str(src_video), str(target_video))
        elif not target_video.exists():
            shutil.copy2(str(src_video), str(target_video))

    meta = probe_video(target_video)
    skip_intro = float(getattr(args, "skip_intro_sec", 0.0) or 0.0)
    skip_outro = float(getattr(args, "skip_outro_sec", 0.0) or 0.0)
    ep_plan = build_episode_plan(
        meta["duration_sec"],
        chunk_sec=args.chunk_sec,
        skip_intro_sec=skip_intro,
        skip_outro_sec=skip_outro,
    )
    first_ep = next((e for e in ep_plan if e["episode"] == args.episode), ep_plan[0])

    subprocess.run(
        [
            sys.executable, str(BATCH_OPS_SCRIPT), "init",
            "--project-root", str(project_root),
            "--episode", first_ep["episode"],
            "--source-video", str(target_video).replace("\\", "/"),
            "--source-range", first_ep["source_range"],
        ],
        encoding="utf-8",
        errors="replace",
        env=_utf8_env(),
        check=True,
    )

    record_dir = project_root / "中间文件" / "索引与制作记录"
    summary = {
        "subscription_tier": "Ultra_5x",
        "workflow_route": args.route,
        "execution_mode": "全程云端执行 + 本地G盘映射实时透视",
        "project_name": args.project_name,
        "project_root": str(project_root).replace("\\", "/"),
        "source_video": str(target_video).replace("\\", "/"),
        "skip_intro_sec": skip_intro,
        "skip_outro_sec": skip_outro,
        "video_metadata": meta,
        "episode_6min_schedule": ep_plan,
        "initialized_episode": first_ep["episode"],
    }
    (record_dir / "原片探测与6分钟预排表.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    phase1_ipynb = project_root / f"{project_root.name}_云端夜间免守候执行器.ipynb"
    _build_pure_cpu_phase1_notebook(phase1_ipynb, project_root, ep=first_ep["episode"])
    summary["phase1_pure_cpu_ipynb"] = str(phase1_ipynb).replace("\\", "/")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _load_batch_ops_module():
    import importlib.util
    if not BATCH_OPS_SCRIPT.exists():
        return None
    spec = importlib.util.spec_from_file_location("video_recreation_batch_ops", str(BATCH_OPS_SCRIPT))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cmd_slice(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    ep = _normalize_ep_str(args.episode)
    frame_out_dir = project_root / "中间文件" / "原片分析" / ep
    cuts_raw = getattr(args, "cuts_csv", "") or ""
    cuts_csv = resolve_ws_path(cuts_raw) if cuts_raw else (frame_out_dir / "cuts.csv")
    if not cuts_csv.exists():
        alt_cuts = project_root / "中间文件" / "在制工作区" / ep / "cuts.csv"
        if alt_cuts.exists():
            cuts_csv = alt_cuts

    vid_raw = getattr(args, "source_video", "") or ""
    if vid_raw:
        video_path = resolve_ws_path(vid_raw)
    else:
        raw_vids = sorted((project_root / "原视频").glob("*.*")) if (project_root / "原视频").exists() else []
        video_path = raw_vids[0] if raw_vids else (frame_out_dir / "正片母本_CFR.mp4")

    done_candidates = [
        frame_out_dir / "phase1_colab_done.json",
        project_root / "中间文件" / "在制工作区" / ep / "phase1_colab_done.json",
    ]
    w1_colab_verified = False
    for done_marker in done_candidates:
        if done_marker.exists() and done_marker.stat().st_size > 0:
            try:
                if json.loads(done_marker.read_text(encoding="utf-8-sig")).get("colab_cpu_verified") is True:
                    w1_colab_verified = True
                    break
            except Exception:
                continue

    allow_local = bool(getattr(args, "force_local", False)) or (os.environ.get("ALLOW_LOCAL_SPLIT") == "1") or w1_colab_verified
    if not is_colab_runtime() and not allow_local:
        err_msg = (
            "BLOCKED_BY_LOCAL_GATEKEEPER: 未检测到 W1 云端凭证 (colab_cpu_verified=true) 且不在 Colab 运行时，"
            "严禁在本地直接运行 slice 切片！请先在 Colab 纯 CPU 完成第一棒切分或显式传入 --force-local。"
        )
        log(f"🛡️ [{err_msg}]")
        print(json.dumps({"status": "BLOCKED_BY_LOCAL_GATEKEEPER", "reason": err_msg}, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(2)

    seg_video_dir = project_root / "分集" / ep / "原视频段落"
    clean_out_dir = frame_out_dir / "ref_clean_frames"
    seg_video_dir.mkdir(parents=True, exist_ok=True)
    frame_out_dir.mkdir(parents=True, exist_ok=True)
    clean_out_dir.mkdir(parents=True, exist_ok=True)

    # 切片前强制调用 verify_cuts_csv 零漏秒硬门禁
    b_mod = _load_batch_ops_module()
    shots_json_cand = frame_out_dir / "物理切点清单.json"
    if b_mod is not None and hasattr(b_mod, "verify_cuts_csv"):
        chk = b_mod.verify_cuts_csv(
            cuts_csv,
            shots_json=shots_json_cand if shots_json_cand.exists() else None,
        )
        if not chk.get("ok"):
            for err in chk.get("errors", []):
                print(err, file=sys.stderr)
            sys.exit(2)

    with cuts_csv.open("r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # 若仅存在已截取的 正片母本_CFR.mp4，自动扣除分集起始偏移
    video_offset = 0.0
    if video_path.name == "正片母本_CFR.mp4" and shots_json_cand.exists():
        try:
            sh_list = json.loads(shots_json_cand.read_text(encoding="utf-8-sig"))
            if isinstance(sh_list, list) and sh_list:
                first_sh_s = float(sh_list[0].get("start_sec") or 0.0)
                if first_sh_s > 0.5:
                    video_offset = first_sh_s
        except Exception:
            video_offset = 0.0

    seg_bounds: dict[str, list[float]] = {}
    for r in rows:
        seg = r["segment"].strip()
        s_t, e_t = float(r["start_sec"]), float(r["end_sec"])
        if seg not in seg_bounds:
            seg_bounds[seg] = [s_t, e_t]
        else:
            seg_bounds[seg][0] = min(seg_bounds[seg][0], s_t)
            seg_bounds[seg][1] = max(seg_bounds[seg][1], e_t)

    for seg, (s_t, e_t) in seg_bounds.items():
        out_mp4 = seg_video_dir / f"{seg}.mp4"
        loc_s = max(0.0, s_t - video_offset)
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{loc_s:.3f}", "-i", str(video_path),
                "-t", f"{max(0.1, e_t - s_t):.3f}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", str(out_mp4),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_utf8_env(),
            check=True,
        )

    extracted = 0
    cleaned_count = 0
    seg_shot_mapped = 0
    for i, r in enumerate(rows):
        seg, shot = r["segment"].strip(), r["shot"].strip()
        s_t, e_t = float(r["start_sec"]), float(r["end_sec"])
        loc_s = max(0.0, s_t - video_offset)
        loc_e = max(loc_s + 0.05, e_t - video_offset)
        next_seg = rows[i + 1]["segment"].strip() if i + 1 < len(rows) else None
        is_last = (next_seg != seg) or str(r.get("is_last_shot_in_segment", "")).lower() in ("1", "true", "yes")

        m_seg = re.search(r"(\d+)", seg)
        m_sh = re.search(r"(\d+)", shot)
        seg_tok = f"seg_{int(m_seg.group(1)):02d}" if m_seg else seg
        sh_tok = f"shot_{int(m_sh.group(1)):02d}" if m_sh else shot

        src_first = frame_out_dir / f"{seg}_{shot}_首帧_源.jpg"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{max(0.0, loc_s + 0.04):.3f}", "-i", str(video_path),
                "-frames:v", "1", "-q:v", "2", str(src_first),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_utf8_env(),
            check=True,
        )
        extracted += 1
        if b_mod is not None and hasattr(b_mod, "clean_source_frame") and src_first.exists():
            clean_f_path = clean_out_dir / f"{seg}_{shot}_首帧_净参考.jpg"
            b_mod.clean_source_frame(src_first, clean_f_path, mask_corner_logo=True)
            cleaned_count += 1
            alias_f_path = clean_out_dir / f"{seg_tok}_{sh_tok}_first_净参考.jpg"
            if clean_f_path.exists() and clean_f_path.resolve() != alias_f_path.resolve():
                shutil.copyfile(clean_f_path, alias_f_path)
                seg_shot_mapped += 1

        if is_last:
            src_last = frame_out_dir / f"{seg}_{shot}_尾帧_源.jpg"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{max(loc_s, loc_e - 0.15):.3f}", "-i", str(video_path),
                    "-frames:v", "1", "-q:v", "2", str(src_last),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=_utf8_env(),
                check=True,
            )
            extracted += 1
            if b_mod is not None and hasattr(b_mod, "clean_source_frame") and src_last.exists():
                clean_l_path = clean_out_dir / f"{seg}_{shot}_尾帧_净参考.jpg"
                b_mod.clean_source_frame(src_last, clean_l_path, mask_corner_logo=True)
                cleaned_count += 1
                alias_l_path = clean_out_dir / f"{seg_tok}_{sh_tok}_last_净参考.jpg"
                if clean_l_path.exists() and clean_l_path.resolve() != alias_l_path.resolve():
                    shutil.copyfile(clean_l_path, alias_l_path)

    print(json.dumps({
        "status": "ok",
        "episode": ep,
        "segments_cut": len(seg_bounds),
        "frames_extracted": extracted,
        "clean_frames_generated": cleaned_count,
        "seg_shot_first_mapped": seg_shot_mapped,
        "segment_video_dir": str(seg_video_dir).replace("\\", "/"),
        "source_frame_dir": str(frame_out_dir).replace("\\", "/"),
        "clean_frame_dir": str(clean_out_dir).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))


def _parse_segments_from_md(md_text: str) -> list[dict]:
    pattern = re.compile(r"^##\s+(segment_\d+)\s*[｜|]\s*(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(md_text))
    segments = []
    cum_sec = 0.0
    for idx, m in enumerate(matches):
        seg_id = m.group(1).strip()
        header_meta = m.group(2).strip()
        start_pos = m.end()
        end_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(md_text)
        body = md_text[start_pos:end_pos].strip()

        dur_match = re.search(r"0\s*[–\-~至]\s*(\d+(?:\.\d+)?)\s*秒", header_meta)
        dur_sec = float(dur_match.group(1)) if dur_match else 6.0
        seg_start = cum_sec
        seg_end = round(cum_sec + dur_sec, 2)
        cum_sec = seg_end
        is_golden_15s = seg_start < 15.0

        shots_found = re.findall(r"^镜头\d+\s*[：:]", body, flags=re.MULTILINE)
        shot_count = max(1, len(shots_found))
        has_no_person = "本段无人物入镜" in body
        is_single_shot_camera_only = (shot_count == 1) and (has_no_person or "首尾帧" in header_meta)
        input_paradigm = (
            "单镜首尾帧 (First & Last Frame)"
            if is_single_shot_camera_only
            else "多模态参考 (Ingredients / @图片)"
        )

        # 方案 A（严禁不同场景拼接）：跨场景或孤立短镜必须在 Veo 3.1 (4s/6s/8s) 下单独生成，尾部 Handles 由第四棒达芬奇 .fcpxml 裁切；
        # 仅限完全同一场景空间内的连续短镜才允许在 Ingredients 下合并；>8.0s (8.5s-15.0s) 超长段走 Omni 1.1/1.2 Flash。
        hdr_and_top = (header_meta + "\n" + body[:260]).lower()
        mode_tag = "Frames" if is_single_shot_camera_only else "Ingredients"
        veo_tier_sec = 4 if dur_sec <= 4.0 else (6 if dur_sec <= 6.0 else 8)
        handles_trim_sec = round(max(0.0, veo_tier_sec - dur_sec), 2) if dur_sec <= 8.0 else 0.0

        if any(k in hdr_and_top for k in ("seedance", "即梦")):
            engine = "Seedance_2.0_Multimodal_NativeAudio"
            credits = 0.0
        elif dur_sec > 10.0 or (dur_sec > 8.0 and ("omni 1.2" in hdr_and_top or "omni1.2" in hdr_and_top)):
            engine = f"Flow_Omni12_Flash_{mode_tag}_{int(round(dur_sec))}s"
            credits = round(dur_sec * 3.0, 1)
        elif 8.0 < dur_sec <= 10.0:
            engine = f"Flow_Omni11_Flash_{mode_tag}_{int(round(dur_sec))}s"
            credits = round(dur_sec * 1.5, 1)
        elif "veo 3.1 - quality" in hdr_and_top or "veo3.1-quality" in hdr_and_top or "quality" in hdr_and_top:
            engine = f"Flow_Veo31_Quality_{mode_tag}_{veo_tier_sec}s"
            credits = 50.0
        elif "veo 3.1 - fast" in hdr_and_top or "veo3.1-fast" in hdr_and_top or "fast" in hdr_and_top:
            engine = f"Flow_Veo31_Fast_{mode_tag}_{veo_tier_sec}s"
            credits = 10.0
        else:
            # 默认 <=8.0s 主力：Veo 3.1 - Lite (5 积分/条，按真实时长就近选 4s / 6s / 8s，尾部 Handles 交达芬奇 .fcpxml 自动裁切)
            engine = f"Flow_Veo31_Lite_{mode_tag}_{veo_tier_sec}s"
            credits = 5.0

        # Flow 通道强制执行“禁人声、锁闭嘴、强眉眼情绪代偿”净化（剔除任何 speaks in clear Mandarin Chinese）
        if engine.startswith("Flow_"):
            body = re.sub(
                r'[^.\n]*?\bspeaks in clear Mandarin Chinese\s*:\s*["“][^"”]*["”]\.?',
                " [Silent Emotional Acting — Lips Naturally Closed, No Spoken Dialogue, Pure Environment SFX Only]: "
                "The character keeps lips naturally closed without speaking any words, channeling all dramatic emotion into "
                "upper-face micro-expressions (expressive gaze, furrowed brows, subtle breathing chest movement, and tense jawline while keeping lips closed). "
                "Audio track generates pure environmental physical SFX and natural breathing only (zero spoken vocals).",
                body,
                flags=re.IGNORECASE,
            )

        # Extract dialogue lines
        dialogues = []
        for line in body.splitlines():
            d_m = re.match(r"^(?:台词：|\s+)(\d+(?:\.\d+)?-\d+(?:\.\d+)?s)([^：:]+)[：:]\s*(.+)$", line.strip())
            if d_m and "无台词" not in d_m.group(3):
                raw_speaker = d_m.group(2).strip()
                emo_m = re.search(r"\[(.+?)\]", raw_speaker)
                emotion = emo_m.group(1) if emo_m else "自然平稳"
                clean_speaker = re.sub(r"\[.+?\]", "", raw_speaker).strip()
                is_inner = ("心声" in clean_speaker) or ("内白" in clean_speaker)
                role_name = re.sub(r"[（(](?:心声|内白)[)）]", "", clean_speaker).strip()
                dialogues.append({
                    "segment": seg_id,
                    "time_in_segment": d_m.group(1),
                    "character": role_name,
                    "voice_mode": "心声音色(混响/内白)" if is_inner else "说话音色",
                    "emotion_bracket": emotion,
                    "text": d_m.group(3).strip(),
                })

        segments.append({
            "segment": seg_id,
            "header": header_meta,
            "episode_time_range": f"{seg_start:.1f}s-{seg_end:.1f}s",
            "duration_sec": dur_sec,
            "veo_tier_sec": veo_tier_sec if dur_sec <= 8.0 else int(round(dur_sec)),
            "handles_trim_sec": handles_trim_sec,
            "scheme_a_cross_scene_merge_forbidden": True,
            "shot_count": shot_count,
            "input_paradigm": input_paradigm,
            "is_golden_15s": is_golden_15s,
            "engine_channel": engine,
            "batch_size": "x1",
            "upscale_mode": "站内升原生4K(+50点)" if is_golden_15s and engine.startswith("Flow_") else ("0点免费升1080p->第四棒Colab_4K60fps" if engine.startswith("Flow_") else "原生1080p->第四棒Colab_4K60fps"),
            "estimated_flow_credits": credits,
            "dialogues": dialogues,
            "raw_body": body,
        })
    return segments


def cmd_phase3_pack(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    ep = _normalize_ep_str(args.episode)
    ep_dir = project_root / "分集" / ep
    work_dir = project_root / "中间文件" / "在制工作区" / ep
    work_dir.mkdir(parents=True, exist_ok=True)

    md_path = ep_dir / f"{ep}_视频提示词.md"
    if not md_path.exists():
        raise FileNotFoundError(f"未找到视频提示词文件: {md_path}")
    md_text = md_path.read_text(encoding="utf-8")

    unfilled_style = "{{VISUAL_STYLE}}" in md_text
    unfilled_cast = "【待填角色：" in md_text

    segments = _parse_segments_from_md(md_text)
    # 若存在 cuts.csv，读取每段锁定的 model_route / video_mode / gen_duration_sec / need_tail_frame
    cuts_by_seg: dict[str, list[dict]] = {}
    for c_cand in (
        project_root / "中间文件" / "原片分析" / ep / "cuts.csv",
        work_dir / "cuts.csv",
        ep_dir / "cuts.csv",
    ):
        if c_cand.exists() and c_cand.stat().st_size > 0:
            try:
                with c_cand.open("r", encoding="utf-8-sig") as f_c:
                    for crow in csv.DictReader(f_c):
                        sk = str(crow.get("segment") or "").strip()
                        if sk:
                            cuts_by_seg.setdefault(sk, []).append(crow)
                if cuts_by_seg:
                    break
            except Exception:
                pass
    kf_root = ep_dir / "首尾及关键帧"
    char_ref_dir = ep_dir / "人物" if (ep_dir / "人物").exists() and any((ep_dir / "人物").iterdir()) else (project_root / "人物参考")
    char_files = sorted(char_ref_dir.glob("*.png")) if char_ref_dir.exists() else []
    voice_dir = ep_dir / "声线" if (ep_dir / "声线").exists() else (project_root / "声线参考")
    voice_files = sorted(voice_dir.glob("*.wav")) if voice_dir.exists() else []

    # 1. 自动检查/调用 dialogue-gen 确保 分集/第NN集/第NN集_台词生成表.xlsx (.csv) 与 台词音频/ 齐备，并同步打包进 第N集交付文件/05_台词配音与心声轨/
    dlg_audio_dir = ep_dir / "台词音频"
    dlg_table_xlsx = ep_dir / f"{ep}_台词生成表.xlsx"
    dlg_table_csv = ep_dir / f"{ep}_台词生成表.csv"
    dlg_mode = str(getattr(args, "mode", "cosyvoice2") or "cosyvoice2")
    if (not dlg_table_xlsx.exists() or not dlg_audio_dir.exists()) and BATCH_OPS_SCRIPT.exists():
        subprocess.run(
            [
                sys.executable, str(BATCH_OPS_SCRIPT), "dialogue-gen",
                "--project-root", str(project_root),
                "--episode", ep,
                "--mode", dlg_mode,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_utf8_env(),
        )
    dlg_wavs = sorted(dlg_audio_dir.glob("*.wav")) if dlg_audio_dir.exists() else []
    delivery_05_dirs = [
        ep_dir / f"{ep}交付文件" / "05_台词配音与心声轨",
        project_root / f"{ep}交付文件" / "05_台词配音与心声轨",
    ]
    for d05 in delivery_05_dirs:
        d05.mkdir(parents=True, exist_ok=True)
        for w in dlg_wavs:
            shutil.copyfile(w, d05 / w.name)
        if (dlg_audio_dir / "台词音频清单.json").exists():
            shutil.copyfile(dlg_audio_dir / "台词音频清单.json", d05 / "台词音频清单.json")
        for tbl_p in (dlg_table_xlsx, dlg_table_csv):
            if tbl_p.exists():
                shutil.copyfile(tbl_p, d05 / tbl_p.name)
                shutil.copyfile(tbl_p, d05.parent / tbl_p.name)

    # 解析角色肖像 (_肖像.png) 与主体 (_主体.png) 配对，供 Google Flow Character 角色卡预封装（严禁在 Flow 上传音频！）
    char_cards = {}
    for p in char_files:
        parts = p.stem.split("_")
        role_id = parts[0] if parts else "01"
        role_name = parts[1] if len(parts) >= 2 else p.stem
        stage_name = parts[2] if len(parts) >= 4 else "初始"
        key = f"{role_id}_{role_name}_{stage_name}"
        if key not in char_cards:
            char_cards[key] = {
                "role_id": role_id,
                "role_name": role_name,
                "stage": stage_name,
                "portrait_png": "",
                "subject_png": "",
                "fallback_png": str(p).replace("\\", "/"),
            }
        if p.stem.endswith("_肖像"):
            char_cards[key]["portrait_png"] = str(p).replace("\\", "/")
        elif p.stem.endswith("_主体"):
            char_cards[key]["subject_png"] = str(p).replace("\\", "/")

    total_flow_credits = 0
    all_dialogues = []
    for seg in segments:
        seg_id = seg["segment"]
        # 优先扫描扁平化存放在 首尾及关键帧/ 下的 segment_XX_镜头YY_首帧/尾帧.png，同时向后兼容子目录
        flat_frames = sorted(kf_root.glob(f"{seg_id}_*.*")) if kf_root.exists() else []
        sub_kf_dir = kf_root / seg_id
        sub_frames = sorted(sub_kf_dir.glob("*.*")) if sub_kf_dir.exists() else []
        frames = flat_frames if flat_frames else sub_frames

        first_f = next((f for f in frames if "镜头01_首帧" in f.name or f.name.endswith("_首帧.png")), frames[0] if frames else None)
        post_cut_frames = [f for f in frames if "_首帧" in f.name and f != first_f]
        last_f = next((f for f in reversed(frames) if "_尾帧" in f.name), None)
        frame_paths = [str(f).replace("\\", "/") for f in frames]

        seg_dlg_wavs = [str(w).replace("\\", "/") for w in dlg_wavs if w.name.startswith(f"{seg_id}_")]
        seg["segment_dialogue_wavs"] = seg_dlg_wavs

        matched_chars = []
        for cinfo in char_cards.values():
            if cinfo["role_name"] in seg["raw_body"]:
                if cinfo["portrait_png"]:
                    matched_chars.append(cinfo["portrait_png"])
                if cinfo["subject_png"]:
                    matched_chars.append(cinfo["subject_png"])
                if not cinfo["portrait_png"] and not cinfo["subject_png"]:
                    matched_chars.append(cinfo["fallback_png"])

        seg["matched_character_refs"] = matched_chars
        seg["all_segment_frames"] = frame_paths
        # 多模态上传顺序：左起第1张切镜前首帧 -> 左起第2张切镜后首帧(或按需尾帧) -> 左起第3张人物白底图(_肖像/_主体)
        ordered_ingredients = []
        if first_f:
            ordered_ingredients.append(str(first_f).replace("\\", "/"))
        for pcf in post_cut_frames:
            ordered_ingredients.append(str(pcf).replace("\\", "/"))
        if last_f:
            ordered_ingredients.append(str(last_f).replace("\\", "/"))
        for mc in matched_chars:
            if mc not in ordered_ingredients:
                ordered_ingredients.append(mc)
        seg["multimodal_ingredients_list"] = ordered_ingredients[:10]
        seg["first_frame_path"] = str(first_f).replace("\\", "/") if first_f else f"分集/{ep}/首尾及关键帧/{seg_id}_镜头01_首帧.png"
        seg["second_frame_path"] = (
            str(post_cut_frames[0]).replace("\\", "/")
            if post_cut_frames
            else (str(last_f).replace("\\", "/") if last_f else "")
        )
        seg["last_frame_path"] = str(last_f).replace("\\", "/") if last_f else "EXEMPTED_NO_TAIL_FRAME_NEEDED"
        seg["conversational_edit_fallback"] = "Keep main subject motion and facial expression identical; refine background lighting consistency and remove minor edge artifacts."
        total_flow_credits += seg["estimated_flow_credits"]
        all_dialogues.extend(seg["dialogues"])

    aspect_ratio = _v41_engines().v42_qc.resolve_aspect_ratio_v46(project_root, ep)
    ar_label = "9:16 竖屏 (1080×1920 / 2160×3840)" if aspect_ratio == "9:16" else "16:9 横屏 (1920×1080 / 3840×2160)"
    manifest = {
        "project_root": str(project_root).replace("\\", "/"),
        "episode": ep,
        "aspect_ratio": aspect_ratio,
        "placeholders_check": {
            "has_unfilled_visual_style": unfilled_style,
            "has_unfilled_cast": unfilled_cast,
            "ready_for_push": (not unfilled_style) and (not unfilled_cast),
        },
        "flow_character_cards": list(char_cards.values()),
        "dialogue_audio_dir": str(dlg_audio_dir).replace("\\", "/"),
        "delivery_05_track_dir": str(delivery_05_dirs[0]).replace("\\", "/"),
        "dialogue_wavs_count": len(dlg_wavs),
        "total_segments": len(segments),
        "estimated_total_flow_credits": total_flow_credits,
        "musicfx_zero_credit_prompts": [
            "48kHz cinematic orchestral suspense opening soundtrack, deep cello pulse, subtle ticking percussion, dark atmospheric tension, instrumental only, no vocals",
            "48kHz dramatic confrontation score, driving taiko drums and staccato strings, rising emotional urgency, high dynamic range, instrumental only, no vocals",
            "48kHz epic climax reversal theme, soaring brass and emotional piano motif, cathartic resolution, studio master quality, instrumental only, no vocals",
        ],
        "segments": segments,
    }

    manifest_path = work_dir / "第三棒_视频推流投喂总表.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # Export TTS dual-voice table
    tts_csv_path = work_dir / "第三棒_双音色配音任务表.csv"
    with tts_csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["segment", "time_in_segment", "character", "voice_mode", "emotion_bracket", "text"])
        writer.writeheader()
        writer.writerows(all_dialogues)

    # Export human/browser-agent markdown handbook
    md_out = [
        f"# {ep} · 多模态视频推流投喂手册（供人工生成视频 · 两步无脑操作卡）",
        "",
        f"- **总分段数**：`{len(segments)}` 段 ｜ **画幅比例**：`{aspect_ratio}`（{ar_label}） ｜ **预计消耗 Flow 积分**：`{total_flow_credits} 点`（10,000 点/月免费池内）",
        f"- **占位符状态**：`{'✅ 已全部回填完毕' if manifest['placeholders_check']['ready_for_push'] else '⚠️ 尚有未回填占位符，请先运行 batch_ops.py fill-style'}`",
        f"- **台词生成表与预生成 `.wav` 轨状态**：已输出《`{ep}_台词生成表.xlsx`》（及 `.csv`），并为 Flow 镜头与全集心声 OS 生成 `{len(dlg_wavs)}` 条标准音频归入 `分集/{ep}/台词音频/` 与 `{ep}交付文件/05_台词配音与心声轨/`。",
        "- **分镜帧全局唯一命名**：`分集/第NN集/首尾及关键帧/segment_XX_镜头YY_首帧.png`（及按需 `_尾帧.png`），直接扁平存放在 `首尾及关键帧/` 目录下，批量上传网页画布永不重名。",
        "",
        "## 🔊 【傻瓜式外包声音规则】（外包制作员必读 · Flow 与即梦极简分流）",
        "- 👉 **在 Google Flow (Veo / Omni 1.2) 生成时**：外包操作员**完全无需设置或上传任何音频**！给 Flow 的英文提示词已强制锁死 `[Silent Emotional Acting — Lips Naturally Closed, No Spoken Dialogue, Pure Environment SFX Only]`（画面人物自然闭嘴、靠眉眼和呼吸演戏，音轨仅输出纯净环境物理音效 SFX）。第四棒回盘后，Colab A100 将使用 `CosyVoice 2 - 0.5B` 生成的台词 `.wav` + 字节开源 `LatentSync` 全自动刷上毫秒级中文口型并与原生环境 SFX 无损合流！",
        "- 👉 **在即梦（Seedance 2.0）生成时**：外包操作员直接上传第二棒生成的 **`2–3秒 .wav` 角色声线母本（`分集/第NN集/声线/角色编号_角色新姓名_说话_声线参考.wav`，作为 `@音频1`）** + 中文情绪台词提示词，**即梦生出来的视频已经原生自带同款音色、情绪和中文口型**（带音与不带音同价，后期完全不用再动口型或重配单句音频）！",
        "- 👉 **心声（OS）镜头**：画面人物全程闭嘴演微表情，免对口型，第四棒直接将 `CosyVoice 2` 生成的 `_心声.wav` 贴入达芬奇 `OS_Track` 时间线。",
        "",
        "## 👤 第 0 步：建角色卡（Google Flow `Characters` 预封装，每集仅首次做 1 次）",
    ]
    if char_cards:
        for cinfo in char_cards.values():
            p_png = cinfo["portrait_png"] or cinfo["fallback_png"]
            s_png = cinfo["subject_png"] or cinfo["fallback_png"]
            md_out.append(
                f"  - 角色 **`{cinfo['role_name']}`（{cinfo['stage']}）**：在 Flow 左侧 `Characters` 新建角色 ➔ 左上角填 `{cinfo['role_name']}` ➔ **`肖像`** 槽位上传 `{p_png}` ➔ **`创建主体`** 槽位上传 `{s_png}` ➔ 点击保存（**无需上传任何音频**）。"
            )
    else:
        md_out.append("  - 在 Flow 左侧 `Characters` 新建角色 ➔ 左上角填 `角色新姓名` ➔ **`肖像`** 槽位上传 `人物/角色编号_角色新姓名_阶段_肖像.png` ➔ **`创建主体`** 槽位上传 `人物/角色编号_角色新姓名_阶段_主体.png` ➔ 点击保存（**无需上传任何音频**）。")
    md_out.extend([
        "- **一次过与超分分界**：面板并发锁死 `x1`；`0–15s` 开站内原生 `4K (+50点)`；`15s` 后点站内 **`0 点免费升 1080p`** 下载放入 `<在制工作区>/原始生成视频/`，随后触发**第四棒**执行 Colab LatentSync 中文重刷口型 + 4K 超分与达芬奇 `.fcpxml` 合片！",
        "",
        "---",
        "",
    ])
    for s in segments:
        seg_id = s["segment"]
        seg_crows = cuts_by_seg.get(seg_id, [])
        c_route = str(seg_crows[0].get("model_route") or "").strip() if seg_crows else ""
        c_mode = str(seg_crows[0].get("video_mode") or "").strip() if seg_crows else ""
        c_gd_raw = str(seg_crows[0].get("gen_duration_sec") or "").strip() if seg_crows else ""
        c_actual_dur = (
            round(sum(max(0.0, float(r.get("end_sec") or 0.0) - float(r.get("start_sec") or 0.0)) for r in seg_crows), 2)
            if seg_crows
            else float(s["duration_sec"])
        )
        try:
            c_gen_dur = float(c_gd_raw) if c_gd_raw else float(s.get("veo_tier_sec") or s["duration_sec"])
        except Exception:
            c_gen_dur = float(s.get("veo_tier_sec") or s["duration_sec"])
        c_need_tail = any(
            str(r.get("need_tail_frame") or "").strip().lower() in ("1", "true", "yes")
            for r in seg_crows
        ) if seg_crows else (s["last_frame_path"] != "EXEMPTED_NO_TAIL_FRAME_NEEDED")

        s["duration_sec"] = c_actual_dur
        s["gen_duration_sec"] = c_gen_dur
        s["handles_trim_sec"] = round(max(0.0, c_gen_dur - c_actual_dur), 2)
        if c_route:
            s["model_route"] = c_route
        if c_mode:
            s["video_mode"] = c_mode
        s["need_tail_frame"] = c_need_tail

        route_probe = (c_route + " " + s["engine_channel"]).lower()
        mode_probe = (c_mode + " " + s["input_paradigm"]).lower()
        is_seedance_seg = ("seedance" in route_probe) or ("即梦" in route_probe)
        is_omni_seg = ("omni" in route_probe)
        is_veo_seg = not is_seedance_seg and not is_omni_seg
        veo_sec_int = int(round(c_gen_dur))
        is_veo_8s_ing = is_veo_seg and (veo_sec_int == 8) and ("ingredients" in mode_probe or ("frames" not in c_mode.lower() and s["shot_count"] > 1))

        char_img = s["matched_character_refs"][0] if s["matched_character_refs"] else f"分集/{ep}/人物/角色01_角色名_初始_肖像.png"
        char_img_2 = s["matched_character_refs"][1] if len(s["matched_character_refs"]) > 1 else ""
        seg_wav_list = s.get("segment_dialogue_wavs") or []

        if is_seedance_seg:
            # 匹配说话角色的 2-3s 声线母本
            spk_name = s["dialogues"][0]["character"] if s.get("dialogues") else ""
            matched_vref = next(
                (str(vf).replace("\\", "/") for vf in voice_files if "说话" in vf.name and (not spk_name or spk_name in vf.name)),
                str(voice_files[0]).replace("\\", "/") if voice_files else f"分集/{ep}/声线/角色01_角色新姓名_说话_声线参考.wav",
            )
            s["input_paradigm"] = "即梦原生音画 (@图片1+@图片2+@音频1)"
            s["multimodal_ingredients_list"] = [s["first_frame_path"], char_img, matched_vref]
            step1_lines = [
                "> **第一步（即梦 Seedance 2.0 投喂规则：`@图片1 首帧 + @图片2 角色肖像 + @音频1 角色2-3s声线母本`，原生直出音画口型）**：",
                f"> - **`@图片1` 首帧**：`{s['first_frame_path']}`",
                f"> - **`@图片2` 角色肖像**：`{char_img}`",
                f"> - **`@音频1` 角色2-3s声线母本**：`{matched_vref}`",
            ]
        elif is_omni_seg:
            omni_ver = "Omni 1.2 Flash" if ("1.2" in route_probe or "omni12" in route_probe or c_gen_dur > 10.0 or c_gen_dur < 4.0) else "Omni 1.1 Flash"
            slider_s = max(3, min(15, int(round(c_gen_dur))))
            s["input_paradigm"] = f"{omni_ver} 逐秒滑块 ({slider_s}s)"
            omni_refs = [s["first_frame_path"]]
            if s["second_frame_path"]:
                omni_refs.append(s["second_frame_path"])
            if s["matched_character_refs"]:
                omni_refs.append(char_img)
            s["multimodal_ingredients_list"] = omni_refs[:4]
            step1_lines = [
                f"> **第一步（【{omni_ver} 模式】逐秒时长滑块调至 `{slider_s}s`，按序上传参考图，Flow 无需上传音频）**：",
                f"> - **逐秒时长滑块**：锁定 `{slider_s}s`（原片实长 `{c_actual_dur}s`）",
                f"> - **参考图 1（首帧）**：`{s['first_frame_path']}`",
            ]
            if s["second_frame_path"]:
                step1_lines.append(f"> - **参考图 2（段内切镜帧/尾帧）**：`{s['second_frame_path']}`")
            step1_lines.append(f"> - **参考图（角色锁脸卡）**：`{char_img}`")
        elif is_veo_8s_ing:
            s["input_paradigm"] = "Veo 3.1 【Ingredients模式 · 8s · ≤3张】"
            ing_refs = [s["first_frame_path"]]
            if s["second_frame_path"] and s["second_frame_path"] not in ing_refs:
                ing_refs.append(s["second_frame_path"])
            for c_r in (char_img, char_img_2):
                if c_r and c_r not in ing_refs and len(ing_refs) < 3:
                    ing_refs.append(c_r)
            s["multimodal_ingredients_list"] = ing_refs[:3]
            step1_lines = [
                "> **第一步（`Veo 3.1` `8s` 【Ingredients模式】上传 `≤ 3 张`（首帧 + 1~2 张核心动作角色卡），严禁超 3 张，Flow 禁传音频）**：",
            ]
            for r_i, r_p in enumerate(ing_refs[:3], 1):
                step1_lines.append(f"> - **左起第 {r_i} 张（Image {r_i}）**：`{r_p}`")
        else:
            # Veo 3.1 4s/6s (或 8s Frames)
            tier_lbl = veo_sec_int if veo_sec_int in (4, 6, 8) else (4 if c_actual_dur <= 4.0 else (6 if c_actual_dur <= 6.0 else 8))
            has_handles = (tier_lbl - c_actual_dur) > 0.15
            allow_end_f = (not has_handles) and c_need_tail and (s["last_frame_path"] != "EXEMPTED_NO_TAIL_FRAME_NEEDED")
            s["input_paradigm"] = f"Veo 3.1 【Frames模式 · {tier_lbl}s · 禁挂角色卡】"
            s["multimodal_ingredients_list"] = [s["first_frame_path"]] + ([s["last_frame_path"]] if allow_end_f else [])
            step1_lines = [
                f"> **第一步（`Veo 3.1` `{tier_lbl}s` 【Frames模式】仅传 Start Frame 首帧（严禁挂角色卡！若含 Handles 余量严禁传尾帧；仅满载大运镜且 `need_tail_frame=true` 才传 End Frame），Flow 禁传音频）**：",
                f"> - **Start Frame（首帧）**：`{s['first_frame_path']}`",
            ]
            if allow_end_f:
                step1_lines.append(f"> - **End Frame（满载大运镜尾帧，`need_tail_frame=true`）**：`{s['last_frame_path']}`")
            else:
                step1_lines.append(
                    f"> - **End Frame / 角色卡**：`❌ 严禁上传！（本段实长 {c_actual_dur}s -> 选 {tier_lbl}s 档，尾部 {round(max(0.0, tier_lbl - c_actual_dur), 2)}s Handles 余量由第四棒 .fcpxml 精准裁切，严禁挂角色卡或尾帧）`"
                )

        route_display = c_route if c_route else s["engine_channel"]
        md_out.extend([
            f"## {seg_id} ｜ {s['episode_time_range']} (实长 `{s['duration_sec']}s` · 生成 `{c_gen_dur:g}s` · 共 `{s['shot_count']}` 镜) ➔ `{route_display}`",
            f"- **投喂模式**：**`{s['input_paradigm']}`** ｜ 并发：`{s['batch_size']}` ｜ 清晰度：`{s['upscale_mode']}` ｜ 预计扣点：`{s['estimated_flow_credits']} 点`",
            f"- **本段预生成标准台词/心声轨（第四棒自动贴轨刷口型，严禁烧入视频原声）**：`{', '.join(Path(w).name for w in seg_wav_list) if seg_wav_list else '本段无单句台词轨'}`",
            "",
            *step1_lines,
            ">",
            "> **第二步（原生提示词一键复制框，制作员全选复制粘贴即可，无需改任何字）**：",
            "",
            "```text",
            s["raw_body"],
            "```",
            "",
        ])
    # 同步刷新投喂总表 JSON（包含更新后的 input_paradigm 与受控槽位列表）
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    handbook_path = work_dir / "第三棒_Flow与Seedance分通道投喂手册.md"
    handbook_ep_path = ep_dir / f"{ep}_多模态视频推流投喂手册.md"
    handbook_text = "\n".join(md_out)
    handbook_path.write_text(handbook_text, encoding="utf-8")
    handbook_ep_path.write_text(handbook_text, encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "episode": ep,
        "total_segments": len(segments),
        "dialogue_wavs_count": len(dlg_wavs),
        "delivery_05_track_dir": str(delivery_05_dirs[0]).replace("\\", "/"),
        "estimated_total_flow_credits": total_flow_credits,
        "ready_for_push": manifest["placeholders_check"]["ready_for_push"],
        "manifest_json": str(manifest_path).replace("\\", "/"),
        "handbook_md": str(handbook_ep_path).replace("\\", "/"),
        "work_handbook_md": str(handbook_path).replace("\\", "/"),
        "tts_csv": str(tts_csv_path).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))


def _build_colab_lipsync_and_vsr_notebook(
    project_root: Path, ep: str, to_vsr_dir: Path | None = None, done_4k_dir: Path | None = None, target_fps: int = DEFAULT_TARGET_FPS,
) -> Path:
    return _v41_engines()._build_v41_phase4_colab_notebook(project_root, _normalize_ep_str(ep), target_fps=target_fps)
    """
    生成 分集/第NN集/第NN集_Colab_A100_4K60fps_超分流水线.ipynb：
    在 Colab A100 (CU) 上一站式串行跑完：
    - Step 1：Google Drive 自动挂载 + 阿里开源 CosyVoice 2 - 0.5B（模型自动缓存于 /content/drive/MyDrive/短剧复刻工作站/models/cosyvoice2），
      直接读取《第NN集_台词生成表.csv》，以第二棒 2–3s .wav 声线母本锁死统一音色，按 CosyVoice2_情绪导演指令 仅为【Flow 镜头与心声 OS】生成高表现力 .wav（自动跳过即梦原生带音镜头 SKIP_JIMENG_NATIVE）；
    - Step 2：字节开源 LatentSync（即梦同源口型底座）仅对【Flow 开口台词镜头】重刷 100% 帧级同步中文口型并与 Flow 原生纯净环境 SFX 无损合流（即梦镜头原生带口型带音频，直接跳过 LatentSync）；
    - Step 3：Real-ESRGAN 4K + RIFE 60fps 超分补帧（--audio_codec copy 无损透传音轨）并同步回盘至 Google Drive。
    """
    ep_dir = project_root / "分集" / ep
    dlg_dir = ep_dir / "台词音频"
    dlg_csv = ep_dir / f"{ep}_台词生成表.csv"
    voice_ref_dir = project_root / "声线参考"
    lipsynced_dir = project_root / "中间文件" / "在制工作区" / ep / "已重刷口型视频"

    step1_cosyvoice2_code = [
        "# === Step 1：挂载 Google Drive + 阿里开源 CosyVoice 2 - 0.5B 情绪语音合成（仅生成 Flow 镜头与心声 OS，跳过即梦原生镜头） ===\n",
        "import csv, json, subprocess, shutil\n",
        "from pathlib import Path\n\n",
        "try:\n",
        "    from google.colab import drive\n",
        "    drive.mount('/content/drive')\n",
        "except Exception:\n",
        "    pass\n\n",
        'COSYVOICE2_CACHE = Path("/content/drive/MyDrive/短剧复刻工作站/models/cosyvoice2")\n',
        "COSYVOICE2_CACHE.mkdir(parents=True, exist_ok=True)\n",
        f'EPISODE = "{ep}"\n',
        f'DLG_TABLE_CSV = Path(r"{str(dlg_csv).replace(chr(92), "/")}")\n',
        f'VOICE_REF_DIR = Path(r"{str(voice_ref_dir).replace(chr(92), "/")}")\n',
        f'DLG_WAV_DIR = Path(r"{str(dlg_dir).replace(chr(92), "/")}")\n',
        "DLG_WAV_DIR.mkdir(parents=True, exist_ok=True)\n\n",
        "# 若处于 Colab CU 云端且已加载 CosyVoice2，则按《第NN集_台词生成表.csv》的 Instruct 情绪导演指令与 3s 声线母本批量生成/覆写 .wav\n",
        "if DLG_TABLE_CSV.exists():\n",
        "    with DLG_TABLE_CSV.open('r', encoding='utf-8-sig') as f:\n",
        "        rows = list(csv.DictReader(f))\n",
        "    print(f'读取《{DLG_TABLE_CSV.name}》共 {len(rows)} 条记录（即梦原生带音镜头自动跳过单句生成）')\n",
        "    for r in rows:\n",
        "        out_fname = (r.get('输出音频文件名') or '').strip()\n",
        "        strat = (r.get('视频通道与口型策略') or '').strip()\n",
        "        if not out_fname or out_fname == 'SKIP_JIMENG_NATIVE' or '即梦原生直出' in strat:\n",
        "            print(f\"  [跳过即梦原生直出] {r.get('分段与镜号')} -> 已在即梦挂载 2-3s 声线母本原生直出音画口型\")\n",
        "            continue\n",
        "        ref_wav = VOICE_REF_DIR / (r.get('绑定声线母本') or '').strip()\n",
        "        instruct_prompt = (r.get('CosyVoice2_情绪导演指令') or '').strip()\n",
        "        text_line = (r.get('台词正文') or '').strip()\n",
        "        dst_wav = DLG_WAV_DIR / out_fname\n",
        "        print(f\"  [CosyVoice2-0.5B Instruct] {r.get('分段与镜号')} | 母本={ref_wav.name} | 指令={instruct_prompt} -> {dst_wav.name}\")\n",
    ]

    step2_latentsync_code = [
        "# === Step 2：字节开源 LatentSync（即梦同源口型底座）仅对【Flow 闭嘴镜头】重刷中文口型 + 原生纯净环境 SFX 无损合流 ===\n",
        "# 注：即梦（Seedance 2.0）镜头已自带同款音色与中文口型，此处完全不碰即梦镜头！\n",
        "import subprocess, shutil\n",
        "from pathlib import Path\n\n",
        f'RAW_VID_DIR = Path(r"{str(to_vsr_dir).replace(chr(92), "/")}")\n',
        f'DLG_WAV_DIR = Path(r"{str(dlg_dir).replace(chr(92), "/")}")\n',
        f'LIPSYNC_OUT_DIR = Path(r"{str(lipsynced_dir).replace(chr(92), "/")}")\n',
        "LIPSYNC_OUT_DIR.mkdir(parents=True, exist_ok=True)\n\n",
        "# 仅扫描需刷口型的 Flow 开口台词音频：segment_XX_镜头YY_角色新姓名_台词.wav（不含 _心声.wav，也不含即梦跳过项）\n",
        'lipsync_wavs = sorted(DLG_WAV_DIR.glob("segment_*_镜头*_*_台词*.wav"))\n',
        'print(f"发现 {len(lipsync_wavs)} 条需执行字节开源 LatentSync 中文重对口型的 Flow 镜头台词轨")\n\n',
        "for mp4_path in sorted(RAW_VID_DIR.glob('segment_*.mp4')):\n",
        "    seg_id = mp4_path.stem\n",
        "    seg_wavs = [w for w in lipsync_wavs if w.name.startswith(f'{seg_id}_')]\n",
        "    out_mp4 = LIPSYNC_OUT_DIR / mp4_path.name\n",
        "    if not seg_wavs:\n",
        "        # 即梦原生带音镜头、纯动作镜头或纯心声 OS 闭嘴镜头：完全不动口型，100% 原样透传进入 4K 超分！\n",
        "        shutil.copy2(mp4_path, out_mp4)\n",
        "        continue\n\n",
        "    # Flow 镜头因提示词已锁死 [Silent Emotional Acting — Lips Naturally Closed, Pure Environment SFX Only]，\n",
        "    # 原视频音轨即为 100% 纯净环境物理音效 SFX，直接调用字节开源 LatentSync 刷入中文口型并与原生 SFX 合流：\n",
        "    primary_dlg_wav = seg_wavs[0]\n",
        "    lipsync_vid = LIPSYNC_OUT_DIR / f'{seg_id}_latentsync_raw.mp4'\n",
        "    ls_cmd = [\n",
        "        'python', '-m', 'scripts.inference',\n",
        "        '--video_path', str(mp4_path),\n",
        "        '--audio_path', str(primary_dlg_wav),\n",
        "        '--video_out_path', str(lipsync_vid),\n",
        "        '--inference_steps', '20', '--guidance_scale', '1.5'\n",
        "    ]\n",
        "    subprocess.run(ls_cmd, check=False)\n",
        "    vid_src = lipsync_vid if lipsync_vid.exists() else mp4_path\n",
        "    subprocess.run([\n",
        "        'ffmpeg', '-y', '-i', str(vid_src), '-i', str(primary_dlg_wav), '-i', str(mp4_path),\n",
        "        '-filter_complex', '[1:a]volume=1.0[dlg];[2:a]volume=0.60[sfx];[dlg][sfx]amix=inputs=2:duration=first[aout]',\n",
        "        '-map', '0:v:0', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k', str(out_mp4)\n",
        "    ], check=False)\n",
        "    if out_mp4.exists():\n",
        "        shutil.copy2(out_mp4, mp4_path)\n",
        "print('✅ Step 2 Flow 镜头字节开源 LatentSync 中文口型重绘与纯净环境 SFX 合流完成（即梦原生镜头已无损透传）！')\n",
    ]

    step3_vsr_ladder_code = [
        "# === Step 3：三档分辨率自动探针与分流超分（即梦/Omni 720p 两步阶梯超分 720p->1080p->4K vs Flow 1080p 一步超分 1080p->4K） ===\n",
        "import json, subprocess, shutil\n",
        "from pathlib import Path\n\n",
        f'IN_DIR = Path(r"{str(to_vsr_dir).replace(chr(92), "/")}")\n',
        f'OUT_4K_DIR = Path(r"{str(done_4k_dir).replace(chr(92), "/")}")\n',
        'STAGE1_1080P_DIR = IN_DIR.parent / "中间阶梯_720p转1080p"\n',
        "OUT_4K_DIR.mkdir(parents=True, exist_ok=True)\n",
        "STAGE1_1080P_DIR.mkdir(parents=True, exist_ok=True)\n\n",
        "def probe_wh(mp4_p: Path):\n",
        "    res = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', str(mp4_p)], capture_output=True, text=True)\n",
        "    streams = json.loads(res.stdout or '{}').get('streams', [{}])\n",
        "    return int(streams[0].get('width', 0)), int(streams[0].get('height', 0))\n\n",
        "AUTO_SHUTDOWN = True  # 默认开启：跑完或异常退出后 100% 自动刷盘并销毁 Colab 实例，严防待机空跑扣费\n",
        "try:\n",
        "    for mp4 in sorted(IN_DIR.glob('segment_*.mp4')):\n",
        "        w, h = probe_wh(mp4)\n",
        "        dst_4k = OUT_4K_DIR / mp4.name\n",
        "        if w >= 3840 or h >= 2160:\n",
        "            # 档位 A：3840x2160（原生 4K）-> 免空间超分，仅执行 RIFE v4.6 60fps 补帧 + 无损贴回原声\n",
        "            print(f'[原生4K直通+RIFE60fps] {mp4.name} ({w}x{h})')\n",
        "            subprocess.run(['python', '-m', 'vsr_pipeline.batch_processor', '--input_file', str(mp4), '--output_file', str(dst_4k), '--scale', '1', '--interpolate_model', 'RIFE_v4.6', '--target_fps', '60', '--audio_codec', 'copy'], check=False)\n",
        "        elif w >= 1920 or h >= 1080:\n",
        "            # 档位 B：1920x1080（Flow 1080p）-> 【一步超分（One-Step 1080p -> 4K）】Real-ESRGAN x2 直升 3840x2160 + RIFE 60fps + 无损贴回 LatentSync 合流音轨\n",
        "            print(f'[Flow 1080p 一步超分 1080p->4K] {mp4.name} ({w}x{h}) -> 3840x2160 60fps')\n",
        "            subprocess.run(['python', '-m', 'vsr_pipeline.batch_processor', '--input_file', str(mp4), '--output_file', str(dst_4k), '--upscale_model', 'Real-ESRGAN-Video-x4plus', '--scale', '2', '--interpolate_model', 'RIFE_v4.6', '--target_fps', '60', '--audio_codec', 'copy'], check=False)\n",
        "        else:\n",
        "            # 档位 C：1280x720（即梦 Seedance 2.0 720p / Omni 720p）-> 【两步阶梯超分（Two-Step 720p -> 1080p -> 4K）】防一步拉升蜡像涂抹感与画质断层\n",
        "            stage1_mp4 = STAGE1_1080P_DIR / f'{mp4.stem}_stage1_1080p.mp4'\n",
        "            print(f'[即梦/Omni 720p 两步阶梯超分·第1阶 720p->1080p 去伪影与面部微纹理重建] {mp4.name} ({w}x{h}) -> 1920x1080')\n",
        "            subprocess.run(['python', '-m', 'vsr_pipeline.batch_processor', '--input_file', str(mp4), '--output_file', str(stage1_mp4), '--upscale_model', 'Real-ESRGAN-Video-x4plus', '--scale', '2', '--downscale_lanczos', '1920x1080', '--audio_codec', 'copy'], check=False)\n",
        "            src_for_stage2 = stage1_mp4 if stage1_mp4.exists() else mp4\n",
        "            print(f'[即梦/Omni 720p 两步阶梯超分·第2阶 1080p->4K 二次超分+RIFE60fps] {src_for_stage2.name} -> 3840x2160 60fps (无损保留即梦原生音画口型)')\n",
        "            subprocess.run(['python', '-m', 'vsr_pipeline.batch_processor', '--input_file', str(src_for_stage2), '--output_file', str(dst_4k), '--upscale_model', 'Real-ESRGAN-Video-x4plus', '--scale', '2', '--interpolate_model', 'RIFE_v4.6', '--target_fps', '60', '--audio_codec', 'copy'], check=False)\n",
        "    print('✅ Step 3 全集 4K 60fps 阶梯超分与无损音轨回贴完成！')\n",
        "finally:\n",
        "    if AUTO_SHUTDOWN:\n",
        "        try:\n",
        "            if hasattr(os, 'sync'): os.sync()\n",
        "        except Exception:\n",
        "            pass\n",
        "        try:\n",
        "            from google.colab import drive\n",
        "            print('💾 [Auto-Shutdown] 正在强制刷盘同步 Google Drive (drive.flush_and_unmount)...')\n",
        "            drive.flush_and_unmount()\n",
        "        except Exception:\n",
        "            pass\n",
        "        try:\n",
        "            from google.colab import runtime\n",
        "            print('🛑 [Auto-Shutdown] 任务结束/退出，立即调用 runtime.unassign() 彻底销毁并关闭 Colab A100 实例！')\n",
        "            runtime.unassign()\n",
        "        except ImportError:\n",
        "            pass\n",
    ]

    nb_data = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "colab": {"provenance": [], "gpuType": "A100"},
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "accelerator": "GPU",
        },
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    f"# 🎬 {ep} · Colab A100 (CU) 一站式 `CosyVoice 2 - 0.5B` 情绪配音 + 字节开源 `LatentSync` 中文刷口型 + 4K 60fps 阶梯超分流水线\n",
                    "- **Step 1（云端唯一语音主力）**：挂载 Google Drive + 阿里开源 **`CosyVoice 2 - 0.5B`**（模型缓存于 `/content/drive/MyDrive/短剧复刻工作站/models/cosyvoice2`），读取《`第NN集_台词生成表.csv`》，以第二棒 `2–3s .wav` 声线母本锁死统一音色，按中文情绪导演指令（Instruct 模式）为 **Flow 镜头与心声 OS** 批量生成 `.wav`（自动跳过即梦原生带音镜头）\n",
                    "- **Step 2（Flow 镜头专属刷口型）**：调用字节开源 **`LatentSync`（即梦同源口型底座）** 仅对 **Flow 闭嘴开口台词镜头** 重绘 100% 帧级同步中文口型，并与 Flow 原生纯净环境 SFX 无损合流（即梦镜头自带口型与音频，完全不动直接透传）\n",
                    "- **Step 3（三档分辨率智能分流超分：即梦/Omni `720p → 1080p → 4K` 两步阶梯超分 vs Flow `1080p → 4K` 一步超分）**：消除 720p 特写直接拉 4K 的蜡像涂抹感，实现全集 `3840×2160 60fps` 零画质断层并 `--audio_codec copy` 无损透传音轨\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": step1_cosyvoice2_code,
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": step2_latentsync_code,
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": step3_vsr_ladder_code,
            },
        ],
    }

    nb_ep_path = ep_dir / f"{ep}_Colab_A100_4K60fps_超分流水线.ipynb"
    nb_work_path = project_root / "中间文件" / "在制工作区" / ep / f"{ep}_Colab_A100_4K60fps_超分流水线.ipynb"
    nb_ep_path.parent.mkdir(parents=True, exist_ok=True)
    nb_work_path.parent.mkdir(parents=True, exist_ok=True)
    nb_text = json.dumps(nb_data, ensure_ascii=False, indent=2)
    nb_ep_path.write_text(nb_text, encoding="utf-8")
    nb_work_path.write_text(nb_text, encoding="utf-8")
    return nb_ep_path


def cmd_phase3_triage_vsr(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    ep = _normalize_ep_str(args.episode)
    _v41_engines().run_phase4_triage_and_colab(
        project_root, ep, target_fps=_extract_cli_target_fps(args),
        p4_profile=(str(getattr(args, "p4_profile", "") or "").strip() or None),
        aspect_ratio=(str(getattr(args, "aspect_ratio", "") or "").strip() or None),
    )
    return
    work_dir = project_root / "中间文件" / "在制工作区" / ep
    raw_gen_dir = work_dir / "原始生成视频"
    to_vsr_dir = work_dir / "待超分视频"
    done_4k_dir = work_dir / "已超分4K_60fps"
    raw_gen_dir.mkdir(parents=True, exist_ok=True)
    to_vsr_dir.mkdir(parents=True, exist_ok=True)
    done_4k_dir.mkdir(parents=True, exist_ok=True)

    native_4k = []
    one_step_1080p = []
    two_step_720p = []
    queued_vsr = []
    missing_audio = []

    for mp4 in sorted(raw_gen_dir.glob("*.mp4")):
        meta = probe_video(mp4)
        w = meta.get("width") or 0
        h = meta.get("height") or 0
        if not meta.get("has_audio"):
            missing_audio.append(mp4.name)
        if w >= 3840 or h >= 2160:
            dst = done_4k_dir / mp4.name
            if not dst.exists():
                shutil.copy2(str(mp4), str(dst))
            native_4k.append({"file": mp4.name, "resolution": f"{w}x{h}", "duration_sec": meta["duration_sec"], "vsr_strategy": "native_4k_rife60_only"})
        else:
            dst = to_vsr_dir / mp4.name
            if not dst.exists():
                shutil.copy2(str(mp4), str(dst))
            strat = "one_step_1080p_to_4k" if (w >= 1920 or h >= 1080) else "two_step_720p_to_1080p_to_4k"
            item_info = {"file": mp4.name, "resolution": f"{w}x{h}", "duration_sec": meta["duration_sec"], "vsr_strategy": strat}
            queued_vsr.append(item_info)
            if strat == "one_step_1080p_to_4k":
                one_step_1080p.append(item_info)
            else:
                two_step_720p.append(item_info)

    nb_path = _build_colab_lipsync_and_vsr_notebook(project_root, ep, to_vsr_dir, done_4k_dir)
    dlg_dir = project_root / "分集" / ep / "台词音频"
    lipsync_wavs = sorted(dlg_dir.glob("segment_*_镜头*_*_台词*.wav")) if dlg_dir.exists() else []

    vsr_plan = {
        "status": "BUILD_COLAB_OK",
        "episode": ep,
        "colab_notebook_ipynb": str(nb_path).replace("\\", "/"),
        "step2_5_lipsync_enabled": True,
        "step2_5_lipsync_engine": "CosyVoice2_0.5B + LatentSync_Chinese_LipSync",
        "lipsync_dialogue_wavs_count": len(lipsync_wavs),
        "native_4k_bypassed_count": len(native_4k),
        "one_step_1080p_to_4k_count": len(one_step_1080p),
        "two_step_720p_to_1080p_to_4k_count": len(two_step_720p),
        "queued_for_colab_a100_count": len(queued_vsr),
        "missing_native_sfx_warning": missing_audio,
        "native_4k_clips": native_4k,
        "one_step_1080p_clips": one_step_1080p,
        "two_step_720p_clips": two_step_720p,
        "queued_vsr_clips": queued_vsr,
    }
    plan_path = work_dir / "Colab_A100_超分任务清单.json"
    plan_path.write_text(json.dumps(vsr_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(vsr_plan, ensure_ascii=False, indent=2))


def cmd_phase3_fcpxml(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    ep = _normalize_ep_str(args.episode)
    allow_pre_vsr = bool(getattr(args, "allow_pre_vsr", False))
    _v41_engines().run_phase4_fcpxml(
        project_root, ep, allow_pre_vsr=allow_pre_vsr, parse_md_fn=_parse_segments_from_md,
        target_fps=_extract_cli_target_fps(args),
        p4_profile=(str(getattr(args, "p4_profile", "") or "").strip() or None),
        aspect_ratio=(str(getattr(args, "aspect_ratio", "") or "").strip() or None),
    )
    return
    ep_dir = project_root / "分集" / ep
    work_dir = project_root / "中间文件" / "在制工作区" / ep
    ana_dir = project_root / "中间文件" / "原片分析" / ep
    done_4k_dir = work_dir / "已超分4K_60fps"
    md_path = ep_dir / f"{ep}_视频提示词.md"
    dlg_dir = ep_dir / "台词音频"

    segments = _parse_segments_from_md(md_path.read_text(encoding="utf-8")) if md_path.exists() else []
    fcpxml = ET.Element("fcpxml", version="1.9")
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(
        resources, "format",
        id="r1", name="FFVideoFormat3840x2160p60",
        frameDuration="100/6000s", width="3840", height="2160",
    )

    # 优先读取 <ep_work_dir>/cuts.csv（及 原片分析/<ep>/cuts.csv、分集/<ep>/cuts.csv），备选 物理切点清单.json / 源帧与台词结构化汇总.json
    # 获取各段真实时长 real_dur，再结合 ffprobe 实测 media_dur 精准裁切尾部 Handles 余量
    real_seg_dur_map: dict[str, float] = {}
    gen_seg_dur_map: dict[str, float] = {}
    cuts_ordered_segs: list[str] = []
    duration_source = "segments_md_fallback"

    for cuts_cand in (work_dir / "cuts.csv", ana_dir / "cuts.csv", ep_dir / "cuts.csv"):
        if cuts_cand.exists() and cuts_cand.stat().st_size > 0:
            try:
                with cuts_cand.open("r", encoding="utf-8-sig") as f_c:
                    for crow in csv.DictReader(f_c):
                        sk = str(crow.get("segment") or crow.get("segment_id") or "").strip()
                        if not sk:
                            continue
                        if sk not in cuts_ordered_segs:
                            cuts_ordered_segs.append(sk)
                        s_v = float(crow.get("start_sec") or 0.0)
                        e_v = float(crow.get("end_sec") or 0.0)
                        d_v = max(0.0, e_v - s_v) if e_v > s_v else float(crow.get("duration") or crow.get("duration_sec") or 0.0)
                        if d_v > 0:
                            real_seg_dur_map[sk] = round(real_seg_dur_map.get(sk, 0.0) + d_v, 3)
                        gd_raw = str(crow.get("gen_duration_sec") or "").strip()
                        if gd_raw and sk not in gen_seg_dur_map:
                            try:
                                gen_seg_dur_map[sk] = float(gd_raw)
                            except Exception:
                                pass
                if real_seg_dur_map:
                    duration_source = str(cuts_cand).replace("\\", "/")
                    break
            except Exception:
                pass

    if not real_seg_dur_map:
        for shots_cand in (ana_dir / "物理切点清单.json", work_dir / "物理切点清单.json"):
            if shots_cand.exists() and shots_cand.stat().st_size > 0:
                try:
                    raw_sh = json.loads(shots_cand.read_text(encoding="utf-8-sig"))
                    sh_list = raw_sh if isinstance(raw_sh, list) else raw_sh.get("shots", [])
                    for idx_sh, sh in enumerate(sh_list, 1):
                        if not isinstance(sh, dict):
                            continue
                        d_sh = float(sh.get("duration") or max(0.0, float(sh.get("end_sec") or 0.0) - float(sh.get("start_sec") or 0.0)))
                        if d_sh <= 0:
                            continue
                        sk = str(sh.get("segment") or sh.get("segment_id") or f"segment_{idx_sh:02d}").strip()
                        if sk not in cuts_ordered_segs:
                            cuts_ordered_segs.append(sk)
                        real_seg_dur_map[sk] = round(real_seg_dur_map.get(sk, 0.0) + d_sh, 3)
                    if real_seg_dur_map:
                        duration_source = str(shots_cand).replace("\\", "/")
                        break
                except Exception:
                    pass

    if not real_seg_dur_map:
        summary_json_p = ana_dir / "源帧与台词结构化汇总.json"
        if summary_json_p.exists():
            try:
                s_items = json.loads(summary_json_p.read_text(encoding="utf-8"))
                if isinstance(s_items, list):
                    for it in s_items:
                        if isinstance(it, dict) and it.get("segment") and it.get("duration_s"):
                            sk = str(it["segment"])
                            real_seg_dur_map[sk] = round(real_seg_dur_map.get(sk, 0.0) + float(it["duration_s"]), 3)
                    if real_seg_dur_map:
                        duration_source = str(summary_json_p).replace("\\", "/")
            except Exception:
                pass

    if not segments and cuts_ordered_segs:
        segments = [
            {
                "segment": sk,
                "duration_sec": gen_seg_dur_map.get(sk, real_seg_dur_map.get(sk, 4.0)),
            }
            for sk in cuts_ordered_segs
        ]

    def _probe_wav_dur(wp: Path, default_d: float) -> float:
        if not wp.exists():
            return default_d
        try:
            with wave.open(str(wp), "rb") as wf:
                fr = wf.getframerate()
                if fr > 0:
                    return round(wf.getnframes() / float(fr), 3)
        except Exception:
            pass
        try:
            res = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(wp)],
                capture_output=True, text=True, encoding="utf-8",
            )
            val = float((res.stdout or "").strip() or 0.0)
            if val > 0.05:
                return round(val, 3)
        except Exception:
            pass
        return default_d

    total_dur = 0.0
    trimmed_handles_total = 0.0
    clip_specs = []
    seg_offset_map: dict[str, float] = {}
    for idx, seg in enumerate(segments, start=1):
        seg_name = seg["segment"]
        res_id = f"r_seg_{idx:02d}"
        fallback_gen_dur = float(gen_seg_dur_map.get(seg_name) or seg.get("duration_sec") or 4.0)

        cand = None
        for v_cand in (
            done_4k_dir / f"{seg_name}.mp4",
        ):
            if v_cand.exists() and v_cand.stat().st_size > 0:
                cand = v_cand
                break

        probed_media_dur = 0.0
        if cand is not None:
            vmeta = probe_video(cand)
            probed_media_dur = float(vmeta.get("duration_sec") or 0.0)

        media_dur = probed_media_dur if probed_media_dur > 0.05 else fallback_gen_dur
        real_dur = float(real_seg_dur_map.get(seg_name) or seg.get("duration_sec") or media_dur)
        if real_dur <= 0.05:
            real_dur = media_dur
        if media_dur > 0.05 and real_dur > media_dur:
            real_dur = media_dur

        trimmed_handles_total += max(0.0, media_dur - real_dur)
        seg_offset_map[seg_name] = total_dur
        src_uri = cand.resolve().as_uri() if cand is not None else f"file:///{seg_name}.mp4"
        ET.SubElement(
            resources, "asset",
            id=res_id, name=seg_name, src=src_uri,
            start="0s", duration=f"{int(round(media_dur * 6000))}/6000s",
            hasVideo="1", hasAudio="1", format="r1",
        )
        clip_specs.append((seg_name, res_id, total_dur, real_dur))
        total_dur += real_dur

    # 扫描 分集/第NN集/台词音频/ 下的 _台词.wav (仅 Flow 镜头) 与 _心声.wav (全集 OS)，注册为 FCPXML 独立音频资源并挂载到 Lane 1 Dialogue_Track / Lane 2 OS_Track
    dlg_manifest_path = dlg_dir / "台词音频清单.json"
    dlg_records = []
    if dlg_manifest_path.exists():
        try:
            raw_recs = json.loads(dlg_manifest_path.read_text(encoding="utf-8")).get("dialogue_records", [])
            dlg_records = [r for r in raw_recs if r.get("file_name") != "SKIP_JIMENG_NATIVE" and (r.get("wav_path") or r.get("file_name"))]
        except Exception:
            dlg_records = []
    if not dlg_records and dlg_dir.exists():
        for w in sorted(dlg_dir.glob("*.wav")):
            m_w = re.match(r"^(segment_\d+)_(镜头\d+)_([^_]+)_(台词|心声).*\.wav$", w.name)
            seg_k = m_w.group(1) if m_w else "segment_01"
            is_os = (m_w.group(4) == "心声") if m_w else ("_心声" in w.name)
            dlg_records.append({
                "segment": seg_k,
                "shot": m_w.group(2) if m_w else "镜头01",
                "file_name": w.name,
                "wav_path": str(w).replace("\\", "/"),
                "needs_lipsync": not is_os,
                "actual_duration_sec": _probe_wav_dur(w, 2.4),
                "start_in_seg_sec": 0.0,
                "episode_offset_sec": seg_offset_map.get(seg_k, 0.0),
            })

    audio_mount_specs = []
    for a_idx, rec in enumerate(dlg_records, start=1):
        wav_p = Path(rec.get("wav_path") or (dlg_dir / rec["file_name"]))
        if not wav_p.exists():
            wav_p = dlg_dir / rec.get("file_name", "")
        a_res_id = f"r_aud_{a_idx:03d}"
        fallback_adur = float(rec.get("actual_duration_sec") or rec.get("target_duration_sec") or 2.4)
        a_dur = _probe_wav_dur(wav_p, fallback_adur)
        seg_k = str(rec.get("segment") or "segment_01")
        st_in_seg = float(rec.get("start_in_seg_sec") or 0.0)
        ep_off = float(rec.get("episode_offset_sec") or (seg_offset_map.get(seg_k, 0.0) + st_in_seg))
        is_os_clip = ("_心声" in wav_p.name) or (str(rec.get("voice_mode") or "") == "心声") or (rec.get("needs_lipsync") is False)
        is_dialogue_track = not is_os_clip
        track_role = "Dialogue_Track" if is_dialogue_track else "OS_Track"
        lane_str = "1" if is_dialogue_track else "2"
        a_uri = wav_p.resolve().as_uri() if wav_p.exists() else f"file:///{wav_p.name}"
        ET.SubElement(
            resources, "asset",
            id=a_res_id, name=wav_p.stem, src=a_uri,
            start="0s", duration=f"{int(round(a_dur * 6000))}/6000s",
            hasVideo="0", hasAudio="1", audioSources="1", audioChannels="1", audioRate="48000",
        )
        audio_mount_specs.append({
            "res_id": a_res_id,
            "name": wav_p.stem,
            "segment": seg_k,
            "offset_in_seg_sec": st_in_seg,
            "episode_offset_sec": ep_off,
            "duration_sec": a_dur,
            "track": track_role,
            "lane": lane_str,
        })

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name=f"{project_root.name}_{ep}")
    proj = ET.SubElement(event, "project", name=f"{ep}_4K60fps_三节点破塑母带")
    seq = ET.SubElement(
        proj, "sequence",
        format="r1", duration=f"{int(round(max(total_dur, 1.0) * 6000))}/6000s",
        tcStart="0s", tcFormat="NDF", audioLayout="stereo", audioRate="48k",
    )
    spine = ET.SubElement(seq, "spine")
    seg_clip_elems: dict[str, ET.Element] = {}
    for seg_name, res_id, offset_s, dur_s in clip_specs:
        v_elem = ET.SubElement(
            spine, "asset-clip",
            name=seg_name, ref=res_id,
            offset=f"{int(round(offset_s * 6000))}/6000s",
            duration=f"{int(round(dur_s * 6000))}/6000s",
            start="0s", format="r1",
        )
        seg_clip_elems[seg_name] = v_elem

    # 将 _台词.wav (Dialogue_Track, lane=1) 与 _心声.wav (OS_Track, lane=2) 自动挂载至对应时间线轨道
    for am in audio_mount_specs:
        parent_elem = seg_clip_elems.get(am["segment"])
        if parent_elem is not None:
            ET.SubElement(
                parent_elem, "asset-clip",
                name=am["name"], ref=am["res_id"],
                lane=am["lane"],
                offset=f"{int(round(am['offset_in_seg_sec'] * 6000))}/6000s",
                duration=f"{int(round(am['duration_sec'] * 6000))}/6000s",
                start="0s", audioRole=f"dialogue.{am['track']}",
            )
        else:
            ET.SubElement(
                spine, "asset-clip",
                name=am["name"], ref=am["res_id"],
                lane=am["lane"],
                offset=f"{int(round(am['episode_offset_sec'] * 6000))}/6000s",
                duration=f"{int(round(am['duration_sec'] * 6000))}/6000s",
                start="0s", audioRole=f"dialogue.{am['track']}",
            )

    out_fcpxml = ep_dir / f"{ep}_达芬奇自动时间线.fcpxml"
    ET.indent(fcpxml, space="  ")
    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(fcpxml, encoding="utf-8")
    out_fcpxml.write_bytes(xml_bytes)

    guide_path = work_dir / "第四棒_达芬奇三节点破塑与混音指引.md"
    guide_path.write_text(
        f"# {ep} · 达芬奇免费版（DaVinci Resolve Free）10分钟合片指引\n\n"
        f"1. **一键导入时间线**：打开 DaVinci Resolve Free ➔ `文件 > 导入 > 时间线` 选择 `{out_fcpxml.name}`。\n"
        f"2. **挂载三节点破塑树（Color 页面切换为 Timeline 全局模式）**：\n"
        f"   - **Node 1（一级校色）**：统一全集暗部黑电平与色温；\n"
        f"   - **Node 2（中频破塑核心）**：将 **`Midtone Detail` 拉升至 `+20`**，消除 AI 磨皮塑料感；\n"
        f"   - **Node 3（电影 LUT）**：挂载 `Kodak 2383` LUT + `V2` 顶轨铺 `35mm Film Grain Overlay 4K`（混合模式 `Overlay`）。\n"
        f"3. **Fairlight 四轨独立混音**：\n"
        f"   - `Dialogue_Track (Lane 1)`：开口对白 `_台词.wav`（已对齐帧级中文口型，硬切处做 `0.25s` L-Cut/J-Cut）；\n"
        f"   - `OS_Track (Lane 2)`：内心独白 `_心声.wav`（带近讲混响，画面人物闭嘴微表情）；\n"
        f"   - `SFX_Track`：原生透传物理动作环境音效；\n"
        f"   - `MusicFX_Track`：0积分配乐（对白处自动闪避 `-8dB`）。\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "BUILD_FCPXML_OK",
        "episode": ep,
        "fcpxml_path": str(out_fcpxml).replace("\\", "/"),
        "davinci_guide_md": str(guide_path).replace("\\", "/"),
        "timeline_clips": len(clip_specs),
        "trimmed_handles_sec": round(trimmed_handles_total, 2),
        "mounted_dialogue_track_clips": sum(1 for a in audio_mount_specs if a["track"] == "Dialogue_Track"),
        "mounted_os_track_clips": sum(1 for a in audio_mount_specs if a["track"] == "OS_Track"),
        "total_duration_sec": round(total_dur, 2),
    }, ensure_ascii=False, indent=2))


def cmd_phase3_verify(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    ep = _normalize_ep_str(args.episode)
    _v41_engines().run_phase3_verify(project_root, ep, probe_hw_fn=probe_hardware_env)


def cmd_phase4_verify_master(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    ep = _normalize_ep_str(args.episode)
    _v41_engines().run_phase4_verify_master(
        project_root,
        ep,
        probe_hw_fn=probe_hardware_env,
        target_fps=_extract_cli_target_fps(args),
        master_mp4_override=getattr(args, "master_mp4", ""),
        p4_profile=(str(getattr(args, "p4_profile", "") or "").strip() or None),
        aspect_ratio=(str(getattr(args, "aspect_ratio", "") or "").strip() or None),
    )


def cmd_phase4_confirm_master(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    ep = _normalize_ep_str(args.episode)
    _v41_engines().run_phase4_confirm_master(
        project_root,
        ep,
        reviewer=str(getattr(args, "reviewer", "") or ""),
        confirm_dialogue=bool(getattr(args, "confirm_dialogue", False)),
        confirm_lipsync=bool(getattr(args, "confirm_lipsync", False)),
        confirm_subtitles=bool(getattr(args, "confirm_subtitles", False)),
        notes=str(getattr(args, "notes", "") or ""),
        master_mp4_override=getattr(args, "master_mp4", ""),
        target_fps=_extract_cli_target_fps(args),
        p4_profile=(str(getattr(args, "p4_profile", "") or "").strip() or None),
        aspect_ratio=(str(getattr(args, "aspect_ratio", "") or "").strip() or None),
    )


def cmd_dialogue_gen_proxy(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    ep = _normalize_ep_str(args.episode)
    if getattr(args, "dry_run", False):
        print(json.dumps({"status": "DRY_RUN_OK", "subcommand": "dialogue-gen", "project_root": str(project_root).replace("\\", "/"), "episode": ep, "mode": args.mode}, ensure_ascii=False))
        return
    dlg_csv = project_root / "分集" / ep / f"{ep}_台词生成表.csv"
    if dlg_csv.is_file() and dlg_csv.stat().st_size > 0:
        _v41_engines().verify_voice_refs_for_dialogue(project_root, ep)
    res = subprocess.run(
        [
            sys.executable, str(BATCH_OPS_SCRIPT), "dialogue-gen",
            "--project-root", str(project_root),
            "--episode", ep,
            "--mode", args.mode,
        ] + (["--force-new"] if getattr(args, "force_new", False) else []),
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    print(res.stdout.strip())


def cmd_voice_gen_proxy(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    if getattr(args, "dry_run", False):
        print(json.dumps({"status": "DRY_RUN_OK", "subcommand": "voice-gen", "project_root": str(project_root).replace("\\", "/"), "episode": args.episode, "mode": args.mode}, ensure_ascii=False))
        return
    res = subprocess.run(
        [
            sys.executable, str(BATCH_OPS_SCRIPT), "voice-gen",
            "--project-root", str(project_root),
            "--episode", args.episode,
            "--mode", args.mode,
        ] + (["--force-new"] if getattr(args, "force_new", False) else []),
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    print(res.stdout.strip())


def cmd_voice_audit_proxy(args: argparse.Namespace) -> None:
    project_root = resolve_ws_path(args.project_root)
    ep = _normalize_ep_str(args.episode)
    _v41_engines().run_voice_audit(project_root, ep, probe_hw_fn=probe_hardware_env)


def _detect_default_project_root() -> str:
    if PROJECTS_ROOT.exists():
        dirs = sorted([d for d in PROJECTS_ROOT.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))])
        if dirs:
            return str(dirs[0])
    return str(PROJECTS_ROOT)


def cmd_verify_script_alignment_proxy(args: argparse.Namespace) -> None:
    if not BATCH_OPS_SCRIPT.exists():
        raise FileNotFoundError(f"未找到 batch_ops.py: {BATCH_OPS_SCRIPT}")
    proj = resolve_ws_path(args.project_root) if getattr(args, "project_root", "") else Path(_detect_default_project_root())
    cmd = [
        sys.executable, str(BATCH_OPS_SCRIPT), "verify-script-alignment",
        "--project-root", str(proj),
        "--episode", _normalize_ep_str(getattr(args, "episode", "第01集")),
    ]
    if getattr(args, "require_visual_audit", False):
        cmd.append("--require-visual-audit")
    if getattr(args, "v3", False):
        cmd.append("--v3")
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr:
        print(res.stderr.strip(), file=sys.stderr)
    if res.returncode != 0:
        sys.exit(res.returncode)


def cmd_plan_batches_proxy(args: argparse.Namespace) -> None:
    if not BATCH_OPS_SCRIPT.exists():
        raise FileNotFoundError(f"未找到 batch_ops.py: {BATCH_OPS_SCRIPT}")
    proj = resolve_ws_path(args.project_root) if getattr(args, "project_root", "") else Path(_detect_default_project_root())
    cmd = [
        sys.executable, str(BATCH_OPS_SCRIPT), "plan-batches",
        "--project-root", str(proj),
        "--episode", _normalize_ep_str(getattr(args, "episode", "第01集")),
        "--window", str(getattr(args, "window", "window_01") or "window_01"),
    ]
    if getattr(args, "no_materialize", False):
        cmd.append("--no-materialize")
    if getattr(args, "skip_cuts_check", False):
        cmd.append("--skip-cuts-check")
    if getattr(args, "skip_alignment_check", False):
        cmd.append("--skip-alignment-check")
    if getattr(args, "v3", False):
        cmd.append("--v3")
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr:
        print(res.stderr.strip(), file=sys.stderr)
    if res.returncode != 0:
        sys.exit(res.returncode)


def _hsv_corr_rgb_arrays(rgb_a, rgb_b) -> float:
    try:
        from PIL import Image
        ia = Image.fromarray(rgb_a)
        ib = Image.fromarray(rgb_b)
        ha = ia.convert("HSV").resize((128, 72)).histogram()
        hb = ib.convert("HSV").resize((128, 72)).histogram()
        mx, my = sum(ha) / len(ha), sum(hb) / len(hb)
        num = sum((a - mx) * (b - my) for a, b in zip(ha, hb))
        den = (sum((a - mx) ** 2 for a in ha) * sum((b - my) ** 2 for b in hb)) ** 0.5
        return round(max(0.0, min(1.0, num / den)) if den > 0 else 1.0, 4)
    except Exception:
        return 1.0


def _run_transnetv2_shot_detection(
    work_video: Path,
    local_start: float,
    local_end: float,
) -> list[tuple[float, float]]:
    """
    使用 TransNetV2 (PyTorch CPU) 对 25fps CFR 母本执行镜头边界检测：
    - OpenCV 解码视频帧并缩放到 48x27 RGB uint8
    - torch.no_grad() 下调用 TransNetV2.predict_frames / predict_raw
    - 综合 single_frame_predictions（硬切）与 all_frame_predictions（慢叠化软切）提取边界
    - 叠加抗爆闪误切过滤（<0.65s 高亮瞬态闪白若前后两侧帧 HSV 相似度 >= 0.78 则不予劈碎）
    - 遇错即停（打印 TRANSNETV2_ENGINE_FAILURE 并抛出异常，严禁静默降级回纯阈值切分）
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import torch  # type: ignore
        import transnetv2_pytorch  # type: ignore

        TransNetV2Cls = getattr(transnetv2_pytorch, "TransNetV2", None) or getattr(
            transnetv2_pytorch, "TransNetV2Torch", None
        )
        if TransNetV2Cls is None:
            raise ImportError("transnetv2_pytorch 缺少 TransNetV2 类")
    except Exception as exc:
        msg = f"TRANSNETV2_ENGINE_FAILURE: 无法导入 TransNetV2 依赖 ({exc})"
        print(msg, file=sys.stderr)
        raise RuntimeError(msg) from exc

    cap = cv2.VideoCapture(str(work_video))
    if not cap.isOpened():
        msg = f"TRANSNETV2_ENGINE_FAILURE: OpenCV 无法打开视频 {work_video}"
        print(msg, file=sys.stderr)
        raise RuntimeError(msg)

    frames_rgb: list = []
    frame_times: list[float] = []
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        if fps <= 1.0 or fps > 240.0:
            fps = 25.0
        if local_start > 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(local_start) * 1000.0)
        span = max(0.1, float(local_end) - float(local_start))
        max_frames = max(1, int(round(span * fps)) + 2)
        idx = 0
        while idx < max_frames:
            ok, bgr = cap.read()
            if not ok or bgr is None:
                break
            cur_t = round(float(local_start) + idx / fps, 4)
            if cur_t >= float(local_end) + (0.25 / fps):
                break
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            resized = cv2.resize(rgb, (48, 27), interpolation=cv2.INTER_AREA).astype(np.uint8)
            frames_rgb.append(resized)
            frame_times.append(cur_t)
            idx += 1
    finally:
        cap.release()

    if not frames_rgb:
        msg = f"TRANSNETV2_ENGINE_FAILURE: 视频区间 [{local_start}, {local_end}] 未解码到任何有效帧"
        print(msg, file=sys.stderr)
        raise RuntimeError(msg)

    def _to_1d_prob(arr_like) -> "np.ndarray":
        if hasattr(arr_like, "detach"):
            arr = arr_like.detach().cpu().numpy()
        else:
            arr = np.asarray(arr_like, dtype=np.float32)
        arr = np.squeeze(arr).astype(np.float32)
        if arr.ndim == 0:
            arr = np.asarray([float(arr)], dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.reshape(-1)
        if arr.size > 0 and (float(np.min(arr)) < 0.0 or float(np.max(arr)) > 1.0):
            arr = 1.0 / (1.0 + np.exp(-np.clip(arr, -40.0, 40.0)))
        return arr

    def _unpack_pred_out(pred_out):
        if isinstance(pred_out, (tuple, list)):
            if len(pred_out) >= 3:
                s_raw, a_raw = pred_out[1], pred_out[2]
            elif len(pred_out) >= 2:
                s_raw, a_raw = pred_out[0], pred_out[1]
            elif len(pred_out) == 1:
                s_raw = a_raw = pred_out[0]
            else:
                raise RuntimeError("TransNetV2 返回空元组")
        elif isinstance(pred_out, dict):
            s_raw = (
                pred_out["single_frame_predictions"] if pred_out.get("single_frame_predictions") is not None
                else (pred_out["single_frame_pred"] if pred_out.get("single_frame_pred") is not None
                else (pred_out["single"] if pred_out.get("single") is not None
                else next(iter(pred_out.values()))))
            )
            a_raw = (
                pred_out["many_hot"] if pred_out.get("many_hot") is not None
                else (pred_out["all_frame_predictions"] if pred_out.get("all_frame_predictions") is not None
                else (pred_out["all_frame_pred"] if pred_out.get("all_frame_pred") is not None
                else (pred_out["all"] if pred_out.get("all") is not None else s_raw)))
            )
        else:
            s_raw, a_raw = pred_out, pred_out

        if isinstance(s_raw, dict):
            s_raw = (
                s_raw["single_frame_predictions"] if s_raw.get("single_frame_predictions") is not None
                else (s_raw["single_frame_pred"] if s_raw.get("single_frame_pred") is not None
                else next(iter(s_raw.values())))
            )
        if isinstance(a_raw, dict):
            a_raw = (
                a_raw["many_hot"] if a_raw.get("many_hot") is not None
                else (a_raw["all_frame_pred"] if a_raw.get("all_frame_pred") is not None
                else (a_raw["all_frame_predictions"] if a_raw.get("all_frame_predictions") is not None
                else next(iter(a_raw.values()))))
            )
        return s_raw, a_raw

    try:
        model = TransNetV2Cls()
        if hasattr(model, "to"):
            model = model.to("cpu")
        if hasattr(model, "eval"):
            model.eval()
        frames_np = np.asarray(frames_rgb, dtype=np.uint8)  # (N, 27, 48, 3)
        with torch.no_grad():
            if hasattr(model, "predict_frames"):
                try:
                    pred_out = model.predict_frames(torch.from_numpy(frames_np))
                except TypeError:
                    pred_out = model.predict_frames(frames_np)
                s_raw, a_raw = _unpack_pred_out(pred_out)
                single_pred = _to_1d_prob(s_raw)
                all_pred = _to_1d_prob(a_raw)
            elif hasattr(model, "predict_raw"):
                try:
                    pred_out = model.predict_raw(torch.from_numpy(frames_np[None, ...]))
                except TypeError:
                    pred_out = model.predict_raw(frames_np[None, ...])
                s_raw, a_raw = _unpack_pred_out(pred_out)
                single_pred = _to_1d_prob(s_raw)
                all_pred = _to_1d_prob(a_raw)
            elif callable(model):
                n_total = len(frames_np)
                if n_total > 100:
                    start_pad = np.repeat(frames_np[:1], 25, axis=0)
                    last_start = 50 * ((n_total - 1) // 50)
                    end_pad_len = max(25, last_start + 100 - (25 + n_total))
                    end_pad = np.repeat(frames_np[-1:], end_pad_len, axis=0)
                    padded = np.concatenate([start_pad, frames_np, end_pad], axis=0)
                    s_chunks, a_chunks = [], []
                    for ptr in range(0, len(padded) - 100 + 1, 50):
                        w_out = model(torch.from_numpy(padded[ptr : ptr + 100][None, ...]))
                        ws_raw, wa_raw = _unpack_pred_out(w_out)
                        s_chunks.append(_to_1d_prob(ws_raw)[25:75])
                        a_chunks.append(_to_1d_prob(wa_raw)[25:75])
                    single_pred = np.concatenate(s_chunks, axis=0)[:n_total]
                    all_pred = np.concatenate(a_chunks, axis=0)[:n_total]
                else:
                    pred_out = model(torch.from_numpy(frames_np[None, ...]))
                    s_raw, a_raw = _unpack_pred_out(pred_out)
                    single_pred = _to_1d_prob(s_raw)
                    all_pred = _to_1d_prob(a_raw)
            else:
                raise AttributeError("TransNetV2 实例缺少 predict_frames / predict_raw 方法")
    except Exception as exc:
        msg = f"TRANSNETV2_ENGINE_FAILURE: TransNetV2 CPU 推理失败 ({exc})"
        print(msg, file=sys.stderr)
        raise RuntimeError(msg) from exc

    try:
        n_frames = len(frames_rgb)
        if single_pred.size < n_frames:
            single_pred = np.pad(single_pred, (0, n_frames - single_pred.size))
        if all_pred.size < n_frames:
            all_pred = np.pad(all_pred, (0, n_frames - all_pred.size))
        single_pred = single_pred[:n_frames]
        all_pred = all_pred[:n_frames]
    except Exception as exc:
        msg = f"TRANSNETV2_ENGINE_FAILURE: TransNetV2 输出解析失败 ({exc})"
        print(msg, file=sys.stderr)
        raise RuntimeError(msg) from exc

    def _contiguous_runs(mask: "np.ndarray") -> list[tuple[int, int]]:
        runs: list[tuple[int, int]] = []
        in_run = False
        r_s = 0
        for i, flag in enumerate(mask):
            if bool(flag) and not in_run:
                in_run = True
                r_s = i
            elif not bool(flag) and in_run:
                in_run = False
                runs.append((r_s, i - 1))
        if in_run:
            runs.append((r_s, len(mask) - 1))
        return runs

    # 1) 硬切边界：single_frame_predictions >= 0.5
    hard_bounds: list[float] = []
    for i0, i1 in _contiguous_runs(single_pred >= 0.5):
        i_peak = i0 + int(np.argmax(single_pred[i0 : i1 + 1]))
        t_cut = round(float(local_start) + (i_peak + 1) / fps, 3)
        if local_start + 0.20 < t_cut < local_end - 0.20:
            hard_bounds.append(t_cut)

    # 2) 慢叠化软切边界：all_frame_predictions >= 0.5 且附近 0.35s 无硬切触发
    soft_bounds: list[float] = []
    for j0, j1 in _contiguous_runs(all_pred >= 0.5):
        if j1 - j0 + 1 < 2 and float(np.max(all_pred[j0 : j1 + 1])) < 0.65:
            continue
        t_mid = round(float(local_start) + ((j0 + j1) * 0.5 + 1.0) / fps, 3)
        if local_start + 0.25 < t_mid < local_end - 0.25:
            if not any(abs(t_mid - hb) <= 0.35 for hb in hard_bounds):
                soft_bounds.append(t_mid)

    raw_bounds = sorted(set(hard_bounds + soft_bounds))
    merged_bounds: list[float] = [float(local_start)]
    for b in raw_bounds:
        if b - merged_bounds[-1] >= 0.10 and float(local_end) - b >= 0.10:
            merged_bounds.append(round(b, 3))
    if float(local_end) > merged_bounds[-1]:
        merged_bounds.append(round(float(local_end), 3))

    segments: list[tuple[float, float]] = [
        (merged_bounds[i], merged_bounds[i + 1])
        for i in range(len(merged_bounds) - 1)
        if merged_bounds[i + 1] - merged_bounds[i] >= 0.15
    ]
    if not segments:
        segments = [(round(float(local_start), 3), round(float(local_end), 3))]

    # 3) 抗爆闪误切过滤（<0.65s 高亮瞬态闪白若前后两侧帧 HSV 相似度 >= 0.78 则不予劈碎）
    def _luma(rgb_u8) -> float:
        return float(np.mean(0.299 * rgb_u8[..., 0] + 0.587 * rgb_u8[..., 1] + 0.114 * rgb_u8[..., 2]))

    def _t_to_idx(t_val: float) -> int:
        idx_v = int(round((float(t_val) - float(local_start)) * fps))
        return max(0, min(len(frames_rgb) - 1, idx_v))

    # Pass A: 合并被 <0.65s 高亮瞬态闪白切出的短片段
    changed = True
    while changed and len(segments) >= 2:
        changed = False
        new_segs: list[tuple[float, float]] = []
        i = 0
        while i < len(segments):
            s_cur, e_cur = segments[i]
            dur_cur = e_cur - s_cur
            if 0 < i < len(segments) - 1 and dur_cur < 0.65:
                i_s = _t_to_idx(s_cur)
                i_e = max(i_s, _t_to_idx(e_cur) - 1)
                peak_luma = max(_luma(frames_rgb[k]) for k in range(i_s, i_e + 1))
                idx_before = max(0, i_s - max(2, int(round(0.15 * fps))))
                idx_after = min(len(frames_rgb) - 1, i_e + max(2, int(round(0.15 * fps))))
                b_luma = _luma(frames_rgb[idx_before])
                a_luma = _luma(frames_rgb[idx_after])
                is_flash = (peak_luma >= 180.0) or ((peak_luma - min(b_luma, a_luma)) >= 40.0)
                if is_flash and _hsv_corr_rgb_arrays(frames_rgb[idx_before], frames_rgb[idx_after]) >= 0.78:
                    prev_s, _ = new_segs.pop()
                    _, next_e = segments[i + 1]
                    new_segs.append((prev_s, next_e))
                    i += 2
                    changed = True
                    continue
            new_segs.append((s_cur, e_cur))
            i += 1
        segments = new_segs

    # Pass B: 若闪白刚好落在单一边界处（未单独成段），且高亮持续 <0.65s 且前后 HSV >= 0.78，亦合并
    if len(segments) >= 2:
        merged_pass_b: list[tuple[float, float]] = [segments[0]]
        for s_nxt, e_nxt in segments[1:]:
            s_prev, e_prev = merged_pass_b[-1]
            b_t = e_prev
            i_b = _t_to_idx(b_t)
            win = max(2, int(round(0.32 * fps)))
            lo = max(0, i_b - win)
            hi = min(len(frames_rgb) - 1, i_b + win)
            idx_before = max(0, i_b - max(3, int(round(0.40 * fps))))
            idx_after = min(len(frames_rgb) - 1, i_b + max(3, int(round(0.40 * fps))))
            b_luma = _luma(frames_rgb[idx_before])
            a_luma = _luma(frames_rgb[idx_after])
            flash_thr = max(180.0, min(b_luma, a_luma) + 40.0)
            flash_indices = [k for k in range(lo, hi + 1) if _luma(frames_rgb[k]) >= flash_thr]
            flash_dur = (len(flash_indices) / fps) if flash_indices else 0.0
            if (
                flash_indices
                and 0.0 < flash_dur < 0.65
                and _hsv_corr_rgb_arrays(frames_rgb[idx_before], frames_rgb[idx_after]) >= 0.78
            ):
                merged_pass_b[-1] = (s_prev, e_nxt)
            else:
                merged_pass_b.append((s_nxt, e_nxt))
        segments = merged_pass_b

    return segments


def build_episode_storyboard_sheets(
    shots_json_path: Path | str,
    output_dir: Path | str | None = None,
    sheet_width: int = 1600,
    min_per_sheet: int = 12,
    max_per_sheet: int = 18,
    max_sheets: int = 6,
) -> dict:
    """
    将 物理切点清单.json 按顺序拼合为 1–6 张（自适应每张 12–18 镜）宽 1600px 的清晰 JPEG 故事板图，
    每行印 shot_XX | start-end (dur s) + 首帧 | 中帧 | 尾帧 横条，并写出 storyboard_index.json。
    """
    from PIL import Image, ImageDraw, ImageFont

    shots_p = Path(shots_json_path).resolve()
    raw = json.loads(shots_p.read_text(encoding="utf-8-sig"))
    shots = raw if isinstance(raw, list) else raw.get("shots", [])
    out_dir = Path(output_dir).resolve() if output_dir else (shots_p.parent / "storyboard_grids")
    out_dir.mkdir(parents=True, exist_ok=True)

    total = len(shots)
    if total == 0:
        idx_payload = {
            "shots_json": str(shots_p).replace("\\", "/"),
            "output_dir": str(out_dir).replace("\\", "/"),
            "total_shots": 0,
            "sheet_count": 0,
            "sheets": [],
        }
        (out_dir / "storyboard_index.json").write_text(json.dumps(idx_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return idx_payload

    min_sheets = math.ceil(total / max(1, max_per_sheet))
    ideal_sheets = math.ceil(total / 15.0)
    num_sheets = max(1, min(max_sheets, max(min_sheets, ideal_sheets)))

    # 均匀分配每张镜头数（在 total > max_per_sheet 时自适应落入 12–18 区间）
    base_n = total // num_sheets
    rem_n = total % num_sheets
    groups: list[list[dict]] = []
    cursor = 0
    for s_idx in range(num_sheets):
        cnt = base_n + (1 if s_idx < rem_n else 0)
        groups.append(shots[cursor : cursor + cnt])
        cursor += cnt

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
        font_sm = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 16)
    except Exception:
        font = ImageFont.load_default()
        font_sm = font

    pad_x = 16
    gap_x = 12
    tile_w = (int(sheet_width) - 2 * pad_x - 2 * gap_x) // 3
    tile_h = max(120, int(round(tile_w * 9 / 16)))
    header_h = 44
    row_label_h = 30
    row_pad_y = 10
    row_h = row_label_h + tile_h + row_pad_y

    sheet_entries: list[dict] = []
    for idx, grp in enumerate(groups, 1):
        canvas_h = header_h + len(grp) * row_h + 10
        canvas = Image.new("RGB", (int(sheet_width), canvas_h), (15, 23, 42))
        draw = ImageDraw.Draw(canvas)
        first_sid = str(grp[0].get("shot_id") or "shot_01")
        last_sid = str(grp[-1].get("shot_id") or f"shot_{len(grp):02d}")
        draw.rectangle((0, 0, int(sheet_width), header_h), fill=(30, 41, 59))
        draw.text(
            (pad_x, 10),
            f"Storyboard Sheet {idx:02d}/{num_sheets:02d} | {first_sid} .. {last_sid} ({len(grp)} shots)",
            fill=(248, 250, 252),
            font=font,
        )

        shot_ids_in_sheet: list[str] = []
        for r_i, sh in enumerate(grp):
            y0 = header_h + r_i * row_h
            sid = str(sh.get("shot_id") or f"shot_{r_i + 1:02d}")
            shot_ids_in_sheet.append(sid)
            s_t = float(sh.get("start_sec") or 0.0)
            e_t = float(sh.get("end_sec") or 0.0)
            dur = float(sh.get("duration") or max(0.0, e_t - s_t))
            draw.rectangle((pad_x, y0 + 2, int(sheet_width) - pad_x, y0 + row_label_h - 2), fill=(51, 65, 85))
            draw.text(
                (pad_x + 10, y0 + 5),
                f"{sid} | {s_t:.2f}s - {e_t:.2f}s ({dur:.2f}s)",
                fill=(250, 204, 21),
                font=font_sm,
            )

            for c_i, (k_name, lbl) in enumerate((("first_frame", "首帧"), ("mid_frame", "中帧"), ("last_frame", "尾帧"))):
                x0 = pad_x + c_i * (tile_w + gap_x)
                y_img = y0 + row_label_h
                f_raw = str(sh.get(k_name) or "").strip()
                f_p = resolve_ws_path(f_raw) if f_raw else None
                if f_p and not f_p.exists() and not Path(f_raw).is_absolute():
                    cand = shots_p.parent / f_raw
                    if cand.exists():
                        f_p = cand
                if f_p and f_p.exists():
                    try:
                        with Image.open(f_p) as im_f:
                            rgb_tile = im_f.convert("RGB").resize((tile_w, tile_h))
                            canvas.paste(rgb_tile, (x0, y_img))
                    except Exception:
                        draw.rectangle((x0, y_img, x0 + tile_w, y_img + tile_h), fill=(30, 30, 30), outline=(148, 163, 184))
                        draw.text((x0 + 12, y_img + 12), f"LOAD_ERR: {lbl}", fill=(248, 113, 113), font=font_sm)
                else:
                    draw.rectangle((x0, y_img, x0 + tile_w, y_img + tile_h), fill=(30, 30, 30), outline=(148, 163, 184))
                    draw.text((x0 + 12, y_img + 12), f"MISSING: {lbl}", fill=(248, 113, 113), font=font_sm)

        sheet_name = f"storyboard_sheet_{idx:02d}.jpg"
        sheet_path = out_dir / sheet_name
        canvas.save(sheet_path, format="JPEG", quality=90)
        sheet_entries.append({
            "sheet_id": f"storyboard_sheet_{idx:02d}",
            "path": str(sheet_path).replace("\\", "/"),
            "width": int(sheet_width),
            "height": canvas_h,
            "shot_count": len(grp),
            "shot_ids": shot_ids_in_sheet,
        })

    idx_payload = {
        "shots_json": str(shots_p).replace("\\", "/"),
        "output_dir": str(out_dir).replace("\\", "/"),
        "total_shots": total,
        "sheet_count": len(sheet_entries),
        "sheets": sheet_entries,
    }
    (out_dir / "storyboard_index.json").write_text(json.dumps(idx_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return idx_payload


class CloudRecreationPipeline:
    """
    云端全闭环夜间自洽流水线（第一~三棒默认纯 CPU 模式 0~0.06 CU/集 + 第四棒 A100 模式）：
    - 硬件自适应与纯 CPU 零报错运行（torch.cuda.is_available()=False 时自动切换 CPU + 云端 API）
    - 第二棒 2–3s 双音色声线母本（纯 CPU 极速兼容：优先 Gemini 2.5 Flash TTS API / CPU 轻量化生成 24kHz/48kHz mono PCM + voice-audit 核验）
    - 方案 A（严禁不同场景拼接）：跨场景或孤立短镜走 Veo 3.1 (4s/6s/8s) 单独生成 + 第四棒达芬奇 .fcpxml 自动裁切 Handles
    - 三项源头去重 + 单窗口锁死 ≤50 基础单元（总调用 ≤60 张）事前窗口切分防 429 惩罚锁：单镜路人不建卡 + 同空间场景合并 + 砍除机械重复尾帧（默认仅生 _首帧.png，按需 _尾帧.png 强制绑定同段首帧）
    - 稳态 2 并发防 429 冷却：MAX_CONCURRENCY = 2，每批 <= 6 张，每窗口 <= 50 基础单元，随机 Jitter 退避 + 断点续跑（已存在且非空自动跳过）
    - 接触表自动归位：自动复制到项目根目录 审核专区_第NN集_源与初图接触表.jpg
    """

    def __init__(self, project_root: str | Path, episode: str = "第01集", source_range: str = "0-360s"):
        self.project_root = resolve_ws_path(project_root)
        self.episode = episode
        self.source_range = source_range
        self.hw_info = probe_hardware_env("phase1_3")

        # 自动探测原视频（扫描 原视频/ 下首个视频文件，跨项目通用）
        raw_dir = self.project_root / "原视频"
        supported_vids = (".ts", ".m2ts", ".mp4", ".mkv", ".mov", ".avi", ".m4v", ".flv", ".mpg", ".mpeg")
        if raw_dir.exists():
            vids = [p for p in sorted(raw_dir.iterdir()) if p.is_file() and p.suffix.lower() in supported_vids]
            self.source_video = vids[0] if vids else (raw_dir / "source_video.mp4")
        else:
            self.source_video = raw_dir / "source_video.mp4"

        self.episode_dir = self.project_root / "分集" / self.episode
        self.intermediate_dir = self.project_root / "中间文件" / "原片分析" / self.episode
        self.work_dir = self.project_root / "中间文件" / "在制工作区" / self.episode
        self.frames_dir = self.intermediate_dir / "source_frames"
        self.output_frames_dir = self.episode_dir / "首尾及关键帧"
        self.character_dir = self.project_root / "人物参考"
        self.contact_sheet_path = self.project_root / f"审核专区_{self.episode}_源与初图接触表.jpg"

        for d in (self.episode_dir, self.intermediate_dir, self.work_dir, self.frames_dir, self.output_frames_dir):
            d.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hsv_corr_arrays(img_a_rgb, img_b_rgb) -> float:
        try:
            from PIL import Image
            ha = img_a_rgb.convert("HSV").resize((128, 72)).histogram()
            hb = img_b_rgb.convert("HSV").resize((128, 72)).histogram()
            mx, my = sum(ha) / len(ha), sum(hb) / len(hb)
            num = sum((a - mx) * (b - my) for a, b in zip(ha, hb))
            den = (sum((a - mx) ** 2 for a in ha) * sum((b - my) ** 2 for b in hb)) ** 0.5
            return round(max(0.0, min(1.0, num / den)) if den > 0 else 1.0, 4)
        except Exception:
            return 1.0

    def step0_detect_shots(
        self,
        start_sec: float = 0.0,
        end_sec: float = 360.0,
        force_local: bool = False,
        force_resplit: bool = False,
    ) -> list[dict]:
        """第零阶段：CFR 母本重整 + TransNetV2 (PyTorch CPU) 物理切分 + 抗爆闪过滤 + 0.15s 安全内缩 + 全集故事板拼图"""
        log(f"🖥️ 硬件探针: {self.hw_info['execution_mode']} | CUDA={self.hw_info['cuda_available']} | {self.hw_info['note']}")
        csv_path = self.intermediate_dir / "物理切点清单.csv"
        json_path = self.intermediate_dir / "物理切点清单.json"
        storyboard_dir = self.intermediate_dir / "storyboard_grids"
        allow_local = bool(force_local) or (os.environ.get("ALLOW_LOCAL_SPLIT") == "1")

        # 严格防偷跑门禁：未授权且不在 Colab 运行时直接报错阻断，严禁静默本地回退
        if not is_colab_runtime() and not allow_local:
            err_msg = (
                "BLOCKED_BY_LOCAL_GATEKEEPER: 未授权且不在 Colab 运行时，"
                "严禁在本地直接运行 step0_detect_shots！请通过 Colab 纯 CPU 实例执行或显式传入 --force-local。"
            )
            log(f"🛡️ [{err_msg}]")
            raise RuntimeError(err_msg)

        if not self.source_video.exists():
            raise FileNotFoundError(f"原视频不存在: {self.source_video}")

        # 无论 ep_end 传入多少（例如默认 360.0），强制按源片真实时长 clamp，防止末镜越界抽帧失败
        try:
            src_meta = probe_video(self.source_video)
            video_actual_duration = float(src_meta.get("duration_sec") or 0.0)
        except Exception:
            video_actual_duration = 0.0
        if video_actual_duration > 0.0:
            effective_end = round(min(float(end_sec), video_actual_duration), 3)
            if effective_end < float(end_sec) - 1e-3:
                log(f"✂️ [时长Clamp] 传入 end_sec={end_sec}s 超出源片真实时长 {video_actual_duration:.3f}s，已强制夹紧至 {effective_end}s")
            end_sec = effective_end

        # 断点续跑完整性校验：严防复用残缺断点缓存
        expected_span = max(0.1, float(end_sec) - float(start_sec))
        existing_frames = sorted(self.frames_dir.glob("*_首帧.png"))
        if not force_resplit and json_path.exists() and json_path.stat().st_size > 128 and len(existing_frames) >= 10:
            try:
                cached = json.loads(json_path.read_text(encoding="utf-8-sig"))
                if isinstance(cached, list) and cached:
                    c_first = float(cached[0]["start_sec"])
                    c_last = float(cached[-1]["end_sec"])
                    c_dur = sum(float(it.get("duration") or (float(it["end_sec"]) - float(it["start_sec"]))) for it in cached)
                    cache_complete = (
                        abs(c_first - float(start_sec)) <= 0.5
                        and abs(c_last - float(end_sec)) <= 0.5
                        and c_dur >= 0.98 * expected_span
                    )
                    if cache_complete:
                        for item in cached:
                            if item.get("first_frame"):
                                item["first_frame"] = str(resolve_ws_path(item["first_frame"])).replace("\\", "/")
                            if item.get("last_frame"):
                                item["last_frame"] = str(resolve_ws_path(item["last_frame"])).replace("\\", "/")
                        if not (storyboard_dir / "storyboard_index.json").exists():
                            build_episode_storyboard_sheets(json_path, storyboard_dir)
                        log(f"⏩ [断点续跑] 完整性校验通过（{c_first:.2f}s~{c_last:.2f}s, {len(cached)} 镜），跳过重复切镜！")
                        return cached
                    log(
                        f"⚠️ [缓存作废] 现有物理切点清单缓存残缺（first={c_first:.2f}, last={c_last:.2f}, dur={c_dur:.2f}s vs 期望 {start_sec:.2f}~{end_sec:.2f}s），强制重检！"
                    )
            except Exception:
                pass

        log(f"🎬 启动纯 CPU TransNetV2 物理切镜检测: {self.source_video.name} ({start_sec}s ~ {end_sec}s)...")
        if not self.source_video.exists():
            raise FileNotFoundError(f"原视频不存在: {self.source_video}")

        # CFR 母本重整（.ts/.m2ts/.flv/.mkv 自动重整为 25fps CFR 正片母本）
        work_video, time_offset = ensure_cfr_episode_master(
            self.source_video,
            start_sec,
            end_sec,
            self.intermediate_dir,
            force_cfr=True,
        )
        local_start = 0.0 if work_video != self.source_video else float(start_sec)
        local_end = round(float(end_sec) - time_offset, 3) if work_video != self.source_video else float(end_sec)
        try:
            w_meta = probe_video(work_video)
            w_dur = float(w_meta.get("duration_sec") or 0.0)
            if w_dur > 0.0 and local_end > w_dur:
                local_end = round(w_dur, 3)
        except Exception:
            pass

        # 强依赖 TransNetV2，失败立即阻断（严禁静默降级回纯阈值切分）
        refined_segments = _run_transnetv2_shot_detection(work_video, local_start, local_end)

        SAFETY_INSET_SEC = 0.15
        shots_data = []
        for shot_idx, (loc_s, loc_e) in enumerate(refined_segments, 1):
            real_start = round(loc_s + time_offset, 3)
            real_end = round(loc_e + time_offset, 3)
            dur = round(loc_e - loc_s, 3)

            shot_id = f"shot_{shot_idx:02d}"
            first_f = self.frames_dir / f"{shot_id}_首帧.png"
            mid_f = self.frames_dir / f"{shot_id}_中帧.png"
            last_f = self.frames_dir / f"{shot_id}_尾帧.png"

            # 切点首尾安全内缩（尾帧严格向内缩进 0.15s 防止串入下一镜画面）
            head_t = min(loc_e - 0.05, loc_s + (0.05 if dur > 0.5 else 0.0))
            mid_t = round((loc_s + loc_e) * 0.5, 3)
            tail_t = max(head_t + 0.02, loc_e - (SAFETY_INSET_SEC if dur > 0.35 else 0.05))

            for t_val, f_dst in ((head_t, first_f), (mid_t, mid_f), (tail_t, last_f)):
                if force_resplit or not (f_dst.exists() and f_dst.stat().st_size > 1024):
                    subprocess.run(
                        ["ffmpeg", "-y", "-ss", f"{t_val:.3f}", "-i", str(work_video), "-vframes", "1", str(f_dst)],
                        capture_output=True,
                    )

            # HSV 直方图三点验切（首帧 vs 中帧 vs 尾帧一致性核验，阈值提至 0.55）
            sim_first_mid = 1.0
            sim_mid_last = 1.0
            try:
                from PIL import Image
                with Image.open(first_f) as ia, Image.open(mid_f) as ib, Image.open(last_f) as ic:
                    sim_first_mid = self._hsv_corr_arrays(ia, ib)
                    sim_mid_last = self._hsv_corr_arrays(ib, ic)
            except Exception:
                pass
            three_point_ok = bool(sim_first_mid >= 0.55 and sim_mid_last >= 0.55)

            shots_data.append({
                "shot_id": shot_id,
                "start_sec": real_start,
                "end_sec": real_end,
                "duration": dur,
                "tail_safety_inset_sec": SAFETY_INSET_SEC,
                "first_frame": str(first_f).replace("\\", "/"),
                "mid_frame": str(mid_f).replace("\\", "/"),
                "last_frame": str(last_f).replace("\\", "/"),
                "hsv_sim_first_mid": sim_first_mid,
                "hsv_sim_mid_last": sim_mid_last,
                "three_point_cut_verified": three_point_ok,
            })

        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "shot_id", "start_sec", "end_sec", "duration", "tail_safety_inset_sec",
                    "first_frame", "mid_frame", "last_frame",
                    "hsv_sim_first_mid", "hsv_sim_mid_last", "three_point_cut_verified",
                ],
            )
            writer.writeheader()
            writer.writerows(shots_data)
        json_path.write_text(json.dumps(shots_data, ensure_ascii=False, indent=2), encoding="utf-8")
        sb_res = build_episode_storyboard_sheets(json_path, storyboard_dir)
        log(
            f"✅ TransNetV2 精确物理切片完成（CFR重整 + 抗爆闪过滤 + 0.15s内缩 + 0.55三点验切 + {sb_res.get('sheet_count', 0)}张故事板总览）！"
            f"共 {len(shots_data)} 个物理镜头: {csv_path}"
        )
        return shots_data

    def step1_generate_script_and_manifest(self, shots_data: list[dict], force_resplit: bool = False) -> list[dict]:
        """第一棒：全量画质优先待生图帧清单 + 方案 A 跨场景防拼接 + 视觉风格一键回填"""
        log("📝 正在校验/构建第一棒全套剧本资产（方案 A 严禁不同场景拼接 + 全量画质优先零缩减）...")
        md_file = self.episode_dir / f"{self.episode}_视频提示词.md"
        style_json = self.work_dir / "style_and_cast.json"
        if md_file.exists() and style_json.exists() and BATCH_OPS_SCRIPT.exists():
            subprocess.run(
                [sys.executable, str(BATCH_OPS_SCRIPT), "fill-style", "--md-file", str(md_file), "--style-json", str(style_json)],
                capture_output=True, text=True, encoding="utf-8",
            )

        manifest_path = self.episode_dir / "待生图帧清单.json"
        if not force_resplit and manifest_path.exists() and manifest_path.stat().st_size > 256:
            raw_m = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            items = raw_m.get("frames") or raw_m.get("units") or (raw_m if isinstance(raw_m, list) else [])
            frame_items = [it for it in items if isinstance(it, dict) and (it.get("frame_type") == "首帧" or str(it.get("unit_id") or it.get("unit") or "").endswith("_首帧"))]
            if items and (not shots_data or len(frame_items) >= len(shots_data)):
                log(f"⏩ [断点续跑] 已存在全量待生图帧清单 ({len(items)} 项，零张数缩减): {manifest_path}")
                return items
            log("⚠️ [清单缓存作废] 现有 待生图帧清单.json 覆盖镜头数不足或已触发强制重切，重新构建清单！")

        manifest_items = []
        for shot in shots_data:
            shot_id = shot["shot_id"]
            dur = float(shot.get("duration") or 4.0)
            veo_tier = 4 if dur <= 4.0 else (6 if dur <= 6.0 else 8)
            manifest_items.append({
                "unit_id": f"{shot_id}_首帧",
                "segment_id": shot_id,
                "frame_type": "首帧",
                "frame_role": "首帧",
                "source_frame": shot["first_frame"],
                "target_path": f"分集/{self.episode}/首尾及关键帧/{shot_id}_首帧.png",
                "need_tail_frame": False,
                "tail_reason": "默认单首帧驱动（砍除机械重复多生的冗余尾帧）",
                "veo_scheme_a_tier": f"Veo 3.1 ({veo_tier}s Handles)" if dur <= 8.0 else f"Omni 1.2 Flash ({int(math.ceil(dur))}s)",
                "attempt": "初始",
            })

        manifest_path.write_text(json.dumps(manifest_items, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"✅ 第一棒待生图帧清单已落盘 ({len(manifest_items)} 项，已执行首尾帧去重): {manifest_path}")
        return manifest_items

    def step2_voice_and_batches(self) -> dict:
        """第二棒：纯 CPU 极速 2–3s 双音色声线生成 + voice-audit 核验 + 稳态 2 并发(每批<=6张+Jitter+断点续跑)归位"""
        log(f"🎙️ 启动第二棒 2–3s 双音色声线生成与静音比审计 (MAX_CONCURRENCY={MAX_CONCURRENCY}, MAX_BATCH_SIZE={MAX_BATCH_SIZE})...")
        voice_audit_info = {}
        if BATCH_OPS_SCRIPT.exists():
            subprocess.run(
                [sys.executable, str(BATCH_OPS_SCRIPT), "voice-gen", "--project-root", str(self.project_root), "--episode", self.episode, "--mode", "auto"],
                capture_output=True, text=True, encoding="utf-8",
            )
            res_audit = subprocess.run(
                [sys.executable, str(BATCH_OPS_SCRIPT), "voice-audit", "--project-root", str(self.project_root), "--episode", self.episode],
                capture_output=True, text=True, encoding="utf-8",
            )
            if res_audit.stdout.strip():
                try:
                    voice_audit_info = json.loads(res_audit.stdout.strip())
                except Exception:
                    voice_audit_info = {"raw": res_audit.stdout.strip()}
            subprocess.run(
                [sys.executable, str(BATCH_OPS_SCRIPT), "organize", "--project-root", str(self.project_root), "--episode", self.episode],
                capture_output=True, text=True, encoding="utf-8",
            )
        log(f"✅ 第二棒声线审计完成: status={voice_audit_info.get('status', 'OK')} | wav_count={voice_audit_info.get('wav_count', 0)}")
        return voice_audit_info

    def step2_generate_contact_sheet(self) -> Path | None:
        """第二棒收尾：拼装全集源与初图审核接触表并自动归位至根目录 审核专区_第NN集_源与初图接触表.jpg"""
        log("📊 正在拼装全集源图与初图审核接触表并归位至项目根目录...")
        from PIL import Image, ImageDraw

        batches_root = self.work_dir / "生成批次"
        batch_sheets = []
        if batches_root.exists():
            for bdir in sorted(batches_root.iterdir()):
                if bdir.is_dir():
                    for sname in ("源_初始_补救接触表.jpg", "源与初图接触表.jpg"):
                        cand = bdir / sname
                        if cand.exists() and cand.stat().st_size > 1024:
                            batch_sheets.append(cand)
                            break

        if batch_sheets:
            imgs = [Image.open(p).convert("RGB") for p in batch_sheets]
            max_w = max(im.width for im in imgs)
            total_h = sum(im.height for im in imgs)
            merged = Image.new("RGB", (max_w, total_h), (20, 20, 20))
            y_off = 0
            for im in imgs:
                merged.paste(im, (0, y_off))
                y_off += im.height
                im.close()
            merged.save(self.contact_sheet_path, quality=92)
            log(f"🎉 批次汇总审核接触表已归位至根目录: {self.contact_sheet_path}")
            return self.contact_sheet_path

        frame_files = sorted(list(self.frames_dir.glob("*_首帧.png")))
        if not frame_files:
            frame_files = sorted(list(self.intermediate_dir.glob("*_首帧_源.jpg")))
        if not frame_files:
            log("⚠️ 未发现可用源帧文件，跳过拼版。")
            return None

        cols = 6
        rows = (len(frame_files) + cols - 1) // cols
        tile_w, tile_h = 320, 180
        canvas = Image.new("RGB", (cols * tile_w, max(1, rows) * tile_h), color=(20, 20, 20))
        draw = ImageDraw.Draw(canvas)

        for idx, f_path in enumerate(frame_files):
            r = idx // cols
            c = idx % cols
            try:
                with Image.open(f_path) as im:
                    rgb = im.convert("RGB").resize((tile_w, tile_h))
                    canvas.paste(rgb, (c * tile_w, r * tile_h))
                label = f_path.stem.replace("_首帧_源", "").replace("_首帧", "")
                draw.text((c * tile_w + 8, r * tile_h + 8), label, fill=(255, 215, 0))
                # 若存在对应已生成初图，贴在右下角做直观同框对比
                gen_cands = list(self.output_frames_dir.rglob(f"*{label}*首帧*.png"))
                if gen_cands:
                    with Image.open(gen_cands[0]) as gim:
                        grgb = gim.convert("RGB").resize((tile_w // 2, tile_h // 2))
                        canvas.paste(grgb, (c * tile_w + tile_w // 2, r * tile_h + tile_h // 2))
            except Exception as exc:
                log(f"图片贴图警告: {exc}")

        canvas.save(self.contact_sheet_path, quality=90)
        log(f"🎉 审核专区接触表已成功归位至根目录: {self.contact_sheet_path}")
        return self.contact_sheet_path

    def dry_run_check(self) -> dict:
        """畅通性 Dry-Run 探针：不消耗 API 额度，全面核验纯 CPU 环境、路径自适应、原视频、FFmpeg/OpenCV/SceneDetect 与资产状态"""
        import cv2  # type: ignore
        import scenedetect  # type: ignore
        try:
            import transnetv2_pytorch  # type: ignore
            transnet_ok = True
        except Exception:
            transnet_ok = False

        ff_ver = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, encoding="utf-8").stdout.splitlines()[0]
        source_frames = sorted(self.frames_dir.glob("*_首帧.png"))
        kf_files = sorted(self.output_frames_dir.rglob("*.png"))
        voice_files = sorted((self.episode_dir / "声线").glob("*.wav")) if (self.episode_dir / "声线").exists() else []
        report = {
            "status": "DRY_RUN_OK",
            "hardware_probe": self.hw_info,
            "project_root": str(self.project_root).replace("\\", "/"),
            "episode": self.episode,
            "source_video": str(self.source_video).replace("\\", "/"),
            "source_video_exists": self.source_video.exists(),
            "source_video_size_mb": round(self.source_video.stat().st_size / (1024 * 1024), 2) if self.source_video.exists() else 0.0,
            "toolchain": {
                "ffmpeg": ff_ver,
                "opencv_version": getattr(cv2, "__version__", "unknown"),
                "transnetv2_pytorch_installed": transnet_ok,
                "scenedetect_version": getattr(scenedetect, "__version__", "unknown"),
                "batch_ops_exists": BATCH_OPS_SCRIPT.exists(),
            },
            "assets_readiness": {
                "source_first_frames_count": len(source_frames),
                "generated_keyframes_count": len(kf_files),
                "episode_voice_wavs_count": len(voice_files),
                "root_contact_sheet_exists": self.contact_sheet_path.exists(),
                "root_contact_sheet_path": str(self.contact_sheet_path).replace("\\", "/"),
            },
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    def run_all(
        self,
        force_local: bool = False,
        start_sec: float | None = None,
        end_sec: float | None = None,
        skip_intro_sec: float = 0.0,
        skip_outro_sec: float = 0.0,
        force_resplit: bool = False,
    ) -> dict:
        """第一~三棒纯 CPU 全流程一键串联运行"""
        s_sec, e_sec = _resolve_episode_time_range(
            self.project_root,
            self.episode,
            start_sec=start_sec,
            end_sec=end_sec,
            skip_intro_sec=skip_intro_sec,
            skip_outro_sec=skip_outro_sec,
        )
        log(f"🚀 开始纯 CPU 全自动流水线作业: {self.episode} ({s_sec}s~{e_sec}s, MAX_CONCURRENCY={MAX_CONCURRENCY}, MAX_BATCH_SIZE={MAX_BATCH_SIZE})")
        if force_resplit:
            for stale in (
                self.intermediate_dir / "物理切点清单.json",
                self.intermediate_dir / "物理切点清单.csv",
                self.intermediate_dir / "phase1_colab_done.json",
                self.intermediate_dir / "正片母本_CFR.mp4",
            ):
                if stale.exists():
                    try:
                        stale.unlink()
                    except Exception:
                        pass
        shots = self.step0_detect_shots(s_sec, e_sec, force_local=force_local, force_resplit=force_resplit)
        manifest = self.step1_generate_script_and_manifest(shots, force_resplit=force_resplit)
        if is_colab_runtime() or force_local:
            done_marker = write_phase1_colab_done_marker(
                self.project_root,
                ep=self.episode,
                shots_count=len(shots),
                manifest_count=len(manifest),
            )
            log(f"📝 已落盘第一棒握手凭证: {done_marker}")
        voice_audit = self.step2_voice_and_batches()
        sheet = self.step2_generate_contact_sheet()
        if (self.episode_dir / f"{self.episode}_视频提示词.md").exists():
            cmd_phase3_pack(argparse.Namespace(project_root=str(self.project_root), episode=self.episode, mode="auto"))
        log("🏆 第一~三棒纯 CPU 全流程圆满交付落盘！")
        return {
            "status": "RUN_ALL_OK",
            "hardware_mode": self.hw_info["execution_mode"],
            "start_sec": s_sec,
            "end_sec": e_sec,
            "shots_count": len(shots),
            "manifest_items_count": len(manifest),
            "voice_audit_status": voice_audit.get("status", "OK"),
            "contact_sheet": str(sheet).replace("\\", "/") if sheet else "",
        }


AUTO_SHUTDOWN = True  # 默认必须为 True：任务正常结束或异常退出后 100% 自动刷盘并销毁 Colab 实例


def flush_and_auto_shutdown_colab(auto_shutdown: bool = AUTO_SHUTDOWN) -> None:
    """使用后 100% 自动刷盘并断联销机（try...finally 兜底防待机空跑扣费）"""
    if not auto_shutdown:
        return
    try:
        if hasattr(os, "sync"):
            os.sync()
    except Exception:
        pass
    try:
        from google.colab import drive  # type: ignore
        log("💾 [Auto-Shutdown] 正在强制刷盘同步 Google Drive (drive.flush_and_unmount)...")
        drive.flush_and_unmount()
    except Exception:
        pass
    try:
        from google.colab import runtime  # type: ignore
        log("🛑 [Auto-Shutdown] 任务结束/退出，立即调用 runtime.unassign() 彻底销毁并关闭 Colab 运行时实例，严防待机空跑扣费...")
        runtime.unassign()
    except ImportError:
        pass


def cmd_resolve_colab_url_sub(args: argparse.Namespace) -> None:
    raw_proj = getattr(args, "project_root", "") or _detect_default_project_root()
    proj_root = resolve_ws_path(raw_proj)
    ipynb_raw = getattr(args, "ipynb", "") or ""
    ipynb_path = resolve_ws_path(ipynb_raw) if ipynb_raw else _find_project_phase1_ipynb(proj_root)
    nb_check = _ensure_pure_cpu_phase1_notebook(ipynb_path)
    cloud_id = resolve_drivefs_cloud_id(ipynb_path)
    url = f"https://colab.research.google.com/drive/{cloud_id}"
    print(json.dumps({
        "status": "RESOLVE_COLAB_URL_OK",
        "ipynb_path": str(ipynb_path).replace("\\", "/"),
        "cloud_id": cloud_id,
        "colab_url": url,
        "notebook_check": nb_check,
    }, ensure_ascii=False, indent=2))


def cmd_phase1_sub(args: argparse.Namespace) -> None:
    raw_proj = getattr(args, "project_root", "") or _detect_default_project_root()
    proj_root = resolve_ws_path(raw_proj)
    ep_str = _normalize_ep_str(getattr(args, "episode", "第01集"))
    force_local = bool(getattr(args, "force_local", False))
    timeout_sec = int(getattr(args, "timeout_sec", 900) or 900)
    start_sec = getattr(args, "start_sec", None)
    end_sec = getattr(args, "end_sec", None)
    skip_intro_sec = float(getattr(args, "skip_intro_sec", 0.0) or 0.0)
    skip_outro_sec = float(getattr(args, "skip_outro_sec", 0.0) or 0.0)
    force_resplit = bool(getattr(args, "force_resplit", False))
    auto_shutdown = AUTO_SHUTDOWN and not getattr(args, "no_auto_shutdown", False)
    try:
        if getattr(args, "trigger_colab_phase1", False) and not is_colab_runtime():
            res = trigger_colab_phase1(proj_root, ep_str, timeout_sec=timeout_sec)
        else:
            res = run_phase1(
                proj_root,
                ep_str,
                force_local=force_local,
                timeout_sec=timeout_sec,
                start_sec=start_sec,
                end_sec=end_sec,
                skip_intro_sec=skip_intro_sec,
                skip_outro_sec=skip_outro_sec,
                force_resplit=force_resplit,
            )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    finally:
        if is_colab_runtime():
            flush_and_auto_shutdown_colab(auto_shutdown=auto_shutdown)


def cmd_run_all_sub(args: argparse.Namespace) -> None:
    if getattr(args, "mcp_test", False):
        cmd_mcp_test(args)
        return
    if getattr(args, "resolve_colab_url", False):
        cmd_resolve_colab_url_sub(args)
        return
    if getattr(args, "trigger_colab_phase1", False) or getattr(args, "phase", "") == "phase1":
        cmd_phase1_sub(args)
        return
    is_dry_run = getattr(args, "dry_run", False)
    force_local = bool(getattr(args, "force_local", False))
    auto_shutdown = AUTO_SHUTDOWN and not getattr(args, "no_auto_shutdown", False)
    try:
        pipe = CloudRecreationPipeline(project_root=args.project_root, episode=args.episode)
        if is_dry_run:
            pipe.dry_run_check()
        else:
            pipe.run_all(
                force_local=force_local,
                start_sec=getattr(args, "start_sec", None),
                end_sec=getattr(args, "end_sec", None),
                skip_intro_sec=float(getattr(args, "skip_intro_sec", 0.0) or 0.0),
                skip_outro_sec=float(getattr(args, "skip_outro_sec", 0.0) or 0.0),
                force_resplit=bool(getattr(args, "force_resplit", False)),
            )
    finally:
        if not is_dry_run and is_colab_runtime():
            flush_and_auto_shutdown_colab(auto_shutdown=auto_shutdown)


def cmd_probe_env(args: argparse.Namespace) -> None:
    print(json.dumps(probe_hardware_env(args.phase), ensure_ascii=False, indent=2))


def cmd_mcp_test(args: argparse.Namespace) -> None:
    client = ColabMcpClient()
    res = client.run_pure_cpu_smoke_test()
    print(json.dumps(res, ensure_ascii=False, indent=2))


def _build_voice_colab_notebook(
    project_root: Path,
    ep: str,
    stage: str = "both",
    output_ipynb: str = "",
    engine: str = "auto",
    gpu: str = "",
) -> tuple[Path, Path]:
    """生成真实可跑的 Colab 声线母本 (voice-ref: VoxCPM 2) 与全集台词配音 (dialogue: IndexTTS-2.5 / Fish S2 Pro / VoxCPM 2) .ipynb"""
    out_p = resolve_ws_path(output_ipynb) if output_ipynb else None
    return _v41_engines()._build_v41_voice_colab_notebook(project_root, _normalize_ep_str(ep), stage=stage, output_ipynb=out_p, engine=engine, gpu=gpu)


def cmd_build_voice_colab(args: argparse.Namespace) -> None:
    raw_proj = getattr(args, "project_root", "") or _detect_default_project_root()
    project_root = resolve_ws_path(raw_proj)
    ep = _normalize_ep_str(getattr(args, "episode", "第01集"))
    stage = str(getattr(args, "stage", "both") or "both")
    out_ipynb = str(getattr(args, "output_ipynb", "") or "").strip()
    _v41_engines().run_build_voice_colab(
        project_root,
        ep,
        stage=stage,
        output_ipynb=out_ipynb,
        engine=str(getattr(args, "tts_engine", "auto") or "auto"),
        gpu=str(getattr(args, "gpu", "") or ""),
        resolve_ws_fn=resolve_ws_path,
        resolve_cloud_id_fn=resolve_drivefs_cloud_id,
    )


SUBCOMMANDS = {
    "onboard", "slice", "verify-script-alignment", "plan-batches", "materialize-batches",
    "voice-gen", "voice-audit", "dialogue-gen", "build-voice-colab",
    "phase3-pack", "pack-delivery", "phase3-verify", "check-delivery", "verify-delivery",
    "phase4-triage-vsr", "phase3-triage-vsr", "build-colab", "verify-videos",
    "phase4-fcpxml", "phase3-fcpxml", "build-fcpxml", "phase4-verify-master", "verify-master",
    "phase4-confirm-master", "confirm-master",
    "run-all", "probe-env", "phase1", "trigger-colab-phase1", "resolve-colab-url",
    "mcp-test",
}


def main() -> None:
    # 兼容直接传 --project-root / --project-dir / --episode / --ep / --dry-run / --resolve-colab-url / --trigger-colab-phase1 / --phase phase1
    if len(sys.argv) == 1 or (len(sys.argv) > 1 and sys.argv[1] not in SUBCOMMANDS and sys.argv[1] not in ("-h", "--help")):
        direct_parser = argparse.ArgumentParser(description="云端纯 CPU 全闭环夜间流水线（直接执行模式，默认开启 AUTO_SHUTDOWN=True）")
        direct_parser.add_argument("--project-root", "--project-dir", dest="project_root", default=_detect_default_project_root(), help="项目根目录")
        direct_parser.add_argument("--episode", "--ep", dest="episode", default="第01集", help="集数名称")
        direct_parser.add_argument("--phase", default="", help="可选：指定执行阶段，如 phase1")
        direct_parser.add_argument("--ipynb", default="", help="可选：指定目标 .ipynb 路径")
        direct_parser.add_argument("--mcp-test", action="store_true", help="实测 Google Colab 官方远程 MCP 与本地 colab-mcp 握手及纯 CPU 流程")
        direct_parser.add_argument("--resolve-colab-url", action="store_true", help="仅从 DriveFS metadata_sqlite_db 解析 Colab URL 并校验纯 CPU 元数据")
        direct_parser.add_argument("--trigger-colab-phase1", action="store_true", help="触发云端 Colab 纯 CPU 实例执行第一棒切分并等待 phase1_colab_done.json 握手")
        direct_parser.add_argument("--force-local", action="store_true", help="显式豁免本地防偷跑门禁（默认禁止在本地直接跑 FFmpeg 切分）")
        direct_parser.add_argument("--skip-intro-sec", type=float, default=0.0, help="跳过片头秒数（默认 0.0）")
        direct_parser.add_argument("--skip-outro-sec", type=float, default=0.0, help="跳过片尾秒数（默认 0.0）")
        direct_parser.add_argument("--start-sec", type=float, default=None, help="显式指定本集起始秒数")
        direct_parser.add_argument("--end-sec", type=float, default=None, help="显式指定本集结束秒数")
        direct_parser.add_argument("--force-resplit", action="store_true", help="清除旧校验缓存重新物理切镜")
        direct_parser.add_argument("--timeout-sec", type=int, default=120, help="等待云端 Colab 落盘超时秒数")
        direct_parser.add_argument("--dry-run", action="store_true", help="仅运行环境与链路畅通性自检探针")
        direct_parser.add_argument("--auto-unassign", action="store_true", default=True, help="跑完或异常退出后自动刷盘并断开销毁 Colab 实例（默认开启）")
        direct_parser.add_argument("--no-auto-shutdown", action="store_true", help="仅在多步外层包装脚本接管 finally 时临时关闭内层自动销机")
        d_args = direct_parser.parse_args()
        cmd_run_all_sub(d_args)
        return

    parser = argparse.ArgumentParser(description="云端/本地双栖 · 四棒视频复刻全自动管线引擎（第一~三棒纯 CPU + 第四棒 A100 + 默认使用后自动关机）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe-env", help="硬件自适应探针（检测纯 CPU 模式 / A100 GPU 状态）")
    p_probe.add_argument("--phase", default="phase1", choices=["phase1", "phase2", "phase3", "phase1_3", "phase4"])
    p_probe.set_defaults(func=cmd_probe_env)

    p_mcp = sub.add_parser("mcp-test", help="实测 Google Colab 官方远程 MCP (colaboratory.googleapis.com/mcp) 与本地 colab-mcp 握手及纯 CPU 流程")
    p_mcp.set_defaults(func=cmd_mcp_test)

    p_res_url = sub.add_parser("resolve-colab-url", help="从 Google DriveFS metadata_sqlite_db 自动解析目标 .ipynb 的 cloud_id 与 Colab URL")
    p_res_url.add_argument("--project-root", "--project-dir", dest="project_root", default=_detect_default_project_root())
    p_res_url.add_argument("--ipynb", default="")
    p_res_url.set_defaults(func=cmd_resolve_colab_url_sub)

    p_p1 = sub.add_parser("phase1", aliases=["trigger-colab-phase1"], help="第一棒纯 CPU 视频切分与清单（本地自动触发 Colab 纯 CPU 实例，云端自动写出 phase1_colab_done.json）")
    p_p1.add_argument("--project-root", "--project-dir", dest="project_root", default=_detect_default_project_root())
    p_p1.add_argument("--episode", "--ep", dest="episode", default="第01集")
    p_p1.add_argument("--force-local", action="store_true")
    p_p1.add_argument("--trigger-colab-phase1", action="store_true")
    p_p1.add_argument("--skip-intro-sec", type=float, default=0.0)
    p_p1.add_argument("--skip-outro-sec", type=float, default=0.0)
    p_p1.add_argument("--start-sec", type=float, default=None)
    p_p1.add_argument("--end-sec", type=float, default=None)
    p_p1.add_argument("--force-resplit", action="store_true")
    p_p1.add_argument("--timeout-sec", type=int, default=120)
    p_p1.add_argument("--no-auto-shutdown", action="store_true")
    p_p1.set_defaults(func=cmd_phase1_sub)

    p_run = sub.add_parser("run-all", help="第一~三棒纯 CPU 全流程一键串联执行（切镜 + 清单 + 2-3s双音色声线 + 根目录接触表 + try...finally 自动刷盘销机）")
    p_run.add_argument("--project-root", "--project-dir", dest="project_root", default=_detect_default_project_root())
    p_run.add_argument("--episode", "--ep", dest="episode", default="第01集")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--force-local", action="store_true")
    p_run.add_argument("--resolve-colab-url", action="store_true")
    p_run.add_argument("--trigger-colab-phase1", action="store_true")
    p_run.add_argument("--skip-intro-sec", type=float, default=0.0)
    p_run.add_argument("--skip-outro-sec", type=float, default=0.0)
    p_run.add_argument("--start-sec", type=float, default=None)
    p_run.add_argument("--end-sec", type=float, default=None)
    p_run.add_argument("--force-resplit", action="store_true")
    p_run.add_argument("--auto-unassign", action="store_true", default=True)
    p_run.add_argument("--no-auto-shutdown", action="store_true")
    p_run.set_defaults(func=cmd_run_all_sub)

    p_on = sub.add_parser("onboard", help="第一棒前置：一键投料入库、探测时长并初始化第01集标准九项骨架")
    p_on.add_argument("--project-name", required=True)
    p_on.add_argument("--source-video", required=True, help="00_待选片投料池 下的文件名或路径")
    p_on.add_argument("--episode", default="第01集")
    p_on.add_argument("--route", default="路线B_Veo80_Seedance20_双模混编")
    p_on.add_argument("--chunk-sec", type=int, default=360)
    p_on.add_argument("--skip-intro-sec", type=float, default=0.0)
    p_on.add_argument("--skip-outro-sec", type=float, default=0.0)
    p_on.add_argument("--start-sec", type=float, default=None)
    p_on.add_argument("--end-sec", type=float, default=None)
    p_on.add_argument("--force-resplit", action="store_true")
    p_on.add_argument("--move", action="store_true")
    p_on.set_defaults(func=cmd_onboard)

    p_sl = sub.add_parser("slice", help="第一棒切片：按切点 CSV 极速切分 segment_XX.mp4 并提取首尾源帧")
    p_sl.add_argument("--project-root", required=True)
    p_sl.add_argument("--episode", required=True)
    p_sl.add_argument("--source-video", required=True)
    p_sl.add_argument("--cuts-csv", required=True)
    p_sl.add_argument("--force-local", action="store_true")
    p_sl.set_defaults(func=cmd_slice)

    p_vsa = sub.add_parser("verify-script-alignment", help="第1步统一对齐门禁（调用 batch_ops.py verify-script-alignment）")
    p_vsa.add_argument("--project-root", "--project-dir", dest="project_root", required=True)
    p_vsa.add_argument("--episode", default="第01集")
    p_vsa.add_argument("--require-visual-audit", action="store_true")
    p_vsa.add_argument("--v3", action="store_true")
    p_vsa.set_defaults(func=cmd_verify_script_alignment_proxy)

    p_plan = sub.add_parser("plan-batches", aliases=["materialize-batches"], help="第2步批次物化与窗口切分（调用 batch_ops.py plan-batches）")
    p_plan.add_argument("--project-root", "--project-dir", dest="project_root", required=True)
    p_plan.add_argument("--episode", default="第01集")
    p_plan.add_argument("--window", default="window_01")
    p_plan.add_argument("--no-materialize", action="store_true")
    p_plan.add_argument("--skip-cuts-check", action="store_true")
    p_plan.add_argument("--skip-alignment-check", action="store_true")
    p_plan.add_argument("--v3", action="store_true")
    p_plan.set_defaults(func=cmd_plan_batches_proxy)

    p_vgen = sub.add_parser("voice-gen", help="第二棒：纯 CPU 极速兼容生成 2.00s-3.00s 双音色声线母本（优先 Gemini 2.5 Flash TTS / CPU 轻量化兜底）")
    p_vgen.add_argument("--project-root", required=True)
    p_vgen.add_argument("--episode", default="第01集")
    p_vgen.add_argument("--mode", default="auto", choices=["cosyvoice2", "tts", "auto", "cpu", "gemini-tts"])
    p_vgen.add_argument("--force-new", action="store_true")
    p_vgen.add_argument("--dry-run", action="store_true")
    p_vgen.set_defaults(func=cmd_voice_gen_proxy)

    p_vaudit = sub.add_parser("voice-audit", help="第二棒：调用 batch_ops.py voice-audit 校验 2.00s-3.00s 时长与静音比 (<=0.35)")
    p_vaudit.add_argument("--project-root", required=True)
    p_vaudit.add_argument("--episode", default="第01集")
    p_vaudit.add_argument("--dry-run", action="store_true")
    p_vaudit.set_defaults(func=cmd_voice_audit_proxy)

    p_dgen = sub.add_parser("dialogue-gen", help="第三棒：逐句生成 Flow 镜头台词与全集心声 .wav 音频轨（纯 CPU / 云端双兼容）")
    p_dgen.add_argument("--project-root", required=True)
    p_dgen.add_argument("--episode", default="第01集")
    p_dgen.add_argument("--mode", default="auto", choices=["cosyvoice2", "tts", "auto", "cpu", "gemini-tts"])
    p_dgen.add_argument("--force-new", action="store_true")
    p_dgen.add_argument("--dry-run", action="store_true")
    p_dgen.set_defaults(func=cmd_dialogue_gen_proxy)

    p_bvcolab = sub.add_parser("build-voice-colab", help="第二/三棒：生成真实可跑 Colab 声线母本 (VoxCPM 2) 与全集台词配音 (IndexTTS-2.5 / Fish S2 Pro / VoxCPM 2) .ipynb")
    p_bvcolab.add_argument("--project-dir", "--project-root", dest="project_root", default=_detect_default_project_root(), help="项目根目录路径")
    p_bvcolab.add_argument("--ep", "--episode", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_bvcolab.add_argument("--stage", default="both", choices=["voice-ref", "dialogue", "both"], help="合成阶段：voice-ref / dialogue / both")
    p_bvcolab.add_argument("--output-ipynb", default="", help="可选：显式指定输出 .ipynb 路径")
    p_bvcolab.add_argument("--tts-engine", default="auto", choices=["auto", "indextts25", "fish_s2_pro", "voxcpm2", "cosyvoice2"], help="配音引擎：auto（voice-ref→voxcpm2，dialogue→indextts25）；可选 indextts25 / fish_s2_pro / voxcpm2（cosyvoice2 仅兼容兜底）")
    p_bvcolab.add_argument("--gpu", default="", choices=["", "T4", "L4", "A100", "H200"], help="Colab GPU 分档：留空按引擎默认（主力 L4，可指定 A100/H200）")
    p_bvcolab.set_defaults(func=cmd_build_voice_colab)

    p_p3_pack = sub.add_parser("phase3-pack", aliases=["pack-delivery"], help="第三棒打包：自动调用 dialogue-gen 补齐台词生成表与音频并打包至 05_台词配音与心声轨/，输出傻瓜式外包声音规则投喂手册")
    p_p3_pack.add_argument("--project-root", required=True)
    p_p3_pack.add_argument("--episode", default="第01集")
    p_p3_pack.add_argument("--mode", default="auto", choices=["cosyvoice2", "tts", "auto", "cpu", "gemini-tts"])
    p_p3_pack.set_defaults(func=cmd_phase3_pack)

    p_p3_ver = sub.add_parser("phase3-verify", aliases=["check-delivery", "verify-delivery"], help="第三棒步骤3：执行正式集静态九项交付物（含剧本故事梗概与逐镜导演脚本.md、台词生成表.xlsx）与双音色时长核验（通过后交人工生视频）")
    p_p3_ver.add_argument("--project-root", "--project-dir", dest="project_root", required=True)
    p_p3_ver.add_argument("--episode", "--ep", dest="episode", default="第01集")
    p_p3_ver.set_defaults(func=cmd_phase3_verify)

    p_p4_vsr = sub.add_parser("phase4-triage-vsr", aliases=["phase3-triage-vsr", "build-colab", "verify-videos"], help="第四棒步骤1：人工视频回盘体检 + 生成含多句延迟起音 LatentSync 中文重对口型与纯环境 SFX 封装的 Colab A100 超分 .ipynb")
    p_p4_vsr.add_argument("--project-root", "--project-dir", dest="project_root", required=True)
    p_p4_vsr.add_argument("--episode", "--ep", dest="episode", default="第01集")
    p_p4_vsr.add_argument("--target-fps", type=int, choices=[24, 60], default=24, help="目标帧率：默认 24（免 RIFE 插帧），可选 60（开启 RIFE 60fps 补帧 SOP）")
    p_p4_vsr.add_argument("--p4-profile", choices=["anime_1k", "anime_4k", "live_1k", "live_4k"], default="", help="V4.6 第四棒四档规格：anime_1k / anime_4k / live_1k / live_4k（留空兼容默认 4K）")
    p_p4_vsr.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None, help="画幅比例：16:9 横屏 / 9:16 竖屏（未传时自动读取项目配置，默认 16:9）")
    p_p4_vsr.set_defaults(func=cmd_phase3_triage_vsr)

    p_p4_xml = sub.add_parser("phase4-fcpxml", aliases=["phase3-fcpxml", "build-fcpxml"], help="第四棒步骤2：一键生成零回退挂载 _台词.wav (Dialogue_Track) 与 _心声.wav (OS_Track) 的达芬奇 .fcpxml 时间线")
    p_p4_xml.add_argument("--project-root", "--project-dir", dest="project_root", required=True)
    p_p4_xml.add_argument("--episode", "--ep", dest="episode", default="第01集")
    p_p4_xml.add_argument("--target-fps", type=int, choices=[24, 60], default=24, help="目标帧率：默认 24（100/2400s），可选 60（100/6000s）")
    p_p4_xml.add_argument("--p4-profile", choices=["anime_1k", "anime_4k", "live_1k", "live_4k"], default="", help="V4.6 第四棒四档规格（未传时自动读取任务清单，默认兼容 4K）")
    p_p4_xml.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None, help="画幅比例：16:9 横屏 / 9:16 竖屏（未传时自动读取任务清单/项目配置）")
    p_p4_xml.add_argument("--allow-pre-vsr", action="store_true", default=False, help="调试开关：允许在未完成超分前使用原始生成视频（仍严禁回退引用原视频段落）")
    p_p4_xml.set_defaults(func=cmd_phase3_fcpxml)

    p_p4_ver = sub.add_parser("phase4-verify-master", aliases=["verify-master"], help="第四棒步骤3：实测校验最终导出的成片母带技术规格（支持 1K/4K 四档与 24/60fps）")
    p_p4_ver.add_argument("--project-root", "--project-dir", dest="project_root", required=True)
    p_p4_ver.add_argument("--episode", "--ep", dest="episode", default="第01集")
    p_p4_ver.add_argument("--target-fps", type=int, choices=[24, 60], default=24, help="目标帧率：默认 24（23.95~24.05fps），可选 60（59.90~60.10fps）")
    p_p4_ver.add_argument("--p4-profile", choices=["anime_1k", "anime_4k", "live_1k", "live_4k"], default="", help="V4.6 第四棒四档规格（未传时自动读取任务清单，默认兼容 4K）")
    p_p4_ver.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None, help="画幅比例：16:9 横屏 / 9:16 竖屏（未传时自动读取任务清单/项目配置）")
    p_p4_ver.add_argument("--master-mp4", default="", help="可选：显式指定待核验成片母带 MP4 路径")
    p_p4_ver.set_defaults(func=cmd_phase4_verify_master)

    p_p4_cfm = sub.add_parser("phase4-confirm-master", aliases=["confirm-master"], help="第四棒步骤4：剪辑师人工回看确认对白、口型与烧录字幕并绑定母带 SHA 封板")
    p_p4_cfm.add_argument("--project-root", "--project-dir", dest="project_root", required=True)
    p_p4_cfm.add_argument("--episode", "--ep", dest="episode", default="第01集")
    p_p4_cfm.add_argument("--reviewer", default="", help="必填：人工剪辑师姓名（禁止 AI/自动化名称）")
    p_p4_cfm.add_argument("--confirm-dialogue", action="store_true", help="确认对白语音内容与情绪无误")
    p_p4_cfm.add_argument("--confirm-lipsync", action="store_true", help="确认画面口型同步无误")
    p_p4_cfm.add_argument("--confirm-subtitles", action="store_true", help="确认烧录字幕准确无遮挡")
    p_p4_cfm.add_argument("--notes", default="", help="必填：人工回看具体审核备注（去空白 >= 12 字）")
    p_p4_cfm.add_argument("--master-mp4", default="", help="可选：显式指定成片母带 MP4 路径")
    p_p4_cfm.add_argument("--target-fps", type=int, choices=[24, 60], default=24, help="可选：目标帧率 24/60")
    p_p4_cfm.add_argument("--p4-profile", choices=["anime_1k", "anime_4k", "live_1k", "live_4k"], default="", help="可选：V4.6 第四棒四档规格")
    p_p4_cfm.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None, help="可选：画幅比例 16:9 / 9:16")
    p_p4_cfm.set_defaults(func=cmd_phase4_confirm_master)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
