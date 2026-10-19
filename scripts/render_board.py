#!/usr/bin/env python3
"""
render_board.py — 把 board_data.json + summaries.json 渲染成「新闻板」单文件页面

    python3 render_board.py board_data.json summaries.json -o daily_bull.html

默认输出 **Artifact 片段**（无 <!DOCTYPE>/<html>/<head>/<body>，发布时由平台包外壳）。
加 --standalone 则包成完整文档，供本地 file:// 直接打开。

页面把全部批次数据内嵌成 JSON，只把当前视图渲染进 DOM —— 560 条全渲染成 DOM 太笨重，
而且收藏夹要跨批次取条目，用同一份数组过滤才自然。

只依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone

WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"]
LOGO = "🐮"

CSS = """
/* 报纸榜单：暖调新闻纸底 + 右侧数据栏（榜单本质是一张行情表，故用窄体无衬线排数字）
   浅色是基准调色板；深色只重定义令牌，组件一律走令牌，不在媒体查询里直接写颜色。 */
:root{
  --paper:#faf8f4; --ink:#16130f; --ink-soft:#3a352e; --muted:#6f675c;
  --rule:#ded7cb; --rule-strong:#16130f; --tint:#f1ece2;
  --link:#2f5d7c; --star:#b07d12; --pending:#9a6b3f; --focus:#2f5d7c;
  --display:"Newsreader",Georgia,"Times New Roman","Songti SC","Noto Serif SC",serif;
  --serif:"Newsreader","Songti SC","Noto Serif SC","Source Han Serif SC",Georgia,serif;
  --agate:"IBM Plex Sans Condensed","Helvetica Neue",Helvetica,Arial,
          "PingFang SC","Hiragino Sans GB",sans-serif;
  --measure:66ch;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#14120f; --ink:#f0ece4; --ink-soft:#ded8ce; --muted:#9a9186;
    --rule:#332e27; --rule-strong:#f0ece4; --tint:#1e1b16;
    --link:#7fa9c6; --star:#dfae4a; --pending:#c08f5c; --focus:#7fa9c6;
  }
}
:root[data-theme="dark"]{
  --paper:#14120f; --ink:#f0ece4; --ink-soft:#ded8ce; --muted:#9a9186;
  --rule:#332e27; --rule-strong:#f0ece4; --tint:#1e1b16;
  --link:#7fa9c6; --star:#dfae4a; --pending:#c08f5c; --focus:#7fa9c6;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
     font-family:var(--serif);-webkit-font-smoothing:antialiased}
a{color:inherit}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.wrap{max-width:1040px;margin:0 auto;padding:0 26px 76px}

/* ---------- 报头 ---------- */
.masthead{padding-top:24px}
.dateline{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
  font-family:var(--agate);font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);border-bottom:1px solid var(--rule);padding-bottom:9px;flex-wrap:wrap;
  font-variant-numeric:tabular-nums}
.plate{display:flex;align-items:center;justify-content:center;gap:18px;margin:20px 0 10px}
.logo{font-size:clamp(32px,6.5vw,58px);line-height:1}
.nameplate{font-family:"UnifrakturMaguntia",var(--display);font-weight:400;
  font-size:clamp(34px,7.5vw,68px);line-height:1.04;margin:0;letter-spacing:.01em;
  text-wrap:balance}
.tagline{text-align:center;font-family:var(--agate);font-size:11.5px;letter-spacing:.2em;
  text-transform:uppercase;color:var(--muted);padding-bottom:14px}
.rule-double{border-top:3px double var(--rule-strong)}

/* ---------- 工具条 ---------- */
.toolbar,.batchbar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;
  padding:11px 0;border-bottom:1px solid var(--rule)}
.toolbar{justify-content:space-between}
.tools{display:flex;gap:8px;flex-wrap:wrap}
button,select{font-family:var(--agate);font-size:12px;letter-spacing:.09em;
  text-transform:uppercase;background:none;border:1px solid var(--rule);
  color:var(--muted);padding:7px 14px;border-radius:1px;cursor:pointer}
select{text-transform:none;letter-spacing:0;padding:6px 8px;color:var(--ink);
  font-variant-numeric:tabular-nums}
