# -*- coding: utf-8 -*-
"""h3出片直喂打包器：将用户零散提供的提示词、故事板、人物图、场景图、音频一键整理为标准工程与 MiniMax-H3 任务包。

特点：
  1) 无需手写 series.json / episode.json，自动落盘完全兼容 ai-anime-series (review_clip.py / compile.py) 的目录结构。
  2) 硬校验图片/音频存在性、数量上限（<=9图、<=3音频）、禁止残留 @图N 语法、禁止 <Picture K>/<Audio M> 越界。
  3) 支持单段（4格 10-15s）及多段追加（--multi-part --part A / --part B --append）合并入同一 manifest.json，一次 Colab 启动连生多段。
  4) 默认采用无损满血 BF16 (h200-max, ref_image_size=max, 768p @ 24fps)。

示例：
  python pack_direct_job.py ^
    --out-dir "<WORKSPACE_ROOT>/h3_projects/demo" ^
    --episode ep01 ^
    --prompt-file "<WORKSPACE_ROOT>/h3_projects/demo/ep01/raw_h3.txt" ^
    --ref "小熊波波=<ASSETS_DIR>/bobo.png" ^
    --ref "小兔朵朵=<ASSETS_DIR>/duoduo.png" ^
    --storyboard "<ASSETS_DIR>/board_4grid.png" ^
    --duration 12 --ratio 16:9 --resolution 768p --profile h200-max
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from pathlib import Path
from typing import Any

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from make_h3_jobs import (  # noqa: E402
    FPS,
    MAX_H3_AUDIOS,
    MAX_H3_REFS,
    MAX_H3_SEG_SEC,
    PROFILES,
    RESOLUTIONS,
    TURBO_LORA,
    align_h3_frames,
    build_comfyui_api_graph,
    validate_h3_prompt,
)


def parse_named_path(raw: str, default_prefix: str, idx: int) -> tuple[str, Path]:
    if "=" in raw:
        name, path_str = raw.split("=", 1)
        name = name.strip() or f"{default_prefix}{idx}"
        return name, Path(path_str.strip()).expanduser().resolve()
    p = Path(raw.strip()).expanduser().resolve()
    return p.stem or f"{default_prefix}{idx}", p


def safe_filename(name: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")
    return cleaned or fallback


def copy_if_needed(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dst.resolve():
        return
    shutil.copy2(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description="将零散故事板+人物图+H3提示词一键打包为标准 MiniMax-H3 任务包")
    parser.add_argument("--out-dir", required=True, help="输出系列工程根目录（自动创建）")
    parser.add_argument("--episode", default="ep01", help="集目录名（默认 ep01）")
    parser.add_argument("--series-title", default=None, help="系列名称（默认取目录名）")
    parser.add_argument("--episode-title", default=None, help="本集标题（默认取 episode 名）")
    parser.add_argument("--prompt-file", required=True, help="已转换好的 MiniMax-H3 专用提示词 txt 文件路径")
    parser.add_argument("--ref", action="append", default=[], help="参考图，格式 '名称=路径' 或直接路径，可重复传（按顺序编号）")
    parser.add_argument("--storyboard", default=None, help="故事板图片路径（默认自动追加在最后一张图，可加 --board-first 放第1张）")
    parser.add_argument("--board-first", action="store_true", help="将 --storyboard 编为 <Picture 1>（默认排在 --ref 之后）")
    parser.add_argument("--audio", action="append", default=[], help="可选参考音频，格式 '角色名=路径' 或直接路径（最多3段）")
    parser.add_argument("--duration", type=float, default=12.0, help="本段目标秒数（推荐 10-15s，默认 12.0）")
    parser.add_argument("--shots", type=int, default=4, help="故事板格数（默认 4）")
    parser.add_argument("--ratio", choices=["16:9", "9:16", "1:1", "4:3"], default="16:9")
    parser.add_argument("--resolution", choices=list(RESOLUTIONS.keys()), default="768p")
    parser.add_argument("--profile", choices=list(PROFILES.keys()), default="h200-max")
    parser.add_argument("--ref-size", choices=["match", "max"], default=None)
    parser.add_argument("--turbo", action="store_true", help="启用官方 4-step Turbo LoRA 快速抽卡")
    parser.add_argument("--seed", type=int, default=None, help="固定随机种子（默认随机）")
    parser.add_argument("--part", default="A", help="多段模式下的段名（A/B/C...，默认 A）")
    parser.add_argument("--multi-part", action="store_true", help="声明本集为多段拼接（文件名带 _A/_B 后缀）")
    parser.add_argument("--append", action="store_true", help="追加写入已有 manifest.json（配合 --multi-part 使用）")
    parser.add_argument("--allow-over-15s", action="store_true", help="允许超过15s时自动截断到362帧（默认报错提醒拆段）")
    args = parser.parse_args()

    if args.duration > MAX_H3_SEG_SEC + 0.5 and not args.allow_over_15s:
        sys.exit(
            f"❌ 本段时长 {args.duration}s 超过 MiniMax-H3 原生上限 15s (362帧)。"
            "请按每段 4 格 10-15s 拆分为 --multi-part --part A 与 --part B --append（或传 --allow-over-15s 强行截断）。"
        )

    prompt_src = Path(args.prompt_file).expanduser().resolve()
    if not prompt_src.exists():
        sys.exit(f"❌ 提示词文件不存在: {prompt_src}")
    prompt_text = prompt_src.read_text(encoding="utf-8").strip() + "\n"

    # 收集并排序图片/音频素材
    img_entries: list[dict[str, Any]] = []
    for i, raw in enumerate(args.ref, 1):
        name, p = parse_named_path(raw, "参考图", i)
        if not p.exists():
            sys.exit(f"❌ 参考图不存在 ({name}): {p}")
        img_entries.append({"kind": "ref", "name": name, "src": p})

    if args.storyboard:
        bp = Path(args.storyboard).expanduser().resolve()
        if not bp.exists():
            sys.exit(f"❌ 故事板不存在: {bp}")
        board_entry = {"kind": "board", "name": "故事板", "src": bp}
        if args.board_first:
            img_entries.insert(0, board_entry)
        else:
            img_entries.append(board_entry)

    if not img_entries:
        sys.exit("❌ 至少需要提供 1 张参考图或故事板（--ref / --storyboard）")
    if len(img_entries) > MAX_H3_REFS:
        sys.exit(f"❌ 参考图数量 {len(img_entries)} 超过 MiniMax-H3 上限 {MAX_H3_REFS}")

    aud_entries: list[dict[str, Any]] = []
    for i, raw in enumerate(args.audio, 1):
        name, p = parse_named_path(raw, "参考音频", i)
        if not p.exists():
            sys.exit(f"❌ 参考音频不存在 ({name}): {p}")
        aud_entries.append({"name": name, "src": p})
    if len(aud_entries) > MAX_H3_AUDIOS:
        sys.exit(f"❌ 参考音频数量 {len(aud_entries)} 超过 MiniMax-H3 上限 {MAX_H3_AUDIOS}")

    # 硬校验 H3 提示词标记
    try:
        warns = validate_h3_prompt(prompt_text, len(img_entries), len(aud_entries))
    except ValueError as exc:
        sys.exit(f"❌ H3 提示词校验失败: {exc}")
    for w in warns:
        print(f"⚠️ 提示词提醒: {w}", file=sys.stderr)

    # 准备兼容 ai-anime-series 的目录结构
    series_dir = Path(args.out_dir).expanduser().resolve()
    refs_dir = series_dir / "refs"
    ep_dir = series_dir / args.episode
    jobs_dir = ep_dir / "h3_jobs"
    refs_dir.mkdir(parents=True, exist_ok=True)
    jobs_dir.mkdir(parents=True, exist_ok=True)

    part_tag = args.part.upper()
    part_suffix = f"_{part_tag}" if args.multi_part else ""

    staged_img_records: list[dict[str, Any]] = []
    staged_img_names: list[str] = []
    char_entries_for_series: list[dict[str, Any]] = []

    for idx, item in enumerate(img_entries, 1):
        ext = item["src"].suffix.lower() or ".png"
        if item["kind"] == "board":
            dst = ep_dir / f"故事板{part_suffix}{ext}"
        else:
            fname = safe_filename(item["name"], f"ref_{idx}")
            dst = refs_dir / f"{fname}{ext}"
            char_entries_for_series.append({
                "id": f"ref{idx}",
                "name": item["name"],
                "ref": f"refs/{fname}{ext}",
                "look": f"见参考图 {item['name']}",
            })
        copy_if_needed(item["src"], dst)
        rel_posix = dst.relative_to(series_dir).as_posix()
        staged_name = f"h3_{args.episode}{part_suffix}_img{idx}{ext}"
        staged_img_names.append(staged_name)
        staged_img_records.append({
            "tag": f"<Picture {idx}>",
            "kind": item["kind"],
            "name": item["name"],
            "src_rel": rel_posix,
            "staged_name": staged_name,
            "exists": True,
        })

    staged_aud_records: list[dict[str, Any]] = []
    staged_aud_names: list[str] = []
    for idx, item in enumerate(aud_entries, 1):
        ext = item["src"].suffix.lower() or ".wav"
        fname = safe_filename(item["name"], f"voice_{idx}")
        dst = refs_dir / f"voice_{fname}{ext}"
        copy_if_needed(item["src"], dst)
        rel_posix = dst.relative_to(series_dir).as_posix()
        staged_name = f"h3_{args.episode}{part_suffix}_aud{idx}{ext}"
        staged_aud_names.append(staged_name)
        staged_aud_records.append({
            "tag": f"<Audio {idx}>",
            "name": item["name"],
            "src_rel": rel_posix,
            "staged_name": staged_name,
            "exists": True,
        })

    # 写出最小兼容的 series.json 与 episode.json（让 review_clip.py / compile.py 开箱可用）
    series_json_path = series_dir / "series.json"
    if not series_json_path.exists():
        series_doc = {
            "title": args.series_title or series_dir.name,
            "ratio": args.ratio,
            "style": "直喂模式（完整画风与分镜见 h3_jobs/h3_prompt.txt）",
            "dialogue": True,
            "characters": char_entries_for_series,
            "scenes": [],
        }
        series_json_path.write_text(json.dumps(series_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    ep_json_path = ep_dir / "episode.json"
    shot_dur = round(args.duration / max(1, args.shots), 2)
    part_stub = {
        "tag": part_tag,
        "shots": [{"dur": shot_dur, "desc": f"分镜格 {i + 1}"} for i in range(args.shots)],
    }
    if ep_json_path.exists() and args.append:
        ep_doc = json.loads(ep_json_path.read_text(encoding="utf-8"))
        parts = [p for p in ep_doc.get("parts", []) if p.get("tag") != part_tag]
        parts.append(part_stub)
        ep_doc["parts"] = parts
    else:
        ep_doc = {
            "title": args.episode_title or args.episode,
            "cast": [c["id"] for c in char_entries_for_series],
            "parts": [part_stub],
        }
    ep_json_path.write_text(json.dumps(ep_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    # 构建 ComfyUI API 图与任务条目
    width, height = RESOLUTIONS[args.resolution][args.ratio]
    profile = dict(PROFILES[args.profile])
    ref_image_size = args.ref_size or profile["ref_image_size"]
    frame_length = align_h3_frames(args.duration)
    seed = args.seed if args.seed is not None else random.randint(1, 2**31 - 1)
    output_prefix = f"h3_{args.episode}{part_suffix}"

    api_graph = build_comfyui_api_graph(
        prompt_text=prompt_text,
        width=width,
        height=height,
        frame_length=frame_length,
        ref_image_names=staged_img_names,
        ref_audio_names=staged_aud_names,
        profile=profile,
        ref_image_size=ref_image_size,
        use_turbo=args.turbo,
        seed=seed,
        output_prefix=output_prefix,
    )

    prompt_out = jobs_dir / f"h3_prompt{part_suffix}.txt"
    custom_out = jobs_dir / f"custom_h3_prompt{part_suffix}.txt"
    api_out = jobs_dir / f"comfyui_api{part_suffix}.json"
    prompt_out.write_text(prompt_text, encoding="utf-8")
    custom_out.write_text(prompt_text, encoding="utf-8")
    api_out.write_text(json.dumps(api_graph, ensure_ascii=False, indent=2), encoding="utf-8")

    job_entry = {
        "part": part_tag,
        "episode": args.episode,
        "shots_count": args.shots,
        "duration_sec": args.duration,
        "frame_length": frame_length,
        "actual_duration_sec": round(frame_length / FPS, 2),
        "width": width,
        "height": height,
        "fps": FPS,
        "seed": seed,
        "ref_image_size": ref_image_size,
        "use_turbo": args.turbo,
        "ref_images": staged_img_records,
        "ref_audios": staged_aud_records,
        "missing_files": [],
        "prompt_file": prompt_out.name,
        "api_file": api_out.name,
        "output_prefix": output_prefix,
        "target_clip_rel": f"{args.episode}/clips/{args.episode}{part_suffix}_h3.mp4",
    }

    manifest_path = jobs_dir / "manifest.json"
    if args.append and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        jobs = [j for j in manifest.get("jobs", []) if j.get("part") != part_tag]
        jobs.append(job_entry)
        manifest["jobs"] = jobs
    else:
        manifest = {
            "schema": "ai-anime-h3/v1",
            "mode": "direct",
            "series_title": args.series_title or series_dir.name,
            "episode": args.episode,
            "episode_title": args.episode_title or args.episode,
            "profile_name": args.profile,
            "profile": profile,
            "use_turbo": args.turbo,
            "turbo_lora": TURBO_LORA if args.turbo else None,
            "resolution": args.resolution,
            "jobs": [job_entry],
        }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 直喂任务打包完成 ({args.profile}, ref_size={ref_image_size}, seed={seed}):")
    print(f"   工程目录: {series_dir}")
    print(f"   任务清单: {manifest_path}")
    print(f"   规格参数: {width}x{height} @ 24fps, {frame_length}帧 (~{round(frame_length / FPS, 2)}s), Turbo={args.turbo}")
    print("   参考图映射:")
    for r in staged_img_records:
        print(f"     {r['tag']} -> {r['name']} ({r['src_rel']})")
    if staged_aud_records:
        print("   参考音频映射:")
        for a in staged_aud_records:
            print(f"     {a['tag']} -> {a['name']} ({a['src_rel']})")


if __name__ == "__main__":
    main()
