#!/usr/bin/env python3
"""
render_html.py — 把 digest_data.json + summaries.json 渲染成 NYT 风格单文件 HTML

    python3 render_html.py digest_data.json summaries.json -o digest.html

summaries.json 形如 {"hn-49437283": "200字中文总结...", ...}，键为条目的 sid。
缺摘要的条目会渲染成显式占位并在编者按中计数 —— 不静默糊弄。

只依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone

WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"]

CSS = """
:root{
  --paper:#fff; --ink:#121212; --ink-soft:#2b2b2b; --muted:#6b6b6b;
  --rule:#dcdcdc; --rule-strong:#121212; --accent:#326891; --tint:#f6f6f4;
  --serif:"Songti SC","Noto Serif SC","Source Han Serif SC",Georgia,"Times New Roman",serif;
  --head:Georgia,"Times New Roman","Songti SC","Noto Serif SC",serif;
  --sans:"Helvetica Neue",Helvetica,Arial,"PingFang SC","Hiragino Sans GB",sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#121212; --ink:#f2f2f2; --ink-soft:#e0e0e0; --muted:#9b9b9b;
    --rule:#343434; --rule-strong:#f2f2f2; --accent:#83b4de; --tint:#1b1b1b;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
     font-family:var(--serif);-webkit-font-smoothing:antialiased}
a{color:inherit}
.wrap{max-width:1180px;margin:0 auto;padding:0 26px 72px}

/* ---------- 报头 ---------- */
.masthead{padding-top:26px}
.dateline{display:flex;justify-content:space-between;align-items:baseline;gap:16px;
  font-family:var(--sans);font-size:11px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--muted);border-bottom:1px solid var(--rule);padding-bottom:9px;flex-wrap:wrap}
.nameplate{font-family:"UnifrakturMaguntia",Georgia,serif;font-weight:400;
  font-size:clamp(40px,9vw,84px);line-height:1.05;text-align:center;
  margin:20px 0 12px;letter-spacing:.01em}
.tagline{text-align:center;font-family:var(--sans);font-size:11.5px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--muted);padding-bottom:14px}
.rule-double{border-top:3px double var(--rule-strong);margin-bottom:8px}

/* ---------- 信源状态条 ---------- */
.sources{display:flex;flex-wrap:wrap;gap:6px 22px;padding:11px 0 13px;
  border-bottom:1px solid var(--rule);font-family:var(--sans);font-size:11.5px;color:var(--muted)}
