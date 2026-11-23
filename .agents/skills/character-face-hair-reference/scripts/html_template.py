# -*- coding: utf-8 -*-
"""Interactive local HTML workbench generator for character-face-hair-reference."""

import json

_HTML_HEAD = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>人物骨相与发型参考资产库（仅作参考 · 不可直接使用）</title>
<style>
  :root {
    --bg: #0b1120; --surface: #111827; --card: #1e293b; --border: #334155;
    --text: #f1f5f9; --muted: #94a3b8; --accent: #6366f1; --accent2: #0ea5e9;
    --warn-bg: #450a0a; --warn-border: #ef4444; --warn-text: #fecaca;
    --ok: #10b981; --amber: #f59e0b;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: var(--bg); color: var(--text); line-height: 1.55; }
  .red-banner { position: sticky; top: 0; z-index: 50; background: linear-gradient(90deg, #7f1d1d, #991b1b, #7f1d1d); color: #fee2e2; border-bottom: 1px solid #ef4444; padding: 10px 20px; text-align: center; font-weight: 700; font-size: 14px; letter-spacing: 0.3px; }
  header { max-width: 1440px; margin: 0 auto; padding: 22px 24px 10px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-end; gap: 16px; }
  h1 { margin: 0; font-size: 26px; background: linear-gradient(90deg, #e0e7ff, #38bdf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
  .sub { color: var(--muted); font-size: 13.5px; margin-top: 4px; }
  .stats { display: flex; gap: 10px; flex-wrap: wrap; }
  .stat-pill { background: var(--card); border: 1px solid var(--border); padding: 6px 12px; border-radius: 999px; font-size: 12.5px; color: #cbd5e1; }
  .tabs { max-width: 1440px; margin: 12px auto 0; padding: 0 24px; display: flex; gap: 8px; flex-wrap: wrap; border-bottom: 1px solid var(--border); }
  .tab-btn { background: transparent; color: var(--muted); border: 1px solid transparent; border-bottom: none; padding: 11px 18px; border-radius: 10px 10px 0 0; cursor: pointer; font-size: 14px; font-weight: 600; transition: all 0.15s; }
  .tab-btn:hover { color: var(--text); background: #1e293b88; }
  .tab-btn.active { background: var(--surface); color: #fff; border-color: var(--border); box-shadow: inset 0 2px 0 var(--accent); }
  main { max-width: 1440px; margin: 0 auto; padding: 20px 24px 60px; }
  .panel { display: none; }
  .panel.active { display: block; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; align-items: center; }
  .search-input { flex: 1; min-width: 240px; padding: 10px 14px; border-radius: 10px; border: 1px solid var(--border); background: var(--surface); color: var(--text); font-size: 14px; }
  .chip { padding: 7px 13px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface); color: var(--muted); cursor: pointer; font-size: 13px; }
  .chip.active { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(330px, 1fr)); gap: 16px; }
  .grid-2 { display: grid; grid-template-columns: repeat(auto-fill, minmax(460px, 1fr)); gap: 18px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; overflow: hidden; display: flex; flex-direction: column; transition: border-color 0.15s, transform 0.15s; }
  .card:hover { border-color: var(--accent2); transform: translateY(-2px); }
  .card-media { background: #0f172a; padding: 10px; display: flex; justify-content: center; align-items: center; border-bottom: 1px solid var(--border); position: relative; cursor: zoom-in; }
  .card-media img { max-width: 100%; max-height: 220px; object-fit: contain; border-radius: 8px; }
  .card-media.anatomy-bg { background: #f8fafc; min-height: 240px; }
  .card-media.anatomy-bg img { max-height: 250px; }
  .card-body { padding: 14px 16px; flex: 1; display: flex; flex-direction: column; gap: 8px; }
  .card-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 11.5px; font-weight: 700; font-family: monospace; background: #312e81; color: #c7d2fe; }
  .badge-warn { background: var(--warn-bg); color: var(--warn-text); border: 1px solid #ef444466; font-family: inherit; font-weight: 600; }
  .badge-hist { background: #78350f; color: #fde68a; font-family: inherit; }
  .badge-link { background: #0f172a; color: #38bdf8; border: 1px solid #0284c766; cursor: pointer; font-family: monospace; }
  .badge-link:hover { background: #0284c7; color: #fff; }
  .card-title { font-size: 16px; font-weight: 700; margin: 2px 0; }
  .card-sub { font-size: 12px; color: var(--muted); }
  .meta-box { background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 8px 10px; font-size: 12.5px; }
  .meta-box b { color: #cbd5e1; }
  .prompt-ref { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; color: #93c5fd; background: #090d16; padding: 7px 9px; border-radius: 6px; border: 1px dashed #334155; word-break: break-word; }
  .card-actions { display: flex; gap: 8px; margin-top: auto; padding-top: 8px; }
  .btn { padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--card); color: var(--text); cursor: pointer; font-size: 12.5px; font-weight: 600; }
  .btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn-primary:hover { filter: brightness(1.1); }
  .btn:hover { border-color: var(--accent2); }
  .formula-hero { display: grid; grid-template-columns: 1.25fr 1fr; gap: 20px; margin-bottom: 20px; }
  @media (max-width: 960px) { .formula-hero, .workbench-layout { grid-template-columns: 1fr !important; } }
  .box { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; padding: 18px; }
  .formula-eq { background: #0f172a; border-left: 4px solid var(--accent); padding: 12px 14px; border-radius: 8px; font-family: ui-monospace, Consolas, monospace; font-size: 13.5px; color: #e0e7ff; margin: 12px 0; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { border: 1px solid var(--border); padding: 9px 10px; text-align: left; vertical-align: top; }
  th { background: #0f172a; color: #cbd5e1; }
  .workbench-layout { display: grid; grid-template-columns: 1fr 1.35fr; gap: 20px; align-items: start; }
  .field { margin-bottom: 12px; }
  .field label { display: block; font-size: 13px; font-weight: 700; margin-bottom: 4px; color: #e2e8f0; }
  .field small { display: block; color: var(--muted); font-size: 11.5px; margin-bottom: 5px; }
  .field textarea, .field input { width: 100%; background: #0f172a; color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 9px 11px; font-size: 13.5px; font-family: inherit; }
  .field textarea { min-height: 58px; resize: vertical; }
  .output-pre { background: #090d16; border: 1px solid var(--accent); border-radius: 10px; padding: 14px; white-space: pre-wrap; font-size: 13.5px; color: #e2e8f0; min-height: 120px; }
  .lightbox { display: none; position: fixed; inset: 0; z-index: 100; background: rgba(2,6,23,0.92); justify-content: center; align-items: center; padding: 24px; flex-direction: column; gap: 12px; }
  .lightbox.open { display: flex; }
  .lightbox img { max-width: 92vw; max-height: 82vh; background: #fff; border-radius: 10px; padding: 8px; }
  .lightbox-cap { color: #e2e8f0; font-size: 14px; text-align: center; max-width: 820px; }
  .toast { position: fixed; bottom: 24px; right: 24px; background: var(--ok); color: #052e16; font-weight: 700; padding: 10px 16px; border-radius: 10px; display: none; z-index: 120; }
</style>
</head>
<body>
<div class="red-banner">⚠️ 严格标注：本库所有 7 维公式示例、开源颅面解剖图（A001–A007）、人物骨相（B001–B024）与发型（H001–H042）均【仅作参考，不可直接使用】！必须结合剧本按 7 维模板原创改写。</div>
<header>
  <div>
    <h1>人物骨相与发型参考资产库 · 7 维公式工作台</h1>
    <div class="sub">character-face-hair-reference · 真人与动画通用 · 开源解剖骨相原图联动 · 防同脸差异化</div>
  </div>
  <div class="stats" id="headerStats"></div>
</header>
<nav class="tabs">
  <button class="tab-btn active" data-tab="formula">📐 7 维人物设计公式</button>
  <button class="tab-btn" data-tab="anatomy">💀 颅面骨相原图 (A001–A007)</button>
  <button class="tab-btn" data-tab="bones">🦴 人物骨相参考库 (B001–B024)</button>
  <button class="tab-btn" data-tab="hairs">💇 人物发型参考库 (H001–H042)</button>
  <button class="tab-btn" data-tab="workbench">✍️ 7 维原创草稿工作台</button>
</nav>
<main>
  <section id="tab-formula" class="panel active"></section>
  <section id="tab-anatomy" class="panel"></section>
  <section id="tab-bones" class="panel"></section>
  <section id="tab-hairs" class="panel"></section>
  <section id="tab-workbench" class="panel"></section>
</main>
<div class="lightbox" id="lightbox" onclick="closeLightbox()">
  <img id="lbImg" src="" alt="" />
  <div class="lightbox-cap" id="lbCap"></div>
  <div style="color:#94a3b8;font-size:12px;">点击任意处或按 ESC 关闭</div>
</div>
<div class="toast" id="toast"></div>
'''


_HTML_SCRIPT_PART1 = r'''
const state = {
  boneGroup: 'ALL', boneQuery: '',
  hairGroup: 'ALL', hairQuery: '',
  pickedBone: null, pickedHair: null
};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}
function jsArg(s) {
  return esc(JSON.stringify(String(s ?? '')));
}
function normText(s) {
  return String(s ?? '').toLowerCase().replace(/\s+/g, ' ').trim();
}
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.display = 'block';
  clearTimeout(t._tid); t._tid = setTimeout(() => t.style.display = 'none', 2200);
}
function copyText(txt, label='已复制') {
  navigator.clipboard.writeText(txt).then(() => toast(label)).catch(() => {
    const ta = document.createElement('textarea'); ta.value = txt; document.body.appendChild(ta);
    ta.select(); document.execCommand('copy'); ta.remove(); toast(label);
  });
}
function copyBlankTemplate() {
  const blankTpl = DATA.formula_7dim.dimensions.map(d => `[${d.index}. ${d.name}]：`).join('\n') + '\n[其他要求（如有）]：';
  copyText(blankTpl, '已复制 7 维空白填写模板');
}
function openLightbox(src, cap) {
  document.getElementById('lbImg').src = src;
  document.getElementById('lbCap').textContent = cap;
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox() { document.getElementById('lightbox').classList.remove('open'); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === 'tab-' + tab));
}
document.querySelectorAll('.tab-btn').forEach(b => b.addEventListener('click', () => switchTab(b.dataset.tab)));

function jumpToBone(bid) {
  state.boneGroup = 'ALL'; state.boneQuery = bid;
  switchTab('bones');
  const inp = document.getElementById('boneSearchInput');
  if (inp) inp.value = bid;
  renderBonesList();
}
function resetBoneFilter() {
  state.boneGroup = 'ALL'; state.boneQuery = '';
  const inp = document.getElementById('boneSearchInput');
  if (inp) { inp.value = ''; inp.focus(); }
  renderBonesList();
}
function resetHairFilter() {
  state.hairGroup = 'ALL'; state.hairQuery = '';
  const inp = document.getElementById('hairSearchInput');
  if (inp) { inp.value = ''; inp.focus(); }
  renderHairsList();
}
function jumpToAnatomy(aid) {
  switchTab('anatomy');
  const el = document.getElementById('card-' + aid);
  if (el) el.scrollIntoView({behavior: 'smooth', block: 'center'});
}
function pickForWorkbench(kind, id) {
  if (kind === 'bone') state.pickedBone = DATA.bones.find(x => x.id === id) || null;
  if (kind === 'hair') state.pickedHair = DATA.hairs.find(x => x.id === id) || null;
  switchTab('workbench'); renderWorkbenchRef(); updateWorkbenchOutput();
  toast(`已载入 ${id} 作为灵感参考（严禁照抄，请右侧原创改写）`);
}

function renderFormula() {
  const f = DATA.formula_7dim;
  const rows = f.dimensions.map(d => `
    <tr>
      <td><b>${d.index}. ${esc(d.name)}</b><div style="color:#94a3b8;font-size:12px;">${esc(d.role)}</div></td>
      <td><code>${esc(d.sub_formula)}</code>${d.linked_library ? `<div style="margin-top:4px;"><span class="badge">联动：${esc(d.linked_library)}</span></div>` : ''}</td>
      <td>${d.examples_ref_only.map(x => `<div style="margin-bottom:4px;">• ${esc(x)}</div>`).join('')}<span class="badge badge-warn">仅作参考示例</span></td>
    </tr>`).join('');
  document.getElementById('tab-formula').innerHTML = `
    <div class="formula-hero">
      <div class="box">
        <span class="badge badge-warn">⚠️ 仅作参考 · 不可直接使用</span>
        <h2 style="margin:8px 0 6px;">${esc(f.title)}</h2>
        <div class="formula-eq">${esc(f.overall_formula)}</div>
        <p style="font-size:13.5px;color:#cbd5e1;">使用规范：先从【颅面骨相原图】确认解剖结构与比例，再按需在【人物骨相库 B001–B024】与【人物发型库 H001–H042】获取维度灵感，最后进入【7 维原创草稿工作台】结合剧本时代、年龄与画风原创改写。</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;">
          <button class="btn btn-primary" onclick="copyBlankTemplate()">📋 复制 7 维空白模板</button>
          <button class="btn" onclick="switchTab('anatomy')">💀 查看开源骨相解剖图</button>
          <button class="btn" onclick="switchTab('workbench')">✍️ 进入 7 维原创工作台</button>
        </div>
      </div>
      <div class="box" style="text-align:center;">
        <div style="font-size:12.5px;color:#94a3b8;margin-bottom:8px;">原图公式参考（点击放大）</div>
        <img src="${esc(f.reference_image)}" alt="7维人物设计公式参考图" style="max-width:100%;max-height:260px;border-radius:10px;cursor:zoom-in;border:1px solid #334155;"
          onclick="openLightbox(${jsArg(f.reference_image)}, ${jsArg('7 维人物设计公式原图（仅作结构参考，不可直接照搬）')})" />
      </div>
    </div>
    <div class="box">
      <h3 style="margin-top:0;">7 维细分公式明细表</h3>
      <table><thead><tr><th style="width:19%;">维度与作用</th><th style="width:36%;">细分公式骨架</th><th>参考举例（不可直接照搬）</th></tr></thead><tbody>${rows}</tbody></table>
    </div>`;
}

function renderAnatomy() {
  const cards = DATA.anatomy_plates.map(a => `
    <div class="card" id="card-${esc(a.id)}">
      <div class="card-media anatomy-bg" onclick="openLightbox(${jsArg(a.file)}, ${jsArg(`${a.id} ${a.name} · 协议：${a.license} · ⚠️ 仅作解剖参考`)})">
        <img src="${esc(a.file)}" alt="${esc(a.name)}" loading="lazy" />
      </div>
      <div class="card-body">
        <div class="card-head">
          <div><span class="badge">${esc(a.id)}</span> <span class="badge">${esc(a.view)}</span></div>
          <span class="badge badge-warn">仅作解剖参考</span>
        </div>
        <div class="card-title">${esc(a.name)}</div>
        <div class="card-sub">${esc(a.en_name)} · 协议：${esc(a.license)}</div>
        <div class="meta-box"><b>映射 7 维公式：</b>${esc(a.formula_link.dimension)}<br><b>对应槽位：</b>${a.formula_link.slots.map(esc).join(' / ')}<br><b>用法说明：</b>${esc(a.formula_link.how_to_use)}</div>
        <div class="meta-box"><b>关键骨点映射：</b>${a.mapped_landmarks.map(m => `<div style="margin-top:3px;">• <b>${esc(m.landmark)}</b> → ${esc(m.slot)}</div>`).join('')}</div>
        <div style="display:flex;gap:5px;flex-wrap:wrap;margin-top:4px;">
          <span style="font-size:12px;color:#94a3b8;align-self:center;">关联骨相：</span>
          ${a.linked_bones.map(b => `<button class="badge badge-link" onclick="jumpToBone(${jsArg(b)})">${esc(b)}</button>`).join('')}
        </div>
      </div>
    </div>`).join('');
  const ext = DATA.external_sources.map(s => `<div class="meta-box" style="margin-bottom:8px;"><b>${esc(s.group)}</b><br>检索词：<code>${s.keywords.map(esc).join('</code> | <code>')}</code><br><span style="color:#94a3b8;">用途：${esc(s.purpose)}</span></div>`).join('');
  document.getElementById('tab-anatomy').innerHTML = `
    <div class="grid-2">${cards}</div>
    <div class="box" style="margin-top:20px;"><h3 style="margin-top:0;">外部 3D / 2D 扩展参考源检索词（按需在线查阅）</h3>${ext}</div>`;
}

function boneSearchText(b) {
  const s = b.formula_slots_ref_only || {};
  return [
    b.id, b.group, b.name, b.en_name, b.aesthetic_tier,
    ...(b.anatomical_landmarks || []), ...(b.suitable_roles || []),
    ...(b.linked_anatomy_plates || []),
    s.eye, s.brow, s.nose, s.lips, s.jaw,
    b.prompt_zh_ref_only, b.prompt_en_ref_only, b.anti_copy_note
  ].join(' ').toLowerCase();
}

function initBones() {
  document.getElementById('tab-bones').innerHTML = `
    <div class="toolbar">
      <input id="boneSearchInput" class="search-input" placeholder="搜索编号/骨相/人设/英文词，如 B018、谋士、猫系、下颌角..." value="${esc(state.boneQuery)}" oninput="state.boneQuery=this.value;renderBonesList();" />
      <button class="btn" onclick="resetBoneFilter()">重置</button>
      <div id="bonesChips" style="display:flex;flex-wrap:wrap;gap:8px;"></div>
    </div>
    <div id="bonesGrid" class="grid"></div>`;
  renderBonesList();
}

function renderBonesList() {
  const groups = ['ALL', ...new Set(DATA.bones.map(x => x.group))];
  const q = state.boneQuery.trim().toLowerCase();
  const list = DATA.bones.filter(b => {
    if (state.boneGroup !== 'ALL' && b.group !== state.boneGroup) return false;
    if (!q) return true;
    return boneSearchText(b).includes(q);
  });
  document.getElementById('bonesChips').innerHTML = groups.map(g =>
    `<button class="chip ${state.boneGroup===g?'active':''}" onclick="state.boneGroup=${jsArg(g)};renderBonesList();">${g==='ALL'?'全部 ('+DATA.bones.length+')':esc(g)}</button>`
  ).join('');
  const cards = list.map(b => `
    <div class="card">
      <div class="card-media" onclick="openLightbox(${jsArg(b.svg_file)}, ${jsArg(`${b.id} ${b.name}（仅作骨相结构参考，不可直接使用）`)})">
        <img src="${esc(b.svg_file)}" alt="${esc(b.name)}" loading="lazy" />
      </div>
      <div class="card-body">
        <div class="card-head">
          <div><span class="badge">${esc(b.id)}</span> ${b.group.includes('历史')?'<span class="badge badge-hist">历史写实·禁网红脸</span>':'<span class="badge">'+esc(b.aesthetic_tier)+'</span>'}</div>
          <span class="badge badge-warn">参考不可直用</span>
        </div>
        <div class="card-title">${esc(b.name)}</div>
        <div class="card-sub">${esc(b.en_name)} · 下颌折角≈${b.proportions.jaw_angle_deg}° · 三庭 ${Math.round(b.proportions.upper*100)}/${Math.round(b.proportions.mid*100)}/${Math.round(b.proportions.lower*100)}</div>
        <div class="meta-box">
          <b>关键骨点：</b>${b.anatomical_landmarks.map(esc).join('、')}<br>
          <b>适用人设：</b>${b.suitable_roles.map(esc).join(' / ')}<br>
          <b>防照搬提醒：</b><span style="color:#fcd34d;">${esc(b.anti_copy_note)}</span>
        </div>
        <div class="prompt-ref">参考英文词汇启发：${esc(b.prompt_en_ref_only)}</div>
        <div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center;">
          <span style="font-size:12px;color:#94a3b8;">对应解剖图：</span>
          ${b.linked_anatomy_plates.map(a => `<button class="badge badge-link" onclick="jumpToAnatomy(${jsArg(a)})">${esc(a)}</button>`).join('')}
        </div>
        <div class="card-actions">
          <button class="btn btn-primary" onclick="pickForWorkbench('bone',${jsArg(b.id)})">✍️ 带入工作台作参考</button>
        </div>
      </div>
    </div>`).join('');
  document.getElementById('bonesGrid').innerHTML = cards || '<div class="box">无匹配骨相条目</div>';
}
'''


_HTML_SCRIPT_PART2 = r'''
function hairSearchText(h) {
  const s = h.formula_slots_ref_only || {};
  return [
    h.id, h.group, h.gender, h.era, h.name, h.en_name,
    h.silhouette_features, ...(h.suggested_bones || []),
    s.color, s.texture, s.noun, s.motion,
    h.prompt_zh_ref_only, h.prompt_en_ref_only
  ].join(' ').toLowerCase();
}

function initHairs() {
  const colors = DATA.hair_color_modifiers.map(c => `<tr><td>${esc(c.category)}</td><td><b>${esc(c.zh)}</b></td><td><code>${esc(c.en)}</code></td><td>${esc(c.note)}</td></tr>`).join('');
  document.getElementById('tab-hairs').innerHTML = `
    <div class="toolbar">
      <input id="hairSearchInput" class="search-input" placeholder="搜索编号/发型/时代/英文词，如 H015、堕马髻、束冠、狼尾..." value="${esc(state.hairQuery)}" oninput="state.hairQuery=this.value;renderHairsList();" />
      <button class="btn" onclick="resetHairFilter()">重置</button>
      <div id="hairsChips" style="display:flex;flex-wrap:wrap;gap:8px;"></div>
    </div>
    <div id="hairsGrid" class="grid"></div>
    <div class="box" style="margin-top:20px;">
      <h3 style="margin-top:0;">发色与层次修饰词对照表（映射公式第 5 维 [具体发色]）</h3>
      <table><thead><tr><th>分类</th><th>中文参考词</th><th>英文参考词</th><th>适用场景</th></tr></thead><tbody>${colors}</tbody></table>
    </div>`;
  renderHairsList();
}

function renderHairsList() {
  const groups = ['ALL', ...new Set(DATA.hairs.map(x => x.group))];
  const q = state.hairQuery.trim().toLowerCase();
  const list = DATA.hairs.filter(h => {
    if (state.hairGroup !== 'ALL' && h.group !== state.hairGroup) return false;
    if (!q) return true;
    return hairSearchText(h).includes(q);
  });
  document.getElementById('hairsChips').innerHTML = groups.map(g =>
    `<button class="chip ${state.hairGroup===g?'active':''}" onclick="state.hairGroup=${jsArg(g)};renderHairsList();">${g==='ALL'?'全部 ('+DATA.hairs.length+')':esc(g)}</button>`
  ).join('');
  const cards = list.map(h => `
    <div class="card">
      <div class="card-media" onclick="openLightbox(${jsArg(h.svg_file)}, ${jsArg(`${h.id} ${h.name}（仅作发型结构参考，不可直接使用）`)})">
        <img src="${esc(h.svg_file)}" alt="${esc(h.name)}" loading="lazy" />
      </div>
      <div class="card-body">
        <div class="card-head">
          <div><span class="badge">${esc(h.id)}</span> <span class="badge">${esc(h.gender)} · ${esc(h.era)}</span></div>
          <span class="badge badge-warn">参考不可直用</span>
        </div>
        <div class="card-title">${esc(h.name)}</div>
        <div class="card-sub">${esc(h.en_name)}</div>
        <div class="meta-box">
          <b>剪影特征：</b>${esc(h.silhouette_features)}<br>
          <b>质感与动势参考：</b>${esc(h.formula_slots_ref_only.texture)}；${esc(h.formula_slots_ref_only.motion)}
        </div>
        <div class="prompt-ref">参考英文词汇启发：${esc(h.prompt_en_ref_only)}</div>
        <div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center;">
          <span style="font-size:12px;color:#94a3b8;">推荐搭配骨相：</span>
          ${h.suggested_bones.map(b => `<button class="badge badge-link" onclick="jumpToBone(${jsArg(b)})">${esc(b)}</button>`).join('')}
        </div>
        <div class="card-actions">
          <button class="btn btn-primary" onclick="pickForWorkbench('hair',${jsArg(h.id)})">✍️ 带入工作台作参考</button>
        </div>
      </div>
    </div>`).join('');
  document.getElementById('hairsGrid').innerHTML = cards || '<div class="box">无匹配发型条目</div>';
}

function initWorkbench() {
  const fields = DATA.formula_7dim.dimensions.map(d => `
    <div class="field">
      <label>${d.index}. [${esc(d.name)}]</label>
      <small>公式骨架：${esc(d.sub_formula)}</small>
      <textarea id="wb_${d.index}" placeholder="请结合当前剧本角色原创改写，严禁直接粘贴参考库示例..." oninput="updateWorkbenchOutput()"></textarea>
    </div>`).join('');
  document.getElementById('tab-workbench').innerHTML = `
    <div class="workbench-layout">
      <div class="box">
        <span class="badge badge-warn">⚠️ 参考灵感区（严禁照抄）</span>
        <h3 style="margin:8px 0 10px;">当前载入的参考条目</h3>
        <div id="wbRefPane"></div>
      </div>
      <div class="box">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px;">
          <h3 style="margin:0;">7 维人物设计原创草稿填写区</h3>
          <div style="display:flex;gap:8px;">
            <button class="btn" onclick="clearWorkbench()">清空草稿</button>
            <button class="btn btn-primary" onclick="copyWorkbench()">📋 复制 7 维完整设定</button>
          </div>
        </div>
        ${fields}
        <div class="field">
          <label>8. [其他要求（如有）]</label>
          <input id="wb_8" placeholder="如：朝右 3/4 设定卡、免冠露额、历史写实禁网红滤镜、保留真实毛孔..." oninput="updateWorkbenchOutput()" />
        </div>
        <div id="wbWarn" style="display:none;background:#450a0a;border:1px solid #ef4444;color:#fecaca;padding:8px 12px;border-radius:8px;font-size:12.5px;margin-bottom:10px;"></div>
        <h4 style="margin:10px 0 6px;">实时合成 7 维角色描述预览</h4>
        <div class="output-pre" id="wbOut">（请在上方逐维原创填写，系统将自动按 7 维顺序拼接）</div>
      </div>
    </div>`;
  renderWorkbenchRef();
}

function renderWorkbenchRef() {
  const pane = document.getElementById('wbRefPane');
  if (!pane) return;
  const b = state.pickedBone, h = state.pickedHair;
  let html = '';
  if (b) {
    const plates = b.linked_anatomy_plates.map(aid => DATA.anatomy_plates.find(p => p.id === aid)).filter(Boolean);
    html += `<div class="meta-box" style="margin-bottom:12px;">
      <div style="display:flex;justify-content:space-between;align-items:center;"><b>🦴 参考骨相：${esc(b.id)} ${esc(b.name)}</b><button class="btn" style="padding:3px 8px;" onclick="state.pickedBone=null;renderWorkbenchRef();updateWorkbenchOutput();">移除</button></div>
      <img src="${esc(b.svg_file)}" style="width:100%;max-width:300px;margin:8px 0;border-radius:8px;display:block;" />
      <div><b>第 3 维拆分启发：</b><br>• 眼：${esc(b.formula_slots_ref_only.eye)}<br>• 眉：${esc(b.formula_slots_ref_only.brow)}<br>• 鼻：${esc(b.formula_slots_ref_only.nose)}<br>• 唇：${esc(b.formula_slots_ref_only.lips)}<br>• 颌：${esc(b.formula_slots_ref_only.jaw)}</div>
      <div style="margin-top:6px;color:#fcd34d;"><b>防照搬提醒：</b>${esc(b.anti_copy_note)}</div>
      <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap;">${plates.map(p => `<button class="badge badge-link" onclick="openLightbox(${jsArg(p.file)},${jsArg(`${p.id} ${p.name}`)})">查看解剖图 ${esc(p.id)}</button>`).join('')}</div>
    </div>`;
  } else {
    html += `<div class="meta-box" style="margin-bottom:12px;">尚未载入骨相参考。<br><button class="btn" style="margin-top:8px;" onclick="switchTab('bones')">去挑选参考骨相 (B001–B024)</button></div>`;
  }
  if (h) {
    html += `<div class="meta-box">
      <div style="display:flex;justify-content:space-between;align-items:center;"><b>💇 参考发型：${esc(h.id)} ${esc(h.name)}</b><button class="btn" style="padding:3px 8px;" onclick="state.pickedHair=null;renderWorkbenchRef();updateWorkbenchOutput();">移除</button></div>
      <img src="${esc(h.svg_file)}" style="width:100%;max-width:300px;margin:8px 0;border-radius:8px;display:block;" />
      <div><b>第 5 维拆分启发：</b><br>• 发色灵感：${esc(h.formula_slots_ref_only.color)}<br>• 质感状态：${esc(h.formula_slots_ref_only.texture)}<br>• 发型名词：${esc(h.formula_slots_ref_only.noun)}<br>• 动静补充：${esc(h.formula_slots_ref_only.motion)}</div>
    </div>`;
  } else {
    html += `<div class="meta-box">尚未载入发型参考。<br><button class="btn" style="margin-top:8px;" onclick="switchTab('hairs')">去挑选参考发型 (H001–H042)</button></div>`;
  }
  pane.innerHTML = html;
}

function updateWorkbenchOutput() {
  const parts = [];
  for (let i = 1; i <= 7; i++) {
    const v = (document.getElementById('wb_' + i)?.value || '').trim();
    if (v) parts.push(`[${i}. ${DATA.formula_7dim.dimensions[i-1].name}] ${v}`);
  }
  const extra = (document.getElementById('wb_8')?.value || '').trim();
  if (extra) parts.push(`[其他要求] ${extra}`);
  const out = parts.join('\n');
  document.getElementById('wbOut').textContent = out || '（请在上方逐维原创填写，系统将自动按 7 维顺序拼接）';
  const warn = document.getElementById('wbWarn');
  const normOut = normText(out);
  const refsToCheck = [];
  if (state.pickedBone) refsToCheck.push(state.pickedBone.prompt_zh_ref_only, state.pickedBone.prompt_en_ref_only);
  if (state.pickedHair) refsToCheck.push(state.pickedHair.prompt_zh_ref_only, state.pickedHair.prompt_en_ref_only);
  const copied = refsToCheck.some(r => {
    const nr = normText(r);
    return nr.length >= 8 && normOut.includes(nr);
  });
  warn.style.display = copied ? 'block' : 'none';
  if (copied) warn.textContent = '⚠️ 检测到草稿中直接照搬了参考库整句中/英文参考词！请按本剧时代、年龄与身份重新原创改写。';
}
function clearWorkbench() {
  for (let i = 1; i <= 8; i++) { const el = document.getElementById('wb_' + i); if (el) el.value = ''; }
  updateWorkbenchOutput();
}
function copyWorkbench() {
  const txt = document.getElementById('wbOut').textContent;
  if (!txt || txt.startsWith('（请')) { toast('请先填写至少一个维度'); return; }
  copyText(txt, '已复制 7 维原创角色草稿');
}

document.getElementById('headerStats').innerHTML = `
  <span class="stat-pill">📐 7 维通用公式</span>
  <span class="stat-pill">💀 开源解剖图 ${DATA.anatomy_plates.length} 张</span>
  <span class="stat-pill">🦴 骨相参考 ${DATA.bones.length} 套</span>
  <span class="stat-pill">💇 发型参考 ${DATA.hairs.length} 套</span>`;
renderFormula(); renderAnatomy(); initBones(); initHairs(); initWorkbench();
'''


def render_html(catalog: dict) -> str:
    payload = json.dumps(catalog, ensure_ascii=False).replace("</", "<\\/")
    return (
        _HTML_HEAD
        + "\n<script>\nconst DATA = "
        + payload
        + ";\n"
        + _HTML_SCRIPT_PART1
        + "\n"
        + _HTML_SCRIPT_PART2
        + "\n</script>\n</body>\n</html>\n"
    )
