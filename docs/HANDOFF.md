# 🔁 セッション引き継ぎ書（analysis-hub）

> 前セッションをアーカイブする際の包括引き継ぎ。**新セッションは CLAUDE.md と本書を最初に読むこと。**
> 数値・確定エッジの詳細は記憶で再構成せず、必ず正本 `docs/edge_playbook.md` を参照する。
> 最終更新: 2026-06-04

---

## 0. 読む順番（最初に必ず）

1. `CLAUDE.md` …… プロジェクト指示（自動読込・最優先）。エッジ正本のルール、環境、作業手順、コード規約。
2. `docs/edge_playbook.md` …… 確定エッジ台帳の【正本】。**総まとめ・共有資料は必ずこれを土台に差分追記**（ゼロから書き直さない）。
3. 本書（`docs/HANDOFF.md`）…… 全体地図・残タスク・地雷。
4. 必要に応じ: `docs/edge_summary_share.md`（共有用1枚）/ `docs/jquants_endpoints.md`（API一覧）/ `docs/RUNBOOK.md` / `docs/SCHEMA.md`。

---

## 1. ✅ EDINET 自社株買い規模%取得（実装修正＋取得完了・2026-06-04）

> 公式キー反映後に実APIへ初到達し、前提が2点ズレていたことが判明したため**スクリプトを修正**して取得した。次セッションは下記の確定事実を土台にすること（170/8年分は誤り）。

- スクリプト: `scripts/edge_candidates/fetch_buyback_edinet.py`。
- 前提: 環境変数 `EDINET_API_KEY`（公式 EDINET API のキー。32桁hex。**`edb_`始まりは別サービス edinetdb.jp のキーで弾く**）。`api.edinet-fsa.go.jp` 許可済み。J-Quants キーは本タスク不要。
- **訂正1: docTypeCode は 220/230**（自己株券買付状況報告書／訂正版）。`170` は訂正半期報告書で別物だった。
- **訂正2: パーサは XBRL 要素名一致では取れない**。実 220 報告書は数値が**テキストブロック**（「取締役会決議による取得の状況」「保有状況」）に埋込で、決議枠の株数・金額は区切り無し連結→カンマ区切りで分割。`parse_edinet_csv` を全面書換（実52件中51件で規模%抽出を確認、全角数字・全角括弧・（上限）注記対応）。
- **訂正3: EDINET はこの報告書を約1年（縦覧期間）しか保持しない**。実測で取得可能なのは概ね**直近12か月（2025-06〜）のみ**。8年遡及は物理的に不可能。それでも TDnet PDF（5週間で消滅）より大幅に長い。
- 指標: `buyback_ratio_pct = 取得枠株数 / 発行済株式総数 ×100`（=決定枠%。TDnet の取得枠上限%と**同一指標**でバッジ比較可）。他に `buyback_max_shares/amount`（取得枠）、`issued_shares`、`cumulative_shares/amount`（報告月末累計）、`decision_date`、`event_date`（報告対象月末）。`source="edinet"`、既存TDnet分は `source="tdnet"` 自動付与。docID 単位で resume（`failed[]` は再実行で再取得）。
- 取得: `python -m scripts.edge_candidates.fetch_buyback_edinet --from 2025-06-01 --sleep 0.7`（既定 `--from` も 2025-06-01）。`data/edge_candidates/buyback_ratios.json` に追記。
- 受け手（別repo stocks-Large-holding-report）は `source` で tdnet/edinet を区別してバッジ表示する。
- 前進蓄積: EDINET は1年で消えるため、週次cron（`weekly_data.yml` / 要 Secrets `EDINET_API_KEY`）で**継続取得して過去を貯める**運用が本筋。

---

## 2. このリポジトリの目的とデータパイプライン

- 目的: PO発表・大量保有報告・適時開示(好悪同日材料)を共通スキーマに統合し、銘柄横断のタイムライン解析と期待値検証。Python 3.11 / stdlib のみ（依存追加は要相談、例外 pypdf は遅延import）。
- 主データ源:
  - **kouaku**（好悪同日材料）: yanoshin TDnet ミラー + J-Quants /fins/summary。`data/kouaku_records.json`。
  - **PO**: 別プロジェクト po-tracker が生成 → `cache/po/po_records.json`（受け渡し）→ `extract_po` → `data/po_records.json`。
  - **holdings**: holdings-tracker raw。
  - **公式 J-Quants アドオン**: `/td/list` `/td/bulk` `/fins/summary` `/equities/master` `/equities/bars/daily` `/indices/bars/daily/topix`（詳細 `docs/jquants_endpoints.md`）。`/fins/details` `/fins/dividend` `/listed/info` は 403。
- 健全性チェック: `python -m scripts.data_health`。再実行: `python -m scripts.update_all [--source kouaku|po|holdings]`。

### 関連リポジトリ（クロスrepo）
- **po-tracker**（公開）: PO の生データ。market_cap 等。raw `https://raw.githubusercontent.com/junf66/po-tracker/main/data/po_records.json`。
- **stocks-Large-holding-report**（好悪ページの受け手）: analysis-hub が公開する raw JSON を fetch する。公開済み: `mild_good.json` / `mild_bad.json` / `mild_zouhai.json` / `mild_genhai.json` / `buyback_ratios.json`（いずれも `data/edge_candidates/` 直下、main raw URL）。

---

## 3. 確定エッジ（詳細・数値は必ず正本を参照）

現在【確定8本】: **②③④⑤①B⑥⑦⑧**、保留 **①A**。各エッジの方向/条件/売買時刻/成績/効きどころは `docs/edge_playbook.md` に集約。共有用1枚は `docs/edge_summary_share.md`。

