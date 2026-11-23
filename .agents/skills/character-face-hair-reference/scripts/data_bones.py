# -*- coding: utf-8 -*-
"""24 Bone Structure Reference Entries (B001-B024).
Policy: reference_only_do_not_use_directly.
"""

from data_anatomy import USAGE_POLICY, USAGE_WARNING_ZH, USAGE_WARNING_EN


def _bone(
    bid, group, name, en_name, aesthetic_tier,
    proportions, landmarks, formula_slots,
    prompt_en_ref, prompt_zh_ref, suitable_roles,
    anatomy_refs, avoid_note, svg_params
):
    return {
        "id": bid,
        "type": "bone_structure",
        "group": group,
        "name": name,
        "en_name": en_name,
        "aesthetic_tier": aesthetic_tier,
        "usage_policy": USAGE_POLICY,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "formula_dimension": "3. 面部细节特征",
        "proportions": proportions,
        "anatomical_landmarks": landmarks,
        "formula_slots_ref_only": formula_slots,
        "prompt_en_ref_only": prompt_en_ref,
        "prompt_zh_ref_only": prompt_zh_ref,
        "suitable_roles": suitable_roles,
        "linked_anatomy_plates": anatomy_refs,
        "anti_copy_note": avoid_note,
        "svg_file": f"assets/bones/{bid}.svg",
        "svg_params": svg_params,
    }


