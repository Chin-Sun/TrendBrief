---
name: tech-news-digest
description: 从 Hacker News、r/LocalLLaMA、Hugging Face Papers、dev.to 四个信源抓取近期热帖，按评论量/转发量/点赞数的统一口径排名，产出带 200 字中文摘要、NYT 风格排版的 HTML 简报。当用户要「技术新闻简报」「最新热帖」「AI 资讯汇总」「爬这几个网站给我最新消息」「今天有什么值得看的」时使用。
---

# 技术热帖简报

产出一份 20 条的 HTML 简报：七日要闻 Top10 + 三日速览 Top5 + Hacker News 专属三日 Top5。

## 工作流：三段式

脚本做确定性工作（抓取、排名、配额），你做语言工作（200 字中文摘要）。**不要把摘要塞进脚本，也不要在对话里手算排名。**

### 第一段 · 取数

```bash
python3 <skill>/scripts/fetch_rank.py --out digest_data.json
```

完成抓取 → 跨信源去重 → 分位归一 → 打分 → 名额分配 → 入选条目正文提取，写出 `digest_data.json`。

常用参数：

| 参数 | 用途 |
|---|---|
| `--reddit-json <path>` | 模式 B：读浏览器导出的 Reddit JSON；传 `.pdf` 则自动走打印版还原 |
| `--hn-min-points N` | HN 候选门槛，默认 20 |
| `--content-limit N` | 单条正文截断长度，默认 3000 |
| `--no-content` | 跳过正文抓取，仅验证排名链路 |
| `--allow-cross-section-dupes` | 允许同一条目跨版块重复出现 |

脚本会在 stderr 打印每个信源的候选数、各版块实际配额、逐条正文长度。**读这些输出**，它会告诉你哪个信源没采到、哪些条目正文不足。

### 第二段 · 写摘要（你来做）

读 `digest_data.json`，对每条写 **200 字左右的中文总结**。

1. **先补正文**：`needs_webfetch: true` 的条目正文不足 400 字符，用 WebFetch 工具抓 `target_url` 补齐。通常只有一两条。
2. **依据**：优先用 `content` 字段；HN 条目另有 `discussion`（帖子正文 + 前 6 条热评），写摘要时把讨论区的争议点带上，这是 HN 内容最有价值的部分。
3. **写什么**：讲清这条讲了什么、为什么现在值得看、社区在争什么。**不要复述标题**，不要写「这篇文章讨论了……」这类空话。
4. **抓不到正文时**：基于标题和元数据如实写，并在摘要里说明「正文未能获取」。**绝不编造内容。**
5. 输出 `summaries.json`，键是条目的 `sid`（形如 `hn-49437283`）：

```json
{
  "hn-49437283": "Nitter 与 XCancel 收到 X 公司的停止侵权函……",
  "hf-2608.25518": "该论文提出……"
}
```

### 第三段 · 渲染

```bash
python3 <skill>/scripts/render_html.py digest_data.json summaries.json \
    -o digest_$(date +%F).html
```

缺摘要的条目会在页面上显式标出并计入编者按 —— 脚本不会用任何自动文本填充。渲染后 `open digest_*.html` 给用户看。

页面报头下有**频道过滤条**（全部 / 各信源），点击即按信源筛选，序号与头条随当前视图重排，空版块整块收起；未采集的信源渲染成禁用状态并在 `title` 里写明原因。链接加 `#ch=hn` 之类可直接落到指定频道。禁用 JavaScript 时频道条隐藏、全部条目照常显示。

## Reddit：三模式降级

Reddit 是四个信源里唯一封锁免鉴权访问的（`.json` 返回反爬 HTML、RSS 返回 0 字节、公共镜像 403）。

- **模式 A · 凭证（首选）**：`~/.claude/.reddit_env` 内配置

  ```
  REDDIT_CLIENT_ID=...
  REDDIT_CLIENT_SECRET=...
  REDDIT_UA=claude-news-digest/1.0 by u/用户名
  ```

  凭证来自 https://www.reddit.com/prefs/apps 注册的 script 类型应用，走 `client_credentials` 只读授权。`REDDIT_UA` 必须含用户名，通用 UA 会被限流。**凭证只经环境变量传递，绝不写入产物、不打印、不进日志。**

- **模式 B · 浏览器导出**：让用户在已登录 Reddit 的浏览器中打开
  `https://www.reddit.com/r/LocalLLaMA/top.json?t=week&limit=100`，另存为文件，再用 `--reddit-json <path>` 传入。真实浏览器会话能绕过针对脚本的反爬。

  **推荐用户用「右键 → 存储为」或 DevTools Network 面板复制响应体**，直接得到 `.json`，`limit=100` 也能生效。

- **模式 B-pdf · 打印版 PDF**：用户若把 JSON 页面打印成了 PDF，`--reddit-json` 传 `.pdf` 即可，`scripts/pdf_to_reddit_json.py` 会还原成 listing。也可单独跑：

  ```bash
  python3 <skill>/scripts/pdf_to_reddit_json.py top.pdf -o reddit.json
  ```

  已实测可用（10 条全部还原、核心字段无缺）。但要如实告诉用户两个限制：**打印时 `limit` 参数往往不生效，实测只得 10 条**，候选池比其他信源浅得多；标题末尾的 emoji 可能因无字形而丢失。字段缺失的条目会被丢弃并在 stderr 列出，**不静默补默认值**。

- **模式 C · 跳过**：无凭证也无导出文件时自动降级。Reddit 的名额按优先级**轮转**分给其余信源，页面编者按显式说明「本期未采集」。**不要假装采到了。**

## 排名口径

完整定义见 `references/ranking.md`。要点：

- 只用平台原生真实计数，**不构造代理指标**。「点击率」在四个信源全部不公开，不参与排名。
- 权重：评论量 0.5 › 转发量 0.3（仅 Reddit 公开）› 点赞/票数 0.2；缺失指标的权重按比例分给可得指标。
- 同信源同窗口内做分位归一（非极值归一，因互动数据重尾）。
- 名额固定，不做跨平台分数比较。版块内先按信源优先级、再按组内得分排序。

用户要改口径时，**先改 `references/ranking.md`，再改脚本**，别让两者漂移。

## 常见坑

- HN Algolia 的 `>` 必须写成 `%3E`，裸 `>` 会被 shell 当重定向符。
- HF 的 `papers/trending` 是**历史热门榜**：实测 50 条里仅 8 条在 7 天内、0 条在 3 天内。必须靠 `api/daily_papers?date=` 逐日补齐。daily_papers 周末不发布，逐日拉取遇到空日属正常。
- 环境无 requests/bs4，全部走标准库。别 `pip install`。
