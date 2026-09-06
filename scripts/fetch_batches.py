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

from fetch_rank import (DEFAULT_LIMIT, assign_sid, cross_source_dedup, enrich,
                        fetch_hf, fetch_hn, log, score_pool)

# 页面用得上的字段。正文另存一份 —— 几百 KB 正文没必要塞进产物。
KEEP = ("sid", "source", "source_label", "title", "permalink", "target_url",
        "created_utc", "published_iso", "metrics", "score",
        "also_seen_in", "also_links")


def slim(it):
    out = {k: it[k] for k in KEEP if k in it}
    ex = it.get("extra") or {}
    out["extra"] = {k: ex[k] for k in ("hn_id", "paper_id", "author") if ex.get(k)}
    return out


def build_pools(since_ts, min_points, hf_days):
    hn, hn_st = fetch_hn(since_ts, min_points)
    hf, hf_st = fetch_hf(days=hf_days)

    # 去重放在打分之前 —— 同一篇论文常同时出现在 HN 与 HF，重复计入会污染分位
    kept = cross_source_dedup([*hn, *hf])
    pools = {"hn": [], "hf": []}
    for it in kept:
        if it["created_utc"] >= since_ts and it["source"] in pools:
            pools[it["source"]].append(it)

    for src, items in pools.items():
        score_pool(items)                       # 同源同窗口内取分位，写入 score
        items.sort(key=lambda x: -x["score"])
        for it in items:
            assign_sid(it)
    return pools, {"hn": hn_st, "hf": hf_st}


def cut_batches(pools, per, max_batches):
    """第 k 批 = 各信源池的 [per*k, per*k+per)。只保留两边都凑得满的批次。"""
    counts = {s: len(pools[s]) // per[s] for s in pools}
    n = min(counts.values())
    if max_batches:
        n = min(n, max_batches)
    batches = []
    for k in range(n):
        items, ranges = [], {}
        for src in ("hn", "hf"):
            lo, hi = per[src] * k, per[src] * k + per[src]
            items.extend(pools[src][lo:hi])
            ranges[src] = [lo + 1, hi]
        batches.append({"n": k + 1, "ranges": ranges, "items": items})
    return batches, counts


def main():
    ap = argparse.ArgumentParser(description="切批次的新闻板取数")
    ap.add_argument("--out", default="board_data.json")
    ap.add_argument("--content-out", default="board_content.json")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--hn-per-batch", type=int, default=30)
    ap.add_argument("--hf-per-batch", type=int, default=10)
    ap.add_argument("--hn-min-points", type=int, default=20)
    ap.add_argument("--enrich-batches", type=int, default=2,
                    help="只对前 N 批抓正文（正文只是写摘要的输入）")
    ap.add_argument("--max-batches", type=int, default=0, help="0 = 不限，由候选数决定")
    ap.add_argument("--content-limit", type=int, default=DEFAULT_LIMIT)
    args = ap.parse_args()

    now = int(time.time())
    since = now - args.window_days * 86400
    per = {"hn": args.hn_per_batch, "hf": args.hf_per_batch}

    print("▸ 采集候选池", file=sys.stderr)
    pools, status = build_pools(since, args.hn_min_points, args.window_days + 1)
    for s in ("hn", "hf"):
        log(f"  {s:<3} 窗口内 {len(pools[s])} 条 → 可切 {len(pools[s]) // per[s]} 批")

    batches, counts = cut_batches(pools, per, args.max_batches)
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
                   "enriched_batches": n_enrich, "batch_limited_by": lim},
        "pool": {s: len(pools[s]) for s in pools},
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
