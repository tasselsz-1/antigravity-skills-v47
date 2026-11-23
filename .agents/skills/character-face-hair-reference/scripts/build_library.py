# -*- coding: utf-8 -*-
"""Build script for character-face-hair-reference skill.
Generates SVG schematics, catalog.json, individual entry JSONs, index.md, and interactive HTML workbench.
"""

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from data_anatomy import (
    ANATOMY_PLATES,
    EXTERNAL_3D_2D_SOURCES,
    FORMULA_7DIM,
    USAGE_POLICY,
    USAGE_WARNING_EN,
    USAGE_WARNING_ZH,
)
from data_bones import BONES
from data_hairs import HAIR_COLOR_MODIFIERS, HAIRS
from html_template import render_html
from svg_generator import render_bone_svg, render_hair_svg


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync_anatomy_bone_links() -> None:
    """Ensure bidirectional symmetry between anatomy plates (A001-A007) and bones (B001-B024)."""
    plate_map = {p["id"]: p for p in ANATOMY_PLATES}
    bone_map = {b["id"]: b for b in BONES}
    a_to_b = {aid: set(p.get("linked_bones", [])) for aid, p in plate_map.items()}
    b_to_a = {bid: set(b.get("linked_anatomy_plates", [])) for bid, b in bone_map.items()}
    for aid, bids in list(a_to_b.items()):
        for bid in bids:
            if bid in b_to_a:
                b_to_a[bid].add(aid)
    for bid, aids in list(b_to_a.items()):
        for aid in aids:
            if aid in a_to_b:
                a_to_b[aid].add(bid)
    for aid, p in plate_map.items():
        p["linked_bones"] = sorted(a_to_b[aid])
    for bid, b in bone_map.items():
        b["linked_anatomy_plates"] = sorted(b_to_a[bid])


def build_index_md() -> str:
    lines = [
        "# 人物骨相与发型参考资产库索引（⚠️ 仅作参考 · 不可直接使用）",
        "",
        f"> **{USAGE_WARNING_ZH}**",
        "> 编写最终角色设定时，请先阅读 [`character-design-template.md`](character-design-template.md)，按需只读取目标条目的 JSON 文件，并严格按 7 维公式重新原创改写。",
        "",
        "---",
        "",
        "## 一、 开源颅面解剖与骨相原图（`A001–A007`，联动 7 维公式第 1/2/3/5 维）",
        "",
        "| 编号 | 视图 | 名称 | 协议 | 对应公式维度 | 关联骨相 | 原图 / 条目 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for a in ANATOMY_PLATES:
        bones_str = ", ".join(a["linked_bones"])
        lines.append(
            f"| `{a['id']}` | {a['view']} | {a['name']} | {a['license']} | {a['formula_link']['dimension']} | {bones_str} | [条目](entries/{a['id']}.json) |"
        )

    lines += [
        "",
        "---",
        "",
        "## 二、 人物骨相参考库（`B001–B024`，映射公式第 3 维 `[面部细节特征]`）",
        "",
        "| 编号 | 分组 | 骨相名称 | 下颌折角 | 关联解剖图 | 适用人设 | 条目 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for b in BONES:
        ap = ", ".join(b["linked_anatomy_plates"])
        roles = " / ".join(b["suitable_roles"][:3])
        lines.append(
            f"| `{b['id']}` | {b['group']} | {b['name']} | ≈{b['proportions']['jaw_angle_deg']}° | {ap} | {roles} | [读取](entries/{b['id']}.json) |"
        )

    lines += [
        "",
        "---",
        "",
        "## 三、 人物发型参考库（`H001–H042`，映射公式第 5 维 `[发型 / 发色]`）",
        "",
        "| 编号 | 分组 | 发型名称 | 时代/风格 | 推荐骨相 | 条目 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for h in HAIRS:
        sb = ", ".join(h["suggested_bones"])
        lines.append(
            f"| `{h['id']}` | {h['group']} | {h['name']} | {h['era']} | {sb} | [读取](entries/{h['id']}.json) |"
        )

    lines += [
        "",
        "---",
        "",
        "## 四、 发色与层次修饰词速查（映射公式第 5 维 `[具体发色]`）",
        "",
        "| 分类 | 中文参考词 | 英文参考词 | 适用场景 |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for c in HAIR_COLOR_MODIFIERS:
        lines.append(f"| {c['category']} | {c['zh']} | `{c['en']}` | {c['note']} |")

    return "\n".join(lines) + "\n"


def main() -> int:
    sync_anatomy_bone_links()
    bones_dir = SKILL_ROOT / "assets" / "bones"
    hairs_dir = SKILL_ROOT / "assets" / "hairs"
    entries_dir = SKILL_ROOT / "references" / "entries"
    bones_dir.mkdir(parents=True, exist_ok=True)
    hairs_dir.mkdir(parents=True, exist_ok=True)
    entries_dir.mkdir(parents=True, exist_ok=True)

    # 1. Verify existing anatomy plates & 7-dim formula reference image
    missing = []
    formula_img = SKILL_ROOT / FORMULA_7DIM["reference_image"]
    if not formula_img.is_file():
        missing.append(str(formula_img))
    for plate in ANATOMY_PLATES:
        p = SKILL_ROOT / plate["file"]
        if not p.is_file():
            missing.append(str(p))
        write_json(entries_dir / f"{plate['id']}.json", plate)

    # 2. Generate Bone SVGs & Entry JSONs
    for b in BONES:
        (bones_dir / f"{b['id']}.svg").write_text(render_bone_svg(b), encoding="utf-8")
        write_json(entries_dir / f"{b['id']}.json", b)

    # 3. Generate Hair SVGs & Entry JSONs
    for h in HAIRS:
        (hairs_dir / f"{h['id']}.svg").write_text(render_hair_svg(h), encoding="utf-8")
        write_json(entries_dir / f"{h['id']}.json", h)

    # 4. Build unified catalog.json
    catalog = {
        "schema_version": "1.0.0",
        "skill": "character-face-hair-reference",
        "usage_policy": USAGE_POLICY,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "counts": {
            "anatomy_plates": len(ANATOMY_PLATES),
            "bones": len(BONES),
            "hairs": len(HAIRS),
            "hair_color_modifiers": len(HAIR_COLOR_MODIFIERS),
        },
        "formula_7dim": FORMULA_7DIM,
        "anatomy_plates": ANATOMY_PLATES,
        "bones": BONES,
        "hairs": HAIRS,
        "hair_color_modifiers": HAIR_COLOR_MODIFIERS,
        "external_sources": EXTERNAL_3D_2D_SOURCES,
    }
    write_json(SKILL_ROOT / "references" / "catalog.json", catalog)

    # 5. Build index.md & interactive HTML workbench
    (SKILL_ROOT / "references" / "index.md").write_text(build_index_md(), encoding="utf-8")
    html_path = SKILL_ROOT / "人物骨相与发型库.html"
    html_path.write_text(render_html(catalog), encoding="utf-8")

    print(f"BUILD_OK: anatomy={len(ANATOMY_PLATES)}, bones={len(BONES)}, hairs={len(HAIRS)}")
    print(f"CATALOG: {SKILL_ROOT / 'references' / 'catalog.json'}")
    print(f"INDEX:   {SKILL_ROOT / 'references' / 'index.md'}")
    print(f"HTML:    {html_path} ({html_path.stat().st_size} bytes)")
    if missing:
        print("MISSING_ASSETS:", missing)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
