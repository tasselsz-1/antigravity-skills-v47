#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V4.1 批次调度与硬门禁模块化扩展 (v41_batch_guards.py)
涵盖：输入哈希防脏复用、双盲审防伪盖戳、60次并发预占账本锁、Google Flow 能力与退役红线、
cuts.csv 区间并集 >=99.5% 门禁、两阶无原型兼容对照表、第三棒九项硬退出及全四棒状态机。
"""

import argparse
import contextlib
import csv
import datetime
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

FLOW_SUNSET_DATE = "2026-12-05"
V41_ELASTIC_MAX_BASE_UNITS = 54
V41_HARD_CAP_CALLS = 60

_LOCK_STRIP_PATTERNS = (
    r"【机位视角：[^\n]*",
    r"\[Reference Role Assignment\]:?[^\n]*",
    r"\[Strict Person Count Lock[^\]]*\]:?[^\n]*",
    r"\[Camera Pitch[^\]]*\]:?[^\n]*",
    r"\[Format Lock[^\]]*\]:?[^\n]*",
    r"\[HISTORICAL PORTRAIT BAREHEADED FACE-LOCK\]:?[^\n]*",
    r"\[SCENE MASTER CLEANLINESS[^\]]*\]:?[^\n]*",
    r"\[CHARACTER (?:PORTRAIT|SUBJECT) 4-IN-1[^\]]*\]:?[^\n]*",
    r"16:9 widescreen aspect ratio, single head-and-shoulders close-up portrait centered,[^\n]*",
    r"16:9 widescreen aspect ratio, full-body head-to-toe standing character portrait centered[^\n]*",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = target.parent / f"{target.name}.tmp.{os.getpid()}.{int(time.time() * 1000) % 100000}"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        for i in range(4):
            try:
                os.replace(str(tmp), str(target))
                break
            except PermissionError:
                if i == 3:
                    raise
                time.sleep(0.02 * (i + 1))
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


def normalize_prompt_for_hash(prompt_text: str) -> str:
    s = str(prompt_text or "").replace("\r\n", "\n").replace("\r", "\n")
    for pat in _LOCK_STRIP_PATTERNS:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)
    norm = re.sub(r"\s+", " ", s).strip()
    return norm if norm else re.sub(r"\s+", " ", str(prompt_text or "")).strip()


def resolve_ref_file(raw_ref: str | Path, root_dir: Path | None = None) -> Path | None:
    s = str(raw_ref or "").strip().replace("\\", "/")
    if not s:
        return None
    p = Path(s)
    if p.is_absolute() and p.is_file():
        return p.resolve()
    if root_dir is not None:
        r_base = Path(root_dir).resolve()
        for anc in [r_base, *r_base.parents[:4]]:
            cand = (anc / s).resolve()
            if cand.is_file():
                return cand
    return p.resolve() if p.is_file() else None


def compute_unit_input_fingerprint(
    prompt_text: str,
    ref_paths: list[str] | None = None,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    """计算标准化 prompt SHA-256 与所有参考图实时字节 SHA-256 的组合输入指纹"""
    r_dir = Path(root_dir).resolve() if root_dir else None
    norm_p = normalize_prompt_for_hash(prompt_text)
    prompt_sha = sha256_bytes(norm_p.encode("utf-8"))
    ref_digests: list[dict[str, str]] = []
    for raw_r in (ref_paths or []):
        rs = str(raw_r or "").strip().replace("\\", "/")
        if not rs:
            continue
        resolved = resolve_ref_file(rs, r_dir)
        if resolved is not None and resolved.is_file():
            r_hash = sha256_file(resolved)
        else:
            r_hash = f"UNMATERIALIZED:{rs}"
        ref_digests.append({"ref": rs, "sha256": r_hash})
    refs_Canon = json.dumps([d["sha256"] for d in ref_digests], ensure_ascii=True, separators=(",", ":"))
    refs_sha = sha256_bytes(refs_Canon.encode("utf-8"))
    combo = sha256_bytes(f"{prompt_sha}:{refs_sha}".encode("utf-8"))
    return {
        "input_fingerprint": combo,
        "prompt_sha256": prompt_sha,
        "refs_sha256": refs_sha,
        "ref_digests": ref_digests,
    }


def extract_item_prompt_and_refs(item: dict[str, Any], root_dir: Path | None = None) -> tuple[str, list[str]]:
    prompt_txt = ""
    for k in ("_manifest_prompt", "image_prompt", "prompt", "full_prompt", "prompt_en", "positive_prompt"):
        v = str(item.get(k) or "").strip()
        if v:
            prompt_txt = v
            break
    refs: list[str] = []
    for k in ("_manifest_refs", "refs", "image_refs", "reference_images"):
        rv = item.get(k)
        if isinstance(rv, list) and rv:
            refs = [str(x).strip() for x in rv if str(x).strip()]
            break
    if not refs:
        for k in ("prototype_photo_ref", "historical_photo_ref", "portrait_ref"):
            sv = str(item.get(k) or "").strip()
            if sv and sv not in refs:
                refs.append(sv)
        arch = item.get("architecture_refs") or item.get("explicit_architecture_refs") or []
        if isinstance(arch, str):
            arch = [arch]
        for a in arch:
            if str(a).strip() and str(a).strip() not in refs:
                refs.append(str(a).strip())
        for k in ("source_frame", "same_segment_first_frame_ref", "scene_ref", "character_ref"):
            sv = str(item.get(k) or "").strip()
            if sv and sv not in refs:
                refs.append(sv)
    normalized_refs: list[str] = []
    for idx, r in enumerate(refs):
        rn = r.replace("\\", "/")
        if idx == 0 and rn.endswith("_源.jpg") and root_dir is not None:
            src_p = resolve_ref_file(rn, root_dir)
            if src_p is not None:
                cand_clean = src_p.parent / "ref_clean_frames" / (src_p.stem[:-2] + "_净参考.jpg")
                if cand_clean.is_file():
                    try:
                        rn = cand_clean.resolve().relative_to(Path(root_dir).resolve()).as_posix()
                    except Exception:
                        rn = str(cand_clean.resolve()).replace("\\", "/")
        normalized_refs.append(rn)
    return prompt_txt, normalized_refs


def detect_stale_approved_units(
    manifest_items: list[dict[str, Any]],
    cls_data: dict[str, Any],
    work_dir: Path,
    project_root: Path | None,
    get_unit_id_fn,
) -> tuple[set[str], list[str]]:
    """对比当前清单实时输入指纹与历史批准指纹，失配自动失效重排"""
    approved_cats = {"用户已确认", "已采用", "自检通过", "审核通过"}
    anchor = project_root or work_dir
    valid_done: set[str] = set()
    stale_units: list[str] = []
    item_map = {get_unit_id_fn(it): it for it in manifest_items if isinstance(it, dict) and get_unit_id_fn(it) != "unknown"}
    for u, entry in (cls_data or {}).items():
        if not isinstance(entry, dict) or entry.get("category") not in approved_cats:
            continue
        if u not in item_map:
            valid_done.add(u)
            continue
        cur_p, cur_r = extract_item_prompt_and_refs(item_map[u], anchor)
        cur_fp = compute_unit_input_fingerprint(cur_p, cur_r, anchor)
        saved_fps = {str(entry.get(k) or "").strip().upper() for k in ("input_fingerprint", "manifest_input_fingerprint") if entry.get(k)}
        if not saved_fps and entry.get("batch"):
            b_jobs_p = work_dir / "生成批次" / str(entry["batch"]) / "jobs.json"
            if b_jobs_p.is_file():
                with contextlib.suppress(Exception):
                    b_raw = read_json(b_jobs_p)
                    b_list = b_raw if isinstance(b_raw, list) else (b_raw.get("jobs") or [])
                    old_j = next((j for j in b_list if isinstance(j, dict) and get_unit_id_fn(j) == u), None)
                    if old_j is not None:
                        op, orf = extract_item_prompt_and_refs(old_j, anchor)
                        saved_fps.add(compute_unit_input_fingerprint(op, orf, anchor)["input_fingerprint"])
        if saved_fps and cur_fp["input_fingerprint"] not in saved_fps:
            stale_units.append(u)
        else:
            valid_done.add(u)
    return valid_done, sorted(stale_units)


def _extract_manifest_items_generic(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for k in ("units", "jobs", "items", "frames", "tasks", "stage_a_units"):
            if isinstance(raw.get(k), list):
                return [x for x in raw[k] if isinstance(x, dict)]
    return []


def _unit_id_generic(job: dict[str, Any]) -> str:
    for k in ("unit", "unit_id", "frame_id", "id", "task_id"):
        if job.get(k):
            return str(job[k]).strip()
    if job.get("segment_id") and job.get("shot_id") and job.get("frame_type"):
        return f"{job['segment_id']}_{job['shot_id']}_{job['frame_type']}"
    return str(job.get("segment") or "unknown").strip()


_FORGED_RUN_ID_LITERALS = {
    "none", "null", "n/a", "na", "todo", "tbd", "placeholder",
    "default", "auto", "fake", "dummy", "mock", "unknown",
}


def _is_forged_or_missing_run_id(run_id: str, agent_name: str) -> bool:
    rid = str(run_id or "").strip()
    if not rid or rid.lower() in _FORGED_RUN_ID_LITERALS:
        return True
    rid_l = rid.lower()
    ag_alnum = re.sub(r"[^a-z0-9]", "", str(agent_name or "").lower())
    rid_alnum = re.sub(r"[^a-z0-9]", "", rid_l)
    if ag_alnum and rid_alnum in {
        ag_alnum, f"run{ag_alnum}", f"{ag_alnum}run",
        f"author{ag_alnum}", f"inspector{ag_alnum}",
        f"runauthor{ag_alnum}", f"runinspector{ag_alnum}",
    }:
        return True
    if re.match(r"^run_(?:author|inspector)_[a-z0-9._-]+$", rid_l) and not re.search(r"\d{4,}|[0-9a-f]{8}", rid_l):
        return True
    if re.match(
        r"^(?:run[_-]?)?(?:author|inspector)?[_-]?(?:gemini|carbon|neon|argon|hydrogen|flash|opus|sonnet|gpt|claude|orchestrator)"
        r"(?:[_-]?(?:gemini|carbon|neon|argon|hydrogen|flash|opus|sonnet|gpt|claude|orchestrator|xhigh|high|medium|low|lite|pro|oss|120b|3\.8|4|4\.1|4\.2|5|5\.5))*$",
        rid_l,
    ):
        return True
    return False


def _required_manifest_unit_ids(manifest_items: list[dict[str, Any]], uid_fn=None) -> list[str]:
    fn = uid_fn or _unit_id_generic
    req: list[str] = []
    for it in manifest_items:
        if not isinstance(it, dict):
            continue
        u = fn(it)
        if not u or u == "unknown":
            continue
        ft = str(it.get("frame_type") or "")
        if (ft == "尾帧" or u.endswith("_尾帧")) and it.get("need_tail_frame") is False:
            continue
        if u not in req:
            req.append(u)
    return req


def cmd_stamp_visual_audit(args: argparse.Namespace) -> None:
    """对双盲审记录计算并绑定实时 audited_prompt_sha256、audited_refs_sha256、author_run_id != inspector_run_id"""
    audit_p = Path(args.audit_json).resolve()
    if not audit_p.is_file():
        print(f"STAMP_AUDIT_ERR: 未找到审图文件 {audit_p}", file=sys.stderr)
        sys.exit(1)
    proj_raw = getattr(args, "project_dir", "") or getattr(args, "project_root", "")
    ep_raw = str(getattr(args, "episode", "第01集") or "第01集").strip()
    m_num = re.search(r"(\d+)", ep_raw)
    ep = f"第{int(m_num.group(1)):02d}集" if (m_num and not ep_raw.startswith("第")) else ep_raw
    proj_dir = Path(proj_raw).resolve() if proj_raw else None

    manifest_raw_arg = getattr(args, "manifest", "") or ""
    cand_manifests: list[Path] = []
    if manifest_raw_arg:
        cand_manifests.append(Path(manifest_raw_arg).resolve())
    cand_manifests.extend([audit_p.parent / "待生图帧清单.json", audit_p.parent.parent / "待生图帧清单.json"])
    if proj_dir is not None:
        cand_manifests.append(proj_dir / "中间文件" / "在制工作区" / ep / "待生图帧清单.json")
    manifest_p = (Path(manifest_raw_arg).resolve() if manifest_raw_arg else next((p for p in cand_manifests if p.is_file()), None))
    if manifest_p is None or not manifest_p.is_file():
        print(f"AUDIT_MANIFEST_MISSING_OR_EMPTY: 未找到有效待生图帧清单 ({manifest_raw_arg or '待生图帧清单.json'})", file=sys.stderr)
        sys.exit(2)
    anchor = proj_dir or manifest_p.parent
    manifest_items = _extract_manifest_items_generic(read_json(manifest_p))
    required_unit_ids = _required_manifest_unit_ids(manifest_items)
    if not required_unit_ids:
        print(f"AUDIT_MANIFEST_MISSING_OR_EMPTY: 待生图帧清单无有效单元 ({manifest_p})", file=sys.stderr)
        sys.exit(2)
    unit_map = {_unit_id_generic(it): it for it in manifest_items if isinstance(it, dict) and _unit_id_generic(it) != "unknown"}

    audit_data = read_json(audit_p)
    if not isinstance(audit_data, dict):
        print("STAMP_AUDIT_ERR: audit JSON 顶层必须为对象", file=sys.stderr)
        sys.exit(2)

    top_auth = str(getattr(args, "author_agent", "") or audit_data.get("author_agent") or "").strip()
    top_insp = str(getattr(args, "inspector_agent", "") or audit_data.get("inspector_agent") or "").strip()
    top_arun = str(getattr(args, "author_run_id", "") or audit_data.get("author_run_id") or "").strip()
    top_irun = str(getattr(args, "inspector_run_id", "") or audit_data.get("inspector_run_id") or "").strip()

    raw_u = audit_data.get("units")
    norm_units: dict[str, dict[str, Any]] = {}
    if isinstance(raw_u, dict):
        for uk, uv in raw_u.items():
            if isinstance(uv, dict):
                norm_units[str(uk).strip()] = dict(uv)
    elif isinstance(raw_u, list):
        for uv in raw_u:
            if isinstance(uv, dict) and _unit_id_generic(uv) != "unknown":
                norm_units[_unit_id_generic(uv)] = dict(uv)

    stamp_errs: list[str] = []
    if not norm_units:
        stamp_errs.append("AUDIT_MISSING_PER_UNIT_VERDICTS: 严禁仅给顶层 PASS 而省略非空 units 逐单元审图明细")
        if _is_forged_or_missing_run_id(top_arun, top_auth) or _is_forged_or_missing_run_id(top_irun, top_insp):
            stamp_errs.append(f"AUDIT_MISSING_EXPLICIT_RUN_IDS: 必须显式提供非伪造的 author_run_id 与 inspector_run_id ({top_arun!r}, {top_irun!r})")

    missing_or_invalid_verdicts: list[str] = []
    for req_u in required_unit_ids:
        u_rec = norm_units.get(req_u)
        if not isinstance(u_rec, dict):
            missing_or_invalid_verdicts.append(f"{req_u}(missing)")
            continue
        st_u = str(u_rec.get("status") or "").strip().upper()
        note_u = re.sub(r"\s+", "", str(u_rec.get("notes") or u_rec.get("reason") or ""))
        if st_u != "PASS" or len(note_u) < 8:
            missing_or_invalid_verdicts.append(f"{req_u}(status={st_u!r},note_len={len(note_u)})")
    if missing_or_invalid_verdicts:
        stamp_errs.append(f"AUDIT_MISSING_PER_UNIT_VERDICTS: 缺失逐单元 PASS 结论或具体审图理由(<8字): {missing_or_invalid_verdicts}")

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    stamped_count = 0
    for uk, u_obj in norm_units.items():
        ua = str(u_obj.get("author_agent") or top_auth).strip()
        ui = str(u_obj.get("inspector_agent") or top_insp).strip()
        if not ua or not ui or ua.lower() == ui.lower():
            stamp_errs.append(f"AUDIT_SELF_REVIEW_FORBIDDEN: 单元 {uk} author_agent={ua!r} 与 inspector_agent={ui!r} 缺失或相同")
            continue
        ar = str(u_obj.get("author_run_id") or top_arun).strip()
        ir = str(u_obj.get("inspector_run_id") or top_irun).strip()
        if _is_forged_or_missing_run_id(ar, ua) or _is_forged_or_missing_run_id(ir, ui):
            stamp_errs.append(f"AUDIT_MISSING_EXPLICIT_RUN_IDS: 单元 {uk} 缺失显式真实 run_id 或含伪造占位符 (author_run_id={ar!r}, inspector_run_id={ir!r})")
            continue
        if ar.lower() == ir.lower():
            stamp_errs.append(f"AUDIT_SELF_REVIEW_FORBIDDEN: 单元 {uk} author_run_id 与 inspector_run_id 严禁相同 ({ar!r})")
            continue
        src_item = unit_map.get(uk) or u_obj
        p_txt, r_list = extract_item_prompt_and_refs(src_item, anchor)
        if not p_txt:
            stamp_errs.append(f"AUDIT_PROMPT_HASH_MISMATCH_OR_MISSING: 无法找到单元 {uk} 的有效英文提示词用于盖戳")
            continue
        fp = compute_unit_input_fingerprint(p_txt, r_list, anchor)
        u_obj.update({
            "author_agent": ua, "inspector_agent": ui, "author_run_id": ar, "inspector_run_id": ir,
            "audited_prompt_sha256": fp["prompt_sha256"], "audited_refs_sha256": fp["refs_sha256"],
            "audited_ref_digests": fp["ref_digests"], "audited_input_fingerprint": fp["input_fingerprint"], "stamped_at": now_iso,
        })
        stamped_count += 1

    if stamp_errs:
        for se in stamp_errs:
            print(se, file=sys.stderr)
        sys.exit(2)
    if top_auth:
        audit_data["author_agent"] = top_auth
    if top_insp:
        audit_data["inspector_agent"] = top_insp
    if top_arun:
        audit_data["author_run_id"] = top_arun
    if top_irun:
        audit_data["inspector_run_id"] = top_irun
    audit_data["units"] = norm_units
    audit_data["stamped_at"] = now_iso
    write_json(audit_p, audit_data)
    print(json.dumps({"status": "STAMP_VISUAL_AUDIT_OK", "audit_json": str(audit_p).replace("\\", "/"), "stamped_units": stamped_count}, ensure_ascii=False, indent=2))


def verify_visual_audit_hashes(
    audit_data: dict[str, Any],
    manifest_items: list[dict[str, Any]],
    anchor: Path | None,
    get_unit_id_fn=None,
) -> list[str]:
    """强校验显式非伪造 run_id、逐单元 PASS+具体结论(>=8字)、author!=inspector 及实时 prompt/refs 哈希"""
    uid_fn = get_unit_id_fn or _unit_id_generic
    errs: list[str] = []
    top_auth = str(audit_data.get("author_agent") or "").strip()
    top_insp = str(audit_data.get("inspector_agent") or "").strip()
    top_arun = str(audit_data.get("author_run_id") or "").strip()
    top_irun = str(audit_data.get("inspector_run_id") or "").strip()
    if top_arun and top_irun and top_arun.lower() == top_irun.lower():
        errs.append(f"AUDIT_SELF_REVIEW_FORBIDDEN: 顶层 author_run_id 与 inspector_run_id 相同 ({top_arun!r})")

    raw_u = audit_data.get("units")
    u_records: dict[str, dict[str, Any]] = {}
    if isinstance(raw_u, dict):
        for uk, uv in raw_u.items():
            if isinstance(uv, dict):
                u_records[str(uk).strip()] = uv
    elif isinstance(raw_u, list):
        for uv in raw_u:
            if isinstance(uv, dict) and uid_fn(uv) != "unknown":
                u_records[uid_fn(uv)] = uv
    if not u_records:
        errs.append("AUDIT_MISSING_PER_UNIT_VERDICTS: 审计 JSON 缺少非空 units 逐单元审图明细")

    bad_verdicts: list[str] = []
    bad_agents: list[str] = []
    missing_runs: list[str] = []
    bad_runs: list[str] = []
    bad_hashes: list[str] = []
    for it in manifest_items:
        if not isinstance(it, dict):
            continue
        u = uid_fn(it)
        if not u or u == "unknown":
            continue
        ft = str(it.get("frame_type") or "")
        if (ft == "尾帧" or u.endswith("_尾帧")) and it.get("need_tail_frame") is False:
            continue
        rec = u_records.get(u)
        if not isinstance(rec, dict):
            bad_verdicts.append(f"{u}(missing_unit_record)")
            continue
        st_u = str(rec.get("status") or "").strip().upper()
        note_u = re.sub(r"\s+", "", str(rec.get("notes") or rec.get("reason") or ""))
        if st_u != "PASS" or len(note_u) < 8:
            bad_verdicts.append(f"{u}(status={st_u!r},note_len={len(note_u)})")
        ua = str(rec.get("author_agent") or top_auth).strip()
        ui = str(rec.get("inspector_agent") or top_insp).strip()
        if not ua or not ui or ua.lower() == ui.lower():
            bad_agents.append(f"{u}({ua!r}=={ui!r})")
        ar = str(rec.get("author_run_id") or top_arun).strip()
        ir = str(rec.get("inspector_run_id") or top_irun).strip()
        if _is_forged_or_missing_run_id(ar, ua) or _is_forged_or_missing_run_id(ir, ui):
            missing_runs.append(f"{u}(author_run_id={ar!r},inspector_run_id={ir!r})")
        elif ar.lower() == ir.lower():
            bad_runs.append(f"{u}(run_id={ar!r})")
        p_txt, r_list = extract_item_prompt_and_refs(it, anchor)
        cur_fp = compute_unit_input_fingerprint(p_txt, r_list, anchor)
        cur_sha = cur_fp["prompt_sha256"]
        aud_sha = str(rec.get("audited_prompt_sha256") or "").strip().upper()
        if not aud_sha or aud_sha != cur_sha:
            bad_hashes.append(f"{u}(expected={cur_sha[:10]},got={aud_sha[:10] if aud_sha else 'MISSING'})")
        elif isinstance(rec.get("audited_ref_digests"), list):
            aud_digs = rec["audited_ref_digests"]
            cur_digs = cur_fp["ref_digests"]
            if [d.get("ref") for d in aud_digs] != [d.get("ref") for d in cur_digs] or any(
                not str(ad.get("sha256", "")).startswith("UNMATERIALIZED:") and ad.get("sha256") != cd.get("sha256")
                for ad, cd in zip(aud_digs, cur_digs)
            ):
                bad_hashes.append(f"{u}(refs_sha_mismatch)")

    if bad_verdicts:
        errs.append(f"AUDIT_MISSING_PER_UNIT_VERDICTS: 缺失逐单元 PASS 结论或具体审图理由(<8字): {bad_verdicts}")
    if missing_runs:
        errs.append(f"AUDIT_MISSING_EXPLICIT_RUN_IDS: 缺失显式真实 run_id 或含伪造占位符: {missing_runs}")
    if bad_agents:
        errs.append(f"AUDIT_SELF_REVIEW_FORBIDDEN: author_agent 与 inspector_agent 缺失或相同: {bad_agents}")
    if bad_runs:
        errs.append(f"AUDIT_SELF_REVIEW_FORBIDDEN: author_run_id 与 inspector_run_id 冲突相同: {bad_runs}")
    if bad_hashes:
        errs.append(f"AUDIT_PROMPT_HASH_MISMATCH_OR_MISSING: 审图凭证缺失实时提示词/参考图哈希或改词换图后失配: {bad_hashes}")
    return errs


def compute_v41_eff_window_size(total_n: int, max_per_window: int = 50, elastic_cap: int = V41_HARD_CAP_CALLS) -> tuple[int, int]:
    """V4.1 弹性并窗硬红线：仅当 total_units <= 54 且 ceil(total_units * 1.10) <= elastic_cap(60) 时才允许单窗合并"""
    safe_limit = min(V41_ELASTIC_MAX_BASE_UNITS, int(math.floor(int(elastic_cap) / 1.10)))
    elastic_base_limit = max(max_per_window, safe_limit) if int(elastic_cap) > max_per_window else max_per_window
    can_merge = (max_per_window < total_n <= V41_ELASTIC_MAX_BASE_UNITS) and (math.ceil(total_n * 1.10) <= int(elastic_cap))
    eff_size = total_n if can_merge else max_per_window
    return eff_size, elastic_base_limit


@contextlib.contextmanager
def _ledger_file_lock(work_dir: Path, timeout_sec: float = 10.0):
    work_dir.mkdir(parents=True, exist_ok=True)
    lock_p = work_dir / ".ledger.lock"
    deadline = time.time() + timeout_sec
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_p), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"{os.getpid()}:{time.time()}".encode("utf-8"))
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock_p.stat().st_mtime > 30.0:
                    lock_p.unlink()
                    continue
            if time.time() >= deadline:
                raise TimeoutError(f"无法获取账本锁 {lock_p}")
            time.sleep(0.02)
    try:
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            lock_p.unlink()


def _rsv_state(rec: dict[str, Any]) -> str:
    return str(rec.get("state") or rec.get("status") or "").strip().upper()


def _recompute_window_counts(win: dict[str, Any]) -> None:
    res_list = win.setdefault("reservations", [])
    win["pending_calls"] = sum(1 for r in res_list if isinstance(r, dict) and _rsv_state(r) == "PENDING")
    win["used_calls"] = sum(1 for r in res_list if isinstance(r, dict) and _rsv_state(r) in {"OK", "429", "TIMEOUT", "ERROR"})


def _load_or_init_ledger(ledger_p: Path, window_id: str, hard_cap: int) -> tuple[dict[str, Any], dict[str, Any]]:
    data = read_json(ledger_p) if ledger_p.is_file() else {"schema_version": "4.2", "global_cooldown_until": 0.0, "windows": {}}
    if not isinstance(data, dict):
        data = {"schema_version": "4.2", "global_cooldown_until": 0.0, "windows": {}}
    wins = data.setdefault("windows", {})
    win = wins.setdefault(window_id, {
        "window": window_id,
        "hard_cap": int(hard_cap),
        "used_calls": 0,
        "pending_calls": 0,
        "cooldown_until": 0.0,
        "reservations": [],
    })
    win["hard_cap"] = int(hard_cap)
    for w_obj in wins.values():
        if isinstance(w_obj, dict):
            _recompute_window_counts(w_obj)
    max_win_cd = max((float(w.get("cooldown_until") or 0.0) for w in wins.values() if isinstance(w, dict)), default=0.0)
    data["global_cooldown_until"] = round(max(float(data.get("global_cooldown_until") or 0.0), max_win_cd), 3)
    return data, win


def cmd_quota_reserve(args: argparse.Namespace) -> None:
    """调用生图前原子预占窗口额度，超 60 硬上限或处于全局/窗口 429 Retry-After 冷却期时立即退出码 4 阻断"""
    work_dir = Path(args.work_dir).resolve()
    win_id = str(getattr(args, "window", "window_01") or "window_01").strip()
    unit_id = str(getattr(args, "unit_id", "") or "").strip()
    kind = str(getattr(args, "kind", "initial") or "initial").strip()
    hard_cap = int(getattr(args, "hard_cap", V41_HARD_CAP_CALLS) or V41_HARD_CAP_CALLS)
    ledger_p = work_dir / "api_call_ledger.json"

    with _ledger_file_lock(work_dir):
        now = time.time()
        data, win = _load_or_init_ledger(ledger_p, win_id, hard_cap)
        global_cd = float(data.get("global_cooldown_until") or 0.0)
        cd_until = max(global_cd, float(win.get("cooldown_until") or 0.0))
        used_n = int(win["used_calls"])
        pend_n = int(win["pending_calls"])
        if cd_until > now:
            blk = {
                "ok": False,
                "status": "QUOTA_BLOCKED_429_COOLDOWN",
                "window": win_id,
                "unit_id": unit_id,
                "global_cooldown_until": global_cd,
                "cooldown_remaining_sec": round(cd_until - now, 2),
                "used_calls": used_n,
                "pending_calls": pend_n,
                "hard_cap": hard_cap,
            }
            print(json.dumps(blk, ensure_ascii=False, indent=2))
            sys.exit(4)
        if used_n + pend_n >= hard_cap:
            blk = {
                "ok": False,
                "status": "QUOTA_BLOCKED_HARD_CAP_EXCEEDED",
                "window": win_id,
                "unit_id": unit_id,
                "used_calls": used_n,
                "pending_calls": pend_n,
                "hard_cap": hard_cap,
            }
            print(json.dumps(blk, ensure_ascii=False, indent=2))
            sys.exit(4)
        rsv_id = f"rsv_{win_id}_{len(win['reservations']) + 1:03d}_{os.getpid()}_{int(now * 1000) % 100000:05d}"
        win["reservations"].append({
            "reservation_id": rsv_id,
            "unit_id": unit_id,
            "kind": kind,
            "state": "PENDING",
            "status": "pending",
            "reserved_at": round(now, 3),
        })
        win["pending_calls"] = pend_n + 1
        write_json(ledger_p, data)
        print(json.dumps({
            "ok": True,
            "status": "QUOTA_RESERVED",
            "reservation_id": rsv_id,
            "state": "PENDING",
            "window": win_id,
            "unit_id": unit_id,
            "kind": kind,
            "used_calls": used_n,
            "pending_calls": win["pending_calls"],
            "remaining_calls": hard_cap - (used_n + win["pending_calls"]),
            "hard_cap": hard_cap,
        }, ensure_ascii=False, indent=2))


def cmd_quota_reconcile(args: argparse.Namespace) -> None:
    """按预占单号严格幂等核销，未知单号报错 exit 2；429 写入账本顶层 global_cooldown_until 跨窗口阻断"""
    work_dir = Path(args.work_dir).resolve()
    win_id = str(getattr(args, "window", "window_01") or "window_01").strip()
    rsv_id = str(getattr(args, "reservation_id", "") or "").strip()
    unit_id = str(getattr(args, "unit_id", "") or "").strip()
    outcome_low = str(args.outcome).strip().lower()
    outcome_up = outcome_low.upper()
    retry_sec = float(getattr(args, "retry_after_sec", 0.0) or 0.0)
    if not rsv_id and not unit_id:
        err_j = {"ok": False, "status": "QUOTA_RECONCILE_UNKNOWN_RESERVATION", "reason": "missing_reservation_id_and_unit_id"}
        print(json.dumps(err_j, ensure_ascii=False, indent=2))
        print("QUOTA_RECONCILE_UNKNOWN_RESERVATION: 必须提供有效 --reservation-id 或存在 PENDING 预占单的 --unit-id", file=sys.stderr)
        sys.exit(2)
    ledger_p = work_dir / "api_call_ledger.json"

    with _ledger_file_lock(work_dir):
        now = time.time()
        data, win = _load_or_init_ledger(ledger_p, win_id, V41_HARD_CAP_CALLS)
        search_wins = [win] + [v for k, v in data.get("windows", {}).items() if k != win_id and isinstance(v, dict)]
        target_rec = None
        if rsv_id:
            for w_obj in search_wins:
                for r in reversed(w_obj.get("reservations") or []):
                    if isinstance(r, dict) and r.get("reservation_id") == rsv_id:
                        target_rec = r
                        win = w_obj
                        win_id = str(w_obj.get("window") or win_id)
                        break
                if target_rec is not None:
                    break
            if target_rec is not None and _rsv_state(target_rec) in {"OK", "429", "TIMEOUT", "ERROR"}:
                print(json.dumps({
                    "ok": True,
                    "status": "QUOTA_RECONCILED",
                    "idempotent_replay": True,
                    "window": win_id,
                    "reservation_id": target_rec["reservation_id"],
                    "state": _rsv_state(target_rec),
                    "outcome": str(target_rec.get("status") or _rsv_state(target_rec).lower()),
                    "used_calls": win["used_calls"],
                    "pending_calls": win["pending_calls"],
                    "cooldown_until": win.get("cooldown_until", 0.0),
                    "global_cooldown_until": float(data.get("global_cooldown_until") or 0.0),
                }, ensure_ascii=False, indent=2))
                return
        else:
            for w_obj in search_wins:
                for r in reversed(w_obj.get("reservations") or []):
                    if isinstance(r, dict) and _rsv_state(r) == "PENDING" and r.get("unit_id") == unit_id:
                        target_rec = r
                        win = w_obj
                        win_id = str(w_obj.get("window") or win_id)
                        break
                if target_rec is not None:
                    break

        if target_rec is None or _rsv_state(target_rec) != "PENDING":
            err_j = {"ok": False, "status": "QUOTA_RECONCILE_UNKNOWN_RESERVATION", "window": win_id, "reservation_id": rsv_id, "unit_id": unit_id}
            print(json.dumps(err_j, ensure_ascii=False, indent=2))
            print(f"QUOTA_RECONCILE_UNKNOWN_RESERVATION: 未找到匹配的 PENDING 预占单 (reservation_id={rsv_id!r}, unit_id={unit_id!r})", file=sys.stderr)
            sys.exit(2)

        target_rec["state"] = outcome_up
        target_rec["status"] = outcome_low
        target_rec["reconciled_at"] = round(now, 3)
        if outcome_low == "429":
            eff_wait = retry_sec if retry_sec > 0 else 60.0
            cd_ts = round(max(float(win.get("cooldown_until") or 0.0), float(data.get("global_cooldown_until") or 0.0), now + eff_wait), 3)
            win["cooldown_until"] = cd_ts
            data["global_cooldown_until"] = cd_ts
        _recompute_window_counts(win)
        write_json(ledger_p, data)
        print(json.dumps({
            "ok": True,
            "status": "QUOTA_RECONCILED",
            "idempotent_replay": False,
            "window": win_id,
            "reservation_id": target_rec["reservation_id"],
            "state": outcome_up,
            "outcome": outcome_low,
            "used_calls": win["used_calls"],
            "pending_calls": win["pending_calls"],
            "cooldown_until": win.get("cooldown_until", 0.0),
            "global_cooldown_until": float(data.get("global_cooldown_until") or 0.0),
        }, ensure_ascii=False, indent=2))


def check_remedy_quota_or_exit(work_dir: Path, window_id: str, remedy_count: int, hard_cap: int = V41_HARD_CAP_CALLS) -> None:
    """在 remedy-prep 生成补救任务前自动核查窗口账本余量与全局/窗口 429 冷却锁"""
    if remedy_count <= 0:
        return
    ledger_p = Path(work_dir).resolve() / "api_call_ledger.json"
    with _ledger_file_lock(Path(work_dir).resolve()):
        now = time.time()
        data, win = _load_or_init_ledger(ledger_p, window_id or "window_01", hard_cap)
        cd_until = max(float(data.get("global_cooldown_until") or 0.0), float(win.get("cooldown_until") or 0.0))
        used_n = int(win["used_calls"])
        pend_n = int(win["pending_calls"])
        cap = int(win.get("hard_cap") or hard_cap)
        if cd_until > now or (used_n + pend_n + remedy_count > cap):
            reason = "QUOTA_BLOCKED_429_COOLDOWN" if cd_until > now else "QUOTA_BLOCKED_HARD_CAP_EXCEEDED"
            print(json.dumps({
                "ok": False,
                "status": reason,
                "stage": "remedy-prep",
                "window": window_id or "window_01",
                "requested_remedies": remedy_count,
                "used_calls": used_n,
                "pending_calls": pend_n,
                "hard_cap": cap,
                "cooldown_remaining_sec": round(max(0.0, cd_until - now), 2),
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            sys.exit(4)


def resolve_as_of_date(as_of_date: str | None = None) -> str:
    s = str(as_of_date or "").strip()
    return s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else datetime.date.today().isoformat()


def resolve_flow_tier(explicit_tier: str | None = None, *hint_paths: Path | str | None) -> str:
    t_exp = str(explicit_tier or "").strip().lower()
    if t_exp in ("free", "paid"):
        return t_exp
    cand_jsons: list[Path] = []
    for hp in hint_paths:
        if not hp:
            continue
        p = Path(hp).resolve()
        base = p if p.is_dir() else p.parent
        if p.is_file() and p.suffix.lower() == ".json":
            cand_jsons.append(p)
        cand_jsons.extend([base / "episode_director_bible.json", base.parent / "episode_director_bible.json"])
        m_ep = re.search(r"(第\d{2,3}集)", str(p))
        ep_n = m_ep.group(1) if m_ep else "第01集"
        for anc in [base, *base.parents[:4]]:
            if (anc / "中间文件").is_dir() or (anc / "分集").is_dir():
                cand_jsons.extend([
                    anc / "中间文件" / "在制工作区" / ep_n / "episode_director_bible.json",
                    anc / "中间文件" / "原片分析" / ep_n / "episode_director_bible.json",
                    anc / "分集" / ep_n / "episode_director_bible.json",
                    anc / "episode_director_bible.json",
                ])
                break
    seen_p: set[Path] = set()
    for jp in cand_jsons:
        if jp in seen_p or not jp.is_file():
            continue
        seen_p.add(jp)
        with contextlib.suppress(Exception):
            doc = read_json(jp)
            if isinstance(doc, dict):
                for raw_ft in (
                    doc.get("flow_tier"),
                    (doc.get("routing_policy") or {}).get("flow_tier") if isinstance(doc.get("routing_policy"), dict) else None,
                    (doc.get("flow_config") or {}).get("flow_tier") if isinstance(doc.get("flow_config"), dict) else None,
                ):
                    ft = str(raw_ft or "").strip().lower()
                    if ft in ("free", "paid"):
                        return ft
    return "paid"


def validate_flow_segment_rule(
    seg_id: str,
    model_route: str,
    video_mode: str,
    gen_dur: float,
    actual_dur: float = 0.0,
    shot_count: int = 1,
    mounted_refs_count: int | None = None,
    has_char_card: bool = False,
    has_tail_ref: bool = False,
    extra_hdr: str = "",
    as_of_date: str | None = None,
    flow_tier: str = "paid",
    extend_sec: float | None = None,
) -> tuple[list[str], list[str]]:
    """按 Google Flow 官方能力表（含 free/paid 档位与 Extend 续写边界）与 2026-12-05 退役红线执行单段路由硬拦截"""
    eff_date = resolve_as_of_date(as_of_date)
    tier_norm = "free" if str(flow_tier or "").strip().lower() == "free" else "paid"
    errors: list[str] = []
    warnings: list[str] = []
    r_low = f"{model_route} {extra_hdr}".lower()
    m_low = str(video_mode or "").strip().lower()
    is_seedance = ("seedance" in r_low) or ("即梦" in r_low) or ("seedance_native" in m_low)
    if is_seedance:
        return errors, warnings

    is_omni_12 = ("omni" in r_low) and ("1.2" in r_low or "12" in r_low)
    is_omni_11 = ("omni" in r_low) and ("1.1" in r_low or "11" in r_low)
    is_veo_qual = ("veo" in r_low) and ("quality" in r_low)
    is_veo_fast = ("veo" in r_low) and ("fast" in r_low)
    is_veo_lite = ("veo" in r_low) and not (is_veo_qual or is_veo_fast or is_omni_11 or is_omni_12)
    is_ing = ("ingredients" in m_low) or ("ingredients" in r_low) or ("多图参考" in r_low)
    is_extend = (m_low == "extend") or ("extend" in m_low) or bool(re.search(r"\bextend\b", r_low))

    if is_veo_qual or is_veo_fast or is_omni_11:
        m_label = "Veo 3.1 Quality" if is_veo_qual else ("Veo 3.1 Fast" if is_veo_fast else "Omni Flash 1.1")
        if eff_date >= FLOW_SUNSET_DATE:
            errors.append(f"FLOW_MODEL_RETIRED_2026_12_05: {seg_id} 使用已于 {FLOW_SUNSET_DATE} 退役的模型 {m_label} (as_of_date={eff_date})")
        else:
            warnings.append(f"SUNSET_WARN_2026_12_05: {seg_id} 使用将于 {FLOW_SUNSET_DATE} 退役的 {m_label}，建议切换 Omni Flash 1.2 或 Veo 3.1 Lite")

    if is_veo_qual:
        if is_ing or has_char_card or shot_count > 1:
            errors.append(f"FLOW_UNSUPPORTED_VEO_QUALITY_INGREDIENTS: {seg_id} Veo 3.1 Quality 严禁 Ingredients/角色卡/多镜合并，仅支持 Text/Frames")
        if not any(abs(gen_dur - v) < 1e-3 for v in (4.0, 6.0, 8.0)):
            errors.append(f"VEO_INVALID_DURATION: {seg_id} Veo 3.1 Quality 时长={gen_dur}s 非法（仅限 4/6/8s）")
    elif is_veo_fast or is_veo_lite:
        if not any(abs(gen_dur - v) < 1e-3 for v in (4.0, 6.0, 8.0)):
            errors.append(f"VEO_INVALID_DURATION: {seg_id} Veo 3.1 时长={gen_dur}s 非法（仅限 4/6/8s）")
        elif abs(gen_dur - 4.0) < 1e-3 or abs(gen_dur - 6.0) < 1e-3:
            if is_ing or has_char_card:
                errors.append(f"VEO_4S_6S_INGREDIENTS_FORBIDDEN: {seg_id} ({int(round(gen_dur))}s Veo 3.1) 禁 Ingredients 或角色卡")
        elif abs(gen_dur - 8.0) < 1e-3 and is_ing and mounted_refs_count is not None and mounted_refs_count > 3:
            errors.append(f"VEO_INGREDIENTS_MAX_3_REFS_EXCEEDED: {seg_id} (8s Veo 3.1 Ingredients) 参考图数={mounted_refs_count} 超过上限 3 张")
    elif is_omni_11:
        if not any(abs(gen_dur - v) < 1e-3 for v in (4.0, 6.0, 8.0, 10.0)):
            errors.append(f"FLOW_INVALID_DURATION_OMNI_1_1: {seg_id} Omni Flash 1.1 时长={gen_dur}s 非法（仅限离散档位 4/6/8/10 秒）")
    elif is_omni_12:
        if is_extend:
            if tier_norm == "free":
                errors.append(f"FLOW_EXTEND_REQUIRES_PAID_SUBSCRIPTION: {seg_id} Omni Flash 1.2 Extend 续写模式仅限 paid 订阅可用 (flow_tier=free)")
            else:
                inc_s = float(extend_sec) if extend_sec is not None else float(gen_dur)
                if not (3.0 - 1e-6 <= inc_s <= 10.0 + 1e-6):
                    errors.append(f"FLOW_EXTEND_INVALID_INCREMENT_3_TO_10S: {seg_id} Omni Flash 1.2 Extend 单次续写增量={inc_s}s 越界（要求 3.0–10.0s）")
        elif tier_norm == "free":
            if gen_dur > 10.0 + 1e-6:
                errors.append(f"FLOW_OMNI_1_2_FREE_CAP_10S_EXCEEDED: {seg_id} Omni Flash 1.2 (flow_tier=free) 普通生成时长={gen_dur}s 超过免费上限 10.0s")
            elif gen_dur < 3.0 - 1e-6:
                errors.append(f"OMNI_INVALID_DURATION: {seg_id} Omni Flash 1.2 时长={gen_dur}s 低于 3.0s 下限")
        else:
            if gen_dur > 15.0 + 1e-6:
                errors.append(f"FLOW_OMNI_1_2_GEN_CAP_15S_EXCEEDED: {seg_id} Omni Flash 1.2 (flow_tier=paid) 普通生成时长={gen_dur}s 超过单次上限 15.0s")
            elif gen_dur < 3.0 - 1e-6:
                errors.append(f"OMNI_INVALID_DURATION: {seg_id} Omni Flash 1.2 时长={gen_dur}s 低于 3.0s 下限")
    return errors, warnings


def validate_cuts_rows_flow_routing(
    cuts_rows: list[dict[str, Any]],
    as_of_date: str | None = None,
    flow_tier: str = "paid",
) -> tuple[list[str], list[str]]:
    """对 cuts.csv 按段聚合校验 Google Flow 模型能力表（含 free/paid 与 Extend）与 2026-12-05 退役红线"""
    errors: list[str] = []
    warnings: list[str] = []
    by_seg: dict[str, list[dict[str, Any]]] = {}
    for r in cuts_rows:
        seg = str(r.get("segment") or "").strip()
        if seg:
            by_seg.setdefault(seg, []).append(r)

    for seg_id, s_rows in sorted(by_seg.items()):
        r0 = s_rows[0]
        route = str(r0.get("model_route") or "").strip()
        vmode = str(r0.get("video_mode") or "").strip()
        if not route and not vmode:
            continue
        act_dur = sum(max(0.0, float(x.get("end_sec") or 0.0) - float(x.get("start_sec") or 0.0)) for x in s_rows)
        raw_gd = str(r0.get("gen_duration_sec") or "").strip()
        try:
            gen_dur = float(raw_gd) if raw_gd else act_dur
        except Exception:
            gen_dur = act_dur
        raw_ext = str(r0.get("extend_sec") or "").strip()
        ext_sec: float | None = None
        if raw_ext:
            with contextlib.suppress(Exception):
                ext_sec = float(raw_ext)
        has_tail = any(str(x.get("need_tail_frame") or "").strip().lower() in ("1", "true", "yes") for x in s_rows)
        s_errs, s_warns = validate_flow_segment_rule(
            seg_id=seg_id,
            model_route=route,
            video_mode=vmode,
            gen_dur=gen_dur,
            actual_dur=act_dur,
            shot_count=len(s_rows),
            has_tail_ref=has_tail,
            as_of_date=as_of_date,
            flow_tier=flow_tier,
            extend_sec=ext_sec,
        )
        errors.extend(s_errs)
        warnings.extend(s_warns)
    return errors, warnings


def _parse_sec_range_token(raw_val: Any) -> tuple[float, float] | None:
    if isinstance(raw_val, (list, tuple)) and len(raw_val) == 2:
        with contextlib.suppress(Exception):
            return float(raw_val[0]), float(raw_val[1])
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~–至]\s*(\d+(?:\.\d+)?)\s*s?", str(raw_val or "").strip(), flags=re.IGNORECASE)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def _probe_video_duration_sec(vp: Path) -> float:
    if not vp.is_file() or vp.stat().st_size <= 1024:
        return 0.0
    with contextlib.suppress(Exception):
        pr = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(vp)],
            capture_output=True, text=True, timeout=10, check=True,
        )
        info = json.loads(pr.stdout or "{}")
        dur = float(info.get("format", {}).get("duration") or 0.0)
        if dur <= 0.0:
            vs = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
            dur = float(vs.get("duration") or 0.0)
        if dur > 0.1:
            return dur
    return 0.0


def infer_expected_cuts_range(
    cuts_p: Path,
    shots_json: Path | None,
    ordered_rows: list[dict[str, Any]],
    exp_start: float | None,
    exp_end: float | None,
    allow_self_span: bool = False,
    source_video: Path | None = None,
    skip_intro_sec: float = 0.0,
    skip_outro_sec: float = 0.0,
) -> tuple[float, float, str]:
    """仅接受 4 类独立可信区间源：① --expected-end-sec(>0) ② 磁盘源片/正片母本_CFR.mp4 ③ 已验真 phase1_colab_done.json ④ 分集索引.json"""
    if exp_end is not None and float(exp_end) > 0.0:
        s0 = float(exp_start) if exp_start is not None else 0.0
        if float(exp_end) > s0:
            return s0, float(exp_end), "explicit_args"

    m_ep = re.search(r"(第\d{2,3}集)", " ".join([cuts_p.name, *(p.name for p in cuts_p.parents[:3]), str(shots_json or "")]))
    ep_name = m_ep.group(1) if m_ep else ""
    proj_root: Path | None = None
    for anc in [cuts_p.parent, *cuts_p.parent.parents[:4]]:
        if (anc / "中间文件").is_dir() or (anc / "分集").is_dir():
            proj_root = anc
            break
    if not ep_name and proj_root is not None:
        ep_name = "第01集"

    sk_in = max(0.0, float(skip_intro_sec or 0.0))
    sk_out = max(0.0, float(skip_outro_sec or 0.0))
    if source_video is not None and Path(source_video).is_file():
        vd = _probe_video_duration_sec(Path(source_video))
        s0 = float(exp_start) if exp_start is not None else sk_in
        e0 = (s0 + vd - sk_out) if Path(source_video).name == "正片母本_CFR.mp4" and exp_start is not None else (vd - sk_out)
        if vd > 0.1 and e0 > s0:
            return s0, e0, Path(source_video).name

    done_cands = [cuts_p.parent / "phase1_colab_done.json"]
    if shots_json is not None:
        done_cands.append(Path(shots_json).parent / "phase1_colab_done.json")
    if proj_root and ep_name:
        done_cands.extend([
            proj_root / "中间文件" / "原片分析" / ep_name / "phase1_colab_done.json",
            proj_root / "中间文件" / "在制工作区" / ep_name / "phase1_colab_done.json",
        ])
    for dp in done_cands:
        if dp.is_file():
            with contextlib.suppress(Exception):
                d = read_json(dp)
                if isinstance(d, dict) and d.get("colab_cpu_verified") is True:
                    rng = _parse_sec_range_token(d.get("source_range") or d.get("source_range_sec") or d.get("effective_range_sec"))
                    sv = d.get("expected_start_sec", d.get("start_sec", d.get("episode_start_sec", rng[0] if rng else None)))
                    ev = d.get("expected_end_sec", d.get("end_sec", d.get("episode_end_sec", rng[1] if rng else None)))
                    if sv is None and ev is None and d.get("duration_sec") is not None and float(d["duration_sec"]) > 0.0:
                        sv, ev = 0.0, float(d["duration_sec"])
                    s_eff = float(exp_start) if exp_start is not None else (float(sv) if sv is not None else 0.0)
                    if ev is not None and float(ev) > s_eff:
                        return s_eff, float(ev), "phase1_colab_done.json"

    idx_cands = [cuts_p.parent / "分集索引.json", cuts_p.parent.parent / "分集索引.json"]
    if proj_root:
        idx_cands.insert(0, proj_root / "中间文件" / "索引与制作记录" / "分集索引.json")
    for ep_idx_p in idx_cands:
        if ep_idx_p.is_file() and ep_name:
            with contextlib.suppress(Exception):
                raw_idx = read_json(ep_idx_p)
                eps_obj = raw_idx.get("episodes") if isinstance(raw_idx, dict) else None
                ep_info = (
                    eps_obj.get(ep_name) if isinstance(eps_obj, dict)
                    else next((x for x in eps_obj if isinstance(x, dict) and x.get("episode") == ep_name), None) if isinstance(eps_obj, list)
                    else raw_idx.get(ep_name) if isinstance(raw_idx, dict) else None
                )
                if isinstance(ep_info, dict):
                    rng = _parse_sec_range_token(ep_info.get("source_range_sec") or ep_info.get("source_range"))
                    sv = ep_info.get("start_sec", ep_info.get("expected_start_sec", rng[0] if rng else None))
                    ev = ep_info.get("end_sec", ep_info.get("expected_end_sec", rng[1] if rng else None))
                    if sv is not None and ev is not None and float(ev) > float(sv):
                        s_eff = float(exp_start) if exp_start is not None else float(sv)
                        if float(ev) > s_eff:
                            return s_eff, float(ev), "分集索引.json"

    vid_cands: list[Path] = [cuts_p.parent / "正片母本_CFR.mp4"]
    if shots_json is not None:
        vid_cands.append(Path(shots_json).parent / "正片母本_CFR.mp4")
    if proj_root and ep_name:
        vid_cands.extend([
            proj_root / "中间文件" / "原片分析" / ep_name / "正片母本_CFR.mp4",
            proj_root / "中间文件" / "在制工作区" / ep_name / "正片母本_CFR.mp4",
        ])
        raw_vdir = proj_root / "原视频"
        if raw_vdir.is_dir():
            ep_vids = [p for p in sorted(raw_vdir.iterdir()) if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".mov") and ep_name in p.stem]
            all_vids = [p for p in sorted(raw_vdir.iterdir()) if p.is_file() and p.suffix.lower() in (".mp4", ".mkv", ".mov")]
            vid_cands.extend(ep_vids if ep_vids else (all_vids if len(all_vids) == 1 else []))
    for vp in vid_cands:
        dur = _probe_video_duration_sec(vp)
        if dur > 0.1:
            s_eff = float(exp_start) if exp_start is not None else sk_in
            raw_e = (s_eff + dur - sk_out) if (vp.name == "正片母本_CFR.mp4" and exp_start is not None) else (dur - sk_out)
            e_eff = min(s_eff + 360.0, raw_e) if (vp.name != "正片母本_CFR.mp4" and raw_e - s_eff > 365.0) else raw_e
            if e_eff > s_eff:
                return s_eff, e_eff, vp.name

    if allow_self_span:
        max_row_e = max((float(x["end_sec"]) for x in ordered_rows), default=0.0)
        e_dbg = max_row_e
        if shots_json is not None and Path(shots_json).is_file():
            with contextlib.suppress(Exception):
                sr = read_json(Path(shots_json))
                s_list = sr if isinstance(sr, list) else (sr.get("shots") or [])
                if s_list:
                    e_dbg = max(max_row_e, max(float(x["end_sec"]) for x in s_list))
        s_dbg = float(exp_start) if exp_start is not None else (0.0 if e_dbg > 0 else float(ordered_rows[0]["start_sec"]))
        return s_dbg, float(e_dbg), "debug_self_span"
    return 0.0, 0.0, "MISSING_TRUSTED_SOURCE_SPAN"


def verify_cuts_csv_v41(
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
) -> dict[str, Any]:
    """V4.1/V4.2 cuts.csv 独立可信源覆盖率 (>=99.5%)、缺口 (<=0.50s) 与 Google Flow 档位/续写硬门禁"""
    cuts_p = Path(cuts_csv)
    if not cuts_p.is_file() or cuts_p.stat().st_size == 0:
        return {"ok": False, "error": "VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV", "error_code": "VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV", "errors": [f"VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV ({cuts_p})"]}
    with cuts_p.open("r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {"ok": False, "error": "VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV", "error_code": "VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV", "errors": [f"VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV ({cuts_p})"]}

    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    eff_tier = resolve_flow_tier(flow_tier, cuts_p, shots_json)
    flow_errs, sunset_warnings = validate_cuts_rows_flow_routing(rows, as_of_date=as_of_date, flow_tier=eff_tier)
    errors.extend(flow_errs)
    seg_groups: dict[str, list[dict[str, Any]]] = {}
    for idx, r in enumerate(rows, 1):
        try:
            s_t, e_t = float(r.get("start_sec", "")), float(r.get("end_sec", ""))
        except Exception:
            errors.append(f"VERIFY_CUTS_FAIL: INVALID_TIME_FORMAT at row {idx}")
            continue
        if e_t <= s_t:
            errors.append(f"VERIFY_CUTS_FAIL: INVALID_TIME_RANGE at row {idx} (start={s_t}, end={e_t})")
        seg = str(r.get("segment") or "").strip()
        scene = str(r.get("scene") or "").strip()
        it = {"row": idx, "segment": seg, "scene": scene, "start_sec": s_t, "end_sec": e_t}
        parsed.append(it)
        if seg:
            seg_groups.setdefault(seg, []).append(it)

    if not parsed:
        return {"ok": False, "error_code": "VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV", "errors": errors or ["VERIFY_CUTS_FAIL: EMPTY_CUTS_CSV"]}

    ordered = sorted(parsed, key=lambda x: (x["start_sec"], x["end_sec"]))
    max_internal_gap = 0.0
    running_end = ordered[0]["end_sec"]
    for i in range(len(ordered) - 1):
        cur_r, nxt_r = ordered[i], ordered[i + 1]
        pair_gap = round(nxt_r["start_sec"] - cur_r["end_sec"], 4)
        true_gap = round(max(0.0, nxt_r["start_sec"] - running_end), 4)
        max_internal_gap = max(max_internal_gap, true_gap)
        if pair_gap < -0.05 or true_gap > max_gap:
            errors.append(
                f"VERIFY_CUTS_FAIL: TIME_GAP_OR_OVERLAP between row {cur_r['row']} ({cur_r['end_sec']}) "
                f"and row {nxt_r['row']} ({nxt_r['start_sec']}): pair_gap={pair_gap}s, internal_gap={true_gap}s (allowed -0.05..{max_gap}s)"
            )
        running_end = max(running_end, nxt_r["end_sec"])

    for seg, items in seg_groups.items():
        if len(items) > 3:
            errors.append(f"VERIFY_CUTS_FAIL: TOO_MANY_SHOTS_IN_SEGMENT {seg} has {len(items)} shots (> 3)")
        if len(items) > 1:
            scenes = [it["scene"] for it in items]
            if any(not sc for sc in scenes) or len(set(scenes)) > 1:
                errors.append(f"VERIFY_CUTS_FAIL: CROSS_SCENE_OR_EMPTY_SCENE_MERGE in {seg}: scenes={scenes}")

    exp_s, exp_e, rng_src = infer_expected_cuts_range(
        cuts_p,
        shots_json,
        ordered,
        expected_start,
        expected_end,
        allow_self_span=allow_self_span,
        source_video=source_video,
        skip_intro_sec=skip_intro_sec,
        skip_outro_sec=skip_outro_sec,
    )
    if rng_src == "MISSING_TRUSTED_SOURCE_SPAN":
        msg = "VERIFY_CUTS_MISSING_TRUSTED_SOURCE_SPAN: 缺少独立可信正片区间源（需 --expected-end-sec、源片/正片母本_CFR.mp4、已验证 phase1_colab_done.json 或 分集索引.json）"
        errors.append(msg)
        return {
            "ok": False,
            "error": "VERIFY_CUTS_MISSING_TRUSTED_SOURCE_SPAN",
            "error_code": "VERIFY_CUTS_MISSING_TRUSTED_SOURCE_SPAN",
            "errors": errors,
            "rows_count": len(parsed),
            "segments_count": len(seg_groups),
            "range_source": rng_src,
            "flow_tier": eff_tier,
            "sunset_warnings": sunset_warnings,
        }

    expected_span = max(0.001, exp_e - exp_s)
    head_leak = round(max(0.0, ordered[0]["start_sec"] - exp_s), 4)
    tail_leak = round(max(0.0, exp_e - running_end), 4)
    if head_leak > max_gap:
        errors.append(f"VERIFY_CUTS_FAIL: HEAD_LEAK_EXCEEDED first_start={ordered[0]['start_sec']}s > exp_start={exp_s}s + {max_gap}s (leak={head_leak}s)")
    if tail_leak > max_gap:
        errors.append(f"VERIFY_CUTS_FAIL: TAIL_LEAK_EXCEEDED last_end={running_end}s < exp_end={exp_e}s - {max_gap}s (leak={tail_leak}s)")

    clipped = sorted((max(exp_s, x["start_sec"]), min(exp_e, x["end_sec"])) for x in ordered if min(exp_e, x["end_sec"]) > max(exp_s, x["start_sec"]))
    merged_ivs: list[list[float]] = []
    for cs, ce in clipped:
        if not merged_ivs or cs > merged_ivs[-1][1]:
            merged_ivs.append([cs, ce])
        else:
            merged_ivs[-1][1] = max(merged_ivs[-1][1], ce)
    union_len = sum(iv[1] - iv[0] for iv in merged_ivs)
    coverage = union_len / expected_span
    if coverage < float(min_coverage):
        errors.append(f"VERIFY_CUTS_FAIL: COVERAGE_TOO_LOW coverage={coverage:.4f} ({union_len:.2f}s / {expected_span:.2f}s) < min_coverage={min_coverage:.4f}")

    err_first = errors[0].split(":", 1)[0].strip() if errors else "OK"
    return {
        "ok": len(errors) == 0,
        "error": err_first,
        "error_code": err_first,
        "errors": errors,
        "rows_count": len(parsed),
        "segments_count": len(seg_groups),
        "expected_start": round(exp_s, 3),
        "expected_end": round(exp_e, 3),
        "expected_span": round(expected_span, 3),
        "range_source": rng_src,
        "flow_tier": eff_tier,
        "sunset_warnings": sunset_warnings,
        "head_leak_sec": head_leak,
        "tail_leak_sec": tail_leak,
        "max_internal_gap_sec": max_internal_gap,
        "covered_union_len": round(union_len, 3),
        "covered_duration": round(union_len, 3),
        "coverage": round(coverage, 4),
        "coverage_ratio": round(coverage, 4),
    }


def _infer_work_and_proj_for_sheet(
    args: argparse.Namespace,
    refs_dir: Path | None,
    chars_dir: Path | None,
    out_path: Path | None,
) -> tuple[Path, Path | None, str]:
    ep_raw = str(getattr(args, "episode", "") or "").strip()
    joined_paths = " ".join(str(p or "") for p in (getattr(args, "work_dir", ""), refs_dir, chars_dir, out_path))
    m_ep = re.search(r"(第\d{2,3}集)", ep_raw or joined_paths)
    ep = m_ep.group(1) if m_ep else "第01集"

    proj_raw = getattr(args, "project_dir", "") or getattr(args, "project_root", "")
    proj_root: Path | None = Path(proj_raw).resolve() if proj_raw else None
    if proj_root is None:
        for base in [p for p in (chars_dir, refs_dir, out_path) if p is not None]:
            cur = base if base.is_dir() else base.parent
            for anc in [cur, *cur.parents[:5]]:
                if (anc / "中间文件").is_dir() or (anc / "分集").is_dir():
                    proj_root = anc.resolve()
                    break
            if proj_root is not None:
                break

    w_raw = str(getattr(args, "work_dir", "") or "").strip()
    if w_raw:
        work_dir = Path(w_raw).resolve()
    elif out_path is not None and ((out_path.parent / "待生图帧清单.json").is_file() or out_path.parent.parent.name == "在制工作区"):
        work_dir = out_path.parent.resolve()
    elif proj_root is not None and (proj_root / "中间文件" / "在制工作区" / ep).is_dir():
        work_dir = (proj_root / "中间文件" / "在制工作区" / ep).resolve()
    elif proj_root is not None and (proj_root / "中间文件").is_dir():
        work_dir = (proj_root / "中间文件" / "在制工作区" / ep).resolve()
    else:
        work_dir = (out_path.parent if out_path is not None else Path.cwd()).resolve()
    return work_dir, proj_root, ep


def _discover_roster_roles(
    spec_json_p: Path | None,
    refs_dir: Path | None,
    chars_dir: Path | None,
    work_dir: Path,
    proj_root: Path | None,
    ep: str,
    is_char_job_fn,
) -> list[dict[str, str]]:
    roles_by_short: dict[str, dict[str, str]] = {}

    def _reg(raw_key: str, desc: str = "") -> None:
        rk = re.sub(r"_(?:历史真容|原型照?|原型|初始|战损|晚年|青年)?_?(?:同向朝右|肖像|主体|portrait|subject)?$", "", Path(str(raw_key or "").strip()).stem)
        rk = re.sub(r"_(?:肖像|主体|历史真容|原型照?|原型).*$", "", rk).strip()
        if not rk or any(x in rk.lower() for x in ("sheet", "对照表", "contact")):
            return
        short = re.sub(r"^角色\d+_", "", rk).strip() or rk
        cur = roles_by_short.get(short)
        if cur is None:
            roles_by_short[short] = {"role_key": rk, "short_name": short, "desc": desc[:120]}
        else:
            if rk.startswith("角色") and not cur["role_key"].startswith("角色"):
                cur["role_key"] = rk
            if desc and not cur["desc"]:
                cur["desc"] = desc[:120]

    if spec_json_p is not None and spec_json_p.is_file():
        s_raw = read_json(spec_json_p)
        s_items = s_raw if isinstance(s_raw, list) else (s_raw.get("items") or s_raw.get("characters") or list(s_raw.keys()))
        if isinstance(s_items, dict):
            for k, v in s_items.items():
                _reg(str(v.get("new_name") or k) if isinstance(v, dict) else str(k), str(v) if isinstance(v, (str, dict)) else "")
        else:
            for it in s_items:
                if isinstance(it, dict):
                    _reg(str(it.get("role_key") or it.get("out_name") or it.get("new_name") or it.get("name") or ""), str(it.get("desc") or it.get("persona") or ""))
                else:
                    _reg(str(it))
        return list(roles_by_short.values())

    manifest_p = work_dir / "待生图帧清单.json"
    if manifest_p.is_file():
        with contextlib.suppress(Exception):
            for it in _extract_manifest_items_generic(read_json(manifest_p)):
                uid = _unit_id_generic(it)
                if is_char_job_fn(it, uid):
                    tp = str(it.get("target_path") or it.get("formal_path") or uid)
                    _reg(Path(tp).stem, str(it.get("character") or it.get("summary") or it.get("prompt") or ""))

    cand_cast_files = [work_dir / "style_and_cast.json"]
    if proj_root is not None:
        cand_cast_files.extend([
            proj_root / "中间文件" / "索引与制作记录" / "角色改名与音色映射.json",
            proj_root / "分集" / ep / "style_and_cast.json",
        ])
    for cf in cand_cast_files:
        if cf.is_file():
            with contextlib.suppress(Exception):
                c_obj = (read_json(cf) or {}).get("characters") or {}
                if isinstance(c_obj, dict):
                    for k, v in c_obj.items():
                        if isinstance(v, dict):
                            rid = str(v.get("role_id") or "").strip()
                            nn = str(v.get("new_name") or k).strip()
                            _reg(f"{rid}_{nn}" if rid and not nn.startswith(rid) else nn, str(v.get("persona") or v.get("appearance") or ""))
                        else:
                            _reg(str(k), str(v))

    for d_scan in [chars_dir, refs_dir]:
        if d_scan is not None and d_scan.is_dir():
            for p in sorted(d_scan.iterdir()):
                if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    _reg(p.stem)

    batches_root = work_dir / "生成批次"
    if not batches_root.is_dir() and proj_root is not None:
        batches_root = proj_root / "中间文件" / "在制工作区" / ep / "生成批次"
    if batches_root.is_dir():
        for bdir in sorted(batches_root.glob("批次*")):
            if not bdir.is_dir():
                continue
            for udir in sorted(bdir.iterdir()):
                if udir.is_dir() and (udir.name.endswith("_肖像") or udir.name.endswith("_主体")):
                    _reg(udir.name)
    return list(roles_by_short.values())


def _make_dark_role_spec_card(role_key: str, short_name: str, desc: str, font, w: int = 628, h: int = 350):
    from PIL import Image, ImageDraw
    card = Image.new("RGB", (w, h), "#0f172a")
    d = ImageDraw.Draw(card)
    d.rectangle((4, 4, w - 5, h - 5), outline="#38bdf8", width=3)
    d.rectangle((4, 4, w - 5, 58), fill="#1e293b", outline="#38bdf8", width=2)
    d.rectangle((18, 72, 190, 96), fill="#0284c7")
    d.rectangle((202, 72, 360, 96), fill="#eab308")
    d.rectangle((372, 72, w - 18, 96), fill="#f8fafc")
    d.text((18, 16), f"[角色设定规格卡] {role_key}", font=font, fill="#f8fafc")
    d.text((18, 114), f"角色名: {short_name} (无历史/真人原照 · 免原型锁脸)", font=font, fill="#e2e8f0")
    spec_txt = (desc or "原创戏剧角色 · 严守 #FFFFFF 纯白底 + 16:9 横屏 + 统一正面正视骨相基准").replace("\n", " ")
    for idx, chunk in enumerate([spec_txt[i : i + 26] for i in range(0, min(len(spec_txt), 104), 26)]):
        d.text((18, 156 + idx * 34), chunk, font=font, fill="#94a3b8")
    return card


def run_prototype_sheet_v41(args: argparse.Namespace, check_white_bg_fn, is_char_job_fn) -> None:
    """V4.1 两阶原型/设定对照表：支持 --stage auto|wave1|wave2 及无原型新角色自动绘制深色规格卡"""
    import numpy as np  # type: ignore
    from PIL import Image, ImageDraw, ImageFont

    refs_raw = str(getattr(args, "refs_dir", "") or "").strip()
    chars_raw = str(getattr(args, "chars_dir", "") or "").strip()
    out_raw = str(getattr(args, "output", "") or "").strip()
    refs_dir = Path(refs_raw).resolve() if refs_raw else None
    chars_dir = Path(chars_raw).resolve() if chars_raw else None
    out_path = Path(out_raw).resolve() if out_raw else None

    work_dir, proj_root, ep = _infer_work_and_proj_for_sheet(args, refs_dir, chars_dir, out_path)
    if chars_dir is None:
        c1 = (proj_root / "人物参考") if proj_root else (work_dir / "人物")
        c2 = (proj_root / "分集" / ep / "人物") if proj_root else c1
        chars_dir = c1 if (c1.is_dir() and any(c1.glob("*.png"))) else c2
    if refs_dir is None:
        refs_dir = (proj_root / "中间文件" / "原片分析" / ep / "prototype_refs") if proj_root else work_dir

    spec_p = Path(args.spec_json).resolve() if getattr(args, "spec_json", "") else None
    role_entries = _discover_roster_roles(spec_p, refs_dir, chars_dir, work_dir, proj_root, ep, is_char_job_fn)
    if not role_entries:
        print("PROTOTYPE_SHEET_INCOMPLETE | 未发现任何在册角色条目", file=sys.stderr)
        sys.exit(2)

    def _find_char_img(directory: Path | None, role_k: str, short_n: str, suffixes: tuple[str, ...]) -> Path | None:
        if directory is None or not directory.is_dir():
            return None
        files = [
            p for p in sorted(directory.iterdir())
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg") and not any(x in p.stem.lower() for x in ("sheet", "对照表", "contact"))
        ]
        for key_tok in (role_k, short_n):
            if not key_tok:
                continue
            for f in files:
                if (f.stem.startswith(key_tok) or key_tok in f.stem) and any(
                    (f.stem.endswith(sf) if sf else ("_肖像" not in f.stem and "_主体" not in f.stem)) for sf in suffixes
                ):
                    return f
        return None

    def _find_char_img_in_batches(role_k: str, short_n: str, suffixes: tuple[str, ...]) -> Path | None:
        batches_root = work_dir / "生成批次"
        if not batches_root.is_dir() and proj_root is not None:
            batches_root = proj_root / "中间文件" / "在制工作区" / ep / "生成批次"
        if not batches_root.is_dir():
            return None
        bdirs = [p for p in batches_root.glob("批次*") if p.is_dir()]
        bdirs.sort(key=lambda p: (tuple(int(n) for n in re.findall(r"\d+", p.name)) or (0,), p.name), reverse=True)
        for bdir in bdirs:
            udirs = [u for u in sorted(bdir.iterdir()) if u.is_dir()]
            for key_tok in (role_k, short_n):
                if not key_tok:
                    continue
                for udir in udirs:
                    uname = udir.name
                    if not (uname.startswith(key_tok) or key_tok in uname):
                        continue
                    if not any((uname.endswith(sf) or sf in uname) for sf in suffixes if sf):
                        continue
                    cands = [udir / "补救.png", *sorted(udir.glob("补救*.png"), reverse=True), udir / "初始.png"]
                    for cand in cands:
                        if cand.is_file() and cand.stat().st_size > 0:
                            return cand
        return None

    def _find_char_img_with_fallback(role_k: str, short_n: str, suffixes: tuple[str, ...]) -> Path | None:
        search_dirs: list[Path | None] = [chars_dir]
        if proj_root is not None:
            for alt_dir in (proj_root / "分集" / ep / "人物", proj_root / "人物参考"):
                if alt_dir not in search_dirs:
                    search_dirs.append(alt_dir)
        for sdir in search_dirs:
            found = _find_char_img(sdir, role_k, short_n, suffixes)
            if found is not None:
                return found
        return _find_char_img_in_batches(role_k, short_n, suffixes)

    req_stage = str(getattr(args, "stage", "auto") or "auto").strip().lower()
    if req_stage == "auto":
        has_any_subj = any(_find_char_img_with_fallback(r["role_key"], r["short_name"], ("_主体",)) is not None for r in role_entries)
        eff_stage = "wave2" if has_any_subj else "wave1"
    else:
        eff_stage = req_stage

    if out_path is None:
        out_path = work_dir / f"prototype_sheet_{eff_stage}.jpg"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 20)
    except Exception:
        font = ImageFont.load_default()

    verified_roles: list[dict[str, Any]] = []
    rendered_rows: list[tuple[str, list[tuple[str, Image.Image]]]] = []
    for r_ent in role_entries:
        rk, sn, desc = r_ent["role_key"], r_ent["short_name"], r_ent["desc"]
        ref_p = _find_char_img(refs_dir, rk, sn, ("_历史真容_同向朝右", "_原型_同向朝右", "_历史真容", "_原型", ""))
        if ref_p is not None and ref_p.is_file():
            with Image.open(ref_p) as rim:
                col1_im = rim.convert("RGB")
            if float(np.asarray(col1_im.convert("L"), dtype=np.float32).std()) < 8.0:
                print(f"PROTOTYPE_SHEET_INCOMPLETE | 角色 {rk} 原型照低对比度 (<8.0)", file=sys.stderr)
                sys.exit(2)
            ref_mode = str(ref_p).replace("\\", "/")
        else:
            col1_im = _make_dark_role_spec_card(rk, sn, desc, font)
            ref_mode = "GENERATED_ROLE_SPEC_CARD"

        req_cols = [("生成_肖像.png", _find_char_img_with_fallback(rk, sn, ("_肖像",)))]
        if eff_stage == "wave2":
            req_cols.append(("生成_主体.png", _find_char_img_with_fallback(rk, sn, ("_主体",))))

        row_cells: list[tuple[str, Image.Image]] = [("原型照/设定规格卡", col1_im)]
        role_rec: dict[str, Any] = {"role_key": rk, "short_name": sn, "col1_source": ref_mode}
        for cname, cp in req_cols:
            if cp is None or not cp.is_file():
                print(f"PROTOTYPE_SHEET_INCOMPLETE | stage={eff_stage} 角色 {rk} 缺失 {cname} (dir={chars_dir})", file=sys.stderr)
                sys.exit(2)
            bg_chk = check_white_bg_fn(cp)
            if not bg_chk.get("compliant"):
                print(f"PROTOTYPE_SHEET_NON_WHITE_BG | 角色 {rk} {cname} 未通过纯白底门禁: {bg_chk}", file=sys.stderr)
                sys.exit(2)
            with Image.open(cp) as cim:
                c_rgb = cim.convert("RGB")
            g_std = float(np.asarray(c_rgb.convert("L"), dtype=np.float32).std())
            if g_std < 8.0:
                print(f"PROTOTYPE_SHEET_LOW_STD | 角色 {rk} {cname} 灰度 std={g_std:.2f} < 8.0", file=sys.stderr)
                sys.exit(2)
            row_cells.append((cname, c_rgb))
            role_rec[cname] = {"path": str(cp).replace("\\", "/"), "sha256": sha256_file(cp), "gray_std": round(g_std, 2)}
        verified_roles.append(role_rec)
        rendered_rows.append((rk, row_cells))

    n_cols = 3 if eff_stage == "wave2" else 2
    col_w, row_h = 640, 390
    canvas = Image.new("RGB", (n_cols * col_w, len(rendered_rows) * row_h), "#f4f4f4")
    draw = ImageDraw.Draw(canvas)
    for r_idx, (rk, cells) in enumerate(rendered_rows):
        for c_idx, (title, cell_im) in enumerate(cells):
            x, y = c_idx * col_w, r_idx * row_h
            draw.text((x + 6, y + 4), f"{rk} | {title}", font=font, fill="black")
            thumb = cell_im.copy()
            thumb.thumbnail((col_w - 12, row_h - 40))
            canvas.paste(thumb, (x + 6, y + 32))
    canvas.save(out_path, format="JPEG", quality=94)

    work_dir.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    ver_name = "wave2_consistency_verified.json" if eff_stage == "wave2" else "wave1_portrait_verified.json"
    ver_status = "WAVE2_CONSISTENCY_VERIFIED_OK" if eff_stage == "wave2" else "WAVE1_PORTRAIT_VERIFIED_OK"
    ver_p = work_dir / ver_name
    ver_payload = {
        "status": ver_status,
        "stage": eff_stage,
        "rows": len(rendered_rows),
        "cols": n_cols,
        "output": str(out_path).replace("\\", "/"),
        "verified_json": str(ver_p).replace("\\", "/"),
        "roles": verified_roles,
        "verified_at": now_iso,
    }
    write_json(ver_p, ver_payload)
    if eff_stage == "wave2" and not (work_dir / "wave1_portrait_verified.json").is_file():
        write_json(work_dir / "wave1_portrait_verified.json", {**ver_payload, "status": "WAVE1_PORTRAIT_VERIFIED_OK", "stage": "wave1"})
    print(json.dumps(ver_payload, ensure_ascii=False, indent=2))


_BANNED_AI_REVIEWERS_GUARD = (
    "neon", "carbon", "argon", "hydrogen", "flash", "gemini",
    "gpt", "codex", "antigravity", "claude", "opus", "sonnet",
    "orchestrator", "agent", "subagent", "bot", "auto", "llm",
)


def _resolve_ep_target_fps_guard(work_dir: Path, formal_ep_dir: Path, proj_root: Path | None, ep_name: str) -> int:
    audit_dir = (proj_root / "中间文件" / "索引与制作记录" / "交付自查状态") if proj_root else work_dir
    cand_jsons = [
        work_dir / "Colab_A100_超分任务清单.json",
        work_dir / "phase4_colab_vsr_done.json",
        audit_dir / f"{ep_name}_第四棒成片母带终检.json",
        formal_ep_dir / f"{ep_name}_第四棒成片母带终检.json",
    ]
    found_fps: set[int] = set()
    for jp in cand_jsons:
        if not jp.is_file() or jp.stat().st_size == 0:
            continue
        with contextlib.suppress(Exception):
            doc = read_json(jp)
            if isinstance(doc, dict):
                raw_v = doc.get("target_fps")
                if raw_v is not None and str(raw_v).strip() != "" and int(raw_v) in (24, 60):
                    found_fps.add(int(raw_v))
    return 60 if found_fps == {60} else 24


def _check_human_master_review_guard(audit_dir: Path, work_dir: Path, ep_name: str, cur_master_sha: str) -> tuple[bool, str, str]:
    exp_sha = str(cur_master_sha or "").strip().lower()
    cands = [audit_dir / f"{ep_name}_成片人工回看确认记录.json", work_dir / f"{ep_name}_成片人工回看确认记录.json"]
    default_p = str(cands[0]).replace("\\", "/")
    if not exp_sha:
        return False, default_p, ""
    for cp in cands:
        if not cp.is_file() or cp.stat().st_size == 0:
            continue
        with contextlib.suppress(Exception):
            doc = read_json(cp)
            if not isinstance(doc, dict):
                continue
            c_sha = str(doc.get("confirmed_master_sha256") or "").strip().lower()
            rev = str(doc.get("reviewer") or "").strip()
            rev_l = rev.lower()
            notes_len = len(re.sub(r"\s+", "", str(doc.get("notes") or "")))
            rev_ok = (
                len(re.sub(r"\s+", "", rev)) >= 2
                and rev_l not in {"none", "null", "n/a", "na", "unknown", "default", "todo", "tbd", "system", "model"}
                and not any(tok in rev_l for tok in _BANNED_AI_REVIEWERS_GUARD)
                and not re.search(r"(?:^|[^a-z0-9])ai(?:$|[^a-z0-9])", rev_l)
                and not any(cn in rev for cn in ("智能体", "子代理", "大模型", "机器人", "自动审核", "自动确认"))
            )
            three_ok = (
                doc.get("confirm_dialogue") is True
                and doc.get("confirm_lipsync") is True
                and doc.get("confirm_subtitles") is True
            )
            if c_sha == exp_sha and three_ok and rev_ok and notes_len >= 12:
                return True, str(cp).replace("\\", "/"), c_sha
    return False, default_p, ""


def evaluate_post_p2_phase_status(ep_dir: Path, proj_root: Path | None, ep_name: str, cuts_p: Path) -> tuple[str, dict[str, Any]]:
    """P2 完成后依次推进 P3_DELIVERY_VERIFY -> WAIT_HUMAN_VIDEO_DROP -> P4A_COLAB_VSR_AND_FCPXML -> P4B_MASTER_VERIFY -> WAIT_HUMAN_MASTER_REVIEW -> ALL_DONE"""
    expected_segs: set[str] = set()
    if cuts_p.is_file() and cuts_p.stat().st_size > 0:
        with contextlib.suppress(Exception), cuts_p.open("r", encoding="utf-8-sig") as f:
            expected_segs = {str(r.get("segment") or "").strip() for r in csv.DictReader(f) if str(r.get("segment") or "").strip()}

    audit_dir = (proj_root / "中间文件" / "索引与制作记录" / "交付自查状态") if proj_root else ep_dir
    work_dir = (proj_root / "中间文件" / "在制工作区" / ep_name) if proj_root else ep_dir
    formal_ep_dir = (proj_root / "分集" / ep_name) if proj_root else ep_dir

    p3_cands = [audit_dir / f"{ep_name}_第三棒静态九项核验.json", ep_dir / f"{ep_name}_第三棒静态九项核验.json"]
    p3_p = next((p for p in p3_cands if p.is_file()), p3_cands[0])
    p3_passed = False
    if p3_p.is_file():
        with contextlib.suppress(Exception):
            p3_passed = bool(read_json(p3_p).get("all_nine_deliverables_passed") is True)

    eff_fps = _resolve_ep_target_fps_guard(work_dir, formal_ep_dir, proj_root, ep_name)
    raw_vid_dirs = [
        work_dir / "原始生成视频", formal_ep_dir / "原始生成视频",
        work_dir / "已超分4K_24fps", formal_ep_dir / "已超分4K_24fps",
        work_dir / "已超分4K_60fps", formal_ep_dir / "已超分4K_60fps",
    ]
    dropped_segs: set[str] = set()
    for vd in raw_vid_dirs:
        if vd.is_dir():
            dropped_segs |= {p.stem for p in vd.glob("segment_*.mp4") if p.is_file() and p.stat().st_size > 0}
    human_drop_ready = (expected_segs.issubset(dropped_segs) if expected_segs else len(dropped_segs) > 0)

    vsr_dirs = [work_dir / f"已超分4K_{eff_fps}fps", formal_ep_dir / f"已超分4K_{eff_fps}fps"]
    vsr_segs: set[str] = set()
    for vd in vsr_dirs:
        if vd.is_dir():
            vsr_segs |= {p.stem for p in vd.glob("segment_*.mp4") if p.is_file() and p.stat().st_size > 0}
    fcpxml_p = formal_ep_dir / f"{ep_name}_达芬奇自动时间线.fcpxml"
    fcpxml_exists = fcpxml_p.is_file() and fcpxml_p.stat().st_size > 0
    p4a_ready = fcpxml_exists and (expected_segs.issubset(vsr_segs) if expected_segs else len(vsr_segs) > 0)

    p4b_cands = [audit_dir / f"{ep_name}_第四棒成片母带终检.json", formal_ep_dir / f"{ep_name}_第四棒成片母带终检.json", ep_dir / f"{ep_name}_第四棒成片母带终检.json"]
    p4b_p = next((p for p in p4b_cands if p.is_file()), p4b_cands[0])
    p4b_status, rep_master_sha, rep_fcpxml_sha, rep_master_path, rep_fps = "", "", "", "", None
    p4b_tech_spec = False
    if p4b_p.is_file():
        with contextlib.suppress(Exception):
            p4b_raw = read_json(p4b_p)
            if isinstance(p4b_raw, dict):
                p4b_status = str(p4b_raw.get("status") or p4b_raw.get("master_verify_status") or "").strip()
                p4b_tech_spec = bool(p4b_raw.get("technical_spec_passed") is True or p4b_status in ("MASTER_TECH_SPEC_PASSED", "MASTER_SEALED_OK"))
                rep_master_sha = str(p4b_raw.get("verified_master_sha256") or "").strip().lower()
                rep_fcpxml_sha = str(p4b_raw.get("verified_fcpxml_sha256") or "").strip().lower()
                rep_master_path = str(p4b_raw.get("master_mp4_path") or p4b_raw.get("verified_master_mp4") or "").strip()
                rep_fps = p4b_raw.get("target_fps")
    if not rep_master_path and (work_dir / "母带渲染后音画字幕核验记录.json").is_file():
        with contextlib.suppress(Exception):
            rep_master_path = str((read_json(work_dir / "母带渲染后音画字幕核验记录.json") or {}).get("verified_master_mp4") or "").strip()

    if rep_master_path:
        rp = Path(rep_master_path)
        master_mp4 = rp if (rp.is_absolute() or proj_root is None) else (proj_root / rp)
    else:
        m_fps = int(rep_fps) if str(rep_fps) in ("24", "60") else eff_fps
        master_mp4 = formal_ep_dir / f"{ep_name}_4K{m_fps}fps_成片.mp4"
    master_exists = master_mp4.is_file() and master_mp4.stat().st_size > 0
    fps_cfg_ok = (str(rep_fps) not in ("24", "60")) or (int(rep_fps) == eff_fps)

    cur_master_sha = sha256_file(master_mp4).lower() if master_exists else ""
    cur_fcpxml_sha = sha256_file(fcpxml_p).lower() if fcpxml_exists else ""
    master_sha_ok = bool(rep_master_sha) and bool(cur_master_sha) and (cur_master_sha == rep_master_sha)
    fcpxml_sha_ok = (not rep_fcpxml_sha) or (bool(cur_fcpxml_sha) and cur_fcpxml_sha == rep_fcpxml_sha)
    p4b_tech_passed = p4b_tech_spec and master_exists and fcpxml_exists and master_sha_ok and fcpxml_sha_ok and fps_cfg_ok
    human_review_ok, human_review_p, human_confirmed_sha = _check_human_master_review_guard(audit_dir, work_dir, ep_name, cur_master_sha)
    p4b_passed = p4b_tech_passed and human_review_ok

    if not p3_passed:
        stage = "P3_DELIVERY_VERIFY"
    elif not human_drop_ready:
        stage = "WAIT_HUMAN_VIDEO_DROP"
    elif not p4a_ready:
        stage = "P4A_COLAB_VSR_AND_FCPXML"
    elif not p4b_tech_passed:
        stage = "P4B_MASTER_VERIFY"
    elif not human_review_ok:
        stage = "WAIT_HUMAN_MASTER_REVIEW"
    else:
        stage = "ALL_DONE"

    reverify_req = (stage == "P4B_MASTER_VERIFY")
    p4b_gate: dict[str, Any] = {
        "path": str(p4b_p).replace("\\", "/"),
        "master_mp4_path": str(master_mp4).replace("\\", "/"),
        "master_mp4_exists": master_exists,
        "fcpxml_exists": fcpxml_exists,
        "status": p4b_status,
        "target_fps": eff_fps,
        "technical_spec_passed": p4b_tech_passed,
        "human_review_verified": human_review_ok,
        "human_review_record": human_review_p,
        "confirmed_master_sha256": human_confirmed_sha,
        "verified_master_sha256": rep_master_sha,
        "current_master_sha256": cur_master_sha,
        "master_sha_matched": master_sha_ok,
        "fcpxml_sha_matched": fcpxml_sha_ok,
        "passed": p4b_passed,
    }
    if reverify_req:
        p4b_gate["master_reverify_required"] = True
        p4b_gate["reason"] = "MASTER_MISSING_OR_SHA_MISMATCH"

    gates_out: dict[str, Any] = {
        "phase3_verify": {"path": str(p3_p).replace("\\", "/"), "passed": p3_passed},
        "human_video_drop": {"ready": human_drop_ready, "dropped_segments_count": len(dropped_segs), "expected_segments_count": len(expected_segs)},
        "phase4a_vsr_fcpxml": {"ready": p4a_ready, "fcpxml_path": str(fcpxml_p).replace("\\", "/"), "vsr_segments_count": len(vsr_segs)},
        "phase4b_master_verify": p4b_gate,
    }
    if reverify_req:
        gates_out["master_reverify_required"] = True
        gates_out["reason"] = "MASTER_MISSING_OR_SHA_MISMATCH"
    return stage, gates_out


def run_phase3_verify_v41(
    args: argparse.Namespace,
    probe_wav_fn,
    check_white_bg_fn,
    is_char_fn,
    is_scene_fn,
    norm_flat_fn,
) -> None:
    """V4.1 第三棒静态九项硬门禁：任一未达标打印报告并强制 sys.exit(2)（严禁在核验门禁内静默代跑补齐掩盖缺件）"""
    raw_root = getattr(args, "project_dir", "") or getattr(args, "project_root", "")
    if not raw_root:
        print("必须提供 --project-dir 或 --project-root", file=sys.stderr)
        sys.exit(1)
    project_root = Path(raw_root).resolve()
    ep_raw = str(args.episode).strip()
    m_ep = re.search(r"(\d+)", ep_raw)
    ep = f"第{int(m_ep.group(1)):02d}集" if (m_ep and not ep_raw.startswith("第")) else ep_raw

    ep_dir = project_root / "分集" / ep
    work_dir = project_root / "中间文件" / "在制工作区" / ep
    analysis_dir = project_root / "中间文件" / "原片分析" / ep
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
    kf_dir = ep_dir / "首尾及关键帧"
    raw_seg_dir = ep_dir / "原视频段落"

    md_sha_ok, no_placeholder_left = False, False
    if md_path.is_file():
        txt = md_path.read_text(encoding="utf-8-sig")
        no_placeholder_left = not any(p in txt for p in ("{{VISUAL_STYLE}}", "{{CHAR:", "【待填角色", "【待补角色造型】"))
        if master_md_path.is_file():
            md_sha_ok = (sha256_file(md_path) == sha256_file(master_md_path))

    roster = _discover_roster_roles(None, None, char_dir, work_dir, project_root, ep, is_char_fn)
    char_files = sorted(char_dir.glob("*.png")) if char_dir.is_dir() else []
    portrait_files = [f for f in char_files if f.stem.endswith("_肖像")]
    subject_files = [f for f in char_files if f.stem.endswith("_主体")]
    missing_char_sheets: list[str] = []
    for r in roster:
        sn, rk = r["short_name"], r["role_key"]
        if not any(sn in f.stem or rk in f.stem for f in portrait_files):
            missing_char_sheets.append(f"{rk}_肖像.png")
        if not any(sn in f.stem or rk in f.stem for f in subject_files):
            missing_char_sheets.append(f"{rk}_主体.png")
    non_white_chars = [f.name for f in (portrait_files + subject_files) if not check_white_bg_fn(f).get("compliant")]
    char_ok = bool(portrait_files) and bool(subject_files) and (len(portrait_files) == len(subject_files)) and not missing_char_sheets and not non_white_chars

    voice_samples = sorted(voice_dir.glob("*.wav")) if voice_dir.is_dir() else []
    voice_details = [probe_wav_fn(vp) for vp in voice_samples]
    voice_all_compliant = bool(voice_samples) and all(v.get("compliant") for v in voice_details)
    voice_unique = len({v.get("sha256") for v in voice_details if v.get("sha256")}) == len(voice_details)
    missing_role_voices: list[str] = []
    for r in roster:
        sn, rk = r["short_name"], r["role_key"]
        for v_tag in ("说话", "心声"):
            if not any((sn in vp.stem or rk in vp.stem) and v_tag in vp.stem for vp in voice_samples):
                missing_role_voices.append(f"{rk}_{v_tag}")
    voice_ok = len(voice_samples) >= max(2, 2 * len(roster)) and voice_all_compliant and voice_unique and not missing_role_voices

    cuts_cands = [analysis_dir / "cuts.csv", work_dir / "cuts.csv", ep_dir / "cuts.csv"]
    cuts_p = next((p for p in cuts_cands if p.is_file() and p.stat().st_size > 0), None)
    cuts_rows = list(csv.DictReader(cuts_p.open("r", encoding="utf-8-sig"))) if cuts_p else []
    expected_segs = sorted({str(r.get("segment") or "").strip() for r in cuts_rows if str(r.get("segment") or "").strip()})

    missing_required_frames: list[str] = []
    exempted_tail_frames: list[str] = []
    manifest_p = work_dir / "待生图帧清单.json"
    if manifest_p.is_file():
        for it in _extract_manifest_items_generic(read_json(manifest_p)):
            u = _unit_id_generic(it)
            if is_char_fn(it, u) or is_scene_fn(it, u):
                continue
            ftype = str(it.get("frame_type") or "")
            if (ftype == "尾帧" or u.endswith("_尾帧")) and it.get("need_tail_frame") is False:
                exempted_tail_frames.append(u)
                continue
            t_raw = str(it.get("target_path") or it.get("formal_path") or it.get("output_path") or "")
            if t_raw:
                nf = norm_flat_fn(t_raw, u, it)
                tp1 = Path(nf) if Path(nf).is_absolute() else (project_root / nf).resolve()
                tp2 = Path(t_raw) if Path(t_raw).is_absolute() else (project_root / t_raw).resolve()
                if not tp1.is_file() and not tp2.is_file():
                    missing_required_frames.append(u)
    for idx_r, r in enumerate(cuts_rows, 1):
        seg_s = str(r.get("segment") or "").strip()
        shot_s = str(r.get("shot") or "").strip() or f"镜头{idx_r:02d}"
        if seg_s and not (kf_dir / f"{seg_s}_{shot_s}_首帧.png").is_file():
            if f"{seg_s}_{shot_s}_首帧" not in missing_required_frames:
                missing_required_frames.append(f"{seg_s}_{shot_s}_首帧")
        if seg_s and str(r.get("need_tail_frame") or "").strip().lower() in ("1", "true", "yes"):
            if not (kf_dir / f"{seg_s}_{shot_s}_尾帧.png").is_file() and f"{seg_s}_{shot_s}_尾帧" not in missing_required_frames:
                missing_required_frames.append(f"{seg_s}_{shot_s}_尾帧")

    kf_ok = kf_dir.is_dir() and any(kf_dir.glob("*.png")) and (len(missing_required_frames) == 0)
    missing_raw_segs = [s for s in expected_segs if not (raw_seg_dir / f"{s}.mp4").is_file() or (raw_seg_dir / f"{s}.mp4").stat().st_size == 0]
    raw_seg_ok = raw_seg_dir.is_dir() and any(raw_seg_dir.glob("*.mp4")) and (len(missing_raw_segs) == 0)

    checks = {
        "1_人物目录_肖像与主体双白底图": char_ok,
        "2_声线目录_双音色2至3秒_48k单声道PCM": voice_ok,
        "3_视频提示词_已回填且主稿SHA一致": md_path.is_file() and no_placeholder_left and master_md_path.is_file() and md_sha_ok,
        "4_首尾及关键帧目录_按需尾帧合规": kf_ok,
        "5_分镜头脚本_xlsx": xlsx_path.is_file() and xlsx_path.stat().st_size > 0,
        "6_原视频段落目录": raw_seg_ok,
        "7_字幕时间轴_csv": csv_path.is_file() and csv_path.stat().st_size > 0,
        "8_台词生成表_xlsx与csv": dlg_table_xlsx.is_file() and dlg_table_xlsx.stat().st_size > 0 and dlg_table_csv.is_file() and dlg_table_csv.stat().st_size > 0,
        "9_剧本故事梗概与逐镜导演脚本_md": director_md_path.is_file() and director_md_path.stat().st_size > 100,
    }
    all_passed = all(checks.values())
    report = {
        "status": "PHASE3_VERIFY_OK" if all_passed else "PHASE3_VERIFY_FAILED",
        "episode": ep,
        "all_nine_deliverables_passed": all_passed,
        "all_seven_deliverables_passed": all(v for k, v in checks.items() if not k.startswith("9_")),
        "deliverable_checks": checks,
        "character_dual_sheet_audit": {
            "total_character_pngs": len(char_files),
            "portrait_count": len(portrait_files),
            "subject_count": len(subject_files),
            "missing_character_sheets": missing_char_sheets,
            "non_white_bg_files": non_white_chars,
        },
        "keyframes_audit": {
            "missing_required_frames": missing_required_frames,
            "exempted_tail_frames_count": len(exempted_tail_frames),
        },
        "raw_segments_audit": {"expected_segments": len(expected_segs), "missing_segments": missing_raw_segs},
        "voice_audit": {
            "wav_count": len(voice_samples),
            "all_compliant_2_to_3s_48k_pcm16": voice_all_compliant,
            "all_voices_distinct": voice_unique,
            "missing_role_voices": missing_role_voices,
        },
    }
    write_json(audit_dir / f"{ep}_第三棒静态九项核验.json", report)
    write_json(audit_dir / f"{ep}_第三棒静态七项核验.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not all_passed:
        sys.exit(2)


def register_v41_subparsers(sub) -> None:
    """向 batch_ops.py 挂载 V4.1 新增 CLI 子命令：stamp-visual-audit / quota-reserve / quota-reconcile"""
    p_stamp = sub.add_parser("stamp-visual-audit", help="V4.1 双盲审防伪盖戳：计算并绑定实时 audited_prompt_sha256、audited_refs_sha256 与跨实例 run_id")
    p_stamp.add_argument("--audit-json", required=True, help="盲审分片或总表 JSON 路径")
    p_stamp.add_argument("--manifest", "--frames-json", dest="manifest", default="", help="可选：待生图帧清单.json 路径")
    p_stamp.add_argument("--project-dir", "--project-root", dest="project_dir", default="", help="可选：项目根目录路径")
    p_stamp.add_argument("--episode", "--ep", dest="episode", default="第01集", help="可选：正式集名称")
    p_stamp.add_argument("--author-agent", default="", help="可选：执笔模型标识")
    p_stamp.add_argument("--inspector-agent", default="", help="可选：盲审模型标识")
    p_stamp.add_argument("--author-run-id", default="", help="可选：执笔实例运行 ID")
    p_stamp.add_argument("--inspector-run-id", default="", help="可选：盲审实例运行 ID")
    p_stamp.set_defaults(func=cmd_stamp_visual_audit)

    p_qres = sub.add_parser("quota-reserve", help="V4.1 额度闸门：调用生图前通过 .ledger.lock 原子预占窗口配额（硬上限 60 / 429 冷却阻断 exit 4）")
    p_qres.add_argument("--work-dir", required=True, help="分集在制工作区目录")
    p_qres.add_argument("--window", default="window_01", help="窗口 ID，默认 window_01")
    p_qres.add_argument("--unit-id", "--unit", dest="unit_id", required=True, help="生图单元 ID")
    p_qres.add_argument("--kind", choices=["initial", "remedy"], default="initial", help="调用类型：initial 或 remedy")
    p_qres.add_argument("--hard-cap", default=V41_HARD_CAP_CALLS, type=int, help="单窗口硬上限，默认 60")
    p_qres.set_defaults(func=cmd_quota_reserve)

    p_qrec = sub.add_parser("quota-reconcile", help="V4.2 额度幂等核销：按预占单号记录 ok/429/timeout/error 并在 429 时写入跨窗口全局冷却锁")
    p_qrec.add_argument("--work-dir", required=True, help="分集在制工作区目录")
    p_qrec.add_argument("--window", default="window_01", help="窗口 ID，默认 window_01")
    p_qrec.add_argument("--reservation-id", default="", help="quota-reserve 返回的预占单号")
    p_qrec.add_argument("--unit-id", "--unit", dest="unit_id", default="", help="或按生图单元 ID 匹配最新 pending 预占单")
    p_qrec.add_argument("--outcome", type=lambda s: str(s).strip().lower(), choices=["ok", "429", "timeout", "error"], required=True, help="本次调用结果")
    p_qrec.add_argument("--retry-after-sec", default=0.0, type=float, help="429 Retry-After 秒数（默认未传时保护性冷却 60s）")
    p_qrec.set_defaults(func=cmd_quota_reconcile)
