#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V4.6 真实可运行 Real-ESRGAN 流式超分与单步 Lanczos 帧率规范化模块 (vsr_pipeline.batch_processor)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _utf8_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _parse_fps(raw: str | None) -> float:
    s = str(raw or "").strip()
    if not s or s in ("0/0", "N/A", "None"):
        return 0.0
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            den = float(b)
            return round(float(a) / den, 4) if den > 0 else 0.0
        except Exception:
            return 0.0
    try:
        return round(float(s), 4)
    except Exception:
        return 0.0


def probe_video_strict(media_path: Path) -> dict[str, Any]:
    if not media_path.is_file() or media_path.stat().st_size == 0:
        raise FileNotFoundError(f"视频文件不存在或为空: {media_path}")
    cmd = [
        "ffprobe", "-v", "error", "-count_frames",
        "-show_entries",
        "format=duration:stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate,duration,nb_frames,nb_read_frames",
        "-of", "json", str(media_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=_utf8_env(), check=True)
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams") or []
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    as_ = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not vs:
        raise RuntimeError(f"未检测到有效视频流: {media_path}")
    fps = _parse_fps(vs.get("avg_frame_rate")) or _parse_fps(vs.get("r_frame_rate"))
    nb_read = int(vs["nb_read_frames"]) if str(vs.get("nb_read_frames") or "").isdigit() else 0
    nb_hdr = int(vs["nb_frames"]) if str(vs.get("nb_frames") or "").isdigit() else 0
    frames = nb_read if nb_read > 0 else nb_hdr
    fmt_dur = float((data.get("format") or {}).get("duration") or 0.0)
    raw_vd = vs.get("duration")
    vd = float(raw_vd) if str(raw_vd or "") not in ("", "N/A", "None") else (frames / fps if (frames > 0 and fps > 0) else fmt_dur)
    if frames <= 0 and vd > 0 and fps > 0:
        frames = int(round(vd * fps))
    return {
        "width": int(vs.get("width") or 0),
        "height": int(vs.get("height") or 0),
        "fps": round(fps, 4),
        "duration_sec": round(vd, 6),
        "video_frames": frames,
        "has_audio": as_ is not None,
    }


SUPPORTED_ASPECT_RATIOS: tuple[str, ...] = ("16:9", "9:16")

P4_PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "anime_1k": {"width": 1920, "height": 1080, "vsr_preset": "anime"},
    "anime_4k": {"width": 3840, "height": 2160, "vsr_preset": "anime"},
    "live_1k": {"width": 1920, "height": 1080, "vsr_preset": "live"},
    "live_4k": {"width": 3840, "height": 2160, "vsr_preset": "live"},
}


def normalize_aspect_ratio(aspect_ratio: str | None = None) -> str:
    raw = str(aspect_ratio or "16:9").strip()
    if raw not in SUPPORTED_ASPECT_RATIOS:
        raise ValueError(f"Unsupported aspect_ratio={aspect_ratio!r}; expected one of {SUPPORTED_ASPECT_RATIOS}")
    return raw


def get_p4_profile_spec(profile: str | None = None, aspect_ratio: str = "16:9") -> dict[str, Any]:
    ar = normalize_aspect_ratio(aspect_ratio)
    key = str(profile or "").strip().lower()
    base = dict(P4_PROFILE_SPECS[key]) if key in P4_PROFILE_SPECS else {"width": 3840, "height": 2160, "vsr_preset": "auto"}
    w, h = int(base["width"]), int(base["height"])
    if ar == "9:16":
        w, h = min(w, h), max(w, h)
    else:
        w, h = max(w, h), min(w, h)
    return {"p4_profile": key if key in P4_PROFILE_SPECS else "", "aspect_ratio": ar, "width": w, "height": h, "vsr_preset": base["vsr_preset"]}


