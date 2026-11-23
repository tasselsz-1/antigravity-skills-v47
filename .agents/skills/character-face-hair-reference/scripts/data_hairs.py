# -*- coding: utf-8 -*-
"""42 Hairstyle Reference Entries (H001-H042) and hair color modifiers.
Policy: reference_only_do_not_use_directly.
"""

from data_anatomy import USAGE_POLICY, USAGE_WARNING_ZH, USAGE_WARNING_EN


def _hair(
    hid, group, gender, era, name, en_name,
    silhouette, formula_slots, prompt_en_ref, prompt_zh_ref,
    suggested_bones, svg_shape, accent
):
    return {
        "id": hid,
        "type": "hairstyle",
        "group": group,
        "gender": gender,
        "era": era,
        "name": name,
        "en_name": en_name,
        "usage_policy": USAGE_POLICY,
        "usage_warning_zh": USAGE_WARNING_ZH,
        "usage_warning_en": USAGE_WARNING_EN,
        "formula_dimension": "5. 发型 / 发色",
        "silhouette_features": silhouette,
        "formula_slots_ref_only": formula_slots,
        "prompt_en_ref_only": prompt_en_ref,
        "prompt_zh_ref_only": prompt_zh_ref,
        "suggested_bones": suggested_bones,
        "svg_file": f"assets/hairs/{hid}.svg",
        "svg_params": {"shape": svg_shape, "accent": accent, "gender": gender},
    }