.sources b{color:var(--ink);font-weight:600;letter-spacing:.03em}
.src-ok::before{content:"●";color:#2e7d4f;margin-right:6px;font-size:9px;vertical-align:2px}
.src-off::before{content:"○";color:var(--muted);margin-right:6px;font-size:9px;vertical-align:2px}

/* ---------- 版块 ---------- */
section{margin-top:46px}
.sec-head{display:flex;justify-content:space-between;align-items:baseline;gap:14px;
  border-bottom:2px solid var(--rule-strong);padding-bottom:7px;flex-wrap:wrap}
.sec-head h2{font-family:var(--head);font-size:25px;font-weight:700;margin:0;letter-spacing:.01em}
.sec-head .k{font-family:var(--sans);font-size:10.5px;letter-spacing:.15em;
  text-transform:uppercase;color:var(--muted)}

/* ---------- 条目通用 ---------- */
.kicker{font-family:var(--sans);font-size:10.5px;letter-spacing:.11em;text-transform:uppercase;
  color:var(--muted);margin-bottom:9px;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.kicker .n{font-family:var(--head);font-size:13px;letter-spacing:0;color:var(--muted);
  font-style:italic}
.kicker .s{color:var(--ink);font-weight:700}
h3.hl,h2.hl{font-family:var(--head);font-weight:700;margin:0 0 9px;letter-spacing:-.005em}
.hl a{text-decoration:none}
.hl a:hover{text-decoration:underline;text-underline-offset:3px}
.meta{font-family:var(--sans);font-size:11.5px;color:var(--muted);margin-bottom:11px;
  line-height:1.6}
.meta .sep{opacity:.5;margin:0 6px}
.sum{font-size:15px;line-height:1.78;color:var(--ink-soft);margin:0 0 13px;
  text-align:justify;text-justify:inter-ideograph}
.sum.missing{color:var(--muted);font-style:italic;text-align:left}
.links{font-family:var(--sans);font-size:11.5px;letter-spacing:.04em;
  display:flex;gap:16px;flex-wrap:wrap}
.links a{color:var(--accent);text-decoration:none;border-bottom:1px solid transparent}
.links a:hover{border-bottom-color:var(--accent)}
.also{font-family:var(--sans);font-size:11px;color:var(--muted);margin-top:7px}

/* ---------- 头条 ---------- */
.lead{padding:26px 0 24px;border-bottom:1px solid var(--rule)}
.lead h2.hl{font-size:clamp(27px,4.1vw,43px);line-height:1.13;margin-bottom:12px}
.lead .sum{font-size:16px;line-height:1.8;column-count:2;column-gap:36px;
  column-rule:1px solid var(--rule)}

/* ---------- 多栏网格 ---------- */
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:34px 0;padding-top:26px}
.story{padding:0 26px;border-left:1px solid var(--rule)}
.story:nth-child(3n+1){border-left:none;padding-left:0}
.story:nth-child(3n){padding-right:0}
.story h3.hl{font-size:19px;line-height:1.28}
@media (max-width:900px){
  .grid{grid-template-columns:repeat(2,1fr);gap:30px 0}
  .story:nth-child(3n+1){border-left:1px solid var(--rule);padding-left:26px}
  .story:nth-child(3n){padding-right:26px}
  .story:nth-child(2n+1){border-left:none;padding-left:0}
  .story:nth-child(2n){padding-right:0}
  .lead .sum{column-count:1}
}
@media (max-width:620px){
  .wrap{padding:0 18px 56px}
  .grid{grid-template-columns:1fr;gap:0}
  .story{border-left:none!important;padding:22px 0!important;
         border-top:1px solid var(--rule)}
  .story:first-child{border-top:none;padding-top:4px!important}
}

/* ---------- 编者按 ---------- */
footer{margin-top:58px;border-top:3px double var(--rule-strong);padding-top:20px}
footer h4{font-family:var(--sans);font-size:11px;letter-spacing:.16em;text-transform:uppercase;
  color:var(--muted);margin:0 0 12px}
footer p{font-size:13.5px;line-height:1.8;color:var(--muted);margin:0 0 10px;max-width:74ch}
footer code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;
  background:var(--tint);padding:1px 5px;border-radius:3px}
.note-warn{border-left:3px solid var(--rule-strong);padding-left:14px;color:var(--ink-soft)}

