#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V4.2 第四棒 FCPXML 1.9 DTD 强校验、视频流独立探针、母带渲染后声画实测、
字节官方 LatentSync (scripts.inference) 与 48kHz 声线批准三元组门禁子模块
严格遵循「Skill 即流程：遇错即停、立即上报、严禁私自绕行」铁律。
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
import xml.etree.ElementTree as ET
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

DEFAULT_TARGET_FPS = 24
SUPPORTED_TARGET_FPS = (24, 60)
FPS_24_MIN = 23.95
FPS_24_MAX = 24.05
FPS_60_MIN = 59.90
FPS_60_MAX = 60.10

LATENTSYNC_GIT_URL = "https://github.com/bytedance/LatentSync.git"
LATENTSYNC_PINNED_COMMIT = "d058a61"
LATENTSYNC_HF_REPO = "ByteDance/LatentSync-1.6"
LATENTSYNC_DEFAULT_UNET_CFG_REL = "configs/unet/stage2_512.yaml"
LATENTSYNC_DEFAULT_CKPT_REL = "checkpoints/latentsync_unet.pt"
LATENTSYNC_DEFAULT_WHISPER_REL = "checkpoints/whisper/tiny.pt"

BANNED_AI_REVIEWER_TOKENS = (
    "neon", "carbon", "argon", "hydrogen", "flash", "gemini",
    "gpt", "codex", "antigravity", "claude", "opus", "sonnet",
    "orchestrator", "agent", "subagent", "bot", "auto", "llm",
)


def utf8_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_target_fps(target_fps: int | str | None = None, default: int = DEFAULT_TARGET_FPS) -> int:
    if target_fps in (None, ""):
        return int(default)
    val = int(target_fps)
    if val not in SUPPORTED_TARGET_FPS:
        raise ValueError(f"Unsupported target_fps={target_fps!r}; expected one of {SUPPORTED_TARGET_FPS}")
    return val


def fps_bounds(target_fps: int = DEFAULT_TARGET_FPS) -> tuple[float, float]:
    tfps = normalize_target_fps(target_fps)
    return (FPS_60_MIN, FPS_60_MAX) if tfps == 60 else (FPS_24_MIN, FPS_24_MAX)


def is_fps_compliant(fps: float | int | None, target_fps: int = DEFAULT_TARGET_FPS) -> bool:
    try:
        v = float(fps or 0.0)
    except Exception:
        return False
    lo, hi = fps_bounds(target_fps)
    return lo <= v <= hi


def is_fps_24_strict(fps: float | int | None) -> bool:
    return is_fps_compliant(fps, 24)


def is_fps_60_strict(fps: float | int | None) -> bool:
    return is_fps_compliant(fps, 60)


P4_PROFILES: dict[str, dict[str, Any]] = {
    "anime_1k": {
        "p4_profile": "anime_1k", "width": 1920, "height": 1080, "res_tag": "1K",
        "vsr_preset": "anime", "profile_status": "production", "passthrough_1080p": False, "profile_note": "",
    },
    "anime_4k": {
        "p4_profile": "anime_4k", "width": 3840, "height": 2160, "res_tag": "4K",
        "vsr_preset": "anime", "profile_status": "production_verified", "passthrough_1080p": False, "profile_note": "",
    },
    "live_1k": {
        "p4_profile": "live_1k", "width": 1920, "height": 1080, "res_tag": "1K",
        "vsr_preset": "live", "profile_status": "production_primary", "passthrough_1080p": True,
        "profile_note": "真人主力档：>=1080p 源直通 High Profile 封装，严禁盲目放大成假 4K；<1080p 源走 RealESRGAN_x2plus 定标 1920x1080",
    },
    "live_4k": {
        "p4_profile": "live_4k", "width": 3840, "height": 2160, "res_tag": "4K",
        "vsr_preset": "live", "profile_status": "reserved_not_recommended_yet", "passthrough_1080p": False,
        "profile_note": "真人 4K 预留档（暂不推荐启用）：待未来接入高码率源 + 人脸细节增强后再启用；当前真人主推 live_1k",
    },
}


SUPPORTED_ASPECT_RATIOS: tuple[str, ...] = ("16:9", "9:16")
DEFAULT_ASPECT_RATIO: str = "16:9"


def normalize_aspect_ratio(aspect_ratio: str | None = None) -> str:
    raw = str(aspect_ratio or DEFAULT_ASPECT_RATIO).strip()
    if raw not in SUPPORTED_ASPECT_RATIOS:
        raise ValueError(f"Unsupported aspect_ratio={aspect_ratio!r}; expected one of {SUPPORTED_ASPECT_RATIOS}")
    return raw


def _extract_aspect_ratio_from_doc(doc: Any, ep_s: str = "") -> str | None:
    if not isinstance(doc, dict):
        return None
    cands: list[Any] = [doc.get("aspect_ratio")]
    if ep_s and isinstance(doc.get("episodes"), dict):
        ep_entry = doc["episodes"].get(ep_s)
        if isinstance(ep_entry, dict):
            cands.insert(0, ep_entry.get("aspect_ratio"))
    for k in ("video_config", "project_config", "style_config"):
        sub = doc.get(k)
        if isinstance(sub, dict):
            cands.append(sub.get("aspect_ratio"))
    for c in cands:
        s = str(c or "").strip()
        if s in SUPPORTED_ASPECT_RATIOS:
            return s
    return None


