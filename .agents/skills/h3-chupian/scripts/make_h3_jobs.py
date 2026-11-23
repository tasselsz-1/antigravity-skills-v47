# -*- coding: utf-8 -*-
"""h3出片核心编译器：将 ai-anime-series (series.json + episode.json) 转为 MiniMax-H3 (ref2va) 任务包与 ComfyUI API 图。

默认无损满血配置：
  - --profile h200-max (BF16 pruned DiT 40.2GB + BF16 Qwen3-VL-32B 51.5GB + FP16 Video VAE + FP32 Audio VAE, ref_image_size=max)
  - --resolution 768p (16:9 -> 1344x768, 9:16 -> 768x1344, 24fps)
  - Colab G4 (96GB VRAM + 176GB RAM) 与 H200 (141GB) 均可零画质折损直跑。

用法：
  python make_h3_jobs.py SERIES ep01
  python make_h3_jobs.py SERIES ep01 --profile h200-max --resolution 768p
  python make_h3_jobs.py SERIES ep01 --profile h200-fast --resolution 480p --turbo
  python make_h3_jobs.py SERIES ep01 --use-custom-prompt
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
from pathlib import Path
from typing import Any

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8")

FPS = 24
MAX_H3_REFS = 9
MAX_H3_AUDIOS = 3
MAX_H3_SEG_SEC = 15.0  # MiniMax-H3 官方训练上限 ~15s (362 frames @ 24fps)

GRIDS = {4: (2, 2), 6: (2, 3), 8: (2, 4), 9: (3, 3)}
CN_NUM = {4: "四", 6: "六", 8: "八", 9: "九"}

# 对应 Comfy-Org/MiniMax-H3 官方公开仓库精确路径与体积（GB）
PROFILES: dict[str, dict[str, Any]] = {
    "h200-max": {
        "label": "无损满血 BF16（默认推荐，G4 96GB / H200 零画质折损）",
        "unet": "minimax_h3_ref2va_pruned_bf16.safetensors",
        "clip": "qwen3vl_32b_minimax_h3_bf16.safetensors",
        "video_vae": "minimax_h3_video_vae_fp16.safetensors",
        "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
        "ref_image_size": "max",
        "steps": 20,
        "scheduler": "beta",
        "sampler": "res_multistep",
        "approx_gb": 98,
    },
    "bf16-raw": {
        "label": "原始未裁剪 BF16（极限对比档，总重 ~124GB，G4 / H200）",
        "unet": "minimax_h3_ref2va_bf16.safetensors",
        "clip": "qwen3vl_32b_minimax_h3_bf16.safetensors",
        "video_vae": "minimax_h3_video_vae_fp16.safetensors",
        "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
        "ref_image_size": "max",
        "steps": 20,
        "scheduler": "beta",
        "sampler": "res_multistep",
        "approx_gb": 124,
    },
    "h200-fast": {
        "label": "省 CU 高速档（FP8 Scaled + INT8 Qwen3-VL，~54GB）",
        "unet": "minimax_h3_ref2va_pruned_fp8_scaled.safetensors",
        "clip": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        "video_vae": "minimax_h3_video_vae_fp16.safetensors",
        "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
        "ref_image_size": "match",
        "steps": 20,
        "scheduler": "beta",
        "sampler": "res_multistep",
        "approx_gb": 54,
    },
    "g4-balanced": {
        "label": "超轻省内存档（INT8 ConvRot + NVFP4 Qwen3-VL，~43GB）",
        "unet": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "clip": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
        "video_vae": "minimax_h3_video_vae_fp16.safetensors",
        "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
        "ref_image_size": "match",
        "steps": 20,
        "scheduler": "beta",
        "sampler": "res_multistep",
        "approx_gb": 43,
    },
}

TURBO_LORA = "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"

# 分辨率严格为 32 的倍数，对齐官方 nodes_minimax_h3.py
RESOLUTIONS: dict[str, dict[str, tuple[int, int]]] = {
    "1080p": {"16:9": (1920, 1088), "9:16": (1088, 1920), "1:1": (1440, 1440), "4:3": (1440, 1088)},
    "768p": {"16:9": (1344, 768), "9:16": (768, 1344), "1:1": (1024, 1024), "4:3": (1152, 864)},
    "480p": {"16:9": (864, 480), "9:16": (480, 864), "1:1": (640, 640), "4:3": (736, 544)},
}


def align_h3_frames(duration_sec: float) -> int:
    """对齐 MiniMax-H3 官方 17k+5 帧网格（24fps，官方训练区间 124~362 帧）。"""
    raw = max(5, round(duration_sec * FPS))
    aligned = raw + (5 - (raw % 17)) % 17
    if aligned < 124:
        aligned = 124
    if aligned > 362:
        aligned = 362
    return aligned


def fmt_sec(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def grid_of(n: int) -> tuple[int, int]:
    if n in GRIDS:
        return GRIDS[n]
    cols = math.ceil(math.sqrt(n))
    return math.ceil(n / cols), cols


def grid_name(n: int) -> str:
    return f"{CN_NUM[n]}宫格" if n in CN_NUM else f"{n}格"


def validate_h3_prompt(prompt_text: str, num_images: int, num_audios: int) -> list[str]:
    """硬校验 H3 提示词标记合法性，返回告警列表；致命错误直接抛 ValueError。"""
    warnings: list[str] = []

    legacy_tags = re.findall(r"@(?:图片?|音频|视频)\s*\d+", prompt_text)
    if legacy_tags:
        raise ValueError(
            f"提示词中仍残留未转换的即梦语法标记 {sorted(set(legacy_tags))}，"
            "请全部转换为 MiniMax-H3 原生标记 <Picture N> / <Audio M> 后再打包！"
        )

    pic_nums = [int(m) for m in re.findall(r"<Picture\s+(\d+)>", prompt_text)]
    aud_nums = [int(m) for m in re.findall(r"<Audio\s+(\d+)>", prompt_text)]

    bad_pics = sorted({n for n in pic_nums if n < 1 or n > num_images})
    if bad_pics:
        raise ValueError(f"提示词引用了越界的图片编号 <Picture {bad_pics}>，当前仅传入 {num_images} 张参考图！")

    bad_auds = sorted({n for n in aud_nums if n < 1 or n > num_audios})
    if bad_auds:
        raise ValueError(f"提示词引用了越界的音频编号 <Audio {bad_auds}>，当前仅传入 {num_audios} 段参考音频！")

    unused_pics = [i for i in range(1, num_images + 1) if i not in pic_nums]
    if unused_pics:
        warnings.append(f"有参考图未在提示词中被显式引用: {[f'<Picture {i}>' for i in unused_pics]}")
    unused_auds = [i for i in range(1, num_audios + 1) if i not in aud_nums]
    if unused_auds:
        warnings.append(f"有参考音频未在提示词中被显式引用: {[f'<Audio {i}>' for i in unused_auds]}")

    return warnings


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def pick_items(items: list[dict], ids: list[str], label: str) -> list[dict]:
    by_id = {x["id"]: x for x in items}
    missing = [i for i in ids if i not in by_id]
    if missing:
        sys.exit(f"episode.json 中的{label} {missing} 在 series.json 中找不到")
    return [by_id[i] for i in ids]


def resolve_board_path(ep_dir: Path, tag: str, multi: bool) -> Path:
    candidates = [
        f"storyboards/故事板_{tag}.png", f"storyboards/故事板_{tag}.jpg",
        f"故事板_{tag}.png", f"故事板_{tag}.jpg", f"故事板_{tag}.jpeg", f"故事板_{tag}.webp",
        f"storyboards/storyboard_{tag}.png", f"storyboard_{tag}.png", f"storyboard_{tag}.jpg",
    ] if multi else [
        "storyboards/故事板.png", "故事板.png", "故事板.jpg", "故事板.jpeg", "故事板.webp",
        "storyboard.png", "storyboard.jpg",
    ]
    for name in candidates:
        p = ep_dir / name
        if p.exists():
            return p
    return ep_dir / candidates[0]


def collect_refs(series_dir: Path, ep_dir: Path, s: dict, e: dict, tag: str, multi: bool, part: dict | None = None):
    p_cast = part["cast"] if (part and "cast" in part) else e.get("cast", [])
    p_groups = part["groups"] if (part and "groups" in part) else e.get("groups", [])
    p_scene = part["scene"] if (part and "scene" in part) else e.get("scene")
    cast = pick_items(s.get("characters", []), p_cast, "角色")
    groups = pick_items(s.get("groups", []), p_groups, "群体角色")
    scenes = pick_items(s.get("scenes", []), [p_scene] if p_scene else [], "场景")

    img_refs: list[dict[str, Any]] = []
    audio_refs: list[dict[str, Any]] = []

    for c in cast:
        img_refs.append({"kind": "char", "name": c["name"], "path": series_dir / c["ref"], "item": c})
        if c.get("voice_ref"):
            audio_refs.append({"name": c["name"], "path": series_dir / c["voice_ref"]})
    for g in groups:
        if g.get("ref"):
            img_refs.append({"kind": "group", "name": g["name"], "path": series_dir / g["ref"], "item": g})
    for sc in scenes:
        if sc.get("ref"):
            img_refs.append({"kind": "scene", "name": sc["name"], "path": series_dir / sc["ref"], "item": sc})

    board_path = resolve_board_path(ep_dir, tag, multi)
    img_refs.append({"kind": "board", "name": "故事板", "path": board_path, "item": None})

    if len(img_refs) > MAX_H3_REFS:
        sys.exit(f"参考图数量 {len(img_refs)} 超过 MiniMax-H3 上限 {MAX_H3_REFS}，请合并配角设定图")
    if len(audio_refs) > MAX_H3_AUDIOS:
        sys.exit(f"参考音频数量 {len(audio_refs)} 超过 MiniMax-H3 上限 {MAX_H3_AUDIOS}")

    for idx, r in enumerate(img_refs, 1):
        r["idx"] = idx
        r["tag"] = f"<Picture {idx}>"
        r["exists"] = r["path"].exists()
    for idx, a in enumerate(audio_refs, 1):
        a["idx"] = idx
        a["tag"] = f"<Audio {idx}>"
        a["exists"] = a["path"].exists()

    return img_refs, audio_refs, cast, groups, scenes


def build_h3_prompt(
    s: dict,
    e: dict,
    shots: list[dict],
    img_refs: list[dict],
    audio_refs: list[dict],
    groups: list[dict],
    scenes: list[dict],
) -> str:
    board = next(r for r in img_refs if r["kind"] == "board")
    sc_refs = [r for r in img_refs if r["kind"] == "scene"]
    audio_by_name = {a["name"]: a["tag"] for a in audio_refs}

    n = len(shots)
    rows, cols = grid_of(n)
    order_hint = "左上→右上→左下→右下" if (rows, cols) == (2, 2) else "从左到右、从上到下"

    lines = [
        f"根据 {board['tag']}（{rows}行{cols}列{grid_name(n)}分镜故事板，按{order_hint}顺序推进）生成连贯全屏动画视频。",
        f"严格要求：仅借鉴 {board['tag']} 的景别、构图站位、动作节奏与镜头切换，严禁将分镜黑框、白边、宫格分割线或镜头序号渲染进画面。",
        "",
    ]

    for r in img_refs:
        if r["kind"] == "char":
            v_hint = f" 说话音色严格参考 {audio_by_name[r['name']]}。" if r["name"] in audio_by_name else ""
            lines.append(f"{r['tag']} 是角色「{r['name']}」，外形、五官比例与配色必须与参考图严格一致：{r['item']['look']}。{v_hint}")
        elif r["kind"] == "group":
            lines.append(f"{r['tag']} 是群体角色「{r['name']}」的设定图：{r['item']['look']}。")
    for g in groups:
        if not g.get("ref"):
            lines.append(f"群体角色「{g['name']}」：{g['look']}。")

    place = scenes[0] if scenes else None
    style_text = s.get("style", "").strip()
    dialogue_mode = bool(s.get("dialogue", False))
    if not dialogue_mode:
        style_text += " 全片无台词，依靠夸张搞笑的肢体表演、表情和清脆卡通音效推动剧情。"
    else:
        style_text += " 中文清晰童声对白，说话口型与面部表情自然精准同步，配乐轻快童趣。"
    if place:
        style_text += f" 全片始终处于同一个{place['name']}、同一个{e.get('time', '午后')}：{place['look']}。"
        if sc_refs:
            style_text += f" 场景风格与布景参考 {sc_refs[0]['tag']}。"
    lines += ["", f"整体画风与场景：{style_text}", ""]

    if e.get("continuity"):
        lines += ["镜头连贯约束：" + "；".join(e["continuity"]) + "。", ""]

    t = 0.0
    for i, sh in enumerate(shots, 1):
        dur = float(sh["dur"])
        lines.append(f"镜头 {i}（{fmt_sec(t)}-{fmt_sec(t + dur)}秒）：{sh['desc']}。")
        if sh.get("dialogue"):
            lines.append(f"对白（精准口型）：{sh['dialogue']}")
        if sh.get("sfx"):
            lines.append(f"音效与配乐：{sh['sfx']}")
        lines.append("")
        t += dur

    lines.append("全程全屏24fps流畅渲染，画面干净无任何文字、字幕、水印、Logo或分镜边框。")
    return "\n".join(lines).strip() + "\n"


def build_comfyui_api_graph(
    prompt_text: str,
    width: int,
    height: int,
    frame_length: int,
    ref_image_names: list[str],
    ref_audio_names: list[str],
    profile: dict[str, Any],
    ref_image_size: str,
    use_turbo: bool,
    seed: int,
    output_prefix: str,
) -> dict[str, Any]:
    """构建源码级对齐 ComfyUI 官方 video_minimax_h3_r2v.json 的 /prompt API 图。"""
    graph: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": profile["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": profile["clip"], "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": profile["video_vae"]}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": profile["audio_vae"]}},
    }

    model_ref: list[Any] = ["1", 0]
    steps = profile["steps"]
    if use_turbo:
        graph["5"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"model": ["1", 0], "lora_name": TURBO_LORA, "strength_model": 1.0},
        }
        model_ref = ["5", 0]
        steps = 4

    h3_inputs: dict[str, Any] = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "audio_vae": ["4", 0],
        "prompt": prompt_text,
        "width": width,
        "height": height,
        "length": frame_length,
        "ref_image_size": ref_image_size,
    }

    next_id = 10
    for idx, fname in enumerate(ref_image_names):
        nid = str(next_id)
        next_id += 1
        graph[nid] = {"class_type": "LoadImage", "inputs": {"image": fname}}
        h3_inputs[f"ref_images.ref_image_{idx}"] = [nid, 0]

    for idx, fname in enumerate(ref_audio_names):
        nid = str(next_id)
        next_id += 1
        graph[nid] = {"class_type": "LoadAudio", "inputs": {"audio": fname}}
        h3_inputs[f"ref_audios.ref_audio_{idx}"] = [nid, 0]

    graph["30"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": h3_inputs}
    graph["31"] = {"class_type": "BasicGuider", "inputs": {"model": model_ref, "conditioning": ["30", 0]}}
    graph["32"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": profile["sampler"]}}
    graph["33"] = {
        "class_type": "BasicScheduler",
        "inputs": {"model": model_ref, "scheduler": profile["scheduler"], "steps": steps, "denoise": 1.0},
    }
    graph["34"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    graph["35"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["34", 0],
            "guider": ["31", 0],
            "sampler": ["32", 0],
            "sigmas": ["33", 0],
            "latent_image": ["30", 1],
        },
    }
    graph["36"] = {"class_type": "VAEDecode", "inputs": {"samples": ["35", 0], "vae": ["3", 0]}}
    graph["37"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["35", 0], "vae": ["4", 0]}}
    graph["38"] = {
        "class_type": "CreateVideo",
        "inputs": {"images": ["36", 0], "audio": ["37", 0], "fps": float(FPS)},
    }
    graph["39"] = {
        "class_type": "SaveVideo",
        "inputs": {"video": ["38", 0], "filename_prefix": output_prefix, "format": "mp4"},
    }
    return graph


def main() -> None:
    parser = argparse.ArgumentParser(description="将 ai-anime-series 剧本转为 MiniMax-H3 (ref2va) 批处理任务")
    parser.add_argument("series_dir", help="系列目录（含 series.json 和 refs/）")
    parser.add_argument("episode", help="集目录名（如 ep01）")
    parser.add_argument("--profile", choices=list(PROFILES.keys()), default="h200-max")
    parser.add_argument("--resolution", choices=list(RESOLUTIONS.keys()), default="768p")
    parser.add_argument("--ref-size", choices=["match", "max"], default=None, help="覆盖 profile 默认 ref_image_size")
    parser.add_argument("--turbo", action="store_true", help="启用官方 4-step Turbo LoRA 快速抽卡")
    parser.add_argument("--seed", type=int, default=None, help="固定随机种子（默认按段随机）")
    parser.add_argument("--use-custom-prompt", action="store_true", help="若 epNN/h3_jobs/custom_h3_prompt*.txt 存在则优先采用精修提示词")
    args = parser.parse_args()

    series_dir = Path(args.series_dir).resolve()
    ep_dir = series_dir / args.episode
    s = load_json(series_dir / "series.json")
    e = load_json(ep_dir / "episode.json")

    parts = e.get("parts") or []
    if not parts:
        sys.exit("episode.json 中缺少 parts")

    ratio = s.get("ratio", "16:9")
    if ratio not in RESOLUTIONS[args.resolution]:
        sys.exit(f"不支持的画幅比例 {ratio}，支持: {list(RESOLUTIONS[args.resolution].keys())}")
    width, height = RESOLUTIONS[args.resolution][ratio]

    profile = dict(PROFILES[args.profile])
    ref_image_size = args.ref_size or profile["ref_image_size"]

    out_dir = ep_dir / "h3_jobs"
    out_dir.mkdir(parents=True, exist_ok=True)
    multi = len(parts) > 1
    jobs: list[dict[str, Any]] = []

    for k, part in enumerate(parts):
        tag = str(part.get("id") or (f"P{k + 1:02d}" if k >= 8 else "ABCDEFGH"[k]))
        shots = part.get("shots") or []
        seg_dur = sum(float(sh["dur"]) for sh in shots)
        if seg_dur > MAX_H3_SEG_SEC + 0.5:
            print(
                f"⚠️ 警告：第 {tag} 段总时长 {fmt_sec(seg_dur)}s 超过 MiniMax-H3 原生 15s 上限，"
                f"帧数将截断在 362 帧（15.08s）。建议将本段拆成多个 4 格（每段 10-15s）！",
                file=sys.stderr,
            )

        frame_length = align_h3_frames(seg_dur)
        img_refs, audio_refs, _cast, groups, scenes = collect_refs(series_dir, ep_dir, s, e, tag, multi, part=part)

        part_ctx = dict(e)
        for _k in ("time", "continuity", "scene"):
            if _k in part and part[_k]:
                part_ctx[_k] = part[_k]

        part_suffix = f"_{tag}" if multi else ""
        custom_path = out_dir / f"custom_h3_prompt{part_suffix}.txt"
        if args.use_custom_prompt and custom_path.exists():
            prompt_text = custom_path.read_text(encoding="utf-8").strip() + "\n"
            print(f"📝 段 {tag} 使用精修自定义 H3 提示词: {custom_path.name}")
        else:
            prompt_text = build_h3_prompt(s, part_ctx, shots, img_refs, audio_refs, groups, scenes)

        warns = validate_h3_prompt(prompt_text, len(img_refs), len(audio_refs))
        for w in warns:
            print(f"⚠️ 段 {tag} 校验提醒: {w}", file=sys.stderr)

        seed = (args.seed + k) if args.seed is not None else random.randint(1, 2**31 - 1)
        staged_imgs = [f"h3_{args.episode}{part_suffix}_img{r['idx']}{r['path'].suffix or '.png'}" for r in img_refs]
        staged_auds = [f"h3_{args.episode}{part_suffix}_aud{a['idx']}{a['path'].suffix or '.wav'}" for a in audio_refs]
        output_prefix = f"h3_{args.episode}{part_suffix}"

        api_graph = build_comfyui_api_graph(
            prompt_text=prompt_text,
            width=width,
            height=height,
            frame_length=frame_length,
            ref_image_names=staged_imgs,
            ref_audio_names=staged_auds,
            profile=profile,
            ref_image_size=ref_image_size,
            use_turbo=args.turbo,
            seed=seed,
            output_prefix=output_prefix,
        )

        prompt_file = out_dir / f"h3_prompt{part_suffix}.txt"
        api_file = out_dir / f"comfyui_api{part_suffix}.json"
        prompt_file.write_text(prompt_text, encoding="utf-8")
        api_file.write_text(json.dumps(api_graph, ensure_ascii=False, indent=2), encoding="utf-8")

        missing_files = [str(r["path"]) for r in img_refs if not r["exists"]] + [
            str(a["path"]) for a in audio_refs if not a["exists"]
        ]
        jobs.append({
            "part": tag,
            "episode": args.episode,
            "shots_count": len(shots),
            "duration_sec": seg_dur,
            "frame_length": frame_length,
            "actual_duration_sec": round(frame_length / FPS, 2),
            "width": width,
            "height": height,
            "fps": FPS,
            "seed": seed,
            "ref_image_size": ref_image_size,
            "use_turbo": args.turbo,
            "ref_images": [
                {"tag": r["tag"], "kind": r["kind"], "name": r["name"], "src_rel": os.path.relpath(r["path"], series_dir).replace("\\", "/"), "staged_name": staged_imgs[i], "exists": r["exists"]}
                for i, r in enumerate(img_refs)
            ],
            "ref_audios": [
                {"tag": a["tag"], "name": a["name"], "src_rel": os.path.relpath(a["path"], series_dir).replace("\\", "/"), "staged_name": staged_auds[i], "exists": a["exists"]}
                for i, a in enumerate(audio_refs)
            ],
            "missing_files": missing_files,
            "prompt_file": str(prompt_file.name),
            "api_file": str(api_file.name),
            "output_prefix": output_prefix,
            "target_clip_rel": f"{args.episode}/clips/{args.episode}{part_suffix}_h3.mp4",
        })

    manifest = {
        "schema": "ai-anime-h3/v1",
        "series_title": s.get("title", series_dir.name),
        "episode": args.episode,
        "episode_title": e.get("title", args.episode),
        "profile_name": args.profile,
        "profile": profile,
        "use_turbo": args.turbo,
        "turbo_lora": TURBO_LORA if args.turbo else None,
        "resolution": args.resolution,
        "jobs": jobs,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 已生成 MiniMax-H3 任务清单 ({args.profile}, ref_size={ref_image_size}): {manifest_path}")
    for j in jobs:
        tags = " / ".join(f"{r['tag']}:{r['name']}" for r in j["ref_images"])
        auds = " / ".join(f"{a['tag']}:{a['name']}" for a in j["ref_audios"])
        aud_str = f" | 音频: {auds}" if auds else ""
        print(f"  - 段 {j['part']} ({j['shots_count']}格): {j['width']}x{j['height']} @ 24fps, {j['frame_length']}帧 (~{j['actual_duration_sec']}s) | {tags}{aud_str}")
        if j["missing_files"]:
            print(f"    ⚠️ 尚未放图的素材: {j['missing_files']}")


if __name__ == "__main__":
    main()
