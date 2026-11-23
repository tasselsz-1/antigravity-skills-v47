#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V4.6 Real-ESRGAN Streaming GPU Engine (spandrel + torch FP16 + ffmpeg rawvideo rgb24)."""

from __future__ import annotations

import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

MODEL_URLS: dict[str, str] = {
    "RealESRGAN_x2plus.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
    "RealESRGAN_x4plus.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
    "realesr-animevideov3.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",
}


class RealESRGANUnavailableError(RuntimeError):
    """Raised when torch/spandrel/CUDA or model weights are unavailable."""


def _utf8_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


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


def select_weight_for_preset(
    vsr_preset: str | None,
    in_h: int,
    target_h: int = 2160,
    in_w: int = 0,
    target_w: int = 0,
) -> tuple[str, str, str]:
    raw = str(vsr_preset or "auto").strip().lower()
    if raw not in ("live", "anime", "auto", ""):
        raise ValueError(f"非法 vsr_preset={vsr_preset!r}，仅支持 live / anime / auto")
    eff_preset = "anime" if raw == "anime" else "live"
    eff_target_short = min(int(target_w), int(target_h)) if int(target_w) > 0 else int(target_h)
    eff_in_short = min(int(in_w), int(in_h)) if int(in_w) > 0 else int(in_h)
    if eff_preset == "anime":
        w_name = "realesr-animevideov3.pth"
    elif eff_target_short <= 1080:
        w_name = "RealESRGAN_x2plus.pth"
    else:
        w_name = "RealESRGAN_x2plus.pth" if eff_in_short >= 1080 else "RealESRGAN_x4plus.pth"
    return eff_preset, w_name, MODEL_URLS[w_name]


def _require_cuda_and_spandrel() -> tuple[Any, Any]:
    try:
        import torch  # type: ignore
        import spandrel  # type: ignore
    except Exception as exc:
        raise RealESRGANUnavailableError(f"VSR_BACKEND_UNAVAILABLE: 未安装 torch 或 spandrel ({exc})") from exc
    if not bool(torch.cuda.is_available()):
        raise RealESRGANUnavailableError("VSR_BACKEND_UNAVAILABLE: CUDA GPU 不可用 (torch.cuda.is_available() == False)")
    return torch, spandrel


def ensure_weight_cached(weight_name: str, url: str, cache_dir: Path | None = None) -> Path:
    cdir = (cache_dir or Path(os.environ.get("REALESRGAN_CACHE_DIR") or (Path.home() / ".cache" / "realesrgan"))).expanduser().resolve()
    cdir.mkdir(parents=True, exist_ok=True)
    dst = cdir / weight_name
    if dst.is_file() and dst.stat().st_size > 102400:
        return dst
    tmp_dst = cdir / f"{weight_name}.part.{os.getpid()}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, tmp_dst.open("wb") as wf:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                wf.write(chunk)
        if not tmp_dst.is_file() or tmp_dst.stat().st_size <= 102400:
            raise RuntimeError(f"下载文件异常或过小: {tmp_dst}")
        tmp_dst.replace(dst)
        return dst
    except Exception as exc:
        tmp_dst.unlink(missing_ok=True)
        raise RealESRGANUnavailableError(f"VSR_BACKEND_UNAVAILABLE: 权重下载失败 {weight_name} ({url}): {exc}") from exc


def _load_spandrel_cuda_model(torch_mod: Any, spandrel_mod: Any, weight_path: Path) -> tuple[Any, int, bool]:
    try:
        desc = spandrel_mod.ModelLoader(device="cuda").load_from_file(str(weight_path))
        use_fp16 = bool(getattr(desc, "supports_half", True))
        model = desc.model.eval().to("cuda")
        if use_fp16:
            model = model.half()
        scale = max(1, int(getattr(desc, "scale", 2) or 2))
        return model, scale, use_fp16
    except Exception as exc:
        raise RealESRGANUnavailableError(f"VSR_BACKEND_UNAVAILABLE: 权重加载失败 {weight_path}: {exc}") from exc


def _forward_padded(torch_mod: Any, model: Any, x: Any, scale: int) -> Any:
    _, _, h, w = x.shape
    pad_h = (4 - (h % 4)) % 4
    pad_w = (4 - (w % 4)) % 4
    x_in = torch_mod.nn.functional.pad(x, (0, pad_w, 0, pad_h), mode="reflect") if (pad_h or pad_w) else x
    y = model(x_in)
    return y[:, :, : h * scale, : w * scale]


def _infer_tiled(torch_mod: Any, model: Any, x: Any, scale: int, tile: int = 512, overlap: int = 32) -> Any:
    _, c, h, w = x.shape
    out = torch_mod.empty((1, c, h * scale, w * scale), dtype=x.dtype, device=x.device)
    for y0 in range(0, h, tile):
        y1 = min(y0 + tile, h)
        py0, py1 = max(0, y0 - overlap), min(h, y1 + overlap)
        for x0 in range(0, w, tile):
            x1 = min(x0 + tile, w)
            px0, px1 = max(0, x0 - overlap), min(w, x1 + overlap)
            patch_out = _forward_padded(torch_mod, model, x[:, :, py0:py1, px0:px1], scale)
            cy0, cy1 = (y0 - py0) * scale, (y0 - py0 + (y1 - y0)) * scale
            cx0, cx1 = (x0 - px0) * scale, (x0 - px0 + (x1 - x0)) * scale
            out[:, :, y0 * scale : y1 * scale, x0 * scale : x1 * scale] = patch_out[:, :, cy0:cy1, cx0:cx1]
    return out


