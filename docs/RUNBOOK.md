# RUNBOOK

kouaku_mixed + PO (公募増資) パイプラインの運用手順。

## 1. 環境準備

```bash
export JQUANTS_API_KEY=...   # J-Quants v2 dashboard で発行
```

ネットワーク allowlist:
- `api.jquants.com` — 必須 (daily/minute/fins/master)
- `webapi.yanoshin.jp` — 推奨 (TDnet 全タイトル: 自社株買い/TOB/優待/分割 等)
- `api.jquants-pro.com` — Pro 契約者のみ (自社株買い TDnet, 任意フォールバック)
- `raw.githubusercontent.com` — PO データ (po-tracker raw JSON 取得)

## 2. 初回フル fetch (~30 分)

```bash
python -m scripts.fetch_disclosures             # /fins/summary 5年分 + yanoshin TDnet 5年分
python -m scripts.update_all                    # extract→enrich→analyze→backtest
```

完走時間:
- fetch (/fins/summary): 約 20 分 (1221 営業日 × ~1秒)
- fetch (TDnet, yanoshin): 約 10 分 (1221 営業日 × 0.5 秒)
- enrich: 約 5 分 (記録件数 × ~1.5秒/件、分足込み)
- analyze + backtest: 数秒

ソースを別々に取りたい場合:

```bash
python -m scripts.fetch_disclosures --skip-tdnet                # /fins/summary だけ
python -m scripts.fetch_disclosures --skip-fins --skip-buyback  # TDnet (yanoshin) だけ
```

## 3. 日次更新 (~2 分)

J-Quants は当日分が翌日の午前に取れる想定。

```bash
# 最終取得日以降だけ追加 fetch (現状 5y 再取得しかないので注意 — incremental は今後)
python -m scripts.fetch_disclosures --since YYYY-MM-DD --until YYYY-MM-DD
python -m scripts.update_all
```

idempotent なので何度走らせても OK。enrich 済 record は skip される。

## 3.5. PO パイプライン

PO (公募増資) は po-tracker リポジトリの enrich 済 JSON を取り込んで共通スキーマに展開する。
J-Quants 追加 fetch は不要 (価格は po-tracker 側で既に enrich 済)。

```bash
# 初回 or raw 再取得時
python -m fetchers.po                   # raw → cache/po/po_records.json
python -m scripts.extract_po            # raw → data/po_records.json (1 PO → 最大 3 events)
python -m scripts.analyze_po_edge       # → reports/po_analysis.md (既知 3 エッジ含む)
python -m scripts.backtest_po           # → reports/po_backtest.md (cell × 既知エッジ net)

# まとめて
python -m scripts.update_all --source po --refresh-po-raw
```

既知 3 エッジ (po-tracker セッション参照 EV):
- 発表翌日 (普通 announce, 9:10 売り long): EV +0.66%
- 受渡日 GD (普通 deliver, gap<=-0.5%, 寄→引 long): EV +0.80%
- リート ショート (REIT decide, next_open→決定日引け short): EV +1.12%

EV 評価除外フラグ (analyze/backtest で自動除外):
- `legacy_record` : 古い不完全データ
- `concurrent_earnings` : 決算同時 (材料混在)
- `split_within_po_window` : 株式分割窓 (価格調整影響)
- `status != complete` (announce のみ "nextday" も許容)

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
- 価格 enrich coverage >= 90% (kouaku / PO 共に)
- /fins/summary 最終日 → 今日 が 7 日以内
- 0行の営業日が異常に多くないか
- PO: count_raw が po-tracker 側の件数と一致しているか

## 6. テスト

```bash
python -m unittest tests.test_kouaku_known_cases tests.test_pipeline_integration -v
```

## 7. パイプライン全体図

### kouaku パイプライン

```
                    ┌─────────────────────────────────┐
JQUANTS_API_KEY ──→ │  scripts/fetch_disclosures.py   │
yanoshin (公開)   ─→ │  → cache/disclosures/{         │
                    │    fins_summary.json,           │
                    │    tdnet_all.json (yanoshin),   │
                    │    share_buyback_tdnet.json     │
                    │  }                              │
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

### PO パイプライン

```
                    ┌─────────────────────────────────┐
po-tracker (公開) ─→ │  fetchers/po.py                 │
                    │  → cache/po/po_records.json     │  (raw + enriched 価格)
                    │  → cache/po/po_audit.json       │
                    └────────────────┬────────────────┘
                                     ↓
                    ┌─────────────────────────────────┐
                    │ scripts/extract_po.py           │
                    │  → data/po_records.json         │
                    │  (1 PO → 最大 3 events:         │
                    │   announce/decide/deliver,      │
                    │   stage 別に attrs 正規化)       │
                    └────────────────┬────────────────┘
                                     ↓
        ┌────────────────────────────┴──────────────────────────┐
        ↓                                                       ↓
┌──────────────────────┐                              ┌──────────────────────┐
│ analyze_po_edge      │                              │ backtest_po          │
│ → reports/po_*.md    │                              │ → reports/po_*.md    │
│  (既知 3 エッジ +    │                              │  (cell × 既知 3 エッジ│
│   stage × type × lend) │                            │   net 損益)          │
└──────────────────────┘                              └──────────────────────┘
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
| PO `cache miss: ...` | po-tracker raw 未取得 | `python -m fetchers.po` で先に取得 |
| PO 価格 enrich coverage < 90% | legacy 古いレコード混入 | EV 評価では自動除外、レポートは無視で OK |
