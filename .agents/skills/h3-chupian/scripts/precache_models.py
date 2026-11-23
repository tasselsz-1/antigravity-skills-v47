# -*- coding: utf-8 -*-
"""h3出片云端模型预热脚本：在便宜的 Colab CPU 实例中，逐个流式拉取 Comfy-Org/MiniMax-H3 权重并校验后写入 Google Drive。

设计要点：
  - 使用 HF_XET_HIGH_PERFORMANCE=1 开启新版 Hugging Face Xet 满速传输。
  - 下载期间启动 15 秒进度心跳线程，实时输出已落盘体积，防止长连接静默超时。
  - 逐个文件 下载 -> rclone copyto 上传 -> 核对远端字节数 -> 删除本地临时文件，支持断点续跑。
  - 支持直接通过 `colab exec -s <sess> --timeout 0 -f precache_models.py` 运行（也支持 CLI 参数）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ID = "Comfy-Org/MiniMax-H3"

PROFILE_FILES: dict[str, list[str]] = {
    "h200-max": [
        "vae/minimax_h3_audio_vae_fp32.safetensors",
        "vae/minimax_h3_video_vae_fp16.safetensors",
        "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
        "diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors",
        "text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors",
    ],
    "h200-fast": [
        "vae/minimax_h3_audio_vae_fp32.safetensors",
        "vae/minimax_h3_video_vae_fp16.safetensors",
        "loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
        "diffusion_models/minimax_h3_ref2va_pruned_fp8_scaled.safetensors",
        "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
    ],
}


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd)
    if check and proc.returncode != 0:
        raise RuntimeError(f"命令失败 (rc={proc.returncode}): {' '.join(cmd)}")
    return proc


def remote_file_size(rclone_conf: str, remote_path: str) -> int | None:
    proc = subprocess.run(
        ["rclone", "--config", rclone_conf, "lsjson", "--stat", remote_path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        info = json.loads(proc.stdout)
        if not info.get("IsDir"):
            return int(info.get("Size", -1))
    except Exception:
        return None
    return None


def dir_bytes(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def download_with_heartbeat(repo_id: str, rel: str, local_dir: Path, exp_size: int, token: str | bool) -> None:
    from huggingface_hub import hf_hub_download

    stop_evt = threading.Event()
    t0 = time.time()

    def _beat() -> None:
        while not stop_evt.wait(15.0):
            cur = dir_bytes(local_dir)
            elapsed = int(time.time() - t0)
            pct = f" ({cur * 100.0 / exp_size:.1f}%)" if exp_size > 0 else ""
            print(f"   ⏳ 下载中 [{rel}]: {cur / 1e9:.2f}/{exp_size / 1e9:.2f} GB{pct} | 已耗时 {elapsed}s", flush=True)

    th = threading.Thread(target=_beat, daemon=True)
    th.start()
    try:
        hf_hub_download(
            repo_id=repo_id,
            filename=rel,
            local_dir=str(local_dir),
            token=token,
        )
    finally:
        stop_evt.set()
        th.join(timeout=2.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="流式预热 MiniMax-H3 权重到 Google Drive")
    parser.add_argument("--profile", choices=list(PROFILE_FILES.keys()), default=os.environ.get("H3_PROFILE", "h200-max"))
    parser.add_argument("--remote-root", default=os.environ.get("H3_REMOTE_ROOT", "gdrive:models/minimax_h3"))
    parser.add_argument("--rclone-config", default=os.environ.get("H3_RCLONE_CONF", "/tmp/rclone.conf"))
    args, _ = parser.parse_known_args()

    print("=== CPU 预热实例磁盘状态 ===", flush=True)
    subprocess.run(["df", "-h", "/content"], check=False)

    run_cmd([sys.executable, "-m", "pip", "install", "-q", "-U", "huggingface_hub", "hf_xet"])
    os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
    from huggingface_hub import HfApi

    tok = (os.environ.get("HF_TOKEN") or "").strip() or False
    api = HfApi(token=tok)
    repo_info = api.model_info(REPO_ID, files_metadata=True)
    expected_sizes = {s.rfilename: int(s.size or 0) for s in (repo_info.siblings or [])}

    stage_root = Path("/content/stage_dl")
    remote_root = args.remote_root.rstrip("/")
    files = PROFILE_FILES[args.profile]
    print(f"\n📦 计划预热档位 [{args.profile}] 共 {len(files)} 个文件 -> {remote_root}", flush=True)

    for idx, rel in enumerate(files, 1):
        exp_size = expected_sizes.get(rel, 0)
        remote_file = f"{remote_root}/{rel}"
        cur_remote = remote_file_size(args.rclone_config, remote_file)
        if cur_remote is not None and exp_size > 0 and cur_remote == exp_size:
            print(f"✅ [{idx}/{len(files)}] 云盘已就绪且大小一致，跳过: {rel} ({cur_remote / 1e9:.2f} GB)", flush=True)
            continue

        if stage_root.exists():
            shutil.rmtree(stage_root, ignore_errors=True)
        stage_root.mkdir(parents=True, exist_ok=True)

        free_bytes = shutil.disk_usage("/content").free
        if exp_size > 0 and free_bytes < exp_size + 5 * (1024**3):
            raise RuntimeError(
                f"磁盘空间不足以暂存 {rel}：需要 {(exp_size / 1e9):.2f} GB + 5GB余量，当前仅剩 {(free_bytes / 1e9):.2f} GB"
            )

        t0 = time.time()
        print(f"\n⬇️ [{idx}/{len(files)}] 下载 {rel} ({exp_size / 1e9:.2f} GB，当前余盘 {free_bytes / 1e9:.1f} GB) ...", flush=True)
        download_with_heartbeat(REPO_ID, rel, stage_root, exp_size, tok)

        local_file = stage_root / rel
        if not local_file.exists():
            raise RuntimeError(f"下载后找不到文件: {local_file}")
        actual_size = local_file.stat().st_size
        if exp_size > 0 and actual_size != exp_size:
            raise RuntimeError(f"下载字节数不匹配 {rel}: actual={actual_size}, expected={exp_size}")
        dl_sec = int(time.time() - t0)
        speed_mb = (actual_size / 1e6) / max(1, dl_sec)
        print(f"   下载完成 ({dl_sec}s, {actual_size / 1e9:.2f} GB, ~{speed_mb:.0f} MB/s)，开始传至 Google Drive ...", flush=True)

        t1 = time.time()
        run_cmd([
            "rclone", "--config", args.rclone_config, "copyto",
            str(local_file), remote_file,
            "--drive-chunk-size", "128M",
            "--stats", "15s", "--stats-one-line",
        ])

        verified = remote_file_size(args.rclone_config, remote_file)
        if verified != actual_size:
            raise RuntimeError(f"云盘校验失败 {remote_file}: remote={verified}, local={actual_size}")
        up_sec = int(time.time() - t1)
        print(f"✅ [{idx}/{len(files)}] 已校验写入 Google Drive ({up_sec}s): {remote_file} ({verified / 1e9:.2f} GB)", flush=True)

        # 立刻释放本地暂存盘
        shutil.rmtree(stage_root, ignore_errors=True)

    print("\n🎉 全套权重已校验预热至 Google Drive，列目录核对如下：", flush=True)
    run_cmd(["rclone", "--config", args.rclone_config, "ls", remote_root])


if __name__ == "__main__":
    main()