BONES = [
    _bone(
        "B001", "基础骨相底盘", "高立体深邃骨相", "High-Relief Deep-Set Bone Structure", "常规/美型/奇幻",
        {"upper": 0.32, "mid": 0.35, "lower": 0.33, "jaw_angle_deg": 116, "profile": "高立体深折"},
        ["高突眉弓 (Prominent brow ridge)", "深陷眼窝 (Deep orbital rim)", "高直鼻骨 (High straight nasal bridge)", "利落下颌折角"],
        {"eye": "深邃窄双或欧式大双，眼窝阴影清晰", "brow": "眉弓高挑立体、眉眼距偏近", "nose": "山根起势高、鼻梁笔直如刻", "lips": "唇峰清晰、薄厚适中", "jaw": "颧下微收、下颌线锐利分明"},
        "defined facial bone structure, prominent brow ridge, deep-set eyes, high straight nose bridge, sharp sculpted jawline",
        "高眉弓深眼窝，山根高挺笔直，颧颌转折立体分明，下颌线利落清晰",
        ["暗黑奇幻主角", "冷艳御姐", "硬朗贵族", "高定视觉人物"],
        ["A002", "A003", "A004", "A006"],
        "仅借鉴立体度与眉鼻落差，须结合角色种族与年龄重定肤质、眉形与眼神。",
        {"face_w": 52, "cheek_w": 55, "jaw_w": 39, "chin_y": 146, "chin_w": 13, "brow_depth": 9, "mid_ratio": 0.35, "accent": "#6366f1", "tag": "高立体"}
    ),
    _bone(
        "B002", "基础骨相底盘", "东方流畅柔和骨相", "Smooth East Asian Oval Bone Structure", "常规/美型/古典",
        {"upper": 0.33, "mid": 0.34, "lower": 0.33, "jaw_angle_deg": 126, "profile": "温润直面"},
        ["内收颧弓 (Tucked zygomatic arch)", "平顺面中 (Smooth mid-face)", "柔和鹅蛋下颌 (Soft oval mandible)"],
        {"eye": "线条温润的杏眼或细长桃花眼", "brow": "眉骨平缓过渡、无强烈压迫阴影", "nose": "鼻梁秀气挺直、鼻头圆润精致", "lips": "唇珠饱满、唇角微扬柔和", "jaw": "下颌角圆润隐约、轮廓如玉流畅"},
        "smooth East Asian facial contours, gentle balanced bone structure, tucked cheekbones, soft oval jawline, delicate proportions",
        "面中平整温润，颧弓柔和内收，下颌转折圆润流畅，三庭五眼均衡舒展",
        ["国风古典美人", "温婉女主", "清雅文人", "日系治愈角色"],
        ["A001", "A002", "A005"],
        "切忌套成千篇一律磨皮网红瓜子脸，需搭配真实肌理与独特眼型神态。",
        {"face_w": 50, "cheek_w": 51, "jaw_w": 38, "chin_y": 145, "chin_w": 16, "brow_depth": 4, "mid_ratio": 0.34, "accent": "#10b981", "tag": "柔和直面"}
    ),
    _bone(
        "B003", "基础骨相底盘", "方圆大气量感骨相", "Dignified Square-Round Bone Structure", "常规/正剧/大女主",
        {"upper": 0.32, "mid": 0.34, "lower": 0.34, "jaw_angle_deg": 112, "profile": "端正稳重"},
        ["饱满颧骨撑肉 (Broad supportive malar)", "清晰下颌角 110°–120°", "敦实稳重下庭"],
        {"eye": "眉眼舒展端方、眼裂开阔有定力", "brow": "眉骨平直大气、眉间开阔", "nose": "鼻梁端正挺拔、鼻翼规整", "lips": "唇形端庄饱满、闭合沉稳", "jaw": "方圆下颌角有骨量支撑、抗老大气"},
        "well-defined mandibular angle around 115 degrees, balanced square-round face, broad supportive cheekbones, dignified bone structure",
        "颧骨宽阔撑肉，下颌角清晰约 115° 呈方圆大气轮廓，整体骨量稳重端庄",
        ["正剧大女主", "皇后/贵妃", "稳重精英", "中青年中坚主角"],
        ["A002", "A003", "A006"],
        "保持下颌角的真实支撑感，不要为了“显瘦”削成尖下巴。",
        {"face_w": 55, "cheek_w": 57, "jaw_w": 48, "chin_y": 144, "chin_w": 22, "brow_depth": 6, "mid_ratio": 0.34, "accent": "#d97706", "tag": "方圆大气"}
    ),
    _bone(
        "B004", "基础骨相底盘", "直角棱角硬朗骨相", "Chiseled Angular Masculine Bone Structure", "常规/硬汉/动作",
        {"upper": 0.31, "mid": 0.34, "lower": 0.35, "jaw_angle_deg": 102, "profile": "强块面直面"},
        ["强直角下颌 (Strong squared mandible)", "方正颏隆凸 (Squared mental protuberance)", "粗壮颧弓切面"],
        {"eye": "狭长刚毅眼型、上睑紧实", "brow": "粗厚平直眉骨、略微前探压眼", "nose": "鼻骨宽直硬朗、棱线分明", "lips": "薄而紧实的唇线、嘴角平直", "jaw": "下颌角近直角折转、块面硬朗"},
        "angular masculine jawline, squared chin, strong zygomatic arch, chiseled facial planes, rugged bone mass",
        "方正下巴与硬朗直角下颌线，颧弓切面清晰，面部骨骼块面感强烈",
        ["动作片硬汉", "现代军官", "机甲驾驶员", "正派刚毅男主"],
        ["A002", "A003", "A006"],
        "仅参考硬朗块面逻辑，按年代与身份调整胡茬、皮肤粗细与眉眼情绪。",
        {"face_w": 56, "cheek_w": 58, "jaw_w": 51, "chin_y": 146, "chin_w": 25, "brow_depth": 8, "mid_ratio": 0.34, "accent": "#2563eb", "tag": "棱角硬朗"}
    ),
    _bone(
        "B005", "基础骨相底盘", "窄长清冷骨相", "Slender Elongated Aloof Bone Structure", "常规/美型/智性",
        {"upper": 0.31, "mid": 0.38, "lower": 0.31, "jaw_angle_deg": 124, "profile": "清瘦微直"},
        ["略长中庭 (Slightly elongated mid-face)", "轻盈颊凹 (Subtle canine fossa)", "窄收下颌支"],
        {"eye": "细长单眼皮或窄扇双、眼尾平展疏离", "brow": "眉骨清秀微起、眉形平长", "nose": "细长高直鼻梁、鼻翼收窄", "lips": "薄唇色淡、唇珠秀气克制", "jaw": "两颊微收无多余软肉、窄长利落"},
        "slender elongated face, slightly long mid-face, subtle hollow cheeks, minimalist low-contrast features, aloof elegance",
        "脸型偏窄长，中庭略拉长显清冷克制，两颊微收，五官留白高雅疏离",
        ["高智感谋士", "清冷师尊", "禁欲系贵公子", "冷面调查员"],
        ["A001", "A004"],
        "中庭不可过度拉长失真，需通过眼神克制与淡颜留白塑造清冷感。",
        {"face_w": 45, "cheek_w": 46, "jaw_w": 33, "chin_y": 152, "chin_w": 13, "brow_depth": 5, "mid_ratio": 0.38, "accent": "#0ea5e9", "tag": "窄长清冷"}
    ),
    _bone(
        "B006", "基础骨相底盘", "幼态短中庭骨相", "Youthful Short Mid-Face Bone Structure", "常规/少年少女",
        {"upper": 0.37, "mid": 0.30, "lower": 0.33, "jaw_angle_deg": 130, "profile": "饱满微凸"},
        ["饱满高额结节 (Full frontal bossing)", "偏短中庭 (Short mid-face)", "圆润短小下巴"],
        {"eye": "圆润大眼、眼位略靠中下", "brow": "眉骨平缓、眉眼间距舒展", "nose": "小巧微翘鼻梁、鼻头柔和圆润", "lips": "短人中、饱满微翘小嘴", "jaw": "下颌线圆润柔和、双颊带自然胶原感"},
        "youthful short mid-face, full rounded forehead, soft small chin, gentle rounded jawline, innocent natural proportions",
        "上庭饱满开阔，中下庭偏短显幼态朝气，下巴圆巧柔和，颊部有自然饱满感",
        ["元气少年少女", "童真主角", "软萌配角", "青涩学徒"],
        ["A001", "A003"],
        "成年角色慎用过度幼态比例，避免比例失调或低龄化违和。",
        {"face_w": 52, "cheek_w": 54, "jaw_w": 40, "chin_y": 138, "chin_w": 17, "brow_depth": 3, "mid_ratio": 0.30, "accent": "#f43f5e", "tag": "短中庭幼态"}
    ),
    _bone(
        "B007", "基础骨相底盘", "心形倒三角锐利骨相", "Heart-Shaped Tapered Bone Structure", "美型/奇幻/动漫",
        {"upper": 0.35, "mid": 0.33, "lower": 0.32, "jaw_angle_deg": 128, "profile": "灵动收尖"},
        ["宽润额骨与高点颧骨", "快速内收下颌体", "小巧精巧尖颏"],
        {"eye": "灵动上扬眼型、眼瞳明亮聚焦", "brow": "微挑细眉骨、灵气鲜明", "nose": "精巧细挺小翘鼻", "lips": "唇角灵巧微翘、立体唇珠", "jaw": "颧部饱满而下颌快速收窄至精巧尖下巴"},
        "heart-shaped face, slightly wider forehead and high cheek fullness, sharply tapered delicate chin, agile lively contours",
        "额与高颧处微宽饱满，下颌线迅速利落收尖，整体呈灵动精巧的心形倒三角",
        ["灵动精灵", "机敏大小姐", "傲娇女配", "敏锐刺客少年"],
        ["A001", "A002"],
        "防止做成畸形蛇精尖下巴，尖收幅度必须保留真实骨肉过渡。",
        {"face_w": 54, "cheek_w": 56, "jaw_w": 33, "chin_y": 146, "chin_w": 10, "brow_depth": 5, "mid_ratio": 0.33, "accent": "#ec4899", "tag": "心形灵动"}
    ),
    _bone(
        "B008", "基础骨相底盘", "宽眼距留白高级脸", "Wide-Set Editorial High-Fashion Bone Structure", "电影/文艺/时尚",
        {"upper": 0.33, "mid": 0.34, "lower": 0.33, "jaw_angle_deg": 118, "profile": "舒展松弛"},
        ["略宽眼距 (>1 眼宽)", "平直微宽鼻骨", "高颧骨微外扩留白"],
        {"eye": "眼距略宽显神秘空灵，眼神慵懒松弛", "brow": "平直自然野生眉骨", "nose": "鼻梁平直略宽、自带故事感", "lips": "厚润松弛唇形、微张带呼吸感", "jaw": "颧骨与下颌有清晰转折与高级留白"},
        "wide-set eyes with generous facial negative space, high sculpted cheekbones, relaxed full lips, editorial cinema bone structure",
        "眼距略宽留白充裕，颧骨微高有电影切面感，唇形丰润松弛，极具银幕文艺辨识度",
        ["文艺电影女主", "神秘异域旅人", "国际时尚角色", "疏离感主角"],
        ["A001", "A003"],
        "高级脸核心在留白与松弛神态，切忌叠加俗艳浓妆与尖锐美颜滤镜。",
        {"face_w": 53, "cheek_w": 57, "jaw_w": 42, "chin_y": 144, "chin_w": 17, "brow_depth": 6, "mid_ratio": 0.34, "accent": "#8b5cf6", "tag": "留白电影脸"}
    ),
]


