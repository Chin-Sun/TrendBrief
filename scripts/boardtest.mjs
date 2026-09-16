// 新闻板交互回归：用最小 DOM 桩直接跑产物里的真实 JS（没有浏览器也能测）。
// 用法：node boardtest.mjs <index.html>
import fs from 'fs';
const PAGE = process.argv[2] || process.env.PAGE;
if (!PAGE) { console.error('用法: node boardtest.mjs <index.html>'); process.exit(2); }
const html = fs.readFileSync(PAGE, 'utf8');

function classList() { const S = new Set(); return {
  add: c => S.add(c), remove: c => S.delete(c), contains: c => S.has(c),
  toggle: (c, on) => { on === undefined ? (S.has(c) ? S.delete(c) : S.add(c)) : (on ? S.add(c) : S.delete(c)); },
  _s: S }; }
function mk(tag) {
  const e = { tag, children: [], style: {}, attrs: {}, _text: '', _cls: '',
    appendChild(c) { e.children.push(c); return c; },
    setAttribute(k, v) { e.attrs[k] = String(v); }, getAttribute(k) { return e.attrs[k] ?? null; },
    removeAttribute(k) { delete e.attrs[k]; }, select() {},
    get className() { return e._cls; },
    set className(v) { e._cls = v; e.classList._s.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => e.classList._s.add(c)); },
    get textContent() { return e._text || e.children.map(c => c.textContent).join(''); },
    set textContent(v) { e.children.length = 0; e._text = String(v); },
    set innerHTML(v) { e.children.length = 0; e._text = String(v).replace(/<[^>]+>/g, ''); },
    get innerHTML() { return e._text; } };
  e.classList = classList(); return e;
}
const ids = {};
['vBoard','vFavs','favCount','batchSel','prev','next','bmeta','batchbar','board',
 'backup','importBox','doImport','impMsg'].forEach(i => { ids[i] = mk('div'); });
ids.batchSel.value = '1';
ids.payload = mk('script');   ids.payload._text   = html.match(/id="payload">([\s\S]*?)<\/script>/)[1];
ids.summaries = mk('script'); ids.summaries._text = html.match(/id="summaries">([\s\S]*?)<\/script>/)[1];

let THROW = false; const store = new Map();
globalThis.localStorage = {
  getItem(k) { if (THROW) throw new Error('SecurityError'); return store.has(k) ? store.get(k) : null; },
  setItem(k, v) { if (THROW) throw new Error('SecurityError'); store.set(k, v); } };
globalThis.document = { getElementById: i => ids[i] || null, createElement: mk,
  createTextNode: t => { const e = mk('#text'); e._text = t; return e; } };
globalThis.scrollTo = () => {};
const js = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const boot = () => new Function(js)();

let fails = 0;
const ok = (c, m) => { if (!c) { console.log('  ✗ ' + m); fails++; } };
function walk(e, out = []) { if (e.classList && e.classList.contains('story')) out.push(e);
  (e.children || []).forEach(c => walk(c, out)); return out; }
function find(e, cls) { if (e.classList && e.classList.contains(cls)) return e;
  for (const c of e.children || []) { const r = find(c, cls); if (r) return r; } return null; }
const cards = () => walk(ids.board);
const stars = () => cards().map(c => find(c, 'star'));
const titles = () => cards().map(c => { const h = find(c, 'hl'); return h ? h.textContent : ''; });
const sumTexts = () => cards().map(c => { const p = find(c, 'sum'); return p ? p.textContent : ''; });
const D = JSON.parse(ids.payload._text), SUM = JSON.parse(ids.summaries._text), LAST = D.batches.length;
const done = n => D.batches[n - 1].items.every(it => SUM[it.sid]);
const go = n => { ids.batchSel.value = String(n); ids.batchSel.onchange.call(ids.batchSel); };

