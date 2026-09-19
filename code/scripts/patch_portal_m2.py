#!/usr/bin/env python3
"""Portal V5.1：省算面板接入真实埋点（/stats/token-economy）—— 一次性脚本"""
import re
import sys

P = "/Volumes/Macintosh HD/Users/lily/WB_Workspace/2026-09-18-15-33-44/ltc-rag-bot/code/portal/index.html"
h = open(P, encoding="utf-8").read()

START = '<div class="hint" data-page-node-id="9TZUtx0dkvVt4PxWC3uLF8">'
END = '<div class="grid2even" style="margin-bottom:12px" data-page-node-id="zTpMaoFjodGBXstwfbqgfH">'
i, j = h.find(START), h.find(END)
if i < 0 or j < 0 or j < i:
    print("❌ 定位失败", i, j)
    sys.exit(1)

NEW = '''<div class="hint" data-page-node-id="9TZUtx0dkvVt4PxWC3uLF8">4-LAYER FUNNEL · 服务端实测埋点 · LLM 只在最后一层</div>
    <div class="grid4" style="grid-template-columns:repeat(4,1fr);margin:4px 0 8px" data-page-node-id="Z6ZYliA1vLz8DxJwlQCOGH">
      <div class="kpi" style="padding:12px 14px" data-page-node-id="QaDweqkKFDLIR70v3tiWAv"><div class="lb" data-page-node-id="ukW4fPe5YGPjRCzDLUvyRG">L0 精确缓存</div><div class="num c-ok" id="tk-l0" style="font-size:19px" data-page-node-id="SWE364mIBauhuBuUDX1Iq8">— <small data-page-node-id="UB1BAiUD2Mrm2cA8kGIGbZ">tok</small></div><div class="foot" id="tk-l0-foot" data-page-node-id="Dk5MMt5RTslTQR02G5mQbv">同问法直返快照</div></div>
      <div class="kpi" style="padding:12px 14px" data-page-node-id="xITsZGjSB0DcFoUACAHPra"><div class="lb" data-page-node-id="ozHovgUSaE0ElSHHbGrIOy">L1 语义近似</div><div class="num c-ok" id="tk-l1" style="font-size:19px" data-page-node-id="uC5jSxHQPURMVLQiYVasZb">— <small data-page-node-id="a0LWJaAaUDx0Yqim4wp4of">tok</small></div><div class="foot" id="tk-l1-foot" data-page-node-id="QeLeHYgTQ7uSZd6XfwcBHX">换种问法也命中</div></div>
      <div class="kpi" style="padding:12px 14px" data-page-node-id="Ow2SN6zMqD8EzeRxi1UuzG"><div class="lb" data-page-node-id="BXWkU6KZEYs0BkpIY1lMTh">L2 拒答短路</div><div class="num c-warn" id="tk-l2" style="font-size:19px" data-page-node-id="J0Cv0KMcIWHDOpJuDhpxoG">— <small data-page-node-id="0MP3vFgzspcgiuzoxbKvJr">tok</small></div><div class="foot" id="tk-l2-foot" data-page-node-id="tipOPvRyYtv46OlDZZTIgt">低分直接拒答</div></div>
      <div class="kpi dark" style="padding:12px 14px" data-page-node-id="KCJtPkegYKuxkqJ2eDXZaO"><div class="lb" data-page-node-id="V4kiQ4G2fSUhSZQ5Tx8Soy">L3 完整 RAG</div><div class="num c-acc" id="tk-l3" style="font-size:19px" data-page-node-id="onehNXHdt0pHTOtEk3QQPv">— <small data-page-node-id="uD1leYG1PIF2ik4TJS3TeA">tok</small></div><div class="foot" id="llm-cost-foot" data-page-node-id="29XE2d9sUDx87KzJmAdoHd">—</div></div>
    </div>
    <div id="tk-funnel" style="display:flex;height:8px;border-radius:5px;overflow:hidden;background:var(--line)"></div>
    <div class="hint" id="tk-sum" style="margin-top:7px">加载中…</div>
  </div>

  '''
