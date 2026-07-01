# AGENTS.md — 全エージェント共通オンボーディング（Codex / Claude / その他）

> このリポジトリはエージェント非依存で運用する。**Codex も Claude も、まずこの順で読む**。
> 規約の正典は **CLAUDE.md**（Claude Code 用の体裁だが、内容は**全エージェント共通の必読規約**）。
> 二重管理を避けるため、本書は規約を複製せず CLAUDE.md を指す（＝単一の真実源）。

## 0. 読む順番（最初に必ず）
1. **`CLAUDE.md`** …… 全エージェント共通の規約・環境・作業手順・コード規約（最優先）。
2. **`docs/HANDOFF.md`** …… 全体地図・残タスク・地雷。
3. **`docs/edge_playbook.md`** …… 確定エッジ台帳の【正本】＝**唯一の真実**。

## 1. 絶対規約（詳細は CLAUDE.md「⚠️ エッジ台帳の正本」節）
- **正本 `docs/edge_playbook.md` が唯一の真実**。総まとめ・共有資料・早見表は必ず正本を土台に差分更新する。
- **記憶・二次資料（スナップショット/監査サマリ/会話履歴）からの再構成は禁止**。必ず正本本体を読んでから答える。
- **照合は単一パス（該当エッジ節だけ）でなく正本"全体横断"で**。別セクション（不採用台帳・分足注・監査注記・鉄則）に上書き/除外条件がある（例: ①B の激深≥10%GD 除外は本文と不採用台帳の両方にある）。
- **ユーザーが「共有したい」＝最新版の正本 `docs/edge_playbook.md` を渡す**。
- **学びは都度・確認なしで正本へ記録**。数値を"それらしく"埋めない＝正本に無ければ「未記載」と書く。
- モデル識別子を commit / PR / コード / 成果物に入れない（チャット回答のみ）。

## 2. 環境とデータ再構築（＝検証を"実行"するために必要）
コード・正本・追跡データは git 管理済み（clone すれば共有される）。ただし**巨大キャッシュは .gitignore で再生成前提**：
- **再取得が要るもの**（git に無い）: `cache/event_bars.json`(調整後日足)・`cache/disclosures/tdnet_all.json`(TDnet全タイトル)・`data/edge_candidates/fins_summary.json`(決算) など。
- **再構築手順は `docs/RUNBOOK.md`**：
  ```bash
  export JQUANTS_API_KEY=...                # J-Quants v2 ダッシュボードで発行
  python -m scripts.fetch_disclosures        # /fins/summary 5年 + yanoshin TDnet 5年（~30分）
  python -m scripts.update_all               # extract→enrich→analyze→backtest
  python -m scripts.data_health              # 健全性チェック（探索前に必ず）
  ```
- ネットワーク allowlist: `api.jquants.com`（必須）・`webapi.yanoshin.jp`（TDnet）・`raw.githubusercontent.com`（PO raw）。
- Python 3.11 / **stdlib のみ**（pandas等の依存追加は要相談）。

## 3. 検証フレームワーク（"過剰最適化ガード" ＝ 正の結果ほど疑う）
確定判定はこの多重ガードを全通過したものだけ。詳細は正本「検証フレームワーク」節と各 audit スクリプト：
- **FDR**（多重検定補正）: `python -m scripts.validate_edges` → `reports/edge_validation.md`
- **DSR / MinBTL**（試行回数補正の絶対バー）: `python -m scripts.edge_candidates.audit_deflated_sharpe`
- **PBO / CSCV**（戦略選択の過学習）: `python -m scripts.edge_candidates.audit_pbo`
- **非重複の正直 t**（重複窓の t 水増し除去）・**PIT ユニバース**（`cache/master_history.json` で時点分類＝先読み/生存バイアス回避）
- **leave-one-year-out**（最良年を1つ抜いても t>2 か＝レジーム依存の検出）
- 方向別コスト: long 0.20% / short 0.15%。日付クラスタ頑健 t。
- 探索 CLI（3ソース共通）: `query_kouaku` / `query_po` / `query_holdings`（`--bootstrap` `--collapse-daily` 等）。

## 4. Codex で「独立検証だけ」したい場合（リポ/API アクセス無しで）
`docs/codex_verification_prompt.md` にコピペ用プロンプトあり。自己完結バンドル `edge_trades.json`（`python -m scripts.edge_candidates.verify_edges_standalone --export edge_trades.json` で生成）を渡せば、Codex はそれだけで統計の再計算・銘柄選定の異常検知・方法論批判ができる（過去に②の重複3件・①A 損益分岐0.1% を Codex 独立監査が発見）。

## 5. 作業・PR 規約（詳細は CLAUDE.md「開発ブランチ規約」）
1機能1PR・squash merge・ドラフトPR→CI green確認→ready化→マージ。テスト: `python -m unittest discover -s tests`。

## 6. 主要ドキュメント索引
- 正本: `docs/edge_playbook.md` ／ 引き継ぎ: `docs/HANDOFF.md` ／ 運用手順: `docs/RUNBOOK.md` ／ スキーマ: `docs/SCHEMA.md`
- API一覧: `docs/jquants_endpoints.md` ／ 早見表: `docs/edge_cheatsheet.html`
- 共有用まとめ: `docs/edge_reverify_share.md`・`docs/edge_summary_share.md`
- 独立検証: `docs/codex_verification_prompt.md`
