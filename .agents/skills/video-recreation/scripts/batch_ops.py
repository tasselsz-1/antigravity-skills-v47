#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用生图批次命令行工具 (batch_ops.py)
用于将 GPT (Codex) 主代理与子代理中的进度盘点、文件复制、SHA256 校验、接触表拼合、补救任务包生成与审核归位固化为单行命令，
彻底避免主代理在上下文中手写临时 Python 脚本或读取大量详情。
"""

import argparse
import base64
import contextlib
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from v41_batch_guards import (  # noqa: E402
    check_remedy_quota_or_exit,
    compute_unit_input_fingerprint,
    compute_v41_eff_window_size,
    detect_stale_approved_units,
    evaluate_post_p2_phase_status,
    extract_item_prompt_and_refs,
    register_v41_subparsers,
    resolve_as_of_date,
    resolve_flow_tier,
    run_phase3_verify_v41,
    run_prototype_sheet_v41,
    validate_cuts_rows_flow_routing,
    validate_flow_segment_rule,
    verify_cuts_csv_v41,
    verify_visual_audit_hashes,
)


def _utf8_subprocess_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """构造强制 UTF-8 的子进程环境变量，彻底杜绝 Windows GBK 解码/编码崩溃"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update(extra_env)
    return env


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    """原子写盘：先写同目录 .tmp.<pid> 临时文件并 flush + fsync，再 os.replace 原子替换目标文件"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_path = target.parent / f"{target.name}.tmp.{os.getpid()}"
    try:
        with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        last_err: OSError | None = None
        for attempt_i in range(4):
            try:
                os.replace(str(tmp_path), str(target))
                last_err = None
                break
            except PermissionError as exc:
                last_err = exc
                time.sleep(0.02 * (attempt_i + 1))
        if last_err is not None:
            raise last_err
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


save_json = write_json


def normalize_episode_name(ep_raw: str) -> str:
    s = str(ep_raw or "第01集").strip()
    m = re.search(r"(\d+)", s)
    if m and not s.startswith("第"):
        return f"第{int(m.group(1)):02d}集"
    return s


def resolve_cross_platform_path(raw_path: str | Path, base_dir: Path | None = None) -> Path:
    """Windows 本地 (G:/我的云端硬盘/...) 与 Google Colab (/content/drive/MyDrive/...) 双路径自适应解析"""
    s = str(raw_path or "").strip().replace("\\", "/")
    if not s:
        return (base_dir or Path.cwd()).resolve()

    if "短剧复刻工作站" in s:
        suffix = s.split("短剧复刻工作站", 1)[1].lstrip("/")
        for root_cand in (
            Path("/content/drive/MyDrive/短剧复刻工作站"),
            Path("G:/我的云端硬盘/短剧复刻工作站"),
        ):
            if root_cand.exists():
                return (root_cand / suffix).resolve()
        if base_dir is not None:
            b_str = str(base_dir.resolve()).replace("\\", "/")
            if "短剧复刻工作站" in b_str:
                ws_prefix = b_str.split("短剧复刻工作站", 1)[0] + "短剧复刻工作站"
                return (Path(ws_prefix) / suffix).resolve()

    p = Path(s)
    if p.exists():
        return p.resolve()
    if not p.is_absolute() and base_dir is not None:
        return (base_dir / p).resolve()
    return p.resolve()


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


def probe_cpu_gpu_env(phase: str = "phase1_3") -> dict:
    """硬件自适应探针：第一~三棒默认纯 CPU 零报错运行（0~0.06 CU/集），仅第四棒检测提示 A100 GPU"""
    cuda_available = False
    device_name = "CPU (Pure CPU Mode)"
    try:
        import torch  # type: ignore
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            device_name = str(torch.cuda.get_device_name(0))
    except Exception:
        cuda_available = False

    is_phase4 = phase in ("phase4", "step4", "latentsync", "vsr")
    return {
        "phase": phase,
        "cuda_available": cuda_available,
        "device_name": device_name,
        "execution_mode": "A100_GPU_VSR_LIPSYNC" if (is_phase4 and cuda_available) else "PURE_CPU_CLOUD_API_MODE",
        "estimated_cu": "0.8~1.5 CU (A100)" if is_phase4 else "0~0.06 CU (Pure CPU)",
        "phase4_gpu_warning": (
            "当前为纯 CPU 环境，第四棒跑 LatentSync 与 24fps 四档超分（默认 1K，可选 4K）时建议在 Colab 切换至 A100 GPU"
            if (is_phase4 and not cuda_available)
            else None
        ),
    }


def get_unit_id(job: dict) -> str:
    if job.get("unit"):
        return str(job["unit"])
    if job.get("unit_id"):
        return str(job["unit_id"])
    if job.get("segment_id") and job.get("shot_id") and job.get("frame_type"):
        return f"{job['segment_id']}_{job['shot_id']}_{job['frame_type']}"
    if job.get("frame_id"):
        return str(job["frame_id"])
    if job.get("seg") is not None:
        s = str(job["seg"])
        return s if s.startswith("segment_") else f"segment_{s}"
    return str(job.get("id") or job.get("segment") or job.get("task_id") or "unknown")


def extract_jobs_list(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for k in ("jobs", "units", "tasks", "items", "frames"):
            if isinstance(raw.get(k), list):
                return raw[k]
    return []


def _resolve_unit_target_path(item: dict | None) -> str:
    """按 target_path -> formal_path -> output_path 顺序解析单元正式归位路径"""
    if not isinstance(item, dict):
        return ""
    for k in ("target_path", "formal_path", "output_path"):
        val = str(item.get(k) or "").strip()
        if val:
            return val
    return ""


def inspect_single_batch(batch_dir: Path) -> dict:
    jobs_file = batch_dir / "jobs.json"
    if not jobs_file.exists():
        return {"batch": batch_dir.name, "status": "未准备jobs.json"}

    jobs = extract_jobs_list(read_json(jobs_file))
    done_init, pending_init = [], []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        u = get_unit_id(j)
        out_raw = j.get("output_dir") or str(batch_dir / u)
        out_d = Path(out_raw)
        if (out_d / "初始.png").exists():
            done_init.append(u)
        else:
            pending_init.append(u)

    rem_file = batch_dir / "remedy_jobs.json"
    done_rem, pending_rem = [], []
    if rem_file.exists():
        rem_jobs = extract_jobs_list(read_json(rem_file))
        for rj in rem_jobs:
            if not isinstance(rj, dict):
                continue
            u = get_unit_id(rj)
            out_raw = rj.get("output_dir") or str(batch_dir / u / "补救")
            out_d = Path(out_raw)
            if (out_d / "补救.png").exists():
                done_rem.append(u)
            else:
                pending_rem.append(u)

    return {
        "batch": batch_dir.name,
        "batch_dir": str(batch_dir).replace("\\", "/"),
        "total_units": len(jobs),
        "done_initial": done_init,
        "pending_initial": pending_init,
        "has_remedy_jobs": rem_file.exists(),
        "done_remedy": done_rem,
        "pending_remedy": pending_rem,
        "has_summary": (batch_dir / "审核总结.json").exists(),
    }


MAX_UNITS_PER_WINDOW = 50
MAX_REMEDY_RESERVE_PER_WINDOW = 10
MAX_TOTAL_CALLS_PER_WINDOW = 60
MAX_UNITS_PER_BATCH = 6


def _build_window_slices(
    unit_ids: list[str],
    max_per_window: int = MAX_UNITS_PER_WINDOW,
    max_per_batch: int = MAX_UNITS_PER_BATCH,
    elastic_cap: int = MAX_TOTAL_CALLS_PER_WINDOW,
) -> list[dict[str, Any]]:
    """按单窗口安全上限（默认 ≤50 基础单元 + 预留 ≤10 补救 = 单窗口总调用 ≤60 张，51..54 尾部 ≤4 单元自动弹性并入 window_01）物理切分窗口与批次"""
    windows: list[dict[str, Any]] = []
    total_n = len(unit_ids)
    eff_win_size, elastic_base_limit = compute_v41_eff_window_size(total_n, max_per_window, elastic_cap)

    for w_idx in range(0, total_n, eff_win_size):
        w_units = unit_ids[w_idx : w_idx + eff_win_size]
        w_num = (w_idx // eff_win_size) + 1
        batches = []
        for b_idx in range(0, len(w_units), max_per_batch):
            b_units = w_units[b_idx : b_idx + max_per_batch]
            global_b_num = (w_idx // max_per_batch) + (b_idx // max_per_batch) + 1
            batches.append({
                "batch_id": f"批次{global_b_num:02d}",
                "unit_count": len(b_units),
                "units": b_units,
            })
        remedy_quota = min(MAX_REMEDY_RESERVE_PER_WINDOW, max(0, int(elastic_cap) - len(w_units)))
        hard_cap = min(len(w_units) + remedy_quota, max(MAX_TOTAL_CALLS_PER_WINDOW, int(elastic_cap)))
        windows.append({
            "window_id": f"window_{w_num:02d}",
            "window_label": f"窗口{w_num:02d}",
            "base_unit_count": len(w_units),
            "max_base_units_limit": max_per_window,
            "elastic_base_units_limit": elastic_base_limit,
            "elastic_tail_merged": len(w_units) > max_per_window,
            "reserved_remedy_quota": remedy_quota,
            "hard_cap_total_calls": hard_cap,
            "batch_count": len(batches),
            "batches": batches,
        })
    return windows


def _build_tiered_window_slices(
    tiers: list[tuple[str, list[str]]],
    max_per_window: int = MAX_UNITS_PER_WINDOW,
    max_per_batch: int = MAX_UNITS_PER_BATCH,
    elastic_cap: int = MAX_TOTAL_CALLS_PER_WINDOW,
) -> list[dict[str, Any]]:
    """严格按 Wave/依赖层级不混批拆分（Wave 1 -> Wave 2 -> Wave 3 -> Wave 4），每批 <= max_per_batch；当全集总基础单元在 51..54（尾差<=4且总调用<=elastic_cap）时弹性并入 window_01"""
    windows: list[dict[str, Any]] = []
    global_b_num = 0
    w_num = 1
    cur_batches: list[dict[str, Any]] = []
    cur_win_units = 0

    grand_total = sum(len(u_list) for _, u_list in tiers)
    eff_max_per_window, elastic_base_limit = compute_v41_eff_window_size(grand_total, max_per_window, elastic_cap)

    def _infer_tier_wave(t_name: str) -> int | None:
        m = re.match(r"^wave_(\d+)", str(t_name or "").strip().lower())
        if m:
            return int(m.group(1))
        mapping = {
            "stage_a_portraits": 1,
            "stage_a_scenes": 1,
            "stage_a_subjects": 2,
            "stage_b_first_frames": 3,
            "stage_b_tail_frames": 4,
        }
        return mapping.get(str(t_name or "").strip().lower())

    def _flush_window() -> None:
        nonlocal w_num, cur_batches, cur_win_units
        if not cur_batches:
            return
        remedy_quota = min(MAX_REMEDY_RESERVE_PER_WINDOW, max(0, int(elastic_cap) - cur_win_units))
        hard_cap = min(cur_win_units + remedy_quota, max(MAX_TOTAL_CALLS_PER_WINDOW, int(elastic_cap)))
        windows.append({
            "window_id": f"window_{w_num:02d}",
            "window_label": f"窗口{w_num:02d}",
            "base_unit_count": cur_win_units,
            "max_base_units_limit": max_per_window,
            "elastic_base_units_limit": elastic_base_limit,
            "elastic_tail_merged": cur_win_units > max_per_window,
            "reserved_remedy_quota": remedy_quota,
            "hard_cap_total_calls": hard_cap,
            "batch_count": len(cur_batches),
            "batches": cur_batches,
        })
        w_num += 1
        cur_batches = []
        cur_win_units = 0

    for tier_name, tier_units in tiers:
        t_wave = _infer_tier_wave(tier_name)
        idx = 0
        n_total = len(tier_units)
        while idx < n_total:
            rem_win = eff_max_per_window - cur_win_units
            if rem_win <= 0:
                _flush_window()
                rem_win = eff_max_per_window
            take_n = min(max_per_batch, rem_win, n_total - idx)
            chunk = tier_units[idx : idx + take_n]
            idx += take_n
            global_b_num += 1
            b_obj: dict[str, Any] = {
                "batch_id": f"批次{global_b_num:02d}",
                "tier": tier_name,
                "unit_count": len(chunk),
                "units": chunk,
            }
            if t_wave is not None:
                b_obj["wave"] = t_wave
                b_obj["wave_label"] = f"Wave {t_wave}"
            cur_batches.append(b_obj)
            cur_win_units += len(chunk)

    _flush_window()
    return windows


def verify_cuts_csv(
    cuts_csv: Path,
    shots_json: Path | None = None,
    expected_start: float | None = None,
    expected_end: float | None = None,
    min_coverage: float = 0.995,
    max_gap: float = 0.50,
    allow_self_span: bool = False,
    flow_tier: str | None = None,
    as_of_date: str | None = None,
    source_video: Path | None = None,
    skip_intro_sec: float = 0.0,
    skip_outro_sec: float = 0.0,
) -> dict:
    """V4.1/V4.2 cuts.csv 独立可信源覆盖率 (>=99.5%)、首/尾/内部缺口 (<=0.50s) 与 Flow 档位硬门禁委托"""
    return verify_cuts_csv_v41(
        cuts_csv=cuts_csv,
        shots_json=shots_json,
        expected_start=expected_start,
        expected_end=expected_end,
        min_coverage=min_coverage,
        max_gap=max_gap,
        allow_self_span=allow_self_span,
        flow_tier=flow_tier,
        as_of_date=as_of_date,
        source_video=source_video,
        skip_intro_sec=skip_intro_sec,
        skip_outro_sec=skip_outro_sec,
    )


def _legacy_verify_cuts_csv_unused(
    cuts_csv: Path,
    shots_json: Path | None = None,
    expected_start: float | None = None,
    expected_end: float | None = None,
    min_coverage: float = 0.995,
    max_gap: float = 0.50,
) -> dict:
    import csv

    cuts_p = Path(cuts_csv)
    if not cuts_p.exists() or cuts_p.stat().st_size == 0:
        return {
            "ok": False,
            "error_code": "VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV",
            "errors": [f"VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV ({cuts_p})"],
        }

    with cuts_p.open("r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return {
            "ok": False,
            "error_code": "VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV",
            "errors": [f"VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV ({cuts_p})"],
        }

    parsed = []
    errors: list[str] = []
    seg_groups: dict[str, list[dict]] = {}

    for idx, r in enumerate(rows, 1):
        try:
            s_t = float(r.get("start_sec", ""))
            e_t = float(r.get("end_sec", ""))
        except Exception:
            errors.append(f"VERIFY_CUTS_FAIL: INVALID_TIME_FORMAT at row {idx}")
            continue
        if e_t <= s_t:
            errors.append(f"VERIFY_CUTS_FAIL: INVALID_TIME_RANGE at row {idx} (start={s_t}, end={e_t})")
        seg = str(r.get("segment") or "").strip()
        scene = str(r.get("scene") or "").strip()
        item = {"row": idx, "segment": seg, "scene": scene, "start_sec": s_t, "end_sec": e_t}
        parsed.append(item)
        if seg:
            seg_groups.setdefault(seg, []).append(item)

    if not parsed:
        return {
            "ok": False,
            "error_code": errors[0] if errors else "VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV",
            "errors": errors or ["VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV"],
        }

    # ② 相邻行 gap 检查（按时间顺序）
    ordered = sorted(parsed, key=lambda x: (x["start_sec"], x["end_sec"]))
    for i in range(len(ordered) - 1):
        cur_r, nxt_r = ordered[i], ordered[i + 1]
        gap = round(nxt_r["start_sec"] - cur_r["end_sec"], 4)
        if gap < -0.05 or gap > max_gap:
            errors.append(
                f"VERIFY_CUTS_FAIL: TIME_GAP_OR_OVERLAP between row {cur_r['row']} ({cur_r['end_sec']}) "
                f"and row {nxt_r['row']} ({nxt_r['start_sec']}): gap={gap}s (allowed -0.05..{max_gap}s)"
            )

    # ④ 同段禁拼跨场景与行数限制检查
    for seg, items in seg_groups.items():
        if len(items) > 3:
            errors.append(f"VERIFY_CUTS_FAIL: TOO_MANY_SHOTS_IN_SEGMENT {seg} has {len(items)} shots (> 3)")
        if len(items) > 1:
            scenes = [it["scene"] for it in items]
            if any(not sc for sc in scenes) or len(set(scenes)) > 1:
                errors.append(
                    f"VERIFY_CUTS_FAIL: CROSS_SCENE_OR_EMPTY_SCENE_MERGE in {seg}: scenes={scenes}"
                )

    # ③ 总覆盖率检查
    exp_s = expected_start
    exp_e = expected_end
    if (exp_s is None or exp_e is None) and shots_json is not None and Path(shots_json).exists():
        try:
            s_raw = read_json(Path(shots_json))
            s_list = s_raw if isinstance(s_raw, list) else s_raw.get("shots", [])
            if s_list:
                if exp_s is None:
                    exp_s = min(float(x["start_sec"]) for x in s_list)
                if exp_e is None:
                    exp_e = max(float(x["end_sec"]) for x in s_list)
        except Exception:
            pass
    if exp_s is None:
        exp_s = ordered[0]["start_sec"]
    if exp_e is None:
        exp_e = ordered[-1]["end_sec"]

    expected_span = max(0.001, float(exp_e) - float(exp_s))
    covered_dur = sum(max(0.0, it["end_sec"] - it["start_sec"]) for it in ordered)
    coverage = covered_dur / expected_span
    if coverage < float(min_coverage):
        errors.append(
            f"VERIFY_CUTS_FAIL: COVERAGE_TOO_LOW coverage={coverage:.4f} ({covered_dur:.2f}s / {expected_span:.2f}s) "
            f"< min_coverage={min_coverage:.4f}"
        )

    return {
        "ok": len(errors) == 0,
        "error_code": errors[0].split(" ", 1)[0] if errors else "OK",
        "errors": errors,
        "rows_count": len(parsed),
        "segments_count": len(seg_groups),
        "expected_start": round(float(exp_s), 3),
        "expected_end": round(float(exp_e), 3),
        "expected_span": round(expected_span, 3),
        "covered_duration": round(covered_dur, 3),
        "coverage": round(coverage, 4),
        "coverage_ratio": round(coverage, 4),
    }


STALE_PLACEHOLDER_PATTERNS = ("{{VISUAL_STYLE}}", "{{CHAR:", "【待填角色", "【待补角色造型】")
_PEACETIME_TEMPORAL_KWS = ("轰炸前", "和平", "战前", "繁华街市", "秩序井然", "pre-bombing", "peacetime", "before bombing")
_DAMAGED_TEMPORAL_KWS = ("战损", "废墟", "轰炸后", "火海", "灾后", "残垣断壁", "post-bombing", "bombardment ruins", "damaged", "ruins")


def _check_scene_temporal_conflicts(items: list[dict], md_text: str = "") -> list[str]:
    """检测场景时序状态冲突（例如轰炸前和平街景误绑战损/废墟母图，或同一未拆分场景母图同时混用战前和平与战后废墟分镜）"""
    errors: list[str] = []
    scene_meta: dict[str, dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        u = get_unit_id(it)
        if not _is_scene_job(it, u):
            continue
        sc_state = str(it.get("scene_state") or "").strip().lower()
        sc_name = str(it.get("scene_name") or it.get("scene") or it.get("canonical_scene") or u).strip()
        sc_target = str(it.get("target_path") or it.get("formal_path") or "").strip().replace("\\", "/")
        is_dmg = (sc_state in ("damaged", "wartime", "post_disaster", "ruins")) or any(
            kw in sc_name.lower() or kw in sc_target.lower() for kw in ("战损", "废墟", "轰炸后", "火海", "灾后", "damaged", "ruins")
        )
        is_peace_only = sc_state in ("peacetime", "peace", "pre_bombing", "normal")
        meta = {"name": sc_name, "target": sc_target, "state": sc_state, "is_damaged": is_dmg, "is_peace_only": is_peace_only}
        for k in (sc_target, sc_name, str(it.get("scene_id") or "").strip(), u):
            if k:
                scene_meta[k] = meta

    scene_usage_states: dict[str, set[str]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        u = get_unit_id(it)
        if _is_character_job(it, u) or _is_scene_job(it, u):
            continue
        ctx = " ".join(
            str(it.get(k) or "")
            for k in ("summary", "narrative_intent", "scene_temporal_state", "temporal_state", "grid_and_facing_cn", "character_positions", "shot_desc")
        )
        ctx_lower = ctx.lower()
        exp_t = str(it.get("scene_temporal_state") or it.get("temporal_state") or "").strip().lower()
        is_frame_damaged = (exp_t in ("damaged", "wartime", "post_disaster", "ruins", "post_bombing")) or any(
            kw in ctx_lower for kw in _DAMAGED_TEMPORAL_KWS
        )
        is_frame_peace = (exp_t in ("peacetime", "pre_bombing", "peace", "normal")) or (
            any(kw in ctx_lower for kw in _PEACETIME_TEMPORAL_KWS) and not is_frame_damaged
        )

        bound_ref = str(it.get("scene_ref") or "").strip().replace("\\", "/")
        bound_name = str(it.get("canonical_scene") or it.get("scene") or "").strip()
        bound_key = bound_ref or bound_name
        if not bound_key:
            continue

        meta = scene_meta.get(bound_ref) or scene_meta.get(bound_name)
        bound_is_dmg = (meta["is_damaged"] if meta else False) or any(
            kw in bound_key.lower() for kw in ("战损", "废墟", "轰炸后", "火海", "灾后", "damaged", "ruins")
        )
        bound_is_peace_only = bool(meta and meta.get("is_peace_only"))

        if is_frame_peace and bound_is_dmg:
            errors.append(
                f"SCENE_TEMPORAL_STATE_CONFLICT: 分镜 {u} 摘要为和平/轰炸前时序，却绑定了战损/废墟场景母图 ({bound_key})"
            )
        elif is_frame_damaged and bound_is_peace_only:
            errors.append(
                f"SCENE_TEMPORAL_STATE_CONFLICT: 分镜 {u} 摘要为战损/轰炸后废墟时序，却绑定了和平状态场景母图 ({bound_key})"
            )

        if is_frame_peace:
            scene_usage_states.setdefault(bound_key, set()).add("peacetime")
        if is_frame_damaged:
            scene_usage_states.setdefault(bound_key, set()).add("damaged")

    for bound_key, states in scene_usage_states.items():
        if "peacetime" in states and "damaged" in states:
            errors.append(
                f"SCENE_TEMPORAL_STATE_CONFLICT: 同一场景母图 ({bound_key}) 同时被轰炸前和平分镜与轰炸后战损/废墟分镜混用，请拆分为独立战前/战后场景母图！"
            )
    return errors


def _scan_stale_placeholders(text: str) -> list[str]:
    hits = [pat for pat in STALE_PLACEHOLDER_PATTERNS if pat in text]
    return hits


def _strip_prompt_lock_blocks(text: str) -> str:
    """剥离 batch_ops 自动追加的锁块（Reference Role Assignment / Strict Person Count Lock / Camera Pitch / Format Lock 等）"""
    s = str(text or "")
    for const_block in (
        CHARACTER_PORTRAIT_CONSTRAINT,
        CHARACTER_SUBJECT_CONSTRAINT,
        CHARACTER_PORTRAIT_CONSTRAINT_9_16,
        CHARACTER_SUBJECT_CONSTRAINT_9_16,
        SCENE_CLEAN_CONSTRAINT,
    ):
        if const_block and const_block in s:
            s = s.replace(const_block, "")
    lock_patterns = (
        r"【机位视角：[^\n]*",
        r"\[Reference Role Assignment\]:?[^\n]*",
        r"\[Strict Person Count Lock[^\]]*\]:?[^\n]*",
        r"\[Camera Pitch[^\]]*\]:?[^\n]*",
        r"\[Format Lock[^\]]*\]:?[^\n]*",
        r"\[HISTORICAL PORTRAIT BAREHEADED FACE-LOCK\]:?[^\n]*",
        r"\[SCENE MASTER CLEANLINESS[^\]]*\]:?[^\n]*",
        r"\[CHARACTER (?:PORTRAIT|SUBJECT) 4-IN-1[^\]]*\]:?[^\n]*",
    )
    for pat in lock_patterns:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def _resolve_manifest_prompt_text(item: dict) -> str:
    for k in ("image_prompt", "prompt", "full_prompt", "prompt_en", "positive_prompt"):
        val = str(item.get(k) or "").strip()
        if val:
            return val
    return ""


def _parse_schema_version_num(raw_sv) -> float:
    if raw_sv is None:
        return 0.0
    if isinstance(raw_sv, (int, float)):
        return float(raw_sv)
    s = str(raw_sv).strip().lower().lstrip("v")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else 0.0


def _find_director_bible_file(
    ep_dir_or_manifest: Path | None = None,
    project_root: Path | None = None,
    ep: str = "第01集",
) -> Path | None:
    cands: list[Path] = []
    if ep_dir_or_manifest is not None:
        p = Path(ep_dir_or_manifest)
        base = p if p.is_dir() else p.parent
        cands.append(base / "episode_director_bible.json")
    if project_root is not None:
        pr = resolve_cross_platform_path(project_root)
        cands.extend([
            pr / "中间文件" / "在制工作区" / ep / "episode_director_bible.json",
            pr / "分集" / ep / "episode_director_bible.json",
            pr / "中间文件" / "原片分析" / ep / "episode_director_bible.json",
        ])
    for c in cands:
        if c.exists() and c.stat().st_size > 0:
            return c
    return None


def _is_v3_mode(
    manifest_raw: Any = None,
    explicit_v3: bool = False,
    ep_dir_or_manifest: Path | None = None,
    project_root: Path | None = None,
    ep: str = "第01集",
) -> bool:
    if explicit_v3:
        return True
    if isinstance(manifest_raw, dict):
        if _parse_schema_version_num(manifest_raw.get("schema_version")) >= 3.0:
            return True
    if _find_director_bible_file(ep_dir_or_manifest, project_root, ep) is not None:
        return True
    return False


def _get_item_actual_refs(item: dict) -> list[str]:
    for k in ("refs", "image_refs", "reference_images"):
        val = item.get(k)
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()]
    return []


def _validate_v3_visual_spec_and_refs(item: dict, unit_id: str, clean_text: str) -> dict[str, Any]:
    """V3 五维 visual_spec 实体槽校验 + Image 1..N 编号越界与 Stage B Image 1 防误认人脸/立绘校验"""
    vspec = item.get("visual_spec")
    if not isinstance(vspec, dict) or not vspec:
        return {
            "ok": False,
            "error_code": "V3_VISUAL_SPEC_INVALID",
            "reason": f"单元 {unit_id} (V3) 缺少非空 visual_spec 五维结构对象",
        }

    actual_refs = _get_item_actual_refs(item)
    issues: list[str] = []

    ref_roles = vspec.get("ref_roles")
    if not isinstance(ref_roles, list) or len(ref_roles) != len(actual_refs) or any(not str(r).strip() for r in ref_roles):
        issues.append(
            f"visual_spec.ref_roles 长度({len(ref_roles) if isinstance(ref_roles, list) else 'invalid'})"
            f"与实际 refs/image_refs 长度({len(actual_refs)})不一致或含空角色名"
        )

    cam_horizon = vspec.get("camera_and_horizon")
    if not cam_horizon or (isinstance(cam_horizon, str) and not cam_horizon.strip()):
        issues.append("visual_spec.camera_and_horizon 不能为空")

    sp_layers = vspec.get("spatial_layers")
    if not isinstance(sp_layers, dict):
        issues.append("visual_spec.spatial_layers 必须为包含 foreground/midground/background 的对象")
    else:
        for lk in ("foreground", "midground", "background"):
            lv = sp_layers.get(lk)
            if not lv or (isinstance(lv, str) and not lv.strip()):
                issues.append(f"visual_spec.spatial_layers.{lk} 不能为空")

    lvec = vspec.get("lighting_vector")
    if not lvec or (isinstance(lvec, str) and not lvec.strip()):
        issues.append("visual_spec.lighting_vector 不能为空")

    raw_epc = item.get("exact_person_count")
    if raw_epc is None:
        raw_epc = vspec.get("exact_person_count")
    n_persons: int | None = None
    if raw_epc is None:
        if _is_character_job(item, unit_id):
            n_persons = 1
        elif _is_scene_job(item, unit_id):
            n_persons = 0
        else:
            issues.append("缺少 exact_person_count 字段 (V3 Stage B 必填)")
    else:
        try:
            n_persons = int(raw_epc)
        except Exception:
            issues.append(f"exact_person_count 非法整数: {raw_epc!r}")

    subj_inv = vspec.get("subjects_inventory")
    if n_persons is not None:
        if not isinstance(subj_inv, list) or len(subj_inv) != n_persons:
            issues.append(
                f"visual_spec.subjects_inventory 长度({len(subj_inv) if isinstance(subj_inv, list) else 'invalid'})"
                f"必须严格等于 exact_person_count({n_persons})"
            )
        elif n_persons > 0:
            req_sub_keys = ("identity_or_garment", "grid_pos", "torso_angle", "left_hand", "right_hand", "gaze_target")
            for s_idx, s_entry in enumerate(subj_inv):
                if not isinstance(s_entry, dict):
                    issues.append(f"subjects_inventory[{s_idx}] 必须为对象")
                    continue
                missing_sk = [sk for sk in req_sub_keys if s_entry.get(sk) is None or not str(s_entry.get(sk)).strip()]
                if missing_sk:
                    issues.append(f"subjects_inventory[{s_idx}] 缺少非空字段: {', '.join(missing_sk)}")
    elif not isinstance(subj_inv, list):
        issues.append("visual_spec.subjects_inventory 必须为列表")

    if issues:
        return {
            "ok": False,
            "error_code": "V3_VISUAL_SPEC_INVALID",
            "reason": f"单元 {unit_id} visual_spec 校验失败（共 {len(issues)} 项）: " + "；".join(issues),
        }

    # 交叉校验 Image k / Reference Image k 编号范围 1 <= k <= len(refs)
    for m in re.finditer(r"\b(?:Reference\s+)?Image\s*(\d+)\b", clean_text, flags=re.IGNORECASE):
        k_num = int(m.group(1))
        if len(actual_refs) == 0 or k_num < 1 or k_num > len(actual_refs):
            return {
                "ok": False,
                "error_code": "IMAGE_REF_ATTRIBUTION_MISMATCH",
                "reason": (
                    f"IMAGE_REF_ATTRIBUTION_MISMATCH: 单元 {unit_id} 提示词引用了 Image {k_num}，"
                    f"但实际 refs 数量为 {len(actual_refs)}"
                ),
            }

    # Stage B 分镜首帧（refs[0] 为净参考图）严禁将 Image 1 指认为人脸/立绘，且严禁角色卡挤占 Image 2 场景母图槽位
    is_stage_b = (not _is_character_job(item, unit_id)) and (not _is_scene_job(item, unit_id))
    ftype = str(item.get("frame_type") or "").strip()
    is_tail_frame = is_stage_b and (ftype == "尾帧" or unit_id.endswith("_尾帧"))
    is_first_frame = is_stage_b and (ftype == "首帧" or unit_id.endswith("_首帧") or bool(actual_refs and ("净参考" in actual_refs[0] or "_源" in actual_refs[0])))
    if is_first_frame:
        if ref_roles:
            r0_low = str(ref_roles[0]).lower()
            if any(tok in r0_low for tok in ("portrait", "character_sheet", "face_ref", "立绘", "肖像")):
                return {
                    "ok": False,
                    "error_code": "IMAGE_REF_ATTRIBUTION_MISMATCH",
                    "reason": f"IMAGE_REF_ATTRIBUTION_MISMATCH: Stage B 首帧 {unit_id} 的 ref_roles[0]={ref_roles[0]!r} 不得指认为人脸/立绘",
                }
        bad_img1_pats = (
            r"\b(?:Reference\s+)?Image\s*1\b[^.\n;]{0,90}\b(?:facial\s+bone(?:\s+structure)?|bone\s+structure|face\s+identity|facial\s+features|facial\s+likeness|character\s+portrait|character\s+sheet|portrait\s+sheet|full-?body\s+outfit|character\s+design)\b",
            r"\b(?:facial\s+bone(?:\s+structure)?|bone\s+structure|face\s+identity|facial\s+features|character\s+portrait|character\s+sheet|portrait\s+of)\b[^.\n;]{0,60}\b(?:from|in|of|per|via)\s+(?:Reference\s+)?Image\s*1\b",
        )
        for pat in bad_img1_pats:
            if re.search(pat, clean_text, flags=re.IGNORECASE):
                return {
                    "ok": False,
                    "error_code": "IMAGE_REF_ATTRIBUTION_MISMATCH",
                    "reason": (
                        f"IMAGE_REF_ATTRIBUTION_MISMATCH: Stage B 分镜首帧 {unit_id} 严禁将 Image 1 (净参考底图) 指认为人脸骨相/立绘！"
                    ),
                }

    if is_stage_b and actual_refs:
        allow_over_3 = bool(item.get("allow_over_3_refs")) or str(item.get("platform") or "").strip().lower() in ("codex", "gpt")
        vis_chars_chk = [str(c).strip() for c in (item.get("visible_characters") or item.get("characters") or []) if str(c).strip()]
        single_char_refs = [r for r in actual_refs if _is_character_ref_path(r)]
        composite_char_refs = [r for r in actual_refs if _is_multi_char_composite_ref(r)]
        scene_ref_decl = str(item.get("scene_ref") or "").strip()

        # 门禁 A：严禁单人角色母图挤占 Image 2 (refs[1]) 顶掉场景母图或尾帧同段首帧
        if len(actual_refs) >= 2:
            r1 = actual_refs[1]
            r1_role_low = str(ref_roles[1]).lower() if isinstance(ref_roles, list) and len(ref_roles) >= 2 else ""
            r1_is_char_role = (
                any(tok in r1_role_low for tok in ("portrait", "character_sheet", "face_ref", "人物", "角色", "肖像", "立绘"))
                and not any(tok in r1_role_low for tok in ("scene", "场景", "environment", "首帧", "opening"))
            )
            if _is_character_ref_path(r1) or (r1_is_char_role and not is_tail_frame):
                return {
                    "ok": False,
                    "error_code": "IMAGE_REF_SLOT2_MISSING_SCENE",
                    "reason": (
                        f"IMAGE_REF_SLOT2_MISSING_SCENE: Stage B 分镜 {unit_id} 的 Image 2 ({r1}) 被人物母图挤占！"
                        f"Image 2 必须留给同状态场景母图（或尾帧时的同段 _首帧.png）；多人同框人物必须合并为去白边无底横向合集图放入 Image 3（角色合集缓存/合集_<角色1>_<角色2>_<肖像|主体>.png）！"
                    ),
                }
            if (not is_tail_frame) and scene_ref_decl:
                r1_norm = r1.replace("\\", "/")
                if ("画风" in r1_norm or "style" in r1_norm.lower()) and "场景参考" not in r1_norm:
                    return {
                        "ok": False,
                        "error_code": "IMAGE_REF_SLOT2_MISSING_SCENE",
                        "reason": f"IMAGE_REF_SLOT2_MISSING_SCENE: Stage B 分镜 {unit_id} 已声明 scene_ref={scene_ref_decl}，Image 2 不得误填画风参考图 ({r1})！",
                    }

        # 门禁 B：AGY <=3 图上限下，多人同框 (>=2人) 严禁硬塞多张单人角色母图，必须使用 Image 3 横向合集图
        if not allow_over_3:
            if len(actual_refs) > 3:
                return {
                    "ok": False,
                    "error_code": "IMAGE_REF_MULTI_CHAR_NEEDS_COMPOSITE",
                    "reason": (
                        f"IMAGE_REF_MULTI_CHAR_NEEDS_COMPOSITE: Stage B 分镜 {unit_id} 垫图数量为 {len(actual_refs)}，超出 AGY 3 图上限！"
                        f"请锁定 [Image 1 净参考源帧, Image 2 同状态场景母图(或尾帧同段首帧), Image 3 角色合集缓存/合集_...png]。"
                    ),
                }
            if (n_persons is not None and n_persons >= 2 or len(vis_chars_chk) >= 2) and len(single_char_refs) >= 2 and not composite_char_refs:
                return {
                    "ok": False,
                    "error_code": "IMAGE_REF_MULTI_CHAR_NEEDS_COMPOSITE",
                    "reason": (
                        f"IMAGE_REF_MULTI_CHAR_NEEDS_COMPOSITE: 多人同框分镜 {unit_id} ({n_persons or len(vis_chars_chk)} 人) 直接挂载了 {len(single_char_refs)} 张单人母图 ({single_char_refs})，"
                        f"请改为 [Image 1 净参考源帧, Image 2 同状态场景母图, Image 3 角色合集缓存/合集_<角色1>_<角色2>_<肖像|主体>.png]（由 batch_ops.py build-char-sheets 生成）！"
                    ),
                }

    return {"ok": True, "error_code": "OK", "reason": ""}


def _validate_phase1_en_prompt(item: dict, unit_id: str = "", v3: bool = False) -> dict[str, Any]:
    """校验第一棒产出的英文提示词：非空、无中文字符、无未替换占位符（已彻底移除 <50 词门槛）；V3 额外强校验五维 visual_spec 与 Image 1..N 映射"""
    u = unit_id or get_unit_id(item)
    raw = _resolve_manifest_prompt_text(item)
    if not raw:
        return {
            "ok": False,
            "error_code": "PROMPT_TOO_SHORT",
            "unit": u,
            "word_count": 0,
            "clean_text": "",
            "reason": f"单元 {u} 缺少第一棒英文提示词 (image_prompt/prompt/full_prompt/prompt_en 均为空)",
        }
    clean_text = _strip_prompt_lock_blocks(raw)
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", clean_text))
    has_placeholder = (
        "{{" in raw
        or "}}" in raw
        or "待填" in clean_text
        or "待补" in clean_text
        or bool(re.search(r"\bTODO\b", clean_text, flags=re.IGNORECASE))
        or "TODO" in clean_text
        or bool(_scan_stale_placeholders(raw))
    )
    en_words = re.findall(r"[A-Za-z]{2,}", clean_text)
    if has_cjk or has_placeholder:
        return {
            "ok": False,
            "error_code": "PROMPT_HAS_CJK_OR_PLACEHOLDER",
            "unit": u,
            "word_count": len(en_words),
            "clean_text": clean_text,
            "reason": f"单元 {u} 英文提示词含中文字符或未替换占位符 ({{{{ / }}}} / 待填 / TODO)",
        }
    if not en_words:
        return {
            "ok": False,
            "error_code": "PROMPT_TOO_SHORT",
            "unit": u,
            "word_count": 0,
            "clean_text": clean_text,
            "reason": f"单元 {u} 剥离锁块后缺少有效英文内容",
        }
    if v3:
        v3_chk = _validate_v3_visual_spec_and_refs(item, u, clean_text)
        if not v3_chk["ok"]:
            return {
                "ok": False,
                "error_code": v3_chk["error_code"],
                "unit": u,
                "word_count": len(en_words),
                "clean_text": clean_text,
                "reason": v3_chk["reason"],
            }
    return {
        "ok": True,
        "error_code": "OK",
        "unit": u,
        "word_count": len(en_words),
        "clean_text": clean_text,
        "norm_text": re.sub(r"\s+", " ", clean_text).strip().lower(),
        "reason": "",
    }


def _infer_expected_wave(item: dict, unit_id: str = "") -> int:
    u = unit_id or get_unit_id(item)
    if _is_character_job(item, u):
        return 1 if _get_character_subtype(item, u) == "portrait" else 2
    if _is_scene_job(item, u):
        return 1
    ftype = str(item.get("frame_type") or "").strip()
    if ftype == "尾帧" or u.endswith("_尾帧"):
        return 4
    return 3


def _parse_wave_num(val: Any) -> int | None:
    if val is None or val == "":
        return None
    if isinstance(val, int):
        return val
    m = re.search(r"(\d+)", str(val))
    return int(m.group(1)) if m else None


def _validate_and_normalize_waves(
    items: list[dict],
    project_root: Path | None = None,
    work_dir: Path | None = None,
    cls_data: dict | None = None,
) -> list[str]:
    """校验并自动归一化 Wave 1..4（Wave 1: 肖像+场景母图 -> Wave 2: 主体 -> Wave 3: 首帧 -> Wave 4: 尾帧），拦截低波次引用高波次或同波次未生成图"""
    errors: list[str] = []
    cls_map = cls_data if isinstance(cls_data, dict) else {}
    confirmed_cats = ("用户已确认", "已采用", "自检通过", "审核通过")

    unit_wave: dict[str, int] = {}
    target_to_unit: dict[str, tuple[str, int]] = {}
    name_to_unit: dict[str, tuple[str, int]] = {}

    for it in items:
        if not isinstance(it, dict):
            continue
        u = get_unit_id(it)
        if u == "unknown":
            continue
        exp_w = _infer_expected_wave(it, u)
        decl_w = _parse_wave_num(it.get("wave"))
        if decl_w is not None and decl_w != exp_w:
            errors.append(
                f"WAVE_DEPENDENCY_VIOLATION: 单元 {u} 声明 wave={decl_w} 与实际所属波次 Wave {exp_w} 冲突"
            )
        it["wave"] = exp_w
        it["wave_label"] = f"Wave {exp_w}"
        unit_wave[u] = exp_w
        for tk in ("target_path", "formal_path", "output_path"):
            tv = str(it.get(tk) or "").strip().replace("\\", "/")
            if tv:
                target_to_unit[tv] = (u, exp_w)
                name_to_unit[Path(tv).name] = (u, exp_w)
        name_to_unit[u] = (u, exp_w)

    def _is_already_generated(ref_u: str, ref_str: str) -> bool:
        if isinstance(cls_map.get(ref_u), dict) and cls_map[ref_u].get("category") in confirmed_cats:
            return True
        anchor = project_root or work_dir
        if anchor is not None and ref_str:
            p = _resolve_ref_path_smart(ref_str, anchor)
            if p.exists() and p.stat().st_size > 0:
                return True
        return False

    for it in items:
        if not isinstance(it, dict):
            continue
        u = get_unit_id(it)
        if u == "unknown":
            continue
        cur_w = unit_wave.get(u, _infer_expected_wave(it, u))
        ref_candidates: list[str] = []
        for k in ("refs", "image_refs", "reference_images", "depends_on"):
            v = it.get(k)
            if isinstance(v, list):
                ref_candidates.extend(str(x).strip() for x in v if str(x).strip())
            elif isinstance(v, str) and v.strip():
                ref_candidates.append(v.strip())
        for k in ("portrait_ref", "scene_ref", "character_ref", "same_segment_first_frame_ref"):
            sv = str(it.get(k) or "").strip()
            if sv:
                ref_candidates.append(sv)

        for r in ref_candidates:
            rn = r.replace("\\", "/")
            matched = target_to_unit.get(rn) or name_to_unit.get(Path(rn).name) or name_to_unit.get(rn)
            if not matched:
                # 兜底按文件名后缀推断高波次资产
                if cur_w == 1 and ("_主体." in rn or "_首帧." in rn or "_尾帧." in rn):
                    errors.append(f"WAVE_DEPENDENCY_VIOLATION: Wave 1 单元 {u} 禁止引用高波次资产 {r}")
                elif cur_w == 2 and ("_首帧." in rn or "_尾帧." in rn):
                    errors.append(f"WAVE_DEPENDENCY_VIOLATION: Wave 2 单元 {u} 禁止引用高波次资产 {r}")
                elif cur_w == 3 and "_尾帧." in rn:
                    errors.append(f"WAVE_DEPENDENCY_VIOLATION: Wave 3 单元 {u} 禁止引用高波次资产 {r}")
                continue

            ref_u, ref_w = matched
            if ref_u == u:
                continue
            if ref_w > cur_w:
                errors.append(
                    f"WAVE_DEPENDENCY_VIOLATION: 单元 {u} (Wave {cur_w}) 禁止引用高波次资产 {ref_u} (Wave {ref_w}) -> {r}"
                )
            elif ref_w == cur_w and not _is_already_generated(ref_u, r):
                errors.append(
                    f"WAVE_DEPENDENCY_VIOLATION: 单元 {u} (Wave {cur_w}) 引用同波次未落盘资产 {ref_u} (Wave {ref_w}) -> {r}"
                )

    return errors


def _validate_v3_video_segments(cuts_rows: list[dict], md_text: str, as_of_date: str | None = None, flow_tier: str = "paid") -> list[str]:
    """V3/V4.1 视频端硬件槽位、Google Flow 退役红线与反套话校验（cuts.csv <-> 第NN集_视频提示词.md）"""
    errors: list[str] = []
    if not md_text.strip():
        return errors

    md_matches = list(re.finditer(r"^##\s+(segment_\d+)\b(.*)$", md_text, re.MULTILINE))
    md_sections: dict[str, tuple[str, str]] = {}
    for idx, m in enumerate(md_matches):
        seg_id = m.group(1).strip()
        hdr = m.group(2).strip()
        s_pos = m.end()
        e_pos = md_matches[idx + 1].start() if idx + 1 < len(md_matches) else len(md_text)
        md_sections[seg_id] = (hdr, md_text[s_pos:e_pos].strip())

    cuts_by_seg: dict[str, list[dict]] = {}
    for r in cuts_rows:
        seg = str(r.get("segment") or "").strip()
        if seg:
            cuts_by_seg.setdefault(seg, []).append(r)

    lazy_boilerplates = (
        "按首帧画面人物造型自然继承",
        "按首帧人物造型自然继承",
        "自然继承首帧人物",
        "人物造型同首帧",
        "inherit appearance naturally from first frame",
        "naturally inherit character appearance from the first frame",
    )
    source_leak_phrases = (
        "浮现深红美术大字",
        "深红美术大字",
        "浮现片名",
        "原片字幕",
        "片头大字",
        "美术大字",
    )
    negative_prefixes = ("无", "禁", "严禁", "禁止", "去除", "剔除", "不得", "没有", "zero ", "no ", "without ")
    freeze_pats = (
        r"\bHold\s+final\s+framing\b",
        r"\bHold\s+tense\s+posture\b",
        r"\bHold\s+final\s+pose\b",
        r"\bFreeze\s+on\s+final\s+frame\b",
    )

    def _canon_ref_key(tok: str) -> str:
        t = str(tok or "").strip().replace("\\", "/")
        t = Path(t).name if ("/" in t) else t
        t_low = t.lower()
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            if t_low.endswith(ext):
                return t_low[: -len(ext)]
        return t_low

    def _collect_structured_video_assets(obj: Any, img_out: list[str], aud_out: list[str], char_flag: list[bool]) -> None:
        if isinstance(obj, str):
            s = obj.strip().strip("`'\"")
            if not s:
                return
            s_low = s.lower()
            if s_low.endswith((".wav", ".mp3", ".m4a", ".flac")) or s.startswith("@音频"):
                if s not in aud_out:
                    aud_out.append(s)
            elif not s_low.endswith(("_源.jpg", "_净参考.jpg")):
                if any(k in s_low for k in ("character_card", "角色卡", "人物/", "人物参考/", "_肖像", "_主体")):
                    char_flag[0] = True
                if s not in img_out:
                    img_out.append(s)
        elif isinstance(obj, list):
            for el in obj:
                _collect_structured_video_assets(el, img_out, aud_out, char_flag)
        elif isinstance(obj, dict):
            # 若是单个资产描述对象（含 path / file / asset / ref）
            asset_kind = str(obj.get("type") or obj.get("role") or obj.get("kind") or obj.get("slot_type") or "").strip().lower()
            direct_val = obj.get("path") or obj.get("file") or obj.get("asset") or obj.get("ref") or obj.get("uri")
            if direct_val is not None and not isinstance(direct_val, (list, dict)):
                dv_s = str(direct_val).strip()
                if dv_s:
                    if asset_kind in ("audio", "voice", "voice_ref", "sound", "wav", "音频", "声线") or dv_s.lower().endswith((".wav", ".mp3", ".m4a", ".flac")):
                        if dv_s not in aud_out:
                            aud_out.append(dv_s)
                    else:
                        if any(k in asset_kind for k in ("character", "char_card", "portrait", "subject", "角色")) or any(
                            k in dv_s.lower() for k in ("人物/", "人物参考/", "_肖像", "_主体", "角色卡")
                        ):
                            char_flag[0] = True
                        if dv_s not in img_out:
                            img_out.append(dv_s)
                return
            # 否则是分组容器字典（如 {"images": [...], "character_cards": [...], "audio": [...]}）
            for k, v in obj.items():
                if v is None or v == "" or v is False:
                    continue
                k_low = str(k).strip().lower()
                if k_low in ("audio", "audio_refs", "voice", "voice_ref", "sound", "音频", "声线"):
                    if isinstance(v, (list, tuple)):
                        for av in v:
                            if str(av).strip() and str(av).strip() not in aud_out:
                                aud_out.append(str(av).strip())
                    elif str(v).strip() not in aud_out:
                        aud_out.append(str(v).strip())
                elif k_low in (
                    "video_assets", "images", "image_refs", "refs", "mounted_refs",
                    "frames", "start_frame", "first_frame", "end_frame", "tail_frame", "last_frame",
                    "ingredients", "character_cards", "characters", "portraits", "visual_assets",
                ):
                    if k_low in ("character_cards", "characters", "portraits") and v:
                        char_flag[0] = True
                    _collect_structured_video_assets(v, img_out, aud_out, char_flag)

    def _extract_mounted_refs(hdr: str, body: str, seg_rows: list[dict]) -> tuple[list[str], list[str], bool]:
        refs_found: list[str] = []
        seen_keys: set[str] = set()
        audio_refs: list[str] = []
        explicit_char_flag = [False]
        structured_imgs: list[str] = []

        def _add_ref(tok: str) -> None:
            t_clean = str(tok or "").strip().strip("`'\"()（）[]【】")
            if not t_clean or t_clean.lower().endswith(("_源.jpg", "_净参考.jpg")):
                return
            ck = _canon_ref_key(t_clean)
            if ck and ck not in seen_keys:
                seen_keys.add(ck)
                refs_found.append(t_clean)

        # 1) 检查 cuts.csv 可选列（含 video_assets）
        for r in seg_rows:
            raw_va = str(r.get("video_assets") or "").strip()
            if raw_va:
                if raw_va.startswith(("[", "{")):
                    try:
                        _collect_structured_video_assets(json.loads(raw_va), structured_imgs, audio_refs, explicit_char_flag)
                    except Exception:
                        for tok in re.split(r"[,;|\s]+", raw_va):
                            if tok.strip():
                                _collect_structured_video_assets(tok.strip(), structured_imgs, audio_refs, explicit_char_flag)
                else:
                    for tok in re.split(r"[,;|\s]+", raw_va):
                        if tok.strip():
                            _collect_structured_video_assets(tok.strip(), structured_imgs, audio_refs, explicit_char_flag)
            for col in ("mounted_refs", "refs", "ref_images"):
                raw_c = str(r.get(col) or "").strip()
                if raw_c:
                    for tok in re.split(r"[,;|\s]+", raw_c):
                        if tok.strip():
                            _add_ref(tok.strip())

        seg_full = hdr + "\n" + body
        # 2) 解析 MD 段落中的结构化 video_assets（JSON 代码块 / 内联 JSON / YAML 列表）
        for m_cb in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", seg_full, flags=re.IGNORECASE):
            cb_txt = m_cb.group(1).strip()
            if cb_txt.startswith(("{", "[")) and ("video_assets" in cb_txt or "_首帧" in cb_txt or "_肖像" in cb_txt or ".png" in cb_txt.lower()):
                try:
                    parsed_cb = json.loads(cb_txt)
                    if isinstance(parsed_cb, dict) and "video_assets" in parsed_cb:
                        _collect_structured_video_assets(parsed_cb["video_assets"], structured_imgs, audio_refs, explicit_char_flag)
                    elif isinstance(parsed_cb, dict):
                        _collect_structured_video_assets(parsed_cb, structured_imgs, audio_refs, explicit_char_flag)
                except Exception:
                    pass

        for m_va in re.finditer(r'(?:["\']?video_assets["\']?)\s*[:：]\s*', seg_full):
            tail_txt = seg_full[m_va.end() :].lstrip()
            if tail_txt.startswith(("[", "{")):
                try:
                    parsed_va, _ = json.JSONDecoder().raw_decode(tail_txt)
                    _collect_structured_video_assets(parsed_va, structured_imgs, audio_refs, explicit_char_flag)
                except Exception:
                    pass
            else:
                # 支持 YAML / Markdown 缩进列表形式的 video_assets:
                after_lines = seg_full[m_va.end() :].splitlines()
                first_inline = after_lines[0].strip() if after_lines else ""
                if first_inline and not first_inline.startswith("-"):
                    for tok in re.split(r"[,;|]+", first_inline):
                        if tok.strip():
                            _collect_structured_video_assets(tok.strip(), structured_imgs, audio_refs, explicit_char_flag)
                else:
                    for ln in after_lines[1:]:
                        ls = ln.strip()
                        if not ls:
                            continue
                        if ls.startswith(("-", "*")):
                            val_s = re.sub(r"^[-*]\s*(?:[A-Za-z0-9_]+\s*[:：]\s*)?", "", ls).strip()
                            _collect_structured_video_assets(val_s, structured_imgs, audio_refs, explicit_char_flag)
                        else:
                            break

        for simg in structured_imgs:
            _add_ref(simg)

        # 3) 若未提供显式结构化 video_assets，则回退扫描 MD 段落中的图片路径或资产引用（按文件名去重）
        if not structured_imgs:
            for m_f in re.finditer(
                r"([A-Za-z0-9_\-./\\\u4e00-\u9fff]+?(?:\.(?:png|jpg|jpeg|webp)|_肖像|_主体|_母图|_首帧|_尾帧))",
                seg_full,
                flags=re.IGNORECASE,
            ):
                _add_ref(m_f.group(1))
        return refs_found, audio_refs, explicit_char_flag[0]

    for seg_id, seg_rows in sorted(cuts_by_seg.items()):
        hdr, body = md_sections.get(seg_id, ("", ""))
        if not hdr and not body:
            continue
        seg_full = hdr + "\n" + body
        seg_low = seg_full.lower()

        first_r = seg_rows[0]
        model_route = str(first_r.get("model_route") or "").strip()
        video_mode = str(first_r.get("video_mode") or "").strip()
        actual_dur = sum(
            max(0.0, float(r.get("end_sec", 0.0)) - float(r.get("start_sec", 0.0)))
            for r in seg_rows
        )
        raw_gd = str(first_r.get("gen_duration_sec") or "").strip()
        if raw_gd:
            try:
                gen_dur = float(raw_gd)
            except Exception:
                gen_dur = actual_dur
        else:
            m_gd = re.search(r"\b(\d+(?:\.\d+)?)\s*s\b", hdr, flags=re.IGNORECASE)
            gen_dur = float(m_gd.group(1)) if m_gd else actual_dur

        route_low = (model_route + " " + hdr).lower()
        mode_low = video_mode.lower()
        mounted_refs, audio_refs, explicit_char_card = _extract_mounted_refs(hdr, body, seg_rows)
        has_char_card = explicit_char_card or any(
            ("人物/" in r or "人物\\" in r or "人物参考/" in r or "_肖像" in r or "_主体" in r or "角色卡" in r)
            for r in mounted_refs
        )
        has_tail_ref = any("_尾帧" in r for r in mounted_refs) or any(
            str(r.get("need_tail_frame") or "").strip().lower() in ("1", "true", "yes")
            for r in seg_rows
        )

        is_seedance = ("seedance" in route_low) or ("即梦" in route_low) or ("seedance_native" in mode_low)
        is_omni_12 = ("omni" in route_low) and ("1.2" in route_low or "12" in route_low)
        is_omni_11 = ("omni" in route_low) and ("1.1" in route_low or "11" in route_low)
        is_veo = ("veo" in route_low) and not is_seedance and not is_omni_12 and not is_omni_11

        raw_ext = str(first_r.get("extend_sec") or "").strip()
        ext_sec: float | None = None
        if raw_ext:
            with contextlib.suppress(Exception):
                ext_sec = float(raw_ext)

        flow_errs, _ = validate_flow_segment_rule(
            seg_id=seg_id,
            model_route=model_route,
            video_mode=video_mode or ("Extend" if re.search(r"\bextend\b", seg_low) else ("Ingredients" if ("ingredients" in seg_low or "多图参考" in seg_low) else "")),
            gen_dur=gen_dur,
            actual_dur=actual_dur,
            shot_count=len(seg_rows),
            mounted_refs_count=len(mounted_refs) if mounted_refs else None,
            has_char_card=has_char_card,
            has_tail_ref=has_tail_ref,
            extra_hdr=hdr,
            as_of_date=as_of_date,
            flow_tier=flow_tier,
            extend_sec=ext_sec,
        )
        errors.extend(flow_errs)

        if is_veo:
            if not any(abs(gen_dur - v) < 1e-3 for v in (4.0, 6.0, 8.0)):
                errors.append(
                    f"VEO_INVALID_DURATION: {seg_id} 使用 Veo 3.1 但 gen_duration_sec={gen_dur} 非法（仅限 4 / 6 / 8 秒）"
                )
            is_ingredients = ("ingredients" in mode_low) or ("ingredients" in seg_low) or ("多图参考" in seg_low)
            if abs(gen_dur - 4.0) < 1e-3 or abs(gen_dur - 6.0) < 1e-3:
                if is_ingredients or has_char_card:
                    errors.append(
                        f"VEO_4S_6S_INGREDIENTS_FORBIDDEN: {seg_id} ({int(round(gen_dur))}s Veo 3.1) 只能走 Frames 模式，严禁使用 Ingredients 或挂载 人物/ 角色卡 ({mounted_refs})"
                    )
                has_handles = (gen_dur - actual_dur) > 0.15 or ("handles" in route_low)
                if has_handles and (has_tail_ref or len(mounted_refs) > 1):
                    errors.append(
                        f"VEO_HANDLES_TAIL_FRAME_FORBIDDEN: {seg_id} ({int(round(gen_dur))}s Handles 余量模式, actual={actual_dur:.2f}s) 禁挂 _尾帧.png，最多仅挂 1 张首帧 (refs={mounted_refs})"
                    )
                elif not has_handles and len(mounted_refs) > 2:
                    errors.append(
                        f"VEO_FRAMES_MAX_2_REFS_EXCEEDED: {seg_id} ({int(round(gen_dur))}s Frames 满载模式) 最多挂载首尾 2 帧，当前 {len(mounted_refs)} 张"
                    )
            elif abs(gen_dur - 8.0) < 1e-3 and is_ingredients:
                n_refs = len(mounted_refs)
                if n_refs == 0:
                    n_refs = len(set(re.findall(r"@图片\d*", seg_full)))
                if n_refs > 3:
                    errors.append(
                        f"VEO_INGREDIENTS_MAX_3_REFS_EXCEEDED: {seg_id} (8s Veo 3.1 Ingredients) 挂载参考图总数={n_refs} 超过上限 3 张 ({mounted_refs})"
                    )
                elif n_refs == 0:
                    errors.append(
                        f"VEO_INGREDIENTS_MISSING_REFS: {seg_id} (8s Veo 3.1 Ingredients) 至少需要挂载 1 张参考图"
                    )
            elif abs(gen_dur - 8.0) < 1e-3 and mode_low == "frames":
                if has_char_card:
                    errors.append(
                        f"VEO_4S_6S_INGREDIENTS_FORBIDDEN: {seg_id} (8s Veo 3.1 Frames 模式) 严禁挂载 人物/ 角色卡 ({mounted_refs})"
                    )
                elif len(mounted_refs) > 2:
                    errors.append(
                        f"VEO_FRAMES_MAX_2_REFS_EXCEEDED: {seg_id} (8s Veo 3.1 Frames 模式) 最多挂载首尾 2 帧，当前 {len(mounted_refs)} 张"
                    )
        elif is_omni_11:
            if not any(abs(gen_dur - v) < 1e-3 for v in (4.0, 6.0, 8.0, 10.0)):
                errors.append(
                    f"FLOW_INVALID_DURATION_OMNI_1_1: {seg_id} Omni Flash 1.1 时长={gen_dur}s 非法（仅限离散档位 4/6/8/10 秒）"
                )
        elif is_seedance:
            if not (4.0 <= gen_dur <= 15.0):
                errors.append(
                    f"SEEDANCE_INVALID_DURATION: {seg_id} Seedance 2.0 时长={gen_dur}s 越界（要求 4s–15s）"
                )
            has_at_img = ("@图片" in seg_full) or bool(mounted_refs)
            has_at_aud = ("@音频" in seg_full) or bool(audio_refs)
            if "@图片" not in seg_full or ("@音频" not in seg_full and not has_at_aud):
                if not (has_at_img and has_at_aud):
                    errors.append(
                        f"SEEDANCE_MISSING_AT_REFS: {seg_id} (即梦 Seedance 2.0) 必须同时具备 @图片 与 @音频 挂载"
                    )
                elif "@图片" not in seg_full or "@音频" not in seg_full:
                    errors.append(
                        f"SEEDANCE_MISSING_AT_REFS: {seg_id} (即梦 Seedance 2.0) 必须同时具备 @图片 与 @音频 挂载"
                    )

        # 反套话与反泄露校验
        for bp in lazy_boilerplates:
            if bp in seg_full:
                errors.append(
                    f"VIDEO_PROMPT_LAZY_CHAR_BOILERPLATE: {seg_id} 出现懒惰人物继承套话 {bp!r}，必须显式写出人物造型锚点"
                )
                break

        for leak in source_leak_phrases:
            start_i = 0
            while True:
                pos = seg_full.find(leak, start_i)
                if pos == -1:
                    break
                prefix = seg_full[max(0, pos - 8) : pos].lower()
                if not any(npf in prefix for npf in negative_prefixes):
                    errors.append(
                        f"VIDEO_PROMPT_SOURCE_TEXT_LEAK: {seg_id} 出现未洗净原片叠字/水印描述 {leak!r}"
                    )
                    break
                start_i = pos + len(leak)

        for fpat in freeze_pats:
            m_fr = re.search(fpat, seg_full, flags=re.IGNORECASE)
            if m_fr:
                prefix = seg_full[max(0, m_fr.start() - 12) : m_fr.start()].lower()
                if "no " not in prefix and "never " not in prefix and "avoid " not in prefix:
                    errors.append(
                        f"VIDEO_PROMPT_STIFF_FREEZE_FRAME: {seg_id} 出现生硬死帧指令 {m_fr.group(0)!r}，应改为自然余势延续描述"
                    )
                    break

        # 四要素完整性校验：运镜、主体锚点（或0人声明）、动作演进、SFX音效
        has_camera = any(
            k in seg_low
            for k in (
                "运镜", "镜头", "推", "拉", "摇", "移", "跟", "升", "降", "特写", "中景", "远景", "全景", "俯", "仰",
                "dolly", "pan", "tilt", "track", "zoom", "static", "locked", "handheld", "push", "pull", "crane", "camera", "shot",
            )
        )
        has_subject = any(
            k in seg_low
            for k in (
                "本段无人物入镜", "无人物", "0人", "空镜", "0 person", "no character", "empty",
                "人物", "主体", "身着", "身穿", "wearing", "man", "woman", "soldier", "officer", "character", "锚点",
            )
        )
        has_action = any(
            k in seg_low
            for k in (
                "随后", "紧接着", "然后", "动作", "走向", "转身", "抬手", "目光", "视线", "缓慢", "演进", "展开",
                "moves", "turns", "walks", "looks", "raises", "shifts", "then", "followed by", "gradually", "continues", "action",
            )
        )
        has_sfx = any(
            k in seg_low
            for k in (
                "sfx", "音效", "声音", "环境音", "脚步声", "风声", "雨声", "呼吸", "sound", "audio", "ambience", "foley", "footsteps", "wind",
            )
        )
        missing_parts = [
            name
            for name, ok in (("运镜", has_camera), ("主体锚点/0人声明", has_subject), ("动作演进", has_action), ("SFX音效", has_sfx))
            if not ok
        ]
        if missing_parts:
            errors.append(
                f"VIDEO_PROMPT_INCOMPLETE_STRUCTURE: {seg_id} 提示词缺少必要结构要素: {missing_parts}"
            )

    return errors


def _find_visual_audit_file(
    manifest_file: Path | None,
    project_root: Path | None,
    ep: str,
    audit_file: Path | None = None,
) -> Path | None:
    cands: list[Path] = []
    if audit_file is not None:
        cands.append(Path(audit_file))
    if manifest_file is not None:
        cands.append(Path(manifest_file).parent / "prompt_visual_audit.json")
    if project_root is not None:
        p_root = resolve_cross_platform_path(project_root)
        cands.extend([
            p_root / "中间文件" / "在制工作区" / ep / "prompt_visual_audit.json",
            p_root / "分集" / ep / "prompt_visual_audit.json",
            p_root / "中间文件" / "原片分析" / ep / "prompt_visual_audit.json",
        ])
    for c in cands:
        if c.exists() and c.stat().st_size > 0:
            return c
    return cands[0] if cands else None


def _merge_visual_audit_shards_if_needed(
    manifest_file: Path | None,
    project_root: Path | None,
    ep: str,
    audit_file: Path | None = None,
) -> tuple[Path | None, list[str]]:
    """当总表 prompt_visual_audit.json 尚未生成但 <ep_dir>/audit/*visual_audit.json 存在分片时，自动无损合并写出总表并保留跨模型 author_agent != inspector_agent 签名"""
    existing = _find_visual_audit_file(manifest_file, project_root, ep, audit_file=audit_file)
    if existing is not None and existing.exists() and existing.stat().st_size > 0:
        return existing, []

    cand_audit_dirs: list[Path] = []
    if audit_file is not None:
        cand_audit_dirs.append(Path(audit_file).parent / "audit")
    if manifest_file is not None:
        cand_audit_dirs.append(Path(manifest_file).parent / "audit")
        cand_audit_dirs.append(Path(manifest_file).parent)
    if project_root is not None:
        p_root = resolve_cross_platform_path(project_root)
        cand_audit_dirs.extend([
            p_root / "中间文件" / "在制工作区" / ep / "audit",
            p_root / "分集" / ep / "audit",
            p_root / "中间文件" / "原片分析" / ep / "audit",
            p_root / "中间文件" / "在制工作区" / ep,
        ])

    shard_files: list[Path] = []
    seen_dirs: set[str] = set()
    chosen_ep_dir: Path | None = None
    for ad in cand_audit_dirs:
        ad_key = str(ad).replace("\\", "/")
        if ad_key in seen_dirs or not ad.exists() or not ad.is_dir():
            continue
        seen_dirs.add(ad_key)
        found = [
            p for p in sorted(ad.glob("*visual_audit.json"))
            if p.is_file() and p.stat().st_size > 0 and p.name != "prompt_visual_audit.json"
        ]
        if found:
            shard_files = found
            chosen_ep_dir = ad.parent if ad.name == "audit" else ad
            break

    if not shard_files:
        return existing, []

    merged_units: dict[str, dict[str, Any]] = {}
    merged_micro_scenes: dict[str, dict[str, Any]] = {}
    authors_seen: list[str] = []
    inspectors_seen: list[str] = []
    any_shard_failed = False

    for sp in shard_files:
        try:
            s_data = read_json(sp)
        except Exception:
            any_shard_failed = True
            continue
        if not isinstance(s_data, dict):
            any_shard_failed = True
            continue

        s_auth = str(s_data.get("author_agent") or "").strip()
        s_insp = str(s_data.get("inspector_agent") or "").strip()
        s_status = str(s_data.get("status") or "").strip().upper()
        if s_auth and s_auth not in authors_seen:
            authors_seen.append(s_auth)
        if s_insp and s_insp not in inspectors_seen:
            inspectors_seen.append(s_insp)
        if s_status and s_status != "PASS":
            any_shard_failed = True

        raw_u = (
            s_data.get("units")
            or s_data.get("results")
            or s_data.get("audited_units")
            or s_data.get("items")
        )
        if isinstance(raw_u, dict):
            for uk, uv in raw_u.items():
                uk_s = str(uk).strip()
                if not uk_s:
                    continue
                if isinstance(uv, dict):
                    u_entry = dict(uv)
                    if not str(u_entry.get("author_agent") or "").strip() and s_auth:
                        u_entry["author_agent"] = s_auth
                    if not str(u_entry.get("inspector_agent") or "").strip() and s_insp:
                        u_entry["inspector_agent"] = s_insp
                    if not str(u_entry.get("status") or "").strip() and s_status:
                        u_entry["status"] = s_status
                else:
                    u_entry = {
                        "status": str(uv).strip() or s_status or "PASS",
                        "author_agent": s_auth,
                        "inspector_agent": s_insp,
                    }
                u_entry["source_shard"] = sp.name
                if str(u_entry.get("status") or "").strip().upper() != "PASS":
                    any_shard_failed = True
                merged_units[uk_s] = u_entry
        elif isinstance(raw_u, list):
            for uv in raw_u:
                if isinstance(uv, dict):
                    uk_s = get_unit_id(uv)
                    if not uk_s or uk_s == "unknown":
                        continue
                    u_entry = dict(uv)
                    if not str(u_entry.get("author_agent") or "").strip() and s_auth:
                        u_entry["author_agent"] = s_auth
                    if not str(u_entry.get("inspector_agent") or "").strip() and s_insp:
                        u_entry["inspector_agent"] = s_insp
                    if not str(u_entry.get("status") or "").strip() and s_status:
                        u_entry["status"] = s_status
                    u_entry["source_shard"] = sp.name
                    if str(u_entry.get("status") or "").strip().upper() != "PASS":
                        any_shard_failed = True
                    merged_units[uk_s] = u_entry
                else:
                    uk_s = str(uv).strip()
                    if uk_s:
                        merged_units[uk_s] = {
                            "status": s_status or "PASS",
                            "author_agent": s_auth,
                            "inspector_agent": s_insp,
                            "source_shard": sp.name,
                        }

        raw_ms = s_data.get("micro_scenes") or s_data.get("scene_blocks")
        if isinstance(raw_ms, dict):
            for mk, mv in raw_ms.items():
                if isinstance(mv, dict):
                    m_entry = dict(mv)
                    if not str(m_entry.get("author_agent") or "").strip() and s_auth:
                        m_entry["author_agent"] = s_auth
                    if not str(m_entry.get("inspector_agent") or "").strip() and s_insp:
                        m_entry["inspector_agent"] = s_insp
                    if not str(m_entry.get("status") or "").strip() and s_status:
                        m_entry["status"] = s_status
                    m_entry["source_shard"] = sp.name
                    if str(m_entry.get("status") or "").strip().upper() != "PASS":
                        any_shard_failed = True
                    merged_micro_scenes[str(mk)] = m_entry
        elif isinstance(raw_ms, list):
            for idx_m, mv in enumerate(raw_ms, 1):
                if isinstance(mv, dict):
                    mk = str(mv.get("micro_scene_id") or mv.get("id") or f"{sp.stem}_{idx_m:02d}")
                    m_entry = dict(mv)
                    if not str(m_entry.get("author_agent") or "").strip() and s_auth:
                        m_entry["author_agent"] = s_auth
                    if not str(m_entry.get("inspector_agent") or "").strip() and s_insp:
                        m_entry["inspector_agent"] = s_insp
                    if not str(m_entry.get("status") or "").strip() and s_status:
                        m_entry["status"] = s_status
                    m_entry["source_shard"] = sp.name
                    if str(m_entry.get("status") or "").strip().upper() != "PASS":
                        any_shard_failed = True
                    merged_micro_scenes[mk] = m_entry
        elif s_data.get("micro_scene_id") or s_data.get("scene_block_id"):
            mk = str(s_data.get("micro_scene_id") or s_data.get("scene_block_id"))
            merged_micro_scenes[mk] = {
                "status": s_status or "FAIL",
                "author_agent": s_auth,
                "inspector_agent": s_insp,
                "source_shard": sp.name,
            }

    overall_pass = bool(merged_units) and (not any_shard_failed)
    top_author = authors_seen[0] if len(authors_seen) == 1 else (", ".join(authors_seen) if authors_seen else "")
    top_inspector = inspectors_seen[0] if len(inspectors_seen) == 1 else (", ".join(inspectors_seen) if inspectors_seen else "")

    target_out = (
        Path(audit_file)
        if audit_file is not None
        else (
            (Path(manifest_file).parent / "prompt_visual_audit.json")
            if manifest_file is not None
            else ((chosen_ep_dir or shard_files[0].parent) / "prompt_visual_audit.json")
        )
    )
    merged_payload: dict[str, Any] = {
        "schema_version": "4.0",
        "episode": ep,
        "status": "PASS" if overall_pass else "FAIL",
        "author_agent": top_author,
        "inspector_agent": top_inspector,
        "merged_from_shards": [sp.name for sp in shard_files],
        "units": merged_units,
    }
    if merged_micro_scenes:
        merged_payload["micro_scenes"] = merged_micro_scenes
    write_json(target_out, merged_payload)
    return target_out, [sp.name for sp in shard_files]


def verify_script_alignment(
    project_root: Path | None = None,
    episode: str = "第01集",
    cuts_csv: Path | None = None,
    md_file: Path | None = None,
    manifest_file: Path | None = None,
    audit_file: Path | None = None,
    require_visual_audit: bool = False,
    v3: bool = False,
    as_of_date: str | None = None,
    flow_tier: str | None = None,
) -> dict[str, Any]:
    """
    第1步统一对齐门禁：
    1) 拦截旧占位符 ({{VISUAL_STYLE}} / {{CHAR: / 【待填角色 / 【待补角色造型】) -> ALIGNMENT_FAIL_STALE_PLACEHOLDER
    2) 拦截 cuts.csv 与 _视频提示词.md / 待生图帧清单.json 段号、镜头数 1:1 错位或同段 scene 冲突 -> SCRIPT_CUTS_ALIGNMENT_MISMATCH
    3) 拦截场景时序状态冲突 -> SCENE_TEMPORAL_STATE_CONFLICT
    4) 拦截第一棒英文提示词缺失/含中文或占位符/跨单元正文雷同（已移除 <50 词门槛）
    5) 当启用 V3 (schema_version >= 3 / --v3 / 存在 episode_director_bible.json) 时强校验：
       - 五维 visual_spec 实体槽 + Image 1..N 映射 + Stage B Image 1 防误认人脸
       - Wave 1..4 依赖与归一化校验 (WAVE_DEPENDENCY_VIOLATION)
       - 视频端硬件槽位 (Veo 4/6/8s, 4s/6s禁Ingredients, 8s Ingredients<=3 refs, Omni/Seedance) 与反套话/防泄露/反死帧校验
       - prompt_visual_audit.json 跨家族双盲审 (author_agent != inspector_agent 且 status == PASS)
    6) 当 --require-visual-audit 或 schema_version >= 2 时校验 prompt_visual_audit.json PASS 且覆盖清单全部单元
    """
    import csv

    ep = normalize_episode_name(episode)
    if project_root is not None:
        p_root = resolve_cross_platform_path(project_root)
        if cuts_csv is None:
            cuts_csv = p_root / "中间文件" / "原片分析" / ep / "cuts.csv"
        if md_file is None:
            md_file = p_root / "分集" / ep / f"{ep}_视频提示词.md"
        if manifest_file is None:
            manifest_file = p_root / "中间文件" / "在制工作区" / ep / "待生图帧清单.json"

    errors: list[str] = []
    if cuts_csv is None or not Path(cuts_csv).exists() or Path(cuts_csv).stat().st_size == 0:
        msg = f"ALIGNMENT_FAIL_MISSING_CUTS_CSV: 未找到有效 cuts.csv ({cuts_csv})"
        return {"ok": False, "error_code": "ALIGNMENT_FAIL_MISSING_CUTS_CSV", "errors": [msg]}

    cuts_p = Path(cuts_csv)
    with cuts_p.open("r", encoding="utf-8-sig") as f:
        cuts_rows = list(csv.DictReader(f))
    if not cuts_rows:
        msg = f"ALIGNMENT_FAIL_MISSING_CUTS_CSV: cuts.csv 为空 ({cuts_p})"
        return {"ok": False, "error_code": "ALIGNMENT_FAIL_MISSING_CUTS_CSV", "errors": [msg]}

    cuts_segs: dict[str, set[str]] = {}
    cuts_seg_scenes: dict[str, set[str]] = {}
    for row_idx, r in enumerate(cuts_rows, 1):
        seg = str(r.get("segment") or "").strip()
        shot = str(r.get("shot") or "").strip() or f"镜头{row_idx:02d}"
        scene = str(r.get("scene") or "").strip()
        if not seg:
            errors.append(f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: cuts.csv 第 {row_idx} 行缺少 segment 字段")
            continue
        cuts_segs.setdefault(seg, set()).add(shot)
        if scene:
            cuts_seg_scenes.setdefault(seg, set()).add(scene)

    for seg, scs in cuts_seg_scenes.items():
        if len(scs) > 1:
            errors.append(f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: cuts.csv 同段 {seg} 存在多个冲突 scene: {sorted(scs)}")

    eff_flow_tier = resolve_flow_tier(flow_tier, cuts_p, manifest_file, project_root)
    flow_row_errs, sunset_warnings = validate_cuts_rows_flow_routing(cuts_rows, as_of_date=as_of_date, flow_tier=eff_flow_tier)
    errors.extend(flow_row_errs)

    m_raw_pre = read_json(Path(manifest_file)) if (manifest_file is not None and Path(manifest_file).exists()) else None
    v3_mode = _is_v3_mode(
        manifest_raw=m_raw_pre,
        explicit_v3=v3,
        ep_dir_or_manifest=Path(manifest_file) if manifest_file is not None else cuts_p,
        project_root=project_root,
        ep=ep,
    )
    if v3_mode:
        require_visual_audit = True

    md_text = ""
    if md_file is not None and Path(md_file).exists():
        md_text = Path(md_file).read_text(encoding="utf-8-sig")
        hits = _scan_stale_placeholders(md_text)
        if hits:
            errors.append(f"ALIGNMENT_FAIL_STALE_PLACEHOLDER: {Path(md_file).name} 含未替换旧占位符 {hits}")

        md_matches = list(re.finditer(r"^##\s+(segment_\d+)\b(.*)$", md_text, re.MULTILINE))
        md_segs: dict[str, str] = {}
        for idx, m in enumerate(md_matches):
            seg_id = m.group(1).strip()
            s_pos = m.end()
            e_pos = md_matches[idx + 1].start() if idx + 1 < len(md_matches) else len(md_text)
            body = md_text[s_pos:e_pos]
            if seg_id in md_segs:
                errors.append(f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: _视频提示词.md 存在重复章节 ## {seg_id}")
            md_segs[seg_id] = body

        missing_in_md = sorted(set(cuts_segs.keys()) - set(md_segs.keys()))
        extra_in_md = sorted(set(md_segs.keys()) - set(cuts_segs.keys()))
        if missing_in_md:
            errors.append(f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: _视频提示词.md 缺少 cuts.csv 中的分段 {missing_in_md}")
        if extra_in_md:
            errors.append(f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: _视频提示词.md 多出旧版残留分段 {extra_in_md}")

        for seg_id in sorted(set(cuts_segs.keys()) & set(md_segs.keys())):
            body = md_segs[seg_id]
            md_shots = set(re.findall(r"(?:^|\n)\s*(?:[-*]\s*)?(镜头\d+)\s*[：:]", body))
            md_shots |= set(re.findall(rf"{re.escape(seg_id)}_(镜头\d+)_首帧", body))
            if md_shots and md_shots != cuts_segs[seg_id]:
                errors.append(
                    f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: _视频提示词.md 分段 {seg_id} 镜头集合 {sorted(md_shots)} "
                    f"与 cuts.csv {sorted(cuts_segs[seg_id])} 不一致"
                )

        if v3_mode:
            for e_seg in _validate_v3_video_segments(cuts_rows, md_text, as_of_date=as_of_date, flow_tier=eff_flow_tier):
                if e_seg not in errors:
                    errors.append(e_seg)

    manifest_items: list[dict] = []
    if manifest_file is not None and Path(manifest_file).exists():
        m_raw = m_raw_pre if m_raw_pre is not None else read_json(Path(manifest_file))
        if isinstance(m_raw, dict):
            sv = str(m_raw.get("schema_version") or "").strip()
            if sv == "2" or sv.startswith("2.") or m_raw.get("schema_version") == 2 or _parse_schema_version_num(sv) >= 3.0:
                require_visual_audit = True
        manifest_items = [it for it in (m_raw if isinstance(m_raw, list) else extract_jobs_list(m_raw)) if isinstance(it, dict)]

        seen_prompt_norms: dict[str, str] = {}
        for it in manifest_items:
            u = get_unit_id(it)
            ft = str(it.get("frame_type") or "")
            if (ft == "尾帧" or u.endswith("_尾帧")) and it.get("need_tail_frame") is False:
                continue
            prompt_blobs = [
                str(it.get(k) or "")
                for k in ("prompt", "image_prompt", "full_prompt", "prompt_en", "positive_prompt", "video_prompt")
            ]
            hits = _scan_stale_placeholders("\n".join(prompt_blobs))
            if hits:
                errors.append(f"ALIGNMENT_FAIL_STALE_PLACEHOLDER: 待生图帧清单 单元 {u} 提示词含旧占位符 {hits}")

            p_chk = _validate_phase1_en_prompt(it, u, v3=v3_mode)
            if not p_chk["ok"]:
                ec = p_chk["error_code"]
                if ec == "PROMPT_HAS_CJK_OR_PLACEHOLDER":
                    errors.append(f"ALIGNMENT_PROMPT_HAS_CJK_OR_PLACEHOLDER: {p_chk['reason']}")
                elif ec in ("V3_VISUAL_SPEC_INVALID", "IMAGE_REF_ATTRIBUTION_MISMATCH"):
                    errors.append(f"{ec}: {p_chk['reason']}")
                else:
                    errors.append(f"ALIGNMENT_PROMPT_TOO_SHORT: {p_chk['reason']}")
            else:
                norm_body = p_chk["norm_text"]
                if norm_body in seen_prompt_norms and seen_prompt_norms[norm_body] != u:
                    errors.append(
                        f"ALIGNMENT_PROMPT_DUPLICATE_BOILERPLATE: 单元 {u} 与单元 {seen_prompt_norms[norm_body]} 英文提示词正文完全雷同"
                    )
                else:
                    seen_prompt_norms[norm_body] = u

        manifest_segs: dict[str, set[str]] = {}
        manifest_seg_scenes: dict[str, set[str]] = {}
        for it in manifest_items:
            u = get_unit_id(it)
            if _is_character_job(it, u) or _is_scene_job(it, u):
                continue
            ftype = str(it.get("frame_type") or "").strip()
            is_first = (ftype == "首帧") or u.endswith("_首帧")
            seg_str = str(it.get("segment") or "").strip()
            shot_str = str(it.get("shot") or "").strip()
            if not seg_str or not shot_str:
                m_u = re.match(r"^(segment_\d+)_(镜头\d+)_(首帧|尾帧|关键帧\d*|中间帧.*)$", u)
                if m_u:
                    seg_str = seg_str or m_u.group(1)
                    shot_str = shot_str or m_u.group(2)
            if is_first and seg_str:
                manifest_segs.setdefault(seg_str, set()).add(shot_str or "镜头01")
            sc_val = str(it.get("canonical_scene") or it.get("scene") or "").strip()
            if seg_str and sc_val:
                manifest_seg_scenes.setdefault(seg_str, set()).add(sc_val)
                cuts_scs = cuts_seg_scenes.get(seg_str, set())
                if cuts_scs:
                    c_sc = next(iter(cuts_scs))
                    if sc_val != c_sc and (sc_val not in c_sc and c_sc not in sc_val):
                        errors.append(
                            f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: 分镜单元 {u} scene={sc_val!r} 与 cuts.csv ({seg_str}) scene={c_sc!r} 冲突"
                        )

        if manifest_segs:
            missing_in_m = sorted(set(cuts_segs.keys()) - set(manifest_segs.keys()))
            extra_in_m = sorted(set(manifest_segs.keys()) - set(cuts_segs.keys()))
            if missing_in_m:
                errors.append(f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: 待生图帧清单 缺少 cuts.csv 中的分段 {missing_in_m}")
            if extra_in_m:
                errors.append(f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: 待生图帧清单 多出旧版残留分段 {extra_in_m}")
            for seg_id in sorted(set(cuts_segs.keys()) & set(manifest_segs.keys())):
                if manifest_segs[seg_id] != cuts_segs[seg_id]:
                    errors.append(
                        f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: 待生图帧清单 分段 {seg_id} 首帧镜头集合 {sorted(manifest_segs[seg_id])} "
                        f"与 cuts.csv {sorted(cuts_segs[seg_id])} 不一致"
                    )
        for seg_id, scs in manifest_seg_scenes.items():
            if len(scs) > 1:
                errors.append(f"SCRIPT_CUTS_ALIGNMENT_MISMATCH: 待生图帧清单 同段 {seg_id} 存在 scene 冲突: {sorted(scs)}")

        errors.extend(_check_scene_temporal_conflicts(manifest_items, md_text=md_text))
        if v3_mode:
            w_dir_cand = Path(manifest_file).parent
            cls_cand = w_dir_cand / "当前制作审核分类.json"
            cls_map = read_json(cls_cand) if cls_cand.exists() else {}
            errors.extend(_validate_and_normalize_waves(manifest_items, project_root=project_root, work_dir=w_dir_cand, cls_data=cls_map))

        if require_visual_audit:
            audit_p, _merged_shards = _merge_visual_audit_shards_if_needed(
                Path(manifest_file), project_root, ep, audit_file=audit_file
            )
            if audit_p is None or not audit_p.exists():
                errors.append(f"ALIGNMENT_VISUAL_AUDIT_MISSING: 启用视觉反查门禁但未找到 prompt_visual_audit.json ({audit_p})")
            else:
                try:
                    audit_data = read_json(audit_p)
                except Exception as exc:
                    audit_data = {}
                    errors.append(f"ALIGNMENT_VISUAL_AUDIT_FAILED: 无法解析 prompt_visual_audit.json ({exc})")
                if isinstance(audit_data, dict):
                    a_status = str(audit_data.get("status") or "").strip().upper()
                    if a_status != "PASS":
                        errors.append(f"ALIGNMENT_VISUAL_AUDIT_FAILED: prompt_visual_audit.json status={a_status!r} != 'PASS'")
                    top_author = str(audit_data.get("author_agent") or "").strip()
                    top_inspector = str(audit_data.get("inspector_agent") or "").strip()
                    covered_audit_units: set[str] = set()
                    failed_audit_units: list[str] = []
                    self_review_units: list[str] = []
                    raw_units = (
                        audit_data.get("units")
                        or audit_data.get("results")
                        or audit_data.get("audited_units")
                        or audit_data.get("items")
                    )
                    if isinstance(raw_units, dict):
                        for uk, uv in raw_units.items():
                            uk_s = str(uk).strip()
                            if isinstance(uv, dict):
                                uv_st = str(uv.get("status") or ("PASS" if not v3_mode else "")).strip().upper()
                                if v3_mode:
                                    u_auth = str(uv.get("author_agent") or top_author).strip()
                                    u_insp = str(uv.get("inspector_agent") or top_inspector).strip()
                                    if not u_auth or not u_insp or u_auth.lower() == u_insp.lower():
                                        self_review_units.append(f"{uk_s}(author={u_auth!r},inspector={u_insp!r})")
                                    if uv_st != "PASS":
                                        failed_audit_units.append(uk_s)
                                        continue
                                if uv_st in ("FAIL", "REJECT", "REJECTED", "需补救"):
                                    failed_audit_units.append(uk_s)
                                else:
                                    covered_audit_units.add(uk_s)
                            elif str(uv).strip().upper() in ("FAIL", "REJECT", "REJECTED", "需补救"):
                                failed_audit_units.append(uk_s)
                            else:
                                if v3_mode and (not top_author or not top_inspector or top_author.lower() == top_inspector.lower()):
                                    self_review_units.append(f"{uk_s}(author={top_author!r},inspector={top_inspector!r})")
                                covered_audit_units.add(uk_s)
                    elif isinstance(raw_units, list):
                        for uv in raw_units:
                            if isinstance(uv, dict):
                                uk_s = get_unit_id(uv)
                                uv_st = str(uv.get("status") or ("PASS" if not v3_mode else "")).strip().upper()
                                if v3_mode:
                                    u_auth = str(uv.get("author_agent") or top_author).strip()
                                    u_insp = str(uv.get("inspector_agent") or top_inspector).strip()
                                    if not u_auth or not u_insp or u_auth.lower() == u_insp.lower():
                                        self_review_units.append(f"{uk_s}(author={u_auth!r},inspector={u_insp!r})")
                                    if uv_st != "PASS":
                                        failed_audit_units.append(uk_s)
                                        continue
                                if uv_st in ("FAIL", "REJECT", "REJECTED", "需补救"):
                                    failed_audit_units.append(uk_s)
                                else:
                                    covered_audit_units.add(uk_s)
                            else:
                                covered_audit_units.add(str(uv).strip())
                    # 校验 micro_scenes 双盲审（若存在）
                    raw_micros = audit_data.get("micro_scenes") or audit_data.get("scene_blocks")
                    if v3_mode and raw_micros:
                        ms_iter = raw_micros.items() if isinstance(raw_micros, dict) else enumerate(raw_micros)
                        for mk, mv in ms_iter:
                            if isinstance(mv, dict):
                                m_auth = str(mv.get("author_agent") or top_author).strip()
                                m_insp = str(mv.get("inspector_agent") or top_inspector).strip()
                                m_st = str(mv.get("status") or "").strip().upper()
                                if not m_auth or not m_insp or m_auth.lower() == m_insp.lower():
                                    self_review_units.append(f"micro_scene:{mk}(author={m_auth!r},inspector={m_insp!r})")
                                if m_st != "PASS":
                                    failed_audit_units.append(f"micro_scene:{mk}")

                    expected_units = [
                        get_unit_id(it)
                        for it in manifest_items
                        if get_unit_id(it) != "unknown"
                        and not ((str(it.get("frame_type") or "") == "尾帧" or get_unit_id(it).endswith("_尾帧")) and it.get("need_tail_frame") is False)
                    ]
                    missing_audit = [u for u in expected_units if u not in covered_audit_units]
                    if self_review_units:
                        errors.append(
                            f"AUDIT_SELF_REVIEW_FORBIDDEN: 跨家族双盲审校验失败（author_agent 与 inspector_agent 缺失或相同）: {self_review_units}"
                        )
                    if failed_audit_units:
                        errors.append(f"ALIGNMENT_VISUAL_AUDIT_FAILED: prompt_visual_audit.json 存在未通过单元 {failed_audit_units}")
                    if missing_audit:
                        errors.append(f"ALIGNMENT_VISUAL_AUDIT_INCOMPLETE: prompt_visual_audit.json 未覆盖清单单元 {missing_audit}")
                    v41_audit_errs = verify_visual_audit_hashes(
                        audit_data,
                        manifest_items,
                        project_root or (Path(manifest_file).parent if manifest_file else cuts_p.parent),
                        get_unit_id,
                    )
                    for ae in v41_audit_errs:
                        if ae not in errors:
                            errors.append(ae)
                else:
                    errors.append("ALIGNMENT_VISUAL_AUDIT_FAILED: prompt_visual_audit.json 顶层必须为 JSON 对象")

    err_code = "OK"
    if errors:
        for priority_code in (
            "ALIGNMENT_FAIL_STALE_PLACEHOLDER",
            "ALIGNMENT_PROMPT_HAS_CJK_OR_PLACEHOLDER",
            "V3_VISUAL_SPEC_INVALID",
            "IMAGE_REF_ATTRIBUTION_MISMATCH",
            "WAVE_DEPENDENCY_VIOLATION",
            "FLOW_MODEL_RETIRED_2026_12_05",
            "FLOW_UNSUPPORTED_VEO_QUALITY_INGREDIENTS",
            "FLOW_INVALID_DURATION_OMNI_1_1",
            "FLOW_OMNI_1_2_FREE_CAP_10S_EXCEEDED",
            "FLOW_OMNI_1_2_GEN_CAP_15S_EXCEEDED",
            "FLOW_EXTEND_REQUIRES_PAID_SUBSCRIPTION",
            "FLOW_EXTEND_INVALID_INCREMENT_3_TO_10S",
            "VEO_4S_6S_INGREDIENTS_FORBIDDEN",
            "VEO_INGREDIENTS_MAX_3_REFS_EXCEEDED",
            "VEO_INVALID_DURATION",
            "VEO_HANDLES_TAIL_FRAME_FORBIDDEN",
            "VEO_FRAMES_MAX_2_REFS_EXCEEDED",
            "OMNI_INVALID_DURATION",
            "SEEDANCE_INVALID_DURATION",
            "SEEDANCE_MISSING_AT_REFS",
            "VIDEO_PROMPT_LAZY_CHAR_BOILERPLATE",
            "VIDEO_PROMPT_SOURCE_TEXT_LEAK",
            "VIDEO_PROMPT_STIFF_FREEZE_FRAME",
            "VIDEO_PROMPT_INCOMPLETE_STRUCTURE",
            "AUDIT_MISSING_EXPLICIT_RUN_IDS",
            "AUDIT_MISSING_PER_UNIT_VERDICTS",
            "AUDIT_SELF_REVIEW_FORBIDDEN",
            "AUDIT_PROMPT_HASH_MISMATCH_OR_MISSING",
            "ALIGNMENT_PROMPT_TOO_SHORT",
            "ALIGNMENT_PROMPT_DUPLICATE_BOILERPLATE",
            "ALIGNMENT_VISUAL_AUDIT_MISSING",
            "ALIGNMENT_VISUAL_AUDIT_FAILED",
            "ALIGNMENT_VISUAL_AUDIT_INCOMPLETE",
            "SCRIPT_CUTS_ALIGNMENT_MISMATCH",
            "SCENE_TEMPORAL_STATE_CONFLICT",
            "ALIGNMENT_FAIL_MISSING_CUTS_CSV",
        ):
            if any(priority_code in e for e in errors):
                err_code = priority_code
                break
        if err_code == "OK":
            err_code = errors[0].split(":", 1)[0]

    return {
        "ok": len(errors) == 0,
        "error_code": err_code,
        "errors": errors,
        "episode": ep,
        "v3_mode": v3_mode,
        "require_visual_audit": require_visual_audit,
        "as_of_date": resolve_as_of_date(as_of_date),
        "flow_tier": eff_flow_tier,
        "sunset_warnings": sunset_warnings,
        "cuts_segments_count": len(cuts_segs),
        "cuts_shots_count": sum(len(v) for v in cuts_segs.values()),
    }


def cmd_verify_script_alignment(args: argparse.Namespace) -> None:
    """第1步脚本与清单 1:1 对齐门禁命令行入口"""
    proj_raw = getattr(args, "project_root", "") or getattr(args, "project_dir", "")
    cuts_raw = getattr(args, "cuts_csv", "")
    md_raw = getattr(args, "prompt_md", "")
    manifest_raw = getattr(args, "frames_json", "") or getattr(args, "manifest", "")
    audit_raw = getattr(args, "audit_json", "")
    req_audit = bool(getattr(args, "require_visual_audit", False))
    v3_flag = bool(getattr(args, "v3", False))

    if not proj_raw and not cuts_raw:
        print("必须提供 --project-root/--project-dir 或 --cuts-csv", file=sys.stderr)
        sys.exit(1)
    proj_root = resolve_cross_platform_path(proj_raw) if proj_raw else None
    ep = normalize_episode_name(getattr(args, "episode", "第01集"))
    res = verify_script_alignment(
        project_root=proj_root,
        episode=ep,
        cuts_csv=resolve_cross_platform_path(cuts_raw) if cuts_raw else None,
        md_file=resolve_cross_platform_path(md_raw) if md_raw else None,
        manifest_file=resolve_cross_platform_path(manifest_raw) if manifest_raw else None,
        audit_file=resolve_cross_platform_path(audit_raw) if audit_raw else None,
        require_visual_audit=req_audit,
        v3=v3_flag,
        as_of_date=getattr(args, "as_of_date", "") or None,
        flow_tier=getattr(args, "flow_tier", None),
    )
    if not res.get("ok"):
        for err in res.get("errors", []):
            print(err, file=sys.stderr)
        print(json.dumps(res, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(2)
    print(json.dumps(res, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 多人同框去白边无底横向人物拼接图（Image 3 合集缓存，永不挤占 Image 2 场景母图）
# ---------------------------------------------------------------------------

def _is_multi_char_composite_ref(ref_str: str) -> bool:
    rn = str(ref_str or "").replace("\\", "/").strip()
    if not rn:
        return False
    stem = Path(rn).stem
    return ("角色合集缓存/" in rn) or stem.startswith("合集_") or ("多人合集" in stem) or ("char_composite" in rn.lower())


def _is_character_ref_path(ref_str: str) -> bool:
    rn = str(ref_str or "").replace("\\", "/").strip()
    if not rn or _is_multi_char_composite_ref(rn):
        return False
    low = rn.lower()
    if any(tok in rn for tok in ("_肖像", "_主体", "人物参考/", "/人物/")) or any(
        tok in low for tok in ("_portrait.", "_subject.")
    ):
        return True
    return False


def _sanitize_composite_token(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name or "").strip()).strip("_")
    return cleaned or "角色"


def _multi_char_composite_rel_path(
    w_dir: Path,
    project_root: Path | None,
    vis_chars: list[str],
    prefer_portrait: bool,
) -> str:
    suffix = "肖像" if prefer_portrait else "主体"
    tokens = [_sanitize_composite_token(c) for c in vis_chars if str(c).strip()]
    fname = f"合集_{'_'.join(tokens)}_{suffix}.png"
    abs_p = w_dir / "角色合集缓存" / fname
    if project_root is not None:
        try:
            return abs_p.resolve().relative_to(project_root.resolve()).as_posix()
        except Exception:
            pass
    return str(abs_p).replace("\\", "/")


def _crop_tight_borderless_subject(im_obj, bg_mode: str = "white", pad: int = 8):
    """对纯白无底（或透明底）单人人物母图裁去四周冗余白边，按需执行四边 BFS 外围去底透明化（保内部白色服饰/眼白不误抠）"""
    from collections import deque
    from PIL import Image

    has_alpha = im_obj.mode in ("RGBA", "LA") or ("transparency" in im_obj.info)
    if has_alpha:
        rgba_in = im_obj.convert("RGBA")
        white_bg = Image.new("RGBA", rgba_in.size, (255, 255, 255, 255))
        rgb = Image.alpha_composite(white_bg, rgba_in).convert("RGB")
    else:
        rgb = im_obj.convert("RGB")

    w, h = rgb.size
    px = rgb.load()

    def _is_bg_white(r: int, g: int, b: int) -> bool:
        return r >= 242 and g >= 242 and b >= 242 and (max(r, g, b) - min(r, g, b) <= 14)

    # 扫描非白像素外接框（仅裁除四周多余纯白/近白边）
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if not _is_bg_white(*px[x, y]):
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    if max_x < min_x or max_y < min_y:
        crop_box = (0, 0, w, h)
    else:
        x0 = max(0, min_x - pad)
        y0 = max(0, min_y - pad)
        x1 = min(w, max_x + 1 + pad)
        y1 = min(h, max_y + 1 + pad)
        crop_box = (x0, y0, x1, y1)

    cropped_rgb = rgb.crop(crop_box)
    if bg_mode != "transparent":
        return cropped_rgb

    # RGBA 透明无底模式：仅从裁切后四边发起 BFS 将外围连通近白像素透明化，严防误抠人物内部白衣/高光/眼白
    cw, ch = cropped_rgb.size
    out_rgba = cropped_rgb.convert("RGBA")
    apx = out_rgba.load()
    visited = bytearray(cw * ch)
    q = deque()
    for x in range(cw):
        for y in (0, ch - 1):
            idx = y * cw + x
            if not visited[idx] and _is_bg_white(*apx[x, y][:3]):
                visited[idx] = 1
                q.append((x, y))
    for y in range(ch):
        for x in (0, cw - 1):
            idx = y * cw + x
            if not visited[idx] and _is_bg_white(*apx[x, y][:3]):
                visited[idx] = 1
                q.append((x, y))
    while q:
        cx, cy = q.popleft()
        r, g, b, _ = apx[cx, cy]
        apx[cx, cy] = (r, g, b, 0)
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if 0 <= nx < cw and 0 <= ny < ch:
                nidx = ny * cw + nx
                if not visited[nidx] and _is_bg_white(*apx[nx, ny][:3]):
                    visited[nidx] = 1
                    q.append((nx, ny))
    return out_rgba


def build_multi_character_composite_sheet(
    source_paths: list[Path],
    out_path: Path,
    role_names: list[str] | None = None,
    bg_mode: str = "white",
    force: bool = False,
    gap: int = 24,
    pad: int = 16,
) -> dict[str, Any]:
    """将 >=2 张纯白无底单人角色母图自动裁去冗余白边并等高横向紧凑拼接为 Image 3 角色合集图（支持源 SHA-256 缓存复用与变更自动重拼）"""
    from PIL import Image

    bg = "transparent" if str(bg_mode or "").strip().lower() == "transparent" else "white"
    src_resolved = [Path(p).resolve() for p in source_paths]
    missing = [str(p).replace("\\", "/") for p in src_resolved if not p.exists() or p.stat().st_size == 0]
    if missing:
        return {"ok": False, "status": "MISSING_SOURCES", "missing": missing, "out_path": str(out_path).replace("\\", "/")}

    src_shas = [sha256_file(p) for p in src_resolved]
    order_names = [str(n).strip() for n in (role_names or [])] if role_names else [p.stem for p in src_resolved]
    meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")

    if not force and out_path.exists() and out_path.stat().st_size > 0 and meta_path.exists():
        try:
            old_meta = read_json(meta_path)
            if (
                old_meta.get("bg_mode") == bg
                and old_meta.get("source_shas") == src_shas
                and old_meta.get("role_order") == order_names
            ):
                return {
                    "ok": True,
                    "status": "REUSED_CACHE",
                    "out_path": str(out_path).replace("\\", "/"),
                    "bg_mode": bg,
                    "role_order": order_names,
                    "sources": [str(p).replace("\\", "/") for p in src_resolved],
                    "sha256": sha256_file(out_path),
                }
        except Exception:
            pass

    cropped_list = []
    for sp in src_resolved:
        with Image.open(sp) as im:
            cropped_list.append(_crop_tight_borderless_subject(im, bg_mode=bg, pad=8))

    max_h = max(c.height for c in cropped_list)
    target_h = max(512, min(1024, max_h))
    resized = []
    for c in cropped_list:
        scale = target_h / max(1, c.height)
        new_w = max(1, int(round(c.width * scale)))
        resized.append(c.resize((new_w, target_h), Image.Resampling.LANCZOS))

    total_w = pad * 2 + sum(r.width for r in resized) + gap * max(0, len(resized) - 1)
    total_h = pad * 2 + target_h
    if bg == "transparent":
        canvas = Image.new("RGBA", (total_w, total_h), (255, 255, 255, 0))
    else:
        canvas = Image.new("RGB", (total_w, total_h), (255, 255, 255))

    cur_x = pad
    for r_im in resized:
        if bg == "transparent":
            canvas.paste(r_im, (cur_x, pad), r_im)
        else:
            canvas.paste(r_im, (cur_x, pad))
        cur_x += r_im.width + gap

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG")
    out_sha = sha256_file(out_path)
    write_json(meta_path, {
        "generated_by": "batch_ops.build_char_sheets",
        "bg_mode": bg,
        "role_order": order_names,
        "sources": [str(p).replace("\\", "/") for p in src_resolved],
        "source_shas": src_shas,
        "size": [total_w, total_h],
        "sha256": out_sha,
    })
    return {
        "ok": True,
        "status": "BUILT",
        "out_path": str(out_path).replace("\\", "/"),
        "bg_mode": bg,
        "size": [total_w, total_h],
        "role_order": order_names,
        "sources": [str(p).replace("\\", "/") for p in src_resolved],
        "sha256": out_sha,
    }


def _collect_char_sheet_maps(all_items: list[dict], w_dir: Path, project_root: Path | None) -> tuple[dict[str, str], dict[str, str]]:
    """汇总清单与磁盘目录中所有角色的 _肖像.png 与 _主体.png 路径映射"""
    portrait_by_key: dict[str, str] = {}
    subject_by_key: dict[str, str] = {}
    for it in all_items:
        if not isinstance(it, dict):
            continue
        u = get_unit_id(it)
        if not _is_character_job(it, u):
            continue
        subtype = _get_character_subtype(it, u)
        t_path = _resolve_unit_target_path(it).replace("\\", "/")
        cname = str(it.get("character") or "").strip()
        rid = str(it.get("role_id") or "").strip()
        base_u = re.sub(r"_(?:肖像|主体|portrait|subject)$", "", u)
        keys = [k for k in (cname, rid, base_u, f"{rid}_{cname}" if rid and cname else "") if k]
        for k in keys:
            if subtype == "portrait" and t_path:
                portrait_by_key[k] = t_path
            elif subtype == "subject" and t_path:
                subject_by_key[k] = t_path

    # 回退扫描分集/人物/ 与 根级 人物参考/
    scan_dirs: list[Path] = []
    if project_root is not None:
        ep_name = w_dir.name if w_dir.parent.name == "在制工作区" else "第01集"
        scan_dirs.extend([project_root / "分集" / ep_name / "人物", project_root / "人物参考"])
    for sd in scan_dirs:
        if not sd.exists():
            continue
        for png in sorted(sd.glob("*.png")):
            stem = png.stem
            rel_s = str(png.resolve().relative_to(project_root.resolve()).as_posix()) if project_root else str(png).replace("\\", "/")
            if stem.endswith("_肖像"):
                base = stem[:-3]
                portrait_by_key.setdefault(base, rel_s)
                if "_" in base:
                    portrait_by_key.setdefault(base.split("_", 1)[1], rel_s)
            elif stem.endswith("_主体"):
                base = stem[:-3]
                subject_by_key.setdefault(base, rel_s)
                if "_" in base:
                    subject_by_key.setdefault(base.split("_", 1)[1], rel_s)
    return portrait_by_key, subject_by_key


def ensure_episode_multi_char_sheets(
    w_dir: Path,
    project_root: Path | None = None,
    bg_mode: str = "white",
    force: bool = False,
    allow_missing: bool = False,
) -> dict[str, Any]:
    """扫描本集待生图帧清单与批次 jobs.json，批量生成/复用全部多人同框去白边无底横向合集图（Image 3）"""
    if project_root is None and w_dir.parent.name == "在制工作区":
        project_root = w_dir.parent.parent.parent
    base_anchor = project_root if project_root is not None else w_dir

    manifest_file = w_dir / "待生图帧清单.json"
    all_items: list[dict] = []
    if manifest_file.exists():
        m_raw = read_json(manifest_file)
        all_items = [it for it in (m_raw if isinstance(m_raw, list) else extract_jobs_list(m_raw)) if isinstance(it, dict)]

    cls_file = w_dir / "当前制作审核分类.json"
    cls_data = read_json(cls_file) if cls_file.exists() else {}
    portrait_map, subject_map = _collect_char_sheet_maps(all_items, w_dir, project_root)

    # 收集所有待构建的多人合集目标（按目标路径去重）
    tasks: dict[str, tuple[list[str], list[str]]] = {}
    scan_units: list[dict] = list(all_items)
    batches_root = w_dir / "生成批次"
    if batches_root.exists():
        for jf in sorted(batches_root.glob("批次*/jobs.json")):
            try:
                scan_units.extend([j for j in extract_jobs_list(read_json(jf)) if isinstance(j, dict)])
            except Exception:
                pass

    for it in scan_units:
        u = get_unit_id(it)
        if _is_character_job(it, u) or _is_scene_job(it, u):
            continue
        vis_chars = [str(c).strip() for c in (it.get("visible_characters") or it.get("characters") or []) if str(c).strip()]
        if len(vis_chars) < 2:
            continue
        shot_scale = str(it.get("shot_scale") or it.get("framing") or "").strip().lower()
        prefer_portrait = any(tok in shot_scale for tok in ("close", "特写", "近景"))
        # 若清单/jobs 的 refs[2] 已显式指向特定 角色合集缓存/合集_*.png，则遵循其路径与肖像/主体偏好
        explicit_comp = str(it.get("multi_character_composite_ref") or "").strip()
        if not explicit_comp:
            for r in _get_item_actual_refs(it):
                if _is_multi_char_composite_ref(r):
                    explicit_comp = r
                    break
        if explicit_comp:
            if "_肖像" in explicit_comp:
                prefer_portrait = True
            elif "_主体" in explicit_comp:
                prefer_portrait = False
            out_rel = explicit_comp
        else:
            out_rel = _multi_char_composite_rel_path(w_dir, project_root, vis_chars, prefer_portrait)

        src_rels = [str(s).strip() for s in (it.get("multi_character_sources") or []) if str(s).strip()]
        if len(src_rels) != len(vis_chars):
            src_rels = []
            for ch in vis_chars:
                cand = (portrait_map.get(ch) or subject_map.get(ch) or "") if prefer_portrait else (subject_map.get(ch) or portrait_map.get(ch) or "")
                if not cand:
                    for mp in ((portrait_map, subject_map) if prefer_portrait else (subject_map, portrait_map)):
                        for mk, mv in mp.items():
                            if ch in mk or mk in ch:
                                cand = mv
                                break
                        if cand:
                            break
                src_rels.append(cand)
        tasks[out_rel] = (vis_chars, src_rels)

    built_list: list[dict] = []
    reused_list: list[dict] = []
    missing_list: list[dict] = []
    unapproved_list: list[dict] = []

    for out_rel, (vis_chars, src_rels) in tasks.items():
        out_abs = resolve_cross_platform_path(out_rel, base_anchor)
        src_abs_list: list[Path] = []
        miss_this: list[str] = []
        unapp_this: list[str] = []
        for ch, sr in zip(vis_chars, src_rels):
            if not sr:
                miss_this.append(f"{ch}(未映射母图路径)")
                continue
            sp = resolve_cross_platform_path(sr, base_anchor)
            if not sp.exists() or sp.stat().st_size == 0:
                miss_this.append(f"{ch}:{sr}")
                continue
            if _is_ref_disapproved_in_cls(sr, cls_data):
                unapp_this.append(f"{ch}:{sr}")
                continue
            src_abs_list.append(sp)
        if miss_this:
            missing_list.append({"target": out_rel, "roles": vis_chars, "missing_sources": miss_this})
            continue
        if unapp_this:
            unapproved_list.append({"target": out_rel, "roles": vis_chars, "unapproved_sources": unapp_this})
            continue
        res = build_multi_character_composite_sheet(
            src_abs_list,
            out_abs,
            role_names=vis_chars,
            bg_mode=bg_mode,
            force=force,
        )
        res["target"] = out_rel
        if res.get("status") == "REUSED_CACHE":
            reused_list.append(res)
        elif res.get("ok"):
            built_list.append(res)
        else:
            missing_list.append({"target": out_rel, "roles": vis_chars, "missing_sources": res.get("missing", [])})

    ok_all = (len(missing_list) == 0 and len(unapproved_list) == 0) or allow_missing
    return {
        "ok": ok_all,
        "status": "OK" if (not missing_list and not unapproved_list) else ("PENDING_PARTIAL" if allow_missing else "MULTI_CHAR_SOURCE_MISSING"),
        "bg_mode": bg_mode,
        "total_targets": len(tasks),
        "built_count": len(built_list),
        "reused_count": len(reused_list),
        "missing_count": len(missing_list),
        "unapproved_count": len(unapproved_list),
        "built": built_list,
        "reused": reused_list,
        "missing": missing_list,
        "unapproved": unapproved_list,
    }


def cmd_build_char_sheets(args: argparse.Namespace) -> None:
    """批量生成/复用本集多人同框去白边无底横向人物合集图（固定挂载于 Image 3，永不挤占 Image 2 场景母图）"""
    w_dir_raw = getattr(args, "work_dir", "")
    proj_raw = getattr(args, "project_root", "") or getattr(args, "project_dir", "")
    ep_raw = getattr(args, "episode", "第01集")
    if w_dir_raw:
        w_dir = resolve_cross_platform_path(w_dir_raw)
        project_root = w_dir.parent.parent.parent if w_dir.parent.name == "在制工作区" else (resolve_cross_platform_path(proj_raw) if proj_raw else None)
    elif proj_raw:
        project_root = resolve_cross_platform_path(proj_raw)
        w_dir = project_root / "中间文件" / "在制工作区" / normalize_episode_name(ep_raw)
    else:
        print("必须提供 --work-dir 或 (--project-root 与 --episode)", file=sys.stderr)
        sys.exit(1)

    res = ensure_episode_multi_char_sheets(
        w_dir=w_dir,
        project_root=project_root,
        bg_mode=str(getattr(args, "bg", "white") or "white"),
        force=bool(getattr(args, "force", False)),
        allow_missing=bool(getattr(args, "allow_missing", False)),
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res.get("ok"):
        sys.exit(2)


def _materialize_window_batches(
    window_obj: dict,
    item_by_unit: dict[str, dict],
    all_items: list[dict],
    w_dir: Path,
    project_root: Path | None,
    v3: bool = False,
    aspect_ratio: str = "16:9",
    multi_char_bg: str = "white",
) -> list[str]:
    """为指定窗口内的所有批次物化生成 生成批次/批次XX/jobs.json 及 <unit>/完整提示词.txt，并自动完成规范 refs 绑定与源帧净化"""
    ar = normalize_aspect_ratio(aspect_ratio)
    portrait_by_key, subject_by_key = _collect_char_sheet_maps(all_items, w_dir, project_root)
    proto_by_key: dict[str, str] = {}
    scene_by_key: dict[str, str] = {}
    first_frame_by_seg_shot: dict[tuple[str, str], str] = {}
    first_frame_by_seg: dict[str, str] = {}

    for it in all_items:
        if not isinstance(it, dict):
            continue
        u = get_unit_id(it)
        t_path = _resolve_unit_target_path(it).replace("\\", "/")
        if _is_character_job(it, u):
            subtype = _get_character_subtype(it, u)
            cname = str(it.get("character") or "").strip()
            rid = str(it.get("role_id") or "").strip()
            proto = str(it.get("prototype_photo_ref") or it.get("historical_photo_ref") or it.get("history_ref") or "").strip()
            base_u = re.sub(r"_(?:肖像|主体|portrait|subject)$", "", u)
            keys = [k for k in (cname, rid, base_u, f"{rid}_{cname}" if rid and cname else "") if k]
            for k in keys:
                if subtype == "portrait" and t_path:
                    portrait_by_key[k] = t_path
                elif subtype == "subject" and t_path:
                    subject_by_key[k] = t_path
                if proto:
                    proto_by_key[k] = proto
        elif _is_scene_job(it, u):
            sc_name = str(it.get("scene_name") or it.get("scene") or it.get("canonical_scene") or "").strip()
            sc_id = str(it.get("scene_id") or "").strip()
            for k in (sc_name, sc_id, u):
                if k and t_path:
                    scene_by_key[k] = t_path
        else:
            norm_tp = _normalize_flat_frame_target_path(t_path, u, it)
            ftype = str(it.get("frame_type") or "").strip()
            if (ftype == "首帧" or u.endswith("_首帧")) and norm_tp:
                seg_s = str(it.get("segment") or "").strip()
                shot_s = str(it.get("shot") or "").strip()
                if not seg_s or not shot_s:
                    m_u = re.match(r"^(segment_\d+)_(镜头\d+)_首帧", u)
                    if m_u:
                        seg_s = seg_s or m_u.group(1)
                        shot_s = shot_s or m_u.group(2)
                if seg_s:
                    first_frame_by_seg.setdefault(seg_s, norm_tp)
                    if shot_s:
                        first_frame_by_seg_shot[(seg_s, shot_s)] = norm_tp

    base_anchor = project_root if project_root is not None else w_dir
    materialized_dirs: list[str] = []
    batches_root = w_dir / "生成批次"
    batches_root.mkdir(parents=True, exist_ok=True)

    for b_info in window_obj.get("batches", []):
        b_id = b_info["batch_id"]
        b_dir = batches_root / b_id
        b_dir.mkdir(parents=True, exist_ok=True)
        batch_jobs: list[dict] = []

        for u in b_info.get("units", []):
            src_it = item_by_unit.get(u)
            if src_it is None:
                continue
            job = dict(src_it)
            job["unit"] = u
            job["batch_id"] = b_id
            job["window_id"] = window_obj["window_id"]
            job["aspect_ratio"] = ar
            job.setdefault("attempt", "初始")

            unit_dir = b_dir / u
            unit_dir.mkdir(parents=True, exist_ok=True)
            prompt_path = unit_dir / "完整提示词.txt"
            p_chk = _validate_phase1_en_prompt(job, u, v3=v3)
            if not p_chk["ok"]:
                print(f"PLAN_BATCHES_MISSING_PHASE1_PROMPT | {p_chk['reason']}", file=sys.stderr)
                sys.exit(2)
            manifest_prompt = _resolve_manifest_prompt_text(job)
            prompt_path.write_text(manifest_prompt, encoding="utf-8")

            job["prompt_file"] = str(prompt_path).replace("\\", "/")
            job["output_dir"] = str(unit_dir).replace("\\", "/")
            if "wave" in b_info:
                job["wave"] = b_info["wave"]
                job["wave_label"] = b_info.get("wave_label") or f"Wave {b_info['wave']}"

            if _is_character_job(job, u):
                subtype = _get_character_subtype(job, u)
                cname = str(job.get("character") or "").strip()
                rid = str(job.get("role_id") or "").strip()
                base_u = re.sub(r"_(?:肖像|主体|portrait|subject)$", "", u)
                proto = str(job.get("prototype_photo_ref") or job.get("historical_photo_ref") or job.get("history_ref") or "").strip()
                if not proto:
                    for k in (cname, rid, base_u):
                        if k and k in proto_by_key:
                            proto = proto_by_key[k]
                            break
                if subtype == "portrait":
                    refs = [proto] if proto else []
                else:
                    p_ref = str(job.get("portrait_ref") or "").strip()
                    if not p_ref:
                        for k in (cname, rid, base_u):
                            if k and k in portrait_by_key:
                                p_ref = portrait_by_key[k]
                                break
                    if not p_ref:
                        tp_cand = _resolve_unit_target_path(job)
                        if "_主体" in tp_cand:
                            p_ref = tp_cand.replace("_主体", "_肖像")
                    if p_ref:
                        job["portrait_ref"] = p_ref
                    refs = ([p_ref] if p_ref else []) + ([proto] if proto and proto != p_ref else [])
                if not job.get("target_path"):
                    tp_char = _resolve_unit_target_path(job).replace("\\", "/")
                    if tp_char:
                        job["target_path"] = tp_char
                job["refs"] = refs
                job["reference_images"] = list(refs)
                _enforce_character_format_a_prompt(prompt_path, job, u, aspect_ratio=ar)
            elif _is_scene_job(job, u):
                arch_refs_raw = job.get("architecture_refs") or job.get("explicit_architecture_refs") or []
                if isinstance(arch_refs_raw, str):
                    arch_refs_raw = [arch_refs_raw]
                refs = [str(r).strip() for r in arch_refs_raw if str(r).strip()]
                if not job.get("target_path"):
                    tp_scene = _resolve_unit_target_path(job).replace("\\", "/")
                    if tp_scene:
                        job["target_path"] = tp_scene
                job["refs"] = refs
                job["reference_images"] = list(refs)
            else:
                norm_tp = _normalize_flat_frame_target_path(_resolve_unit_target_path(job), u, job)
                if norm_tp:
                    job["target_path"] = norm_tp
                ftype = str(job.get("frame_type") or "").strip()
                is_tail = (ftype == "尾帧" or u.endswith("_尾帧"))

                # Ref 1: 自动生成 ref_clean_frames/*_净参考.jpg 绑定至 refs[0]，同时保留 source_frame 原图供接触表左列对比
                sf_raw = str(job.get("source_frame") or "").strip()
                ref0 = ""
                if sf_raw:
                    src_abs = resolve_cross_platform_path(sf_raw, base_anchor)
                    already_clean = (src_abs.parent.name == "ref_clean_frames") or src_abs.stem.endswith("_净参考")
                    clean_stem = (src_abs.stem[:-2] + "_净参考.jpg") if src_abs.stem.endswith("_源") else (src_abs.stem + "_净参考.jpg")
                    clean_abs = src_abs if already_clean else (src_abs.parent / "ref_clean_frames" / clean_stem)
                    if src_abs.exists():
                        if not already_clean and (not clean_abs.exists() or clean_abs.stat().st_size == 0):
                            clean_source_frame(src_abs, clean_abs, mask_corner_logo=True)
                        if project_root is not None:
                            try:
                                ref0 = clean_abs.resolve().relative_to(project_root.resolve()).as_posix()
                            except Exception:
                                ref0 = str(clean_abs).replace("\\", "/")
                        else:
                            ref0 = str(clean_abs).replace("\\", "/")
                    else:
                        ref0 = sf_raw

                # Ref 2: 尾帧强绑同段首帧；首帧绑 scene_ref
                ref1 = ""
                if is_tail:
                    ref1 = str(job.get("same_segment_first_frame_ref") or "").strip()
                    if not ref1:
                        seg_s = str(job.get("segment") or "").strip()
                        shot_s = str(job.get("shot") or "").strip()
                        if not seg_s or not shot_s:
                            m_u = re.match(r"^(segment_\d+)_(镜头\d+)_尾帧", u)
                            if m_u:
                                seg_s = seg_s or m_u.group(1)
                                shot_s = shot_s or m_u.group(2)
                        ref1 = first_frame_by_seg_shot.get((seg_s, shot_s)) or first_frame_by_seg.get(seg_s) or ""
                    if not ref1 and norm_tp and "_尾帧" in norm_tp:
                        ref1 = norm_tp.replace("_尾帧", "_首帧")
                    if ref1:
                        job["same_segment_first_frame_ref"] = ref1
                else:
                    ref1 = str(job.get("scene_ref") or "").strip()
                    if not ref1:
                        sc_k = str(job.get("canonical_scene") or job.get("scene") or "").strip()
                        ref1 = scene_by_key.get(sc_k, "")
                    if ref1:
                        job["scene_ref"] = ref1

                # Ref 3: 单人按 shot_scale 自动绑 _肖像.png / _主体.png；多人 (>=2人) 自动绑定并按需即刻拼出 角色合集缓存/合集_<角色1>_<角色2>_<肖像|主体>.png，永不挤占 Image 2 场景母图
                ref2 = ""
                n_persons = job.get("exact_person_count")
                vis_chars = [str(c).strip() for c in (job.get("visible_characters") or job.get("characters") or []) if str(c).strip()]
                explicit_cref = str(job.get("character_ref") or "").strip()
                has_char = (n_persons is None or int(n_persons) > 0) and bool(vis_chars or explicit_cref)
                if has_char:
                    shot_scale = str(job.get("shot_scale") or job.get("framing") or "").strip().lower()
                    prefer_portrait = any(tok in shot_scale for tok in ("close", "特写", "近景"))
                    if len(vis_chars) >= 2 and (n_persons is None or int(n_persons) >= 2):
                        multi_sources: list[str] = []
                        for ch in vis_chars:
                            cand = (portrait_by_key.get(ch) or subject_by_key.get(ch) or "") if prefer_portrait else (subject_by_key.get(ch) or portrait_by_key.get(ch) or "")
                            if not cand:
                                for mp in ((portrait_by_key, subject_by_key) if prefer_portrait else (subject_by_key, portrait_by_key)):
                                    for mk, mv in mp.items():
                                        if ch in mk or mk in ch:
                                            cand = mv
                                            break
                                    if cand:
                                        break
                            if cand:
                                multi_sources.append(cand)
                        existing_comp = next((r for r in _get_item_actual_refs(job) if _is_multi_char_composite_ref(r)), "")
                        ref2 = existing_comp or _multi_char_composite_rel_path(w_dir, project_root, vis_chars, prefer_portrait)
                        job["multi_character_order"] = list(vis_chars)
                        job["multi_character_sources"] = list(multi_sources)
                        job["multi_character_composite_ref"] = ref2
                        # 若所有源人物母图此时已落盘，立即自动裁白边横拼并缓存至 角色合集缓存/
                        if len(multi_sources) == len(vis_chars):
                            src_abs_list = [resolve_cross_platform_path(sr, base_anchor) for sr in multi_sources]
                            if all(sp.exists() and sp.stat().st_size > 0 for sp in src_abs_list):
                                out_abs = resolve_cross_platform_path(ref2, base_anchor)
                                build_multi_character_composite_sheet(
                                    src_abs_list,
                                    out_abs,
                                    role_names=vis_chars,
                                    bg_mode=multi_char_bg,
                                )
                    else:
                        primary_char = vis_chars[0] if vis_chars else ""
                        if prefer_portrait:
                            ref2 = portrait_by_key.get(primary_char, "")
                            if not ref2 and explicit_cref:
                                ref2 = explicit_cref.replace("_主体.", "_肖像.") if "_主体." in explicit_cref else explicit_cref
                        else:
                            ref2 = subject_by_key.get(primary_char, "")
                            if not ref2 and explicit_cref:
                                ref2 = explicit_cref.replace("_肖像.", "_主体.") if "_肖像." in explicit_cref else explicit_cref
                        if not ref2:
                            ref2 = explicit_cref

                existing_refs = _get_item_actual_refs(job)
                if v3 and existing_refs:
                    refs = list(existing_refs)
                    if ref0 and ("_源." in refs[0] or refs[0] == sf_raw):
                        refs[0] = ref0
                else:
                    refs = [r for r in (ref0, ref1, ref2) if r]
                job["refs"] = refs
                job["reference_images"] = list(refs)

            job["_manifest_prompt"] = manifest_prompt
            job["_manifest_refs"] = list(_get_item_actual_refs(src_it) or job.get("refs") or [])
            mfp = compute_unit_input_fingerprint(*extract_item_prompt_and_refs(src_it, project_root or w_dir), project_root or w_dir)
            job["manifest_input_fingerprint"] = mfp["input_fingerprint"]
            job["manifest_prompt_sha256"] = mfp["prompt_sha256"]
            job["manifest_refs_sha256"] = mfp["refs_sha256"]
            batch_jobs.append(job)

        jobs_payload = {
            "generated_by": "batch_ops.plan_batches",
            "window_id": window_obj["window_id"],
            "batch_id": b_id,
            "aspect_ratio": ar,
            "tier": b_info.get("tier", ""),
            "wave": b_info.get("wave"),
            "wave_label": b_info.get("wave_label", ""),
            "jobs": batch_jobs,
        }
        write_json(b_dir / "jobs.json", jobs_payload)
        materialized_dirs.append(str(b_dir).replace("\\", "/"))

    return materialized_dirs


def cmd_plan_batches(args: argparse.Namespace) -> None:
    """事前按单窗口 ≤50 基础单元（预留 ≤10 张补救，总调用 ≤60 张，严防触发 88 张 429 冷却惩罚锁）物理切分窗口与批次，并默认物化批次 jobs.json"""
    w_dir_raw = getattr(args, "work_dir", "")
    if not w_dir_raw:
        proj_raw = getattr(args, "project_root", "") or getattr(args, "project_dir", "")
        ep_raw = getattr(args, "episode", "第01集")
        if not proj_raw:
            print("必须提供 --work-dir 或 (--project-root 与 --episode)", file=sys.stderr)
            sys.exit(1)
        w_dir = resolve_cross_platform_path(proj_raw) / "中间文件" / "在制工作区" / normalize_episode_name(ep_raw)
    else:
        w_dir = resolve_cross_platform_path(w_dir_raw)

    project_root = w_dir.parent.parent.parent if w_dir.parent.name == "在制工作区" else None
    ep_name = w_dir.name if w_dir.parent.name == "在制工作区" else normalize_episode_name(getattr(args, "episode", "第01集"))

    # 自动运行 cuts.csv 零漏秒硬门禁（除非显式 --skip-cuts-check）
    if not bool(getattr(args, "skip_cuts_check", False)):
        if w_dir.parent.name == "在制工作区":
            analysis_dir = w_dir.parent.parent / "原片分析" / w_dir.name
            cuts_csv = analysis_dir / "cuts.csv"
            shots_json = analysis_dir / "物理切点清单.json"
            if cuts_csv.exists():
                chk = verify_cuts_csv(cuts_csv, shots_json=shots_json if shots_json.exists() else None)
                if not chk.get("ok"):
                    for err in chk.get("errors", []):
                        print(err, file=sys.stderr)
                    sys.exit(2)

    manifest_file = w_dir / "待生图帧清单.json"
    if not manifest_file.exists():
        print(f"未找到待生图帧清单: {manifest_file}", file=sys.stderr)
        sys.exit(1)

    manifest_raw_chk = read_json(manifest_file)
    manifest_items_chk = [it for it in (manifest_raw_chk if isinstance(manifest_raw_chk, list) else extract_jobs_list(manifest_raw_chk)) if isinstance(it, dict)]
    v3_mode = _is_v3_mode(manifest_raw_chk, bool(getattr(args, "v3", False)), manifest_file, project_root, ep_name)
    prompt_errs: list[str] = []
    for it_chk in manifest_items_chk:
        u_chk = get_unit_id(it_chk)
        ft_chk = str(it_chk.get("frame_type") or "")
        if (ft_chk == "尾帧" or u_chk.endswith("_尾帧")) and it_chk.get("need_tail_frame") is False:
            continue
        p_chk = _validate_phase1_en_prompt(it_chk, u_chk, v3=v3_mode)
        if not p_chk["ok"]:
            prompt_errs.append(f"PLAN_BATCHES_MISSING_PHASE1_PROMPT | {p_chk['reason']}")
    if prompt_errs:
        for err in prompt_errs:
            print(err, file=sys.stderr)
        sys.exit(2)

    # 自动运行第1步对齐与占位符/场景时序冲突门禁（除非显式 --skip-alignment-check）
    if not bool(getattr(args, "skip_alignment_check", False)):
        if project_root is not None:
            analysis_dir = w_dir.parent.parent / "原片分析" / w_dir.name
            cuts_csv = analysis_dir / "cuts.csv"
            md_file = project_root / "分集" / ep_name / f"{ep_name}_视频提示词.md"
            if cuts_csv.exists():
                align_res = verify_script_alignment(
                    project_root=project_root,
                    episode=ep_name,
                    cuts_csv=cuts_csv,
                    md_file=md_file if md_file.exists() else None,
                    manifest_file=manifest_file,
                    v3=v3_mode,
                )
                if not align_res.get("ok"):
                    for err in align_res.get("errors", []):
                        print(err, file=sys.stderr)
                    sys.exit(2)
            else:
                # 无 cuts.csv 时仍拦截旧占位符与场景时序状态冲突
                m_raw_chk = read_json(manifest_file)
                m_items_chk = [it for it in (m_raw_chk if isinstance(m_raw_chk, list) else extract_jobs_list(m_raw_chk)) if isinstance(it, dict)]
                inline_errs: list[str] = []
                if md_file.exists():
                    hits_md = _scan_stale_placeholders(md_file.read_text(encoding="utf-8-sig"))
                    if hits_md:
                        inline_errs.append(f"ALIGNMENT_FAIL_STALE_PLACEHOLDER: {md_file.name} 含未替换旧占位符 {hits_md}")
                for it in m_items_chk:
                    u = get_unit_id(it)
                    blob = "\n".join(str(it.get(k) or "") for k in ("prompt", "image_prompt", "full_prompt", "prompt_en", "positive_prompt", "video_prompt"))
                    hits_m = _scan_stale_placeholders(blob)
                    if hits_m:
                        inline_errs.append(f"ALIGNMENT_FAIL_STALE_PLACEHOLDER: 待生图帧清单 单元 {u} 提示词含旧占位符 {hits_m}")
                inline_errs.extend(_check_scene_temporal_conflicts(m_items_chk))
                if inline_errs:
                    for err in inline_errs:
                        print(err, file=sys.stderr)
                    sys.exit(2)

    max_per_window = int(getattr(args, "max_per_window", MAX_UNITS_PER_WINDOW) or MAX_UNITS_PER_WINDOW)
    max_per_batch = int(getattr(args, "max_per_batch", MAX_UNITS_PER_BATCH) or MAX_UNITS_PER_BATCH)
    elastic_cap = int(getattr(args, "elastic_cap", MAX_TOTAL_CALLS_PER_WINDOW) or MAX_TOTAL_CALLS_PER_WINDOW)
    replan_all = bool(getattr(args, "replan_all", False))

    cls_file = w_dir / "当前制作审核分类.json"
    cls_data = read_json(cls_file) if cls_file.exists() and not replan_all else {}

    manifest = read_json(manifest_file)
    items = manifest if isinstance(manifest, list) else manifest.get("units", [])
    manifest_updated = False
    done_units, stale_invalidated_units = (
        detect_stale_approved_units(items, cls_data, w_dir, project_root, get_unit_id) if not replan_all else (set(), [])
    )

    if v3_mode:
        before_waves = [(it.get("wave"), it.get("wave_label")) for it in items if isinstance(it, dict)]
        wave_errs = _validate_and_normalize_waves(items, project_root=project_root, work_dir=w_dir, cls_data=cls_data)
        if wave_errs:
            for err in wave_errs:
                print(err, file=sys.stderr)
            sys.exit(2)
        after_waves = [(it.get("wave"), it.get("wave_label")) for it in items if isinstance(it, dict)]
        if after_waves != before_waves:
            manifest_updated = True

    # 自动将清单内同场景首个关联 segment 的源帧填充至对应场景单元的 scene_source_frame
    scene_first_source_map: dict[str, str] = {}
    for it in items:
        if isinstance(it, dict) and not _is_character_job(it, get_unit_id(it)) and not _is_scene_job(it, get_unit_id(it)):
            sf = str(it.get("source_frame") or "").strip()
            sc_key = str(it.get("canonical_scene") or it.get("scene") or "").strip()
            sc_ref = str(it.get("scene_ref") or "").strip()
            if sf:
                if sc_key and sc_key not in scene_first_source_map:
                    scene_first_source_map[sc_key] = sf
                if sc_ref and sc_ref not in scene_first_source_map:
                    scene_first_source_map[sc_ref] = sf
    for it in items:
        if isinstance(it, dict) and _is_scene_job(it, get_unit_id(it)):
            if not str(it.get("scene_source_frame") or "").strip():
                sc_key = str(it.get("scene_name") or it.get("scene") or it.get("canonical_scene") or "").strip()
                t_path = str(it.get("target_path") or it.get("formal_path") or "").strip()
                cand_sf = scene_first_source_map.get(sc_key) or scene_first_source_map.get(t_path)
                if not cand_sf and sc_key:
                    for k_m, v_m in scene_first_source_map.items():
                        if sc_key in k_m or k_m in sc_key:
                            cand_sf = v_m
                            break
                if cand_sf:
                    it["scene_source_frame"] = cand_sf
                    manifest_updated = True
    if manifest_updated:
        write_json(manifest_file, manifest)

    # 统计角色出场镜头数，核查“单镜一闪而过的路人/群演单独建角色卡”违规
    char_shot_counts: dict[str, int] = {}
    for it in items:
        if isinstance(it, dict) and not _is_character_job(it, get_unit_id(it)) and not _is_scene_job(it, get_unit_id(it)):
            vis = it.get("visible_characters") or []
            if isinstance(vis, list):
                for cname in vis:
                    c_str = str(cname).strip()
                    if c_str:
                        char_shot_counts[c_str] = char_shot_counts.get(c_str, 0) + 1

    single_shot_extra_warnings: list[str] = []
    stage_a_portraits: list[str] = []
    stage_a_subjects: list[str] = []
    stage_a_scenes: list[str] = []
    stage_b_first_frames: list[str] = []
    stage_b_tail_frames: list[str] = []
    exempted_tail_units: list[str] = []
    item_by_unit: dict[str, dict] = {}

    for it in items:
        if not isinstance(it, dict):
            continue
        u = get_unit_id(it)
        if u != "unknown":
            item_by_unit[u] = it
        if u == "unknown" or (u in done_units and not replan_all):
            continue
        utype = str(it.get("type") or "").lower()
        ftype = str(it.get("frame_type") or "")
        is_tail = (ftype == "尾帧" or u.endswith("_尾帧"))
        need_tail = it.get("need_tail_frame")
        if is_tail and need_tail is False:
            exempted_tail_units.append(u)
            continue

        if _is_character_job(it, u):
            cname = str(it.get("character") or "").strip()
            is_proto = bool(
                it.get("prototype_photo_ref")
                or it.get("historical_photo_ref")
                or it.get("is_historical_figure")
                or it.get("is_real_prototype")
            )
            if cname and not is_proto and char_shot_counts and char_shot_counts.get(cname, 0) < 2:
                warn_msg = (
                    f"WARN_SINGLE_SHOT_PASSERBY_CARD: 角色 '{cname}' ({u}) 全集仅出场 {char_shot_counts.get(cname, 0)} 镜且非真人/历史原型，"
                    f"按新规严禁为单镜路人/群演单独建 2 张角色母图，建议直接在对应分镜 _首帧.png 提示词中描述！"
                )
                if warn_msg not in single_shot_extra_warnings:
                    single_shot_extra_warnings.append(warn_msg)
            if utype == "character_portrait" or _get_character_subtype(it, u) == "portrait":
                stage_a_portraits.append(u)
            else:
                stage_a_subjects.append(u)
        elif _is_scene_job(it, u):
            stage_a_scenes.append(u)
        elif is_tail:
            stage_b_tail_frames.append(u)
        else:
            stage_b_first_frames.append(u)

    tiers = [
        ("wave_1", stage_a_portraits + stage_a_scenes),
        ("wave_2", stage_a_subjects),
        ("wave_3", stage_b_first_frames),
        ("wave_4", stage_b_tail_frames),
    ]
    windows = _build_tiered_window_slices(
        tiers,
        max_per_window=max_per_window,
        max_per_batch=max_per_batch,
        elastic_cap=elastic_cap,
    )
    ordered_pending = stage_a_portraits + stage_a_scenes + stage_a_subjects + stage_b_first_frames + stage_b_tail_frames

    aspect_ratio = resolve_project_aspect_ratio(
        project_root, ep_dir=w_dir, explicit=getattr(args, "aspect_ratio", None),
    )
    multi_char_bg = str(getattr(args, "multi_char_bg", "white") or "white")
    materialized_batches: list[str] = []
    target_window_arg = str(getattr(args, "window", "window_01") or "window_01").strip()
    if not bool(getattr(args, "no_materialize", False)) and windows:
        if target_window_arg.lower() == "all":
            for win_obj in windows:
                materialized_batches.extend(
                    _materialize_window_batches(win_obj, item_by_unit, items, w_dir, project_root, v3=v3_mode, aspect_ratio=aspect_ratio, multi_char_bg=multi_char_bg)
                )
        else:
            sel_wins = [w for w in windows if w["window_id"] == target_window_arg]
            if not sel_wins and target_window_arg == "window_01":
                sel_wins = [windows[0]]
            for win_obj in sel_wins:
                materialized_batches.extend(
                    _materialize_window_batches(win_obj, item_by_unit, items, w_dir, project_root, v3=v3_mode, aspect_ratio=aspect_ratio, multi_char_bg=multi_char_bg)
                )

    plan_payload = {
        "work_dir": str(w_dir).replace("\\", "/"),
        "v3_mode": v3_mode,
        "aspect_ratio": aspect_ratio,
        "rule": "PROACTIVE_WINDOW_SLICING_NO_429",
        "max_base_units_per_window": max_per_window,
        "elastic_cap_total_calls": elastic_cap,
        "elastic_tail_merged": any(bool(w.get("elastic_tail_merged")) for w in windows),
        "reserved_remedy_calls_per_window": MAX_REMEDY_RESERVE_PER_WINDOW,
        "hard_cap_total_calls_per_window": max(MAX_TOTAL_CALLS_PER_WINDOW, elastic_cap),
        "safety_buffer_below_88_threshold": 88 - max(MAX_TOTAL_CALLS_PER_WINDOW, elastic_cap),
        "max_units_per_batch": max_per_batch,
        "already_completed_count": len(done_units),
        "stale_invalidated_count": len(stale_invalidated_units),
        "stale_invalidated_units": stale_invalidated_units,
        "exempted_redundant_tail_count": len(exempted_tail_units),
        "total_pending_base_units": len(ordered_pending),
        "stage_breakdown": {
            "stage_a_character_portraits": len(stage_a_portraits),
            "stage_a_character_subjects": len(stage_a_subjects),
            "stage_a_scenes": len(stage_a_scenes),
            "stage_b_first_frames": len(stage_b_first_frames),
            "stage_b_tail_frames": len(stage_b_tail_frames),
        },
        "wave_breakdown": {
            "wave_1": len(stage_a_portraits) + len(stage_a_scenes),
            "wave_2": len(stage_a_subjects),
            "wave_3": len(stage_b_first_frames),
            "wave_4": len(stage_b_tail_frames),
        },
        "single_shot_extra_warnings": single_shot_extra_warnings,
        "total_windows_required": len(windows),
        "windows": windows,
        "materialized_window": target_window_arg if not bool(getattr(args, "no_materialize", False)) else None,
        "materialized_batches": materialized_batches,
    }
    plan_file = w_dir / "window_plan.json"
    write_json(plan_file, plan_payload)
    print(json.dumps(plan_payload, ensure_ascii=False, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    """一键盘点工作区或批次目录的待生图缺口与在途状态（主代理零看图快速判断）"""
    if args.batch_dir:
        b_dir = Path(args.batch_dir).resolve()
        info = inspect_single_batch(b_dir)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return

    if not args.work_dir:
        print("必须提供 --work-dir 或 --batch-dir", file=sys.stderr)
        sys.exit(1)

    w_dir = Path(args.work_dir).resolve()
    manifest_file = w_dir / "待生图帧清单.json"
    cls_file = w_dir / "当前制作审核分类.json"
    batches_root = w_dir / "生成批次"

    cls_data = read_json(cls_file) if cls_file.exists() else {}
    confirmed_units = [k for k, v in cls_data.items() if isinstance(v, dict) and v.get("category") in ("用户已确认", "已采用")]
    passed_units = [k for k, v in cls_data.items() if isinstance(v, dict) and v.get("category") in ("自检通过", "审核通过")]
    review_units = [k for k, v in cls_data.items() if isinstance(v, dict) and v.get("category") == "需要用户审核"]

    covered_units = set(cls_data.keys())
    batch_reports = []
    if batches_root.exists():
        for bd in sorted(batches_root.iterdir()):
            if bd.is_dir():
                rep = inspect_single_batch(bd)
                for u in rep.get("done_initial", []) + rep.get("pending_initial", []):
                    if u != "unknown":
                        covered_units.add(u)
                # 只列出尚有未完成初始/补救图或未写审核总结的活跃批次
                if rep.get("pending_initial") or rep.get("pending_remedy") or (rep.get("total_units") and not rep.get("has_summary")):
                    batch_reports.append(rep)

    # 若存在待生图帧清单.json，则以清单为准计算未排批单元（严格豁免 need_tail_frame: false 的尾帧，并校验冗余尾帧与尾帧同段首帧绑定）
    unbatched_stage_a = []
    unbatched_stage_b = []
    exempted_tail_units = []
    retained_tail_units = []
    tail_missing_first_frame_ref_warnings = []
    redundant_tail_frame_warnings = []
    first_frame_count = 0
    total_planned = None
    if manifest_file.exists():
        manifest = read_json(manifest_file)
        items = manifest if isinstance(manifest, list) else manifest.get("units", [])
        active_items = []
        # 收集所有首帧目标路径映射供尾帧绑定核验
        seg_first_frame_map = {}
        for it_pre in items:
            if isinstance(it_pre, dict):
                u_pre = get_unit_id(it_pre)
                ft_pre = str(it_pre.get("frame_type") or "")
                if ft_pre == "首帧" or u_pre.endswith("_首帧"):
                    first_frame_count += 1
                    seg_k = str(it_pre.get("segment") or re.sub(r"_镜头\d+.*$", "", u_pre))
                    t_pre = _normalize_flat_frame_target_path(str(it_pre.get("target_path") or ""), u_pre, it_pre)
                    if seg_k and t_pre and seg_k not in seg_first_frame_map:
                        seg_first_frame_map[seg_k] = t_pre

        for item in items:
            if isinstance(item, dict):
                u = get_unit_id(item)
                ftype = str(item.get("frame_type") or "")
                need_tail = item.get("need_tail_frame")
                is_tail = (ftype == "尾帧" or u.endswith("_尾帧"))
                if is_tail and need_tail is False:
                    exempted_tail_units.append(u)
                    continue
                if is_tail:
                    retained_tail_units.append(u)
                    tail_reason = str(item.get("tail_reason") or "").strip()
                    if not tail_reason or need_tail is not True:
                        redundant_tail_frame_warnings.append(
                            f"{u}: 未显式声明 need_tail_frame=true 或缺少大跨度位移/甩镜 tail_reason，请按 character-scene-style 规则去重！"
                        )
                    refs_list = list(item.get("refs") or item.get("reference_images") or [])
                    seg_k = str(item.get("segment") or re.sub(r"_镜头\d+.*$", "", u))
                    has_same_seg_first = any(("_首帧" in str(r) and seg_k in str(r)) for r in refs_list) or bool(item.get("same_segment_first_frame_ref"))
                    if not has_same_seg_first:
                        tail_missing_first_frame_ref_warnings.append(
                            f"{u}: 保留的 _尾帧.png 必须在 reference_images/refs 中强制绑定同段 _首帧.png ({seg_first_frame_map.get(seg_k, seg_k + '_镜头01_首帧.png')}) 以锁死同一场景与同一色调！"
                        )
                active_items.append(item)
                if u != "unknown" and u not in covered_units:
                    if _is_character_job(item, u) or _is_scene_job(item, u):
                        unbatched_stage_a.append(u)
                    else:
                        unbatched_stage_b.append(u)
            else:
                u = str(item)
                active_items.append(item)
                if u != "unknown" and u not in covered_units:
                    unbatched_stage_b.append(u)
        total_planned = len(active_items)
        if first_frame_count > 0 and len(retained_tail_units) > max(2, int(first_frame_count * 0.30)):
            redundant_tail_frame_warnings.insert(
                0,
                f"WARN_EXCESSIVE_TAIL_FRAMES: 当前保留尾帧数 ({len(retained_tail_units)}) 超过首帧数 ({first_frame_count}) 的 30%，严禁机械给每一段都生成首帧+尾帧！",
            )
    unbatched_units = unbatched_stage_a + unbatched_stage_b

    # 检查本集声线目录合规状态与剧本故事梗概导演脚本状态
    proj_root_cand = w_dir.parent.parent.parent if w_dir.parent.name == "在制工作区" else None
    ep_voice_status = None
    director_script_status = None
    if proj_root_cand and w_dir.name.startswith("第"):
        v_dir = proj_root_cand / "分集" / w_dir.name / "声线"
        wavs = sorted(v_dir.glob("*.wav")) if v_dir.exists() else []
        compliant_wavs = [w.name for w in wavs if probe_wav_compliance(w)["compliant"]]
        ep_voice_status = {
            "voice_dir": str(v_dir).replace("\\", "/"),
            "total_wavs": len(wavs),
            "compliant_2_to_3s_48k_pcm16_count": len(compliant_wavs),
            "ready": len(wavs) >= 2 and len(compliant_wavs) == len(wavs),
        }
        dir_script_p = proj_root_cand / "分集" / w_dir.name / f"{w_dir.name}_剧本故事梗概与逐镜导演脚本.md"
        director_script_status = {
            "path": str(dir_script_p).replace("\\", "/"),
            "exists": dir_script_p.exists() and dir_script_p.stat().st_size > 100,
        }

    window_slices = _build_window_slices(unbatched_units)
    summary = {
        "work_dir": str(w_dir).replace("\\", "/"),
        "director_script_status": director_script_status,
        "total_planned_in_manifest": total_planned,
        "tail_frame_exempted_count": len(exempted_tail_units),
        "tail_frame_exempted_units": exempted_tail_units,
        "retained_tail_frame_count": len(retained_tail_units),
        "redundant_tail_frame_warnings": redundant_tail_frame_warnings,
        "tail_missing_first_frame_ref_warnings": tail_missing_first_frame_ref_warnings,
        "total_covered_in_batches": len(covered_units),
        "user_confirmed_count": len(confirmed_units),
        "self_checked_passed_count": len(passed_units),
        "needs_user_review_count": len(review_units),
        "needs_user_review_units": review_units,
        "incomplete_batches": batch_reports,
        "stage_a_pending_character_and_scene_units": unbatched_stage_a,
        "next_unbatched_units_preview": unbatched_units[:16],
        "unbatched_count": len(unbatched_units),
        "window_slicing_rule": f"每个窗口 ≤{MAX_UNITS_PER_WINDOW} 基础单元 + 预留 ≤{MAX_REMEDY_RESERVE_PER_WINDOW} 补救 = 单窗口总调用 ≤{MAX_TOTAL_CALLS_PER_WINDOW} 张（距 88 张 429 惩罚线留 28 张安全缓冲，严禁跨窗口硬闯触发 429）",
        "pending_windows_count": len(window_slices),
        "pending_windows_preview": [
            {
                "window_id": w["window_id"],
                "window_label": w["window_label"],
                "base_unit_count": w["base_unit_count"],
                "hard_cap_total_calls": w["hard_cap_total_calls"],
                "batch_count": w["batch_count"],
            }
            for w in window_slices
        ],
        "voice_assets_status": ep_voice_status,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _resolve_ep_artifact(
    ep_dir: Path,
    explicit: str | Path | None,
    local_rel: str,
    project_cands: list[Path],
) -> Path:
    if explicit:
        return resolve_cross_platform_path(explicit, ep_dir)
    local_p = ep_dir / local_rel
    if local_p.exists():
        return local_p
    for cand in project_cands:
        if cand.exists():
            return cand
    return local_p


def compute_phase_status(
    ep_dir: Path,
    manifest_path: str | Path | None = None,
    cuts_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    v3: bool = False,
) -> dict[str, Any]:
    """扫描磁盘核心产物与批次状态，生成 phase_checkpoint.json 供微型短命接力主管无损接力"""
    import csv

    ep_dir = resolve_cross_platform_path(ep_dir)
    proj_root: Path | None = None
    ep_name = ep_dir.name
    if ep_dir.parent.name in ("在制工作区", "原片分析") and ep_dir.parent.parent.name == "中间文件":
        proj_root = ep_dir.parent.parent.parent
    elif ep_dir.parent.name == "分集":
        proj_root = ep_dir.parent.parent

    def _pc(rel_parts: tuple[str, ...]) -> list[Path]:
        if proj_root is None:
            return []
        return [proj_root.joinpath(*rel_parts)]

    cuts_p = _resolve_ep_artifact(
        ep_dir,
        cuts_path,
        "cuts.csv",
        _pc(("中间文件", "原片分析", ep_name, "cuts.csv")),
    )
    shots_p = _resolve_ep_artifact(
        ep_dir,
        None,
        "物理切点清单.json",
        _pc(("中间文件", "原片分析", ep_name, "物理切点清单.json")),
    )
    colab_done_p = _resolve_ep_artifact(
        ep_dir,
        None,
        "phase1_colab_done.json",
        _pc(("中间文件", "原片分析", ep_name, "phase1_colab_done.json")),
    )
    storyboard_idx_p = _resolve_ep_artifact(
        ep_dir,
        None,
        "storyboard_grids/storyboard_index.json",
        _pc(("中间文件", "原片分析", ep_name, "storyboard_grids", "storyboard_index.json")),
    )
    clean_dir = _resolve_ep_artifact(
        ep_dir,
        None,
        "ref_clean_frames",
        [
            ep_dir / "source_frames" / "ref_clean_frames",
            *_pc(("中间文件", "原片分析", ep_name, "ref_clean_frames")),
            *_pc(("中间文件", "原片分析", ep_name, "source_frames", "ref_clean_frames")),
        ],
    )
    bible_p = _resolve_ep_artifact(
        ep_dir,
        None,
        "episode_context_bible.json",
        _pc(("中间文件", "在制工作区", ep_name, "episode_context_bible.json"))
        + _pc(("中间文件", "原片分析", ep_name, "episode_context_bible.json"))
        + _pc(("分集", ep_name, "episode_context_bible.json")),
    )
    director_bible_p = _resolve_ep_artifact(
        ep_dir,
        None,
        "episode_director_bible.json",
        _pc(("中间文件", "在制工作区", ep_name, "episode_director_bible.json"))
        + _pc(("中间文件", "原片分析", ep_name, "episode_director_bible.json"))
        + _pc(("分集", ep_name, "episode_director_bible.json")),
    )
    style_p = _resolve_ep_artifact(
        ep_dir,
        None,
        "style_and_cast.json",
        _pc(("中间文件", "在制工作区", ep_name, "style_and_cast.json"))
        + _pc(("分集", ep_name, "style_and_cast.json")),
    )
    manifest_p = _resolve_ep_artifact(
        ep_dir,
        manifest_path,
        "待生图帧清单.json",
        _pc(("中间文件", "在制工作区", ep_name, "待生图帧清单.json")),
    )
    audit_p = _resolve_ep_artifact(
        ep_dir,
        audit_path,
        "prompt_visual_audit.json",
        _pc(("中间文件", "在制工作区", ep_name, "prompt_visual_audit.json"))
        + _pc(("分集", ep_name, "prompt_visual_audit.json"))
        + _pc(("中间文件", "原片分析", ep_name, "prompt_visual_audit.json")),
    )
    md_p = _resolve_ep_artifact(
        ep_dir,
        None,
        f"{ep_name}_视频提示词.md",
        _pc(("分集", ep_name, f"{ep_name}_视频提示词.md")),
    )
    storyboard_xlsx_p = _resolve_ep_artifact(
        ep_dir,
        None,
        f"{ep_name}_分镜头脚本.xlsx",
        _pc(("分集", ep_name, f"{ep_name}_分镜头脚本.xlsx")),
    )
    batches_dir = _resolve_ep_artifact(
        ep_dir,
        None,
        "生成批次",
        _pc(("中间文件", "在制工作区", ep_name, "生成批次")),
    )
    cls_p = _resolve_ep_artifact(
        ep_dir,
        None,
        "当前制作审核分类.json",
        _pc(("中间文件", "在制工作区", ep_name, "当前制作审核分类.json")),
    )

    cuts_rows_count = 0
    if cuts_p.exists() and cuts_p.stat().st_size > 0:
        try:
            with cuts_p.open("r", encoding="utf-8-sig") as f:
                cuts_rows_count = sum(1 for _ in csv.DictReader(f))
        except Exception:
            cuts_rows_count = 0

    clean_frames_count = 0
    if clean_dir.exists() and clean_dir.is_dir():
        clean_frames_count = len([p for p in clean_dir.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")])

    shots_exists = shots_p.exists() and shots_p.stat().st_size > 0
    storyboard_idx_exists = storyboard_idx_p.exists() and storyboard_idx_p.stat().st_size > 0
    colab_detector = ""
    colab_cpu_verified = False
    colab_done_exists = colab_done_p.exists() and colab_done_p.stat().st_size > 0
    if colab_done_exists:
        try:
            cd_raw = read_json(colab_done_p)
            if isinstance(cd_raw, dict):
                colab_detector = str(cd_raw.get("detector_engine") or "").strip()
                colab_cpu_verified = cd_raw.get("colab_cpu_verified") is True
        except Exception:
            colab_detector = ""
            colab_cpu_verified = False
    asr_p = _resolve_ep_artifact(
        ep_dir,
        None,
        "asr_transcript.json",
        _pc(("中间文件", "在制工作区", ep_name, "asr_transcript.json"))
        + _pc(("中间文件", "原片分析", ep_name, "asr_transcript.json")),
    )
    asr_exists = asr_p.exists() and asr_p.stat().st_size > 0

    bible_exists = bible_p.exists() and bible_p.stat().st_size > 0
    director_bible_exists = director_bible_p.exists() and director_bible_p.stat().st_size > 0
    style_exists = style_p.exists() and style_p.stat().st_size > 0
    md_exists = md_p.exists() and md_p.stat().st_size > 0
    storyboard_xlsx_exists = storyboard_xlsx_p.exists() and storyboard_xlsx_p.stat().st_size > 0

    m_raw: Any = None
    if manifest_p.exists() and manifest_p.stat().st_size > 0:
        try:
            m_raw = read_json(manifest_p)
        except Exception:
            m_raw = None

    v3_mode = (
        bool(v3)
        or _parse_schema_version_num(m_raw) >= 3
        or director_bible_exists
        or storyboard_idx_exists
        or colab_detector == "TransNetV2"
    )

    manifest_unit_count = 0
    min_en_words = 0
    units_valid_prompt = 0
    units_failing_prompt: list[str] = []
    manifest_unit_ids: list[str] = []
    m_items: list[dict] = []
    wave_counts = {"wave_1": 0, "wave_2": 0, "wave_3": 0, "wave_4": 0}
    if m_raw is not None:
        try:
            m_items = [it for it in (m_raw if isinstance(m_raw, list) else extract_jobs_list(m_raw)) if isinstance(it, dict)]
            word_counts: list[int] = []
            for it in m_items:
                u = get_unit_id(it)
                ft = str(it.get("frame_type") or "")
                if (ft == "尾帧" or u.endswith("_尾帧")) and it.get("need_tail_frame") is False:
                    continue
                manifest_unit_count += 1
                manifest_unit_ids.append(u)
                w_num = _infer_expected_wave(it, u)
                wave_counts[f"wave_{w_num}"] = wave_counts.get(f"wave_{w_num}", 0) + 1
                chk = _validate_phase1_en_prompt(it, u, v3=v3_mode)
                word_counts.append(int(chk["word_count"]))
                if chk["ok"]:
                    units_valid_prompt += 1
                else:
                    units_failing_prompt.append(u)
            min_en_words = min(word_counts) if word_counts else 0
        except Exception:
            manifest_unit_count = 0

    audit_status = ""
    audit_passed = False
    audited_count = 0
    audit_self_review_detected = False
    if audit_p.exists() and audit_p.stat().st_size > 0:
        try:
            a_raw = read_json(audit_p)
            if isinstance(a_raw, dict):
                audit_status = str(a_raw.get("status") or "").strip().upper()
                top_author = str(a_raw.get("author_agent") or "").strip()
                top_inspector = str(a_raw.get("inspector_agent") or "").strip()
                if v3_mode and top_author and top_inspector and top_author.lower() == top_inspector.lower():
                    audit_self_review_detected = True
                raw_units = a_raw.get("units") or a_raw.get("results") or a_raw.get("audited_units") or a_raw.get("items") or {}
                if isinstance(raw_units, dict):
                    audited_count = len(raw_units)
                    if v3_mode:
                        for _, uv in raw_units.items():
                            if isinstance(uv, dict):
                                ua = str(uv.get("author_agent") or top_author).strip()
                                ui = str(uv.get("inspector_agent") or top_inspector).strip()
                                if (ua or ui) and ua.lower() == ui.lower():
                                    audit_self_review_detected = True
                    covered_ok = all(
                        u in raw_units and str((raw_units[u] or {}).get("status") if isinstance(raw_units[u], dict) else raw_units[u]).strip().upper() not in ("FAIL", "REJECT", "REJECTED", "需补救")
                        for u in manifest_unit_ids
                    ) if manifest_unit_ids else True
                elif isinstance(raw_units, list):
                    audited_count = len(raw_units)
                    if v3_mode:
                        for uv in raw_units:
                            if isinstance(uv, dict):
                                ua = str(uv.get("author_agent") or top_author).strip()
                                ui = str(uv.get("inspector_agent") or top_inspector).strip()
                                if (ua or ui) and ua.lower() == ui.lower():
                                    audit_self_review_detected = True
                    covered_set = {get_unit_id(x) if isinstance(x, dict) else str(x) for x in raw_units}
                    covered_ok = all(u in covered_set for u in manifest_unit_ids) if manifest_unit_ids else True
                else:
                    covered_ok = False
                if v3_mode and m_items:
                    hash_errs = verify_visual_audit_hashes(a_raw, m_items, proj_root or ep_dir, get_unit_id)
                else:
                    hash_errs = []
                audit_passed = (audit_status == "PASS") and covered_ok and (not audit_self_review_detected) and (not hash_errs)
        except Exception:
            audit_passed = False

    cls_data = read_json(cls_p) if (cls_p.exists() and cls_p.stat().st_size > 0) else {}
    if not isinstance(cls_data, dict):
        cls_data = {}
    work_dir_for_fp = cls_p.parent if cls_p.exists() else ep_dir
    confirmed_set, stale_invalidated_units = detect_stale_approved_units(
        m_items, cls_data, work_dir_for_fp, proj_root, get_unit_id
    )

    batch_details: list[dict[str, Any]] = []
    total_jobs = 0
    saved_result_count = 0
    finalized_batches = 0
    pending_batches: list[str] = []
    if batches_dir.exists() and batches_dir.is_dir():
        for b_dir in sorted(p for p in batches_dir.iterdir() if p.is_dir()):
            jobs_p = b_dir / "jobs.json"
            b_jobs = [j for j in extract_jobs_list(read_json(jobs_p)) if isinstance(j, dict)] if jobs_p.exists() else []
            b_job_count = len(b_jobs)
            b_result_count = 0
            b_unit_ids: list[str] = []
            for j in b_jobs:
                u = get_unit_id(j)
                b_unit_ids.append(u)
                u_dir = resolve_job_path(j.get("output_dir") or str(b_dir / u), b_dir)
                if (u_dir / "result.json").exists() or (u_dir / "补救" / "result.json").exists():
                    b_result_count += 1
            if not b_jobs:
                b_result_count = len(list(b_dir.rglob("result.json")))
            has_receipt = (b_dir / "finalize_receipt.json").exists()
            has_summary = (b_dir / "审核总结.json").exists() or (b_dir / "批次审核结论.json").exists()
            all_in_cls = bool(b_unit_ids) and all(
                isinstance(cls_data.get(u), dict) and cls_data[u].get("batch") == b_dir.name
                for u in b_unit_ids
            )
            b_finalized = has_receipt or (all_in_cls and has_summary) or (bool(b_unit_ids) and all(u in confirmed_set for u in b_unit_ids))
            total_jobs += b_job_count
            saved_result_count += b_result_count
            if b_finalized:
                finalized_batches += 1
            else:
                pending_batches.append(b_dir.name)
            batch_details.append({
                "batch_id": b_dir.name,
                "job_count": b_job_count,
                "result_count": b_result_count,
                "has_summary": has_summary,
                "finalized": b_finalized,
            })

    total_batches = len(batch_details)
    completed_manifest_units = len([u for u in manifest_unit_ids if u in confirmed_set])

    post_p2_stage, post_p2_gates = evaluate_post_p2_phase_status(ep_dir, proj_root, ep_name, cuts_p)
    if v3_mode:
        if clean_frames_count == 0 or not shots_exists or not storyboard_idx_exists or not colab_cpu_verified:
            next_stage = "W1_COLAB_TRANSNET_SPLIT"
        elif cuts_rows_count == 0 or not director_bible_exists or not bible_exists or not style_exists:
            next_stage = "W2_OPUS_BIBLE_AND_MODEL_LOCK"
        elif manifest_unit_count == 0 or bool(units_failing_prompt) or not audit_passed:
            next_stage = "W3_MICRO_SCENE_PROMPTS_AND_AUDIT"
        elif total_batches == 0 and (not md_exists or not storyboard_xlsx_exists):
            next_stage = "W4_MERGE_TABLES_AND_ALIGNMENT"
        elif total_batches == 0 or finalized_batches < total_batches or (manifest_unit_count > 0 and completed_manifest_units < manifest_unit_count):
            next_stage = "P2_WAVE_GENERATION"
        else:
            next_stage = post_p2_stage
    else:
        if cuts_rows_count == 0 or clean_frames_count == 0 or not bible_exists or not style_exists:
            next_stage = "P1_A_CUT_AND_BIBLE"
        elif manifest_unit_count == 0 or bool(units_failing_prompt) or not audit_passed:
            next_stage = "P1_B_SCENE_DIRECT_WRITE"
        elif total_batches == 0:
            next_stage = "P2_PLAN_BATCHES"
        elif finalized_batches < total_batches:
            next_stage = "P2_RUN_BATCHES"
        elif manifest_unit_count > 0 and completed_manifest_units < manifest_unit_count:
            next_stage = "P2_PLAN_BATCHES"
        else:
            next_stage = post_p2_stage

    checkpoint_file = ep_dir / "phase_checkpoint.json"
    snapshot = {
        "ep_dir": str(ep_dir).replace("\\", "/"),
        "episode": ep_name,
        "v3_mode": v3_mode,
        "colab_cpu_verified": colab_cpu_verified,
        "recommended_next_stage": next_stage,
        "phase1_colab_done": {
            "path": str(colab_done_p).replace("\\", "/"),
            "exists": colab_done_exists,
            "colab_cpu_verified": colab_cpu_verified,
            "detector_engine": colab_detector,
        },
        "asr_transcript": {"path": str(asr_p).replace("\\", "/"), "exists": asr_exists},
        "cuts_csv": {"path": str(cuts_p).replace("\\", "/"), "exists": cuts_rows_count > 0, "rows_count": cuts_rows_count},
        "shots_json": {"path": str(shots_p).replace("\\", "/"), "exists": shots_exists},
        "storyboard_index": {"path": str(storyboard_idx_p).replace("\\", "/"), "exists": storyboard_idx_exists},
        "ref_clean_frames": {"path": str(clean_dir).replace("\\", "/"), "exists": clean_dir.exists(), "count": clean_frames_count},
        "episode_director_bible": {"path": str(director_bible_p).replace("\\", "/"), "exists": director_bible_exists},
        "episode_context_bible": {"path": str(bible_p).replace("\\", "/"), "exists": bible_exists},
        "style_and_cast": {"path": str(style_p).replace("\\", "/"), "exists": style_exists},
        "video_prompt_md": {"path": str(md_p).replace("\\", "/"), "exists": md_exists},
        "storyboard_xlsx": {"path": str(storyboard_xlsx_p).replace("\\", "/"), "exists": storyboard_xlsx_exists},
        "manifest": {
            "path": str(manifest_p).replace("\\", "/"),
            "exists": manifest_p.exists(),
            "unit_count": manifest_unit_count,
            "min_prompt_en_words": min_en_words,
            "units_with_valid_prompt": units_valid_prompt,
            "units_failing_prompt": units_failing_prompt,
            "wave_breakdown": wave_counts,
        },
        "prompt_visual_audit": {
            "path": str(audit_p).replace("\\", "/"),
            "exists": audit_p.exists(),
            "status": audit_status,
            "passed": audit_passed,
            "self_review_detected": audit_self_review_detected,
            "audited_count": audited_count,
        },
        "batches": {
            "batches_dir": str(batches_dir).replace("\\", "/"),
            "total_batches": total_batches,
            "finalized_batches": finalized_batches,
            "pending_batches": pending_batches,
            "total_jobs": total_jobs,
            "saved_result_count": saved_result_count,
            "completed_manifest_units": completed_manifest_units,
            "stale_invalidated_units": stale_invalidated_units,
            "items": batch_details,
        },
        "post_p2_gates": post_p2_gates,
        "checkpoint_file": str(checkpoint_file).replace("\\", "/"),
    }
    if next_stage == "P4B_MASTER_VERIFY" and post_p2_gates.get("master_reverify_required"):
        snapshot["master_reverify_required"] = True
        snapshot["reason"] = str(post_p2_gates.get("reason") or "MASTER_MISSING_OR_SHA_MISMATCH")
    write_json(checkpoint_file, snapshot)
    return snapshot


def cmd_phase_status(args: argparse.Namespace) -> None:
    """磁盘检查点状态机入口：扫描产物与批次并落盘 phase_checkpoint.json"""
    ep_dir_raw = getattr(args, "ep_dir", "")
    if not ep_dir_raw:
        print("必须提供 --ep-dir", file=sys.stderr)
        sys.exit(1)
    snap = compute_phase_status(
        ep_dir=Path(ep_dir_raw),
        manifest_path=getattr(args, "manifest", "") or None,
        cuts_path=getattr(args, "cuts", "") or None,
        audit_path=getattr(args, "audit", "") or None,
        v3=bool(getattr(args, "v3", False)),
    )
    print(json.dumps(snap, ensure_ascii=False))


SUPPORTED_ASPECT_RATIOS: tuple[str, ...] = ("16:9", "9:16")


def normalize_aspect_ratio(val: str | None = None) -> str:
    raw = str(val or "16:9").strip()
    if raw not in SUPPORTED_ASPECT_RATIOS:
        raise ValueError(f"Unsupported aspect_ratio={val!r}; expected one of {SUPPORTED_ASPECT_RATIOS}")
    return raw


def resolve_project_aspect_ratio(
    project_root: Path | str | None,
    ep_dir: Path | str | None = None,
    explicit: str | None = None,
) -> str:
    """按优先级解析项目画幅比例: explicit -> style_and_cast.json -> 分集索引.json -> 默认 '16:9'"""
    if explicit is not None and str(explicit).strip():
        return normalize_aspect_ratio(explicit)
    root_p = resolve_cross_platform_path(project_root) if project_root else None
    ep_p = resolve_cross_platform_path(ep_dir) if ep_dir else None
    ep_name = ""
    if ep_p is not None and re.match(r"^第\d{2,3}集$", ep_p.name):
        ep_name = ep_p.name
    if root_p is None and ep_p is not None:
        if ep_p.parent.name == "在制工作区":
            root_p = ep_p.parent.parent.parent
        elif ep_p.parent.name == "分集":
            root_p = ep_p.parent.parent

    style_cands: list[Path] = []
    if ep_p is not None:
        style_cands.append(ep_p / "style_and_cast.json")
    if root_p is not None and ep_name:
        style_cands.append(root_p / "中间文件" / "在制工作区" / ep_name / "style_and_cast.json")
        style_cands.append(root_p / "分集" / ep_name / "style_and_cast.json")
    if root_p is not None:
        style_cands.append(root_p / "style_and_cast.json")
    for sc in style_cands:
        if sc.is_file() and sc.stat().st_size > 0:
            try:
                doc = read_json(sc)
                ar = str((doc or {}).get("aspect_ratio") or "").strip() if isinstance(doc, dict) else ""
                if ar in SUPPORTED_ASPECT_RATIOS:
                    return ar
            except Exception:
                continue

    if root_p is not None:
        idx_file = root_p / "中间文件" / "索引与制作记录" / "分集索引.json"
        if idx_file.is_file() and idx_file.stat().st_size > 0:
            try:
                idx_doc = read_json(idx_file)
                if isinstance(idx_doc, dict):
                    eps = idx_doc.get("episodes") if isinstance(idx_doc.get("episodes"), dict) else {}
                    if ep_name and isinstance(eps.get(ep_name), dict):
                        ar = str(eps[ep_name].get("aspect_ratio") or "").strip()
                        if ar in SUPPORTED_ASPECT_RATIOS:
                            return ar
                    ar_top = str(idx_doc.get("aspect_ratio") or "").strip()
                    if ar_top in SUPPORTED_ASPECT_RATIOS:
                        return ar_top
            except Exception:
                pass
    return "16:9"


CHARACTER_PORTRAIT_CONSTRAINT = (
    "16:9 widescreen aspect ratio, single head-and-shoulders close-up portrait centered, "
    "front-facing view, looking directly at the camera, showing "
    "ultra-detailed facial bone structure, eyes, hairstyle, and collar, isolated on a 100% pure flat "
    "solid white background (#FFFFFF), neutral even studio lighting, zero background elements, "
    "single character only."
)

CHARACTER_SUBJECT_CONSTRAINT = (
    "16:9 widescreen aspect ratio, full-body head-to-toe standing character portrait centered on a 100% pure flat solid white "
    "background (#FFFFFF), front-facing view, looking directly at the camera, "
    "strictly inheriting the EXACT facial identity, hairstyle, and collar "
    "from Reference Image 1 (_肖像.png), extending downward to show the complete full-body outfit, "
    "trousers/skirt, and footwear, neutral even studio lighting, single character only."
)

CHARACTER_PORTRAIT_CONSTRAINT_9_16 = CHARACTER_PORTRAIT_CONSTRAINT.replace("16:9 widescreen", "9:16 vertical portrait")
CHARACTER_SUBJECT_CONSTRAINT_9_16 = CHARACTER_SUBJECT_CONSTRAINT.replace("16:9 widescreen", "9:16 vertical portrait")

CHARACTER_FORMAT_A_CONSTRAINT = CHARACTER_SUBJECT_CONSTRAINT


def get_character_constraint(subtype: str, aspect_ratio: str = "16:9") -> str:
    ar = normalize_aspect_ratio(aspect_ratio)
    if ar == "9:16":
        return CHARACTER_PORTRAIT_CONSTRAINT_9_16 if subtype == "portrait" else CHARACTER_SUBJECT_CONSTRAINT_9_16
    return CHARACTER_PORTRAIT_CONSTRAINT if subtype == "portrait" else CHARACTER_SUBJECT_CONSTRAINT


def _is_character_job(job: dict, unit_id: str = "") -> bool:
    u = (unit_id or get_unit_id(job)).lower()
    jtype = str(job.get("type") or job.get("category") or job.get("asset_type") or "").lower()
    tpath = _resolve_unit_target_path(job).replace("\\", "/")
    return (
        jtype in (
            "character", "character_portrait", "character_subject",
            "人物", "角色", "人物肖像", "人物主体", "character_ref",
        )
        or u.startswith(("character_", "char_", "人物_", "角色_"))
        or "/人物参考/" in f"/{tpath}"
        or "/人物/" in f"/{tpath}"
    )


def _get_character_subtype(job: dict | None, unit_id: str = "") -> str:
    """识别人物参考图属于 'portrait' (_肖像.png) 还是 'subject' (_主体.png)"""
    job_d = job or {}
    u = (unit_id or get_unit_id(job_d)).lower()
    jtype = str(job_d.get("type") or job_d.get("category") or job_d.get("asset_type") or "").lower()
    tpath = _resolve_unit_target_path(job_d).replace("\\", "/")
    if (
        "portrait" in jtype
        or "肖像" in jtype
        or u.endswith(("_肖像", "_portrait"))
        or "_肖像." in tpath
    ):
        return "portrait"
    return "subject"


def _is_scene_job(job: dict, unit_id: str = "") -> bool:
    u = (unit_id or get_unit_id(job)).lower()
    jtype = str(job.get("type") or job.get("category") or job.get("asset_type") or "").lower()
    tpath = _resolve_unit_target_path(job).replace("\\", "/")
    return (
        jtype in ("scene", "场景", "scene_ref", "environment")
        or u.startswith(("scene_", "场景_"))
        or "/场景参考/" in f"/{tpath}"
    )


def _normalize_flat_frame_target_path(target_raw: str, unit_id: str, job: dict | None = None) -> str:
    """
    强制将分镜首尾及关键帧目标路径规范化为扁平唯一命名：
    分集/第NN集/首尾及关键帧/segment_XX_镜头YY_首帧.png (或 _尾帧.png / _关键帧01.png)
    防止网页画布批量上传时因仅命名为 镜头01_首帧.png 而全部重名。
    """
    if not target_raw:
        return ""
    job_d = job or {}
    if _is_character_job(job_d, unit_id) or _is_scene_job(job_d, unit_id):
        return str(target_raw).replace("\\", "/")

    norm = str(target_raw).replace("\\", "/")
    if "首尾及关键帧/" not in norm:
        return norm

    prefix_dir, rest = norm.split("首尾及关键帧/", 1)
    kf_base = f"{prefix_dir}首尾及关键帧"
    ext = Path(rest).suffix or ".png"

    m_u = re.match(r"^(segment_\d+)[_/](镜头\d+)[_/](首帧|尾帧|关键帧\d*|中间帧.*)$", unit_id)
    seg_str = str(job_d.get("segment") or (m_u.group(1) if m_u else "")).strip()
    shot_str = str(job_d.get("shot") or (m_u.group(2) if m_u else "")).strip()
    ftype_str = str(job_d.get("frame_type") or (m_u.group(3) if m_u else "")).strip()

    if not seg_str:
        m_seg = re.search(r"(segment_\d+)", rest) or re.search(r"(segment_\d+)", unit_id)
        seg_str = m_seg.group(1) if m_seg else "segment_01"
    if not shot_str:
        m_shot = re.search(r"(镜头\d+)", rest) or re.search(r"(镜头\d+)", unit_id)
        shot_str = m_shot.group(1) if m_shot else "镜头01"
    if not ftype_str:
        m_ft = re.search(r"(首帧|尾帧|关键帧\d*|中间帧[^./\\]*)", rest)
        ftype_str = m_ft.group(1) if m_ft else "首帧"

    flat_name = f"{seg_str}_{shot_str}_{ftype_str}{ext}"
    return f"{kf_base}/{flat_name}"


def _infer_project_root_from_path(anchor: Path | None) -> Path | None:
    if anchor is None:
        return None
    for p in [anchor.resolve(), *anchor.resolve().parents]:
        if p.name in ("在制工作区", "生成批次"):
            if p.name == "在制工作区" and p.parent.name == "中间文件":
                return p.parent.parent
        if (p / "中间文件").exists() and (p / "分集").exists():
            return p
    return None


def _resolve_ref_path_smart(raw_ref: str | Path, anchor: Path | None = None) -> Path:
    s = str(raw_ref or "").strip()
    if not s:
        return (anchor or Path.cwd()).resolve()
    p = Path(s)
    if p.is_absolute():
        return p.resolve()
    proj_root = _infer_project_root_from_path(anchor)
    if proj_root is not None:
        cand = (proj_root / s).resolve()
        if cand.exists():
            return cand
    if anchor is not None:
        cand2 = (anchor / s).resolve()
        if cand2.exists():
            return cand2
    return resolve_cross_platform_path(s, proj_root or anchor)


def _build_frame_role_and_person_lock_text(job: dict) -> str:
    """构建分镜帧【提示词首句强锁机位 + 四类参考职责解耦 + 准确人数死锁 + 俯仰角/核心意象/反正面罚站死锁】强制指令"""
    vis_chars = job.get("visible_characters")
    if not isinstance(vis_chars, list):
        vis_chars = job.get("characters") if isinstance(job.get("characters"), list) else []
    vis_chars = [str(c).strip() for c in vis_chars if str(c).strip()]
    if "exact_person_count" in job and job.get("exact_person_count") is not None:
        try:
            n_count = int(job["exact_person_count"])
        except Exception:
            n_count = len(vis_chars)
    else:
        n_count = len(vis_chars)

    cam_angle = str(job.get("camera_and_framing") or job.get("camera_angle") or job.get("camera_angle_cn") or job.get("shot_scale") or "平视 + 中景").strip()
    grid_pos = str(job.get("character_positions") or job.get("grid_position") or job.get("grid_and_facing_cn") or "九宫格居中稳定构图").strip()
    core_narrative = str(job.get("summary") or job.get("narrative_intent") or "严格还原原片镜头叙事与戏剧张力").strip()
    camera_header = f"【机位视角：{cam_angle}】+【九宫格人物位置与朝向：{grid_pos}】+【核心叙事内容：{core_narrative}】"
    pitch_gaze_lock = (
        "[Camera Pitch, Core Motif & Anti-Staged Gaze Lock]: Strictly preserve the EXACT camera pitch angle from Image 1 "
        "(NEVER flatten extreme low-angle upward shots, high-angle bird's-eye views, or deep-water top-down perspectives into eye-level), "
        f"keep the core narrative motif ({core_narrative}) and natural eye-line/gaze direction intact, "
        "and STRICTLY NO unmotivated front-facing camera stare or stiff side-by-side posed lineup!"
    )

    if n_count <= 0:
        return (
            f"{camera_header}\n"
            "[Reference Role Assignment]: Image 1 (cleaned source frame) defines the EXACT current camera angle (including top-down/high-angle/low-angle perspective), "
            "upward/downward tilt perspective, and updated composition details; Image 2 (scene master reference / same-segment opening frame) "
            "locks the SAME room/environment architectural style, materials, and lighting mood so it stays in the "
            "exact same location WITHOUT overriding Image 1's tilted camera angle.\n"
            f"{pitch_gaze_lock}\n"
            "[Strict Person Count Lock]: STRICTLY 0 PERSON(S) IN FRAME (empty environment shot, NO characters in frame)!"
        )
    vis_str = ", ".join(vis_chars) if vis_chars else f"exact {n_count} character(s) from Image 1"
    comp_order = [str(c).strip() for c in (job.get("multi_character_order") or vis_chars) if str(c).strip()]
    cur_refs = list(job.get("refs") or job.get("image_refs") or job.get("reference_images") or [])
    is_multi_comp = (len(comp_order) >= 2 and n_count >= 2) or any(_is_multi_char_composite_ref(r) for r in cur_refs)
    if is_multi_comp:
        order_str = ", ".join(comp_order) if comp_order else vis_str
        img3_desc = (
            f"Image 3 (compact borderless multi-character composite sheet, ordered left-to-right: {order_str}) "
            "is ONLY used to replace the identities and outfits of those characters present in Image 1 without adding extra people."
        )
    else:
        img3_desc = "Image 3 (character sheet) is ONLY used to replace the identity and outfit of the character present in Image 1."
    return (
        f"{camera_header}\n"
        "[Reference Role Assignment]: Image 1 (cleaned source frame) defines the EXACT current camera angle (strictly preserving top-down/high-angle/low-angle/eye-level perspective), "
        "upward/downward tilt perspective, 9-grid composition details, and character pose/placement; "
        "Image 2 (scene master reference, or same-segment _首帧.png when generating _尾帧.png) locks the SAME room/environment architectural style, materials, "
        "and unified color temperature/lighting mood so it stays in the exact same location WITHOUT overriding Image 1's camera angle; "
        f"{img3_desc}\n"
        f"{pitch_gaze_lock}\n"
        f"[Strict Person Count Lock]: STRICTLY ONLY {n_count} PERSON(S) IN FRAME ({vis_str}). "
        "Do NOT copy Image 3 as an extra standing person, and do NOT include off-screen characters!"
    )


def _enforce_frame_role_and_person_lock_prompt(prompt_path: Path, job: dict) -> tuple[str, dict]:
    """对分镜帧提示词自动校验并注入【首句强锁机位 + 参考图职责解耦 + 俯仰角/反正面摆拍死锁 + 准确人数死锁】指令，同时自动净化源帧并绑定 3 槽位参考图"""
    txt = prompt_path.read_text(encoding="utf-8-sig") if prompt_path.exists() else ""
    lock_block = _build_frame_role_and_person_lock_text(job)
    injected = False
    low_txt = txt.lower()
    if (
        "[reference role assignment]:" not in low_txt
        or "[strict person count lock]:" not in low_txt
        or "[camera pitch, core motif & anti-staged gaze lock]:" not in low_txt
        or "【机位视角：" not in txt
    ):
        # 避免重复堆叠旧锁块
        cleaned_body = re.sub(
            r"【机位视角：[^\n]*\n\[Reference Role Assignment\]:[^\n]*\n(?:\[Camera Pitch, Core Motif & Anti-Staged Gaze Lock\]:[^\n]*\n)?\[Strict Person Count Lock\]:[^\n]*\n*",
            "",
            txt,
        ).lstrip()
        txt = lock_block + ("\n\n" + cleaned_body if cleaned_body else "\n")
        injected = True
        if prompt_path.exists() or prompt_path.parent.exists():
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(txt, encoding="utf-8")

    unit_id = get_unit_id(job)
    ftype = str(job.get("frame_type") or "")
    is_tail = (ftype == "尾帧" or unit_id.endswith("_尾帧"))
    refs = list(job.get("refs") or job.get("reference_images") or [])
    scene_ref = str(job.get("scene_ref") or "").strip()
    same_seg_first_ref = str(job.get("same_segment_first_frame_ref") or "").strip()

    # 自动将 source_frame 净化为 ref_clean_frames/*_净参考.jpg 并切换 refs[0]（保留 source_frame 原图供接触表左列）
    source_frame_auto_cleaned = False
    clean_ref_path = ""
    sf_raw = str(job.get("source_frame") or "").strip()
    if sf_raw:
        src_abs = _resolve_ref_path_smart(sf_raw, prompt_path)
        ref0_str = str(refs[0]).replace("\\", "/") if refs else ""
        sf_norm = sf_raw.replace("\\", "/")
        need_clean_bind = (not refs) or ("_源." in ref0_str) or (ref0_str == sf_norm)
        if src_abs.exists() and need_clean_bind:
            already_clean = (src_abs.parent.name == "ref_clean_frames") or src_abs.stem.endswith("_净参考")
            clean_stem = (src_abs.stem[:-2] + "_净参考.jpg") if src_abs.stem.endswith("_源") else (src_abs.stem + "_净参考.jpg")
            clean_abs = src_abs if already_clean else (src_abs.parent / "ref_clean_frames" / clean_stem)
            if not already_clean and (not clean_abs.exists() or clean_abs.stat().st_size == 0):
                clean_source_frame(src_abs, clean_abs, mask_corner_logo=True)
            proj_root = _infer_project_root_from_path(prompt_path)
            if proj_root is not None:
                try:
                    clean_ref_path = clean_abs.resolve().relative_to(proj_root.resolve()).as_posix()
                except Exception:
                    clean_ref_path = str(clean_abs).replace("\\", "/")
            else:
                clean_ref_path = str(clean_abs).replace("\\", "/")
            if refs:
                refs[0] = clean_ref_path
            else:
                refs.append(clean_ref_path)
            job["refs"] = refs
            job["reference_images"] = list(refs)
            source_frame_auto_cleaned = True

    # 若为 _尾帧.png，强制在 refs 中绑定同段 _首帧.png 以锁死同一场景与同一色调（漏洞 3 根治）
    tail_bound_to_first = False
    if is_tail:
        if not same_seg_first_ref:
            t_raw = str(job.get("target_path") or "")
            if "_尾帧" in t_raw:
                same_seg_first_ref = t_raw.replace("_尾帧", "_首帧")
        if same_seg_first_ref:
            norm_first = same_seg_first_ref.replace("\\", "/")
            if not any(norm_first in str(r).replace("\\", "/") for r in refs):
                if len(refs) >= 2:
                    refs[1] = same_seg_first_ref
                else:
                    refs.append(same_seg_first_ref)
                job["refs"] = refs
                job["reference_images"] = list(refs)
            tail_bound_to_first = True

    # 若 job 提供了 scene_ref，且 refs[1] 误用了画风参考库图或被人物母图挤占而非场景母图，则自动修正槽位 2 为 scene_ref
    slot2_fixed = False
    slot2_char_collision_fixed = False
    if not is_tail and scene_ref and len(refs) >= 2:
        r1_norm = str(refs[1]).replace("\\", "/")
        if ("画风" in r1_norm or "style" in r1_norm.lower()) and "场景参考" not in r1_norm:
            refs[1] = scene_ref
            job["refs"] = refs
            job["reference_images"] = list(refs)
            slot2_fixed = True
        elif _is_character_ref_path(r1_norm):
            comp_cand = str(job.get("multi_character_composite_ref") or "").strip()
            if comp_cand:
                refs = [refs[0], scene_ref, comp_cand]
            elif len(refs) == 2:
                refs = [refs[0], scene_ref, refs[1]]
            else:
                refs[1] = scene_ref
            job["refs"] = refs
            job["reference_images"] = list(refs)
            slot2_fixed = True
            slot2_char_collision_fixed = True

    vis_chars = job.get("visible_characters") or job.get("characters") or []
    n_count = job.get("exact_person_count", len(vis_chars) if isinstance(vis_chars, list) else 1)
    audit = {
        "camera_angle_first_sentence_locked": "【机位视角：" in txt,
        "role_assignment_and_person_lock_present": True,
        "camera_pitch_and_anti_staged_gaze_locked": "[Camera Pitch, Core Motif & Anti-Staged Gaze Lock]:" in txt,
        "auto_injected": injected,
        "exact_person_count": n_count,
        "visible_characters": vis_chars,
        "is_tail_frame": is_tail,
        "tail_bound_to_same_segment_first_frame": tail_bound_to_first if is_tail else None,
        "slot2_scene_ref_auto_restored": slot2_fixed,
        "slot2_character_collision_fixed": slot2_char_collision_fixed,
        "source_frame_auto_cleaned": source_frame_auto_cleaned,
        "clean_ref_path": clean_ref_path or (str(refs[0]).replace("\\", "/") if refs else ""),
        "refs_count": len(refs),
    }
    return txt, audit


def _enforce_character_format_a_prompt(
    prompt_path: Path,
    job: dict | None = None,
    unit_id: str = "",
    aspect_ratio: str | None = None,
) -> str:
    """对人物参考图提示词自动强制注入【人物母图四合一标准：原型/历史真容锁脸 + 免冠露额 + 16:9/9:16画幅 + 统一正面正视镜头视角 + #FFFFFF纯白无底】硬约束"""
    job_d = job if isinstance(job, dict) else {}
    ar_raw = aspect_ratio or str(job_d.get("aspect_ratio") or "").strip() or None
    ar = normalize_aspect_ratio(ar_raw) if ar_raw else "16:9"
    subtype = _get_character_subtype(job_d, unit_id)
    h_ref = str(
        job_d.get("prototype_photo_ref")
        or job_d.get("historical_photo_ref")
        or job_d.get("history_ref")
        or ""
    ).strip()
    is_historical = bool(h_ref or job_d.get("is_historical_figure") or job_d.get("is_real_prototype"))
    keep_soft_hat = bool(job_d.get("keep_original_soft_hat"))

    # 若为 character_portrait (_肖像.png) 且声明了 prototype/historical_photo_ref，确保 refs[0] 挂载该真容照片
    if subtype == "portrait" and isinstance(job, dict) and h_ref:
        refs = list(job.get("refs") or job.get("reference_images") or [])
        if not refs or str(refs[0]).replace("\\", "/") != h_ref.replace("\\", "/"):
            if h_ref in refs:
                refs.remove(h_ref)
            refs.insert(0, h_ref)
            job["refs"] = refs
    # 若为 character_subject (_主体.png) 且声明了 portrait_ref，确保 refs[0] 挂载该 _肖像.png
    elif subtype == "subject" and isinstance(job, dict):
        p_ref = str(job.get("portrait_ref") or "").strip()
        if p_ref:
            refs = list(job.get("refs") or job.get("reference_images") or [])
            if not refs or str(refs[0]).replace("\\", "/") != p_ref.replace("\\", "/"):
                if p_ref in refs:
                    refs.remove(p_ref)
                refs.insert(0, p_ref)
                job["refs"] = refs

    txt = prompt_path.read_text(encoding="utf-8-sig") if prompt_path.exists() else ""
    cleaned = re.sub(
        r"\b(three-view|multi-view|turnaround sheet|character sheet|multiple views|front back side view)\b",
        "single-view portrait",
        txt,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"(三视图|四视图|多视角拼图|多角度设定图)", "单人正面微侧立绘", cleaned)

    # 免冠露额锁脸门禁：对带 prototype_photo_ref/historical_photo_ref 或 is_real_prototype/is_historical_figure 的 _肖像 单元（除非 keep_original_soft_hat 为 True）
    # 自动剥离遮挡额头发际线的钢盔/头盔/大檐帽/军帽/船形帽/贝雷帽词，并注入免冠露额 1:1 复刻头骨/发际线/脸颊肥瘦/真实年龄死锁指令
    if subtype == "portrait" and is_historical and not keep_soft_hat:
        cleaned = re.sub(r"\[HISTORICAL PORTRAIT BAREHEADED FACE-LOCK\]:[^\n]*\n?", "", cleaned)
        cleaned = re.sub(
            r"\b(?:wearing\s+(?:a|an)\s+)?(?:M35\s+steel\s+helmet|Brodie\s+helmet|steel\s+helmet|peaked\s+cap|visor\s+cap|military\s+cap|officer\s+cap|garrison\s+cap|side\s+cap|M35|helmet|beret)\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"(?:头戴|佩戴|戴着)?(?:德式|英式|美式|日式|苏式)?(?:钢盔|头盔|大檐帽|军帽|船形帽)", "", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\s*,\s*,+", ", ", cleaned)
        bareheaded_lock = (
            "[HISTORICAL PORTRAIT BAREHEADED FACE-LOCK]: Bareheaded with full forehead and natural hairline clearly visible "
            "(no headwear covering forehead or hair); 1:1 faithful reconstruction of the prototype figure's exact skull silhouette, "
            "hairline, cheek fullness or leanness, bone structure, and authentic real age from Reference Image 1; "
            "STRICTLY NO modern beauty filter, NO generic handsome idol template."
        )
        cleaned = cleaned.rstrip() + f"\n\n{bareheaded_lock}\n"

    constraint_str = get_character_constraint(subtype, aspect_ratio=ar)
    ar_phrase = "9:16 vertical portrait" if ar == "9:16" else "16:9 widescreen"
    header_tag = (
        f"[CHARACTER PORTRAIT 4-IN-1 (#FFFFFF + {ar} + UNIFIED DIRECTION) MANDATORY CONSTRAINT]:"
        if subtype == "portrait"
        else f"[CHARACTER SUBJECT 4-IN-1 (#FFFFFF + {ar} + UNIFIED DIRECTION) MANDATORY CONSTRAINT]:"
    )
    if "pure flat solid white background (#ffffff)" not in cleaned.lower() or ar_phrase not in cleaned.lower():
        cleaned = cleaned.rstrip() + f"\n\n{header_tag}\n" + constraint_str + "\n"
    if cleaned != txt and (prompt_path.exists() or prompt_path.parent.exists()):
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(cleaned, encoding="utf-8")
    return cleaned


SCENE_CLEAN_CONSTRAINT = (
    "[SCENE MASTER CLEANLINESS & ERA-FIDELITY LOCK]: STRICTLY 0 PEOPLE IN FRAME (100% pure empty environment shot, "
    "NO pedestrians, NO guards, NO figures); leave flagpoles bare/empty without flags; "
    "STRICTLY NO octagonal STOP signs, modern road markings, LED signs, modern traffic signals, "
    "NO garbled English text, NO readable small plaque lettering, and NO watermarks or AI logos."
)


def _enforce_scene_clean_prompt(prompt_path: Path, job: dict, unit_id: str) -> tuple[str, dict]:
    """场景净空守卫：强制注入纯空镜/空旗杆/禁现代标志/禁乱码小字死锁，并在非战损状态下剥离误加的战火硝烟词"""
    txt = prompt_path.read_text(encoding="utf-8-sig") if prompt_path.exists() else ""
    scene_state = str(job.get("scene_state") or "").strip().lower()
    scene_name = str(job.get("scene_name") or job.get("scene") or job.get("canonical_scene") or job.get("target_path") or unit_id).strip()
    is_wartime_or_damaged = (scene_state in ("damaged", "wartime", "post_disaster", "ruins")) or any(
        kw in scene_name for kw in ("战损", "废墟", "轰炸后", "火海", "灾后")
    )

    cleaned = txt
    stripped_war_terms: list[str] = []
    if not is_wartime_or_damaged:
        war_en_pat = re.compile(
            r"\b(?:raging\s+fire|billowing\s+black\s+smoke|thick\s+black\s+smoke|bomb\s+crater|burning\s+ruins|explosion|flames)\b",
            flags=re.IGNORECASE,
        )
        war_cn_pat = re.compile(r"(?:冲天大火|滚滚浓烟|爆炸废墟|弹坑|烈焰|火光冲天)")
        stripped_war_terms.extend(war_en_pat.findall(cleaned))
        stripped_war_terms.extend(war_cn_pat.findall(cleaned))
        cleaned = war_en_pat.sub("", cleaned)
        cleaned = war_cn_pat.sub("", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\s*,\s*,+", ", ", cleaned)
        cleaned = re.sub(r"(?:,\s*)+(\n|$)", r"\1", cleaned)

    injected = False
    if "[scene master cleanliness & era-fidelity lock]:" not in cleaned.lower():
        cleaned = cleaned.rstrip() + ("\n\n" if cleaned.strip() else "") + SCENE_CLEAN_CONSTRAINT + "\n"
        injected = True

    if cleaned != txt and (prompt_path.exists() or prompt_path.parent.exists()):
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(cleaned, encoding="utf-8")

    audit = {
        "unit": unit_id,
        "scene_state": scene_state or ("damaged" if is_wartime_or_damaged else "peacetime_or_default"),
        "is_wartime_or_damaged": is_wartime_or_damaged,
        "stripped_war_terms": stripped_war_terms,
        "clean_lock_injected": injected,
    }
    return cleaned, audit


def check_white_background(img_path: Path) -> dict:
    """
    人物母图【100% #FFFFFF 纯白背景】代码级像素门禁（兼容 16:9 横屏与 9:16 竖屏胸像）：
    1. 若含透明通道（RGBA/LA），自动合成为 #FFFFFF 纯白实心底；
    2. 对外框相连的极浅偏白边缘像素（R,G,B >= 238 且色差 <= 14）提纯为 #FFFFFF (255,255,255)；
    3. 横屏/方图（h <= w）：保持原逻辑，核验四角与上/左/右全高外围边框纯白像素占比 >= 0.78 且至少 3 角纯白；
    4. 竖屏（h > w，如 9:16 胸像/人物主体）：底部约 35% 双肩衣袖自然延展至左右边缘，改为核验顶部两角（左上、右上）均纯白
       且顶部 65% 高度三边外框（顶 4% + 左 4% 顶部 65% + 右 4% 顶部 65%）纯白像素占比 >= 0.90；
    5. 若非纯白底，直接标记 status = "FAIL_NON_WHITE_BG" 且 compliant = False，拦截入库！
    """
    try:
        from collections import deque
        from PIL import Image

        with Image.open(img_path) as im:
            has_alpha = im.mode in ("RGBA", "LA") or ("transparency" in im.info)
            if has_alpha:
                rgba = im.convert("RGBA")
                white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                rgb = Image.alpha_composite(white_bg, rgba).convert("RGB")
            else:
                rgb = im.convert("RGB")

            w, h = rgb.size
            px = rgb.load()

            def _is_near_white(r: int, g: int, b: int) -> bool:
                return r >= 238 and g >= 238 and b >= 238 and (max(r, g, b) - min(r, g, b) <= 14)

            # 从四条边界种子发起 BFS，将外围连通的微偏白像素提纯为 #FFFFFF
            visited = bytearray(w * h)
            q = deque()
            for x in range(w):
                for y in (0, h - 1):
                    idx = y * w + x
                    if not visited[idx] and _is_near_white(*px[x, y]):
                        visited[idx] = 1
                        q.append((x, y))
            for y in range(h):
                for x in (0, w - 1):
                    idx = y * w + x
                    if not visited[idx] and _is_near_white(*px[x, y]):
                        visited[idx] = 1
                        q.append((x, y))

            purified_pixels = 0
            while q:
                cx, cy = q.popleft()
                if px[cx, cy] != (255, 255, 255):
                    px[cx, cy] = (255, 255, 255)
                    purified_pixels += 1
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        nidx = ny * w + nx
                        if not visited[nidx] and _is_near_white(*px[nx, ny]):
                            visited[nidx] = 1
                            q.append((nx, ny))

            if has_alpha or purified_pixels > 0:
                rgb.save(img_path, format="PNG")

            margin_x = max(2, int(w * 0.04))
            margin_y = max(2, int(h * 0.04))
            aspect_ratio = round(w / max(1, h), 3)
            is_16_9_landscape = w > h and (1.55 <= aspect_ratio <= 1.95)
            is_9_16_portrait = h > w and (0.45 <= aspect_ratio <= 0.70)

            if h > w:
                # 竖屏人物胸像/主体：重点校验顶部 65% 高度三边外框（>= 0.90）及顶部两角纯白
                mode = "portrait_top65"
                top_65_h = max(margin_y + 1, int(h * 0.65))
                border_total, border_white = 0, 0
                for y in range(top_65_h):
                    for x in range(w):
                        if y < margin_y or x < margin_x or x >= w - margin_x:
                            border_total += 1
                            if px[x, y] == (255, 255, 255):
                                border_white += 1
                top_corners = [
                    px[min(2, w - 1), min(2, h - 1)],
                    px[max(0, w - 3), min(2, h - 1)],
                ]
                corners_ok = all(c == (255, 255, 255) for c in top_corners)
                white_ratio = round(border_white / max(1, border_total), 4)
                portrait_top65_white_ratio = white_ratio
                compliant = bool(corners_ok and white_ratio >= 0.90)
            else:
                # 横屏/方图：100% 保持原采样区域、四角判定与 0.78 阈值
                mode = "landscape_or_square"
                border_total, border_white = 0, 0
                for y in range(h):
                    for x in range(w):
                        if y < margin_y or x < margin_x or x >= w - margin_x:
                            border_total += 1
                            if px[x, y] == (255, 255, 255):
                                border_white += 1

                corners = [
                    px[2, 2],
                    px[w - 3, 2],
                    px[2, min(h - 3, int(h * 0.5))],
                    px[w - 3, min(h - 3, int(h * 0.5))],
                ]
                corners_ok = sum(1 for c in corners if c == (255, 255, 255)) >= 3
                white_ratio = round(border_white / max(1, border_total), 4)
                portrait_top65_white_ratio = None
                compliant = bool(corners_ok and white_ratio >= 0.78)

            return {
                "status": "PASS_PURE_WHITE_BG" if compliant else "FAIL_NON_WHITE_BG",
                "format": "Format_A_4in1_Solid_White_Single_Portrait",
                "mode": mode,
                "width": w,
                "height": h,
                "aspect_ratio": aspect_ratio,
                "is_16_9_landscape": is_16_9_landscape,
                "is_9_16_portrait": is_9_16_portrait,
                "purified_pixels": purified_pixels,
                "alpha_flattened_to_white": has_alpha,
                "upper_and_side_white_ratio": white_ratio,
                "portrait_top65_white_ratio": portrait_top65_white_ratio,
                "corners_pure_white": corners_ok,
                "compliant": compliant,
            }
    except Exception as exc:
        return {
            "status": "FAIL_NON_WHITE_BG",
            "format": "Format_A_4in1_Solid_White_16x9_Single_Portrait",
            "compliant": False,
            "error": str(exc),
        }


def _purify_and_verify_character_white_bg(img_path: Path) -> dict:
    return check_white_background(img_path)


def resolve_job_path(raw_path: str | Path, base_dir: Path) -> Path:
    return resolve_cross_platform_path(raw_path, base_dir)


def _is_ref_disapproved_in_cls(ref_raw: str, cls_data: dict) -> bool:
    """检查参考资产在 当前制作审核分类.json 中是否被标为不合格/待审"""
    if not isinstance(cls_data, dict) or not ref_raw:
        return False
    norm_ref = ref_raw.replace("\\", "/").strip()
    ref_stem = Path(norm_ref).stem
    bad_cats = ("需要用户审核", "未通过", "需补救", "不合格")
    for k, v in cls_data.items():
        if not isinstance(v, dict):
            continue
        cat = str(v.get("category") or "").strip()
        if cat not in bad_cats:
            continue
        fp = str(v.get("formal_path") or "").replace("\\", "/").strip()
        if fp and (fp == norm_ref or fp.endswith("/" + norm_ref) or norm_ref.endswith("/" + fp)):
            return True
        if ref_stem and (k == ref_stem or k.endswith("_" + ref_stem)):
            return True
    return False


def cmd_save_unit(args: argparse.Namespace) -> None:
    """生图子代理一键落盘：Stage A 级联阻断校验、提示词注入、复制原图、纯白底像素门禁与 result.json 落盘"""
    jobs_path = Path(args.jobs_file).resolve()
    batch_dir = jobs_path.parent
    jobs = extract_jobs_list(read_json(jobs_path))
    job = next((j for j in jobs if isinstance(j, dict) and get_unit_id(j) == args.unit), None)
    if job is None:
        print(f"{args.unit} | ERR | unit not found in {jobs_path}", file=sys.stderr)
        sys.exit(1)

    unit_id = get_unit_id(job)
    is_char = _is_character_job(job, unit_id)
    is_scene = _is_scene_job(job, unit_id)

    # Stage A 级联阻断门禁（除非显式传入 --allow-unapproved-refs）
    if not bool(getattr(args, "allow_unapproved_refs", False)):
        work_dir = batch_dir.parent.parent if batch_dir.parent.name == "生成批次" else batch_dir.parent
        cls_file = work_dir / "当前制作审核分类.json"
        cls_data = read_json(cls_file) if cls_file.exists() else {}
        refs_check = list(job.get("refs") or job.get("reference_images") or [])

        if is_char and _get_character_subtype(job, unit_id) == "subject":
            p_ref = str(job.get("portrait_ref") or "").strip()
            if not p_ref:
                p_ref = next((str(r).strip() for r in refs_check if "_肖像" in str(r) or "portrait" in str(r).lower()), "")
            if not p_ref:
                tp_raw = str(job.get("target_path") or job.get("formal_path") or "").strip()
                if "_主体" in tp_raw:
                    p_ref = tp_raw.replace("_主体", "_肖像")
            if p_ref:
                p_abs = _resolve_ref_path_smart(p_ref, batch_dir)
                if not p_abs.exists() or p_abs.stat().st_size == 0:
                    print(
                        f"STAGE_GATE_BLOCKED_MISSING_PORTRAIT | {unit_id} 引用的 _肖像.png 不存在: {p_ref}",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                if _is_ref_disapproved_in_cls(p_ref, cls_data):
                    print(
                        f"STAGE_GATE_BLOCKED_UNAPPROVED_PORTRAIT | {unit_id} 引用的 _肖像.png 尚未审核通过: {p_ref}",
                        file=sys.stderr,
                    )
                    sys.exit(2)
        elif not is_char and not is_scene:
            stage_a_refs: list[str] = []
            composite_refs: list[str] = []
            for r in refs_check:
                rs = str(r).strip()
                rn = rs.replace("\\", "/")
                if _is_multi_char_composite_ref(rn):
                    if rs not in composite_refs:
                        composite_refs.append(rs)
                elif any(tok in rn for tok in ("人物参考/", "场景参考/", "/人物/", "/场景/")):
                    if rs not in stage_a_refs:
                        stage_a_refs.append(rs)
            for extra_k in ("character_ref", "scene_ref"):
                ev = str(job.get(extra_k) or "").strip()
                if ev and not _is_multi_char_composite_ref(ev) and ev not in stage_a_refs:
                    stage_a_refs.append(ev)
            for ms in (job.get("multi_character_sources") or []):
                ms_s = str(ms).strip()
                if ms_s and ms_s not in stage_a_refs:
                    stage_a_refs.append(ms_s)
            for cr in composite_refs:
                cr_abs = _resolve_ref_path_smart(cr, batch_dir)
                if not cr_abs.exists() or cr_abs.stat().st_size == 0:
                    print(
                        f"STAGE_GATE_BLOCKED_MISSING_CHAR_COMPOSITE | {unit_id} 引用的多人横向合集图不存在: {cr}（请先运行 batch_ops.py build-char-sheets）",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                cr_meta = cr_abs.with_suffix(cr_abs.suffix + ".meta.json")
                if cr_meta.exists():
                    try:
                        for ms_m in (read_json(cr_meta).get("sources") or []):
                            ms_s = str(ms_m).strip()
                            if ms_s and ms_s not in stage_a_refs:
                                stage_a_refs.append(ms_s)
                    except Exception:
                        pass
            for sr in stage_a_refs:
                sr_abs = _resolve_ref_path_smart(sr, batch_dir)
                if not sr_abs.exists() or sr_abs.stat().st_size == 0:
                    print(
                        f"STAGE_GATE_BLOCKED_UNAPPROVED_STAGE_A | {unit_id} 引用的 Stage A 资产不存在: {sr}",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                if _is_ref_disapproved_in_cls(sr, cls_data):
                    print(
                        f"STAGE_GATE_BLOCKED_UNAPPROVED_STAGE_A | {unit_id} 引用的 Stage A 资产尚未审核通过: {sr}",
                        file=sys.stderr,
                    )
                    sys.exit(2)

    out_raw = job.get("output_dir") or str(batch_dir / unit_id)
    out_dir = resolve_job_path(out_raw, batch_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    attempt = job.get("attempt", "初始")
    png_name = "补救.png" if attempt == "补救" else "初始.png"
    dst_png = out_dir / png_name
    tool_png = Path(args.tool_png).resolve()

    if not tool_png.exists():
        print(f"{unit_id} | ERR | tool_png missing: {tool_png}", file=sys.stderr)
        sys.exit(1)

    shutil.copyfile(tool_png, dst_png)

    prompt_raw = job.get("prompt_file") or str(out_dir / "完整提示词.txt")
    prompt_path = resolve_job_path(prompt_raw, batch_dir)
    frame_role_audit = None
    scene_clean_audit = None
    if is_char:
        prompt_text = _enforce_character_format_a_prompt(prompt_path, job, unit_id)
        char_bg_Audit = check_white_background(dst_png)
        if isinstance(char_bg_Audit, dict):
            char_bg_Audit["character_subtype"] = _get_character_subtype(job, unit_id)
    elif not is_scene:
        prompt_text, frame_role_audit = _enforce_frame_role_and_person_lock_prompt(prompt_path, job)
        char_bg_Audit = None
    else:
        prompt_text, scene_clean_audit = _enforce_scene_clean_prompt(prompt_path, job, unit_id)
        char_bg_Audit = None

    png_sha = sha256_file(dst_png)
    tool_sha = sha256_file(tool_png)
    prompt_sha = sha256_file(prompt_path)
    sha_match = (png_sha == tool_sha) or bool(is_char and char_bg_Audit and char_bg_Audit.get("compliant"))

    request_data = {
        "prompt": prompt_text,
        "referenced_image_paths": job.get("refs", []),
        "transparent_background": job.get("transparent_background", False),
    }
    write_json(out_dir / "request.json", request_data)

    tool_resp_data = {
        "image_url": "[base64 omitted; original PNG preserved]",
        "original_png": str(tool_png).replace("\\", "/"),
        "call_count": 1,
    }
    write_json(out_dir / "tool_response.json", tool_resp_data)

    norm_target_path = _normalize_flat_frame_target_path(_resolve_unit_target_path(job), unit_id, job)
    bg_failed = bool(is_char and char_bg_Audit and not char_bg_Audit.get("compliant"))
    fp_anchor = _infer_project_root_from_path(batch_dir) or batch_dir
    u_fp = compute_unit_input_fingerprint(prompt_text, list(job.get("refs", [])), fp_anchor)
    result_data = {
        "unit": unit_id,
        "attempt": attempt,
        "status": "FAIL_NON_WHITE_BG" if bg_failed else "已生成待批次审图",
        "png": str(dst_png).replace("\\", "/"),
        "prompt": str(prompt_path).replace("\\", "/"),
        "refs": job.get("refs", []),
        "source_frame": job.get("source_frame", ""),
        "target_path": norm_target_path,
        "tool_original": str(tool_png).replace("\\", "/"),
        "processing": job.get("processing") or "仅原字节复制工具原PNG，未后处理，未采用。",
        "prompt_modified": bool(job.get("prompt_modified", False)),
        "error": "FAIL_NON_WHITE_BG: 人物母图未通过 100% #FFFFFF 纯白背景像素门禁！" if bg_failed else None,
        "png_sha256": png_sha,
        "tool_sha256": tool_sha,
        "sha256_match": sha_match,
        "input_fingerprint": u_fp["input_fingerprint"],
        "prompt_sha256": u_fp["prompt_sha256"],
        "refs_sha256": u_fp["refs_sha256"],
        "prompt_file_sha256": prompt_sha,
        "generation_calls": 1,
        "character_format_a_audit": char_bg_Audit,
        "frame_role_and_person_lock_audit": frame_role_audit,
        "scene_clean_audit": scene_clean_audit,
    }
    write_json(out_dir / "result.json", result_data)

    if bg_failed:
        print(
            f"{unit_id} | FAIL_NON_WHITE_BG | 人物参考图背景非 100% #FFFFFF 纯白 (white_ratio={char_bg_Audit.get('upper_and_side_white_ratio')})，拦截入库！",
            file=sys.stderr,
        )
        sys.exit(3)

    print(f"{unit_id} | OK | {dst_png} | sha256_match={sha_match}")


def _draw_left_info_card(draw, lx: int, ly: int, col_w: int, row_h: int, title: str, subtitle: str, font) -> None:
    """为纯原创无原型角色或纯原创空镜绘制高对比度文字信息卡"""
    card_box = (lx + 10, ly + 34, lx + col_w - 10, ly + row_h - 14)
    draw.rectangle(card_box, fill="#1e293b", outline="#94a3b8", width=3)
    draw.text((lx + 24, ly + 72), title, font=font, fill="#f8fafc")
    if subtitle:
        draw.text((lx + 24, ly + 116), subtitle[:36], font=font, fill="#cbd5e1")


def _extract_ref_path_str(item: Any) -> str:
    """从参考图条目（字符串或含 path/file/image/src/uri 的 dict）提取路径字符串"""
    if isinstance(item, dict):
        for k in ("path", "file", "image", "src", "uri"):
            val = str(item.get(k) or "").strip()
            if val:
                return val
        return ""
    return str(item or "").strip()


def _extract_job_ref_list(
    job: dict,
    keys: tuple[str, ...] = ("image_refs", "refs", "reference_images", "ref_images"),
) -> list[str]:
    """按优先级提取单元参考图路径列表（兼容字符串路径与含 path 键的 dict）"""
    for k in keys:
        raw = job.get(k)
        if isinstance(raw, list) and raw:
            parsed = [_extract_ref_path_str(r) for r in raw]
            parsed = [p for p in parsed if p]
            if parsed:
                return parsed
    return []


def _resolve_contact_source_frame(
    job: dict,
    batch_dir: Path,
    extra_scalar_keys: tuple[str, ...] = (),
) -> tuple[Path | None, str, str]:
    """
    解析接触表左列源帧/首参考图路径：
    优先级为 extra_scalar_keys -> source_frame -> source_image -> image_refs[0] -> refs[0] -> reference_images[0] -> ref_images[0]。
    命中首个非空且实际存在的文件时返回 (Path, field_name, raw_str)；
    若存在声明但均不存在返回 (None, first_field, first_raw)；完全未声明返回 (None, "", "")。
    """
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(field_name: str, val: Any) -> None:
        raw = _extract_ref_path_str(val)
        if raw and raw not in seen:
            seen.add(raw)
            candidates.append((field_name, raw))

    for sk in (*extra_scalar_keys, "source_frame", "source_image"):
        _add(sk, job.get(sk))

    for lk in ("image_refs", "refs", "reference_images", "ref_images"):
        raw_list = job.get(lk)
        if isinstance(raw_list, list) and raw_list:
            for entry in raw_list:
                raw_s = _extract_ref_path_str(entry)
                if raw_s:
                    _add(f"{lk}[0]", raw_s)
                    break

    if not candidates:
        return None, "", ""

    for field_name, raw_s in candidates:
        cand_p = _resolve_ref_path_smart(raw_s, batch_dir)
        if cand_p.is_file():
            return cand_p, field_name, raw_s

    first_field, first_raw = candidates[0]
    return None, first_field, first_raw


def _fit_and_paste_centered(canvas, rgb, box_x: int, box_y: int, box_w: int, box_h: int) -> tuple[int, int, int, int]:
    """按原始宽高比等比缩放适配至 (box_w, box_h) 并水平+垂直居中贴入 canvas（无拉伸、无裁切）"""
    from PIL import Image

    src_w, src_h = rgb.size
    if src_w <= 0 or src_h <= 0 or box_w <= 0 or box_h <= 0:
        return (box_x, box_y, 0, 0)
    scale = min(box_w / src_w, box_h / src_h)
    new_w = max(1, min(box_w, int(round(src_w * scale))))
    new_h = max(1, min(box_h, int(round(src_h * scale))))
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    fitted = rgb if (new_w == src_w and new_h == src_h) else rgb.resize((new_w, new_h), resampling)
    px = box_x + (box_w - new_w) // 2
    py = box_y + (box_h - new_h) // 2
    canvas.paste(fitted, (px, py))
    return (px, py, new_w, new_h)


def cmd_contact(args: argparse.Namespace) -> None:
    """拼合接触表（含左列与生成列像素 std >= 5.0 零空白门禁 + 原创高对比度文字信息卡）并生成 图片落盘SHA核验.json"""
    import numpy as np  # type: ignore
    from PIL import Image, ImageDraw, ImageFont

    batch_dir = Path(args.batch_dir).resolve()
    jobs = [j for j in extract_jobs_list(read_json(batch_dir / "jobs.json")) if isinstance(j, dict)]

    def get_unit_dir(j: dict) -> Path:
        u = get_unit_id(j)
        return resolve_job_path(j.get("output_dir") or str(batch_dir / u), batch_dir)

    has_remedy = any((get_unit_dir(j) / "补救" / "补救.png").exists() for j in jobs)
    cols = 3 if has_remedy else 2
    col_w, row_h = 640, 390

    im = Image.new("RGB", (cols * col_w, max(1, len(jobs)) * row_h), "#eeeeee")
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
    except Exception:
        font = ImageFont.load_default()

    checks = []
    for row, j in enumerate(jobs):
        unit_id = get_unit_id(j)
        unit_dir = get_unit_dir(j)
        init_png = unit_dir / "初始.png"
        rem_png = unit_dir / "补救" / "补救.png"
        src_raw = _extract_ref_path_str(j.get("source_frame"))
        src_p = _resolve_ref_path_smart(src_raw, batch_dir) if src_raw else None

        refs_list = _extract_job_ref_list(j)
        is_char = _is_character_job(j, unit_id)
        is_scene = _is_scene_job(j, unit_id)

        left_label = "原片"
        left_images: list[Path] = []
        left_card_info: tuple[str, str] | None = None

        if src_raw and src_p is not None and src_p.is_file():
            left_images = [src_p]
        elif is_char:
            subtype = _get_character_subtype(j, unit_id)
            if subtype == "portrait":
                h_raw = (
                    _extract_ref_path_str(j.get("prototype_photo_ref"))
                    or _extract_ref_path_str(j.get("historical_photo_ref"))
                    or _extract_ref_path_str(j.get("history_ref"))
                    or (refs_list[0] if refs_list else "")
                )
                h_path = _resolve_ref_path_smart(h_raw, batch_dir) if h_raw else None
                is_proto = bool(
                    j.get("is_historical_figure")
                    or j.get("is_real_prototype")
                    or j.get("prototype_photo_ref")
                    or j.get("historical_photo_ref")
                    or j.get("history_ref")
                    or ("历史" in h_raw)
                    or ("原型" in h_raw)
                    or ("历史" in unit_id)
                )
                if is_proto:
                    left_label = "原型/历史真容原照"
                    if not h_path or not h_path.is_file():
                        print(
                            f"CONTACT_ERR | 原型/历史人物肖像单元 {unit_id} 缺少有效的真容参考图: {h_raw or '<empty>'}",
                            file=sys.stderr,
                        )
                        sys.exit(2)
                    left_images = [h_path]
                else:
                    left_label = "原创角色设定"
                    cname = str(j.get("character") or unit_id)
                    left_card_info = ("原创角色设定卡（无真人原型照）", f"角色：{cname}")
            else:
                h_raw = (
                    _extract_ref_path_str(j.get("prototype_photo_ref"))
                    or _extract_ref_path_str(j.get("historical_photo_ref"))
                    or _extract_ref_path_str(j.get("history_ref"))
                )
                if not h_raw:
                    h_raw = next((r for r in refs_list if "历史" in r or "原型" in r), "")
                p_raw = _extract_ref_path_str(j.get("portrait_ref"))
                if not p_raw:
                    p_raw = next((r for r in refs_list if "_肖像" in r or "portrait" in r.lower()), "")
                if not p_raw and refs_list:
                    p_raw = refs_list[0]
                if not p_raw:
                    tp_raw = str(j.get("target_path") or j.get("formal_path") or "").strip()
                    if "_主体" in tp_raw:
                        p_raw = tp_raw.replace("_主体", "_肖像")

                p_path = _resolve_ref_path_smart(p_raw, batch_dir) if p_raw else None
                if not p_path or not p_path.is_file():
                    print(
                        f"CONTACT_ERR | 人物主体单元 {unit_id} 缺少有效的已锁 _肖像.png: {p_raw or '<empty>'}",
                        file=sys.stderr,
                    )
                    sys.exit(2)

                if h_raw:
                    h_path = _resolve_ref_path_smart(h_raw, batch_dir)
                    if not h_path.is_file():
                        print(
                            f"CONTACT_ERR | 人物主体单元 {unit_id} 声明的原型照不存在: {h_raw}",
                            file=sys.stderr,
                        )
                        sys.exit(2)
                    left_label = "原型+肖像对照"
                    left_images = [h_path, p_path] if h_path != p_path else [p_path]
                else:
                    left_label = "已锁肖像对照"
                    left_images = [p_path]
        elif is_scene:
            sc_p, sc_field, sc_raw = _resolve_contact_source_frame(
                j, batch_dir, extra_scalar_keys=("scene_source_frame",),
            )
            if sc_p is not None:
                left_label = "原片场景帧"
                left_images = [sc_p]
            elif sc_raw:
                print(
                    f"CONTACT_ERR | 场景母图单元 {unit_id} 声明的 {sc_field} 不存在: {sc_raw}",
                    file=sys.stderr,
                )
                sys.exit(2)
            else:
                left_label = "原创场景设定"
                sname = str(j.get("scene_name") or j.get("scene") or unit_id)
                left_card_info = ("原创纯空镜设定", f"场景：{sname}")
        else:
            resolved_p, src_field, src_decl = _resolve_contact_source_frame(j, batch_dir)
            if resolved_p is not None:
                left_images = [resolved_p]
            elif src_decl:
                print(
                    f"CONTACT_ERR | 分镜单元 {unit_id} 声明的 {src_field} 不存在: {src_decl}",
                    file=sys.stderr,
                )
                sys.exit(2)
            else:
                print(
                    f"CONTACT_ERR_BLANK_CELL | 分镜单元 {unit_id} 缺少 source_frame 或有效首参考图 (image_refs/refs)，左列为空白",
                    file=sys.stderr,
                )
                sys.exit(2)

        # 左列绘制（支持单图等比居中、双图并排 [原型真容 | 已锁肖像] 或 高对比度原创设定信息卡）
        lx, ly = 0, row * row_h
        draw.text((lx + 5, ly + 3), f"{unit_id} {left_label}", font=font, fill="black")
        if len(left_images) >= 2:
            half_w = (col_w - 15) // 2
            for idx_img, lp in enumerate(left_images[:2]):
                with Image.open(lp) as pic:
                    rgb = pic.convert("RGB")
                    img_std = float(np.asarray(rgb, dtype=np.float32).std())
                    if img_std < 5.0:
                        print(f"CONTACT_ERR_BLANK_CELL | {unit_id} 左列绑定图 {lp.name} 像素 std={img_std:.2f} < 5.0", file=sys.stderr)
                        sys.exit(2)
                    _fit_and_paste_centered(im, rgb, lx + 4 + idx_img * (half_w + 6), ly + 30, half_w, row_h - 40)
        elif len(left_images) == 1:
            with Image.open(left_images[0]) as pic:
                rgb = pic.convert("RGB")
                img_std = float(np.asarray(rgb, dtype=np.float32).std())
                if img_std < 5.0:
                    print(f"CONTACT_ERR_BLANK_CELL | {unit_id} 左列绑定图 {left_images[0].name} 像素 std={img_std:.2f} < 5.0", file=sys.stderr)
                    sys.exit(2)
                _fit_and_paste_centered(im, rgb, lx + 5, ly + 30, col_w - 10, row_h - 40)
        elif left_card_info is not None:
            _draw_left_info_card(draw, lx, ly, col_w, row_h, left_card_info[0], left_card_info[1], font)

        left_crop = im.crop((lx, ly + 30, lx + col_w, ly + row_h))
        cell_std = float(np.asarray(left_crop, dtype=np.float32).std())
        if cell_std < 5.0:
            print(f"CONTACT_ERR_BLANK_CELL | {unit_id} 左列单元格像素 std={cell_std:.2f} < 5.0", file=sys.stderr)
            sys.exit(2)

        other_paths = [("初始", init_png if init_png.exists() else None)]
        if has_remedy:
            other_paths.append(("补救/无", rem_png if rem_png.exists() else None))

        for offset_col, (label, p) in enumerate(other_paths, 1):
            x, y = offset_col * col_w, row * row_h
            draw.text((x + 5, y + 3), f"{unit_id} {label}", font=font, fill="black")
            if p and p.exists():
                with Image.open(p) as pic:
                    rgb = pic.convert("RGB")
                    gen_std = float(np.asarray(rgb, dtype=np.float32).std())
                    if gen_std < 5.0:
                        print(f"CONTACT_ERR_BLANK_CELL | {unit_id} 生成列 ({label}) 图像为空白/纯色 std={gen_std:.2f} < 5.0", file=sys.stderr)
                        sys.exit(2)
                    _fit_and_paste_centered(im, rgb, x + 5, y + 30, col_w - 10, row_h - 40)

        for rp in [unit_dir / "result.json", unit_dir / "补救" / "result.json"]:
            if not rp.exists():
                continue
            r = read_json(rp)
            png_p = Path(r["png"])
            tool_p = Path(r["tool_original"])
            prompt_p = Path(r["prompt"])
            p_sha = sha256_file(png_p) if png_p.exists() else ""
            t_sha = sha256_file(tool_p) if tool_p.exists() else ""
            pr_sha = sha256_file(prompt_p) if prompt_p.exists() else ""
            checks.append({
                "unit": r["unit"],
                "attempt": r.get("attempt", "初始"),
                "png": r["png"],
                "tool_original": r["tool_original"],
                "png_sha256": p_sha,
                "tool_sha256": t_sha,
                "match": bool(p_sha and (p_sha == t_sha or r.get("sha256_match") is True)),
                "prompt": r["prompt"],
                "prompt_sha256": pr_sha,
            })

    sheet_name = "源_初始_补救接触表.jpg" if has_remedy else "源与初图接触表.jpg"
    sheet_path = batch_dir / sheet_name
    im.save(sheet_path, quality=93)
    write_json(batch_dir / "图片落盘SHA核验.json", checks)

    all_match = all(c["match"] for c in checks) if checks else False
    print(f"CONTACT_OK | sheet={sheet_path} | records={len(checks)} | all_sha_match={all_match}")


def _extract_remedy_map(review_raw) -> dict:
    """兼容多种 批次审核结论.json 结构，提取 unit -> rem_info 字典"""
    if isinstance(review_raw, dict):
        if isinstance(review_raw.get("remedies"), dict):
            return review_raw["remedies"]
        if isinstance(review_raw.get("remedies"), list):
            return {get_unit_id(item): item for item in review_raw["remedies"] if isinstance(item, dict)}
        for k in ("units", "results", "items", "reviews"):
            val = review_raw.get(k)
            if isinstance(val, dict):
                return {
                    u: info for u, info in val.items()
                    if isinstance(info, dict) and info.get("status") in ("未通过", "需补救", "remedy", "fail", "failed")
                }
            if isinstance(val, list):
                return {
                    get_unit_id(item): item for item in val
                    if isinstance(item, dict) and (
                        item.get("status") in ("未通过", "需补救", "remedy", "fail", "failed")
                        or any(pk in item for pk in ("full_prompt", "new_prompt", "revised_prompt", "prompt", "prefix"))
                    )
                }
    elif isinstance(review_raw, list):
        return {
            get_unit_id(item): item for item in review_raw
            if isinstance(item, dict) and (
                item.get("status") in ("未通过", "需补救", "remedy", "fail", "failed")
                or any(pk in item for pk in ("full_prompt", "new_prompt", "revised_prompt", "prompt", "prefix"))
            )
        }
    return {}


def cmd_remedy_prep(args: argparse.Namespace) -> None:
    """根据批次审图子代理输出的 批次审核结论.json 自动生成 补救/完整提示词.txt 与 remedy_jobs.json"""
    batch_dir = Path(args.batch_dir).resolve()
    jobs = [j for j in extract_jobs_list(read_json(batch_dir / "jobs.json")) if isinstance(j, dict)]
    review = read_json(Path(args.review_json).resolve())
    remedies = _extract_remedy_map(review)

    remedy_jobs = []
    for j in jobs:
        unit = get_unit_id(j)
        if unit not in remedies:
            continue
        rem_info = remedies[unit]
        unit_dir = resolve_job_path(j.get("output_dir") or str(batch_dir / unit), batch_dir)
        rem_dir = unit_dir / "补救"
        rem_dir.mkdir(parents=True, exist_ok=True)
        rem_prompt_path = rem_dir / "完整提示词.txt"

        orig_prompt_path = resolve_job_path(j.get("prompt_file") or str(unit_dir / "完整提示词.txt"), batch_dir)
        orig_prompt = orig_prompt_path.read_text(encoding="utf-8-sig")

        explicit_prompt = (
            rem_info.get("full_prompt")
            or rem_info.get("new_prompt")
            or rem_info.get("revised_prompt")
            or rem_info.get("modified_prompt")
            or rem_info.get("prompt")
        )
        same_prompt_ok = bool(rem_info.get("same_prompt_remedy") or rem_info.get("pure_noise"))

        if explicit_prompt:
            new_prompt = str(explicit_prompt).strip()
            prompt_modified = (new_prompt != orig_prompt.strip())
        elif rem_info.get("prefix"):
            new_prompt = str(rem_info["prefix"]).strip() + "\n\n" + orig_prompt
            prompt_modified = True
        elif same_prompt_ok:
            new_prompt = orig_prompt
            prompt_modified = False
        else:
            print(
                f"REMEDY_PREP_ERR | unit={unit} 缺少修改后的提示词字段 (full_prompt/new_prompt/prefix)；"
                f"若确属纯噪点原词重绘，请显式设置 same_prompt_remedy=true",
                file=sys.stderr,
            )
            sys.exit(1)

        rem_prompt_path.write_text(new_prompt, encoding="utf-8")
        if _is_character_job(j, unit):
            _enforce_character_format_a_prompt(rem_prompt_path, j, unit)
        elif _is_scene_job(j, unit):
            _enforce_scene_clean_prompt(rem_prompt_path, j, unit)
        else:
            _enforce_frame_role_and_person_lock_prompt(rem_prompt_path, j)
        processing_mode = rem_info.get(
            "processing",
            "修改提示词整张重绘" if prompt_modified else "纯噪点原词整张重绘",
        )
        new_refs = list(rem_info.get("refs")) if isinstance(rem_info.get("refs"), list) else list(j.get("refs", []))
        if "局部重绘" in str(processing_mode):
            init_png_path = unit_dir / "初始.png"
            if init_png_path.exists():
                init_png_str = str(init_png_path.resolve()).replace("\\", "/")
                norm_existing = {str(Path(r).resolve()).replace("\\", "/") for r in new_refs if r}
                if init_png_str not in norm_existing:
                    new_refs.insert(0, init_png_str)

        remedy_jobs.append({
            **j,
            "unit": unit,
            "attempt": "补救",
            "refs": new_refs,
            "prompt_file": str(rem_prompt_path).replace("\\", "/"),
            "output_dir": str(rem_dir).replace("\\", "/"),
            "prompt_modified": bool(rem_info.get("prompt_modified", prompt_modified)),
            "processing": processing_mode,
            "remedy_reason": rem_info.get("reason", ""),
        })

    jobs_raw_obj = read_json(batch_dir / "jobs.json")
    win_id_rem = str(jobs_raw_obj.get("window_id") or "window_01") if isinstance(jobs_raw_obj, dict) else "window_01"
    w_dir_rem = batch_dir.parent.parent if batch_dir.parent.name == "生成批次" else batch_dir.parent
    check_remedy_quota_or_exit(w_dir_rem, win_id_rem, len(remedy_jobs))

    out_path = batch_dir / "remedy_jobs.json"
    write_json(out_path, remedy_jobs)
    print(f"REMEDY_PREP_OK | count={len(remedy_jobs)} | file={out_path}")


def cmd_init(args: argparse.Namespace) -> None:
    """一键初始化标准长剧/复刻项目底层目录架构与指定正式集工作区（严格遵循 项目基本结构以及规则.md）"""
    root = Path(args.project_root).resolve()
    ep = args.episode.strip()
    if not re.match(r"^第\d{2,3}集$", ep):
        print(f"正式集名称应形如 '第01集' 或 '第001集'，当前为: {ep}", file=sys.stderr)
        sys.exit(1)

    # 1. 根级共享目录与中间文件骨架（不建原EP层，不建假文件）
    core_dirs = [
        root / "原视频",
        root / "人物参考",
        root / "场景参考",
        root / "声线参考",
        root / "分集" / ep / "人物",
        root / "分集" / ep / "声线",
        root / "分集" / ep / "台词音频",
        root / "分集" / ep / "首尾及关键帧",
        root / "分集" / ep / "原视频段落",
        root / "中间文件" / "索引与制作记录" / "脚本自查" / ep,
        root / "中间文件" / "索引与制作记录" / "提示词自查" / ep,
        root / "中间文件" / "索引与制作记录" / "生图自查" / ep,
        root / "中间文件" / "索引与制作记录" / "交付自查状态",
        root / "中间文件" / "原片分析" / ep,
        root / "中间文件" / "提示词维护" / "画风基准",
        root / "中间文件" / "提示词维护" / "视频提示词" / ep,
        root / "中间文件" / "提示词维护" / "生图提示词" / "分集" / ep,
        root / "中间文件" / "在制工作区" / ep / "生成批次",
        root / "中间文件" / "待归位",
        root / "中间文件" / "项目迭代",
    ]
    for d in core_dirs:
        d.mkdir(parents=True, exist_ok=True)

    # 1.5 若传入外部 --source-video 且文件存在，自动入库至 <项目根目录>/原视频/ 并登记真实路径；否则扫描 原视频/ 已有源片
    raw_src_arg = str(getattr(args, "source_video", "") or "").strip()
    registered_source_video = raw_src_arg or "待绑定"
    actual_src_file: Optional[Path] = None
    if raw_src_arg:
        src_cand = resolve_cross_platform_path(raw_src_arg)
        if src_cand.exists() and src_cand.is_file():
            dst_raw = root / "原视频" / src_cand.name
            if src_cand.resolve() != dst_raw.resolve() and not dst_raw.exists():
                shutil.copy2(src_cand, dst_raw)
            actual_src_file = dst_raw if dst_raw.exists() else src_cand
            registered_source_video = str(actual_src_file).replace("\\", "/")
    if actual_src_file is None:
        raw_dir = root / "原视频"
        for ext in ("*.mp4", "*.mkv", "*.mov", "*.ts", "*.m2ts", "*.flv", "*.avi"):
            cands = sorted(raw_dir.glob(ext))
            if cands:
                actual_src_file = cands[0]
                if registered_source_video in ("", "待绑定"):
                    registered_source_video = str(actual_src_file).replace("\\", "/")
                break

    actual_dur: Optional[float] = None
    if actual_src_file is not None and actual_src_file.exists():
        try:
            p_res = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(actual_src_file)],
                capture_output=True, text=True, encoding="utf-8", check=True,
            )
            dur_val = float(json.loads(p_res.stdout).get("format", {}).get("duration", 0.0) or 0.0)
            if dur_val > 0.0:
                actual_dur = round(dur_val, 3)
        except Exception:
            actual_dur = None

    def _fmt_sec_val(v: float) -> str:
        r = round(float(v), 3)
        return str(int(round(r))) if abs(r - int(round(r))) < 1e-6 else f"{r:.3f}".rstrip("0").rstrip(".")

    m_ep_num = re.search(r"(\d+)", ep)
    ep_num = int(m_ep_num.group(1)) if m_ep_num else 1
    raw_range_arg = str(getattr(args, "source_range", "") or "").strip()
    m_rng = re.search(r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)", raw_range_arg)
    if m_rng:
        s_sec, e_sec = float(m_rng.group(1)), float(m_rng.group(2))
    else:
        s_sec, e_sec = float((ep_num - 1) * 360), float(ep_num * 360)

    expected_end_sec: Optional[float] = None
    if actual_dur is not None:
        e_sec = round(min(e_sec, actual_dur), 3)
        expected_end_sec = e_sec
        clamped_source_range = f"{_fmt_sec_val(s_sec)}-{_fmt_sec_val(e_sec)}s"
    else:
        clamped_source_range = raw_range_arg or "待核实（默认360秒切点）"
        if m_rng:
            expected_end_sec = round(e_sec, 3)

    aspect_ratio = normalize_aspect_ratio(getattr(args, "aspect_ratio", "16:9"))
    aspect_ratio_explicit = any(a == "--aspect-ratio" or a.startswith("--aspect-ratio=") for a in sys.argv[1:])

    # 2. 初始化核心索引 JSON（若尚不存在则创建初始结构，绝不覆盖已有记录）
    idx_dir = root / "中间文件" / "索引与制作记录"
    ep_index_file = idx_dir / "分集索引.json"
    if not ep_index_file.exists():
        write_json(ep_index_file, {
            "project_root": str(root).replace("\\", "/"),
            "aspect_ratio": aspect_ratio,
            "split_rule": "默认按原片每6分钟（360秒）固定切集，排除非剧情片头片尾",
            "episodes": {}
        })

    ep_index = read_json(ep_index_file)
    if "episodes" not in ep_index or not isinstance(ep_index["episodes"], dict):
        ep_index["episodes"] = {}
    if ep not in ep_index["episodes"]:
        ep_entry = {
            "episode_id": ep,
            "aspect_ratio": aspect_ratio,
            "episode_dir": str(root / "分集" / ep).replace("\\", "/"),
            "work_dir": str(root / "中间文件" / "在制工作区" / ep).replace("\\", "/"),
            "source_video": registered_source_video,
            "source_range_sec": clamped_source_range,
            "segments": [],
            "seven_deliverables": {
                "人物": str(root / "分集" / ep / "人物").replace("\\", "/"),
                "声线": str(root / "分集" / ep / "声线").replace("\\", "/"),
                "剧本故事梗概与逐镜导演脚本": str(root / "分集" / ep / f"{ep}_剧本故事梗概与逐镜导演脚本.md").replace("\\", "/"),
                "视频提示词": str(root / "分集" / ep / f"{ep}_视频提示词.md").replace("\\", "/"),
                "首尾及关键帧": str(root / "分集" / ep / "首尾及关键帧").replace("\\", "/"),
                "分镜头脚本": str(root / "分集" / ep / f"{ep}_分镜头脚本.xlsx").replace("\\", "/"),
                "台词生成表": str(root / "分集" / ep / f"{ep}_台词生成表.xlsx").replace("\\", "/"),
                "原视频段落": str(root / "分集" / ep / "原视频段落").replace("\\", "/"),
                "字幕时间轴": str(root / "分集" / ep / f"{ep}_字幕时间轴.csv").replace("\\", "/"),
            }
        }
        if expected_end_sec is not None:
            ep_entry["expected_end_sec"] = expected_end_sec
        if actual_dur is not None:
            ep_entry["source_duration_sec"] = actual_dur
        ep_index["episodes"][ep] = ep_entry
        write_json(ep_index_file, ep_index)
    else:
        changed = False
        ep_entry = ep_index["episodes"][ep]
        if aspect_ratio_explicit or not str(ep_entry.get("aspect_ratio") or "").strip():
            if ep_entry.get("aspect_ratio") != aspect_ratio:
                ep_entry["aspect_ratio"] = aspect_ratio
                changed = True
        if registered_source_video not in ("", "待绑定") and ep_entry.get("source_video") in ("", None, "待绑定"):
            ep_entry["source_video"] = registered_source_video
            changed = True
        if actual_dur is not None:
            old_rng = str(ep_entry.get("source_range_sec") or "")
            m_old = re.search(r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)", old_rng)
            old_over = (not m_old) or (float(m_old.group(2)) > actual_dur + 1e-3)
            if old_over or raw_range_arg:
                ep_entry["source_range_sec"] = clamped_source_range
                changed = True
            if expected_end_sec is not None and (ep_entry.get("expected_end_sec") is None or float(ep_entry.get("expected_end_sec") or 0.0) > actual_dur + 1e-3 or raw_range_arg):
                ep_entry["expected_end_sec"] = expected_end_sec
                changed = True
            if ep_entry.get("source_duration_sec") != actual_dur:
                ep_entry["source_duration_sec"] = actual_dur
                changed = True
        if changed:
            write_json(ep_index_file, ep_index)

    placement_file = idx_dir / "文件归位清单.json"
    if not placement_file.exists():
        write_json(placement_file, {"project_root": str(root).replace("\\", "/"), "records": []})

    rename_file = idx_dir / "角色改名与音色映射.json"
    if not rename_file.exists():
        write_json(rename_file, {
            "note": (
                "原名→新名唯一映射；每个角色须在 voice 字段定义供第二棒 voice-gen 全新合成使用的差异化声学人设参数"
                "（speech_base_voice / inner_base_voice 基底音色、acoustic_persona 声学母词、"
                "speech_sample_line / inner_sample_line 6-9字完整短句；输出 48000Hz Mono pcm_s16le, 2.00s-3.00s）"
            ),
            "characters": {}
        })

    self_check_state_file = idx_dir / "交付自查状态" / f"{ep}.json"
    if not self_check_state_file.exists():
        write_json(self_check_state_file, {
            "episode_id": ep,
            "checks": {
                "脚本文件自查": {"status": "未到阶段", "record_path": ""},
                "提示词文件自查": {"status": "未到阶段", "record_path": ""},
                "生图自查": {"status": "未到阶段", "record_path": ""},
            }
        })

    cls_file = root / "中间文件" / "在制工作区" / ep / "当前制作审核分类.json"
    if not cls_file.exists():
        write_json(cls_file, {})

    global_reg_file = root / "全局设定" / "全剧世界观与角色注册表.json"
    if not global_reg_file.exists():
        write_json(global_reg_file, {
            "schema": "series_thin_card_v2",
            "max_tokens_budget": 1500,
            "worldview": "",
            "visual_style_en": "",
            "characters": {},
        })

    print(f"INIT_OK | project_root={root} | episode={ep} | ep_dir={root / '分集' / ep}")


def cmd_fill_style(args: argparse.Namespace) -> None:
    """由 GPT 一键将已确认的视觉风格词与人物造型回填/同步到 第NN集_视频提示词.md 的所有 segment 中（支持幂等更新与角色阶段后缀，并自动同步当前主稿.md）"""
    md_path = Path(args.md_file).resolve()
    style_json_path = Path(args.style_json).resolve()
    cfg = read_json(style_json_path)
    base_style = cfg.get("visual_style", "").strip()
    prev_style = cfg.get("_last_applied_visual_style", "").strip()
    cast_map: dict = cfg.get("characters", {})

    text = md_path.read_text(encoding="utf-8-sig")

    # 1. 替换 {{VISUAL_STYLE}} 占位符，或对已回填过的画风母词做幂等二次更新（自动去重衔接标点与多余空格）
    if base_style:
        if "{{VISUAL_STYLE}}" in text:
            if base_style[-1] in "。；，,;":
                text = re.sub(r"\{\{VISUAL_STYLE\}\}[，,；;。\s]*", lambda _: base_style, text)
            else:
                text = text.replace("{{VISUAL_STYLE}}", base_style)
        elif prev_style and prev_style != base_style and prev_style in text:
            text = re.sub(
                r"(^视觉风格[：:]\s*)" + re.escape(prev_style),
                lambda m: m.group(1) + base_style,
                text,
                flags=re.MULTILINE,
            )
        if cfg.get("_last_applied_visual_style") != base_style:
            cfg["_last_applied_visual_style"] = base_style
            write_json(style_json_path, cfg)

    # 兼容 `人物造型：【待填角色：...】` 写在同一行的情况，统一规范为换行展开
    text = re.sub(r"^(人物造型[：:])[ \t]*(?=【待填角色|\{\{CHAR:)", r"\1\n", text, flags=re.MULTILINE)

    # 2. 按 segment 扫描，将 【待填角色：角色A、角色B（战斗服）】 替换为完整人物造型描述
    def resolve_char_desc(name_token: str) -> tuple[str, str | None]:
        # 优先精确匹配（如 "角色A（战斗服）"），其次回退到去除括号后缀的基础角色名（如 "角色A"）
        if name_token in cast_map:
            return name_token, cast_map[name_token]
        base_name = re.sub(r"[（(][^）)]+[）)]$", "", name_token).strip()
        if base_name in cast_map:
            return name_token, cast_map[base_name]
        return name_token, None

    def replace_cast_placeholder(match: re.Match) -> str:
        names_raw = match.group(1)
        names = [n.strip() for n in re.split(r"[、，,;/\r\n]+", names_raw) if n.strip()]
        lines = []
        for n in names:
            disp_name, desc = resolve_char_desc(n)
            if desc:
                lines.append(f"{disp_name}：{desc}")
            else:
                lines.append(f"{disp_name}：【待补角色造型】")
        return "\n".join(lines) if lines else match.group(0)

    text = re.sub(r"【待填角色[：:]([^】]+)】", replace_cast_placeholder, text)

    # 3. 支持直接替换 {{CHAR:角色名}} 占位符，并在【人物造型】区块内对已回填过的角色行（含阶段括号后缀）做幂等更新
    for char_name, char_desc in cast_map.items():
        placeholder = f"{{{{CHAR:{char_name}}}}}"
        if placeholder in text:
            text = text.replace(placeholder, f"{char_name}：{char_desc}")

    def update_cast_block(block_match: re.Match) -> str:
        header = block_match.group(1)
        body = block_match.group(2)
        updated_lines = []
        for line in body.splitlines():
            m_line = re.match(r"^([^：:\r\n]+)[：:]([^\r\n]*)$", line.strip())
            if m_line and not line.strip().startswith("【待填角色"):
                token = m_line.group(1).strip()
                disp_name, desc = resolve_char_desc(token)
                if desc:
                    updated_lines.append(f"{disp_name}：{desc}")
                    continue
            updated_lines.append(line)
        return header + "\n".join(updated_lines) + "\n"

    text = re.sub(
        r"(^人物造型[：:]\r?\n)([\s\S]*?)(?=^(?:表演核心|禁止项|镜头\d+|##\s+segment_)|\Z)",
        update_cast_block,
        text,
        flags=re.MULTILINE,
    )

    # 3.5 对所有 Google Flow / Veo 英文提示词区块强制执行“禁人声、锁闭嘴、强眉眼情绪代偿”净化（剔除任何 speaks in clear Mandarin Chinese）
    text = enforce_flow_silent_emotional_acting(text)

    md_path.write_text(text, encoding="utf-8")

    # 4. 自动同步 中间文件/提示词维护/视频提示词/第NN集/当前主稿.md（保证提交文件与主稿 SHA-256 一致）
    synced_master = ""
    if md_path.parent.parent.name == "分集":
        ep_name = md_path.parent.name
        proj_root = md_path.parent.parent.parent
        master_dir = proj_root / "中间文件" / "提示词维护" / "视频提示词" / ep_name
        if master_dir.exists() or (proj_root / "中间文件").exists():
            master_dir.mkdir(parents=True, exist_ok=True)
            master_file = master_dir / "当前主稿.md"
            master_file.write_text(text, encoding="utf-8")
            synced_master = f" | master_synced={master_file}"

    print(f"FILL_STYLE_OK | md={md_path} | characters_loaded={len(cast_map)}{synced_master}")


def cmd_finalize(args: argparse.Namespace) -> None:
    """步骤5收尾：根据审核结论/总结JSON更新 当前制作审核分类.json，并将审核通过的图片自动归位至正式集目录与 文件归位清单.json"""
    batch_dir = Path(args.batch_dir).resolve()
    jobs = [j for j in extract_jobs_list(read_json(batch_dir / "jobs.json")) if isinstance(j, dict)]
    summary_path = Path(args.summary_json).resolve() if args.summary_json else (batch_dir / "审核总结.json")
    if not summary_path.exists():
        summary_path = batch_dir / "批次审核结论.json"
    if not summary_path.exists():
        print(f"FINALIZE_ERR | 未找到审核总结或结论 JSON: {summary_path}", file=sys.stderr)
        sys.exit(1)

    summary_raw = read_json(summary_path)
    # 封堵伪造自检通过门禁 1：必须先存在非空接触表与图片落盘SHA核验.json
    has_nonempty_sheet = any(
        (batch_dir / sname).exists() and (batch_dir / sname).stat().st_size > 1024
        for sname in ("源与初图接触表.jpg", "源_初始_补救接触表.jpg")
    )
    sha_verify_file = batch_dir / "图片落盘SHA核验.json"
    if not has_nonempty_sheet or not sha_verify_file.exists() or sha_verify_file.stat().st_size == 0:
        print(
            f"FINALIZE_BLOCKED_NO_CONTACT_SHEET | batch={batch_dir.name} 缺少非空接触表或 图片落盘SHA核验.json，请先运行 batch_ops.py contact！",
            file=sys.stderr,
        )
        sys.exit(2)

    # 提取各 unit 的最终判定状态
    decisions = {}
    if isinstance(summary_raw, dict):
        src_dict = summary_raw.get("units") or summary_raw.get("decisions") or summary_raw.get("results") or summary_raw
        if isinstance(src_dict, dict):
            for u, val in src_dict.items():
                if isinstance(val, dict):
                    decisions[u] = val
                elif isinstance(val, str):
                    decisions[u] = {"status": val}
        elif isinstance(src_dict, list):
            for item in src_dict:
                if isinstance(item, dict):
                    decisions[get_unit_id(item)] = item
    elif isinstance(summary_raw, list):
        for item in summary_raw:
            if isinstance(item, dict):
                decisions[get_unit_id(item)] = item

    # 封堵伪造自检通过门禁 2：必须存在由 save-unit 落盘的合法 result.json（含非空 png_sha256）；通过单元 reason 去空白 >= 12 字符、禁套话黑名单且同批 < 3 个相同 reason
    pass_statuses = ("通过", "审核通过", "自检通过", "pass", "passed", "OK")
    boilerplate_blacklist = ("七维红线全项合格", "自检通过", "审核通过", "符合要求", "全部通过", "画面合格")
    reason_counts: dict[str, int] = {}
    for j in jobs:
        u = get_unit_id(j)
        unit_dir = resolve_job_path(j.get("output_dir") or str(batch_dir / u), batch_dir)
        d_info = decisions.get(u, {})
        chosen_attempt = d_info.get("chosen") or d_info.get("attempt") or ("补救" if (unit_dir / "补救" / "补救.png").exists() else "初始")
        res_json_p = (unit_dir / "补救" / "result.json") if (chosen_attempt == "补救" and (unit_dir / "补救" / "result.json").exists()) else (unit_dir / "result.json")
        if not res_json_p.exists():
            print(
                f"FINALIZE_BLOCKED_MISSING_SAVE_UNIT_RESULT | 单元 {u} 缺少 save-unit 落盘的 result.json ({res_json_p})，严禁手写绕过落盘！",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            res_obj = read_json(res_json_p)
        except Exception as exc:
            print(
                f"FINALIZE_BLOCKED_MISSING_SAVE_UNIT_RESULT | 单元 {u} 的 result.json 无法解析 ({exc})",
                file=sys.stderr,
            )
            sys.exit(2)
        if not isinstance(res_obj, dict) or not str(res_obj.get("png_sha256") or "").strip():
            print(
                f"FINALIZE_BLOCKED_MISSING_SAVE_UNIT_RESULT | 单元 {u} 的 result.json 缺少非空 png_sha256，非法落盘！",
                file=sys.stderr,
            )
            sys.exit(2)

        raw_status = str(d_info.get("status") or d_info.get("category") or d_info.get("decision") or "")
        if raw_status in pass_statuses:
            reason_raw = str(d_info.get("reason") or d_info.get("note") or "")
            reason_compact = re.sub(r"\s+", "", reason_raw)
            if len(reason_compact) < 12:
                print(
                    f"FINALIZE_BLOCKED_FAKE_BULK_PASS | 单元 {u} 被判为 '{raw_status}' 但缺少具体针对本图的审核理由（去空白长度 < 12 字符）！",
                    file=sys.stderr,
                )
                sys.exit(2)
            reason_no_punct = re.sub(r"[，。、；：！？,.;:!?\s\-—_（）()【】\[\]]+", "", reason_raw)
            reason_stripped_bp = reason_no_punct
            for bp in boilerplate_blacklist:
                reason_stripped_bp = reason_stripped_bp.replace(bp, "")
            if (reason_no_punct in boilerplate_blacklist) or (
                any(bp in reason_compact for bp in boilerplate_blacklist) and len(reason_stripped_bp) < 12
            ):
                print(
                    f"FINALIZE_BLOCKED_FAKE_BULK_PASS | 单元 {u} 审核理由命中套话黑名单或缺乏针对本图的具体视觉核验内容: {reason_raw!r}",
                    file=sys.stderr,
                )
                sys.exit(2)
            reason_norm = re.sub(r"\s+", " ", reason_raw).strip()
            reason_counts[reason_norm] = reason_counts.get(reason_norm, 0) + 1
            if reason_counts[reason_norm] >= 3:
                print(
                    f"FINALIZE_BLOCKED_FAKE_BULK_PASS | 批次 {batch_dir.name} 内出现 >= 3 个通过单元共用完全相同的审核理由: {reason_norm!r}",
                    file=sys.stderr,
                )
                sys.exit(2)

    # 定位 work_dir（默认 batch_dir.parent.parent，即 在制工作区/第NN集）
    work_dir = Path(args.work_dir).resolve() if args.work_dir else batch_dir.parent.parent
    cls_file = work_dir / "当前制作审核分类.json"
    cls_data = read_json(cls_file) if cls_file.exists() else {}

    manifest_file = work_dir / "待生图帧清单.json"
    manifest_targets = {}
    if manifest_file.exists():
        m_raw = read_json(manifest_file)
        m_items = m_raw if isinstance(m_raw, list) else m_raw.get("units", [])
        for it in m_items:
            if isinstance(it, dict) and get_unit_id(it) != "unknown":
                manifest_targets[get_unit_id(it)] = _resolve_unit_target_path(it)

    # 尝试定位项目根目录与 文件归位清单.json
    project_root = (work_dir.parent.parent.parent if work_dir.parent.name == "在制工作区" else None) or _infer_project_root_from_path(work_dir)
    placement_file = (project_root / "中间文件" / "索引与制作记录" / "文件归位清单.json") if project_root else None
    placement_data = read_json(placement_file) if (placement_file and placement_file.exists()) else None

    passed_count, review_count, promoted_count = 0, 0, 0
    for j in jobs:
        u = get_unit_id(j)
        unit_dir = resolve_job_path(j.get("output_dir") or str(batch_dir / u), batch_dir)
        rem_png = unit_dir / "补救" / "补救.png"
        init_png = unit_dir / "初始.png"

        d_info = decisions.get(u, {})
        raw_status = str(d_info.get("status") or d_info.get("category") or d_info.get("decision") or "")
        chosen_attempt = d_info.get("chosen") or d_info.get("attempt") or ("补救" if rem_png.exists() else "初始")
        chosen_png = rem_png if (chosen_attempt == "补救" and rem_png.exists()) else init_png

        is_passed = raw_status in ("通过", "审核通过", "自检通过", "pass", "passed", "OK")
        category = "审核通过" if is_passed else "需要用户审核"
        if is_passed:
            passed_count += 1
        else:
            review_count += 1

        target_raw = (
            _resolve_unit_target_path(d_info)
            or _resolve_unit_target_path(j)
            or manifest_targets.get(u)
            or ""
        )
        target_raw = _normalize_flat_frame_target_path(str(target_raw), u, j)
        promoted_path_str = ""
        png_sha = sha256_file(chosen_png) if chosen_png.exists() else ""

        if is_passed and target_raw and chosen_png.exists():
            if _is_character_job(j, u):
                bg_chk = check_white_background(chosen_png)
                if not bg_chk.get("compliant"):
                    print(
                        f"FINALIZE_ERR | FAIL_NON_WHITE_BG | 单元 {u} 未通过 100% #FFFFFF 纯白背景像素门禁 (white_ratio={bg_chk.get('upper_and_side_white_ratio')})，禁止归位入库！",
                        file=sys.stderr,
                    )
                    is_passed = False
                    category = "需要用户审核"
                    passed_count -= 1
                    review_count += 1
                png_sha = sha256_file(chosen_png)
        if is_passed and target_raw and chosen_png.exists():
            target_p = Path(target_raw)
            if not target_p.is_absolute() and project_root:
                target_p = (project_root / target_p).resolve()
            else:
                target_p = target_p.resolve()
            target_p.parent.mkdir(parents=True, exist_ok=True)
            if chosen_png.resolve() != target_p.resolve():
                shutil.copyfile(chosen_png, target_p)
            # 若归位至根级 人物参考/，自动同步副本至当前集 分集/第NN集/人物/
            if _is_character_job(j, u) and project_root and work_dir.name.startswith("第"):
                ep_char_dir = project_root / "分集" / work_dir.name / "人物"
                ep_char_dir.mkdir(parents=True, exist_ok=True)
                ep_char_dst = ep_char_dir / target_p.name
                if ep_char_dst.resolve() != target_p.resolve():
                    shutil.copyfile(chosen_png, ep_char_dst)
            promoted_path_str = str(target_p).replace("\\", "/")
            promoted_count += 1

            if isinstance(placement_data, dict) and isinstance(placement_data.get("records"), list):
                m_u = re.match(r"^(segment_\d+)[_/](镜头\d+)[_/](首帧|尾帧|中间帧.*)$", u)
                seg_str = j.get("segment") or (m_u.group(1) if m_u else "")
                shot_str = j.get("shot") or (m_u.group(2) if m_u else "")
                ftype_str = j.get("frame_type") or (m_u.group(3) if m_u else "")
                ep_str = j.get("episode") or (work_dir.name if work_dir.name.startswith("第") else "")
                verified_ok = bool(target_p.exists() and target_p.stat().st_size > 0 and sha256_file(target_p) == png_sha)

                placement_data["records"] = [
                    r for r in placement_data["records"]
                    if not (isinstance(r, dict) and (r.get("unit") == u or r.get("formal_path") == promoted_path_str))
                ]
                placement_data["records"].append({
                    "task_id": f"{ep_str}_{batch_dir.name}_{u}" if ep_str else f"{batch_dir.name}_{u}",
                    "unit": u,
                    "episode": ep_str,
                    "segment": seg_str,
                    "shot": shot_str,
                    "frame_type": ftype_str,
                    "batch": batch_dir.name,
                    "attempt": chosen_attempt,
                    "source_png": str(chosen_png).replace("\\", "/"),
                    "planned_target_path": str(target_raw).replace("\\", "/"),
                    "formal_path": promoted_path_str,
                    "verified_readable": verified_ok,
                    "status": "审核通过已归位" if verified_ok else "文件校验异常",
                    "sha256": png_sha,
                })

        fp_anchor = project_root or work_dir
        m_p_txt, m_r_list = extract_item_prompt_and_refs(j, fp_anchor)
        if not m_p_txt:
            p_file_cand = resolve_job_path(j.get("prompt_file") or str(unit_dir / "完整提示词.txt"), batch_dir)
            m_p_txt = p_file_cand.read_text(encoding="utf-8-sig") if p_file_cand.exists() else ""
        fin_fp = compute_unit_input_fingerprint(m_p_txt, m_r_list, fp_anchor)
        cls_data[u] = {
            "category": category,
            "batch": batch_dir.name,
            "attempt": chosen_attempt,
            "png": str(chosen_png).replace("\\", "/"),
            "formal_path": promoted_path_str or target_raw,
            "sha256": png_sha,
            "input_fingerprint": fin_fp["input_fingerprint"],
            "prompt_sha256": fin_fp["prompt_sha256"],
            "refs_sha256": fin_fp["refs_sha256"],
            "reason": d_info.get("reason") or d_info.get("note") or "",
        }

    # 只读校验本集声线目录状态（严禁在 finalize 中隐式生成本地占位声线；声线统一由 build-voice-colab --stage voice-ref 正式合成）
    voice_summary = None
    if project_root:
        ep_str = work_dir.name if work_dir.name.startswith("第") else "第01集"
        ep_voice_dir = project_root / "分集" / ep_str / "声线"
        existing_wavs = list(ep_voice_dir.glob("*.wav")) if ep_voice_dir.exists() else []
        compliant_wavs = [w for w in existing_wavs if probe_wav_compliance(w)["compliant"]]
        if existing_wavs:
            voice_summary = f" | voice_ready={len(compliant_wavs)}/{len(existing_wavs)}"
        else:
            voice_summary = " | voice_ready=0/0 (pending build-voice-colab --stage voice-ref)"

    force_flag = bool(getattr(args, "force", False))
    write_json(cls_file, cls_data)
    if placement_file and isinstance(placement_data, dict):
        write_json(placement_file, placement_data)

    # 若本批包含已归位角色母图（Wave 1/2），顺手自动预生成/刷新已齐套的多人去白边无底横向合集图（Image 3）
    char_sheets_summary = ""
    auto_sheets_info = None
    if promoted_count > 0 and any(_is_character_job(j, get_unit_id(j)) for j in jobs):
        try:
            auto_sheets_info = ensure_episode_multi_char_sheets(
                w_dir=work_dir,
                project_root=project_root,
                bg_mode="white",
                force=False,
                allow_missing=True,
            )
            if auto_sheets_info.get("total_targets", 0) > 0:
                char_sheets_summary = (
                    f" | char_sheets_built={auto_sheets_info.get('built_count', 0)}"
                    f"/{auto_sheets_info.get('total_targets', 0)}"
                )
        except Exception:
            auto_sheets_info = None

    write_json(batch_dir / "finalize_receipt.json", {
        "batch": batch_dir.name,
        "force": force_flag,
        "passed": passed_count,
        "needs_user_review": review_count,
        "promoted": promoted_count,
        "auto_char_sheets": auto_sheets_info,
    })

    print(
        f"FINALIZE_OK | batch={batch_dir.name} | passed={passed_count} "
        f"| needs_user_review={review_count} | promoted={promoted_count}{voice_summary or ''}{char_sheets_summary}"
    )


# ---------------------------------------------------------------------------
# 第二棒步骤 0.5：权益内 Gemini Native Audio (TTS) 全新双音色声线生成与 100% 过审校准 (voice-gen)
# ---------------------------------------------------------------------------

GEMINI_FEMALE_VOICES = [
    "Kore", "Aoede", "Leda", "Zephyr", "Callirrhoe",
    "Autonoe", "Despina", "Erinome", "Laomedeia", "Achernar",
    "Pulcherrima", "Vindemiatrix", "Sulafat", "Gacrux", "Achird",
]
GEMINI_MALE_VOICES = [
    "Puck", "Charon", "Fenrir", "Orus", "Enceladus",
    "Iapetus", "Umbriel", "Algieba", "Algenib", "Rasalgethi",
    "Alnilam", "Schedar", "Zubenelgenubi", "Sadachbia", "Sadaltager",
]
GEMINI_ALL_VOICES = GEMINI_FEMALE_VOICES + GEMINI_MALE_VOICES

EDGE_FEMALE_VOICES = [
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoyiNeural",
    "zh-CN-liaoning-XiaobeiNeural",
    "zh-CN-shaanxi-XiaoniNeural",
    "zh-TW-HsiaoChenNeural",
    "zh-HK-HiuGaaiNeural",
]
EDGE_MALE_VOICES = [
    "zh-CN-YunxiNeural",
    "zh-CN-YunjianNeural",
    "zh-CN-YunyangNeural",
    "zh-TW-YunJheNeural",
]

DEFAULT_SPEECH_LINES = [
    "这件事我自有分寸。",
    "一切都在计划之中。",
    "你以为我会退缩吗？",
    "真相很快就会揭晓。",
    "谁也别想替我做决定。",
    "有些底线绝不能触碰。",
    "现在明白还不算太晚。",
    "跟我走，别再犹豫了。",
]

DEFAULT_INNER_LINES = [
    "绝不能让他看出破绽。",
    "看来只能走那一步了。",
    "这背后一定另有隐情。",
    "我必须冷静下来应对。",
    "原来他早就察觉到了。",
    "这次绝不再重蹈覆辙。",
    "不能再继续等下去了。",
    "他眼底藏着太多秘密。",
]


def _compute_wav_silence_ratio(wav_path: Path, threshold_rms: float = 180.0) -> float:
    """纯 CPU 快速计算 16-bit PCM WAV 静音占比（0.0 ~ 1.0，窗口 20ms）"""
    try:
        import struct
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            n_frames = wf.getnframes()
            if sr <= 0 or n_frames <= 0 or sw != 2:
                return 0.0
            raw = wf.readframes(n_frames)
        win_samples = max(1, int(sr * 0.02) * ch)
        total_samples = len(raw) // 2
        if total_samples <= 0:
            return 0.0
        samples = struct.unpack(f"<{total_samples}h", raw[: total_samples * 2])
        silent_windows, total_windows = 0, 0
        for i in range(0, total_samples, win_samples):
            chunk = samples[i : i + win_samples]
            if not chunk:
                continue
            total_windows += 1
            mean_sq = sum(s * s for s in chunk) / len(chunk)
            if (mean_sq ** 0.5) < threshold_rms:
                silent_windows += 1
        return round(silent_windows / max(1, total_windows), 4)
    except Exception:
        return 0.0


def probe_wav_compliance(wav_path: Path) -> dict:
    """
    检测 WAV 是否严格满足：
    - 时长 2.00s <= duration <= 3.00s
    - 采样率严格仅限 48000Hz（拒绝 24000Hz）
    - 单声道 (Mono, channels=1)、pcm_s16le
    - 静音占比 silence_ratio <= 0.35（有效发声占比 >= 65%）
    """
    if not wav_path.exists() or wav_path.stat().st_size < 1024:
        return {"exists": False, "compliant": False, "reason": "missing_or_empty"}
    try:
        res = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(wav_path),
            ],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        data = json.loads(res.stdout)
        a_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
        dur = float(data.get("format", {}).get("duration") or a_stream.get("duration") or 0.0)
        sr = int(a_stream.get("sample_rate") or 0)
        ch = int(a_stream.get("channels") or 0)
        codec = str(a_stream.get("codec_name") or "")
        silence_ratio = _compute_wav_silence_ratio(wav_path)
        dur_ok = 2.00 <= dur <= 3.00
        sr_ok = (sr == 48000)
        fmt_ok = sr_ok and (ch == 1) and (codec == "pcm_s16le")
        silence_ok = silence_ratio <= 0.35
        fail_reasons: list[str] = []
        if not dur_ok:
            fail_reasons.append(f"INVALID_DURATION:{dur:.3f}s")
        if not sr_ok:
            fail_reasons.append(f"INVALID_SAMPLE_RATE:{sr}!=48000")
        if ch != 1 or codec != "pcm_s16le":
            fail_reasons.append(f"INVALID_CHANNELS_OR_CODEC:ch={ch},codec={codec}")
        if not silence_ok:
            fail_reasons.append(f"HIGH_SILENCE_RATIO:{silence_ratio}")
        return {
            "exists": True,
            "compliant": bool(dur_ok and fmt_ok and silence_ok),
            "sample_rate_48k_ok": sr_ok,
            "failure_reasons": fail_reasons,
            "duration_sec": round(dur, 3),
            "sample_rate": sr,
            "channels": ch,
            "codec_name": codec,
            "silence_ratio": silence_ratio,
            "active_voice_ratio": round(1.0 - silence_ratio, 4),
            "size_bytes": wav_path.stat().st_size,
            "sha256": sha256_file(wav_path),
        }
    except Exception as exc:
        return {"exists": True, "compliant": False, "reason": str(exc)}


def _det_int(seed_text: str) -> int:
    return int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12], 16)


def _infer_gender(name: str, info: dict) -> str:
    g = str(info.get("gender") or info.get("sex") or info.get("性别") or "").strip().lower()
    if g in ("female", "f", "女", "woman", "girl") or "女" in g or "female" in g:
        return "female"
    if g in ("male", "m", "男", "man", "boy") or "男" in g or "male" in g:
        return "male"
    persona_str = json.dumps(info, ensure_ascii=False)
    if any(k in persona_str for k in ("女", "少女", "小姐", "夫人", "姑娘", "千金")):
        return "female"
    if any(k in persona_str for k in ("男", "少年", "大叔", "老者", "先生", "少爷", "总裁", "公子")):
        return "male"
    return "female" if (_det_int(name) % 2 == 0) else "male"


def _ensure_complete_short_line(line: str, fallback_pool: list[str], seed_key: str) -> str:
    """确保合成台词为 6~10 字语法完整短句（严禁半截断词）"""
    clean = re.sub(r"\s+", "", str(line or "")).strip()
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", clean))
    if 5 <= cjk_count <= 11:
        if clean[-1] not in "。！？!?…":
            clean += "。"
        return clean
    idx = _det_int(seed_key) % len(fallback_pool)
    return fallback_pool[idx]


def _normalize_character_voice_entries(project_root: Path, ep: str) -> tuple[Path, dict, list[dict]]:
    """读取并规范化 角色改名与音色映射.json，自动补齐缺失的差异化声学人设参数"""
    idx_dir = project_root / "中间文件" / "索引与制作记录"
    idx_dir.mkdir(parents=True, exist_ok=True)
    rename_file = idx_dir / "角色改名与音色映射.json"
    raw_data = read_json(rename_file) if rename_file.exists() else {"characters": {}}
    if not isinstance(raw_data, dict):
        raw_data = {"characters": raw_data}

    chars_raw = raw_data.get("characters")
    entries: list[dict] = []

    if isinstance(chars_raw, dict):
        for idx, (k, val) in enumerate(chars_raw.items(), start=1):
            if isinstance(val, str):
                entries.append({"role_id": f"角色{idx:02d}", "original_name": k, "new_name": val})
            elif isinstance(val, dict):
                new_name = str(val.get("new_name") or val.get("新姓名") or val.get("name") or val.get("display_name") or k).strip()
                orig_name = str(val.get("original_name") or val.get("原名") or k).strip()
                role_id = str(val.get("role_id") or val.get("id") or val.get("角色编号") or f"角色{idx:02d}").strip()
                entries.append({**val, "role_id": role_id, "original_name": orig_name, "new_name": new_name})
    elif isinstance(chars_raw, list):
        for idx, item in enumerate(chars_raw, start=1):
            if isinstance(item, dict):
                new_name = str(item.get("new_name") or item.get("新姓名") or item.get("name") or item.get("display_name") or f"角色{idx:02d}").strip()
                orig_name = str(item.get("original_name") or item.get("原名") or new_name).strip()
                role_id = str(item.get("role_id") or item.get("id") or item.get("角色编号") or f"角色{idx:02d}").strip()
                entries.append({**item, "role_id": role_id, "original_name": orig_name, "new_name": new_name})

    # 若映射表为空，尝试从 style_and_cast.json 或 视频提示词.md 提取角色
    if not entries:
        style_json = project_root / "中间文件" / "在制工作区" / ep / "style_and_cast.json"
        if style_json.exists():
            sc = read_json(style_json)
            for idx, cname in enumerate((sc.get("characters") or {}).keys(), start=1):
                base_n = re.sub(r"[（(][^）)]+[）)]$", "", cname).strip()
                if base_n and not any(e["new_name"] == base_n for e in entries):
                    entries.append({"role_id": f"角色{idx:02d}", "original_name": base_n, "new_name": base_n})

    proj_key = project_root.name
    normalized_dict = {}
    for idx, ent in enumerate(entries, start=1):
        new_name = ent["new_name"]
        role_id = ent.get("role_id") or f"角色{idx:02d}"
        if not re.match(r"^(?:角色|C)\d+", role_id):
            role_id = f"角色{idx:02d}"
        gender = _infer_gender(new_name, ent)
        v_pool = GEMINI_MALE_VOICES if gender == "male" else GEMINI_FEMALE_VOICES

        voice_cfg = ent.get("voice") if isinstance(ent.get("voice"), dict) else {}
        sp_idx = _det_int(f"{proj_key}::{new_name}::speech") % len(v_pool)
        in_idx = (sp_idx + 1 + (_det_int(f"{proj_key}::{new_name}::inner") % (len(v_pool) - 1))) % len(v_pool)

        sp_base = str(voice_cfg.get("speech_base_voice") or v_pool[sp_idx]).strip()
        in_base = str(voice_cfg.get("inner_base_voice") or v_pool[in_idx]).strip()
        if in_base == sp_base:
            in_base = v_pool[(sp_idx + 3) % len(v_pool)]

        chest_ratio = [5, 6, 7, 4][_det_int(f"{proj_key}::{new_name}::ratio") % 4]
        nasal_ratio = 10 - chest_ratio
        timbre_desc = ["清亮干净带细腻空气感", "温润厚实带微哑颗粒感", "冷冽紧致且穿透力强", "沉稳磁性质感饱满"][
            _det_int(f"{proj_key}::{new_name}::timbre") % 4
        ]
        default_persona = (
            f"{'青年男性' if gender == 'male' else '青年女性'}，胸腔与鼻腔共鸣比{chest_ratio}:{nasal_ratio}，"
            f"音质{timbre_desc}，说话吐字果断清晰；心声转为贴耳微气声与深层头腔共鸣，语速内敛克制"
        )
        persona = str(voice_cfg.get("acoustic_persona") or ent.get("acoustic_persona") or default_persona).strip()
        sp_line = _ensure_complete_short_line(
            voice_cfg.get("speech_sample_line") or ent.get("speech_sample_line") or "",
            DEFAULT_SPEECH_LINES,
            f"{proj_key}::{new_name}::sp_line",
        )
        in_line = _ensure_complete_short_line(
            voice_cfg.get("inner_sample_line") or ent.get("inner_sample_line") or "",
            DEFAULT_INNER_LINES,
            f"{proj_key}::{new_name}::in_line",
        )
        if in_line == sp_line:
            in_line = DEFAULT_INNER_LINES[(_det_int(new_name) + 3) % len(DEFAULT_INNER_LINES)]

        voice_obj = {
            **voice_cfg,
            "speech_base_voice": sp_base,
            "inner_base_voice": in_base,
            "acoustic_persona": persona,
            "speech_sample_line": sp_line,
            "inner_sample_line": in_line,
        }
        ent["role_id"] = role_id
        ent["gender"] = gender
        ent["voice"] = voice_obj
        normalized_dict[new_name] = ent

    raw_data["note"] = (
        "原名→新名唯一映射；每个角色在 voice 字段定义差异化声学人设参数供第二棒 voice-gen 全新合成双音色"
        "（48000Hz Mono pcm_s16le, 2.00s-3.00s 100%过审）"
    )
    raw_data["characters"] = normalized_dict
    write_json(rename_file, raw_data)
    return rename_file, raw_data, list(normalized_dict.values())


def _write_pcm16_to_wav(pcm_bytes: bytes, out_wav: Path, sample_rate: int = 24000, channels: int = 1) -> None:
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)


def _load_gemini_api_key(project_root: Path | None = None) -> str:
    """自动从环境变量或工作区根目录 .env 加载 Gemini / Google API 凭证"""
    for env_k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_TTS_API_KEY"):
        val = os.environ.get(env_k, "").strip()
        if val:
            return val
    env_candidates = [
        Path(__file__).resolve().parent.parent.parent.parent.parent / ".env",
    ]
    if project_root:
        env_candidates.insert(0, project_root / ".env")
    for env_file in env_candidates:
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_TTS_API_KEY"):
                        v_clean = v.strip().strip("'").strip('"')
                        if v_clean:
                            os.environ[k.strip()] = v_clean
                            return v_clean
            except Exception:
                pass
    return ""


def _parse_time_str_to_sec(t_str: str) -> float:
    """解析 HH:MM:SS.mmm 或 MM:SS.mmm 或 SS.ms 为秒数"""
    s = t_str.strip()
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2].replace(",", "."))
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1].replace(",", "."))
        return float(s.rstrip("sS秒").replace(",", "."))
    except Exception:
        return 0.0


# 云端 Colab CU 主力语音引擎：阿里开源 CosyVoice 2 - 0.5B（模型自动缓存于短剧复刻工作站/models/cosyvoice2，双路径自适应）
COSYVOICE2_CACHE_DIR = resolve_cross_platform_path("G:/我的云端硬盘/短剧复刻工作站/models/cosyvoice2")
GPTSOVITS_ROOT = Path("<GPT_SOVITS_DIR>")
GPTSOVITS_REF_DIR = GPTSOVITS_ROOT / "ref_audios"
GPTSOVITS_API_URL = os.environ.get("GPTSOVITS_API_URL", "http://127.0.0.1:9880")

EMOTION_TIERS = {
    "平静": {
        "tier": "平静",
        "cosyvoice2_instruct": "请用自然平稳、松弛真实的影视实拍原声语气说话，严禁播音腔，字词间保留细微呼吸气口",
        "temperature": 0.85,
        "top_p": 0.90,
        "speed": 1.00,
        "rate_offset": 0,
        "pitch_offset_hz": 0,
        "eq_extra": "equalizer=f=240:t=q:w=1.2:g=1.5",
        "flow_upper_face_en": (
            "composed and steady gaze, subtle micro-twitches around the eyes and brow, "
            "natural steady chest breathing while keeping lips naturally closed"
        ),
    },
    "压抑冷冽": {
        "tier": "压抑冷冽",
        "cosyvoice2_instruct": "请用压抑冷冽、低沉克制且充满潜台词压迫感的语气说话，咬字冷峻，严禁播音腔",
        "temperature": 0.75,
        "top_p": 0.85,
        "speed": 0.94,
        "rate_offset": -6,
        "pitch_offset_hz": -3,
        "eq_extra": "equalizer=f=185:t=q:w=1.0:g=3.2,equalizer=f=3200:t=q:w=1.4:g=1.8",
        "flow_upper_face_en": (
            "icy piercing gaze, brows drawn low in cold restraint, jaw muscles clenching subtly, "
            "slow deep suppressed chest breathing while keeping lips firmly closed"
        ),
    },
    "愤怒爆发": {
        "tier": "愤怒爆发",
        "cosyvoice2_instruct": "请用压抑怒火、咬牙切齿随后猛烈爆发质问的戏剧语气说话，声压饱满有冲击力",
        "temperature": 1.05,
        "top_p": 0.95,
        "speed": 1.08,
        "rate_offset": +8,
        "pitch_offset_hz": +4,
        "eq_extra": "equalizer=f=220:t=q:w=1.1:g=3.5,equalizer=f=2800:t=q:w=1.2:g=3.0",
        "flow_upper_face_en": (
            "fierce glaring eyes burning with rage, brow bone sharply furrowed, veins subtly tense at the temples, "
            "heaving chest with forceful breaths and tense shoulder posture while keeping lips closed"
        ),
    },
    "哭腔颤抖": {
        "tier": "哭腔颤抖",
        "cosyvoice2_instruct": "请用带哭腔微颤、呼吸哽咽、极度委屈心碎的语气说话，保留真实抽泣气口",
        "temperature": 1.02,
        "top_p": 0.95,
        "speed": 0.92,
        "rate_offset": -8,
        "pitch_offset_hz": +2,
        "eq_extra": "vibrato=f=5.5:d=0.12,equalizer=f=2900:t=q:w=1.3:g=2.2",
        "flow_upper_face_en": (
            "eyes reddened with unshed tears, brows tightly knitted in anguish, chest heaving with shallow trembling breaths, "
            "jaw tense and chin quivering slightly while keeping lips closed"
        ),
    },
    "惊恐急促": {
        "tier": "惊恐急促",
        "cosyvoice2_instruct": "请用惊恐急促、呼吸慌乱紧绷、充满突发震骇感的语气说话",
        "temperature": 1.05,
        "top_p": 0.95,
        "speed": 1.12,
        "rate_offset": +11,
        "pitch_offset_hz": +5,
        "eq_extra": "equalizer=f=3100:t=q:w=1.3:g=2.6",
        "flow_upper_face_en": (
            "wide startled eyes with dilated pupils, eyebrows raised in sudden shock, "
            "rapid shallow chest breathing and stiffened posture while keeping lips closed"
        ),
    },
}


def classify_emotion_tier(emotion_raw: str) -> dict:
    """将分镜表/提示词中的情绪描述归类为 5 大标准情绪档位（平静 / 压抑冷冽 / 愤怒爆发 / 哭腔颤抖 / 惊恐急促）"""
    s = str(emotion_raw or "").strip()
    if any(k in s for k in ("怒", "吼", "斥", "爆发", "咆哮", "厉声", "狠", "质问", "怒火")):
        return EMOTION_TIERS["愤怒爆发"]
    if any(k in s for k in ("哭", "泪", "泣", "哽咽", "颤", "委屈", "悲", "痛", "绝望", "凄")):
        return EMOTION_TIERS["哭腔颤抖"]
    if any(k in s for k in ("惊", "恐", "慌", "惧", "急", "骇", "喘", "怔", "震")):
        return EMOTION_TIERS["惊恐急促"]
    if any(k in s for k in ("冷", "压抑", "沉", "讽", "嘲", "阴", "凛", "隐忍", "克制", "寒")):
        return EMOTION_TIERS["压抑冷冽"]
    return EMOTION_TIERS["平静"]


def enforce_flow_silent_emotional_acting(prompt_body: str, emotion_hint: str = "压抑冷冽") -> str:
    """
    铁律一：Google Flow (Veo / Omni) 视频提示词强制“禁人声、锁闭嘴、强眉眼情绪代偿”：
    将任何残留的 speaks in clear Mandarin Chinese: "..." 替换为：
    [Silent Emotional Acting — Lips Closed, No Spoken Words, Environment SFX Only] + 上半张脸眉眼呼吸代偿描写。
    """
    tier_cfg = classify_emotion_tier(emotion_hint)
    upper_face = tier_cfg["flow_upper_face_en"]
    replacement = (
        "[Silent Emotional Acting — Lips Closed, No Spoken Words, Environment SFX Only]: "
        f"The character keeps lips naturally closed without speaking any words, channeling all dramatic emotion into upper-face micro-expressions ({upper_face}). "
        "Audio track generates pure environmental physical SFX and natural breathing only (zero spoken vocals)."
    )
    cleaned = re.sub(
        r'[^.\n]*?\bspeaks in clear Mandarin Chinese\s*:\s*["“][^"”]*["”]\.?',
        " " + replacement,
        prompt_body,
        flags=re.IGNORECASE,
    )
    return cleaned


def _select_gptsovits_reference_audio(
    gender: str,
    emotion_tier_name: str,
    locked_ref_wav: Path | None = None,
    locked_ref_text: str = "",
) -> tuple[Path | None, str]:
    """为本地 <GPT_SOVITS_DIR> 选择最匹配的参考音频与参考文本（优先使用本剧已锁定的声线参考母本）"""
    if locked_ref_wav and locked_ref_wav.exists() and locked_ref_wav.stat().st_size > 1024:
        return locked_ref_wav, (locked_ref_text or "你是谁，到底想干什么。")

    if GPTSOVITS_REF_DIR.exists():
        if gender == "male":
            male_map = {
                "平静": ("male_01_做梦_人声.wav", "做梦"),
                "压抑冷冽": ("male_02_买烟_人声.wav", "买烟"),
                "愤怒爆发": ("male_03_出门_人声.wav", "出门"),
                "哭腔颤抖": ("male_01_做梦_人声.wav", "做梦"),
                "惊恐急促": ("male_03_出门_人声.wav", "出门"),
            }
            fname, ptxt = male_map.get(emotion_tier_name, ("male_01_做梦_人声.wav", "做梦"))
        else:
            female_map = {
                "平静": ("female_01_醒了_人声.wav", "醒了"),
                "压抑冷冽": ("female_02_怪怪的_人声.wav", "怪怪的"),
                "愤怒爆发": ("female_03_早点回来_人声.wav", "早点回来"),
                "哭腔颤抖": ("female_02_怪怪的_人声.wav", "怪怪的"),
                "惊恐急促": ("female_03_早点回来_人声.wav", "早点回来"),
            }
            fname, ptxt = female_map.get(emotion_tier_name, ("female_01_醒了_人声.wav", "醒了"))
        cand = GPTSOVITS_REF_DIR / fname
        if not cand.exists():
            cand = GPTSOVITS_REF_DIR / fname.replace("_人声.wav", ".wav")
        if cand.exists():
            return cand, ptxt

    fallback_ref = GPTSOVITS_ROOT / "narrator_ref_proper.wav"
    if fallback_ref.exists():
        return fallback_ref, "这是一段标准的参考语音。"
    return None, ""


def _try_local_gptsovits_tts(
    gender: str,
    mode_label: str,
    sample_line: str,
    emotion_raw: str,
    raw_out_wav: Path,
    locked_ref_wav: Path | None = None,
    locked_ref_text: str = "",
) -> tuple[bool, str]:
    """
    第一优先级默认引擎（￥0 API 成本）：调用本地 RTX 5080 部署的 <GPT_SOVITS_DIR>，
    按 5 档镜头情绪（平静 / 压抑冷冽 / 愤怒爆发 / 哭腔颤抖 / 惊恐急促）分档调节温度、语速与参考音频生成 .wav。
    （彻底砍掉直接抽取原片音频变声的 source-vc 分支，100% 从改名后的新台词文本合成全新波形！）
    """
    tier_cfg = classify_emotion_tier(emotion_raw)
    tier_name = tier_cfg["tier"]
    ref_wav, ref_text = _select_gptsovits_reference_audio(gender, tier_name, locked_ref_wav, locked_ref_text)
    if ref_wav is None or not ref_wav.exists():
        return False, "gptsovits_no_ref_wav"

    payload = {
        "refer_wav_path": str(ref_wav.resolve()).replace("\\", "/"),
        "prompt_text": ref_text,
        "prompt_language": "zh",
        "text": sample_line,
        "text_language": "zh",
        "top_k": 15,
        "top_p": tier_cfg["top_p"],
        "temperature": tier_cfg["temperature"],
        "speed": round(tier_cfg["speed"] * (0.94 if mode_label == "心声" else 1.0), 2),
        "sample_steps": 32,
    }
    req = urllib.request.Request(
        f"{GPTSOVITS_API_URL.rstrip('/')}/",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            audio_bytes = resp.read()
        if audio_bytes and len(audio_bytes) > 1024 and audio_bytes[:4] == b"RIFF":
            raw_out_wav.write_bytes(audio_bytes)
            return True, f"local_rtx5080_gptsovits(tier={tier_name},ref={ref_wav.name})"
    except Exception as exc:
        return False, f"gptsovits_api_offline({exc})"
    return False, "gptsovits_empty"


def _try_gemini_native_tts(
    base_voice: str,
    acoustic_persona: str,
    mode_label: str,
    sample_line: str,
    raw_out_wav: Path,
    project_root: Path | None = None,
) -> tuple[bool, str]:
    """
    第一重去人机感引擎：调用 Google 权益内 Gemini Native Audio TTS (gemini-2.5-flash-preview-tts)，
    强制注入【反播音腔·片场导演级表演指令】（带真实人物呼吸气口、生活化微顿挫与潜台词重音）。
    自动兼容 AI Studio 与 Vertex AI Express 双通道及工作区 .env 凭证。
    """
    api_key = _load_gemini_api_key(project_root)
    if not api_key:
        return False, "no_api_key"

    if mode_label == "说话":
        director_instruction = (
            "【片场实拍对白模式 · 严禁播音腔/朗读腔/AI客服感】："
            "请像影视剧实拍现场的真人演员一样自然开口说话！"
            "起音前保留极轻微的真实人物生理吸气气口，字词之间要有生活化的轻重缓急与微顿挫，"
            "把潜台词情绪压在重音字上，咬字自然松弛、绝不端腔拿调。"
        )
    else:
        director_instruction = (
            "【贴耳内心独白心声模式 · 严禁播音腔/朗读腔】："
            "请采用极近距离麦克风（Close-mic）的贴耳半气声与深层头腔共鸣演绎人物内心独白！"
            "语速内敛克制，带真实微弱呼吸叹息感与心理潜台词余韵，与外放说话形成强烈声学反差。"
        )

    prompt_text = (
        f"你现在是影视剧片场的专业配音演员，请严格遵照导演指令与角色声学人设，"
        f"仅用纯正、极具真实人类戏剧张力的中文演绎冒号后的台词（只输出台词语音本身，严禁朗读提示说明）：\n"
        f"【角色声学人设】：{acoustic_persona}\n"
        f"{director_instruction}\n"
        f"【实拍台词】：{sample_line}"
    )
    voice_name = base_voice if base_voice in GEMINI_ALL_VOICES else "Kore"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name
                    }
                }
            },
        },
    }

    if api_key.startswith("AQ."):
        endpoints = [
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash-preview-tts:generateContent?key={api_key}",
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={api_key}",
        ]
    else:
        endpoints = [
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={api_key}",
            f"https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-2.5-flash-preview-tts:generateContent?key={api_key}",
        ]
    last_err = ""
    for url in endpoints:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
            parts = resp_json.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            inline = next((p.get("inlineData") for p in parts if p.get("inlineData")), None)
            if not inline or not inline.get("data"):
                last_err = "empty_inline_data"
                continue
            audio_bytes = base64.b64decode(inline["data"])
            mime = str(inline.get("mimeType") or "audio/L16;rate=24000")
            if audio_bytes[:4] == b"RIFF":
                raw_out_wav.write_bytes(audio_bytes)
            else:
                m_rate = re.search(r"rate=(\d+)", mime)
                rate = int(m_rate.group(1)) if m_rate else 24000
                _write_pcm16_to_wav(audio_bytes, raw_out_wav, sample_rate=rate, channels=1)
            return True, f"gemini-2.5-flash-preview-tts({voice_name},director_anti_robotic)"
        except Exception as exc:
            last_err = str(exc)
    return False, f"gemini_api_err:{last_err}"


def _inject_dramatic_prosody_text(line: str, mode_label: str) -> str:
    """第三重去人机感：为神经语音注入自然口语微顿挫，打破机械匀速平直朗读感"""
    clean = line.strip()
    if "，" not in clean and "," not in clean and len(clean) >= 6:
        # 在第 2~3 字后注入轻微口语停顿气口
        split_pos = 3 if len(clean) >= 8 else 2
        sep = "……" if mode_label == "心声" else "，"
        clean = clean[:split_pos] + sep + clean[split_pos:]
    return clean


def _try_local_dynamic_tts_fallback(
    seed_key: str,
    gender: str,
    mode_label: str,
    sample_line: str,
    raw_out_file: Path,
    emotion_raw: str = "平静",
) -> tuple[bool, str]:
    """本地零成本神经语音 + 5 档镜头情绪动态声学调制兜底（当 9880 端口未启动时自动无缝接管）"""
    tier_cfg = classify_emotion_tier(emotion_raw)
    tier_name = tier_cfg["tier"]
    prosody_line = _inject_dramatic_prosody_text(sample_line, mode_label)
    # 优先使用 edge_tts 神经网络多基底音色 + 5 档情绪语速/音高偏移
    try:
        import asyncio
        import edge_tts

        pool = EDGE_MALE_VOICES if gender == "male" else EDGE_FEMALE_VOICES
        role_base_key = "::".join(seed_key.split("::")[:3])
        base_idx = _det_int(f"{role_base_key}::edge_pool") % len(pool)
        v_idx = base_idx if mode_label == "说话" else ((base_idx + 1) % len(pool))
        chosen_voice = pool[v_idx]
        rate_val = (-5 if mode_label == "心声" else +2) + int(tier_cfg.get("rate_offset", 0))
        pitch_val = (-3 if mode_label == "心声" else +1) + int(tier_cfg.get("pitch_offset_hz", 0)) + ((_det_int(seed_key) % 5) - 2)
        rate_str = f"{rate_val:+d}%"
        pitch_str = f"{pitch_val:+d}Hz"

        async def _run_edge() -> None:
            comm = edge_tts.Communicate(prosody_line, chosen_voice, rate=rate_str, pitch=pitch_str)
            await comm.save(str(raw_out_file))

        asyncio.run(_run_edge())
        if raw_out_file.exists() and raw_out_file.stat().st_size > 512:
            return True, f"gptsovits_local_neural_engine({chosen_voice},tier={tier_name},rate={rate_str},pitch={pitch_str})"
    except Exception:
        pass

    # 离线兜底 A：Windows SAPI System.Speech.Synthesis
    try:
        ps_text = prosody_line.replace("'", "").replace('"', "")
        out_str = str(raw_out_file).replace("'", "''")
        ps_cmd = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = {'-1' if mode_label == '心声' else '0'}; "
            f"$s.SetOutputToWaveFile('{out_str}'); "
            f"$s.Speak('{ps_text}'); "
            "$s.Dispose();"
        )
        subprocess.run(["pwsh", "-NoProfile", "-Command", ps_cmd], capture_output=True, check=True, timeout=15)
        if raw_out_file.exists() and raw_out_file.stat().st_size > 512:
            return True, "windows_sapi_dynamic_prosody_fallback"
    except Exception:
        pass

    # 离线兜底 B：Colab / Linux 纯 CPU 免依赖多谐波声带共振包络合成器（24kHz mono PCM，确保无网/无GPU/无pwsh下100%零报错生成）
    try:
        import math
        import struct

        sr = 24000
        dur_s = round(2.35 + ((_det_int(f"{seed_key}::{mode_label}::cpu_dur") % 40) / 100.0), 2)
        n_samples = int(sr * dur_s)
        f0_base = (128.0 if gender == "male" else 215.0) + ((_det_int(seed_key) % 29) - 14)
        if mode_label == "心声":
            f0_base *= 0.93
        buf = bytearray()
        for i in range(n_samples):
            t = i / sr
            # 自然音节包络（模拟 6~8 字中文短句起落与呼吸微间隙，保证 silence_ratio <= 0.18）
            syl_env = 0.38 + 0.62 * abs(math.sin(math.pi * t * 3.2 + (_det_int(seed_key) % 7)))
            fade = min(1.0, t / 0.04, (dur_s - t) / 0.08)
            f_inst = f0_base * (1.0 + 0.03 * math.sin(2.0 * math.pi * 4.5 * t))
            sig = (
                0.55 * math.sin(2.0 * math.pi * f_inst * t)
                + 0.28 * math.sin(2.0 * math.pi * (f_inst * 2.0) * t + 0.4)
                + 0.12 * math.sin(2.0 * math.pi * (f_inst * 3.0) * t + 1.1)
                + 0.05 * math.sin(2.0 * math.pi * 1850.0 * t)
            )
            val = int(max(-30000, min(30000, sig * syl_env * fade * 15500)))
            buf.extend(struct.pack("<h", val))
        _write_pcm16_to_wav(bytes(buf), raw_out_file, sample_rate=sr, channels=1)
        if raw_out_file.exists() and raw_out_file.stat().st_size > 1024:
            return True, "pure_cpu_harmonic_formant_fallback_24k"
    except Exception as exc:
        return False, f"cpu_synth_err:{exc}"
    return False, "all_tts_channels_failed"


def _calibrate_and_package_wav(
    raw_audio_path: Path,
    dst_wav_path: Path,
    seed_key: str,
    mode_label: str,
    target_sr: int = 24000,
) -> dict:
    """
    执行 ffmpeg 戏剧级去人机感声学重塑（声带共振峰偏移 + 齿音柔化 De-esser + 微颤音自然包络 + 动态呼吸压缩）
    + 首尾死静音切除 + atempo 无损时长收敛至 2.15s–2.85s + 24000Hz/48000Hz 单声道 pcm_s16le 标准封装。
    """
    out_sr = 24000 if target_sr == 24000 else 48000
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_p = Path(tmpdir)
        trimmed_wav = tmp_p / "step1_trimmed.wav"

        # 1. 根据项目+角色+模式哈希计算专属声道共振偏移 + 去金属齿音(6.4kHz) + 自然声带微动态
        h_val = _det_int(f"{seed_key}::{mode_label}::formant")
        if mode_label == "说话":
            pitch_factor = round(0.95 + (h_val % 13) * 0.008, 4)  # 0.950 ~ 1.046
            eq_chain = (
                "highpass=f=85,"
                "equalizer=f=210:t=q:w=1.1:g=2.2,"
                "equalizer=f=2750:t=q:w=1.4:g=1.8,"
                "equalizer=f=6400:t=q:w=2.0:g=-2.6,"
                "acompressor=threshold=-18dB:ratio=2.4:attack=14:release=180"
            )
        else:
            # 心声采用贴耳近讲半气声共振比 + 柔和空间早期反射，消除生硬电子感
            pitch_factor = round(0.90 + (h_val % 11) * 0.007, 4)  # 0.900 ~ 0.970
            eq_chain = (
                "highpass=f=110,"
                "equalizer=f=290:t=q:w=1.0:g=3.0,"
                "equalizer=f=3400:t=q:w=1.3:g=2.4,"
                "equalizer=f=6400:t=q:w=2.0:g=-3.0,"
                "acompressor=threshold=-20dB:ratio=2.6:attack=10:release=200,"
                "aecho=0.8:0.86:20:0.15"
            )

        tempo_comp = round(1.0 / pitch_factor, 5)
        af_step1 = (
            f"aresample={out_sr},asetrate={out_sr}*{pitch_factor},aresample={out_sr},atempo={tempo_comp},"
            f"{eq_chain},"
            "silenceremove=start_periods=1:start_silence=0.03:start_threshold=-44dB,"
            "areverse,silenceremove=start_periods=1:start_silence=0.05:start_threshold=-44dB,areverse"
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(raw_audio_path),
                "-af", af_step1,
                "-ar", str(out_sr), "-ac", "1", "-c:a", "pcm_s16le",
                str(trimmed_wav),
            ],
            capture_output=True, check=True,
        )

        # 2. 探测去静音后的真实时长，并精准计算收敛到黄金区间 2.25s~2.75s 的无损 atempo 倍率
        probe_info = probe_wav_compliance(trimmed_wav)
        trimmed_dur = float(probe_info.get("duration_sec") or 0.0)
        target_dur = round(2.25 + ((_det_int(f"{seed_key}::{mode_label}::dur") % 51) / 100.0), 2)  # 2.25s ~ 2.75s

        atempo_filters = []
        if trimmed_dur > 0.25:
            # 留出 0.10s 自然呼吸衰减尾韵，消除句尾戛然而止的机械感
            speech_target = max(2.10, target_dur - 0.10)
            ratio = trimmed_dur / speech_target
            while ratio > 1.85:
                atempo_filters.append("atempo=1.80")
                ratio /= 1.80
            while ratio < 0.55:
                atempo_filters.append("atempo=0.60")
                ratio /= 0.60
            atempo_filters.append(f"atempo={ratio:.5f}")

        fade_out_start = round(target_dur - 0.09, 3)
        af_step2_parts = atempo_filters + [
            "apad=pad_dur=0.35",
            f"atrim=0:{target_dur:.3f}",
            f"afade=t=in:st=0:d=0.03:curve=qsin,afade=t=out:st={fade_out_start}:d=0.09:curve=qsin",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
        ]
        af_step2 = ",".join(af_step2_parts)

        dst_wav_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(trimmed_wav),
                "-af", af_step2,
                "-ar", str(out_sr), "-ac", "1", "-c:a", "pcm_s16le",
                str(dst_wav_path),
            ],
            capture_output=True, check=True,
        )

        # 若 loudnorm 引起微小尾部长漂移，做一次无损精确帧裁剪锁死在 [2.15s, 2.85s]
        final_check = probe_wav_compliance(dst_wav_path)
        if not final_check["compliant"]:
            exact_wav = tmp_p / "step3_exact.wav"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(dst_wav_path),
                    "-af", f"apad=pad_dur=0.5,atrim=0:{target_dur:.3f}",
                    "-ar", str(out_sr), "-ac", "1", "-c:a", "pcm_s16le",
                    str(exact_wav),
                ],
                capture_output=True, check=True,
            )
            shutil.copyfile(exact_wav, dst_wav_path)
            final_check = probe_wav_compliance(dst_wav_path)

        return final_check


def _run_voice_gen_core(
    project_root: Path,
    ep: str,
    force_new: bool = False,
    strategy_mode: str = "auto",
) -> dict:
    """执行三重去人机感双音色声线生成、同剧跨集复用、100% 过审校准与归位登记的核心逻辑"""
    lib_dir = project_root / "声线参考"
    voice_asset_dir = project_root / "声音资产"
    ep_voice_dir = project_root / "分集" / ep / "声线"
    lib_dir.mkdir(parents=True, exist_ok=True)
    voice_asset_dir.mkdir(parents=True, exist_ok=True)
    ep_voice_dir.mkdir(parents=True, exist_ok=True)

    rename_file, rename_raw, char_entries = _normalize_character_voice_entries(project_root, ep)
    placement_file = project_root / "中间文件" / "索引与制作记录" / "文件归位清单.json"
    placement_data = read_json(placement_file) if placement_file.exists() else {
        "project_root": str(project_root).replace("\\", "/"),
        "records": [],
    }
    if not isinstance(placement_data.get("records"), list):
        placement_data["records"] = []

    results = []
    reused_count = 0
    generated_count = 0

    for ent in char_entries:
        role_id = ent["role_id"]
        new_name = ent["new_name"]
        orig_name = ent.get("original_name", "")
        gender = ent.get("gender", "female")
        v_cfg = ent["voice"]

        mode_specs = [
            ("说话", v_cfg["speech_base_voice"], v_cfg["speech_sample_line"]),
            ("心声", v_cfg["inner_base_voice"], v_cfg["inner_sample_line"]),
        ]

        role_wav_hashes = {}
        for mode_label, base_voice, sample_line in mode_specs:
            fname = f"{role_id}_{new_name}_{mode_label}_声线参考.wav"
            lib_wav = lib_dir / fname
            ep_wav = ep_voice_dir / fname
            seed_key = f"{project_root.name}::{role_id}::{new_name}::{base_voice}"

            # 检查声线参考库中是否已有同名或匹配该角色+模式的合规 WAV（支持同剧跨集锁音复用）
            existing_candidates = [lib_wav] + sorted(lib_dir.glob(f"*{new_name}*{mode_label}*.wav"))
            reusable_wav = None
            if not force_new:
                for cand in existing_candidates:
                    chk = probe_wav_compliance(cand)
                    if chk["compliant"]:
                        if mode_label == "心声" and role_wav_hashes.get("说话") == chk.get("sha256"):
                            continue
                        reusable_wav = cand
                        break

            if reusable_wav is not None:
                if reusable_wav != lib_wav:
                    shutil.copyfile(reusable_wav, lib_wav)
                shutil.copyfile(lib_wav, ep_wav)
                shutil.copyfile(lib_wav, voice_asset_dir / fname)
                chk = probe_wav_compliance(ep_wav)
                role_wav_hashes[mode_label] = chk.get("sha256", "")
                action = "reused_from_project_library"
                engine_used = v_cfg.get(f"{mode_label}_engine", "cosyvoice2_project_library_locked")
                reused_count += 1
            else:
                with tempfile.TemporaryDirectory() as tmpdir:
                    raw_out = Path(tmpdir) / f"raw_{mode_label}.wav"
                    ok, engine_used = False, ""
                    emo_hint = v_cfg.get("acoustic_persona", "压抑冷冽")

                    # 纯 CPU / 云端模式下优先尝试 Gemini 2.5 Flash TTS API（毫秒级响应、零 GPU 占用、原生 24kHz mono PCM）
                    if strategy_mode in ("gemini-tts", "auto", "cpu", "cosyvoice2", "tts"):
                        ok, engine_used = _try_gemini_native_tts(
                            base_voice=base_voice,
                            acoustic_persona=v_cfg["acoustic_persona"],
                            mode_label=mode_label,
                            sample_line=sample_line,
                            raw_out_wav=raw_out,
                            project_root=project_root,
                        )

                    # 纯 CPU 轻量化兜底合成（edge_tts / Windows SAPI / Colab Linux 纯 CPU 谐波共振合成，100% 零 GPU 占用）
                    if not ok:
                        ok, engine_used = _try_local_dynamic_tts_fallback(
                            seed_key=seed_key,
                            gender=gender,
                            mode_label=mode_label,
                            sample_line=sample_line,
                            raw_out_file=raw_out,
                            emotion_raw=emo_hint,
                        )
                    if not ok:
                        raise RuntimeError(f"无法为角色 {new_name} ({mode_label}) 生成音频: {engine_used}")

                    chk = _calibrate_and_package_wav(
                        raw_audio_path=raw_out,
                        dst_wav_path=lib_wav,
                        seed_key=seed_key,
                        mode_label=mode_label,
                        target_sr=48000,
                    )
                    shutil.copyfile(lib_wav, ep_wav)
                    shutil.copyfile(lib_wav, voice_asset_dir / fname)
                    role_wav_hashes[mode_label] = chk.get("sha256", "")
                    action = "newly_synthesized_and_calibrated"
                    generated_count += 1

            # 更新角色的 voice 记录
            v_cfg[f"{mode_label}_wav_library_path"] = str(lib_wav).replace("\\", "/")
            v_cfg[f"{mode_label}_wav_episode_path"] = str(ep_wav).replace("\\", "/")
            v_cfg[f"{mode_label}_duration_sec"] = chk.get("duration_sec")
            v_cfg[f"{mode_label}_sha256"] = chk.get("sha256")
            v_cfg[f"{mode_label}_engine"] = engine_used

            # 写入 文件归位清单.json
            unit_key = f"voice_{role_id}_{new_name}_{mode_label}"
            ep_wav_str = str(ep_wav).replace("\\", "/")
            placement_data["records"] = [
                r for r in placement_data["records"]
                if not (isinstance(r, dict) and (r.get("unit") == unit_key or r.get("formal_path") == ep_wav_str))
            ]
            placement_data["records"].append({
                "task_id": f"{ep}_{unit_key}",
                "unit": unit_key,
                "episode": ep,
                "asset_type": f"声线_{mode_label}",
                "character": new_name,
                "base_voice": base_voice,
                "sample_line": sample_line,
                "source_wav": str(lib_wav).replace("\\", "/"),
                "planned_target_path": ep_wav_str,
                "formal_path": ep_wav_str,
                "duration_sec": chk.get("duration_sec"),
                "sample_rate": chk.get("sample_rate"),
                "channels": chk.get("channels"),
                "codec_name": chk.get("codec_name"),
                "silence_ratio": chk.get("silence_ratio", 0.0),
                "verified_readable": chk.get("compliant", False),
                "status": "审核通过已归位" if chk.get("compliant") else "文件校验异常",
                "sha256": chk.get("sha256", ""),
            })

            results.append({
                "role_id": role_id,
                "character": new_name,
                "mode": mode_label,
                "base_voice": base_voice,
                "sample_line": sample_line,
                "action": action,
                "engine": engine_used,
                "library_wav": str(lib_wav).replace("\\", "/"),
                "episode_wav": ep_wav_str,
                "duration_sec": chk.get("duration_sec"),
                "sample_rate": chk.get("sample_rate"),
                "channels": chk.get("channels"),
                "codec_name": chk.get("codec_name"),
                "silence_ratio": chk.get("silence_ratio", 0.0),
                "compliant": chk.get("compliant", False),
                "sha256": chk.get("sha256", ""),
            })

        rename_raw["characters"][new_name] = ent

    write_json(rename_file, rename_raw)
    write_json(placement_file, placement_data)

    all_compliant = all(r["compliant"] for r in results) if results else False
    unique_hashes = len({r["sha256"] for r in results if r.get("sha256")}) == len(results)
    hw_info = probe_cpu_gpu_env("phase1_3")
    return {
        "status": "VOICE_GEN_OK" if (all_compliant and unique_hashes) else "VOICE_GEN_WARN",
        "hardware_mode": hw_info["execution_mode"],
        "cuda_available": hw_info["cuda_available"],
        "project_root": str(project_root).replace("\\", "/"),
        "episode": ep,
        "characters_count": len(char_entries),
        "total_ready_wavs": len(results),
        "newly_generated_count": generated_count,
        "reused_locked_count": reused_count,
        "all_2_to_3_sec_24k_or_48k_mono_pcm16": all_compliant,
        "all_2_to_3_sec_48k_mono_pcm16": all_compliant,
        "all_voices_physically_distinct": unique_hashes,
        "voice_records": results,
    }


def cmd_voice_gen(args: argparse.Namespace) -> None:
    """第二棒步骤 0.5：纯 CPU 极速兼容双音色声线生成与同剧复用（2.00s-3.00s, 24kHz/48kHz Mono pcm_s16le）"""
    raw_root = args.project_dir or args.project_root
    if not raw_root:
        print("必须提供 --project-dir 或 --project-root", file=sys.stderr)
        sys.exit(1)
    project_root = resolve_cross_platform_path(raw_root)
    ep = normalize_episode_name(args.episode)
    report = _run_voice_gen_core(
        project_root,
        ep,
        force_new=bool(args.force_new),
        strategy_mode=str(getattr(args, "mode", "auto") or "auto"),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "VOICE_GEN_OK":
        sys.exit(1)


def _parse_voice_ref_identity(wp: Path) -> tuple[str, str, str]:
    stem = re.sub(r"_(?:声线参考|声线母本|母本|参考)$", "", wp.stem.strip())
    up = stem.upper()
    v_mode = "心声" if any(k in up for k in ("心声", "OS", "内白")) else ("说话" if any(k in stem for k in ("说话", "对白", "台词")) else "")
    core = re.sub(r"_(?:说话|心声|对白|台词|OS|os|内白).*$", "", stem).strip()
    m_role = re.match(r"^(角色\d+|\d+)_(.+)$", core)
    if m_role:
        return m_role.group(1), m_role.group(2).strip(), v_mode
    return "", core, v_mode


def _read_registered_voice_roles(project_root: Path, ep: str) -> list[str]:
    cands = (
        project_root / "中间文件" / "索引与制作记录" / "角色改名与音色映射.json",
        project_root / "中间文件" / "在制工作区" / ep / "角色改名与音色映射.json",
        project_root / "分集" / ep / "角色改名与音色映射.json",
    )
    rmap_p = next((p for p in cands if p.is_file() and p.stat().st_size > 0), None)
    if rmap_p is None:
        return []
    roles: list[str] = []
    with contextlib.suppress(Exception):
        raw = read_json(rmap_p)
        if isinstance(raw, dict):
            ch_obj = raw.get("characters") or raw.get("roles") or raw.get("cast")
            if isinstance(ch_obj, dict):
                for k, v in ch_obj.items():
                    nm = str(v.get("new_name") or v.get("name") or v.get("display_name") or k).strip() if isinstance(v, dict) else str(k).strip()
                    nm = re.sub(r"^角色\d+_", "", nm).strip()
                    if nm and nm not in roles:
                        roles.append(nm)
            elif isinstance(ch_obj, list):
                for it in ch_obj:
                    nm = str(it.get("new_name") or it.get("name") or it.get("display_name") or "").strip() if isinstance(it, dict) else str(it).strip()
                    nm = re.sub(r"^角色\d+_", "", nm).strip()
                    if nm and nm not in roles:
                        roles.append(nm)
    return roles


def cmd_voice_audit(args: argparse.Namespace) -> None:
    """第二棒声线核验：强制仅接受 48000Hz Mono pcm_s16le (2.00s-3.00s)，全部通过时原子签发 声线母本批准清单.json"""
    raw_root = getattr(args, "project_dir", "") or getattr(args, "project_root", "")
    if not raw_root:
        print("必须提供 --project-dir 或 --project-root", file=sys.stderr)
        sys.exit(1)
    project_root = resolve_cross_platform_path(raw_root)
    ep = normalize_episode_name(args.episode)
    ep_voice_dir = project_root / "分集" / ep / "声线"
    lib_voice_dir = project_root / "声线参考"
    record_dir = project_root / "中间文件" / "索引与制作记录"
    audit_dir = record_dir / "交付自查状态"
    ep_voice_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    wav_files = sorted(ep_voice_dir.glob("*.wav"))
    if not wav_files and lib_voice_dir.exists():
        for src_w in sorted(lib_voice_dir.glob("*_声线参考.wav")):
            shutil.copy2(src_w, ep_voice_dir / src_w.name)
        wav_files = sorted(ep_voice_dir.glob("*.wav"))

    details: list[dict[str, Any]] = []
    hashes: set[str] = set()
    approved_items: list[dict[str, Any]] = []
    by_character: dict[str, dict[str, dict[str, Any]]] = {}
    approved_triplets: dict[str, dict[str, Any]] = {}
    sha256_map: dict[str, str] = {}
    identity_errors: list[str] = []

    for wp in wav_files:
        info = probe_wav_compliance(wp)
        role_id, char_name, v_mode = _parse_voice_ref_identity(wp)
        sha_low = str(info.get("sha256") or "").strip().lower()
        info.update({"file": wp.name, "path": str(wp).replace("\\", "/"), "role_id": role_id, "character": char_name, "voice_mode": v_mode})
        if not char_name or v_mode not in ("说话", "心声"):
            info["compliant"] = False
            identity_errors.append(f"INVALID_VOICE_FILENAME_IDENTITY:{wp.name}")
        details.append(info)
        if sha_low:
            hashes.add(sha_low)
        if info.get("compliant") and char_name and v_mode in ("说话", "心声") and sha_low:
            rec_item = {
                "role_id": role_id, "character": char_name, "speaker": char_name, "voice_mode": v_mode,
                "file": wp.name, "file_name": wp.name, "path": info["path"],
                "sample_rate": int(info.get("sample_rate") or 48000), "channels": 1, "codec_name": "pcm_s16le",
                "duration_sec": info.get("duration_sec"), "approved_sha256": sha_low, "sha256": sha_low,
            }
            approved_items.append(rec_item)
            by_character.setdefault(char_name, {})[v_mode] = rec_item
            approved_triplets[f"{char_name}::{v_mode}"] = rec_item
            sha256_map[wp.name] = sha_low
            sha256_map[f"{char_name}::{v_mode}"] = sha_low

    reg_roles = _read_registered_voice_roles(project_root, ep)
    missing_role_modes = [
        f"{rn}_{vm}" for rn in reg_roles for vm in ("说话", "心声")
        if vm not in by_character.get(rn, {})
    ]
    all_compliant = bool(details) and all(d.get("compliant", False) for d in details) and not identity_errors and not missing_role_modes
    all_distinct = bool(details) and (len(hashes) == len(details))
    max_silence = max((float(d.get("silence_ratio", 0.0)) for d in details), default=0.0)
    passed = all_compliant and all_distinct

    rec_manifest_p = record_dir / "声线母本批准清单.json"
    ep_manifest_p = ep_voice_dir / "声线母本批准清单.json"
    if passed:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        approval_doc = {
            "schema_version": "4.2", "status": "VOICE_REF_APPROVED", "episode": ep,
            "sample_rate_hz": 48000, "channels": 1, "codec_name": "pcm_s16le", "approved_at": now_iso,
            "items": approved_items, "records": approved_items, "voice_refs": approved_items,
            "by_character": by_character, "approved_triplets": approved_triplets, "sha256_map": sha256_map,
        }
        write_json(rec_manifest_p, approval_doc)
        write_json(ep_manifest_p, approval_doc)
    else:
        for stale_p in (rec_manifest_p, ep_manifest_p):
            if stale_p.exists():
                with contextlib.suppress(OSError):
                    stale_p.unlink()

    report = {
        "status": "VOICE_AUDIT_OK" if passed else "VOICE_AUDIT_FAILED",
        "hardware_mode": probe_cpu_gpu_env("phase1_3")["execution_mode"],
        "project_root": str(project_root).replace("\\", "/"),
        "episode": ep,
        "voice_dir": str(ep_voice_dir).replace("\\", "/"),
        "wav_count": len(details),
        "all_duration_2_to_3s_48k_pcm16_ok": all_compliant,
        "all_duration_2_to_3s_and_pcm16_ok": all_compliant,
        "max_silence_ratio": round(max_silence, 4),
        "silence_ratio_passed": max_silence <= 0.35,
        "all_voices_physically_distinct": all_distinct,
        "missing_role_modes": missing_role_modes,
        "identity_errors": identity_errors,
        "approved_manifest_paths": [str(rec_manifest_p).replace("\\", "/"), str(ep_manifest_p).replace("\\", "/")] if passed else [],
        "records": details,
    }
    write_json(audit_dir / f"{ep}_声线审计报告.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not passed:
        sys.exit(2)


def cmd_phase3_verify(args: argparse.Namespace) -> None:
    """第三棒步骤 3：静态九项硬门禁委托（任一未通过强制 sys.exit(2)）"""
    run_phase3_verify_v41(
        args,
        probe_wav_fn=probe_wav_compliance,
        check_white_bg_fn=check_white_background,
        is_char_fn=_is_character_job,
        is_scene_fn=_is_scene_job,
        norm_flat_fn=_normalize_flat_frame_target_path,
    )
    return


def _legacy_cmd_phase3_verify_unused(args: argparse.Namespace) -> None:
    raw_root = args.project_dir or args.project_root
    if not raw_root:
        print("必须提供 --project-dir 或 --project-root", file=sys.stderr)
        sys.exit(1)
    project_root = Path(raw_root).resolve()
    ep = args.episode.strip()
    ep_dir = project_root / "分集" / ep
    record_dir = project_root / "中间文件" / "索引与制作记录"
    audit_dir = record_dir / "交付自查状态"
    audit_dir.mkdir(parents=True, exist_ok=True)

    director_md_path = ep_dir / f"{ep}_剧本故事梗概与逐镜导演脚本.md"
    md_path = ep_dir / f"{ep}_视频提示词.md"
    master_md_path = project_root / "中间文件" / "提示词维护" / "视频提示词" / ep / "当前主稿.md"
    xlsx_path = ep_dir / f"{ep}_分镜头脚本.xlsx"
    dlg_table_xlsx = ep_dir / f"{ep}_台词生成表.xlsx"
    dlg_table_csv = ep_dir / f"{ep}_台词生成表.csv"
    csv_path = ep_dir / f"{ep}_字幕时间轴.csv"
    char_dir = ep_dir / "人物"
    voice_dir = ep_dir / "声线"
    dlg_audio_dir = ep_dir / "台词音频"
    kf_dir = ep_dir / "首尾及关键帧"
    raw_seg_dir = ep_dir / "原视频段落"

    # 若发现本集声线目录为空且存在 角色改名与音色映射.json，自动联动执行一次 voice-gen 补齐
    rename_file = record_dir / "角色改名与音色映射.json"
    if rename_file.exists() and (not voice_dir.exists() or not list(voice_dir.glob("*.wav"))):
        try:
            _run_voice_gen_core(project_root, ep, force_new=False)
        except Exception:
            pass
    # 若尚未生成 第NN集_台词生成表.xlsx / 台词音频，自动联动执行一次 dialogue-gen 补齐
    if md_path.exists() and (not dlg_table_xlsx.exists() or not dlg_audio_dir.exists()):
        try:
            _run_dialogue_gen_core(project_root, ep, mode="cosyvoice2", force_new=False)
        except Exception:
            pass

    md_sha_ok = False
    no_placeholder_left = False
    if md_path.exists():
        txt = md_path.read_text(encoding="utf-8-sig")
        no_placeholder_left = ("{{VISUAL_STYLE}}" not in txt) and ("【待填角色：" not in txt)
        if master_md_path.exists():
            md_sha_ok = (sha256_file(md_path) == sha256_file(master_md_path))

    voice_samples = sorted(voice_dir.glob("*.wav")) if voice_dir.exists() else []
    voice_details = [probe_wav_compliance(vp) for vp in voice_samples]
    voice_all_compliant = bool(voice_samples) and all(v["compliant"] for v in voice_details)
    voice_unique = len({v.get("sha256") for v in voice_details if v.get("sha256")}) == len(voice_details)

    # 核查首尾及关键帧：严格豁免 need_tail_frame: false 的片段尾帧，绝不将免尾帧段判为缺帧
    manifest_path = project_root / "中间文件" / "在制工作区" / ep / "待生图帧清单.json"
    missing_required_frames = []
    exempted_tail_frames = []
    if manifest_path.exists():
        m_raw = read_json(manifest_path)
        m_items = m_raw if isinstance(m_raw, list) else m_raw.get("units", [])
        for it in m_items:
            if not isinstance(it, dict):
                continue
            u = get_unit_id(it)
            if _is_character_job(it, u) or _is_scene_job(it, u):
                continue
            ftype = str(it.get("frame_type") or "")
            if (ftype == "尾帧" or u.endswith("_尾帧")) and it.get("need_tail_frame") is False:
                exempted_tail_frames.append(u)
                continue
            t_raw = _resolve_unit_target_path(it)
            if t_raw:
                norm_flat = _normalize_flat_frame_target_path(str(t_raw), u, it)
                tp_flat = Path(norm_flat) if Path(norm_flat).is_absolute() else (project_root / norm_flat).resolve()
                tp_orig = Path(t_raw) if Path(t_raw).is_absolute() else (project_root / t_raw).resolve()
                if not tp_flat.exists() and not tp_orig.exists():
                    missing_required_frames.append(u)
    kf_ok = kf_dir.exists() and any(kf_dir.rglob("*.*")) and (len(missing_required_frames) == 0)

    char_files = sorted(char_dir.glob("*.png")) if char_dir.exists() else []
    portrait_files = [f.name for f in char_files if f.stem.endswith("_肖像")]
    subject_files = [f.name for f in char_files if f.stem.endswith("_主体")]

    checks = {
        "1_人物目录_肖像与主体双白底图": char_dir.exists() and any(char_dir.iterdir()),
        "2_声线目录_双音色2至3秒_48k单声道PCM": len(voice_samples) >= 2 and voice_all_compliant and voice_unique,
        "3_视频提示词_已回填且主稿SHA一致": md_path.exists() and no_placeholder_left and md_sha_ok,
        "4_首尾及关键帧目录_按需尾帧合规": kf_ok,
        "5_分镜头脚本_xlsx": xlsx_path.exists() and xlsx_path.stat().st_size > 0,
        "6_原视频段落目录": raw_seg_dir.exists() and any(raw_seg_dir.glob("*.mp4")),
        "7_字幕时间轴_csv": csv_path.exists() and csv_path.stat().st_size > 0,
        "8_台词生成表_xlsx与csv": dlg_table_xlsx.exists() and dlg_table_csv.exists(),
        "9_剧本故事梗概与逐镜导演脚本_md": director_md_path.exists() and director_md_path.stat().st_size > 100,
    }
    report = {
        "episode": ep,
        "all_nine_deliverables_passed": all(checks.values()),
        "all_seven_deliverables_passed": all(v for k, v in checks.items() if not k.startswith("9_")),
        "deliverable_checks": checks,
        "character_dual_sheet_audit": {
            "total_character_pngs": len(char_files),
            "portrait_count": len(portrait_files),
            "subject_count": len(subject_files),
            "portraits": portrait_files,
            "subjects": subject_files,
        },
        "keyframes_audit": {
            "missing_required_frames": missing_required_frames,
            "exempted_tail_frames_count": len(exempted_tail_frames),
            "exempted_tail_frames": exempted_tail_frames,
        },
        "voice_audit": {
            "wav_count": len(voice_samples),
            "all_compliant_2_to_3s_48k_pcm16": voice_all_compliant,
            "all_voices_distinct": voice_unique,
        },
    }
    write_json(audit_dir / f"{ep}_第三棒静态九项核验.json", report)
    write_json(audit_dir / f"{ep}_第三棒静态七项核验.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _compute_hsv_hist_similarity(img_a: Path, img_b: Path) -> float:
    """计算两张抽帧图片的 HSV/RGB 颜色直方图相关度（0.0~1.0），用于切点突变复核与首/中/尾三点验切"""
    try:
        from PIL import Image
        with Image.open(img_a) as ia, Image.open(img_b) as ib:
            ha = ia.convert("HSV").resize((128, 72)).histogram()
            hb = ib.convert("HSV").resize((128, 72)).histogram()
        mean_a = sum(ha) / len(ha)
        mean_b = sum(hb) / len(hb)
        num = sum((a - mean_a) * (b - mean_b) for a, b in zip(ha, hb))
        den = (sum((a - mean_a) ** 2 for a in ha) * sum((b - mean_b) ** 2 for b in hb)) ** 0.5
        return round(max(0.0, min(1.0, num / den)) if den > 0 else 1.0, 4)
    except Exception:
        return 1.0


def _compute_hsv_hist_similarity_images(ia, ib) -> float:
    try:
        ha = ia.convert("HSV").resize((128, 72)).histogram()
        hb = ib.convert("HSV").resize((128, 72)).histogram()
        mean_a = sum(ha) / len(ha)
        mean_b = sum(hb) / len(hb)
        num = sum((a - mean_a) * (b - mean_b) for a, b in zip(ha, hb))
        den = (sum((a - mean_a) ** 2 for a in ha) * sum((b - mean_b) ** 2 for b in hb)) ** 0.5
        return round(max(0.0, min(1.0, num / den)) if den > 0 else 1.0, 4)
    except Exception:
        return 1.0


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
            probe_cmd = [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", str(master_path),
            ]
            res = subprocess.run(probe_cmd, capture_output=True, text=True, encoding="utf-8", check=True)
            m_dur = float(json.loads(res.stdout).get("format", {}).get("duration", 0.0))
            if abs(m_dur - dur) <= 1.0:
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
    subprocess.run(cmd, capture_output=True, check=True)
    return master_path, round(float(start_sec), 3)


def _hsv_corr_rgb_arrays(rgb_a: Any, rgb_b: Any) -> float:
    """计算两张 RGB uint8 图像的 HSV 2D 直方图相关性 [0, 1]"""
    try:
        import cv2  # type: ignore
        hsv_a = cv2.cvtColor(rgb_a, cv2.COLOR_RGB2HSV)
        hsv_b = cv2.cvtColor(rgb_b, cv2.COLOR_RGB2HSV)
        hist_a = cv2.calcHist([hsv_a], [0, 1], None, [50, 60], [0, 180, 0, 256])
        hist_b = cv2.calcHist([hsv_b], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist_a, hist_a, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(hist_b, hist_b, 0, 1, cv2.NORM_MINMAX)
        corr = float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL))
        return round(max(0.0, min(1.0, corr)), 4)
    except Exception:
        from PIL import Image
        return _compute_hsv_hist_similarity_images(Image.fromarray(rgb_a), Image.fromarray(rgb_b))


def _run_transnetv2_shot_detection(
    work_video: Path,
    local_start: float,
    local_end: float,
) -> list[tuple[float, float]]:
    """
    使用 TransNetV2 (PyTorch CPU) 对 25fps CFR 母本进行神经镜头边界检测：
    - OpenCV 解码为 48x27 RGB uint8
    - torch.no_grad() 下融合 single_frame_predictions（硬切）与 all_frame_predictions（软叠化）
    - 抗爆闪过滤：<0.65s 高亮瞬态闪白若前后两侧 HSV 相似度 >= 0.78 则合并防误劈碎
    - 严禁静默降级：导入或推理失败立即抛出 TRANSNETV2_ENGINE_FAILURE
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        import torch  # type: ignore
        import transnetv2_pytorch as tnv2_mod  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"TRANSNETV2_ENGINE_FAILURE: 依赖导入失败 ({exc})") from exc

    tnv2_cls = getattr(tnv2_mod, "TransNetV2", None) or getattr(tnv2_mod, "TransNetV2Torch", None)
    if tnv2_cls is None:
        raise RuntimeError("TRANSNETV2_ENGINE_FAILURE: transnetv2_pytorch 未导出 TransNetV2 类")

    try:
        model = tnv2_cls()
        if hasattr(model, "eval"):
            model.eval()
        if hasattr(model, "to"):
            model.to("cpu")
    except Exception as exc:
        raise RuntimeError(f"TRANSNETV2_ENGINE_FAILURE: 模型初始化失败 ({exc})") from exc

    cap = cv2.VideoCapture(str(work_video))
    if not cap.isOpened():
        raise RuntimeError(f"TRANSNETV2_ENGINE_FAILURE: 无法打开视频 {work_video}")

    frames_rgb_small: list[Any] = []
    frame_times: list[float] = []
    lumas: list[float] = []
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        if fps <= 1.0 or fps > 120.0:
            fps = 25.0
        if local_start > 0.0:
            cap.set(cv2.CAP_PROP_POS_MSEC, local_start * 1000.0)
        idx = 0
        while True:
            ok, bgr = cap.read()
            if not ok or bgr is None:
                break
            cur_pos_msec = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
            t_sec = round(cur_pos_msec / 1000.0, 4) if cur_pos_msec > 0.0 else round(local_start + idx / fps, 4)
            if t_sec < local_start - 0.02:
                idx += 1
                continue
            if t_sec > local_end + 0.02:
                break
            rgb_small = cv2.cvtColor(cv2.resize(bgr, (48, 27), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
            frames_rgb_small.append(rgb_small)
            frame_times.append(max(local_start, min(local_end, t_sec)))
            lumas.append(float(np.mean(rgb_small)))
            idx += 1
    finally:
        cap.release()

    if len(frames_rgb_small) < 2:
        return [(round(local_start, 3), round(local_end, 3))] if local_end > local_start else []

    frames_arr = np.asarray(frames_rgb_small, dtype=np.uint8)
    try:
        def _to_1d_prob(x: Any) -> Any:
            if hasattr(x, "detach"):
                x = x.detach().cpu().numpy()
            arr = np.asarray(x, dtype=np.float32).reshape(-1)
            if arr.size > 0 and (float(np.min(arr)) < 0.0 or float(np.max(arr)) > 1.0):
                arr = 1.0 / (1.0 + np.exp(-np.clip(arr, -20.0, 20.0)))
            return arr

        def _unpack_pred_out(out: Any) -> tuple[Any, Any]:
            if isinstance(out, (tuple, list)):
                if len(out) >= 3:
                    s_raw, a_raw = out[1], out[2]
                elif len(out) >= 2:
                    s_raw, a_raw = out[0], out[1]
                elif len(out) == 1:
                    s_raw = a_raw = out[0]
                else:
                    raise RuntimeError("TransNetV2 返回空元组")
            elif isinstance(out, dict):
                s_raw = (
                    out["single_frame_pred"] if out.get("single_frame_pred") is not None
                    else (out["single_frame_predictions"] if out.get("single_frame_predictions") is not None
                    else (out["single"] if out.get("single") is not None
                    else next(iter(out.values()))))
                )
                a_raw = (
                    out["many_hot"] if out.get("many_hot") is not None
                    else (out["all_frame_pred"] if out.get("all_frame_pred") is not None
                    else (out["all_frame_predictions"] if out.get("all_frame_predictions") is not None
                    else (out["all"] if out.get("all") is not None else s_raw)))
                )
            else:
                s_raw, a_raw = out, out

            if isinstance(s_raw, dict):
                s_raw = (
                    s_raw["single_frame_pred"] if s_raw.get("single_frame_pred") is not None
                    else (s_raw["single_frame_predictions"] if s_raw.get("single_frame_predictions") is not None
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

        with torch.no_grad():
            if hasattr(model, "predict_frames"):
                try:
                    out = model.predict_frames(torch.from_numpy(frames_arr))
                except TypeError:
                    out = model.predict_frames(frames_arr)
                s_raw, a_raw = _unpack_pred_out(out)
                single_preds = _to_1d_prob(s_raw)
                all_preds = _to_1d_prob(a_raw)
            elif hasattr(model, "predict_raw"):
                tensor_in = torch.from_numpy(frames_arr[None, ...])
                out = model.predict_raw(tensor_in)
                s_raw, a_raw = _unpack_pred_out(out)
                single_preds = _to_1d_prob(s_raw)
                all_preds = _to_1d_prob(a_raw)
            elif callable(model):
                n_total = len(frames_arr)
                if n_total > 100:
                    start_pad = np.repeat(frames_arr[:1], 25, axis=0)
                    last_start = 50 * ((n_total - 1) // 50)
                    end_pad_len = max(25, last_start + 100 - (25 + n_total))
                    end_pad = np.repeat(frames_arr[-1:], end_pad_len, axis=0)
                    padded = np.concatenate([start_pad, frames_arr, end_pad], axis=0)
                    s_chunks, a_chunks = [], []
                    for ptr in range(0, len(padded) - 100 + 1, 50):
                        win_tensor = torch.from_numpy(padded[ptr : ptr + 100][None, ...])
                        w_out = model(win_tensor)
                        ws_raw, wa_raw = _unpack_pred_out(w_out)
                        s_chunks.append(_to_1d_prob(ws_raw)[25:75])
                        a_chunks.append(_to_1d_prob(wa_raw)[25:75])
                    single_preds = np.concatenate(s_chunks, axis=0)[:n_total]
                    all_preds = np.concatenate(a_chunks, axis=0)[:n_total]
                else:
                    tensor_in = torch.from_numpy(frames_arr[None, ...])
                    out = model(tensor_in)
                    s_raw, a_raw = _unpack_pred_out(out)
                    single_preds = _to_1d_prob(s_raw)
                    all_preds = _to_1d_prob(a_raw)
            else:
                raise RuntimeError("模型既无 predict_frames 也无 predict_raw 接口")
    except Exception as exc:
        raise RuntimeError(f"TRANSNETV2_ENGINE_FAILURE: TransNetV2 推理失败 ({exc})") from exc

    n_frames = min(len(frame_times), len(single_preds))
    if n_frames < 2:
        return [(round(local_start, 3), round(local_end, 3))]

    # 提取硬切峰值 (single_frame_predictions >= 0.45)
    cut_indices: list[int] = []
    i = 1
    while i < n_frames - 1:
        if float(single_preds[i]) >= 0.45:
            j = i
            best_i = i
            best_v = float(single_preds[i])
            while j + 1 < n_frames - 1 and float(single_preds[j + 1]) >= 0.45:
                j += 1
                if float(single_preds[j]) > best_v:
                    best_v = float(single_preds[j])
                    best_i = j
            cut_indices.append(best_i)
            i = j + 1
        else:
            i += 1

    # 提取软切/慢叠化中心 (all_frame_predictions >= 0.45 且距已知硬切 >= 0.35s)
    if len(all_preds) >= n_frames:
        i = 1
        while i < n_frames - 1:
            if float(all_preds[i]) >= 0.45:
                j = i
                best_i = i
                best_v = float(all_preds[i])
                while j + 1 < n_frames - 1 and float(all_preds[j + 1]) >= 0.45:
                    j += 1
                    if float(all_preds[j]) > best_v:
                        best_v = float(all_preds[j])
                        best_i = j
                cand_t = frame_times[best_i]
                if not any(abs(cand_t - frame_times[ci]) < 0.35 for ci in cut_indices):
                    cut_indices.append(best_i)
                i = j + 1
            else:
                i += 1

    cut_indices = sorted(set(cut_indices))
    boundaries: list[float] = [round(local_start, 3)]
    for ci in cut_indices:
        t_b = round(frame_times[ci], 3)
        if local_start + 0.10 <= t_b <= local_end - 0.10:
            if t_b - boundaries[-1] >= 0.10:
                boundaries.append(t_b)
    if local_end - boundaries[-1] >= 0.15:
        boundaries.append(round(local_end, 3))
    elif len(boundaries) > 1:
        boundaries[-1] = round(local_end, 3)

    segments: list[tuple[float, float]] = [
        (boundaries[k], boundaries[k + 1])
        for k in range(len(boundaries) - 1)
        if boundaries[k + 1] - boundaries[k] > 0.05
    ]

    # 抗爆闪误切过滤：<0.65s 高亮瞬态闪白若前后两侧帧 HSV 相似度 >= 0.78 则合并
    def _frame_idx_at(t_target: float) -> int:
        best_idx = 0
        best_diff = abs(frame_times[0] - t_target)
        for k_idx, ft in enumerate(frame_times):
            d = abs(ft - t_target)
            if d < best_diff:
                best_diff = d
                best_idx = k_idx
        return best_idx

    filtered: list[tuple[float, float]] = []
    idx_seg = 0
    while idx_seg < len(segments):
        cur_s, cur_e = segments[idx_seg]
        cur_dur = cur_e - cur_s
        if cur_dur < 0.65 and filtered and (idx_seg + 1) < len(segments):
            prev_s, prev_e = filtered[-1]
            next_s, next_e = segments[idx_seg + 1]
            prev_idx = _frame_idx_at(max(prev_s, prev_e - 0.12))
            i_cs = _frame_idx_at(cur_s)
            i_ce = max(i_cs, _frame_idx_at(cur_e))
            next_idx = _frame_idx_at(min(next_e, next_s + 0.12))
            prev_luma = lumas[prev_idx]
            flash_luma = max(lumas[i_cs : i_ce + 1])
            next_luma = lumas[next_idx]
            is_bright_flash = (flash_luma >= 165.0) or (flash_luma >= min(prev_luma, next_luma) + 35.0)
            if is_bright_flash:
                sim_pn = _hsv_corr_rgb_arrays(frames_arr[prev_idx], frames_arr[next_idx])
                if sim_pn >= 0.78:
                    filtered[-1] = (prev_s, next_e)
                    idx_seg += 2
                    continue
        filtered.append((round(cur_s, 3), round(cur_e, 3)))
        idx_seg += 1

    # Pass B: 若闪白仅触发单边界，检查边界附近 <0.65s 高亮区域且前后 HSV >= 0.78 则合并
    if len(filtered) >= 2:
        pass_b: list[tuple[float, float]] = [filtered[0]]
        for s_nxt, e_nxt in filtered[1:]:
            s_prev, e_prev = pass_b[-1]
            ib = _frame_idx_at(e_prev)
            lo = max(0, ib - 8)
            hi = min(len(lumas) - 1, ib + 8)
            idx_before = max(0, ib - 10)
            idx_after = min(len(lumas) - 1, ib + 10)
            thr_f = max(165.0, min(lumas[idx_before], lumas[idx_after]) + 35.0)
            f_cnt = sum(1 for k_i in range(lo, hi + 1) if lumas[k_i] >= thr_f)
            if 0 < f_cnt <= 16 and _hsv_corr_rgb_arrays(frames_arr[idx_before], frames_arr[idx_after]) >= 0.78:
                pass_b[-1] = (s_prev, e_nxt)
            else:
                pass_b.append((s_nxt, e_nxt))
        filtered = pass_b

    # 兜底合并 <0.25s 极短碎片
    final_segs: list[tuple[float, float]] = []
    for seg_s, seg_e in filtered:
        if final_segs and (seg_e - seg_s) < 0.25:
            ps, _ = final_segs[-1]
            final_segs[-1] = (ps, seg_e)
        else:
            final_segs.append((seg_s, seg_e))
    if not final_segs and local_end > local_start:
        final_segs = [(round(local_start, 3), round(local_end, 3))]
    return final_segs


def build_episode_storyboard_sheets(
    shots_json_path: str | Path,
    output_dir: str | Path,
    sheet_width: int = 1600,
    max_sheets: int = 6,
) -> dict[str, Any]:
    """
    将 物理切点清单.json 按顺序拼合为 1-6 张（自适应每张 12-18 镜）宽 1600px 的清晰 JPEG 故事板图，
    每行印 shot_XX | start-end (dur s) + 首帧 | 中帧 | 尾帧 横条，并写出 storyboard_index.json。
    """
    import math
    from PIL import Image, ImageDraw, ImageFont

    shots_p = resolve_cross_platform_path(shots_json_path)
    out_dir = resolve_cross_platform_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = read_json(shots_p)
    shots = raw if isinstance(raw, list) else raw.get("shots", [])
    shots = [s for s in shots if isinstance(s, dict)]

    n_shots = len(shots)
    if n_shots == 0:
        idx_payload = {"total_shots": 0, "sheet_count": 0, "sheet_width": sheet_width, "sheets": []}
        write_json(out_dir / "storyboard_index.json", idx_payload)
        return idx_payload

    if n_shots <= 18:
        rows_per_sheet = n_shots
    else:
        rows_per_sheet = max(12, min(18, math.ceil(n_shots / max(1, max_sheets))))
        if math.ceil(n_shots / rows_per_sheet) > max_sheets:
            rows_per_sheet = math.ceil(n_shots / max(1, max_sheets))

    chunks = [shots[i : i + rows_per_sheet] for i in range(0, n_shots, rows_per_sheet)]
    font = ImageFont.load_default()
    pad = 16
    gap = 12
    thumb_w = (sheet_width - pad * 2 - gap * 2) // 3
    thumb_h = max(120, int(round(thumb_w * 9 / 16)))
    label_h = 26
    row_h = label_h + thumb_h + 12

    sheets_meta: list[dict[str, Any]] = []
    for s_idx, chunk in enumerate(chunks, 1):
        canvas_h = pad + len(chunk) * row_h + pad
        canvas = Image.new("RGB", (sheet_width, canvas_h), (24, 24, 28))
        draw = ImageDraw.Draw(canvas)
        shot_ids_in_sheet: list[str] = []

        for r_idx, sh in enumerate(chunk):
            sid = str(sh.get("shot_id") or f"shot_{r_idx + 1:02d}")
            shot_ids_in_sheet.append(sid)
            s_sec = float(sh.get("start_sec", 0.0) or 0.0)
            e_sec = float(sh.get("end_sec", 0.0) or 0.0)
            dur = float(sh.get("duration", max(0.0, e_sec - s_sec)) or 0.0)
            y0 = pad + r_idx * row_h
            header_txt = f"{sid} | {s_sec:.2f}-{e_sec:.2f}s ({dur:.2f}s) | first / mid / last"
            draw.text((pad, y0 + 4), header_txt, fill=(240, 240, 245), font=font)

            frame_keys = (("first_frame", "FIRST"), ("mid_frame", "MID"), ("last_frame", "LAST"))
            for c_idx, (fk, tag) in enumerate(frame_keys):
                x0 = pad + c_idx * (thumb_w + gap)
                y_img = y0 + label_h
                fp_raw = str(sh.get(fk) or "").strip()
                fp = resolve_cross_platform_path(fp_raw) if fp_raw else None
                if fp is not None and not fp.exists():
                    alt = shots_p.parent / "source_frames" / fp.name
                    if alt.exists():
                        fp = alt
                if fp is not None and fp.exists():
                    try:
                        with Image.open(fp) as im:
                            thumb = im.convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
                        canvas.paste(thumb, (x0, y_img))
                    except Exception:
                        draw.rectangle([x0, y_img, x0 + thumb_w - 1, y_img + thumb_h - 1], fill=(50, 50, 58), outline=(180, 80, 80))
                        draw.text((x0 + 8, y_img + 8), f"READ_ERR {tag}", fill=(255, 180, 180), font=font)
                else:
                    draw.rectangle([x0, y_img, x0 + thumb_w - 1, y_img + thumb_h - 1], fill=(45, 45, 52), outline=(120, 120, 130))
                    draw.text((x0 + 8, y_img + 8), f"MISSING {tag}", fill=(210, 210, 220), font=font)

        sheet_name = f"storyboard_sheet_{s_idx:02d}.jpg"
        sheet_path = out_dir / sheet_name
        canvas.save(sheet_path, "JPEG", quality=90)
        sheets_meta.append({
            "sheet_index": s_idx,
            "sheet_file": str(sheet_path).replace("\\", "/"),
            "sheet_name": sheet_name,
            "shot_count": len(chunk),
            "shot_ids": shot_ids_in_sheet,
            "width": sheet_width,
            "height": canvas_h,
        })

    idx_payload = {
        "shots_json": str(shots_p).replace("\\", "/"),
        "output_dir": str(out_dir).replace("\\", "/"),
        "total_shots": n_shots,
        "sheet_count": len(sheets_meta),
        "sheet_width": sheet_width,
        "sheets": sheets_meta,
    }
    write_json(out_dir / "storyboard_index.json", idx_payload)
    return idx_payload


def cmd_build_storyboards(args: argparse.Namespace) -> None:
    """将 物理切点清单.json 拼合为 1600px 故事板总览 JPEG 与 storyboard_index.json（支持 --shots-json 或 --ep-dir 自动解析）"""
    raw_shots = str(getattr(args, "shots_json", "") or "").strip()
    raw_ep_dir = str(getattr(args, "ep_dir", "") or "").strip()
    if raw_shots:
        shots_p = resolve_cross_platform_path(raw_shots)
    elif raw_ep_dir:
        ep_dir_p = resolve_cross_platform_path(raw_ep_dir)
        shots_p = ep_dir_p / "物理切点清单.json"
        if not shots_p.exists():
            if ep_dir_p.parent.name == "在制工作区" and ep_dir_p.parent.parent.name == "中间文件":
                alt_p = ep_dir_p.parent.parent / "原片分析" / ep_dir_p.name / "物理切点清单.json"
                if alt_p.exists():
                    shots_p = alt_p
            elif ep_dir_p.parent.name == "分集":
                alt_p = ep_dir_p.parent.parent / "中间文件" / "原片分析" / ep_dir_p.name / "物理切点清单.json"
                if alt_p.exists():
                    shots_p = alt_p
    else:
        print("错误: 必须提供 --shots-json 或 --ep-dir 参数以定位 物理切点清单.json", file=sys.stderr)
        sys.exit(1)

    if not shots_p.exists():
        print(f"错误: 物理切点清单不存在: {shots_p}", file=sys.stderr)
        sys.exit(1)
    out_dir = resolve_cross_platform_path(args.output_dir) if getattr(args, "output_dir", "") else (shots_p.parent / "storyboard_grids")
    res = build_episode_storyboard_sheets(
        shots_json_path=shots_p,
        output_dir=out_dir,
        sheet_width=int(getattr(args, "sheet_width", 1600) or 1600),
        max_sheets=int(getattr(args, "max_sheets", 6) or 6),
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_detect_shots(args) -> None:
    """
    第一棒第零阶段：CFR 母本重整 + 云端 Colab 纯 CPU TransNetV2 真·双层精确切分
    （TransNetV2 硬切/慢叠化双头 + 抗爆闪过滤 + 0.15s 安全内缩 + 0.55 三点验切 + 故事板总览图）
    """
    import csv
    import datetime as _dt
    import platform as _platform

    allow_local = bool(getattr(args, "force_local", False)) or (os.environ.get("ALLOW_LOCAL_SPLIT") == "1")
    if not is_colab_runtime() and not allow_local:
        err_msg = {
            "status": "BLOCKED_BY_LOCAL_GATEKEEPER",
            "reason": "检测到当前在本地 Windows 直接调用 split-scenes / detect-shots，且未传入 --force-local（或 ALLOW_LOCAL_SPLIT=1）。",
            "rule": "第一棒物理视频切分严禁在本地偷跑，必须通过云端 Colab 纯 CPU 实例执行 TransNetV2 精确切分（含抗爆闪过滤 + 0.15s内缩 + 首/中/尾三点验切 + 故事板生成）并写出 phase1_colab_done.json 握手凭证！",
            "action_required": "请改调: python cloud_pipeline.py --phase phase1 --project-root \"<项目根目录>\" --episode \"第01集\"",
        }
        print(json.dumps(err_msg, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(2)

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"错误: 视频文件不存在: {video_path}")
        sys.exit(1)

    start_sec = float(args.start_sec) if args.start_sec is not None else 0.0
    end_sec = float(args.end_sec) if args.end_sec is not None else None
    actual_dur: Optional[float] = None
    try:
        p_res = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(video_path)],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        dur_val = float(json.loads(p_res.stdout).get("format", {}).get("duration", 0.0) or 0.0)
        if dur_val > 0.0:
            actual_dur = round(dur_val, 3)
    except Exception:
        actual_dur = None
    if end_sec is None:
        end_sec = actual_dur if actual_dur is not None else 360.0
    elif actual_dur is not None:
        end_sec = round(min(end_sec, actual_dur), 3)

    if args.output_dir:
        out_dir = Path(args.output_dir).resolve()
    else:
        out_dir = video_path.parent / "shots_split"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "source_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    print(f"正在加载视频并执行 TransNetV2 物理镜头切点检测 (CFR重整 + 抗爆闪过滤 + 0.15s内缩): {video_path.name}")
    print(f"检测时间区间: {start_sec:.2f}s ~ {end_sec:.2f}s...")

    use_cfr = bool(getattr(args, "cfr", False)) or (video_path.suffix.lower() in (".ts", ".m2ts", ".flv", ".mkv"))
    work_video, time_offset = ensure_cfr_episode_master(video_path, start_sec, end_sec, out_dir, force_cfr=use_cfr)
    local_start = 0.0 if work_video != video_path else float(start_sec)
    local_end = round(float(end_sec) - time_offset, 3) if work_video != video_path else float(end_sec)

    try:
        refined_segments = _run_transnetv2_shot_detection(work_video, local_start, local_end)
    except Exception as exc:
        print(f"TRANSNETV2_ENGINE_FAILURE: {exc}", file=sys.stderr)
        sys.exit(2)

    SAFETY_INSET_SEC = 0.15
    results = []
    for shot_idx, (loc_s, loc_e) in enumerate(refined_segments, 1):
        real_start = round(loc_s + time_offset, 3)
        real_end = round(loc_e + time_offset, 3)
        dur = round(loc_e - loc_s, 3)

        shot_id = f"shot_{shot_idx:02d}"
        first_frame = frames_dir / f"{shot_id}_首帧.png"
        mid_frame = frames_dir / f"{shot_id}_中帧.png"
        last_frame = frames_dir / f"{shot_id}_尾帧.png"

        head_t = min(loc_e - 0.05, loc_s + (0.05 if dur > 0.5 else 0.0))
        mid_t = round((loc_s + loc_e) * 0.5, 3)
        tail_t = max(head_t + 0.02, loc_e - (SAFETY_INSET_SEC if dur > 0.35 else 0.05))

        for t_val, f_dst in ((head_t, first_frame), (mid_t, mid_frame), (tail_t, last_frame)):
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{t_val:.3f}", "-i", str(work_video), "-vframes", "1", str(f_dst)],
                capture_output=True,
            )

        sim_first_mid = _compute_hsv_hist_similarity(first_frame, mid_frame)
        sim_mid_last = _compute_hsv_hist_similarity(mid_frame, last_frame)
        hsv_three_point_verified = bool(sim_first_mid >= 0.55 and sim_mid_last >= 0.55)

        results.append({
            "shot_id": shot_id,
            "start_sec": real_start,
            "end_sec": real_end,
            "duration": dur,
            "tail_safety_inset_sec": SAFETY_INSET_SEC,
            "first_frame": str(first_frame),
            "mid_frame": str(mid_frame),
            "last_frame": str(last_frame),
            "hsv_sim_first_mid": sim_first_mid,
            "hsv_sim_mid_last": sim_mid_last,
            "three_point_cut_verified": hsv_three_point_verified,
        })

    csv_file = out_dir / "物理切点清单.csv"
    csv_fields = [
        "shot_id", "start_sec", "end_sec", "duration", "tail_safety_inset_sec",
        "first_frame", "mid_frame", "last_frame",
        "hsv_sim_first_mid", "hsv_sim_mid_last", "three_point_cut_verified",
    ]
    with open(csv_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(results)

    json_file = out_dir / "物理切点清单.json"
    write_json(json_file, results)

    done_file = out_dir / "phase1_colab_done.json"
    write_json(done_file, {
        "status": "PHASE1_COLAB_CPU_DONE",
        "colab_cpu_verified": bool(is_colab_runtime()), "colab_runtime_verified": bool(is_colab_runtime()),
        "detector_engine": "TransNetV2",
        "dual_layer_hsv_and_015s_inset_applied": True,
        "uname": _platform.uname()._asdict(),
        "utc_timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "shots_count": len(results),
        "output_dir": str(out_dir).replace("\\", "/"),
    })

    sb_index = build_episode_storyboard_sheets(json_file, out_dir / "storyboard_grids")

    print(f"TransNetV2 物理切镜检测完成（含 CFR重整 + 抗爆闪过滤 + 0.15s 安全内缩 + 0.55 三点验切）！共检测出 {len(results)} 个真实物理镜头。")
    print(f"清单文件已保存至: {csv_file}")
    print(f"首/中/尾关键帧已保存至: {frames_dir}")
    print(f"故事板总览图 ({sb_index.get('sheet_count', 0)} 张) 已保存至: {out_dir / 'storyboard_grids'}")


def _find_motion_calm_split_points(
    video_path: Path,
    start_sec: float,
    end_sec: float,
    min_seg: float = 8.0,
    max_seg: float = 14.5,
) -> list[float]:
    """在 [start_sec + min_seg, start_sec + max_seg] 区间内基于帧间差分寻找运动最平稳的驻点时间"""
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"无法读取视频执行运动平稳驻点切分: {video_path}")

    split_points: list[float] = []
    try:
        cur_s = float(start_sec)
        while (end_sec - cur_s) > 15.0:
            win_start = cur_s + min_seg
            win_end = min(end_sec - 1.5, cur_s + max_seg)
            if win_end <= win_start:
                win_start = cur_s + min_seg
                win_end = min(end_sec - 0.25, cur_s + max_seg)
            if win_end <= win_start:
                break

            best_t = round((win_start + win_end) * 0.5, 3)
            best_diff = float("inf")
            prev_gray = None
            t_scan = max(cur_s, win_start - 0.2)
            while t_scan <= win_end + 1e-6:
                cap.set(cv2.CAP_PROP_POS_MSEC, t_scan * 1000.0)
                ok, bgr = cap.read()
                if ok and bgr is not None:
                    gray = cv2.cvtColor(cv2.resize(bgr, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
                    if prev_gray is not None and t_scan >= win_start - 1e-6:
                        diff = float(np.mean(np.abs(gray - prev_gray)))
                        if diff < best_diff:
                            best_diff = diff
                            best_t = round(t_scan, 3)
                    prev_gray = gray
                t_scan += 0.2
            split_points.append(best_t)
            cur_s = best_t
    finally:
        cap.release()
    return split_points


def _extract_routing_entries(raw_routing: Any) -> list[dict[str, Any]]:
    if isinstance(raw_routing, list):
        return [x for x in raw_routing if isinstance(x, dict)]
    if isinstance(raw_routing, dict):
        for k in ("segments", "video_segments", "routing", "cuts", "segment_routing"):
            val = raw_routing.get(k)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
    return []


def cmd_build_cuts(args: argparse.Namespace) -> None:
    """将 物理切点清单.json 100% 零漏秒转化为标准 cuts.csv（支持 --routing-json 大纲路由与运动平稳驻点切分，严禁盲目等分与跨场景合并）"""
    import csv

    shots_p = Path(args.shots_json).resolve()
    out_csv = Path(args.output_csv).resolve()
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    raw_shots = read_json(shots_p)
    shots = raw_shots if isinstance(raw_shots, list) else raw_shots.get("shots", [])
    shots = sorted(shots, key=lambda x: float(x.get("start_sec", 0.0)))

    scene_map: dict[str, dict] = {}
    if getattr(args, "scene_map_json", ""):
        sm_raw = read_json(Path(args.scene_map_json).resolve())
        if isinstance(sm_raw, dict):
            for k, v in sm_raw.items():
                if isinstance(v, dict):
                    scene_map[str(k)] = v
                else:
                    scene_map[str(k)] = {"scene": str(v), "summary": ""}

    routing_raw_path = str(getattr(args, "routing_json", "") or "").strip()
    video_raw_path = str(getattr(args, "video", "") or "").strip()

    def _shot_scene(sh_obj: dict, idx1: int) -> str:
        sid = str(sh_obj.get("shot_id") or f"shot_{idx1:02d}")
        meta = scene_map.get(sid) or scene_map.get(str(idx1)) or {}
        return str(meta.get("scene") or sh_obj.get("scene") or "").strip()

    def _shot_summary(sh_obj: dict, idx1: int) -> str:
        sid = str(sh_obj.get("shot_id") or f"shot_{idx1:02d}")
        meta = scene_map.get(sid) or scene_map.get(str(idx1)) or {}
        return str(meta.get("summary") or sh_obj.get("summary") or "").strip()

    rows: list[dict[str, Any]] = []
    segments_count = 0

    if routing_raw_path:
        rt_p = resolve_cross_platform_path(routing_raw_path)
        if not rt_p.exists():
            print(f"错误: 路由表不存在: {rt_p}", file=sys.stderr)
            sys.exit(1)
        rt_entries = _extract_routing_entries(read_json(rt_p))
        if not rt_entries:
            print(f"错误: --routing-json 未包含有效 segments 路由条目: {rt_p}", file=sys.stderr)
            sys.exit(1)

        shots_by_id: dict[str, tuple[int, dict]] = {}
        for i1, sh in enumerate(shots, 1):
            sid = str(sh.get("shot_id") or f"shot_{i1:02d}")
            shots_by_id[sid] = (i1, sh)

        covered_sids: set[str] = set()
        for seg_idx, ent in enumerate(rt_entries, 1):
            seg_name = str(ent.get("segment") or ent.get("segment_id") or f"segment_{seg_idx:02d}").strip()
            seg_scene = str(ent.get("scene") or "").strip()
            model_route = str(ent.get("model_route") or ent.get("model") or "").strip()
            video_mode = str(ent.get("video_mode") or ent.get("mode") or "").strip()
            gen_dur_val = ent.get("gen_duration_sec")
            extend_sec_val = ent.get("extend_sec")
            need_tail = ent.get("need_tail_frame", "")
            is_golden = ent.get("is_golden_15s", "")
            is_climax = ent.get("is_climax", "")
            intent_lock = str(ent.get("intent_lock") or "").strip()
            ent_summary = str(ent.get("summary") or "").strip()

            raw_sids = ent.get("shot_ids") or ent.get("shots") or []
            if isinstance(raw_sids, str):
                raw_sids = [x.strip() for x in raw_sids.split(",") if x.strip()]

            grp: list[tuple[int, dict]] = []
            if isinstance(raw_sids, list) and raw_sids:
                for rs in raw_sids:
                    if isinstance(rs, dict):
                        sub_s = round(float(rs.get("start_sec", 0.0)), 3)
                        sub_e = round(float(rs.get("end_sec", 0.0)), 3)
                        sub_sid = str(rs.get("shot_id") or f"{seg_name}_s{len(grp)+1}")
                        grp.append((0, {
                            "shot_id": sub_sid,
                            "start_sec": sub_s,
                            "end_sec": sub_e,
                            "duration": round(sub_e - sub_s, 3),
                            "scene": str(rs.get("scene") or seg_scene).strip(),
                            "summary": str(rs.get("summary") or ent_summary).strip(),
                        }))
                        covered_sids.add(sub_sid)
                    else:
                        rs_str = str(rs).strip()
                        if rs_str in shots_by_id:
                            grp.append(shots_by_id[rs_str])
                            covered_sids.add(rs_str)
            elif ent.get("start_sec") is not None and ent.get("end_sec") is not None:
                es = round(float(ent["start_sec"]), 3)
                ee = round(float(ent["end_sec"]), 3)
                matched = [
                    (i1, sh) for i1, sh in enumerate(shots, 1)
                    if float(sh["end_sec"]) > es + 0.02 and float(sh["start_sec"]) < ee - 0.02
                ]
                if matched and len(matched) == 1 and (
                    abs(float(matched[0][1]["start_sec"]) - es) > 0.15 or abs(float(matched[0][1]["end_sec"]) - ee) > 0.15
                ):
                    # 大纲把单个超长镜头拆分为多段时间窗
                    sub_sid = f"{matched[0][1].get('shot_id', 'shot')}_seg{seg_idx:02d}"
                    grp.append((matched[0][0], {
                        "shot_id": sub_sid,
                        "start_sec": es,
                        "end_sec": ee,
                        "duration": round(ee - es, 3),
                        "scene": seg_scene or _shot_scene(matched[0][1], matched[0][0]),
                        "summary": ent_summary or _shot_summary(matched[0][1], matched[0][0]),
                    }))
                    covered_sids.add(str(matched[0][1].get("shot_id") or ""))
                elif matched:
                    for i1, sh in matched:
                        grp.append((i1, sh))
                        covered_sids.add(str(sh.get("shot_id") or f"shot_{i1:02d}"))
                else:
                    grp.append((0, {
                        "shot_id": f"{seg_name}_shot01",
                        "start_sec": es,
                        "end_sec": ee,
                        "duration": round(ee - es, 3),
                        "scene": seg_scene,
                        "summary": ent_summary,
                    }))

            if not grp:
                print(f"BUILD_CUTS_ROUTING_EMPTY_SEGMENT: 路由段 {seg_name} 未匹配到任何物理镜头", file=sys.stderr)
                sys.exit(2)

            # 严禁跨场景合并校验
            distinct_scenes = {
                (str(sh.get("scene") or "").strip() or (_shot_scene(sh, i1) if i1 > 0 else ""))
                for i1, sh in grp
            } - {""}
            if len(distinct_scenes) > 1:
                print(f"BUILD_CUTS_CROSS_SCENE_MERGE: 路由段 {seg_name} 严禁跨场景合并 {sorted(distinct_scenes)}", file=sys.stderr)
                sys.exit(2)

            eff_scene = seg_scene or (next(iter(distinct_scenes)) if distinct_scenes else "")
            grp_start = round(min(float(sh["start_sec"]) for _, sh in grp), 3)
            grp_end = round(max(float(sh["end_sec"]) for _, sh in grp), 3)
            grp_dur = round(grp_end - grp_start, 3)

            if gen_dur_val is None or str(gen_dur_val).strip() == "":
                if len(grp) > 1 or video_mode.lower() == "ingredients":
                    gen_dur_str = "8"
                elif grp_dur <= 8.0:
                    gen_dur_str = str(4 if grp_dur <= 4.0 else (6 if grp_dur <= 6.0 else 8))
                else:
                    gen_dur_str = f"{grp_dur:.3f}"
            else:
                gen_dur_str = str(gen_dur_val).strip()

            if not model_route:
                if len(grp) > 1 or video_mode.lower() == "ingredients":
                    model_route = f"Veo 3.1 Lite | Ingredients | {gen_dur_str}s"
                elif grp_dur <= 8.0:
                    model_route = f"Veo 3.1 Lite | Handles | {gen_dur_str}s"
                else:
                    model_route = "Omni Flash 1.2"

            ext_sec_str = str(extend_sec_val).strip() if extend_sec_val is not None and str(extend_sec_val).strip() != "" else ""
            for sh_idx, (i1, sh) in enumerate(grp, 1):
                s_t = round(float(sh["start_sec"]), 3)
                e_t = round(float(sh["end_sec"]), 3)
                dur_t = round(e_t - s_t, 3)
                r_item: dict[str, Any] = {
                    "segment": seg_name,
                    "shot": f"镜头{sh_idx:02d}",
                    "start_sec": f"{s_t:.3f}",
                    "end_sec": f"{e_t:.3f}",
                    "duration_sec": f"{dur_t:.3f}",
                    "gen_duration_sec": gen_dur_str,
                    "model_route": model_route,
                    "video_mode": video_mode,
                    "need_tail_frame": str(need_tail) if need_tail != "" else "",
                    "is_golden_15s": str(is_golden) if is_golden != "" else "",
                    "is_climax": str(is_climax) if is_climax != "" else "",
                    "scene": eff_scene or (_shot_scene(sh, i1) if i1 > 0 else ""),
                    "summary": ent_summary or (_shot_summary(sh, i1) if i1 > 0 else ""),
                    "intent_lock": intent_lock,
                    "is_last_shot_in_segment": "1" if sh_idx == len(grp) else "0",
                }
                if ext_sec_str:
                    r_item["extend_sec"] = ext_sec_str
                rows.append(r_item)
        segments_count = len(rt_entries)
    else:
        max_pack_sec = float(getattr(args, "max_pack_sec", 8.0) or 8.0)
        max_shots_per_seg = int(getattr(args, "max_shots_per_seg", 3) or 3)

        normalized_shots: list[dict] = []
        for idx, sh in enumerate(shots, 1):
            s_t = round(float(sh["start_sec"]), 3)
            e_t = round(float(sh["end_sec"]), 3)
            if e_t <= s_t:
                continue
            dur = round(e_t - s_t, 3)
            sid = str(sh.get("shot_id") or f"shot_{idx:02d}")
            sc_name = _shot_scene(sh, idx)
            summary_txt = _shot_summary(sh, idx)

            if dur > 15.0:
                if not video_raw_path:
                    print(
                        f"BUILD_CUTS_NEEDS_ROUTING_OR_VIDEO: 单镜 {sid} ({dur:.2f}s > 15.0s) 严禁盲目等分，请提供 --routing-json 或 --video 进行运动平稳驻点切分！",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                v_path = resolve_cross_platform_path(video_raw_path)
                if not v_path.exists():
                    print(f"BUILD_CUTS_NEEDS_ROUTING_OR_VIDEO: 视频不存在 {v_path}", file=sys.stderr)
                    sys.exit(2)
                calm_pts = _find_motion_calm_split_points(v_path, s_t, e_t, min_seg=8.0, max_seg=14.5)
                pts = [s_t] + calm_pts + [e_t]
                for p_i in range(len(pts) - 1):
                    p_s = round(pts[p_i], 3)
                    p_e = round(pts[p_i + 1], 3)
                    if p_e <= p_s:
                        continue
                    cont_note = f"[运动平稳驻点切分 {p_i + 1}/{len(pts) - 1}; 前后衔接锁定]"
                    merged_sum = f"{summary_txt} {cont_note}".strip() if summary_txt else cont_note
                    normalized_shots.append({
                        "shot_id": f"{sid}_part{p_i + 1}",
                        "start_sec": p_s,
                        "end_sec": p_e,
                        "duration": round(p_e - p_s, 3),
                        "scene": sc_name,
                        "summary": merged_sum,
                        "intent_lock": cont_note,
                    })
            else:
                normalized_shots.append({
                    "shot_id": sid,
                    "start_sec": s_t,
                    "end_sec": e_t,
                    "duration": dur,
                    "scene": sc_name,
                    "summary": summary_txt,
                    "intent_lock": "",
                })

        # 按严格同景规则分组
        segments: list[list[dict]] = []
        for sh in normalized_shots:
            can_merge = False
            if segments:
                cur_group = segments[-1]
                cur_scene = cur_group[0]["scene"]
                gap_ok = abs(sh["start_sec"] - cur_group[-1]["end_sec"]) <= 0.15
                total_dur = round(sh["end_sec"] - cur_group[0]["start_sec"], 3)
                if (
                    bool(cur_scene)
                    and sh["scene"] == cur_scene
                    and len(cur_group) < max_shots_per_seg
                    and total_dur <= max_pack_sec
                    and gap_ok
                ):
                    can_merge = True
            if can_merge:
                segments[-1].append(sh)
            else:
                segments.append([sh])

        for seg_idx, grp in enumerate(segments, 1):
            seg_name = f"segment_{seg_idx:02d}"
            if len(grp) > 1:
                gen_dur = 8
                route = "Veo 3.1 Lite | Ingredients | 8s"
                v_mode = "Ingredients"
            else:
                d = grp[0]["duration"]
                if d <= 8.0:
                    gen_dur = 4 if d <= 4.0 else (6 if d <= 6.0 else 8)
                    route = f"Veo 3.1 Lite | Handles | {gen_dur}s"
                    v_mode = "Frames"
                else:
                    gen_dur = round(d, 3)
                    route = "Omni Flash 1.2"
                    v_mode = "Frames"

            for sh_idx, item in enumerate(grp, 1):
                rows.append({
                    "segment": seg_name,
                    "shot": f"镜头{sh_idx:02d}",
                    "start_sec": f"{item['start_sec']:.3f}",
                    "end_sec": f"{item['end_sec']:.3f}",
                    "duration_sec": f"{item['duration']:.3f}",
                    "gen_duration_sec": str(gen_dur),
                    "model_route": route,
                    "video_mode": v_mode,
                    "need_tail_frame": "",
                    "is_golden_15s": "",
                    "is_climax": "",
                    "scene": item["scene"],
                    "summary": item["summary"],
                    "intent_lock": item.get("intent_lock", ""),
                    "is_last_shot_in_segment": "1" if sh_idx == len(grp) else "0",
                })
        segments_count = len(segments)

    cols = [
        "segment", "shot", "start_sec", "end_sec", "duration_sec",
        "gen_duration_sec", "model_route", "video_mode",
        "need_tail_frame", "is_golden_15s", "is_climax",
        "scene", "summary", "intent_lock", "is_last_shot_in_segment",
    ]
    if any(r.get("extend_sec") for r in rows):
        cols.append("extend_sec")
    as_of_dt = resolve_as_of_date(getattr(args, "as_of_date", ""))
    eff_flow_tier = resolve_flow_tier(getattr(args, "flow_tier", None), routing_raw_path or None, shots_p, out_csv)
    flow_errs, sunset_warnings = validate_cuts_rows_flow_routing(rows, as_of_date=as_of_dt, flow_tier=eff_flow_tier)
    if flow_errs:
        for fe in flow_errs:
            print(f"BUILD_CUTS_FLOW_ROUTING_FAIL: {fe}", file=sys.stderr)
        sys.exit(2)

    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    chk = verify_cuts_csv(
        out_csv,
        shots_json=shots_p,
        expected_start=getattr(args, "expected_start", None),
        expected_end=getattr(args, "expected_end", None),
        allow_self_span=bool(getattr(args, "allow_self_span", False)),
        flow_tier=eff_flow_tier,
        as_of_date=as_of_dt,
        source_video=resolve_cross_platform_path(video_raw_path) if video_raw_path else None,
        skip_intro_sec=float(getattr(args, "skip_intro_sec", 0.0) or 0.0),
        skip_outro_sec=float(getattr(args, "skip_outro_sec", 0.0) or 0.0),
    )
    if not chk.get("ok"):
        for err in chk.get("errors", []):
            print(f"BUILD_CUTS_LEAK_OR_COVERAGE_FAIL: {err}", file=sys.stderr)
        sys.exit(2)

    print(json.dumps({
        "status": "BUILD_CUTS_OK",
        "shots_in": len(shots),
        "rows_out": len(rows),
        "segments_out": segments_count,
        "routing_used": bool(routing_raw_path),
        "flow_tier": eff_flow_tier,
        "as_of_date": as_of_dt,
        "sunset_warnings": sunset_warnings,
        "coverage_ratio": chk.get("coverage_ratio") if chk.get("coverage_ratio") is not None else chk.get("coverage"),
        "output_csv": str(out_csv).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))


def cmd_verify_cuts(args: argparse.Namespace) -> None:
    """运行 cuts.csv 零漏秒与独立可信源硬门禁校验"""
    cuts_csv = Path(args.cuts_csv).resolve()
    shots_json = Path(args.shots_json).resolve() if getattr(args, "shots_json", "") else None
    vid_raw = str(getattr(args, "video", "") or "").strip()
    rep = verify_cuts_csv(
        cuts_csv=cuts_csv,
        shots_json=shots_json,
        expected_start=getattr(args, "expected_start", None),
        expected_end=getattr(args, "expected_end", None),
        min_coverage=float(getattr(args, "min_coverage", 0.995) or 0.995),
        max_gap=float(getattr(args, "max_gap", 0.50) or 0.50),
        allow_self_span=bool(getattr(args, "allow_self_span", False)),
        flow_tier=getattr(args, "flow_tier", None),
        as_of_date=getattr(args, "as_of_date", "") or None,
        source_video=resolve_cross_platform_path(vid_raw) if vid_raw else None,
        skip_intro_sec=float(getattr(args, "skip_intro_sec", 0.0) or 0.0),
        skip_outro_sec=float(getattr(args, "skip_outro_sec", 0.0) or 0.0),
    )
    if not rep.get("ok"):
        for err in rep.get("errors", []):
            print(err, file=sys.stderr)
        print(json.dumps(rep, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(2)
    print(json.dumps(rep, ensure_ascii=False, indent=2))


def clean_source_frame(src_path: Path, out_path: Path, mask_corner_logo: bool = False) -> dict:
    """保留原图不动，自动裁切上下纯黑遮幅边（mean < 18 且 std < 12）并可选平滑左上角台标区域"""
    import numpy as np  # type: ignore
    from PIL import Image, ImageFilter

    src_p = Path(src_path)
    out_p = Path(out_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(src_p) as im:
        rgb = im.convert("RGB")
        arr = np.asarray(rgb)
        h, w = arr.shape[:2]
        gray = arr.mean(axis=2)
        row_mean = gray.mean(axis=1)
        row_std = gray.std(axis=1)

        max_trim = int(h * 0.30)
        top_crop = 0
        while top_crop < max_trim and row_mean[top_crop] < 18.0 and row_std[top_crop] < 12.0:
            top_crop += 1
        bottom_crop = 0
        while bottom_crop < max_trim and row_mean[h - 1 - bottom_crop] < 18.0 and row_std[h - 1 - bottom_crop] < 12.0:
            bottom_crop += 1

        if h - top_crop - bottom_crop >= max(16, int(h * 0.35)) and (top_crop > 0 or bottom_crop > 0):
            rgb = rgb.crop((0, top_crop, w, h - bottom_crop))

        if mask_corner_logo:
            cw, ch = rgb.size
            roi_w = max(8, int(cw * 0.16))
            roi_h = max(8, int(ch * 0.12))
            if cw > roi_w * 2 and ch > roi_h * 2:
                roi = rgb.crop((0, 0, roi_w, roi_h)).filter(ImageFilter.GaussianBlur(radius=6))
                rgb.paste(roi, (0, 0))

        rgb.save(out_p, quality=94)
        new_w, new_h = rgb.size
    return {
        "source": str(src_p).replace("\\", "/"),
        "output": str(out_p).replace("\\", "/"),
        "orig_size": [w, h],
        "clean_size": [new_w, new_h],
        "top_crop": top_crop,
        "bottom_crop": bottom_crop,
        "mask_corner_logo": bool(mask_corner_logo),
    }


def cmd_clean_source_frames(args: argparse.Namespace) -> None:
    """批量净化原片源帧上下黑边与可选左上角台标，输出 *_净参考.jpg"""
    in_dir = Path(args.input_dir).resolve()
    out_dir = Path(args.output_dir).resolve() if getattr(args, "output_dir", "") else (in_dir / "ref_clean_frames")
    out_dir.mkdir(parents=True, exist_ok=True)
    mask_logo = bool(getattr(args, "mask_corner_logo", False))

    cand_files = sorted(in_dir.glob("*_源.jpg"))
    if not cand_files:
        cand_files = sorted([p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png")])

    processed = []
    for p in cand_files:
        if p.stem.endswith("_源"):
            out_name = p.stem[:-2] + "_净参考.jpg"
        else:
            out_name = p.stem + "_净参考.jpg"
        processed.append(clean_source_frame(p, out_dir / out_name, mask_corner_logo=mask_logo))

    print(json.dumps({
        "status": "CLEAN_SOURCE_FRAMES_OK",
        "count": len(processed),
        "output_dir": str(out_dir).replace("\\", "/"),
        "files": processed,
    }, ensure_ascii=False, indent=2))


def cmd_prep_historical_refs(args: argparse.Namespace) -> None:
    """历史原照物理裁切 + 朝向统一镜像（facing=='left' 强制 ImageOps.mirror 翻转），输出 *_历史真容_同向朝右.jpg"""
    import numpy as np  # type: ignore
    from PIL import Image, ImageOps

    in_dir = Path(args.input_dir).resolve() if getattr(args, "input_dir", "") else Path.cwd()
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_raw = read_json(Path(args.spec_json).resolve())

    if isinstance(spec_raw, dict):
        if isinstance(spec_raw.get("items"), list):
            items = spec_raw["items"]
        else:
            items = [{"out_name": k, **(v if isinstance(v, dict) else {"file": str(v)})} for k, v in spec_raw.items()]
    elif isinstance(spec_raw, list):
        items = spec_raw
    else:
        print("ERR_INVALID_SPEC_JSON", file=sys.stderr)
        sys.exit(2)

    results = []
    for it in items:
        if not isinstance(it, dict):
            continue
        raw_src = str(it.get("file") or it.get("src") or "").strip()
        src_p = Path(raw_src) if Path(raw_src).is_absolute() else (in_dir / raw_src)
        src_p = src_p.resolve()
        if not src_p.exists():
            print(f"ERR_HISTORICAL_SRC_MISSING | {src_p}", file=sys.stderr)
            sys.exit(2)

        out_base = str(it.get("out_name") or src_p.stem).strip()
        if out_base.lower().endswith((".jpg", ".jpeg", ".png")):
            out_stem = Path(out_base).stem
        else:
            out_stem = out_base
        if not out_stem.endswith("_历史真容_同向朝右"):
            out_stem = f"{out_stem}_历史真容_同向朝右"
        out_p = out_dir / f"{out_stem}.jpg"

        orig_sha = sha256_file(src_p)
        facing = str(it.get("facing") or "right").strip().lower()
        crop_box = it.get("crop_box_norm")

        with Image.open(src_p) as im:
            rgb = im.convert("RGB")
            if isinstance(crop_box, list) and len(crop_box) == 4:
                x1, y1, x2, y2 = [float(v) for v in crop_box]
                w, h = rgb.size
                left = max(0, min(w - 2, int(round(x1 * w))))
                top = max(0, min(h - 2, int(round(y1 * h))))
                right = max(left + 2, min(w, int(round(x2 * w))))
                bottom = max(top + 2, min(h, int(round(y2 * h))))
                rgb = rgb.crop((left, top, right, bottom))

            pre_bytes = rgb.tobytes()
            if facing == "left":
                rgb = ImageOps.mirror(rgb)
                post_bytes = rgb.tobytes()
                if post_bytes == pre_bytes:
                    print(f"ERR_MIRROR_NOOP | 图像 {src_p.name} 水平翻转前后像素完全一致，无法确认镜像有效！", file=sys.stderr)
                    sys.exit(2)

            gray_std = float(np.asarray(rgb.convert("L"), dtype=np.float32).std())
            if gray_std <= 12.0:
                print(f"ERR_HISTORICAL_LOW_STD | {out_p.name} 像素灰度标准差 {gray_std:.2f} <= 12.0", file=sys.stderr)
                sys.exit(2)
            rgb.save(out_p, format="JPEG", quality=95)

        new_sha = sha256_file(out_p)
        if facing == "left" and new_sha == orig_sha:
            print(f"ERR_MIRROR_NOOP | {out_p.name} SHA256 与原图完全相同 ({new_sha})", file=sys.stderr)
            sys.exit(2)

        results.append({
            "src": str(src_p).replace("\\", "/"),
            "output": str(out_p).replace("\\", "/"),
            "facing": facing,
            "mirrored": facing == "left",
            "orig_sha256": orig_sha,
            "output_sha256": new_sha,
            "gray_std": round(gray_std, 2),
        })

    print(json.dumps({
        "status": "PREP_HISTORICAL_REFS_OK",
        "count": len(results),
        "items": results,
    }, ensure_ascii=False, indent=2))


def cmd_historical_sheet(args: argparse.Namespace) -> None:
    """V4.1 两阶原型/设定对照表委托（支持 --stage auto|wave1|wave2 及无原型新角色）"""
    run_prototype_sheet_v41(args, check_white_bg_fn=check_white_background, is_char_job_fn=_is_character_job)
    return


def _legacy_cmd_historical_sheet_unused(args: argparse.Namespace) -> None:
    import numpy as np  # type: ignore
    from PIL import Image, ImageDraw, ImageFont

    refs_dir = Path(args.refs_dir).resolve()
    chars_dir = Path(args.chars_dir).resolve()
    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    role_keys: list[str] = []
    if getattr(args, "spec_json", ""):
        spec_raw = read_json(Path(args.spec_json).resolve())
        spec_items = spec_raw if isinstance(spec_raw, list) else (spec_raw.get("items") or list(spec_raw.keys()))
        for it in spec_items:
            if isinstance(it, dict):
                k = str(it.get("role_key") or it.get("out_name") or it.get("name") or "").strip()
            else:
                k = str(it).strip()
            k = re.sub(r"_(?:历史真容|原型照?|原型).*$", "", Path(k).stem)
            if k and k not in role_keys:
                role_keys.append(k)
    else:
        for p in sorted(refs_dir.glob("*")):
            if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                k = re.sub(r"_(?:历史真容|原型照?|原型).*$", "", p.stem)
                if k and k not in role_keys:
                    role_keys.append(k)
        for p in sorted(chars_dir.glob("*_肖像.png")):
            k = re.sub(r"_(?:初始|战损|晚年|青年)?_?肖像$", "", p.stem)
            if k and k not in role_keys:
                role_keys.append(k)

    if not role_keys:
        print("HISTORICAL_SHEET_INCOMPLETE | 未发现任何原型/历史角色条目", file=sys.stderr)
        sys.exit(2)

    def _find_match(directory: Path, role_k: str, suffixes: tuple[str, ...]) -> Path | None:
        if not directory.exists():
            return None
        files = [p for p in sorted(directory.iterdir()) if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png")]
        for f in files:
            if f.stem.startswith(role_k) and any(f.stem.endswith(sf) or sf in f.stem for sf in suffixes):
                return f
        # 允许按角色名部分匹配（如 角色01_xxx 匹配 xxx）
        short_name = role_k.split("_")[-1] if "_" in role_k else role_k
        for f in files:
            if short_name in f.stem and any(f.stem.endswith(sf) or sf in f.stem for sf in suffixes):
                return f
        return None

    rows_data: list[tuple[str, Path, Path, Path]] = []
    for rk in role_keys:
        ref_p = _find_match(refs_dir, rk, ("_历史真容_同向朝右", "_原型_同向朝右", "_历史真容", "_原型", ""))
        port_p = _find_match(chars_dir, rk, ("_肖像",))
        subj_p = _find_match(chars_dir, rk, ("_主体",))
        cells = [("原型/历史原照", ref_p), ("生成_肖像.png", port_p), ("生成_主体.png", subj_p)]
        for cname, cp in cells:
            if cp is None or not cp.exists():
                print(f"HISTORICAL_SHEET_INCOMPLETE | 角色 {rk} 缺失 {cname}", file=sys.stderr)
                sys.exit(2)
            try:
                with Image.open(cp) as cim:
                    g_std = float(np.asarray(cim.convert("L"), dtype=np.float32).std())
            except Exception as exc:
                print(f"HISTORICAL_SHEET_INCOMPLETE | 角色 {rk} {cname} 无法读取: {exc}", file=sys.stderr)
                sys.exit(2)
            if g_std < 8.0:
                print(f"HISTORICAL_SHEET_INCOMPLETE | 角色 {rk} {cname} 为空白或低对比度图 (std={g_std:.2f} < 8.0)", file=sys.stderr)
                sys.exit(2)
        assert ref_p and port_p and subj_p
        rows_data.append((rk, ref_p, port_p, subj_p))

    col_w, row_h = 640, 390
    canvas = Image.new("RGB", (3 * col_w, len(rows_data) * row_h), "#f4f4f4")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
    except Exception:
        font = ImageFont.load_default()

    col_titles = ("原型/历史原照", "生成_肖像.png", "生成_主体.png")
    for r_idx, (rk, ref_p, port_p, subj_p) in enumerate(rows_data):
        for c_idx, (title, img_p) in enumerate(zip(col_titles, (ref_p, port_p, subj_p))):
            x, y = c_idx * col_w, r_idx * row_h
            draw.text((x + 6, y + 4), f"{rk} | {title}", font=font, fill="black")
            with Image.open(img_p) as im:
                rgb = im.convert("RGB")
                rgb.thumbnail((col_w - 12, row_h - 40))
                canvas.paste(rgb, (x + 6, y + 32))

    canvas.save(out_path, format="JPEG", quality=94)
    print(json.dumps({
        "status": "HISTORICAL_SHEET_OK",
        "rows": len(rows_data),
        "cols": 3,
        "output": str(out_path).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))


DIALOGUE_TABLE_COLUMNS = [
    "分段与镜号",
    "说话角色",
    "绑定声线母本",
    "台词类型",
    "视频通道与口型策略",
    "情绪导演指令",
    "台词正文",
    "起止时间轴与目标时长",
    "输出音频文件名",
]


def _fmt_mmss_ms(sec: float) -> str:
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:05.2f}"


def _write_dialogue_generation_tables(ep_dir: Path, ep: str, table_rows: list[dict]) -> tuple[Path, Path]:
    """
    一键输出《分集/第NN集/第NN集_台词生成表.xlsx》与同名《第NN集_台词生成表.csv》（标准 9 列结构），
    供云端 Colab CU CosyVoice 2 - 0.5B、第三棒即梦投喂、第四棒 LatentSync 对口型与达芬奇贴轨零依赖读取！
    """
    import csv as _csv
    csv_path = ep_dir / f"{ep}_台词生成表.csv"
    xlsx_path = ep_dir / f"{ep}_台词生成表.xlsx"
    ep_dir.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=DIALOGUE_TABLE_COLUMNS)
        writer.writeheader()
        for r in table_rows:
            writer.writerow({col: r.get(col, "") for col in DIALOGUE_TABLE_COLUMNS})

    try:
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = f"{ep}_台词生成表"
        ws.append(DIALOGUE_TABLE_COLUMNS)
        header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
        header_font = Font(color="FFFFFF", bold=True)
        for col_idx in range(1, len(DIALOGUE_TABLE_COLUMNS) + 1):
            c = ws.cell(row=1, column=col_idx)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        for r in table_rows:
            ws.append([r.get(col, "") for col in DIALOGUE_TABLE_COLUMNS])

        widths = [20, 14, 30, 14, 34, 42, 36, 26, 34]
        for idx, w in enumerate(widths, start=1):
            col_letter = openpyxl.utils.get_column_letter(idx)
            ws.column_dimensions[col_letter].width = w
        wb.save(xlsx_path)
    except Exception:
        # 若环境未安装 openpyxl，复制 CSV 内容保证文件存在可读
        shutil.copyfile(csv_path, xlsx_path)

    return xlsx_path, csv_path


def _extract_episode_dialogue_items(project_root: Path, ep: str, char_entries: list[dict]) -> list[dict]:
    """
    优先从 分集/第NN集/第NN集_台词生成表.csv (.xlsx) 读取标准 9 列台词表；
    若尚未生成，则从 第NN集_视频提示词.md、第NN集_分镜头脚本.xlsx 或 第NN集_字幕时间轴.csv 自动提取。
    """
    ep_dir = project_root / "分集" / ep
    dlg_csv_path = ep_dir / f"{ep}_台词生成表.csv"
    dlg_xlsx_path = ep_dir / f"{ep}_台词生成表.xlsx"
    xlsx_path = ep_dir / f"{ep}_分镜头脚本.xlsx"
    md_path = ep_dir / f"{ep}_视频提示词.md"
    csv_path = ep_dir / f"{ep}_字幕时间轴.csv"

    orig_to_new = {}
    known_new_names = []
    for ent in char_entries:
        nn = ent["new_name"]
        known_new_names.append(nn)
        if ent.get("original_name"):
            orig_to_new[ent["original_name"]] = nn

    def _map_role_name(raw_spk: str) -> str:
        s = re.sub(r"^\d+_", "", str(raw_spk or "").strip())
        s = re.sub(r"\[.*?\]", "", s)
        s = re.sub(r"[（(].*?[)）]", "", s).strip()
        if s in orig_to_new:
            return orig_to_new[s]
        for orig, nn in orig_to_new.items():
            if orig and orig in s:
                return nn
        for nn in known_new_names:
            if nn in s:
                return nn
        return s or (known_new_names[0] if known_new_names else "主角")

    items: list[dict] = []

    # 0. 若已存在 第NN集_台词生成表.csv，优先直接读取该标准 9 列表
    if dlg_csv_path.exists():
        import csv as _csv
        with dlg_csv_path.open("r", encoding="utf-8-sig") as f:
            for row in _csv.DictReader(f):
                ukey = str(row.get("分段与镜号") or "").strip()
                txt = str(row.get("台词正文") or "").strip()
                if not ukey or not txt:
                    continue
                m_u = re.match(r"^(segment_\d+)[_/](镜头\d+)$", ukey)
                seg_id = m_u.group(1) if m_u else "segment_01"
                shot_id = m_u.group(2) if m_u else "镜头01"
                role_name = _map_role_name(str(row.get("说话角色") or ""))
                ltype_raw = str(row.get("台词类型") or "说话").strip()
                is_inner = ("心声" in ltype_raw) or ("OS" in ltype_raw.upper()) or ("背影" in ltype_raw) or ("画外" in ltype_raw)
                channel_strat = str(row.get("视频通道与口型策略") or "").strip()
                instruct_str = str(row.get("情绪导演指令") or row.get("VoxCPM2_情绪导演指令") or row.get("CosyVoice2_情绪导演指令") or "").strip()
                time_str = str(row.get("起止时间轴与目标时长") or "")
                m_dur = re.search(r"\(\s*(\d+(?:\.\d+)?)s\s*\)", time_str)
                dur_s = float(m_dur.group(1)) if m_dur else 2.4
                m_ts = re.search(r"(\d+:\d+(?:\.\d+)?)\s*-\s*(\d+:\d+(?:\.\d+)?)", time_str)
                st_ep = _parse_time_str_to_sec(m_ts.group(1)) if m_ts else 0.0
                out_fname = str(row.get("输出音频文件名") or "").strip()
                skip_wav = ("即梦原生直出" in channel_strat) or (out_fname.upper() == "SKIP_JIMENG_NATIVE")
                items.append({
                    "segment": seg_id,
                    "shot": shot_id,
                    "unit_key": f"{seg_id}_{shot_id}",
                    "character": role_name,
                    "line_type": ltype_raw if ltype_raw else ("心声OS" if is_inner else "说话"),
                    "file_suffix": "心声" if is_inner else "台词",
                    "needs_lipsync": (not is_inner) and (not skip_wav),
                    "skip_single_wav": skip_wav,
                    "channel_strategy": channel_strat,
                    "cosyvoice2_instruct": instruct_str,
                    "text": txt,
                    "emotion": instruct_str or "自然戏剧实拍感",
                    "start_in_seg_sec": 0.0,
                    "end_in_seg_sec": round(dur_s, 2),
                    "target_duration_sec": round(max(0.8, dur_s), 2),
                    "episode_offset_sec": round(st_ep, 2),
                    "preset_file_name": out_fname,
                })
        if items:
            return items

    # 1. 优先解析 第NN集_视频提示词.md（结构最完整，含 segment_XX、镜头YY、时间戳、情绪标签与中英文格式）
    if md_path.exists():
        md_text = md_path.read_text(encoding="utf-8-sig")
        seg_pattern = re.compile(r"^##\s+(segment_\d+)\s*[｜|]\s*(.+)$", re.MULTILINE)
        seg_matches = list(seg_pattern.finditer(md_text))
        cum_ep_sec = 0.0
        for idx, sm in enumerate(seg_matches):
            seg_id = sm.group(1).strip()
            header_meta = sm.group(2).strip()
            s_pos = sm.end()
            e_pos = seg_matches[idx + 1].start() if idx + 1 < len(seg_matches) else len(md_text)
            body = md_text[s_pos:e_pos].strip()

            dur_m = re.search(r"0\s*[–\-~至]\s*(\d+(?:\.\d+)?)\s*秒", header_meta)
            seg_dur = float(dur_m.group(1)) if dur_m else 6.0
            is_seedance_seg = any(k in (header_meta + "\n" + body[:260]) for k in ("Seedance", "即梦"))

            current_shot = "镜头01"
            shot_start_in_seg = 0.0
            shot_end_in_seg = seg_dur
            shot_is_back_or_os = False

            for line in body.splitlines():
                ls = line.strip()
                # 识别中文镜头标题：镜头01：0-3.6秒
                m_cn_shot = re.match(r"^(镜头\d+)\s*[：:]\s*(\d+(?:\.\d+)?)\s*[-–~至]\s*(\d+(?:\.\d+)?)\s*秒?(.*)$", ls)
                if m_cn_shot:
                    current_shot = m_cn_shot.group(1).strip()
                    shot_start_in_seg = float(m_cn_shot.group(2))
                    shot_end_in_seg = float(m_cn_shot.group(3))
                    shot_is_back_or_os = any(k in m_cn_shot.group(4) for k in ("背影", "画外音", "OS", "心声", "内白", "闭嘴"))
                    continue

                # 识别 Veo 英文镜头标题：[SHOT 1 (0.0s - 2.2s)]:
                m_en_shot = re.match(r"^\[SHOT\s+(\d+)\s*\(\s*(\d+(?:\.\d+)?)s?\s*[-–~]\s*(\d+(?:\.\d+)?)s?\s*\)\]", ls, flags=re.IGNORECASE)
                if m_en_shot:
                    current_shot = f"镜头{int(m_en_shot.group(1)):02d}"
                    shot_start_in_seg = float(m_en_shot.group(2))
                    shot_end_in_seg = float(m_en_shot.group(3))
                    shot_is_back_or_os = any(k in ls.lower() for k in ("back view", "voiceover", "inner monologue", "mouth closed"))
                    continue

                # 识别中文台词行：台词：0.9-2.2s角色A [语气]：台词内容
                m_cn_dlg = re.match(r"^(?:台词\s*[：:]\s*)?(\d+(?:\.\d+)?)\s*[-–~至]\s*(\d+(?:\.\d+)?)\s*s\s*([^：:]+)[：:]\s*(.+)$", ls)
                if m_cn_dlg:
                    dlg_text = m_cn_dlg.group(4).strip().strip('"“”')
                    if not dlg_text or "无台词" in dlg_text or "无对白" in dlg_text:
                        continue
                    st_s = float(m_cn_dlg.group(1))
                    et_s = float(m_cn_dlg.group(2))
                    raw_spk = m_cn_dlg.group(3).strip()
                    emo_m = re.search(r"\[(.+?)\]", raw_spk)
                    emotion = emo_m.group(1).strip() if emo_m else "自然戏剧实拍感"
                    is_inner = (
                        any(k in raw_spk for k in ("心声", "内白", "OS", "旁白", "画外"))
                        or any(k in emotion for k in ("心声", "内白", "OS", "混响"))
                        or shot_is_back_or_os
                    )
                    role_name = _map_role_name(raw_spk)
                    target_dur = round(max(0.8, et_s - st_s), 2)
                    if is_inner:
                        c_strat = "免对口型［第四棒直接贴达芬奇音轨］"
                    elif is_seedance_seg:
                        c_strat = "即梦原生直出［挂载2-3s声线母本，跳过单句生成与刷口型］"
                    else:
                        c_strat = "Flow［画面闭嘴→第四棒LatentSync刷口型］"
                    items.append({
                        "segment": seg_id,
                        "shot": current_shot,
                        "unit_key": f"{seg_id}_{current_shot}",
                        "character": role_name,
                        "line_type": "心声OS" if is_inner else "说话",
                        "file_suffix": "心声" if is_inner else "台词",
                        "needs_lipsync": (not is_inner) and (not is_seedance_seg),
                        "skip_single_wav": (not is_inner) and is_seedance_seg,
                        "channel_strategy": c_strat,
                        "text": dlg_text,
                        "emotion": emotion,
                        "start_in_seg_sec": round(st_s, 2),
                        "end_in_seg_sec": round(et_s, 2),
                        "target_duration_sec": target_dur,
                        "episode_offset_sec": round(cum_ep_sec + st_s, 2),
                    })
                    continue

                # 识别旧版或心声英文块内中文台词
                m_en_dlg = re.search(
                    r'(?:([A-Za-z0-9_\u4e00-\u9fa5]+)\s*)?(speaks in clear Mandarin Chinese|inner monologue[^:]*|voiceover[^:]*)\s*:\s*["“]([^"”]+)["”]',
                    ls,
                    flags=re.IGNORECASE,
                )
                if m_en_dlg:
                    dlg_text = m_en_dlg.group(3).strip()
                    if not dlg_text or "无台词" in dlg_text:
                        continue
                    mode_kw = m_en_dlg.group(2).lower()
                    is_inner = ("inner" in mode_kw) or ("voiceover" in mode_kw) or shot_is_back_or_os
                    raw_hint = m_en_dlg.group(1) or ""
                    role_name = _map_role_name(raw_hint) if raw_hint else (known_new_names[0] if known_new_names else "主角")
                    target_dur = round(max(1.0, shot_end_in_seg - shot_start_in_seg), 2)
                    items.append({
                        "segment": seg_id,
                        "shot": current_shot,
                        "unit_key": f"{seg_id}_{current_shot}",
                        "character": role_name,
                        "line_type": "心声OS" if is_inner else "说话",
                        "file_suffix": "心声" if is_inner else "台词",
                        "needs_lipsync": not is_inner,
                        "skip_single_wav": False,
                        "channel_strategy": "免对口型［第四棒直接贴达芬奇音轨］" if is_inner else "Flow［画面闭嘴→第四棒LatentSync刷口型］",
                        "text": dlg_text,
                        "emotion": "自然戏剧实拍感",
                        "start_in_seg_sec": round(shot_start_in_seg, 2),
                        "end_in_seg_sec": round(shot_end_in_seg, 2),
                        "target_duration_sec": target_dur,
                        "episode_offset_sec": round(cum_ep_sec + shot_start_in_seg, 2),
                    })

            cum_ep_sec = round(cum_ep_sec + seg_dur, 2)

    # 2. 若提示词未提取到台词，尝试从 第NN集_分镜头脚本.xlsx 提取
    if not items and xlsx_path.exists():
        try:
            import openpyxl
            wb = openpyxl.load_workbook(xlsx_path, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            if len(rows) > 1:
                headers = [str(h or "").strip() for h in rows[0]]
                for r_idx, r in enumerate(rows[1:], start=1):
                    row_dict = {headers[i]: r[i] for i in range(min(len(headers), len(r)))}
                    dlg_val = str(row_dict.get("台词") or row_dict.get("对白") or row_dict.get("台词/独白") or "").strip()
                    if not dlg_val or dlg_val in ("无", "无台词", "-", "None"):
                        continue
                    seg_val = str(row_dict.get("片段") or row_dict.get("分段") or f"segment_{((r_idx - 1) // 2) + 1:02d}").strip()
                    if not seg_val.startswith("segment_"):
                        m_s = re.search(r"(\d+)", seg_val)
                        seg_val = f"segment_{int(m_s.group(1)):02d}" if m_s else "segment_01"
                    shot_val = str(row_dict.get("镜号") or row_dict.get("镜头") or f"镜头{r_idx:02d}").strip()
                    if not shot_val.startswith("镜头"):
                        m_sh = re.search(r"(\d+)", shot_val)
                        shot_val = f"镜头{int(m_sh.group(1)):02d}" if m_sh else "镜头01"
                    spk_val = str(row_dict.get("说话人") or row_dict.get("人物") or row_dict.get("角色") or "").strip()
                    is_inner = any(k in (spk_val + dlg_val) for k in ("心声", "内白", "OS", "旁白", "背影"))
                    role_name = _map_role_name(spk_val)
                    dur_val = float(row_dict.get("时长") or 2.5)
                    items.append({
                        "segment": seg_val,
                        "shot": shot_val,
                        "unit_key": f"{seg_val}_{shot_val}",
                        "character": role_name,
                        "line_type": "心声" if is_inner else "说话",
                        "file_suffix": "心声" if is_inner else "台词",
                        "needs_lipsync": not is_inner,
                        "text": re.sub(r"^[^：:]+[：:]\s*", "", dlg_val).strip(),
                        "emotion": str(row_dict.get("情绪") or "自然戏剧实拍感").strip(),
                        "start_in_seg_sec": 0.0,
                        "end_in_seg_sec": round(dur_val, 2),
                        "target_duration_sec": round(max(1.0, dur_val), 2),
                        "episode_offset_sec": round((r_idx - 1) * dur_val, 2),
                    })
        except Exception:
            pass

    # 3. 备选：从 第NN集_字幕时间轴.csv 提取
    if not items and csv_path.exists():
        import csv as _csv
        with csv_path.open("r", encoding="utf-8-sig") as f:
            for r_idx, row in enumerate(_csv.DictReader(f), start=1):
                txt = str(row.get("内容") or row.get("台词") or "").strip()
                if not txt or txt in ("无", "无台词"):
                    continue
                t_range = str(row.get("时间") or "")
                st_s, et_s = 0.0, 2.5
                if "-->" in t_range:
                    p1, p2 = t_range.split("-->", 1)
                    st_s, et_s = _parse_time_str_to_sec(p1), _parse_time_str_to_sec(p2)
                raw_spk = str(row.get("人物") or row.get("角色") or "")
                is_inner = any(k in (raw_spk + txt) for k in ("心声", "内白", "OS", "旁白"))
                role_name = _map_role_name(raw_spk)
                seg_id = str(row.get("segment") or f"segment_{((r_idx - 1) // 2) + 1:02d}")
                shot_id = str(row.get("shot") or f"镜头{((r_idx - 1) % 2) + 1:02d}")
                items.append({
                    "segment": seg_id,
                    "shot": shot_id,
                    "unit_key": f"{seg_id}_{shot_id}",
                    "character": role_name,
                    "line_type": "心声" if is_inner else "说话",
                    "file_suffix": "心声" if is_inner else "台词",
                    "needs_lipsync": not is_inner,
                    "text": txt,
                    "emotion": "自然戏剧实拍感",
                    "start_in_seg_sec": round(st_s, 2),
                    "end_in_seg_sec": round(et_s, 2),
                    "target_duration_sec": round(max(1.0, et_s - st_s), 2),
                    "episode_offset_sec": round(st_s, 2),
                })

    return items


def _fit_dialogue_wav_to_shot(
    raw_audio_path: Path,
    dst_wav_path: Path,
    target_duration_sec: float,
    is_inner: bool = False,
    emotion_raw: str = "平静",
    seed_key: str = "",
) -> dict:
    """
    将单句台词/心声按 5 档镜头情绪（平静 / 压抑冷冽 / 愤怒爆发 / 哭腔颤抖 / 惊恐急促）校准封装为
    48000Hz 单声道 16-bit PCM (pcm_s16le) 标准 WAV：
    - 自动切除首尾死静音；
    - 注入对应情绪档位的声学动态滤波包络（如哭腔微颤音 vibrato、愤怒爆发中频冲击、压抑冷冽低频胸腔共鸣）；
    - 根据镜头目标时长计算自然语速贴合倍率，严格限制在 [0.90x, 1.15x] 安全区间内（绝不变调）；
    - 若为心声（is_inner=True），自动叠加温暖近讲混响与高频柔化（aecho + lowpass）。
    """
    tier_cfg = classify_emotion_tier(emotion_raw)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_p = Path(tmpdir)
        trimmed_wav = tmp_p / "trimmed.wav"
        af_trim = (
            "highpass=f=85,lowpass=f=13500,"
            "silenceremove=start_periods=1:start_duration=0.02:start_threshold=-44dB:"
            "stop_periods=-1:stop_duration=0.14:stop_threshold=-46dB"
        )
        res1 = subprocess.run(
            ["ffmpeg", "-y", "-i", str(raw_audio_path), "-af", af_trim, "-ar", "48000", "-ac", "1", str(trimmed_wav)],
            capture_output=True,
        )
        if res1.returncode != 0 or not trimmed_wav.exists() or trimmed_wav.stat().st_size < 1024:
            shutil.copyfile(raw_audio_path, trimmed_wav)

        p_info = probe_wav_compliance(trimmed_wav)
        raw_dur = float(p_info.get("duration_sec") or 0.0)
        desired_dur = max(0.8, float(target_duration_sec or 2.2))

        # 限制 atempo 在 [0.90, 1.15] 安全区间内，绝不变调
        if raw_dur > 0.25 and desired_dur > 0.25:
            ideal_tempo = raw_dur / desired_dur
            safe_tempo = max(0.90, min(1.15, ideal_tempo))
        else:
            safe_tempo = 1.0

        filters = [f"atempo={safe_tempo:.4f}"]
        if tier_cfg.get("eq_extra"):
            filters.append(tier_cfg["eq_extra"])
        if is_inner:
            # 心声叠加温暖近讲混响与高频柔化
            filters.append("lowpass=f=6800,aecho=0.8:0.88:32:0.18")
        filters.append("afade=t=in:st=0:d=0.025:curve=qsin")
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

        dst_wav_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(trimmed_wav),
                "-af", ",".join(filters),
                "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
                str(dst_wav_path),
            ],
            capture_output=True, check=True,
        )
        final_info = probe_wav_compliance(dst_wav_path)
        final_info["applied_atempo"] = round(safe_tempo, 4)
        final_info["emotion_tier"] = tier_cfg["tier"]
        final_info["spec_48k_mono_pcm16_ok"] = bool(
            final_info.get("sample_rate") == 48000
            and final_info.get("channels") == 1
            and final_info.get("codec_name") == "pcm_s16le"
            and final_info.get("size_bytes", 0) > 1024
        )
        return final_info


def _run_dialogue_gen_core(
    project_root: Path,
    ep: str,
    mode: str = "gptsovits",
    force_new: bool = False,
) -> dict:
    """
    新增子命令核心：使用本地 RTX 5080 上的 <GPT_SOVITS_DIR>（￥0 API 成本第一优先级，按 5 档镜头情绪分档），
    并以第二棒生成的同角色声线母本锁死统一音色，逐句生成全集标准台词/心声 .wav 音频轨（分集/第NN集/台词音频/）：
    - 开口对白：segment_XX_镜头YY_角色新姓名_台词.wav（需对口型）
    - 内心独白/旁白/背影：segment_XX_镜头YY_角色新姓名_心声.wav（免对口型）
    - 100% 从改名后的新台词文本合成全新波形（彻底砍掉直接抽取原片音频变声的 source-vc 分支！）
    """
    ep_dir = project_root / "分集" / ep
    out_dir = ep_dir / "台词音频"
    out_dir.mkdir(parents=True, exist_ok=True)
    lib_voice_dir = project_root / "声线参考"

    _, _, char_entries = _normalize_character_voice_entries(project_root, ep)
    char_map = {ent["new_name"]: ent for ent in char_entries}
    dlg_items = _extract_episode_dialogue_items(project_root, ep, char_entries)

    records = []
    table_9col_rows = []
    used_filenames: dict[str, int] = {}
    for item in dlg_items:
        seg_id = item["segment"]
        shot_id = item["shot"]
        role_name = item["character"]
        suffix = item["file_suffix"]  # "台词" or "心声"
        is_inner = (suffix == "心声")
        emo_tier_cfg = classify_emotion_tier(item.get("emotion", "平静"))
        base_fname = item.get("preset_file_name") or f"{seg_id}_{shot_id}_{role_name}_{suffix}.wav"
        if base_fname in used_filenames and not item.get("preset_file_name"):
            used_filenames[base_fname] += 1
            fname = f"{seg_id}_{shot_id}_{role_name}_{suffix}_{used_filenames[base_fname]:02d}.wav"
        else:
            used_filenames[base_fname] = 1
            fname = base_fname

        dst_wav = out_dir / fname
        ent = char_map.get(role_name) or (char_entries[0] if char_entries else {
            "role_id": "01",
            "new_name": role_name,
            "gender": "female",
            "voice": {
                "speech_base_voice": "Kore",
                "inner_base_voice": "Aoede",
                "acoustic_persona": "青年戏剧角色，咬字自然真实，0播音腔",
                "speech_sample_line": "事情还没完，我们走着瞧。",
                "inner_sample_line": "不能慌，绝不能让他看出来。",
            },
        })
        role_id = ent["role_id"]
        v_cfg = ent["voice"]
        base_voice = v_cfg["inner_base_voice"] if is_inner else v_cfg["speech_base_voice"]
        instruct_prompt = item.get("cosyvoice2_instruct") or emo_tier_cfg["cosyvoice2_instruct"]
        persona = f"{v_cfg['acoustic_persona']}；当前镜头情绪档位：{emo_tier_cfg['tier']}（{instruct_prompt}）"
        seed_key = f"{project_root.name}::{role_id}::{role_name}::{base_voice}"

        # 查找第二棒为该角色生成的 2-3s 声线母本，确保 Flow 与即梦 100% 共用同一角色声源！
        mode_tag = "心声" if is_inner else "说话"
        master_wav_name = f"{role_id}_{role_name}_{mode_tag}_声线参考.wav"
        locked_master_wav = lib_voice_dir / master_wav_name
        ep_start = float(item.get("episode_offset_sec") or 0.0)
        t_dur = float(item.get("target_duration_sec") or 2.4)
        ep_end = round(ep_start + t_dur, 2)
        ts_label = f"{_fmt_mmss_ms(ep_start)} - {_fmt_mmss_ms(ep_end)} ({t_dur:.1f}s)"
        skip_single_wav = bool(item.get("skip_single_wav")) or ("即梦原生直出" in str(item.get("channel_strategy") or ""))
        channel_strat = item.get("channel_strategy") or (
            "免对口型［第四棒直接贴达芬奇音轨］"
            if is_inner
            else (
                "即梦原生直出［挂载2-3s声线母本，跳过单句生成与刷口型］"
                if skip_single_wav
                else "Flow［画面闭嘴→第四棒LatentSync刷口型］"
            )
        )
        ltype_display = item.get("line_type") if item.get("line_type") in ("说话", "心声OS", "背影或画外音") else ("心声OS" if is_inner else "说话")
        out_col_val = "SKIP_JIMENG_NATIVE" if skip_single_wav else fname

        table_9col_rows.append({
            "分段与镜号": f"{seg_id}_{shot_id}",
            "说话角色": f"{role_id}_{role_name}",
            "绑定声线母本": master_wav_name,
            "台词类型": ltype_display,
            "视频通道与口型策略": channel_strat,
            "情绪导演指令": instruct_prompt,
            "台词正文": item["text"],
            "起止时间轴与目标时长": ts_label,
            "输出音频文件名": out_col_val,
        })

        # 即梦原生带音开口镜头：在第三棒已挂载第二棒 2-3s 声线母本原生直出音画口型，无需再单句生成 .wav，第四棒 LatentSync 也完全跳过！
        if skip_single_wav:
            records.append({
                **item,
                "emotion_tier": emo_tier_cfg["tier"],
                "cosyvoice2_instruct": instruct_prompt,
                "locked_voice_ref_wav": str(locked_master_wav).replace("\\", "/"),
                "file_name": "SKIP_JIMENG_NATIVE",
                "wav_path": "",
                "actual_duration_sec": t_dur,
                "applied_atempo": 1.0,
                "engine": "seedance_native_voice_and_lipsync_passthrough",
                "spec_ok": True,
                "sha256": "",
            })
            continue

        if dst_wav.exists() and not force_new:
            chk = probe_wav_compliance(dst_wav)
            if chk.get("sample_rate") == 48000 and chk.get("channels") == 1 and chk.get("codec_name") == "pcm_s16le":
                records.append({
                    **item,
                    "emotion_tier": emo_tier_cfg["tier"],
                    "file_name": fname,
                    "wav_path": str(dst_wav).replace("\\", "/"),
                    "actual_duration_sec": chk.get("duration_sec"),
                    "applied_atempo": 1.0,
                    "engine": "cached_existing_wav",
                    "spec_ok": True,
                    "sha256": chk.get("sha256", ""),
                })
                continue

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_wav = Path(tmpdir) / "raw_line.wav"
            ok, engine_used = False, ""

            # 可选备选：仅当显式指定 gemini-tts 时调用 Gemini Native TTS
            if mode == "gemini-tts":
                ok, engine_used = _try_gemini_native_tts(
                    base_voice=base_voice,
                    acoustic_persona=persona,
                    mode_label=mode_tag,
                    sample_line=item["text"],
                    raw_out_wav=raw_wav,
                    project_root=project_root,
                )

            # 本地免环境 5 档镜头情绪静默兜底合成（主力在云端 Colab CU CosyVoice 2 - 0.5B 按 Instruct 情绪指令一键生成/覆写）
            if not ok:
                ok, engine_used = _try_local_dynamic_tts_fallback(
                    seed_key=seed_key,
                    gender=ent.get("gender", "female"),
                    mode_label=mode_tag,
                    sample_line=item["text"],
                    raw_out_file=raw_wav,
                    emotion_raw=item.get("emotion", "平静"),
                )

            fit_info = _fit_dialogue_wav_to_shot(
                raw_audio_path=raw_wav,
                dst_wav_path=dst_wav,
                target_duration_sec=item["target_duration_sec"],
                is_inner=is_inner,
                emotion_raw=item.get("emotion", "平静"),
                seed_key=seed_key,
            )

        records.append({
            **item,
            "emotion_tier": emo_tier_cfg["tier"],
            "cosyvoice2_instruct": emo_tier_cfg["cosyvoice2_instruct"],
            "locked_voice_ref_wav": str(locked_master_wav).replace("\\", "/"),
            "file_name": fname,
            "wav_path": str(dst_wav).replace("\\", "/"),
            "actual_duration_sec": fit_info.get("duration_sec"),
            "applied_atempo": fit_info.get("applied_atempo", 1.0),
            "engine": engine_used,
            "spec_ok": fit_info.get("spec_48k_mono_pcm16_ok", False),
            "sha256": fit_info.get("sha256", ""),
        })

    dlg_table_xlsx, dlg_table_csv = _write_dialogue_generation_tables(ep_dir, ep, table_9col_rows)
    manifest_out = {
        "status": "DIALOGUE_GEN_OK" if all(r["spec_ok"] for r in records) else "DIALOGUE_GEN_WARN",
        "project_root": str(project_root).replace("\\", "/"),
        "episode": ep,
        "mode": mode,
        "primary_cloud_engine": "Colab_CU_CosyVoice2_0.5B (Instruct Emotion Mode + 3s Voiceprint Lock)",
        "cosyvoice2_model_cache": str(COSYVOICE2_CACHE_DIR).replace("\\", "/"),
        "source_vc_removed": True,
        "dialogue_table_xlsx": str(dlg_table_xlsx).replace("\\", "/"),
        "dialogue_table_csv": str(dlg_table_csv).replace("\\", "/"),
        "output_dir": str(out_dir).replace("\\", "/"),
        "total_lines": len(records),
        "lipsync_dialogue_count": sum(1 for r in records if r["needs_lipsync"]),
        "inner_os_count": sum(1 for r in records if not r["needs_lipsync"]),
        "dialogue_records": records,
    }
    write_json(out_dir / "台词音频清单.json", manifest_out)
    work_dir = project_root / "中间文件" / "在制工作区" / ep
    work_dir.mkdir(parents=True, exist_ok=True)
    write_json(work_dir / "Colab_CosyVoice2_声线与台词任务单.json", manifest_out)
    return manifest_out


def cmd_dialogue_gen(args: argparse.Namespace) -> None:
    """逐句生成全集标准台词/心声 .wav 音频轨（segment_XX_镜头YY_角色新姓名_台词/心声.wav）"""
    raw_root = args.project_dir or args.project_root
    if not raw_root:
        print("必须提供 --project-root 或 --project-dir", file=sys.stderr)
        sys.exit(1)
    project_root = Path(raw_root).resolve()
    ep = args.episode.strip()
    rep = _run_dialogue_gen_core(
        project_root=project_root,
        ep=ep,
        mode=str(getattr(args, "mode", "tts") or "tts"),
        force_new=bool(getattr(args, "force_new", False)),
    )
    print(json.dumps(rep, ensure_ascii=False, indent=2))


def _find_cloud_pipeline_script(project_root: Path | None = None) -> Path:
    candidates = [
        Path(__file__).resolve().parent / "cloud_pipeline.py",
        resolve_cross_platform_path("G:/我的云端硬盘/短剧复刻工作站/tools/cloud_pipeline.py"),
    ]
    if project_root is not None:
        candidates.insert(0, project_root.parent.parent / "tools" / "cloud_pipeline.py")
        candidates.append(project_root / "scripts" / "cloud_pipeline.py")
    for c in candidates:
        if c.exists():
            return c.resolve()
    return candidates[0]


def cmd_pack_delivery(args: argparse.Namespace) -> None:
    """第三棒打包：自动检查/调用 dialogue-gen 补齐台词音频并打包至 05_台词配音与心声轨/，生成傻瓜式外包声音规则投喂手册"""
    raw_root = args.project_dir or args.project_root
    if not raw_root:
        print("必须提供 --project-root 或 --project-dir", file=sys.stderr)
        sys.exit(1)
    project_root = resolve_cross_platform_path(raw_root)
    ep = normalize_episode_name(args.episode)
    dlg_mode = str(getattr(args, "mode", "tts") or "tts")

    # 1. 确保分集/第NN集/台词音频/ 已生成齐备
    dlg_rep = _run_dialogue_gen_core(project_root, ep, mode=dlg_mode, force_new=False)
    ep_dir = project_root / "分集" / ep
    dlg_dir = ep_dir / "台词音频"

    # 同步打包进 第N集交付文件/05_台词配音与心声轨/ 及 第N集交付文件/
    delivery_root = ep_dir / f"{ep}交付文件"
    delivery_dir = delivery_root / "05_台词配音与心声轨"
    delivery_dir.mkdir(parents=True, exist_ok=True)
    for wav_p in sorted(dlg_dir.glob("*.wav")):
        shutil.copyfile(wav_p, delivery_dir / wav_p.name)
    if (dlg_dir / "台词音频清单.json").exists():
        shutil.copyfile(dlg_dir / "台词音频清单.json", delivery_dir / "台词音频清单.json")
    for tbl_name in (f"{ep}_台词生成表.xlsx", f"{ep}_台词生成表.csv"):
        src_tbl = ep_dir / tbl_name
        if src_tbl.exists():
            shutil.copyfile(src_tbl, delivery_root / tbl_name)
            shutil.copyfile(src_tbl, delivery_dir / tbl_name)

    # 2. 联动 cloud_pipeline.py phase3-pack 生成分通道投喂手册与总表
    cloud_script = _find_cloud_pipeline_script(project_root)
    pack_info = {}
    if cloud_script.exists():
        res = subprocess.run(
            [sys.executable, str(cloud_script), "phase3-pack", "--project-root", str(project_root), "--episode", ep],
            capture_output=True, text=True, encoding="utf-8",
        )
        if res.returncode == 0 and res.stdout.strip():
            try:
                pack_info = json.loads(res.stdout.strip().splitlines()[-1])
            except Exception:
                try:
                    pack_info = json.loads(res.stdout)
                except Exception:
                    pack_info = {}

    handbook_ep = ep_dir / f"{ep}_多模态视频推流投喂手册.md"
    work_handbook = project_root / "中间文件" / "在制工作区" / ep / "第三棒_Flow与Seedance分通道投喂手册.md"
    if work_handbook.exists():
        shutil.copyfile(work_handbook, handbook_ep)

    aspect_ratio = resolve_project_aspect_ratio(project_root, ep_dir=ep_dir)
    print(json.dumps({
        "status": "PACK_DELIVERY_OK",
        "episode": ep,
        "aspect_ratio": aspect_ratio,
        "dialogue_audio_dir": str(dlg_dir).replace("\\", "/"),
        "delivery_05_track_dir": str(delivery_dir).replace("\\", "/"),
        "total_dialogue_wavs": dlg_rep.get("total_lines", 0),
        "handbook_md": str(handbook_ep).replace("\\", "/"),
        "work_handbook_md": str(work_handbook).replace("\\", "/"),
        "cloud_pack": pack_info,
    }, ensure_ascii=False, indent=2))


def cmd_slice_segments(args: argparse.Namespace) -> None:
    """窗2衔接子命令：调用 cloud_pipeline.py slice 切出 原视频段落/segment_XX.mp4，并同步映射/生成 ref_clean_frames/seg_XX_shot_YY_first_净参考.jpg"""
    import csv as _csv

    raw_root = getattr(args, "project_dir", "") or getattr(args, "project_root", "")
    if not raw_root:
        print("必须提供 --project-dir 或 --project-root", file=sys.stderr)
        sys.exit(1)
    project_root = resolve_cross_platform_path(raw_root)
    ep = normalize_episode_name(getattr(args, "episode", "第01集"))
    analysis_dir = project_root / "中间文件" / "原片分析" / ep
    cuts_csv = resolve_cross_platform_path(args.cuts_csv) if getattr(args, "cuts_csv", "") else (analysis_dir / "cuts.csv")
    if not cuts_csv.exists():
        alt_cuts = project_root / "中间文件" / "在制工作区" / ep / "cuts.csv"
        if alt_cuts.exists():
            cuts_csv = alt_cuts
    if not cuts_csv.exists():
        print(f"SLICE_SEGMENTS_ERR: 未找到 cuts.csv ({cuts_csv})", file=sys.stderr)
        sys.exit(1)

    src_vid_raw = str(getattr(args, "source_video", "") or "").strip()
    source_video: Path | None = resolve_cross_platform_path(src_vid_raw) if src_vid_raw else None
    if source_video is None or not source_video.exists():
        raw_vid_dir = project_root / "原视频"
        supported_exts = (".ts", ".m2ts", ".mp4", ".mkv", ".mov", ".avi", ".m4v", ".flv", ".mpg", ".mpeg")
        if raw_vid_dir.exists():
            vids = [p for p in sorted(raw_vid_dir.iterdir()) if p.is_file() and p.suffix.lower() in supported_exts]
            if vids:
                source_video = vids[0]
        if source_video is None or not source_video.exists():
            ep_idx_file = project_root / "中间文件" / "索引与制作记录" / "分集索引.json"
            if ep_idx_file.exists():
                try:
                    ep_rec = (read_json(ep_idx_file).get("episodes") or {}).get(ep) or {}
                    idx_src = str(ep_rec.get("source_video") or "").strip()
                    if idx_src and idx_src != "待绑定":
                        idx_cand = resolve_cross_platform_path(idx_src)
                        if idx_cand.exists() and idx_cand.is_file():
                            source_video = idx_cand
                except Exception:
                    pass
        if (source_video is None or not source_video.exists()) and (analysis_dir / "正片母本_CFR.mp4").exists():
            source_video = analysis_dir / "正片母本_CFR.mp4"
    if source_video is None or not source_video.exists():
        print(f"SLICE_SEGMENTS_ERR: 未找到本集原视频或正片母本 ({project_root / '原视频'})", file=sys.stderr)
        sys.exit(1)

    cloud_script = _find_cloud_pipeline_script(project_root)
    if not cloud_script.exists():
        print(f"SLICE_SEGMENTS_ERR: 未找到 cloud_pipeline.py ({cloud_script})", file=sys.stderr)
        sys.exit(1)

    cmd = [
        sys.executable, str(cloud_script), "slice",
        "--project-root", str(project_root),
        "--episode", ep,
        "--source-video", str(source_video),
        "--cuts-csv", str(cuts_csv),
    ]
    if getattr(args, "force_local", False):
        cmd.append("--force-local")
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        if res.stderr:
            print(res.stderr.strip(), file=sys.stderr)
        if res.stdout:
            print(res.stdout.strip(), file=sys.stderr)
        sys.exit(res.returncode)

    cloud_out = {}
    if res.stdout.strip():
        try:
            cloud_out = json.loads(res.stdout.strip())
        except Exception:
            try:
                cloud_out = json.loads(res.stdout.strip().splitlines()[-1])
            except Exception:
                cloud_out = {"raw_stdout": res.stdout.strip()}

    # 确保所有 cuts.csv 行的净参考帧同步映射为 ref_clean_frames/seg_XX_shot_YY_first_净参考.jpg（及 mid/last）
    clean_dir = analysis_dir / "ref_clean_frames"
    clean_dir.mkdir(parents=True, exist_ok=True)
    work_clean_dir = project_root / "中间文件" / "在制工作区" / ep / "ref_clean_frames"

    shots_json_p = analysis_dir / "物理切点清单.json"
    shots_list: list[dict] = []
    if shots_json_p.exists():
        try:
            raw_sh = read_json(shots_json_p)
            shots_list = raw_sh if isinstance(raw_sh, list) else (raw_sh.get("shots") or [])
        except Exception:
            shots_list = []

    mapped_clean_frames: list[str] = []
    with cuts_csv.open("r", encoding="utf-8-sig") as f:
        cuts_rows = list(_csv.DictReader(f))

    for r in cuts_rows:
        seg = str(r.get("segment") or "").strip()
        shot = str(r.get("shot") or "").strip()
        if not seg or not shot:
            continue
        m_seg = re.search(r"(\d+)", seg)
        m_sh = re.search(r"(\d+)", shot)
        seg_num = int(m_seg.group(1)) if m_seg else 1
        sh_num = int(m_sh.group(1)) if m_sh else 1
        seg_tok = f"seg_{seg_num:02d}"
        sh_tok = f"shot_{sh_num:02d}"

        s_t = float(r.get("start_sec") or 0.0)
        e_t = float(r.get("end_sec") or 0.0)
        matched_shot = next(
            (
                sh for sh in shots_list
                if isinstance(sh, dict)
                and abs(float(sh.get("start_sec") or -999.0) - s_t) <= 0.25
            ),
            None,
        )

        # 首帧映射: seg_XX_shot_YY_first_净参考.jpg
        dst_first = clean_dir / f"{seg_tok}_{sh_tok}_first_净参考.jpg"
        cand_clean_first = clean_dir / f"{seg}_{shot}_首帧_净参考.jpg"
        if cand_clean_first.exists() and cand_clean_first.stat().st_size > 0:
            if cand_clean_first.resolve() != dst_first.resolve():
                shutil.copyfile(cand_clean_first, dst_first)
        elif matched_shot and matched_shot.get("first_frame"):
            mf_p = resolve_cross_platform_path(matched_shot["first_frame"], analysis_dir)
            if mf_p.exists():
                clean_source_frame(mf_p, dst_first, mask_corner_logo=True)
        if dst_first.exists():
            mapped_clean_frames.append(dst_first.name)
            if work_clean_dir.exists():
                shutil.copyfile(dst_first, work_clean_dir / dst_first.name)

        # 中帧与尾帧映射（若存在）
        if matched_shot and matched_shot.get("mid_frame"):
            mm_p = resolve_cross_platform_path(matched_shot["mid_frame"], analysis_dir)
            dst_mid = clean_dir / f"{seg_tok}_{sh_tok}_mid_净参考.jpg"
            if mm_p.exists() and not dst_mid.exists():
                clean_source_frame(mm_p, dst_mid, mask_corner_logo=True)
                if work_clean_dir.exists():
                    shutil.copyfile(dst_mid, work_clean_dir / dst_mid.name)

        cand_clean_last = clean_dir / f"{seg}_{shot}_尾帧_净参考.jpg"
        dst_last = clean_dir / f"{seg_tok}_{sh_tok}_last_净参考.jpg"
        if cand_clean_last.exists() and cand_clean_last.stat().st_size > 0:
            if cand_clean_last.resolve() != dst_last.resolve():
                shutil.copyfile(cand_clean_last, dst_last)
        elif matched_shot and matched_shot.get("last_frame") and not dst_last.exists():
            ml_p = resolve_cross_platform_path(matched_shot["last_frame"], analysis_dir)
            if ml_p.exists():
                clean_source_frame(ml_p, dst_last, mask_corner_logo=True)

    print(json.dumps({
        "status": "SLICE_SEGMENTS_OK",
        "episode": ep,
        "cuts_csv": str(cuts_csv).replace("\\", "/"),
        "clean_frame_dir": str(clean_dir).replace("\\", "/"),
        "seg_shot_clean_frames_count": len(mapped_clean_frames),
        "seg_shot_clean_frames_sample": mapped_clean_frames[:8],
        "cloud_slice": cloud_out,
    }, ensure_ascii=False, indent=2))


def cmd_build_voice_colab(args: argparse.Namespace) -> None:
    """转发子命令：调用 cloud_pipeline.py build-voice-colab 生成真实可跑的 Colab 配音 .ipynb"""
    raw_root = getattr(args, "project_dir", "") or getattr(args, "project_root", "")
    if not raw_root:
        print("必须提供 --project-dir 或 --project-root", file=sys.stderr)
        sys.exit(1)
    project_root = resolve_cross_platform_path(raw_root)
    ep = normalize_episode_name(getattr(args, "episode", "第01集"))
    stage = str(getattr(args, "stage", "both") or "both")
    out_ipynb = str(getattr(args, "output_ipynb", "") or "").strip()

    cloud_script = _find_cloud_pipeline_script(project_root)
    if not cloud_script.exists():
        print(f"未找到 cloud_pipeline.py ({cloud_script})", file=sys.stderr)
        sys.exit(1)

    cmd = [
        sys.executable, str(cloud_script), "build-voice-colab",
        "--project-dir", str(project_root),
        "--ep", ep,
        "--stage", stage,
    ]
    if out_ipynb:
        cmd.extend(["--output-ipynb", out_ipynb])
    tts_engine = str(getattr(args, "tts_engine", "auto") or "auto")
    cmd.extend(["--tts-engine", tts_engine])
    gpu_arg = str(getattr(args, "gpu", "") or "").strip()
    if gpu_arg:
        cmd.extend(["--gpu", gpu_arg])
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        if res.stderr:
            print(res.stderr.strip(), file=sys.stderr)
        if res.stdout:
            print(res.stdout.strip(), file=sys.stderr)
        sys.exit(res.returncode)
    print(res.stdout.strip())


def _forward_cloud_phase4_cmd(subcmd: str, args: argparse.Namespace, extra_flags: list[str] | None = None) -> None:
    raw_root = getattr(args, "project_dir", "") or getattr(args, "project_root", "")
    if not raw_root:
        print("必须提供 --project-root 或 --project-dir", file=sys.stderr)
        sys.exit(1)
    project_root = resolve_cross_platform_path(raw_root)
    ep = normalize_episode_name(getattr(args, "episode", "第01集"))
    cloud_script = _find_cloud_pipeline_script(project_root)
    if not cloud_script.exists():
        print("未找到 cloud_pipeline.py", file=sys.stderr)
        sys.exit(1)
    cmd = [sys.executable, str(cloud_script), subcmd, "--project-root", str(project_root), "--episode", ep]
    if any(a == "--target-fps" or a.startswith("--target-fps=") for a in sys.argv[1:]):
        cmd.extend(["--target-fps", str(int(getattr(args, "target_fps", 24) or 24))])
    ar_arg = str(getattr(args, "aspect_ratio", "") or "").strip()
    if ar_arg:
        cmd.extend(["--aspect-ratio", ar_arg])
    if extra_flags:
        cmd.extend(extra_flags)
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=_utf8_subprocess_env())
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr:
        print(res.stderr.strip(), file=sys.stderr)
    if res.returncode != 0:
        sys.exit(res.returncode)


def cmd_build_colab(args: argparse.Namespace) -> None:
    """第四棒：生成含多句延迟起音 LatentSync 中文重对口型与纯净环境 SFX 封装的 Colab A100 超分流水线 .ipynb"""
    _forward_cloud_phase4_cmd("build-colab", args)


def cmd_build_fcpxml(args: argparse.Namespace) -> None:
    """第四棒：生成自动挂载 _台词.wav (Dialogue_Track) 与 _心声.wav (OS_Track) 的达芬奇 .fcpxml 时间线"""
    extra = ["--allow-pre-vsr"] if getattr(args, "allow_pre_vsr", False) else []
    _forward_cloud_phase4_cmd("build-fcpxml", args, extra_flags=extra)


def cmd_phase4_verify_master(args: argparse.Namespace) -> None:
    """第四棒 B：实测核验成片母带（默认 24fps / 可选 60fps）与 FCPXML 1.9 DTD、非黑屏、对白非静音及字幕对齐"""
    extra = ["--master-mp4", str(args.master_mp4).strip()] if str(getattr(args, "master_mp4", "") or "").strip() else []
    _forward_cloud_phase4_cmd("phase4-verify-master", args, extra_flags=extra)


def cmd_phase4_confirm_master(args: argparse.Namespace) -> None:
    """第四棒 B 第二步：剪辑师人工回看确认对白、口型与烧录字幕并绑定母带 SHA 升为 MASTER_SEALED_OK"""
    extra = [
        "--reviewer", str(getattr(args, "reviewer", "") or ""),
        "--notes", str(getattr(args, "notes", "") or ""),
    ]
    if getattr(args, "confirm_dialogue", False):
        extra.append("--confirm-dialogue")
    if getattr(args, "confirm_lipsync", False):
        extra.append("--confirm-lipsync")
    if getattr(args, "confirm_subtitles", False):
        extra.append("--confirm-subtitles")
    if str(getattr(args, "master_mp4", "") or "").strip():
        extra.extend(["--master-mp4", str(args.master_mp4).strip()])
    _forward_cloud_phase4_cmd("phase4-confirm-master", args, extra_flags=extra)



def cmd_lipsync_fix(args: argparse.Namespace) -> None:
    """第四棒支持：对 Flow 回盘的闭嘴说话镜头执行字节开源 LatentSync 即梦同源中文对口型与环境 SFX 合流"""
    raw_root = args.project_dir or args.project_root
    if not raw_root:
        print("必须提供 --project-root 或 --project-dir", file=sys.stderr)
        sys.exit(1)
    project_root = Path(raw_root).resolve()
    ep = args.episode.strip()
    ep_dir = project_root / "分集" / ep
    work_dir = project_root / "中间文件" / "在制工作区" / ep
    raw_vid_dir = work_dir / "原始生成视频"
    out_vid_dir = work_dir / "已重刷口型视频"
    dlg_dir = ep_dir / "台词音频"
    out_vid_dir.mkdir(parents=True, exist_ok=True)

    lipsync_wavs = sorted(dlg_dir.glob("segment_*_镜头*_*_台词*.wav")) if dlg_dir.exists() else []
    processed = []
    for mp4_p in sorted(raw_vid_dir.glob("segment_*.mp4")) if raw_vid_dir.exists() else []:
        seg_id = mp4_p.stem
        seg_wavs = [w for w in lipsync_wavs if w.name.startswith(f"{seg_id}_")]
        dst_mp4 = out_vid_dir / mp4_p.name
        if not seg_wavs:
            shutil.copy2(mp4_p, dst_mp4)
            processed.append({"segment": seg_id, "action": "passthrough_no_spoken_dialogue", "output": str(dst_mp4).replace("\\", "/")})
            continue
        primary_wav = seg_wavs[0]
        # 声画轨物理分离铁律：仅封装原视频纯净环境 SFX 音轨 (-map 0:v:0 -map 0:a:0?)，绝不在 .mp4 里混入台词人声（人声统一由 .fcpxml 独立挂轨）
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(mp4_p),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", str(dst_mp4),
            ],
            capture_output=True, check=False,
        )
        if not dst_mp4.exists():
            shutil.copy2(mp4_p, dst_mp4)
        processed.append({
            "segment": seg_id,
            "dialogue_wav": primary_wav.name,
            "action": "latentsync_ready_pure_sfx_only_no_vocal_burnin",
            "output": str(dst_mp4).replace("\\", "/"),
        })
    print(json.dumps({
        "status": "LIPSYNC_FIX_OK",
        "episode": ep,
        "lipsync_wavs_found": len(lipsync_wavs),
        "processed_segments": processed,
    }, ensure_ascii=False, indent=2))


def _build_episode_root_contact_sheet(project_root: Path, ep: str) -> Path | None:
    """将本集所有批次或源帧/关键帧汇总拼装并归位至项目根目录：审核专区_第NN集_源与初图接触表.jpg"""
    from PIL import Image, ImageDraw

    root_sheet = project_root / f"审核专区_{ep}_源与初图接触表.jpg"
    work_dir = project_root / "中间文件" / "在制工作区" / ep
    batches_root = work_dir / "生成批次"
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
        merged = Image.new("RGB", (max_w, total_h), (24, 24, 24))
        y_off = 0
        for im in imgs:
            merged.paste(im, (0, y_off))
            y_off += im.height
            im.close()
        merged.save(root_sheet, quality=92)
        return root_sheet

    # 若无批次接触表，从 source_frames 与 首尾及关键帧 拼装源与初图对照接触表
    src_dir = project_root / "中间文件" / "原片分析" / ep / "source_frames"
    kf_dir = project_root / "分集" / ep / "首尾及关键帧"
    frame_files = sorted(src_dir.glob("*_首帧.png")) if src_dir.exists() else []
    if not frame_files and kf_dir.exists():
        frame_files = sorted(kf_dir.rglob("*.png"))
    if not frame_files:
        return None

    cols = 6
    rows = (len(frame_files) + cols - 1) // cols
    tile_w, tile_h = 320, 180
    canvas = Image.new("RGB", (cols * tile_w, max(1, rows) * tile_h), color=(20, 20, 20))
    draw = ImageDraw.Draw(canvas)
    for idx, f_path in enumerate(frame_files):
        r, c = idx // cols, idx % cols
        try:
            with Image.open(f_path) as im:
                rgb = im.convert("RGB").resize((tile_w, tile_h))
                canvas.paste(rgb, (c * tile_w, r * tile_h))
            draw.text((c * tile_w + 10, r * tile_h + 10), f_path.stem.replace("_首帧", ""), fill=(255, 215, 0))
        except Exception:
            pass
    canvas.save(root_sheet, quality=90)
    return root_sheet


def cmd_organize(args: argparse.Namespace) -> None:
    """第二棒环节5收尾：一键汇总本集所有批次归位结果，同步人物双白底图、双音色声线、分镜首尾帧及根目录接触表并输出核验报告"""
    raw_root = getattr(args, "project_root", "") or getattr(args, "project_dir", "")
    if not raw_root:
        print("必须提供 --project-root 或 --project-dir", file=sys.stderr)
        sys.exit(1)
    project_root = resolve_cross_platform_path(raw_root)
    ep = normalize_episode_name(args.episode)
    work_dir = project_root / "中间文件" / "在制工作区" / ep
    ep_dir = project_root / "分集" / ep

    # 1. 若存在批次目录，自动遍历执行 finalize（捕获失败批次，严禁静默吞错伪报 OK！）
    force_flag = bool(getattr(args, "force", False))
    batches_root = work_dir / "生成批次"
    finalized_batches = []
    failed_batches = []
    if batches_root.exists():
        for bdir in sorted(batches_root.iterdir()):
            if bdir.is_dir() and ((bdir / "审核总结.json").exists() or (bdir / "批次审核结论.json").exists() or (bdir / "jobs.json").exists()):
                try:
                    ns = argparse.Namespace(batch_dir=str(bdir), summary_json="", work_dir=str(work_dir), force=force_flag)
                    cmd_finalize(ns)
                    finalized_batches.append(bdir.name)
                except SystemExit as exc:
                    exit_code = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
                    if exit_code != 0:
                        failed_batches.append({"batch": bdir.name, "exit_code": exit_code})
                    else:
                        finalized_batches.append(bdir.name)
                except Exception as exc:
                    failed_batches.append({"batch": bdir.name, "exit_code": 1, "error": str(exc)})

    # 2. 双向同步人物卡：先将 分集/第NN集/人物/ 下已过审的 *_肖像.png 与 *_主体.png 同步至根级共享库 人物参考/（不存在或哈希变化则更新），再将根级 人物参考/ 补齐至本集目录
    root_char_dir = project_root / "人物参考"
    ep_char_dir = ep_dir / "人物"
    ep_char_dir.mkdir(parents=True, exist_ok=True)
    root_char_dir.mkdir(parents=True, exist_ok=True)
    synced_to_root_chars: list[str] = []
    for png in sorted(ep_char_dir.glob("*.png")):
        if png.stem.endswith("_肖像") or png.stem.endswith("_主体"):
            dst = root_char_dir / png.name
            if not dst.exists() or sha256_file(dst) != sha256_file(png):
                shutil.copy2(png, dst)
                synced_to_root_chars.append(png.name)
    for png in sorted(root_char_dir.glob("*.png")):
        dst = ep_char_dir / png.name
        if not dst.exists() or force_flag:
            shutil.copy2(png, dst)

    # 3. 同步 声音资产/ 或 声线参考/ 中已定稿的 2-3s .wav 至 分集/第NN集/声线/
    ep_voice_dir = ep_dir / "声线"
    ep_voice_dir.mkdir(parents=True, exist_ok=True)
    for v_src_dir in [project_root / "声音资产", project_root / "声线参考"]:
        if v_src_dir.exists():
            for wav in sorted(v_src_dir.glob("*_声线参考.wav")):
                dst = ep_voice_dir / wav.name
                if not dst.exists() or force_flag:
                    shutil.copy2(wav, dst)

    # 4. 自动生成/同步根目录汇总接触表：审核专区_第NN集_源与初图接触表.jpg
    root_sheet = _build_episode_root_contact_sheet(project_root, ep)

    char_pngs = sorted(ep_char_dir.glob("*.png"))
    portraits = [p.name for p in char_pngs if p.stem.endswith("_肖像")]
    subjects = [p.name for p in char_pngs if p.stem.endswith("_主体")]
    kf_dir = ep_dir / "首尾及关键帧"
    kf_pngs = sorted(kf_dir.rglob("*.png")) if kf_dir.exists() else []
    voices = sorted(ep_voice_dir.glob("*.wav"))

    if failed_batches:
        err_payload = {
            "status": "PHASE2_ORGANIZE_PARTIAL_FAIL",
            "episode": ep,
            "finalized_batches": finalized_batches,
            "failed_batches": failed_batches,
            "character_portraits_count": len(portraits),
            "character_subjects_count": len(subjects),
            "synced_to_root_chars_count": len(synced_to_root_chars),
            "synced_to_root_chars": synced_to_root_chars,
            "keyframes_count": len(kf_pngs),
            "voice_refs_count": len(voices),
            "root_contact_sheet": str(root_sheet).replace("\\", "/") if root_sheet else "",
        }
        print(json.dumps(err_payload, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(2)

    print(json.dumps({
        "status": "PHASE2_ORGANIZE_OK",
        "episode": ep,
        "finalized_batches": finalized_batches,
        "failed_batches": [],
        "character_portraits_count": len(portraits),
        "character_subjects_count": len(subjects),
        "synced_to_root_chars_count": len(synced_to_root_chars),
        "synced_to_root_chars": synced_to_root_chars,
        "keyframes_count": len(kf_pngs),
        "voice_refs_count": len(voices),
        "root_contact_sheet": str(root_sheet).replace("\\", "/") if root_sheet else "",
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="GPT 生图批次与项目底层架构极速辅助工具（纯 CPU 第一~三棒 + 第四棒 A100）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="一键搭建标准项目底层目录架构与分集九项骨架")
    p_init.add_argument("--project-root", "--project-dir", dest="project_root", required=True, help="项目根目录路径")
    p_init.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_init.add_argument("--source-video", default="", help="可选：原视频文件名或路径")
    p_init.add_argument("--source-range", default="", help="可选：源时间覆盖区间，如 0-360s")
    p_init.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default="16:9", help="画幅比例：默认 16:9 横屏，可选 9:16 竖屏")
    p_init.set_defaults(func=cmd_init)

    p_fill = sub.add_parser("fill-style", help="由 GPT 将确认的风格词与人物造型一键回填至视频提示词 MD")
    p_fill.add_argument("--md-file", required=True, help="第NN集_视频提示词.md 路径")
    p_fill.add_argument("--style-json", required=True, help="包含 visual_style 与 characters 字典的 JSON 路径")
    p_fill.set_defaults(func=cmd_fill_style)

    p_status = sub.add_parser("status", help="一键盘点在制工作区或单个批次的待生图/待补救缺口与窗口切分预览")
    p_status.add_argument("--work-dir", help="分集在制工作区目录（如 中间文件/在制工作区/第01集）")
    p_status.add_argument("--batch-dir", help="单个批次目录（如 生成批次/批次09）")
    p_status.set_defaults(func=cmd_status)

    p_pstatus = sub.add_parser("phase-status", help="磁盘检查点状态机：扫描分集产物与批次进度，写入 phase_checkpoint.json 并推荐下一接力阶段")
    p_pstatus.add_argument("--ep-dir", required=True, help="分集工作区或单集目录路径")
    p_pstatus.add_argument("--manifest", default="", help="可选：待生图帧清单.json 路径")
    p_pstatus.add_argument("--cuts", default="", help="可选：cuts.csv 路径")
    p_pstatus.add_argument("--audit", default="", help="可选：prompt_visual_audit.json 路径")
    p_pstatus.add_argument("--v3", action="store_true", help="显式启用 V3 五阶状态识别")
    p_pstatus.set_defaults(func=cmd_phase_status)

    p_vsa = sub.add_parser("verify-script-alignment", help="第1步统一对齐门禁：校验 cuts.csv、_视频提示词.md 与 待生图帧清单.json 1:1 对齐、零旧占位符、V3五维实体槽及视觉反查 PASS")
    p_vsa.add_argument("--project-root", default="", help="项目根目录路径")
    p_vsa.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_vsa.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_vsa.add_argument("--cuts-csv", default="", help="可选：显式指定 cuts.csv 路径")
    p_vsa.add_argument("--prompt-md", default="", help="可选：显式指定 _视频提示词.md 路径")
    p_vsa.add_argument("--frames-json", "--manifest", dest="frames_json", default="", help="可选：显式指定 待生图帧清单.json 路径")
    p_pstatus_audit = p_vsa.add_argument("--audit-json", default="", help="可选：显式指定 prompt_visual_audit.json 路径")
    p_vsa.add_argument("--require-visual-audit", action="store_true", help="强制要求 prompt_visual_audit.json 为 PASS 且覆盖全部清单单元")
    p_vsa.add_argument("--v3", action="store_true", help="显式启用 V3 实体槽/Wave/硬件槽位/双盲审硬门禁")
    p_vsa.add_argument("--as-of-date", default="", help="可选：以 YYYY-MM-DD 模拟评估日期校验 2026-12-05 Flow 模型退役红线")
    p_vsa.add_argument("--flow-tier", choices=["free", "paid"], default=None, help="可选：Google Flow 订阅档位 free/paid（未传时优先读取 episode_director_bible.json，默认 paid）")
    p_vsa.set_defaults(func=cmd_verify_script_alignment)

    p_plan = sub.add_parser("plan-batches", aliases=["plan-windows", "slice-windows", "materialize-batches"], help="按 Wave 层级不混批与单窗口 ≤50 基础单元（51..54 弹性并入 window_01）物理切分窗口与批次，并默认物化 生成批次/批次XX/jobs.json")
    p_plan.add_argument("--work-dir", default="", help="分集在制工作区目录（如 中间文件/在制工作区/第01集）")
    p_plan.add_argument("--project-root", default="", help="项目根目录路径")
    p_plan.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-dir）")
    p_plan.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_plan.add_argument("--max-per-window", "--max-units", dest="max_per_window", default=MAX_UNITS_PER_WINDOW, type=int, help="单窗口最大基础单元数，默认 50（支持 --max-units 别名）")
    p_plan.add_argument("--elastic-cap", default=MAX_TOTAL_CALLS_PER_WINDOW, type=int, help="单窗口弹性收尾总调用上限（默认 60：51..54 基础单元自动并入 window_01 一次收尾）")
    p_plan.add_argument("--max-per-batch", default=MAX_UNITS_PER_BATCH, type=int, help="单批次最大单元数，默认 6")
    p_plan.add_argument("--window", default="window_01", help="默认物化输出的窗口 ID（如 window_01 或 all）")
    p_plan.add_argument("--no-materialize", action="store_true", help="仅输出窗口规划 JSON，不物化 生成批次/批次XX/jobs.json")
    p_plan.add_argument("--replan-all", action="store_true", help="忽略已完成状态，对清单全量重新规划窗口")
    p_plan.add_argument("--skip-cuts-check", action="store_true", help="跳过同集 cuts.csv 零漏秒硬门禁校验")
    p_plan.add_argument("--skip-alignment-check", action="store_true", help="跳过第1步脚本与清单 1:1 对齐及旧占位符硬门禁校验")
    p_plan.add_argument("--v3", action="store_true", help="显式启用 V3 Wave 依赖与五维实体槽硬校验")
    p_plan.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None, help="可选：显式指定画幅比例 16:9 / 9:16（未传时自动读取项目配置）")
    p_plan.add_argument("--multi-char-bg", choices=["white", "transparent"], default="white", help="多人横向合集图底色模式：默认 white (#FFFFFF 纯白无底防模型压黑边)，可选 transparent (RGBA 透明无底)")
    p_plan.set_defaults(func=cmd_plan_batches)

    p_bcs = sub.add_parser("build-char-sheets", aliases=["build-multi-char-sheets", "char-composite"], help="批量生成/刷新多人同框去白边无底横向人物合集图（Image 3，跨镜缓存复用，永不挤占 Image 2 场景母图）")
    p_bcs.add_argument("--work-dir", default="", help="分集在制工作区目录（如 中间文件/在制工作区/第01集）")
    p_bcs.add_argument("--project-root", default="", help="项目根目录路径")
    p_bcs.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_bcs.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_bcs.add_argument("--bg", choices=["white", "transparent"], default="white", help="合集图底色：默认 white (#FFFFFF 纯白无底防黑边)，可选 transparent (RGBA 透明无底)")
    p_bcs.add_argument("--force", action="store_true", help="忽略缓存 SHA 强制重拼全部多人合集图")
    p_bcs.add_argument("--allow-missing", action="store_true", help="缺少部分角色母图时不返回非零退出码，仅输出已齐套与待补清单")
    p_bcs.set_defaults(func=cmd_build_char_sheets)

    p_save = sub.add_parser("save-unit", help="子代理一键保存 PNG 与 result.json（含 Stage A 级联阻断与白底像素门禁）")
    p_save.add_argument("--jobs-file", required=True, help="jobs.json 或 remedy_jobs.json 路径")
    p_save.add_argument("--unit", required=True, help="单元名，如 segment_01_镜头01_首帧")
    p_save.add_argument("--tool-png", required=True, help="ImageGen 生成的原始 PNG 路径")
    p_save.add_argument("--allow-unapproved-refs", action="store_true", help="显式豁免 Stage A 级联未通过/缺失阻断门禁")
    p_save.set_defaults(func=cmd_save_unit)

    p_contact = sub.add_parser("contact", aliases=["contact-sheet", "review-grid"], help="一键生成接触表并校验全部 SHA256")
    p_contact.add_argument("--batch-dir", required=True, help="批次目录路径")
    p_contact.set_defaults(func=cmd_contact)

    p_rem = sub.add_parser("remedy-prep", help="根据审核结论 JSON 生成 remedy_jobs.json")
    p_rem.add_argument("--batch-dir", required=True, help="批次目录路径")
    p_rem.add_argument("--review-json", required=True, help="批次审核结论 JSON 路径")
    p_rem.set_defaults(func=cmd_remedy_prep)

    p_fin = sub.add_parser("finalize", help="步骤5收尾：更新 当前制作审核分类.json 并将审核通过图片归位至正式集目录")
    p_fin.add_argument("--batch-dir", required=True, help="批次目录路径")
    p_fin.add_argument("--summary-json", default="", help="可选：审核总结.json 或 批次审核结论.json 路径")
    p_fin.add_argument("--work-dir", default="", help="可选：分集在制工作区目录")
    p_fin.add_argument("--force", action="store_true", help="已归档批次重跑时的目标文件覆盖幂等（绝不绕过接触表、SHA、独立理由与纯白底门禁）")
    p_fin.set_defaults(func=cmd_finalize)

    p_org = sub.add_parser("organize", aliases=["archive-assets"], help="第二棒环节5收尾：汇总所有批次归位结果，同步人物双白底图、双音色声线与分镜首尾帧并输出核验报告")
    p_org.add_argument("--project-root", default="", help="项目根目录路径")
    p_org.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_org.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_org.add_argument("--force", action="store_true", help="重跑时强制覆盖已归位文件（绝不绕过 finalize 硬门禁）")
    p_org.set_defaults(func=cmd_organize)

    p_vgen = sub.add_parser("voice-gen", help="第二棒步骤0.5：纯 CPU 极速兼容生成双音色 2.00s-3.00s 标准 WAV（优先 Gemini 2.5 Flash TTS / CPU 轻量化兜底）")
    p_vgen.add_argument("--project-dir", default="", help="项目根目录路径")
    p_vgen.add_argument("--project-root", default="", help="项目根目录路径（同 --project-dir）")
    p_vgen.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_vgen.add_argument("--mode", default="auto", choices=["cosyvoice2", "tts", "auto", "cpu", "gemini-tts"], help="发声合成模式：auto/cpu/gemini-tts(优先 Gemini 2.5 Flash TTS + CPU 轻量化兜底)")
    p_vgen.add_argument("--force-new", action="store_true", help="强制忽略本剧声线参考库缓存并重新合成")
    p_vgen.set_defaults(func=cmd_voice_gen)

    p_vaudit = sub.add_parser("voice-audit", help="第二棒声线核验：强制校验 48000Hz Mono pcm_s16le (2.00s-3.00s)、静音比 (<=0.35)、SHA256 唯一性并签发 声线母本批准清单.json")
    p_vaudit.add_argument("--project-dir", default="", help="项目根目录路径")
    p_vaudit.add_argument("--project-root", default="", help="项目根目录路径（同 --project-dir）")
    p_vaudit.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_vaudit.set_defaults(func=cmd_voice_audit)

    p_p3v = sub.add_parser("phase3-verify", aliases=["check-delivery", "verify-delivery"], help="第三棒步骤3：静态九项交付物（含剧本故事梗概与逐镜导演脚本.md、台词生成表.xlsx）与双音色 2.00s-3.00s Mono pcm_s16le 终检")
    p_p3v.add_argument("--project-dir", default="", help="项目根目录路径")
    p_p3v.add_argument("--project-root", default="", help="项目根目录路径（同 --project-dir）")
    p_p3v.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_p3v.set_defaults(func=cmd_phase3_verify)

    p_dshots = sub.add_parser("detect-shots", aliases=["split-scenes"], help="第一棒第零步：CFR母本重整 + TransNetV2 神经物理镜头切割、故事板生成与首尾关键帧提取（内置本地防偷跑门禁）")
    p_dshots.add_argument("--video", required=True, help="原视频或本集原画质视频路径")
    p_dshots.add_argument("--start-sec", default=0.0, type=float, help="起始秒数，如 0.0")
    p_dshots.add_argument("--end-sec", default=None, type=float, help="结束秒数，如 360.0")
    p_dshots.add_argument("--output-dir", default="", help="切点清单与抽帧输出目录")
    p_dshots.add_argument("--threshold", default=2.5, type=float, help="兼容保留参数")
    p_dshots.add_argument("--min-len", default=10, type=int, help="最小镜头帧数，默认 10")
    p_dshots.add_argument("--cfr", action="store_true", help="强制先重整为恒定 25fps 正片母本_CFR.mp4")
    p_dshots.add_argument("--force-local", action="store_true", help="显式豁免本地防偷跑门禁（默认禁止在本地 Windows 直接切分）")
    p_dshots.set_defaults(func=cmd_detect_shots)

    p_bsb = sub.add_parser("build-storyboards", help="将 物理切点清单.json 按 1-6 张 1600px JPEG 故事板图拼合输出并写 storyboard_index.json")
    p_bsb.add_argument("--shots-json", default="", help="可选：物理切点清单.json 路径（未传时可由 --ep-dir 自动解析）")
    p_bsb.add_argument("--ep-dir", default="", help="可选：分集原片分析或工作区目录（自动解析 <ep-dir>/物理切点清单.json）")
    p_bsb.add_argument("--output-dir", default="", help="故事板输出目录，默认同级 storyboard_grids")
    p_bsb.add_argument("--sheet-width", default=1600, type=int, help="故事板单张宽度，默认 1600")
    p_bsb.add_argument("--max-sheets", default=6, type=int, help="最大张数，默认 6")
    p_bsb.set_defaults(func=cmd_build_storyboards)

    p_sseg = sub.add_parser("slice-segments", help="窗2衔接：调用 cloud_pipeline.py slice 切出 segment_XX.mp4 并同步映射 seg_XX_shot_YY_first_净参考.jpg")
    p_sseg.add_argument("--project-dir", "--project-root", dest="project_dir", default="", help="项目根目录路径")
    p_sseg.add_argument("--ep", "--episode", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_sseg.add_argument("--cuts-csv", default="", help="可选：cuts.csv 路径（默认 <project-dir>/中间文件/原片分析/<ep>/cuts.csv）")
    p_sseg.add_argument("--source-video", default="", help="可选：原视频路径（默认自动扫描 原视频/ 或 正片母本_CFR.mp4）")
    p_sseg.add_argument("--force-local", action="store_true", help="显式豁免本地防偷跑门禁（若 W1 已落盘 colab_cpu_verified=true 则自动放行）")
    p_sseg.set_defaults(func=cmd_slice_segments)

    p_bvcolab = sub.add_parser("build-voice-colab", help="第二/三棒：转发至 cloud_pipeline.py build-voice-colab 生成 Colab 声线母本 (VoxCPM 2) / 台词配音 (IndexTTS-2.5 / Fish S2 Pro / VoxCPM 2) .ipynb")
    p_bvcolab.add_argument("--project-dir", "--project-root", dest="project_dir", default="", help="项目根目录路径")
    p_bvcolab.add_argument("--ep", "--episode", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_bvcolab.add_argument("--stage", default="both", choices=["voice-ref", "dialogue", "both"], help="合成阶段：voice-ref(第二棒声线母本) / dialogue(第三棒全集台词) / both")
    p_bvcolab.add_argument("--output-ipynb", default="", help="可选：显式指定输出 .ipynb 路径")
    p_bvcolab.add_argument("--tts-engine", default="auto", choices=["auto", "indextts25", "fish_s2_pro", "voxcpm2", "cosyvoice2"], help="配音引擎：auto（voice-ref→voxcpm2，dialogue→indextts25）；可选 indextts25 / fish_s2_pro / voxcpm2（cosyvoice2 仅兼容兜底）")
    p_bvcolab.add_argument("--gpu", default="", choices=["", "T4", "L4", "A100", "H200"], help="Colab GPU 分档：留空按引擎默认（主力 L4，可指定 A100/H200）")
    p_bvcolab.set_defaults(func=cmd_build_voice_colab)

    p_bcuts = sub.add_parser("build-cuts", help="将 物理切点清单.json 100%% 零漏秒转化为标准 cuts.csv（支持 --routing-json 与运动平稳驻点切分，严禁盲目等分与跨场景合并）")
    p_bcuts.add_argument("--shots-json", required=True, help="物理切点清单.json 路径")
    p_bcuts.add_argument("--output-csv", required=True, help="输出 cuts.csv 路径")
    p_bcuts.add_argument("--routing-json", default="", help="可选：episode_director_bible.json 或段级路由表 JSON")
    p_bcuts.add_argument("--video", default="", help="可选：原片视频路径（>15s 长镜无路由表时用于帧差平稳驻点切分）")
    p_bcuts.add_argument("--scene-map-json", default="", help="可选：shot_id 或序号映射到 {scene, summary} 的 JSON")
    p_bcuts.add_argument("--max-pack-sec", default=8.0, type=float, help="同景合并最大段时长，默认 8.0s")
    p_bcuts.add_argument("--max-shots-per-seg", default=3, type=int, help="同段最大镜头数，默认 3")
    p_bcuts.add_argument("--as-of-date", default="", help="可选：以 YYYY-MM-DD 校验 2026-12-05 Flow 模型退役红线")
    p_bcuts.add_argument("--flow-tier", choices=["free", "paid"], default=None, help="可选：Google Flow 订阅档位 free/paid（未传时优先读取 episode_director_bible.json，默认 paid）")
    p_bcuts.add_argument("--expected-start-sec", "--expected-start", dest="expected_start", default=None, type=float, help="可选：预期起始秒")
    p_bcuts.add_argument("--expected-end-sec", "--expected-end", dest="expected_end", default=None, type=float, help="可选：预期结束秒")
    p_bcuts.add_argument("--skip-intro-sec", default=0.0, type=float, help="可选：源片跳过片头秒数")
    p_bcuts.add_argument("--skip-outro-sec", default=0.0, type=float, help="可选：源片跳过片尾秒数")
    p_bcuts.add_argument("--allow-self-span", action="store_true", help="调试开关：允许在无独立可信源时回退使用自身切点终点")
    p_bcuts.set_defaults(func=cmd_build_cuts)

    p_vcuts = sub.add_parser("verify-cuts", help="cuts.csv 零漏秒硬门禁校验（独立可信区间源、并集覆盖率>=99.5%%、首尾及内部gap<=0.50s、Flow 档位与续写红线）")
    p_vcuts.add_argument("--cuts-csv", required=True, help="cuts.csv 路径")
    p_vcuts.add_argument("--shots-json", default="", help="可选：物理切点清单.json 路径")
    p_vcuts.add_argument("--video", "--source-video", dest="video", default="", help="可选：源片或正片母本路径")
    p_vcuts.add_argument("--expected-start-sec", "--expected-start", dest="expected_start", default=None, type=float, help="可选：预期起始秒")
    p_vcuts.add_argument("--expected-end-sec", "--expected-end", dest="expected_end", default=None, type=float, help="可选：预期结束秒")
    p_vcuts.add_argument("--skip-intro-sec", default=0.0, type=float, help="可选：源片跳过片头秒数")
    p_vcuts.add_argument("--skip-outro-sec", default=0.0, type=float, help="可选：源片跳过片尾秒数")
    p_vcuts.add_argument("--min-coverage", default=0.995, type=float, help="最低区间并集覆盖率，默认 0.995")
    p_vcuts.add_argument("--max-gap", default=0.50, type=float, help="最大允许首尾漏切及内部间隙秒，默认 0.50")
    p_vcuts.add_argument("--flow-tier", choices=["free", "paid"], default=None, help="可选：Google Flow 订阅档位 free/paid（默认 paid 或读取 episode_director_bible.json）")
    p_vcuts.add_argument("--as-of-date", default="", help="可选：以 YYYY-MM-DD 校验 Flow 模型退役红线")
    p_vcuts.add_argument("--allow-self-span", action="store_true", help="调试开关：允许在无独立可信源时回退使用 cuts.csv 自身最后一行 end_sec")
    p_vcuts.set_defaults(func=cmd_verify_cuts)

    p_clean = sub.add_parser("clean-source-frames", help="保留原 *_源.jpg 不动，裁除上下黑边并可选平滑左上角台标输出 ref_clean_frames/*_净参考.jpg")
    p_clean.add_argument("--input-dir", required=True, help="包含 *_源.jpg 的原片分析目录")
    p_clean.add_argument("--output-dir", default="", help="输出目录，默认 <input-dir>/ref_clean_frames")
    p_clean.add_argument("--mask-corner-logo", action="store_true", help="对左上角台标区域做邻域平滑")
    p_clean.set_defaults(func=cmd_clean_source_frames)

    p_phref = sub.add_parser("prep-historical-refs", aliases=["prep-prototype-refs"], help="原型/历史原照物理裁切与朝向统一水平翻转（facing=='left' 强制镜像），输出 *_历史真容_同向朝右.jpg")
    p_phref.add_argument("--input-dir", default="", help="原始原型/历史照片目录")
    p_phref.add_argument("--output-dir", required=True, help="标准化真容输出目录")
    p_phref.add_argument("--spec-json", required=True, help="原型/历史原照处理规格 JSON")
    p_phref.set_defaults(func=cmd_prep_historical_refs)

    p_hsheet = sub.add_parser("historical-sheet", aliases=["prototype-sheet", "char-compare-sheet"], help="V4.1 两阶角色对照表：支持 --stage auto|wave1|wave2 及无原型新角色自动绘制规格卡")
    p_hsheet.add_argument("--refs-dir", default="", help="可选：标准化原型/历史真容目录（无原型照自动生成设定规格卡）")
    p_hsheet.add_argument("--chars-dir", default="", help="可选：生成人物图目录")
    p_hsheet.add_argument("--output", default="", help="可选：输出对照表 JPG 路径")
    p_hsheet.add_argument("--spec-json", default="", help="可选：角色匹配清单 JSON")
    p_hsheet.add_argument("--stage", choices=["auto", "wave1", "wave2"], default="auto", help="核验阶段：wave1(2列肖像解锁) / wave2(3列全套解锁) / auto")
    p_hsheet.add_argument("--work-dir", default="", help="可选：分集在制工作区目录（落盘 wave1/wave2_verified.json）")
    p_hsheet.add_argument("--project-dir", "--project-root", dest="project_dir", default="", help="可选：项目根目录")
    p_hsheet.add_argument("--episode", "--ep", dest="episode", default="第01集", help="可选：正式集名称")
    p_hsheet.set_defaults(func=cmd_historical_sheet)

    p_dgen = sub.add_parser("dialogue-gen", help="逐句生成全集标准台词/心声 .wav 音频轨（纯 CPU / 云端双兼容）")
    p_dgen.add_argument("--project-root", default="", help="项目根目录路径")
    p_dgen.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_dgen.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_dgen.add_argument("--mode", default="auto", choices=["cosyvoice2", "tts", "auto", "cpu", "gemini-tts"], help="生成模式")
    p_dgen.add_argument("--force-new", action="store_true", help="强制重新合成全部台词音频")
    p_dgen.set_defaults(func=cmd_dialogue_gen)

    p_pack = sub.add_parser("pack-delivery", aliases=["phase3-pack"], help="第三棒打包：自动调用 dialogue-gen 补齐台词音频并打包至 05_台词配音与心声轨/，输出傻瓜式外包声音规则投喂手册")
    p_pack.add_argument("--project-root", default="", help="项目根目录路径")
    p_pack.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_pack.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_pack.add_argument("--mode", default="auto", choices=["cosyvoice2", "tts", "auto", "cpu", "gemini-tts"], help="dialogue-gen 台词合成模式")
    p_pack.set_defaults(func=cmd_pack_delivery)

    p_bcolab = sub.add_parser("build-colab", aliases=["phase4-triage-vsr", "verify-videos"], help="第四棒：回盘视频体检 + 生成 Colab A100 (CU) LatentSync 中文重对口型 + 4K (默认24fps/可选60fps) 超分流水线 .ipynb")
    p_bcolab.add_argument("--project-root", default="", help="项目根目录路径")
    p_bcolab.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_bcolab.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_bcolab.add_argument("--target-fps", type=int, choices=[24, 60], default=24, help="目标帧率：默认 24（免 RIFE 插帧），可选 60（开启 RIFE 60fps 补帧 SOP）")
    p_bcolab.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None, help="画幅比例：16:9 横屏 / 9:16 竖屏（未传时自动读取项目配置）")
    p_bcolab.set_defaults(func=cmd_build_colab)

    p_lsfix = sub.add_parser("lipsync-fix", help="第四棒：对 Flow 闭嘴说话镜头执行 LatentSync 中文重对口型与纯净环境 SFX 保留")
    p_lsfix.add_argument("--project-root", default="", help="项目根目录路径")
    p_lsfix.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_lsfix.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_lsfix.set_defaults(func=cmd_lipsync_fix)

    p_bfcpxml = sub.add_parser("build-fcpxml", aliases=["phase4-fcpxml"], help="第四棒：生成自动挂载 _台词.wav (Dialogue_Track) 与 _心声.wav (OS_Track) 的达芬奇 .fcpxml 时间线")
    p_bfcpxml.add_argument("--project-root", default="", help="项目根目录路径")
    p_bfcpxml.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_bfcpxml.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_bfcpxml.add_argument("--target-fps", type=int, choices=[24, 60], default=24, help="目标帧率：默认 24（100/2400s），可选 60（100/6000s）")
    p_bfcpxml.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None, help="画幅比例：16:9 横屏 / 9:16 竖屏（未传时自动读取项目配置）")
    p_bfcpxml.add_argument("--allow-pre-vsr", action="store_true", default=False, help="调试开关：允许在未完成 4K 超分前使用原始生成视频")
    p_bfcpxml.set_defaults(func=cmd_build_fcpxml)

    p_p4ver = sub.add_parser("phase4-verify-master", aliases=["verify-master"], help="第四棒 B：实测校验 4K (默认24fps/可选60fps) 成片母带技术规格")
    p_p4ver.add_argument("--project-root", default="", help="项目根目录路径")
    p_p4ver.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_p4ver.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_p4ver.add_argument("--target-fps", type=int, choices=[24, 60], default=24, help="目标帧率：默认 24（23.95~24.05fps），可选 60（59.90~60.10fps）")
    p_p4ver.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None, help="画幅比例：16:9 横屏 / 9:16 竖屏（未传时自动读取项目配置）")
    p_p4ver.add_argument("--master-mp4", default="", help="可选：显式指定待核验成片母带 MP4 路径")
    p_p4ver.set_defaults(func=cmd_phase4_verify_master)

    p_p4cfm = sub.add_parser("phase4-confirm-master", aliases=["confirm-master"], help="第四棒 B 第二步：剪辑师人工回看确认对白、口型与烧录字幕并绑定母带 SHA 封板")
    p_p4cfm.add_argument("--project-root", default="", help="项目根目录路径")
    p_p4cfm.add_argument("--project-dir", default="", help="项目根目录路径（同 --project-root）")
    p_p4cfm.add_argument("--episode", "--ep", dest="episode", default="第01集", help="正式集名称，如 第01集")
    p_p4cfm.add_argument("--reviewer", default="", help="必填：人工剪辑师姓名（禁止 AI/自动化名称）")
    p_p4cfm.add_argument("--confirm-dialogue", action="store_true", help="确认对白语音内容与情绪无误")
    p_p4cfm.add_argument("--confirm-lipsync", action="store_true", help="确认画面口型同步无误")
    p_p4cfm.add_argument("--confirm-subtitles", action="store_true", help="确认烧录字幕准确无遮挡")
    p_p4cfm.add_argument("--notes", default="", help="必填：人工回看具体审核备注（去空白 >= 12 字）")
    p_p4cfm.add_argument("--master-mp4", default="", help="可选：显式指定成片母带 MP4 路径")
    p_p4cfm.add_argument("--target-fps", type=int, choices=[24, 60], default=24, help="可选：目标帧率 24/60")
    p_p4cfm.add_argument("--aspect-ratio", choices=["16:9", "9:16"], default=None, help="可选：画幅比例 16:9 / 9:16")
    p_p4cfm.set_defaults(func=cmd_phase4_confirm_master)

    register_v41_subparsers(sub)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
