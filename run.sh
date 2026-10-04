#!/usr/bin/env bash
# run.sh — 终端一条命令刷新 TrendBrief（取数 → 排名 → 渲染 → 写入站点文件）
#
#   ./run.sh              等同 all
#   ./run.sh fetch        只取数（日报 + 新闻板），产出 .work/*.json
#   ./run.sh summarize    用本地 llama-server 给缺摘要的条目写中文摘要，写完自动 render
#   ./run.sh render       只渲染，把结果写到 index.html / digest.html
#   ./run.sh all          fetch + summarize（本地模型在线时）+ render
#   ./run.sh status       统计当前数据里有多少条已有摘要、多少条待补
#
# 选项（放在子命令后）：
#   --reddit-json PATH    Reddit 浏览器导出的 .json 或打印版 .pdf（缺省则按凭证 / 跳过降级）
#   --hn-min-points N     HN 候选门槛，默认 20
#   --no-content          跳过正文抓取，只验证排名链路
#   --limit N             summarize 最多写 N 条（试跑用）
#   --redo-all            summarize 用本地模型重写全部条目（已由本地模型写过的跳过，可续跑）
#
# 摘要：已有摘要会从已发布页面恢复并合并进 .work/summaries.json，永不覆盖。
# 缺的由 summarize 交给本地模型写（需先手动启动 llama-server，地址可用环境变量
# LLAMA_SERVER 覆盖，默认 http://127.0.0.1:8080）；模型没起来时 all 会跳过这一步，
# 新条目在页面上显式标「摘要待补」。
#
# 只用 Python 3 标准库，无需 pip install。不做 git commit / push —— 发布由你决定。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS="$ROOT/scripts"
WORK="$ROOT/.work"
SUMMARIES="$WORK/summaries.json"

cmd="${1:-all}"
[[ $# -gt 0 ]] && shift
[[ "$cmd" == -h || "$cmd" == --help ]] && { sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0; }

REDDIT_JSON=""
HN_MIN=20
NO_CONTENT=0
LIMIT=0
REDO_ALL=0
LLAMA_SERVER="${LLAMA_SERVER:-http://127.0.0.1:8080}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reddit-json)   REDDIT_JSON="${2:?--reddit-json 需要路径}"; shift 2 ;;
    --hn-min-points) HN_MIN="${2:?--hn-min-points 需要数字}"; shift 2 ;;
    --no-content)    NO_CONTENT=1; shift ;;
    --limit)         LIMIT="${2:?--limit 需要数字}"; shift 2 ;;
    --redo-all)      REDO_ALL=1; shift ;;
    -h|--help)       sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知选项: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "$WORK"

log() { printf '\033[1m==> %s\033[0m\n' "$*" >&2; }

# 从已发布页面恢复摘要并合并进 summaries.json（已有条目不被覆盖）
recover_summaries() {
  python3 - "$ROOT" "$SUMMARIES" <<'PY'
import html, json, os, re, sys
root, out = sys.argv[1:3]
got = {}
try:
    got = json.load(open(out, encoding="utf-8"))
except (OSError, ValueError):
    pass
n0 = len(got)

def add(sid, text):
    text = (text or "").strip()
    if sid and text and sid not in got:
        got[sid] = text

# 新闻板：<script id="summaries"> 就是完整 JSON
p = os.path.join(root, "index.html")
if os.path.exists(p):
    m = re.search(r'<script[^>]*id="summaries"[^>]*>(.*?)</script>',
                  open(p, encoding="utf-8").read(), re.S)
    if m:
        try:
            for k, v in json.loads(m.group(1)).items():
                add(k, v)
        except ValueError:
            print("index.html 里的 summaries 解析失败，已跳过", file=sys.stderr)

# 日报：每个 article[data-sid] 下的 p.sum
p = os.path.join(root, "digest.html")
if os.path.exists(p):
    src = open(p, encoding="utf-8").read()
    for m in re.finditer(r'data-sid="([^"]+)".*?<p class="sum">(.*?)</p>', src, re.S):
        add(m.group(1), html.unescape(re.sub(r"<[^>]+>", "", m.group(2))))

json.dump(got, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"summaries.json: {len(got)} 条（本次从已发布页面恢复 {len(got) - n0} 条）", file=sys.stderr)
PY
}