button:hover:not(:disabled){border-color:var(--rule-strong);color:var(--ink)}
button:disabled{opacity:.4;cursor:not-allowed}
button.on{background:var(--rule-strong);color:var(--paper);border-color:var(--rule-strong)}
#next{border-color:var(--rule-strong);color:var(--ink);font-weight:600}
#next:disabled{border-color:var(--rule);color:var(--muted);font-weight:400}
.cnt{font-family:var(--display);font-style:italic;text-transform:none;letter-spacing:0;
  margin-left:5px;font-variant-numeric:tabular-nums}
.sib{font-family:var(--agate);font-size:12px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--link);text-decoration:none;border:1px solid transparent;padding:7px 4px}
.sib:hover{border-bottom-color:var(--link)}
.bmeta{font-family:var(--agate);font-size:12px;color:var(--muted);letter-spacing:.05em;
  font-variant-numeric:tabular-nums}
.bmeta b{color:var(--ink);font-weight:600}
.warn{color:var(--pending)}

/* ---------- 版块 ---------- */
section{margin-top:40px}
.sec-head{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
  border-bottom:2px solid var(--rule-strong);padding-bottom:7px;flex-wrap:wrap}
.sec-head h2{font-family:var(--display);font-size:24px;font-weight:600;margin:0;
  letter-spacing:-.01em}
.sec-head .k{font-family:var(--agate);font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--muted);font-variant-numeric:tabular-nums}

/* ---------- 条目：星标 | 正文（限行宽） | 数据栏 ---------- */
.story{display:grid;grid-template-columns:30px minmax(0,1fr) 132px;gap:0 18px;
  padding:20px 0;border-bottom:1px solid var(--rule)}
.star{grid-row:1/span 4;align-self:start;background:none;border:none;padding:1px 0 0;
  font-size:19px;line-height:1;color:var(--rule);cursor:pointer;text-transform:none}
.star:hover{color:var(--star)}
.star.on{color:var(--star)}
.body{grid-column:2;max-width:var(--measure)}
.kicker{font-family:var(--agate);font-size:11px;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted);margin-bottom:6px}
.hl{font-family:var(--display);font-size:20px;font-weight:600;line-height:1.32;
  margin:0 0 8px;letter-spacing:-.008em;text-wrap:balance}
.hl a{text-decoration:none}
.hl a:hover{text-decoration:underline;text-underline-offset:3px}
.sum{font-size:15px;line-height:1.8;color:var(--ink-soft);margin:0 0 11px;
  text-align:justify;text-justify:inter-ideograph}
.sum.todo{color:var(--pending);font-style:italic;text-align:left}
.links{font-family:var(--agate);font-size:12px;letter-spacing:.05em;
  display:flex;gap:16px;flex-wrap:wrap}
.links a{color:var(--link);text-decoration:none;border-bottom:1px solid transparent}
.links a:hover{border-bottom-color:var(--link)}
/* 右侧数据栏：排名与指标各占一行，等宽数字对齐成一列 */
.rail{grid-column:3;grid-row:1/span 4;font-family:var(--agate);font-size:12px;
  color:var(--muted);text-align:right;font-variant-numeric:tabular-nums;
  border-left:1px solid var(--rule);padding-left:14px;align-self:start}
.rail .rank{font-family:var(--display);font-size:22px;font-weight:600;color:var(--ink);
  line-height:1;display:block;margin-bottom:7px}
.rail .rank small{font-size:11px;font-family:var(--agate);font-weight:400;
  color:var(--muted);letter-spacing:.1em;display:block;margin-bottom:3px;
  text-transform:uppercase}
.rail div{margin-top:3px}
.rail b{color:var(--ink-soft);font-weight:600}
.from{font-family:var(--agate);font-size:11.5px;color:var(--muted);margin-top:7px;
  letter-spacing:.05em}
.empty{font-size:15px;line-height:1.8;color:var(--muted);font-style:italic;
  padding:34px 0;max-width:var(--measure)}

/* ---------- 备份与页脚 ---------- */
details{margin-top:36px;border-top:1px solid var(--rule);padding-top:15px}
summary{font-family:var(--agate);font-size:11.5px;letter-spacing:.15em;
  text-transform:uppercase;color:var(--muted);cursor:pointer}
