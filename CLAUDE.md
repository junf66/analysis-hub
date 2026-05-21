# analysis-hub - Claude Code 用コンテキスト

## このリポジトリの目的

PO 発表・大量保有報告・適時開示 (好悪同日材料) を共通スキーマに統合し、
銘柄横断のタイムライン解析と期待値検証を行う。

詳細は [README.md](README.md) / [docs/](docs/) を参照。

## 環境

- Python 3.11、stdlib のみ (依存ライブラリなし)
- J-Quants v2 API キー (`x-api-key` ヘッダ方式) を環境変数 `JQUANTS_API_KEY` で取得
- リモート実行環境の network policy で `api.jquants.com` (および Pro 契約者は `api.jquants-pro.com`) が allowlist 必要

## 主な作業手順

```bash
# 健全性チェック (探索開始前に必ず)
python -m scripts.data_health

# パイプライン再実行 (キャッシュから即座)
python -m scripts.update_all

# ad-hoc EV 計算 (主な探索インターフェイス)
python -m scripts.query_kouaku --subpattern <X> --disc-time-bucket <Y> --bootstrap

# 全 cell 横並び比較
python -m scripts.query_kouaku --group-by subpattern
python -m scripts.query_kouaku --group-by disc_time_bucket

# テスト
python -m unittest tests.test_kouaku_known_cases tests.test_pipeline_integration -v
```

## キーとなる発見済みエッジ

`kouhou_genshu × 場中 (11-15) 開示`:
- 翌寄り→翌引 EV(net)=+1.51% / t=+3.19 / win=65% / cumul=+30.15% (5y, n=20)
- bootstrap 95% CI: [-2.62%, -0.86%]
- 戦略: 翌寄りでショート → 翌引けで買戻

他の cell は |t|<2 でノイズ範囲 (`scripts/backtest_kouaku.py` の出力参照)。

## 既知制約

- J-Quants Light 契約のため Pro 専用 (自社株買い TDnet) は未取得
- 分足は 2024-05-21 以降のみ (それ以前は日足のみ)
- 上場廃止銘柄は daily 取得不能 (price_error で記録、約 4-5%)

## コード規約

- stdlib のみ (pandas/numpy 等の依存追加は要相談)
- ファイル先頭の docstring に意図を簡潔に
- マジックナンバーは定数化 (例: `NP_YOY_BAD_THRESHOLD_PCT`)
- 共通スキーマ準拠の dict を中継するスタイル
- 新サブパターン追加 = `extract_mixed_disclosures._SUBPATTERN_RULES` + (必要なら) `data/kouaku_classification.csv`
- 新メトリクス追加 = `enrich_price_kouaku._INTRADAY_TARGETS` + `analyze_kouaku_edge._METRIC_FIELDS` + `query_kouaku._METRIC_CHOICES`

## 開発ブランチ規約

ユーザー指示に従う。1 機能 1 PR、squash merge デフォルト。