def resolve_aspect_ratio_v46(
    project_root: Path,
    ep_s: str,
    explicit_aspect_ratio: str | None = None,
) -> str:
    if explicit_aspect_ratio is not None and str(explicit_aspect_ratio).strip():
        return normalize_aspect_ratio(explicit_aspect_ratio)
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    ep_dir = project_root / "分集" / ep_s
    idx_file = project_root / "中间文件" / "索引与制作记录" / "分集索引.json"
    cands = (
        work_dir / "phase4_tasks.json",
        work_dir / "Colab_A100_超分任务清单.json",
        work_dir / "style_and_cast.json",
        ep_dir / "style_and_cast.json",
        project_root / "style_and_cast.json",
        idx_file,
    )
    for cp in cands:
        if not cp.is_file() or cp.stat().st_size == 0:
            continue
        try:
            doc = json.loads(cp.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        found = _extract_aspect_ratio_from_doc(doc, ep_s=ep_s)
        if found:
            return found
    return DEFAULT_ASPECT_RATIO


def normalize_p4_profile(p4_profile: str | None = None) -> str | None:
    raw = str(p4_profile or "").strip().lower()
    if not raw:
        return None
    if raw not in P4_PROFILES:
        raise ValueError(f"Unsupported p4_profile={p4_profile!r}; expected one of {tuple(P4_PROFILES.keys())}")
    return raw


def get_p4_profile_spec(p4_profile: str | None = None, aspect_ratio: str = DEFAULT_ASPECT_RATIO) -> dict[str, Any]:
    ar = normalize_aspect_ratio(aspect_ratio)
    key = normalize_p4_profile(p4_profile)
    if key is not None:
        spec = dict(P4_PROFILES[key])
    else:
        spec = {
            "p4_profile": "", "width": 3840, "height": 2160, "res_tag": "4K",
            "vsr_preset": "auto", "profile_status": "default_legacy_4k", "passthrough_1080p": False, "profile_note": "",
        }
    w, h = int(spec["width"]), int(spec["height"])
    spec["width"], spec["height"] = (min(w, h), max(w, h)) if ar == "9:16" else (max(w, h), min(w, h))
    spec["aspect_ratio"] = ar
    return spec


def _infer_p4_1k_profile_from_style_doc(doc: Any) -> str | None:
    if not isinstance(doc, dict):
        return None
    parts: list[str] = []
    for k in ("visual_style", "style_name", "style", "style_category", "art_style", "style_prompt", "target_style", "genre"):
        v = doc.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
        elif isinstance(v, dict):
            parts.append(json.dumps(v, ensure_ascii=False))
    blob = (" ".join(parts) if parts else json.dumps(doc, ensure_ascii=False)).lower()
    anime_kw = ("动漫", "二次元", "2d", "日漫", "国漫", "动画", "赛璐璐", "插画", "番剧", "anime", "cel", "manga", "comic", "illustration")
    live_kw = ("真人", "写实", "实拍", "电影写实", "live", "photoreal", "real human")
    if any(w in blob for w in anime_kw):
        return "anime_1k"
    if any(w in blob for w in live_kw):
        return "live_1k"
    return None


def resolve_p4_profile_v46(
    project_root: Path,
    ep_s: str,
    explicit_profile: str | None = None,
    explicit_aspect_ratio: str | None = None,
) -> dict[str, Any]:
    ar = resolve_aspect_ratio_v46(project_root, ep_s, explicit_aspect_ratio=explicit_aspect_ratio)
    key = normalize_p4_profile(explicit_profile)
    if key is not None:
        return get_p4_profile_spec(key, aspect_ratio=ar)
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    ep_dir = project_root / "分集" / ep_s
    for cand in (work_dir / "phase4_tasks.json", work_dir / "Colab_A100_超分任务清单.json"):
        if not cand.is_file() or cand.stat().st_size == 0:
            continue
        try:
            doc = json.loads(cand.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(doc, dict) and str(doc.get("p4_profile") or "").strip():
            try:
                return get_p4_profile_spec(str(doc.get("p4_profile")), aspect_ratio=ar)
            except ValueError:
                continue
    for sc in (work_dir / "style_and_cast.json", ep_dir / "style_and_cast.json", work_dir / "episode_director_bible.json", ep_dir / "episode_director_bible.json"):
        if not sc.is_file() or sc.stat().st_size == 0:
            continue
        try:
            sdoc = json.loads(sc.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        inferred = _infer_p4_1k_profile_from_style_doc(sdoc)
        if inferred:
            return get_p4_profile_spec(inferred, aspect_ratio=ar)
    return get_p4_profile_spec(None, aspect_ratio=ar)


def done_vsr_dir_name(target_fps: int = DEFAULT_TARGET_FPS, p4_profile: str | None = None) -> str:
    spec = get_p4_profile_spec(p4_profile)
    return f"已超分{spec['res_tag']}_{normalize_target_fps(target_fps)}fps"


def default_master_mp4_name(ep_s: str, target_fps: int = DEFAULT_TARGET_FPS, p4_profile: str | None = None) -> str:
    spec = get_p4_profile_spec(p4_profile)
    return f"{ep_s}_{spec['res_tag']}{normalize_target_fps(target_fps)}fps_成片.mp4"


def fcpxml_format_name(width: int = 3840, height: int = 2160, target_fps: int = DEFAULT_TARGET_FPS) -> str:
    return f"FFVideoFormat{int(width)}x{int(height)}p{normalize_target_fps(target_fps)}"


def _extract_fps_from_json_doc(doc: Any) -> int | None:
    if not isinstance(doc, dict):
        return None
    for cand in (
        doc.get("target_fps"),
        (doc.get("routing_policy") or {}).get("target_fps") if isinstance(doc.get("routing_policy"), dict) else None,
        (doc.get("flow_config") or {}).get("target_fps") if isinstance(doc.get("flow_config"), dict) else None,
        (doc.get("video_config") or {}).get("target_fps") if isinstance(doc.get("video_config"), dict) else None,
    ):
        if cand is not None and str(cand).strip() != "":
            try:
                iv = int(cand)
                if iv in SUPPORTED_TARGET_FPS:
                    return iv
            except Exception:
                continue
    return None


def inspect_legacy_fps_conflict_v42(
    project_root: Path,
    ep_s: str,
    explicit_fps: int | str | None = None,
) -> dict[str, Any]:
    if explicit_fps not in (None, ""):
        return {"legacy_plan_fps_conflict": False, "legacy_plan_fps_warning": ""}
    plan_json = project_root / "中间文件" / "在制工作区" / ep_s / "Colab_A100_超分任务清单.json"
    if plan_json.is_file() and plan_json.stat().st_size > 0:
        try:
            old_fps = _extract_fps_from_json_doc(json.loads(plan_json.read_text(encoding="utf-8-sig")))
            if old_fps == 60:
                warn_msg = (
                    f"检测到旧版 Colab_A100_超分任务清单.json 记录 target_fps=60，但本次未显式传 --target-fps；"
                    f"已按默认主线冲突覆盖并锁定为 24fps ({str(plan_json).replace(chr(92), '/')})"
                )
                return {"legacy_plan_fps_conflict": True, "legacy_plan_fps_warning": warn_msg}
        except Exception:
            pass
    return {"legacy_plan_fps_conflict": False, "legacy_plan_fps_warning": ""}


def resolve_target_fps_v42(
    project_root: Path,
    ep_s: str,
    explicit_fps: int | str | None = None,
    prefer_bible_first: bool = False,
) -> int:
    _ = prefer_bible_first
    if explicit_fps not in (None, ""):
        return normalize_target_fps(explicit_fps)
    conflict = inspect_legacy_fps_conflict_v42(project_root, ep_s, explicit_fps=explicit_fps)
    if conflict["legacy_plan_fps_conflict"]:
        print(f"⚠️ [LEGACY_PLAN_FPS_CONFLICT] {conflict['legacy_plan_fps_warning']}", file=sys.stderr)
    return DEFAULT_TARGET_FPS


def _sec_to_frames_half_up(sec: float | int | Decimal, target_fps: int = DEFAULT_TARGET_FPS, min_one_frame: bool = False) -> int:
    tfps = normalize_target_fps(target_fps)
    s_dec = max(Decimal("0"), Decimal(str(sec or 0)))
    frames = int((s_dec * Decimal(tfps)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return max(1, frames) if min_one_frame else frames


def build_timeline_cutpoint_table_v42(
    seg_durations: list[tuple[str, float]],
    target_fps: int = DEFAULT_TARGET_FPS,
) -> dict[str, Any]:
    tfps = normalize_target_fps(target_fps)
    tfps_dec = Decimal(tfps)
    den = tfps * 100
    cum_sec = Decimal("0")
    cutpoint_frames: list[int] = [0]
    cutpoint_seconds: list[float] = [0.0]
    segments: list[dict[str, Any]] = []
    by_segment: dict[str, dict[str, Any]] = {}
    prev_f = 0
    for idx, (seg_name, raw_dur) in enumerate(seg_durations):
        dur_f = max(0.0, float(raw_dur or 0.0))
        dur_dec = Decimal(str(dur_f))
        raw_s = float(cum_sec)
        cum_sec = cum_sec + dur_dec
        raw_e = float(cum_sec)
        cur_f = int((cum_sec * tfps_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if dur_f > 0.0 and cur_f < prev_f + 1:
            cur_f = prev_f + 1
        d_i = cur_f - prev_f
        ent = {
            "index": idx, "segment": str(seg_name), "raw_duration_sec": round(dur_f, 6),
            "raw_start_sec": round(raw_s, 6), "raw_end_sec": round(raw_e, 6),
            "start_frame": prev_f, "end_frame": cur_f, "duration_frames": d_i,
            "timeline_start_sec": round(prev_f / float(tfps), 6),
            "timeline_end_sec": round(cur_f / float(tfps), 6),
            "timeline_duration_sec": round(d_i / float(tfps), 6),
            "offset_rational": f"{prev_f * 100}/{den}s", "duration_rational": f"{d_i * 100}/{den}s",
        }
        segments.append(ent)
        by_segment[str(seg_name)] = ent
        cutpoint_frames.append(cur_f)
        cutpoint_seconds.append(round(raw_e, 6))
        prev_f = cur_f
    return {
        "target_fps": tfps, "rational_denominator": den, "cutpoint_frames": cutpoint_frames,
        "cutpoint_raw_seconds": cutpoint_seconds, "segment_frame_durations": [s["duration_frames"] for s in segments],
        "total_frames": prev_f, "total_timeline_frames": prev_f, "total_raw_duration_sec": round(float(cum_sec), 6),
        "total_timeline_duration_sec": round(prev_f / float(tfps), 6),
        "sequence_duration_rational": f"{prev_f * 100}/{den}s" if prev_f > 0 else f"100/{den}s",
        "segments": segments, "by_segment": by_segment,
    }


def parse_fps_fraction(raw_fps: str | None) -> float:
    s = str(raw_fps or "").strip()
    if not s or s in ("0/0", "N/A"):
        return 0.0
    if "/" in s:
        p1, p2 = s.split("/", 1)
        try:
            den = float(p2)
            return round(float(p1) / den, 4) if den > 0 else 0.0
        except Exception:
            return 0.0
    try:
        return round(float(s), 4)
    except Exception:
        return 0.0


def parse_fcpxml_rational_sec(val: str | float | int | None) -> float:
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val or "").strip().rstrip("s")
    if not s:
        return 0.0
    if "/" in s:
        n_s, d_s = s.split("/", 1)
        try:
            den = float(d_s)
            return float(n_s) / den if den != 0 else 0.0
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _safe_pos_int(val: Any) -> int:
    if val in (None, "", "N/A"):
        return 0
    try:
        iv = int(val)
        return iv if iv > 0 else 0
    except Exception:
        return 0


def _safe_pos_float(val: Any) -> float:
    if val in (None, "", "N/A"):
        return 0.0
    try:
        fv = float(val)
        return fv if fv > 0.0 else 0.0
    except Exception:
        return 0.0


def probe_video_streams_v42(media_path: Path) -> dict[str, Any]:
    if not media_path.exists() or media_path.stat().st_size == 0:
        return {"ok": False, "error": f"文件不存在或为空: {media_path}"}
    cmd = [
        "ffprobe", "-v", "error", "-count_frames",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,duration,nb_frames,nb_read_frames,sample_rate,channels",
        "-of", "json", str(media_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=utf8_env(), check=True)
        data = json.loads(res.stdout or "{}")
    except Exception as exc:
        return {"ok": False, "error": f"ffprobe 解析失败: {exc}"}

    streams = data.get("streams") or []
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt_dur = _safe_pos_float((data.get("format") or {}).get("duration"))

    avg_fps_str = v_stream.get("avg_frame_rate") if v_stream else None
    r_fps_str = v_stream.get("r_frame_rate") if v_stream else None
    avg_fps = parse_fps_fraction(avg_fps_str)
    r_fps = parse_fps_fraction(r_fps_str)
    fps = avg_fps if avg_fps > 0 else r_fps

    nb_read = _safe_pos_int(v_stream.get("nb_read_frames")) if v_stream else 0
    nb_hdr = _safe_pos_int(v_stream.get("nb_frames")) if v_stream else 0
    counted_frames = nb_read if nb_read > 0 else nb_hdr

    v_stream_dur = _safe_pos_float(v_stream.get("duration")) if v_stream else 0.0
    if v_stream is not None:
        if v_stream_dur > 0.0:
            if nb_read > 0 and fps > 0.0 and (nb_read / fps) + 0.15 < v_stream_dur:
                video_dur = nb_read / fps
            else:
                video_dur = v_stream_dur
        elif counted_frames > 0 and fps > 0.0:
            video_dur = counted_frames / fps
        else:
            video_dur = 0.0
        video_frames = counted_frames if counted_frames > 0 else (int(round(video_dur * fps)) if (video_dur > 0 and fps > 0) else 0)
    else:
        video_dur = 0.0
        video_frames = 0

    a_stream_dur = _safe_pos_float(a_stream.get("duration")) if a_stream else 0.0
    if a_stream is not None and a_stream_dur <= 0.0 and v_stream is None:
        a_stream_dur = fmt_dur
    primary_dur = video_dur if v_stream is not None else (a_stream_dur if a_stream_dur > 0 else fmt_dur)

    return {
        "ok": True,
        "duration_sec": round(primary_dur, 4),
        "video_duration_sec": round(video_dur, 4),
        "audio_duration_sec": round(a_stream_dur, 4),
        "format_duration_sec": round(fmt_dur, 4),
        "video_frames": video_frames,
        "nb_read_frames": nb_read,
        "nb_frames": video_frames,
        "width": int(v_stream.get("width") or 0) if v_stream else 0,
        "height": int(v_stream.get("height") or 0) if v_stream else 0,
        "avg_frame_rate": avg_fps_str,
        "r_frame_rate": r_fps_str,
        "avg_fps": avg_fps,
        "r_fps": r_fps,
        "fps": round(fps, 4),
        "video_codec": v_stream.get("codec_name") if v_stream else None,
        "has_video": v_stream is not None,
        "has_audio": a_stream is not None,
        "audio_codec": a_stream.get("codec_name") if a_stream else None,
        "sample_rate": int(a_stream.get("sample_rate") or 0) if a_stream else 0,
        "channels": int(a_stream.get("channels") or 0) if a_stream else 0,
    }


APPLE_FCPXML_1_9_DTD = """
<!ENTITY % time "CDATA">
<!ELEMENT fcpxml (import-options?, resources?, (library | event+ | project+))>
<!ATTLIST fcpxml version CDATA #FIXED "1.9">
<!ELEMENT import-options (option*)>
<!ELEMENT option EMPTY>
<!ATTLIST option key CDATA #REQUIRED value CDATA #REQUIRED>
<!ELEMENT resources (asset | effect | format | media | object-tracker)*>
<!ELEMENT format EMPTY>
<!ATTLIST format id ID #REQUIRED name CDATA #IMPLIED frameDuration %time; #IMPLIED fieldOrder CDATA #IMPLIED width CDATA #IMPLIED height CDATA #IMPLIED paspH CDATA #IMPLIED paspV CDATA #IMPLIED colorSpace CDATA #IMPLIED projection CDATA #IMPLIED stereoscopic CDATA #IMPLIED>
<!ELEMENT asset (media-rep+, metadata?)>
<!ATTLIST asset id ID #REQUIRED name CDATA #IMPLIED uid CDATA #IMPLIED start %time; #IMPLIED duration %time; #IMPLIED hasVideo CDATA #IMPLIED format IDREF #IMPLIED hasAudio CDATA #IMPLIED audioSources CDATA #IMPLIED audioChannels CDATA #IMPLIED audioRate CDATA #IMPLIED customLutOverride CDATA #IMPLIED colorSpaceOverride CDATA #IMPLIED projectionOverride CDATA #IMPLIED stereoscopicOverride CDATA #IMPLIED auxVideoFlags CDATA #IMPLIED>
<!ELEMENT media-rep (bookmark?)>
<!ATTLIST media-rep kind (original-media | proxy-media) "original-media" sig CDATA #IMPLIED src CDATA #REQUIRED suggestedFilename CDATA #IMPLIED>
<!ELEMENT bookmark (#PCDATA)>
<!ELEMENT effect EMPTY>
<!ATTLIST effect id ID #REQUIRED name CDATA #IMPLIED uid CDATA #REQUIRED src CDATA #IMPLIED>
<!ELEMENT media (multicam | sequence)?>
<!ATTLIST media id ID #REQUIRED name CDATA #IMPLIED uid CDATA #IMPLIED projectRef IDREF #IMPLIED modDate CDATA #IMPLIED>
<!ELEMENT multicam EMPTY>
<!ELEMENT object-tracker EMPTY>
<!ELEMENT metadata (md*)>
<!ELEMENT md (array | string)*>
<!ATTLIST md key CDATA #REQUIRED value CDATA #IMPLIED editable (0 | 1) "0" type CDATA #IMPLIED displayName CDATA #IMPLIED description CDATA #IMPLIED source CDATA #IMPLIED>
<!ELEMENT string (#PCDATA)>
<!ELEMENT array (string*)>
<!ELEMENT library (event | smart-collection)*>
<!ATTLIST library location CDATA #IMPLIED colorProcessing (standard | wide | wide-hdr) #IMPLIED>
<!ELEMENT smart-collection EMPTY>
<!ATTLIST smart-collection name CDATA #REQUIRED match (any | all) #IMPLIED>
<!ELEMENT collection-folder (collection-folder | keyword-collection | smart-collection)*>
<!ATTLIST collection-folder name CDATA #REQUIRED>
<!ELEMENT keyword-collection EMPTY>
<!ATTLIST keyword-collection name CDATA #REQUIRED>
<!ELEMENT event (clip | audition | mc-clip | ref-clip | sync-clip | asset-clip | collection-folder | keyword-collection | smart-collection | project)*>
<!ATTLIST event name CDATA #IMPLIED uid CDATA #IMPLIED>
<!ELEMENT project (sequence, metadata?)>
<!ATTLIST project name CDATA #IMPLIED uid CDATA #IMPLIED id ID #IMPLIED modDate CDATA #IMPLIED>
<!ELEMENT sequence (note?, spine, metadata?)>
<!ATTLIST sequence format IDREF #REQUIRED duration %time; #IMPLIED tcStart %time; #IMPLIED tcFormat (DF | NDF) #IMPLIED audioLayout (mono | stereo | surround) #IMPLIED audioRate (32k | 44.1k | 48k | 88.2k | 96k | 176.4k | 192k) #IMPLIED renderFormat CDATA #IMPLIED keywords CDATA #IMPLIED>
<!ELEMENT note (#PCDATA)>
<!ELEMENT spine (clip | audition | mc-clip | ref-clip | sync-clip | asset-clip | video | audio | gap | title | transition)*>
<!ATTLIST spine lane CDATA #IMPLIED offset %time; #IMPLIED name CDATA #IMPLIED format IDREF #IMPLIED>
<!ELEMENT asset-clip (note?, conform-rate?, timeMap?, (adjust-transform | adjust-volume)*, (caption | clip | audition | mc-clip | ref-clip | sync-clip | asset-clip | video | audio | gap | title | transition)*, marker*, audio-channel-source*, filter-video*, filter-audio*, metadata?)>
<!ATTLIST asset-clip ref IDREF #REQUIRED lane CDATA #IMPLIED offset %time; #IMPLIED name CDATA #IMPLIED start %time; #IMPLIED duration %time; #REQUIRED enabled (0 | 1) "1" format IDREF #IMPLIED tcStart %time; #IMPLIED tcFormat (DF | NDF) #IMPLIED audioStart %time; #IMPLIED audioDuration %time; #IMPLIED srcEnable (all | audio | video) "all" audioRole CDATA #IMPLIED videoRole CDATA #IMPLIED modDate CDATA #IMPLIED>
<!ELEMENT conform-rate EMPTY>
<!ELEMENT timeMap (timept*)>
<!ELEMENT timept EMPTY>
<!ELEMENT adjust-transform EMPTY>
<!ELEMENT adjust-volume EMPTY>
<!ATTLIST adjust-volume amount CDATA #IMPLIED>
<!ELEMENT clip EMPTY>
<!ELEMENT audition EMPTY>
<!ELEMENT mc-clip EMPTY>
<!ELEMENT ref-clip EMPTY>
<!ELEMENT sync-clip EMPTY>
<!ELEMENT video EMPTY>
<!ATTLIST video ref IDREF #REQUIRED lane CDATA #IMPLIED offset %time; #IMPLIED name CDATA #IMPLIED start %time; #IMPLIED duration %time; #REQUIRED role CDATA #IMPLIED>
<!ELEMENT audio EMPTY>
<!ATTLIST audio ref IDREF #REQUIRED lane CDATA #IMPLIED offset %time; #IMPLIED name CDATA #IMPLIED start %time; #IMPLIED duration %time; #REQUIRED role CDATA #IMPLIED srcCh CDATA #IMPLIED outCh CDATA #IMPLIED>
<!ELEMENT gap EMPTY>
<!ATTLIST gap name CDATA #IMPLIED offset %time; #IMPLIED start %time; #IMPLIED duration %time; #REQUIRED>
<!ELEMENT title EMPTY>
<!ELEMENT transition EMPTY>
<!ELEMENT caption EMPTY>
<!ELEMENT marker EMPTY>
<!ELEMENT audio-channel-source EMPTY>
<!ELEMENT filter-video EMPTY>
<!ELEMENT filter-audio EMPTY>
""".strip()


def validate_fcpxml_1_9_dtd(xml_source: Path | bytes | str) -> dict[str, Any]:
    from lxml import etree as LET  # type: ignore

    if isinstance(xml_source, Path):
        raw_bytes = xml_source.read_bytes()
    elif isinstance(xml_source, str):
        raw_bytes = xml_source.encode("utf-8")
    else:
        raw_bytes = xml_source

    try:
        dtd = LET.DTD(io.StringIO(APPLE_FCPXML_1_9_DTD))
        parser = LET.XMLParser(resolve_entities=False, no_network=True)
        xml_doc = LET.fromstring(raw_bytes, parser=parser)
        valid = bool(dtd.validate(xml_doc))
        if not valid:
            errs = [f"line {e.line}: {e.message}" for e in dtd.error_log]
            return {"ok": False, "error_code": "FCPXML_1_9_DTD_INVALID", "errors": errs}
        return {"ok": True, "errors": []}
    except Exception as exc:
        return {"ok": False, "error_code": "FCPXML_1_9_DTD_INVALID", "errors": [str(exc)]}


def check_and_collect_fcpxml_clips_v42(
    ep_s: str,
    seg_targets: list[tuple[str, float]],
    done_4k_dir: Path,
    raw_gen_dir: Path,
    allow_pre_vsr: bool,
    target_fps: int = DEFAULT_TARGET_FPS,
    extra_done_dirs: list[Path] | None = None,
    legacy_conflict: dict[str, Any] | None = None,
    target_width: int = 3840,
    target_height: int = 2160,
    p4_profile: str = "",
) -> list[dict[str, Any]]:
    tfps = normalize_target_fps(target_fps)
    tw, th = int(target_width or 3840), int(target_height or 2160)
    f_lo, f_hi = fps_bounds(tfps)
    cut_table = build_timeline_cutpoint_table_v42(seg_targets, target_fps=tfps)
    missing_segments: list[dict[str, Any]] = []
    short_video_stream_segments: list[dict[str, Any]] = []
    unqualified_spec_segments: list[dict[str, Any]] = []
    verified_clips: list[dict[str, Any]] = []

    for seg_name, real_dur in seg_targets:
        seg_cut = cut_table["by_segment"].get(seg_name, {})
        d_i = max(1, int(seg_cut.get("duration_frames") or _sec_to_frames_half_up(real_dur, tfps, min_one_frame=True)))
        cands = [done_4k_dir / f"{seg_name}.mp4"]
        for ex_d in (extra_done_dirs or []):
            ex_cand = ex_d / f"{seg_name}.mp4"
            if ex_cand not in cands:
                cands.append(ex_cand)
        if allow_pre_vsr:
            cands.append(raw_gen_dir / f"{seg_name}.mp4")
        picked = next((c for c in cands if c.exists() and c.stat().st_size > 0), None)
        if picked is None:
            missing_segments.append({
                "segment": seg_name,
                "target_duration_sec": real_dur,
                "expected_frames": d_i,
                "searched_paths": [str(c).replace("\\", "/") for c in cands],
            })
            continue

        vmeta = probe_video_streams_v42(picked)
        video_dur = float(vmeta.get("video_duration_sec") or 0.0)
        audio_dur = float(vmeta.get("audio_duration_sec") or 0.0)
        video_frames = int(vmeta.get("video_frames") or 0)
        w = int(vmeta.get("width") or 0)
        h = int(vmeta.get("height") or 0)
        fps = float(vmeta.get("fps") or 0.0)

        if not vmeta.get("ok") or not vmeta.get("has_video") or video_dur <= 0.0:
            unqualified_spec_segments.append({
                "segment": seg_name,
                "file": str(picked).replace("\\", "/"),
                "error": vmeta.get("error", "video_duration<=0"),
            })
            continue

        use_table_frames = (not allow_pre_vsr) or is_fps_compliant(fps, tfps) or fps <= 0.0
        expected_frames = d_i if use_table_frames else int(round(real_dur * fps))
        if (video_frames < expected_frames - 2) or (video_dur + 0.05 < real_dur):
            missing_frames = max(0, expected_frames - video_frames)
            short_video_stream_segments.append({
                "segment": seg_name,
                "file": str(picked).replace("\\", "/"),
                "video_duration_sec": round(video_dur, 4),
                "audio_duration_sec": round(audio_dur, 4),
                "target_duration_sec": real_dur,
                "target_fps": tfps,
                "video_frames": video_frames,
                "expected_frames": expected_frames,
                "missing_frames": missing_frames,
                "shortfall_sec": round(max(0.0, real_dur - video_dur), 4),
            })

        if (not allow_pre_vsr) and (w != tw or h != th or not is_fps_compliant(fps, tfps)):
            unqualified_spec_segments.append({
                "segment": seg_name,
                "file": str(picked).replace("\\", "/"),
                "width": w,
                "height": h,
                "fps": fps,
                "target_fps": tfps,
                "p4_profile": p4_profile,
                "required": f"{tw}x{th} @ {f_lo:.2f}~{f_hi:.2f}fps",
            })

        verified_clips.append({
            "segment": seg_name,
            "path": picked,
            "video_dur": video_dur,
            "real_dur": real_dur,
            "video_frames": video_frames,
            "start_frame": int(seg_cut.get("start_frame") or 0),
            "end_frame": int(seg_cut.get("end_frame") or d_i),
            "duration_frames": d_i,
            "meta": vmeta,
        })

    if missing_segments or short_video_stream_segments or unqualified_spec_segments:
        err_doc: dict[str, Any] = {
            "status": "FCPXML_BUILD_BLOCKED",
            "episode": ep_s,
            "target_fps": tfps,
            "allow_pre_vsr": allow_pre_vsr,
            "missing_segments": missing_segments,
            "short_video_stream_segments": short_video_stream_segments,
            "short_media_segments": short_video_stream_segments,
            "unqualified_spec_segments": unqualified_spec_segments,
        }
        if legacy_conflict and legacy_conflict.get("legacy_plan_fps_conflict"):
            err_doc["legacy_plan_fps_conflict"] = True
            err_doc["legacy_plan_fps_warning"] = str(legacy_conflict.get("legacy_plan_fps_warning") or "")
        print(json.dumps(err_doc, ensure_ascii=False, indent=2))
        sys.exit(2)

    return verified_clips


def _append_media_rep(asset_el: ET.Element, file_path: Path) -> None:
    ET.SubElement(asset_el, "media-rep", kind="original-media", src=file_path.resolve().as_uri())


def _fcpxml_frame_rational(sec: float, target_fps: int = DEFAULT_TARGET_FPS, min_one_frame: bool = False) -> str:
    tfps = normalize_target_fps(target_fps)
    frames = _sec_to_frames_half_up(max(0.0, float(sec)), tfps, min_one_frame=min_one_frame)
    return f"{frames * 100}/{tfps * 100}s"


def build_fcpxml_1_9_tree_and_layout_v42(
    project_root: Path,
    ep_s: str,
    ep_dir: Path,
    work_dir: Path,
    verified_clips: list[dict[str, Any]],
    dlg_lines: list[dict[str, Any]],
    music_files: list[Path],
    probe_wav_fn: Callable[[Path], dict[str, Any]],
    target_fps: int = DEFAULT_TARGET_FPS,
    legacy_conflict: dict[str, Any] | None = None,
    target_width: int = 3840,
    target_height: int = 2160,
    p4_profile: str = "",
) -> tuple[Path, Path, list[tuple[str, str, float, float]], list[dict[str, Any]], list[dict[str, Any]], float, float, bool]:
    tfps = normalize_target_fps(target_fps)
    tw, th = int(target_width or 3840), int(target_height or 2160)
    den = tfps * 100
    cut_table = build_timeline_cutpoint_table_v42([(vc["segment"], float(vc["real_dur"])) for vc in verified_clips], target_fps=tfps)
    fcpxml = ET.Element("fcpxml", version="1.9")
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(
        resources, "format", id="r1",
        name=fcpxml_format_name(tw, th, tfps),
        frameDuration=f"100/{den}s", width=str(tw), height=str(th),
    )

    trimmed_handles_total = 0.0
    seg_offset_map: dict[str, float] = {}
    seg_dur_map: dict[str, float] = {}
    clip_specs: list[tuple[str, str, float, float]] = []

    for idx, vc in enumerate(verified_clips, 1):
        seg_name = vc["segment"]
        res_id = f"r_seg_{idx:02d}"
        video_dur = float(vc["video_dur"])
        v_frames = int(vc.get("video_frames") or vc["meta"].get("video_frames") or _sec_to_frames_half_up(video_dur, tfps, min_one_frame=True))
        seg_cut = cut_table["by_segment"][seg_name]
        start_f = int(seg_cut["start_frame"])
        d_i = max(1, int(seg_cut["duration_frames"]))
        clip_off_s = start_f / float(tfps)
        clip_dur_s = d_i / float(tfps)
        trimmed_handles_total += max(0.0, video_dur - clip_dur_s)
        seg_offset_map[seg_name] = clip_off_s
        seg_dur_map[seg_name] = clip_dur_s
        asset_frames = max(1, v_frames, d_i)
        v_ch = str(max(1, int(vc["meta"].get("channels") or 2)))
        v_asset = ET.SubElement(
            resources, "asset", id=res_id, name=seg_name,
            start="0s", duration=f"{asset_frames * 100}/{den}s",
            hasVideo="1", hasAudio="1", format="r1",
            audioSources="1", audioChannels=v_ch, audioRate="48000",
        )
        _append_media_rep(v_asset, vc["path"])
        clip_specs.append((seg_name, res_id, round(clip_off_s, 6), round(clip_dur_s, 6)))
    total_dur = round(int(cut_table["total_frames"]) / float(tfps), 6)

    audio_mounts: list[dict[str, Any]] = []
    align_records: list[dict[str, Any]] = []
    aud_idx = 0
    for rec in dlg_lines:
        sg, sh, spk, vmode = rec["segment"], rec["shot"], rec["speaker"], rec["voice_mode"]
        st_in = float(rec["start_in_seg_sec"])
        ed_in = float(rec["end_in_seg_sec"])
        seg_cut = cut_table["by_segment"].get(sg)
        in_tl = seg_cut is not None
        seg_start_f = int(seg_cut["start_frame"]) if seg_cut else 0
        seg_d = seg_dur_map.get(sg, 0.0)
        st_in_frames = _sec_to_frames_half_up(max(0.0, st_in), tfps, min_one_frame=False)
        if rec["skip_jimeng"]:
            w_dur = round(max(0.1, ed_in - st_in), 3)
            dur_frames = _sec_to_frames_half_up(w_dur, tfps, min_one_frame=True)
            tl_s = round((seg_start_f + st_in_frames) / float(tfps), 3)
            tl_e = round((seg_start_f + st_in_frames + dur_frames) / float(tfps), 3)
            is_ok = bool(in_tl and st_in >= -0.01 and ed_in <= seg_d + 0.15)
        else:
            wp: Path = rec["wav_path"]
            p_w = probe_wav_fn(wp)
            w_exists = bool(p_w.get("ok") and p_w.get("duration_sec", 0.0) > 0.05)
            w_dur = round(float(p_w.get("duration_sec") or 0.0), 3)
            eff_end = round(st_in + w_dur, 3) if w_dur > 0 else ed_in
            mount_dur = min(w_dur, max(0.1, seg_d - st_in)) if seg_d > st_in else w_dur
            dur_frames = _sec_to_frames_half_up(max(0.04, mount_dur), tfps, min_one_frame=True)
            tl_s = round((seg_start_f + st_in_frames) / float(tfps), 3)
            tl_e = round((seg_start_f + st_in_frames + dur_frames) / float(tfps), 3)
            is_ok = bool(in_tl and w_exists and st_in >= -0.01 and eff_end <= seg_d + 0.20)
            if w_exists:
                aud_idx += 1
                a_id = f"r_aud_{aud_idx:03d}"
                is_dlg = vmode == "说话"
                track_role = "Dialogue_Track" if is_dlg else "OS_Track"
                a_asset_f = max(1, _sec_to_frames_half_up(w_dur, tfps, min_one_frame=True), dur_frames)
                a_asset = ET.SubElement(
                    resources, "asset", id=a_id, name=wp.stem,
                    start="0s", duration=f"{a_asset_f * 100}/{den}s",
                    hasVideo="0", hasAudio="1", audioSources="1", audioChannels="1", audioRate="48000",
                )
                _append_media_rep(a_asset, wp)
                audio_mounts.append({
                    "res_id": a_id, "name": wp.stem, "segment": sg, "offset_in_seg_sec": st_in,
                    "offset_in_seg_frames": st_in_frames, "episode_offset_frames": seg_start_f + st_in_frames,
                    "episode_offset_sec": tl_s, "duration_frames": dur_frames, "duration_sec": mount_dur,
                    "track": track_role, "lane": "1" if is_dlg else "2",
                })
        align_records.append({
            "segment": sg, "shot": sh, "speaker": spk, "voice_mode": vmode,
            "start_in_seg_sec": round(st_in, 3), "end_in_seg_sec": round(ed_in, 3),
            "timeline_start_sec": tl_s, "timeline_end_sec": tl_e,
            "wav_duration_sec": w_dur, "layout_aligned": is_ok,
        })

    return _emit_fcpxml_1_9_and_layout_json(
        project_root, ep_s, ep_dir, work_dir, fcpxml, resources,
        clip_specs, audio_mounts, align_records, music_files, total_dur, trimmed_handles_total, tfps,
        cutpoint_table=cut_table, legacy_conflict=legacy_conflict, p4_profile=p4_profile,
    )


def _emit_fcpxml_1_9_and_layout_json(
    project_root: Path, ep_s: str, ep_dir: Path, work_dir: Path,
    fcpxml: ET.Element, resources: ET.Element,
    clip_specs: list[tuple[str, str, float, float]],
    audio_mounts: list[dict[str, Any]],
    align_records: list[dict[str, Any]],
    music_files: list[Path],
    total_dur: float, trimmed_handles_total: float,
    target_fps: int = DEFAULT_TARGET_FPS,
    cutpoint_table: dict[str, Any] | None = None,
    legacy_conflict: dict[str, Any] | None = None,
    p4_profile: str = "",
) -> tuple[Path, Path, list[tuple[str, str, float, float]], list[dict[str, Any]], list[dict[str, Any]], float, float, bool]:
    tfps = normalize_target_fps(target_fps)
    res_tag = get_p4_profile_spec(p4_profile or None)["res_tag"]
    bgm_mounts: list[dict[str, Any]] = []
    for m_idx, mp in enumerate(music_files, 1):
        m_meta = probe_video_streams_v42(mp)
        m_dur = float(m_meta.get("audio_duration_sec") or m_meta.get("duration_sec") or total_dur or 10.0)
        m_ch = str(max(1, int(m_meta.get("channels") or 2)))
        m_id = f"r_bgm_{m_idx:02d}"
        m_asset = ET.SubElement(
            resources, "asset", id=m_id, name=mp.stem,
            start="0s", duration=_fcpxml_frame_rational(max(m_dur, 0.1), tfps, min_one_frame=True),
            hasVideo="0", hasAudio="1", audioSources="1", audioChannels=m_ch, audioRate="48000",
        )
        _append_media_rep(m_asset, mp)
        bgm_mounts.append({"res_id": m_id, "name": mp.stem, "path": str(mp).replace("\\", "/"), "duration_sec": round(min(m_dur, max(total_dur, 0.1)), 3)})

    den = tfps * 100
    cut_tbl = cutpoint_table or build_timeline_cutpoint_table_v42([(s_n, float(d_s)) for s_n, _r, _o, d_s in clip_specs], target_fps=tfps)
    total_tl_frames = int(cut_tbl["total_frames"])
    seq_dur_rat = f"{max(1, total_tl_frames) * 100}/{den}s"

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", name=f"{project_root.name}_{ep_s}")
    proj = ET.SubElement(event, "project", name=f"{ep_s}_{res_tag}{tfps}fps_三节点破塑母带")
    seq = ET.SubElement(proj, "sequence", format="r1", duration=seq_dur_rat, tcStart="0s", tcFormat="NDF", audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(seq, "spine")

    seg_elems: dict[str, ET.Element] = {}
    first_v_elem: ET.Element | None = None
    for seg_name, res_id, _offset_s, dur_s in clip_specs:
        seg_cut = cut_tbl["by_segment"].get(seg_name)
        f_start = int(seg_cut["start_frame"]) if seg_cut else 0
        d_frames = max(1, int(seg_cut["duration_frames"])) if seg_cut else _sec_to_frames_half_up(dur_s, tfps, min_one_frame=True)
        ve = ET.SubElement(
            spine, "asset-clip", name=seg_name, ref=res_id,
            offset=f"{f_start * 100}/{den}s",
            duration=f"{d_frames * 100}/{den}s", start="0s", format="r1",
        )
        seg_elems[seg_name] = ve
        if first_v_elem is None:
            first_v_elem = ve

    for am in audio_mounts:
        parent_el = seg_elems.get(am["segment"])
        if parent_el is not None and "offset_in_seg_frames" in am:
            off_rat = f"{int(am['offset_in_seg_frames']) * 100}/{den}s"
        elif "episode_offset_frames" in am and parent_el is None:
            off_rat = f"{int(am['episode_offset_frames']) * 100}/{den}s"
        else:
            off_val = am["offset_in_seg_sec"] if parent_el is not None else am["episode_offset_sec"]
            off_rat = _fcpxml_frame_rational(off_val, tfps, min_one_frame=False)
        dur_rat = f"{int(am['duration_frames']) * 100}/{den}s" if "duration_frames" in am else _fcpxml_frame_rational(am["duration_sec"], tfps, min_one_frame=True)
        ET.SubElement(
            parent_el if parent_el is not None else spine, "asset-clip",
            name=am["name"], ref=am["res_id"], lane=am["lane"],
            offset=off_rat, duration=dur_rat, start="0s", audioRole=f"dialogue.{am['track']}",
        )

    for bm in bgm_mounts:
        ET.SubElement(
            first_v_elem if first_v_elem is not None else spine, "asset-clip",
            name=bm["name"], ref=bm["res_id"], lane="-1",
            offset="0s", duration=_fcpxml_frame_rational(bm["duration_sec"], tfps, min_one_frame=True),
            start="0s", audioRole="music.MusicFX_Track",
        )

    out_fcpxml = ep_dir / f"{ep_s}_达芬奇自动时间线.fcpxml"
    ET.indent(fcpxml, space="  ")
    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(fcpxml, encoding="utf-8")
    out_fcpxml.write_bytes(xml_bytes)

    dtd_chk = validate_fcpxml_1_9_dtd(xml_bytes)
    if not dtd_chk.get("ok"):
        err_out: dict[str, Any] = {
            "status": "FCPXML_1_9_DTD_INVALID", "error_code": "FCPXML_1_9_DTD_INVALID",
            "episode": ep_s, "target_fps": tfps,
            "fcpxml_path": str(out_fcpxml).replace("\\", "/"), "dtd_error_log": dtd_chk.get("errors", []),
        }
        if legacy_conflict and legacy_conflict.get("legacy_plan_fps_conflict"):
            err_out["legacy_plan_fps_conflict"] = True
            err_out["legacy_plan_fps_warning"] = str(legacy_conflict.get("legacy_plan_fps_warning") or "")
        print(json.dumps(err_out, ensure_ascii=False, indent=2))
        sys.exit(2)

    eff_total_dur = round(total_tl_frames / float(tfps), 6) if total_tl_frames > 0 else total_dur
    layout_aligned = all(r["layout_aligned"] for r in align_records)
    align_doc: dict[str, Any] = {
        "episode": ep_s, "target_fps": tfps, "verification_stage": "TIMELINE_LAYOUT_PRECHECK_ONLY",
        "total_timeline_frames": total_tl_frames, "total_timeline_duration_sec": round(eff_total_dur, 4),
        "cutpoint_frame_table": cut_tbl, "records_count": len(align_records),
        "timeline_layout_aligned": layout_aligned, "master_render_verified": False, "records": align_records,
    }
    if legacy_conflict and legacy_conflict.get("legacy_plan_fps_conflict"):
        align_doc["legacy_plan_fps_conflict"] = True
        align_doc["legacy_plan_fps_warning"] = str(legacy_conflict.get("legacy_plan_fps_warning") or "")
    align_path = work_dir / "口型与字幕时间点核验记录.json"
    align_path.write_text(json.dumps(align_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_fcpxml, align_path, clip_specs, audio_mounts, bgm_mounts, eff_total_dur, trimmed_handles_total, layout_aligned


def sample_master_video_luma_qc(master_mp4: Path, video_dur: float, seg_count: int) -> dict[str, Any]:
    import numpy as np  # type: ignore
    from PIL import Image

    n_samples = 3 if video_dur <= 0.25 else max(5, min(20, max(seg_count, 5)))
    if video_dur <= 0.05:
        sample_ts = [0.0]
    elif n_samples == 1:
        sample_ts = [round(video_dur * 0.5, 3)]
    else:
        t_lo = min(0.05, video_dur * 0.1)
        t_hi = max(t_lo, video_dur - max(0.05, video_dur * 0.08))
        step = (t_hi - t_lo) / float(max(1, n_samples - 1))
        sample_ts = [round(t_lo + i * step, 3) for i in range(n_samples)]

    sampled_records: list[dict[str, Any]] = []
    luma_arrays: list[Any] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, ts in enumerate(sample_ts):
            fp = Path(tmpdir) / f"sample_{idx:02d}.png"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(master_mp4), "-frames:v", "1", str(fp)],
                capture_output=True, env=utf8_env(), check=False,
            )
            if not fp.is_file() or fp.stat().st_size == 0:
                continue
            with Image.open(fp) as im:
                arr = np.asarray(im.convert("RGB"), dtype=np.float32)
            luma = 0.299 * arr[:, :, 0] + 0.587 * arr[:, :, 1] + 0.114 * arr[:, :, 2]
            m_val = float(np.mean(luma))
            s_val = float(np.std(luma))
            luma_arrays.append(luma)
            sampled_records.append({"frame_index": idx, "time_sec": ts, "luma_mean": round(m_val, 4), "luma_std": round(s_val, 4)})

    if not sampled_records:
        return {"ok": False, "blank_rejected": True, "static_dead_frame": False, "reason": "未能从成片抽取任何有效画面帧", "sampled_frames": [], "luma_std": [], "max_luma_std": 0.0, "mean_luma": 0.0, "max_adjacent_diff": 0.0}

    stds = [r["luma_std"] for r in sampled_records]
    means = [r["luma_mean"] for r in sampled_records]
    adj_diffs = [float(np.mean(np.abs(luma_arrays[i] - luma_arrays[i - 1]))) for i in range(1, len(luma_arrays)) if luma_arrays[i].shape == luma_arrays[i - 1].shape]
    max_std = max(stds)
    mean_luma = float(sum(means) / len(means))
    max_adj = max(adj_diffs) if adj_diffs else 0.0

    pure_black = max_std < 3.0
    static_dead = (video_dur >= 1.0 and len(luma_arrays) >= 3 and max_adj < 0.05)
    blank_rejected = pure_black or static_dead
    reason = ""
    if pure_black:
        reason = f"采样帧最大像素标准差 max_std={max_std:.4f} < 3.0（纯黑或纯色假画面）"
    elif static_dead:
        reason = f"STATIC_DEAD_FRAME: 总长 {video_dur:.2f}s >= 1.0s 但抽样帧画面静止零运动差异 (max_adjacent_diff={max_adj:.6f} < 0.05，拦截静止彩条或死帧)"

    return {
        "ok": not blank_rejected,
        "blank_rejected": blank_rejected,
        "static_dead_frame": static_dead,
        "reason": reason,
        "sampled_frames": sampled_records,
        "luma_std": stds,
        "max_luma_std": round(max_std, 4),
        "mean_luma": round(mean_luma, 4),
        "max_adjacent_diff": round(max_adj, 4),
    }


def _parse_ts_sec(raw_ts: Any) -> float:
    if isinstance(raw_ts, (int, float)):
        return float(raw_ts)
    s = str(raw_ts or "").strip().replace(",", ".").rstrip("s秒")
    if not s:
        return 0.0
    ps = s.split(":")
    try:
        if len(ps) == 3:
            return float(ps[0]) * 3600.0 + float(ps[1]) * 60.0 + float(ps[2])
        if len(ps) == 2:
            return float(ps[0]) * 60.0 + float(ps[1])
        return float(s)
    except Exception:
        return 0.0


def _reanchor_raw_ep_sec_v42(raw_sec: float, seg_hint: str, cutpoint_table: dict[str, Any] | None) -> float:
    if not cutpoint_table or not cutpoint_table.get("segments"):
        return raw_sec
    by_seg = cutpoint_table.get("by_segment") or {}
    if seg_hint and seg_hint in by_seg:
        se = by_seg[seg_hint]
        if float(se["raw_start_sec"]) - 0.05 <= raw_sec <= float(se["raw_end_sec"]) + 0.50:
            return max(0.0, float(se["timeline_start_sec"]) + (raw_sec - float(se["raw_start_sec"])))
    segs = cutpoint_table["segments"]
    for idx_s, se in enumerate(segs):
        if idx_s == len(segs) - 1 or raw_sec <= float(se["raw_end_sec"]) + 1e-6:
            return max(0.0, float(se["timeline_start_sec"]) + (raw_sec - float(se["raw_start_sec"])))
    return raw_sec


def load_subtitle_entries_v42(
    ep_dir: Path, ep_s: str, seg_tl_off: dict[str, float], cutpoint_table: dict[str, Any] | None = None
) -> dict[str, Any]:
    sub_files: list[str] = []
    entries: list[dict[str, Any]] = []
    by_seg = (cutpoint_table.get("by_segment") if cutpoint_table else None) or {}
    csv_p = ep_dir / f"{ep_s}_字幕时间轴.csv"
    if csv_p.is_file() and csv_p.stat().st_size > 0:
        sub_files.append(str(csv_p).replace("\\", "/"))
        with csv_p.open("r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                txt = str(row.get("内容") or row.get("台词") or row.get("text") or row.get("字幕") or "").strip()
                tr = str(row.get("时间") or row.get("time") or "").strip()
                sg = str(row.get("segment") or "").strip()
                sh = str(row.get("shot") or "").strip()
                s_ep, e_ep = 0.0, 0.0
                if "-->" in tr:
                    p1, p2 = tr.split("-->", 1)
                    s_ep = _reanchor_raw_ep_sec_v42(_parse_ts_sec(p1), sg, cutpoint_table)
                    e_ep = _reanchor_raw_ep_sec_v42(_parse_ts_sec(p2), sg, cutpoint_table)
                elif row.get("start_sec") not in (None, ""):
                    s_ep = _reanchor_raw_ep_sec_v42(_parse_ts_sec(row.get("start_sec")), sg, cutpoint_table)
                    e_ep = _reanchor_raw_ep_sec_v42(_parse_ts_sec(row.get("end_sec")), sg, cutpoint_table)
                elif row.get("start_in_seg_sec") not in (None, ""):
                    off = float(by_seg[sg]["timeline_start_sec"]) if sg in by_seg else seg_tl_off.get(sg, 0.0)
                    s_ep = off + _parse_ts_sec(row.get("start_in_seg_sec"))
                    e_ep = off + _parse_ts_sec(row.get("end_in_seg_sec"))
                if txt or e_ep > s_ep:
                    entries.append({"segment": sg, "shot": sh, "start_sec": round(s_ep, 3), "end_sec": round(e_ep, 3), "text": txt})

    srt_cands = [ep_dir / f"{ep_s}_字幕时间轴.srt", *sorted(ep_dir.glob("*.srt"))]
    seen_srt: set[Path] = set()
    for sp in srt_cands:
        if not sp.is_file() or sp.stat().st_size == 0 or sp.resolve() in seen_srt:
            continue
        seen_srt.add(sp.resolve())
        sub_files.append(str(sp).replace("\\", "/"))
        raw_srt = sp.read_text(encoding="utf-8-sig", errors="replace")
        for blk in re.split(r"\r?\n\r?\n+", raw_srt.strip()):
            lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]
            for i_l, ln in enumerate(lines):
                if "-->" in ln:
                    p1, p2 = ln.split("-->", 1)
                    txt_s = " ".join(lines[i_l + 1 :]).strip()
                    s_srt = _reanchor_raw_ep_sec_v42(_parse_ts_sec(p1), "", cutpoint_table)
                    e_srt = _reanchor_raw_ep_sec_v42(_parse_ts_sec(p2), "", cutpoint_table)
                    entries.append({"segment": "", "shot": "", "start_sec": round(s_srt, 3), "end_sec": round(e_srt, 3), "text": txt_s})
                    break
    return {"has_subtitle_file": len(sub_files) > 0, "subtitle_files": sub_files, "entries": entries}


def extract_fcpxml_dialogue_mounts_v42(fcpxml_path: Path) -> list[dict[str, Any]]:
    mounts: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(fcpxml_path.read_bytes())
    except Exception:
        return mounts
    for sp in root.findall(".//spine"):
        for v_el in list(sp):
            p_off = parse_fcpxml_rational_sec(v_el.get("offset"))
            p_st = parse_fcpxml_rational_sec(v_el.get("start"))
            v_role = str(v_el.get("audioRole") or v_el.get("role") or "")
            if v_role.startswith("dialogue") or str(v_el.get("lane") or "") in ("1", "2"):
                d0 = parse_fcpxml_rational_sec(v_el.get("duration"))
                mounts.append({"name": str(v_el.get("name") or ""), "segment": "", "t_start": round(p_off, 3), "t_end": round(p_off + d0, 3), "audio_role": v_role})
            for ch_el in list(v_el):
                ar = str(ch_el.get("audioRole") or ch_el.get("role") or "")
                ln = str(ch_el.get("lane") or "")
                if ar.startswith("dialogue") or ln in ("1", "2"):
                    c_off = parse_fcpxml_rational_sec(ch_el.get("offset"))
                    c_dur = parse_fcpxml_rational_sec(ch_el.get("duration"))
                    ts = max(0.0, p_off + (c_off - p_st))
                    mounts.append({"name": str(ch_el.get("name") or ""), "segment": str(v_el.get("name") or ""), "t_start": round(ts, 3), "t_end": round(ts + c_dur, 3), "audio_role": ar or f"lane_{ln}"})
    return mounts


def measure_dialogue_windows_rms_v42(
    project_root: Path,
    ep_s: str,
    master_mp4: Path,
    fcpxml_path: Path,
    cspec: dict[str, Any],
    sub_info: dict[str, Any],
    seg_tl_off: dict[str, float],
    resolve_dlg_fn: Callable[..., list[dict[str, Any]]],
    probe_wav_fn: Callable[[Path], dict[str, Any]],
    cutpoint_table: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import numpy as np  # type: ignore

    by_seg = (cutpoint_table.get("by_segment") if cutpoint_table else None) or {}
    fcpxml_mounts = extract_fcpxml_dialogue_mounts_v42(fcpxml_path)
    used_mount_idx: set[int] = set()
    dlg_sched = resolve_dlg_fn(project_root, ep_s, cuts_spec=cspec)
    expected_windows: list[dict[str, Any]] = []

    for rec in dlg_sched:
        sg, sh = rec["segment"], rec["shot"]
        fn_stem = Path(str(rec.get("out_fname") or "")).stem
        matched_i = next((i for i, m in enumerate(fcpxml_mounts) if i not in used_mount_idx and fn_stem and m["name"] == fn_stem), None)
        if matched_i is not None:
            used_mount_idx.add(matched_i)
            t_s, t_e = fcpxml_mounts[matched_i]["t_start"], fcpxml_mounts[matched_i]["t_end"]
        else:
            s_off = float(by_seg[sg]["timeline_start_sec"]) if sg in by_seg else seg_tl_off.get(sg, (cspec.get("seg_ep_bounds") or {}).get(sg, (0.0, 0.0))[0])
            st_in = float(rec.get("start_in_seg_sec") or 0.0)
            ed_in = float(rec.get("end_in_seg_sec") or (st_in + 0.5))
            if (not rec.get("skip_jimeng")) and isinstance(rec.get("wav_path"), Path) and rec["wav_path"].is_file():
                pw = probe_wav_fn(rec["wav_path"])
                wd = float(pw.get("duration_sec") or 0.0)
                if wd > 0.05:
                    ed_in = st_in + wd
            t_s = round(s_off + st_in, 3)
            t_e = round(s_off + max(st_in + 0.08, ed_in), 3)
        expected_windows.append({
            "segment": sg, "shot": sh, "speaker": rec.get("speaker", ""),
            "voice_mode": rec.get("voice_mode", "说话"), "text": rec.get("text", ""),
            "t_start": t_s, "t_end": t_e, "source": "schedule",
        })

    for i, m in enumerate(fcpxml_mounts):
        if i not in used_mount_idx and m["t_end"] > m["t_start"] + 0.04:
            vmode = "心声" if ("OS_Track" in m["audio_role"] or "_心声" in m["name"]) else "说话"
            expected_windows.append({
                "segment": m["segment"] or "segment_01", "shot": "", "speaker": "",
                "voice_mode": vmode, "text": "", "t_start": m["t_start"], "t_end": m["t_end"], "source": "fcpxml_mount",
            })

    if not expected_windows:
        for se in sub_info.get("entries", []):
            if se["end_sec"] > se["start_sec"] + 0.04 and se["text"] and "无台词" not in se["text"]:
                expected_windows.append({
                    "segment": se["segment"], "shot": se["shot"], "speaker": "",
                    "voice_mode": "说话", "text": se["text"], "t_start": se["start_sec"], "t_end": se["end_sec"], "source": "subtitle_timeline",
                })

    if not expected_windows:
        return {"ok": True, "silent_rejected": False, "windows": [], "silent_windows": []}

    sr = 16000
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(master_mp4), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
            capture_output=True, env=utf8_env(), check=True,
        )
        pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float64)
    except Exception:
        pcm = np.zeros(0, dtype=np.float64)

    measured_list: list[dict[str, Any]] = []
    silent_list: list[dict[str, Any]] = []
    for w_it in expected_windows:
        i0 = max(0, int(round(float(w_it["t_start"]) * sr)))
        i1 = min(int(pcm.size), int(round(float(w_it["t_end"]) * sr)))
        if i1 <= i0 or pcm.size == 0:
            rms_db = -120.0
        else:
            rms = float(np.sqrt(np.mean(np.square(pcm[i0:i1]))))
            rms_db = round(20.0 * math.log10(rms / 32768.0), 3) if rms > 1e-9 else -120.0
        rec_out = {**w_it, "measured_rms_db": rms_db}
        measured_list.append(rec_out)
        if rms_db < -50.0:
            silent_list.append(rec_out)

    return {"ok": len(silent_list) == 0, "silent_rejected": len(silent_list) > 0, "windows": measured_list, "silent_windows": silent_list}


def verify_master_post_render_v42(
    project_root: Path,
    ep_s: str,
    master_mp4: Path,
    fcpxml_path: Path,
    align_json_path: Path,
    audit_dir: Path,
    hw_info: dict[str, Any],
    cspec: dict[str, Any],
    resolve_dlg_fn: Callable[..., list[dict[str, Any]]],
    probe_wav_fn: Callable[[Path], dict[str, Any]],
    target_fps: int = DEFAULT_TARGET_FPS,
    legacy_conflict: dict[str, Any] | None = None,
    target_width: int = 3840,
    target_height: int = 2160,
    p4_profile: str = "",
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> dict[str, Any]:
    tfps = normalize_target_fps(target_fps)
    tw, th = int(target_width or 3840), int(target_height or 2160)
    ar = normalize_aspect_ratio(aspect_ratio)
    f_lo, f_hi = fps_bounds(tfps)
    ep_dir = project_root / "分集" / ep_s
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    work_dir.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    dtd_res = validate_fcpxml_1_9_dtd(fcpxml_path)
    if not dtd_res.get("ok"):
        failures.append(f"FCPXML_1_9_DTD_INVALID: {dtd_res.get('errors')}")

    meta = probe_video_streams_v42(master_mp4)
    if not meta.get("ok"):
        failures.append(f"成片 ffprobe 探测失败: {meta.get('error')}")
    w, h = int(meta.get("width") or 0), int(meta.get("height") or 0)
    fps = float(meta.get("fps") or 0.0)
    has_audio = bool(meta.get("has_audio"))
    actual_dur = float(meta.get("video_duration_sec") or 0.0)
    actual_frames = int(meta.get("video_frames") or 0)

    if w != tw or h != th:
        failures.append(f"分辨率不达标: 实测 {w}x{h} != {tw}x{th}")
    if not is_fps_compliant(fps, tfps):
        failures.append(f"帧率不达标: 实测 fps={fps} 不在 target_fps={tfps} 合规区间 [{f_lo:.2f}, {f_hi:.2f}]")
    if not has_audio:
        failures.append("音轨缺失: has_audio != True")

    seg_durations = [(sk, float(cspec["seg_real_dur"].get(sk, 0.0))) for sk in cspec["ordered_segments"] if float(cspec["seg_real_dur"].get(sk, 0.0)) > 0]
    cut_table = build_timeline_cutpoint_table_v42(seg_durations, target_fps=tfps)
    seg_count = len(seg_durations)
    expected_frames = int(cut_table["total_frames"])
    expected_dur = float(cut_table["total_timeline_duration_sec"]) if expected_frames > 0 else float(cspec["expected_total_dur"])
    tol_sec = max(0.5, seg_count * 0.05)
    tol_frames = int(math.ceil(tol_sec * float(tfps)))
    if cspec["cuts_path"] is None or seg_count == 0 or expected_frames <= 0:
        failures.append("缺少有效 cuts.csv，无法校验预期总时长与总帧数")
    else:
        if abs(actual_dur - expected_dur) > tol_sec:
            failures.append(f"视频流总时长误差超限: 实测 {actual_dur:.3f}s vs 预期 {expected_dur:.3f}s (容差 ±{tol_sec:.3f}s)")
        if abs(actual_frames - expected_frames) > tol_frames:
            failures.append(f"视频流可读总帧数误差超限: 实测 {actual_frames}帧 vs 预期 {expected_frames}帧 (容差 ±{tol_frames}帧)")

    seg_tl_off = {s["segment"]: float(s["timeline_start_sec"]) for s in cut_table["segments"]}
    luma_qc = sample_master_video_luma_qc(master_mp4, actual_dur, seg_count)
    if luma_qc["blank_rejected"]:
        failures.append(f"MASTER_VERIFY_REJECTED_BLANK_VIDEO: {luma_qc['reason']}")

    sub_info = load_subtitle_entries_v42(ep_dir, ep_s, seg_tl_off, cutpoint_table=cut_table)
    dlg_qc = measure_dialogue_windows_rms_v42(
        project_root, ep_s, master_mp4, fcpxml_path, cspec, sub_info, seg_tl_off, resolve_dlg_fn, probe_wav_fn, cutpoint_table=cut_table,
    )
    if dlg_qc["silent_rejected"]:
        failures.append(f"MASTER_VERIFY_REJECTED_SILENT_DIALOGUE_WINDOW: 发现 {len(dlg_qc['silent_windows'])} 个静音对白窗口 (rms_db < -50.0 dBFS)")

    return _finalize_master_verify_report_v42(
        ep_s, work_dir, audit_dir, master_mp4, fcpxml_path, align_json_path, hw_info,
        meta, expected_dur, expected_frames, tol_sec, luma_qc, dlg_qc, sub_info, dtd_res, failures, tfps,
        cutpoint_table=cut_table, legacy_conflict=legacy_conflict,
        target_width=tw, target_height=th, p4_profile=p4_profile, aspect_ratio=ar,
    )


def _finalize_master_verify_report_v42(
    ep_s: str, work_dir: Path, audit_dir: Path, master_mp4: Path, fcpxml_path: Path, align_json_path: Path,
    hw_info: dict[str, Any], meta: dict[str, Any], expected_dur: float, expected_frames: int, tol_sec: float,
    luma_qc: dict[str, Any], dlg_qc: dict[str, Any], sub_info: dict[str, Any], dtd_res: dict[str, Any], failures: list[str],
    target_fps: int = DEFAULT_TARGET_FPS,
    cutpoint_table: dict[str, Any] | None = None,
    legacy_conflict: dict[str, Any] | None = None,
    target_width: int = 3840,
    target_height: int = 2160,
    p4_profile: str = "",
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
) -> dict[str, Any]:
    tfps = normalize_target_fps(target_fps)
    ar = normalize_aspect_ratio(aspect_ratio)
    prof_spec = get_p4_profile_spec(p4_profile or None, aspect_ratio=ar)
    layout_ok = False
    if not align_json_path.is_file() or align_json_path.stat().st_size == 0:
        failures.append(f"缺少时间线排布预检文件: {align_json_path.name}")
    else:
        try:
            adoc = json.loads(align_json_path.read_text(encoding="utf-8-sig"))
            layout_ok = bool(adoc.get("timeline_layout_aligned") is True or adoc.get("all_aligned") is True)
            if not layout_ok:
                failures.append("口型与字幕时间点核验记录中 timeline_layout_aligned != True")
        except Exception as exc:
            failures.append(f"时间线排布预检文件解析失败: {exc}")

    missing_subs: list[dict[str, Any]] = []
    if dlg_qc["windows"]:
        subs = sub_info.get("entries", [])
        if not sub_info.get("has_subtitle_file") or not subs:
            failures.append(f"存在 {len(dlg_qc['windows'])} 条对白但缺失非空字幕时间轴文件 (.csv/.srt)")
        else:
            for dw in dlg_qc["windows"]:
                matched_sub = any(
                    (se["end_sec"] > se["start_sec"] and min(dw["t_end"], se["end_sec"]) >= max(dw["t_start"], se["start_sec"]) - 1.0)
                    or (dw.get("text") and se.get("text") and (dw["text"] in se["text"] or se["text"] in dw["text"]))
                    or (dw.get("segment") and se.get("segment") == dw["segment"] and (not dw.get("shot") or not se.get("shot") or se["shot"] == dw["shot"]))
                    for se in subs
                )
                if not matched_sub and dw.get("voice_mode", "说话") == "说话":
                    missing_subs.append({"segment": dw.get("segment"), "shot": dw.get("shot"), "t_start": dw["t_start"], "t_end": dw["t_end"], "text": dw.get("text", "")})
            if missing_subs:
                failures.append(f"字幕时间轴缺少对应开口对白条目: {len(missing_subs)} 处")

    master_sha = sha256_file(master_mp4)
    fcpxml_sha = sha256_file(fcpxml_path)
    passed = len(failures) == 0
    human_ok, human_doc, human_rec_path = (
        load_valid_human_master_review_v43(audit_dir, work_dir, ep_s, master_sha) if passed else (False, None, None)
    )

    if luma_qc["blank_rejected"]:
        status_code = "MASTER_VERIFY_REJECTED_BLANK_VIDEO"
    elif dlg_qc["silent_rejected"]:
        status_code = "MASTER_VERIFY_REJECTED_SILENT_DIALOGUE_WINDOW"
    elif not dtd_res.get("ok"):
        status_code = "FCPXML_1_9_DTD_INVALID"
    elif not passed:
        status_code = "MASTER_VERIFY_FAILED"
    else:
        status_code = "MASTER_SEALED_OK" if human_ok else "MASTER_TECH_SPEC_PASSED"

    sub_status = (
        ("技术规格通过且人工已确认对白口型与字幕" if human_ok else "技术规格通过（待剪辑师人工确认对白、口型与字幕后封板）")
        if passed
        else f"未通过_{status_code}"
    )

    master_mp4_str = str(master_mp4).replace("\\", "/")
    post_qc_path = work_dir / "母带渲染后音画字幕核验记录.json"
    if passed:
        post_qc_doc: dict[str, Any] = {
            "status": status_code, "episode": ep_s, "target_fps": tfps,
            "technical_spec_passed": True, "human_review_verified": bool(human_ok),
            "master_mp4_path": master_mp4_str, "verified_master_mp4": master_mp4_str,
            "verified_master_sha256": master_sha,
            "verified_fcpxml_path": str(fcpxml_path).replace("\\", "/"),
            "verified_fcpxml_sha256": fcpxml_sha,
            "luma_std": luma_qc["luma_std"], "max_luma_std": luma_qc["max_luma_std"],
            "sampled_frames": luma_qc["sampled_frames"], "dialogue_windows": dlg_qc["windows"],
            "subtitle_files": sub_info["subtitle_files"], "subtitle_entries_count": len(sub_info["entries"]),
        }
        post_qc_path.write_text(json.dumps(post_qc_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    report: dict[str, Any] = {
        "status": status_code,
        "error_code": "" if passed else status_code,
        "episode": ep_s,
        "p4_profile": prof_spec["p4_profile"],
        "profile_status": prof_spec["profile_status"],
        "aspect_ratio": ar,
        "target_resolution": f"{int(target_width)}x{int(target_height)}",
        "target_fps": tfps,
        "technical_spec_passed": passed,
        "human_review_verified": bool(passed and human_ok),
        "human_review_record": str(human_rec_path).replace("\\", "/") if (passed and human_ok and human_rec_path) else "",
        "human_reviewer": str((human_doc or {}).get("reviewer") or "") if (passed and human_ok) else "",
        "hardware_probe": hw_info,
        "fcpxml_timeline_exists": fcpxml_path.exists(),
        "fcpxml_1_9_dtd_valid": bool(dtd_res.get("ok")),
        "master_mp4_path": master_mp4_str,
        "master_mp4_exists": master_mp4.exists(),
        "verified_master_sha256": master_sha if passed else "",
        "verified_fcpxml_sha256": fcpxml_sha if passed else "",
        "post_render_qc_record": str(post_qc_path).replace("\\", "/") if passed else "",
        "master_metadata": meta,
        "expected_duration_sec": round(expected_dur, 3),
        "expected_frames": expected_frames,
        f"expected_frames_{tfps}fps": expected_frames,
        "duration_tolerance_sec": round(tol_sec, 3),
        "timeline_layout_aligned": layout_ok,
        "cutpoint_frame_table": cutpoint_table or {},
        "video_luma_qc": luma_qc,
        "dialogue_audio_qc": {"windows_count": len(dlg_qc["windows"]), "windows": dlg_qc["windows"], "silent_dialogue_windows": dlg_qc["silent_windows"]},
        "silent_dialogue_windows": dlg_qc["silent_windows"],
        "missing_subtitle_windows": missing_subs,
        "failures": failures,
        "subtitle_status": sub_status,
    }
    if legacy_conflict and legacy_conflict.get("legacy_plan_fps_conflict"):
        report["legacy_plan_fps_conflict"] = True
        report["legacy_plan_fps_warning"] = str(legacy_conflict.get("legacy_plan_fps_warning") or "")
    out_txt = json.dumps(report, ensure_ascii=False, indent=2)
    (audit_dir / f"{ep_s}_第四棒成片母带终检.json").write_text(out_txt, encoding="utf-8")
    print(out_txt)
    if not passed:
        sys.exit(2)
    return report


def is_banned_ai_reviewer_v43(reviewer: str) -> bool:
    s = str(reviewer or "").strip()
    if len(re.sub(r"\s+", "", s)) < 2:
        return True
    low = s.lower()
    if low in {"none", "null", "n/a", "na", "unknown", "default", "todo", "tbd", "system", "model"}:
        return True
    if any(tok in low for tok in BANNED_AI_REVIEWER_TOKENS):
        return True
    if re.search(r"(?:^|[^a-z0-9])ai(?:$|[^a-z0-9])", low):
        return True
    if any(cn in s for cn in ("智能体", "子代理", "大模型", "机器人", "自动审核", "自动确认")):
        return True
    return False


def load_valid_human_master_review_v43(
    audit_dir: Path, work_dir: Path, ep_s: str, expected_master_sha: str
) -> tuple[bool, dict[str, Any] | None, Path | None]:
    exp_sha = str(expected_master_sha or "").strip().lower()
    if not exp_sha:
        return False, None, None
    cands = [audit_dir / f"{ep_s}_成片人工回看确认记录.json", work_dir / f"{ep_s}_成片人工回看确认记录.json"]
    for cp in cands:
        if not cp.is_file() or cp.stat().st_size == 0:
            continue
        try:
            doc = json.loads(cp.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        c_sha = str(doc.get("confirmed_master_sha256") or "").strip().lower()
        rev = str(doc.get("reviewer") or "").strip()
        notes_c = re.sub(r"\s+", "", str(doc.get("notes") or ""))
        three_ok = (
            doc.get("confirm_dialogue") is True
            and doc.get("confirm_lipsync") is True
            and doc.get("confirm_subtitles") is True
        )
        if c_sha == exp_sha and three_ok and (not is_banned_ai_reviewer_v43(rev)) and len(notes_c) >= 12:
            return True, doc, cp
    return False, None, None


def confirm_master_human_review_v43(
    project_root: Path,
    ep_s: str,
    reviewer: str,
    confirm_dialogue: bool,
    confirm_lipsync: bool,
    confirm_subtitles: bool,
    notes: str,
    master_mp4_override: str | Path | None = None,
    target_fps: int | None = None,
    p4_profile: str | None = None,
    aspect_ratio: str | None = None,
) -> dict[str, Any]:
    ep_dir = project_root / "分集" / ep_s
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    audit_dir = project_root / "中间文件" / "索引与制作记录" / "交付自查状态"
    work_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    p4b_path = audit_dir / f"{ep_s}_第四棒成片母带终检.json"
    failures: list[str] = []
    p4b_doc: dict[str, Any] = {}
    if not p4b_path.is_file() or p4b_path.stat().st_size == 0:
        failures.append(f"缺少第四棒成片母带技术终检记录: {p4b_path.name}（请先运行 phase4-verify-master）")
    else:
        try:
            p4b_doc = json.loads(p4b_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            failures.append(f"解析 {p4b_path.name} 失败: {exc}")
    tech_ok = bool(p4b_doc.get("technical_spec_passed") is True or p4b_doc.get("status") in ("MASTER_TECH_SPEC_PASSED", "MASTER_SEALED_OK"))
    if p4b_doc and not tech_ok:
        failures.append(f"技术规格终检尚未通过 (status={p4b_doc.get('status')!r}, technical_spec_passed={p4b_doc.get('technical_spec_passed')!r})")

    tfps = normalize_target_fps(target_fps if target_fps not in (None, "") else (p4b_doc.get("target_fps") or DEFAULT_TARGET_FPS))
    ar_hint = aspect_ratio or (str(p4b_doc.get("aspect_ratio") or "").strip() or None)
    prof_spec = resolve_p4_profile_v46(
        project_root, ep_s,
        explicit_profile=(p4_profile or str(p4b_doc.get("p4_profile") or "") or None),
        explicit_aspect_ratio=ar_hint,
    )
    eff_prof = prof_spec["p4_profile"] or None
    if master_mp4_override and str(master_mp4_override).strip():
        raw_mp = Path(str(master_mp4_override).strip())
        master_mp4 = raw_mp.resolve() if (raw_mp.is_absolute() or raw_mp.exists()) else (project_root / raw_mp).resolve()
    elif p4b_doc.get("master_mp4_path"):
        rp = Path(str(p4b_doc["master_mp4_path"]))
        master_mp4 = rp.resolve() if rp.is_absolute() else (project_root / rp).resolve()
    else:
        master_mp4 = (ep_dir / default_master_mp4_name(ep_s, tfps, p4_profile=eff_prof)).resolve()

    cur_sha = sha256_file(master_mp4).lower() if (master_mp4.is_file() and master_mp4.stat().st_size > 0) else ""
    rep_sha = str(p4b_doc.get("verified_master_sha256") or "").strip().lower()
    if not cur_sha:
        failures.append(f"待确认成片母带不存在或为空: {master_mp4}")
    elif not rep_sha or cur_sha != rep_sha:
        failures.append(f"成片 SHA-256 与技术终检记录不一致 (current={cur_sha[:12]}, verified={rep_sha[:12]})，严禁跨版本复用确认")

    rev_clean = str(reviewer or "").strip()
    if is_banned_ai_reviewer_v43(rev_clean):
        failures.append(f"HUMAN_REVIEWER_INVALID_OR_AI_NAME: reviewer={rev_clean!r} 为空或包含受禁 AI/自动化标识")
    if not (confirm_dialogue is True and confirm_lipsync is True and confirm_subtitles is True):
        failures.append("必须同时显式传入 --confirm-dialogue --confirm-lipsync --confirm-subtitles 三项人工确认")
    notes_clean = str(notes or "").strip()
    notes_len = len(re.sub(r"\s+", "", notes_clean))
    if notes_len < 12:
        failures.append(f"人工回看确认备注去空白长度不足 12 字 (当前 {notes_len} 字)")

    if failures:
        err_doc = {"status": "MASTER_CONFIRM_REJECTED", "error_code": "MASTER_CONFIRM_REJECTED", "episode": ep_s, "target_fps": tfps, "master_mp4_path": str(master_mp4).replace("\\", "/"), "failures": failures}
        print(json.dumps(err_doc, ensure_ascii=False, indent=2))
        sys.exit(2)

    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    rec_audit_p = audit_dir / f"{ep_s}_成片人工回看确认记录.json"
    rec_work_p = work_dir / f"{ep_s}_成片人工回看确认记录.json"
    confirm_doc = {
        "status": "HUMAN_MASTER_REVIEW_CONFIRMED", "episode": ep_s, "target_fps": tfps,
        "aspect_ratio": prof_spec.get("aspect_ratio", DEFAULT_ASPECT_RATIO),
        "master_mp4_path": str(master_mp4).replace("\\", "/"), "confirmed_master_sha256": cur_sha,
        "reviewer": rev_clean, "confirm_dialogue": True, "confirm_lipsync": True, "confirm_subtitles": True,
        "notes": notes_clean, "confirmed_at_utc": now_utc,
    }
    rec_txt = json.dumps(confirm_doc, ensure_ascii=False, indent=2)
    rec_audit_p.write_text(rec_txt, encoding="utf-8")
    rec_work_p.write_text(rec_txt, encoding="utf-8")
    p4b_doc.update({
        "status": "MASTER_SEALED_OK", "error_code": "", "technical_spec_passed": True, "human_review_verified": True,
        "human_review_record": str(rec_audit_p).replace("\\", "/"), "human_reviewer": rev_clean, "confirmed_at_utc": now_utc,
        "subtitle_status": "技术规格通过且人工已确认对白口型与字幕",
    })
    p4b_path.write_text(json.dumps(p4b_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    out_res = {"status": "MASTER_SEALED_OK", "episode": ep_s, "target_fps": tfps, "technical_spec_passed": True, "human_review_verified": True, "confirmed_master_sha256": cur_sha, "reviewer": rev_clean, "human_review_record": str(rec_audit_p).replace("\\", "/"), "master_verify_report": str(p4b_path).replace("\\", "/"), "subtitle_status": "技术规格通过且人工已确认对白口型与字幕"}
    print(json.dumps(out_res, ensure_ascii=False, indent=2))
    return out_res


def execute_step2_lipsync_pipeline(
    raw_vid_dir: Path | str,
    dlg_schedule: list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
    out_dir: Path | str,
    latentsync_repo: Path | str,
    dlg_wav_dir: Path | str | None = None,
    unet_config_path: Path | str | None = None,
    inference_ckpt_path: Path | str | None = None,
    whisper_ckpt_path: Path | str | None = None,
    require_whisper_ckpt: bool = True,
    bootstrap_if_missing: bool = False,
    git_url: str | None = None,
    pinned_commit: str = LATENTSYNC_PINNED_COMMIT,
    inference_steps: int = 20,
    guidance_scale: float = 1.5,
    python_bin: str = "python",
    overwrite_source: bool = False,
    target_fps: int = DEFAULT_TARGET_FPS,
) -> dict[str, Any]:
    tfps = normalize_target_fps(target_fps)
    f_lo, f_hi = fps_bounds(tfps)
    vid_dir = Path(raw_vid_dir).resolve()
    dst_dir = Path(out_dir).resolve()
    repo_dir = Path(latentsync_repo).resolve()
    wav_base = Path(dlg_wav_dir).resolve() if dlg_wav_dir else None
    dst_dir.mkdir(parents=True, exist_ok=True)
    mp4_files = (sorted(vid_dir.glob("segment_*.mp4")) or sorted(vid_dir.glob("*.mp4"))) if vid_dir.is_dir() else []
    pending_segs = {(re.search(r"(segment_\d+)", p.stem).group(1) if re.search(r"(segment_\d+)", p.stem) else p.stem) for p in mp4_files}

    flat_items: list[dict[str, Any]] = []
    if isinstance(dlg_schedule, dict):
        for seg_k, arr in dlg_schedule.items():
            for it in (arr or []):
                flat_items.append({**it, "segment": it.get("segment") or seg_k})
    else:
        flat_items = list(dlg_schedule or [])

    seg_lines_map: dict[str, list[dict[str, Any]]] = {}
    for rec in flat_items:
        fn_raw = str(rec.get("out_fname") or rec.get("file_name") or "").strip()
        if rec.get("skip_jimeng") or fn_raw.upper() == "SKIP_JIMENG_NATIVE":
            continue
        is_os = (str(rec.get("voice_mode") or "") == "心声") or ("_心声" in fn_raw)
        needs_ls = bool(rec["needs_lipsync"]) if "needs_lipsync" in rec else (not is_os)
        if not needs_ls:
            continue
        m_sg = re.search(r"(segment_\d+)", f"{rec.get('segment', '')}_{rec.get('unit_key', '')}_{fn_raw}")
        seg_id = m_sg.group(1) if m_sg else str(rec.get("segment") or "segment_01")
        if mp4_files and seg_id not in pending_segs:
            continue
        wp_cand = rec.get("wav_path") or rec.get("wav") or rec.get("audio_path")
        wp = Path(wp_cand).resolve() if wp_cand else ((wav_base / fn_raw).resolve() if (wav_base and fn_raw) else None)
        if wp is None or not wp.is_file():
            raise FileNotFoundError(f"缺少待刷口型对白音频: {wp or fn_raw}")
        st_s = float(rec.get("start_in_seg_sec") if rec.get("start_in_seg_sec") is not None else (rec.get("offset_in_seg_sec") or 0.0))
        seg_lines_map.setdefault(seg_id, []).append({"segment": seg_id, "start_in_seg_sec": max(0.0, st_s), "wav": wp, "speaker": str(rec.get("speaker") or "")})

    has_active_dlg = any(bool(seg_lines_map.get(re.search(r"(segment_\d+)", p.stem).group(1) if re.search(r"(segment_\d+)", p.stem) else p.stem)) for p in mp4_files)
    if not has_active_dlg and not (not mp4_files and seg_lines_map):
        results_skip: list[dict[str, Any]] = []
        for mp4_path in mp4_files:
            mp4_abs = mp4_path.resolve()
            m_s = re.search(r"(segment_\d+)", mp4_abs.stem)
            seg_id = m_s.group(1) if m_s else mp4_abs.stem
            out_mp4 = (dst_dir / mp4_abs.name).resolve()
            if mp4_abs != out_mp4:
                shutil.copy2(mp4_abs, out_mp4)
            results_skip.append({"segment": seg_id, "mode": "passthrough", "output_video": str(out_mp4)})
        return {"status": "STEP2_LIPSYNC_SKIPPED_NO_DIALOGUE", "lipsync_initialized": False, "processed_count": len(results_skip), "segments": results_skip}

    eff_git_url = str(git_url or LATENTSYNC_GIT_URL).strip()
    eff_commit = str(pinned_commit or LATENTSYNC_PINNED_COMMIT).strip()
    if bootstrap_if_missing:
        if not (repo_dir / ".git").exists() and not (repo_dir / "scripts" / "inference.py").is_file():
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", eff_git_url, str(repo_dir)], env=utf8_env(), check=True)
        if (repo_dir / ".git").exists() and eff_commit:
            subprocess.run(["git", "checkout", "--detach", eff_commit], cwd=str(repo_dir), env=utf8_env(), check=True)
            head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), capture_output=True, text=True, env=utf8_env(), check=True).stdout.strip().lower()
            if not head_sha.startswith(eff_commit.lower()):
                raise RuntimeError(f"LatentSync HEAD 校验失配: {head_sha} 不匹配固定提交 {eff_commit}")

    unet_cfg = Path(unet_config_path).resolve() if unet_config_path else (repo_dir / LATENTSYNC_DEFAULT_UNET_CFG_REL)
    ckpt_pt = Path(inference_ckpt_path).resolve() if inference_ckpt_path else (repo_dir / LATENTSYNC_DEFAULT_CKPT_REL)
    whisper_pt = Path(whisper_ckpt_path).resolve() if whisper_ckpt_path else (repo_dir / LATENTSYNC_DEFAULT_WHISPER_REL)
    if not repo_dir.is_dir():
        raise FileNotFoundError(f"LatentSync 仓库目录不存在: {repo_dir}")
    if not unet_cfg.is_file():
        raise FileNotFoundError(f"缺少 LatentSync UNet 配置文件: {unet_cfg}")
    if not ckpt_pt.is_file():
        raise FileNotFoundError(f"缺少 LatentSync 权重文件: {ckpt_pt}")
    if require_whisper_ckpt and not whisper_pt.is_file():
        raise FileNotFoundError(f"缺少 LatentSync Whisper 权重文件: {whisper_pt}")

    results: list[dict[str, Any]] = []
    for mp4_path in mp4_files:
        mp4_abs = mp4_path.resolve()
        m_s = re.search(r"(segment_\d+)", mp4_abs.stem)
        seg_id = m_s.group(1) if m_s else mp4_abs.stem
        lines = sorted(seg_lines_map.get(seg_id, []), key=lambda x: x["start_in_seg_sec"])
        out_mp4 = (dst_dir / mp4_abs.name).resolve()
        if not lines:
            if mp4_abs != out_mp4:
                shutil.copy2(mp4_abs, out_mp4)
            results.append({"segment": seg_id, "mode": "passthrough", "output_video": str(out_mp4)})
            continue

        vmeta = probe_video_streams_v42(mp4_abs)
        n_src = max(1, int(vmeta.get("video_frames") or 0))
        src_fps = float(vmeta.get("fps") or 24.0)
        vid_dur = max(0.2, float(vmeta.get("video_duration_sec") or vmeta.get("duration_sec") or (n_src / max(1.0, src_fps))))
        has_fps_mode = "fps_mode" in subprocess.run(["ffmpeg", "-hide_banner", "-h", "long"], capture_output=True, text=True, env=utf8_env()).stdout
        relabel_args = (lambda f: ["-fps_mode", "passthrough", "-enc_time_base", f"1/{f}"] if has_fps_mode else ["-vsync", "0", "-r", str(f)])
        is_native_24 = (23.95 <= src_fps <= 24.05)
        norm_in_mp4 = mp4_abs
        if tfps == 24 and is_native_24:
            n_work = n_src
            expected_frames = n_src
            expected_dur = n_src / 24.0
        elif tfps == 24:
            expected_frames = max(1, int(round(vid_dur * 24.0)))
            expected_dur = expected_frames / 24.0
            norm24_mp4 = (dst_dir / f"{seg_id}_norm24.mp4").resolve()
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4_abs), "-an", "-vf", "fps=24", "-frames:v", str(expected_frames), "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p", str(norm24_mp4)], capture_output=True, env=utf8_env(), check=True)
            norm_in_mp4 = norm24_mp4
            n_work = expected_frames
        else:
            n_work = n_src
            expected_frames = max(1, int(round(vid_dur * tfps)))
            expected_dur = vid_dur
        driver_wav = (dst_dir / f"{seg_id}_lipsync_driver.wav").resolve()
        in_args, f_parts, mix_tags, delays_ms = [], [], [], []
        for i, ln in enumerate(lines):
            in_args.extend(["-i", str(ln["wav"])])
            ms = max(0, int(round(float(ln["start_in_seg_sec"]) * 1000)))
            delays_ms.append(ms)
            f_parts.append(f"[{i}:a]adelay={ms}|{ms}[a{i}]")
            mix_tags.append(f"[a{i}]")
        use_pullup = (tfps == 24) or (abs(src_fps - 25.0) > 0.05)
        ls_in_mp4 = norm_in_mp4
        if use_pullup:
            pullup_mp4 = (dst_dir / f"{seg_id}_pullup25.mp4").resolve()
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(norm_in_mp4), "-an", "-vf", "settb=1/25,setpts=N/(25*TB)", *relabel_args(25), "-c:v", "libx264", "-crf", "10", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p", str(pullup_mp4)], capture_output=True, env=utf8_env(), check=True)
            ls_in_mp4 = pullup_mp4
            drv_dur_25 = (n_work + 1) / 25.0
            atempo_str = "25/24" if (tfps == 24 or abs(src_fps - 24.0) <= 0.15) else f"{25.0 / max(1.0, src_fps):.6f}"
            mix_dur = expected_dur if tfps == 24 else vid_dur
            raw_mix_wav = (dst_dir / f"{seg_id}_mix_orig.wav").resolve()
            fc_str = ";".join(f_parts) + ";" + "".join(mix_tags) + f"amix=inputs={len(lines)}:duration=longest:dropout_transition=0:normalize=0,apad=whole_dur={mix_dur:.6f},atrim=0:{mix_dur:.6f}[drv]"
            subprocess.run(["ffmpeg", "-y", "-v", "error", *in_args, "-filter_complex", fc_str, "-map", "[drv]", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(raw_mix_wav)], capture_output=True, env=utf8_env(), check=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw_mix_wav), "-af", f"atempo={atempo_str},apad=whole_dur={drv_dur_25:.6f}", "-t", f"{drv_dur_25:.6f}", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(driver_wav)], capture_output=True, env=utf8_env(), check=True)
        else:
            fc_str = ";".join(f_parts) + ";" + "".join(mix_tags) + f"amix=inputs={len(lines)}:duration=longest:dropout_transition=0:normalize=0,apad=whole_dur={vid_dur:.3f},atrim=0:{vid_dur:.3f}[drv]"
            subprocess.run(["ffmpeg", "-y", *in_args, "-filter_complex", fc_str, "-map", "[drv]", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(driver_wav)], capture_output=True, env=utf8_env(), check=True)

        lipsync_vid = (dst_dir / f"{seg_id}_latentsync_raw.mp4").resolve()
        ls_cmd = [
            python_bin, "-m", "scripts.inference",
            "--unet_config_path", str(unet_cfg), "--inference_ckpt_path", str(ckpt_pt),
            "--inference_steps", str(inference_steps), "--guidance_scale", str(guidance_scale),
            "--video_path", str(ls_in_mp4), "--audio_path", str(driver_wav), "--video_out_path", str(lipsync_vid),
        ]
        subprocess.run(ls_cmd, cwd=str(repo_dir), env=utf8_env(), check=True)
        if not lipsync_vid.is_file() or lipsync_vid.stat().st_size == 0:
            raise RuntimeError(f"LatentSync 输出缺失或为空，严禁回退原片: {lipsync_vid}")

        if tfps == 24:
            remux_cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(lipsync_vid), "-i", str(mp4_abs), "-map", "0:v:0", "-map", "1:a:0?", "-vf", "settb=1/24,setpts=N/(24*TB)", *relabel_args(24), "-c:v", "libx264", "-crf", "10", "-preset", "fast", "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-b:a", "256k", str(out_mp4)]
        else:
            remux_cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(lipsync_vid), "-i", str(mp4_abs), "-vf", f"fps={tfps}", "-t", f"{vid_dur:.4f}", "-c:v", "libx264", "-crf", "10", "-preset", "fast", "-profile:v", "high", "-pix_fmt", "yuv420p", "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "aac", "-ar", "48000", "-b:a", "256k", str(out_mp4)]
        subprocess.run(remux_cmd, capture_output=True, env=utf8_env(), check=True)
        out_meta = probe_video_streams_v42(out_mp4)
        out_fps = float(out_meta.get("fps") or 0.0)
        if not out_meta.get("ok") or not (f_lo <= out_fps <= f_hi):
            raise RuntimeError(f"Step 1 口型封装后帧率未落入 [{f_lo:.2f}, {f_hi:.2f}]fps: {out_mp4.name} -> {out_meta}")
        actual_frames = int(out_meta.get("video_frames") or 0)
        actual_video_dur = float(out_meta.get("video_duration_sec") or out_meta.get("duration_sec") or 0.0)
        if abs(actual_frames - expected_frames) > 1 or abs(actual_video_dur - expected_dur) > 0.08:
            missing_frames = max(0, expected_frames - actual_frames)
            excess_frames = max(0, actual_frames - expected_frames)
            raise RuntimeError(f"Step 1 口型帧锁定双向核验失败: {out_mp4.name} missing_frames={missing_frames} excess_frames={excess_frames} (actual_frames={actual_frames}, expected_frames={expected_frames}, actual_video_dur={actual_video_dur:.4f}s, expected_dur={expected_dur:.4f}s)")
        if overwrite_source and out_mp4 != mp4_abs:
            shutil.copy2(out_mp4, mp4_abs)
        results.append({"segment": seg_id, "mode": "latentsync_adelay_pullup25" if use_pullup else "latentsync_adelay", "source_fps": src_fps, "source_frames": n_src, "expected_frames": expected_frames, "output_frames": actual_frames, "output_duration_sec": actual_video_dur, "lines_count": len(lines), "delays_ms": delays_ms, "driver_wav": str(driver_wav), "lipsync_raw_video": str(lipsync_vid), "output_video": str(out_mp4), "output_fps": out_fps, "target_fps": tfps, "command": ls_cmd})

    return {"status": "STEP2_LIPSYNC_OK", "lipsync_initialized": True, "pinned_commit": eff_commit, "target_fps": tfps, "processed_count": len(results), "segments": results}


def clean_spk_v42(raw_spk: str) -> str:
    s = re.sub(r"^角色\d+_", "", str(raw_spk or "").strip())
    s = re.sub(r"^\d+_", "", s)
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"[（(].*?[)）]", "", s).strip()
    return s


def infer_spk_mode_from_filename(fname: str, expected_roles: list[dict[str, str]] | None = None) -> tuple[str, str, str]:
    stem = Path(fname).stem
    vmode = "心声" if ("心声" in stem or "_OS" in stem.upper()) else ("说话" if "说话" in stem else "")
    for r in (expected_roles or []):
        rn, rid = r.get("new_name", ""), r.get("role_id", "")
        if rn and (rn in stem or (rid and f"{rid}_" in stem)):
            return rid, rn, vmode
    m = re.match(r"^(角色\d+)_([^_]+)_(说话|心声)", stem)
    if m:
        return m.group(1), m.group(2), m.group(3)
    m2 = re.match(r"^([^_]+)_(说话|心声)", stem)
    if m2:
        return "", m2.group(1), m2.group(2)
    return "", clean_spk_v42(stem), vmode


def load_approved_voice_ref_triplets_v42(
    project_root: Path,
    ep_s: str,
    expected_roles: list[dict[str, str]] | None = None,
) -> tuple[Path | None, dict[tuple[str, str], str]]:
    rec_manifest = project_root / "中间文件" / "索引与制作记录" / "声线母本批准清单.json"
    ep_manifest = project_root / "分集" / ep_s / "声线" / "声线母本批准清单.json"
    existing = [p for p in (rec_manifest, ep_manifest) if p.is_file() and p.stat().st_size > 0]
    if not existing:
        return None, {}
    existing.sort(key=lambda p: p.stat().st_mtime)
    triplet_map: dict[tuple[str, str], str] = {}

    def _put(spk_raw: str, vm_raw: str, sha_raw: str) -> None:
        spk_s = str(spk_raw or "").strip()
        vm_s = "心声" if ("心声" in str(vm_raw or "") or str(vm_raw or "").upper() == "OS") else ("说话" if "说话" in str(vm_raw or "") else str(vm_raw or "").strip())
        sha_s = str(sha_raw or "").strip().lower()
        if not spk_s or vm_s not in ("说话", "心声") or len(sha_s) < 32:
            return
        triplet_map[(spk_s, vm_s)] = sha_s
        c_spk = clean_spk_v42(spk_s)
        if c_spk:
            triplet_map[(c_spk, vm_s)] = sha_s

    for mp in existing:
        try:
            doc = json.loads(mp.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        recs: list[Any] = []
        if isinstance(doc, list):
            recs = doc
        elif isinstance(doc, dict):
            for k_list in ("approved_voice_refs", "triplets", "records", "items", "voice_refs", "voices"):
                if isinstance(doc.get(k_list), list):
                    recs.extend(doc[k_list])
                    break
            for k_map in ("approved_map", "triplets_map", "approved_triplets"):
                if isinstance(doc.get(k_map), dict):
                    for mk, mv in doc[k_map].items():
                        if isinstance(mv, dict):
                            for vm_k, sh_v in mv.items():
                                _put(str(mk), str(vm_k), str(sh_v))
                        elif isinstance(mv, str):
                            for sep in ("|", ":", "/"):
                                if sep in str(mk):
                                    p1, p2 = str(mk).split(sep, 1)
                                    _put(p1, p2, mv)
                                    break
                            else:
                                if mk.endswith(("_说话", "_心声")):
                                    _put(mk[:-3], mk[-2:], mv)
        for it in recs:
            if isinstance(it, (list, tuple)) and len(it) >= 3:
                _put(str(it[0]), str(it[1]), str(it[2]))
            elif isinstance(it, dict):
                if it.get("approved") is False:
                    continue
                sr_v = it.get("sample_rate")
                if sr_v is not None and int(sr_v or 0) != 48000:
                    continue
                fn = str(it.get("file_name") or it.get("file") or Path(str(it.get("path") or "")).name or "")
                _, inf_spk, inf_vm = infer_spk_mode_from_filename(fn, expected_roles) if fn else ("", "", "")
                spk = str(it.get("speaker") or it.get("character") or it.get("new_name") or it.get("role_name") or inf_spk or "").strip()
                vm = str(it.get("voice_mode") or it.get("mode") or inf_vm or "").strip()
                sha = str(it.get("approved_sha256") or it.get("sha256") or "").strip()
                _put(spk, vm, sha)

    return (existing[-1] if triplet_map else None), triplet_map


def measure_wav_silence_ratio(wav_path: Path) -> float:
    import numpy as np  # type: ignore

    try:
        with wave.open(str(wav_path), "rb") as wf:
            fr = wf.getframerate()
            ch = max(1, wf.getnchannels())
            sw = wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
        if sw != 2 or fr <= 0 or not raw:
            return 1.0
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if ch > 1:
            arr = arr.reshape(-1, ch).mean(axis=1)
        win = max(160, int(fr * 0.02))
        n_frames = len(arr) // win
        if n_frames <= 0:
            return 0.0 if float(np.sqrt(np.mean(arr * arr))) >= 185.0 else 1.0
        trimmed = arr[: n_frames * win].reshape(n_frames, win)
        rms = np.sqrt(np.mean(trimmed * trimmed, axis=1))
        return round(float(np.mean(rms < 185.0)), 4)
    except Exception:
        return 1.0


def run_voice_audit_and_approve_v42(
    project_root: Path,
    ep_s: str,
    expected_roles: list[dict[str, str]],
    probe_wav_fn: Callable[[Path], dict[str, Any]],
    hw_mode: str = "PURE_CPU_CLOUD_API_MODE",
) -> dict[str, Any]:
    ep_voice_dir = project_root / "分集" / ep_s / "声线"
    root_voice_dir = project_root / "声线参考"
    record_dir = project_root / "中间文件" / "索引与制作记录"
    audit_dir = record_dir / "交付自查状态"
    ep_voice_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    wav_files = sorted(ep_voice_dir.glob("*.wav"))
    if not wav_files and root_voice_dir.is_dir():
        for sw in sorted(root_voice_dir.glob("*_声线参考.wav")):
            shutil.copy2(sw, ep_voice_dir / sw.name)
        wav_files = sorted(ep_voice_dir.glob("*.wav"))

    failures: list[str] = []
    details: list[dict[str, Any]] = []
    approved_triplets: list[dict[str, Any]] = []
    hashes: set[str] = set()
    valid_wavs: list[Path] = []
    for wp in wav_files:
        wm = probe_wav_fn(wp)
        dur = float(wm.get("duration_sec") or 0.0)
        sr = int(wm.get("sample_rate") or 0)
        ch = int(wm.get("channels") or 0)
        codec = str(wm.get("codec_name") or "")
        sha = str(wm.get("sha256") or "")
        sil = measure_wav_silence_ratio(wp)
        rid, spk, vmode = infer_spk_mode_from_filename(wp.name, expected_roles)
        spec_ok = bool(wm.get("ok") and (2.00 <= dur <= 3.00) and sr == 48000 and ch == 1 and codec == "pcm_s16le" and sil <= 0.35 and vmode in ("说话", "心声") and spk)
        if not spec_ok:
            failures.append(f"VOICE_REF_NOT_APPROVED_OR_NOT_48K: {wp.name} 不合规 (dur={dur:.3f}s, sr={sr}, ch={ch}, codec={codec}, silence={sil:.3f}, spk={spk}, mode={vmode})")
        else:
            valid_wavs.append(wp)
            approved_triplets.append({"role_id": rid, "speaker": spk, "voice_mode": vmode, "file_name": wp.name, "sample_rate": sr, "channels": ch, "codec_name": codec, "duration_sec": dur, "silence_ratio": sil, "approved_sha256": sha, "sha256": sha})
        if sha:
            hashes.add(sha)
        details.append({"file": wp.name, "path": str(wp).replace("\\", "/"), "speaker": spk, "voice_mode": vmode, "compliant": spec_ok, "silence_ratio": sil, **wm})

    return _finish_voice_audit_and_approve_v42(project_root, ep_s, ep_voice_dir, record_dir, audit_dir, expected_roles, wav_files, valid_wavs, hashes, details, approved_triplets, failures, hw_mode)


def _finish_voice_audit_and_approve_v42(
    project_root: Path, ep_s: str, ep_voice_dir: Path, record_dir: Path, audit_dir: Path,
    expected_roles: list[dict[str, str]], wav_files: list[Path], valid_wavs: list[Path],
    hashes: set[str], details: list[dict[str, Any]], approved_triplets: list[dict[str, Any]],
    failures: list[str], hw_mode: str,
) -> dict[str, Any]:
    if len(wav_files) < 2:
        failures.append("VOICE_REF_NOT_APPROVED_OR_NOT_48K: 声线目录少于 2 条母本")
    if wav_files and len(hashes) != len(wav_files):
        failures.append("VOICE_REF_NOT_APPROVED_OR_NOT_48K: 存在重复 SHA-256 声线母本")
    for rinfo in expected_roles:
        rname, rid = rinfo["new_name"], rinfo["role_id"]
        for req_m in ("说话", "心声"):
            if not any(req_m in vp.stem and (rname in vp.stem or (rid and f"{rid}_" in vp.stem)) for vp in valid_wavs):
                failures.append(f"VOICE_REF_NOT_APPROVED_OR_NOT_48K: {rid}_{rname} 缺少 48kHz 合规【{req_m}】母本")

    passed = len(failures) == 0
    rec_manifest = record_dir / "声线母本批准清单.json"
    ep_manifest = ep_voice_dir / "声线母本批准清单.json"
    if passed:
        app_doc = {
            "status": "VOICE_REF_APPROVED",
            "episode": ep_s,
            "sample_rate_locked": 48000,
            "channels_locked": 1,
            "codec_locked": "pcm_s16le",
            "approved_voice_refs": approved_triplets,
            "records": approved_triplets,
        }
        app_txt = json.dumps(app_doc, ensure_ascii=False, indent=2)
        rec_manifest.write_text(app_txt, encoding="utf-8")
        ep_manifest.write_text(app_txt, encoding="utf-8")

    report = {
        "status": "VOICE_AUDIT_OK" if passed else "VOICE_REF_NOT_APPROVED_OR_NOT_48K",
        "error_code": "" if passed else "VOICE_REF_NOT_APPROVED_OR_NOT_48K",
        "hardware_mode": hw_mode,
        "project_root": str(project_root).replace("\\", "/"),
        "episode": ep_s,
        "voice_dir": str(ep_voice_dir).replace("\\", "/"),
        "approved_manifest": str(rec_manifest).replace("\\", "/") if passed else "",
        "wav_count": len(details),
        "all_duration_2_to_3s_and_48k_pcm16_ok": passed,
        "all_voices_physically_distinct": len(hashes) == len(details),
        "failures": failures,
        "records": details,
    }
    out_txt = json.dumps(report, ensure_ascii=False, indent=2)
    (audit_dir / f"{ep_s}_声线审计报告.json").write_text(out_txt, encoding="utf-8")
    print(out_txt)
    if not passed:
        sys.exit(2)
    return report


def verify_voice_refs_48k_approved_v42(
    project_root: Path,
    ep_s: str,
    required_items: list[dict[str, Any]],
    expected_roles: list[dict[str, str]],
    probe_wav_fn: Callable[[Path], dict[str, Any]],
    locate_fn: Callable[[Path, Path, str, str, str], Path | None],
    check_all_ep_wavs: bool = False,
) -> dict[str, Any]:
    ep_voice_dir = project_root / "分集" / ep_s / "声线"
    root_voice_dir = project_root / "声线参考"
    manifest_p, triplet_map = load_approved_voice_ref_triplets_v42(project_root, ep_s, expected_roles)
    issues: list[dict[str, Any]] = []

    if manifest_p is None or not triplet_map:
        issues.append({"speaker": "*", "voice_mode": "*", "reason": "VOICE_REF_NOT_APPROVED_OR_NOT_48K: 缺少物理批准清单 声线母本批准清单.json 或清单为空", "affected_shots": []})

    if check_all_ep_wavs:
        wavs = sorted(ep_voice_dir.glob("*.wav")) if ep_voice_dir.is_dir() else []
        if len(wavs) < 2:
            issues.append({"speaker": "*", "voice_mode": "*", "reason": "VOICE_REF_NOT_APPROVED_OR_NOT_48K: 分集声线目录少于 2 条 .wav", "affected_shots": []})
        seen_shas: set[str] = set()
        for wp in wavs:
            wm = probe_wav_fn(wp)
            dur = float(wm.get("duration_sec") or 0.0)
            _, spk_i, vm_i = infer_spk_mode_from_filename(wp.name, expected_roles)
            if not wm.get("ok") or not (2.00 <= dur <= 3.00) or wm.get("sample_rate") != 48000 or wm.get("channels") != 1 or wm.get("codec_name") != "pcm_s16le":
                issues.append({"speaker": spk_i, "voice_mode": vm_i, "file": wp.name, "reason": f"VOICE_REF_NOT_APPROVED_OR_NOT_48K: {wp.name} 非 48000Hz/Mono/pcm_s16le/2-3s (sr={wm.get('sample_rate')}, dur={dur:.3f}s)", "affected_shots": []})
            else:
                exp_sha = triplet_map.get((spk_i, vm_i)) or triplet_map.get((clean_spk_v42(spk_i), vm_i))
                if not exp_sha or str(wm.get("sha256") or "").lower() != exp_sha:
                    issues.append({"speaker": spk_i, "voice_mode": vm_i, "file": wp.name, "reason": f"VOICE_REF_NOT_APPROVED_OR_NOT_48K: ({spk_i}, {vm_i}) 未在批准清单找到对应 approved_sha256 或与实时 WAV SHA-256 不匹配", "affected_shots": []})
            if wm.get("sha256"):
                seen_shas.add(str(wm["sha256"]))
        if wavs and len(seen_shas) != len(wavs):
            issues.append({"speaker": "*", "voice_mode": "*", "reason": "VOICE_REF_NOT_APPROVED_OR_NOT_48K: 声线目录存在重复 SHA-256 假文件", "affected_shots": []})

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for req in required_items:
        spk = str(req.get("speaker") or "").strip()
        vmode = str(req.get("voice_mode") or "说话").strip()
        rname = str(req.get("ref_name") or "").strip()
        shot_tag = str(req.get("shot_tag") or "")
        ref_p = locate_fn(ep_voice_dir, root_voice_dir, rname, spk, vmode)
        err = ""
        if ref_p is None or not ref_p.is_file() or ref_p.stat().st_size == 0:
            err = f"VOICE_REF_NOT_APPROVED_OR_NOT_48K: 缺失 ({spk}, {vmode}) 48kHz 声线母本"
        else:
            wm = probe_wav_fn(ref_p)
            dur = float(wm.get("duration_sec") or 0.0)
            if not wm.get("ok") or not (2.00 <= dur <= 3.00) or wm.get("sample_rate") != 48000 or wm.get("channels") != 1 or wm.get("codec_name") != "pcm_s16le":
                err = f"VOICE_REF_NOT_APPROVED_OR_NOT_48K: 母本非 48000Hz/Mono/pcm_s16le/2-3s: {ref_p.name} (sr={wm.get('sample_rate')}, ch={wm.get('channels')}, codec={wm.get('codec_name')}, dur={dur:.3f}s)"
            else:
                exp_sha = triplet_map.get((spk, vmode)) or triplet_map.get((clean_spk_v42(spk), vmode))
                if not exp_sha or str(wm.get("sha256") or "").lower() != exp_sha:
                    err = f"VOICE_REF_NOT_APPROVED_OR_NOT_48K: ({spk}, {vmode}) 未获批准清单授权或实时 SHA-256 失配 ({ref_p.name})"
        if err:
            ent = grouped.setdefault((spk, vmode), {"speaker": spk, "voice_mode": vmode, "expected_ref": rname, "reason": err, "affected_shots": []})
            if shot_tag and shot_tag not in ent["affected_shots"]:
                ent["affected_shots"].append(shot_tag)

    all_issues = issues + list(grouped.values())
    return {"ok": len(all_issues) == 0, "approved_manifest_path": str(manifest_p).replace("\\", "/") if manifest_p else "", "issues": all_issues}
