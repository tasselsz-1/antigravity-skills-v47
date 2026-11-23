#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V4.1 云端语音合成、第四棒 A100 口型超分、零回退 FCPXML 时间线与严苛终检模块化引擎 (v41_cloud_engines.py)
严格遵循「Skill 即流程：遇错即停、立即上报、严禁私自绕行」铁律。
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

_HERE_DIR = str(Path(__file__).resolve().parent)
if _HERE_DIR not in sys.path:
    sys.path.insert(0, _HERE_DIR)
import v42_fcpxml_and_master_qc as v42_qc  # noqa: E402

LATENTSYNC_GIT_URL = v42_qc.LATENTSYNC_GIT_URL
LATENTSYNC_PINNED_COMMIT = v42_qc.LATENTSYNC_PINNED_COMMIT
LATENTSYNC_HF_REPO = v42_qc.LATENTSYNC_HF_REPO
execute_step2_lipsync_pipeline = v42_qc.execute_step2_lipsync_pipeline
validate_fcpxml_1_9_dtd = v42_qc.validate_fcpxml_1_9_dtd
is_fps_compliant = v42_qc.is_fps_compliant
resolve_target_fps_v42 = v42_qc.resolve_target_fps_v42
inspect_legacy_fps_conflict_v42 = v42_qc.inspect_legacy_fps_conflict_v42
build_timeline_cutpoint_table_v42 = v42_qc.build_timeline_cutpoint_table_v42
confirm_master_human_review_v43 = v42_qc.confirm_master_human_review_v43


def _utf8_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _norm_ep(ep: int | str) -> str:
    if isinstance(ep, int):
        return f"第{ep:02d}集"
    s = str(ep or "第01集").strip()
    m = re.search(r"(\d+)", s)
    if m and not s.startswith("第"):
        return f"第{int(m.group(1)):02d}集"
    return s


def _extract_drive_rel_hint(project_root: Path) -> str:
    s = str(project_root.resolve()).replace("\\", "/").rstrip("/")
    for marker in ("/MyDrive/", "/我的云端硬盘/"):
        if marker in s:
            return s.split(marker, 1)[1].lstrip("/")
    if "短剧复刻工作站" in s:
        return "短剧复刻工作站" + s.split("短剧复刻工作站", 1)[1]
    return re.sub(r"^[A-Za-z]:/+", "", s) or project_root.name


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_fps(raw_fps: str | None) -> float:
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


def _parse_time_sec(ts: str | float | int | None) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts or "").strip().replace(",", ".").rstrip("s秒")
    if not s:
        return 0.0
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600.0 + float(parts[1]) * 60.0 + float(parts[2])
        if len(parts) == 2:
            return float(parts[0]) * 60.0 + float(parts[1])
        return float(s)
    except Exception:
        return 0.0


def probe_media_strict(media_path: Path) -> dict[str, Any]:
    return v42_qc.probe_video_streams_v42(media_path)


def probe_wav_strict(wav_path: Path) -> dict[str, Any]:
    if not wav_path.exists() or wav_path.stat().st_size == 0:
        return {"ok": False, "exists": False, "duration_sec": 0.0, "sample_rate": 0, "channels": 0, "codec_name": "", "sha256": ""}
    sha = _sha256_file(wav_path)
    meta = probe_media_strict(wav_path)
    if meta.get("ok") and meta.get("has_audio"):
        return {
            "ok": True,
            "exists": True,
            "duration_sec": float(meta["duration_sec"]),
            "sample_rate": int(meta["sample_rate"]),
            "channels": int(meta["channels"]),
            "codec_name": str(meta.get("audio_codec") or ""),
            "sha256": sha,
        }
    try:
        with wave.open(str(wav_path), "rb") as wf:
            fr = wf.getframerate()
            ch = wf.getnchannels()
            sw = wf.getsampwidth()
            dur = wf.getnframes() / float(fr) if fr > 0 else 0.0
            return {
                "ok": fr > 0,
                "exists": True,
                "duration_sec": round(dur, 4),
                "sample_rate": fr,
                "channels": ch,
                "codec_name": "pcm_s16le" if sw == 2 else f"pcm_sw{sw}",
                "sha256": sha,
            }
    except Exception:
        return {"ok": False, "exists": True, "duration_sec": 0.0, "sample_rate": 0, "channels": 0, "codec_name": "", "sha256": sha}


