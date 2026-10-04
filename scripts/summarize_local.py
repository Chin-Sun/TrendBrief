#!/usr/bin/env python3
"""
summarize_local.py — 用本地 llama-server 为缺摘要的条目写 200 字中文摘要

    python3 summarize_local.py --work .work
    python3 summarize_local.py --work .work --dry-run --limit 2   # 只看提示词

读 digest_data.json（日报）与 board_content.json（新闻板前几批的正文），
只补 summaries.json 里还没有的 sid —— 已有摘要不覆盖（--redo / --redo-all 除外）。
写作规范与 SKILL.md 第二段一致。校验不过的条目跳过，页面照常标「摘要待补」，
绝不塞低质量文本。哪些摘要出自本地模型记在 summaries_meta.json。

服务端是 llama.cpp 的 llama-server（OpenAI 兼容接口），启动方式见 README。
只依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from fetch_rank import DEFAULT_LIMIT, MIN_TEXT, extract_page_text, log

SYSTEM_PROMPT = """你是技术资讯编辑，为中文读者写单条热帖的摘要。

要求：
1. 用简体中文写一段 200 字左右的摘要（150–300 字），不分段，不用标题、列表或 Markdown。
2. 讲清三件事：这条讲了什么（具体的事实、数字、方法）；为什么现在值得看；社区在争什么（若给了讨论区）。
3. 不要复述标题，不要写「这篇文章讨论了」「本文介绍了」之类的空话，直接进入内容。
4. 只依据给出的正文与讨论，不得编造任何事实、数字或引语。材料不足就少写，不要补充。
5. 若提示「正文未能获取」，基于标题和讨论如实写，并在摘要中注明「正文未能获取」。
6. 专有名词、模型名、项目名保留英文原文。
只输出摘要正文本身。"""

EMPTY_PHRASES = ("这篇文章", "本文介绍", "本文讨论", "该文章讨论", "文章讨论了", "以下是", "摘要：")
DISCUSSION_LIMIT = 1500


# ---------------------------------------------------------------- 输入

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    """先写临时文件再改名，中途中断也不会留下半截 JSON。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def collect_items(work, only):
    """返回 [(sid, item)]，日报在前、新闻板在后，按 sid 去重。"""
    out, seen = [], set()
    if only in (None, "digest"):
        d = load_json(os.path.join(work, "digest_data.json"), {})
        for sec in d.get("sections", []):
            for it in sec.get("items", []):
                if it["sid"] not in seen:
                    seen.add(it["sid"])
                    out.append((it["sid"], it))
    if only in (None, "board"):
        b = load_json(os.path.join(work, "board_content.json"), {})
        for sid, it in b.items():
            if sid not in seen:
                seen.add(sid)
                out.append((sid, it))
        # 前几批之外没抓正文的条目：标 needs_webfetch，build_prompt 会现场补抓
        d = load_json(os.path.join(work, "board_data.json"), {})
        for bt in d.get("batches", []):
            for it in bt.get("items", []):
                if it["sid"] not in seen:
                    seen.add(it["sid"])
                    out.append((it["sid"], {**it, "needs_webfetch": True,
                                            "content": "", "discussion": ""}))
    return out


# ---------------------------------------------------------------- 提示词

def fmt_metrics(m):
    names = {"comments": "评论", "shares": "转发", "votes": "点赞"}
    return "，".join(f"{names.get(k, k)} {v}" for k, v in (m or {}).items() if v is not None)


def build_prompt(it, content_limit):
    content = (it.get("content") or "").strip()
    if it.get("needs_webfetch") and it.get("target_url"):
        try:
            text, err = extract_page_text(it["target_url"], content_limit)
        except Exception as e:                       # noqa: BLE001
            text, err = "", str(e)
        if len(text) > len(content):
            content = text
        log(f"  补抓正文 {len(content)} 字符" + (f"（{err}）" if err else ""))
    parts = [f"标题：{it.get('title', '')}",
             f"信源：{it.get('source_label') or it.get('source', '')}",
             f"互动：{fmt_metrics(it.get('metrics'))}"]
    if len(content) < MIN_TEXT // 4:
        parts.append("正文：正文未能获取。")
    else:
        parts.append("正文：\n" + content[:content_limit])
    disc = (it.get("discussion") or "").strip()
    if disc:
        parts.append("讨论区热评：\n" + disc[:DISCUSSION_LIMIT])
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 调用