BONES.extend([
    _bone(
        "B009", "动物系防同脸美型", "猫系冷艳娇俏骨相", "Cat-Like Aloof & Agile Bone Structure", "常规/美型/奇幻",
        {"upper": 0.34, "mid": 0.32, "lower": 0.34, "jaw_angle_deg": 124, "profile": "紧致微翘"},
        ["微上挑眼眶外缘", "尖锐内眼角与短人中", "紧致小巧 V 颌"],
        {"eye": "微上挑猫眼/桃花眼，尖内眼角带疏离感", "brow": "轻扬细挺眉弓", "nose": "小巧精致略带微翘感鼻尖", "lips": "短人中、薄翘花瓣唇", "jaw": "下颌紧致小巧，侧颜线条利落"},
        "cat-like upturned eyes, sharp inner canthus, short philtrum, taut delicate V-line jaw, aloof mysterious elegance",
        "微上挑猫眼配尖内眼角，人中偏短，鼻尖精巧微翘，下颌紧致清冷难驯",
        ["傲娇女主", "神秘暗夜侠女", "高冷大小姐", "灵动妖猫人设"],
        ["A002"],
        "保持眼神冷傲与短人中的平衡，避免流于二次元模板化尖脸。",
        {"face_w": 50, "cheek_w": 53, "jaw_w": 34, "chin_y": 141, "chin_w": 12, "brow_depth": 5, "mid_ratio": 0.32, "accent": "#a855f7", "tag": "猫系冷艳"}
    ),
    _bone(
        "B010", "动物系防同脸美型", "狐系狭长魅惑骨相", "Fox-Like Narrow & Cunning Bone Structure", "常规/美型/奇幻",
        {"upper": 0.31, "mid": 0.36, "lower": 0.33, "jaw_angle_deg": 126, "profile": "纤细锋利"},
        ["狭长上扬眼裂", "细直水滴鼻骨", "窄眼距与瘦尖下巴"],
        {"eye": "眼裂极狭长上挑，眼尾细长勾勒", "brow": "修长上挑眉骨", "nose": "细直高挺水滴鼻，鼻翼精窄", "lips": "薄而弧度狡黠的微笑唇", "jaw": "瘦削流畅下颌收至纤巧尖颏"},
        "fox-like narrow elongated upturned eyes, slender straight nose, narrow interocular spacing, lean pointed chin, cunning grace",
        "狭长上扬狐眼与偏窄眼距，鼻梁细直精巧，下颌瘦削尖收，自带城府与魅惑",
        ["智谋权臣/女谋士", "魅惑反派", "仙侠狐族", "心思深沉美型角色"],
        ["A001", "A002"],
        "切勿做成夸张倒三角锥子脸，用细长眼裂和鼻梁精窄感表达狐系神韵。",
        {"face_w": 47, "cheek_w": 49, "jaw_w": 31, "chin_y": 148, "chin_w": 10, "brow_depth": 6, "mid_ratio": 0.36, "accent": "#f97316", "tag": "狐系智魅"}
    ),
    _bone(
        "B011", "动物系防同脸美型", "鹿系清澈温润骨相", "Fawn-Like Clear & Gentle Bone Structure", "常规/美型/治愈",
        {"upper": 0.35, "mid": 0.32, "lower": 0.33, "jaw_angle_deg": 128, "profile": "柔润微圆"},
        ["开阔圆润眼眶", "轻盈小巧圆钝鼻头", "柔和饱满面颊骨肉"],
        {"eye": "大而圆润清澈鹿眼，眼尾轻微下垂显温柔", "brow": "自然柔和弯眉骨", "nose": "鼻梁秀挺、鼻头柔和略圆", "lips": "丰润自然樱桃唇", "jaw": "下颌轮廓柔和圆润、毫无攻击性"},
        "fawn-like large luminous round eyes, gently downturned outer corners, soft rounded nose tip, gentle oval cheeks, warm purity",
        "眼眶开阔圆润，眼神清澈无害，鼻头柔润，面颊柔和流畅，富有亲和治愈感",
        ["纯善主角", "忠犬/守护者", "森林精灵", "清澈治愈系少年少女"],
        ["A001"],
        "保留骨相支点与清晰下颌边缘，避免过度圆润显得模糊失真。",
        {"face_w": 50, "cheek_w": 51, "jaw_w": 37, "chin_y": 142, "chin_w": 15, "brow_depth": 3, "mid_ratio": 0.32, "accent": "#14b8a6", "tag": "鹿系清澈"}
    ),
    _bone(
        "B012", "动物系防同脸美型", "兔系柔萌娇憨骨相", "Rabbit-Like Soft & Innocent Bone Structure", "常规/美型/甜暖",
        {"upper": 0.36, "mid": 0.31, "lower": 0.33, "jaw_angle_deg": 131, "profile": "软糯饱满"},
        ["圆饱满额颊软组织", "短小人中与微翘唇珠", "轻巧小下颌"],
        {"eye": "亮泽杏圆眼、微带卧蚕显无辜", "brow": "浅淡平缓短眉骨", "nose": "小巧柔圆翘鼻", "lips": "短人中配微凸饱满唇珠，似微微轻嘟", "jaw": "柔软圆弧下颌线，面颊自带轻盈婴儿肥"},
        "rabbit-like delicate soft features, short mid-face, bright wide innocent eyes, subtly pouty full lips, soft rounded cheeks",
        "短中庭与饱满额颊，杏圆亮眼配短人中微翘唇珠，线条软糯亲切无攻击性",
        ["软萌小师妹", "纯情配角", "古灵精怪幼年角色", "甜暖日常女主"],
        ["A001", "A003"],
        "仅作柔美娇憨维度的词汇参考，严禁与写实历史正剧角色混用。",
        {"face_w": 51, "cheek_w": 53, "jaw_w": 39, "chin_y": 139, "chin_w": 16, "brow_depth": 3, "mid_ratio": 0.31, "accent": "#fb7185", "tag": "兔系软萌"}
    ),
    _bone(
        "B013", "动物系防同脸美型", "鹰系锐利威严骨相", "Hawk-Like Aquiline & Dominant Bone Structure", "美型/权谋/史诗",
        {"upper": 0.31, "mid": 0.36, "lower": 0.33, "jaw_angle_deg": 108, "profile": "鹰钩峻峭"},
        ["低位厚重压眼眉弓", "微驼峰高挺鹰鼻骨", "瘦削锋利下颌折角"],
        {"eye": "深陷狭长锐眼，目光自带俯视审视感", "brow": "眉弓低且厚重前探，眉眼距极近", "nose": "高挺带微驼峰骨节的鹰鼻", "lips": "薄而紧抿的理性唇线", "jaw": "瘦削劲挺、下颌线如刃般干脆"},
        "hawk-like aquiline nose bridge with subtle dorsal bump, heavy low-set brow ridge, piercing focused eyes, lean razor-sharp jaw",
        "厚重低位眉弓压眼，高挺微驼峰鼻骨，瘦削锋利下颌线，尽显鹰视狼顾之威严",
        ["上位掌权者", "严苛统帅", "冷峻导师", "暗黑奇幻君王"],
        ["A002", "A003", "A006"],
        "驼峰鼻节度需自然克制，避免卡通化过度弯曲。",
        {"face_w": 50, "cheek_w": 53, "jaw_w": 42, "chin_y": 148, "chin_w": 15, "brow_depth": 10, "mid_ratio": 0.36, "accent": "#b45309", "tag": "鹰系峻峭"}
    ),
    _bone(
        "B014", "动物系防同脸美型", "蛇系冷冽骨感骨相", "Serpentine High-Cheekbone Cold Structure", "美型/暗黑/反派",
        {"upper": 0.31, "mid": 0.36, "lower": 0.33, "jaw_angle_deg": 120, "profile": "低脂骨感"},
        ["极细长冷眼眶", "高瘦外扩颧骨", "低体脂削瘦薄颌"],
        {"eye": "极细长冷漠眼裂，微遮瞳显危险感", "brow": "高细冷锐眉骨", "nose": "细直高峻、鼻翼极收紧", "lips": "极薄紧绷的冷艳唇线", "jaw": "高颧骨配极低体脂削瘦下颌线"},
        "serpentine narrow cold eyes, high lean prominent cheekbones, thin tight lips, low-body-fat sculpted sharp jawline, dangerous elegance",
        "极细长冷眼配高瘦颧骨，低体脂骨感强烈，薄唇紧敛，自带优雅而危险的气场",
        ["阴鸷反派", "冷血刺客", "毒舌贵族", "暗黑异族角色"],
        ["A002", "A004"],
        "用颧骨与冷眼比例建立辨识度，避免过度病态消瘦或尖锐失真。",
        {"face_w": 47, "cheek_w": 53, "jaw_w": 32, "chin_y": 149, "chin_w": 11, "brow_depth": 7, "mid_ratio": 0.36, "accent": "#059669", "tag": "蛇系冷锐"}
    ),
    _bone(
        "B015", "动物系防同脸美型", "狼系野性俊朗骨相", "Wolf-Like Wild & Untamed Bone Structure", "美型/少年/武侠",
        {"upper": 0.32, "mid": 0.34, "lower": 0.34, "jaw_angle_deg": 110, "profile": "俊朗野性"},
        ["前探锐利眉弓", "直挺带骨节鼻梁", "紧实利落收紧下颌"],
        {"eye": "眼尾微挑的俊朗狼眼，下三白或强聚焦感", "brow": "斜飞硬朗浓眉骨", "nose": "鼻骨直挺有力、鼻头棱角分明", "lips": "线条清晰不羁的薄中唇", "jaw": "下颌角分明而下巴精干收束，野性利落"},
        "wolf-like sharp untamed features, angled intense eyes, strong straight nasal bridge, taut angular jawline, wild heroic charisma",
        "斜飞眉骨配锐利狼眼，鼻骨直挺有力，下颌角清晰精干，兼具野性攻击力与少年俊朗",
        ["江湖浪子", "少年战神", "反叛骑士", "野性不羁男主"],
        ["A002", "A003", "A005"],
        "狼系与鹰系的区别在于更年轻的骨肉紧实度与少年不羁感。",
        {"face_w": 51, "cheek_w": 54, "jaw_w": 43, "chin_y": 147, "chin_w": 16, "brow_depth": 8, "mid_ratio": 0.34, "accent": "#4f46e5", "tag": "狼系野性"}
    ),
    _bone(
        "B016", "动物系防同脸美型", "鹤系清逸仙姿骨相", "Crane-Like Ethereal & Noble Bone Structure", "美型/仙侠/国风",
        {"upper": 0.32, "mid": 0.36, "lower": 0.32, "jaw_angle_deg": 125, "profile": "修长舒朗"},
        ["修长天庭与高颅顶", "淡雅平长眉眼", "清峻微直仙骨下颌"],
        {"eye": "清长丹凤眼或静谧细杏眼，留白澄澈", "brow": "淡远平展眉骨，气韵舒朗", "nose": "鼻梁修直如玉箸、收尾清雅", "lips": "淡色薄润秀唇，神情超然", "jaw": "修长颈线承托清瘦流畅下颌，仙风鹤骨"},
        "crane-like ethereal slender bone structure, high cranial crown, serene elongated eyes, refined straight nose, graceful poised jawline",
        "高颅顶配清瘦修长骨相，眉眼舒朗淡雅，鼻若玉箸，整体散发出超然脱俗的鹤骨仙姿",
        ["仙侠师尊/仙君", "隐世琴师", "魏晋风流名士", "清高神明"],
        ["A001", "A005"],
        "必须配合修长脖颈、宽肩削体态与留白布光，忌油腻浓眉大眼。",
        {"face_w": 46, "cheek_w": 47, "jaw_w": 34, "chin_y": 150, "chin_w": 13, "brow_depth": 4, "mid_ratio": 0.36, "accent": "#0284c7", "tag": "鹤系清逸"}
    ),
])


