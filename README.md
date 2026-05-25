# analysis-hub

PO 発表・大量保有報告・適時開示 (好悪同日材料) を共通スキーマに正規化し、
銘柄横断のタイムライン解析と期待値検証を行うリポジトリ。

3 つのソースを同居させ、`(code, event_date)` で結合可能にしてある。

| ソース | event_type | fetcher | 取得元 |
|---|---|---|---|
| po-tracker | `po_announce` / `po_decide` / `po_deliver` | `fetchers/po.py` | GitHub raw |
| holdings-tracker | `holdings_filing` / `holdings_change` / ... | `fetchers/holdings.py` | GitHub raw |
| kouaku_mixed | `kouaku_mixed` | `scripts/fetch_disclosures.py` + `scripts/extract_mixed_disclosures.py` | J-Quants API v2 |

## クイックスタート

```bash
# 1. 環境変数: JQUANTS_API_KEY を設定 (J-Quants v2 の x-api-key)

# 2. データ健全性チェック
python -m scripts.data_health

# 3. キャッシュからレポート全部再生成 (1秒)
python -m scripts.update_all --skip-fetch

# 4. ad-hoc EV 計算
python -m scripts.query_kouaku --subpattern kouhou_genshu --disc-time-bucket 場中 --bootstrap --plot-cumul
```

## ファイル構成

```
fetchers/        ソース別の raw データ取得 + cache
normalizers/     ソース別の共通スキーマ変換
analyzers/       timeline / po_edges 等の解析
scripts/         kouaku_mixed + PO パイプライン (fetch / extract / enrich / analyze / backtest / query)
data/            git 管理する正規化済みデータ (kouaku_records.json / po_records.json 等)
cache/           再生成可能な生データ (gitignore)
reports/         生成物 (gitignore)
docs/            仕様書
tests/           unittest 一式
```

## 詳細

- 仕様: [docs/kouaku_edge_spec.md](docs/kouaku_edge_spec.md)
- 運用手順: [docs/RUNBOOK.md](docs/RUNBOOK.md)
- データスキーマ: [docs/SCHEMA.md](docs/SCHEMA.md)

## 既知の制約

- J-Quants Light 契約のため `api.jquants-pro.com` の自社株買い TDnet エンドポイントは未取得 (`jisha_*` サブパターン 0 件)
- J-Quants 分足アドオンは 2024-05-21 以降のみ
- /equities/bars/daily は上場廃止銘柄を返さない (price_error として記録)