HAIRS = [
    # 女性 · 现代与动漫幻想 (H001-H014)
    _hair(
        "H001", "女性 · 现代与动漫幻想", "女性", "现代/动漫", "姬发式 / 公主切", "Hime Cut",
        "齐眉钝感刘海 + 两侧齐下颌垂落切面鬓发 + 顺直长发大轮廓",
        {"color": "乌黑 / 墨蓝黑 / 深酒红", "texture": "垂顺丝滑带微冷反光", "noun": "经典姬发式公主切长直发", "motion": "两侧齐切鬓发贴合脸颊，后背长发微风轻扬"},
        "hime cut, blunt straight bangs, cheek-length flat sidelocks, long sleek straight hair",
        "齐刘海配两侧齐下颌公主切垂发，后披顺滑长直发，线条利落具东方贵族神秘感",
        ["B002", "B005", "B009"], "hime", "#ec4899"
    ),
    _hair(
        "H002", "女性 · 现代与动漫幻想", "女性", "现代/动漫", "高耸利落高马尾", "Sleek High Ponytail",
        "干净饱满发际线 + 脑后高位束起 + 弧度轻盈飘动长尾",
        {"color": "黑茶色 / 暖栗棕 / 焰红", "texture": "紧致顺滑发根配轻盈发尾", "noun": "高耸利落高马尾", "motion": "几缕细碎耳发垂落，高马尾随动作大幅甩动"},
        "high ponytail, clean exposed hairline, sleek lifted crown, dynamic flowing ponytail strands",
        "额前干净利落，脑后高位束起饱满高马尾，发尾在风中划出飒爽动感弧线",
        ["B003", "B007", "B015", "B022"], "high_ponytail", "#f43f5e"
    ),
    _hair(
        "H003", "女性 · 现代与动漫幻想", "女性", "动漫/现代", "对称高/低双马尾", "Symmetrical Twintails",
        "中分或偏分头顶 + 两侧对称束发 + 轻盈跳跃垂坠尾束",
        {"color": "浅金 / 樱粉 / 焦糖棕", "texture": "蓬松轻盈空气感发丝", "noun": "对称双马尾", "motion": "双束发尾随步伐轻快晃动，发梢微卷"},
        "symmetrical high twintails, airy layered bangs, lively bouncy hair tails",
        "头顶蓬松自然，两侧对称束起轻盈双马尾，发尾微卷带元气灵动剪影",
        ["B006", "B007", "B012"], "twintails", "#fb7185"
    ),
    _hair(
        "H004", "女性 · 现代与动漫幻想", "女性", "幻想/欧式", "螺旋钻头卷", "Ornate Twin Drill Ringlets",
        "两侧高体量螺旋圆柱大卷 + 华丽层次发冠剪影",
        {"color": "香槟金 / 玫瑰金 / 银白", "texture": "弹性饱满高光泽立体卷发", "noun": "对称螺旋钻头大卷发", "motion": "厚重弹性卷发随转身微颤，边缘透出逆光金边"},
        "twin drill hair, ornate sculpted spiral ringlets, regal voluminous curls",
        "两侧呈雕塑般饱满对称的螺旋钻头大卷，光泽华丽，凸显贵族大小姐辨识度",
        ["B001", "B007", "B009"], "drills", "#f59e0b"
    ),
    _hair(
        "H005", "女性 · 现代与动漫幻想", "女性", "现代/古今通用", "温柔半扎披发", "Half-Up Half-Down Long Hair",
        "上半区柔顺束于脑后 + 下半区自然垂落肩背 + 柔和八字碎刘海",
        {"color": "深栗棕 / 乌黑 / 柔灰褐", "texture": "柔润微光泽自然顺直", "noun": "优雅半扎披肩长发", "motion": "肩头垂落发缕轻贴锁骨，微风吹散些许鬓丝"},
        "half-up half-down long hair, gentle face-framing wisps, elegant flowing shoulder strands",
        "上层发丝优雅半束，下层长发柔顺披落双肩，两侧留出柔和碎鬓发修饰脸庞",
        ["B002", "B003", "B011"], "half_up", "#10b981"
    ),
    _hair(
        "H006", "女性 · 现代与动漫幻想", "女性", "现代/复古港风", "蓬松慵懒大波浪", "Voluminous Loose Waves",
        "大偏分高颅顶弧度 + 饱满 S 型层次波浪 + 宽肩包裹感",
        {"color": "暖焦糖棕 / 深黑茶 / 勃艮第红", "texture": "蓬松空气感弹性大卷", "noun": "复古蓬松大波浪长卷发", "motion": "刚睡醒般的慵懒凌乱层次，数缕发丝轻搭锁骨"},
        "voluminous long wavy hair, deep side part, soft layered loose S-waves, effortless glamour",
        "大偏分高颅顶配饱满弹性 S 型大波浪，层次蓬松慵懒，富有电影复古风情",
        ["B001", "B003", "B008", "B010"], "long_waves", "#d97706"
    ),
    _hair(
        "H007", "女性 · 现代与动漫幻想", "女性", "现代/职场/科幻", "一刀切齐短发 / 波波头", "Sleek Blunt Bob Cut",
        "齐下颌线锋利水平底边 + 贴合头型几何直切轮廓",
        {"color": "冷乌黑 / 铂金灰 / 银白", "texture": "哑光偏光直顺、发尾锐利整齐", "noun": "一刀切齐下颌短波波头", "motion": "发尾如刀裁般整齐，微微随转头轻扫下颌线"},
        "sleek blunt bob cut, chin-length razor-straight hair, crisp geometric edge",
        "齐下颌线一刀切直短发，发尾锋利整齐，紧贴头骨勾勒干练酷飒轮廓",
        ["B001", "B005", "B008", "B014"], "blunt_bob", "#6366f1"
    ),
    _hair(
        "H008", "女性 · 现代与动漫幻想", "女性", "现代/中性酷飒", "锁骨层次狼尾", "Feminine Shaggy Wolf Cut",
        "头顶轻盈高层次蓬松 + 颈后修长碎尾外翘 + 碎感遮眉刘海",
        {"color": "冷灰黑 / 烟熏青 / 挑染银灰", "texture": "轻薄微蓬碎发肌理", "noun": "锁骨高层次女性狼尾发", "motion": "发尾自然外翘杂而不乱，迎风翻飞感强"},
        "feminine wolf cut, shaggy high-layered shoulder-length hair, wispy flipped ends",
        "上蓬下收的锁骨高层次狼尾碎发，颈后发尾轻盈外翘，兼具少年感与酷飒气场",
        ["B005", "B008", "B009", "B015"], "wolf_female", "#0ea5e9"
    ),
    _hair(
        "H009", "女性 · 现代与动漫幻想", "女性", "国潮/动漫", "双丸子头 / 哪吒头", "Double High Hair Buns",
        "头顶两侧对称圆润发包 + 轻盈胎毛碎发 + 可配丝带或流苏",
        {"color": "漆黑 / 深棕 / 红黑挑染", "texture": "饱满紧实盘发配软萌碎发", "noun": "对称高双丸子头", "motion": "额角细碎绒发随微风颤动，俏皮利落"},
        "symmetrical double high hair buns, playful wispy forehead strands, energetic silhouette",
        "头顶左右对称盘起饱满双丸子头，耳际自然散落轻盈碎发，灵动俏皮辨识度高",
        ["B006", "B007", "B012"], "double_buns", "#f97316"
    ),
    _hair(
        "H010", "女性 · 现代与动漫幻想", "女性", "现代/田园/奇幻", "单侧垂肩麻花辫", "Single Side Braid Over Shoulder",
        "松弛蓬松顶发 + 粗松纹理单侧编辫垂落一侧胸前",
        {"color": "亚麻棕 / 深褐 / 蜜糖金", "texture": "微松自然编织发股纹理", "noun": "单侧慵懒垂肩麻花辫", "motion": "辫身抽丝自然蓬松，耳畔垂落几缕柔和细丝"},
        "loose thick single braid draped over one shoulder, soft framing wisps, warm natural texture",
        "发丝松散编成粗纹理麻花辫垂落一侧肩前，鬓边留自然碎发，温婉质朴有故事感",
        ["B002", "B003", "B011"], "side_braid", "#84cc16"
    ),
    _hair(
        "H011", "女性 · 现代与动漫幻想", "女性", "现代/职场/古典", "慵懒低盘丸子头", "Low Messy Chignon Bun",
        "后枕低位松弛盘结 + 纤细脖颈完全露出 + 几缕自然垂落发丝",
        {"color": "自然黑 / 冷茶棕 / 银灰", "texture": "柔和哑光轻蓬松质感", "noun": "法式慵懒低盘丸子头", "motion": "几缕碎发散落颈侧与脸颊边缘，松弛从容"},
        "low messy chignon hair bun at nape, loose face-framing strands, refined effortless updo",
        "发丝在后颈低位松弛盘成慵懒丸子髻，露出修长颈线，脸颊边零星碎发温柔随性",
        ["B002", "B003", "B005", "B008"], "low_bun", "#64748b"
    ),
    _hair(
        "H012", "女性 · 现代与动漫幻想", "女性", "暗黑/动漫/悬疑", "遮单眼不对称长斜发", "Asymmetrical One-Eye-Covered Cut",
        "大比例斜分长刘海遮蔽一侧眼眶 + 左右不等长层次发尾",
        {"color": "深紫黑 / 乌黑 / 冷银灰", "texture": "顺滑微重垂坠分束感", "noun": "不对称遮单眼斜分长发", "motion": "斜刘海在微风中若隐若现露出一只冷眸"},
        "asymmetrical long diagonal bangs covering one eye, uneven layered dark hair, mysterious silhouette",
        "大斜分长刘海自然遮掩一侧眼眸，左右层次错落不等长，营造强烈悬疑与暗黑张力",
        ["B001", "B005", "B010", "B014"], "one_eye", "#7c3aed"
    ),
    _hair(
        "H013", "女性 · 现代与动漫幻想", "女性", "动漫/日常", "呆毛配短碎发", "Short Tousled Hair with Ahoge",
        "耳下蓬松轻碎短发 + 头顶一缕标志性翘起呆毛弧线",
        {"color": "琥珀棕 / 浅橘 / 墨黑", "texture": "轻盈抓松凌乱短发丝", "noun": "带标志性呆毛的蓬松短碎发", "motion": "头顶翘起的单缕发丝随情绪晃动，朝气满满"},
        "short tousled layered hair with a distinct sticking-up ahoge strand on crown, lively casual cut",
        "轻盈凌乱的短碎发包裹耳际，头顶翘起一缕生动标志性呆毛，满溢元气少年/少女感",
        ["B006", "B011", "B012"], "ahoge_short", "#eab308"
    ),
    _hair(
        "H014", "女性 · 现代与动漫幻想", "女性", "仙侠/神明/幻想", "极长及踝飘逸仙发", "Absurdly Long Ethereal Flowing Hair",
        "超长瀑布般垂直与风动体量 + 轻纱般分缕尾端",
        {"color": "霜月银白 / 墨玉黑 / 极光浅金", "texture": "丝绸般细腻通透发光发丝", "noun": "极长及踝飘逸长发", "motion": "发海随气流在半空舒展翻涌，发梢透出柔光"},
        "absurdly long floor-grazing flowing hair, ethereal weightless wind-swept strands, mythic silhouette",
        "极长至踝的瀑布长发如轻纱般在风中舒展悬浮，发丝分缕轻盈通透，极具神性史诗感",
        ["B002", "B005", "B016"], "ethereal_long", "#38bdf8"
    ),
]