def _compute_target_size(
    in_w: int,
    in_h: int,
    scale: int | None,
    downscale_lanczos: str,
    backend: str = "ffmpeg_lanczos",
    target_width: int | None = None,
    target_height: int | None = None,
    p4_profile: str = "",
    aspect_ratio: str = "16:9",
) -> tuple[int, int]:
    ar = normalize_aspect_ratio(aspect_ratio)
    key = str(p4_profile or "").strip().lower()
    prof = get_p4_profile_spec(key, aspect_ratio=ar) if key in P4_PROFILE_SPECS else None
    if target_width and target_height and int(target_width) > 0 and int(target_height) > 0:
        w, h = int(target_width), int(target_height)
    elif downscale_lanczos:
        m = re.match(r"^\s*(\d+)\s*[xX×]\s*(\d+)\s*$", downscale_lanczos)
        if not m:
            raise ValueError(f"非法 --downscale_lanczos 参数: {downscale_lanczos!r}")
        w, h = int(m.group(1)), int(m.group(2))
    elif prof is not None:
        w, h = int(prof["width"]), int(prof["height"])
    elif backend == "realesrgan" or scale is None or int(scale) <= 0:
        w, h = (2160, 3840) if ar == "9:16" else (3840, 2160)
    else:
        sc = max(1, int(scale))
        w, h = in_w * sc, in_h * sc
    return max(2, (w // 2) * 2), max(2, (h // 2) * 2)


def _resolve_task_vsr_preset(in_path: Path, cli_preset: str | None, tasks_json: str = "") -> str:
    raw = str(cli_preset or "auto").strip().lower()
    if raw in ("live", "anime"):
        return raw
    cands = ([Path(tasks_json)] if tasks_json else []) + [
        in_path.parent.parent / "phase4_tasks.json",
        in_path.parent.parent / "Colab_A100_超分任务清单.json",
    ]
    seg_m = re.search(r"(segment_\d+)", in_path.stem)
    seg_id = seg_m.group(1) if seg_m else in_path.stem
    for cp in cands:
        if not cp.is_file():
            continue
        try:
            doc = json.loads(cp.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        for k in ("tasks", "clips", "segments", "queued_vsr_clips"):
            items = doc.get(k)
            if isinstance(items, dict) and isinstance(items.get(seg_id), dict):
                pv = str(items[seg_id].get("vsr_preset") or "").strip().lower()
                if pv in ("live", "anime"):
                    return pv
            elif isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and (it.get("file") == in_path.name or it.get("segment") == seg_id):
                        pv = str(it.get("vsr_preset") or "").strip().lower()
                        if pv in ("live", "anime"):
                            return pv
        top_p = str(doc.get("vsr_preset") or "").strip().lower()
        if top_p in ("live", "anime"):
            return top_p
    return "auto"


def _run_ffmpeg_lanczos(
    in_path: Path, out_path: Path, out_w: int, out_h: int,
    in_dur: float, target_fps: int | None, audio_codec: str, has_audio: bool,
) -> str:
    vf_parts = [f"scale={out_w}:{out_h}:flags=lanczos", "unsharp=5:5:0.35:5:5:0.0"]
    if target_fps is not None and int(target_fps) > 0:
        vf_parts.append(f"fps={int(target_fps)}")
    vf_str = ",".join(vf_parts)
    base_cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", str(in_path),
        "-vf", vf_str, "-t", f"{in_dur:.6f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-profile:v", "high", "-pix_fmt", "yuv420p",
    ]
    ac = (audio_codec or "copy").strip()
    a_args = (["-c:a", ac] if ac == "copy" else ["-c:a", ac, "-b:a", "256k"]) if has_audio else ["-an"]
    try:
        subprocess.run(base_cmd + a_args + [str(out_path)], env=_utf8_env(), check=True, capture_output=True)
        return ac if has_audio else "none"
    except subprocess.CalledProcessError:
        if has_audio and ac == "copy":
            subprocess.run(base_cmd + ["-c:a", "aac", "-b:a", "256k", str(out_path)], env=_utf8_env(), check=True, capture_output=True)
            return "aac_fallback"
        raise


def _run_ffmpeg_passthrough_1080p(
    in_path: Path, out_path: Path, in_w: int, in_h: int, out_w: int, out_h: int,
    in_dur: float, target_fps: int | None, audio_codec: str, has_audio: bool,
) -> str:
    vf_parts: list[str] = []
    if in_w != out_w or in_h != out_h:
        vf_parts.append(f"scale={out_w}:{out_h}:flags=lanczos")
    if target_fps is not None and int(target_fps) > 0:
        vf_parts.append(f"fps={int(target_fps)}")
    base_cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(in_path)]
    if vf_parts:
        base_cmd.extend(["-vf", ",".join(vf_parts)])
    base_cmd.extend([
        "-t", f"{in_dur:.6f}", "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-crf", "15", "-preset", "medium", "-profile:v", "high", "-pix_fmt", "yuv420p",
    ])
    ac = (audio_codec or "copy").strip()
    a_args = (["-c:a", ac] if ac == "copy" else ["-c:a", ac, "-b:a", "256k"]) if has_audio else ["-an"]
    try:
        subprocess.run(base_cmd + a_args + [str(out_path)], env=_utf8_env(), check=True, capture_output=True)
        return ac if has_audio else "none"
    except subprocess.CalledProcessError:
        if has_audio and ac == "copy":
            subprocess.run(base_cmd + ["-c:a", "aac", "-b:a", "256k", str(out_path)], env=_utf8_env(), check=True, capture_output=True)
            return "aac_fallback"
        raise


def _import_realesrgan_engine():
    try:
        from . import realesrgan_engine as re_eng
        return re_eng
    except ImportError:
        import realesrgan_engine as re_eng  # type: ignore
        return re_eng


def process_video(args: argparse.Namespace) -> dict[str, Any]:
    backend_req = str(args.backend or "ffmpeg_lanczos").strip().lower()
    p4_profile = str(getattr(args, "p4_profile", "") or "").strip().lower()
    aspect_ratio = normalize_aspect_ratio(getattr(args, "aspect_ratio", "16:9"))
    prof_spec = get_p4_profile_spec(p4_profile, aspect_ratio=aspect_ratio) if p4_profile in P4_PROFILE_SPECS else None
    in_p = Path(args.input_file).resolve()
    out_p = Path(args.output_file).resolve()
    in_meta = probe_video_strict(in_p)
    cli_preset = getattr(args, "vsr_preset", "auto")
    if cli_preset in ("", "auto", None) and prof_spec:
        cli_preset = prof_spec["vsr_preset"]
    preset_req = _resolve_task_vsr_preset(in_p, cli_preset, getattr(args, "tasks_json", ""))
    out_w, out_h = _compute_target_size(
        in_meta["width"], in_meta["height"], args.scale, args.downscale_lanczos,
        backend=backend_req,
        target_width=getattr(args, "target_width", None),
        target_height=getattr(args, "target_height", None),
        p4_profile=p4_profile,
        aspect_ratio=aspect_ratio,
    )

    model_weight = "none"
    vsr_preset_used = preset_req
    is_live_1k_passthrough = (
        (p4_profile == "live_1k" or (preset_req == "live" and ((out_w == 1920 and out_h == 1080) or (out_w == 1080 and out_h == 1920))))
        and in_meta["width"] >= out_w and in_meta["height"] >= out_h
    )
    if is_live_1k_passthrough:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        backend_used = "passthrough_1080p_high"
        vsr_preset_used = "live"
        audio_used = _run_ffmpeg_passthrough_1080p(
            in_p, out_p, in_meta["width"], in_meta["height"], out_w, out_h,
            in_meta["duration_sec"], args.target_fps, args.audio_codec, in_meta["has_audio"],
        )
    elif backend_req in ("realesrgan", "auto"):
        re_eng = _import_realesrgan_engine()
        try:
            eng_res = re_eng.run_realesrgan_video(
                in_p, out_p, in_meta["width"], in_meta["height"], in_meta["fps"], in_meta["duration_sec"],
                in_meta["has_audio"], target_w=out_w, target_h=out_h, target_fps=args.target_fps, vsr_preset=preset_req,
            )
            backend_used = "realesrgan"
            audio_used = eng_res["audio_codec_used"]
            model_weight = eng_res["model_weight"]
            vsr_preset_used = eng_res["vsr_preset"]
        except re_eng.RealESRGANUnavailableError as exc:
            out_p.unlink(missing_ok=True)
            if backend_req == "realesrgan":
                err_report = {"status": "VSR_BACKEND_UNAVAILABLE", "backend_requested": "realesrgan", "backend_used": None, "verified_backends": ["realesrgan", "ffmpeg_lanczos"], "error": str(exc)}
                print(json.dumps(err_report, ensure_ascii=False, indent=2))
                sys.exit(2)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            backend_used = "ffmpeg_lanczos"
            audio_used = _run_ffmpeg_lanczos(in_p, out_p, out_w, out_h, in_meta["duration_sec"], args.target_fps, args.audio_codec, in_meta["has_audio"])
    else:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        backend_used = "ffmpeg_lanczos"
        audio_used = _run_ffmpeg_lanczos(in_p, out_p, out_w, out_h, in_meta["duration_sec"], args.target_fps, args.audio_codec, in_meta["has_audio"])

    out_meta = probe_video_strict(out_p)
    fps_ok = True
    if args.target_fps:
        tf = int(args.target_fps)
        lo, hi = (59.90, 60.10) if tf == 60 else (tf - 0.05, tf + 0.05)
        fps_ok = lo <= out_meta["fps"] <= hi
    ok = out_meta["width"] == out_w and out_meta["height"] == out_h and out_meta["video_frames"] > 0 and fps_ok
    report = {
        "status": "VSR_OK" if ok else "VSR_VERIFY_FAILED",
        "backend_requested": backend_req,
        "backend_used": backend_used,
        "p4_profile": p4_profile,
        "aspect_ratio": aspect_ratio,
        "target_resolution": f"{out_w}x{out_h}",
        "vsr_preset": vsr_preset_used,
        "model_weight": model_weight,
        "upscale_model": model_weight if backend_used == "realesrgan" else args.upscale_model,
        "interpolate_model": args.interpolate_model or "",
        "scdet": bool(args.scdet),
        "audio_codec_used": audio_used,
        "input": in_meta,
        "output": out_meta,
        "output_file": str(out_p).replace("\\", "/"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        sys.exit(2)
    return report


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="V4.6 Real-ESRGAN 流式超分与单步 Lanczos 帧率规范化模块 (vsr_pipeline.batch_processor)")
    p.add_argument("--input_file", required=True)
    p.add_argument("--output_file", required=True)
    p.add_argument("--upscale_model", default="ffmpeg_lanczos")
    p.add_argument("--scale", type=int, default=None)
    p.add_argument("--downscale_lanczos", default="")
    p.add_argument("--interpolate_model", default="")
    p.add_argument("--target_fps", type=int, default=None)
    p.add_argument("--target-width", "--target_width", dest="target_width", type=int, default=None)
    p.add_argument("--target-height", "--target_height", dest="target_height", type=int, default=None)
    p.add_argument("--p4-profile", "--p4_profile", dest="p4_profile", choices=["", "anime_1k", "anime_4k", "live_1k", "live_4k"], default="")
    p.add_argument("--aspect-ratio", "--aspect_ratio", dest="aspect_ratio", choices=["16:9", "9:16"], default="16:9")
    p.add_argument("--scdet", action="store_true")
    p.add_argument("--audio_codec", default="copy")
    p.add_argument("--backend", choices=["ffmpeg_lanczos", "auto", "realesrgan"], default="ffmpeg_lanczos")
    p.add_argument("--vsr-preset", "--vsr_preset", dest="vsr_preset", choices=["live", "anime", "auto"], default="auto")
    p.add_argument("--tasks-json", "--tasks_json", dest="tasks_json", default="")
    args = p.parse_args(argv)
    try:
        process_video(args)
    except Exception as exc:
        print(json.dumps({"status": "VSR_ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
