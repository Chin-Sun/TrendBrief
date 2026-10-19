#!/usr/bin/env python3
"""
fetch_batches.py — 把近 N 天的 HN / HF 榜单切成等量批次，供「新闻板」翻阅

    python3 fetch_batches.py --out board_data.json --content-out board_content.json

与 fetch_rank.py 的分工：那个产出固定 20 条的日报（名额分配 + 跨信源配额），
这个产出「同一榜单往下翻」的批次流 —— 不做名额分配，各信源池内按 score 降序
直接切段。排名口径完全一致，见 references/ranking.md。

只依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from fetch_rank import (DEFAULT_LIMIT, FRESH_DAYS, assign_sid, cross_source_dedup,
                        enrich, fetch_hf, fetch_hn, fetch_hn_front, log, score_pool)

# 页面用得上的字段。正文另存一份 —— 几百 KB 正文没必要塞进产物。
KEEP = ("sid", "source", "source_label", "title", "permalink", "target_url",
        "created_utc", "published_iso", "metrics", "score",
        "also_seen_in", "also_links", "age_bucket", "lingering_signal")


def slim(it):
    out = {k: it[k] for k in KEEP if k in it}
    ex = it.get("extra") or {}
    out["extra"] = {k: ex[k] for k in ("hn_id", "paper_id", "author") if ex.get(k)}
    return out


def build_pools(since_ts, min_points, hf_days, linger_since=None, picks=None):
    """返回 {src: {"fresh": [...], "lingering": [...]}}。
    fresh = 3 天内（在 since_ts 窗口内取分位）；lingering = 4 天以上、linger_since 之后
    （在整个 linger 窗口内取分位）。lingering 里「当前仍在榜」的（HN 首页 / HF trending）
    排在前面并带 lingering_signal，其余按得分补在后面。

    picks = {"hn": [sid...], "hf": [sid...]} 时，lingering 池换成编辑精选、按给定顺序；
    sid 不在池里直接报错，不静默跳过。"""
    linger_since = min(linger_since or since_ts, since_ts)
    linger_days = int((time.time() - linger_since) // 86400)
    # Algolia 单次最多 1000 条：窗口内照旧取全，更早的部分另取一次、只要高票帖，再按 id 合并
    hn, hn_st = fetch_hn(since_ts, min_points)
    if linger_since < since_ts:
        seen = {it["extra"]["hn_id"] for it in hn}
        far, _ = fetch_hn(linger_since, max(min_points, 100))
        hn += [it for it in far if it["extra"]["hn_id"] not in seen]
    hf, hf_st = fetch_hf(days=max(hf_days, linger_days + 1))
    front = fetch_hn_front()
    fresh_since = int(time.time()) - FRESH_DAYS * 86400

    # 去重放在打分之前 —— 同一篇论文常同时出现在 HN 与 HF，重复计入会污染分位
    kept = cross_source_dedup([*hn, *hf])
    flat = {"hn": [], "hf": []}
    for it in kept:
        if it["created_utc"] >= linger_since and it["source"] in flat:
            flat[it["source"]].append(it)

    pools = {}
    for src, items in flat.items():
        score_pool(items)                       # 整个 linger 窗口内取分位
        for it in items:
            assign_sid(it)
        old = sorted((it for it in items if it["created_utc"] < fresh_since),
                     key=lambda x: -x["score"])
        # fresh 仍在原窗口内取分位，保持与日报一致的口径
        recent = [it for it in items if it["created_utc"] >= since_ts]
        score_pool(recent)
        fresh = sorted((it for it in recent if it["created_utc"] >= fresh_since),
                       key=lambda x: -x["score"])
        for it in fresh:
            it["age_bucket"] = "fresh"
        sig, rest = [], []
        for it in old:
            it["age_bucket"] = "lingering"
            if src == "hn" and it["extra"].get("hn_id") in front:
                it["lingering_signal"] = "HN 首页在榜"
            elif src == "hf" and it["extra"].get("trending"):
                it["lingering_signal"] = "HF trending 在榜"
            if not it.get("lingering_signal") and it["score"] >= 0.7:
                it["lingering_signal"] = f"{linger_days} 天热榜前列"
            (sig if it.get("lingering_signal") else rest).append(it)
        lingering = sig + rest
        if picks is not None:
            by_sid = {it["sid"]: it for it in old}
            missing = [s for s in picks.get(src, []) if s not in by_sid]
            if missing:
                sys.exit(f"精选 sid 不在 {src} 的 lingering 池里: {missing}")
            lingering = [by_sid[s] for s in picks.get(src, [])]
            for it in lingering:
                it["lingering_signal"] = "编辑精选"
        pools[src] = {"fresh": fresh, "lingering": lingering, "signal": len(sig),
                      "old_all": old}
    return pools, {"hn": hn_st, "hf": hf_st}


def dump_candidates(pools, path, top=80):
    """lingering 池按评论量降序导出，供人工挑精选。HF 无评论数，按 upvotes。"""
    out = {}
    for src in ("hn", "hf"):
        key = "comments" if src == "hn" else "votes"
        rows = sorted(pools[src]["old_all"],
                      key=lambda x: -((x.get("metrics") or {}).get(key) or 0))[:top]
        out[src] = [{"sid": it["sid"], "title": it["title"], "url": it["target_url"],
                     "permalink": it["permalink"], "metrics": it["metrics"],
                     "date": datetime.fromtimestamp(it["created_utc"], timezone.utc)
                     .date().isoformat()} for it in rows]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log(f"✓ 写入 {path}（HN {len(out['hn'])} / HF {len(out['hf'])} 条候选）")


def cut_batches(pools, per, max_batches, linger_share=0.3):
    """第 k 批 = 各信源 fresh 池与 lingering 池各自的第 k 段，约 70/30 合并。
    只保留两边都凑得满的批次。"""
    n_old = {s: round(per[s] * linger_share) for s in pools}
    n_new = {s: per[s] - n_old[s] for s in pools}
    counts = {s: min(len(pools[s]["fresh"]) // n_new[s],
                     len(pools[s]["lingering"]) // max(n_old[s], 1)) for s in pools}
    n = min(counts.values())
    if max_batches:
        n = min(n, max_batches)
    batches = []
    for k in range(n):
        items, ranges = [], {}
        for src in ("hn", "hf"):
            lo, hi = n_new[src] * k, n_new[src] * (k + 1)
            items.extend(pools[src]["fresh"][lo:hi])
            lo2, hi2 = n_old[src] * k, n_old[src] * (k + 1)
            items.extend(pools[src]["lingering"][lo2:hi2])
            ranges[src] = [per[src] * k + 1, per[src] * (k + 1)]   # 批内连续名次
        batches.append({"n": k + 1, "ranges": ranges, "items": items})
    return batches, counts


def main():
    ap = argparse.ArgumentParser(description="切批次的新闻板取数")
    ap.add_argument("--out", default="board_data.json")
    ap.add_argument("--content-out", default="board_content.json")
    ap.add_argument("--window-days", type=int, default=14)
    ap.add_argument("--hn-per-batch", type=int, default=30)
    ap.add_argument("--hf-per-batch", type=int, default=10)
    ap.add_argument("--hn-min-points", type=int, default=20)
    ap.add_argument("--enrich-batches", type=int, default=2,
                    help="只对前 N 批抓正文（正文只是写摘要的输入）")
    ap.add_argument("--max-batches", type=int, default=0, help="0 = 不限，由候选数决定")
    ap.add_argument("--content-limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--linger-days", type=int, default=0,
                    help="lingering 池回溯天数；0 = 与 --window-days 相同")
    ap.add_argument("--linger-share", type=float, default=0.3,
                    help="每批中 lingering 条目占比")
    ap.add_argument("--dump-candidates", default=None, metavar="PATH",
                    help="只导出 lingering 候选（按评论量）供人工挑选，然后退出")
    ap.add_argument("--picks", default=None, metavar="PATH",
                    help='编辑精选 JSON：{"hn": [sid...], "hf": [sid...]}，替换 lingering 池')
    args = ap.parse_args()

    now = int(time.time())
    since = now - args.window_days * 86400
    linger_days = args.linger_days or args.window_days
    linger_since = now - linger_days * 86400
    per = {"hn": args.hn_per_batch, "hf": args.hf_per_batch}
    picks = None
    if args.picks:
        with open(args.picks, encoding="utf-8") as f:
            picks = json.load(f)

    print("▸ 采集候选池", file=sys.stderr)
    pools, status = build_pools(since, args.hn_min_points, args.window_days + 1,
                                linger_since=linger_since, picks=picks)
    for s in ("hn", "hf"):
        log(f"  {s:<3} fresh {len(pools[s]['fresh'])} 条 / lingering "
            f"{len(pools[s]['lingering'])} 条（其中仍在榜 {pools[s]['signal']}）")

    if args.dump_candidates:
        dump_candidates(pools, args.dump_candidates)
        return

    batches, counts = cut_batches(pools, per, args.max_batches, args.linger_share)
    if not batches:
        sys.exit("候选不足，一批都凑不满 —— 检查窗口天数或 --hn-min-points")
    lim = min(counts, key=counts.get)
    log(f"  共 {len(batches)} 批 × {sum(per.values())} 条 = "
        f"{len(batches) * sum(per.values())} 条（批次数受 {lim} 限制）")

    n_enrich = min(args.enrich_batches, len(batches))
    todo = [it for b in batches[:n_enrich] for it in b["items"]]
    if todo:
        print(f"▸ 抓取前 {n_enrich} 批正文（{len(todo)} 条）", file=sys.stderr)
        for i, it in enumerate(todo, 1):
            enrich(it, args.content_limit)
            flag = "需 WebFetch 补" if it["needs_webfetch"] else f"{len(it['content'])} 字符"
            log(f"[{i}/{len(todo)}] {it['source']:<3} {flag:<14} {it['title'][:52]}")

    gen = datetime.now(timezone.utc)
    data = {
        "generated_at": gen.isoformat().replace("+00:00", "Z"),
        "generated_at_local": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": {"days": args.window_days, "since_utc": since,
                   "since_iso": datetime.fromtimestamp(since, timezone.utc)
                   .isoformat().replace("+00:00", "Z")},
        "config": {"hn_per_batch": per["hn"], "hf_per_batch": per["hf"],
                   "hn_min_points": args.hn_min_points,
                   "enriched_batches": n_enrich, "batch_limited_by": lim,
                   "linger_days": linger_days, "linger_share": args.linger_share,
                   "curated": picks is not None},
        "pool": {s: {"fresh": len(pools[s]["fresh"]), "lingering": len(pools[s]["old_all"]),
                     "signal": pools[s]["signal"]} for s in pools},
        "source_status": status,
        "batches": [{"n": b["n"], "ranges": b["ranges"],
                     "items": [slim(it) for it in b["items"]]} for b in batches],
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    # 正文单独存：它是我写摘要的输入，不进页面
    content = {}
    for b in batches[:n_enrich]:
        for it in b["items"]:
            content[it["sid"]] = {
                "batch": b["n"], "source": it["source"], "title": it["title"],
                "permalink": it["permalink"], "target_url": it["target_url"],
                "metrics": it["metrics"], "needs_webfetch": it.get("needs_webfetch"),
                "content": it.get("content", ""), "discussion": it.get("discussion", ""),
            }
    with open(args.content_out, "w", encoding="utf-8") as f:
        json.dump(content, f, ensure_ascii=False, indent=1)

    total = sum(len(b["items"]) for b in data["batches"])
    log(f"\n✓ 写入 {args.out}（{len(batches)} 批 / {total} 条）")
    log(f"✓ 写入 {args.content_out}（{len(content)} 条正文，供写摘要）")
    need = sum(1 for v in content.values() if v["needs_webfetch"])
    if need:
        log(f"  其中 {need} 条正文不足，需用 WebFetch 补齐后再写摘要")


if __name__ == "__main__":
    main()