def chat(server, messages, temperature, max_tokens, timeout):
    body = json.dumps({"messages": messages, "temperature": temperature,
                       "top_p": 0.9, "min_p": 0.05, "repeat_penalty": 1.05,
                       "max_tokens": max_tokens, "stream": False}).encode()
    req = urllib.request.Request(server.rstrip("/") + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    text = resp["choices"][0]["message"].get("content") or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip(), resp.get("model")


def check(text, title):
    """返回不合格原因；合格返回 None。"""
    han = len(re.findall(r"[一-鿿]", text))
    if han < 100:
        return f"中文只有 {han} 字，太短或不是中文"
    if len(text) > 500:
        return f"共 {len(text)} 字符，太长"
    if title and text.startswith(title[:15]):
        return "开头在复述标题"
    for p in EMPTY_PHRASES:
        if p in text:
            return f"出现空话「{p}」"
    if "\n\n" in text or text.lstrip().startswith(("#", "-", "*")):
        return "分段或用了 Markdown"
    return None


def server_ok(server):
    try:
        with urllib.request.urlopen(server.rstrip("/") + "/health", timeout=5) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="用本地 llama-server 补写中文摘要")
    ap.add_argument("--work", default=".work", help="digest_data.json 等所在目录")
    ap.add_argument("--summaries", default=None, help="默认 <work>/summaries.json")
    ap.add_argument("--server", default=os.environ.get("LLAMA_SERVER", "http://127.0.0.1:8080"))
    ap.add_argument("--only", choices=("digest", "board"), default=None)
    ap.add_argument("--limit", type=int, default=0, help="最多写 N 条，0 = 不限")
    ap.add_argument("--redo", action="append", default=[], metavar="SID",
                    help="强制重写该条（可多次指定）")
    ap.add_argument("--redo-all", action="store_true",
                    help="用本地模型重写当前数据里的全部条目（已由本地模型写过的跳过，可断点续跑）")
    ap.add_argument("--content-limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--max-tokens", type=int, default=700)
    ap.add_argument("--timeout", type=int, default=300, help="单次请求超时秒数")
    ap.add_argument("--dry-run", action="store_true", help="只打印提示词，不调用模型")
    args = ap.parse_args()

    spath = args.summaries or os.path.join(args.work, "summaries.json")
    mpath = os.path.join(os.path.dirname(spath) or ".", "summaries_meta.json")
    summaries = load_json(spath, {})
    meta = load_json(mpath, {})
    redo = set(args.redo)

    def has(sid):
        return bool((summaries.get(sid) or "").strip())

    todo = [(sid, it) for sid, it in collect_items(args.work, args.only)
            if sid in redo or not has(sid) or (args.redo_all and sid not in meta)]
    if args.limit:
        todo = todo[:args.limit]
    n_redo = sum(1 for sid, _ in todo if has(sid))
    log(f"待写摘要 {len(todo)} 条（重写 {n_redo} / 新补 {len(todo) - n_redo}）")
    if not todo:
        return
    if not args.dry_run and not server_ok(args.server):
        sys.exit(f"连不上 {args.server}/health —— 先启动 llama-server")

    done, skipped, t0 = 0, [], time.time()
    for i, (sid, it) in enumerate(todo, 1):
        title = it.get("title", "")
        log(f"[{i}/{len(todo)}] {sid} {title[:50]}")
        prompt = build_prompt(it, args.content_limit)
        if args.dry_run:
            print(f"===== {sid} =====\n{prompt}\n", flush=True)
            continue
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}]
        text, why, model = "", None, None
        for attempt in range(2):                     # 不合格就带着原因重试一次
            t1 = time.time()
            try:
                text, model = chat(args.server, messages, args.temperature,
                                   args.max_tokens, args.timeout)
            except Exception as e:                   # noqa: BLE001
                why = f"请求失败：{e}"
                break
            why = check(text, title)
            log(f"  {len(text)} 字符，{time.time() - t1:.1f}s" + (f"，不合格：{why}" if why else ""))
            if not why:
                break
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"不合格：{why}。请按要求重写，只输出摘要正文。"}]
        if why:
            skipped.append((sid, why))
            continue
        summaries[sid] = text
        meta[sid] = {"model": model or "local", "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        save_json(spath, summaries)                  # 每条落盘，中断不丢进度
        save_json(mpath, meta)
        done += 1

    if args.dry_run:
        return
    log(f"完成：写入 {done} 条，跳过 {len(skipped)} 条，用时 {time.time() - t0:.0f}s")
    for sid, why in skipped:
        tail = "保留原摘要" if has(sid) else "页面上将标「摘要待补」"
        log(f"  跳过 {sid}：{why}（{tail}）")


if __name__ == "__main__":
    main()