@media print{
  body{background:#fff;color:#000}
  .links{display:none}
  section{break-inside:avoid}
  .story{break-inside:avoid}
}
"""


def esc(t):
    return html.escape(str(t if t is not None else ""), quote=True)


def cn_date(ts):
    d = datetime.fromtimestamp(ts).astimezone()
    return f"{d.month}月{d.day}日"


def cn_full_date(dt):
    return f"{dt.year}年{dt.month}月{dt.day}日 星期{WEEKDAY[dt.weekday()]}"


def age_text(ts, now):
    h = max(0, (now - ts)) / 3600
    if h < 1:
        return "刚刚"
    if h < 24:
        return f"{int(h)} 小时前"
    return f"{int(h // 24)} 天前"


def meta_line(it, now):
    m, parts = it["metrics"], []
    if m.get("votes") is not None:
        parts.append(f"{m['votes']:,} 票")
    if m.get("comments") is not None:
        parts.append(f"{m['comments']:,} 评论")
    if m.get("shares") is not None:
        parts.append(f"{m['shares']:,} 转发")
    parts.append(cn_date(it["created_utc"]))
    parts.append(age_text(it["created_utc"], now))
    parts.append(f"得分 {it.get('score', 0):.3f}")
    return '<span class="sep">·</span>'.join(esc(p) for p in parts)


def render_item(it, idx, summaries, now, lead=False):
    tag = "h2" if lead else "h3"
    sid = it.get("sid", "")
    summary = (summaries.get(sid) or "").strip()
    if summary:
        sum_html = f'<p class="sum">{esc(summary)}</p>'
    else:
        sum_html = ('<p class="sum missing">（本条摘要缺失：summaries.json 中没有 '
                    f'<code>{esc(sid)}</code>）</p>')

    also = ""
    if it.get("also_links"):
        links = " · ".join(f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>'
                           for l in it["also_links"])
        also = f'<div class="also">另见 {links}</div>'

    same = it["target_url"] == it["permalink"]
    link_html = f'<a href="{esc(it["permalink"])}" target="_blank" rel="noopener">讨论区 →</a>'
    if not same:
        link_html = (f'<a href="{esc(it["target_url"])}" target="_blank" rel="noopener">'
                     f'阅读原文 →</a>' + link_html)

    return f"""
      <article class="{'lead' if lead else 'story'}">
        <div class="kicker"><span class="n">{idx:02d}</span>
          <span class="s">{esc(it['source_label'])}</span>
          <span>{esc(cn_date(it['created_utc']))}</span></div>
        <{tag} class="hl"><a href="{esc(it['permalink'])}" target="_blank"
          rel="noopener">{esc(it['title'])}</a></{tag}>
        <div class="meta">{meta_line(it, now)}</div>
        {sum_html}
        <div class="links">{link_html}</div>
        {also}
      </article>"""


def render_section(sec, summaries, now):
    items = sec["items"]
    if not items:
        return (f'<section><div class="sec-head"><h2>{esc(sec["title"])}</h2>'
                f'<span class="k">{esc(sec["kicker"])}</span></div>'
                f'<p class="sum missing">本版块没有可用条目。</p></section>')
    body = render_item(items[0], 1, summaries, now, lead=True)
    if len(items) > 1:
        cards = "".join(render_item(it, i, summaries, now)
                        for i, it in enumerate(items[1:], start=2))
        body += f'<div class="grid">{cards}</div>'
    return f"""
    <section>
      <div class="sec-head"><h2>{esc(sec['title'])}</h2>
        <span class="k">{esc(sec['kicker'])}</span></div>
      {body}
    </section>"""


def render_sources(status):
    bits = []
    for key, st in status.items():
        cls = "src-ok" if st.get("ok") else "src-off"
        if st.get("ok"):
            tail = f"{st.get('candidates', 0)} 条候选"
            if st.get("mode"):
                tail += f"（模式 {st['mode']}）"
        else:
            tail = "未采集：" + (st.get("reason") or "未知原因")
        bits.append(f'<span class="{cls}"><b>{esc(st.get("label", key))}</b> {esc(tail)}</span>')
    return "".join(bits)


def render_footer(data, summaries, missing):
    st = data["source_status"]
    down = [s for s in st.values() if not s.get("ok")]
    cfg = data.get("config", {})
    gen = data.get("generated_at_local") or data.get("generated_at")

    notes = [
        "本期排名只使用各平台<strong>原生公开的真实计数</strong>：评论量（权重 0.5）、"
        "转发量（0.3，仅 Reddit 公开）、点赞或票数（0.2）。某信源缺某项指标时，"
        "其权重按比例分配给该信源可得的指标。「点击率」在四个信源中全部不公开，"
        "因此不参与排名，也不出现在本页任何数字中。",
        "各指标先在<strong>同信源同窗口</strong>的候选池内换算成分位值（0~1）再加权求和。"
        "用分位而非极值归一，是因为互动数据呈重尾分布，极值归一会被单个爆款压平中段。"
        "得分只在同信源内可比，因此版块内先按信源优先级排列，再按组内得分降序 —— "
        "不做跨平台的分数比较。",
        "名额固定：七日要闻 HN 4 / Reddit 3 / HF 2 / dev.to 1，三日速览 2/1/1/1，"
        "另设 Hacker News 专属三日专栏 5 条。某信源不足额时，空缺按 "
        "HN → Reddit → HF → dev.to 的优先级顺位补上。三个版块之间不重复选取同一条目。",
    ]
    if down:
        notes.append('<span class="note-warn">本期未采集：'
                     + "；".join(f"<strong>{esc(s['label'])}</strong>（{esc(s.get('reason', ''))}）"
                                 for s in down)
                     + "。其名额已按优先级顺位转移，页面中不含该信源的任何条目。</span>")
    if missing:
        notes.append(f'<span class="note-warn">有 {missing} 条目缺少中文摘要，'
                     "已在正文位置显式标出，未用任何自动文本填充。</span>")
    notes.append(f"生成时间 {esc(gen)}　·　HN 候选门槛 <code>points &gt; "
                 f"{esc(cfg.get('hn_min_points', '?'))}</code>　·　"
                 "排名口径全文见 <code>references/ranking.md</code>")

    return ("<footer><h4>编者按 · Editor&rsquo;s Note</h4>"
            + "".join(f"<p>{n}</p>" for n in notes) + "</footer>")


def render(data, summaries, nameplate):
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        gen_dt = datetime.fromisoformat(
            data["generated_at"].replace("Z", "+00:00")).astimezone()
    except Exception:                                # noqa: BLE001
        gen_dt = datetime.now().astimezone()

    total = sum(len(s["items"]) for s in data["sections"])
    missing = sum(1 for s in data["sections"] for it in s["items"]
                  if not (summaries.get(it.get("sid", "")) or "").strip())
    live = sum(1 for s in data["source_status"].values() if s.get("ok"))

    sections = "".join(render_section(s, summaries, now) for s in data["sections"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(nameplate)} · {esc(cn_full_date(gen_dt))}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=UnifrakturMaguntia&display=swap"
      rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <div class="dateline">
      <span>{esc(cn_full_date(gen_dt))}</span>
      <span>本期 {total} 条　·　信源 {live}/{len(data['source_status'])} 在线</span>
      <span>采集于 {esc(gen_dt.strftime('%H:%M'))}</span>
    </div>
    <h1 class="nameplate">{esc(nameplate)}</h1>
    <div class="tagline">四源技术热帖简报　·　排名依据平台原生计数</div>
    <div class="rule-double"></div>
    <div class="sources">{render_sources(data['source_status'])}</div>
  </header>
  {sections}
  {render_footer(data, summaries, missing)}
</div>
</body>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="渲染 NYT 风格简报")
    ap.add_argument("data", help="fetch_rank.py 产出的 digest_data.json")
    ap.add_argument("summaries", nargs="?", default=None,
                    help="{sid: 200字中文摘要} 的 JSON；省略则全部标为缺失")
    ap.add_argument("-o", "--out", default="digest.html")
    ap.add_argument("--nameplate", default="The Daily Digest")
    args = ap.parse_args()

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)
    summaries = {}
    if args.summaries:
        with open(args.summaries, encoding="utf-8") as f:
            summaries = json.load(f)
        if not isinstance(summaries, dict):
            sys.exit("summaries 必须是 {sid: 摘要} 的对象")

    doc = render(data, summaries, args.nameplate)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(doc)

    total = sum(len(s["items"]) for s in data["sections"])
    missing = sum(1 for s in data["sections"] for it in s["items"]
                  if not (summaries.get(it.get("sid", "")) or "").strip())
    print(f"✓ 写入 {args.out}（{total} 条，缺摘要 {missing} 条）", file=sys.stderr)
    if missing:
        for s in data["sections"]:
            for it in s["items"]:
                if not (summaries.get(it.get("sid", "")) or "").strip():
                    print(f"  缺: {it.get('sid')}  {it['title'][:56]}", file=sys.stderr)


if __name__ == "__main__":
    main()
