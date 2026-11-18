# REAL_GLOBE_DATA_IMPLEMENTATION

## What the globe means

The globe displays **Steam review language markets**, not verified player locations.
Steam's public review payload exposes a review language but not the review author's country.
Coordinates are approximate visual centroids used to place language-market markers.

## Pipeline

```text
data branch: reviews-samples.jsonl
  -> samples_io.parse_samples() (appid, id, created, voted_up, lang, text)
  -> language + appid aggregation
  -> review recommendation signal + keyword topic candidates
  -> backend/collectors/globe_publish.py
  -> data branch: globe-daily.json
  -> front/static_html/js/realdata.js
  -> front/static_html/js/globe-stage.js
```

The existing `analyze-sentiment.yml` workflow runs the publisher after review sentiment/topic/risk jobs.
The publisher consumes the same real review sample file and does not introduce a second Steam collector.

## Data contract

`globe-daily.json` includes:

- `updated_at`, `source`, `data_type=derived`, `period_days`
- `definition`: explicit caveats and metric definitions
- `markets[]`: appid, language, display names, approximate centroid, review_count,
  positive/negative recommendation counts and rates, top topic, risk score/level

A market is emitted only when it has at least `GLOBE_MIN_REVIEWS` samples in the
configured period (default 20). Unknown/unmapped languages are omitted rather than
guessed. The output remains game-scoped.

## Metric semantics

- `positive_rate` / `negative_rate`: Steam `voted_up` recommendation signal.
  This is not the model's emotion classification and is not proof of player sentiment.
- `top_topic`: most frequent keyword candidate from the existing topic lexicon.
  It is a candidate signal, not a semantic conclusion.
- `risk_score`: project heuristic = 75% negative recommendation share + 25% top-topic
  concentration. It is not churn probability and must not be described as such.
- The recent sample stream is not a random sample of all Steam players.
- English and other multi-country languages are language markets, not countries.

## Frontend behavior

The homepage loads `globe-daily.json` through RealData's remote-data / local-snapshot
chain. It starts with no markers, does not fall back to the old hardcoded player
coordinates, and shows an explicit empty/unavailable status if no market data exists.
Point size is based on review sample count; point color and rings use the derived
risk level. Clicking a marker shows the language, sample count, recommendation
signal, risk score and top topic.

## Configuration

- `GLOBE_MIN_REVIEWS`: minimum current-window samples per market (default 20)
- `GLOBE_PERIOD_DAYS`: current aggregation window (default 7)

## Local verification

```bash
python backend/collectors/globe_publish.py --dry-run --samples-file path/to/reviews-samples.jsonl
python -m unittest tests.test_globe_publish -v
```

No synthetic market data is written when the sample file is missing or empty.
