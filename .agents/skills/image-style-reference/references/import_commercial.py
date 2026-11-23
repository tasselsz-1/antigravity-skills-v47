"""Import the user-selected local commercial style list, preserving stable IDs."""
import json, re, hashlib, shutil
from pathlib import Path
from urllib.request import urlopen
from datetime import datetime
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
URL = 'http://127.0.0.1:8766/styles.html'
MODULES = [
'经典电视动画赛璐璐，清晰线稿与平涂色块，两至三层硬边阴影；局部规则网点辅助暗部，色面整洁，形体关系明确',
'精细二维动画电影质感，细密有序的背景描绘，云层具有丰富体积与层次，电影式光影和受控光晕，主体轮廓清晰',
'细腻日常动画，精细轻盈线稿，柔和通透上色，克制的逆光发丝与自然明暗，表情和细节处理含蓄',
'幻想游戏动画质感，清晰细致的角色线稿，高饱和而有层级的配色，服装材质与装饰结构分明；角色比例遵循当前设定',
'少年热血漫画，粗细鲜明的有力轮廓，概括明暗块面，富有张力的透视；速度线与夸张动势仅在动作需要时使用',
'九十年代手绘赛璐璐动画质感，偏暗而层次分明的配色，清晰硬边阴影，机械与建筑细节精密有序，保留手绘轮廓，不添加胶片颗粒',
'精细三维国漫质感，可信的形体结构和写实光照，布料、金属与皮肤具有明确材质差异，细节有序；比例与特效服从当前角色和剧情',
'风格化三维与二维绘画融合，三维体积照明结合手绘贴图和概括笔触，明暗色块清晰，避免材质过度摄影化',
'东方厚涂，清楚可见且有方向的绘画笔触，硬边高光与暖色暗部形成层次，皮肤和物体通过笔触过渡塑形，材质干净',
'中国水墨动画，墨色浓淡与素描式结构线结合，轮廓虚实相生，层次简练，二维笔墨保留空间纵深与清楚形体',
'赛博国潮的材质与设色，精密机械表面结合克制鎏金装饰，青色与紫红霓虹对照，电子自发光受控；是否出现机械和纹样由当前内容决定',
'港漫硬派，粗黑轮廓与明确形体块面，规则网点阴影，红黑金高对比配色，硬朗而有力的墨线表现',
'剪纸皮影视觉，镂空轮廓、平面色块和层叠纸片，背光透射形成边缘层次，朱红、墨黑与米黄配色，切边清晰',
'全彩韩式条漫，洁净线稿，柔和渐变上色，通透肤色与清晰表情，形体简练而有体积；画幅与景别按当前需求决定',
'韩式半写实厚涂，细腻有序的皮肤笔触，柔和偏冷的照明，克制成熟的配色，写实结构与绘画概括并存',
'韩式武侠绘画，水墨的虚实笔触与厚涂体积结合，边缘疏密有致，留白清爽，动作需要时表现飘逸线势',
'美式卡通，概括夸张的造型语言，粗而干净的轮廓，高对比色块，表情具有弹性；人物身份与基本比例遵循当前设定',
'亲和三维动画电影质感，柔和体积光、自然次表面散射与受控景深，材质圆润细腻，形体结构清楚，暖冷照明协调',
'美式英雄漫画质感，坚实形体块面、粗黑轮廓、规则半调网点和强明暗对比；动作与透视张力依剧情选择',
'法比清线漫画，整洁等宽线条，不使用密集排线，平涂色块，透视与空间关系清楚，细节简洁可读',
'暗黑哥特绘画，细密有序钢笔线、适度拉长和扭曲的造型语言，单一冷光与深色阴影，暗部保留必要结构',
'电影感写实，可信材质与物理光照，受控体积光和自然景深，明暗层级清楚，皮肤与表面细节真实细腻，不添加胶片颗粒',
'自然真人质感，符合尺度的皮肤纹理、细致真实毛发、自然微表情，光照和材质可信，避免蜡质皮肤与过度磨皮',
'末世题材的低饱和色彩与粗粝实体材质，冷暖和明暗分层明确；破损、废墟与沙尘仅按场景需要出现，不把随机脏点当作材质',
'赛博朋克光色，冷暗底色搭配高饱和青色与品红局部光源，湿润表面的反射有序，薄雾层次受控；雨夜和城市内容按当前场景决定',
'复古胶片色彩倾向，轻度褪色、青绿色偏色与柔和受控光晕，明暗过渡细腻，保留清晰结构；默认无随机颗粒和录像噪声',
'定格黏土质感，可辨的塑形边缘和克制工具痕，微缩尺度的实体布景照明，材质柔软而形体清晰；不默认添加指纹、污痕或画面抖动',
'像素艺术，清晰点阵与受限色板，形体以有序像素块概括；局部规则抖色用于层次过渡，不引入随机噪点或模糊缩放',
'实验水墨，受控泼墨、飞白和大面积洁净留白，笔势连贯，墨色浓淡层次清楚，笔迹服务形体，不出现无关污点',
'受控故障艺术，局部RGB色道分离、规则扫描线与几何数据块，结构断裂具有设计节奏，主体仍可辨，不添加随机噪声或压缩伪影',
'混合媒介拼贴，清晰撕纸边缘、剪纸层叠和克制胶带材质，多种表面形成有意的视觉差异；不添加可识别报刊文字、标志或无关污渍',
'低多边形视觉，清楚几何面片与平面着色，以面片明暗塑造体积，色块整洁，不叠加表面纹理'
]

