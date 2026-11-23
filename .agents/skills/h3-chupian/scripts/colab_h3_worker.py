# -*- coding: utf-8 -*-
"""h3出片 Colab 云端执行引擎（支持 G4 96GB / H200 141GB / H100 80GB）。

核心能力：
  1) 打印硬件与磁盘状态，防爆盘硬检查；
  2) 自动安装/更新 ComfyUI + sageattention + hf_transfer；
  3) 按 manifest.json 精确拉取所需权重（零双份占盘）：
     - 优先命中 Google Drive rclone 精确单文件缓存（Google 内网极速直拉原位）；
     - 未命中则免密（或可选 HF_TOKEN）走 hf_transfer 直连 Comfy-Org/MiniMax-H3 下载，下完顺手回写 G 盘缓存复用；
     - 若传入本地挂载目录 --model-cache-dir，同盘自动软链、异盘单文件拷贝；
  4) 启动 ComfyUI（默认动态卸载管理，G4 96GB + 176GB RAM 稳跑无损 BF16；自动启用 --use-sage-attention）；
  5) batch 模式：逐段加载参考图/音频/故事板 -> 采样 -> 音画联合解码 -> ffprobe 校验 -> 写入 epNN/clips/；
  6) webui 模式：开启 Cloudflare Tunnel 输出 ComfyUI 网页地址供手动精调。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

COMFY_DIR = Path("/content/ComfyUI")
REPO_ID = "Comfy-Org/MiniMax-H3"


def run_cmd(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if check and proc.returncode != 0:
        raise RuntimeError(f"命令失败 (rc={proc.returncode}): {' '.join(cmd)}")
    return proc


def print_system_banner() -> None:
    print("=== Colab 云端硬件与磁盘状态 ===", flush=True)
    subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader"],
        check=False,
    )
    subprocess.run(["free", "-h"], check=False)
    subprocess.run(["df", "-h", "/content"], check=False)
    print("================================", flush=True)


def free_gb(path: Path = Path("/content")) -> float:
    return shutil.disk_usage(str(path)).free / (1024**3)


def ensure_comfyui(install_tunnel: bool = False) -> None:
    if not COMFY_DIR.exists():
        run_cmd(["git", "clone", "--depth", "1", "https://github.com/comfyanonymous/ComfyUI", str(COMFY_DIR)])
    else:
        run_cmd(["git", "-C", str(COMFY_DIR), "pull", "--ff-only"], check=False)

    run_cmd([sys.executable, "-m", "pip", "install", "-q", "-r", str(COMFY_DIR / "requirements.txt")])
    run_cmd([sys.executable, "-m", "pip", "install", "-q", "-U", "huggingface_hub", "hf_xet"])
    # SageAttention 在 G4 (Blackwell) / H200 / H100 上可提供显著加速；若无预编译轮子则平滑回退原生 Flash/SDPA
    sage = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "sageattention"])
    if sage.returncode != 0:
        print("⚠️ sageattention 安装失败，将回退至 PyTorch 原生 Flash/SDPA 注意力。", flush=True)

    if install_tunnel and not Path("/usr/local/bin/cloudflared").exists():
        run_cmd([
            "wget", "-q",
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
            "-O", "/usr/local/bin/cloudflared",
        ])
        run_cmd(["chmod", "+x", "/usr/local/bin/cloudflared"])


def rclone_remote_exists(rclone_conf: str, remote_file: str) -> bool:
    proc = subprocess.run(
        ["rclone", "--config", rclone_conf, "lsf", remote_file],
        capture_output=True, text=True,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _dir_bytes(root: Path) -> int:
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


def _copy_with_heartbeat(src: Path, dst: Path, label: str) -> None:
    total = src.stat().st_size
    done = 0
    t0 = time.time()
    last = t0
    tmp_dst = dst.with_suffix(dst.suffix + ".part")
    with src.open("rb") as f_in, tmp_dst.open("wb") as f_out:
        while True:
            buf = f_in.read(16 * 1024 * 1024)
            if not buf:
                break
            f_out.write(buf)
            done += len(buf)
            now = time.time()
            if now - last >= 15.0:
                pct = (done / total * 100.0) if total else 0.0
                spd = done / max(0.1, now - t0) / 1e6
                print(
                    f"   ⏳ 拷贝缓存 [{label}]: {done / 1e9:.2f}/{total / 1e9:.2f} GB ({pct:.1f}%) | ~{spd:.0f} MB/s",
                    flush=True,
                )
                last = now
    tmp_dst.replace(dst)
    elapsed = max(1, int(time.time() - t0))
    print(f"✅ 挂载缓存就绪: {label} ({total / 1e9:.2f} GB, 耗时 {elapsed}s, ~{total / elapsed / 1e6:.0f} MB/s)", flush=True)


def _download_with_heartbeat(repo_id: str, repo_path: str, local_dir: Path, token: str | bool) -> None:
    from huggingface_hub import hf_hub_download

    stop_evt = threading.Event()
    t0 = time.time()
    base_bytes = _dir_bytes(local_dir)

    def _beat() -> None:
        while not stop_evt.wait(15.0):
            delta = max(0, _dir_bytes(local_dir) - base_bytes)
            print(f"   ⏳ 下载中 [{repo_path}]: +{delta / 1e9:.2f} GB | 已耗时 {int(time.time() - t0)}s", flush=True)

    th = threading.Thread(target=_beat, daemon=True)
    th.start()
    try:
        hf_hub_download(
            repo_id=repo_id,
            filename=repo_path,
            local_dir=str(local_dir),
            token=token,
        )
    finally:
        stop_evt.set()
        th.join(timeout=2.0)


def ensure_models(
    manifest: dict[str, Any],
    hf_token: str | None,
    model_cache_dir: Path | None,
    rclone_model_cache: str | None,
    rclone_conf: str | None,
    no_cache_push: bool = False,
) -> None:
    os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"

    prof = manifest["profile"]
    needed: list[tuple[str, str]] = [
        ("diffusion_models", prof["unet"]),
        ("text_encoders", prof["clip"]),
        ("vae", prof["video_vae"]),
        ("vae", prof["audio_vae"]),
    ]
    if manifest.get("use_turbo") and manifest.get("turbo_lora"):
        needed.append(("loras", manifest["turbo_lora"]))

    models_root = COMFY_DIR / "models"
    models_root.mkdir(parents=True, exist_ok=True)

    for sub, fname in needed:
        dst = models_root / sub / fname
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and dst.stat().st_size > 1_000_000:
            print(f"✅ 已有本地权重: {sub}/{fname} ({dst.stat().st_size / 1e9:.2f} GB)", flush=True)
            continue

        avail = free_gb(Path("/content"))
        if avail < 12.0:
            raise RuntimeError(f"容器 /content 可用磁盘仅剩 {avail:.1f} GB，无法继续拉取 {sub}/{fname}！")

        # 通道 1：rclone 精确单文件内网缓存（零双份占盘）
        if rclone_model_cache and rclone_conf and Path(rclone_conf).exists():
            remote_file = f"{rclone_model_cache.rstrip('/')}/{sub}/{fname}"
            if rclone_remote_exists(rclone_conf, remote_file):
                print(f"⚡ 命中 Google Drive 缓存，内网直拉原位: {remote_file} -> {dst}", flush=True)
                run_cmd([
                    "rclone", "--config", rclone_conf, "copyto",
                    remote_file, str(dst),
                    "--drive-chunk-size", "128M",
                    "--stats", "15s", "--stats-one-line",
                ])
                if dst.exists() and dst.stat().st_size > 1_000_000:
                    print(f"✅ 内网缓存就绪: {sub}/{fname} ({dst.stat().st_size / 1e9:.2f} GB)", flush=True)
                    continue

        # 通道 2：本地/Drive FUSE 目录精确单文件缓存（同盘软链防双份，异盘流式单文件拷贝带进度心跳）
        if model_cache_dir is not None:
            cached = model_cache_dir / sub / fname
            if cached.exists() and cached.stat().st_size > 1_000_000:
                same_dev = os.stat(cached.parent).st_dev == os.stat(dst.parent).st_dev
                if same_dev:
                    print(f"🔗 同盘零拷贝软链缓存: {cached} -> {dst}", flush=True)
                    os.symlink(cached, dst)
                else:
                    print(f"📦 从 Google Drive 挂载缓存流式拷贝到高速 SSD: {cached} -> {dst}", flush=True)
                    _copy_with_heartbeat(cached, dst, f"{sub}/{fname}")
                continue

        # 通道 3：从公开仓库 Comfy-Org/MiniMax-H3 直拉（无 Token 显式传 False 跳过 Colab vault 超时等待）
        repo_path = f"{sub}/{fname}"
        tok: str | bool = (hf_token or os.environ.get("HF_TOKEN") or "").strip() or False
        print(f"⬇️ 从 Hugging Face 拉取 {REPO_ID}/{repo_path} (余盘 {free_gb():.1f} GB) ...", flush=True)
        _download_with_heartbeat(REPO_ID, repo_path, models_root, tok)
        # 清理 .cache 临时元数据碎片，节省磁盘
        dl_cache = models_root / ".cache"
        if dl_cache.exists():
            shutil.rmtree(dl_cache, ignore_errors=True)

        if not dst.exists() or dst.stat().st_size < 1_000_000:
            raise RuntimeError(f"模型下载不完整: {dst}")
        print(f"✅ HF 下载完成: {sub}/{fname} ({dst.stat().st_size / 1e9:.2f} GB，余盘 {free_gb():.1f} GB)", flush=True)

        # 回写持久化缓存供后续秒拉
        if not no_cache_push:
            if rclone_model_cache and rclone_conf and Path(rclone_conf).exists():
                remote_file = f"{rclone_model_cache.rstrip('/')}/{sub}/{fname}"
                print(f"💾 同步备份权重至 Google Drive 缓存: {remote_file}", flush=True)
                run_cmd([
                    "rclone", "--config", rclone_conf, "copyto",
                    str(dst), remote_file,
                    "--drive-chunk-size", "128M",
                    "--stats", "15s", "--stats-one-line",
                ], check=False)
            elif model_cache_dir is not None:
                cache_dst = model_cache_dir / sub / fname
                cache_dst.parent.mkdir(parents=True, exist_ok=True)
                if not cache_dst.exists() and os.stat(cache_dst.parent).st_dev != os.stat(dst.parent).st_dev:
                    print(f"💾 同步备份权重至挂载缓存: {cache_dst}", flush=True)
                    shutil.copy2(dst, cache_dst)


def http_json(url: str, payload: dict[str, Any] | None = None, timeout: int = 30) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def has_sageattention() -> bool:
    try:
        import sageattention  # noqa: F401
        return True
    except Exception:
        return False


def tail_log(log_path: Path, lines: int = 80) -> str:
    if not log_path.exists():
        return "(无日志文件)"
    content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(content[-lines:])


def start_comfy_server(port: int = 8188, listen: str = "127.0.0.1") -> tuple[subprocess.Popen, Path]:
    cmd = [sys.executable, str(COMFY_DIR / "main.py"), "--listen", listen, "--port", str(port)]
    if has_sageattention():
        cmd.append("--use-sage-attention")
        print("⚡ 已启用 --use-sage-attention", flush=True)
    log_path = Path("/tmp/comfyui_h3.log")
    log_f = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=str(COMFY_DIR), stdout=log_f, stderr=subprocess.STDOUT)

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 180
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"ComfyUI 启动失败 (rc={proc.returncode}):\n{tail_log(log_path)}")
        try:
            info = http_json(f"{base}/object_info/MiniMaxH3ReferenceToVideo", timeout=5)
            if "MiniMaxH3ReferenceToVideo" in info:
                print("✅ ComfyUI 服务就绪，已确认原生节点 MiniMaxH3ReferenceToVideo", flush=True)
                return proc, log_path
        except Exception:
            time.sleep(2)
    proc.terminate()
    raise RuntimeError(f"ComfyUI 启动超时或缺少 MiniMaxH3ReferenceToVideo 节点:\n{tail_log(log_path)}")


def patch_save_video_node(graph: dict[str, Any], base_url: str) -> None:
    """根据当前 ComfyUI 的 SaveVideo schema 自动适配 format/codec 入参。"""
    try:
        schema = http_json(f"{base_url}/object_info/SaveVideo", timeout=10)
        req_inputs = schema.get("SaveVideo", {}).get("input", {}).get("required", {})
        fmt_spec = req_inputs.get("format")
        if isinstance(fmt_spec, list) and fmt_spec and isinstance(fmt_spec[0], list):
            allowed_fmt = fmt_spec[0]
            if graph["39"]["inputs"].get("format") not in allowed_fmt:
                graph["39"]["inputs"]["format"] = "mp4" if "mp4" in allowed_fmt else ("auto" if "auto" in allowed_fmt else allowed_fmt[0])
        if "codec" in req_inputs:
            codec_spec = req_inputs["codec"]
            if isinstance(codec_spec, list) and codec_spec and isinstance(codec_spec[0], list):
                allowed_codec = codec_spec[0]
                graph["39"]["inputs"]["codec"] = "auto" if "auto" in allowed_codec else allowed_codec[0]
            else:
                graph["39"]["inputs"]["codec"] = "auto"
    except Exception:
        pass


def stage_job_assets(series_dir: Path, job: dict[str, Any]) -> None:
    input_dir = COMFY_DIR / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    for item in job["ref_images"]:
        src_rel = item["src_rel"].replace("\\", "/")
        src = series_dir / src_rel
        if not src.exists():
            raise FileNotFoundError(f"缺少参考图 ({item['tag']} {item['name']}): {src}")
        shutil.copy2(src, input_dir / item["staged_name"])
    for item in job["ref_audios"]:
        src_rel = item["src_rel"].replace("\\", "/")
        src = series_dir / src_rel
        if not src.exists():
            raise FileNotFoundError(f"缺少参考音频 ({item['tag']} {item['name']}): {src}")
        shutil.copy2(src, input_dir / item["staged_name"])


def probe_video(path: Path) -> dict[str, Any]:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe 失败 ({path}): {r.stderr}")
    info = json.loads(r.stdout or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    dur = float(info.get("format", {}).get("duration", 0.0))
    if not v or dur <= 0.5:
        raise RuntimeError(f"输出视频校验异常: {path} (duration={dur}, video={v})")
    return {
        "duration": round(dur, 2),
        "width": v.get("width"),
        "height": v.get("height"),
        "fps": v.get("r_frame_rate"),
        "vcodec": v.get("codec_name"),
        "has_audio": a is not None,
        "acodec": a.get("codec_name") if a else None,
        "sample_rate": a.get("sample_rate") if a else None,
        "channels": a.get("channels") if a else None,
    }


def run_batch(
    series_dir: Path,
    episode: str,
    manifest: dict[str, Any],
    base_url: str,
    server: subprocess.Popen,
    log_path: Path,
    skip_existing: bool = False,
    only_parts: set[str] | None = None,
    rclone_clips_dest: str | None = None,
    rclone_conf: str | None = None,
    local_clips_dest: Path | None = None,
) -> list[Path]:
    ep_dir = series_dir / episode
    jobs_dir = ep_dir / "h3_jobs"
    output_dir = COMFY_DIR / "output"
    produced: list[Path] = []

    selected_jobs = [j for j in manifest["jobs"] if (not only_parts or j["part"] in only_parts)]
    if only_parts:
        missing_parts = sorted(only_parts - {j["part"] for j in selected_jobs})
        if missing_parts:
            raise ValueError(f"--only-parts 指定了 manifest 中不存在的段号: {missing_parts}")
        print(f"🎯 按 --only-parts 筛选执行 {len(selected_jobs)}/{len(manifest['jobs'])} 段: {[j['part'] for j in selected_jobs]}", flush=True)

    for job in selected_jobs:
        part = job["part"]
        target = series_dir / job["target_clip_rel"].replace("\\", "/")
        if skip_existing and target.exists() and target.stat().st_size > 100_000:
            print(f"⏭️ 跳过已有成片段 {part}: {target}", flush=True)
            produced.append(target)
            continue

        print(
            f"\n🎬 开始生成 {episode} 段 {part} ({job['width']}x{job['height']}, "
            f"{job['frame_length']}帧 ~{job['actual_duration_sec']}s, ref_size={job['ref_image_size']}, seed={job['seed']}) ...",
            flush=True,
        )
        stage_job_assets(series_dir, job)

        graph = json.loads((jobs_dir / job["api_file"]).read_text(encoding="utf-8"))
        patch_save_video_node(graph, base_url)

        t0 = time.time()
        try:
            resp = http_json(f"{base_url}/prompt", payload={"prompt": graph}, timeout=30)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"提交 ComfyUI 任务被拒 (HTTP {exc.code}): {body}\n--- ComfyUI 日志 ---\n{tail_log(log_path)}")
        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"提交 ComfyUI 任务失败: {resp}")
        print(f"   prompt_id={prompt_id}，等待 Qwen3-VL 编码、DiT 采样与音画解码...", flush=True)

        out_video: Path | None = None
        last_beat = time.time()
        while True:
            time.sleep(5)
            if server.poll() is not None:
                raise RuntimeError(f"ComfyUI 进程异常退出 (rc={server.returncode}):\n{tail_log(log_path)}")
            hist = http_json(f"{base_url}/history/{prompt_id}", timeout=15)
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise RuntimeError(
                        f"ComfyUI 执行报错 ({prompt_id}): {json.dumps(status, ensure_ascii=False)}\n"
                        f"--- ComfyUI 日志 ---\n{tail_log(log_path)}"
                    )
                outputs = entry.get("outputs", {})
                for node_out in outputs.values():
                    for key in ("videos", "images", "gifs"):
                        for item in node_out.get(key, []):
                            fname = item.get("filename", "")
                            if fname.lower().endswith((".mp4", ".webm", ".mkv", ".mov")):
                                sub = item.get("subfolder", "")
                                cand = output_dir / sub / fname if sub else output_dir / fname
                                if cand.exists():
                                    out_video = cand
                                    break
                break
            if time.time() - last_beat >= 20:
                print(f"   ⏳ 段 {part} 运行中 ({int(time.time() - t0)}s) ...", flush=True)
                last_beat = time.time()

        if out_video is None:
            cands = sorted(output_dir.rglob(f"{job['output_prefix']}*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not cands:
                raise RuntimeError(f"未在 {output_dir} 找到前缀 {job['output_prefix']} 的输出视频\n{tail_log(log_path)}")
            out_video = cands[0]

        meta = probe_video(out_video)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(out_video, target)
        elapsed = int(time.time() - t0)
        aud_info = f"{meta['acodec']} {meta['sample_rate']}Hz {meta['channels']}ch" if meta["has_audio"] else "无音轨!"
        print(
            f"✅ 段 {part} 完成 ({elapsed}s): {target} | "
            f"{meta['width']}x{meta['height']} {meta['fps']}fps ({meta['vcodec']}), {meta['duration']}s, 音频={aud_info}",
            flush=True,
        )
        if not meta["has_audio"]:
            print("⚠️ 警告：输出视频未检测到音轨，请检查 ComfyUI 日志与音频 VAE 配置！", flush=True)
        if rclone_clips_dest and rclone_conf and Path(rclone_conf).exists():
            remote_clip = f"{rclone_clips_dest.rstrip('/')}/{target.name}"
            print(f"☁️ 实时备份成片至 Google Drive: {remote_clip}", flush=True)
            run_cmd([
                "rclone", "--config", rclone_conf, "copyto",
                str(target), remote_clip,
                "--drive-chunk-size", "64M",
            ], check=False)
        if local_clips_dest is not None:
            local_clips_dest.mkdir(parents=True, exist_ok=True)
            dst_mp4 = local_clips_dest / target.name
            shutil.copy2(target, dst_mp4)
            if hasattr(os, "sync"):
                os.sync()
            print(f"💾 实时落盘挂载云盘: {dst_mp4}", flush=True)
        produced.append(target)

    return produced


def run_webui(port: int = 8188) -> None:
    def _tunnel():
        p = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        assert p.stdout is not None
        for line in p.stdout:
            m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
            if m:
                print(f"\n🚀 ComfyUI 公网链接: {m.group(0)}\n", flush=True)

    threading.Thread(target=_tunnel, daemon=True).start()
    print("🌐 WebUI 模式运行中（在 Colab 中点击停止按钮即可结束）...", flush=True)
    while True:
        time.sleep(30)


def main() -> None:
    parser = argparse.ArgumentParser(description="h3出片 Colab MiniMax-H3 云端执行引擎")
    parser.add_argument("--series-dir", required=True, help="系列目录（容器内路径）")
    parser.add_argument("--episode", required=True, help="集目录名（如 ep01）")
    parser.add_argument("--mode", choices=["batch", "webui"], default="batch")
    parser.add_argument("--hf-token", default=None, help="可选 Hugging Face Token（公开仓库可不传）")
    parser.add_argument("--model-cache-dir", default=None, help="可选本地/挂载模型缓存目录")
    parser.add_argument("--rclone-model-cache", default=None, help="可选 rclone 远程缓存路径（如 gdrive:models/minimax_h3）")
    parser.add_argument("--rclone-config", default="/tmp/rclone.conf", help="rclone 配置文件路径")
    parser.add_argument("--no-cache-push", action="store_true", help="下载新权重后不回传至远程缓存")
    parser.add_argument("--skip-existing", action="store_true", help="跳过目标 clips 目录中已存在的成片")
    parser.add_argument("--only-parts", default="", help="仅运行指定段号（逗号分隔，如 S01 或 S02,S03,S04）")
    parser.add_argument("--rclone-clips-dest", default=None, help="每完成一段即刻回传至 Google Drive 远端 clips 目录")
    parser.add_argument("--local-clips-dest", default=None, help="每完成一段即刻拷贝至本地/挂载云盘 clips 目录并刷盘")
    parser.add_argument("--port", type=int, default=8188)
    args = parser.parse_args()

    print_system_banner()

    series_dir = Path(args.series_dir).resolve()
    manifest_path = series_dir / args.episode / "h3_jobs" / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"未找到任务清单，请先运行 pack_direct_job.py 或 make_h3_jobs.py: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"📋 加载任务清单: {manifest.get('series_title')} / {args.episode} | profile={manifest.get('profile_name')} | 共 {len(manifest.get('jobs', []))} 段", flush=True)

    ensure_comfyui(install_tunnel=(args.mode == "webui"))
    cache_dir = Path(args.model_cache_dir) if args.model_cache_dir else None
    ensure_models(
        manifest=manifest,
        hf_token=args.hf_token,
        model_cache_dir=cache_dir,
        rclone_model_cache=args.rclone_model_cache,
        rclone_conf=args.rclone_config,
        no_cache_push=args.no_cache_push,
    )

    listen = "0.0.0.0" if args.mode == "webui" else "127.0.0.1"
    server, log_path = start_comfy_server(port=args.port, listen=listen)
    try:
        if args.mode == "webui":
            if manifest["jobs"]:
                try:
                    stage_job_assets(series_dir, manifest["jobs"][0])
                    print(f"📂 已将参考素材预置到 {COMFY_DIR / 'input'}", flush=True)
                except FileNotFoundError as exc:
                    print(f"⚠️ 预置参考素材提醒: {exc}", flush=True)
            run_webui(port=args.port)
        else:
            only_set = {p.strip() for p in args.only_parts.split(",") if p.strip()} or None
            local_dest = Path(args.local_clips_dest) if args.local_clips_dest else None
            produced = run_batch(
                series_dir=series_dir,
                episode=args.episode,
                manifest=manifest,
                base_url=f"http://127.0.0.1:{args.port}",
                server=server,
                log_path=log_path,
                skip_existing=args.skip_existing,
                only_parts=only_set,
                rclone_clips_dest=args.rclone_clips_dest,
                rclone_conf=args.rclone_config,
                local_clips_dest=local_dest,
            )
            print(f"\n🎉 全部 {len(produced)} 个片段生成并校验完成！", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()


if __name__ == "__main__":
    main()
