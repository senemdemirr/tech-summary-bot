

import os
import re
import time
import json
import hashlib
import calendar
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, List, Any

import requests
import feedparser
from dateutil import parser as dtparser
from bs4 import BeautifulSoup

FEEDS: Dict[str, str] = {
    "react": "https://react.dev/rss.xml",
    "nextjs": "https://github.com/vercel/next.js/releases.atom",
    "angular": "https://blog.angular.dev/feed",
    "java":"https://inside.java/feed.xml",
    "dotnet":"https://devblogs.microsoft.com/dotnet/feed/",
    "claude":"https://status.claude.com/history.rss",
    "openai": "https://openai.com/news/rss.xml",
    "google_ai": "https://blog.google/technology/ai/rss/",
    "deepmind": "https://deepmind.google/blog/rss.xml",
    "arxiv_ai": "https://rss.arxiv.org/rss/cs.AI",
    "mit_ai": "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    "huggingface": "https://hf.co/blog/feed.xml",
    "meta_ai": "https://rsshub.rssforever.com/meta/ai/blog",
}

BASE_DIR = "txtler"
COMBINED_DIR = os.path.join(BASE_DIR, "_combined")

REQUEST_TIMEOUT = 25
SLEEP_BETWEEN_REQUESTS_SEC = 0.5

NOW_UTC = datetime.now(timezone.utc)
CUTOFF_UTC = NOW_UTC - timedelta(hours=24)

MIN_FEED_TEXT_CHARS = 100

MAX_KEEP_PER_SITE = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; rss-to-txt/1.0)"
}


STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is", "are", "was", "were", "be",
    "this", "that", "these", "those", "from", "as", "at", "by", "it", "its", "we", "you", "they", "their",
    "i", "our", "your", "not", "can", "will", "may", "new", "now", "more", "most", "into", "than", "about",
    "what", "when", "how", "why", "who",
}

SITE_WEIGHT = {
    "openai": 1.40,
    "deepmind": 1.30,
    "google_ai": 1.20,
    "huggingface": 1.15,
    "mit_ai": 1.10,
    "arxiv_ai": 1.10,
    "meta_ai": 1.05,
    "react": 1.00,
    "nextjs": 1.00,
    "angular": 1.00,
    "java":1.15,
    "dotnet":1.15,
    "claude":1.15
}

PATTERNS = [
    ("security_or_policy", 3.0, r"\b(security|vulnerability|cve|policy|compliance|safety|governance)\b"),
    ("pricing_or_api",     2.3, r"\b(pricing|price|billing|api|rate limit|token|quota|endpoint)\b"),
    ("release_launch",     2.2, r"\b(release|launch|announc|introduc|available|rollout|update|changelog)\b"),
    ("research_paper",     2.0, r"\b(paper|preprint|arxiv|research|study|experiment)\b"),
    ("benchmark_eval",     2.0, r"\b(benchmark|eval|evaluation|leaderboard|mmlu|sota|state-of-the-art)\b"),
    ("dataset_data",       1.8, r"\b(dataset|data set|corpus|collection|annotation)\b"),
    ("engineering",        1.4, r"\b(performance|latency|throughput|memory|optimization|gpu|cuda)\b"),
]


def tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    toks = [t for t in text.split() if len(t) >= 3 and t not in STOPWORDS]
    return toks


def shannon_entropy(tokens: List[str]) -> float:
    if not tokens:
        return 0.0
    c = Counter(tokens)
    n = sum(c.values())
    ent = 0.0
    for _, v in c.items():
        p = v / n
        ent -= p * math.log(p + 1e-12)
    return ent


def topk_ngrams(tokens: List[str], n: int = 2, k: int = 25) -> set:
    if len(tokens) < n:
        return set()
    grams = [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]
    c = Counter(grams)
    return set([g for g, _ in c.most_common(k)])