def main():
    raw = urlopen(URL).read()
    source = raw.decode('utf-8')
    group_blocks = re.split(r"no: '([0-9]+)', name: '([^']+)'", source)[1:]
    rows = []
    for i in range(0, len(group_blocks), 3):
        no, category, block = group_blocks[i:i+3]
        for match in re.finditer(r"\{ n: '([^']*)'.*?pos: '(main|sub|exp)' \}", block):
            fields = dict(re.findall(r"(\w+): '([^']*)'", match.group()))
            rows.append((category, fields))
    assert len(rows) == len(MODULES) == 32
    catpath = ROOT/'references/catalog.json'
    catalog = json.loads(catpath.read_text('utf-8'))
    assert len(catalog['entries']) == 76, 'Already imported or unexpected catalog; inspect before updating.'
    backup = ROOT/'references/backups'/datetime.now().strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True)
    for rel in ['SKILL.md','references/catalog.json','references/index.md','画风参考库.html']:
        dest = backup/rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/rel, dest)
    srcdir = ROOT/'references/sources/commercial-2026-11-01'
    srcdir.mkdir(parents=True, exist_ok=True)
    (srcdir/'styles.html').write_bytes(raw)
    related = {0:['S023'],1:['S069'],8:['S062'],9:['S064'],10:['S065'],11:['S066'],12:['S068'],13:['S028'],17:['S072'],18:['S073'],21:['S053'],24:['S049'],25:['S055'],26:['S037'],28:['S017','S064'],29:['S076']}
    entries = []
    for i, ((category,s), module) in enumerate(zip(rows, MODULES)):
        e = dict(id=f'S{77+i:03}',category='商业画风·'+category,slug='commercial-'+s['en'].lower().replace(' ','-'),name=s['n'],aliases=[s['en']],style_prompt_zh=module,reference_image=None,source_page=URL,source_snapshot='references/sources/commercial-2026-11-01/styles.html',source_title=s['n'],source_original_prompt_en=s['prompt'],summary=s['feat'],use_cases=s['fit'].split('、'),reference_coordinates=s['ref'],source_position=s['pos'],source_position_note='源作者的主流/细分/实验判断，未经独立市场数据验证。',related_entries=related.get(i,[]),visual_review='无来源样图，仅文字描述；未完成视觉验证。',reference_limitations='源页未提供图片，代表作品名称仅作来源坐标，不等于已查看作品。',prompt_status='根据源页特点通用化改写，未做出图实测。',adaptation_note='仅借用媒介、线条、设色、材质与光影；人物、服装、场景、动作、画幅与构图由当前任务决定。',optional_effects_note='颗粒、脏污、沙尘、旧化、光斑与抖动不默认继承；规则网点、像素与必要媒介纹理可受控保留。',license_note='用户指定本地文本页面；未发现明确授权声明，不沿用其他来源的CC0许可。',sha256=None,image_size=None)
        entries.append(e)
        (ROOT/f'references/entries/{e["id"]}.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n','utf-8')
    catalog['entries'].extend(entries)
    catalog['date']='2026-11-01'
    catalog['reference_origin']+=' S077–S108 来自用户指定商业画风全集 styles.html，32条均为文字模块，无来源图片。'
    catpath.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n','utf-8')
    idx=ROOT/'references/index.md'
    with idx.open('a',encoding='utf-8') as f:
        f.write('\n## 商业画风全集 · S077–S108\n\n来源：http://127.0.0.1:8766/styles.html ，2026-11-01 归档。六大体系32条，仅文字模块，未做视觉验证。与旧画风相近的条目保留独立来源并记录 related_entries，旧编号不变。\n\n|编号|分类|画风|条目|\n|---|---|---|---|\n')
        for e in entries: f.write(f'|{e["id"]}|{e["category"]}|{e["name"]}|[读取](entries/{e["id"]}.json)|\n')
    page=ROOT/'画风参考库.html'
    soup=BeautifulSoup(page.read_text('utf-8'),'html.parser')
    soup.select_one('header p').string='108种画风：原图鉴60种＋银幕画风志16种＋商业画风32种。新增S077–S108均为文字模块，没有来源样图，尚未做视觉验证和出图实测。支持按编号、名称或分类检索。'
    nav=soup.new_tag('p'); a=soup.new_tag('a',href='商业画风全集.html');a.string='查看商业画风全集 · S077–S108';nav.append(a);soup.header.append(nav)
    for category in dict.fromkeys(e['category'] for e in entries):
        option=soup.new_tag('option');option.string=category;soup.select_one('#category').append(option)
    for e in entries:
        card=soup.new_tag('article',attrs={'data-category':e['category']})
        placeholder=soup.new_tag('div',style='padding:44px 20px;text-align:center;background:#eee9e0');placeholder.string='仅文字模块 · 来源未提供样图';card.append(placeholder)
        body=soup.new_tag('div',attrs={'class':'body'});card.append(body)
        for tag,cls,txt in [('small',None,e['id']+' · '+e['category']),('h2',None,e['name']),('p','prompt',e['style_prompt_zh']),('button','copy','复制画风提示词'),('p','status',e['visual_review'])]:
            node=soup.new_tag(tag,attrs={'class':cls} if cls else {});node.string=txt;body.append(node)
        details=soup.new_tag('details');body.append(details)
        summary=soup.new_tag('summary');summary.string='适配题材、来源与复用说明';details.append(summary)
        for txt in ['适配：'+'、'.join(e['use_cases']),'来源特点：'+e['summary'],'代表坐标（未验证）：'+e['reference_coordinates'],'相近条目：'+('、'.join(e['related_entries']) or '无已标注关联'),e['adaptation_note'],e['optional_effects_note'],'来源英文关键词（仅追溯）：'+e['source_original_prompt_en'],e['license_note']]:
            p=soup.new_tag('p');p.string=txt;details.append(p)
        soup.select_one('#grid').append(card)
    page.write_text(str(soup),'utf-8')
    subset=BeautifulSoup(str(soup),'html.parser')
    subset.title.string='商业画风全集'
    subset.h1.string='商业画风全集'
    subset.select_one('header p').string='六大体系 · 32种画风 · S077–S108。仅文字模块，未做视觉验证和出图实测。'
    subset.select_one('header a')['href']='画风参考库.html';subset.select_one('header a').string='返回全部108种画风'
    for card in subset.select('article')[:76]:card.decompose()
    for option in subset.select('#category option')[1:]:
        if not option.text.startswith('商业画风·'):option.decompose()
    (ROOT/'商业画风全集.html').write_text(str(subset),'utf-8')
    skill=ROOT/'SKILL.md'
    text=skill.read_text('utf-8').replace('S001–S076','S001–S108')
    text+='\n## 商业画风全集 · 2026-11-01 更新\n\nS077–S108来自用户指定 `http://127.0.0.1:8766/styles.html`，涵盖日式动画、国漫、韩漫、欧美、写实、特殊媒介六类，共32条；独立入口为 `商业画风全集.html`。全部仅文字模块、未做视觉验证，无参考图片，不宣称已参考代表作品。原页归档在 `references/sources/commercial-2026-11-01/styles.html`。保留源英文关键词与特点，复用时使用通用化中文 `style_prompt_zh`；颗粒、脏污、固定题材和构图不默认继承。相近旧条目通过 `related_entries` 关联，原编号不变；市场定位仅为源作者判断，源页没有明确许可，不套用其他来源CC0说明。\n'
    skill.write_text(text,'utf-8')
    (srcdir/'import-report.json').write_text(json.dumps(dict(date='2026-11-01',url=URL,sha256=hashlib.sha256(raw).hexdigest(),added=[e['id'] for e in entries],images_added=0,backup=str(backup)),ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(dict(total=len(catalog['entries']),added=len(entries),backup=str(backup)),ensure_ascii=False))

if __name__ == '__main__': main()