BONES.extend([
    _bone(
        "B017", "历史写实与沧桑骨相（禁网红脸）", "乱世枭雄厚重骨相", "Historical Warlord Heavy Bone Structure", "历史写实（严禁网红滤镜）",
        {"upper": 0.30, "mid": 0.35, "lower": 0.35, "jaw_angle_deg": 106, "profile": "厚重威压"},
        ["厚重压眼眉弓 (Heavy superciliary arch)", "宽实坚硬下颌体 (Broad firm mandible)", "深刻法令纹与川字眉间纹"],
        {"eye": "深邃精明细长眼，眼袋与鱼尾纹自然可见", "brow": "厚重低垂眉骨，眉心带常年思虑竖纹", "nose": "鼻骨宽挺有力、鼻翼厚实", "lips": "薄而紧抿的威严唇线，嘴角下压", "jaw": "宽大厚实下颌与清晰咬肌，带真实风霜纹理"},
        "heavy brow ridge, deep-set piercing eyes, broad firm mandible, thin tight-lipped mouth, weathered skin with nasolabial folds, unpolished historical realism",
        "厚重眉弓压目，宽实下颌骨量雄浑，法令纹与眉间纹深刻，皮肤粗粝无磨皮，尽显枭雄威压",
        ["乱世枭雄", "权谋霸主", "中年开国统帅", "铁血宰执"],
        ["A001", "A002", "A003", "A004", "A006", "A007"],
        "严格禁止网红光滑磨皮与偶像化眉眼，必须保留真实年龄皱纹、毛孔与厚重下颌骨。",
        {"face_w": 57, "cheek_w": 59, "jaw_w": 52, "chin_y": 146, "chin_w": 24, "brow_depth": 11, "mid_ratio": 0.35, "accent": "#78350f", "tag": "历史·枭雄"}
    ),
    _bone(
        "B018", "历史写实与沧桑骨相（禁网红脸）", "清瘦谋士高颧骨相", "Historical Strategist Lean Zygomatic Structure", "历史写实（严禁网红滤镜）",
        {"upper": 0.31, "mid": 0.38, "lower": 0.31, "jaw_angle_deg": 118, "profile": "清癯显骨"},
        ["微陷颞窝/太阳穴 (Hollow temporal fossa)", "清晰高颧弓 (Prominent zygomatic arch)", "微凹颊窝与抬头纹"],
        {"eye": "深沉内敛细长眼，眼窝微陷显操劳思虑", "brow": "清瘦微蹙眉骨，额间带真实细抬头纹", "nose": "瘦挺高直鼻骨，骨节隐约可见", "lips": "薄唇微抿，唇纹真实克制", "jaw": "两颊微凹、颧弓与下颌骨线分明，清癯嶙峋"},
        "lean angular East Asian face, hollow temples, prominent zygomatic arches, slightly sunken cheeks, subtle forehead thinking lines, calculating deep-set eyes, historical realism",
        "太阳穴微陷、颧弓清晰显骨，两颊微凹带思虑抬头纹，眼神深沉，呈真实历史清癯谋士相",
        ["历史军师/谋士", "清流谏臣", "苦读老学者", "隐忍幕僚"],
        ["A001", "A002", "A004", "A005", "A006", "A007"],
        "切忌套成现代美男清冷脸，必须加入真实劳碌眼袋、肤色微黄与颞窝/颊窝骨感。",
        {"face_w": 46, "cheek_w": 52, "jaw_w": 36, "chin_y": 150, "chin_w": 15, "brow_depth": 7, "mid_ratio": 0.38, "accent": "#475569", "tag": "历史·谋士"}
    ),
    _bone(
        "B019", "历史写实与沧桑骨相（禁网红脸）", "久战武将粗粝骨相", "Battle-Hardened Historical General Bone Structure", "历史写实（严禁网红滤镜）",
        {"upper": 0.30, "mid": 0.34, "lower": 0.36, "jaw_angle_deg": 100, "profile": "宽厚粗犷"},
        ["粗壮宽厚下颌与粗颈", "微断/硬朗驼峰鼻骨", "宽额眉骨与陈年风霜伤痕"],
        {"eye": "久经沙场的锐利虎目，外眼角深刻日晒笑纹/风霜纹", "brow": "粗硬前凸眉骨，局部可带旧伤断眉细节", "nose": "宽厚粗犷鼻骨，略带微凸骨节", "lips": "厚实粗粝唇形，周边带硬茬络腮胡", "jaw": "宽大雄厚方颌配粗壮颈部，风吹日晒粗糙肤质"},
        "massive broad jaw, thick muscular neck, rugged slightly crooked nose bridge, sun-weathered rough skin texture, coarse stubble beard, battle-hardened historical anatomy",
        "宽厚方颌配粗颈，鼻骨粗硬带骨节，日晒粗粝肤质与硬朗胡茬，充满久战沙场的真实厚重感",
        ["边关大将", "先锋猛将", "乱世豪侠", "中年铁骑统领"],
        ["A001", "A002", "A003", "A005", "A006", "A007"],
        "严禁白净纤细小生脸，需确保颈围、颌骨宽度与日晒风霜皮质高度统一。",
        {"face_w": 59, "cheek_w": 61, "jaw_w": 55, "chin_y": 146, "chin_w": 27, "brow_depth": 10, "mid_ratio": 0.34, "accent": "#991b1b", "tag": "历史·武将"}
    ),
    _bone(
        "B020", "历史写实与沧桑骨相（禁网红脸）", "沧桑市井与老者骨相", "Weathered Elder & Commoner Cranial Structure", "历史写实（严禁网红滤镜）",
        {"upper": 0.32, "mid": 0.35, "lower": 0.33, "jaw_angle_deg": 115, "profile": "松弛显骨"},
        ["可见颞骨与颧骨起伏", "深刻鱼尾纹/木偶纹/眼袋", "自然肤色不均与老年斑"],
        {"eye": "眼皮微垂带深刻鱼尾纹与岁月浑浊光泽", "brow": "眉骨清晰而眉梢花白稀疏", "nose": "鼻翼略宽且皮肤毛孔粗大真实", "lips": "唇周放射状细纹、略显干涩厚实", "jaw": "下颌软组织自然微垂，仍见清晰骨骼起伏"},
        "visible cranial and zygomatic bone structure, deep crow's feet and expression lines, natural coarse skin pores, uneven sun-weathered complexion, documentary historical elder realism",
        "可见清晰颞骨与颧骨起伏，眼周鱼尾纹与唇周岁月纹深刻，毛孔与肤色不均真实自然",
        ["历史老臣", "市井老匠人", "乡村长者", "饱经沧桑平民"],
        ["A003", "A004", "A005", "A006", "A007"],
        "不要仅仅“把年轻脸加几条白线皱纹”，必须体现眼皮微垂、颞凹、耳垂与颌下组织衰老。",
        {"face_w": 52, "cheek_w": 55, "jaw_w": 44, "chin_y": 146, "chin_w": 20, "brow_depth": 7, "mid_ratio": 0.35, "accent": "#713f12", "tag": "历史·老者"}
    ),
    _bone(
        "B021", "历史写实与沧桑骨相（禁网红脸）", "威严帝王方颐骨相", "Imperial Sovereign Broad-Mandible Structure", "历史写实（严禁网红滤镜）",
        {"upper": 0.32, "mid": 0.34, "lower": 0.34, "jaw_angle_deg": 110, "profile": "饱满天庭地阁"},
        ["开阔天庭与饱满枕顶", "端方隆准鼻骨", "宽阔厚重地阁方颐"],
        {"eye": "不怒自威的深沉凤目或端庄长眼，目光沉稳难测", "brow": "舒展雄浑眉弓，印堂宽阔平正", "nose": "鼻梁高广端正、准头丰隆", "lips": "唇形厚实端方、闭合有仪态威势", "jaw": "地阁方圆饱满，下颌稳重厚实具帝王气象"},
        "broad dignified imperial forehead, straight prominent nose, full firm square-round mandible, composed piercing gaze, mature weathered royal realism without beautification",
        "天庭开阔、印堂平正，准头丰隆，地阁方颐厚重稳健，带有不怒自威的真实史书帝王气象",
        ["历史开国皇帝", "盛世君主", "一代名相", "宗室重臣"],
        ["A001", "A002", "A005", "A006", "A007"],
        "避免偶像古装剧里白嫩奶油皇帝形象，保留中年成熟体量、威仪肌理与历史厚重感。",
        {"face_w": 56, "cheek_w": 58, "jaw_w": 50, "chin_y": 147, "chin_w": 24, "brow_depth": 8, "mid_ratio": 0.34, "accent": "#a16207", "tag": "历史·帝王"}
    ),
    _bone(
        "B022", "历史写实与沧桑骨相（禁网红脸）", "边塞飒爽历史女将骨相", "Historical Frontier Female Warrior Bone Structure", "历史写实（严禁网红滤镜）",
        {"upper": 0.32, "mid": 0.34, "lower": 0.34, "jaw_angle_deg": 114, "profile": "英气劲瘦"},
        ["英挺平直眉骨", "高颧骨紧实面颊", "清晰干练下颌转折"],
        {"eye": "狭长炯炯英气眼，眼神果决坚毅，无浓妆眼线", "brow": "自然英挺剑眉骨，眉宇利落", "nose": "挺拔直鼻、线条硬朗干净", "lips": "自然微干健康唇色，闭合果断", "jaw": "下颌折角清晰约 114°，颧骨有力，带微晒高原红/风霜肌理"},
        "resolute historical East Asian female warrior face, clear 114-degree jawline, firm cheekbones, natural wind-chapped sun-kissed skin texture, zero modern makeup or beauty filter",
        "眉骨英挺、颧颌折角干脆利落，面颊紧实带边塞风沙晒痕与真实毛孔，彻底杜绝网红妆感",
        ["历史巾帼女将", "边塞女斥候", "乱世女族长", "写实侠女"],
        ["A003", "A005", "A007"],
        "严禁画现代韩式半永久眉、假睫毛、红唇咬唇妆与磨皮白皮肤。",
        {"face_w": 51, "cheek_w": 54, "jaw_w": 43, "chin_y": 145, "chin_w": 17, "brow_depth": 7, "mid_ratio": 0.34, "accent": "#be123c", "tag": "历史·女将"}
    ),
    _bone(
        "B023", "历史写实与沧桑骨相（禁网红脸）", "丝路胡商深目高鼻骨相", "Silk Road Central-Asian High-Bridge Structure", "历史写实（严禁网红滤镜）",
        {"upper": 0.31, "mid": 0.36, "lower": 0.33, "jaw_angle_deg": 112, "profile": "深目高鼻"},
        ["明显眉间鼻根落差 (Deep nasion)", "高耸微弯鼻骨", "深邃眼眶与立体颧骨"],
        {"eye": "深陷有神大眼眶，眼窝投影浓郁", "brow": "粗浓高耸眉弓，前探落差大", "nose": "山根极高、鼻梁长挺略带西域微弯骨感", "lips": "轮廓清晰自然唇形，常配修整胡须", "jaw": "立体中下颌骨配风尘古铜肤色，真实西域风貌"},
        "Silk Road Central Asian historical bone structure, deep-set eyes under strong brow ridge, tall slightly curved nasal bridge, sculpted cheekbones, weathered bronze skin",
        "眉弓与鼻根落差深邃，鼻梁高耸修长，眼窝阴影分明，配古铜风尘肌理，还原丝路西域真实骨相",
        ["丝路胡商", "西域使节", "唐代胡人乐师/将领", "异域历史旅者"],
        ["A003", "A007"],
        "避免直接套用现代欧美超模脸，应体现中亚/西域民族真实的鼻骨弧度与风沙肤质。",
        {"face_w": 52, "cheek_w": 55, "jaw_w": 44, "chin_y": 148, "chin_w": 18, "brow_depth": 11, "mid_ratio": 0.36, "accent": "#0f766e", "tag": "历史·丝路"}
    ),
    _bone(
        "B024", "历史写实与沧桑骨相（禁网红脸）", "枯槁高僧与乱世隐士骨相", "Ascetic Monk & Hermit Gaunt Cranial Structure", "历史写实（严禁网红滤镜）",
        {"upper": 0.34, "mid": 0.35, "lower": 0.31, "jaw_angle_deg": 122, "profile": "清癯见骨"},
        ["明显颅顶骨感与颞窝凹陷", "深陷眼眶与颧下颊凹", "极低皮下脂肪筋骨毕露"],
        {"eye": "深陷古井无波之眼，上睑褶皱深而静定", "brow": "眉骨清晰突显，眉尾垂淡", "nose": "瘦削嶙峋直鼻，鼻骨边界清楚", "lips": "枯淡静敛薄唇，无世俗火气", "jaw": "双颊深收、颧骨与下颌骨轮廓毕现，如古画罗汉清癯有神"},
        "gaunt ascetic cranial bone structure, deep orbital hollows, sunken temples and canine fossae, pronounced cheekbones, serene weathered skin with fine age lines, documentary realism",
        "低皮脂见骨，颞窝与颊窝凹陷自然，眉骨与颧骨如古雕罗汉般清癯鲜明，神态枯寂澄明",
        ["得道高僧", "乱世山野隐士", "苦行道长", "古籍抄经老儒"],
        ["A004", "A005", "A007"],
        "突出“清癯见骨而神采静定”的雕刻感，不要画成阴森恐怖干尸。",
        {"face_w": 46, "cheek_w": 51, "jaw_w": 34, "chin_y": 149, "chin_w": 14, "brow_depth": 9, "mid_ratio": 0.35, "accent": "#57534e", "tag": "历史·隐士"}
    ),
])