console.log('① 初始'); boot();
ok(cards().length === 40, '首批应 40 条'); ok(ids.prev.disabled === true, '第 1 批「上一批」应禁用');
ok(ids.bmeta.innerHTML.includes(done(1) ? '摘要 ✓' : '摘要待补'), '批次条摘要状态应与实际一致');
ok(sumTexts().every(t => t.trim()), '每条都应有摘要或「待补」占位');
const pending = [...Array(LAST).keys()].map(i => i + 1).find(n => !done(n));
if (pending) { go(pending);
  ok(ids.bmeta.innerHTML.includes('摘要待补'), `第 ${pending} 批应标「摘要待补」`);
  // 逐条核对：批次可能只有部分条目有摘要（例如从旧页面恢复的），有的显示正文、没有的显示占位
  const items = D.batches[pending - 1].items, texts = sumTexts();
  const wrong = items.filter((it, i) => SUM[it.sid] ? texts[i] !== SUM[it.sid] : !texts[i].includes('摘要待补'));
  ok(wrong.length === 0, `第 ${pending} 批有 ${wrong.length} 条摘要/占位与数据不符`);
  console.log(`   第 ${pending} 批：${items.filter(it => SUM[it.sid]).length} 条有摘要，其余 ${items.filter(it => !SUM[it.sid]).length} 条显示待补`);
  go(1); }
console.log('② 更新按钮走到底'); for (let i = 2; i <= LAST; i++) ids.next.onclick();
ok(Number(ids.batchSel.value) === LAST && ids.next.disabled, `应停在第 ${LAST} 批且禁用`);
ids.next.onclick(); ok(Number(ids.batchSel.value) === LAST, '末批再点不应越界');
console.log('③ 回看'); go(3); const t3 = titles()[0]; ids.prev.onclick();
ok(Number(ids.batchSel.value) === 2 && titles()[0] !== t3, '上一批应到第 2 批且内容不同');
console.log('④ 星标跨批'); go(3);
const a = D.batches[2].items[0].sid, b = D.batches[2].items[35].sid;
stars()[0].onclick(); stars()[35].onclick(); ok(ids.favCount.textContent === '2', '计数应为 2');
ids.next.onclick(); ids.prev.onclick(); ok(stars()[0].classList.contains('on'), '翻走再翻回星标仍在');
ids.vFavs.onclick(); ok(cards().length === 2 && ids.batchbar.style.display === 'none', '收藏夹 2 条且隐藏批次条');
ok(cards().every(c => !!find(c, 'from')), '收藏条目应标注来源批次');
ok(cards().every(c => { const r = find(c, 'rank'); return r && r.textContent.includes('#'); }), '应显示全局排名');
ok(ids.backup.value === JSON.stringify([a, b]), '备份框应含两个 sid');
console.log('⑤ 重载恢复'); boot();
ok(ids.favCount.textContent === '2' && ids.vFavs.classList.contains('on') && Number(ids.batchSel.value) === 3, '应恢复收藏、视图与批次');
console.log('⑥⑦ 取消与导入'); stars()[0].onclick(); ok(ids.favCount.textContent === '1', '取消后应剩 1');
ids.importBox.value = JSON.stringify([a, b, 'hn-nope']); ids.doImport.onclick();
ok(ids.favCount.textContent === '2', '导入后应为 2');
ids.importBox.value = '不是 JSON'; ids.doImport.onclick(); ok(ids.impMsg.textContent.includes('不是合法 JSON'), '非法输入应提示');
console.log('⑧ localStorage 不可用'); THROW = true; store.clear();
try { boot(); ok(cards().length === 40 && ids.favCount.textContent === '0', '存储抛异常仍应渲染首批');
      stars()[0].onclick(); ok(ids.favCount.textContent === '1', '写入失败不应阻断交互'); }
catch (e) { console.log('  ✗ 抛出异常: ' + e.message); fails++; }
console.log(fails ? `❌ ${fails} 项失败` : `✅ 全部通过（${LAST} 批）`); process.exit(fails ? 1 : 0);
