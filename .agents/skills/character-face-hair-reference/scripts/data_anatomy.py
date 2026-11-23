# -*- coding: utf-8 -*-
"""7-dimensional character design formula and anatomical bone-plate reference data.
Policy: reference_only_do_not_use_directly.
"""

USAGE_POLICY = "reference_only_do_not_use_directly"
USAGE_WARNING_ZH = "⚠️ 仅作参考 · 不可直接使用（须结合剧本身份、时代背景与画风按 7 维人物设计公式原创改写）"
USAGE_WARNING_EN = "REFERENCE ONLY — DO NOT USE DIRECTLY. Rewrite originally via the 7-dimension character design formula."

FORMULA_7DIM = {
    "title": "人物设计公式模板（真人与动画人物通用）",
    "policy": USAGE_POLICY,
    "warning": USAGE_WARNING_ZH,
    "reference_image": "references/sources/7dim_character_formula_ref.jpg",
    "overall_formula": (
        "人物 = [1. 年龄 / 种族] + [2. 肤色 / 皮肤质感] + [3. 面部细节特征] "
        "+ [4. 眼神 / 灵魂] + [5. 发型 / 发色] + [6. 服装 / 服装材质] "
        "+ [7. 体型 / 情绪 / 气质] + [其他要求（如有）]"
    ),
    "dimensions": [
        {
            "index": 1,
            "key": "age_ethnicity",
            "name": "年龄 / 种族",
            "role": "锁定人物年龄基调、人种与基础脸型",
            "sub_formula": "[具体岁数] + [具体国籍 / 人种] + [风格形容词] + [脸型名词]",
            "linked_library": None,
            "examples_ref_only": [
                "22 岁东亚女性，清冷古典的丹凤脸",
                "25 岁英国男性，瘦削高傲的英伦贵族脸",
                "26 岁非裔黑人女性，骨相极其立体的黑珍珠超模脸",
            ],
        },
        {
            "index": 2,
            "key": "skin_texture",
            "name": "肤色 / 皮肤质感",
            "role": "避免 AI 磨皮塑胶白，增强真实微细肌理",
            "sub_formula": "[冷暖色调] + [具体肤色名词] + [皮肤质感形容词] + [强制保真后缀：保留真实的微细毛孔与肌肤纹理（可增加雀斑 / 瑕疵）]",
            "linked_library": None,
            "examples_ref_only": [
                "冷调白皙肤色，透出细腻的温润玉石质感，保留真实的微细毛孔与肌肤纹理",
                "暖调蜜糖色，带有阳光照射后的光泽感，鼻梁处有淡淡的真实雀斑，保留微细毛孔与肌肤纹理",
                "粗糙的小麦色皮肤，带有风霜感与细微干纹，保留真实的粗大毛孔、胡茬质感与肌肤纹理",
            ],
        },
        {
            "index": 3,
            "key": "facial_bone_details",
            "name": "面部细节特征",
            "role": "决定角色辨识度与多人防同脸（至少组合 3–4 个解剖维度）",
            "sub_formula": "[眼型描述] + [眉骨特征] + [鼻梁特征] + [唇形特征] + [下颌线 / 骨相特征]",
            "linked_library": "bones (B001–B024) + anatomy (A001–A007)",
            "examples_ref_only": [
                "细长的桃花眼（眼眶微润），鼻梁小巧挺直，唇部丰润且嘴角带着若有若无的浅笑，下颌线条柔和",
                "微微上挑的丹凤眼，折叠度极高的立体眉骨，鼻梁高挺光洁，颧骨微凸，下颌线像刀削般清晰利落",
                "圆润的杏眼，眼睑下至带无辜感，鼻头微翘略带肉感，饱满的唇珠，脸颊带有轻微的婴儿肥，面部线条流畅",
            ],
        },
        {
            "index": 4,
            "key": "gaze_soul",
            "name": "眼神 / 灵魂",
            "role": "赋予人物情绪内核、心理层次与戏剧张力",
            "sub_formula": "[眼神形容词] + [目光传达的信息 / 比喻] + [透出的深层情绪]",
            "linked_library": None,
            "examples_ref_only": [
                "眼神极其深情、含情脉脉，眼底仿佛有一汪春水，目光温柔而专注，透着深深的眷恋与一丝不舍",
                "眼神极为冷静、空洞，像深不见底的黑洞，目光锐利且极具压迫感，透着一丝难以捉摸的危险气息",
                "眼神涣散、暗淡无光，目光低垂没有焦点，透着深深的疲惫与对生活的麻木感",
            ],
        },
        {
            "index": 5,
            "key": "hair_style_color",
            "name": "发型 / 发色",
            "role": "拒绝单调词，明确发色、状态质感、具体发型名词与动静细节",
            "sub_formula": "[具体发色] + [头发状态 / 质感] + [具体发型名词] + [环境与动静补充（如被风吹 / 凌乱）]",
            "linked_library": "hairs (H001–H042)",
            "examples_ref_only": [
                "乌黑的发丝盘成随性典雅的古典低盘发，仅用一支素色玉簪固定，几缕细碎鬓发垂落脸颊随微风轻拂",
                "焦糖棕色的微卷长发，呈现刚睡醒的自然蓬松与凌乱感，几缕发丝随意地搭在锁骨处",
                "银白色的齐耳短发，带有强烈的哑光偏光质感，发丝边缘锐利，紧贴头皮",
            ],
        },
        {
            "index": 6,
            "key": "costume_material",
            "name": "服装 / 服装材质",
            "role": "通过款式剪裁、面料肌理与新旧状态侧面烘托时代与身份",
            "sub_formula": "[繁简 / 剪裁] + [颜色] + [具体服装名词] + [布料材质 / 新旧状态] + [穿着方式 / 细节]",
            "linked_library": None,
            "examples_ref_only": [
                "极简的素白色交领古装，布料带有柔软光泽的素雅真丝或轻纱，衣领处微微敞开，无多余刺绣",
                "宽大的做旧深棕色机车皮夹克，皮革表面带有真实的自然折痕与金属拉链反光，内搭洗到发白的灰色 T 恤",
                "剪裁极其修身的深黑色双排扣西装，采用挺括的高级羊毛精纺面料，搭配酒红色真丝领带，一丝不苟",
            ],
        },
        {
            "index": 7,
            "key": "body_mood_aura",
            "name": "体型 / 情绪 / 气质",
            "role": "决定骨架体态、镜头构图与整体故事氛围（若分镜已含构图/动作可省略该两项）",
            "sub_formula": "[骨架 / 肩部特征] + [构图 / 镜头要求] + [人物动作 / 视线] + [整体散发的氛围词]",
            "linked_library": None,
            "examples_ref_only": [
                "身姿纤细，双肩削瘦，胸部以上半身特写构图，眼神直视镜头，散发温婉深情的古典浪漫气质",
                "骨架宽大，肩膀厚实微圆，低机位仰拍全身构图，侧身站立回头，散发绝境求生的悲壮电影史诗感",
                "身形修长挺拔，面部大特写构图，半张脸隐没阴影中，散发神秘危险的惊悚电影主角气质",
            ],
        },
    ],
}