def load_cuts_spec(project_root: Path, ep: str) -> dict[str, Any]:
    ep_s = _norm_ep(ep)
    ep_dir = project_root / "分集" / ep_s
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    ana_dir = project_root / "中间文件" / "原片分析" / ep_s
    cuts_path: Path | None = None
    for cand in (work_dir / "cuts.csv", ana_dir / "cuts.csv", ep_dir / "cuts.csv"):
        if cand.exists() and cand.stat().st_size > 0:
            cuts_path = cand
            break

    ordered_segs: list[str] = []
    seg_real_dur: dict[str, float] = {}
    seg_gen_dur: dict[str, float] = {}
    seg_raw_bounds: dict[str, list[float]] = {}
    shot_raw_bounds: dict[tuple[str, str], tuple[float, float]] = {}
    all_starts: list[float] = []

    if cuts_path is not None:
        with cuts_path.open("r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                raw_seg = str(row.get("segment") or row.get("segment_id") or "").strip()
                m_seg = re.search(r"(segment_\d+)", raw_seg)
                seg_k = m_seg.group(1) if m_seg else raw_seg
                if not seg_k:
                    continue
                if seg_k not in ordered_segs:
                    ordered_segs.append(seg_k)
                raw_shot = str(row.get("shot") or row.get("shot_id") or "").strip()
                m_sh = re.search(r"(镜头\d+)", raw_shot)
                if m_sh:
                    sh_k = m_sh.group(1)
                else:
                    m_num = re.search(r"(\d+)", raw_shot)
                    sh_k = f"镜头{int(m_num.group(1)):02d}" if m_num else "镜头01"
                try:
                    s_v = float(row.get("start_sec") or 0.0)
                    e_v = float(row.get("end_sec") or 0.0)
                except Exception:
                    s_v, e_v = 0.0, 0.0
                d_v = max(0.0, e_v - s_v) if e_v > s_v else float(row.get("duration") or row.get("duration_sec") or 0.0)
                if d_v > 0:
                    seg_real_dur[seg_k] = round(seg_real_dur.get(seg_k, 0.0) + d_v, 4)
                if e_v > s_v:
                    all_starts.append(s_v)
                    if seg_k not in seg_raw_bounds:
                        seg_raw_bounds[seg_k] = [s_v, e_v]
                    else:
                        seg_raw_bounds[seg_k][0] = min(seg_raw_bounds[seg_k][0], s_v)
                        seg_raw_bounds[seg_k][1] = max(seg_raw_bounds[seg_k][1], e_v)
                    shot_raw_bounds[(seg_k, sh_k)] = (s_v, e_v)
                gd_s = str(row.get("gen_duration_sec") or "").strip()
                if gd_s and seg_k not in seg_gen_dur:
                    try:
                        seg_gen_dur[seg_k] = float(gd_s)
                    except Exception:
                        pass

    ep_base = min(all_starts) if all_starts else 0.0
    seg_ep_bounds: dict[str, tuple[float, float]] = {}
    cum = 0.0
    for sk in ordered_segs:
        if sk in seg_raw_bounds:
            seg_ep_bounds[sk] = (round(seg_raw_bounds[sk][0] - ep_base, 4), round(seg_raw_bounds[sk][1] - ep_base, 4))
        else:
            d = seg_real_dur.get(sk, 4.0)
            seg_ep_bounds[sk] = (round(cum, 4), round(cum + d, 4))
        cum += seg_real_dur.get(sk, 0.0)

    shot_rel_bounds: dict[tuple[str, str], tuple[float, float]] = {}
    for (sk, shk), (sv, ev) in shot_raw_bounds.items():
        s0 = seg_raw_bounds[sk][0] if sk in seg_raw_bounds else 0.0
        shot_rel_bounds[(sk, shk)] = (round(max(0.0, sv - s0), 4), round(max(0.0, ev - s0), 4))

    return {
        "cuts_path": cuts_path,
        "ordered_segments": ordered_segs,
        "seg_real_dur": seg_real_dur,
        "seg_gen_dur": seg_gen_dur,
        "seg_ep_bounds": seg_ep_bounds,
        "shot_rel_bounds": shot_rel_bounds,
        "expected_total_dur": round(sum(seg_real_dur.get(k, 0.0) for k in ordered_segs), 4),
    }


def _extract_expected_roles(project_root: Path, ep: str) -> tuple[Path | None, list[dict[str, str]]]:
    ep_s = _norm_ep(ep)
    cands = (
        project_root / "中间文件" / "索引与制作记录" / "角色改名与音色映射.json",
        project_root / "中间文件" / "在制工作区" / ep_s / "角色改名与音色映射.json",
        project_root / "分集" / ep_s / "角色改名与音色映射.json",
    )
    rmap_path = next((p for p in cands if p.exists() and p.stat().st_size > 0), None)
    if rmap_path is None:
        return None, []
    try:
        raw = json.loads(rmap_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return rmap_path, []

    entries = []
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        for k in ("characters", "roles", "cast", "mappings", "character_mappings"):
            if isinstance(raw.get(k), list):
                entries = raw[k]
                break
        if not entries:
            for k, v in raw.items():
                if k in ("episode", "project", "version", "notes", "style"):
                    continue
                if isinstance(v, str) and v.strip():
                    entries.append({"original_name": k, "new_name": v.strip()})
                elif isinstance(v, dict):
                    entries.append({"role_id": k, **v})

    roles: list[dict[str, str]] = []
    seen: set[str] = set()
    for idx, it in enumerate(entries, 1):
        if isinstance(it, dict):
            rid = str(it.get("role_id") or it.get("id") or f"角色{idx:02d}").strip()
            rname = str(it.get("new_name") or it.get("name") or it.get("display_name") or it.get("character_name") or "").strip()
        else:
            rid = f"角色{idx:02d}"
            rname = str(it or "").strip()
        if rname and rname not in seen:
            seen.add(rname)
            roles.append({"role_id": rid, "new_name": rname})
    return rmap_path, roles


def load_voice_ref_sha_map(project_root: Path, ep: str) -> dict[str, str]:
    ep_s = _norm_ep(ep)
    sha_map: dict[str, str] = {}
    cands = (
        project_root / "中间文件" / "索引与制作记录" / "交付自查状态" / f"{ep_s}_声线审计报告.json",
        project_root / "中间文件" / "在制工作区" / ep_s / "voice_ref_colab_done.json",
        project_root / "分集" / ep_s / "声线" / "声线母本清单.json",
        project_root / "声线参考" / "声线母本清单.json",
    )
    for cp in cands:
        if not cp.exists() or cp.stat().st_size == 0:
            continue
        try:
            doc = json.loads(cp.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        recs = []
        if isinstance(doc, dict):
            recs = doc.get("records") or doc.get("voice_refs") or doc.get("items") or []
            if isinstance(doc.get("sha256_map"), dict):
                for fk, fv in doc["sha256_map"].items():
                    if fk and fv:
                        sha_map[str(fk)] = str(fv)
        elif isinstance(doc, list):
            recs = doc
        for r in recs:
            if not isinstance(r, dict):
                continue
            fn = str(r.get("file") or r.get("file_name") or Path(str(r.get("path") or "")).name or "").strip()
            sh = str(r.get("sha256") or "").strip()
            if fn and sh:
                sha_map[fn] = sh
    return sha_map


def _clean_speaker_name(raw_spk: str) -> str:
    s = re.sub(r"^角色\d+_", "", str(raw_spk or "").strip())
    s = re.sub(r"^\d+_", "", s)
    s = re.sub(r"\[.*?\]", "", s)
    s = re.sub(r"[（(].*?[)）]", "", s).strip()
    return s or "主角"


def _parse_seg_shot(raw_str: str) -> tuple[str, str]:
    m_seg = re.search(r"(segment_\d+)", str(raw_str or ""))
    m_sh = re.search(r"(镜头\d+)", str(raw_str or ""))
    return (m_seg.group(1) if m_seg else "segment_01"), (m_sh.group(1) if m_sh else "镜头01")


def resolve_dialogue_schedule(project_root: Path, ep: str, cuts_spec: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    ep_s = _norm_ep(ep)
    ep_dir = project_root / "分集" / ep_s
    dlg_dir = ep_dir / "台词音频"
    dlg_csv = ep_dir / f"{ep_s}_台词生成表.csv"
    sub_csv = ep_dir / f"{ep_s}_字幕时间轴.csv"
    manifest_p = dlg_dir / "台词音频清单.json"
    cspec = cuts_spec or load_cuts_spec(project_root, ep_s)
    seg_ep_bounds = cspec["seg_ep_bounds"]
    seg_real_dur = cspec["seg_real_dur"]
    shot_rel_bounds = cspec["shot_rel_bounds"]

    manifest_by_key: dict[str, dict[str, Any]] = {}
    manifest_list: list[dict[str, Any]] = []
    if manifest_p.exists() and manifest_p.stat().st_size > 0:
        try:
            raw_m = json.loads(manifest_p.read_text(encoding="utf-8-sig"))
            for r in raw_m.get("dialogue_records", []):
                if not isinstance(r, dict):
                    continue
                sg, sh = _parse_seg_shot(f"{r.get('segment', '')}_{r.get('shot', '')}_{r.get('unit_key', '')}_{r.get('file_name', '')}")
                r_copy = {**r, "segment": sg, "shot": sh}
                manifest_list.append(r_copy)
                fn = str(r.get("file_name") or "").strip()
                if fn and fn != "SKIP_JIMENG_NATIVE":
                    manifest_by_key[fn] = r_copy
                manifest_by_key[f"{sg}_{sh}_{_clean_speaker_name(str(r.get('speaker') or r.get('character') or ''))}"] = r_copy
        except Exception:
            pass

    sub_Hints: list[dict[str, Any]] = []
    if sub_csv.exists() and sub_csv.stat().st_size > 0:
        try:
            with sub_csv.open("r", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    tr = str(row.get("时间") or row.get("time") or "")
                    s1, s2 = (tr.split("-->", 1) if "-->" in tr else (row.get("start_sec") or "0", row.get("end_sec") or "0"))
                    sub_Hints.append({
                        "seg": str(row.get("segment") or "").strip(),
                        "shot": str(row.get("shot") or "").strip(),
                        "start_ep": _parse_time_sec(s1),
                        "end_ep": _parse_time_sec(s2),
                        "start_in_seg": row.get("start_in_seg_sec"),
                        "end_in_seg": row.get("end_in_seg_sec"),
                        "text": str(row.get("内容") or row.get("台词") or row.get("text") or "").strip(),
                        "speaker": _clean_speaker_name(str(row.get("人物") or row.get("角色") or row.get("speaker") or "")),
                    })
        except Exception:
            pass

    items: list[dict[str, Any]] = []
    if dlg_csv.exists() and dlg_csv.stat().st_size > 0:
        with dlg_csv.open("r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                txt = str(row.get("台词正文") or "").strip()
                ukey = str(row.get("分段与镜号") or "").strip()
                if not ukey or not txt:
                    continue
                seg_id, shot_id = _parse_seg_shot(ukey)
                raw_spk = str(row.get("说话角色") or "").strip()
                spk = _clean_speaker_name(raw_spk)
                ltype = str(row.get("台词类型") or "说话").strip()
                is_os = any(k in ltype.upper() for k in ("心声", "OS", "背影", "画外", "内白"))
                vmode = "心声" if is_os else "说话"
                strat = str(row.get("视频通道与口型策略") or "").strip()
                out_fn = str(row.get("输出音频文件名") or "").strip()
                skip_jm = (out_fn.upper() == "SKIP_JIMENG_NATIVE") or ("即梦原生直出" in strat)
                ts_raw = str(row.get("起止时间轴与目标时长") or "").strip()
                m_dur = re.search(r"\(\s*(\d+(?:\.\d+)?)s\s*\)", ts_raw)
                t_dur = float(m_dur.group(1)) if m_dur else 2.2
                st_seg: float | None = None
                ed_seg: float | None = None
                if row.get("start_in_seg_sec") not in (None, ""):
                    st_seg = float(row["start_in_seg_sec"])
                    ed_seg = float(row.get("end_in_seg_sec") or (st_seg + t_dur))
                items.append({
                    "segment": seg_id, "shot": shot_id, "raw_speaker": raw_spk, "speaker": spk,
                    "voice_mode": vmode, "ref_name": str(row.get("绑定声线母本") or "").strip(),
                    "instruct": str(row.get("VoxCPM2_情绪导演指令") or row.get("CosyVoice2_情绪导演指令") or "用贴合剧情人物情绪的自然语气演绎").strip(),
                    "text": txt, "ts_raw": ts_raw, "target_duration_sec": t_dur,
                    "start_in_seg_sec": st_seg, "end_in_seg_sec": ed_seg,
                    "out_fname": out_fn, "skip_jimeng": skip_jm, "needs_lipsync": (not is_os) and (not skip_jm),
                })
    return _finalize_dialogue_timing(items, manifest_list, manifest_by_key, sub_Hints, dlg_dir, seg_ep_bounds, seg_real_dur, shot_rel_bounds)


def _finalize_dialogue_timing(
    items: list[dict[str, Any]],
    manifest_list: list[dict[str, Any]],
    manifest_by_key: dict[str, dict[str, Any]],
    sub_hints: list[dict[str, Any]],
    dlg_dir: Path,
    seg_ep_bounds: dict[str, tuple[float, float]],
    seg_real_dur: dict[str, float],
    shot_rel_bounds: dict[tuple[str, str], tuple[float, float]],
) -> list[dict[str, Any]]:
    if not items and manifest_list:
        for r in manifest_list:
            fn = str(r.get("file_name") or "").strip()
            skip_jm = fn.upper() == "SKIP_JIMENG_NATIVE"
            is_os = ("_心声" in fn) or ("心声" in str(r.get("voice_mode") or r.get("line_type") or "")) or (r.get("needs_lipsync") is False and not skip_jm)
            items.append({
                "segment": r["segment"], "shot": r["shot"],
                "raw_speaker": str(r.get("speaker") or r.get("character") or "主角"),
                "speaker": _clean_speaker_name(str(r.get("speaker") or r.get("character") or "主角")),
                "voice_mode": "心声" if is_os else "说话",
                "ref_name": Path(str(r.get("locked_voice_ref_wav") or "")).name,
                "instruct": str(r.get("cosyvoice2_instruct") or "用自然语气演绎"),
                "text": str(r.get("text") or ""),
                "ts_raw": "",
                "target_duration_sec": float(r.get("target_duration_sec") or r.get("actual_duration_sec") or 2.2),
                "start_in_seg_sec": r.get("start_in_seg_sec"),
                "end_in_seg_sec": r.get("end_in_seg_sec"),
                "out_fname": fn, "skip_jimeng": skip_jm, "needs_lipsync": (not is_os) and (not skip_jm),
            })
    if not items and dlg_dir.exists():
        for wp in sorted(dlg_dir.glob("*.wav")):
            sg, sh = _parse_seg_shot(wp.name)
            m_w = re.match(r"^segment_\d+_镜头\d+_([^_]+)_(台词|心声).*\.wav$", wp.name)
            spk = _clean_speaker_name(m_w.group(1) if m_w else "主角")
            is_os = (m_w.group(2) == "心声") if m_w else ("_心声" in wp.name)
            p_w = probe_wav_strict(wp)
            d_w = float(p_w.get("duration_sec") or 2.0)
            items.append({
                "segment": sg, "shot": sh, "raw_speaker": spk, "speaker": spk,
                "voice_mode": "心声" if is_os else "说话", "ref_name": "", "instruct": "", "text": "",
                "ts_raw": "", "target_duration_sec": d_w, "start_in_seg_sec": None, "end_in_seg_sec": None,
                "out_fname": wp.name, "skip_jimeng": False, "needs_lipsync": not is_os,
            })

    resolved: list[dict[str, Any]] = []
    seg_cursor: dict[str, float] = {}
    for it in items:
        sg, sh, spk = it["segment"], it["shot"], it["speaker"]
        seg_ep_s, _ = seg_ep_bounds.get(sg, (0.0, seg_real_dur.get(sg, 6.0)))
        seg_d = seg_real_dur.get(sg, 6.0)
        fn = it["out_fname"] or (f"{sg}_{sh}_{spk}_{'心声' if it['voice_mode'] == '心声' else '台词'}.wav" if not it["skip_jimeng"] else "SKIP_JIMENG_NATIVE")
        m_rec = manifest_by_key.get(fn) or manifest_by_key.get(f"{sg}_{sh}_{spk}") or {}

        st = it["start_in_seg_sec"]
        ed = it["end_in_seg_sec"]
        if st is None and m_rec.get("start_in_seg_sec") not in (None, ""):
            m_st = float(m_rec["start_in_seg_sec"])
            m_ed = float(m_rec.get("end_in_seg_sec") or (m_st + it["target_duration_sec"]))
            if m_st > 0.0 or (sg, sh) not in shot_rel_bounds or shot_rel_bounds[(sg, sh)][0] == 0.0:
                st, ed = m_st, m_ed
        if st is None and it["ts_raw"]:
            m_rng = re.search(r"(\d+(?::\d+)*(?:\.\d+)?)s?\s*[-–~]\s*(\d+(?::\d+)*(?:\.\d+)?)s?", it["ts_raw"])
            if m_rng:
                t1, t2 = _parse_time_sec(m_rng.group(1)), _parse_time_sec(m_rng.group(2))
                has_colon = ":" in m_rng.group(1)
                if not has_colon and 0.0 <= t1 < seg_d + 0.25 and t2 > t1:
                    st, ed = t1, t2
                elif 0.0 <= (t1 - seg_ep_s) < seg_d + 0.25 and t2 > t1:
                    st, ed = max(0.0, t1 - seg_ep_s), max(0.05, t2 - seg_ep_s)
        if st is None:
            for sh_h in sub_hints:
                if (sh_h["seg"] == sg and (not sh_h["shot"] or sh_h["shot"] == sh)) or (it["text"] and sh_h["text"] and it["text"] == sh_h["text"]):
                    if sh_h["start_in_seg"] not in (None, ""):
                        st = float(sh_h["start_in_seg"])
                        ed = float(sh_h["end_in_seg"] or (st + it["target_duration_sec"]))
                    elif sh_h["end_ep"] > sh_h["start_ep"] and 0.0 <= (sh_h["start_ep"] - seg_ep_s) < seg_d + 0.25:
                        st = max(0.0, sh_h["start_ep"] - seg_ep_s)
                        ed = max(st + 0.1, sh_h["end_ep"] - seg_ep_s)
                    break
        if st is None and (sg, sh) in shot_rel_bounds:
            sh_s, sh_e = shot_rel_bounds[(sg, sh)]
            st = max(sh_s, seg_cursor[sg]) if (sg in seg_cursor and seg_cursor[sg] + 0.2 < sh_e) else sh_s
            ed = sh_e
        if st is None:
            st = seg_cursor.get(sg, 0.0) if seg_cursor.get(sg, 0.0) + 0.2 < seg_d else 0.0
            ed = st + it["target_duration_sec"]
        elif st == 0.0 and sg in seg_cursor and seg_cursor[sg] + 0.2 < seg_d and it["start_in_seg_sec"] is None and not it["ts_raw"]:
            st = seg_cursor[sg]
            ed = st + it["target_duration_sec"]

        t_dur = round(max(0.4, (ed - st) if (ed is not None and ed > st) else it["target_duration_sec"]), 3)
        ed_f = round(float(ed if (ed is not None and ed > st) else (st + t_dur)), 3)
        st_f = round(max(0.0, float(st)), 3)
        seg_cursor[sg] = max(seg_cursor.get(sg, 0.0), ed_f)
        wav_p = Path(str(m_rec.get("wav_path") or "")) if m_rec.get("wav_path") else (dlg_dir / fn)
        if not wav_p.exists() and fn != "SKIP_JIMENG_NATIVE":
            wav_p = dlg_dir / fn
        resolved.append({
            **it, "out_fname": fn, "wav_path": wav_p,
            "start_in_seg_sec": st_f, "end_in_seg_sec": ed_f, "target_duration_sec": t_dur,
        })
    return resolved


def run_voice_audit(
    project_root: Path,
    ep: str,
    probe_hw_fn: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ep_s = _norm_ep(ep)
    _, expected_roles = _extract_expected_roles(project_root, ep_s)
    hw_mode = probe_hw_fn("phase1_3")["execution_mode"] if probe_hw_fn else "PURE_CPU_CLOUD_API_MODE"
    return v42_qc.run_voice_audit_and_approve_v42(project_root, ep_s, expected_roles, probe_wav_strict, hw_mode=hw_mode)


def _check_phase3_voice_48k_approved(project_root: Path, ep_s: str, cspec: dict[str, Any], failures: list[str]) -> bool:
    rmap_path, expected_roles = _extract_expected_roles(project_root, ep_s)
    if rmap_path is not None and not expected_roles:
        failures.append(f"VOICE_REF_NOT_APPROVED_OR_NOT_48K: 角色改名与音色映射为空或损坏: {rmap_path.name}")
        return False
    req_items: list[dict[str, Any]] = []
    for rinfo in expected_roles:
        for req_m in ("说话", "心声"):
            req_items.append({"speaker": rinfo["new_name"], "voice_mode": req_m, "ref_name": "", "shot_tag": rinfo["role_id"]})
    for drec in resolve_dialogue_schedule(project_root, ep_s, cuts_spec=cspec):
        if not drec.get("skip_jimeng"):
            req_items.append({"speaker": drec["speaker"], "voice_mode": drec["voice_mode"], "ref_name": drec.get("ref_name", ""), "shot_tag": f"{drec['segment']}_{drec['shot']}"})
    v_res = v42_qc.verify_voice_refs_48k_approved_v42(
        project_root, ep_s, req_items, expected_roles, probe_wav_strict, _locate_voice_ref_file, check_all_ep_wavs=True
    )
    for iss in v_res["issues"]:
        failures.append(f"2_声线未批准或非48k: {iss['reason']}")
    return bool(v_res["ok"])


def run_phase3_verify(
    project_root: Path,
    ep: str,
    probe_hw_fn: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ep_s = _norm_ep(ep)
    ep_dir = project_root / "分集" / ep_s
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    ana_dir = project_root / "中间文件" / "原片分析" / ep_s
    record_dir = project_root / "中间文件" / "索引与制作记录"
    audit_dir = record_dir / "交付自查状态"
    audit_dir.mkdir(parents=True, exist_ok=True)

    director_md = ep_dir / f"{ep_s}_剧本故事梗概与逐镜导演脚本.md"
    md_path = ep_dir / f"{ep_s}_视频提示词.md"
    master_md = project_root / "中间文件" / "提示词维护" / "视频提示词" / ep_s / "当前主稿.md"
    xlsx_path = ep_dir / f"{ep_s}_分镜头脚本.xlsx"
    csv_path = ep_dir / f"{ep_s}_字幕时间轴.csv"
    dlg_xlsx = ep_dir / f"{ep_s}_台词生成表.xlsx"
    dlg_csv = ep_dir / f"{ep_s}_台词生成表.csv"
    char_dir = ep_dir / "人物"
    kf_dir = ep_dir / "首尾及关键帧"
    raw_seg_dir = ep_dir / "原视频段落"

    failures: list[str] = []
    char_pngs = [p for p in (sorted(char_dir.glob("*.png")) if char_dir.exists() else []) if p.is_file() and p.stat().st_size > 0]
    has_portrait = any(p.stem.endswith("_肖像") for p in char_pngs)
    has_subject = any(p.stem.endswith("_主体") for p in char_pngs)
    chk_1 = bool(char_dir.exists() and has_portrait and has_subject)
    if not chk_1:
        failures.append("1_人物目录缺少非空 _肖像.png 或 _主体.png")

    cspec_pre = load_cuts_spec(project_root, ep_s)
    chk_2 = _check_phase3_voice_48k_approved(project_root, ep_s, cspec_pre, failures)

    md_sha_ok = False
    no_placeholder = False
    if md_path.exists() and md_path.stat().st_size > 0:
        mtxt = md_path.read_text(encoding="utf-8-sig")
        no_placeholder = ("{{VISUAL_STYLE}}" not in mtxt) and ("【待填角色：" not in mtxt)
        if master_md.exists() and master_md.stat().st_size > 0:
            md_sha_ok = _sha256_file(md_path) == _sha256_file(master_md)
    chk_3 = bool(md_path.exists() and no_placeholder and md_sha_ok)
    if not chk_3:
        failures.append("3_视频提示词缺失、含未回填占位符或与当前主稿 SHA-256 不一致")

    any_cuts_file = next((c for c in (work_dir / "cuts.csv", ana_dir / "cuts.csv", ep_dir / "cuts.csv") if c.exists()), None)
    cspec = load_cuts_spec(project_root, ep_s)
    req_segs = cspec["ordered_segments"]
    cuts_valid = (any_cuts_file is None) or bool(req_segs)

    kf_pngs = [p for p in (sorted(kf_dir.rglob("*.png")) if kf_dir.exists() else []) if p.is_file() and p.stat().st_size > 0]
    missing_kf_segs = [
        sk for sk in req_segs
        if not any((p.name.startswith(f"{sk}_") or p.parent.name == sk) and ("首帧" in p.stem or "first" in p.stem.lower()) for p in kf_pngs)
    ]
    chk_4 = bool(kf_pngs and cuts_valid and not missing_kf_segs)
    if not chk_4:
        failures.append(f"4_首尾及关键帧缺失分段首帧: {missing_kf_segs or '目录无有效PNG'}")

    chk_5 = bool(xlsx_path.exists() and xlsx_path.stat().st_size > 0)
    raw_mp4s = [p for p in (sorted(raw_seg_dir.glob("*.mp4")) if raw_seg_dir.exists() else []) if p.is_file() and p.stat().st_size > 0]
    missing_raw_segs = [sk for sk in req_segs if not (raw_seg_dir / f"{sk}.mp4").exists() or (raw_seg_dir / f"{sk}.mp4").stat().st_size == 0]
    chk_6 = bool(raw_mp4s and cuts_valid and not missing_raw_segs)
    if not chk_6:
        failures.append(f"6_原视频段落缺失: {missing_raw_segs or '目录无有效MP4'}")

    chk_7 = bool(csv_path.exists() and csv_path.stat().st_size > 0)
    chk_8 = bool(dlg_xlsx.exists() and dlg_xlsx.stat().st_size > 0 and dlg_csv.exists() and dlg_csv.stat().st_size > 0)
    chk_9 = bool(director_md.exists() and director_md.stat().st_size > 100)
    return _emit_phase3_verify_report(ep_s, audit_dir, probe_hw_fn, chk_1, chk_2, chk_3, chk_4, chk_5, chk_6, chk_7, chk_8, chk_9, failures)


def _emit_phase3_verify_report(
    ep_s: str, audit_dir: Path, probe_hw_fn: Callable[[str], dict[str, Any]] | None,
    c1: bool, c2: bool, c3: bool, c4: bool, c5: bool, c6: bool, c7: bool, c8: bool, c9: bool,
    failures: list[str],
) -> dict[str, Any]:
    if not c5:
        failures.append("5_分镜头脚本_xlsx 不存在或为 0 字节")
    if not c7:
        failures.append("7_字幕时间轴_csv 不存在或为 0 字节")
    if not c8:
        failures.append("8_台词生成表_xlsx 或 csv 不存在或为 0 字节")
    if not c9:
        failures.append("9_剧本故事梗概与逐镜导演脚本_md 不存在或小于等于 100 字节")
    checks = {
        "1_人物目录": c1,
        "2_声线目录_双音色2至3秒_48k单声道PCM_已批准SHA匹配": c2,
        "3_视频提示词_已回填且主稿SHA一致": c3,
        "4_首尾及关键帧目录": c4,
        "5_分镜头脚本_xlsx": c5,
        "6_原视频段落目录": c6,
        "7_字幕时间轴_csv": c7,
        "8_台词生成表_xlsx与csv": c8,
        "9_剧本故事梗概与逐镜导演脚本_md": c9,
    }
    all_passed = all(checks.values())
    hw_mode = probe_hw_fn("phase1_3")["execution_mode"] if probe_hw_fn else "PURE_CPU_CLOUD_API_MODE"
    err_code = "VOICE_REF_NOT_APPROVED_OR_NOT_48K" if (not c2) else ("" if all_passed else "PHASE3_VERIFY_FAILED")
    report = {
        "status": "PHASE3_VERIFY_OK" if all_passed else ("VOICE_REF_NOT_APPROVED_OR_NOT_48K" if not c2 else "PHASE3_VERIFY_FAILED"),
        "error_code": err_code,
        "episode": ep_s,
        "hardware_mode": hw_mode,
        "all_nine_deliverables_passed": all_passed,
        "all_seven_deliverables_passed": all(v for k, v in checks.items() if not k.startswith("9_")),
        "deliverable_checks": checks,
        "failures": failures,
    }
    txt = json.dumps(report, ensure_ascii=False, indent=2)
    (audit_dir / f"{ep_s}_第三棒静态九项核验.json").write_text(txt, encoding="utf-8")
    (audit_dir / f"{ep_s}_第三棒静态七项核验.json").write_text(txt, encoding="utf-8")
    print(txt)
    if not all_passed:
        sys.exit(2)
    return report


def run_phase4_verify_master(
    project_root: Path,
    ep: str,
    probe_hw_fn: Callable[[str], dict[str, Any]] | None = None,
    target_fps: int | None = None,
    master_mp4_override: str | Path | None = None,
    p4_profile: str | None = None,
    aspect_ratio: str | None = None,
) -> dict[str, Any]:
    ep_s = _norm_ep(ep)
    legacy_conflict = v42_qc.inspect_legacy_fps_conflict_v42(project_root, ep_s, explicit_fps=target_fps)
    tfps = v42_qc.resolve_target_fps_v42(project_root, ep_s, explicit_fps=target_fps, prefer_bible_first=False)
    prof_spec = v42_qc.resolve_p4_profile_v46(project_root, ep_s, explicit_profile=p4_profile, explicit_aspect_ratio=aspect_ratio)
    eff_prof = prof_spec["p4_profile"] or None
    ar = str(prof_spec.get("aspect_ratio") or "16:9")
    tw, th, res_tag = int(prof_spec["width"]), int(prof_spec["height"]), str(prof_spec["res_tag"])
    ep_dir = project_root / "分集" / ep_s
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    audit_dir = project_root / "中间文件" / "索引与制作记录" / "交付自查状态"
    audit_dir.mkdir(parents=True, exist_ok=True)

    if master_mp4_override and str(master_mp4_override).strip():
        raw_mp = Path(str(master_mp4_override).strip())
        master_mp4 = raw_mp.resolve() if (raw_mp.is_absolute() or raw_mp.exists()) else (project_root / raw_mp).resolve()
    else:
        default_mp = ep_dir / v42_qc.default_master_mp4_name(ep_s, tfps, p4_profile=eff_prof)
        compat_cands = [default_mp, ep_dir / f"{ep_s}_{res_tag}_{tfps}fps_成片.mp4", work_dir / default_mp.name]
        master_mp4 = next((c for c in compat_cands if c.is_file() and c.stat().st_size > 0), default_mp)

    fcpxml_path = ep_dir / f"{ep_s}_达芬奇自动时间线.fcpxml"
    align_json_path = work_dir / "口型与字幕时间点核验记录.json"
    hw_info = probe_hw_fn("phase4") if probe_hw_fn else {"phase": "phase4", "execution_mode": "A100_GPU_MODE"}

    fcpxml_ok = fcpxml_path.exists() and fcpxml_path.stat().st_size > 0
    mp4_ok = master_mp4.exists() and master_mp4.stat().st_size > 0
    if not fcpxml_ok or not mp4_ok:
        missing_f = []
        if not fcpxml_ok:
            missing_f.append(f"缺少或空时间线: {fcpxml_path.name}")
        if not mp4_ok:
            missing_f.append(f"缺少或空成片: {master_mp4.name}")
        report: dict[str, Any] = {
            "status": "MASTER_VERIFY_FAILED", "episode": ep_s, "target_fps": tfps,
            "technical_spec_passed": False, "human_review_verified": False,
            "master_mp4_path": str(master_mp4).replace("\\", "/"), "hardware_probe": hw_info,
            "fcpxml_timeline_exists": fcpxml_ok, "master_mp4_exists": mp4_ok,
            "master_metadata": {}, "failures": missing_f, "subtitle_status": "未通过_缺少成片或时间线",
        }
        if legacy_conflict["legacy_plan_fps_conflict"]:
            report["legacy_plan_fps_conflict"] = True
            report["legacy_plan_fps_warning"] = legacy_conflict["legacy_plan_fps_warning"]
        out_txt = json.dumps(report, ensure_ascii=False, indent=2)
        (audit_dir / f"{ep_s}_第四棒成片母带终检.json").write_text(out_txt, encoding="utf-8")
        print(out_txt)
        sys.exit(2)

    return _verify_master_mp4_and_alignment(
        project_root, ep_s, master_mp4, fcpxml_path, align_json_path, audit_dir, hw_info, tfps, legacy_conflict,
        target_width=tw, target_height=th, p4_profile=prof_spec["p4_profile"], aspect_ratio=ar,
    )


def _verify_master_mp4_and_alignment(
    project_root: Path, ep_s: str, master_mp4: Path, fcpxml_path: Path, align_json_path: Path,
    audit_dir: Path, hw_info: dict[str, Any], target_fps: int = v42_qc.DEFAULT_TARGET_FPS,
    legacy_conflict: dict[str, Any] | None = None,
    target_width: int = 3840, target_height: int = 2160, p4_profile: str = "",
    aspect_ratio: str = "16:9",
) -> dict[str, Any]:
    cspec = load_cuts_spec(project_root, ep_s)
    return v42_qc.verify_master_post_render_v42(
        project_root, ep_s, master_mp4, fcpxml_path, align_json_path,
        audit_dir, hw_info, cspec, resolve_dialogue_schedule, probe_wav_strict,
        target_fps=target_fps, legacy_conflict=legacy_conflict,
        target_width=target_width, target_height=target_height, p4_profile=p4_profile,
        aspect_ratio=aspect_ratio,
    )


def run_phase4_confirm_master(
    project_root: Path, ep: str, reviewer: str, confirm_dialogue: bool, confirm_lipsync: bool,
    confirm_subtitles: bool, notes: str, master_mp4_override: str | Path | None = None,
    target_fps: int | None = None, p4_profile: str | None = None, aspect_ratio: str | None = None,
) -> dict[str, Any]:
    return v42_qc.confirm_master_human_review_v43(
        project_root, _norm_ep(ep), reviewer=reviewer, confirm_dialogue=confirm_dialogue,
        confirm_lipsync=confirm_lipsync, confirm_subtitles=confirm_subtitles, notes=notes,
        master_mp4_override=master_mp4_override, target_fps=target_fps, p4_profile=p4_profile,
        aspect_ratio=aspect_ratio,
    )


def run_phase4_fcpxml(
    project_root: Path,
    ep: str,
    allow_pre_vsr: bool = False,
    parse_md_fn: Callable[[str], list[dict[str, Any]]] | None = None,
    target_fps: int | None = None,
    p4_profile: str | None = None,
    aspect_ratio: str | None = None,
) -> dict[str, Any]:
    ep_s = _norm_ep(ep)
    legacy_conflict = v42_qc.inspect_legacy_fps_conflict_v42(project_root, ep_s, explicit_fps=target_fps)
    tfps = v42_qc.resolve_target_fps_v42(project_root, ep_s, explicit_fps=target_fps, prefer_bible_first=False)
    prof_spec = v42_qc.resolve_p4_profile_v46(project_root, ep_s, explicit_profile=p4_profile, explicit_aspect_ratio=aspect_ratio)
    eff_prof = prof_spec["p4_profile"] or None
    tw, th = int(prof_spec["width"]), int(prof_spec["height"])
    ep_dir = project_root / "分集" / ep_s
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    done_4k_dir = work_dir / v42_qc.done_vsr_dir_name(tfps, p4_profile=eff_prof)
    extra_done_dirs = [ep_dir / v42_qc.done_vsr_dir_name(tfps, p4_profile=eff_prof)]
    raw_gen_dir = work_dir / "原始生成视频"
    work_dir.mkdir(parents=True, exist_ok=True)
    ep_dir.mkdir(parents=True, exist_ok=True)

    cspec = load_cuts_spec(project_root, ep_s)
    seg_targets: list[tuple[str, float]] = []
    if cspec["ordered_segments"]:
        for sk in cspec["ordered_segments"]:
            rd = float(cspec["seg_real_dur"].get(sk) or 0.0)
            if rd > 0:
                seg_targets.append((sk, round(rd, 3)))
    if not seg_targets and parse_md_fn is not None:
        for md_cand in (ep_dir / f"{ep_s}_视频提示词.md", project_root / "中间文件" / "提示词维护" / "视频提示词" / ep_s / "当前主稿.md"):
            if md_cand.exists() and md_cand.stat().st_size > 0:
                for s_it in parse_md_fn(md_cand.read_text(encoding="utf-8-sig")):
                    sk = str(s_it.get("segment") or "").strip()
                    rd = float(s_it.get("duration_sec") or 0.0)
                    if sk and rd > 0:
                        seg_targets.append((sk, round(rd, 3)))
                if seg_targets:
                    break

    if not seg_targets:
        err_doc: dict[str, Any] = {
            "status": "FCPXML_ERR_NO_SEGMENTS", "error_code": "FCPXML_ERR_NO_SEGMENTS",
            "episode": ep_s, "target_fps": tfps,
            "reason": "cuts.csv 与视频提示词清单均为空或缺失，无法构建达芬奇时间线",
        }
        if legacy_conflict["legacy_plan_fps_conflict"]:
            err_doc["legacy_plan_fps_conflict"] = True
            err_doc["legacy_plan_fps_warning"] = legacy_conflict["legacy_plan_fps_warning"]
        print(json.dumps(err_doc, ensure_ascii=False, indent=2))
        sys.exit(2)

    verified_clips = v42_qc.check_and_collect_fcpxml_clips_v42(
        ep_s, seg_targets, done_4k_dir, raw_gen_dir, allow_pre_vsr,
        target_fps=tfps, extra_done_dirs=extra_done_dirs, legacy_conflict=legacy_conflict,
        target_width=tw, target_height=th, p4_profile=prof_spec["p4_profile"],
    )
    return _write_fcpxml_and_handover(
        project_root, ep_s, ep_dir, work_dir, cspec, verified_clips, allow_pre_vsr, tfps, legacy_conflict,
        prof_spec=prof_spec,
    )


def _find_existing_music_files(project_root: Path, ep_dir: Path, work_dir: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for mdir in (ep_dir / "配乐", ep_dir / "音乐", work_dir / "配乐", project_root / "配乐", project_root / "音乐资产"):
        if not mdir.exists():
            continue
        for ext in ("*.wav", "*.mp3", "*.flac", "*.m4a"):
            for p in sorted(mdir.glob(ext)):
                rp = p.resolve()
                if p.is_file() and p.stat().st_size > 0 and rp not in seen:
                    seen.add(rp)
                    found.append(p)
    return found


def _write_fcpxml_and_handover(
    project_root: Path, ep_s: str, ep_dir: Path, work_dir: Path, cspec: dict[str, Any],
    verified_clips: list[dict[str, Any]], allow_pre_vsr: bool, target_fps: int = v42_qc.DEFAULT_TARGET_FPS,
    legacy_conflict: dict[str, Any] | None = None,
    prof_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tfps = v42_qc.normalize_target_fps(target_fps)
    ps = prof_spec or v42_qc.get_p4_profile_spec(None)
    dlg_lines = resolve_dialogue_schedule(project_root, ep_s, cuts_spec=cspec)
    music_files = _find_existing_music_files(project_root, ep_dir, work_dir)
    out_fcpxml, align_path, clip_specs, audio_mounts, bgm_mounts, total_dur, trimmed_handles_total, layout_aligned = (
        v42_qc.build_fcpxml_1_9_tree_and_layout_v42(
            project_root, ep_s, ep_dir, work_dir, verified_clips, dlg_lines, music_files, probe_wav_strict,
            target_fps=tfps, legacy_conflict=legacy_conflict,
            target_width=int(ps["width"]), target_height=int(ps["height"]), p4_profile=str(ps["p4_profile"]),
        )
    )
    return _write_handover_manifest_and_md(
        ep_s, work_dir, out_fcpxml, align_path, clip_specs, audio_mounts, bgm_mounts,
        total_dur, trimmed_handles_total, layout_aligned, allow_pre_vsr, tfps, legacy_conflict,
        prof_spec=ps,
    )


def _write_handover_manifest_and_md(
    ep_s: str, work_dir: Path, out_fcpxml: Path, align_path: Path,
    clip_specs: list[tuple[str, str, float, float]],
    audio_mounts: list[dict[str, Any]], bgm_mounts: list[dict[str, Any]],
    total_dur: float, trimmed_handles_total: float, layout_aligned: bool, allow_pre_vsr: bool,
    target_fps: int = v42_qc.DEFAULT_TARGET_FPS,
    legacy_conflict: dict[str, Any] | None = None,
    prof_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tfps = v42_qc.normalize_target_fps(target_fps)
    ps = prof_spec or v42_qc.get_p4_profile_spec(None)
    tw, th, res_tag = int(ps["width"]), int(ps["height"]), str(ps["res_tag"])
    dlg_cnt = sum(1 for a in audio_mounts if a["track"] == "Dialogue_Track")
    os_cnt = sum(1 for a in audio_mounts if a["track"] == "OS_Track")
    handover_doc: dict[str, Any] = {
        "episode": ep_s,
        "p4_profile": ps["p4_profile"],
        "profile_status": ps["profile_status"],
        "profile_note": ps["profile_note"],
        "aspect_ratio": str(ps.get("aspect_ratio") or "16:9"),
        "target_resolution": f"{tw}x{th}",
        "target_fps": tfps,
        "fcpxml_path": str(out_fcpxml).replace("\\", "/"),
        "lipsync_subtitle_audit_json": str(align_path).replace("\\", "/"),
        "xml_generated_items": {
            "v1_main_video_and_native_sfx": {
                "status": "AUTO_MOUNTED_IN_XML",
                "clips_count": len(clip_specs),
                "total_duration_sec": round(total_dur, 3),
                "trimmed_tail_handles_sec": round(trimmed_handles_total, 3),
                "note": "V1 主视频已裁切尾部 Handles 余量并仅保留原生纯环境 SFX 音轨（零对白人声混流）",
            },
            "lane_1_dialogue_track": {"status": "AUTO_MOUNTED_IN_XML", "lane": "1", "audioRole": "dialogue.Dialogue_Track", "clips_count": dlg_cnt},
            "lane_2_os_track": {"status": "AUTO_MOUNTED_IN_XML", "lane": "2", "audioRole": "dialogue.OS_Track", "clips_count": os_cnt},
            "lane_minus_1_music_track": {
                "status": "AUTO_MOUNTED_IN_XML" if bgm_mounts else "NO_EXISTING_BGM_FILE_FOUND",
                "lane": "-1", "audioRole": "music.MusicFX_Track",
                "mounted_files": [b["path"] for b in bgm_mounts],
            },
        },
        "davinci_manual_checklist": [
            "Color 页面切换 Timeline 全局模式挂载三节点树：Node 1 一级暗部黑电平与色温统一；Node 2 Midtone Detail +20 中频破塑；Node 3 挂载 Kodak 2383 LUT",
            "Edit 页面新建 V2 顶轨铺设 35mm Film Grain Overlay 4K（混合模式设为 Overlay）",
            "Fairlight / Edit 页面对硬切对白交界执行 0.25s L-Cut / J-Cut 微调，并确认配乐轨对白处 -8dB 闪避",
            "导入本集字幕（.srt / .csv），在字幕「检查器 → 描边（Stroke）」开启 **2–3 px** 纯黑描边并人工终审对齐",
            f"Deliver 交付页按 {tw}x{th} @ {tfps}fps 导出 分集/{ep_s}/{ep_s}_{res_tag}{tfps}fps_成片.mp4：H.264 导出必须将「编码配置文件」显式设为 **High**（或 `H.265 Main10`）",
        ],
    }
    if legacy_conflict and legacy_conflict.get("legacy_plan_fps_conflict"):
        handover_doc["legacy_plan_fps_conflict"] = True
        handover_doc["legacy_plan_fps_warning"] = str(legacy_conflict.get("legacy_plan_fps_warning") or "")
    handover_json = work_dir / "第四棒_时间线与达芬奇交接单.json"
    handover_md = work_dir / "第四棒_时间线与达芬奇交接单.md"
    handover_json.write_text(json.dumps(handover_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# {ep_s} · 第四棒时间线与达芬奇（DaVinci Resolve）交接单", "",
        *(
            [f"> **档位状态提示** (`{ps['p4_profile']}` / `{ps['profile_status']}`): {ps['profile_note']}", ""]
            if ps.get("profile_note") else []
        ),
        "## 一、FCPXML 已自动生成挂载项 (`xml_generated_items`)",
        f"- **目标规格**：`{tw}x{th} @ {tfps}fps`（档位 `{ps['p4_profile'] or 'default_4k'}`，状态 `{ps['profile_status']}`）",
        f"- **`V1` 裁切主视频 + 原生环境 SFX**：共 `{len(clip_specs)}` 段（总长 `{total_dur:.2f}s`，自动裁切尾部余量 `{trimmed_handles_total:.2f}s`）",
        f"- **`Lane 1 Dialogue_Track`**：已自动挂载开口对白音频 `{dlg_cnt}` 条",
        f"- **`Lane 2 OS_Track`**：已自动挂载心声独白音频 `{os_cnt}` 条",
        f"- **`lane=-1 MusicFX_Track`**：{'已自动挂载配乐 ' + ', '.join(Path(b['path']).name for b in bgm_mounts) if bgm_mounts else '未检测到现成配乐文件（可按需拖入配乐）'}", "",
        "## 二、达芬奇导出前人工确认项 (`davinci_manual_checklist`)",
        *[f"{i}. {line}" for i, line in enumerate(handover_doc["davinci_manual_checklist"], 1)], "",
    ]
    md_text = "\n".join(md_lines)
    handover_md.write_text(md_text, encoding="utf-8")
    (work_dir / "时间线与达芬奇交接单.md").write_text(md_text, encoding="utf-8")
    (work_dir / "第四棒_达芬奇三节点破塑与混音指引.md").write_text(md_text, encoding="utf-8")

    summary: dict[str, Any] = {
        "status": "BUILD_FCPXML_OK", "episode": ep_s, "p4_profile": ps["p4_profile"],
        "profile_status": ps["profile_status"], "aspect_ratio": str(ps.get("aspect_ratio") or "16:9"),
        "target_resolution": f"{tw}x{th}",
        "target_fps": tfps, "allow_pre_vsr": allow_pre_vsr,
        "fcpxml_path": str(out_fcpxml).replace("\\", "/"), "fcpxml_1_9_dtd_valid": True,
        "handover_json": str(handover_json).replace("\\", "/"), "handover_md": str(handover_md).replace("\\", "/"),
        "davinci_guide_md": str(handover_md).replace("\\", "/"),
        "lipsync_subtitle_audit_json": str(align_path).replace("\\", "/"),
        "timeline_layout_aligned": layout_aligned, "master_render_verified": False,
        "timeline_clips": len(clip_specs), "trimmed_handles_sec": round(trimmed_handles_total, 2),
        "mounted_dialogue_track_clips": dlg_cnt, "mounted_os_track_clips": os_cnt,
        "mounted_music_track_clips": len(bgm_mounts), "total_duration_sec": round(total_dur, 2),
    }
    if legacy_conflict and legacy_conflict.get("legacy_plan_fps_conflict"):
        summary["legacy_plan_fps_conflict"] = True
        summary["legacy_plan_fps_warning"] = str(legacy_conflict.get("legacy_plan_fps_warning") or "")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def run_phase4_triage_and_colab(
    project_root: Path,
    ep: str,
    target_fps: int | None = None,
    p4_profile: str | None = None,
    aspect_ratio: str | None = None,
) -> dict[str, Any]:
    ep_s = _norm_ep(ep)
    legacy_conflict = v42_qc.inspect_legacy_fps_conflict_v42(project_root, ep_s, explicit_fps=target_fps)
    tfps = v42_qc.resolve_target_fps_v42(project_root, ep_s, explicit_fps=target_fps, prefer_bible_first=True)
    prof_spec = v42_qc.resolve_p4_profile_v46(project_root, ep_s, explicit_profile=p4_profile, explicit_aspect_ratio=aspect_ratio)
    eff_prof = prof_spec["p4_profile"] or None
    ar = str(prof_spec.get("aspect_ratio") or "16:9")
    tw, th, res_tag = int(prof_spec["width"]), int(prof_spec["height"]), str(prof_spec["res_tag"])
    vsr_preset = str(prof_spec["vsr_preset"])
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    raw_gen_dir = work_dir / "原始生成视频"
    to_vsr_dir = work_dir / "待超分视频"
    done_4k_dir = work_dir / v42_qc.done_vsr_dir_name(tfps, p4_profile=eff_prof)
    raw_gen_dir.mkdir(parents=True, exist_ok=True)
    to_vsr_dir.mkdir(parents=True, exist_ok=True)
    done_4k_dir.mkdir(parents=True, exist_ok=True)

    cspec = load_cuts_spec(project_root, ep_s)
    dlg_schedule = resolve_dialogue_schedule(project_root, ep_s, cuts_spec=cspec)
    lipsync_by_seg: dict[str, list[dict[str, Any]]] = {}
    for rec in dlg_schedule:
        if rec.get("needs_lipsync"):
            lipsync_by_seg.setdefault(rec["segment"], []).append(rec)

    ready_4k_clips: list[dict[str, Any]] = []
    native_4k_need_proc: list[dict[str, Any]] = []
    one_step_1080p: list[dict[str, Any]] = []
    two_step_720p: list[dict[str, Any]] = []
    queued_vsr: list[dict[str, Any]] = []
    missing_audio: list[str] = []

    for mp4 in sorted(raw_gen_dir.glob("*.mp4")):
        m_seg = re.search(r"(segment_\d+)", mp4.stem)
        seg_id = m_seg.group(1) if m_seg else mp4.stem
        meta = probe_media_strict(mp4)
        w = int(meta.get("width") or 0)
        h = int(meta.get("height") or 0)
        fps = float(meta.get("fps") or 0.0)
        dur = float(meta.get("duration_sec") or 0.0)
        if not meta.get("has_audio"):
            missing_audio.append(mp4.name)

        needs_lipsync = bool(lipsync_by_seg.get(seg_id))
        needs_upscale = (w < tw) or (h < th)
        needs_fps_interp = not v42_qc.is_fps_compliant(fps, tfps)
        exact_res = (w == tw and h == th)

        info = {
            "file": mp4.name, "segment": seg_id, "resolution": f"{w}x{h}", "fps": fps, "target_fps": tfps,
            "target_resolution": f"{tw}x{th}", "p4_profile": prof_spec["p4_profile"], "vsr_preset": vsr_preset,
            "duration_sec": round(dur, 3), "needs_lipsync": needs_lipsync,
            "needs_upscale": needs_upscale, "needs_fps_interp": needs_fps_interp,
        }
        stale_done = done_4k_dir / mp4.name
        stale_vsr = to_vsr_dir / mp4.name

        if (not needs_lipsync) and (not needs_upscale) and (not needs_fps_interp) and exact_res:
            shutil.copy2(str(mp4), str(stale_done))
            if stale_vsr.exists():
                stale_vsr.unlink()
            info["vsr_strategy"] = f"already_{res_tag.lower()}{tfps}_passthrough"
            ready_4k_clips.append(info)
        else:
            if stale_done.exists():
                stale_done.unlink()
            shutil.copy2(str(mp4), str(stale_vsr))
            if not needs_upscale:
                if eff_prof == "live_1k":
                    strat = f"live_1k_passthrough_high_{tfps}fps"
                else:
                    strat = f"native_{res_tag.lower()}_lipsync_or_rife60" if tfps == 60 else f"native_{res_tag.lower()}_lipsync_keep_24fps"
                native_4k_need_proc.append({**info, "vsr_strategy": strat})
            elif w >= 1920 or h >= 1080:
                strat = f"one_step_1080p_to_{res_tag.lower()}_{tfps}fps"
                one_step_1080p.append({**info, "vsr_strategy": strat})
            else:
                strat = f"one_step_720p_to_1k_{tfps}fps" if res_tag == "1K" else f"two_step_720p_to_1080p_to_4k_{tfps}fps"
                two_step_720p.append({**info, "vsr_strategy": strat})
            info["vsr_strategy"] = strat
            queued_vsr.append(info)

    nb_path = _build_v41_phase4_colab_notebook(project_root, ep_s, target_fps=tfps, p4_profile=eff_prof, aspect_ratio=ar)
    vsr_plan: dict[str, Any] = {
        "status": "BUILD_COLAB_OK",
        "episode": ep_s,
        "p4_profile": prof_spec["p4_profile"],
        "profile_status": prof_spec["profile_status"],
        "profile_note": prof_spec["profile_note"],
        "aspect_ratio": ar,
        "vsr_preset": vsr_preset,
        "target_width": tw,
        "target_height": th,
        "target_resolution": f"{tw}x{th}",
        "target_fps": tfps,
        "done_vsr_dir": str(done_4k_dir).replace("\\", "/"),
        "colab_notebook_ipynb": str(nb_path).replace("\\", "/"),
        "step2_5_lipsync_enabled": bool(lipsync_by_seg),
        "step2_5_lipsync_engine": "LatentSync_1.6_MultiLine_Adelay_Driver + Pure_Environment_SFX_Remux",
        "latentsync_pinned_commit": LATENTSYNC_PINNED_COMMIT,
        "lipsync_segments_count": len(lipsync_by_seg),
        "lipsync_dialogue_wavs_count": sum(len(v) for v in lipsync_by_seg.values()),
        "native_4k_bypassed_count": len(ready_4k_clips),
        "native_4k_queued_rife_or_lipsync_count": len(native_4k_need_proc),
        "one_step_1080p_to_4k_count": len(one_step_1080p),
        "two_step_720p_to_1080p_to_4k_count": len(two_step_720p),
        "queued_for_colab_a100_count": len(queued_vsr),
        "missing_native_sfx_warning": missing_audio,
        "native_4k_clips": ready_4k_clips,
        "native_4k_queued_clips": native_4k_need_proc,
        "one_step_1080p_clips": one_step_1080p,
        "two_step_720p_clips": two_step_720p,
        "queued_vsr_clips": queued_vsr,
    }
    if legacy_conflict["legacy_plan_fps_conflict"]:
        vsr_plan["legacy_plan_fps_conflict"] = True
        vsr_plan["legacy_plan_fps_warning"] = legacy_conflict["legacy_plan_fps_warning"]
    plan_txt = json.dumps(vsr_plan, ensure_ascii=False, indent=2)
    (work_dir / "Colab_A100_超分任务清单.json").write_text(plan_txt, encoding="utf-8")
    (work_dir / "phase4_tasks.json").write_text(plan_txt, encoding="utf-8")
    print(plan_txt)
    return vsr_plan


def _build_v41_phase4_colab_notebook(
    project_root: Path,
    ep_s: str,
    target_fps: int = v42_qc.DEFAULT_TARGET_FPS,
    p4_profile: str | None = None,
    aspect_ratio: str = "16:9",
) -> Path:
    tfps = v42_qc.normalize_target_fps(target_fps)
    rel_hint = _extract_drive_rel_hint(project_root)
    proj_name = project_root.resolve().name
    ep_dir = project_root / "分集" / ep_s
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    ep_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    cell_lipsync_code = [
        "# === Step 1：挂载云盘（相对路径零硬编码） + 按需多句延迟起音 adelay 驱动字节开源 LatentSync 1.6 + 纯净环境 SFX 声画分离封装 ===\n",
        "import csv, json, os, re, shutil, subprocess, sys\n",
        "from pathlib import Path\n\n",
        f'PROJECT_NAME = "{proj_name}"\n',
        f'REL_FROM_DRIVE = "{rel_hint}"\n',
        f'EPISODE = "{ep_s}"\n\n',
        "def resolve_colab_project_root(project_name: str, rel_hint: str) -> Path:\n",
        "    drive_my = Path('/content/drive/MyDrive')\n",
        "    for cand in [drive_my / rel_hint, drive_my / '短剧复刻工作站' / '01_在制项目库' / project_name, drive_my / project_name]:\n",
        "        if cand.exists(): return cand.resolve()\n",
        "    matches = list(drive_my.glob(f'*/*/{project_name}')) + list(drive_my.glob(f'*/{project_name}'))\n",
        "    if matches: return matches[0].resolve()\n",
        "    raise FileNotFoundError(f'云盘未找到项目目录: {project_name} ({rel_hint})')\n\n",
        "from google.colab import drive\n",
        "drive.mount('/content/drive')\n",
        "PROJECT_ROOT = resolve_colab_project_root(PROJECT_NAME, REL_FROM_DRIVE)\n",
        "EP_DIR = PROJECT_ROOT / '分集' / EPISODE\n",
        "WORK_DIR = PROJECT_ROOT / '中间文件' / '在制工作区' / EPISODE\n",
        "ANA_DIR = PROJECT_ROOT / '中间文件' / '原片分析' / EPISODE\n",
        "TO_VSR_DIR = WORK_DIR / '待超分视频'\n",
        "LIPSYNC_OUT_DIR = WORK_DIR / '已重刷口型视频'\n",
        "DLG_WAV_DIR = EP_DIR / '台词音频'\n",
        "LIPSYNC_OUT_DIR.mkdir(parents=True, exist_ok=True)\n\n",
        "def parse_seg_shot(text: str):\n",
        "    ms = re.search(r'(segment_\\d+)', str(text or ''))\n",
        "    mh = re.search(r'(镜头\\d+)', str(text or ''))\n",
        "    return (ms.group(1) if ms else 'segment_01'), (mh.group(1) if mh else '镜头01')\n\n",
        "def parse_t_sec(ts_s: str) -> float:\n",
        "    s = str(ts_s or '').strip().replace(',', '.').rstrip('s秒')\n",
        "    if not s: return 0.0\n",
        "    ps = s.split(':')\n",
        "    return float(ps[0])*3600+float(ps[1])*60+float(ps[2]) if len(ps)==3 else (float(ps[0])*60+float(ps[1]) if len(ps)==2 else float(s))\n\n",
        "def probe_stream(mp4_p: Path) -> dict:\n",
        "    r = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_entries', 'format=duration:stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate,duration,nb_frames,nb_read_frames', '-of', 'json', str(mp4_p)], capture_output=True, text=True, check=True)\n",
        "    d = json.loads(r.stdout or '{}')\n",
        "    vs = next((s for s in d.get('streams', []) if s.get('codec_type') == 'video'), {})\n",
        "    rf = str(vs.get('avg_frame_rate') or vs.get('r_frame_rate') or '0/1')\n",
        "    fps = (float(rf.split('/')[0]) / max(1.0, float(rf.split('/')[1]))) if '/' in rf else float(rf or 0.0)\n",
        "    vf = int(vs.get('nb_read_frames') or vs.get('nb_frames') or 0) if str(vs.get('nb_read_frames') or vs.get('nb_frames') or '').isdigit() else 0\n",
        "    vd = float(vs.get('duration') or 0.0) if str(vs.get('duration') or '') not in ('', 'N/A', 'None') else (vf / fps if (vf > 0 and fps > 0) else 0.0)\n",
        "    return {'width': int(vs.get('width') or 0), 'height': int(vs.get('height') or 0), 'fps': round(fps, 4), 'video_duration_sec': round(vd, 4), 'video_frames': vf or (int(round(vd * fps)) if fps > 0 else 0), 'duration_sec': round(vd, 4)}\n\n",
        "shot_rel_map, seg_ep_start = {}, {}\n",
        "for c_p in (WORK_DIR / 'cuts.csv', ANA_DIR / 'cuts.csv', EP_DIR / 'cuts.csv'):\n",
        "    if c_p.exists() and c_p.stat().st_size > 0:\n",
        "        rows_c = list(csv.DictReader(c_p.open('r', encoding='utf-8-sig')))\n",
        "        base_t = min((float(x.get('start_sec') or 0.0) for x in rows_c), default=0.0)\n",
        "        for x in rows_c:\n",
        "            sg, sh = parse_seg_shot(f\"{x.get('segment','')}_{x.get('shot','')}\")\n",
        "            st_v, ed_v = float(x.get('start_sec') or 0.0), float(x.get('end_sec') or 0.0)\n",
        "            seg_ep_start.setdefault(sg, st_v - base_t)\n",
        "            s0 = seg_ep_start[sg] + base_t\n",
        "            shot_rel_map[(sg, sh)] = (max(0.0, st_v - s0), max(0.0, ed_v - s0))\n",
        "        break\n",
    ]
    return _finish_v41_phase4_colab_notebook(
        ep_s, ep_dir, work_dir, cell_lipsync_code, tfps, p4_profile=p4_profile, aspect_ratio=aspect_ratio,
    )


def _finish_v41_phase4_colab_notebook(
    ep_s: str, ep_dir: Path, work_dir: Path, cell_lipsync_head: list[str],
    target_fps: int = v42_qc.DEFAULT_TARGET_FPS, p4_profile: str | None = None,
    aspect_ratio: str = "16:9",
) -> Path:
    tfps = v42_qc.normalize_target_fps(target_fps)
    f_lo, f_hi = v42_qc.fps_bounds(tfps)
    ar = v42_qc.normalize_aspect_ratio(aspect_ratio)
    prof_spec = v42_qc.get_p4_profile_spec(p4_profile, aspect_ratio=ar)
    eff_prof = prof_spec["p4_profile"] or None
    tw, th, res_tag = int(prof_spec["width"]), int(prof_spec["height"]), str(prof_spec["res_tag"])
    cell_lipsync_tail = [
        "seg_lines_map = {}\n",
        "manifest_p = DLG_WAV_DIR / '台词音频清单.json'\n",
        "sub_csv_p = EP_DIR / f'{EPISODE}_字幕时间轴.csv'\n",
        "dlg_csv_p = EP_DIR / f'{EPISODE}_台词生成表.csv'\n",
        "sub_map = {}\n",
        "if sub_csv_p.exists():\n",
        "    for sr in csv.DictReader(sub_csv_p.open('r', encoding='utf-8-sig')):\n",
        "        sg_s, sh_s = parse_seg_shot(f\"{sr.get('segment','')}_{sr.get('shot','')}\")\n",
        "        tr = str(sr.get('时间') or '')\n",
        "        if sr.get('start_in_seg_sec') not in (None, ''): sub_map[(sg_s, sh_s)] = (float(sr['start_in_seg_sec']), float(sr.get('end_in_seg_sec') or 2.4))\n",
        "        elif '-->' in tr:\n",
        "            p1, p2 = tr.split('-->', 1); s0_ep = seg_ep_start.get(sg_s, 0.0)\n",
        "            sub_map[(sg_s, sh_s)] = (max(0.0, parse_t_sec(p1) - s0_ep), max(0.2, parse_t_sec(p2) - s0_ep))\n",
        "raw_recs = json.loads(manifest_p.read_text(encoding='utf-8-sig')).get('dialogue_records', []) if manifest_p.exists() else []\n",
        "if not raw_recs and dlg_csv_p.exists():\n",
        "    for r in csv.DictReader(dlg_csv_p.open('r', encoding='utf-8-sig')):\n",
        "        fn = (r.get('输出音频文件名') or '').strip()\n",
        "        if not fn or fn == 'SKIP_JIMENG_NATIVE' or '即梦原生直出' in (r.get('视频通道与口型策略') or ''): continue\n",
        "        sg, sh = parse_seg_shot(r.get('分段与镜号') or fn)\n",
        "        sh_s, sh_e = sub_map.get((sg, sh)) or shot_rel_map.get((sg, sh), (0.0, 2.4))\n",
        "        m_ts = re.search(r'(\\d+(?::\\d+)*(?:\\.\\d+)?)s?\\s*[-–~]\\s*(\\d+(?::\\d+)*(?:\\.\\d+)?)s?', r.get('起止时间轴与目标时长') or '')\n",
        "        if m_ts:\n",
        "            t1, t2 = parse_t_sec(m_ts.group(1)), parse_t_sec(m_ts.group(2))\n",
        "            s0_ep = seg_ep_start.get(sg, 0.0)\n",
        "            sh_s, sh_e = (t1, t2) if (':' not in m_ts.group(1) and t1 < 15.0) else (max(0.0, t1 - s0_ep), max(0.2, t2 - s0_ep))\n",
        "        raw_recs.append({'segment': sg, 'shot': sh, 'speaker': r.get('说话角色',''), 'file_name': fn, 'needs_lipsync': ('心声' not in (r.get('台词类型') or '') and '_心声' not in fn), 'start_in_seg_sec': sh_s, 'end_in_seg_sec': sh_e})\n",
        "pending_mp4s = sorted(TO_VSR_DIR.glob('segment_*.mp4'))\n",
        "pending_segs = {parse_seg_shot(p.stem)[0] for p in pending_mp4s}\n",
        "for rec in raw_recs:\n",
        "    fn = str(rec.get('file_name') or '').strip()\n",
        "    if not fn or fn == 'SKIP_JIMENG_NATIVE' or not rec.get('needs_lipsync', '_心声' not in fn): continue\n",
        "    sg, sh = parse_seg_shot(f\"{rec.get('segment','')}_{rec.get('shot','')}_{fn}\")\n",
        "    if sg not in pending_segs: continue\n",
        "    wp = DLG_WAV_DIR / fn\n",
        "    if not wp.exists(): raise FileNotFoundError(f'缺少待刷口型对白音频: {wp}')\n",
        "    st_s = float(rec.get('start_in_seg_sec') if rec.get('start_in_seg_sec') not in (None, '') else shot_rel_map.get((sg, sh), (0.0, 2.4))[0])\n",
        "    ed_s = float(rec.get('end_in_seg_sec') if rec.get('end_in_seg_sec') not in (None, '') else max(st_s + 0.5, shot_rel_map.get((sg, sh), (0.0, 2.4))[1]))\n",
        "    if st_s == 0.0 and sh != '镜头01' and (sg, sh) in shot_rel_map: st_s, ed_s = shot_rel_map[(sg, sh)]\n",
        "    seg_lines_map.setdefault(sg, []).append({'segment': sg, 'shot': sh, 'speaker': rec.get('speaker',''), 'start_in_seg_sec': st_s, 'end_in_seg_sec': ed_s, 'wav': wp})\n\n",
        "active_ls_mp4s = [p for p in pending_mp4s if seg_lines_map.get(parse_seg_shot(p.stem)[0])]\n",
        "if not active_ls_mp4s:\n",
        "    for mp4_path in pending_mp4s:\n",
        "        shutil.copy2(mp4_path, LIPSYNC_OUT_DIR / mp4_path.name)\n",
        "    print('⏭️ Step 1 所有待处理片段均无待刷口型对白，已直接透传拷贝并跳过 LatentSync 仓库/依赖/权重初始化！')\n",
        "else:\n",
        "    LATENTSYNC_GIT_URL = 'https://github.com/bytedance/LatentSync.git'\n",
        "    LATENTSYNC_PINNED_COMMIT = 'd058a61'\n",
        "    LATENTSYNC_HF_REPO = 'ByteDance/LatentSync-1.6'\n",
        "    LATENTSYNC_REPO = Path(os.environ.get('LATENTSYNC_REPO', '/content/LatentSync'))\n",
        "    if not (LATENTSYNC_REPO / '.git').exists() and not (LATENTSYNC_REPO / 'scripts' / 'inference.py').is_file():\n",
        "        subprocess.run(['git', 'clone', LATENTSYNC_GIT_URL, str(LATENTSYNC_REPO)], check=True)\n",
        "    if (LATENTSYNC_REPO / '.git').exists():\n",
        "        subprocess.run(['git', 'checkout', '--detach', LATENTSYNC_PINNED_COMMIT], cwd=str(LATENTSYNC_REPO), check=True)\n",
        "        head_sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(LATENTSYNC_REPO), capture_output=True, text=True, check=True).stdout.strip().lower()\n",
        "        if not head_sha.startswith(LATENTSYNC_PINNED_COMMIT.lower()):\n",
        "            raise RuntimeError(f'LatentSync HEAD 校验失败: {head_sha} != {LATENTSYNC_PINNED_COMMIT}')\n",
        "    _ls_py = sys.executable\n",
        "    _ls_env = {**os.environ, 'MPLBACKEND': 'Agg'}\n",
        "    if os.environ.get('LATENTSYNC_SKIP_PIP', '') != '1':\n",
        "        req_src = LATENTSYNC_REPO / 'requirements.txt'\n",
        "        if sys.version_info >= (3, 12):\n",
        "            subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'uv'], check=True)\n",
        "            _ls_venv = Path(os.environ.get('LATENTSYNC_VENV', '/content/ls_venv'))\n",
        "            if not (_ls_venv / 'bin' / 'python').is_file():\n",
        "                subprocess.run(['uv', 'venv', str(_ls_venv), '--python', '3.10'], check=True)\n",
        "            _ls_py = str(_ls_venv / 'bin' / 'python')\n",
        "            _uv_lines = [ln.strip() for ln in (req_src.read_text(encoding='utf-8', errors='replace').splitlines() if req_src.is_file() else []) if ln.strip() and not ln.strip().startswith(('--extra-index-url', 'gradio', 'torch==', 'torchvision==', 'torchaudio=='))]\n",
        "            _colab_req = LATENTSYNC_REPO / '_colab_requirements.txt'\n",
        "            _colab_req.write_text('\\n'.join(_uv_lines) + '\\n', encoding='utf-8')\n",
        "            subprocess.run(['uv', 'pip', 'install', '--python', _ls_py, 'torch==2.5.1', 'torchvision==0.20.1', 'torchaudio==2.5.1', '--index-url', 'https://download.pytorch.org/whl/cu124'], check=True)\n",
        "            subprocess.run(['uv', 'pip', 'install', '--python', _ls_py, '-r', str(_colab_req), 'DeepCache==0.1.1', 'huggingface-hub==0.30.2'], check=True)\n",
        "        elif req_src.is_file():\n",
        "            try:\n",
        "                import torch as _t_chk\n",
        "                _has_cuda = bool(_t_chk.cuda.is_available())\n",
        "            except Exception:\n",
        "                _has_cuda = False\n",
        "            _req_lines = []\n",
        "            for _ln in req_src.read_text(encoding='utf-8', errors='replace').splitlines():\n",
        "                _st = _ln.strip()\n",
        "                if _has_cuda and re.match(r'^(torch|torchvision|torchaudio|xformers)([=<>!~\\s\\[]|$)', _st, re.I):\n",
        "                    continue\n",
        "                _req_lines.append(_ln)\n",
        "            _colab_req = LATENTSYNC_REPO / '_colab_requirements.txt'\n",
        "            _colab_req.write_text('\\n'.join(_req_lines) + '\\n', encoding='utf-8')\n",
        "            subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(_colab_req), 'DeepCache==0.1.1', 'huggingface_hub'], check=True)\n",
        "        else:\n",
        "            subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'DeepCache==0.1.1', 'huggingface_hub', 'omegaconf', 'diffusers', 'transformers', 'accelerate', 'einops', 'decord', 'insightface', 'onnxruntime-gpu', 'mediapipe', 'imageio', 'kornia', 'librosa', 'av', 'scenedetect', 'ffmpeg-python'], check=True)\n",
        "    unet_cfg = LATENTSYNC_REPO / 'configs' / 'unet' / 'stage2_512.yaml'\n",
        "    ckpt_dir = LATENTSYNC_REPO / 'checkpoints'\n",
        "    ckpt_pt = ckpt_dir / 'latentsync_unet.pt'\n",
        "    whisper_pt = ckpt_dir / 'whisper' / 'tiny.pt'\n",
        "    drive_ls_dir = Path('/content/drive/MyDrive/短剧复刻工作站/models/LatentSync-1.6')\n",
        "    for rel_w, dst_w in (('latentsync_unet.pt', ckpt_pt), ('whisper/tiny.pt', whisper_pt)):\n",
        "        drv_w = drive_ls_dir / rel_w\n",
        "        if not dst_w.is_file() and drv_w.is_file():\n",
        "            dst_w.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(drv_w, dst_w)\n",
        "    if not ckpt_pt.is_file() or not whisper_pt.is_file():\n",
        "        from huggingface_hub import hf_hub_download\n",
        "        ckpt_dir.mkdir(parents=True, exist_ok=True)\n",
        "        if not ckpt_pt.is_file(): hf_hub_download(repo_id=LATENTSYNC_HF_REPO, filename='latentsync_unet.pt', local_dir=str(ckpt_dir))\n",
        "        if not whisper_pt.is_file(): hf_hub_download(repo_id=LATENTSYNC_HF_REPO, filename='whisper/tiny.pt', local_dir=str(ckpt_dir))\n",
        "    if not unet_cfg.is_file() or not ckpt_pt.is_file() or not whisper_pt.is_file():\n",
        "        raise FileNotFoundError(f'LatentSync 1.6 前置校验失败: unet_cfg={unet_cfg} ({unet_cfg.is_file()}), ckpt_pt={ckpt_pt} ({ckpt_pt.is_file()}), whisper_pt={whisper_pt} ({whisper_pt.is_file()})')\n",
        "    subprocess.run([_ls_py, '-m', 'scripts.inference', '--help'], cwd=str(LATENTSYNC_REPO), env=_ls_env, check=True)\n",
        "    _HAS_FPS_MODE = 'fps_mode' in subprocess.run(['ffmpeg', '-hide_banner', '-h', 'long'], capture_output=True, text=True).stdout\n",
        "    def _relabel_fps_args(fps_v: int) -> list:\n",
        "        return ['-fps_mode', 'passthrough', '-enc_time_base', f'1/{fps_v}'] if _HAS_FPS_MODE else ['-vsync', '0', '-r', str(fps_v)]\n",
        "    for mp4_path in pending_mp4s:\n",
        "        seg_id, _ = parse_seg_shot(mp4_path.stem)\n",
        "        lines = sorted(seg_lines_map.get(seg_id, []), key=lambda x: x['start_in_seg_sec'])\n",
        "        out_mp4 = LIPSYNC_OUT_DIR / mp4_path.name\n",
        "        if not lines:\n",
        "            shutil.copy2(mp4_path, out_mp4); continue\n",
        "        src_st = probe_stream(mp4_path)\n",
        "        N_src = max(1, int(src_st.get('video_frames') or 0))\n",
        "        src_fps = float(src_st.get('fps') or 24.0)\n",
        "        vid_dur = max(0.2, float(src_st.get('video_duration_sec') or (N_src / max(1.0, src_fps))))\n",
        "        is_native_24 = (23.95 <= src_fps <= 24.05)\n",
        "        norm_in_mp4 = mp4_path\n",
        f"        if {tfps} == 24 and is_native_24:\n",
        "            N = N_src; expected_frames = N_src; expected_dur = N_src / 24.0\n",
        f"        elif {tfps} == 24:\n",
        "            expected_frames = max(1, int(round(vid_dur * 24.0))); expected_dur = expected_frames / 24.0\n",
        "            norm24_mp4 = LIPSYNC_OUT_DIR / f'{seg_id}_norm24.mp4'\n",
        "            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(mp4_path), '-an', '-vf', 'fps=24', '-frames:v', str(expected_frames), '-c:v', 'libx264', '-crf', '10', '-preset', 'veryfast', '-profile:v', 'high', '-pix_fmt', 'yuv420p', str(norm24_mp4)], check=True)\n",
        "            norm_in_mp4 = norm24_mp4; N = expected_frames\n",
        "        else:\n",
        f"            N = N_src; expected_frames = max(1, int(round(vid_dur * {tfps}))); expected_dur = vid_dur\n",
        "        driver_wav = LIPSYNC_OUT_DIR / f'{seg_id}_lipsync_driver.wav'\n",
        "        in_args, f_parts, mix_tags = [], [], []\n",
        "        for i, ln in enumerate(lines):\n",
        "            in_args.extend(['-i', str(ln['wav'])])\n",
        "            ms = max(0, int(round(float(ln['start_in_seg_sec']) * 1000)))\n",
        "            f_parts.append(f'[{i}:a]adelay={ms}|{ms}[a{i}]'); mix_tags.append(f'[a{i}]')\n",
        f"        use_pullup = ({tfps} == 24) or (abs(src_fps - 25.0) > 0.05)\n",
        "        ls_in_mp4 = norm_in_mp4\n",
        "        if use_pullup:\n",
        "            pullup_mp4 = LIPSYNC_OUT_DIR / f'{seg_id}_pullup25.mp4'\n",
        "            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(norm_in_mp4), '-an', '-vf', 'settb=1/25,setpts=N/(25*TB)', *_relabel_fps_args(25), '-c:v', 'libx264', '-crf', '10', '-preset', 'veryfast', '-profile:v', 'high', '-pix_fmt', 'yuv420p', str(pullup_mp4)], check=True)\n",
        "            ls_in_mp4 = pullup_mp4\n",
        "            drv_dur_25 = (N + 1) / 25.0\n",
        f"            atempo_str = '25/24' if ({tfps} == 24 or abs(src_fps - 24.0) <= 0.15) else f'{{25.0 / max(1.0, src_fps):.6f}}'\n",
        f"            mix_dur = expected_dur if {tfps} == 24 else vid_dur\n",
        "            raw_mix_wav = LIPSYNC_OUT_DIR / f'{seg_id}_mix_orig.wav'\n",
        "            fc_str = ';'.join(f_parts) + ';' + ''.join(mix_tags) + f'amix=inputs={len(lines)}:duration=longest:dropout_transition=0:normalize=0,apad=whole_dur={mix_dur:.6f},atrim=0:{mix_dur:.6f}[drv]'\n",
        "            subprocess.run(['ffmpeg', '-y', '-v', 'error', *in_args, '-filter_complex', fc_str, '-map', '[drv]', '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(raw_mix_wav)], check=True)\n",
        "            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(raw_mix_wav), '-af', f'atempo={atempo_str},apad=whole_dur={drv_dur_25:.6f}', '-t', f'{drv_dur_25:.6f}', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(driver_wav)], check=True)\n",
        "        else:\n",
        "            fc_str = ';'.join(f_parts) + ';' + ''.join(mix_tags) + f'amix=inputs={len(lines)}:duration=longest:dropout_transition=0:normalize=0,apad=whole_dur={vid_dur:.3f},atrim=0:{vid_dur:.3f}[drv]'\n",
        "            subprocess.run(['ffmpeg', '-y', '-v', 'error', *in_args, '-filter_complex', fc_str, '-map', '[drv]', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(driver_wav)], check=True)\n",
        "        lipsync_vid = LIPSYNC_OUT_DIR / f'{seg_id}_latentsync_raw.mp4'\n",
        "        subprocess.run([_ls_py, '-m', 'scripts.inference', '--unet_config_path', str(unet_cfg), '--inference_ckpt_path', str(ckpt_pt), '--inference_steps', '20', '--guidance_scale', '1.5', '--video_path', str(ls_in_mp4), '--audio_path', str(driver_wav), '--video_out_path', str(lipsync_vid)], cwd=str(LATENTSYNC_REPO), env=_ls_env, check=True)\n",
        "        if not lipsync_vid.exists() or lipsync_vid.stat().st_size == 0:\n",
        "            raise RuntimeError(f'LatentSync 输出缺失或为空，严禁静默回退原片: {lipsync_vid}')\n",
        f"        if {tfps} == 24:\n",
        "            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(lipsync_vid), '-i', str(mp4_path), '-map', '0:v:0', '-map', '1:a:0?', '-vf', 'settb=1/24,setpts=N/(24*TB)', *_relabel_fps_args(24), '-c:v', 'libx264', '-crf', '10', '-preset', 'fast', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000', '-b:a', '256k', str(out_mp4)], check=True)\n",
        "        else:\n",
        f"            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(lipsync_vid), '-i', str(mp4_path), '-vf', 'fps={tfps}', '-t', f'{{vid_dur:.4f}}', '-c:v', 'libx264', '-crf', '10', '-preset', 'fast', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-map', '0:v:0', '-map', '1:a:0?', '-c:a', 'aac', '-ar', '48000', '-b:a', '256k', str(out_mp4)], check=True)\n",
        "        ls_chk = probe_stream(out_mp4)\n",
        f"        if not ({f_lo:.2f} <= ls_chk['fps'] <= {f_hi:.2f}):\n",
        f"            raise RuntimeError(f'Step 1 口型封装后帧率未落入 {f_lo:.2f}~{f_hi:.2f}fps: {{mp4_path.name}} -> {{ls_chk}}')\n",
        "        actual_frames = int(ls_chk.get('video_frames') or 0)\n",
        "        actual_dur = float(ls_chk.get('video_duration_sec') or ls_chk.get('duration_sec') or 0.0)\n",
        "        if abs(actual_frames - expected_frames) > 1 or abs(actual_dur - expected_dur) > 0.08:\n",
        "            missing_frames = max(0, expected_frames - actual_frames); excess_frames = max(0, actual_frames - expected_frames)\n",
        "            raise RuntimeError(f'Step 1 口型帧锁定双向核验失败: {mp4_path.name} missing_frames={missing_frames} excess_frames={excess_frames} (actual_frames={actual_frames}, expected_frames={expected_frames}, actual_dur={actual_dur:.4f}s, expected_dur={expected_dur:.4f}s)')\n",
        "        shutil.copy2(out_mp4, mp4_path)\n",
        "    print('✅ Step 1 多句延迟起音 LatentSync 1.6 (24<->25fps 无损帧锁 + 纯环境 SFX 封装) 完成！')\n",
    ]

    done_dir_name = v42_qc.done_vsr_dir_name(tfps, p4_profile=eff_prof)
    vsr_bp_src = (Path(__file__).resolve().parent / "vsr_pipeline" / "batch_processor.py").read_bytes()
    vsr_re_src = (Path(__file__).resolve().parent / "vsr_pipeline" / "realesrgan_engine.py").read_bytes()
    vsr_bp_b64 = base64.b64encode(vsr_bp_src).decode("ascii")
    vsr_re_b64 = base64.b64encode(vsr_re_src).decode("ascii")
    scdet_args = ", '--scdet'" if tfps == 60 else ""
    prof_arg = f", '--p4-profile', {eff_prof!r}" if eff_prof else ""
    ar_arg = f", '--aspect-ratio', {ar!r}"
    step2_hdr = f"# === Step 2：安装 spandrel + Real-ESRGAN FP16 流式 GPU 超分 + 云端 ffprobe {tw}x{th}@{tfps}fps ({ar}, {f_lo:.2f}~{f_hi:.2f}) 强复测与自动关机 ===\n"
    branch_lines = [
        f"        if w == {tw} and h == {th} and ({f_lo:.2f} <= fps <= {f_hi:.2f}) and ({eff_prof!r} != 'anime_1k' or seg_preset != 'anime'):\n",
        "            shutil.copy2(mp4, stg_out)\n",
        f"            bp_rep = {{'backend_used': 'passthrough_{res_tag.lower()}', 'model_weight': 'none', 'vsr_preset': seg_preset}}\n",
        f"        elif w >= {tw} and h >= {th} and ({eff_prof!r} == 'live_1k' or {res_tag!r} == '4K'):\n",
        f"            r_bp = subprocess.run([sys.executable, '-m', 'vsr_pipeline.batch_processor', '--backend', 'ffmpeg_lanczos', '--input_file', str(mp4), '--output_file', str(stg_out), '--target-width', '{tw}', '--target-height', '{th}'{prof_arg}{ar_arg}, '--target_fps', '{tfps}'{scdet_args}, '--audio_codec', 'copy'], capture_output=True, text=True, env=_vsr_env, check=True)\n",
        "            bp_rep = json.loads(r_bp.stdout[r_bp.stdout.find('{'):]) if '{' in r_bp.stdout else {'backend_used': 'ffmpeg_lanczos', 'model_weight': 'none'}\n",
        "        else:\n",
        f"            r_bp = subprocess.run([sys.executable, '-m', 'vsr_pipeline.batch_processor', '--backend', 'realesrgan', '--vsr-preset', seg_preset, '--input_file', str(mp4), '--output_file', str(stg_out), '--target-width', '{tw}', '--target-height', '{th}'{prof_arg}{ar_arg}, '--target_fps', '{tfps}'{scdet_args}, '--audio_codec', 'aac'], capture_output=True, text=True, env=_vsr_env, check=True)\n",
        "            bp_rep = json.loads(r_bp.stdout[r_bp.stdout.find('{'):]) if '{' in r_bp.stdout else {'backend_used': 'realesrgan', 'model_weight': ''}\n",
    ]

    cell_vsr_code = [
        step2_hdr,
        "import base64, importlib.util\n",
        "if importlib.util.find_spec('spandrel') is None:\n",
        "    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'spandrel'], check=True)\n",
        f"_EMBEDDED_VSR_BP_B64 = {vsr_bp_b64!r}\n",
        f"_EMBEDDED_VSR_RE_B64 = {vsr_re_b64!r}\n",
        "def ensure_vsr_pipeline_env() -> dict:\n",
        "    env = os.environ.copy()\n",
        "    try:\n",
        "        _has_vsr = all(importlib.util.find_spec(m) is not None for m in ('vsr_pipeline', 'vsr_pipeline.batch_processor', 'vsr_pipeline.realesrgan_engine'))\n",
        "    except Exception:\n",
        "        _has_vsr = False\n",
        "    if _has_vsr:\n",
        "        return env\n",
        "    search_roots = [PROJECT_ROOT, PROJECT_ROOT / 'tools', PROJECT_ROOT / 'scripts', Path('/content/drive/MyDrive/短剧复刻工作站/tools'), Path('/content/drive/MyDrive/短剧复刻工作站/scripts')]\n",
        "    found_root = next((r for r in search_roots if (r / 'vsr_pipeline' / 'batch_processor.py').is_file() and (r / 'vsr_pipeline' / 'realesrgan_engine.py').is_file()), None)\n",
        "    if found_root is None:\n",
        "        found_root = WORK_DIR / '_runtime_modules'\n",
        "        pkg_dir = found_root / 'vsr_pipeline'\n",
        "        pkg_dir.mkdir(parents=True, exist_ok=True)\n",
        "        (pkg_dir / '__init__.py').write_text('# -*- coding: utf-8 -*-\\n__all__ = [\"batch_processor\", \"realesrgan_engine\"]\\n', encoding='utf-8')\n",
        "        (pkg_dir / 'batch_processor.py').write_bytes(base64.b64decode(_EMBEDDED_VSR_BP_B64))\n",
        "        (pkg_dir / 'realesrgan_engine.py').write_bytes(base64.b64decode(_EMBEDDED_VSR_RE_B64))\n",
        "    mod_str = str(found_root.resolve())\n",
        "    if mod_str not in sys.path: sys.path.insert(0, mod_str)\n",
        "    prev_pp = env.get('PYTHONPATH', '')\n",
        "    env['PYTHONPATH'] = (mod_str + os.pathsep + prev_pp) if prev_pp else mod_str\n",
        "    os.environ['PYTHONPATH'] = env['PYTHONPATH']\n",
        "    return env\n\n",
        "_vsr_env = ensure_vsr_pipeline_env()\n",
        f"ASPECT_RATIO = {ar!r}\n",
        f"TARGET_WIDTH = {tw}\n",
        f"TARGET_HEIGHT = {th}\n",
        f"TARGET_FPS = {tfps}\n",
        f"OUT_4K_DIR = WORK_DIR / '{done_dir_name}'\n",
        f"STAGING_4K_DIR = WORK_DIR / '_vsr_staging_4k{tfps}'\n",
        "for _d in (OUT_4K_DIR, STAGING_4K_DIR): _d.mkdir(parents=True, exist_ok=True)\n",
        "_preset_map, _default_preset = {}, 'auto'\n",
        "for _tp in (WORK_DIR / 'phase4_tasks.json', WORK_DIR / 'Colab_A100_超分任务清单.json'):\n",
        "    if _tp.is_file():\n",
        "        _td = json.loads(_tp.read_text(encoding='utf-8-sig'))\n",
        "        if isinstance(_td, dict):\n",
        "            if _td.get('vsr_preset') in ('live', 'anime'): _default_preset = _td['vsr_preset']\n",
        "            for _k in ('tasks', 'clips', 'segments', 'queued_vsr_clips'):\n",
        "                _its = _td.get(_k)\n",
        "                if isinstance(_its, dict):\n",
        "                    for _sk, _sv in _its.items():\n",
        "                        if isinstance(_sv, dict) and _sv.get('vsr_preset'): _preset_map[_sk] = str(_sv['vsr_preset'])\n",
        "                elif isinstance(_its, list):\n",
        "                    for _it in _its:\n",
        "                        if isinstance(_it, dict) and _it.get('vsr_preset'):\n",
        "                            for _ik in (_it.get('file'), _it.get('segment')):\n",
        "                                if _ik: _preset_map[str(_ik)] = str(_it['vsr_preset'])\n",
        "AUTO_SHUTDOWN = True\n",
        "verified_done, backends_seen = [], set()\n",
        "try:\n",
        "    for mp4 in sorted(TO_VSR_DIR.glob('segment_*.mp4')):\n",
        "        st = probe_stream(mp4)\n",
        "        w, h, fps = st['width'], st['height'], st['fps']\n",
        "        seg_k = parse_seg_shot(mp4.stem)[0]\n",
        "        seg_preset = _preset_map.get(mp4.name) or _preset_map.get(seg_k) or _default_preset\n",
        "        stg_out = STAGING_4K_DIR / mp4.name\n",
        *branch_lines,
        "        chk = probe_stream(stg_out)\n",
        f"        if chk['width'] != {tw} or chk['height'] != {th} or not ({f_lo:.2f} <= chk['fps'] <= {f_hi:.2f}):\n",
        f"            raise RuntimeError(f\"云端复测未达 {tw}x{th}@{f_lo:.2f}~{f_hi:.2f}fps 硬指标: {{mp4.name}} -> {{chk}}\")\n",
        "        shutil.move(str(stg_out), str(OUT_4K_DIR / mp4.name))\n",
        "        b_used = str(bp_rep.get('backend_used') or 'realesrgan')\n",
        "        m_wgt = str(bp_rep.get('model_weight') or 'none')\n",
        "        backends_seen.add(b_used)\n",
        "        verified_done.append({'file': mp4.name, 'vsr_preset': seg_preset, 'backend_used': b_used, 'model_weight': m_wgt, **chk})\n",
        "    top_backend = 'realesrgan' if 'realesrgan' in backends_seen else (sorted(backends_seen)[0] if backends_seen else 'realesrgan')\n",
        f"    (WORK_DIR / 'phase4_colab_vsr_done.json').write_text(json.dumps({{'episode': EPISODE, 'p4_profile': {prof_spec['p4_profile']!r}, 'aspect_ratio': ASPECT_RATIO, 'target_resolution': '{tw}x{th}', 'target_fps': TARGET_FPS, 'backend_used': top_backend, 'colab_a100_verified': True, 'clips': verified_done}}, ensure_ascii=False, indent=2), encoding='utf-8')\n",
        "finally:\n",
        "    if AUTO_SHUTDOWN:\n",
        "        for fn_clean in ('sync',):\n",
        "            try: getattr(os, fn_clean)()\n",
        "            except Exception: pass\n",
        "        try:\n",
        "            from google.colab import drive; drive.flush_and_unmount()\n",
        "        except Exception: pass\n",
        "        try:\n",
        "            from google.colab import runtime; runtime.unassign()\n",
        "        except Exception: pass\n",
    ]
    return _save_phase4_ipynb_json(
        ep_s, ep_dir, work_dir, cell_lipsync_head + cell_lipsync_tail, cell_vsr_code,
        tfps, p4_profile=eff_prof, aspect_ratio=ar,
    )


def _save_phase4_ipynb_json(
    ep_s: str, ep_dir: Path, work_dir: Path,
    cell_lipsync_source: list[str], cell_vsr_source: list[str],
    target_fps: int = v42_qc.DEFAULT_TARGET_FPS,
    p4_profile: str | None = None,
    aspect_ratio: str = "16:9",
) -> Path:
    tfps = v42_qc.normalize_target_fps(target_fps)
    f_lo, f_hi = v42_qc.fps_bounds(tfps)
    ar = v42_qc.normalize_aspect_ratio(aspect_ratio)
    prof_spec = v42_qc.get_p4_profile_spec(p4_profile, aspect_ratio=ar)
    tw, th, res_tag = int(prof_spec["width"]), int(prof_spec["height"]), str(prof_spec["res_tag"])
    done_dir_name = v42_qc.done_vsr_dir_name(tfps, p4_profile=p4_profile)
    step2_desc = (
        f"- **Step 2（`spandrel` Real-ESRGAN FP16 流式 GPU 超分 + `{tfps}fps` 帧率规范化 + 云端 `ffprobe` 强复测）**：档位 `{prof_spec['p4_profile'] or 'default_4k'}`；每段实测 `{tw}x{th} & {f_lo:.2f} <= fps <= {f_hi:.2f}` 达标才移入 `{done_dir_name}/`，写入真实 `backend_used` 与 `model_weight` 后自动刷盘关机\n"
    )
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
                    f"# 🎬 {ep_s} · V4.6 Colab A100 多句延迟起音 `LatentSync 1.6`（`24↔25fps` 无损帧锁）+ Real-ESRGAN {res_tag} {tfps}fps 超分流水线\n",
                    "- **Step 1（`24↔25fps` 无损 Pull-up/Pull-down 帧锁定 + 多句对白 `adelay` 口型驱动 + 纯环境 SFX 封装）**：推理前无损改标 `25fps`，口型重绘后截取精确 `N` 帧无损改标回 `24fps` 并封装原版未变速纯 SFX 音轨\n",
                    step2_desc,
                ],
            },
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": cell_lipsync_source},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": cell_vsr_source},
        ],
    }
    nb_ep_path = ep_dir / f"{ep_s}_Colab_A100_{res_tag}{tfps}fps_超分流水线.ipynb"
    nb_work_path = work_dir / f"{ep_s}_Colab_A100_{res_tag}{tfps}fps_超分流水线.ipynb"
    nb_txt = json.dumps(nb_data, ensure_ascii=False, indent=2)
    nb_ep_path.write_text(nb_txt, encoding="utf-8")
    nb_work_path.write_text(nb_txt, encoding="utf-8")
    return nb_ep_path


def _locate_voice_ref_file(ep_voice_dir: Path, root_voice_dir: Path, ref_name: str, speaker: str, voice_mode: str) -> Path | None:
    if ref_name:
        for base_d in (ep_voice_dir, root_voice_dir):
            cand = base_d / ref_name
            if cand.exists() and cand.is_file():
                return cand
    for base_d in (ep_voice_dir, root_voice_dir):
        if not base_d.exists():
            continue
        for wp in sorted(base_d.glob("*.wav")):
            if speaker in wp.stem and voice_mode in wp.stem:
                return wp
    return None


def verify_voice_refs_for_dialogue(project_root: Path, ep_s: str) -> list[dict[str, Any]]:
    ep_dir = project_root / "分集" / ep_s
    dlg_csv = ep_dir / f"{ep_s}_台词生成表.csv"
    if not dlg_csv.exists() or dlg_csv.stat().st_size == 0:
        err = {
            "status": "VOICE_COLAB_MISSING_DIALOGUE_TABLE",
            "episode": ep_s,
            "reason": f"未找到非空台词生成表: {dlg_csv.name}",
        }
        print(json.dumps(err, ensure_ascii=False, indent=2))
        sys.exit(2)

    cspec = load_cuts_spec(project_root, ep_s)
    schedule = resolve_dialogue_schedule(project_root, ep_s, cuts_spec=cspec)
    active_lines = [r for r in schedule if not r.get("skip_jimeng")]
    _, expected_roles = _extract_expected_roles(project_root, ep_s)
    req_items = [
        {
            "speaker": r["speaker"],
            "voice_mode": r["voice_mode"],
            "ref_name": r.get("ref_name", ""),
            "shot_tag": f"{r['segment']}_{r['shot']}",
        }
        for r in active_lines
    ]
    v_res = v42_qc.verify_voice_refs_48k_approved_v42(
        project_root, ep_s, req_items, expected_roles, probe_wav_strict, _locate_voice_ref_file, check_all_ep_wavs=False
    )
    if not v_res["ok"]:
        all_shots = sorted({s for it in v_res["issues"] for s in it.get("affected_shots", [])})
        err_doc = {
            "status": "VOICE_REF_NOT_APPROVED_OR_NOT_48K",
            "error_code": "VOICE_REF_NOT_APPROVED_OR_NOT_48K",
            "episode": ep_s,
            "approved_manifest_path": v_res["approved_manifest_path"],
            "missing_or_invalid_voice_refs": v_res["issues"],
            "affected_dialogue_shots": all_shots,
        }
        print(json.dumps(err_doc, ensure_ascii=False, indent=2))
        sys.exit(2)
    return active_lines



def _resolve_voice_engine_and_gpu(stage: str, engine: str = "auto", gpu: str = "") -> tuple[str, str]:
    st = (stage or "both").strip().lower()
    eng = (engine or "auto").strip().lower()
    if eng == "auto":
        eng = "indextts25" if st == "dialogue" else "voxcpm2"
    if eng not in ("voxcpm2", "indextts25", "fish_s2_pro", "cosyvoice2"):
        raise ValueError(f"unsupported tts engine: {eng}")
    if st in ("voice-ref", "both") and eng in ("indextts25", "fish_s2_pro"):
        raise ValueError(
            f"tts_engine={eng} 仅用于 --stage dialogue（基于第二棒 VoxCPM 2 批准母本配音）；"
            f"--stage {st} 请使用 voxcpm2"
        )
    default_gpu = "T4" if eng == "cosyvoice2" else "L4"
    gpu_norm = (gpu or default_gpu).strip().upper()
    return eng, gpu_norm


def _colab_drive_header_code(title_comment: str, stage: str, proj_name: str, rel_hint: str, ep_s: str, extra_imports: str = "") -> list[str]:
    lines = [
        f"# === Step 0：{title_comment} ===\n",
        "import csv, hashlib, json, os, re, shutil, subprocess, sys\n",
        "from pathlib import Path\n",
    ]
    if extra_imports:
        lines.append(f"{extra_imports}\n")
    lines.extend([
        "\n",
        f'STAGE = "{stage}"\n',
        "AUTO_SHUTDOWN = True\n",
        f'PROJECT_NAME = "{proj_name}"\n',
        f'REL_FROM_DRIVE = "{rel_hint}"\n',
        f'EPISODE = "{ep_s}"\n\n',
        "def resolve_colab_project_root(project_name: str, rel_hint: str) -> Path:\n",
        "    drive_my = Path('/content/drive/MyDrive')\n",
        "    for cand in [drive_my / rel_hint, drive_my / '短剧复刻工作站' / '01_在制项目库' / project_name, drive_my / project_name]:\n",
        "        if cand.exists(): return cand.resolve()\n",
        "    matches = list(drive_my.glob(f'*/*/{project_name}')) + list(drive_my.glob(f'*/{project_name}'))\n",
        "    if matches: return matches[0].resolve()\n",
        "    raise FileNotFoundError(f'云盘未找到项目目录: {project_name} ({rel_hint})')\n\n",
        "from google.colab import drive\n",
        "drive.mount('/content/drive')\n",
        "PROJECT_ROOT = resolve_colab_project_root(PROJECT_NAME, REL_FROM_DRIVE)\n",
        "TTS_STUDIO_ROOT = Path('/content/drive/MyDrive/tts_studio')\n",
        "MODELS_DIR = TTS_STUDIO_ROOT / 'models'\n",
        "HF_CACHE = TTS_STUDIO_ROOT / 'hf_cache'\n",
        "for _d in (MODELS_DIR, HF_CACHE): _d.mkdir(parents=True, exist_ok=True)\n",
        "os.environ['HF_HOME'] = str(HF_CACHE)\n",
    ])
    return lines


def _voxcpm2_setup_code(stage: str, proj_name: str, rel_hint: str, ep_s: str) -> list[str]:
    """VoxCPM 2 (openbmb/VoxCPM2, 2B, 原生 48kHz, L4 24GB bf16) Step 0：挂载云盘 + 加载模型 + 原生 48kHz 工具函数。"""
    return _colab_drive_header_code(
        "挂载 Google Drive + 加载 openbmb/VoxCPM2（原生 48kHz，默认 --gpu L4）",
        stage, proj_name, rel_hint, ep_s,
        extra_imports="import numpy as np\nimport torch, torchaudio",
    ) + [
        "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'voxcpm', 'soundfile'], check=True)\n",
        "import soundfile as sf\n",
        "from voxcpm import VoxCPM\n",
        "vox_model = VoxCPM.from_pretrained('openbmb/VoxCPM2', load_denoiser=False)\n",
        "VOX_SR = int(vox_model.tts_model.sample_rate)\n",
        "if VOX_SR != 48000: raise RuntimeError(f'VOXCPM2_NOT_NATIVE_48K: sample_rate={VOX_SR}（严禁外部重采样冒充 48kHz）')\n\n",
        "def vox_gen(text: str, ref_wav=None, seed: int = 0):\n",
        "    # Voice Design：text 以 (中文自然语言声线描述) 开头且不传参考；Controllable Cloning：传 reference_wav_path + (情绪/语速括号指令)\n",
        "    torch.manual_seed(int(seed)); np.random.seed(int(seed) % (2**32 - 1))\n",
        "    kw = dict(text=text, cfg_value=2.0, inference_timesteps=10)\n",
        "    if ref_wav is not None: kw['reference_wav_path'] = str(ref_wav)\n",
        "    wav = vox_model.generate(**kw)\n",
        "    return np.asarray(wav, dtype=np.float32).reshape(-1)\n\n",
        "def vox_trim_fade(wav, thr_db: float = -45.0, keep_sec: float = 0.04, fade_sec: float = 0.01):\n",
        "    # 仅首尾轻量静音裁剪 + 10ms 淡入淡出（严禁 atempo 变速/重采样）\n",
        "    idx = np.where(np.abs(wav) > 10 ** (thr_db / 20.0))[0]\n",
        "    if idx.size == 0: return wav[:0]\n",
        "    a, b = max(0, int(idx[0]) - int(keep_sec * VOX_SR)), min(len(wav), int(idx[-1]) + int(keep_sec * VOX_SR))\n",
        "    out = wav[a:b].copy(); n = int(fade_sec * VOX_SR)\n",
        "    if len(out) > 2 * n and n > 0:\n",
        "        out[:n] *= np.linspace(0.0, 1.0, n, dtype=np.float32); out[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)\n",
        "    return out\n\n",
        "def vox_silence_ratio(wav, thr_db: float = -45.0, frame_sec: float = 0.02) -> float:\n",
        "    fl = max(1, int(frame_sec * VOX_SR)); nfr = len(wav) // fl\n",
        "    if nfr <= 0: return 1.0\n",
        "    rms = np.sqrt(np.mean(np.square(wav[:nfr * fl].reshape(nfr, fl)), axis=1) + 1e-12)\n",
        "    return float(np.mean(20 * np.log10(rms + 1e-12) < thr_db))\n\n",
        "def vox_write_pcm16(dst: Path, wav) -> None:\n",
        "    dst.parent.mkdir(parents=True, exist_ok=True)\n",
        "    sf.write(str(dst), np.clip(wav, -1.0, 1.0), VOX_SR, subtype='PCM_16')\n\n",
        "print(f'✅ VoxCPM 2 加载就绪 (native {VOX_SR}Hz): PROJECT_ROOT={PROJECT_ROOT}')\n",
    ]


def _indextts25_setup_code(stage: str, proj_name: str, rel_hint: str, ep_s: str) -> list[str]:
    """IndexTTS-2.5 (IndexTeam/IndexTTS-2.5) Step 0：独立 uv 虚拟环境 + Google Drive 权重持久化。"""
    return _colab_drive_header_code(
        "挂载 Google Drive + 独立 uv 隔离加载 IndexTTS-2.5（情绪解耦与精准卡时长主力）",
        stage, proj_name, rel_hint, ep_s,
    ) + [
        "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'uv', 'huggingface_hub'], check=True)\n",
        "from huggingface_hub import snapshot_download\n",
        "INDEX_REPO = Path('/content/index-tts')\n",
        "INDEX_CKPT = MODELS_DIR / 'IndexTTS-2.5'\n",
        "if not INDEX_REPO.exists():\n",
        "    subprocess.run(['git', 'lfs', 'install'], check=True)\n",
        "    subprocess.run(['git', 'clone', 'https://github.com/index-tts/index-tts.git', str(INDEX_REPO)], check=True)\n",
        "    subprocess.run(['git', 'lfs', 'pull'], cwd=str(INDEX_REPO), check=True)\n",
        "    subprocess.run(['uv', 'sync', '--all-extras'], cwd=str(INDEX_REPO), check=True)\n",
        "if not (INDEX_CKPT / 'config.yaml').exists():\n",
        "    snapshot_download('IndexTeam/IndexTTS-2.5', local_dir=str(INDEX_CKPT))\n",
        "print(f'✅ IndexTTS-2.5 就绪 (权重持久化于 Drive): PROJECT_ROOT={PROJECT_ROOT}, CKPT={INDEX_CKPT}')\n",
    ]


def _fish_s2_pro_setup_code(stage: str, proj_name: str, rel_hint: str, ep_s: str) -> list[str]:
    """Fish Audio S2 Pro (fishaudio/s2-pro) Step 0：独立 uv 虚拟环境 + Secrets HF_TOKEN + Drive 权重持久化。"""
    return _colab_drive_header_code(
        "挂载 Google Drive + 独立 uv 隔离加载 Fish Audio S2 Pro（拟人度与自然长旁白天花板）",
        stage, proj_name, rel_hint, ep_s,
    ) + [
        "subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'uv', 'huggingface_hub'], check=True)\n",
        "from huggingface_hub import snapshot_download\n",
        "try:\n",
        "    from google.colab import userdata\n",
        "    _hf_tok = userdata.get('HF_TOKEN')\n",
        "    if _hf_tok: os.environ['HF_TOKEN'] = _hf_tok\n",
        "except Exception:\n",
        "    pass\n",
        "FISH_REPO = Path('/content/fish-speech')\n",
        "FISH_VENV = Path('/content/envs/fish')\n",
        "FISH_CKPT = MODELS_DIR / 'fish-s2-pro'\n",
        "if not FISH_REPO.exists():\n",
        "    subprocess.run(['git', 'clone', 'https://github.com/fishaudio/fish-speech.git', str(FISH_REPO)], check=True)\n",
        "    subprocess.run(['uv', 'venv', str(FISH_VENV), '--python', '3.11'], check=True)\n",
        "    subprocess.run(['uv', 'pip', 'install', '--python', str(FISH_VENV / 'bin' / 'python'), '-e', str(FISH_REPO)], check=True)\n",
        "if not (FISH_CKPT / 'config.json').exists():\n",
        "    if not os.environ.get('HF_TOKEN'): raise RuntimeError('MISSING_HF_TOKEN: 请先在 Colab 左侧 Secrets 配置 HF_TOKEN 并同意 fishaudio/s2-pro 协议')\n",
        "    snapshot_download('fishaudio/s2-pro', local_dir=str(FISH_CKPT), token=os.environ.get('HF_TOKEN'))\n",
        "print(f'✅ Fish Audio S2 Pro 就绪 (权重持久化于 Drive): PROJECT_ROOT={PROJECT_ROOT}, CKPT={FISH_CKPT}')\n",
    ]


_VOXCPM2_VOICE_REF_BLOCK = [
    "        gen_records, sha_map_out = [], {}\n",
    "        for idx_c, ch in enumerate(chars, 1):\n",
    "            rid = str(ch.get('role_id') or ch.get('id') or f'角色{idx_c:02d}').strip()\n",
    "            rname = str(ch.get('new_name') or ch.get('name') or ch.get('display_name') or f'角色{idx_c:02d}').strip()\n",
    "            timbre = str(ch.get('voice_style') or ch.get('timbre') or '自然清晰影视剧声线').strip()\n",
    "            for mode_lbl, txt_s, ctrl_s in (\n",
    "                ('说话', ch.get('speak_sample_text') or '这件事到此为止，谁也别再提了。', f'{timbre}，自然坚定的说话语气，干声无混响'),\n",
    "                ('心声', ch.get('os_sample_text') or '原来从一开始，他就已经布好了局。', f'{timbre}，压低嗓音的内心独白，低沉内敛，干声无混响'),\n",
    "            ):\n",
    "                out_name = f'{rid}_{rname}_{mode_lbl}_声线参考.wav'\n",
    "                seed0 = int(hashlib.sha256(f'{rid}|{rname}|{mode_lbl}'.encode('utf-8')).hexdigest()[:6], 16)\n",
    "                best_w = None\n",
    "                for attempt in range(10):\n",
    "                    w_try = vox_trim_fade(vox_gen(f'({ctrl_s}){txt_s}', None, seed0 + attempt * 101))\n",
    "                    d_try = len(w_try) / VOX_SR\n",
    "                    if 2.05 <= d_try <= 2.95 and vox_silence_ratio(w_try) <= 0.35:\n",
    "                        best_w = w_try; break\n",
    "                if best_w is None: raise RuntimeError(f'VOICE_REF_NATURAL_DURATION_RETRY_EXHAUSTED: {out_name} 10 次重试仍未得到 2.05–2.95s 原生自然时长（请微调样句字数）')\n",
    "                dst_p = voice_ep_dir / out_name\n",
    "                vox_write_pcm16(dst_p, best_w)\n",
    "                v_meta = probe_wav_meta(dst_p)\n",
    "                shutil.copy2(dst_p, voice_root_dir / out_name)\n",
    "                sha_map_out[out_name] = v_meta['sha256']\n",
    "                gen_records.append({'role_id': rid, 'speaker': rname, 'voice_mode': mode_lbl, 'file': out_name, 'control_prompt': ctrl_s, 'text': txt_s, 'seed': seed0, **v_meta})\n",
]

_VOXCPM2_DIALOGUE_BLOCK = [
    "            inst_v = re.sub(r'[()（）]', '', tk['inst']).strip()\n",
    "            seed_d = int(hashlib.sha256(f\"{tk['segment']}|{tk['shot']}|{tk['out_fname']}\".encode('utf-8')).hexdigest()[:6], 16)\n",
    "            w_ok = None\n",
    "            for attempt in range(4):\n",
    "                ctrl_d = inst_v if attempt == 0 else f'{inst_v}，语速稍快'\n",
    "                w_try = vox_trim_fade(vox_gen(f\"({ctrl_d}){tk['txt']}\", tk['ref_p'], seed_d + attempt * 101))\n",
    "                w_ok = w_try\n",
    "                if len(w_try) / VOX_SR <= max(0.5, float(tk['budget_sec'])) * 1.12: break\n",
    "            dst_w = dlg_dir / tk['out_fname']\n",
    "            d_meta, atempo_v = save_dialogue_fit_budget(torch.from_numpy(w_ok).float().unsqueeze(0), VOX_SR, dst_w, tk['budget_sec'], f\"{tk['segment']}_{tk['shot']}\")\n",
]


_INDEXTTS25_DIALOGUE_EXEC_BLOCK = [
    "        idx_jobs = [{'ref_p': str(tk['ref_p']), 'txt': tk['txt'], 'inst': tk['inst'], 'raw_wav': f\"/tmp/_idx25_{i:04d}.wav\"} for i, tk in enumerate(active_tasks)]\n",
    "        Path('/tmp/indextts25_jobs.json').write_text(json.dumps({'ckpt': str(INDEX_CKPT), 'jobs': idx_jobs}, ensure_ascii=False), encoding='utf-8')\n",
    "        idx_worker = (\n",
    "            'import inspect, json, sys\\n'\n",
    "            'from pathlib import Path\\n'\n",
    "            'try:\\n'\n",
    "            '    from indextts.infer_v2_5 import IndexTTS2\\n'\n",
    "            'except ImportError:\\n'\n",
    "            '    from indextts.infer_v2 import IndexTTS2\\n'\n",
    "            'cfg = json.loads(Path(sys.argv[1]).read_text(encoding=\"utf-8\"))\\n'\n",
    "            'ckpt = cfg[\"ckpt\"]\\n'\n",
    "            'init_p = inspect.signature(IndexTTS2.__init__).parameters\\n'\n",
    "            'init_kw = {\"cfg_path\": f\"{ckpt}/config.yaml\", \"model_dir\": ckpt}\\n'\n",
    "            'if \"use_bf16\" in init_p: init_kw[\"use_bf16\"] = True\\n'\n",
    "            'elif \"use_fp16\" in init_p: init_kw[\"use_fp16\"] = False\\n'\n",
    "            'tts = IndexTTS2(**init_kw)\\n'\n",
    "            'inf_p = inspect.signature(tts.infer).parameters\\n'\n",
    "            'for j in cfg[\"jobs\"]:\\n'\n",
    "            '    kw = {\"spk_audio_prompt\": j[\"ref_p\"], \"text\": j[\"txt\"], \"output_path\": j[\"raw_wav\"]}\\n'\n",
    "            '    if \"use_emo_text\" in inf_p: kw[\"use_emo_text\"] = True\\n'\n",
    "            '    if \"emo_text\" in inf_p: kw[\"emo_text\"] = j[\"inst\"]\\n'\n",
    "            '    if \"emo_alpha\" in inf_p: kw[\"emo_alpha\"] = 0.6\\n'\n",
    "            '    if \"use_random\" in inf_p: kw[\"use_random\"] = False\\n'\n",
    "            '    tts.infer(**kw)\\n'\n",
    "        )\n",
    "        Path('/tmp/run_indextts25_batch.py').write_text(idx_worker, encoding='utf-8')\n",
    "        subprocess.run(['uv', 'run', 'python', '/tmp/run_indextts25_batch.py', '/tmp/indextts25_jobs.json'], cwd=str(INDEX_REPO), check=True)\n",
    "        records = []\n",
    "        for tk, jb in zip(active_tasks, idx_jobs):\n",
    "            dst_w = dlg_dir / tk['out_fname']\n",
    "            d_meta, atempo_v = save_dialogue_fit_budget_from_wav(Path(jb['raw_wav']), dst_w, tk['budget_sec'], f\"{tk['segment']}_{tk['shot']}\")\n",
    "            records.append({'segment': tk['segment'], 'shot': tk['shot'], 'unit_key': f\"{tk['segment']}_{tk['shot']}\", 'speaker': tk['speaker'], 'character': tk['speaker'], 'voice_mode': tk['voice_mode'], 'start_in_seg_sec': tk['start_in_seg_sec'], 'end_in_seg_sec': tk['end_in_seg_sec'], 'target_duration_sec': tk['budget_sec'], 'actual_duration_sec': d_meta['duration_sec'], 'applied_atempo': atempo_v, 'file_name': tk['out_fname'], 'wav_path': str(dst_w), 'needs_lipsync': (tk['voice_mode'] == '说话'), 'locked_voice_ref_wav': str(tk['ref_p']), 'locked_voice_ref_sha256': tk['ref_sha'], 'sha256': d_meta['sha256'], 'spec_ok': True})\n",
    "        (dlg_dir / '台词音频清单.json').write_text(json.dumps({'episode': EPISODE, 'colab_indextts25_verified': True, 'tts_engine': 'indextts25', 'model': 'IndexTeam/IndexTTS-2.5', 'dialogue_records': records}, ensure_ascii=False, indent=2), encoding='utf-8')\n",
]

_FISH_S2_PRO_DIALOGUE_EXEC_BLOCK = [
    "        vref_txt_map = {}\n",
    "        _vlist_p = voice_ep_dir / '声线母本清单.json'\n",
    "        if _vlist_p.exists():\n",
    "            for _vr in (json.loads(_vlist_p.read_text(encoding='utf-8-sig')).get('records') or []):\n",
    "                if isinstance(_vr, dict) and _vr.get('file') and _vr.get('text'): vref_txt_map[str(_vr['file'])] = str(_vr['text'])\n",
    "        fish_jobs = [{'ref_p': str(tk['ref_p']), 'ref_txt': vref_txt_map.get(Path(tk['ref_p']).name, '这件事到此为止，谁也别再提了。'), 'txt': tk['txt'], 'raw_wav': f\"/tmp/_fishs2_{i:04d}.wav\"} for i, tk in enumerate(active_tasks)]\n",
    "        Path('/tmp/fish_s2_jobs.json').write_text(json.dumps({'ckpt': str(FISH_CKPT), 'jobs': fish_jobs}, ensure_ascii=False), encoding='utf-8')\n",
    "        fish_worker = (\n",
    "            'import json, subprocess, sys, time, urllib.request\\n'\n",
    "            'from pathlib import Path\\n'\n",
    "            'import ormsgpack\\n'\n",
    "            'from fish_speech.utils.schema import ServeReferenceAudio, ServeTTSRequest\\n'\n",
    "            'cfg = json.loads(Path(sys.argv[1]).read_text(encoding=\"utf-8\"))\\n'\n",
    "            'ckpt = Path(cfg[\"ckpt\"])\\n'\n",
    "            'srv = subprocess.Popen([sys.executable, \"tools/api_server.py\", \"--listen\", \"127.0.0.1:8080\", \"--llama-checkpoint-path\", str(ckpt), \"--decoder-checkpoint-path\", str(ckpt / \"codec.pth\"), \"--compile\"])\\n'\n",
    "            'try:\\n'\n",
    "            '    ready = False\\n'\n",
    "            '    for _ in range(180):\\n'\n",
    "            '        try:\\n'\n",
    "            '            with urllib.request.urlopen(\"http://127.0.0.1:8080/v1/health\", timeout=2) as r:\\n'\n",
    "            '                if r.status == 200: ready = True; break\\n'\n",
    "            '        except Exception: time.sleep(2)\\n'\n",
    "            '    if not ready: raise RuntimeError(\"FISH_S2_PRO_SERVER_TIMEOUT\")\\n'\n",
    "            '    for j in cfg[\"jobs\"]:\\n'\n",
    "            '        req_obj = ServeTTSRequest(text=j[\"txt\"], references=[ServeReferenceAudio(audio=Path(j[\"ref_p\"]).read_bytes(), text=j[\"ref_txt\"])], format=\"wav\")\\n'\n",
    "            '        payload = ormsgpack.packb(req_obj.model_dump())\\n'\n",
    "            '        req = urllib.request.Request(\"http://127.0.0.1:8080/v1/tts\", data=payload, headers={\"Content-Type\": \"application/msgpack\"})\\n'\n",
    "            '        with urllib.request.urlopen(req, timeout=300) as resp:\\n'\n",
    "            '            Path(j[\"raw_wav\"]).write_bytes(resp.read())\\n'\n",
    "            'finally:\\n'\n",
    "            '    srv.terminate()\\n'\n",
    "            '    try: srv.wait(timeout=10)\\n'\n",
    "            '    except Exception: srv.kill()\\n'\n",
    "        )\n",
    "        Path('/tmp/run_fish_s2_batch.py').write_text(fish_worker, encoding='utf-8')\n",
    "        subprocess.run([str(FISH_VENV / 'bin' / 'python'), '/tmp/run_fish_s2_batch.py', '/tmp/fish_s2_jobs.json'], cwd=str(FISH_REPO), check=True)\n",
    "        records = []\n",
    "        for tk, jb in zip(active_tasks, fish_jobs):\n",
    "            dst_w = dlg_dir / tk['out_fname']\n",
    "            d_meta, atempo_v = save_dialogue_fit_budget_from_wav(Path(jb['raw_wav']), dst_w, tk['budget_sec'], f\"{tk['segment']}_{tk['shot']}\")\n",
    "            records.append({'segment': tk['segment'], 'shot': tk['shot'], 'unit_key': f\"{tk['segment']}_{tk['shot']}\", 'speaker': tk['speaker'], 'character': tk['speaker'], 'voice_mode': tk['voice_mode'], 'start_in_seg_sec': tk['start_in_seg_sec'], 'end_in_seg_sec': tk['end_in_seg_sec'], 'target_duration_sec': tk['budget_sec'], 'actual_duration_sec': d_meta['duration_sec'], 'applied_atempo': atempo_v, 'file_name': tk['out_fname'], 'wav_path': str(dst_w), 'needs_lipsync': (tk['voice_mode'] == '说话'), 'locked_voice_ref_wav': str(tk['ref_p']), 'locked_voice_ref_sha256': tk['ref_sha'], 'sha256': d_meta['sha256'], 'spec_ok': True})\n",
    "        (dlg_dir / '台词音频清单.json').write_text(json.dumps({'episode': EPISODE, 'colab_fish_s2_pro_verified': True, 'tts_engine': 'fish_s2_pro', 'model': 'fishaudio/s2-pro', 'dialogue_records': records}, ensure_ascii=False, indent=2), encoding='utf-8')\n",
]


def _build_v41_voice_colab_notebook(
    project_root: Path,
    ep_s: str,
    stage: str = "both",
    output_ipynb: Path | None = None,
    engine: str = "auto",
    gpu: str = "",
) -> tuple[Path, Path]:
    engine, gpu = _resolve_voice_engine_and_gpu(stage, engine, gpu)
    eng_tag = {"voxcpm2": "VoxCPM2", "indextts25": "IndexTTS25", "fish_s2_pro": "FishS2Pro", "cosyvoice2": "CosyVoice2"}[engine]
    rel_hint = _extract_drive_rel_hint(project_root)
    proj_name = project_root.resolve().name
    ep_dir = project_root / "分集" / ep_s
    work_dir = project_root / "中间文件" / "在制工作区" / ep_s
    ep_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    fname_map = {
        "voice-ref": f"{ep_s}_Colab_{eng_tag}_声线母本.ipynb",
        "dialogue": f"{ep_s}_Colab_{eng_tag}_全集台词克隆.ipynb",
        "both": f"{ep_s}_Colab_{eng_tag}_声线与台词流水线.ipynb",
    }
    default_fname = fname_map.get(stage, fname_map["both"])
    nb_work_path = work_dir / default_fname
    nb_ep_path = output_ipynb if output_ipynb is not None else (ep_dir / default_fname)
    nb_ep_path.parent.mkdir(parents=True, exist_ok=True)

    if engine == "voxcpm2":
        setup_code = _voxcpm2_setup_code(stage, proj_name, rel_hint, ep_s)
    elif engine == "indextts25":
        setup_code = _indextts25_setup_code(stage, proj_name, rel_hint, ep_s)
    elif engine == "fish_s2_pro":
        setup_code = _fish_s2_pro_setup_code(stage, proj_name, rel_hint, ep_s)
    else:
        setup_code = _colab_drive_header_code(
            "挂载 Google Drive（相对路径零硬编码） + 加载 CosyVoice 2 - 0.5B（兼容兜底）",
            stage, proj_name, rel_hint, ep_s,
            extra_imports="import torch, torchaudio",
        ) + [
            "COSY_REPO = Path('/content/CosyVoice')\n",
            "MODEL_CACHE = Path('/content/drive/MyDrive/短剧复刻工作站/models/CosyVoice2-0.5B')\n",
            "if not COSY_REPO.exists():\n",
            "    subprocess.run(['git', 'clone', '--recursive', 'https://github.com/FunAudioLLM/CosyVoice.git', str(COSY_REPO)], check=True)\n",
            "    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'modelscope', 'conformer', 'diffusers', 'omegaconf', 'hydra-core', 'hyperpyyaml', 'onnxruntime-gpu', 'pyworld', 'inflect', 'wetext', 'edge-tts'], check=True)\n",
            "sys.path.insert(0, str(COSY_REPO))\n",
            "sys.path.insert(0, str(COSY_REPO / 'third_party' / 'Matcha-TTS'))\n",
            "if not (MODEL_CACHE / 'cosyvoice2.yaml').exists():\n",
            "    from modelscope import snapshot_download\n",
            "    MODEL_CACHE.parent.mkdir(parents=True, exist_ok=True)\n",
            "    snapshot_download('iic/CosyVoice2-0.5B', local_dir=str(MODEL_CACHE))\n",
            "from cosyvoice.cli.cosyvoice import CosyVoice2\n",
            "from cosyvoice.utils.file_utils import load_wav\n",
            "cosyvoice = CosyVoice2(str(MODEL_CACHE), load_jit=False, load_trt=False, fp16=torch.cuda.is_available())\n",
            "print(f'✅ CosyVoice 2 - 0.5B 加载就绪: PROJECT_ROOT={PROJECT_ROOT}')\n",
        ]
    return _finish_v41_voice_colab_notebook(ep_s, stage, nb_ep_path, nb_work_path, setup_code, engine=engine, gpu=gpu)


def _finish_v41_voice_colab_notebook(
    ep_s: str, stage: str, nb_ep_path: Path, nb_work_path: Path, setup_code: list[str],
    engine: str = "voxcpm2", gpu: str = "",
) -> tuple[Path, Path]:
    run_code_part1 = [
        "# === Step 1：严格声线母本核验（零默认示例回退） + [0.90, 1.15] atempo 时长贴合 + 48kHz Mono pcm_s16le 合成 ===\n",
        "def sha256_f(p: Path) -> str:\n",
        "    return hashlib.sha256(p.read_bytes()).hexdigest()\n\n",
        "def probe_wav_meta(p: Path) -> dict:\n",
        "    r = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', str(p)], capture_output=True, text=True, check=True)\n",
        "    d = json.loads(r.stdout or '{}')\n",
        "    s = next((x for x in d.get('streams', []) if x.get('codec_type') == 'audio'), {})\n",
        "    dur = float(d.get('format', {}).get('duration') or s.get('duration') or 0.0)\n",
        "    return {'duration_sec': round(dur, 4), 'sample_rate': int(s.get('sample_rate') or 0), 'channels': int(s.get('channels') or 0), 'codec_name': str(s.get('codec_name') or ''), 'sha256': sha256_f(p)}\n\n",
        "def parse_seg_shot(raw_s: str):\n",
        "    ms = re.search(r'(segment_\\d+)', str(raw_s or ''))\n",
        "    mh = re.search(r'(镜头\\d+)', str(raw_s or ''))\n",
        "    return (ms.group(1) if ms else 'segment_01'), (mh.group(1) if mh else '镜头01')\n\n",
        "def parse_t_sec(ts_s: str) -> float:\n",
        "    s = str(ts_s or '').strip().replace(',', '.').rstrip('s秒')\n",
        "    if not s: return 0.0\n",
        "    ps = s.split(':')\n",
        "    return float(ps[0])*3600+float(ps[1])*60+float(ps[2]) if len(ps)==3 else (float(ps[0])*60+float(ps[1]) if len(ps)==2 else float(s))\n\n",
        "def save_voice_ref_2_to_3s(raw_tensor, sr: int, dst_wav: Path) -> dict:\n",
        "    dst_wav.parent.mkdir(parents=True, exist_ok=True)\n",
        "    tmp_p = Path('/tmp') / f'_vref_{dst_wav.name}'\n",
        "    torchaudio.save(str(tmp_p), raw_tensor.cpu(), sr)\n",
        "    dur_s = float(raw_tensor.shape[-1]) / max(1, sr)\n",
        "    af = ['silenceremove=start_periods=1:start_silence=0.04:start_threshold=-45dB']\n",
        "    if dur_s > 2.90: af.append(f'atempo={min(1.5, dur_s / 2.60):.4f}')\n",
        "    af.extend(['apad=pad_dur=2.35', 'atrim=0:2.85'])\n",
        "    subprocess.run(['ffmpeg', '-y', '-i', str(tmp_p), '-af', ','.join(af), '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(dst_wav)], check=True)\n",
        "    return probe_wav_meta(dst_wav)\n\n",
        "def save_dialogue_fit_budget_from_wav(raw_wav: Path, dst_wav: Path, budget_sec: float, seg_shot_tag: str) -> tuple[dict, float]:\n",
        "    dst_wav.parent.mkdir(parents=True, exist_ok=True)\n",
        "    trim_p = Path('/tmp') / f'_dlg_trim_{dst_wav.name}'\n",
        "    subprocess.run(['ffmpeg', '-y', '-i', str(raw_wav), '-af', 'silenceremove=start_periods=1:start_duration=0.02:start_threshold=-44dB:stop_periods=-1:stop_duration=0.12:stop_threshold=-46dB', '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(trim_p)], check=True)\n",
        "    raw_dur = max(0.1, probe_wav_meta(trim_p)['duration_sec'])\n",
        "    target_b = max(0.5, float(budget_sec))\n",
        "    ideal_tempo = raw_dur / target_b\n",
        "    if ideal_tempo > 1.15:\n",
        "        raise RuntimeError(f'DIALOGUE_OVER_BUDGET: {seg_shot_tag} 原始时长 {raw_dur:.2f}s 超出镜头预算 {target_b:.2f}s 的 1.15x 上限 (需 atempo={ideal_tempo:.3f} > 1.15)，严禁强压变形，请精简台词或扩充镜长！')\n",
        "    safe_tempo = max(0.90, min(1.15, ideal_tempo))\n",
        "    subprocess.run(['ffmpeg', '-y', '-i', str(trim_p), '-af', f'atempo={safe_tempo:.4f}', '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(dst_wav)], check=True)\n",
        "    return probe_wav_meta(dst_wav), round(safe_tempo, 4)\n\n",
        "def save_dialogue_fit_budget(raw_tensor, sr: int, dst_wav: Path, budget_sec: float, seg_shot_tag: str) -> tuple[dict, float]:\n",
        "    raw_p = Path('/tmp') / f'_dlg_raw_{dst_wav.name}'\n",
        "    torchaudio.save(str(raw_p), raw_tensor.cpu(), sr)\n",
        "    return save_dialogue_fit_budget_from_wav(raw_p, dst_wav, budget_sec, seg_shot_tag)\n\n",
    ]
    return _emit_voice_colab_ipynb(ep_s, stage, nb_ep_path, nb_work_path, setup_code, run_code_part1, engine=engine, gpu=gpu)


def _emit_voice_colab_ipynb(
    ep_s: str, stage: str, nb_ep_path: Path, nb_work_path: Path,
    setup_code: list[str], run_code_part1: list[str],
    engine: str = "voxcpm2", gpu: str = "",
) -> tuple[Path, Path]:
    run_code_part2 = [
        "try:\n",
        "    ep_dir = PROJECT_ROOT / '分集' / EPISODE\n",
        "    work_dir = PROJECT_ROOT / '中间文件' / '在制工作区' / EPISODE\n",
        "    ana_dir = PROJECT_ROOT / '中间文件' / '原片分析' / EPISODE\n",
        "    audit_dir = PROJECT_ROOT / '中间文件' / '索引与制作记录' / '交付自查状态'\n",
        "    voice_ep_dir = ep_dir / '声线'\n",
        "    voice_root_dir = PROJECT_ROOT / '声线参考'\n",
        "    for _d in (voice_ep_dir, voice_root_dir, work_dir, audit_dir): _d.mkdir(parents=True, exist_ok=True)\n\n",
        "    if STAGE in ('voice-ref', 'both'):\n",
        "        rmap_p = PROJECT_ROOT / '中间文件' / '索引与制作记录' / '角色改名与音色映射.json'\n",
        "        if not rmap_p.exists(): raise FileNotFoundError(f'未找到角色改名与音色映射: {rmap_p}')\n",
        "        rmap = json.loads(rmap_p.read_text(encoding='utf-8-sig'))\n",
        "        chars = rmap.get('characters') or rmap.get('roles') or (rmap if isinstance(rmap, list) else [])\n",
        "        if not chars: raise RuntimeError(f'角色改名与音色映射为空: {rmap_p}')\n",
        "        gen_records, sha_map_out = [], {}\n",
        "        for idx_c, ch in enumerate(chars, 1):\n",
        "            rid = str(ch.get('role_id') or ch.get('id') or f'角色{idx_c:02d}').strip()\n",
        "            rname = str(ch.get('new_name') or ch.get('name') or ch.get('display_name') or f'角色{idx_c:02d}').strip()\n",
        "            gender = str(ch.get('gender') or 'female').lower()\n",
        "            timbre = str(ch.get('voice_style') or ch.get('timbre') or '自然清晰影视剧声线').strip()\n",
        "            edge_v = 'zh-CN-YunxiNeural' if 'male' in gender and 'fe' not in gender else 'zh-CN-XiaoxiaoNeural'\n",
        "            carrier_mp3 = Path('/tmp') / f'_carrier_{idx_c}.mp3'\n",
        "            carrier_16k = Path('/tmp') / f'_carrier_{idx_c}_16k.wav'\n",
        "            subprocess.run(['edge-tts', '--voice', edge_v, '--text', '今天天气平稳，一切按计划推进。', '--write-media', str(carrier_mp3)], check=True)\n",
        "            subprocess.run(['ffmpeg', '-y', '-i', str(carrier_mp3), '-af', 'atrim=0:2.6', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(carrier_16k)], check=True)\n",
        "            prompt_16k = load_wav(str(carrier_16k), 16000)\n",
        "            for mode_lbl, txt_s, inst_s in (\n",
        "                ('说话', ch.get('speak_sample_text') or '这件事到此为止，谁也别再提了。', f'用{timbre}自然坚定地说出这句话'),\n",
        "                ('心声', ch.get('os_sample_text') or '原来从一开始，他就已经布好了局。', f'用{timbre}低沉内敛的内心独白语气轻声诉说'),\n",
        "            ):\n",
        "                out_name = f'{rid}_{rname}_{mode_lbl}_声线参考.wav'\n",
        "                chunks = [o['tts_speech'] for o in cosyvoice.inference_instruct2(txt_s, inst_s, prompt_16k, stream=False)]\n",
        "                dst_p = voice_ep_dir / out_name\n",
        "                v_meta = save_voice_ref_2_to_3s(torch.cat(chunks, dim=-1), cosyvoice.sample_rate, dst_p)\n",
        "                shutil.copy2(dst_p, voice_root_dir / out_name)\n",
        "                sha_map_out[out_name] = v_meta['sha256']\n",
        "                gen_records.append({'role_id': rid, 'speaker': rname, 'voice_mode': mode_lbl, 'file': out_name, **v_meta})\n",
        "        vref_doc = {'episode': EPISODE, 'colab_cosyvoice2_verified': True, 'sha256_map': sha_map_out, 'records': gen_records}\n",
        "        (work_dir / 'voice_ref_colab_done.json').write_text(json.dumps(vref_doc, ensure_ascii=False, indent=2), encoding='utf-8')\n",
        "        (voice_ep_dir / '声线母本清单.json').write_text(json.dumps(vref_doc, ensure_ascii=False, indent=2), encoding='utf-8')\n",
        "        print(f'✅ Stage voice-ref 完成: 共生成 {len(gen_records)} 条 2-3s 48kHz Mono 双母本')\n\n",
        "    if STAGE in ('dialogue', 'both'):\n",
        "        dlg_csv = ep_dir / f'{EPISODE}_台词生成表.csv'\n",
        "        dlg_dir = ep_dir / '台词音频'\n",
        "        dlg_dir.mkdir(parents=True, exist_ok=True)\n",
        "        if not dlg_csv.exists(): raise FileNotFoundError(f'未找到台词生成表: {dlg_csv}')\n",
        "        record_dir = PROJECT_ROOT / '中间文件' / '索引与制作记录'\n",
        "        app_cands = [p for p in (record_dir / '声线母本批准清单.json', voice_ep_dir / '声线母本批准清单.json') if p.is_file() and p.stat().st_size > 0]\n",
        "        if not app_cands:\n",
        "            print(json.dumps({'status': 'VOICE_REF_NOT_APPROVED_OR_NOT_48K', 'error_code': 'VOICE_REF_NOT_APPROVED_OR_NOT_48K', 'reason': '缺少物理 声线母本批准清单.json'}, ensure_ascii=False)); sys.exit(2)\n",
        "        approved_triplets = {}\n",
        "        for ap in sorted(app_cands, key=lambda x: x.stat().st_mtime):\n",
        "            ad = json.loads(ap.read_text(encoding='utf-8-sig'))\n",
        "            items_a = ad if isinstance(ad, list) else (ad.get('approved_voice_refs') or ad.get('records') or ad.get('items') or [])\n",
        "            for mr in items_a:\n",
        "                if isinstance(mr, dict):\n",
        "                    sk_a = re.sub(r'^角色\\d+_', '', str(mr.get('speaker') or mr.get('character') or mr.get('new_name') or '').strip())\n",
        "                    vm_a = str(mr.get('voice_mode') or mr.get('mode') or '').strip()\n",
        "                    sh_a = str(mr.get('approved_sha256') or mr.get('sha256') or '').strip().lower()\n",
        "                    if sk_a and vm_a in ('说话', '心声') and sh_a: approved_triplets[(sk_a, vm_a)] = sh_a\n",
        "        shot_rel, seg_ep_s = {}, {}\n",
        "        for cp in (work_dir / 'cuts.csv', ana_dir / 'cuts.csv', ep_dir / 'cuts.csv'):\n",
        "            if cp.exists() and cp.stat().st_size > 0:\n",
        "                rc = list(csv.DictReader(cp.open('r', encoding='utf-8-sig')))\n",
        "                b0 = min((float(x.get('start_sec') or 0.0) for x in rc), default=0.0)\n",
        "                for x in rc:\n",
        "                    sg, sh = parse_seg_shot(f\"{x.get('segment','')}_{x.get('shot','')}\")\n",
        "                    s_v, e_v = float(x.get('start_sec') or 0.0), float(x.get('end_sec') or 0.0)\n",
        "                    seg_ep_s.setdefault(sg, s_v - b0)\n",
        "                    shot_rel[(sg, sh)] = (max(0.0, s_v - (seg_ep_s[sg] + b0)), max(0.2, e_v - (seg_ep_s[sg] + b0)))\n",
        "                break\n",
    ]
    if engine in ("indextts25", "fish_s2_pro"):
        v0 = next(i for i, s in enumerate(run_code_part2) if s.startswith("    if STAGE in ('voice-ref', 'both'):"))
        v1 = next(i for i, s in enumerate(run_code_part2) if s.startswith("    if STAGE in ('dialogue', 'both'):"))
        del run_code_part2[v0:v1]
    elif engine == "voxcpm2":
        i0 = next(i for i, s in enumerate(run_code_part2) if s.startswith("        gen_records, sha_map_out"))
        i1 = next(i for i, s in enumerate(run_code_part2) if s.startswith("                gen_records.append("))
        run_code_part2[i0:i1 + 1] = _VOXCPM2_VOICE_REF_BLOCK
        run_code_part2 = [s.replace("'colab_cosyvoice2_verified': True", "'colab_voxcpm2_verified': True, 'tts_engine': 'voxcpm2', 'model': 'openbmb/VoxCPM2'") for s in run_code_part2]
    return _finalize_voice_colab_ipynb(ep_s, stage, nb_ep_path, nb_work_path, setup_code, run_code_part1 + run_code_part2, engine=engine, gpu=gpu)


def _finalize_voice_colab_ipynb(
    ep_s: str, stage: str, nb_ep_path: Path, nb_work_path: Path,
    setup_code: list[str], run_code_prefix: list[str],
    engine: str = "auto", gpu: str = "",
) -> tuple[Path, Path]:
    engine, gpu = _resolve_voice_engine_and_gpu(stage, engine, gpu)
    run_code_part3 = [
        "        rows = list(csv.DictReader(dlg_csv.open('r', encoding='utf-8-sig')))\n",
        "        active_tasks, missing_vrefs = [], {}\n",
        "        for r in rows:\n",
        "            out_fname = (r.get('输出音频文件名') or '').strip()\n",
        "            strat = (r.get('视频通道与口型策略') or '').strip()\n",
        "            txt = (r.get('台词正文') or '').strip()\n",
        "            if not out_fname or out_fname == 'SKIP_JIMENG_NATIVE' or '即梦原生直出' in strat or not txt: continue\n",
        "            seg_id, shot_id = parse_seg_shot(r.get('分段与镜号') or out_fname)\n",
        "            raw_spk = re.sub(r'^角色\\d+_', '', (r.get('说话角色') or '主角').strip())\n",
        "            vmode = '心声' if any(k in (r.get('台词类型') or '').upper() for k in ('心声', 'OS', '内白', '背影', '画外')) or '_心声' in out_fname else '说话'\n",
        "            ref_name = (r.get('绑定声线母本') or '').strip()\n",
        "            cands = ([voice_ep_dir / ref_name, voice_root_dir / ref_name] if ref_name else []) + [w for d in (voice_ep_dir, voice_root_dir) if d.exists() for w in sorted(d.glob('*.wav')) if raw_spk in w.stem and vmode in w.stem]\n",
        "            ref_p = next((p for p in cands if p.exists() and p.is_file() and p.stat().st_size > 0), None)\n",
        "            err_r = ''\n",
        "            if ref_p is None:\n",
        "                err_r = f'缺失 ({raw_spk}, {vmode}) 声线母本'\n",
        "            else:\n",
        "                wm = probe_wav_meta(ref_p)\n",
        "                exp_sha = approved_triplets.get((raw_spk, vmode))\n",
        "                if not (2.00 <= wm['duration_sec'] <= 3.00) or wm['sample_rate'] != 48000 or wm['channels'] != 1 or wm['codec_name'] != 'pcm_s16le':\n",
        "                    err_r = f\"VOICE_REF_NOT_APPROVED_OR_NOT_48K: 母本非 48kHz/Mono/pcm_s16le/2-3s: {ref_p.name} ({wm})\"\n",
        "                elif not exp_sha or wm['sha256'].lower() != exp_sha:\n",
        "                    err_r = f\"VOICE_REF_NOT_APPROVED_OR_NOT_48K: ({raw_spk}, {vmode}) 未在批准清单授权或 SHA-256 失配: {ref_p.name}\"\n",
        "            if err_r:\n",
        "                missing_vrefs.setdefault((raw_spk, vmode), {'reason': err_r, 'shots': []})['shots'].append(f'{seg_id}_{shot_id}')\n",
        "                continue\n",
        "            sh_s, sh_e = shot_rel.get((seg_id, shot_id), (0.0, 2.4))\n",
        "            ts_s = r.get('起止时间轴与目标时长') or ''\n",
        "            m_dur = re.search(r'\\(\\s*(\\d+(?:\\.\\d+)?)s\\s*\\)', ts_s)\n",
        "            m_rng = re.search(r'(\\d+(?::\\d+)*(?:\\.\\d+)?)s?\\s*[-–~]\\s*(\\d+(?::\\d+)*(?:\\.\\d+)?)s?', ts_s)\n",
        "            if m_rng:\n",
        "                t1, t2 = parse_t_sec(m_rng.group(1)), parse_t_sec(m_rng.group(2))\n",
        "                s0_e = seg_ep_s.get(seg_id, 0.0)\n",
        "                sh_s, sh_e = (t1, t2) if (':' not in m_rng.group(1) and t1 < 15.0) else (max(0.0, t1 - s0_e), max(0.2, t2 - s0_e))\n",
        "            budget = float(m_dur.group(1)) if m_dur else max(0.5, sh_e - sh_s)\n",
        "            active_tasks.append({'segment': seg_id, 'shot': shot_id, 'speaker': raw_spk, 'voice_mode': vmode, 'ref_p': ref_p, 'ref_sha': wm['sha256'], 'inst': (r.get('情绪导演指令') or r.get('VoxCPM2_情绪导演指令') or r.get('CosyVoice2_情绪导演指令') or '用自然语气演绎').strip(), 'txt': txt, 'out_fname': out_fname, 'start_in_seg_sec': round(sh_s, 3), 'end_in_seg_sec': round(sh_s + budget, 3), 'budget_sec': round(budget, 3)})\n",
        "        if missing_vrefs:\n",
        "            print(json.dumps({'status': 'VOICE_REF_NOT_APPROVED_OR_NOT_48K', 'error_code': 'VOICE_REF_NOT_APPROVED_OR_NOT_48K', 'issues': {f\"{k[0]}_{k[1]}\": v for k, v in missing_vrefs.items()}}, ensure_ascii=False)); sys.exit(2)\n",
        "        records = []\n",
        "        for tk in active_tasks:\n",
        "            prompt_16k = load_wav(str(tk['ref_p']), 16000)\n",
        "            chunks = [o['tts_speech'] for o in cosyvoice.inference_instruct2(tk['txt'], tk['inst'], prompt_16k, stream=False)]\n",
        "            dst_w = dlg_dir / tk['out_fname']\n",
        "            d_meta, atempo_v = save_dialogue_fit_budget(torch.cat(chunks, dim=-1), cosyvoice.sample_rate, dst_w, tk['budget_sec'], f\"{tk['segment']}_{tk['shot']}\")\n",
        "            records.append({'segment': tk['segment'], 'shot': tk['shot'], 'unit_key': f\"{tk['segment']}_{tk['shot']}\", 'speaker': tk['speaker'], 'character': tk['speaker'], 'voice_mode': tk['voice_mode'], 'start_in_seg_sec': tk['start_in_seg_sec'], 'end_in_seg_sec': tk['end_in_seg_sec'], 'target_duration_sec': tk['budget_sec'], 'actual_duration_sec': d_meta['duration_sec'], 'applied_atempo': atempo_v, 'file_name': tk['out_fname'], 'wav_path': str(dst_w), 'needs_lipsync': (tk['voice_mode'] == '说话'), 'locked_voice_ref_wav': str(tk['ref_p']), 'locked_voice_ref_sha256': tk['ref_sha'], 'sha256': d_meta['sha256'], 'spec_ok': True})\n",
        "        (dlg_dir / '台词音频清单.json').write_text(json.dumps({'episode': EPISODE, 'colab_cosyvoice2_verified': True, 'dialogue_records': records}, ensure_ascii=False, indent=2), encoding='utf-8')\n",
        "        print(f'✅ Stage dialogue 完成: 共合成 {len(records)} 条 48kHz Mono pcm_s16le 台词/心声 .wav')\n",
        "finally:\n",
        "    try: Path('/tmp/rclone.conf').unlink()\n",
        "    except Exception: pass\n",
        "    if AUTO_SHUTDOWN:\n",
        "        try:\n",
        "            if hasattr(os, 'sync'): os.sync()\n",
        "        except Exception: pass\n",
        "        try:\n",
        "            from google.colab import drive; drive.flush_and_unmount()\n",
        "        except Exception: pass\n",
        "        try:\n",
        "            from google.colab import runtime; runtime.unassign()\n",
        "        except Exception: pass\n",
    ]
    if engine == "indextts25":
        k0 = next(i for i, s in enumerate(run_code_part3) if s.startswith("        records = []"))
        k1 = next(i for i, s in enumerate(run_code_part3) if s.startswith("        (dlg_dir / '台词音频清单.json').write_text("))
        run_code_part3[k0:k1 + 1] = _INDEXTTS25_DIALOGUE_EXEC_BLOCK
        eng_title = "IndexTTS-2.5 (IndexTeam/IndexTTS-2.5 情绪解耦与精准卡时长)"
    elif engine == "fish_s2_pro":
        k0 = next(i for i, s in enumerate(run_code_part3) if s.startswith("        records = []"))
        k1 = next(i for i, s in enumerate(run_code_part3) if s.startswith("        (dlg_dir / '台词音频清单.json').write_text("))
        run_code_part3[k0:k1 + 1] = _FISH_S2_PRO_DIALOGUE_EXEC_BLOCK
        eng_title = "Fish Audio S2 Pro (fishaudio/s2-pro 拟人自然旁白)"
    elif engine == "voxcpm2":
        j0 = next(i for i, s in enumerate(run_code_part3) if s.startswith("            prompt_16k = load_wav(str(tk['ref_p'])"))
        j1 = next(i for i, s in enumerate(run_code_part3) if s.startswith("            d_meta, atempo_v = save_dialogue_fit_budget("))
        run_code_part3[j0:j1 + 1] = _VOXCPM2_DIALOGUE_BLOCK
        run_code_part3 = [s.replace("'colab_cosyvoice2_verified': True", "'colab_voxcpm2_verified': True, 'tts_engine': 'voxcpm2', 'model': 'openbmb/VoxCPM2'") for s in run_code_part3]
        eng_title = "VoxCPM 2 (openbmb/VoxCPM2 原生 48kHz)"
    else:
        eng_title = "CosyVoice 2 - 0.5B (兼容兜底)"
    nb_data = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"colab": {"provenance": [], "gpuType": gpu}, "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}, "accelerator": "GPU"},
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": [f"# 🎙️ {ep_s} · V4.7 Colab `{eng_title}` 严格母本声线与时长贴合流水线 (`STAGE={stage}` | `GPU={gpu}`)\n"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": setup_code},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": run_code_prefix + run_code_part3},
        ],
    }
    nb_txt = json.dumps(nb_data, ensure_ascii=False, indent=2)
    nb_ep_path.write_text(nb_txt, encoding="utf-8")
    if nb_work_path.resolve() != nb_ep_path.resolve():
        nb_work_path.write_text(nb_txt, encoding="utf-8")
    return nb_ep_path, nb_work_path


def run_build_voice_colab(
    project_root: Path,
    ep: str,
    stage: str = "both",
    output_ipynb: str = "",
    resolve_ws_fn: Callable[[str], Path] | None = None,
    resolve_cloud_id_fn: Callable[[Path], str] | None = None,
    engine: str = "auto",
    gpu: str = "",
) -> dict[str, Any]:
    ep_s = _norm_ep(ep)
    engine, gpu = _resolve_voice_engine_and_gpu(stage, engine, gpu)
    if stage == "dialogue":
        verify_voice_refs_for_dialogue(project_root, ep_s)

    out_p = (resolve_ws_fn(output_ipynb) if resolve_ws_fn else Path(output_ipynb).resolve()) if output_ipynb else None
    nb_ep_path, nb_work_path = _build_v41_voice_colab_notebook(project_root, ep_s, stage=stage, output_ipynb=out_p, engine=engine, gpu=gpu)
    colab_url = ""
    if resolve_cloud_id_fn is not None:
        try:
            cid = resolve_cloud_id_fn(nb_ep_path)
            colab_url = f"https://colab.research.google.com/drive/{cid}"
        except Exception:
            colab_url = ""

    result = {
        "status": "BUILD_VOICE_COLAB_OK",
        "episode": ep_s,
        "stage": stage,
        "tts_engine": engine,
        "gpu": gpu,
        "notebook_ipynb": str(nb_ep_path).replace("\\", "/"),
        "work_notebook_ipynb": str(nb_work_path).replace("\\", "/"),
        "colab_url": colab_url,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result
