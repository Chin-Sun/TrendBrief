#!/usr/bin/env python3
"""
fetch_rank.py — 四源热帖抓取 · 排名 · 正文提取

产出 digest_data.json，供 render_html.py 渲染。
排名口径见 ../references/ranking.md（本文件是它的实现，改口径请先改文档）。

只依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import base64
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

# ---------------------------------------------------------------- 常量

BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SOURCE_ORDER = ["hn", "reddit", "hf", "devto"]          # 顺序即优先级
SOURCE_LABEL = {
    "hn": "Hacker News",
    "reddit": "r/LocalLLaMA",
    "hf": "Hugging Face Papers",
    "devto": "DEV Community",
}
SOURCE_HOME = {
    "hn": "https://news.ycombinator.com/",
    "reddit": "https://www.reddit.com/r/LocalLLaMA/",
    "hf": "https://huggingface.co/papers/trending",
    "devto": "https://dev.to/",
}

BASE_WEIGHTS = {"comments": 0.5, "shares": 0.3, "votes": 0.2}
AVAILABLE_METRICS = {
    "hn":     ("comments", "votes"),
    "reddit": ("comments", "shares", "votes"),
    "hf":     ("comments", "votes"),
    "devto":  ("comments", "votes"),
}

SECTIONS = [
    {"id": "d7",  "window": 7, "title": "七日要闻",
     "kicker": "PAST 7 DAYS · TOP 10",
     "quota": {"hn": 4, "reddit": 3, "hf": 2, "devto": 1}},
    {"id": "d3",  "window": 3, "title": "三日速览",
     "kicker": "PAST 3 DAYS · TOP 5",
     "quota": {"hn": 2, "reddit": 1, "hf": 1, "devto": 1}},
    {"id": "hn3", "window": 3, "title": "Hacker News 专栏",
     "kicker": "HACKER NEWS · PAST 3 DAYS · TOP 5",
     "quota": {"hn": 5}},
]

MIN_TEXT = 400          # 正文低于此长度视为抓取不足，交给 WebFetch 补
DEFAULT_LIMIT = 3000    # 单条正文截断长度


def log(msg):
    print(f"  {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- HTTP 层

def _open(url, headers=None, data=None, timeout=25):
    hdrs = {"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}
    hdrs.update(headers or {})
    return urllib.request.urlopen(
        urllib.request.Request(url, data=data, headers=hdrs), timeout=timeout)


def http_bytes(url, headers=None, data=None, timeout=25, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            with _open(url, headers, data, timeout) as r:
                return r.read(), (r.headers.get("Content-Type") or "")
        except Exception as e:                       # noqa: BLE001
            last = e
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
    raise last


def http_json(url, headers=None, data=None, timeout=25, retries=2):
    raw, _ = http_bytes(url, headers, data, timeout, retries)
    return json.loads(raw.decode("utf-8", "replace"))


# ---------------------------------------------------------------- 正文抽取

class _TextExtractor(HTMLParser):
    """标准库实现的标签剥离器 —— 环境无 bs4，且正文提取不值得引入依赖。"""

    SKIP = {"script", "style", "noscript", "nav", "header", "footer",
            "aside", "form", "svg", "iframe", "button", "select", "template"}
    BREAK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
             "tr", "section", "article", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buf, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BREAK:
            self.buf.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self.BREAK:
            self.buf.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip > 0:
            self.skip -= 1
        elif tag in self.BREAK:
            self.buf.append("\n")

    def handle_data(self, data):
        if self.skip == 0:
            self.buf.append(data)

    def text(self):
        t = "".join(self.buf)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\n[ \t]*\n+", "\n\n", t)
        return t.strip()


def strip_html(fragment):
    p = _TextExtractor()
    try:
        p.feed(fragment or "")
    except Exception:                                # noqa: BLE001
        return re.sub(r"<[^>]+>", " ", fragment or "").strip()
    return p.text()


def meta_description(doc):
    m = re.search(
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]*'
        r'content=["\'](.*?)["\']', doc, re.I | re.S)
    return html_mod.unescape(m.group(1)).strip() if m else ""


def main_region(doc):
    """优先只取 <article>/<main> 区域 —— 站点导航多用 div，按标签名跳过挡不住。"""
    for tag in ("article", "main"):
        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", doc, re.S | re.I)
        if m and len(m.group(1)) > 800:
            return m.group(1)
    m = re.search(r'<[^>]+role=["\']main["\'][^>]*>(.*?)</\w+>', doc, re.S | re.I)
    return m.group(1) if m and len(m.group(1)) > 800 else doc


_CHROME = re.compile(
    r"(skip to (main )?content|you signed (in|out) with|reload to refresh|"
    r"cookie|subscribe|newsletter|sign in|sign up|log in|all rights reserved|"
    r"privacy policy|terms of service|share this|follow us|advertisement)", re.I)


def densify(text):
    """丢掉模板噪声：过短且非句子的行、重复行、明显的站点 chrome。"""
    seen, out = set(), []
    for ln in text.split("\n"):
        ln = ln.strip()
        if not ln or _CHROME.search(ln):
            continue
        if len(ln) < 40 and not re.search(r"[.!?。！？：:]$", ln):
            continue
        k = ln.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(ln)
    return "\n".join(out)


def extract_page_text(url, limit):
    """返回 (text, error)。非 HTML（PDF 等）不硬解析，直接交给上层降级。"""
    raw, ctype = http_bytes(url, retries=1, timeout=20)
    if "html" not in ctype.lower():
        return "", f"非 HTML 内容({ctype.split(';')[0] or 'unknown'})"
    doc = raw.decode("utf-8", "replace")
    body = densify(strip_html(main_region(doc)))
    if len(body) < MIN_TEXT:                 # 主区域太薄，退回全页再过滤
        body = densify(strip_html(doc))
    desc = meta_description(doc)
    if desc and desc[:80] not in body[:2500]:
        body = desc + "\n\n" + body
    return body[:limit], None


# ---------------------------------------------------------------- 条目工厂

def make_item(source, title, permalink, target_url, created_utc,
              comments=None, shares=None, votes=None, text="", extra=None):
    return {
        "source": source,
        "source_label": SOURCE_LABEL[source],
        "title": title,
        "permalink": permalink,
        "target_url": target_url or permalink,
        "created_utc": int(created_utc),
        "published_iso": datetime.fromtimestamp(
            int(created_utc), timezone.utc).isoformat().replace("+00:00", "Z"),
        "metrics": {"comments": comments, "shares": shares, "votes": votes},
        "content": (text or "").strip(),
        "extra": extra or {},
        "also_seen_in": [],
        "also_links": [],
    }


# ---------------------------------------------------------------- 适配器：HN

def fetch_hn(since_ts, min_points):
    # '>' 必须编码为 %3E —— 裸 '>' 会被 shell 当重定向符，也不被 Algolia 正确解析
    url = ("https://hn.algolia.com/api/v1/search?tags=story"
           f"&numericFilters=created_at_i%3E{int(since_ts)},points%3E{int(min_points)}"
           "&hitsPerPage=1000")
    try:
        data = http_json(url)
    except Exception as e:                           # noqa: BLE001
        return [], {"ok": False, "reason": f"Algolia 请求失败: {type(e).__name__}"}

    items = []
    for h in data.get("hits") or []:
        title = (h.get("title") or "").strip()
        oid, ts = h.get("objectID"), h.get("created_at_i")
        if not (title and oid and ts):
            continue
        permalink = f"https://news.ycombinator.com/item?id={oid}"
        items.append(make_item(
            "hn", title, permalink, h.get("url") or permalink, ts,
            comments=h.get("num_comments") or 0,
            shares=None,
            votes=h.get("points") or 0,
            extra={"author": h.get("author"), "hn_id": str(oid)},
        ))
    log(f"HN      候选 {len(items)} 条 (points>{min_points}, nbHits={data.get('nbHits')})")
    return items, {"ok": True, "candidates": len(items),
                   "note": f"points>{min_points} 的 story"}


# ---------------------------------------------------------------- 适配器：Reddit

def load_env_file(path):
    """把 KEY=VALUE 读进环境变量。已存在的环境变量优先，不覆盖。"""
    if not path or not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return True


def reddit_token():
    """client_credentials 只读授权。返回 (token, error)；错误信息绝不含凭证。"""
    cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    sec = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    ua = os.environ.get("REDDIT_UA", "").strip()
    if not cid or not sec:
        return None, "未配置 REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET"
    if not ua:
        return None, "未配置 REDDIT_UA（Reddit 对通用 UA 限流，必须含你的用户名）"
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    try:
        resp = http_json(
            "https://www.reddit.com/api/v1/access_token",
            headers={"Authorization": f"Basic {auth}", "User-Agent": ua,
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
            retries=1)
    except urllib.error.HTTPError as e:
        return None, f"换取 token 失败: HTTP {e.code}（凭证无效或应用类型不对）"
    except Exception as e:                           # noqa: BLE001
        return None, f"换取 token 失败: {type(e).__name__}"
    tok = resp.get("access_token")
    return (tok, None) if tok else (None, f"token 响应异常: {resp.get('error', 'no access_token')}")


def parse_reddit_listing(payload):
    out = []
    for child in ((payload or {}).get("data") or {}).get("children") or []:
        d = child.get("data") or {}
        title = (d.get("title") or "").strip()
        pid, ts = d.get("id"), d.get("created_utc")
        if not (title and pid and ts is not None):
            continue
        permalink = "https://www.reddit.com" + (d.get("permalink") or f"/comments/{pid}/")
        target = permalink if d.get("is_self") else (
            d.get("url_overridden_by_dest") or d.get("url") or permalink)
        votes = d.get("ups")
        out.append(make_item(
            "reddit", title, permalink, target, ts,
            comments=d.get("num_comments") or 0,
            shares=d.get("num_crossposts") or 0,
            votes=(votes if votes is not None else (d.get("score") or 0)),
            text=(d.get("selftext") or "").strip(),
            extra={"author": d.get("author"),
                   "upvote_ratio": d.get("upvote_ratio"),
                   "flair": d.get("link_flair_text")},
        ))
    return out


def fetch_reddit(export_path):
    """三模式降级：A 凭证 / B 浏览器导出 / C 跳过。"""
    # 模式 B —— 用户从已登录浏览器导出的 JSON
    if export_path:
        try:
            with open(export_path, encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:                       # noqa: BLE001
            return [], {"ok": False, "mode": "C",
                        "reason": f"读取导出文件失败: {type(e).__name__}"}
        items = parse_reddit_listing(payload)
        if not items:
            return [], {"ok": False, "mode": "C",
                        "reason": "导出文件里没有解析到帖子（是否存成了 HTML？）"}
        log(f"Reddit  候选 {len(items)} 条（模式 B：浏览器导出）")
        return items, {"ok": True, "mode": "B", "candidates": len(items),
                       "note": "浏览器导出的 JSON"}

    # 模式 A —— OAuth 凭证
    tok, err = reddit_token()
    if tok:
        try:
            payload = http_json(
                "https://oauth.reddit.com/r/LocalLLaMA/top?t=week&limit=100&raw_json=1",
                headers={"Authorization": f"bearer {tok}",
                         "User-Agent": os.environ.get("REDDIT_UA", BROWSER_UA)},
                retries=1)
            items = parse_reddit_listing(payload)
            log(f"Reddit  候选 {len(items)} 条（模式 A：OAuth）")
            return items, {"ok": True, "mode": "A", "candidates": len(items),
                           "note": "OAuth client_credentials 只读"}
        except Exception as e:                       # noqa: BLE001
            err = f"OAuth 请求失败: {type(e).__name__}"

    # 模式 C —— 放弃，名额顺位转移
    log(f"Reddit  未采集：{err}")
    return [], {"ok": False, "mode": "C", "reason": err}


# ---------------------------------------------------------------- 适配器：HF

def _hf_ts(entry):
    """上榜时间优先于论文发表日 —— 更贴合『最新消息』语义。"""
    paper = entry.get("paper") or {}
    for v in (paper.get("submittedOnDailyAt"), entry.get("publishedAt"),
              paper.get("publishedAt")):
        if not v:
            continue
        try:
            return int(datetime.fromisoformat(
                str(v).replace("Z", "+00:00")).timestamp())
        except Exception:                            # noqa: BLE001
            continue
    return None


def _hf_item(entry):
    paper = entry.get("paper") or {}
    pid = paper.get("id")
    title = (paper.get("title") or "").strip()
    ts = _hf_ts(entry)
    if not (pid and title and ts):
        return None
    authors = [a.get("name") for a in (paper.get("authors") or [])
               if isinstance(a, dict) and a.get("name")]
    return make_item(
        "hf", title,
        f"https://huggingface.co/papers/{pid}",
        f"https://arxiv.org/abs/{pid}", ts,
        comments=entry.get("numComments") or 0,
        shares=None,
        votes=paper.get("upvotes") or 0,
        text=(paper.get("summary") or "").strip(),
        extra={"paper_id": pid,
               "ai_summary": paper.get("ai_summary"),
               "ai_keywords": paper.get("ai_keywords"),
               "github": paper.get("githubRepo"),
               "github_stars": paper.get("githubStars"),
               "authors": authors[:6]},
    )


def fetch_hf(days=8):
    """trending 是历史热门榜（实测 50 条中仅 8 条在 7 天内、0 条在 3 天内），
    因此必须用 daily_papers 逐日补齐。daily_papers 周末不发布，空日属正常。"""
    entries, notes = {}, []

    try:
        raw, _ = http_bytes("https://huggingface.co/papers/trending", retries=1)
        doc = raw.decode("utf-8", "replace")
        for blob in re.findall(r'data-props="(.*?)"', doc, re.S):
            try:
                obj = json.loads(html_mod.unescape(blob))
            except Exception:                        # noqa: BLE001
                continue
            if isinstance(obj, dict) and isinstance(obj.get("dailyPapers"), list):
                for e in obj["dailyPapers"]:
                    it = _hf_item(e)
                    if it:
                        entries[it["extra"]["paper_id"]] = it
                break
    except Exception as e:                           # noqa: BLE001
        notes.append(f"trending 页不可用({type(e).__name__})")
    n_trending = len(entries)

    today = datetime.now(timezone.utc).date()
    empty_days = []
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        try:
            arr = http_json(f"https://huggingface.co/api/daily_papers?date={d}", retries=1)
        except Exception:                            # noqa: BLE001
            continue
        if not isinstance(arr, list) or not arr:
            empty_days.append(d)
            continue
        for e in arr:
            it = _hf_item(e)
            if it:
                entries.setdefault(it["extra"]["paper_id"], it)

    items = list(entries.values())
    if empty_days:
        notes.append(f"{len(empty_days)} 天无日报(周末/尚未发布)")
    log(f"HF      候选 {len(items)} 条 (trending {n_trending} + 日报补齐 {len(items) - n_trending})")
    return items, {"ok": bool(items), "candidates": len(items),
                   "note": "; ".join(notes) or "trending + 逐日日报",
                   **({} if items else {"reason": "未取到任何论文"})}


# ---------------------------------------------------------------- 适配器：dev.to

def fetch_devto():
    items, seen = {}, set()
    errs = []
    for top in (7, 3):                # top=N 的语义正是『近 N 天热榜』
        try:
            arr = http_json(f"https://dev.to/api/articles?top={top}&per_page=100", retries=1)
        except Exception as e:                       # noqa: BLE001
            errs.append(f"top={top}: {type(e).__name__}")
            continue
        for a in arr if isinstance(arr, list) else []:
            aid = a.get("id")
            title = (a.get("title") or "").strip()
            pub = a.get("published_at") or a.get("published_timestamp")
            if not (aid and title and pub) or aid in seen:
                continue
            try:
                ts = int(datetime.fromisoformat(
                    str(pub).replace("Z", "+00:00")).timestamp())
            except Exception:                        # noqa: BLE001
                continue
            seen.add(aid)
            items[aid] = make_item(
                "devto", title, a.get("url"), a.get("canonical_url") or a.get("url"), ts,
                comments=a.get("comments_count") or 0,
                shares=None,
                votes=a.get("public_reactions_count") or 0,
                text=(a.get("description") or "").strip(),
                extra={"article_id": aid,
                       "author": (a.get("user") or {}).get("name"),
                       "tags": a.get("tag_list") or a.get("tags"),
                       "reading_time": a.get("reading_time_minutes")},
            )
    out = list(items.values())
    log(f"dev.to  候选 {len(out)} 条")
    status = {"ok": bool(out), "candidates": len(out), "note": "全站热榜 top=7 + top=3"}
    if errs:
        status["note"] += "；" + "，".join(errs)
    if not out:
        status["reason"] = "; ".join(errs) or "未取到文章"
    return out, status


# ---------------------------------------------------------------- 排名

def percentile_ranks(values):
    """并列取平均秩，归一到 0~1。对重尾分布远比 min-max 稳健。"""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return [r / (n - 1) for r in ranks]


def score_pool(items):
    """就地写入 score / rank_detail / weights。items 必须同信源同窗口。"""
    if not items:
        return
    src = items[0]["source"]
    metrics = [m for m in AVAILABLE_METRICS[src]
               if any(it["metrics"].get(m) is not None for it in items)]
    if not metrics:
        for it in items:
            it["score"], it["rank_detail"], it["weights"] = 0.0, {}, {}
        return
    total = sum(BASE_WEIGHTS[m] for m in metrics)
    weights = {m: BASE_WEIGHTS[m] / total for m in metrics}
    pcts = {m: percentile_ranks([it["metrics"].get(m) or 0 for it in items])
            for m in metrics}
    for idx, it in enumerate(items):
        it["rank_detail"] = {m: round(pcts[m][idx], 4) for m in metrics}
        it["weights"] = {m: round(weights[m], 4) for m in metrics}
        it["score"] = round(sum(weights[m] * pcts[m][idx] for m in metrics), 4)


# ---------------------------------------------------------------- 去重

_TRACK = re.compile(r"^(utm_|ref$|ref_|source$|fbclid$|gclid$|mc_|si$)", re.I)
_HN_PREFIX = re.compile(r"^(show|ask|tell|launch)\s+hn:?\s*", re.I)


def norm_url(u):
    if not u:
        return ""
    try:
        p = urllib.parse.urlsplit(u)
    except Exception:                                # noqa: BLE001
        return u.strip().lower()
    host = (p.netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = (p.path or "/").rstrip("/") or "/"
    if host == "arxiv.org":                          # /abs/x /pdf/x.pdf xv2 → /abs/x
        m = re.search(r"(\d{4}\.\d{4,5})", path)
        if m:
            path = f"/abs/{m.group(1)}"
    if host == "huggingface.co" and path.startswith("/papers/"):
        m = re.search(r"(\d{4}\.\d{4,5})", path)
        if m:
            host, path = "arxiv.org", f"/abs/{m.group(1)}"
    q = sorted((k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
               if not _TRACK.match(k))
    return urllib.parse.urlunsplit(("", host, path, urllib.parse.urlencode(q), ""))


def norm_title(t):
    t = _HN_PREFIX.sub("", (t or "").strip())
    t = re.sub(r"[^0-9a-z一-鿿]+", " ", t.lower()).strip()
    return re.sub(r"\s+", " ", t)


SID_PREFIX = {"hn": "hn", "reddit": "rd", "hf": "hf", "devto": "dv"}


def assign_sid(item):
    """短而稳定的条目 ID —— 摘要文件用它做键，手写时不易出错。"""
    src, ex = item["source"], item["extra"]
    raw = (ex.get("hn_id") or ex.get("paper_id") or ex.get("article_id") or "")
    if not raw and src == "reddit":
        m = re.search(r"/comments/([a-z0-9]+)", item["permalink"] or "")
        raw = m.group(1) if m else ""
    if not raw:
        raw = str(abs(hash(item["permalink"])) % 10**8)
    item["sid"] = f"{SID_PREFIX[src]}-{re.sub(r'[^A-Za-z0-9._-]', '', str(raw))}"
    return item


def cross_source_dedup(items):
    """同一内容跨信源出现时保留高优先级信源，低优先级的折叠为『另见』。"""
    prio = {s: i for i, s in enumerate(SOURCE_ORDER)}
    index, kept = {}, []
    for it in sorted(items, key=lambda x: (prio[x["source"]], -x["created_utc"])):
        keys = set()
        nu = norm_url(it["target_url"])
        if nu and "news.ycombinator.com" not in nu and "reddit.com" not in nu:
            keys.add("u:" + nu)
        nt = norm_title(it["title"])
        if len(nt) >= 12:
            keys.add("t:" + nt)
        hit = next((index[k] for k in keys if k in index), None)
        if hit is not None:
            label = it["source_label"]
            if label not in hit["also_seen_in"]:
                hit["also_seen_in"].append(label)
                hit["also_links"].append({"label": label, "url": it["permalink"]})
            continue
        for k in keys:
            index[k] = it
        kept.append(it)
    return kept


# ---------------------------------------------------------------- 名额分配

def select(pools, quota, used, allow_dupes):
    """pools: {source: [已按 score 降序的条目]}。返回 (选中列表, 缺额数)。"""
    chosen, leftovers, shortfall = [], {}, 0
    for src in SOURCE_ORDER:
        q = quota.get(src, 0)
        cands = [it for it in pools.get(src, [])
                 if allow_dupes or it["key"] not in used]
        take = cands[:q]
        chosen.extend(take)
        leftovers[src] = cands[q:]
        shortfall += q - len(take)
        for it in take:
            used.add(it["key"])
    # 顺位补位：空缺按 HN → Reddit → HF → dev.to 给下一家
    for src in SOURCE_ORDER:
        while shortfall > 0 and leftovers.get(src):
            it = leftovers[src].pop(0)
            if not allow_dupes and it["key"] in used:
                continue
            chosen.append(it)
            used.add(it["key"])
            shortfall -= 1
        if shortfall == 0:
            break
    # 展示顺序：先信源优先级，再组内得分。
    # 不做跨信源的分数排序 —— 不同平台的分位值不可比，那样排是伪精度。
    prio = {s: i for i, s in enumerate(SOURCE_ORDER)}
    chosen.sort(key=lambda x: (prio[x["source"]], -x["score"]))
    return chosen, shortfall


# ---------------------------------------------------------------- 正文补齐

def enrich(item, limit):
    """只对入选条目抓正文。抓不动的标 needs_webfetch，交给 WebFetch 工具补。"""
    src = item["source"]

    if src == "hn":
        hid = item["extra"].get("hn_id")
        if hid:                                      # 讨论区帮助理解争议点
            try:
                o = http_json(f"https://hn.algolia.com/api/v1/items/{hid}", retries=1)
                bits = []
                if o.get("text"):
                    bits.append(strip_html(o["text"]))
                for ch in (o.get("children") or [])[:6]:
                    if ch.get("text"):
                        bits.append(f"[{ch.get('author') or 'anon'}] "
                                    + strip_html(ch["text"]))
                item["discussion"] = "\n\n".join(b for b in bits if b)[:1500]
            except Exception:                        # noqa: BLE001
                pass
        if not item["target_url"].startswith("https://news.ycombinator.com/"):
            try:
                txt, err = extract_page_text(item["target_url"], limit)
                item["content"] = txt
                if err:
                    item["content_error"] = err
            except Exception as e:                   # noqa: BLE001
                item["content_error"] = f"{type(e).__name__}"
        else:
            item["content"] = (item.get("discussion") or "")[:limit]

    elif src == "devto":
        aid = item["extra"].get("article_id")
        try:
            o = http_json(f"https://dev.to/api/articles/{aid}", retries=1)
            body = (o.get("body_markdown") or "").strip()
            if body:
                item["content"] = re.sub(r"^---.*?---\s*", "", body, flags=re.S)[:limit]
        except Exception as e:                       # noqa: BLE001
            item["content_error"] = f"{type(e).__name__}"

    elif src == "hf":
        parts = [item["content"]]
        if item["extra"].get("ai_summary"):
            parts.append("AI summary: " + item["extra"]["ai_summary"])
        item["content"] = "\n\n".join(p for p in parts if p)[:limit]
        if len(item["content"]) < MIN_TEXT:      # 摘要缺失时回源单篇论文接口
            try:
                o = http_json(
                    f"https://huggingface.co/api/papers/{item['extra']['paper_id']}",
                    retries=1)
                abstract = (o.get("summary") or "").strip()
                if abstract:
                    item["content"] = "\n\n".join(
                        x for x in (abstract, item["extra"].get("ai_summary")) if x)[:limit]
            except Exception as e:               # noqa: BLE001
                item["content_error"] = f"{type(e).__name__}"

    elif src == "reddit":
        if len(item["content"]) < MIN_TEXT and not item["target_url"].startswith(
                "https://www.reddit.com/"):
            try:
                txt, err = extract_page_text(item["target_url"], limit)
                if txt:
                    item["content"] = (item["content"] + "\n\n" + txt).strip()[:limit]
                elif err:
                    item["content_error"] = err
            except Exception as e:                   # noqa: BLE001
                item["content_error"] = f"{type(e).__name__}"

    item["content"] = (item.get("content") or "")[:limit]
    item["needs_webfetch"] = len(item["content"]) < MIN_TEXT
    return item


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="四源热帖抓取 · 排名 · 正文提取")
    ap.add_argument("--out", default="digest_data.json")
    ap.add_argument("--hn-min-points", type=int, default=20)
    ap.add_argument("--reddit-json", default=None,
                    help="模式 B：浏览器导出的 top.json 路径")
    ap.add_argument("--reddit-env", default=os.path.expanduser("~/.claude/.reddit_env"))
    ap.add_argument("--content-limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--allow-cross-section-dupes", action="store_true")
    ap.add_argument("--no-content", action="store_true", help="跳过正文抓取（调试用）")
    args = ap.parse_args()

    now = int(time.time())
    since = {7: now - 7 * 86400, 3: now - 3 * 86400}

    print("▸ 采集候选池", file=sys.stderr)
    load_env_file(args.reddit_env)
    raw, status = {}, {}
    raw["hn"], status["hn"] = fetch_hn(since[7], args.hn_min_points)
    raw["reddit"], status["reddit"] = fetch_reddit(args.reddit_json)
    raw["hf"], status["hf"] = fetch_hf()
    raw["devto"], status["devto"] = fetch_devto()

    # 跨信源去重（在打分前做，避免重复条目污染分位分布）
    flat = [it for s in SOURCE_ORDER for it in raw[s]]
    before = len(flat)
    flat = cross_source_dedup(flat)
    log(f"跨信源去重: {before} → {len(flat)} 条")
    for it in flat:
        it["key"] = f"{it['source']}:{norm_url(it['permalink']) or it['title'][:60]}"
        assign_sid(it)

    # 分窗口 · 分信源打分
    pools = {}
    for win in (7, 3):
        pools[win] = {}
        for src in SOURCE_ORDER:
            sel = [dict(it) for it in flat
                   if it["source"] == src and it["created_utc"] >= since[win]]
            score_pool(sel)
            sel.sort(key=lambda x: -x["score"])
            pools[win][src] = sel
        log(f"{win} 天窗口池: " + ", ".join(
            f"{s}={len(pools[win][s])}" for s in SOURCE_ORDER))

    # 按版块分配名额
    used, sections = set(), []
    for spec in SECTIONS:
        chosen, shortfall = select(pools[spec["window"]], spec["quota"],
                                   used, args.allow_cross_section_dupes)
        sections.append({**{k: spec[k] for k in ("id", "title", "kicker", "quota", "window")},
                         "shortfall": shortfall, "items": chosen})
        got = {}
        for it in chosen:
            got[it["source"]] = got.get(it["source"], 0) + 1
        log(f"{spec['title']}: {len(chosen)} 条 "
            + ("(" + ", ".join(f"{k}×{v}" for k, v in got.items()) + ")" if got else "")
            + (f" 缺额 {shortfall}" if shortfall else ""))

    # 只对入选条目抓正文
    if not args.no_content:
        print("▸ 抓取入选条目正文", file=sys.stderr)
        total = sum(len(s["items"]) for s in sections)
        i = 0
        for sec in sections:
            for it in sec["items"]:
                i += 1
                enrich(it, args.content_limit)
                flag = "需 WebFetch 补" if it["needs_webfetch"] else f"{len(it['content'])} 字符"
                log(f"[{i}/{total}] {it['source']:<6} {flag:<14} {it['title'][:48]}")
    else:
        for sec in sections:
            for it in sec["items"]:
                it["needs_webfetch"] = len(it["content"]) < MIN_TEXT

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "generated_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "windows": {f"d{w}": {"since_utc": since[w],
                              "since_iso": datetime.fromtimestamp(
                                  since[w], timezone.utc).isoformat().replace("+00:00", "Z")}
                    for w in (7, 3)},
        "source_status": {s: {**status[s], "label": SOURCE_LABEL[s],
                              "home": SOURCE_HOME[s], "priority": i + 1}
                          for i, s in enumerate(SOURCE_ORDER)},
        "config": {"hn_min_points": args.hn_min_points,
                   "base_weights": BASE_WEIGHTS,
                   "available_metrics": {k: list(v) for k, v in AVAILABLE_METRICS.items()},
                   "cross_section_dupes": args.allow_cross_section_dupes},
        "sections": sections,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    need = sum(1 for s in sections for it in s["items"] if it["needs_webfetch"])
    print(f"\n✓ 写入 {args.out}", file=sys.stderr)
    print(f"  条目 {sum(len(s['items']) for s in sections)} 条，"
          f"其中 {need} 条正文不足需 WebFetch 补齐", file=sys.stderr)
    down = [SOURCE_LABEL[s] for s in SOURCE_ORDER if not status[s].get("ok")]
    if down:
        print(f"  未采集信源: {', '.join(down)}", file=sys.stderr)


if __name__ == "__main__":
    main()