details p{font-size:13.5px;line-height:1.78;color:var(--muted);max-width:var(--measure)}
textarea{width:100%;max-width:var(--measure);height:92px;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;
  background:var(--tint);color:var(--ink);border:1px solid var(--rule);
  border-radius:1px;padding:9px;margin-top:8px;display:block}
footer{margin-top:48px;border-top:3px double var(--rule-strong);padding-top:19px}
footer h4{font-family:var(--agate);font-size:11.5px;letter-spacing:.17em;
  text-transform:uppercase;color:var(--muted);margin:0 0 12px}
footer p{font-size:13.5px;line-height:1.8;color:var(--muted);margin:0 0 10px;
  max-width:var(--measure)}
footer code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
  background:var(--tint);padding:1px 5px;border-radius:2px}

@media (max-width:760px){
  .story{grid-template-columns:26px minmax(0,1fr);gap:0 12px}
  .rail{grid-column:2;grid-row:auto;text-align:left;border-left:none;padding-left:0;
    border-top:1px solid var(--rule);padding-top:8px;margin-top:2px;
    display:flex;gap:14px;flex-wrap:wrap;align-items:baseline}
  .rail .rank{font-size:16px;margin:0}
  .rail .rank small{display:inline;margin:0 5px 0 0}
  .rail div{margin-top:0}
  .hl{font-size:18px}
  .wrap{padding:0 17px 60px}
}
@media print{
  body{background:#fff;color:#000}
  .toolbar,.batchbar,.links,.star,details,.sib{display:none}
  .story{break-inside:avoid}
}
"""


def esc(t):
    return html.escape(str(t if t is not None else ""), quote=True)


def cn_full_date(dt):
    return f"{dt.year}年{dt.month}月{dt.day}日 星期{WEEKDAY[dt.weekday()]}"


JS = r"""
(function(){
  var D = JSON.parse(document.getElementById('payload').textContent);
  var S = JSON.parse(document.getElementById('summaries').textContent);
  var B = D.batches, LAST = B.length;

  // 存取器本身可能抛（无痕模式、禁用站点数据、缩略图捕获），一律包起来
  function get(k, dflt){ try{ var v=localStorage.getItem(k);
    return v===null ? dflt : JSON.parse(v); }catch(e){ return dflt; } }
  function set(k, v){ try{ localStorage.setItem(k, JSON.stringify(v)); }catch(e){} }

  var favs = get('bull:favs:v1', []);
  if(!Array.isArray(favs)) favs = [];
  var batch = get('bull:batch:v1', 1);
  if(!(batch>=1 && batch<=LAST)) batch = 1;
  var view = get('bull:view:v1', 'board');
  if(view!=='board' && view!=='favs') view = 'board';

  var byId = {}, batchOf = {};
  B.forEach(function(b){ b.items.forEach(function(it){
    byId[it.sid]=it; batchOf[it.sid]=b.n; }); });

  var $ = function(id){ return document.getElementById(id); };
  function el(tag, cls, txt){ var e=document.createElement(tag);
    if(cls) e.className=cls; if(txt!=null) e.textContent=txt; return e; }
  function cnDate(ts){ var d=new Date(ts*1000); return (d.getMonth()+1)+'月'+d.getDate()+'日'; }

  // 排名是全局的：翻到第 2 批就是 31、32…，不重新从 01 开始。
  // 榜单往下翻，序号必须跟着往下走，否则「往下翻」这件事在页面上看不出来。
  var rankOf = {};
  B.forEach(function(b){
    ['hn','hf'].forEach(function(src){
      var i = 0;
      b.items.forEach(function(it){
        if(it.source===src){ rankOf[it.sid] = b.ranges[src][0] + i; i++; }
      });
    });
  });

  function card(it, showFrom){
    var a = el('article','story');
    var on = favs.indexOf(it.sid)>=0;
    var star = el('button','star'+(on?' on':''), on?'★':'☆');
    star.title = on ? '取消收藏' : '收藏';
    star.setAttribute('aria-pressed', on?'true':'false');
    star.setAttribute('aria-label', (on?'取消收藏：':'收藏：')+it.title);
    star.onclick = function(){ toggle(it.sid); };
    a.appendChild(star);

    var body = el('div','body');
    body.appendChild(el('div','kicker', it.source_label + ' · ' + cnDate(it.created_utc) +
      (it.lingering_signal ? ' · 🔥 持续热门 · ' + it.lingering_signal :
       (it.age_bucket === 'lingering' ? ' · 近两周高热' : ''))));

    var h = el('h3','hl'), link = el('a', null, it.title);
    link.href = it.permalink; link.target='_blank'; link.rel='noopener';
    h.appendChild(link); body.appendChild(h);

    if(S[it.sid]) body.appendChild(el('p','sum', S[it.sid]));
    else body.appendChild(el('p','sum todo',
      '摘要待补 —— 第 '+batchOf[it.sid]+' 批尚未撰写中文摘要。'));

    var L = el('div','links');
    if(it.target_url && it.target_url!==it.permalink){
      var o=el('a',null,'阅读原文 →'); o.href=it.target_url; o.target='_blank';
      o.rel='noopener'; L.appendChild(o); }
    var p=el('a',null,'讨论区 →'); p.href=it.permalink; p.target='_blank';
    p.rel='noopener'; L.appendChild(p);
    body.appendChild(L);
    if(showFrom) body.appendChild(el('div','from','来自第 '+batchOf[it.sid]+' 批'));
    a.appendChild(body);

    // 数据栏：排名 + 原生计数，等宽数字，纵向对齐成一列
    var rail = el('div','rail'), rank = el('span','rank');
    rank.appendChild(el('small', null, it.source==='hf'?'HF 排名':'HN 排名'));
    rank.appendChild(document.createTextNode('#'+rankOf[it.sid]));
    rail.appendChild(rank);
    var m = it.metrics||{};
    if(m.votes!=null) rail.appendChild(el('div', null,
      m.votes.toLocaleString()+(it.source==='hf'?' upvotes':' 票')));
    if(m.comments!=null) rail.appendChild(el('div', null, m.comments.toLocaleString()+' 评论'));
    rail.appendChild(el('div', null, '得分 '+(it.score!=null?it.score.toFixed(3):'—')));
    a.appendChild(rail);
    return a;
  }

  function section(title, kicker, items, showFrom){
    var s=el('section'), h=el('div','sec-head');
    h.appendChild(el('h2', null, title));
    h.appendChild(el('span','k', kicker));
    s.appendChild(h);
    items.forEach(function(it){ s.appendChild(card(it, showFrom)); });
    return s;
  }

  var LABEL = {hn:'Hacker News', hf:'Hugging Face Papers'};

  function renderBoard(){
    var b = B[batch-1], out = $('board');
    out.textContent = '';
    ['hn','hf'].forEach(function(src){
      var xs = b.items.filter(function(x){return x.source===src;});
      if(!xs.length) return;
      out.appendChild(section(LABEL[src],
        '第 '+b.ranges[src][0]+'–'+b.ranges[src][1]+' 名 · '+xs.length+' 条', xs, false));
    });
  }

  function renderFavs(){
    var out = $('board');
    out.textContent = '';
    var items = favs.map(function(s){return byId[s];}).filter(Boolean);
    if(!items.length){
      out.appendChild(el('p','empty',
        '收藏夹是空的。在任意条目左侧点 ☆ 即可收藏。收藏只存在这台设备的浏览器里，'
        + '翻批次、刷新、关掉重开都不会丢。'));
      return;
    }
    ['hn','hf'].forEach(function(src){
      var xs = items.filter(function(x){return x.source===src;});
      if(!xs.length) return;
      xs.sort(function(a,b){ return rankOf[a.sid]-rankOf[b.sid]; });
      out.appendChild(section(LABEL[src], '收藏 '+xs.length+' 条', xs, true));
    });
  }

  function toggle(sid){
    var i = favs.indexOf(sid);
    if(i>=0) favs.splice(i,1); else favs.push(sid);
    set('bull:favs:v1', favs);
    render();
  }

  function render(){
    var b = B[batch-1];
    $('vBoard').classList.toggle('on', view==='board');
    $('vFavs').classList.toggle('on', view==='favs');
    $('favCount').textContent = favs.length;
    $('batchSel').value = String(batch);
    $('prev').disabled = batch<=1;
    $('next').disabled = batch>=LAST;
    $('next').textContent = batch>=LAST ? '已是最后一批' : '更新 ▸ 下一批';
    var done = b.items.every(function(it){ return !!S[it.sid]; });
    $('bmeta').innerHTML = 'HN <b>'+b.ranges.hn[0]+'–'+b.ranges.hn[1]+'</b>'
      + '<span class="sep">·</span>HF <b>'+b.ranges.hf[0]+'–'+b.ranges.hf[1]+'</b>'
      + '<span class="sep">·</span>'
      + (done ? '摘要 ✓' : '<span class="warn">摘要待补</span>');
    $('batchbar').style.display = view==='board' ? '' : 'none';
    if(view==='board') renderBoard(); else renderFavs();
    set('bull:batch:v1', batch); set('bull:view:v1', view);
    $('backup').value = JSON.stringify(favs);
  }

  function go(n){ batch = Math.min(LAST, Math.max(1, n)); view='board'; render();
                  scrollTo(0,0); }
  $('next').onclick   = function(){ go(batch+1); };
  $('prev').onclick   = function(){ go(batch-1); };
  $('batchSel').onchange = function(){ go(parseInt(this.value,10)); };
  $('vBoard').onclick = function(){ view='board'; render(); };
  $('vFavs').onclick  = function(){ view='favs';  render(); };
  $('backup').onfocus = function(){ this.select(); };
  $('doImport').onclick = function(){
    var raw = $('importBox').value.trim();
    if(!raw) return;
    var arr;
    try{ arr = JSON.parse(raw); }catch(e){ $('impMsg').textContent='解析失败：不是合法 JSON'; return; }
    if(!Array.isArray(arr)){ $('impMsg').textContent='解析失败：应当是一个 sid 数组'; return; }
    var known = arr.filter(function(s){ return byId[s]; });
    var added = known.filter(function(s){ return favs.indexOf(s)<0; });
    favs = favs.concat(added);
    set('bull:favs:v1', favs);
    $('impMsg').textContent = '导入 '+arr.length+' 条：新增 '+added.length
      + '，已有 '+(known.length-added.length)+'，本页数据里找不到 '+(arr.length-known.length)+'。';
    $('importBox').value='';
    render();
  };
  render();
})();
"""


def render_static_batch(data, summaries):
    """<noscript> 兜底：页面是客户端渲染的，禁用 JS 会只剩报头，故静态嵌入第 1 批。"""
    b = data["batches"][0]
    rows, seen = [], {}
    for it in b["items"]:
        src = it["source"]
        seen[src] = seen.get(src, 0) + 1
        rank = b["ranges"][src][0] + seen[src] - 1
        s = (summaries.get(it["sid"]) or "").strip()
        rows.append(
            f'<article class="story"><div></div><div class="body">'
            f'<div class="kicker">{esc(it["source_label"])}</div>'
            f'<h3 class="hl"><a href="{esc(it["permalink"])}">{esc(it["title"])}</a></h3>'
            + (f'<p class="sum">{esc(s)}</p>' if s
               else '<p class="sum todo">摘要待补。</p>')
            + f'</div><div class="rail"><span class="rank">'
              f'<small>{"HF" if src == "hf" else "HN"} 排名</small>#{rank}</span></div>'
            + '</article>')
    return ('<noscript><p class="empty">此页需要 JavaScript 才能翻批次与收藏。'
            f'以下是第 1 批（共 {len(data["batches"])} 批）的静态内容。</p>'
            + "".join(rows) + "</noscript>")


def render_footer(data, summaries):
    cfg, st = data["config"], data["source_status"]
    total = sum(len(b["items"]) for b in data["batches"])
    done = sum(1 for b in data["batches"] for it in b["items"]
               if (summaries.get(it["sid"]) or "").strip())
    off = [f"<strong>{esc(v.get('label', k))}</strong>（{esc(v.get('reason', '未知原因'))}）"
           for k, v in st.items() if not v.get("ok")]

    notes = [
        f"本板收录近 {cfg.get('hn_min_points') and data['window']['days']} 天内的榜单，"
        f"共 <strong>{len(data['batches'])} 批 {total} 条</strong>：每批 Hacker News "
        f"{cfg['hn_per_batch']} 条 + Hugging Face Papers {cfg['hf_per_batch']} 条。"
        f"点「更新」往下翻一批，往回翻也随时可以 —— 所有批次都在这一页里，不会消失。"
        f"批次数受 <code>{esc(cfg.get('batch_limited_by', '?'))}</code> 的候选量限制"
        f"（本期候选池 HN {data['pool']['hn']['fresh']}+{data['pool']['hn']['lingering']} 条 / "
        f"HF {data['pool']['hf']['fresh']}+{data['pool']['hf']['lingering']} 篇，"
        f"前者为三日内、后者为 4–{cfg.get('linger_days', 14)} 天）。"
        + (f"每批约 {round(100 * (1 - cfg.get('linger_share', 0.3)))}% 为三日内新内容，"
           f"约 {round(100 * cfg.get('linger_share', 0.3))}% 为 4–{cfg.get('linger_days', 14)} 天内"
           + ("编辑从高讨论量帖子中精选的技术长帖（标 🔥 编辑精选）。" if cfg.get("curated")
              else "仍在榜的持续热门（标 🔥）。")),

        "排名只使用平台原生公开计数：评论量（权重 0.5）与票数／upvotes（0.2），"
        "缺失指标的权重按比例分配给该信源可得的指标。各指标先在<strong>同信源同窗口</strong>"
        "的候选池内换算成分位值再加权求和 —— 用分位而非极值归一，是因为互动数据呈重尾分布。"
        "得分只在同信源内可比，因此 HN 与 HF 分区呈现，不做跨平台排序。",

        f"中文摘要目前写了 <strong>{done}/{total}</strong> 条（前 "
        f"{cfg['enriched_batches']} 批）。其余条目显式标注「摘要待补」，"
        "<strong>没有用任何自动文本填充</strong>；想要哪一批，让 Claude 补写后重新发布即可，"
        "重新发布落回同一个地址，<strong>你的收藏不受影响</strong>。",

        "收藏只存在<strong>你这台设备的这个浏览器</strong>里（localStorage）。翻批次、刷新、"
        "关掉重开都不会丢，但换浏览器、清站点数据、无痕窗口会丢 —— 上面的备份面板是唯一兜底。"
        "页面运行在沙箱里，无法直接把文件存到你的磁盘，所以备份只能复制粘贴。",
    ]
    if off:
        notes.append('<span class="warn">本期未采集：' + "；".join(off) + "。</span>")
    notes.append(f"采集于 {esc(data.get('generated_at_local') or data['generated_at'])}"
                 f"　·　HN 候选门槛 <code>points &gt; {esc(cfg['hn_min_points'])}</code>"
                 "　·　排名口径全文见 <code>references/ranking.md</code>")
    return ("<footer><h4>编者按 · Editor&rsquo;s Note</h4>"
            + "".join(f"<p>{n}</p>" for n in notes) + "</footer>")


def sibling_link(spec):
    """--sibling "文本|href" → 站内导航链接。两页互指，别让读者掉进单页孤岛。"""
    if not spec or "|" not in spec:
        return ""
    text, href = spec.split("|", 1)
    return f'<a class="sib" href="{esc(href.strip())}">{esc(text.strip())} →</a>'


def render(data, summaries, nameplate, standalone, sibling=None):
    try:
        gen = datetime.fromisoformat(
            data["generated_at"].replace("Z", "+00:00")).astimezone()
    except Exception:                                    # noqa: BLE001
        gen = datetime.now().astimezone()
    n = len(data["batches"])
    total = sum(len(b["items"]) for b in data["batches"])
    opts = "".join(f'<option value="{b["n"]}">第 {b["n"]} 批</option>'
                   for b in data["batches"])
    sibling = sibling_link(sibling)      # 先算再覆盖，别把参数冲掉

    body = f"""<title>{esc(nameplate)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?\
family=UnifrakturMaguntia&\
family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&\
family=IBM+Plex+Sans+Condensed:wght@400;600&display=swap">
<style>{CSS}</style>
<div class="wrap">
  <header class="masthead">
    <div class="dateline">
      <span>{esc(cn_full_date(gen))}</span>
      <span>近 {data['window']['days']} 天榜单　·　{n} 批 {total} 条</span>
      <span>采集于 {esc(gen.strftime('%H:%M'))}</span>
    </div>
    <div class="plate"><span class="logo">{LOGO}</span>
      <h1 class="nameplate">{esc(nameplate)}</h1></div>
    <div class="tagline">Hacker News　·　Hugging Face Papers</div>
    <div class="rule-double"></div>
    <div class="toolbar">
      <div class="tools">
        <button id="vBoard" type="button" class="on">新闻板</button>
        <button id="vFavs" type="button">★ 收藏夹 <span class="cnt" id="favCount">0</span></button>
        {sibling}
      </div>
      <button id="next" type="button">更新 ▸ 下一批</button>
    </div>
    <div class="batchbar" id="batchbar">
      <button id="prev" type="button">◂ 上一批</button>
      <select id="batchSel" aria-label="选择批次">{opts}</select>
      <span class="bmeta">／ 共 {n} 批</span>
      <span class="bmeta" id="bmeta"></span>
    </div>
  </header>

  <main id="board"></main>
  {render_static_batch(data, summaries)}

  <details>
    <summary>收藏夹备份</summary>
    <p>收藏只存在这个浏览器里。换设备或清缓存前，把下面这串复制走；换到新环境后粘进导入框即可恢复。
       页面在沙箱中运行，无法直接存文件到磁盘，所以只能复制粘贴。</p>
    <textarea id="backup" readonly aria-label="收藏夹备份 JSON"></textarea>
    <textarea id="importBox" placeholder="把备份 JSON 粘到这里，再点导入" aria-label="导入收藏夹"></textarea>
    <p><button id="doImport" type="button">导入</button>
       <span id="impMsg" class="bmeta"></span></p>
  </details>

  {render_footer(data, summaries)}
</div>
<script type="application/json" id="payload">{json.dumps(data, ensure_ascii=False, separators=(',', ':'))}</script>
<script type="application/json" id="summaries">{json.dumps(summaries, ensure_ascii=False, separators=(',', ':'))}</script>
<script>{JS}</script>"""

    if not standalone:
        return body
    return ('<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            + body + "\n</body>\n</html>")


def main():
    ap = argparse.ArgumentParser(description="渲染 🐮 新闻板")
    ap.add_argument("data")
    ap.add_argument("summaries", nargs="?", default=None)
    ap.add_argument("-o", "--out", default="daily_bull.html")
    ap.add_argument("--nameplate", default="The Daily Bull")
    ap.add_argument("--standalone", action="store_true",
                    help="包成完整 HTML 文档；默认输出 Artifact 片段")
    ap.add_argument("--site-out", default=None, metavar="PATH",
                    help="额外写一份完整 HTML 文档到此路径（站点用，始终 standalone）")
    ap.add_argument("--sibling", default=None, metavar="文本|href",
                    help='站内导航链接，如 "日报|digest.html"')
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)
    summaries = {}
    if args.summaries:
        with open(args.summaries, encoding="utf-8") as f:
            summaries = json.load(f)
        if not isinstance(summaries, dict):
            sys.exit("summaries 必须是 {sid: 摘要} 的对象")
    # 只带上本板用得到的摘要，别把别处的摘要一起塞进页面
    sids = {it["sid"] for b in data["batches"] for it in b["items"]}
    summaries = {k: v for k, v in summaries.items() if k in sids and (v or "").strip()}

    doc = render(data, summaries, args.nameplate, args.standalone, args.sibling)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(doc)
    if args.site_out:                                # 站点副本必须是完整文档
        site = (doc if args.standalone
                else render(data, summaries, args.nameplate, True, args.sibling))
        with open(args.site_out, "w", encoding="utf-8") as f:
            f.write(site)
        print(f"✓ 同步 {args.site_out}（standalone）", file=sys.stderr)

    total = len(sids)
    print(f"✓ 写入 {args.out}（{len(data['batches'])} 批 / {total} 条，"
          f"摘要 {len(summaries)} 条，{len(doc.encode()) // 1024} KB）", file=sys.stderr)
    per = {}
    for b in data["batches"]:
        per[b["n"]] = sum(1 for it in b["items"] if summaries.get(it["sid"]))
    miss = [f"第{k}批 {v}/{len(data['batches'][k-1]['items'])}"
            for k, v in per.items() if v < len(data["batches"][k - 1]["items"])]
    if miss:
        print("  摘要未满的批次: " + "、".join(miss), file=sys.stderr)


if __name__ == "__main__":
    main()