def _infer_frame(torch_mod: Any, model: Any, x: Any, scale: int, force_tile: bool, tile: int = 512, overlap: int = 32) -> tuple[Any, bool]:
    if not force_tile:
        try:
            return _forward_padded(torch_mod, model, x, scale), False
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower():
                raise
            torch_mod.cuda.empty_cache()
    return _infer_tiled(torch_mod, model, x, scale, tile=tile, overlap=overlap), True


def _read_exact(stream: Any, nbytes: int) -> bytes:
    buf = bytearray(nbytes)
    mv = memoryview(buf)
    pos = 0
    while pos < nbytes:
        chunk = stream.read(nbytes - pos)
        if not chunk:
            return bytes(mv[:pos])
        mv[pos : pos + len(chunk)] = chunk
        pos += len(chunk)
    return bytes(buf)


def run_realesrgan_video(
    in_path: Path,
    out_path: Path,
    in_w: int,
    in_h: int,
    in_fps: float,
    in_dur: float,
    has_audio: bool,
    target_w: int = 3840,
    target_h: int = 2160,
    target_fps: int | None = None,
    vsr_preset: str = "auto",
    tile: int = 512,
    overlap: int = 32,
) -> dict[str, Any]:
    torch_mod, spandrel_mod = _require_cuda_and_spandrel()
    eff_preset, weight_name, weight_url = select_weight_for_preset(vsr_preset, in_h, target_h=target_h, in_w=in_w, target_w=target_w)
    weight_path = ensure_weight_cached(weight_name, weight_url)
    model, scale, use_fp16 = _load_spandrel_cuda_model(torch_mod, spandrel_mod, weight_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    need_fps_conv = bool(target_fps and int(target_fps) > 0 and abs(float(in_fps) - float(target_fps)) > 0.05)
    eff_fps = int(target_fps) if (target_fps and int(target_fps) > 0) else (round(float(in_fps), 4) if in_fps > 0 else 24)
    dec_vf = ["-vf", f"fps={int(target_fps)}"] if need_fps_conv else []
    dec_cmd = ["ffmpeg", "-v", "error", "-i", str(in_path), *dec_vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    enc_cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{target_w}x{target_h}", "-framerate", str(eff_fps), "-i", "-"]
    if has_audio:
        enc_cmd.extend(["-i", str(in_path), "-map", "0:v:0", "-map", "1:a:0?", "-filter:a", f"atrim=0:{max(0.05, float(in_dur)):.6f}"])
    else:
        enc_cmd.append("-an")
    enc_cmd.extend(["-c:v", "libx264", "-crf", "15", "-preset", "fast", "-profile:v", "high", "-pix_fmt", "yuv420p"])
    if has_audio:
        enc_cmd.extend(["-c:a", "aac", "-ar", "48000", "-b:a", "256k"])
    enc_cmd.extend(["-movflags", "+faststart", str(out_path)])

    frame_bytes = int(in_w) * int(in_h) * 3
    dtype_in = torch_mod.float16 if use_fp16 else torch_mod.float32
    dec_p = subprocess.Popen(dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=max(frame_bytes * 2, 65536), env=_utf8_env())
    enc_p = subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=max(target_w * target_h * 3, 65536), env=_utf8_env())
    frames_done, used_tile = 0, False
    try:
        with torch_mod.inference_mode():
            while True:
                raw = _read_exact(dec_p.stdout, frame_bytes)
                if len(raw) < frame_bytes:
                    break
                arr = np.frombuffer(raw, dtype=np.uint8).reshape((in_h, in_w, 3))
                ten = torch_mod.from_numpy(arr).to(device="cuda", dtype=dtype_in).permute(2, 0, 1).unsqueeze(0).div_(255.0)
                sr_out, tiled_now = _infer_frame(torch_mod, model, ten, scale, used_tile, tile=tile, overlap=overlap)
                used_tile = used_tile or tiled_now
                out_f32 = sr_out.float()
                if out_f32.shape[-2:] != (target_h, target_w):
                    out_f32 = torch_mod.nn.functional.interpolate(out_f32, size=(target_h, target_w), mode="bicubic", align_corners=False, antialias=True)
                u8 = out_f32.clamp_(0.0, 1.0).mul_(255.0).round_().to(torch_mod.uint8).squeeze(0).permute(1, 2, 0).contiguous().cpu().numpy()
                assert enc_p.stdin is not None
                enc_p.stdin.write(u8.tobytes())
                frames_done += 1
    except Exception:
        dec_p.kill()
        enc_p.kill()
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if dec_p.stdout:
            dec_p.stdout.close()
        if enc_p.stdin:
            enc_p.stdin.close()
    dec_err = (dec_p.stderr.read().decode("utf-8", "replace") if dec_p.stderr else "").strip()
    enc_err = (enc_p.stderr.read().decode("utf-8", "replace") if enc_p.stderr else "").strip()
    dec_rc, enc_rc = dec_p.wait(), enc_p.wait()
    if dec_rc != 0 or enc_rc != 0 or frames_done <= 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"Real-ESRGAN 流式超分失败 (frames={frames_done}, dec_rc={dec_rc}, enc_rc={enc_rc}): {dec_err} | {enc_err}")
    return {
        "backend_used": "realesrgan",
        "vsr_preset": eff_preset,
        "model_weight": weight_name,
        "model_scale": scale,
        "frames_processed": frames_done,
        "tiled_fallback_used": used_tile,
        "audio_codec_used": "aac" if has_audio else "none",
    }