def similarity_jaccard(set_a: set, set_b: set) -> float:
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def score_posts_for_site(site: str, items: List[Dict[str, Any]]) -> None:

    for it in items:
        full = (it.get("title", "") + "\n" + it.get("text", "")).strip()
        toks = tokenize(full)
        it["_tokens"] = toks
        it["_bigrams"] = topk_ngrams(toks, n=2, k=25)

    for i, it in enumerate(items):
        max_sim = 0.0
        for j, ot in enumerate(items):
            if i == j:
                continue
            sim = similarity_jaccard(it["_bigrams"], ot["_bigrams"])
            if sim > max_sim:
                max_sim = sim
        it["_max_sim"] = max_sim  # 0..1

    site_w = SITE_WEIGHT.get(site, 1.0)

    for it in items:
        title = it.get("title", "") or ""
        text = it.get("text", "") or ""
        published_utc = it.get("published_utc")

        recency = 0.0
        if published_utc:
            hours_old = (NOW_UTC - published_utc).total_seconds() / 3600
            recency = max(0.0, 1.0 - (hours_old / 24.0))

        L = len(text)
        length_norm = min(L / 2500.0, 1.0)
        if L < 250:
            length_norm *= 0.2

        ent = shannon_entropy(it["_tokens"])
        info_density = min(ent / 6.0, 1.0)

        low = (title + " " + text).lower()
        pattern_score = 0.0
        for _, w, rgx in PATTERNS:
            if re.search(rgx, low):
                pattern_score += w
        pattern_norm = min(pattern_score / 8.0, 1.0)

        novelty = 1.0 - min(it["_max_sim"], 1.0)

        base = (
            0.30 * recency +
            0.20 * length_norm +
            0.20 * info_density +
            0.20 * pattern_norm +
            0.10 * novelty
        )

        it["score"] = 100.0 * base * site_w

    for it in items:
        it.pop("_tokens", None)
        it.pop("_bigrams", None)
        it.pop("_max_sim", None)

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = (name or "").strip().lower()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^\w\-_\.]+", "", name)
    name = name.strip("._-")
    if not name:
        name = "post"
    return name[:max_len]


def stable_id(entry: Any) -> str:
    for key in ("id", "guid"):
        if key in entry and entry.get(key):
            return str(entry.get(key))
    link = str(entry.get("link", ""))
    title = str(entry.get("title", ""))
    raw = (link + "||" + title).encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:16]


def parse_entry_datetime_utc(entry: Any) -> Optional[datetime]:

    for k in ("published_parsed", "updated_parsed"):
        st = entry.get(k)
        if st:
            return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)

    for k in ("published", "updated", "date"):
        s = entry.get(k)
        if s:
            try:
                dt = dtparser.parse(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass

    return None


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines).strip()


def extract_from_entry(entry: Any) -> str:
    if entry.get("content"):
        try:
            parts = []
            for c in entry["content"]:
                val = c.get("value", "")
                if val:
                    parts.append(html_to_text(val))
            joined = "\n\n".join([p for p in parts if p]).strip()
            if joined:
                return joined
        except Exception:
            pass

    for k in ("summary", "description", "subtitle"):
        if entry.get(k):
            t = html_to_text(entry.get(k, ""))
            if t:
                return t

    return ""


def fetch_page_text(url: str) -> str:

    if not url:
        return ""

    if "arxiv.org/abs/" in url:
        arxiv_id = url.split("arxiv.org/abs/")[-1].strip("/")
        url = f"https://arxiv.org/html/{arxiv_id}"

    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        html = r.text
    except Exception:
        return ""

    try:
        import trafilatura  
        downloaded = trafilatura.extract(html, include_comments=False, include_tables=False)
        if downloaded and downloaded.strip():
            return downloaded.strip()
    except Exception:
        pass

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    candidate = None
    for sel in ["article", "main", "#content", ".ltx_document"]:
        candidate = soup.select_one(sel)
        if candidate:
            break

    if candidate:
        return html_to_text(str(candidate))

    return html_to_text(html)


def build_output_text(text: str, source_url: str) -> str:
    text = (text or "").strip()
    source_url = (source_url or "").strip()

    if source_url:
        return f"{text}\n\nKAYNAK : {source_url}"
    else:
        return text


def write_post_file(site_dir: str, entry: Any, published_utc: Optional[datetime], text: str) -> str:
    title = (entry.get("title") or "post").strip()
    ts = published_utc.strftime("%Y%m%d_%H%M%S") if published_utc else NOW_UTC.strftime("%Y%m%d_%H%M%S")
    sid = stable_id(entry)
    fname = sanitize_filename(f"{ts}_{title}_{sid}.txt")
    path = os.path.join(site_dir, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.strip())
    return path