HAIRS.extend([
    # 女性 · 中国古代发髻 (H015-H022, 8套)
    _hair(
        "H015", "女性 · 中国古代发髻", "女性", "唐宋宫廷/古装", "盛唐高髻（峨髻 / 高椎髻）", "High Towering Tang Gaoji Bun",
        "高耸饱满头顶云髻 + 饱满两侧抱面鬓角 + 玉簪金钗点缀",
        {"color": "漆黑如墨（带天然乌润光泽）", "texture": "厚实饱满盘梳规整丝感", "noun": "盛唐高耸峨髻", "motion": "两鬓微贴面颊显端庄丰润，发簪步摇随步轻晃"},
        "high towering traditional Chinese Tang dynasty hair bun (Gaoji), full sculpted temples, ornate jade hairpins",
        "乌黑浓发高耸盘成端庄盛唐高髻，两鬓抱面丰盈，插素玉或金钿发簪，尽显雍容贵气",
        ["B002", "B003", "B021"], "ancient_gaoji", "#d97706"
    ),
    _hair(
        "H016", "女性 · 中国古代发髻", "女性", "汉晋古装/闺秀", "汉晋堕马髻", "Asymmetrical Drooping Duomaji Bun",
        "发髻明显偏向一侧下垂倾倒 + 慵懒垂耳软鬓发",
        {"color": "墨黑 / 柔和乌褐", "texture": "柔顺厚实微松盘垂质感", "noun": "古典侧倾堕马髻", "motion": "偏坠发髻伴随侧颜慵懒倾垂，鬓丝随微风拂动"},
        "asymmetrical drooping traditional Chinese bun (Duomaji) tilted to one side, soft framing wisps, graceful classical allure",
        "发髻精巧偏向一侧垂堕而下，耳边留数缕轻柔鬓丝，呈现汉晋闺秀独有的慵懒娇媚韵味",
        ["B002", "B009", "B010"], "ancient_duoma", "#c026d3"
    ),
    _hair(
        "H017", "女性 · 中国古代发髻", "女性", "汉晋/仙侠少女", "飞仙髻（结鬟双环式）", "Flying Immortal Twin-Loop Feixianji",
        "头顶左右双空心高环发髻 + 脑后披散长发 + 轻纱发带",
        {"color": "乌黑 / 墨蓝黑", "texture": "轻盈凌空盘环发丝", "noun": "古典结鬟飞仙髻", "motion": "双环凌空高耸，发带与背披长发随山风舒展"},
        "flying immortal twin-loop traditional Chinese hair bun (Feixianji), hollow elevated hair rings, flowing silk ribbons",
        "发丝左右结成高耸空心双环飞仙髻，余发自然披肩并缀轻纱丝带，极具灵动仙气",
        ["B002", "B006", "B007", "B011"], "ancient_feixian", "#06b6d4"
    ),
    _hair(
        "H018", "女性 · 中国古代发髻", "女性", "魏晋风流/才女", "拧旋灵蛇髻", "Twisted Sculptural Lingsheji Updo",
        "长发股拧转盘旋叠加 + 蜿蜒雕塑感不对称流动发环",
        {"color": "浓墨乌黑", "texture": "顺滑紧致拧股雕塑光泽", "noun": "魏晋拧旋灵蛇髻", "motion": "盘旋发股如游龙婉转，几缕细丝顺颈侧轻垂"},
        "twisted coiled traditional Chinese updo (Lingsheji), sculptural flowing hair loops, refined Wei-Jin scholar elegance",
        "乌发拧转盘旋叠出灵动不对称发环，轮廓如游龙般富有流动雕塑感，彰显魏晋名门才女气韵",
        ["B005", "B008", "B010", "B016"], "ancient_lingshe", "#0d9488"
    ),
    _hair(
        "H019", "女性 · 中国古代发髻", "女性", "古装少女/侍女", "双挂丫髻（垂环髻）", "Symmetrical Twin-Hanging Maiden Loops",
        "中分露额 + 耳侧左右对称下垂环形双髻 + 简约彩绳或小花钿",
        {"color": "自然乌黑", "texture": "规整软顺细密发丝", "noun": "古风对称双挂丫髻", "motion": "垂环轻贴耳后，碎刘海与胎毛自然贴额显娇俏"},
        "symmetrical twin-hanging looped hair buns beside ears, youthful ancient Chinese maiden hairstyle, simple silk ties",
        "中分清爽露额，耳侧对称垂挂精致双环丫髻，缀以素雅丝绳，充满古代灵动少女朝气",
        ["B006", "B011", "B012"], "ancient_yaji", "#f43f5e"
    ),
    _hair(
        "H020", "女性 · 中国古代发髻", "女性", "宋明正剧/主母", "盘叠端庄低髻配步摇", "Folded Low Classical Bun with Buyao",
        "后脑规整层叠低发髻 + 利落分缝全露额头 + 珠玉步摇垂饰",
        {"color": "乌黑 / 中年微夹几缕银丝", "texture": "细腻紧贴克制哑光发丝", "noun": "宋明盘叠端庄低髻", "motion": "整体仪态稳重端方，珠玉步摇在侧颜微晃生光"},
        "neatly folded low coiled traditional Chinese updo, exposed clean forehead, adorned with subtle gold pearl Buyao hairpin",
        "乌发向后规整层叠盘成端方低髻，额际干净克制，仅以一支珠玉步摇点缀，沉稳持重",
        ["B002", "B003", "B020"], "ancient_low_buyao", "#b45309"
    ),
    _hair(
        "H021", "女性 · 中国古代发髻", "女性", "宋代写实/清雅", "宋式素雅珍珠矮髻", "Song Dynasty Minimalist Pearl Low Bun",
        "低平圆润脑后矮髻 + 素色抹额或极简珍珠排簪 + 清淡无繁饰",
        {"color": "自然纯黑", "texture": "柔和哑光自然微蓬质感", "noun": "宋代素雅矮髻", "motion": "耳际极少量软碎发随风微动，整体素净留白"},
        "Song dynasty understated low rounded Chinese bun, minimalist small white pearl hairpins, restrained classical aesthetics",
        "发丝在后脑柔顺挽成低平圆润宋式矮髻，仅点缀一排极简小白珍珠簪，素雅清丽极富宋画韵味",
        ["B002", "B005", "B016"], "ancient_song", "#64748b"
    ),
    _hair(
        "H022", "女性 · 中国古代发髻", "女性", "古装武侠/女将", "侠女束冠高马尾混编发", "Wuxia Female Warrior Topknot & Ponytail",
        "顶心利落金属/皮质小冠束起 + 长直发高束垂落 + 侧边极细编辫",
        {"color": "乌黑 / 深栗黑", "texture": "紧实利落微带风尘韧感", "noun": "古风女侠束冠高束发", "motion": "高束长尾与两鬓散落碎丝迎风翻卷，飒爽果敢"},
        "traditional Chinese female warrior high topknot with simple dark bronze crown, long wind-swept ponytail and slim side braids",
        "顶心以素铁小冠利落束起高马尾，耳侧编入一两股细辫，全露英挺额眉，尽显边塞女将与女侠风范",
        ["B003", "B015", "B022"], "ancient_wuxia_f", "#be123c"
    ),
    # 男性 · 现代与动漫潮流 (H023-H028, 前6套)
    _hair(
        "H023", "男性 · 现代与动漫潮流", "男性", "现代/硬汉/军旅", "干练寸头 / 极短平头", "Crisp Military Buzz Cut",
        "紧贴头骨极短轮廓 + 清晰硬朗发际线与颞角转折",
        {"color": "乌黑 / 铁灰短茬", "texture": "粗糙硬挺极短发茬质感", "noun": "利落硬汉寸头", "motion": "完全露出头骨与眉骨比例，毫无遮挡修饰"},
        "short military buzz cut, crisp exposed hairline, tight cranial contour, rugged masculine minimalism",
        "紧贴头骨的极短寸头，清晰展现额角发际线、颅顶弧度与下颌硬朗块面，极度干练硬核",
        ["B004", "B017", "B019"], "male_buzz", "#334155"
    ),
    _hair(
        "H024", "男性 · 现代与动漫潮流", "男性", "现代/商务/复古", "侧分油头 / 渐变背头", "Slicked-Back Undercut Side Part",
        "两侧渐变修短 + 顶部整齐侧分向后梳拢 + 饱满立体弧度",
        {"color": "漆黑 / 深棕 / 鬓角微灰", "texture": "微光泽整洁定型分明发束", "noun": "经典侧分渐变背头", "motion": "整体一丝不苟，仅前额偶有一缕微松垂落增添张力"},
        "slicked-back undercut with crisp side parting, faded temples, refined gentleman silhouette",
        "两侧利落渐变推短，顶发清晰侧分并向后梳起饱满弧度，展现精英权贵或复古绅士克制气场",
        ["B001", "B003", "B004", "B013"], "male_slick", "#1d4ed8"
    ),
    _hair(
        "H025", "男性 · 现代与动漫潮流", "男性", "现代/都市/偶像", "三七分括号微卷刘海", "Two-Block Parted Curtain Bangs",
        "自然三七分露部分额头 + 两侧柔和内扣括号弧度 + 后颈干净收短",
        {"color": "自然黑 / 冷茶棕 / 深亚麻", "texture": "柔软蓬松自然空气感", "noun": "都市三七分括号刘海短发", "motion": "前额两缕括号弧度刘海随微风轻晃，层次清爽"},
        "modern two-block haircut, 70-30 parted curtain bangs softly framing forehead, natural airy volume",
        "清爽三七分露额轮廓，前额发丝呈柔和括号弧度轻修饰眉眼，蓬松自然贴合现代都市青年气质",
        ["B002", "B005", "B011", "B015"], "male_curtain", "#0284c7"
    ),
    _hair(
        "H026", "男性 · 现代与动漫潮流", "男性", "现代/少年/校园", "纹理蓬松碎盖短发", "Textured Tousled Short Crop",
        "圆润蓬松顶部层次 + 轻薄凌乱齐眉碎刘海 + 耳际轻盈收尾",
        {"color": "自然黑 / 暖栗褐", "texture": "轻盈抓松哑光纹理碎发", "noun": "自然纹理蓬松碎短发", "motion": "发顶略显刚运动后的随意蓬松感，生动少年气息"},
        "textured tousled short crop, lightweight layered messy fringe, natural youthful movement",
        "头顶抓出蓬松自然纹理层次，前额轻薄碎刘海微遮眉梢，洋溢清澈自然的少年感与日常真实感",
        ["B006", "B011", "B015"], "male_crop", "#10b981"
    ),
    _hair(
        "H027", "男性 · 现代与动漫潮流", "男性", "现代/动漫/酷哥", "层次不羁男性狼尾", "Layered Men's Shaggy Wolf Mullet",
        "顶区蓬松抓乱 + 两侧锐利鬓发修饰颧颊 + 后颈修长层次碎尾",
        {"color": "冷乌黑 / 银灰挑染 / 暗蓝黑", "texture": "干爽锋利分缕碎发肌理", "noun": "高层次不羁男性狼尾发", "motion": "后颈发尾随风扬起，前额碎发微遮半眼显叛逆"},
        "layered men's wolf cut, shaggy elongated mullet nape, sharp face-framing side locks, rebellious edge",
        "前碎后长的锐利层次男性狼尾，后颈修长碎尾自然外散，极具现代酷哥与二次元不羁辨识度",
        ["B001", "B005", "B014", "B015"], "male_wolf", "#6366f1"
    ),
    _hair(
        "H028", "男性 · 现代与动漫潮流", "男性", "动漫/热血少年", "锐利动态刺猬短发", "Spiky Dynamic Anime Shounen Hair",
        "放射状锐利分簇发尖 + 向上张扬的动态大剪影",
        {"color": "炭黑 / 烈焰红棕 / 亮金", "texture": "分簇明确富有张力的硬朗发束", "noun": "热血动态层次刺猬短发", "motion": "发簇如火焰般向斜上方自然张扬，力量感强烈"},
        "spiky dynamic anime shounen hairstyle, sharp layered directional hair clumps, energetic heroic silhouette",
        "棱角分明的放射状分簇短发，发束自带强烈向上的动势张力，是热血少年与动作主角经典轮廓",
        ["B004", "B006", "B015"], "male_spiky", "#ef4444"
    ),
])