このセッションでの主な変遷（教訓込み）:
- **⑤封印を撤回**: mild_good(軽い減益×増配)はFDR✅健在。zouhai_genshu(深い減益=別物・エッジなし)と取り違えた誤封印を撤回。**パターン名で判断せず実体を見る**。
- **①Aを保留に格下げ**: 旧値が再現不能（出所スクリプト不在）。大型午前ロングの芽はあるが n不足。→ **採用エッジには必ず再現スクリプトを持たせる**。
- **⑥受渡日ロング確定**: 駆動因はPO規模の絶対額(調達額)。規模割合では効かない。
- **⑦中型decideショート確定**: TOPIX β実推定(`analyze_decide_beta.py`)で β交絡を否定（α控除後も強い）。
- **⑧好悪×医薬品×信用LONG確定**: kouaku全体ショート優位の中で唯一の逆張り。信用が分かれ目(貸借は無効)。基線超過demeanで顕在化。

確定判定の基準は `scripts/validate_edges.py`（事前登録仮説の独立FDR + walk-forward OOS + 方向別コスト + クラスタt）。**新エッジはここに事前登録して通すこと**。

---

## 4. このセッションで作った主な資産（main反映済み）

- 候補スキャナー: `scripts/scan_po_candidates.py` / `scripts/scan_kouaku_candidates.py`（全次元総当たり+2軸+基線超過demean+FDR/OOS）。`--since` で期間限定スキャン可。
- β実推定: `scripts/edge_candidates/analyze_decide_beta.py`（topix_daily + daily_bars_po 2017-）。
- 規模分析: `analyze_po_long_size_brackets` / `analyze_delivery_long_filters` / `analyze_pharma_long` ほか。
- mild補完: `extract_mild_good`(⑤母体・触るな) + `extract_mild_cases`(mild_bad/zouhai/genhai 公開)。
- 自社株買い規模%: `enrich_buyback_pdf`(TDnet PDF・最新分・pypdf遅延import) + `fetch_buyback_edinet`(EDINET・過去分)。
- 週次自動化: `.github/workflows/weekly_data.yml`（要 Secrets: `JQUANTS_API_KEY`, `EDINET_API_KEY`）。
- 参照: `docs/jquants_endpoints.md`。

---

## 5. ⚠️ 地雷・注意（事故防止）

- **⑤は `mild_good.json` の `alpha_d3_ret` に依存**。`extract_mild_good` を再実行すると最小スキーマで上書きし alpha を失い⑤が壊れる。update_all でも mild_good は再生成しない設計（新3ケースのみ）。
- **規模区分は「円の閾値」でなく TOPIX ScaleCat**（`equities_master.scale_band`）で切る。円レンジは重複する。例外は③株式分割のみ円閾値(≤500億)。
- **market_cap の「兆」切り捨てバグ**は po-tracker側で修正済み。残存は 日本ビルファンド2026-01(REIT) 1件のみ。mc を疑うときは ScaleCat と突合。
- **reports/ は概ね .gitignore**（一部 tracked: edge_validation.md 等）。スクリプトで再生成する設計。
- **環境変数はコンテナ起動時に読み込まれる**。後から足したら新セッションが必要（EDINETキーで実証済み）。
- **GitHub API はレート制限が頻発**。draft解除/merge が rate-limited になったら時間を置いて再試行（Monitor で自己リマインド可）。auto-merge はリポジトリ設定で無効。
- **TDnet PDF(release.tdnet.info)は約5週間で消える** → 過去分は EDINET 経由（タスク1）。
- **記憶/二次資料からエッジ総まとめを再構成しない**（過去に①A・⑤の欠落事故）。必ず正本から。

---

## 6. 宿題（優先度順・任意）

1. **④中型decideショートのβ確認**（⑦と同手法 `analyze_decide_beta` で可能・未実施）。
2. **mild_kahou_nx / mild_kouhou_nx**（軽い来期上方/下方帯）: 来期予想NPが /fins で 403のため未作成。株探等の外部ソース要判断。
3. **mild 反対材料の「特損/下方修正」DiscItems コード特定**（現状 減配/減益のみ対応）。
4. **buyback 週次cron の Secrets 登録**（`JQUANTS_API_KEY` / `EDINET_API_KEY`）→ 前進蓄積を自動化。
5. **候補スキャナーの基線超過の芽**を深掘り（医薬品ファミリー等）。
6. **/td/list の Title 文言ベース新分類**（未活用テキスト → 死角の新サブパターン）。

---

## 7. 開発規約

- **1機能1PR**、main からブランチを切る、**squash merge** デフォルト。develop ブランチ規約はユーザー指示に従う。
- 緑必須: `python -m unittest discover -s tests` / `python -m scripts.audit_all`（0件）。
- 新規スクリプトは **docstring + テスト必須**（audit_all がカバレッジ/docstring を検査）。
- **依存追加は要相談**（pandas/numpy 等）。pypdf は遅延importで CI は stdlib のまま。
- push後はドラフトPRを作成 → CI緑 + ドラフト解除 → squash merge。
- 出力先: edges 正本=`docs/edge_playbook.md`、共有=`docs/edge_summary_share.md`。

---

## 8. ドキュメント/レポート地図

- `docs/`: edge_playbook.md(正本) / edge_summary_share.md(共有) / jquants_endpoints.md(API) / RUNBOOK.md / SCHEMA.md / kouaku_edge_spec.md / po_edges_briefing.md / holdings_investigation.md / HANDOFF.md(本書)。
- `reports/`(tracked): edge_validation.md(検証結果) / magnitude_sweep.md / mild_good.md / po_scale_timing.md / edge_candidates_summary.md など。多くは .gitignore で再生成可。
- データ: `data/`(共通スキーマ) / `data/edge_candidates/`(分析中間・公開JSON)。
