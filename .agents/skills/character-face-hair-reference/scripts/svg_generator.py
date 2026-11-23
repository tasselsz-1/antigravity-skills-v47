# -*- coding: utf-8 -*-
"""Generate clean schematic SVG diagrams for Bone (B001-B024) and Hair (H001-H042) references.
All diagrams include a clear '仅作参考 · 不可直接使用' watermark badge.
"""

from xml.sax.saxutils import escape


def render_bone_svg(entry: dict) -> str:
    p = entry["svg_params"]
    bid = escape(entry["id"])
    tag = escape(p.get("tag", entry["name"][:6]))
    accent = p.get("accent", "#6366f1")
    face_w = p["face_w"]
    cheek_w = p["cheek_w"]
    jaw_w = p["jaw_w"]
    chin_y = p["chin_y"]
    chin_w = p["chin_w"]
    brow_depth = p["brow_depth"]
    mid_ratio = p["mid_ratio"]
    jaw_deg = entry["proportions"]["jaw_angle_deg"]

    # Front schematic centered at x=98, top=32
    cx = 98
    top_y = 32
    total_h = chin_y - top_y
    hair_y = top_y + total_h * 0.14
    brow_y = hair_y + total_h * (0.28 + (0.34 - mid_ratio) * 0.25)
    nose_y = brow_y + total_h * mid_ratio
    eye_y = brow_y + (nose_y - brow_y) * 0.28
    mouth_y = nose_y + (chin_y - nose_y) * 0.36
    cheek_y = brow_y + (nose_y - brow_y) * 0.56
    jaw_y = nose_y + (chin_y - nose_y) * 0.50

    # Front skull / face contour path
    front_path = (
        f"M {cx},{top_y} "
        f"C {cx + face_w * 0.72},{top_y} {cx + face_w},{top_y + 18} {cx + face_w * 0.96},{brow_y:.1f} "
        f"C {cx + cheek_w},{cheek_y - 6:.1f} {cx + cheek_w},{cheek_y + 8:.1f} {cx + jaw_w},{jaw_y:.1f} "
        f"Q {cx + jaw_w * 0.82},{chin_y - 4:.1f} {cx + chin_w * 0.5},{chin_y:.1f} "
        f"L {cx - chin_w * 0.5},{chin_y:.1f} "
        f"Q {cx - jaw_w * 0.82},{chin_y - 4:.1f} {cx - jaw_w},{jaw_y:.1f} "
        f"C {cx - cheek_w},{cheek_y + 8:.1f} {cx - cheek_w},{cheek_y - 6:.1f} {cx - face_w * 0.96},{brow_y:.1f} "
        f"C {cx - face_w},{top_y + 18} {cx - face_w * 0.72},{top_y} {cx},{top_y} Z"
    )

    # Side profile schematic centered around x=232
    sx = 236
    brow_x = sx + 22 + brow_depth * 0.65
    nose_tip_x = brow_x + 7 + brow_depth * 0.35
    lip_x = brow_x + 2
    chin_x = brow_x - 1 + (120 - jaw_deg) * 0.14
    jaw_back_x = sx - 12
    occiput_x = sx - 42

    side_path = (
        f"M {sx - 10},{top_y} "
        f"C {sx + 16},{top_y} {brow_x - 4},{hair_y:.1f} {brow_x},{brow_y:.1f} "
        f"L {brow_x - 3.5},{eye_y:.1f} "
        f"L {nose_tip_x:.1f},{nose_y - 3:.1f} "
        f"L {lip_x - 2:.1f},{nose_y + 2:.1f} "
        f"Q {lip_x + 2:.1f},{mouth_y:.1f} {lip_x - 3:.1f},{mouth_y + 8:.1f} "
        f"Q {chin_x + 3:.1f},{chin_y - 6:.1f} {chin_x:.1f},{chin_y:.1f} "
        f"L {jaw_back_x},{jaw_y + 6:.1f} "
        f"L {jaw_back_x - 6},{cheek_y + 4:.1f} "
        f"C {occiput_x},{cheek_y:.1f} {occiput_x},{top_y + 18} {sx - 10},{top_y} Z"
    )

    eye_sep = 16 if bid != "B008" else 20
    if bid == "B010":
        eye_sep = 13.5

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 190" width="320" height="190">
  <defs>
    <linearGradient id="bg_{bid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="100%" stop-color="#1e293b"/>
    </linearGradient>
    <linearGradient id="bone_{bid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{accent}" stop-opacity="0.26"/>
      <stop offset="100%" stop-color="{accent}" stop-opacity="0.08"/>
    </linearGradient>
  </defs>
  <rect width="320" height="190" rx="12" fill="url(#bg_{bid})"/>
  <g stroke="#334155" stroke-width="0.7" stroke-dasharray="3,3" opacity="0.75">
    <line x1="24" y1="{hair_y:.1f}" x2="296" y2="{hair_y:.1f}"/>
    <line x1="24" y1="{brow_y:.1f}" x2="296" y2="{brow_y:.1f}"/>
    <line x1="24" y1="{nose_y:.1f}" x2="296" y2="{nose_y:.1f}"/>
    <line x1="24" y1="{chin_y:.1f}" x2="296" y2="{chin_y:.1f}"/>
    <line x1="{cx}" y1="24" x2="{cx}" y2="162"/>
  </g>
  <g font-family="system-ui, -apple-system, sans-serif" font-size="8" fill="#94a3b8">
    <text x="10" y="{hair_y + 3:.1f}">上</text>
    <text x="10" y="{brow_y + 3:.1f}">眉</text>
    <text x="10" y="{nose_y + 3:.1f}">鼻</text>
    <text x="10" y="{chin_y + 3:.1f}">颏</text>
  </g>
  <!-- Front Contour -->
  <path d="{front_path}" fill="url(#bone_{bid})" stroke="{accent}" stroke-width="2"/>
  <!-- Brow ridge & Zygomatic landmark plane -->
  <path d="M {cx - face_w * 0.76:.1f},{brow_y:.1f} Q {cx},{brow_y - brow_depth * 0.55:.1f} {cx + face_w * 0.76:.1f},{brow_y:.1f}" fill="none" stroke="#e2e8f0" stroke-width="1.4" opacity="0.85"/>
  <path d="M {cx - cheek_w * 0.92:.1f},{cheek_y:.1f} L {cx - jaw_w * 0.65:.1f},{jaw_y:.1f} M {cx + cheek_w * 0.92:.1f},{cheek_y:.1f} L {cx + jaw_w * 0.65:.1f},{jaw_y:.1f}" stroke="{accent}" stroke-width="1.1" stroke-dasharray="2,2" opacity="0.85"/>
  <!-- Eyes & Nose & Lips landmarks -->
  <ellipse cx="{cx - eye_sep:.1f}" cy="{eye_y:.1f}" rx="8" ry="3.6" fill="none" stroke="#cbd5e1" stroke-width="1.2"/>
  <ellipse cx="{cx + eye_sep:.1f}" cy="{eye_y:.1f}" rx="8" ry="3.6" fill="none" stroke="#cbd5e1" stroke-width="1.2"/>
  <path d="M {cx},{brow_y + 2:.1f} L {cx},{nose_y:.1f} M {cx - 6},{nose_y - 1:.1f} Q {cx},{nose_y + 3:.1f} {cx + 6},{nose_y - 1:.1f}" fill="none" stroke="#cbd5e1" stroke-width="1.2"/>
  <path d="M {cx - 11},{mouth_y:.1f} Q {cx},{mouth_y + 3:.1f} {cx + 11},{mouth_y:.1f}" fill="none" stroke="#cbd5e1" stroke-width="1.2"/>
  <!-- Key bone landmark dots -->
  <g fill="#f8fafc" stroke="{accent}" stroke-width="1">
    <circle cx="{cx - cheek_w + 1:.1f}" cy="{cheek_y:.1f}" r="2.4"/>
    <circle cx="{cx + cheek_w - 1:.1f}" cy="{cheek_y:.1f}" r="2.4"/>
    <circle cx="{cx - jaw_w:.1f}" cy="{jaw_y:.1f}" r="2.4"/>
    <circle cx="{cx + jaw_w:.1f}" cy="{jaw_y:.1f}" r="2.4"/>
    <circle cx="{cx}" cy="{chin_y:.1f}" r="2.4"/>
  </g>
  <!-- Lateral Profile -->
  <path d="{side_path}" fill="url(#bone_{bid})" stroke="{accent}" stroke-width="1.8"/>
  <circle cx="{jaw_back_x}" cy="{jaw_y + 6:.1f}" r="2.5" fill="#fbbf24"/>
  <text x="{jaw_back_x - 24}" y="{jaw_y + 20:.1f}" font-family="monospace" font-size="8.5" fill="#fbbf24">{jaw_deg}°</text>
  <!-- Labels & Warning -->
  <rect x="10" y="8" width="52" height="18" rx="4" fill="{accent}" opacity="0.9"/>
  <text x="36" y="20" text-anchor="middle" font-family="monospace" font-size="10" font-weight="bold" fill="#ffffff">{bid}</text>
  <text x="70" y="20" font-family="system-ui, -apple-system, sans-serif" font-size="10" font-weight="600" fill="#e2e8f0">{tag}</text>
  <rect x="178" y="8" width="132" height="17" rx="4" fill="#7f1d1d" stroke="#ef4444" stroke-width="0.8"/>
  <text x="244" y="19.5" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="8.2" font-weight="600" fill="#fecaca">仅作参考 · 不可直接使用</text>
  <text x="98" y="180" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="9" fill="#94a3b8">正面骨点比例示意图</text>
  <text x="234" y="180" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="9" fill="#94a3b8">侧颜折角示意</text>
