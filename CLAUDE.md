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
python -m scripts.update_all                       # 全ソース (kouaku + PO)
python -m scripts.update_all --source kouaku       # kouaku のみ
python -m scripts.update_all --source po           # PO のみ

# ad-hoc EV 計算 (主な探索インターフェイス)
python -m scripts.query_kouaku --subpattern <X> --disc-time-bucket <Y> --bootstrap

# 全 cell 横並び比較
python -m scripts.query_kouaku --group-by subpattern
python -m scripts.query_kouaku --group-by disc_time_bucket

# PO エッジ確認
python -m scripts.analyze_po_edge   # reports/po_analysis.md
python -m scripts.backtest_po       # reports/po_backtest.md

# 好悪サイト用 slim JSON 生成 + プレビュー
python -m scripts.export_kouaku_site                 # data/kouaku_site.json
python -m http.server                                # → http://localhost:8000/site/kouaku.html

# テスト
python -m unittest discover -s tests
```

## キーとなる発見済みエッジ

### kouaku
source of truth は `scripts/backtest_kouaku.py` の net 損益 (往復コスト 0.20%)。
全 cell の現行ランキングは `reports/kouaku_backtest.md` / `data/kouaku_site.json` を参照。

現行の高 n 有意セル (net |t|≥2、n≥100、いずれも 寄りショート→引け買戻):
- `zouhai_kahou_nx × 大引け後` short: EV(net)+0.83% / t+4.37 / win=63% / cumul+199.5% (n=239)
- `kouhou_seikyu × 大引け後`   short: EV(net)+0.39% / t+2.24 / win=55% / cumul+282.4% (n=715)

`kouhou_genshu × 場中 (11-15) 開示` (旧・主エッジ):
- 翌寄り→翌引 EV(net)=+0.91% / t=+1.52 / win=55% / cumul=+18.27% (5y, n=20)
- 生 (cost 前) bootstrap 95% CI: [-2.23%, -0.02%]
- per-trade は大きいが n が小さく cost 後 |t|<2 に低下。要追検証。
- 戦略: 翌寄りでショート → 翌引けで買戻

### PO (発見済 3 エッジ、cost 0% raw)
- 発表翌日 (普通 announce, 9:10 売り long): EV +0.44% / t +3.07 / n 121
- 受渡日 GD (普通 deliver, gap<=-0.5%, 寄→引 long): EV +0.38% / t +2.40 / n 226
- リート ショート (REIT decide, next_open→決定日引け short): EV +1.08% / t +4.82 / n 177

新発見 (Phase D, cost 0.20% net):
- decide × REIT × 貸借 short: t +3.43 / EV +0.93% / n 131
- decide × 普通 × 貸借 short: t +2.54 / EV +0.91% / n 349

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
- PO の attrs キー変更 = `extract_po._attrs_*` + `analyze_po_edge._METRIC_FIELDS_BY_STAGE` + `audit_all._PO_ATTR_KEYS_BY_STAGE`

## 開発ブランチ規約

ユーザー指示に従う。1 機能 1 PR、squash merge デフォルト。
