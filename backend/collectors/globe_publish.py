"""
Build language-market sentiment signals for the homepage globe.

Input: data-branch reviews-samples.jsonl (real Steam review samples).
Output: data-branch globe-daily.json.

Important: Steam review language is NOT player geolocation. The coordinates are
visual centroids for language markets, not verified user locations. Steam's
voted_up field is a recommendation signal, not a model emotion label.
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(BACKEND, "analysis"))

import ci_publish as P
import samples_io as SIO
import topics as T
import game_config

OUT = "globe-daily.json"
MIN_REVIEWS = int(os.environ.get("GLOBE_MIN_REVIEWS", "20"))
PERIOD_DAYS = int(os.environ.get("GLOBE_PERIOD_DAYS", "7"))

# Centroids represent language markets only; they must never be described as player locations.
MARKETS = {
    "schinese": ("简体中文市场", "Simplified Chinese", 104.1954, 35.8617, "east_asia"),
    "tchinese": ("繁体中文市场", "Traditional Chinese", 121.0, 23.7, "east_asia"),
    "japanese": ("日语市场", "Japanese", 138.2529, 36.2048, "east_asia"),
    "koreana": ("韩语市场", "Korean", 127.7669, 35.9078, "east_asia"),
    "russian": ("俄语市场", "Russian", 90.0, 55.0, "eurasia"),
    "german": ("德语市场", "German", 10.4515, 51.1657, "europe"),
    "french": ("法语市场", "French", 2.2137, 46.2276, "europe"),
    "spanish": ("西语市场", "Spanish", -3.7038, 40.4637, "global"),
    "brazilian": ("巴西葡语市场", "Brazilian Portuguese", -51.9253, -14.2350, "south_america"),
    "english": ("英语评论市场", "English", -20.0, 20.0, "global"),
    "polish": ("波兰语市场", "Polish", 19.1451, 51.9194, "europe"),
    "turkish": ("土耳其语市场", "Turkish", 35.2433, 38.9637, "eurasia"),
    "thai": ("泰语市场", "Thai", 100.9925, 15.87, "southeast_asia"),
    "ukrainian": ("乌克兰语市场", "Ukrainian", 31.1656, 48.3794, "europe"),
}


def build_doc(rows, games, now=None, min_reviews=MIN_REVIEWS, period_days=PERIOD_DAYS):
    now = now or datetime.now(timezone.utc)
    cutoff = (now.date() - timedelta(days=period_days - 1)).isoformat()
    previous_cutoff = (now.date() - timedelta(days=period_days * 2 - 1)).isoformat()
    game_meta = {g["appid"]: g for g in games}
    grouped = defaultdict(list)

    for row in rows:
        if row.get("date") and row["date"] >= previous_cutoff:
            lang = (row.get("lang") or "unknown").lower()
            grouped[(row.get("appid"), lang)].append(row)

    output = []
    for (appid, lang), all_rows in sorted(grouped.items(), key=lambda x: (str(x[0][0]), x[0][1])):
        current = [r for r in all_rows if r.get("date", "") >= cutoff]
        if len(current) < min_reviews:
            continue
        meta = MARKETS.get(lang)
        if not meta:
            continue
        name, name_en, lng, lat, region = meta
        negative = sum(1 for r in current if not r.get("voted_up"))
        positive = len(current) - negative
        negative_rate = negative / len(current)
        topic_counts = Counter()
        for row in current:
            topic_counts.update(slug for slug, _term in T.match_topics(row.get("text", "")))
        top = topic_counts.most_common(1)
        topic_count = top[0][1] if top else 0
        topic_concentration = topic_count / len(current)
        # Transparent heuristic: 75% negative recommendation share + 25% topic concentration.
        # It is a project signal score, not a churn probability or emotion classifier.
        score = round(min(100.0, max(0.0, (negative_rate * 75 + topic_concentration * 25) * 100 / 100)), 1)
        if score >= 60:
            level = "high"
        elif score >= 30:
            level = "medium"
        else:
            level = "low"
        game = game_meta.get(appid, {})
        output.append({
            "id": "%s:%s" % (appid, lang),
            "appid": appid,
            "game": game.get("name") or str(appid),
            "language": lang,
            "name": name,
            "name_en": name_en,
            "latitude": lat,
            "longitude": lng,
            "region": region,
            "review_count": len(current),
            "positive_recommendations": positive,
            "negative_recommendations": negative,
            "positive_rate": round(positive / len(current), 4),
            "negative_rate": round(negative_rate, 4),
            "top_topic": T.LABEL.get(top[0][0], top[0][0]) if top else None,
            "top_topic_slug": top[0][0] if top else None,
            "top_topic_count": topic_count,
            "risk_score": score,
            "risk_level": level,
            "period_days": period_days,
            "sample_basis": "Steam recent review samples",
            "recommendation_signal": "Steam voted_up; not model sentiment",
            "data_type": "derived",
        })

    return {
        "updated_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": "Steam review samples on data branch",
        "data_type": "derived",
        "metric": "language_market_recommendation_risk",
        "period_days": period_days,
        "minimum_reviews": min_reviews,
        "definition": {
            "market": "Steam review language; not reviewer/player geolocation",
            "negative_rate": "share of sampled reviews with voted_up=false",
            "risk_score": "75% negative recommendation share + 25% top-topic concentration",
            "caveat": "Recent review sample is not a random sample of all players. Score is not churn probability.",
        },
        "markets": output,
    }


def main():
    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN") == "1"
    repo = os.environ.get("GITHUB_REPOSITORY", "aggressive0/My-web")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    local = None
    for i, arg in enumerate(sys.argv):
        if arg == "--samples-file" and i + 1 < len(sys.argv):
            local = sys.argv[i + 1]

    if local:
        with open(local, encoding="utf-8") as f:
            raw = f.read()
    else:
        if not token and not dry_run:
            raise SystemExit("缺少 GITHUB_TOKEN")
        raw, _sha = SIO.read_text(SIO.SAMPLES, repo, token)
    rows, skipped = SIO.parse_samples(raw)
    if not rows:
        raise SystemExit("没有可用 Steam 评论样本；不写空文件覆盖已有数据")

    games = game_config.load_games()["games"]
    doc = build_doc(rows, games)
    doc["sample_size"] = len(rows)
    doc["skipped"] = dict(skipped)
    if dry_run:
        print("[dry-run] markets=%d sample_size=%d" % (len(doc["markets"]), len(rows)))
        print(json.dumps(doc, ensure_ascii=False, indent=2)[:6000])
        return

    _old, sha = P.get_remote(OUT, repo, token)
    P.put_remote(OUT, doc, sha, repo, token, "data: build language-market globe %s" % doc["updated_at"])
    print("已发布 %s：%d 个语言市场" % (OUT, len(doc["markets"])))


if __name__ == "__main__":
    main()