do_fetch() {
  log "日报取数 (fetch_rank.py)"
  args=(--out "$WORK/digest_data.json" --hn-min-points "$HN_MIN")
  [[ -n "$REDDIT_JSON" ]] && args+=(--reddit-json "$REDDIT_JSON")
  [[ "$NO_CONTENT" == 1 ]] && args+=(--no-content)
  python3 "$SCRIPTS/fetch_rank.py" "${args[@]}"

  log "新闻板取数 (fetch_batches.py)"
  args=(--out "$WORK/board_data.json" --content-out "$WORK/board_content.json"
        --hn-min-points "$HN_MIN")
  python3 "$SCRIPTS/fetch_batches.py" "${args[@]}"
}

server_up() { curl -sf -m 5 "$LLAMA_SERVER/health" >/dev/null 2>&1; }

do_summarize() {
  for f in digest_data.json board_data.json; do
    [[ -f "$WORK/$f" ]] || { echo "缺 $WORK/$f，请先 ./run.sh fetch" >&2; exit 1; }
  done
  recover_summaries                     # 先恢复已发布的摘要，避免重写
  log "本地模型写摘要 (summarize_local.py → $LLAMA_SERVER)"
  args=(--work "$WORK" --server "$LLAMA_SERVER" --limit "$LIMIT")
  [[ "$REDO_ALL" == 1 ]] && args+=(--redo-all)
  python3 "$SCRIPTS/summarize_local.py" "${args[@]}"
}

do_render() {
  for f in digest_data.json board_data.json; do
    [[ -f "$WORK/$f" ]] || { echo "缺 $WORK/$f，请先 ./run.sh fetch" >&2; exit 1; }
  done
  recover_summaries

  log "渲染新闻板 → index.html"
  python3 "$SCRIPTS/render_board.py" "$WORK/board_data.json" "$SUMMARIES" \
    -o "$WORK/daily_bull.html" --sibling "📰 日报|digest.html" --site-out "$ROOT/index.html"

  log "渲染日报 → digest.html"
  python3 "$SCRIPTS/render_html.py" "$WORK/digest_data.json" "$SUMMARIES" \
    -o "$WORK/digest_$(date +%F).html" --sibling "🐮 新闻板|index.html" --site-out "$ROOT/digest.html"
}

do_status() {
  [[ -f "$SUMMARIES" ]] || recover_summaries
  python3 - "$WORK" <<'PY'
import json, os, sys
w = sys.argv[1]
try:
    s = {k for k, v in json.load(open(f"{w}/summaries.json", encoding="utf-8")).items() if (v or "").strip()}
except (OSError, ValueError):
    s = set()
def count(name, sids):
    print(f"{name}: {len(sids & s)}/{len(sids)} 条有摘要，{len(sids - s)} 条待补")
p = f"{w}/digest_data.json"
if os.path.exists(p):
    d = json.load(open(p, encoding="utf-8"))
    ids = set()
    def walk(o):
        if isinstance(o, dict):
            if "sid" in o: ids.add(o["sid"])
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(d); count("日报", ids)
p = f"{w}/board_data.json"
if os.path.exists(p):
    d = json.load(open(p, encoding="utf-8"))
    count("新闻板", {it["sid"] for b in d["batches"] for it in b["items"]})
PY
}

case "$cmd" in
  fetch)  do_fetch ;;
  summarize) do_summarize; do_render ;;
  render) do_render ;;
  all)    do_fetch
          if server_up; then do_summarize
          else log "本地模型未启动（$LLAMA_SERVER），跳过写摘要"; fi
          do_render; do_status ;;
  status) do_status ;;
  *) echo "用法: ./run.sh [fetch|summarize|render|all|status] [选项]" >&2; exit 2 ;;
esac

log "完成"