HAIRS.extend([
    # 男性 · 现代与动漫潮流 (H029-H032, 后4套)
    _hair(
        "H029", "男性 · 现代与动漫潮流", "男性", "现代/文艺/悬疑", "中分齐耳慵懒长微卷", "Center-Parted Ear-Length Wavy Hair",
        "自然中分弧度 + 齐耳至下颌柔卷发尾 + 略遮眉尾阴郁文艺感",
        {"color": "深黑 / 深棕 / 灰褐", "texture": "微湿哑光自然垂顺微卷", "noun": "文艺中分齐耳微卷发", "motion": "发缕轻拂颧骨与耳廓，微风吹动下自带电影故事感"},
        "center-parted ear-length slightly wavy men's hair, relaxed melancholic cinema silhouette, soft layered ends",
        "中分齐耳微卷发自然贴合颧颊，质感略带松弛哑光，非常适合悬疑侦探、艺术家或忧郁智性男角",
        ["B005", "B008", "B014"], "male_center_wave", "#4f46e5"
    ),
    _hair(
        "H030", "男性 · 现代与动漫潮流", "男性", "现代/日系/都市", "艺术感武士低马尾 / 半丸子", "Modern Samurai Half-Bun / Low Tie",
        "两侧微短或自然垂落 + 头顶后部随性扎成小丸子或低短马尾",
        {"color": "自然黑 / 深咖啡 / 烟灰", "texture": "略显粗硬自然松散束发", "noun": "现代随性后束半丸子短长发", "motion": "鬓边散落几缕碎发，干练中透着不羁松弛"},
        "modern men's half-up man bun, low tied back knot with loose side strands, rugged creative silhouette",
        "将上层中长发随性在脑后扎成低丸子或短束，耳际留自然散落碎丝，兼具雅痞与行动派气场",
        ["B001", "B004", "B015"], "male_manbun", "#0f766e"
    ),
    _hair(
        "H031", "男性 · 现代与动漫潮流", "男性", "现代/成熟精英", "两鬓霜白大背头", "Mature Silver-Templed Swept-Back Hair",
        "饱满成熟额角发际线 + 向后自然梳拢微浪 + 两鬓清晰自然斑白",
        {"color": "乌黑底色配两鬓自然霜白 (Salt-and-pepper temples)", "texture": "干爽微硬成熟质感", "noun": "成熟两鬓霜白后梳短发", "motion": "发际线自然微后移显真实阅历，稳重有压迫感"},
        "mature men's swept-back hair with natural salt-and-pepper graying temples, authentic receding hairline, authoritative presence",
        "成熟自然的向后微浪梳发，两鬓点缀真实斑白霜丝与微后移发际线，尽显中年重臣、父辈或商界大佬威仪",
        ["B003", "B013", "B017", "B021"], "male_silver_back", "#475569"
    ),
    _hair(
        "H032", "男性 · 现代与动漫潮流", "男性", "动漫/暗黑奇幻", "银灰偏分过眉冷峻长碎发", "Long Swept Fringe Dark-Fantasy Cut",
        "大偏分锐利长刘海斜掠眉眼 + 贴颈修长分束碎发",
        {"color": "霜银灰 / 墨紫黑 / 冷白", "texture": "丝滑锐利细长分束感", "noun": "偏分过眉冷峻长碎发", "motion": "斜长碎刘海半掩锋利眼神，气场冷冽危险"},
        "long asymmetrical swept fringe skimming past eyes, sleek silver-ash layered anime-cinema hair, cold dangerous aura",
        "大偏分锐利长碎刘海斜掠一侧眉眼，颈后发缕纤细修长，极具暗黑奇幻与高冷反派张力",
        ["B001", "B005", "B010", "B014"], "male_long_fringe", "#7c3aed"
    ),
    # 男性 · 古风束冠与史诗奇幻 (H033-H042, 10套)
    _hair(
        "H033", "男性 · 古风束冠与史诗奇幻", "男性", "古风/世子/侠客", "高束玉冠半披长发", "High Jade-Crown Topknot with Flowing Back",
        "全露饱满额头 + 头顶高耸小冠束发 + 下半长直发如瀑披散",
        {"color": "乌墨黑", "texture": "顺直微光泽长发丝配精致束冠", "noun": "古风高束玉冠半披长发", "motion": "脑后长发与冠带随策马疾风向后翻卷"},
        "traditional Chinese male high topknot secured with carved jade crown (Guan), clean exposed forehead, flowing loose back hair",
        "前额洁净无刘海，头顶高束精致青玉发冠，后背长发如墨披散并随风扬起，少年侠客与世子经典形制",
        ["B002", "B005", "B015", "B016"], "ancient_m_jade", "#0ea5e9"
    ),
    _hair(
        "H034", "男性 · 古风束冠与史诗奇幻", "男性", "历史正剧/文臣帝王", "全部束冠正式发髻", "Formal Fully Gathered Crowned Topknot",
        "发丝全部向上一丝不苟收拢 + 顶心正规梁冠/金冠/纱冠 + 零散碎披发",
        {"color": "纯黑 / 中年两鬓斑白", "texture": "严整紧贴哑光历史发丝", "noun": "古代正式全束冠发髻", "motion": "鬓角修整严密，无多余现代飘逸碎发，恪守礼法威严"},
        "fully gathered traditional Chinese male topknot inside formal historical crown, completely clean hairline, zero loose modern bangs, authentic period drama",
        "全部发丝严密向上梳拢束入礼冠，无一缕现代碎刘海，干净显露真实额骨与鬓角，符合历史正剧文臣帝王礼制",
        ["B003", "B017", "B018", "B021"], "ancient_m_formal", "#a16207"
    ),
    _hair(
        "H035", "男性 · 古风束冠与史诗奇幻", "男性", "仙侠/隐士/道家", "道髻木簪半披长发", "Taoist Topknot with Wooden Pin",
        "顶心松雅挽出圆润道髻 + 素色木簪横插 + 垂顺长发披肩",
        {"color": "乌黑 / 灰白霜发", "texture": "轻盈自然哑光微松发丝", "noun": "古朴木簪道髻半披发", "motion": "双鬓垂落两缕清逸碎发，长发随山岚轻拂"},
        "Taoist scholar topknot fastened with plain weathered wooden hairpin, half-up half-down straight hair, serene hermit silhouette",
        "头顶以朴素旧木簪横挽道髻，余发自然垂肩，双鬓零星碎发随清风微动，尽显隐士道长超然风骨",
        ["B005", "B016", "B018", "B024"], "ancient_m_taoist", "#14b8a6"
    ),
    _hair(
        "H036", "男性 · 古风束冠与史诗奇幻", "男性", "乱世/狂士/武将", "乱世布带半束披散发", "Wild Cloth-Headband Swept Long Hair",
        "粗麻或暗色布带束额/半扎 + 凌乱厚重黑发狂放披散",
        {"color": "粗粝墨黑 / 沾尘灰黑", "texture": "略显粗糙干爽凌乱发缕", "noun": "乱世布带半束狂放长发", "motion": "大股乱发随沙场狂风肆意翻卷，豪迈沧桑"},
        "wild partially tied long dark hair with worn cloth headband, rugged wind-swept tangled strands, battle-scarred wanderer",
        "以旧粗布带简单束额或半扎，厚重略乱的长发在沙场或山风中狂放翻卷，饱含乱世豪杰与狂士野气",
        ["B004", "B015", "B017", "B019"], "ancient_m_wild", "#b91c1c"
    ),
    _hair(
        "H037", "男性 · 古风束冠与史诗奇幻", "男性", "历史/沙场武将", "武将铁箍高椎髻（配硬茬胡须）", "General Iron-Circlet War Topknot",
        "厚实发丝全部紧束为结实高椎髻 + 暗铁箍/皮绳缠束 + 两侧干净贴合头盔",
        {"color": "硬挺炭黑 / 灰白夹杂", "texture": "粗硬微乱带风沙质感", "noun": "沙场武将铁箍高椎髻", "motion": "额角有少许汗湿碎发贴附，粗犷实用可随时戴盔"},
        "tightly bound historical Chinese general high war topknot wrapped with dark iron circlet and leather cord, sweat-dampened stray hairs at temples",
        "全部粗硬发丝以皮绳铁箍紧束为结实高椎髻，额角残留几缕沙场汗湿碎丝，粗犷写实毫无偶像浮华",
        ["B004", "B017", "B019"], "ancient_m_general", "#78350f"
    ),
    _hair(
        "H038", "男性 · 古风束冠与史诗奇幻", "男性", "历史/清雅谋士", "幅巾/纶巾包发配斑白双鬓", "Historical Scholar Kerchief with Graying Temples",
        "素布幅巾或纶巾包覆头顶发髻 + 露出清癯额骨与斑白两鬓",
        {"color": "乌黑夹杂自然霜白两鬓", "texture": "干爽细瘦略显稀疏发丝", "noun": "古风谋士幅巾包发", "motion": "两鬓斑白碎丝微现，巾带在脑后轻垂"},
        "traditional Chinese historical scholar cloth kerchief (Funjin) wrapping topknot, exposed lean forehead and salt-and-pepper temples",
        "以素色麻布幅巾规整包覆头顶发髻，露出清瘦思虑的额头与斑白双鬓，高度贴合三国两宋真实谋士形象",
        ["B018", "B020", "B024"], "ancient_m_kerchief", "#475569"
    ),
    _hair(
        "H039", "男性 · 古风束冠与史诗奇幻", "男性", "历史/高僧苦行", "写实戒疤/青茬光头（全露颅骨）", "Authentic Shaved Monk Head with Stubble",
        "完全呈现颅骨顶骨/额骨/枕骨真实起伏 + 极浅青灰色剃发发茬",
        {"color": "极浅青灰短剃茬", "texture": "真实头皮纹理、毛孔与轻微风霜晒痕", "noun": "写实青茬光头", "motion": "全无遮挡，头骨解剖比例与光影立体度一览无余"},
        "authentic shaved monk head showing cranial bone contour and subtle bluish hair stubble, weathered natural scalp skin texture",
        "全露真实颅骨起伏与极浅青灰剃发发茬，保留头皮真实光影与岁月肌理，绝非塑料反光卤蛋头",
        ["B019", "B020", "B024"], "ancient_m_monk", "#57534e"
    ),
    _hair(
        "H040", "男性 · 古风束冠与史诗奇幻", "男性", "丝路/北方游牧历史", "游牧民族多股细辫垂肩发", "Steppe Nomad Multi-Braid Shoulder Hair",
        "头顶留发或分股编成多条紧实细辫垂落双肩 + 骨珠/皮绳装饰",
        {"color": "深褐黑 / 日晒微棕黑", "texture": "粗硬干爽紧实细辫纹理", "noun": "草原/丝路多股细辫发", "motion": "数十缕紧编细辫随骑射晃动，充满边塞异域真实感"},
        "historical steppe nomad hairstyle with multiple tight slim braids falling over shoulders, leather ties and weathered bronze beads",
        "粗硬黑发编成多股紧实细辫垂落双肩，点缀皮绳与做旧铜珠，高度还原草原与丝路异域历史武士风貌",
        ["B019", "B023"], "epic_nomad_braids", "#0f766e"
    ),
    _hair(
        "H041", "男性 · 古风束冠与史诗奇幻", "男性", "欧美史诗/维京奇幻", "维京铲青编发战髻", "Viking Undercut Braided Warrior Hair",
        "两侧剃短铲青显露颞骨 + 顶部厚发向后编入粗辫并扎战髻",
        {"color": "暗灰金 / 粗褐 / 铁灰", "texture": "粗糙风霜干涩发丝与粗辫", "noun": "维京铲青战辫发", "motion": "顶辫厚重粗犷，侧边剃短发茬配浓密胡须"},
        "Viking warrior shaved-sides undercut with thick braided top locks pulled into rugged back knot, medieval epic realism",
        "两侧铲青突出硬朗颧颌与颞骨，顶发编成粗犷战辫向后扎结，洋溢北欧史诗战场的野性力量感",
        ["B001", "B004", "B013", "B019"], "epic_viking", "#b45309"
    ),
    _hair(
        "H042", "男性 · 古风束冠与史诗奇幻", "男性", "欧美古典/暗黑史诗", "中世纪贵族中分微卷披肩发", "Medieval Noble Shoulder-Length Wavy Hair",
        "优雅中分或微偏分 + 及肩厚实自然大微卷 + 贵族高贵剪影",
        {"color": "深栗棕 / 苍白银金 / 墨黑", "texture": "厚重柔润带自然油画光泽波浪", "noun": "中世纪贵族及肩微卷发", "motion": "齐肩波浪发缕轻垂披风领口，沉郁古典史诗氛围"},
        "medieval European noble shoulder-length wavy hair, natural center parting, heavy Renaissance-like flowing locks, regal solemn silhouette",
        "自然中分的及肩厚重微卷发，发尾轻覆古典披风领口，带有欧洲古典油画与史诗奇幻领主的高贵沉郁感",
        ["B001", "B005", "B013", "B014"], "epic_medieval", "#4338ca"
    ),
])


