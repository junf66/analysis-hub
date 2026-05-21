# RUNBOOK

kouaku_mixed パイプラインの運用手順。

## 1. 環境準備

```bash
export JQUANTS_API_KEY=...   # J-Quants v2 dashboard で発行
```

ネットワーク allowlist:
- `api.jquants.com` — 必須 (daily/minute/fins/master)
- `api.jquants-pro.com` — Pro 契約者のみ (自社株買い TDnet)

## 2. 初回フル fetch (~25 分)

```bash
python -m scripts.fetch_disclosures             # /fins/summary 5年分 (95k 行)
python -m scripts.update_all                    # extract→enrich→analyze→backtest
```

完走時間:
- fetch: 約 20 分 (1221 営業日 × ~1秒)
- enrich: 約 5 分 (186 records × ~1.5秒/件、分足込み)
- analyze + backtest: 数秒

## 3. 日次更新 (~2 分)

J-Quants は当日分が翌日の午前に取れる想定。

```bash
# 最終取得日以降だけ追加 fetch (現状 5y 再取得しかないので注意 — incremental は今後)
python -m scripts.fetch_disclosures --since YYYY-MM-DD --until YYYY-MM-DD
python -m scripts.update_all
```

idempotent なので何度走らせても OK。enrich 済 record は skip される。

## 4. ad-hoc 探索

```bash
# 全件
python -m scripts.query_kouaku

# サブパターン別
python -m scripts.query_kouaku --subpattern kouhou_genshu

# サブパターン × DiscTime
python -m scripts.query_kouaku --subpattern kouhou_genshu --disc-time-bucket 場中

# グルーピング集計 (横並び比較)
python -m scripts.query_kouaku --subpattern kouhou_genshu --group-by disc_time_bucket
python -m scripts.query_kouaku --group-by subpattern

# 詳細統計
python -m scripts.query_kouaku --subpattern kouhou_genshu --disc-time-bucket 場中 \
    --bootstrap --histogram --plot-cumul --list-records

# 別メトリクス
python -m scripts.query_kouaku --metric next_day_910_ret
python -m scripts.query_kouaku --metric gap_pct

# GAP 範囲フィルタ
python -m scripts.query_kouaku --gap-min -5 --gap-max 0

# JSON 出力 (script から呼ぶ用)
python -m scripts.query_kouaku --subpattern X --json
```

## 5. データ健全性チェック

```bash
python -m scripts.data_health           # stdout + reports/data_health.md
python -m scripts.data_health --strict  # critical があれば非ゼロ exit (CI 用)
```

確認ポイント:
- 価格 enrich coverage >= 90%
- /fins/summary 最終日 → 今日 が 7 日以内
- 0行の営業日が異常に多くないか

## 6. テスト

```bash
python -m unittest tests.test_kouaku_known_cases tests.test_pipeline_integration -v
```

## 7. パイプライン全体図

```
                    ┌─────────────────────────────────┐
JQUANTS_API_KEY ──→ │  scripts/fetch_disclosures.py   │
                    │  → cache/disclosures/*.json     │
                    └────────────────┬────────────────┘
                                     ↓
                    ┌─────────────────────────────────┐
                    │ scripts/extract_mixed_disclosures│  ← scripts/classify_kouaku
                    │  → data/kouaku_records.json     │  ← data/kouaku_classification.csv
                    └────────────────┬────────────────┘
                                     ↓ (id でマージ、attrs 保持)
                    ┌─────────────────────────────────┐
                    │ scripts/enrich_price_kouaku     │
                    │  → 価格 + 分足 attrs 付与       │  (idempotent)
                    └────────────────┬────────────────┘
                                     ↓
        ┌────────────────────────────┼──────────────────────────┐
        ↓                            ↓                          ↓
┌──────────────────┐  ┌──────────────────────┐  ┌──────────────────┐
│ analyze_kouaku_edge│ │ backtest_kouaku       │ │ query_kouaku      │
│ → reports/*.md    │ │ → reports/*.md        │ │ (interactive CLI) │
└──────────────────┘  └──────────────────────┘  └──────────────────┘
        ↑                            ↑
        └─── analyzers/timeline.py で 3 ソース横断観察
```

## 8. トラブルシュート

| 症状 | 原因 | 対処 |
|---|---|---|
| `JQUANTS_API_KEY env var is not set` | env 未設定 | export JQUANTS_API_KEY=... |
| `HTTP 400 Your subscription covers...` | 契約範囲外 (古すぎる日付) | 過去 5 年以内 / minute は 2024-05-21 以降 |
| `HTTP 403 not subscribed` | Light 契約で Pro endpoint | Pro 契約必要 |
| `Host not in allowlist` | 環境の network policy | 環境設定で host 追加 (新セッション必要) |
| `HTTP 429 Rate limit` | リクエスト過多 | `--sleep 0.2` 等で間隔広げて再実行 |
| price_error 'no bars' | 上場廃止銘柄 | スキップで OK (data_health で確認) |