h = h[:i] + NEW + h[j:]

# ── loadSys：拉真实埋点并渲染 ──
OLD_JS = """  document.getElementById('llm-cost-foot').textContent = HEALTH.llm_model ? HEALTH.llm_model + ' · 按量' : '—';
"""
NEW_JS = """  document.getElementById('llm-cost-foot').textContent = HEALTH.llm_model ? HEALTH.llm_model + ' · 按量' : '—';
  renderTokenEconomy();
"""
h, n1 = re.subn(re.escape(OLD_JS), NEW_JS, h, count=1)

FN = r"""
/* ─── 省算漏斗：服务端实测埋点（/stats/token-economy）─── */
async function renderTokenEconomy(){
  let s = null;
  try { s = await api('/stats/token-economy?days=7'); } catch(e){}
  const setNum = (id, txt) => { const el = document.getElementById(id); if (el) el.innerHTML = txt + ' <small>tok</small>'; };
  if (!s) {
    ['tk-l0','tk-l1','tk-l2','tk-l3'].forEach(id => setNum(id, '—'));
    const sum = document.getElementById('tk-sum');
    if (sum) sum.textContent = '埋点未就绪（后端版本 < V5-M2）';
    return;
  }
  const avg = s.avg_tokens_per_llm || 0;
  const q = s.queries_total || 0;
  setNum('tk-l0', '0');
  setNum('tk-l1', '≈0');
  setNum('tk-l2', '0');
  setNum('tk-l3', String(Math.round(avg)));
  document.getElementById('tk-l0-foot').textContent = s.l0_hits + ' 次 · 省 ' + (s.l0_hits*Math.round(avg)) + ' tok';
  document.getElementById('tk-l1-foot').textContent = s.l1_hits + ' 次' + (s.thresholds && s.thresholds.l1_enabled ? '' : ' · 未启用');
  document.getElementById('tk-l2-foot').textContent = s.l2_short + ' 次' + (s.thresholds && s.thresholds.l2_enabled ? '' : ' · 未启用');
  document.getElementById('llm-cost-foot').textContent = s.llm_calls + ' 次真实调用 · ' + (HEALTH.llm_model || 'LLM');

  const seg = [
    { n: s.l0_hits || 0, c: 'var(--ok)',   t: 'L0' },
    { n: s.l1_hits || 0, c: 'var(--acc)',  t: 'L1' },
    { n: s.l2_short || 0, c: 'var(--warn)', t: 'L2' },
    { n: s.l3_full_rag || 0, c: 'var(--bad)', t: 'L3' },
  ];
  const tot = seg.reduce((a, b) => a + b.n, 0) || 1;
  document.getElementById('tk-funnel').innerHTML = seg.map(x =>
    `<div title="${x.t} ${x.n}" style="width:${(x.n/tot*100).toFixed(2)}%;background:${x.c}"></div>`
  ).join('');
  document.getElementById('tk-sum').textContent =
    `近 ${s.range_days} 天：${q} 次提问 → 缓存命中率 ${(s.cache_hit_rate*100).toFixed(1)}% · `
    + `真实调 LLM ${(s.llm_call_rate*100).toFixed(1)}% · 估算省下 ${s.saved_tokens_est} tokens`;
}
async function resetTokenEconomy(){
  if (!confirm('确定清空省算埋点统计？（A/B 实测前归零用，不影响答案缓存）')) return;
  try { await api('/stats/token-economy', { method:'DELETE' }); toast('✓ 埋点已归零'); loadSys(); }
  catch(e){ toast('归零失败','error'); }
}
"""
h, n2 = re.subn(r"\nasync function rebuildEmbed\(\)\{", FN + "\nasync function rebuildEmbed(){", h, count=1)

open(P, "w", encoding="utf-8").write(h)
print(f"✅ 写入完成 · HTML块=1 · renderTokenEconomy调用={n1} · 函数注入={n2}")