HAIR_COLOR_MODIFIERS = [
    {"category": "自然经典单色", "zh": "漆黑如墨", "en": "jet-black hair", "note": "国风古典、历史写实、高冷对比首选"},
    {"category": "自然经典单色", "zh": "墨蓝黑", "en": "ink-blue black hair", "note": "冷调月光、仙侠、暗夜奇幻质感"},
    {"category": "自然经典单色", "zh": "暖栗棕", "en": "warm chestnut brown hair", "note": "柔和亲和、阳光治愈、日常电影感"},
    {"category": "自然经典单色", "zh": "烟灰银白", "en": "ash silver-white hair", "note": "神明、仙尊、赛博科幻、高冷反派"},
    {"category": "自然经典单色", "zh": "香槟浅金", "en": "champagne blonde hair", "note": "欧式贵族、史诗奇幻、温柔光感"},
    {"category": "自然经典单色", "zh": "深红酒棕", "en": "deep crimson auburn hair", "note": "热烈御姐、异域奇幻、强戏剧冲突"},
    {"category": "岁月写实层次", "zh": "两鬓霜白（盐椒灰）", "en": "salt-and-pepper graying temples", "note": "历史谋士、中年帝王、重臣与沧桑主角必用"},
    {"category": "岁月写实层次", "zh": "日晒微枯深褐", "en": "sun-bleached dusty dark brown hair", "note": "边塞女将、游牧武士、历史平民去塑料感"},
    {"category": "现代动漫层次", "zh": "发尾自然渐变", "en": "subtle gradient dip-dyed hair ends", "note": "现代潮酷、二次元幻想高辨识度"},
    {"category": "现代动漫层次", "zh": "挂耳染 / 挑染", "en": "subtle inner-layer highlights / streaked highlights", "note": "多人同框快速区分现代都市/赛博角色"},
]