def run():
    ensure_dir(BASE_DIR)
    ensure_dir(COMBINED_DIR)

    combined_all: List[Dict[str, Any]] = []
    results_summary: Dict[str, Any] = {}

    for site, feed_url in FEEDS.items():
        site_dir = os.path.join(BASE_DIR, site)
        ensure_dir(site_dir)

        print(f"\n=== {site} ===")
        print(f"Feed: {feed_url}")

        feed = feedparser.parse(feed_url)
        entries = feed.entries or []

        saved_initial = 0

        for entry in entries:
            published_utc = parse_entry_datetime_utc(entry)
            if not published_utc:
                continue
            if published_utc < CUTOFF_UTC:
                continue

            text = extract_from_entry(entry)

            # --- FIXED INDENT BLOCK ---
            if site == "arxiv_ai":
                page_text = fetch_page_text(entry.get("link", ""))
                if len(page_text) > len(text):
                    text = page_text
            elif len(text) < MIN_FEED_TEXT_CHARS:
                page_text = fetch_page_text(entry.get("link", ""))
                if len(page_text) > len(text):
                    text = page_text

            text = (text or "").strip()
            if not text:
                continue

            source_url = entry.get("link", "")
            out_text = build_output_text(text, source_url)
            path = write_post_file(site_dir, entry, published_utc, out_text)

            title = (entry.get("title") or "").strip()

            combined_all.append({
                "site": site,
                "title": title,
                "text": out_text,
                "score": 0.0,
                "path": path,
                "published_utc": published_utc,
            })

            saved_initial += 1
            time.sleep(SLEEP_BETWEEN_REQUESTS_SEC)

        site_items = [x for x in combined_all if x["site"] == site]
        score_posts_for_site(site, site_items)

        removed_count = 0
        if len(site_items) > MAX_KEEP_PER_SITE:
            site_items.sort(key=lambda x: x["score"], reverse=True)
            keep = site_items[:MAX_KEEP_PER_SITE]
            remove = site_items[MAX_KEEP_PER_SITE:]

            for item in remove:
                try:
                    os.remove(item["path"])
                    removed_count += 1
                except Exception:
                    pass

            combined_all = [x for x in combined_all if x["site"] != site] + keep

        site_combined_path = os.path.join(COMBINED_DIR, f"{site}_last24h.txt")
        with open(site_combined_path, "w", encoding="utf-8") as f:
            final_site_items = sorted(
                [x for x in combined_all if x["site"] == site],
                key=lambda x: x["score"],
                reverse=True,
            )
            for item in final_site_items:
                f.write(
                    f'--- {item["site"]} | {item["title"]} | '
                    f'score={item["score"]:.2f} ---\n'
                )
                f.write(item["text"].strip())
                f.write("\n\n")

        results_summary[site] = {
            "feed": feed_url,
            "saved_initial_count": saved_initial,
            "kept_after_top5": len([x for x in combined_all if x["site"] == site]),
            "removed_due_to_top5": removed_count,
            "site_folder": site_dir,
            "site_combined": site_combined_path,
        }

        print(f"Initial saved (last24h): {saved_initial}")
        print(f"Removed (over top5): {removed_count}")
        print(f"Kept: {results_summary[site]['kept_after_top5']}")
        print(f"Folder: {site_dir}")
        print(f"Combined: {site_combined_path}")

    all_path = os.path.join(COMBINED_DIR, "all_last24h.txt")
    with open(all_path, "w", encoding="utf-8") as f:
        for item in sorted(combined_all, key=lambda x: x["score"], reverse=True):
            f.write(
                f'--- {item["site"]} | {item["title"]} | '
                f'score={item["score"]:.2f} ---\n'
            )
            f.write(item["text"].strip())
            f.write("\n\n")

    summary_path = os.path.join(COMBINED_DIR, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at_utc": NOW_UTC.isoformat(),
                "cutoff_utc": CUTOFF_UTC.isoformat(),
                "max_keep_per_site": MAX_KEEP_PER_SITE,
                "all_combined": all_path,
                "sites": results_summary,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("\n=== DONE ===")
    print(f"ALL combined: {all_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    run()