ANATOMY_PLATES = [
    {
        "id": "A001",
        "name": "头部三庭五眼与标准比例图",
        "en_name": "Human Head Proportions (Three Courts & Five Eyes)",
        "file": "assets/anatomy/head_proportions.svg",
        "view": "正面比例基准",
        "license": "CC BY-SA 4.0",
        "source_url": "https://commons.wikimedia.org/wiki/File:Human_head_proportions.svg",
        "usage_policy": USAGE_POLICY,
        "usage_warning": USAGE_WARNING_ZH,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "formula_link": {
            "dimension": "1. 年龄 / 种族 & 3. 面部细节特征",
            "slots": ["脸型名词", "眼型描述", "中庭比例", "下颌收束比例"],
            "how_to_use": "核对上/中/下三庭占比与眼距：幼态/少女调短中庭与下巴（B006/B012），清冷智性/谋士微拉长中庭（B005/B018），高级文艺脸微放宽眼距（B008）。",
        },
        "mapped_landmarks": [
            {"landmark": "上庭（发际线→眉弓）", "slot": "1. 脸型名词 / 5. 发型发际线", "related_bones": ["B006", "B007", "B019"]},
            {"landmark": "中庭（眉弓→鼻底）", "slot": "3. 眼型 + 鼻梁特征", "related_bones": ["B005", "B008", "B010", "B016"]},
            {"landmark": "下庭（鼻底→颏下）", "slot": "3. 唇形 + 下颌线特征", "related_bones": ["B003", "B004", "B017", "B021"]},
        ],
        "linked_bones": ["B002", "B005", "B006", "B008", "B011", "B012", "B016"],
    },
    {
        "id": "A002",
        "name": "正面颅面分骨色块图（额骨/颧骨/鼻骨/上下颌骨）",
        "en_name": "Human Skull Front — Color-Coded Facial Bones",
        "file": "assets/anatomy/skull_front_bones.svg",
        "view": "正面分骨解剖",
        "license": "Public domain (Mariana Ruiz Villarreal / LadyofHats)",
        "source_url": "https://commons.wikimedia.org/wiki/File:Human_skull_front_bones.svg",
        "usage_policy": USAGE_POLICY,
        "usage_warning": USAGE_WARNING_ZH,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "formula_link": {
            "dimension": "3. 面部细节特征",
            "slots": ["眉骨特征", "眼眶开口形状", "颧骨外扩/内收", "鼻骨宽窄", "下颌骨体量"],
            "how_to_use": "对照正面色块明确区分多人脸型骨量：颧骨（Zygomatic）决定面部折叠度与外轮廓宽窄，下颌骨（Mandible）决定方圆/锐角/鹅蛋底盘。",
        },
        "mapped_landmarks": [
            {"landmark": "额骨与眉弓 (Frontal / Brow Ridge)", "slot": "3. 眉骨特征", "related_bones": ["B001", "B013", "B015", "B017"]},
            {"landmark": "颧骨与颧弓 (Zygomatic Arch)", "slot": "3. 下颌线 / 骨相特征", "related_bones": ["B003", "B007", "B014", "B018"]},
            {"landmark": "鼻骨与梨状孔 (Nasal Bone)", "slot": "3. 鼻梁特征", "related_bones": ["B001", "B010", "B013", "B019"]},
            {"landmark": "下颌体与颏隆凸 (Mandible / Chin)", "slot": "3. 下颌线 / 骨相特征", "related_bones": ["B003", "B004", "B019", "B021"]},
        ],
        "linked_bones": ["B001", "B002", "B003", "B004", "B007", "B009", "B014", "B017", "B018", "B021"],
    },
    {
        "id": "A003",
        "name": "侧面颅面分骨色块图（眉弓/鼻骨/颧弓/下颌角转折）",
        "en_name": "Human Skull Lateral — Color-Coded Facial Bones",
        "file": "assets/anatomy/skull_side_bones.svg",
        "view": "侧面分骨解剖",
        "license": "Public domain (LadyofHats)",
        "source_url": "https://commons.wikimedia.org/wiki/File:Human_skull_side_bones.svg",
        "usage_policy": USAGE_POLICY,
        "usage_warning": USAGE_WARNING_ZH,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "formula_link": {
            "dimension": "3. 面部细节特征（3/4 侧颜与侧剪影锁模）",
            "slots": ["眉骨前突深度", "鼻根山根落差", "下颌角折角（100°–135°）", "直面/微凹/微凸颌面型"],
            "how_to_use": "角色设定卡统一采用朝右 3/4 视角时，重点参考此图的眉弓前探量、鼻梁起势角度与下颌角折点位置，避免角色“侧面纸片化”。",
        },
        "mapped_landmarks": [
            {"landmark": "眉间与鼻根转折 (Glabella & Nasion)", "slot": "3. 眉骨特征 + 鼻梁特征", "related_bones": ["B001", "B013", "B015", "B023"]},
            {"landmark": "下颌角折角 (Gonial / Mandibular Angle)", "slot": "3. 下颌线 / 骨相特征", "related_bones": ["B003", "B004", "B017", "B022"]},
            {"landmark": "上颌齿槽与颏部前突度 (Maxilla & Chin)", "slot": "3. 唇形特征 + 下颌线", "related_bones": ["B006", "B008", "B012", "B020"]},
        ],
        "linked_bones": ["B001", "B003", "B004", "B013", "B015", "B017", "B019", "B022", "B023"],
    },
    {
        "id": "A004",
        "name": "正面完整人颅骨结构图（含骨缝与眼眶比例）",
        "en_name": "Human Skull Front Complete Vector",
        "file": "assets/anatomy/skull_front_complete.svg",
        "view": "正面精细骨相",
        "license": "Public domain (LadyofHats)",
        "source_url": "https://commons.wikimedia.org/wiki/File:Human_skull_front_complete.svg",
        "usage_policy": USAGE_POLICY,
        "usage_warning": USAGE_WARNING_ZH,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "formula_link": {
            "dimension": "3. 面部细节特征 & 2. 皮肤质感（光影显骨）",
            "slots": ["眼窝深浅与眶缘形态", "颧下颊窝阴影区", "鼻梁骨软骨交界"],
            "how_to_use": "用于高写实、历史正剧、暗调电影布光时定位正面受光骨点（额结节、眉弓、颧点、鼻尖、颏点）与暗面落影区。",
        },
        "mapped_landmarks": [
            {"landmark": "眶上缘与眶下缘 (Orbital Rim)", "slot": "3. 眼型描述 + 4. 眼神阴影", "related_bones": ["B001", "B014", "B018", "B024"]},
            {"landmark": "犬齿窝与颊凹 (Canine Fossa)", "slot": "2. 肤质纹理 + 3. 骨相特征", "related_bones": ["B005", "B018", "B020", "B024"]},
        ],
        "linked_bones": ["B001", "B005", "B014", "B017", "B018", "B020", "B024"],
    },
    {
        "id": "A005",
        "name": "侧面完整人颅骨结构图（含颞窝/枕骨/颈部承托）",
        "en_name": "Human Skull Side Complete Vector",
        "file": "assets/anatomy/skull_side_complete.svg",
        "view": "侧面精细骨相",
        "license": "Public domain (LadyofHats)",
        "source_url": "https://commons.wikimedia.org/wiki/File:Human_skull_side_complete.svg",
        "usage_policy": USAGE_POLICY,
        "usage_warning": USAGE_WARNING_ZH,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "formula_link": {
            "dimension": "3. 面部细节特征 & 5. 发型轮廓（颅顶与后脑勺饱满度）",
            "slots": ["颅顶高度", "后脑勺枕骨弧度", "颞窝凹陷度", "下颌支长度"],
            "how_to_use": "不仅定侧脸骨相，也决定第 5 维发型的头包脸比例、高颅顶/束冠受力点与后脑勺发髻支撑位置。",
        },
        "mapped_landmarks": [
            {"landmark": "颞窝与颞线 (Temporal Fossa)", "slot": "3. 眉骨与太阳穴轮廓", "related_bones": ["B018", "B020", "B024"]},
            {"landmark": "顶骨与枕骨隆凸 (Parietal & Occipital)", "slot": "5. 发型颅顶与盘发支撑", "related_bones": ["B002", "B016", "B021", "B022"]},
        ],
        "linked_bones": ["B015", "B018", "B019", "B021", "B022", "B024"],
    },
    {
        "id": "A006",
        "name": "侧面颅面解剖细节全标注图（骨性标志点字典）",
        "en_name": "Human Skull Side Details — Labeled Landmarks",
        "file": "assets/anatomy/skull_side_details.svg",
        "view": "侧面骨点术语标注",
        "license": "Public domain (LadyofHats)",
        "source_url": "https://commons.wikimedia.org/wiki/File:Human_skull_side_details.svg",
        "usage_policy": USAGE_POLICY,
        "usage_warning": USAGE_WARNING_ZH,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "formula_link": {
            "dimension": "3. 面部细节特征（英文解剖 Prompt 精准选词）",
            "slots": ["Zygomatic arch", "Superciliary arch", "Nasal spine", "Mandibular angle", "Mental protuberance"],
            "how_to_use": "编写高精度英文生图词或做历史人物防网红脸校验时，直接查阅骨性标志点的准确解剖英文术语。",
        },
        "mapped_landmarks": [
            {"landmark": "Superciliary Arch / Glabella", "slot": "brow_ridge", "related_bones": ["B001", "B013", "B017", "B019"]},
            {"landmark": "Zygomatic Arch / Malar Bone", "slot": "jawline_bone", "related_bones": ["B003", "B014", "B018", "B022"]},
            {"landmark": "Mental Protuberance & Mandibular Angle", "slot": "jawline_bone", "related_bones": ["B004", "B017", "B019", "B021"]},
        ],
        "linked_bones": ["B001", "B004", "B013", "B017", "B018", "B019", "B020"],
    },
    {
        "id": "A007",
        "name": "写实侧面头骨体积光影医学插画（Patrick J. Lynch）",
        "en_name": "Realistic Lateral Skull Medical Illustration",
        "file": "assets/anatomy/skull_lateral_view.jpg",
        "view": "写实明暗体积解剖",
        "license": "CC BY 2.5 (Patrick J. Lynch)",
        "source_url": "https://commons.wikimedia.org/wiki/File:Human_skull_lateral_view.jpg",
        "usage_policy": USAGE_POLICY,
        "usage_warning": USAGE_WARNING_ZH,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "formula_link": {
            "dimension": "2. 肤质纹理 & 3. 历史写实沧桑骨相（B017–B024 专用）",
            "slots": ["骨性体积明暗交界", "颧弓与下颌支粗壮度", "眼眶与鼻骨真实凹凸"],
            "how_to_use": "专为历史正剧、乱世武将、谋士与老者角色提供真实体积与光影参照，彻底杜绝“平面贴图感”和“现代医美磨皮脸”。",
        },
        "mapped_landmarks": [
            {"landmark": "明暗交界与侧光显骨", "slot": "2. 皮肤质感 + 3. 骨相特征", "related_bones": ["B017", "B018", "B019", "B020", "B021", "B022", "B023", "B024"]},
        ],
        "linked_bones": ["B017", "B018", "B019", "B020", "B021", "B022", "B023", "B024"],
    },
]

EXTERNAL_3D_2D_SOURCES = [
    {
        "group": "3D 头骨块面与比例旋转参考（Sketchfab）",
        "keywords": ["Asaro Head / Planes of the Head", "Reilly Head / Loomis Head 3D", "Visual Art Anatomy (VAA): Base Head Planes With Skull & Bony Landmarks"],
        "purpose": "定 3/4 侧颜光影切面、三庭五眼节律与骨-肌-皮三层对照",
    },
    {
        "group": "面部解剖雕刻与肖像结构包（ArtStation Marketplace）",
        "keywords": ["Head Planes Reference Pack", "Facial Anatomy Sculpt Pack", "Portrait Construction Pack"],
        "purpose": "区分不同年龄、性别、种族的真实骨量与软组织走向",
    },
    {
        "group": "2D 发型三视图与动画设定集（ArtStation / Gumroad / Settei）",
        "keywords": ["Stylized Hair Asset Pack", "Anime Hair Turnaround Sheet", "Ancient Chinese Hairstyle Reference", "Sakugabooru / Settei character sheet"],
        "purpose": "参考发丝分组、前后左右轮廓剪影与古代发髻结构",
    },
]
