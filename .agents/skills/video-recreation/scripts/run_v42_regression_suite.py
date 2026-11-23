#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V4.2 随稿永久自动化黑盒对抗回归套件 (run_v42_regression_suite.py)
全量覆盖：
1) 一审 21 项回归用例（适配 V4.2 主线默认 24fps + 备选 --target-fps 60 + 48kHz 批准声线 + 可信切点源 + 显式 run_id）
2) 二审新增全部深水区黑盒对抗用例（纯黑/静音对白母带拦截、FCPXML 1.9 DTD 双帧率校验、长音短画缺帧精确计数、
   移走母带状态回退、省略逐单元/run_id 拦截、预占单幂等核销与跨窗全局 429 冷却、Omni 1.2 订阅分档与 Extend、
   bytedance/LatentSync scripts.inference 子进程真跑验证、24kHz/缺批准清单阻断、空目录无外部源切点阻断）
全程使用 tempfile.TemporaryDirectory 运行，结束后自动清理临时目录，零工作区污染。
"""

from __future__ import annotations

import argparse
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from lxml import etree as LET
from PIL import Image, ImageDraw

SCRIPTS_DIR = Path(__file__).resolve().parent
if not (SCRIPTS_DIR / "batch_ops.py").is_file():
    raise FileNotFoundError(f"batch_ops.py not found under {SCRIPTS_DIR}")
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

BATCH_OPS = SCRIPTS_DIR / "batch_ops.py"
CLOUD_PIPE = SCRIPTS_DIR / "cloud_pipeline.py"
DEFAULT_BRAIN_ROOT = Path(os.environ.get("ANTIGRAVITY_BRAIN_ROOT", str(Path(tempfile.gettempdir()) / "antigravity_qa")))
DEFAULT_OUT_JSON = DEFAULT_BRAIN_ROOT / "scratch" / "qa_v42_results.json"

RESULTS: list[dict[str, Any]] = []


def run_cli(script: Path, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )


def record(case_id: str, title: str, passed: bool, exit_code: int, detail: str) -> None:
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {case_id} | exit={exit_code} | {title} -> {detail}", flush=True)
    RESULTS.append({
        "case_id": case_id,
        "title": title,
        "passed": bool(passed),
        "exit_code": int(exit_code),
        "detail": detail,
    })
    if not passed:
        raise AssertionError(f"Regression case failed: {case_id} ({title}) -> {detail}")


def parse_cli_json(text: str) -> dict[str, Any]:
    s = (text or "").strip()
    m = re.search(r"(?m)^\{", s)
    if m:
        return json.loads(s[m.start():])
    idx = s.find("{")
    return json.loads(s[idx:]) if idx >= 0 else {}


def sha256_f(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def make_test_mp4(
    dst: Path,
    width: int,
    height: int,
    fps: int,
    duration: float,
    video_src: str = "testsrc2",
    audio_mode: str = "tone",
    freq: int = 440,
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    v_lavfi = (
        f"color=c=black:size={width}x{height}:rate={fps}:duration={duration}"
        if video_src == "black"
        else f"{video_src}=size={width}x{height}:rate={fps}:duration={duration}"
    )
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", v_lavfi]
    if audio_mode == "silent":
        cmd.extend(["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}"])
    elif audio_mode == "quiet_sfx":
        cmd.extend(["-f", "lavfi", "-i", f"sine=frequency=180:sample_rate=48000:duration={duration},volume=0.015"])
    elif audio_mode == "window_02_07":
        cmd.extend([
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={duration}",
            "-f", "lavfi", "-i", "sine=frequency=520:sample_rate=48000:duration=0.52",
            "-filter_complex", f"[2:a]adelay=200|200[d];[1:a][d]amix=inputs=2:duration=first:normalize=0,atrim=0:{duration}[aout]",
            "-map", "0:v:0", "-map", "[aout]",
        ])
    elif audio_mode != "none":
        cmd.extend(["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=48000:duration={duration}"])
    n_frames = max(1, int(round(duration * fps)))
    cmd.extend(["-frames:v", str(n_frames), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", "-r", str(fps)])
    if audio_mode != "none":
        cmd.extend(["-c:a", "aac", "-ar", "48000", "-ac", "2"])
    cmd.append(str(dst))
    subprocess.run(cmd, check=True, capture_output=True)


def make_short_video_long_audio_mp4(dst: Path, v_dur: float = 1.0, a_dur: float = 4.0, fps: int = 24) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    v_frames = max(1, int(round(v_dur * fps)))
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=3840x2160:rate={fps}:duration={v_dur}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={a_dur}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-frames:v", str(v_frames),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-ar", "48000", "-ac", "2", str(dst),
    ], check=True, capture_output=True)


def make_test_wav(dst: Path, duration: float = 2.50, freq: int = 330, sr: int = 48000) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate={sr}:duration={duration}",
        "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le", str(dst),
    ], check=True, capture_output=True)


def make_white_bg_char_png(dst: Path, color: tuple[int, int, int] = (35, 65, 120)) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (1280, 720), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle((420, 140, 860, 620), fill=color, outline=(15, 20, 30), width=5)
    d.ellipse((540, 80, 740, 260), fill=(220, 180, 150), outline=(20, 20, 20), width=4)
    im.save(dst, format="PNG")


def measure_media_window_rms_db(media_path: Path, t_start: float, t_end: float, sr: int = 16000) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(media_path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True, check=True,
    )
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float64)
    i0, i1 = max(0, int(round(t_start * sr))), min(int(pcm.size), int(round(t_end * sr)))
    if i1 <= i0 or pcm.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(pcm[i0:i1]))))
    return round(20.0 * math.log10(rms / 32768.0), 3) if rms > 1e-9 else -120.0


def test_v41_baseline_part1(tmp_base: Path) -> None:
    proj = tmp_base / "v41_p0_1"
    (proj / "分集" / "第01集").mkdir(parents=True, exist_ok=True)
    r1 = run_cli(BATCH_OPS, ["check-delivery", "--project-root", str(proj), "--episode", "第01集"])
    j1 = parse_cli_json(r1.stdout)
    record("V41-P0-1a", "空项目 batch_ops.py check-delivery 非零退出", r1.returncode == 2 and not j1["all_nine_deliverables_passed"], r1.returncode, f"status={j1['status']}")

    r2 = run_cli(CLOUD_PIPE, ["check-delivery", "--project-root", str(proj), "--episode", "第01集"])
    j2 = parse_cli_json(r2.stdout)
    record("V41-P0-1b", "空项目 cloud_pipeline.py check-delivery 非零退出", r2.returncode == 2 and not j2["all_nine_deliverables_passed"], r2.returncode, f"status={j2['status']}")

    r3 = run_cli(CLOUD_PIPE, ["phase4-verify-master", "--project-root", str(proj), "--episode", "第01集"])
    j3 = parse_cli_json(r3.stdout)
    record("V41-P0-1c", "空项目默认 24fps phase4-verify-master 非零退出且不伪报对齐", r3.returncode == 2 and j3.get("target_fps") == 24 and "已实测通过" not in str(j3.get("subtitle_status", "")), r3.returncode, f"sub={j3.get('subtitle_status')}")

    p2 = tmp_base / "v41_p0_2"
    ep2, wk2 = p2 / "分集" / "第01集", p2 / "中间文件" / "在制工作区" / "第01集"
    (ep2 / "原视频段落").mkdir(parents=True, exist_ok=True)
    (wk2 / "已超分4K_24fps").mkdir(parents=True, exist_ok=True)
    (wk2 / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_02,镜头01,0.000,4.000,4.000,6,Omni Flash 1.2 | Ingredients | 6s,Ingredients,大厅\n", encoding="utf-8-sig")
    make_test_mp4(ep2 / "原视频段落" / "segment_02.mp4", 1920, 1080, 24, 0.125)
    f_out = ep2 / "第01集_达芬奇自动时间线.fcpxml"
    rm = run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(p2), "--episode", "第01集"])
    record("V41-P0-2a", "仅存 0.125s 原视频段落时默认 24fps build-fcpxml 严禁回退原片", rm.returncode == 2 and not f_out.exists(), rm.returncode, "零原片回退")

    make_test_mp4(wk2 / "已超分4K_24fps" / "segment_02.mp4", 3840, 2160, 24, 1.0)
    rs = run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(p2), "--episode", "第01集"])
    record("V41-P0-2b", "已超分4K_24fps 素材短于目标时长 (1.0s < 4.0s) 时 build-fcpxml 阻断", rs.returncode == 2 and bool(parse_cli_json(rs.stdout).get("short_video_stream_segments")), rs.returncode, "短画面被阻断")

    make_test_mp4(wk2 / "已超分4K_24fps" / "segment_02.mp4", 3840, 2160, 24, 5.0)
    rok = run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(p2), "--episode", "第01集"])
    jok = parse_cli_json(rok.stdout)
    clip_el = ET.parse(f_out).getroot().find(".//spine/asset-clip[@name='segment_02']")
    clip_dur = clip_el.attrib.get("duration") if clip_el is not None else ""
    record("V41-P0-2c", "默认 24fps 下 5.0s 素材精准裁切 1.0s Handles 为 4.0s (9600/2400s)", rok.returncode == 0 and clip_dur == "9600/2400s" and abs(float(jok.get("trimmed_handles_sec", 0.0)) - 1.0) < 0.12, rok.returncode, f"clip_dur={clip_dur}")

    hj = json.loads((wk2 / "第四棒_时间线与达芬奇交接单.json").read_text(encoding="utf-8"))
    aj = json.loads((wk2 / "口型与字幕时间点核验记录.json").read_text(encoding="utf-8"))
    record("V41-P2-1", "排布预检 (master_render_verified=False) 与自动/人工分界交接单同步落盘", "xml_generated_items" in hj and "davinci_manual_checklist" in hj and aj.get("master_render_verified") is False, rok.returncode, f"stage={aj.get('verification_stage')}")

    p3a = tmp_base / "v41_p0_3_24"
    wk3a = p3a / "中间文件" / "在制工作区" / "第01集"
    make_test_mp4(wk3a / "原始生成视频" / "segment_01.mp4", 3840, 2160, 24, 1.5)
    make_test_mp4(wk3a / "原始生成视频" / "segment_02.mp4", 1920, 1080, 24, 1.5)
    rt24 = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(p3a), "--episode", "第01集"])
    ok_3a = rt24.returncode == 0 and (wk3a / "已超分4K_24fps" / "segment_01.mp4").is_file() and (wk3a / "待超分视频" / "segment_02.mp4").is_file()
    record("V41-P0-3a", "主线默认 24fps 下无口型 4K@24fps 免 RIFE 直通而 1080p 进入待超分", ok_3a, rt24.returncode, "24fps 智能分流")

    p3b = tmp_base / "v41_p0_3_60"
    wk3b = p3b / "中间文件" / "在制工作区" / "第01集"
    make_test_mp4(wk3b / "原始生成视频" / "segment_01.mp4", 3840, 2160, 24, 1.5)
    rt60 = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(p3b), "--episode", "第01集", "--target-fps", "60"])
    ok_3b = rt60.returncode == 0 and (wk3b / "待超分视频" / "segment_01.mp4").is_file() and not (wk3b / "已超分4K_60fps" / "segment_01.mp4").exists()
    record("V41-P0-3b", "--target-fps 60 下原生 4K@24fps 视频拦截进待超分严禁直通已超分4K_60fps", ok_3b, rt60.returncode, "60fps 拦截 24fps")


def test_v41_baseline_part2(tmp_base: Path) -> None:
    p4 = tmp_base / "v41_p0_4"
    ep4, wk4, vd4, dd4 = p4 / "分集" / "第01集", p4 / "中间文件" / "在制工作区" / "第01集", p4 / "分集" / "第01集" / "声线", p4 / "分集" / "第01集" / "台词音频"
    rec4 = p4 / "中间文件" / "索引与制作记录"
    for d in (ep4, wk4, vd4, dd4, rec4):
        d.mkdir(parents=True, exist_ok=True)
    (rec4 / "角色改名与音色映射.json").write_text(json.dumps({"characters": [{"role_id": "角色01", "new_name": "沈惊鸿"}, {"role_id": "角色02", "new_name": "顾清歌"}]}, ensure_ascii=False), encoding="utf-8")
    (wk4 / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_02,镜头01,0.000,1.800,1.800,4,Omni Flash 1.2 | Ingredients | 4s,Ingredients,书房\nsegment_02,镜头02,1.800,4.000,2.200,4,Omni Flash 1.2 | Ingredients | 4s,Ingredients,书房\n", encoding="utf-8-sig")
    (ep4 / "第01集_台词生成表.csv").write_text("分段与镜号,说话角色,台词类型,起止时间轴与目标时长,绑定声线母本,CosyVoice2_情绪导演指令,视频通道与口型策略,输出音频文件名,台词正文\nsegment_02_镜头01,角色01_沈惊鸿,说话,0.0s-1.5s (1.5s),角色01_沈惊鸿_说话_声线参考.wav,冷静质问,Flow闭嘴后刷口型,segment_02_镜头01_沈惊鸿_台词.wav,这件事你早就知道。\nsegment_02_镜头02,角色02_顾清歌,说话,2.0s-3.6s (1.6s),角色02_顾清歌_说话_声线参考.wav,急促反驳,Flow闭嘴后刷口型,segment_02_镜头02_顾清歌_台词.wav,我也是刚刚收到密信！\n", encoding="utf-8-sig")
    for rid, rnm, f_s, f_o in (("角色01", "沈惊鸿", 310, 340), ("角色02", "顾清歌", 460, 500)):
        make_test_wav(vd4 / f"{rid}_{rnm}_说话_声线参考.wav", duration=2.45, freq=f_s, sr=48000)
        make_test_wav(vd4 / f"{rid}_{rnm}_心声_声线参考.wav", duration=2.55, freq=f_o, sr=48000)
    r_va = run_cli(CLOUD_PIPE, ["voice-audit", "--project-root", str(p4), "--episode", "第01集"])
    make_test_wav(dd4 / "segment_02_镜头01_沈惊鸿_台词.wav", duration=1.50, freq=310, sr=48000)
    make_test_wav(dd4 / "segment_02_镜头02_顾清歌_台词.wav", duration=1.60, freq=460, sr=48000)
    make_test_mp4(wk4 / "原始生成视频" / "segment_02.mp4", 1920, 1080, 24, 4.0)
    rvc = run_cli(CLOUD_PIPE, ["build-voice-colab", "--project-root", str(p4), "--episode", "第01集", "--stage", "dialogue"])
    rp4 = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(p4), "--episode", "第01集"])
    vc_src = "".join("".join(c["source"]) for c in json.loads(Path(parse_cli_json(rvc.stdout)["notebook_ipynb"]).read_text(encoding="utf-8"))["cells"] if c["cell_type"] == "code")
    p4_src = "".join("".join(c["source"]) for c in json.loads(Path(parse_cli_json(rp4.stdout)["colab_notebook_ipynb"]).read_text(encoding="utf-8"))["cells"] if c["cell_type"] == "code")
    comb = vc_src + "\n" + p4_src
    record("V41-P0-4", "两人两句延迟起音 build-colab 含 adelay/官方 scripts.inference/纯SFX封装且免RIFE", rp4.returncode == 0 and "seg_wavs[0]" not in p4_src and "adelay=" in p4_src and "scripts.inference" in p4_src and "--video_out_path" in p4_src and "RIFE_v4.6" not in p4_src, rp4.returncode, "24fps P4A Notebook 合规")
    record("V41-P1-5/6", "48kHz voice-audit 签发批准SHA后对话克隆 Notebook 零盘符/零回退/零吞错", r_va.returncode == 0 and rvc.returncode == 0 and not re.search(r"(?<![A-Za-z])[A-Za-z]:(?:\\|/(?!/))", comb) and "zero_shot_prompt.wav" not in comb and "check=False" not in comb and "DIALOGUE_OVER_BUDGET" in vc_src, rvc.returncode, "Voice Colab 合规")

    p5 = tmp_base / "v41_p0_5"
    wk5, rc5, ch5 = p5 / "中间文件" / "在制工作区" / "第01集", p5 / "中间文件" / "索引与制作记录", p5 / "分集" / "第01集" / "人物"
    for d in (wk5, rc5, ch5):
        d.mkdir(parents=True, exist_ok=True)
    (rc5 / "角色改名与音色映射.json").write_text(json.dumps({"characters": {"老A": {"role_id": "角色01", "new_name": "沈惊鸿", "persona": "冷峻剑客"}}}, ensure_ascii=False), encoding="utf-8")
    make_white_bg_char_png(ch5 / "角色01_沈惊鸿_肖像.png", (40, 80, 140))
    rw1 = run_cli(BATCH_OPS, ["prototype-sheet", "--project-dir", str(p5), "--episode", "第01集", "--stage", "wave1"])
    record("V41-P0-5a", "无原型新角色仅凭纯白底 _肖像.png 跑 prototype-sheet --stage wave1 产出2列图", rw1.returncode == 0 and json.loads((wk5 / "wave1_portrait_verified.json").read_text(encoding="utf-8")).get("cols") == 2, rw1.returncode, "cols=2")
    rw2f = run_cli(BATCH_OPS, ["prototype-sheet", "--project-dir", str(p5), "--episode", "第01集", "--stage", "wave2"])
    record("V41-P0-5b", "缺少 _主体.png 时运行 prototype-sheet --stage wave2 报错阻断", rw2f.returncode == 2, rw2f.returncode, "缺主体阻断")
    make_white_bg_char_png(ch5 / "角色01_沈惊鸿_主体.png", (60, 100, 150))
    rw2o = run_cli(BATCH_OPS, ["prototype-sheet", "--project-dir", str(p5), "--episode", "第01集", "--stage", "wave2"])
    record("V41-P0-5c", "补齐纯白底 _主体.png 后 prototype-sheet --stage wave2 产出3列总表", rw2o.returncode == 0 and json.loads((wk5 / "wave2_consistency_verified.json").read_text(encoding="utf-8")).get("cols") == 3, rw2o.returncode, "cols=3")

    qd = tmp_base / "v41_quota" / "中间文件" / "在制工作区" / "第01集"
    qd.mkdir(parents=True, exist_ok=True)
    rq1 = run_cli(BATCH_OPS, ["quota-reserve", "--work-dir", str(qd), "--window", "window_01", "--unit-id", "u1", "--hard-cap", "2"])
    rq2 = run_cli(BATCH_OPS, ["quota-reserve", "--work-dir", str(qd), "--window", "window_01", "--unit-id", "u2", "--hard-cap", "2"])
    rq3 = run_cli(BATCH_OPS, ["quota-reserve", "--work-dir", str(qd), "--window", "window_01", "--unit-id", "u3", "--hard-cap", "2"])
    record("V41-P1-3a", "quota-reserve --hard-cap 2 第3次调用返回 exit=4 硬阻断", rq1.returncode == 0 and rq2.returncode == 0 and rq3.returncode == 4, rq3.returncode, parse_cli_json(rq3.stdout).get("status", ""))

    cd = tmp_base / "v41_cuts"
    cd.mkdir(parents=True, exist_ok=True)
    rows98 = ["segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene"] + [f"segment_{i+1:02d},镜头01,{i*8.0:.3f},{(i+1)*8.0:.3f},8.000,8,Veo 3.1 Lite | Frames | 8s,Frames,场景{i+1}" for i in range(44)]
    (cd / "cuts_98.csv").write_text("\n".join([*rows98, "segment_45,镜头01,352.000,352.800,0.800,4,Veo 3.1 Lite | Frames | 4s,Frames,场景45"]) + "\n", encoding="utf-8-sig")
    (cd / "cuts_999.csv").write_text("\n".join([*rows98, "segment_45,镜头01,352.000,359.700,7.700,8,Veo 3.1 Lite | Frames | 8s,Frames,场景45"]) + "\n", encoding="utf-8-sig")
    rcf = run_cli(BATCH_OPS, ["verify-cuts", "--cuts-csv", str(cd / "cuts_98.csv"), "--expected-start-sec", "0", "--expected-end-sec", "360"])
    rco = run_cli(BATCH_OPS, ["verify-cuts", "--cuts-csv", str(cd / "cuts_999.csv"), "--expected-start-sec", "0", "--expected-end-sec", "360"])
    record("V41-P1-7a/b", "verify-cuts 带可信区间源：98.0% 漏切 7.2s 阻断 (exit=2) 而 99.92% 通过 (exit=0)", rcf.returncode == 2 and rco.returncode == 0, rco.returncode, f"cov={parse_cli_json(rco.stdout).get('coverage_ratio')}")

    sp = cd / "shots.json"
    sp.write_text(json.dumps({"shots": [{"shot_id": "shot_01", "start_sec": 0.0, "end_sec": 4.8, "scene": "古街", "summary": "对峙"}]}, ensure_ascii=False), encoding="utf-8")
    def _flow_rt(route: str, mode: str, gd: int, as_of: str = "2026-11-19") -> subprocess.CompletedProcess:
        rp = cd / "rt.json"
        rp.write_text(json.dumps({"segments": [{"segment": "segment_01", "shots": ["shot_01"], "scene": "古街", "model_route": route, "video_mode": mode, "gen_duration_sec": gd}]}, ensure_ascii=False), encoding="utf-8")
        return run_cli(BATCH_OPS, ["build-cuts", "--shots-json", str(sp), "--output-csv", str(cd / "out.csv"), "--routing-json", str(rp), "--as-of-date", as_of, "--expected-start-sec", "0", "--expected-end-sec", "4.8"])
    rf1 = _flow_rt("Veo 3.1 Quality | Ingredients | 8s", "Ingredients", 8)
    rf2 = _flow_rt("Omni Flash 1.1 | Ingredients | 5s", "Ingredients", 5)
    rf3 = _flow_rt("Veo 3.1 Fast | Frames | 6s", "Frames", 6, as_of="2026-12-06")
    record("V41-P1-4a", "Veo 3.1 Quality + Ingredients 被报错拦截", rf1.returncode == 2 and "FLOW_UNSUPPORTED_VEO_QUALITY_INGREDIENTS" in rf1.stderr, rf1.returncode, "Quality 禁 Ingredients")
    record("V41-P1-4b", "Omni Flash 1.1 + 5s 非离散秒数被报错拦截", rf2.returncode == 2 and "FLOW_INVALID_DURATION_OMNI_1_1" in rf2.stderr, rf2.returncode, "Omni 1.1 限 4/6/8/10s")
    record("V41-P1-4c", "--as-of-date 2026-12-06 下已退役模型 Veo 3.1 Fast 被拦截", rf3.returncode == 2 and "FLOW_MODEL_RETIRED_2026_12_05" in rf3.stderr, rf3.returncode, "2026-12-05 退役生效")

    spj = tmp_base / "v41_stamp"
    swk, san, sep = spj / "中间文件" / "在制工作区" / "第01集", spj / "中间文件" / "原片分析" / "第01集", spj / "分集" / "第01集"
    for d in (swk, san, sep):
        d.mkdir(parents=True, exist_ok=True)
    (san / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_01,镜头01,0.000,4.000,4.000,4,Veo 3.1 Lite | Frames | 4s,Frames,书房\n", encoding="utf-8-sig")
    (sep / "第01集_视频提示词.md").write_text("### segment_01 (0.0s-4.0s)\n- Model: Veo 3.1 Lite | Frames | 4s\n- Scene: 书房\n- Prompt: Static medium shot, character stands calmly with lips firmly closed.\n", encoding="utf-8")
    mfp, audp = swk / "待生图帧清单.json", swk / "stage_a_visual_audit.json"
    mfp.write_text(json.dumps({"units": [{"unit": "角色01_沈惊鸿_肖像", "prompt": "16:9 widescreen aspect ratio, pure #FFFFFF white background, cinematic character portrait of Shen Jinghong."}]}, ensure_ascii=False), encoding="utf-8")
    audp.write_text(json.dumps({"status": "PASS", "units": {"角色01_沈惊鸿_肖像": {"status": "PASS", "notes": "纯白底16比9横屏且面部朝右骨相清晰无遮挡"}}}, ensure_ascii=False), encoding="utf-8")
    rs_same = run_cli(BATCH_OPS, ["stamp-visual-audit", "--audit-json", str(audp), "--manifest", str(mfp), "--author-agent", "gemini-4-carbon-medium", "--inspector-agent", "gemini-4-carbon-medium", "--author-run-id", "swarm_c_20261119_a1b2c3d4", "--inspector-run-id", "swarm_c_20261119_e5f60718"])
    rs_ok = run_cli(BATCH_OPS, ["stamp-visual-audit", "--audit-json", str(audp), "--manifest", str(mfp), "--author-agent", "gemini-4-carbon-medium", "--inspector-agent", "gemini-3.8-flash-high", "--author-run-id", "swarm_c_20261119_a1b2c3d4", "--inspector-run-id", "swarm_f_20261119_e5f60718"])
    record("V41-P1-2a", "stamp-visual-audit 同模型自审拦截且显式异源run_id写入实时SHA256", rs_same.returncode == 2 and rs_ok.returncode == 0 and bool(json.loads(audp.read_text(encoding="utf-8"))["units"]["角色01_沈惊鸿_肖像"].get("audited_prompt_sha256")), rs_ok.returncode, "防自审+写哈希")
    mfp.write_text(json.dumps({"units": [{"unit": "角色01_沈惊鸿_肖像", "prompt": "16:9 widescreen aspect ratio, pure #FFFFFF white background, TAMPERED AFTER AUDIT."}]}, ensure_ascii=False), encoding="utf-8")
    rs_tp = run_cli(BATCH_OPS, ["verify-script-alignment", "--project-dir", str(spj), "--episode", "第01集", "--frames-json", str(mfp), "--audit-json", str(audp), "--require-visual-audit"])
    record("V41-P1-1/2b", "修改提示词后旧盲审回执触发 AUDIT_PROMPT_HASH_MISMATCH_OR_MISSING 拦截", rs_tp.returncode == 2 and "AUDIT_PROMPT_HASH_MISMATCH_OR_MISSING" in (rs_tp.stdout + rs_tp.stderr), rs_tp.returncode, "旧回执失效")


def test_v42_deep_p0_cases(tmp_base: Path) -> None:
    import v42_fcpxml_and_master_qc as v42_qc

    pm = tmp_base / "v42_p0_1_2"
    epm, wkm = pm / "分集" / "第01集", pm / "中间文件" / "在制工作区" / "第01集"
    (epm / "台词音频").mkdir(parents=True, exist_ok=True)
    (epm / "配乐").mkdir(parents=True, exist_ok=True)
    (wkm / "已超分4K_24fps").mkdir(parents=True, exist_ok=True)
    (wkm / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_01,镜头01,0.000,2.000,2.000,4,Omni Flash 1.2 | Ingredients | 4s,Ingredients,书房\n", encoding="utf-8-sig")
    (epm / "第01集_台词生成表.csv").write_text("分段与镜号,说话角色,台词类型,起止时间轴与目标时长,绑定声线母本,CosyVoice2_情绪导演指令,视频通道与口型策略,输出音频文件名,台词正文\nsegment_01_镜头01,角色01_沈惊鸿,说话,0.2s-0.7s (0.5s),角色01_沈惊鸿_说话_声线参考.wav,沉声开口,Flow闭嘴后刷口型,segment_01_镜头01_沈惊鸿_台词.wav,真相只有一个。\n", encoding="utf-8-sig")
    (epm / "第01集_字幕时间轴.csv").write_text("segment,shot,start_sec,end_sec,内容\nsegment_01,镜头01,0.200,0.700,真相只有一个。\n", encoding="utf-8-sig")
    make_test_wav(epm / "台词音频" / "segment_01_镜头01_沈惊鸿_台词.wav", duration=0.50, freq=420, sr=48000)
    make_test_wav(epm / "配乐" / "bgm_tension.wav", duration=2.00, freq=220, sr=48000)
    make_test_mp4(wkm / "已超分4K_24fps" / "segment_01.mp4", 3840, 2160, 24, 2.0)

    rx24 = run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(pm), "--episode", "第01集"])
    fcpxml_24 = epm / "第01集_达芬奇自动时间线.fcpxml"
    dtd = LET.DTD(io.StringIO(v42_qc.APPLE_FCPXML_1_9_DTD))
    root24 = ET.fromstring(fcpxml_24.read_bytes())
    fmt24 = root24.find(".//resources/format[@id='r1']")
    assets24 = root24.findall(".//resources/asset")
    clips24 = root24.findall(".//asset-clip")
    dtd_ok_24 = bool(dtd.validate(LET.fromstring(fcpxml_24.read_bytes())))
    ok_dtd24 = (
        rx24.returncode == 0 and dtd_ok_24 and fmt24 is not None and fmt24.get("frameDuration") == "100/2400s"
        and len(assets24) >= 3 and all("src" not in a.attrib and a.find("media-rep[@kind='original-media']") is not None and bool(a.find("media-rep").get("src")) for a in assets24)
        and all("role" not in c.attrib for c in clips24) and any(str(c.get("audioRole", "")).startswith("dialogue.") for c in clips24)
    )
    record("V42-P0-2a", "FCPXML 1.9 DTD 严格校验 (默认 24fps · 100/2400s · media-rep · audioRole)", ok_dtd24, rx24.returncode, f"dtd={dtd_ok_24}, frameDuration={fmt24.get('frameDuration') if fmt24 is not None else ''}")

    master_24 = epm / "第01集_4K24fps_成片.mp4"
    make_test_mp4(master_24, 3840, 2160, 24, 2.0, video_src="black", audio_mode="silent")
    rm_blk = run_cli(CLOUD_PIPE, ["phase4-verify-master", "--project-root", str(pm), "--episode", "第01集"])
    jm_blk = parse_cli_json(rm_blk.stdout)
    record("V42-P0-1a", "纯黑静音母带 (4K@24fps) 含 0.2-0.7s 对白要求跑 phase4-verify-master 拦截", rm_blk.returncode == 2 and jm_blk.get("status") == "MASTER_VERIFY_REJECTED_BLANK_VIDEO", rm_blk.returncode, f"status={jm_blk.get('status')}")

    make_test_mp4(master_24, 3840, 2160, 24, 2.0, video_src="testsrc2", audio_mode="silent")
    rm_sil = run_cli(CLOUD_PIPE, ["phase4-verify-master", "--project-root", str(pm), "--episode", "第01集"])
    jm_sil = parse_cli_json(rm_sil.stdout)
    record("V42-P0-1b", "动态非黑画面但 0.2-0.7s 对白窗静音母带跑 phase4-verify-master 拦截", rm_sil.returncode == 2 and jm_sil.get("status") == "MASTER_VERIFY_REJECTED_SILENT_DIALOGUE_WINDOW", rm_sil.returncode, f"status={jm_sil.get('status')}")

    make_test_mp4(master_24, 3840, 2160, 24, 2.0, video_src="smptebars", audio_mode="window_02_07")
    rm_bar = run_cli(CLOUD_PIPE, ["phase4-verify-master", "--project-root", str(pm), "--episode", "第01集"])
    jm_bar = parse_cli_json(rm_bar.stdout)
    record("V43-P0-2a", "2秒静止彩条+对白单音母带跑 phase4-verify-master 触发 STATIC_DEAD_FRAME 拦截", rm_bar.returncode == 2 and "STATIC_DEAD_FRAME" in str(jm_bar.get("failures", [])), rm_bar.returncode, f"status={jm_bar.get('status')}")

    make_test_mp4(master_24, 3840, 2160, 24, 2.0, video_src="testsrc2", audio_mode="window_02_07")
    rm_ok = run_cli(CLOUD_PIPE, ["phase4-verify-master", "--project-root", str(pm), "--episode", "第01集"])
    jm_ok = parse_cli_json(rm_ok.stdout)
    exp_sha = sha256_f(master_24)
    record("V42-P0-1c", "合成动态图卡+窗口测试音轨（仅测技术规格自动门禁）仅标 MASTER_TECH_SPEC_PASSED 待人工确认", rm_ok.returncode == 0 and jm_ok.get("status") == "MASTER_TECH_SPEC_PASSED" and jm_ok.get("technical_spec_passed") is True and jm_ok.get("human_review_verified") is False and jm_ok.get("verified_master_sha256") == exp_sha, rm_ok.returncode, f"status={jm_ok.get('status')}")

    rc_ai = run_cli(BATCH_OPS, ["phase4-confirm-master", "--project-root", str(pm), "--episode", "第01集", "--reviewer", "gemini-4-argon", "--confirm-dialogue", "--confirm-lipsync", "--confirm-subtitles", "--notes", "已逐镜回看确认对白清晰口型同步字幕准确无误"])
    rc_hu = run_cli(BATCH_OPS, ["phase4-confirm-master", "--project-root", str(pm), "--episode", "第01集", "--reviewer", "张剪辑", "--confirm-dialogue", "--confirm-lipsync", "--confirm-subtitles", "--notes", "已逐镜回看确认对白清晰口型同步字幕准确无误"])
    jc_hu = parse_cli_json(rc_hu.stdout)
    record("V43-P0-2b", "phase4-confirm-master 拦截 AI 署名并在人工确认后升为 MASTER_SEALED_OK", rc_ai.returncode == 2 and rc_hu.returncode == 0 and jc_hu.get("status") == "MASTER_SEALED_OK" and jc_hu.get("confirmed_master_sha256") == exp_sha, rc_hu.returncode, f"sealed={jc_hu.get('status')}")

    pm60 = tmp_base / "v42_p0_2_60"
    ep60, wk60 = pm60 / "分集" / "第01集", pm60 / "中间文件" / "在制工作区" / "第01集"
    (wk60 / "已超分4K_60fps").mkdir(parents=True, exist_ok=True)
    (wk60 / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_01,镜头01,0.000,2.000,2.000,4,Omni Flash 1.2 | Ingredients | 4s,Ingredients,书房\n", encoding="utf-8-sig")
    make_test_mp4(wk60 / "已超分4K_60fps" / "segment_01.mp4", 3840, 2160, 60, 2.0)
    rx60 = run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(pm60), "--episode", "第01集", "--target-fps", "60"])
    fcpxml_60 = ep60 / "第01集_达芬奇自动时间线.fcpxml"
    root60 = ET.fromstring(fcpxml_60.read_bytes())
    fmt60 = root60.find(".//resources/format[@id='r1']")
    dtd_ok_60 = bool(dtd.validate(LET.fromstring(fcpxml_60.read_bytes())))
    ok_dtd60 = rx60.returncode == 0 and dtd_ok_60 and fmt60 is not None and fmt60.get("frameDuration") == "100/6000s" and all("src" not in a.attrib for a in root60.findall(".//resources/asset")) and all("role" not in c.attrib for c in root60.findall(".//asset-clip"))
    record("V42-P0-2b", "FCPXML 1.9 DTD 严格校验 (--target-fps 60 · 100/6000s · media-rep · audioRole)", ok_dtd60, rx60.returncode, f"dtd={dtd_ok_60}, frameDuration={fmt60.get('frameDuration') if fmt60 is not None else ''}")

    ps24 = tmp_base / "v42_p0_3_24"
    wks24 = ps24 / "中间文件" / "在制工作区" / "第01集"
    (wks24 / "cuts.csv").parent.mkdir(parents=True, exist_ok=True)
    (wks24 / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_01,镜头01,0.000,4.000,4.000,4,Veo 3.1 Lite | Frames | 4s,Frames,走廊\n", encoding="utf-8-sig")
    make_short_video_long_audio_mp4(wks24 / "已超分4K_24fps" / "segment_01.mp4", v_dur=1.0, a_dur=4.0, fps=24)
    rs_24 = run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(ps24), "--episode", "第01集"])
    sv24 = (parse_cli_json(rs_24.stdout).get("short_video_stream_segments") or [{}])[0]
    record("V42-P0-3a", "默认 24fps 下 1.0s视频+4.0s长音轨伪装素材拦截并精确报 missing_frames==72", rs_24.returncode == 2 and sv24.get("missing_frames") == 72, rs_24.returncode, f"missing_frames={sv24.get('missing_frames')}")

    ps60 = tmp_base / "v42_p0_3_60"
    wks60 = ps60 / "中间文件" / "在制工作区" / "第01集"
    (wks60 / "cuts.csv").parent.mkdir(parents=True, exist_ok=True)
    (wks60 / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_01,镜头01,0.000,4.000,4.000,4,Veo 3.1 Lite | Frames | 4s,Frames,走廊\n", encoding="utf-8-sig")
    make_short_video_long_audio_mp4(wks60 / "已超分4K_60fps" / "segment_01.mp4", v_dur=1.0, a_dur=4.0, fps=60)
    rs_60 = run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(ps60), "--episode", "第01集", "--target-fps", "60"])
    sv60 = (parse_cli_json(rs_60.stdout).get("short_video_stream_segments") or [{}])[0]
    record("V42-P0-3b", "--target-fps 60 下 1.0s视频+4.0s长音轨伪装素材拦截并精确报 missing_frames==180", rs_60.returncode == 2 and sv60.get("missing_frames") == 180, rs_60.returncode, f"missing_frames={sv60.get('missing_frames')}")


def test_v42_deep_p1_part1(tmp_base: Path) -> None:
    pr = tmp_base / "v42_p1_1"
    wk = pr / "中间文件" / "在制工作区" / "第01集"
    an = pr / "中间文件" / "原片分析" / "第01集"
    ep = pr / "分集" / "第01集"
    au = pr / "中间文件" / "索引与制作记录" / "交付自查状态"
    b1 = wk / "生成批次" / "批次01"
    for d in (wk, an / "ref_clean_frames", an / "storyboard_grids", ep, au, b1, wk / "已超分4K_24fps"):
        d.mkdir(parents=True, exist_ok=True)
    make_white_bg_char_png(an / "ref_clean_frames" / "seg_01_shot_01_first_净参考.jpg")
    (an / "物理切点清单.json").write_text(json.dumps({"shots": [{"shot_id": "shot_01", "start_sec": 0.0, "end_sec": 2.0}]}), encoding="utf-8")
    (an / "storyboard_grids" / "storyboard_index.json").write_text(json.dumps({"sheets": ["grid_01.jpg"]}), encoding="utf-8")
    (an / "phase1_colab_done.json").write_text(json.dumps({"colab_cpu_verified": True, "detector_engine": "TransNetV2", "expected_start_sec": 0.0, "expected_end_sec": 2.0}), encoding="utf-8")
    (wk / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_01,镜头01,0.000,2.000,2.000,4,Veo 3.1 Lite | Frames | 4s,Frames,书房\n", encoding="utf-8-sig")
    for jf in ("episode_director_bible.json", "episode_context_bible.json", "style_and_cast.json"):
        (wk / jf).write_text(json.dumps({"episode": "第01集", "target_fps": 24}), encoding="utf-8")
    (ep / "第01集_视频提示词.md").write_text("### segment_01\n- Prompt: Static shot.\n", encoding="utf-8")
    (ep / "第01集_分镜头脚本.xlsx").write_bytes(b"PK_DUMMY_XLSX_NONEMPTY")
    u_item = {
        "unit": "场景_书房",
        "type": "scene",
        "prompt": "16:9 widescreen empty ancient study room interior, warm lantern lighting, zero people.",
        "refs": [],
        "visual_spec": {
            "ref_roles": [],
            "camera_and_horizon": "eye-level wide shot",
            "spatial_layers": {"foreground": "wooden desk", "midground": "ink scroll", "background": "lattice window"},
            "lighting_vector": "warm amber key light from right",
            "exact_person_count": 0,
            "subjects_inventory": [],
        },
    }
    (wk / "待生图帧清单.json").write_text(json.dumps({"schema_version": "4.2", "units": [u_item]}, ensure_ascii=False), encoding="utf-8")
    (wk / "prompt_visual_audit.json").write_text(json.dumps({"status": "PASS", "units": {"场景_书房": {"status": "PASS", "notes": "纯空镜书房构图准确无人物水印光影合规"}}}, ensure_ascii=False), encoding="utf-8")
    assert run_cli(BATCH_OPS, ["stamp-visual-audit", "--audit-json", str(wk / "prompt_visual_audit.json"), "--manifest", str(wk / "待生图帧清单.json"), "--author-agent", "gemini-4-carbon-medium", "--inspector-agent", "gemini-3.8-flash-high", "--author-run-id", "swarm_c_20261119_11223344", "--inspector-run-id", "swarm_f_20261119_55667788"]).returncode == 0
    (b1 / "jobs.json").write_text(json.dumps({"jobs": [u_item]}, ensure_ascii=False), encoding="utf-8")
    (b1 / "finalize_receipt.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (b1 / "审核总结.json").write_text(json.dumps({"场景_书房": {"status": "通过"}}), encoding="utf-8")
    (wk / "当前制作审核分类.json").write_text(json.dumps({"场景_书房": {"category": "自检通过", "batch": "批次01"}}, ensure_ascii=False), encoding="utf-8")
    (au / "第01集_第三棒静态九项核验.json").write_text(json.dumps({"all_nine_deliverables_passed": True}), encoding="utf-8")
    make_test_mp4(wk / "已超分4K_24fps" / "segment_01.mp4", 3840, 2160, 24, 2.0)
    assert run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(pr), "--episode", "第01集"]).returncode == 0
    m_p = ep / "第01集_4K24fps_成片.mp4"
    make_test_mp4(m_p, 3840, 2160, 24, 2.0)
    assert run_cli(CLOUD_PIPE, ["phase4-verify-master", "--project-root", str(pr), "--episode", "第01集"]).returncode == 0
    st_wait_hu = parse_cli_json(run_cli(BATCH_OPS, ["phase-status", "--ep-dir", str(wk)]).stdout).get("recommended_next_stage")
    assert run_cli(CLOUD_PIPE, ["confirm-master", "--project-root", str(pr), "--episode", "第01集", "--reviewer", "王剪辑", "--confirm-dialogue", "--confirm-lipsync", "--confirm-subtitles", "--notes", "成片对白口型与烧录字幕均已人工逐镜复核通过"]).returncode == 0

    st_done1 = parse_cli_json(run_cli(BATCH_OPS, ["phase-status", "--ep-dir", str(wk)]).stdout).get("recommended_next_stage")
    bak_p = tmp_base / "master_backup.mp4"
    shutil.move(str(m_p), str(bak_p))
    rp_miss = run_cli(BATCH_OPS, ["phase-status", "--ep-dir", str(wk)])
    jp_miss = parse_cli_json(rp_miss.stdout)
    st_miss = jp_miss.get("recommended_next_stage")
    shutil.move(str(bak_p), str(m_p))
    st_done2 = parse_cli_json(run_cli(BATCH_OPS, ["phase-status", "--ep-dir", str(wk)]).stdout).get("recommended_next_stage")
    ok_p1_1 = st_wait_hu == "WAIT_HUMAN_MASTER_REVIEW" and st_done1 == "ALL_DONE" and st_miss == "P4B_MASTER_VERIFY" and jp_miss.get("master_reverify_required") is True and st_done2 == "ALL_DONE"
    record("V42-P1-1", "技术终检停在 WAIT_HUMAN_MASTER_REVIEW，人工确认升 ALL_DONE，移走母带回退 P4B_MASTER_VERIFY", ok_p1_1, rp_miss.returncode, f"{st_wait_hu} -> {st_done1} -> {st_miss} -> {st_done2}")

    orig_sha = sha256_f(m_p)
    make_test_mp4(m_p, 3840, 2160, 24, 2.0, freq=660)
    new_sha = sha256_f(m_p)
    rp_tamper = run_cli(BATCH_OPS, ["phase-status", "--ep-dir", str(wk)])
    jp_tamper = parse_cli_json(rp_tamper.stdout)
    st_tamper = jp_tamper.get("recommended_next_stage")
    ok_p0_2c = (orig_sha != new_sha) and (st_tamper != "ALL_DONE") and (jp_tamper.get("master_reverify_required") is True)
    record("V43-P0-2c", "封板进入 ALL_DONE 后替换成片母带字节内容，phase-status 因 confirmed_master_sha256 失配自动打回", ok_p0_2c, rp_tamper.returncode, f"next={st_tamper}, reverify={jp_tamper.get('master_reverify_required')}")

    sa_dir = tmp_base / "v42_p1_2"
    sa_dir.mkdir(parents=True, exist_ok=True)
    mf2 = sa_dir / "待生图帧清单.json"
    mf2.write_text(json.dumps({"units": [{"unit": "场景_大厅", "prompt": "16:9 widescreen grand hall empty architectural master shot."}]}, ensure_ascii=False), encoding="utf-8")
    au_empty = sa_dir / "audit_no_units.json"
    au_empty.write_text(json.dumps({"status": "PASS", "units": {}}), encoding="utf-8")
    r_nou = run_cli(BATCH_OPS, ["stamp-visual-audit", "--audit-json", str(au_empty), "--manifest", str(mf2), "--author-agent", "gemini-4-carbon-medium", "--inspector-agent", "gemini-3.8-flash-high", "--author-run-id", "swarm_c_20261119_aabbccdd", "--inspector-run-id", "swarm_f_20261119_11223344"])
    record("V42-P1-2a", "省略逐单元结论仅传顶层 PASS 跑 stamp-visual-audit 返回 exit=2", r_nou.returncode == 2 and "AUDIT_MISSING_PER_UNIT_VERDICTS" in r_nou.stderr, r_nou.returncode, "空 units 阻断")

    au_norun = sa_dir / "audit_no_runid.json"
    au_norun.write_text(json.dumps({"status": "PASS", "units": {"场景_大厅": {"status": "PASS", "notes": "大厅净空构图透视准确无人物水印"}}}, ensure_ascii=False), encoding="utf-8")
    r_nor = run_cli(BATCH_OPS, ["stamp-visual-audit", "--audit-json", str(au_norun), "--manifest", str(mf2), "--author-agent", "gemini-4-carbon-medium", "--inspector-agent", "gemini-3.8-flash-high"])
    record("V42-P1-2b", "省略显式 run_id 跑 stamp-visual-audit 返回 exit=2 禁自动编造", r_nor.returncode == 2 and "AUDIT_MISSING_EXPLICIT_RUN_IDS" in r_nor.stderr, r_nor.returncode, "缺显式 run_id 阻断")

    q42 = tmp_base / "v42_p1_3" / "中间文件" / "在制工作区" / "第01集"
    q42.mkdir(parents=True, exist_ok=True)
    rv1 = run_cli(BATCH_OPS, ["quota-reserve", "--work-dir", str(q42), "--window", "window_01", "--unit-id", "unit_a", "--hard-cap", "60"])
    rid1 = parse_cli_json(rv1.stdout)["reservation_id"]
    rc_ok1 = run_cli(BATCH_OPS, ["quota-reconcile", "--work-dir", str(q42), "--window", "window_01", "--reservation-id", rid1, "--outcome", "ok"])
    rc_ok2 = run_cli(BATCH_OPS, ["quota-reconcile", "--work-dir", str(q42), "--window", "window_01", "--reservation-id", rid1, "--outcome", "ok"])
    rc_unk = run_cli(BATCH_OPS, ["quota-reconcile", "--work-dir", str(q42), "--window", "window_01", "--reservation-id", "rsv_nonexistent_999", "--outcome", "ok"])
    j_ok2 = parse_cli_json(rc_ok2.stdout)
    record("V42-P1-3a", "同一 reservation_id 连续两次 quota-reconcile --outcome ok 保持 used_calls==1 严格幂等", rc_ok1.returncode == 0 and rc_ok2.returncode == 0 and j_ok2.get("used_calls") == 1 and j_ok2.get("idempotent_replay") is True and rc_unk.returncode == 2, rc_ok2.returncode, f"used_calls={j_ok2.get('used_calls')}, replay={j_ok2.get('idempotent_replay')}")

    rv_w2 = run_cli(BATCH_OPS, ["quota-reserve", "--work-dir", str(q42), "--window", "window_02", "--unit-id", "unit_w2", "--hard-cap", "60"])
    rid_w2 = parse_cli_json(rv_w2.stdout)["reservation_id"]
    run_cli(BATCH_OPS, ["quota-reconcile", "--work-dir", str(q42), "--window", "window_02", "--reservation-id", rid_w2, "--outcome", "429", "--retry-after-sec", "90"])
    rv_w3 = run_cli(BATCH_OPS, ["quota-reserve", "--work-dir", str(q42), "--window", "window_03", "--unit-id", "unit_w3", "--hard-cap", "60"])
    j_w3 = parse_cli_json(rv_w3.stdout)
    record("V42-P1-3b", "window_02 触发 429 后切 window_03 调 quota-reserve 返回 exit=4 跨窗全局冷却阻断", rv_w3.returncode == 4 and j_w3.get("status") == "QUOTA_BLOCKED_429_COOLDOWN", rv_w3.returncode, f"status={j_w3.get('status')}")


def test_v42_deep_p1_part2(tmp_base: Path) -> None:
    import cloud_pipeline as cp_mod

    fd = tmp_base / "v42_p1_4"
    fd.mkdir(parents=True, exist_ok=True)
    sp = fd / "shots.json"
    sp.write_text(json.dumps({"shots": [{"shot_id": "shot_01", "start_sec": 0.0, "end_sec": 11.8, "scene": "大殿", "summary": "长镜"}]}, ensure_ascii=False), encoding="utf-8")
    def _omni12(tier: str, route: str, vmode: str, gd: int, ext_s: int | None = None) -> subprocess.CompletedProcess:
        seg_obj: dict[str, Any] = {"segment": "segment_01", "shots": ["shot_01"], "scene": "大殿", "model_route": route, "video_mode": vmode, "gen_duration_sec": gd}
        if ext_s is not None:
            seg_obj["extend_sec"] = ext_s
        rp = fd / "rt.json"
        rp.write_text(json.dumps({"segments": [seg_obj]}, ensure_ascii=False), encoding="utf-8")
        return run_cli(BATCH_OPS, ["build-cuts", "--shots-json", str(sp), "--output-csv", str(fd / "cuts.csv"), "--routing-json", str(rp), "--flow-tier", tier, "--expected-start-sec", "0", "--expected-end-sec", "11.8"])

    r_f12 = _omni12("free", "Omni Flash 1.2 | Ingredients | 12s", "Ingredients", 12)
    r_p20 = _omni12("paid", "Omni Flash 1.2 | Ingredients | 20s", "Ingredients", 20)
    r_fex = _omni12("free", "Omni Flash 1.2 | Extend | 6s", "Extend", 6, ext_s=6)
    r_p12 = _omni12("paid", "Omni Flash 1.2 | Ingredients | 12s", "Ingredients", 12)
    r_pex = _omni12("paid", "Omni Flash 1.2 | Extend | 6s", "Extend", 12, ext_s=6)
    ok_omni_blk = r_f12.returncode == 2 and "FLOW_OMNI_1_2_FREE_CAP_10S_EXCEEDED" in r_f12.stderr and r_p20.returncode == 2 and "FLOW_OMNI_1_2_GEN_CAP_15S_EXCEEDED" in r_p20.stderr and r_fex.returncode == 2 and "FLOW_EXTEND_REQUIRES_PAID_SUBSCRIPTION" in r_fex.stderr
    record("V42-P1-4a", "Omni Flash 1.2 free档12s / paid普通20s / free档Extend 全部返回 exit=2 拦截", ok_omni_blk, r_p20.returncode, "free<=10s, paid<=15s, free禁Extend")
    record("V42-P1-4b", "Omni Flash 1.2 paid档12s普通生成与 Extend +6s 合法放行 (exit=0)", r_p12.returncode == 0 and r_pex.returncode == 0, r_pex.returncode, "paid 12s & Extend 6s 通过")

    ls_base = tmp_base / "v42_p1_5"
    repo_d, raw_v_d, out_v_d, wav_d = ls_base / "mock_LatentSync", ls_base / "raw_vid", ls_base / "out_vid", ls_base / "wavs"
    raw_v_d.mkdir(parents=True, exist_ok=True)
    make_test_mp4(raw_v_d / "segment_02.mp4", 1280, 720, 24, 4.0, video_src="testsrc2", audio_mode="quiet_sfx")
    ls_skip = cp_mod.execute_step2_lipsync_pipeline(raw_v_d, [], ls_base / "skip_out", ls_base / "nonexistent_repo")
    record("V43-P0-1a", "纯超分无对白时 execute_step2_lipsync_pipeline 免检跳过 LatentSync 仓库与权重", ls_skip["status"] == "STEP2_LIPSYNC_SKIPPED_NO_DIALOGUE" and ls_skip["lipsync_initialized"] is False, 0, ls_skip["status"])

    for d in (repo_d / "configs" / "unet", repo_d / "checkpoints" / "whisper", repo_d / "scripts", out_v_d, wav_d):
        d.mkdir(parents=True, exist_ok=True)
    (repo_d / "configs" / "unet" / "stage2_512.yaml").write_text("unet: stage2_512\n", encoding="utf-8")
    (repo_d / "checkpoints" / "latentsync_unet.pt").write_bytes(b"MOCK_CKPT_WEIGHTS")
    (repo_d / "checkpoints" / "whisper" / "tiny.pt").write_bytes(b"MOCK_WHISPER_TINY")
    (repo_d / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    mock_inf_code = (
        "import argparse, json, subprocess, sys\nfrom pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "for f in ('--unet_config_path','--inference_ckpt_path','--inference_steps','--guidance_scale','--video_path','--audio_path','--video_out_path'): p.add_argument(f, required=True)\n"
        "a = p.parse_args()\n"
        "assert Path(a.unet_config_path).is_file() and Path(a.inference_ckpt_path).is_file() and Path(a.audio_path).is_file()\n"
        "(Path(a.video_out_path).parent / 'cli_args.json').write_text(json.dumps(sys.argv), encoding='utf-8')\n"
        "subprocess.run(['ffmpeg','-y','-v','error','-i',a.video_path,'-i',a.audio_path,'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac',a.video_out_path], check=True)\n"
    )
    (repo_d / "scripts" / "inference.py").write_text(mock_inf_code, encoding="utf-8")
    make_test_mp4(raw_v_d / "segment_02.mp4", 1280, 720, 24, 4.0, video_src="testsrc2", audio_mode="quiet_sfx")
    make_test_wav(wav_d / "segment_02_镜头01_沈惊鸿_台词.wav", duration=1.40, freq=880, sr=48000)
    make_test_wav(wav_d / "segment_02_镜头02_顾清歌_台词.wav", duration=1.40, freq=1200, sr=48000)
    sched = [
        {"segment": "segment_02", "out_fname": "segment_02_镜头01_沈惊鸿_台词.wav", "start_in_seg_sec": 0.0, "needs_lipsync": True, "speaker": "沈惊鸿"},
        {"segment": "segment_02", "out_fname": "segment_02_镜头02_顾清歌_台词.wav", "start_in_seg_sec": 2.0, "needs_lipsync": True, "speaker": "顾清歌"},
    ]
    ls_res = cp_mod.execute_step2_lipsync_pipeline(raw_v_d, sched, out_v_d, repo_d, dlg_wav_dir=wav_d, python_bin=sys.executable)
    drv_wav = Path(ls_res["segments"][0]["driver_wav"])
    final_mp4 = Path(ls_res["segments"][0]["output_video"])
    cli_argv = json.loads((out_v_d / "cli_args.json").read_text(encoding="utf-8"))
    drv_r1 = measure_media_window_rms_db(drv_wav, 0.1, 1.3)
    drv_gap = measure_media_window_rms_db(drv_wav, 1.55, 1.85)
    drv_r2 = measure_media_window_rms_db(drv_wav, 2.1, 3.3)
    orig_sfx = measure_media_window_rms_db(raw_v_d / "segment_02.mp4", 0.1, 1.3)
    out_r1 = measure_media_window_rms_db(final_mp4, 0.1, 1.3)
    out_r2 = measure_media_window_rms_db(final_mp4, 2.1, 3.3)
    ok_ls = ls_res["status"] == "STEP2_LIPSYNC_OK" and "--video_out_path" in cli_argv and drv_r1 > -30.0 and drv_gap < -60.0 and drv_r2 > -30.0 and out_r1 < -45.0 and abs(out_r1 - orig_sfx) < 2.5 and abs(out_r2 - orig_sfx) < 2.5
    record("V42-P1-5", "bytedance/LatentSync scripts.inference --video_out_path 真跑双人延迟起音且成品仅留纯环境SFX", ok_ls, 0, f"drv=({drv_r1:.1f},{drv_gap:.1f},{drv_r2:.1f})dB, out_sfx=({out_r1:.1f},{out_r2:.1f})dB")

    git_base = tmp_base / "v43_p0_1b"
    seed_repo, bare_repo, cloned_repo, git_out_d = git_base / "seed_repo", git_base / "bare_latentsync.git", git_base / "clean_clone_target", git_base / "git_out"
    for d in (seed_repo / "configs" / "unet", seed_repo / "checkpoints" / "whisper", seed_repo / "scripts"):
        d.mkdir(parents=True, exist_ok=True)
    (seed_repo / "configs" / "unet" / "stage2_512.yaml").write_text("unet: stage2_512\n", encoding="utf-8")
    (seed_repo / "checkpoints" / "latentsync_unet.pt").write_bytes(b"MOCK_CKPT_16")
    (seed_repo / "checkpoints" / "whisper" / "tiny.pt").write_bytes(b"MOCK_WHISPER_16")
    (seed_repo / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (seed_repo / "scripts" / "inference.py").write_text(mock_inf_code, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(seed_repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "qa@local.test"], cwd=str(seed_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "QA"], cwd=str(seed_repo), check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(seed_repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init mock latentsync 1.6"], cwd=str(seed_repo), check=True, capture_output=True)
    commit_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(seed_repo), capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["git", "clone", "--bare", "-q", str(seed_repo), str(bare_repo)], check=True, capture_output=True)
    ls_boot = cp_mod.execute_step2_lipsync_pipeline(
        raw_v_d, sched, git_out_d, cloned_repo, dlg_wav_dir=wav_d,
        bootstrap_if_missing=True, git_url=str(bare_repo), pinned_commit=commit_sha[:7], python_bin=sys.executable,
    )
    nb_proj = git_base / "nb_check"
    nb_ep, nb_wk = nb_proj / "分集" / "第01集", nb_proj / "中间文件" / "在制工作区" / "第01集"
    (nb_ep / "台词音频").mkdir(parents=True, exist_ok=True)
    (nb_wk / "原始生成视频").mkdir(parents=True, exist_ok=True)
    (nb_wk / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_02,镜头01,0.000,4.000,4.000,4,Omni Flash 1.2 | Ingredients | 4s,Ingredients,书房\n", encoding="utf-8-sig")
    (nb_ep / "第01集_台词生成表.csv").write_text("分段与镜号,说话角色,台词类型,起止时间轴与目标时长,绑定声线母本,CosyVoice2_情绪导演指令,视频通道与口型策略,输出音频文件名,台词正文\nsegment_02_镜头01,角色01_沈惊鸿,说话,0.0s-1.5s (1.5s),角色01_沈惊鸿_说话_声线参考.wav,沉稳,Flow闭嘴后刷口型,segment_02_镜头01_沈惊鸿_台词.wav,你好。\n", encoding="utf-8-sig")
    make_test_wav(nb_ep / "台词音频" / "segment_02_镜头01_沈惊鸿_台词.wav", duration=1.50, freq=330, sr=48000)
    make_test_mp4(nb_wk / "原始生成视频" / "segment_02.mp4", 1920, 1080, 24, 4.0)
    r_nb = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(nb_proj), "--episode", "第01集"])
    nb_src = "".join("".join(c["source"]) for c in json.loads(Path(parse_cli_json(r_nb.stdout)["colab_notebook_ipynb"]).read_text(encoding="utf-8"))["cells"] if c["cell_type"] == "code")
    ok_boot = (
        ls_boot["status"] == "STEP2_LIPSYNC_OK" and ls_boot.get("pinned_commit") == commit_sha[:7] and (cloned_repo / ".git").exists()
        and r_nb.returncode == 0 and all(k in nb_src for k in ("d058a61", "ByteDance/LatentSync-1.6", "stage2_512.yaml", "whisper/tiny.pt"))
        and ("--branch" not in nb_src) and ("v1.5" not in nb_src)
    )
    record("V43-P0-1b", "干净环境 bare Git 自动 clone+detach 跑通且 build-colab 锁 d058a61/LatentSync-1.6/stage2_512/tiny.pt 零--branch/v1.5", ok_boot, r_nb.returncode, f"sha={commit_sha[:7]}, status={ls_boot['status']}")

    pv = tmp_base / "v42_p1_6"
    epv, vdv, rcv = pv / "分集" / "第01集", pv / "分集" / "第01集" / "声线", pv / "中间文件" / "索引与制作记录"
    for d in (epv, vdv, rcv):
        d.mkdir(parents=True, exist_ok=True)
    (rcv / "角色改名与音色映射.json").write_text(json.dumps({"characters": [{"role_id": "角色01", "new_name": "沈惊鸿"}]}, ensure_ascii=False), encoding="utf-8")
    (epv / "第01集_台词生成表.csv").write_text("分段与镜号,说话角色,台词类型,起止时间轴与目标时长,绑定声线母本,CosyVoice2_情绪导演指令,视频通道与口型策略,输出音频文件名,台词正文\nsegment_01_镜头01,角色01_沈惊鸿,说话,0.0s-1.5s (1.5s),角色01_沈惊鸿_说话_声线参考.wav,沉稳,Flow闭嘴后刷口型,segment_01_镜头01_沈惊鸿_台词.wav,出发。\n", encoding="utf-8-sig")
    make_test_wav(vdv / "角色01_沈惊鸿_说话_声线参考.wav", duration=2.40, freq=320, sr=48000)
    make_test_wav(vdv / "角色01_沈惊鸿_心声_声线参考.wav", duration=2.50, freq=360, sr=48000)
    r_no_app = run_cli(CLOUD_PIPE, ["build-voice-colab", "--project-root", str(pv), "--episode", "第01集", "--stage", "dialogue"])
    record("V42-P1-6a", "存在 48kHz WAV 但缺 声线母本批准清单.json 时 build-voice-colab --stage dialogue 阻断", r_no_app.returncode == 2 and "VOICE_REF_NOT_APPROVED_OR_NOT_48K" in r_no_app.stdout, r_no_app.returncode, "缺批准清单阻断")

    assert run_cli(CLOUD_PIPE, ["voice-audit", "--project-root", str(pv), "--episode", "第01集"]).returncode == 0
    make_test_wav(vdv / "角色01_沈惊鸿_说话_声线参考.wav", duration=2.40, freq=320, sr=24000)
    r_24k_va = run_cli(CLOUD_PIPE, ["voice-audit", "--project-root", str(pv), "--episode", "第01集"])
    r_24k_dlg = run_cli(CLOUD_PIPE, ["build-voice-colab", "--project-root", str(pv), "--episode", "第01集", "--stage", "dialogue"])
    record("V42-P1-6b", "24kHz WAV 声线母本跑 voice-audit 与 build-voice-colab --stage dialogue 均返回 exit=2", r_24k_va.returncode == 2 and r_24k_dlg.returncode == 2 and "VOICE_REF_NOT_APPROVED_OR_NOT_48K" in r_24k_dlg.stdout, r_24k_dlg.returncode, "24kHz 硬阻断")

    iso_d = tmp_base / "v42_p1_7_isolated"
    iso_d.mkdir(parents=True, exist_ok=True)
    iso_c = iso_d / "cuts.csv"
    iso_c.write_text("segment,shot,start_sec,end_sec,duration_sec,gen_duration_sec,model_route,video_mode,scene\nsegment_01,镜头01,0.000,1.000,1.000,4,Veo 3.1 Lite | Frames | 4s,Frames,书房\n", encoding="utf-8-sig")
    r_iso = run_cli(BATCH_OPS, ["verify-cuts", "--cuts-csv", str(iso_c)])
    j_iso = parse_cli_json(r_iso.stdout or r_iso.stderr)
    record("V42-P1-7", "空目录无可信外部源仅放 0-1s 单行 cuts.csv 跑 verify-cuts 返回 exit=2 阻断自证", r_iso.returncode == 2 and j_iso.get("error_code") == "VERIFY_CUTS_MISSING_TRUSTED_SOURCE_SPAN", r_iso.returncode, f"err={j_iso.get('error_code')}")

    import v42_fcpxml_and_master_qc as v42_qc
    ct = v42_qc.build_timeline_cutpoint_table_v42([(f"segment_{i:02d}", 1.44) for i in range(1, 6)], 24)
    record("V43-P1-2", "5x1.44s 在 24fps 下累计切点映射 [0,35,69,104,138,173] 与总长 173帧 (17300/2400s)", ct["cutpoint_frames"] == [0, 35, 69, 104, 138, 173] and ct["segment_frame_durations"] == [35, 34, 35, 34, 35] and ct["total_frames"] == 173 and ct["sequence_duration_rational"] == "17300/2400s", 0, f"frames={ct['total_frames']}")

    lg_p = tmp_base / "v43_legacy_fps"
    lg_wk = lg_p / "中间文件" / "在制工作区" / "第01集"
    (lg_wk / "原始生成视频").mkdir(parents=True, exist_ok=True)
    (lg_wk / "Colab_A100_超分任务清单.json").write_text(json.dumps({"target_fps": 60}), encoding="utf-8")
    make_test_mp4(lg_wk / "原始生成视频" / "segment_01.mp4", 3840, 2160, 24, 2.0)
    r_lg = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(lg_p), "--episode", "第01集"])
    j_lg = parse_cli_json(r_lg.stdout)
    record("V43-P1-1", "旧60fps任务单未显式传--target-fps时告警并强制覆盖锁定为24fps", r_lg.returncode == 0 and j_lg.get("target_fps") == 24 and j_lg.get("legacy_plan_fps_conflict") is True and j_lg.get("step2_5_lipsync_enabled") is False and "60" in r_lg.stderr, r_lg.returncode, f"fps={j_lg.get('target_fps')}, conflict={j_lg.get('legacy_plan_fps_conflict')}")


def test_v44_p0_p1_cases(tmp_base: Path) -> None:
    import cloud_pipeline as cp_mod
    import v42_fcpxml_and_master_qc as v42_qc

    fake_drive = tmp_base / "v44_fake_drive"
    nb_proj = fake_drive / "v44_p0_1_proj"
    nb_ep = nb_proj / "分集" / "第01集"
    nb_wk = nb_proj / "中间文件" / "在制工作区" / "第01集"
    (nb_wk / "原始生成视频").mkdir(parents=True, exist_ok=True)
    nb_ep.mkdir(parents=True, exist_ok=True)
    make_test_mp4(nb_wk / "原始生成视频" / "segment_01.mp4", 1920, 1080, 24, 0.5, video_src="testsrc2", audio_mode="quiet_sfx")
    r_bc = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(nb_proj), "--episode", "第01集"])
    j_bc = parse_cli_json(r_bc.stdout)
    nb_path = Path(j_bc["colab_notebook_ipynb"])
    nb_doc = json.loads(nb_path.read_text(encoding="utf-8"))
    code_cells = ["".join(c["source"]) for c in nb_doc["cells"] if c["cell_type"] == "code"]
    cell1_src, cell2_src = code_cells[0], code_cells[1]

    runner_py = tmp_base / "run_nb_v44_p0_1.py"
    runner_py.write_text(
        "import sys, types\n"
        "g = types.ModuleType('google'); gc = types.ModuleType('google.colab')\n"
        "class _D:\n    @staticmethod\n    def mount(p): pass\n    @staticmethod\n    def flush_and_unmount(): pass\n"
        "class _R:\n    @staticmethod\n    def unassign(): pass\n"
        "gc.drive, gc.runtime = _D, _R; g.colab = gc\n"
        "sys.modules['google'] = g; sys.modules['google.colab'] = gc\n"
        f"c1 = {cell1_src!r}.replace(\"/content/drive/MyDrive\", {fake_drive.as_posix()!r})\n"
        f"c2 = {cell2_src!r}\n"
        "ns = {}\nexec(compile(c1, 'cell1', 'exec'), ns)\nexec(compile(c2, 'cell2', 'exec'), ns)\n",
        encoding="utf-8",
    )
    clean_env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
    clean_env["PYTHONUTF8"] = "1"
    r_exec = subprocess.run([sys.executable, str(runner_py)], cwd=str(tmp_base), capture_output=True, text=True, encoding="utf-8", errors="replace", env=clean_env)
    out_4k = nb_wk / "已超分4K_24fps" / "segment_01.mp4"
    p_4k = v42_qc.probe_video_streams_v42(out_4k) if out_4k.is_file() else {"ok": False}
    ok_p0_1 = (
        r_bc.returncode == 0 and r_exec.returncode == 0 and ("跳过 LatentSync" in r_exec.stdout)
        and p_4k.get("ok") and p_4k.get("width") == 3840 and p_4k.get("height") == 2160
        and 23.95 <= float(p_4k.get("fps") or 0.0) <= 24.05 and int(p_4k.get("video_frames") or 0) == 12
        and abs(float(p_4k.get("video_duration_sec") or 0.0) - 0.50) <= 0.05
    )
    record("V44-P0-1", "干净子进程真执行生成 Notebook Cell1+Cell2 自举 vsr_pipeline 产出 3840x2160@24fps 12帧成片", ok_p0_1, r_exec.returncode, f"w={p_4k.get('width')}x{p_4k.get('height')}, fps={p_4k.get('fps')}, frames={p_4k.get('video_frames')}, dur={p_4k.get('video_duration_sec')}, err={r_exec.stderr[-160:] if r_exec.returncode else ''}")

    ok_p0_2 = ("DeepCache==0.1.1" in cell1_src) and ("requirements.txt" in cell1_src) and ("scripts.inference" in cell1_src) and ("'--help'" in cell1_src)
    record("V44-P0-2", "[生成内容检查] 生成 Notebook Step 1 静态包含 DeepCache==0.1.1、requirements.txt 与 scripts.inference --help", ok_p0_2, 0, "DeepCache==0.1.1 + requirements.txt + --help")

    p1_base = tmp_base / "v44_p1_1"
    repo_25, raw_25, out_25, wav_25 = p1_base / "repo_25", p1_base / "raw", p1_base / "out", p1_base / "wavs"
    for d in (repo_25 / "configs" / "unet", repo_25 / "checkpoints" / "whisper", repo_25 / "scripts", raw_25, out_25, wav_25):
        d.mkdir(parents=True, exist_ok=True)
    (repo_25 / "configs" / "unet" / "stage2_512.yaml").write_text("unet: stage2_512\n", encoding="utf-8")
    (repo_25 / "checkpoints" / "latentsync_unet.pt").write_bytes(b"MOCK_CKPT")
    (repo_25 / "checkpoints" / "whisper" / "tiny.pt").write_bytes(b"MOCK_WHISPER")
    (repo_25 / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    mock_25_code = (
        "import argparse, subprocess\n"
        "p = argparse.ArgumentParser()\n"
        "for f in ('--unet_config_path','--inference_ckpt_path','--inference_steps','--guidance_scale','--video_path','--audio_path','--video_out_path'): p.add_argument(f, required=True)\n"
        "a = p.parse_args()\n"
        "subprocess.run(['ffmpeg','-y','-v','error','-i',a.video_path,'-i',a.audio_path,'-vf','fps=25','-map','0:v:0','-map','1:a:0','-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-c:a','aac',a.video_out_path], check=True)\n"
    )
    (repo_25 / "scripts" / "inference.py").write_text(mock_25_code, encoding="utf-8")
    make_test_mp4(raw_25 / "segment_01.mp4", 1920, 1080, 24, 1.0, video_src="testsrc2", audio_mode="quiet_sfx")
    make_test_wav(wav_25 / "segment_01_镜头01_沈惊鸿_台词.wav", duration=0.80, freq=880, sr=48000)
    sched_25 = [{"segment": "segment_01", "out_fname": "segment_01_镜头01_沈惊鸿_台词.wav", "start_in_seg_sec": 0.0, "needs_lipsync": True, "speaker": "沈惊鸿"}]
    res_25 = cp_mod.execute_step2_lipsync_pipeline(raw_25, sched_25, out_25, repo_25, dlg_wav_dir=wav_25, python_bin=sys.executable, target_fps=24)
    s1_mp4 = Path(res_25["segments"][0]["output_video"])
    s1_m = v42_qc.probe_video_streams_v42(s1_mp4)
    orig_rms = measure_media_window_rms_db(raw_25 / "segment_01.mp4", 0.1, 0.8)
    s1_rms = measure_media_window_rms_db(s1_mp4, 0.1, 0.8)
    s2_mp4 = p1_base / "segment_01_4k24.mp4"
    vsr_env = os.environ.copy()
    vsr_env["PYTHONPATH"] = str(SCRIPTS_DIR)
    r_vsr = subprocess.run([sys.executable, "-m", "vsr_pipeline.batch_processor", "--input_file", str(s1_mp4), "--output_file", str(s2_mp4), "--scale", "2", "--target_fps", "24", "--audio_codec", "copy"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=vsr_env)
    s2_m = v42_qc.probe_video_streams_v42(s2_mp4) if s2_mp4.is_file() else {"ok": False}
    ok_p1_1 = (
        res_25["status"] == "STEP2_LIPSYNC_OK" and 23.95 <= float(s1_m.get("fps") or 0.0) <= 24.05
        and int(s1_m.get("video_frames") or 0) == 24 and s1_rms < -45.0 and abs(s1_rms - orig_rms) < 2.5
        and r_vsr.returncode == 0 and s2_m.get("ok") and s2_m.get("width") == 3840 and s2_m.get("height") == 2160
        and 23.95 <= float(s2_m.get("fps") or 0.0) <= 24.05 and int(s2_m.get("video_frames") or 0) == 24
        and abs(float(s2_m.get("video_duration_sec") or 0.0) - 1.0) <= 0.05
    )
    record("V44-P1-1", "mock LatentSync 输出 25fps 经 Step1 严转回 24fps 仅留纯SFX，再经 vsr_pipeline 输出 3840x2160@24fps 24帧", ok_p1_1, r_vsr.returncode, f"s1_fps={s1_m.get('fps')}, s1_sfx={s1_rms:.1f}dB, s2={s2_m.get('width')}x{s2_m.get('height')}@{s2_m.get('fps')}f{s2_m.get('video_frames')}")


def test_v45_p1_cases(tmp_base: Path) -> None:
    """V4.5 五审闭环反例：口型短输出缺帧硬阻断 + 显式 --backend realesrgan 报错阻断。"""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import v42_fcpxml_and_master_qc as v42_qc

    p1_short = tmp_base / "v45_p1_1_short"
    repo_sh, raw_sh, out_sh, wav_sh = p1_short / "repo_sh", p1_short / "raw", p1_short / "out", p1_short / "wavs"
    for d in (repo_sh / "configs" / "unet", repo_sh / "checkpoints" / "whisper", repo_sh / "scripts", raw_sh, out_sh, wav_sh):
        d.mkdir(parents=True, exist_ok=True)
    (repo_sh / "configs" / "unet" / "stage2_512.yaml").write_text("unet: stage2_512\n", encoding="utf-8")
    (repo_sh / "checkpoints" / "latentsync_unet.pt").write_bytes(b"MOCK_CKPT")
    (repo_sh / "checkpoints" / "whisper" / "tiny.pt").write_bytes(b"MOCK_WHISPER")
    (repo_sh / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    mock_short_code = (
        "import argparse, subprocess\n"
        "p = argparse.ArgumentParser()\n"
        "for f in ('--unet_config_path','--inference_ckpt_path','--inference_steps','--guidance_scale','--video_path','--audio_path','--video_out_path'): p.add_argument(f, required=True)\n"
        "a = p.parse_args()\n"
        "subprocess.run(['ffmpeg','-y','-v','error','-i',a.video_path,'-t','0.48','-vf','fps=24','-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-an',a.video_out_path], check=True)\n"
    )
    (repo_sh / "scripts" / "inference.py").write_text(mock_short_code, encoding="utf-8")
    make_test_mp4(raw_sh / "segment_01.mp4", 1920, 1080, 24, 1.0, video_src="testsrc2", audio_mode="quiet_sfx")
    make_test_wav(wav_sh / "segment_01_镜头01_沈惊鸿_台词.wav", duration=0.80, freq=880, sr=48000)
    sched_sh = [{"segment": "segment_01", "out_fname": "segment_01_镜头01_沈惊鸿_台词.wav", "start_in_seg_sec": 0.0, "needs_lipsync": True, "speaker": "沈惊鸿"}]
    err_msg = ""
    raised_rt = False
    try:
        v42_qc.execute_step2_lipsync_pipeline(raw_sh, sched_sh, out_sh, repo_sh, dlg_wav_dir=wav_sh, python_bin=sys.executable, target_fps=24)
    except RuntimeError as exc:
        raised_rt = True
        err_msg = str(exc)
    ok_v45_1 = raised_rt and ("missing_frames=" in err_msg) and ("12" in err_msg or "missing_frames=12" in err_msg)
    record("V45-P1-1", "1.0s@24fps原片经mock口型仅输出0.48s(12帧)短画面时硬抛RuntimeError并报出missing_frames", ok_v45_1, 1 if raised_rt else 0, err_msg[:180])

    re_out = p1_short / "realesrgan_blocked.mp4"
    vsr_env_nocuda = os.environ.copy()
    vsr_env_nocuda["PYTHONPATH"] = str(SCRIPTS_DIR)
    vsr_env_nocuda["CUDA_VISIBLE_DEVICES"] = ""
    r_re = subprocess.run(
        [sys.executable, "-m", "vsr_pipeline.batch_processor", "--input_file", str(raw_sh / "segment_01.mp4"), "--output_file", str(re_out), "--scale", "2", "--target_fps", "24", "--backend", "realesrgan"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=vsr_env_nocuda,
    )
    combined_out = (r_re.stdout or "") + "\n" + (r_re.stderr or "")
    ok_v45_2 = (r_re.returncode == 2) and ("VSR_BACKEND_UNAVAILABLE" in combined_out) and ("VSR_OK" not in combined_out) and (not re_out.exists())
    record("V45-P1-2", "无CUDA环境下显式指定 --backend realesrgan 必须返回 exit=2 与 VSR_BACKEND_UNAVAILABLE，严禁静默回退 ffmpeg_lanczos", ok_v45_2, r_re.returncode, (r_re.stdout or r_re.stderr).strip()[:160])


def test_v46_p0_cases(tmp_base: Path) -> None:
    d46 = tmp_base / "v46_p0"
    d46.mkdir(parents=True, exist_ok=True)
    v720 = d46 / "segment_01.mp4"
    make_test_mp4(v720, 1280, 720, 24, 0.25, video_src="testsrc2", audio_mode="quiet_sfx")
    vsr_env = {**os.environ, "PYTHONPATH": str(SCRIPTS_DIR)}
    lan_out = d46 / "lan_4k.mp4"
    r_lan = subprocess.run([sys.executable, "-m", "vsr_pipeline.batch_processor", "--input_file", str(v720), "--output_file", str(lan_out), "--target_fps", "24", "--backend", "ffmpeg_lanczos"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=vsr_env)
    j_lan = parse_cli_json(r_lan.stdout)
    record("V46-P0-1", "ffmpeg_lanczos 720p->4K 单步直放 (3840x2160@24fps 6帧 High profile)", r_lan.returncode == 0 and j_lan.get("output", {}).get("width") == 3840 and j_lan.get("output", {}).get("video_frames") == 6, r_lan.returncode, f"out={j_lan.get('output')}")
    import torch  # type: ignore
    if torch.cuda.is_available():
        re_live = d46 / "re_live_4k.mp4"
        r_rl = subprocess.run([sys.executable, "-m", "vsr_pipeline.batch_processor", "--input_file", str(v720), "--output_file", str(re_live), "--target_fps", "24", "--backend", "realesrgan", "--vsr-preset", "live"], capture_output=True, text=True, encoding="utf-8", errors="replace", env=vsr_env)
        j_rl = parse_cli_json(r_rl.stdout)
        record("V46-P0-2", "spandrel Real-ESRGAN GPU FP16 720p->4K (RealESRGAN_x4plus.pth + FP32 bicubic 直出 3840x2160@24fps 6帧)", r_rl.returncode == 0 and j_rl.get("backend_used") == "realesrgan" and j_rl.get("model_weight") == "RealESRGAN_x4plus.pth" and j_rl.get("output", {}).get("width") == 3840 and j_rl.get("output", {}).get("video_frames") == 6, r_rl.returncode, f"wgt={j_rl.get('model_weight')}")


def test_v46_four_profiles_cases(tmp_base: Path) -> None:
    d4 = tmp_base / "v46_profiles"
    d4.mkdir(parents=True, exist_ok=True)
    vsr_env = {**os.environ, "PYTHONPATH": str(SCRIPTS_DIR), "CUDA_VISIBLE_DEVICES": ""}
    v1080 = d4 / "live_in_1080p.mp4"
    make_test_mp4(v1080, 1920, 1080, 24, 0.25, video_src="testsrc2", audio_mode="quiet_sfx")
    live_out = d4 / "live_1k_pass.mp4"
    r_lp = subprocess.run(
        [sys.executable, "-m", "vsr_pipeline.batch_processor", "--input_file", str(v1080), "--output_file", str(live_out), "--p4-profile", "live_1k", "--target_fps", "24", "--backend", "realesrgan"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=vsr_env,
    )
    j_lp = parse_cli_json(r_lp.stdout)
    ok_lp = r_lp.returncode == 0 and j_lp.get("backend_used") == "passthrough_1080p_high" and j_lp.get("model_weight") == "none" and j_lp.get("output", {}).get("width") == 1920 and j_lp.get("output", {}).get("height") == 1080
    record("V46-P4-1", "live_1k 对 1080p 源直接走 passthrough_1080p_high 封装（严禁盲目放大成假4K）", ok_lp, r_lp.returncode, f"backend={j_lp.get('backend_used')}, out={j_lp.get('output')}")

    proj = d4 / "proj_anime_1k"
    work = proj / "中间文件" / "在制工作区" / "第01集"
    ep_d = proj / "分集" / "第01集"
    (work / "原始生成视频").mkdir(parents=True, exist_ok=True)
    ep_d.mkdir(parents=True, exist_ok=True)
    (work / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,model\nsegment_01,镜头01,0.0,1.0,1.0,Omni Flash 1.2\n", encoding="utf-8")
    make_test_mp4(work / "原始生成视频" / "segment_01.mp4", 1280, 720, 24, 1.0, video_src="testsrc2", audio_mode="quiet_sfx")
    r_bc = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(proj), "--episode", "第01集", "--p4-profile", "anime_1k"])
    j_bc = parse_cli_json(r_bc.stdout)
    make_test_mp4(work / "已超分1K_24fps" / "segment_01.mp4", 1920, 1080, 24, 1.0, video_src="testsrc2", audio_mode="quiet_sfx")
    r_xml = run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(proj), "--episode", "第01集"])
    j_xml = parse_cli_json(r_xml.stdout)
    xml_txt = (ep_d / "第01集_达芬奇自动时间线.fcpxml").read_text(encoding="utf-8") if (ep_d / "第01集_达芬奇自动时间线.fcpxml").exists() else ""
    make_test_mp4(ep_d / "第01集_1K24fps_成片.mp4", 1920, 1080, 24, 1.0, video_src="testsrc2", audio_mode="speech_like")
    r_vm = run_cli(CLOUD_PIPE, ["phase4-verify-master", "--project-root", str(proj), "--episode", "第01集"])
    j_vm = parse_cli_json(r_vm.stdout)
    ok_a1k = (
        r_bc.returncode == 0 and j_bc.get("p4_profile") == "anime_1k" and "已超分1K_24fps" in str(j_bc.get("done_vsr_dir") or "")
        and r_xml.returncode == 0 and j_xml.get("fcpxml_1_9_dtd_valid") is True and "FFVideoFormat1920x1080p24" in xml_txt
        and r_vm.returncode == 0 and j_vm.get("status") == "MASTER_TECH_SPEC_PASSED" and j_vm.get("target_resolution") == "1920x1080"
    )
    record("V46-P4-2", "anime_1k 全链路(build-colab -> 自动继承 build-fcpxml FFVideoFormat1920x1080p24+DTD -> phase4-verify-master 1920x1080)", ok_a1k, r_vm.returncode, f"xml_ok={j_xml.get('fcpxml_1_9_dtd_valid')}, vm={j_vm.get('status')}/{j_vm.get('target_resolution')}")

    proj_l4k = d4 / "proj_live_4k"
    (proj_l4k / "中间文件" / "在制工作区" / "第01集" / "原始生成视频").mkdir(parents=True, exist_ok=True)
    r_l4k = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(proj_l4k), "--episode", "第01集", "--p4-profile", "live_4k"])
    j_l4k = parse_cli_json(r_l4k.stdout)
    ok_l4k = r_l4k.returncode == 0 and j_l4k.get("p4_profile") == "live_4k" and j_l4k.get("profile_status") == "reserved_not_recommended_yet" and j_l4k.get("target_resolution") == "3840x2160" and j_l4k.get("vsr_preset") == "live"
    record("V46-P4-3", "live_4k 预留档标记回归 (profile_status=reserved_not_recommended_yet, 3840x2160, vsr_preset=live)", ok_l4k, r_l4k.returncode, f"status={j_l4k.get('profile_status')}, res={j_l4k.get('target_resolution')}")

    p_auto_a, p_auto_l = d4 / "proj_auto_anime", d4 / "proj_auto_live"
    for p_root, style_val in ((p_auto_a, "日漫赛璐璐2D动画风"), (p_auto_l, "电影写实古装真人实拍")):
        wk = p_root / "中间文件" / "在制工作区" / "第01集"
        (wk / "原始生成视频").mkdir(parents=True, exist_ok=True)
        (wk / "style_and_cast.json").write_text(json.dumps({"visual_style": style_val}, ensure_ascii=False), encoding="utf-8")
    r_aa = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(p_auto_a), "--episode", "第01集"])
    r_al = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(p_auto_l), "--episode", "第01集"])
    j_aa, j_al = parse_cli_json(r_aa.stdout), parse_cli_json(r_al.stdout)
    ok_auto = (
        r_aa.returncode == 0 and j_aa.get("p4_profile") == "anime_1k" and j_aa.get("target_resolution") == "1920x1080"
        and r_al.returncode == 0 and j_al.get("p4_profile") == "live_1k" and j_al.get("target_resolution") == "1920x1080"
    )
    record("V46-P4-4", "未传 --p4-profile 时按第一棒 style_and_cast.json 画风自动路由至默认 1K (anime_1k / live_1k)", ok_auto, 0 if ok_auto else 1, f"anime={j_aa.get('p4_profile')}/{j_aa.get('target_resolution')}, live={j_al.get('p4_profile')}/{j_al.get('target_resolution')}")


def test_v46_p1_mixed_fps_cases(tmp_base: Path) -> None:
    """V4.6 收尾回归：24/25/30fps 混合源对白口型统一锁定 24帧/1.000s + 双向过长/过短硬拦截。"""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import v42_fcpxml_and_master_qc as v42_qc

    mx_base = tmp_base / "v46_p1_mixed_fps"
    repo_mx, wav_mx = mx_base / "repo_mx", mx_base / "wavs"
    for d in (repo_mx / "configs" / "unet", repo_mx / "checkpoints" / "whisper", repo_mx / "scripts", wav_mx):
        d.mkdir(parents=True, exist_ok=True)
    (repo_mx / "configs" / "unet" / "stage2_512.yaml").write_text("unet: stage2_512\n", encoding="utf-8")
    (repo_mx / "checkpoints" / "latentsync_unet.pt").write_bytes(b"MOCK_CKPT")
    (repo_mx / "checkpoints" / "whisper" / "tiny.pt").write_bytes(b"MOCK_WHISPER")
    (repo_mx / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    mock_ok_code = (
        "import argparse, subprocess\n"
        "p = argparse.ArgumentParser()\n"
        "for f in ('--unet_config_path','--inference_ckpt_path','--inference_steps','--guidance_scale','--video_path','--audio_path','--video_out_path'): p.add_argument(f, required=True)\n"
        "a = p.parse_args()\n"
        "subprocess.run(['ffmpeg','-y','-v','error','-i',a.video_path,'-i',a.audio_path,'-vf','fps=25','-map','0:v:0','-map','1:a:0','-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-c:a','aac',a.video_out_path], check=True)\n"
    )
    (repo_mx / "scripts" / "inference.py").write_text(mock_ok_code, encoding="utf-8")
    make_test_wav(wav_mx / "segment_01_镜头01_沈惊鸿_台词.wav", duration=0.80, freq=880, sr=48000)
    sched_mx = [{"segment": "segment_01", "out_fname": "segment_01_镜头01_沈惊鸿_台词.wav", "start_in_seg_sec": 0.0, "needs_lipsync": True, "speaker": "沈惊鸿"}]

    fps_summary: list[str] = []
    all_mx_ok = True
    for fps_in in (24, 25, 30):
        raw_i, out_i = mx_base / f"raw_{fps_in}", mx_base / f"out_{fps_in}"
        raw_i.mkdir(parents=True, exist_ok=True); out_i.mkdir(parents=True, exist_ok=True)
        make_test_mp4(raw_i / "segment_01.mp4", 1280, 720, fps_in, 1.0, video_src="testsrc2", audio_mode="quiet_sfx")
        res_i = v42_qc.execute_step2_lipsync_pipeline(raw_i, sched_mx, out_i, repo_mx, dlg_wav_dir=wav_mx, python_bin=sys.executable, target_fps=24)
        pm = v42_qc.probe_video_streams_v42(Path(res_i["segments"][0]["output_video"]))
        dur_v, fr_v = float(pm.get("video_duration_sec") or 0.0), int(pm.get("video_frames") or 0)
        fps_summary.append(f"{fps_in}fps->{fr_v}f/{dur_v:.4f}s")
        if not (fr_v == 24 and abs(dur_v - 1.0) <= 0.05 and 23.95 <= float(pm.get("fps") or 0.0) <= 24.05):
            all_mx_ok = False
    record("V46-P1-MIXED-FPS", "1.0s 的 24/25/30fps 混合源经 Step1 口型封装均精确输出 24帧/1.000s（误差<=0.05s）", all_mx_ok, 0 if all_mx_ok else 1, ", ".join(fps_summary))

    bidir_notes: list[str] = []
    bidir_ok = True
    prev_pydontwrite = os.environ.get("PYTHONDONTWRITEBYTECODE")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        for tag, n_fake, exp_key in (("long30f", 30, "excess_frames=6"), ("short18f", 18, "missing_frames=6")):
            raw_b, out_b = mx_base / f"raw_{tag}", mx_base / f"out_{tag}"
            raw_b.mkdir(parents=True, exist_ok=True); out_b.mkdir(parents=True, exist_ok=True)
            make_test_mp4(raw_b / "segment_01.mp4", 1280, 720, 24, 1.0, video_src="testsrc2", audio_mode="quiet_sfx")
            mock_bad = (
                "import argparse, subprocess\n"
                "p = argparse.ArgumentParser()\n"
                "for f in ('--unet_config_path','--inference_ckpt_path','--inference_steps','--guidance_scale','--video_path','--audio_path','--video_out_path'): p.add_argument(f, required=True)\n"
                "a = p.parse_args()\n"
                f"subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=size=1280x720:rate=25','-frames:v','{n_fake}','-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-an',a.video_out_path], check=True)\n"
            )
            shutil.rmtree(repo_mx / "scripts" / "__pycache__", ignore_errors=True)
            (repo_mx / "scripts" / "inference.py").write_text(mock_bad, encoding="utf-8")
            hit_err = ""
            try:
                v42_qc.execute_step2_lipsync_pipeline(raw_b, sched_mx, out_b, repo_mx, dlg_wav_dir=wav_mx, python_bin=sys.executable, target_fps=24)
            except RuntimeError as exc:
                hit_err = str(exc)
            ok_b = (exp_key in hit_err)
            bidir_ok = bidir_ok and ok_b
            bidir_notes.append(f"{tag}:{'BLOCKED' if ok_b else 'LEAKED'}({exp_key})")
    finally:
        if prev_pydontwrite is None:
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        else:
            os.environ["PYTHONDONTWRITEBYTECODE"] = prev_pydontwrite
    record("V46-P1-BIDIR-GATE", "Step1 口型双向时长/帧数门禁同时拦截过长(30帧/1.25s)与过短(18帧/0.75s)异常输出", bidir_ok, 0 if bidir_ok else 1, "; ".join(bidir_notes))


def test_v46_aspect_ratio_916_case(tmp_base: Path) -> None:
    """V4.6 全链路 9:16 竖屏选项回归：透传、人物词约束、1080x1920/2160x3840、FCPXML 1.9 DTD 及横竖屏验收拦截。"""
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import batch_ops as bo
    from vsr_pipeline import batch_processor as bp_mod, realesrgan_engine as re_mod

    proj = tmp_base / "v46_ar_916"
    r_init = run_cli(BATCH_OPS, ["init", "--project-root", str(proj), "--episode", "第01集", "--aspect-ratio", "9:16"])
    ep_d = proj / "分集" / "第01集"
    work = proj / "中间文件" / "在制工作区" / "第01集"
    ar_auto = bo.resolve_project_aspect_ratio(proj, ep_dir=ep_d)
    p_txt = work / "char_916_prompt.txt"
    p_txt.write_text("Single portrait of hero in dark coat.", encoding="utf-8")
    enforced = bo._enforce_character_format_a_prompt(p_txt, {"type": "character_portrait"}, "C01_主角_肖像", aspect_ratio=ar_auto)
    char_ok = ("9:16 vertical portrait" in enforced) and ("16:9 widescreen" not in enforced)

    s1k_re = re_mod.get_p4_profile_spec("anime_1k", aspect_ratio="9:16")
    s4k_bp = bp_mod.get_p4_profile_spec("anime_4k", aspect_ratio="9:16")
    spec_ok = (s1k_re["width"], s1k_re["height"]) == (1080, 1920) and (s4k_bp["width"], s4k_bp["height"]) == (2160, 3840)

    (work / "原始生成视频").mkdir(parents=True, exist_ok=True)
    (work / "cuts.csv").write_text("segment,shot,start_sec,end_sec,duration_sec,model\nsegment_01,镜头01,0.0,1.0,1.0,Omni Flash 1.2\n", encoding="utf-8")
    make_test_mp4(work / "原始生成视频" / "segment_01.mp4", 720, 1280, 24, 1.0, video_src="testsrc2", audio_mode="quiet_sfx")
    r_bc4k = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(proj), "--episode", "第01集", "--p4-profile", "anime_4k"])
    j_bc4k = parse_cli_json(r_bc4k.stdout)
    r_bc1k = run_cli(CLOUD_PIPE, ["build-colab", "--project-root", str(proj), "--episode", "第01集", "--p4-profile", "anime_1k"])
    j_bc1k = parse_cli_json(r_bc1k.stdout)
    tasks_doc = json.loads((work / "phase4_tasks.json").read_text(encoding="utf-8")) if (work / "phase4_tasks.json").exists() else {}
    nb_txt = (ep_d / "第01集_Colab_A100_1K24fps_超分流水线.ipynb").read_text(encoding="utf-8") if (ep_d / "第01集_Colab_A100_1K24fps_超分流水线.ipynb").exists() else ""
    bc_ok = (
        r_bc4k.returncode == 0 and j_bc4k.get("target_resolution") == "2160x3840" and j_bc4k.get("aspect_ratio") == "9:16"
        and r_bc1k.returncode == 0 and j_bc1k.get("target_resolution") == "1080x1920" and tasks_doc.get("aspect_ratio") == "9:16"
        and tasks_doc.get("target_width") == 1080 and tasks_doc.get("target_height") == 1920 and "ASPECT_RATIO = '9:16'" in nb_txt
    )

    make_test_mp4(work / "已超分1K_24fps" / "segment_01.mp4", 1080, 1920, 24, 1.0, video_src="testsrc2", audio_mode="quiet_sfx")
    r_xml = run_cli(CLOUD_PIPE, ["build-fcpxml", "--project-root", str(proj), "--episode", "第01集"])
    j_xml = parse_cli_json(r_xml.stdout)
    xml_txt = (ep_d / "第01集_达芬奇自动时间线.fcpxml").read_text(encoding="utf-8") if (ep_d / "第01集_达芬奇自动时间线.fcpxml").exists() else ""
    xml_ok = (
        r_xml.returncode == 0 and j_xml.get("fcpxml_1_9_dtd_valid") is True
        and 'FFVideoFormat1080x1920p24' in xml_txt and 'width="1080"' in xml_txt and 'height="1920"' in xml_txt
    )

    good_mp4 = ep_d / "第01集_1K24fps_成片.mp4"
    bad_mp4 = ep_d / "第01集_错误横屏成片.mp4"
    make_test_mp4(good_mp4, 1080, 1920, 24, 1.0, video_src="testsrc2", audio_mode="speech_like")
    make_test_mp4(bad_mp4, 1920, 1080, 24, 1.0, video_src="testsrc2", audio_mode="speech_like")
    r_ok = run_cli(CLOUD_PIPE, ["phase4-verify-master", "--project-root", str(proj), "--episode", "第01集", "--master-mp4", str(good_mp4)])
    j_ok = parse_cli_json(r_ok.stdout)
    r_bad = run_cli(CLOUD_PIPE, ["phase4-verify-master", "--project-root", str(proj), "--episode", "第01集", "--master-mp4", str(bad_mp4)])
    j_bad = parse_cli_json(r_bad.stdout)
    qc_ok = (
        r_ok.returncode == 0 and j_ok.get("status") == "MASTER_TECH_SPEC_PASSED" and j_ok.get("target_resolution") == "1080x1920"
        and r_bad.returncode != 0 and j_bad.get("status") == "MASTER_VERIFY_FAILED"
    )

    all_916_ok = r_init.returncode == 0 and ar_auto == "9:16" and char_ok and spec_ok and bc_ok and xml_ok and qc_ok
    record(
        "V46-AR-916",
        "全链路 9:16 竖屏回归 (自动透传 + 竖屏人物约束 + 1080x1920/2160x3840 + FCPXML 1.9 DTD + 竖屏验收/横屏拦截)",
        all_916_ok, 0 if all_916_ok else 1,
        f"ar={ar_auto}, char={char_ok}, spec={spec_ok}, bc={bc_ok}, xml={xml_ok}, qc={qc_ok}",
    )


def test_v47_voxcpm2_cases(tmp_base: Path) -> None:
    import batch_ops as bo_mod

    pv = tmp_base / "v47_vox"
    rc = pv / "中间文件" / "索引与制作记录"
    (pv / "分集" / "第01集").mkdir(parents=True, exist_ok=True)
    rc.mkdir(parents=True, exist_ok=True)
    (rc / "角色改名与音色映射.json").write_text(json.dumps({"characters": [{"role_id": "角色01", "display_name": "杨惠敏", "voice_style": "清亮坚定女声"}, {"role_id": "角色02", "name": "谢晋元"}]}, ensure_ascii=False), encoding="utf-8")
    roles = bo_mod._read_registered_voice_roles(pv, "第01集")
    record("V47-1", "_read_registered_voice_roles 兼容 display_name / name / new_name", roles == ["杨惠敏", "谢晋元"], 0, f"roles={roles}")

    def nb_of(extra: list[str]) -> tuple[int, dict, dict, str]:
        r = run_cli(CLOUD_PIPE, ["build-voice-colab", "--project-root", str(pv), "--episode", "第01集", "--stage", "voice-ref", *extra])
        j = parse_cli_json(r.stdout) if r.returncode == 0 else {}
        doc = json.loads(Path(j["notebook_ipynb"]).read_text(encoding="utf-8")) if j else {}
        src = "\n".join("".join(c["source"]) for c in doc.get("cells", []) if c["cell_type"] == "code")
        return r.returncode, j, doc, src

    rc0, j0, d0, s0 = nb_of([])
    compiled = False
    try:
        compile(s0, "vox_nb", "exec"); compiled = True
    except SyntaxError:
        pass
    record("V47-2", "build-voice-colab 默认 tts_engine=voxcpm2 + gpu=L4 + gpuType=L4 + openbmb/VoxCPM2 原生48k + 无 CosyVoice 加载", rc0 == 0 and j0.get("tts_engine") == "voxcpm2" and j0.get("gpu") == "L4" and d0["metadata"]["colab"]["gpuType"] == "L4" and "openbmb/VoxCPM2" in s0 and "VOXCPM2_NOT_NATIVE_48K" in s0 and "reference_wav_path" in s0 and "CosyVoice2(" not in s0 and compiled, rc0, f"engine={j0.get('tts_engine')}, gpu={j0.get('gpu')}")
    record("V47-3", "VoxCPM2 声线母本 2.05–2.95s 自然时长重试 + 首尾裁剪淡入淡出 + display_name + 无 atempo 变速", "2.05 <= d_try <= 2.95" in s0 and "vox_trim_fade" in s0 and "display_name" in s0 and "atempo" not in s0.split("gen_records, sha_map_out = [], {}")[1].split("gen_records.append(")[0] and "/tmp/rclone.conf" in s0, 0, "voice-ref 生成逻辑")

    rc1, j1, d1, s1 = nb_of(["--tts-engine", "cosyvoice2"])
    record("V47-4", "--tts-engine cosyvoice2 兜底：默认 gpu=T4 + CosyVoice2 加载保留", rc1 == 0 and j1.get("tts_engine") == "cosyvoice2" and j1.get("gpu") == "T4" and d1["metadata"]["colab"]["gpuType"] == "T4" and "CosyVoice2(" in s1, rc1, f"engine={j1.get('tts_engine')}, gpu={j1.get('gpu')}")

    rc2, j2, d2, s2 = nb_of(["--gpu", "A100"])
    record("V47-5", "显式 --gpu A100 覆盖引擎默认分档", rc2 == 0 and j2.get("gpu") == "A100" and d2["metadata"]["colab"]["gpuType"] == "A100", rc2, f"gpu={j2.get('gpu')}")

    rb = run_cli(BATCH_OPS, ["build-voice-colab", "--project-root", str(pv), "--episode", "第01集", "--stage", "voice-ref"])
    jb = parse_cli_json(rb.stdout) if rb.returncode == 0 else {}
    record("V47-6", "batch_ops build-voice-colab 默认转发 voxcpm2/L4", rb.returncode == 0 and jb.get("tts_engine") == "voxcpm2" and jb.get("gpu") == "L4", rb.returncode, f"engine={jb.get('tts_engine')}")

    # V47-7..10: 第三棒 dialogue 默认 IndexTTS-2.5 + 按需 Fish S2 Pro (H200) / VoxCPM 2 + 误用阻断
    vd = pv / "分集" / "第01集" / "声线"
    vd.mkdir(parents=True, exist_ok=True)
    for rid, rnm, fs, fo in (("角色01", "杨惠敏", 330, 360), ("角色02", "谢晋元", 240, 265)):
        make_test_wav(vd / f"{rid}_{rnm}_说话_声线参考.wav", duration=2.45, freq=fs, sr=48000)
        make_test_wav(vd / f"{rid}_{rnm}_心声_声线参考.wav", duration=2.55, freq=fo, sr=48000)
    (pv / "分集" / "第01集" / "第01集_台词生成表.csv").write_text(
        "分段与镜号,说话角色,绑定声线母本,台词类型,视频通道与口型策略,情绪导演指令,台词正文,起止时间轴与目标时长,输出音频文件名\n"
        "segment_01_镜头01,角色01_杨惠敏,角色01_杨惠敏_说话_声线参考.wav,说话,Flow闭嘴后刷口型,坚定微颤,请把国旗交给我！,0.0s-1.8s (1.8s),segment_01_镜头01_杨惠敏_台词.wav\n",
        encoding="utf-8-sig",
    )
    r_va_v = run_cli(CLOUD_PIPE, ["voice-audit", "--project-root", str(pv), "--episode", "第01集"])

    def dlg_nb_of(extra: list[str]) -> tuple[int, dict, dict, str, bool]:
        r = run_cli(CLOUD_PIPE, ["build-voice-colab", "--project-root", str(pv), "--episode", "第01集", "--stage", "dialogue", *extra])
        j = parse_cli_json(r.stdout) if r.returncode == 0 else {}
        doc = json.loads(Path(j["notebook_ipynb"]).read_text(encoding="utf-8")) if j else {}
        src = "\n".join("".join(c["source"]) for c in doc.get("cells", []) if c["cell_type"] == "code")
        ok_c = False
        if src:
            try:
                compile(src, "dlg_nb", "exec"); ok_c = True
            except SyntaxError:
                pass
        return r.returncode, j, doc, src, ok_c

    rc7, j7, d7, s7, c7 = dlg_nb_of([])
    record("V47-7", "stage=dialogue 默认自动路由 indextts25 + L4 + 独立 uv + IndexTTS-2.5 + 无 CosyVoice", r_va_v.returncode == 0 and rc7 == 0 and c7 and j7.get("tts_engine") == "indextts25" and j7.get("gpu") == "L4" and "IndexTeam/IndexTTS-2.5" in s7 and "use_emo_text" in s7 and "DIALOGUE_OVER_BUDGET" in s7 and "CosyVoice2(" not in s7, rc7, f"engine={j7.get('tts_engine')}, gpu={j7.get('gpu')}")

    rc8, j8, d8, s8, c8 = dlg_nb_of(["--tts-engine", "fish_s2_pro", "--gpu", "H200"])
    record("V47-8", "stage=dialogue --tts-engine fish_s2_pro --gpu H200 独立 uv + Secrets HF_TOKEN + gpuType=H200", rc8 == 0 and c8 and j8.get("tts_engine") == "fish_s2_pro" and j8.get("gpu") == "H200" and d8["metadata"]["colab"]["gpuType"] == "H200" and "fishaudio/s2-pro" in s8 and "userdata.get('HF_TOKEN')" in s8 and "CosyVoice2(" not in s8, rc8, f"engine={j8.get('tts_engine')}, gpu={j8.get('gpu')}")

    rc9, j9, d9, s9, c9 = dlg_nb_of(["--tts-engine", "voxcpm2"])
    record("V47-9", "stage=dialogue --tts-engine voxcpm2 保持原生 48kHz + 无 CosyVoice", rc9 == 0 and c9 and j9.get("tts_engine") == "voxcpm2" and "openbmb/VoxCPM2" in s9 and "VOXCPM2_NOT_NATIVE_48K" in s9 and "CosyVoice2(" not in s9, rc9, f"engine={j9.get('tts_engine')}")

    rc10, _, _, _ = nb_of(["--tts-engine", "indextts25"])
    record("V47-10", "stage=voice-ref 误传 indextts25 时快速阻断（仅限 dialogue 阶段）", rc10 != 0, rc10, f"rc={rc10}")


def test_v47_portrait_white_bg_cases(tmp_base: Path) -> None:
    import batch_ops as bo_mod

    wb_dir = tmp_base / "v47_portrait_whitebg"
    wb_dir.mkdir(parents=True, exist_ok=True)

    # 1) 9:16 纯白底 + 底部 35% 宽肩深色服饰遮挡左右底边 -> 必须 PASS
    p_pass = wb_dir / "portrait_916_shoulders_pass.png"
    im1 = Image.new("RGB", (720, 1280), (255, 255, 255))
    d1 = ImageDraw.Draw(im1)
    d1.ellipse((240, 180, 480, 460), fill=(210, 170, 140), outline=(25, 25, 25), width=4)
    # 底部 35% (y >= 832) 宽肩深色服饰完全延展至左右边缘
    d1.rectangle((0, int(1280 * 0.65), 719, 1279), fill=(35, 45, 65))
    im1.save(p_pass, format="PNG")
    r1 = bo_mod.check_white_background(p_pass)
    ok1 = (
        r1.get("compliant") is True
        and r1.get("status") == "PASS_PURE_WHITE_BG"
        and r1.get("mode") == "portrait_top65"
        and (r1.get("portrait_top65_white_ratio") or 0.0) >= 0.90
        and r1.get("corners_pure_white") is True
    )
    record("V47-WB-1", "9:16 纯白底 + 底部 35% 宽肩深色服饰遮挡左右底边 -> PASS", ok1, 0 if ok1 else 1, f"mode={r1.get('mode')}, ratio={r1.get('portrait_top65_white_ratio')}")

    # 2) 9:16 棚拍浅灰/灰白背景 (225,225,225) -> 必须 FAIL
    p_gray = wb_dir / "portrait_916_studio_gray_fail.png"
    im2 = Image.new("RGB", (720, 1280), (225, 225, 225))
    d2 = ImageDraw.Draw(im2)
    d2.ellipse((240, 180, 480, 460), fill=(210, 170, 140))
    d2.rectangle((140, 500, 580, 1279), fill=(40, 50, 70))
    im2.save(p_gray, format="PNG")
    r2 = bo_mod.check_white_background(p_gray)
    ok2 = r2.get("compliant") is False and r2.get("status") == "FAIL_NON_WHITE_BG" and r2.get("mode") == "portrait_top65"
    record("V47-WB-2", "9:16 棚拍浅灰背景 (225,225,225) -> FAIL 拦截", ok2, 0 if ok2 else 1, f"status={r2.get('status')}, ratio={r2.get('portrait_top65_white_ratio')}")

    # 3) 9:16 顶部/上半部含实景或灰暗背景杂物 -> 必须 FAIL
    p_clutter = wb_dir / "portrait_916_top_clutter_fail.png"
    im3 = Image.new("RGB", (720, 1280), (255, 255, 255))
    d3 = ImageDraw.Draw(im3)
    # 上半部左右外框与顶角含深色实景建筑/杂物
    d3.rectangle((0, 0, 220, 420), fill=(70, 75, 80))
    d3.rectangle((520, 0, 719, 360), fill=(85, 80, 75))
    im3.save(p_clutter, format="PNG")
    r3 = bo_mod.check_white_background(p_clutter)
    ok3 = r3.get("compliant") is False and r3.get("status") == "FAIL_NON_WHITE_BG" and r3.get("corners_pure_white") is False
    record("V47-WB-3", "9:16 顶部/上半部含实景杂物 -> FAIL 拦截", ok3, 0 if ok3 else 1, f"status={r3.get('status')}, corners={r3.get('corners_pure_white')}, ratio={r3.get('portrait_top65_white_ratio')}")

    # 4) 16:9 横屏旧逻辑保持原有严格行为 -> 回归 PASS（白底通过，非白底拦截）
    p_land_ok = wb_dir / "landscape_169_pass.png"
    make_white_bg_char_png(p_land_ok, (35, 65, 120))
    r4_ok = bo_mod.check_white_background(p_land_ok)
    p_land_fail = wb_dir / "landscape_169_gray_fail.png"
    Image.new("RGB", (1280, 720), (220, 220, 220)).save(p_land_fail, format="PNG")
    r4_fail = bo_mod.check_white_background(p_land_fail)
    ok4 = (
        r4_ok.get("compliant") is True
        and r4_ok.get("mode") == "landscape_or_square"
        and r4_ok.get("portrait_top65_white_ratio") is None
        and r4_fail.get("compliant") is False
        and r4_fail.get("status") == "FAIL_NON_WHITE_BG"
    )
    record("V47-WB-4", "16:9 横屏旧逻辑保持原有严格行为 -> 回归 PASS", ok4, 0 if ok4 else 1, f"ok_ratio={r4_ok.get('upper_and_side_white_ratio')}, fail_status={r4_fail.get('status')}")


def test_v47_contact_sheet_cases(tmp_base: Path) -> None:
    ct_root = tmp_base / "v47_contact_suite"
    proj = ct_root / "proj_ct"
    clean_dir = proj / "中间文件" / "原片分析" / "第01集" / "ref_clean_frames"
    batches_root = proj / "中间文件" / "在制工作区" / "第01集" / "生成批次"
    (proj / "分集" / "第01集").mkdir(parents=True, exist_ok=True)
    clean_dir.mkdir(parents=True, exist_ok=True)
    batches_root.mkdir(parents=True, exist_ok=True)

    def _make_textured_img(
        dst: Path,
        size: tuple[int, int],
        c1: tuple[int, int, int],
        c2: tuple[int, int, int],
    ) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        w, h = size
        im = Image.new("RGB", (w, h), c1)
        d = ImageDraw.Draw(im)
        d.rectangle((0, h // 2, w - 1, h - 1), fill=c2)
        d.rectangle((w // 6, h // 6, w * 5 // 6, h * 5 // 6), outline=(245, 240, 210), width=max(4, min(w, h) // 40))
        d.line((0, 0, w - 1, h - 1), fill=(250, 250, 250), width=max(4, min(w, h) // 45))
        fmt = "JPEG" if dst.suffix.lower() in (".jpg", ".jpeg") else "PNG"
        im.save(dst, format=fmt)

    def _is_canvas_bg(px: tuple[int, int, int]) -> bool:
        return all(abs(int(ch) - 238) <= 8 for ch in px)

    # 1920x930 横屏原片、768x1376 9:16 竖屏图、绿/蓝区分图及纯色异常图
    ref_wide = clean_dir / "seg_02_shot_01_first_净参考.jpg"
    ref_tall = clean_dir / "seg_02_shot_02_tall_净参考.jpg"
    ref_green = clean_dir / "src_green_priority.jpg"
    ref_blue = clean_dir / "ref_blue_fallback.jpg"
    ref_white = clean_dir / "blank_white.png"
    ref_solid = clean_dir / "blank_solid_gray.jpg"
    gen_tall = ct_root / "gen_tall_916.png"

    _make_textured_img(ref_wide, (1920, 930), (20, 60, 155), (185, 75, 25))
    _make_textured_img(ref_tall, (768, 1376), (30, 130, 80), (165, 45, 95))
    _make_textured_img(ref_green, (1280, 720), (15, 175, 40), (20, 130, 35))
    _make_textured_img(ref_blue, (1280, 720), (20, 45, 195), (15, 35, 150))
    _make_textured_img(gen_tall, (768, 1376), (45, 55, 95), (140, 95, 50))
    Image.new("RGB", (768, 1376), (255, 255, 255)).save(ref_white, format="PNG")
    Image.new("RGB", (1920, 930), (128, 128, 128)).save(ref_solid, format="JPEG")

    def _setup_batch(name: str, jobs: list[dict[str, Any]], with_init: bool = True) -> Path:
        bdir = batches_root / name
        bdir.mkdir(parents=True, exist_ok=True)
        for j in jobs:
            u = j["unit_id"]
            udir = bdir / u
            udir.mkdir(parents=True, exist_ok=True)
            if with_init:
                shutil.copyfile(gen_tall, udir / "初始.png")
        (bdir / "jobs.json").write_text(
            json.dumps({"batch_id": name, "wave": 3, "jobs": jobs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return bdir

    # V47-CT-1: Wave 3 缺 source_frame，仅 image_refs[0]（相对路径 + 1920x930 横屏）-> 左列正常渲染 + 等比居中 + std>=5.0
    b1 = _setup_batch("批次CT01", [{
        "unit_id": "seg_02_shot_01_首帧",
        "wave": 3,
        "asset_type": "frame",
        "image_refs": [
            "中间文件/原片分析/第01集/ref_clean_frames/seg_02_shot_01_first_净参考.jpg",
            "场景参考/不存在的占位场景.png",
        ],
    }])
    r1 = run_cli(BATCH_OPS, ["contact", "--batch-dir", str(b1)])
    sheet1 = b1 / "源与初图接触表.jpg"
    ok1 = False
    det1 = f"rc={r1.returncode} err={r1.stderr.strip()}"
    if r1.returncode == 0 and sheet1.is_file():
        with Image.open(sheet1) as im1:
            rgb1 = im1.convert("RGB")
            left_std1 = float(np.asarray(rgb1.crop((0, 30, 640, 390)), dtype=np.float32).std())
            # 1920x930 在 630x350 框内缩至 630x305，居中贴入 py=52..356，故 y=36 与 y=372 应为背景 #eeeeee，y=205 为图像内容
            top_pad_ok = _is_canvas_bg(rgb1.getpixel((320, 36)))
            bot_pad_ok = _is_canvas_bg(rgb1.getpixel((320, 372)))
            mid_img_ok = not _is_canvas_bg(rgb1.getpixel((320, 205)))
            # 右列 768x1376 (9:16) 缩至 195x350，居中贴入 px=640+222..640+416，左右 x=690 与 x=1230 应为背景，中心 x=960 为图像
            gen_l_pad = _is_canvas_bg(rgb1.getpixel((690, 205)))
            gen_r_pad = _is_canvas_bg(rgb1.getpixel((1230, 205)))
            gen_mid = not _is_canvas_bg(rgb1.getpixel((960, 205)))
            ok1 = (
                "CONTACT_OK" in r1.stdout
                and left_std1 >= 5.0
                and top_pad_ok and bot_pad_ok and mid_img_ok
                and gen_l_pad and gen_r_pad and gen_mid
            )
            det1 = f"left_std={left_std1:.2f}, wide_center=({top_pad_ok},{bot_pad_ok},{mid_img_ok}), tall_center=({gen_l_pad},{gen_r_pad},{gen_mid})"
    record("V47-CT-1", "缺 source_frame 时回退 image_refs[0] 渲染左列 + 横/竖图等比居中 + std>=5.0", ok1, r1.returncode, det1)

    # V47-CT-2: 缺 source_frame 与 image_refs，仅 refs[0]（含字符串路径与 dict{'path': ...} + 9:16 竖屏居中）-> PASS
    b2 = _setup_batch("批次CT02", [
        {
            "unit_id": "seg_02_shot_02_首帧",
            "wave": 3,
            "asset_type": "frame",
            "refs": ["中间文件/原片分析/第01集/ref_clean_frames/seg_02_shot_02_tall_净参考.jpg"],
        },
        {
            "unit_id": "seg_02_shot_03_首帧",
            "wave": 3,
            "asset_type": "frame",
            "refs": [{"path": str(ref_tall), "role": "clean_source"}],
        },
    ])
    r2 = run_cli(BATCH_OPS, ["contact", "--batch-dir", str(b2)])
    sheet2 = b2 / "源与初图接触表.jpg"
    ok2 = False
    det2 = f"rc={r2.returncode} err={r2.stderr.strip()}"
    if r2.returncode == 0 and sheet2.is_file():
        with Image.open(sheet2) as im2:
            rgb2 = im2.convert("RGB")
            std_r0 = float(np.asarray(rgb2.crop((0, 30, 640, 390)), dtype=np.float32).std())
            std_r1 = float(np.asarray(rgb2.crop((0, 420, 640, 780)), dtype=np.float32).std())
            # 9:16 左列水平居中：x=60/x=580 为背景 #eeeeee，x=320 为图像内容
            tall_c0 = _is_canvas_bg(rgb2.getpixel((60, 205))) and _is_canvas_bg(rgb2.getpixel((580, 205))) and (not _is_canvas_bg(rgb2.getpixel((320, 205))))
            tall_c1 = _is_canvas_bg(rgb2.getpixel((60, 595))) and _is_canvas_bg(rgb2.getpixel((580, 595))) and (not _is_canvas_bg(rgb2.getpixel((320, 595))))
            ok2 = "CONTACT_OK" in r2.stdout and std_r0 >= 5.0 and std_r1 >= 5.0 and tall_c0 and tall_c1
            det2 = f"std_str={std_r0:.2f}, std_dict={std_r1:.2f}, tall_centered=({tall_c0},{tall_c1})"
    record("V47-CT-2", "缺 source_frame 时回退 refs[0]（字符串 + dict path 键）正常渲染 9:16 左列居中并通过", ok2, r2.returncode, det2)

    # V47-CT-3: 解析优先级 source_frame(存在优先) -> source_frame(缺失回退) -> image_refs[0] -> refs[0]
    b3 = _setup_batch("批次CT03", [
        {
            "unit_id": "seg_03_shot_01_首帧",
            "wave": 3,
            "asset_type": "frame",
            "source_frame": str(ref_green),
            "image_refs": [str(ref_blue)],
        },
        {
            "unit_id": "seg_03_shot_02_首帧",
            "wave": 3,
            "asset_type": "frame",
            "source_frame": "中间文件/原片分析/第01集/ref_clean_frames/not_exist_source.jpg",
            "image_refs": [str(ref_blue)],
        },
    ])
    r3 = run_cli(BATCH_OPS, ["contact", "--batch-dir", str(b3)])
    sheet3 = b3 / "源与初图接触表.jpg"
    ok3 = False
    det3 = f"rc={r3.returncode} err={r3.stderr.strip()}"
    if r3.returncode == 0 and sheet3.is_file():
        with Image.open(sheet3) as im3:
            rgb3 = im3.convert("RGB")
            px0 = rgb3.getpixel((200, 120))  # row 0 upper half -> green
            px1 = rgb3.getpixel((200, 510))  # row 1 upper half -> blue
            row0_is_green = px0[1] > px0[2] + 40
            row1_is_blue = px1[2] > px1[1] + 40
            ok3 = row0_is_green and row1_is_blue
            det3 = f"row0_px={px0} (expect green), row1_px={px1} (expect blue)"
    record("V47-CT-3", "源帧优先级：source_frame 存在优先、失效回退 image_refs[0]", ok3, r3.returncode, det3)

    # V47-CT-4: image_refs[0] 指向全白/纯灰异常图 (std < 5.0) -> 必须非零拦截，绝不静默跳过
    b4w = _setup_batch("批次CT04W", [{
        "unit_id": "seg_04_shot_01_首帧",
        "wave": 3,
        "asset_type": "frame",
        "image_refs": [str(ref_white)],
    }])
    r4w = run_cli(BATCH_OPS, ["contact", "--batch-dir", str(b4w)])
    b4g = _setup_batch("批次CT04G", [{
        "unit_id": "seg_04_shot_02_首帧",
        "wave": 3,
        "asset_type": "frame",
        "image_refs": [str(ref_solid)],
    }])
    r4g = run_cli(BATCH_OPS, ["contact", "--batch-dir", str(b4g)])
    ok4 = (
        r4w.returncode != 0 and "CONTACT_ERR_BLANK_CELL" in r4w.stderr
        and r4g.returncode != 0 and "CONTACT_ERR_BLANK_CELL" in r4g.stderr
    )
    record("V47-CT-4", "image_refs[0] 指向全白/纯灰图时 std<5.0 门禁准确拦截（非零退出）", ok4, r4w.returncode, f"white_rc={r4w.returncode}, gray_rc={r4g.returncode}")

    # V47-CT-5: image_refs[0] 缺失文件 与 无任何源图/参考图（左列空白）-> 均必须非零拦截
    b5m = _setup_batch("批次CT05M", [{
        "unit_id": "seg_05_shot_01_首帧",
        "wave": 3,
        "asset_type": "frame",
        "image_refs": ["中间文件/原片分析/第01集/ref_clean_frames/missing_ref.jpg", str(ref_wide)],
    }])
    r5m = run_cli(BATCH_OPS, ["contact", "--batch-dir", str(b5m)])
    b5e = _setup_batch("批次CT05E", [{
        "unit_id": "seg_05_shot_02_首帧",
        "wave": 3,
        "asset_type": "frame",
    }])
    r5e = run_cli(BATCH_OPS, ["contact", "--batch-dir", str(b5e)])
    ok5 = (
        r5m.returncode != 0 and "CONTACT_ERR" in r5m.stderr
        and r5e.returncode != 0 and "CONTACT_ERR" in r5e.stderr
    )
    record("V47-CT-5", "image_refs[0] 缺失文件或分镜单元完全缺源图导致左列空白时准确拦截（非零退出）", ok5, r5m.returncode, f"missing_rc={r5m.returncode}, empty_rc={r5e.returncode}")


def test_v47_finalize_output_path_cases(tmp_base: Path) -> None:
    fin_root = tmp_base / "v47_fin_output_path"
    proj = fin_root / "proj_fin"
    ep_dir = proj / "分集" / "第01集"
    work_dir = proj / "中间文件" / "在制工作区" / "第01集"
    clean_dir = proj / "中间文件" / "原片分析" / "第01集" / "ref_clean_frames"
    batches_root = work_dir / "生成批次"
    idx_dir = proj / "中间文件" / "索引与制作记录"
    for d in (ep_dir, work_dir, clean_dir, batches_root, idx_dir):
        d.mkdir(parents=True, exist_ok=True)
    (idx_dir / "文件归位清单.json").write_text(json.dumps({"records": []}, ensure_ascii=False), encoding="utf-8")

    def _make_img(dst: Path, size: tuple[int, int], c1: tuple[int, int, int], c2: tuple[int, int, int]) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        w, h = size
        im = Image.new("RGB", (w, h), c1)
        d = ImageDraw.Draw(im)
        d.rectangle((0, h // 2, w - 1, h - 1), fill=c2)
        d.rectangle((w // 5, h // 5, w * 4 // 5, h * 4 // 5), outline=(245, 240, 210), width=6)
        im.save(dst, format="JPEG" if dst.suffix.lower() in (".jpg", ".jpeg") else "PNG")

    src_clean = clean_dir / "seg_01_shot_01_first_净参考.jpg"
    tool_png1 = fin_root / "tool_gen_01.png"
    tool_png2 = fin_root / "tool_gen_02.png"
    tool_png3 = fin_root / "tool_gen_03.png"
    _make_img(src_clean, (1920, 930), (30, 70, 140), (160, 80, 40))
    _make_img(tool_png1, (768, 1376), (40, 90, 120), (150, 100, 60))
    _make_img(tool_png2, (768, 1376), (70, 110, 60), (130, 60, 90))
    _make_img(tool_png3, (768, 1376), (95, 55, 130), (50, 120, 110))

    en_prompt = (
        "[Subject] Pre-war 1937 Shanghai shikumen tiled rooftops stretching into cool overcast mist toward the river bridge, "
        "strictly 0 persons visible in frame. [Camera] 9:16 vertical aspect ratio, elevated wide shot looking slightly downward, "
        "horizon on upper third, 35mm prime lens, deep focus. [Environment] Foreground weathered grey clay tiles, midground dense "
        "brick chimneys and gables, background misty skyline. [Lighting] Diffuse cold overcast top light with crisp iron-black "
        "shadow pockets. [Style] 35mm Kodak bleach bypass film grain, gritty historical realism, no text, no watermark."
    )

    # V47-FIN-1: jobs.json 仅含 output_path（无 target_path/formal_path），经 save-unit -> contact -> finalize 准确归位且 promoted=1
    b1 = batches_root / "批次FIN01"
    u1 = "seg_01_shot_01_首帧"
    (b1 / u1).mkdir(parents=True, exist_ok=True)
    (b1 / u1 / "完整提示词.txt").write_text(en_prompt, encoding="utf-8")
    rel_out1 = "分集/第01集/首尾及关键帧/segment_01_镜头01_首帧.png"
    (b1 / "jobs.json").write_text(
        json.dumps({
            "batch_id": "批次FIN01",
            "wave": 3,
            "jobs": [{
                "unit_id": u1,
                "wave": 3,
                "asset_type": "frame",
                "frame_type": "首帧",
                "segment": "segment_01",
                "shot": "镜头01",
                "output_path": rel_out1,
                "image_refs": ["中间文件/原片分析/第01集/ref_clean_frames/seg_01_shot_01_first_净参考.jpg"],
                "refs": ["中间文件/原片分析/第01集/ref_clean_frames/seg_01_shot_01_first_净参考.jpg"],
                "exact_person_count": 0,
            }],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    r_su1 = run_cli(BATCH_OPS, ["save-unit", "--jobs-file", str(b1 / "jobs.json"), "--unit", u1, "--tool-png", str(tool_png1)])
    r_ct1 = run_cli(BATCH_OPS, ["contact", "--batch-dir", str(b1)])
    (b1 / "批次审核结论.json").write_text(
        json.dumps({
            "units": {
                u1: {
                    "status": "通过",
                    "chosen": "初始",
                    "reason": "实看1K首帧构图层次清晰，灰瓦屋顶与晨雾天际线匹配源帧机位，全画零人无水印乱码。",
                }
            }
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    r_fin1 = run_cli(BATCH_OPS, ["finalize", "--batch-dir", str(b1)])
    dst1 = proj / rel_out1
    receipt1 = json.loads((b1 / "finalize_receipt.json").read_text(encoding="utf-8")) if (b1 / "finalize_receipt.json").is_file() else {}
    res1_json = json.loads((b1 / u1 / "result.json").read_text(encoding="utf-8")) if (b1 / u1 / "result.json").is_file() else {}
    ok_fin1 = (
        r_su1.returncode == 0
        and r_ct1.returncode == 0
        and r_fin1.returncode == 0
        and receipt1.get("promoted") == 1
        and dst1.is_file()
        and sha256_f(dst1) == sha256_f(tool_png1)
        and res1_json.get("target_path") == rel_out1
    )
    record(
        "V47-FIN-1",
        "jobs.json 仅含 output_path 时 save-unit/finalize 回退归位且 promoted=1",
        ok_fin1,
        r_fin1.returncode,
        f"promoted={receipt1.get('promoted')}, dst_exists={dst1.is_file()}, res_tp={res1_json.get('target_path')}",
    )

    # V47-FIN-2: 清单仅含 output_path 回退 + target_path>formal_path>output_path 优先级 + plan-batches 自动物化 target_path
    u2 = "seg_02_shot_01_首帧"
    u3 = "seg_03_shot_01_首帧"
    rel_out2 = "分集/第01集/首尾及关键帧/segment_02_镜头01_首帧.png"
    rel_tgt3 = "分集/第01集/首尾及关键帧/segment_03_镜头01_首帧.png"
    rel_out3_shadow = "分集/第01集/首尾及关键帧/segment_99_镜头99_首帧.png"
    manifest_doc = {
        "schema_version": "2.0",
        "units": [
            {
                "unit_id": u2,
                "wave": 3,
                "asset_type": "frame",
                "frame_type": "首帧",
                "segment": "segment_02",
                "shot": "镜头01",
                "output_path": rel_out2,
                "image_refs": ["中间文件/原片分析/第01集/ref_clean_frames/seg_01_shot_01_first_净参考.jpg"],
                "prompt_en": en_prompt,
                "exact_person_count": 0,
            },
            {
                "unit_id": u3,
                "wave": 3,
                "asset_type": "frame",
                "frame_type": "首帧",
                "segment": "segment_03",
                "shot": "镜头01",
                "target_path": rel_tgt3,
                "formal_path": rel_out3_shadow,
                "output_path": rel_out3_shadow,
                "image_refs": ["中间文件/原片分析/第01集/ref_clean_frames/seg_01_shot_01_first_净参考.jpg"],
                "prompt_en": en_prompt,
                "exact_person_count": 0,
            },
        ],
    }
    (work_dir / "待生图帧清单.json").write_text(json.dumps(manifest_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    # 1) 验证 plan-batches 自动将仅含 output_path 的清单单元物化写入 jobs.json["target_path"]
    r_plan = run_cli(BATCH_OPS, ["plan-batches", "--work-dir", str(work_dir), "--skip-cuts-check", "--skip-alignment-check", "--replan-all"])
    plan_jobs_p = batches_root / "批次01" / "jobs.json"
    plan_tp_u2 = ""
    if r_plan.returncode == 0 and plan_jobs_p.is_file():
        pj_list = json.loads(plan_jobs_p.read_text(encoding="utf-8")).get("jobs", [])
        u2_plan_j = next((x for x in pj_list if x.get("unit_id") == u2), {})
        plan_tp_u2 = str(u2_plan_j.get("target_path") or "")

    # 2) 验证 cmd_finalize 在 jobs.json 缺路径时回退清单 output_path，且三键共存时 target_path 优先于 formal_path/output_path
    b2 = batches_root / "批次FIN02"
    for uid_x in (u2, u3):
        (b2 / uid_x).mkdir(parents=True, exist_ok=True)
        (b2 / uid_x / "完整提示词.txt").write_text(en_prompt, encoding="utf-8")
    (b2 / "jobs.json").write_text(
        json.dumps({
            "batch_id": "批次FIN02",
            "wave": 3,
            "jobs": [
                {
                    "unit_id": u2,
                    "wave": 3,
                    "asset_type": "frame",
                    "frame_type": "首帧",
                    "segment": "segment_02",
                    "shot": "镜头01",
                    "image_refs": ["中间文件/原片分析/第01集/ref_clean_frames/seg_01_shot_01_first_净参考.jpg"],
                    "refs": ["中间文件/原片分析/第01集/ref_clean_frames/seg_01_shot_01_first_净参考.jpg"],
                    "exact_person_count": 0,
                },
                {
                    "unit_id": u3,
                    "wave": 3,
                    "asset_type": "frame",
                    "frame_type": "首帧",
                    "segment": "segment_03",
                    "shot": "镜头01",
                    "target_path": rel_tgt3,
                    "formal_path": rel_out3_shadow,
                    "output_path": rel_out3_shadow,
                    "image_refs": ["中间文件/原片分析/第01集/ref_clean_frames/seg_01_shot_01_first_净参考.jpg"],
                    "refs": ["中间文件/原片分析/第01集/ref_clean_frames/seg_01_shot_01_first_净参考.jpg"],
                    "exact_person_count": 0,
                },
            ],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    r_su2 = run_cli(BATCH_OPS, ["save-unit", "--jobs-file", str(b2 / "jobs.json"), "--unit", u2, "--tool-png", str(tool_png2)])
    r_su3 = run_cli(BATCH_OPS, ["save-unit", "--jobs-file", str(b2 / "jobs.json"), "--unit", u3, "--tool-png", str(tool_png3)])
    r_ct2 = run_cli(BATCH_OPS, ["contact", "--batch-dir", str(b2)])
    (b2 / "批次审核结论.json").write_text(
        json.dumps({
            "units": {
                u2: {
                    "status": "通过",
                    "chosen": "初始",
                    "reason": "第二镜屋檐微俯透视与左下枝叶前景贴合净源帧，青灰冷调质感自然无杂物。",
                },
                u3: {
                    "status": "通过",
                    "chosen": "初始",
                    "reason": "第三镜高位俯瞰石库门瓦顶与远景天际线空间纵深准确，严格零人无现代建筑。",
                },
            }
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    r_fin2 = run_cli(BATCH_OPS, ["finalize", "--batch-dir", str(b2)])
    dst2 = proj / rel_out2
    dst3 = proj / rel_tgt3
    dst3_shadow = proj / rel_out3_shadow
    receipt2 = json.loads((b2 / "finalize_receipt.json").read_text(encoding="utf-8")) if (b2 / "finalize_receipt.json").is_file() else {}
    ok_fin2 = (
        r_plan.returncode == 0
        and plan_tp_u2 == rel_out2
        and r_su2.returncode == 0
        and r_su3.returncode == 0
        and r_ct2.returncode == 0
        and r_fin2.returncode == 0
        and receipt2.get("promoted") == 2
        and dst2.is_file() and sha256_f(dst2) == sha256_f(tool_png2)
        and dst3.is_file() and sha256_f(dst3) == sha256_f(tool_png3)
        and (not dst3_shadow.exists())
    )
    record(
        "V47-FIN-2",
        "plan-batches 物化 output_path->target_path + finalize 清单回退与 target_path 优先归位",
        ok_fin2,
        r_fin2.returncode,
        f"plan_tp={plan_tp_u2}, promoted={receipt2.get('promoted')}, dst2={dst2.is_file()}, dst3={dst3.is_file()}, shadow={dst3_shadow.exists()}",
    )


def test_v47_multi_char_composite_and_slot2_cases(tmp_base: Path) -> None:
    """V47-MC-1..4: 多人去白边无底横向合集图 (Image 3) 自动拼图/缓存/透明抠底 + Image 2 场景母图防挤占门禁 + Stage A 级联阻断"""
    from PIL import Image, ImageDraw

    proj = tmp_base / "v47_multi_char_proj"
    ep = "第01集"
    work_dir = proj / "中间文件" / "在制工作区" / ep
    ref_dir = proj / "中间文件" / "原片分析" / ep / "ref_clean_frames"
    char_dir = proj / "分集" / ep / "人物"
    scene_dir = proj / "场景参考"
    for d in (work_dir, ref_dir, char_dir, scene_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 造 2 张带大量四周白边 + 内部含白色衣领的 16:9 纯白无底单人母图（检验 tight crop 与四边 BFS 绝不误抠白衣）
    char1_p = char_dir / "角色01_绯琳_肖像.png"
    char2_p = char_dir / "角色02_兰薇_肖像.png"
    im1 = Image.new("RGB", (800, 450), (255, 255, 255))
    d1 = ImageDraw.Draw(im1)
    d1.rectangle([300, 80, 500, 380], fill=(185, 50, 60))
    d1.rectangle([360, 220, 440, 310], fill=(255, 255, 255))  # 内部白色蕾丝衣领
    im1.save(char1_p)

    im2 = Image.new("RGB", (800, 450), (255, 255, 255))
    d2 = ImageDraw.Draw(im2)
    d2.rectangle([310, 90, 490, 390], fill=(35, 70, 165))
    d2.rectangle([370, 230, 430, 300], fill=(252, 252, 252))  # 内部白衬衫
    im2.save(char2_p)

    scene_p = scene_dir / "场景01_卧房_母图.png"
    sc_im = Image.new("RGB", (640, 360), (95, 72, 54))
    ImageDraw.Draw(sc_im).rectangle([120, 60, 520, 300], fill=(150, 120, 85))
    sc_im.save(scene_p)

    src_clean = ref_dir / "seg_01_shot_01_first_净参考.jpg"
    sf_im = Image.new("RGB", (640, 360), (70, 90, 110))
    ImageDraw.Draw(sf_im).ellipse([160, 80, 480, 280], fill=(170, 130, 90))
    sf_im.save(src_clean, format="JPEG", quality=92)

    rel_src = "中间文件/原片分析/第01集/ref_clean_frames/seg_01_shot_01_first_净参考.jpg"
    rel_scene = "场景参考/场景01_卧房_母图.png"
    rel_c1 = "分集/第01集/人物/角色01_绯琳_肖像.png"
    rel_c2 = "分集/第01集/人物/角色02_兰薇_肖像.png"
    rel_comp = "中间文件/在制工作区/第01集/角色合集缓存/合集_角色01_绯琳_角色02_兰薇_肖像.png"

    base_vspec = {
        "camera_and_horizon": "eye-level medium close-up two-shot",
        "spatial_layers": {"foreground": "wooden bedstead", "midground": "two sisters seated", "background": "warm noble bedroom"},
        "lighting_vector": "warm 3400K upper-left sunlight",
        "subjects_inventory": [
            {"identity_or_garment": "Feilin in ivory lace blouse", "grid_pos": "left-center", "torso_angle": "30deg right", "left_hand": "relaxed", "right_hand": "relaxed", "gaze_target": "Lanwei"},
            {"identity_or_garment": "Lanwei in royal-blue uniform", "grid_pos": "right-center", "torso_angle": "25deg left", "left_hand": "relaxed", "right_hand": "relaxed", "gaze_target": "Feilin"},
        ],
    }

    # V47-MC-2: 复现 pilot_v46_p1p2 事故 —— 把人物塞进 Image 2 挤掉场景母图 / 塞入 4 张图 -> 必须硬阻断
    bad_slot2_manifest = {
        "schema_version": "v3",
        "units": [
            {
                "unit_id": "segment_01_镜头01_首帧",
                "type": "frame",
                "frame_type": "首帧",
                "wave": 3,
                "shot_scale": "medium close-up",
                "exact_person_count": 2,
                "visible_characters": ["角色01_绯琳", "角色02_兰薇"],
                "scene_ref": rel_scene,
                "source_frame": rel_src,
                "target_path": "分集/第01集/首尾及关键帧/segment_01_镜头01_首帧.png",
                "image_refs": [rel_src, rel_c1, rel_c2],
                "visual_spec": {**base_vspec, "ref_roles": ["cleaned source frame", "Feilin portrait", "Lanwei portrait"]},
                "image_prompt": "Use Image 1 for camera composition, Image 2 for Feilin, and Image 3 for Lanwei in a warm bedroom.",
            }
        ],
    }
    (work_dir / "待生图帧清单.json").write_text(json.dumps(bad_slot2_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    r_bad_slot2 = run_cli(BATCH_OPS, ["plan-batches", "--work-dir", str(work_dir), "--v3", "--skip-cuts-check", "--skip-alignment-check", "--replan-all"])

    bad_over3_manifest = {
        "schema_version": "v3",
        "units": [
            {
                "unit_id": "segment_01_镜头01_首帧",
                "type": "frame",
                "frame_type": "首帧",
                "wave": 3,
                "shot_scale": "medium close-up",
                "exact_person_count": 2,
                "visible_characters": ["角色01_绯琳", "角色02_兰薇"],
                "scene_ref": rel_scene,
                "source_frame": rel_src,
                "target_path": "分集/第01集/首尾及关键帧/segment_01_镜头01_首帧.png",
                "image_refs": [rel_src, rel_scene, rel_c1, rel_c2],
                "visual_spec": {**base_vspec, "ref_roles": ["cleaned source frame", "bedroom scene master", "Feilin portrait", "Lanwei portrait"]},
                "image_prompt": "Use Image 1 for camera composition, Image 2 for bedroom scene, Image 3 for Feilin, and Image 4 for Lanwei.",
            }
        ],
    }
    (work_dir / "待生图帧清单.json").write_text(json.dumps(bad_over3_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    r_bad_over3 = run_cli(BATCH_OPS, ["plan-batches", "--work-dir", str(work_dir), "--v3", "--skip-cuts-check", "--skip-alignment-check", "--replan-all"])

    ok_mc2 = (
        r_bad_slot2.returncode == 2 and "IMAGE_REF_SLOT2_MISSING_SCENE" in (r_bad_slot2.stderr + r_bad_slot2.stdout)
        and r_bad_over3.returncode == 2 and "IMAGE_REF_MULTI_CHAR_NEEDS_COMPOSITE" in (r_bad_over3.stderr + r_bad_over3.stdout)
    )
    record(
        "V47-MC-2",
        "双人镜把人物塞进 Image 2 挤掉场景母图或硬塞4图时 plan-batches/V3 硬拦截",
        ok_mc2,
        r_bad_slot2.returncode,
        f"slot2_rc={r_bad_slot2.returncode}, over3_rc={r_bad_over3.returncode}",
    )

    # 切回规范三槽位多人清单：[Image 1 净源帧, Image 2 场景母图, Image 3 角色合集缓存/合集_...png]
    good_manifest = {
        "schema_version": "v3",
        "units": [
            {
                "unit_id": "segment_01_镜头01_首帧",
                "type": "frame",
                "frame_type": "首帧",
                "wave": 3,
                "shot_scale": "medium close-up",
                "exact_person_count": 2,
                "visible_characters": ["角色01_绯琳", "角色02_兰薇"],
                "scene_ref": rel_scene,
                "source_frame": rel_src,
                "target_path": "分集/第01集/首尾及关键帧/segment_01_镜头01_首帧.png",
                "image_refs": [rel_src, rel_scene, rel_comp],
                "visual_spec": {
                    **base_vspec,
                    "ref_roles": ["cleaned source frame", "noble bedroom scene master", "multi-character borderless composite sheet left-to-right Feilin and Lanwei"],
                },
                "image_prompt": "Use Image 1 for camera pitch and two-shot placement, Image 2 for warm European noble bedroom lighting and architecture, and Image 3 left-to-right for Feilin and Lanwei.",
            }
        ],
    }
    (work_dir / "待生图帧清单.json").write_text(json.dumps(good_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # V47-MC-1: 测试 build-char-sheets 去白边紧致横拼、缓存复用、源图变更自动重拼、以及 --bg transparent 真透明抠底保内部白衣
    r_b1 = run_cli(BATCH_OPS, ["build-char-sheets", "--work-dir", str(work_dir)])
    j_b1 = json.loads(r_b1.stdout) if r_b1.returncode == 0 else {}
    comp_abs = proj / rel_comp
    white_ok = False
    if comp_abs.is_file():
        with Image.open(comp_abs) as cim:
            # 确认已去白边紧致横拼（明显小于两张 800 宽原图之和 1600）且为 RGB 纯白底
            white_ok = (cim.mode == "RGB") and (cim.width < 1200) and (cim.getpixel((0, 0)) == (255, 255, 255))

    # 二次运行应命中缓存 REUSED_CACHE
    r_b2 = run_cli(BATCH_OPS, ["build-char-sheets", "--work-dir", str(work_dir)])
    j_b2 = json.loads(r_b2.stdout) if r_b2.returncode == 0 else {}

    # 修改源母图后应自动识别 SHA 变化重新构建 BUILT
    ImageDraw.Draw(im1).rectangle([320, 100, 350, 130], fill=(240, 200, 40))
    im1.save(char1_p)
    r_b3 = run_cli(BATCH_OPS, ["build-char-sheets", "--work-dir", str(work_dir)])
    j_b3 = json.loads(r_b3.stdout) if r_b3.returncode == 0 else {}

    # 透明无底模式 (--bg transparent): 外围四角 alpha==0 且主体内部白衣保持不透明 alpha==255
    r_bt = run_cli(BATCH_OPS, ["build-char-sheets", "--work-dir", str(work_dir), "--bg", "transparent"])
    trans_ok = False
    if r_bt.returncode == 0 and comp_abs.is_file():
        with Image.open(comp_abs) as tim:
            # 测四角全透明 + 左侧角色中心内部白衣处 alpha==255
            c_alpha = [tim.getpixel(pt)[3] for pt in ((0, 0), (tim.width - 1, 0), (0, tim.height - 1), (tim.width - 1, tim.height - 1))]
            inner_a = tim.getpixel((int(tim.width * 0.25), int(tim.height * 0.60)))[3]
            trans_ok = (tim.mode == "RGBA") and all(a == 0 for a in c_alpha) and (inner_a == 255)

    # 还原默认纯白无底供后续 MC-3/4 测试
    run_cli(BATCH_OPS, ["build-char-sheets", "--work-dir", str(work_dir), "--bg", "white"])
    ok_mc1 = (
        r_b1.returncode == 0 and j_b1.get("built_count") == 1 and white_ok
        and r_b2.returncode == 0 and j_b2.get("reused_count") == 1 and j_b2.get("built_count") == 0
        and r_b3.returncode == 0 and j_b3.get("built_count") == 1
        and trans_ok
    )
    record(
        "V47-MC-1",
        "build-char-sheets 去白边紧凑横拼 + SHA缓存复用 + 源图变更重拼 + --bg transparent 保内部白衣",
        ok_mc1,
        r_b1.returncode,
        f"white={white_ok}, reuse={j_b2.get('reused_count')}, rebuild={j_b3.get('built_count')}, trans={trans_ok}",
    )

    # V47-MC-3: 合规三槽位跑通 plan-batches -> save-unit，验证 Image 2=场景、Image 3=合集且提示词含左->右映射
    comp_abs.unlink(missing_ok=True)
    r_plan_ok = run_cli(BATCH_OPS, ["plan-batches", "--work-dir", str(work_dir), "--v3", "--skip-cuts-check", "--skip-alignment-check", "--replan-all"])
    b1_jobs_p = work_dir / "生成批次" / "批次01" / "jobs.json"
    tool_gen = tmp_base / "v47_mc_gen.png"
    Image.new("RGB", (640, 360), (140, 110, 90)).save(tool_gen)
    r_su_ok = run_cli(BATCH_OPS, ["save-unit", "--jobs-file", str(b1_jobs_p), "--unit", "segment_01_镜头01_首帧", "--tool-png", str(tool_gen)])
    pt_file = work_dir / "生成批次" / "批次01" / "segment_01_镜头01_首帧" / "完整提示词.txt"
    pt_txt = pt_file.read_text(encoding="utf-8") if pt_file.is_file() else ""
    req_doc = json.loads((work_dir / "生成批次" / "批次01" / "segment_01_镜头01_首帧" / "request.json").read_text(encoding="utf-8")) if (work_dir / "生成批次" / "批次01" / "segment_01_镜头01_首帧" / "request.json").is_file() else {}
    ok_mc3 = (
        r_plan_ok.returncode == 0
        and comp_abs.is_file()  # plan-batches 自动物化实体合集图
        and r_su_ok.returncode == 0
        and req_doc.get("referenced_image_paths") == [rel_src, rel_scene, rel_comp]
        and "ordered left-to-right: 角色01_绯琳, 角色02_兰薇" in pt_txt
    )
    record(
        "V47-MC-3",
        "多人同框三槽位 [净源帧, 场景母图, 角色合集图] 跑通 plan-batches 自动拼图 + save-unit 注入左->右映射",
        ok_mc3,
        r_su_ok.returncode,
        f"plan_rc={r_plan_ok.returncode}, comp_auto={comp_abs.is_file()}, refs={req_doc.get('referenced_image_paths')}",
    )

    # V47-MC-4: Stage A 级联阻断（删合集图报 MISSING_CHAR_COMPOSITE；源角色标未通过报 UNAPPROVED_STAGE_A）
    comp_abs.unlink(missing_ok=True)
    r_miss_c = run_cli(BATCH_OPS, ["save-unit", "--jobs-file", str(b1_jobs_p), "--unit", "segment_01_镜头01_首帧", "--tool-png", str(tool_gen)])
    run_cli(BATCH_OPS, ["build-char-sheets", "--work-dir", str(work_dir)])
    (work_dir / "当前制作审核分类.json").write_text(
        json.dumps({"角色01_绯琳_肖像": {"category": "需要用户审核", "formal_path": rel_c1}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    r_unapp_c = run_cli(BATCH_OPS, ["save-unit", "--jobs-file", str(b1_jobs_p), "--unit", "segment_01_镜头01_首帧", "--tool-png", str(tool_gen)])
    ok_mc4 = (
        r_miss_c.returncode == 2 and "STAGE_GATE_BLOCKED_MISSING_CHAR_COMPOSITE" in (r_miss_c.stderr + r_miss_c.stdout)
        and r_unapp_c.returncode == 2 and "STAGE_GATE_BLOCKED_UNAPPROVED_STAGE_A" in (r_unapp_c.stderr + r_unapp_c.stdout)
    )
    record(
        "V47-MC-4",
        "save-unit 对缺失多人合集图及未过审源单人母图均触发 Stage A 级联硬阻断",
        ok_mc4,
        r_unapp_c.returncode,
        f"miss_rc={r_miss_c.returncode}, unapp_rc={r_unapp_c.returncode}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="V4.6 随稿自动化检查与对抗回归套件 (默认 24fps + 备选 60fps + 一审至六审全量用例)")
    parser.add_argument("--out-json", default="", help="回归测试结果 JSON 输出路径")
    args = parser.parse_args()

    RESULTS.clear()
    tmp_path_holder: Path | None = None
    with tempfile.TemporaryDirectory(prefix="qa_v46_suite_") as tmp_dir_str:
        tmp_base = Path(tmp_dir_str).resolve()
        tmp_path_holder = tmp_base
        test_v41_baseline_part1(tmp_base)
        test_v41_baseline_part2(tmp_base)
        test_v42_deep_p0_cases(tmp_base)
        test_v42_deep_p1_part1(tmp_base)
        test_v42_deep_p1_part2(tmp_base)
        test_v44_p0_p1_cases(tmp_base)
        test_v45_p1_cases(tmp_base)
        test_v46_p0_cases(tmp_base)
        test_v46_four_profiles_cases(tmp_base)
        test_v46_p1_mixed_fps_cases(tmp_base)
        test_v46_aspect_ratio_916_case(tmp_base)
        test_v47_voxcpm2_cases(tmp_base)
        test_v47_portrait_white_bg_cases(tmp_base)
        test_v47_contact_sheet_cases(tmp_base)
        test_v47_finalize_output_path_cases(tmp_base)
        test_v47_multi_char_composite_and_slot2_cases(tmp_base)

    temp_cleaned = (tmp_path_holder is not None) and (not tmp_path_holder.exists())
    passed_cnt = sum(1 for r in RESULTS if r["passed"])
    total_cnt = len(RESULTS)
    all_ok = (passed_cnt == total_cnt) and (total_cnt >= 57) and temp_cleaned

    summary_doc = {
        "suite": "run_v42_regression_suite.py",
        "version": "V4.6",
        "executed_at_utc": datetime.now(timezone.utc).isoformat(),
        "default_target_fps": 24,
        "optional_hfr_target_fps": 60,
        "total_cases": total_cnt,
        "passed_cases": passed_cnt,
        "failed_cases": total_cnt - passed_cnt,
        "all_passed": all_ok,
        "temp_dir_auto_cleaned": temp_cleaned,
        "results": RESULTS,
    }
    payload_txt = json.dumps(summary_doc, ensure_ascii=False, indent=2)
    cand_outs = [Path(args.out_json).resolve()] if args.out_json else [
        DEFAULT_OUT_JSON,
        Path(tempfile.gettempdir()) / "qa_v42_results.json",
    ]
    out_p = cand_outs[-1]
    for cp in cand_outs:
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(payload_txt, encoding="utf-8")
            out_p = cp
            break
        except OSError:
            if args.out_json:
                raise
            continue
    print(f"\n=== V4.6 AUTOMATED CHECK SUMMARY: {passed_cnt}/{total_cnt} PASSED | temp_cleaned={temp_cleaned} | report={out_p} ===", flush=True)
    if not all_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