</svg>'''



_HAIR_SHAPES = {
    "hime": '<path d="M98,38 C64,38 50,62 50,156 L67,156 L67,118 L74,118 L74,78 L122,78 L122,118 L129,118 L129,156 L146,156 C146,62 132,38 98,38 Z"/>',
    "high_ponytail": '<path d="M98,40 C68,40 60,64 64,92 L72,82 Q98,64 124,82 L132,92 C136,64 128,40 98,40 Z"/><path d="M126,56 C158,42 174,76 164,142 C154,112 146,84 126,74 Z"/>',
    "twintails": '<path d="M98,40 C68,40 62,62 66,86 Q98,68 130,86 C134,62 128,40 98,40 Z"/><path d="M64,56 C38,54 28,92 38,148 C48,120 54,90 66,76 Z"/><path d="M132,56 C158,54 168,92 158,148 C148,120 142,90 130,76 Z"/>',
    "drills": '<path d="M98,40 C68,40 60,64 64,86 Q98,68 132,86 C136,64 128,40 98,40 Z"/><ellipse cx="52" cy="112" rx="14" ry="38"/><ellipse cx="144" cy="112" rx="14" ry="38"/>',
    "half_up": '<path d="M98,38 C66,38 56,64 56,154 L72,150 L72,84 Q98,64 124,84 L124,150 L140,154 C140,64 130,38 98,38 Z"/><ellipse cx="98" cy="40" rx="18" ry="10"/>',
    "long_waves": '<path d="M98,36 C60,36 46,64 52,96 C44,116 48,140 58,158 L72,152 C68,126 70,96 74,78 Q98,64 122,78 C126,96 128,126 124,152 L138,158 C148,140 152,116 144,96 C150,64 136,36 98,36 Z"/>',
    "blunt_bob": '<path d="M98,38 C64,38 56,64 58,124 L74,124 L74,78 Q98,66 122,78 L122,124 L138,124 C140,64 132,38 98,38 Z"/>',
    "wolf_female": '<path d="M98,36 C64,36 54,60 56,96 L46,128 L66,118 L58,148 L74,132 L74,78 Q98,64 122,78 L122,132 L138,148 L130,118 L150,128 L140,96 C142,60 132,36 98,36 Z"/>',
    "double_buns": '<circle cx="64" cy="44" r="16"/><circle cx="132" cy="44" r="16"/><path d="M98,42 C70,42 62,64 66,88 Q98,68 130,88 C134,64 126,42 98,42 Z"/>',
    "side_braid": '<path d="M98,38 C66,38 58,64 62,102 L72,82 Q98,66 124,82 L134,102 C138,64 130,38 98,38 Z"/><path d="M126,98 C138,112 126,126 138,140 C130,152 124,160 120,164 C114,154 122,140 116,126 C122,112 118,104 126,98 Z"/>',
    "low_bun": '<path d="M98,40 C68,40 60,64 64,96 L72,80 Q98,64 124,80 L132,96 C136,64 128,40 98,40 Z"/><circle cx="98" cy="144" r="16"/>',
    "one_eye": '<path d="M98,38 C62,38 52,64 56,148 L72,140 L72,90 L118,108 L124,78 L126,144 L142,148 C144,64 134,38 98,38 Z"/>',
    "ahoge_short": '<path d="M98,42 C68,42 58,64 62,106 L74,94 L76,76 Q98,66 120,76 L122,94 L134,106 C138,64 128,42 98,42 Z"/><path d="M98,42 Q108,16 122,26 Q106,30 102,42 Z"/>',
    "ethereal_long": '<path d="M98,34 C58,34 40,68 42,166 L66,158 L70,84 Q98,64 126,84 L130,158 L154,166 C156,68 138,34 98,34 Z"/>',
    "ancient_gaoji": '<path d="M98,18 C78,18 72,38 78,48 C62,54 58,76 64,98 L72,78 Q98,62 124,78 L132,98 C138,76 134,54 118,48 C124,38 118,18 98,18 Z"/><line x1="68" y1="32" x2="128" y2="32" stroke="#fbbf24" stroke-width="2.5"/>',
    "ancient_duoma": '<path d="M98,38 C70,38 62,62 66,94 L72,78 Q98,64 124,78 L132,94 C136,62 128,38 98,38 Z"/><ellipse cx="136" cy="76" rx="18" ry="26" transform="rotate(24 136 76)"/>',
    "ancient_feixian": '<ellipse cx="70" cy="36" rx="16" ry="18" fill="none" stroke-width="7"/><ellipse cx="126" cy="36" rx="16" ry="18" fill="none" stroke-width="7"/><path d="M98,42 C66,42 56,70 58,154 L72,148 L72,78 Q98,64 124,78 L124,148 L138,154 C140,70 130,42 98,42 Z"/>',
    "ancient_lingshe": '<path d="M98,26 C72,26 62,56 64,104 L74,78 Q98,64 122,78 L134,110 C142,66 130,26 98,26 Z"/><path d="M74,34 C96,16 126,26 130,52" fill="none" stroke-width="6"/>',
    "ancient_yaji": '<path d="M98,40 C70,40 62,62 66,86 Q98,66 130,86 C134,62 126,40 98,40 Z"/><ellipse cx="56" cy="106" rx="11" ry="20" fill="none" stroke-width="6"/><ellipse cx="140" cy="106" rx="11" ry="20" fill="none" stroke-width="6"/>',
    "ancient_low_buyao": '<path d="M98,38 C68,38 60,64 64,96 L72,76 Q98,60 124,76 L132,96 C136,64 128,38 98,38 Z"/><ellipse cx="98" cy="144" rx="24" ry="12"/><line x1="120" y1="62" x2="146" y2="54" stroke="#fbbf24" stroke-width="2"/><circle cx="146" cy="64" r="3" fill="#fbbf24"/>',
    "ancient_song": '<path d="M98,40 C70,40 62,64 66,94 L74,76 Q98,62 122,76 L130,94 C134,64 126,40 98,40 Z"/><ellipse cx="98" cy="140" rx="20" ry="10"/><circle cx="88" cy="46" r="2.2" fill="#f8fafc"/><circle cx="98" cy="44" r="2.2" fill="#f8fafc"/><circle cx="108" cy="46" r="2.2" fill="#f8fafc"/>',
    "ancient_wuxia_f": '<rect x="88" y="24" width="20" height="14" rx="3" fill="#fbbf24"/><path d="M98,38 C68,38 60,64 64,92 L72,76 Q98,60 124,76 L132,92 C136,64 128,38 98,38 Z"/><path d="M124,52 C158,48 168,94 156,154 C146,120 142,84 124,72 Z"/>',
    "male_buzz": '<path d="M98,42 C72,42 64,62 66,82 L72,72 Q98,58 124,72 L130,82 C132,62 124,42 98,42 Z"/>',
    "male_slick": '<path d="M98,34 C68,34 62,56 66,82 L74,66 Q104,54 128,70 L132,82 C136,54 126,34 98,34 Z"/>',
    "male_curtain": '<path d="M98,38 C66,38 58,62 62,92 L74,78 Q88,62 96,78 Q108,62 122,78 L134,92 C138,62 130,38 98,38 Z"/>',
    "male_crop": '<path d="M98,36 C64,36 58,62 62,88 L74,76 L84,82 L96,74 L110,82 L122,76 L134,88 C138,62 132,36 98,36 Z"/>',
    "male_wolf": '<path d="M98,36 C64,36 56,62 58,98 L50,136 L70,122 L74,76 L94,84 L112,74 L122,76 L126,122 L146,136 L138,98 C140,62 132,36 98,36 Z"/>',
    "male_spiky": '<path d="M98,20 L110,36 L132,26 L126,48 L146,52 L132,74 L124,72 Q98,62 72,72 L64,74 L50,52 L70,48 L64,26 L86,36 Z"/>',
    "male_center_wave": '<path d="M98,38 C64,38 54,64 56,118 L72,114 L74,76 Q98,64 122,76 L124,114 L140,118 C142,64 132,38 98,38 Z"/>',
    "male_manbun": '<circle cx="98" cy="28" r="12"/><path d="M98,38 C68,38 62,62 66,92 L72,74 Q98,60 124,74 L130,92 C134,62 128,38 98,38 Z"/>',
    "male_silver_back": '<path d="M98,36 C68,36 62,58 66,84 L74,68 Q98,56 122,68 L130,84 C134,58 128,36 98,36 Z"/><rect x="64" y="72" width="6" height="14" fill="#cbd5e1"/><rect x="126" y="72" width="6" height="14" fill="#cbd5e1"/>',
    "male_long_fringe": '<path d="M98,36 C64,36 56,62 60,124 L74,114 L76,82 L114,104 L122,78 L126,120 L138,124 C142,62 132,36 98,36 Z"/>',
    "ancient_m_jade": '<rect x="86" y="20" width="24" height="16" rx="4" fill="#38bdf8"/><path d="M98,36 C64,36 54,66 56,154 L72,148 L72,74 Q98,58 124,74 L124,148 L140,154 C142,66 132,36 98,36 Z"/>',
    "ancient_m_formal": '<path d="M82,16 L114,16 L110,36 L86,36 Z" fill="#eab308"/><path d="M98,36 C70,36 62,60 66,86 L72,70 Q98,56 124,70 L130,86 C134,60 126,36 98,36 Z"/>',
    "ancient_m_taoist": '<circle cx="98" cy="28" r="11"/><line x1="74" y1="28" x2="122" y2="28" stroke="#d97706" stroke-width="2.5"/><path d="M98,38 C64,38 56,68 58,152 L72,146 L72,76 Q98,60 124,76 L124,146 L138,152 C140,68 132,38 98,38 Z"/>',
    "ancient_m_wild": '<path d="M98,36 C60,36 46,66 48,152 L68,140 L72,78 Q98,64 124,78 L128,140 L148,152 C150,66 136,36 98,36 Z"/><rect x="64" y="54" width="68" height="7" rx="3" fill="#dc2626"/>',
    "ancient_m_general": '<path d="M88,18 L108,18 L106,38 L90,38 Z"/><rect x="86" y="30" width="24" height="6" rx="2" fill="#94a3b8"/><path d="M98,38 C70,38 62,62 66,88 L72,70 Q98,56 124,70 L130,88 C134,62 126,38 98,38 Z"/>',
    "ancient_m_kerchief": '<path d="M68,54 C68,26 128,26 128,54 L124,68 Q98,56 72,68 Z" fill="#64748b"/><rect x="64" y="72" width="6" height="14" fill="#cbd5e1"/><rect x="126" y="72" width="6" height="14" fill="#cbd5e1"/>',
    "ancient_m_monk": '<ellipse cx="98" cy="92" rx="31" ry="48" fill="none" stroke="#94a3b8" stroke-dasharray="3,3" stroke-width="1.5"/>',
    "epic_nomad_braids": '<path d="M98,38 C70,38 62,62 66,88 L72,72 Q98,58 124,72 L130,88 C134,62 126,38 98,38 Z"/><g stroke-width="4" stroke-dasharray="6,3"><line x1="62" y1="86" x2="54" y2="152"/><line x1="68" y1="88" x2="64" y2="150"/><line x1="134" y1="86" x2="142" y2="152"/><line x1="128" y1="88" x2="132" y2="150"/></g>',
    "epic_viking": '<path d="M98,28 C82,28 78,48 80,68 Q98,58 116,68 C118,48 114,28 98,28 Z"/><path d="M116,42 C142,46 150,82 142,126" fill="none" stroke-width="7" stroke-dasharray="8,4"/>',
    "epic_medieval": '<path d="M98,36 C60,36 48,66 52,134 L72,128 C70,104 72,82 74,74 Q98,60 122,74 C124,82 126,104 124,128 L144,134 C148,66 136,36 98,36 Z"/>',
}


def render_hair_svg(entry: dict) -> str:
    hid = escape(entry["id"])
    shape_key = entry["svg_params"]["shape"]
    accent = entry["svg_params"]["accent"]
    era = escape(entry["era"])
    gender = escape(entry["gender"])
    shape_svg = _HAIR_SHAPES.get(shape_key, _HAIR_SHAPES["half_up"])

    era_line = f"时代/风格：{era}"
    era_est_w = sum(10.0 if ord(c) > 127 else 5.8 for c in era_line)
    era_font = 9.5 if era_est_w <= 134 else 8.6
    era_fit_attr = ' textLength="134" lengthAdjust="spacingAndGlyphs"' if (era_est_w * (era_font / 10.0)) > 134 else ""

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 185" width="320" height="185">
  <defs>
    <linearGradient id="hbg_{hid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0f172a"/>
      <stop offset="100%" stop-color="#1e1b4b"/>
    </linearGradient>
  </defs>
  <rect width="320" height="185" rx="12" fill="url(#hbg_{hid})"/>
  <!-- Neck & shoulders guide -->
  <path d="M84,136 L84,154 L54,174 L142,174 L112,154 L112,136 Z" fill="#1e293b" stroke="#475569" stroke-width="1"/>
  <!-- Neutral head silhouette -->
  <ellipse cx="98" cy="92" rx="30" ry="44" fill="#334155" stroke="#94a3b8" stroke-width="1.2"/>
  <path d="M84,92 Q90,94 94,92 M102,92 Q106,94 112,92 M94,114 Q98,117 102,114" fill="none" stroke="#94a3b8" stroke-width="1" opacity="0.7"/>
  <!-- Hair silhouette -->
  <g fill="{accent}" fill-opacity="0.78" stroke="{accent}" stroke-width="1.5">
    {shape_svg}
  </g>
  <!-- Metadata panel on right -->
  <rect x="176" y="10" width="134" height="18" rx="4" fill="#7f1d1d" stroke="#ef4444" stroke-width="0.8"/>
  <text x="243" y="22" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif" font-size="8.2" font-weight="600" fill="#fecaca">仅作参考 · 不可直接使用</text>
  <rect x="176" y="38" width="54" height="20" rx="5" fill="{accent}"/>
  <text x="203" y="51.5" text-anchor="middle" font-family="monospace" font-size="11" font-weight="bold" fill="#ffffff">{hid}</text>
  <text x="238" y="51.5" font-family="system-ui, -apple-system, sans-serif" font-size="10.5" fill="#cbd5e1">{gender}</text>
  <text x="176" y="76" font-family="system-ui, -apple-system, sans-serif" font-size="{era_font}" fill="#94a3b8"{era_fit_attr}>{era_line}</text>
  <text x="176" y="96" font-family="system-ui, -apple-system, sans-serif" font-size="9.5" fill="#94a3b8">映射公式第 5 维</text>
  <text x="176" y="112" font-family="system-ui, -apple-system, sans-serif" font-size="9.5" fill="#e2e8f0">[发型 / 发色] 剪影结构</text>
  <text x="176" y="140" font-family="system-ui, -apple-system, sans-serif" font-size="8.8" fill="#cbd5e1">• 结合发色与状态改写</text>
  <text x="176" y="156" font-family="system-ui, -apple-system, sans-serif" font-size="8.8" fill="#cbd5e1">• 须补充环境风动细节</text>
</svg>'''
