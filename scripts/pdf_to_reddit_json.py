#!/usr/bin/env python3
"""
pdf_to_reddit_json.py — 把「Chrome 打印成 PDF 的 Reddit JSON 页面」还原成 Reddit listing

    python3 pdf_to_reddit_json.py top.pdf -o reddit.json

用途：Reddit 对未鉴权访问全面封锁（.json 返反爬 HTML、.rss 返 0 字节），模式 B 要求
用户从已登录浏览器导出。若用户导出的是打印版 PDF 而非 .json，本脚本把它转回来。

为什么不能直接抽字符串：PDF 用子集字体存 CID 码（<005E> 才是 '{'），必须按对象图
取每个字体自己的 /ToUnicode CMap。把所有 CMap 合并成一张表会串码 —— 实测
NVIDIA 会被解成 NVI输IA，因为多个字体在同一码位上有不同字形。

只依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import re
import sys
import zlib
from datetime import datetime

# 核心字段：缺任何一个这条就不可用，不静默补默认值
CORE = ("id", "title", "created_utc", "permalink", "url", "ups", "num_comments")


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- PDF 对象图

def parse_objects(raw):
    """对象号 -> (字典部分, 解压后的流)。流解压失败则原样保留。"""
    objs = {}
    for m in re.finditer(rb"(\d+)\s+0\s+obj\b(.*?)\bendobj", raw, re.S):
        body = m.group(2)
        sm = re.search(rb"stream\r?\n(.*?)\r?\nendstream", body, re.S)
        data = None
        if sm:
            try:
                data = zlib.decompress(sm.group(1))
            except Exception:                        # noqa: BLE001
                data = sm.group(1)                   # 未压缩流
        objs[int(m.group(1))] = (body.split(b"stream")[0], data)
    return objs


def _utf16(raw):
    """ToUnicode 的目标值是 UTF-16BE；逐 4 位 chr() 会把代理对拆成孤立代理。"""
    try:
        b = raw if isinstance(raw, (bytes, bytearray)) else bytes.fromhex(raw)
        return bytes(b).decode("utf-16-be")
    except Exception:                                # noqa: BLE001
        return "�"


def _bump(base_hex, i):
    """bfrange 的目标值按 UTF-16 **码元**递增。直接 int(dst)+i 会在代理对上溢出码点上限。"""
    b = bytearray(bytes.fromhex(base_hex))
    if len(b) < 2:
        return _utf16(b)
    b[-2:] = ((int.from_bytes(b[-2:], "big") + i) & 0xFFFF).to_bytes(2, "big")
    return _utf16(b)


def parse_cmap(data):
    cm = {}
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            cm[int(src, 16)] = _utf16(dst.decode())
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        # 形式一：<lo> <hi> <dst>，目标按码元连续递增
        for lo, hi, dst in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            lo, hi = int(lo, 16), int(hi, 16)
            if hi - lo > 0xFFFF:                     # 畸形区间，跳过而不是撑爆内存
                continue
            for i, code in enumerate(range(lo, hi + 1)):
                cm[code] = _bump(dst.decode(), i)
        # 形式二：<lo> <hi> [<d1> <d2> …]，逐个列出
        for lo, hi, arr in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", blk, re.S):
            lo = int(lo, 16)
            for i, dst in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", arr)):
                cm[lo + i] = _utf16(dst.decode())
    return cm


def font_cmaps(objs):
    out = {}
    for num, (dic, _) in objs.items():
        m = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", dic)
        if not m:
            continue
        tgt = objs.get(int(m.group(1)))
        if tgt and tgt[1]:
            out[num] = parse_cmap(tgt[1])
    return out


def page_fonts(objs):
    """内容流对象号 -> {资源名: 字体对象号}"""
    out = {}
    for _, (dic, _) in objs.items():
        if b"/Page" not in dic or b"/Contents" not in dic:
            continue
        cm = re.search(rb"/Contents\s+(\d+)\s+0\s+R", dic)
        if not cm:
            continue
        fm = re.search(rb"/Font\s*<<(.*?)>>", dic, re.S)
        src = fm.group(1) if fm else b""
        out[int(cm.group(1))] = {k.decode(): int(v)
                                 for k, v in re.findall(rb"/(F\d+)\s+(\d+)\s+0\s+R", src)}
    return out


# ---------------------------------------------------------------- 文本重建

LINE_GAP = 6        # 行距约 15；同一视觉行内 emoji 基线偏移远小于此


def _page_segments(objs, fcm, pf):
    """逐页取出 (y, x, 字体, 文本) 片段。"""
    pages = []
    for cn in sorted(pf):
        data = objs[cn][1]
        if not data:
            continue
        fonts, cur, segs = pf[cn], None, []
        for m in re.finditer(
                rb"/(F\d+)\s+[\d.]+\s+Tf"
                rb"|([-\d.]+)\s+([-\d.]+)\s+Tm\s*\n?<([0-9A-Fa-f]+)>\s*Tj", data):
            if m.group(1):
                cur = fonts.get(m.group(1).decode())
                continue
            h, cm = m.group(4).decode(), fcm.get(cur, {})
            segs.append((float(m.group(3)), float(m.group(2)), cur,
                         "".join(cm.get(int(h[i:i + 4], 16), "\ufffd")
                                 for i in range(0, len(h), 4))))
        pages.append(segs)
    return pages


def chrome_fonts(pages, body):
    """识别打印页眉页脚（及 JSON 查看器的「美观输出」标签）所用字体：每页都在，且总落在同几个
    页内纵坐标上。Chrome 打印长页面时正文坐标跨页连续、页眉页脚却用页内坐标，于是页脚的 y
    会落进后续页正文的 y 区间，按行过滤拦不住 —— 实测把 "num_comments": 22 拼成了 h2/1322。"""
    pages_of, ys_of = collections.defaultdict(set), collections.defaultdict(set)
    for i, segs in enumerate(pages):
        for y, _, f, _ in segs:
            if f != body:
                pages_of[f].add(i)
                ys_of[f].add(round(y))
    n = max(len(pages), 1)
    return {f for f in pages_of if len(pages_of[f]) >= n * 0.5 and len(ys_of[f]) <= 4}


def pdf_text(path):
    raw = open(path, "rb").read()
    objs = parse_objects(raw)
    fcm = font_cmaps(objs)
    if not fcm:
        raise SystemExit("这个 PDF 里没有可解的 /ToUnicode 字体映射 —— 可能是扫描件或图片版")
    body = max(fcm, key=lambda k: len(fcm[k]))       # 字形最多的即正文等宽字体
    pages = _page_segments(objs, fcm, page_fonts(objs))
    chrome = chrome_fonts(pages, body)

    rows = []
    for segs in pages:
        segs = sorted((s for s in segs if s[2] not in chrome), key=lambda s: (s[0], s[1]))
        line, last = [], None
        for s in segs:
            if last is not None and abs(s[0] - last) > LINE_GAP:
                _flush(rows, line, body)
                line = []
            line.append(s)
            last = s[0]
        _flush(rows, line, body)
    return join_soft_wraps(rows)


def _flush(rows, line, body):
    """整行都没有正文字形 —— 打印页眉页脚，或 JSON 查看器的「美观输出」勾选框。丢弃。"""
    if line and any(s[2] == body for s in line):
        rows.append("".join(s[3] for s in line))


def join_soft_wraps(rows):
    """JSON 美化输出每行必带缩进，故不以空格开头的行必是上一行的软换行续行。"""
    lines = []
    for s in rows:
        if not s.strip():
            continue
        if lines and not s.startswith(" ") and s.strip() not in ("{", "}"):
            lines[-1] += s
        else:
            lines.append(s)
    return "\n".join(lines)


# ---------------------------------------------------------------- 字段提取

def _field(block, key):
    """取一个字段的原始字面量。值不跨真实换行 —— 跨了说明该行残缺（多半是 emoji
    这类无字形字符断了行），截断即可，绝不让贪婪匹配吞掉下一个键。"""
    m = re.search(r'"%s":\s*(.*?),\s*\n' % re.escape(key), block, re.S)
    if not m:
        return None
    return m.group(1).split("\n")[0].strip()


def _s(block, key):
    v = _field(block, key)
    if v is None or v == "null":
        return None
    v = v[1:-1] if len(v) >= 2 and v[0] == v[-1] == '"' else v.strip('"')
    # Reddit 默认对 & 做 HTML 转义（OAuth 路径靠 raw_json=1 拿到原文），这里补齐以对齐
    return html.unescape(v)


def _n(block, key):
    v = _field(block, key)
    if v is None:
        return None
    try:
        return int(v) if re.fullmatch(r"-?\d+", v) else float(v)
    except ValueError:
        return None


def _b(block, key):
    v = _field(block, key)
    return None if v is None else v == "true"


def extract_posts(txt):
    posts = []
    for block in re.split(r'"kind":\s*"t3"', txt)[1:]:
        permalink = _s(block, "permalink")
        pid = None
        if permalink:
            m = re.search(r"/comments/([a-z0-9]+)/", permalink)
            pid = m.group(1) if m else None
        posts.append({
            "id": pid,
            "title": _s(block, "title"),
            "author": _s(block, "author"),
            "permalink": permalink,
            "url": _s(block, "url"),
            "url_overridden_by_dest": _s(block, "url_overridden_by_dest"),
            "selftext": _s(block, "selftext") or "",
            "is_self": _b(block, "is_self"),
            "link_flair_text": _s(block, "link_flair_text"),
            "created_utc": _n(block, "created_utc"),
            "ups": _n(block, "ups"),
            "score": _n(block, "score"),
            "num_comments": _n(block, "num_comments"),
            "num_crossposts": _n(block, "num_crossposts") or 0,
            "upvote_ratio": _n(block, "upvote_ratio"),
            "subreddit": _s(block, "subreddit"),
        })
    return posts


def to_listing(posts):
    return {"kind": "Listing",
            "data": {"children": [{"kind": "t3", "data": p} for p in posts]}}


# ---------------------------------------------------------------- 入口

_UNESCAPE = ("title", "selftext", "url", "url_overridden_by_dest", "permalink")


def convert(path):
    """返回 (listing, 报告)。先试整份 JSON 直接解析（压缩版原始 JSON 最保真），
    不合法再回退字段级容错提取（美化版常因 emoji 等无字形字符断行而不完整）。"""
    txt = pdf_text(path)
    posts, method = None, "字段级容错提取"
    try:
        o = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        kids = ((o or {}).get("data") or {}).get("children") or []
        if kids:
            posts = []
            for c in kids:
                d = dict(c.get("data") or {})
                for k in _UNESCAPE:                  # 与字段级路径一致：补齐 & 转义
                    if isinstance(d.get(k), str):
                        d[k] = html.unescape(d[k])
                posts.append(d)
            method = "整份 JSON 直接解析"
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    if posts is None:
        posts = extract_posts(txt)
    good, bad = [], []
    for p in posts:
        miss = [k for k in CORE if p.get(k) in (None, "")]
        (bad if miss else good).append((p, miss))
    return to_listing([p for p, _ in good]), {
        "method": method,
        "total": len(posts),
        "usable": len(good),
        "dropped": [{"title": (p.get("title") or "?")[:60], "missing": m} for p, m in bad],
    }


def main():
    ap = argparse.ArgumentParser(description="打印版 Reddit JSON PDF → listing JSON")
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default=None, help="省略则打到标准输出")
    args = ap.parse_args()

    listing, rep = convert(args.pdf)
    kids = listing["data"]["children"]

    log(f"解析 {rep['total']} 条（{rep['method']}），核心字段完整 {rep['usable']} 条")
    for d in rep["dropped"]:
        log(f"  ✗ 丢弃：{d['title']}  缺 {d['missing']}")
    now = datetime.now().timestamp()
    for label, days in (("7 天", 7), ("3 天", 3)):
        n = sum(1 for c in kids if c["data"]["created_utc"] >= now - days * 86400)
        log(f"  {label}窗口内 {n}/{len(kids)}")
    if kids:
        ts = [c["data"]["created_utc"] for c in kids]
        log(f"  时间跨度 {datetime.fromtimestamp(min(ts)):%m-%d} ~ "
            f"{datetime.fromtimestamp(max(ts)):%m-%d}")

    text = json.dumps(listing, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        log(f"✓ 写入 {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
