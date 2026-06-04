# analysis-hub - Claude Code 用コンテキスト

> **新セッション/引き継ぎ時は [docs/HANDOFF.md](docs/HANDOFF.md) を読む**（全体地図・残タスク・地雷・規約）。

## ⚠️ エッジ台帳の正本（最重要・厳守）

**確定エッジの正本は [docs/edge_playbook.md](docs/edge_playbook.md)。**
エッジの「総まとめ」「共有資料」「現状の採用エッジ一覧」を求められたら:

1. **必ず docs/edge_playbook.md を読んでから答える**（このCLAUDE.mdの抜粋や記憶・スクリプト出力から再構成してはならない）
2. まとめは **正本を土台に差分追記/更新するだけ**。ゼロから書き直さない
3. 新しい検証結果が出たら、まず正本に追記してから共有する

理由: 過去に記憶＋二次資料から総まとめを再構成し、採用エッジ①A（PO大型LONG絞り込み通過版）
と⑤（zouhai_genshu）を**丸ごと欠落**させ、保留中の中型shortを誤って推奨する重大事故を起こした。
正本を読まずに要約すると必ずズレる。**記憶ベースの再構成は禁止**。

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
python -m scripts.update_all                       # 全ソース (kouaku + PO + holdings)
python -m scripts.update_all --source kouaku       # kouaku のみ
python -m scripts.update_all --source po           # PO のみ
python -m scripts.update_all --source holdings     # 大量保有のみ

# ad-hoc EV 計算 (主な探索インターフェイス) — 3 ソースとも同じ操作感
python -m scripts.query_kouaku --subpattern <X> --disc-time-bucket <Y> --bootstrap
python -m scripts.query_po --stage decide --po-type リート --metric ret_close --bootstrap
python -m scripts.query_holdings --holder 外資ファンド --group-by purpose --bootstrap

# 全 cell 横並び比較
python -m scripts.query_kouaku --group-by subpattern
python -m scripts.query_kouaku --group-by disc_time_bucket
python -m scripts.query_po --group-by lending_type --stage decide --metric ret_close
python -m scripts.query_holdings --group-by purpose

# PO エッジ確認
python -m scripts.analyze_po_edge   # reports/po_analysis.md
python -m scripts.backtest_po       # reports/po_backtest.md

# 大量保有エッジ確認
python -m scripts.analyze_holdings_edge   # reports/holdings_analysis.md
python -m scripts.backtest_holdings       # reports/holdings_backtest.md

# エッジ検証 (過剰最適化ガード: 日付クラスタ頑健t + FDR多重検定補正 + walk-forward OOS)
# 方向別コスト (short 0.15%=楽天滑りのみ / long 0.20%=日興込み)、既知3エッジ監査セクション付き
python -m scripts.validate_edges          # reports/edge_validation.md (3ソース横断)

# 好悪サイト用 slim JSON 生成 + プレビュー
python -m scripts.export_kouaku_site                 # data/kouaku_site.json
python -m http.server                                # → http://localhost:8000/site/kouaku.html

# テスト
python -m unittest discover -s tests
```

## キーとなる発見済みエッジ

**過剰最適化ガード (`scripts.validate_edges`) を通過する真に頑健なエッジ** (日付クラスタ頑健 t
＋ Benjamini-Hochberg FDR ＋ walk-forward OOS、**方向別コスト net: short 0.15% / long 0.20%**):
- kouaku: `zouhai_kahou_nx × 大引け後` short (t_clust+4.98 / p≈0 / OOS test +1.28% / n239)
- PO:     `decide × リート × 貸借` short (t_clust+3.49 / p=0.0005 / OOS test +0.93% / n131)
- **edge_candidates #4 株式分割 翌寄→+10日引 long** (TOPIX β=1 α 控除後、`reports/edge_candidates_detail/#4α.md`):
  - α net+1.64% / t_clust+2.64 / OOS+1.68% / n939 (FDR★)
  - +5日: α+1.16%/t+2.55、**+3日: α+0.76%/t+2.19** (短期版も通過)
  - +1日: α+0.14%/t+0.66 = エッジなし。**+3日が最短の通過点**
  - 引け入りは寄り入りより 0.5% 弱（寄→引で 0.5% 上昇）
  - 留保: β=1 近似。daily_bars_universe完了後に β 実推定で再々検証予定
- holdings: **通過セルなし** (最善でも p>0.05、データ期間が短い)

コスト前提 (実約定環境に合わせ方向別): **ショート=楽天 手数料0・逆日歩無視で寄りの滑りのみ
0.15%、ロング=日興手数料込み安全側 0.20%**。`validate_edges --short-cost/--long-cost` で変更可。

backtest_* の単純 |t| では有意に見えるセル (kouhou_seikyu×大引け後 t+2.24 等) も、
多重検定・クラスタ補正後は p>0.05 に落ちる。**実運用判断は edge_validation.md を基準にする**こと。

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
  - = PO発表の翌営業日寄りでショート → 発行価格決定日の引けで買戻し (数日またぎ)。

**既知3エッジ監査** (`validate_edges` の専用セクション、当時の特殊な仕掛けのまま再評価、
方向別コスト net + 日付クラスタ頑健 t + FDR + walk-forward OOS):
- ③ リート short のみ通過 (t_clust+4.02 / p=0.0001 / OOS test +0.89% / n177)。主力エッジ健在。
- ① 発表翌日 9:10 long は cost+クラスタ後 t_clust+1.63 / p=0.10 で**脱落** (OOS+0.12%)。
- ② 受渡日GD long も t_clust+1.07 / p=0.28 で**脱落** (OOS+0.48%、符号は正だが noise 内)。
- → 当時の raw |t| (3.07 / 2.40) は cost 前・クラスタ未補正。実運用は③のみ信頼。
  ③は short(0.15%) なので方向別コスト化で raw 寄りに強化、①②は long(0.20%) で据え置き。

新発見 (Phase D, cost 0.20% net):
- decide × REIT × 貸借 short: t +3.43 / EV +0.93% / n 131
- decide × 普通 × 貸借 short: t +2.54 / EV +0.91% / n 349

### holdings (大量保有報告)
holdings-tracker raw (価格 enrich 済、J-Quants 追加 fetch 不要) を共通スキーマに展開し、
PO と同じ extract → analyze → backtest で期待値検証。partition は purpose × holder。
source of truth は `reports/holdings_backtest.md`。

現状の所見 (cost 0.20% net):
- データ期間が短い (~2025-05〜2026-04, n≈1700) ため n が小さめ。
- |t|≥2 かつ n≥50 の頑健なセルは**未検出**。上位は n<15 の小サンプル
  (資産運用×事業会社 short t+2.32/n11、業務提携×PEファンド short t+2.03/n5 等)。
- 高 n セル (純投資×事業会社 n223、取引関係×国内ファンド n238) は net ほぼゼロ。
- 要・期間拡大 + 別軸 (gap_label / holding_ratio / filer_freq) 探索。
- 価格タイミング: **提出日の翌営業日の寄り→引け** (J-Quants 実データ照合で確定済)。
  prev_close=提出日終値。提出日に開示を見て翌営業日寄りでエントリー可能＝**約定可能**。
- 非独立サンプル注意: 同一銘柄・同日に複数提出者の報告あり (holdings 167件 / PO 58件)。
  翌日リターンが同値で n/t を水増しするため、有意性判断は `query_* --collapse-daily`
  (同一 code+date を1観測に集約) で独立補正して確認すること。data_health に独立性行あり。

## 災害イベント駆動 (台風) — scripts/disaster_event/

JMA RSMC東京ベストトラック (bst_all.zip) を起点に、台風イベント周辺の
テーマ16銘柄リターンを検証する独立パイプライン (network: `www.jma.go.jp` allowlist 要)。

```bash
python -m scripts.disaster_event.fetch_typhoon_data      # bst取得→best_track.json
python -m scripts.disaster_event.identify_typhoons       # 日本接近・大型台風49件→typhoon_records.json
python -m scripts.disaster_event.enrich_typhoon_prices   # 16銘柄日足→typhoon_price_data.json
python -m scripts.disaster_event.analyze_typhoon_edge    # reports/typhoon_event_simple.md
```

所見 (簡易版、FDR/OOS 未適用): 5戦略いずれも簡易基準
(EV>0.5%&勝率>55%&n≥20&t_clust>+1.5、台風単位クラスタ補正) を通過せず＝**エッジなし**。
直撃日ロングは有意マイナス。強度を絞っても復興需要ロングは強まらない (有名事例は非代表)。
詳細: `reports/typhoon_event_simple.md` / `reports/edge_candidates_summary.md`。

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
- PO の attrs キー変更 = `extract_po._attrs_*` + `analyze_po_edge._METRIC_FIELDS_BY_STAGE` + `audit_all._PO_ATTR_KEYS_BY_STAGE` + `query_po._METRIC_CHOICES`
- holdings の field 追加 = `extract_holdings._PRICE_MAP / _DIM_KEYS` + `analyze_holdings_edge._METRIC_FIELDS` + `query_holdings._METRIC_CHOICES` + `audit_all._HOLDINGS_ATTR_KEYS`
- ad-hoc 探索 CLI は 3 ソース共通: `query_kouaku` / `query_po` / `query_holdings` (集計・表示は `_query_report` を共有)
- 約定可能性 (流動性) フィルタ: `query_holdings --min-turnover/--min-mktcap`、`query_po --min-mktcap`。
  非独立補正は `--collapse-daily`。エッジの最終判断は `validate_edges` (FDR+OOS) を基準に。

## 開発ブランチ規約

ユーザー指示に従う。1 機能 1 PR、squash merge デフォルト。


## 公式 J-Quants データ基盤 (TDnet/財務/銘柄属性アドオン — 2026-06 稼働確認)

> **エンドポイント完全リファレンスは [docs/jquants_endpoints.md](docs/jquants_endpoints.md)**
> （使える/403の一覧・テキストフィールド・取得スクリプト・前提条件）。

yanoshin(第三者TDnetミラー)に加え、契約アドオンで以下の**公式エンドポイント**が使える
(キーは環境変数 JQUANTS_API_KEY、base=https://api.jquants.com/v2):

- `/td/list` (code or date) / `/td/bulk` (5年一括CSV.gz, 759k件): 適時開示インデックス。
  権威フィールド **DiscDate/DiscTime/DiscItems(公開項目コード)**。yanoshin と日時 160/160 一致・
  主要材料捕捉率 99.6〜100%(=過去分析は yanoshin 起因で非破損)。disc_no 先頭8桁は採番日で公表日とは別。
- `/fins/summary` (code): 当期利益NP/営業OP/経常OdP/売上Sales/EPS・配当(Div1Q〜DivFY)。**連続YoY**が取れる。
  (`/fins/details` BS/PL/CF と `/fins/dividend` は契約外403)
- `/equities/master` (date): 全4,449銘柄の **業種(S17/S33)・規模区分(ScaleCat→大型/中型/小型)・
  信用区分(Mrgn)・市場(Mkt)**。1コール。`scripts/edge_candidates/fetch_equities_master.py` で取得
  (data/edge_candidates/equities_master.json, 永続化)。
  ※以前「業種/時価総額は listed/info 403で不可」としたが /equities/master で取得可能(訂正)。

### 「程度による分析の死角」(重要な構造課題)
kouaku 分類器は閾値でタグ化するため中立帯が**丸ごと未分析**:
決算NP YoY ±10% / 業績修正±3% / 配当修正±3%。この帯の材料(軽い増減益・軽微修正)は
好悪ペアにならず kouaku から脱落していた。**/fins/summary の連続YoYで magnitude 軸を復活**させ
正面検証するのが死角埋めの方針。

### 公式基盤の新パイプライン/スクリプト
- `fetch_equities_master` — 銘柄属性マスタ(横断結合の土台)。
- `extract_buyback_earnings` / `enrich_buyback_returns` / `analyze_buyback_earnings` —
  自社株買い×同日決算を連続NP YoYで検証(キッコーマン型)。中間データは .gitignore。
- `enrich_po_close_scale` / `analyze_po_scale_timing` — PO に翌日引け(15:30)EV付与+規模/信用結合、
  規模×時刻 FDR/OOS。

### このセッションの新findings
- **PO発表翌日ロングは中型(Mid400≈300億-1兆)に偏在しFDR生存**: 中型×翌日GD×引け
  net+1.14%/t_clust+3.32/OOS+1.52% ★。大型(Core30/Large70=1兆級)は9:30以降-0.8〜-1.3%で逆、
  小型は負。広義のPO翌日ロング脱落は規模で中型に絞ると通過=規模が鍵。`reports/po_scale_timing.md`。
- **#4分割は小型に偏在**(B規模軸: 小型+10日α+2.13%/t+2.65 ≫ 中大型は負)。`reports/edge4_split_detailed.md`。
- **キッコーマン型(軽い減益+自社株買いロング)はエッジなし**(全帯フラット、FDR生存ゼロ)。
  キッコーマン2026の引け+7.24%は帯平均+0.11%に対する外れ値(n=1)。`reports/buyback_earnings.md`。

### 残ロードマップ (公式DiscItems全面移行 — 多セッション規模)
1. kouaku を公式DiscItems分類+連続magnitudeで全面再構築(死角ゼロ・確定エッジ再検証)。
   ※Stage2核心(確定エッジ zouhai_kahou_nx の程度別再検証)は完了し健在を確認済。
2. 他の死角(業績修正±3%帯・配当±3%帯)を同手法で順次被覆。
3. 全エッジ横断の統一FDR/OOS再検証(方向別コスト+クラスタt+FDR+walk-forward OOS)。

### 死角駆逐 完了 (2026-06)
「程度による分析の死角」を2系統とも掃討:
1. **タグ内二値化の死角** (genshu=any<-10% 等で程度が潰れる): 全(subpattern×開示時刻)を
   埋め込みmagnitude三分位に割り一括掃討 (`scripts.edge_candidates.analyze_magnitude_sweep`,
   `reports/magnitude_sweep.md`)。全48セル横断FDRで**3セル生存=隠れエッジ**:
   - zouhai_kahou_nx×大引け後×中magnitude(来期-30〜-17%) short **net+1.34%/t+3.70/勝率68%**
     (確定エッジの芯。全体+0.88%より強い。中程度の来期失望が本体、極端減額は弱化)。
   - 同×強(-17〜-10%) short +0.87%/t+3.05。
   - **kouhou_nx_genshu×大引け後×深減益(当期-48〜-10%) short net+0.40%/t+3.00/n502 (新発見)**。
     来期上方+当期減益で、当期の深い減益ほど翌日(寄→引)ショートが効く。
2. **閾値除外(中立帯)の死角**: 決算±10%×自社株買い(キッコーマン型)=エッジなし、
   muhai系(好材料×無風決算)も全て|t|<1.4でエッジなし → 軽微帯に隠れエッジ無しを確認。
→ 結論: 隠れていたのは『深い程度バンドの精緻化』(2エッジ発見)で、軽い程度帯は空。死角は駆逐済。


### #4 株式分割ロング — 規模・保有・勝率の運用ルール (2026-06 確定)
- **規模が決定的: 小型のみ。超大型(Core30/Large70)は対象外**。
  小型 +10日α+2.13%/t+2.6 ✅ ≫ 中型(負) ・ **超大型 +10日α-0.27%(負)/勝率44%**。
  理由: 分割ロングの源泉は『個人の参加しやすさ=retailフロー/流動性改善』。超大型は元々
  流動的・機関中心・効率価格で限界改善が小さく再評価が起きない(むしろ大口益出しで下押し)。
- **保有は10日不要、5日で十分厚い**: 全体 +5日α+1.19%、効きどころの**信用銘柄×GU寄り×+5日で+3.97%**
  (n274/勝率47%)。+3日でも信用×GUで+2.67%。最短の通過点は+3日(全体+0.76%)。
- **勝率は構造的に上げられない(44〜49%)**: どのフィルタでも勝率はほぼ不変。フィルタが増やすのは
  『勝ちの大きさ(EV/ペイオフ)』であって『勝つ頻度』ではない。=宝くじ型(平均勝ち+10%≫平均負け-6%)。
  運用は『低勝率・高ペイオフを受容/全シグナル機械的に/勝ちを早く切らない/連敗に耐える資金管理』。
- 推奨構成: **小型 × 信用銘柄 × GU寄り(翌寄り前日比>+1%) × 翌寄り買い→+5日引け売り**。
  ただし信用×GU等は加点フィルタ(過剰最適化注意)、FDR通過の確定は『全体の+3/+5/+10日α』。


### 増配＋来期下方修正ショート — 精緻化の運用ルール (2026-06 確定)
確定エッジ zouhai_kahou_nx×大引け後 short(全体+0.88%/t4.98/勝67%/n239) の効きどころ:
- **好材料は"増配"specific**: 来期下方修正と組んで効くのは増配のみ。
  増配+来期下方 +0.88%/t4.98 ≫ **自社株買い+来期下方 +0.10%/t0.36(無)** ・増益決算+来期下方 +0.25%/t1.54(弱)。
  =「来期を下げるのに"配当を増やす"取り繕い」を市場が最も嫌気。自社株買い(機動的)・増益(本物)では効かない。
- **増配が大きいほど強い**: 増配大(25〜50%) short +1.85%/t4.49/勝75%(n36) ≫ 小(〜10%)+0.78%/t1.95。
  派手に取り繕うほど売られる。
- **来期下方は中程度(−30〜−17%)が芯**: +1.30%/t3.63/勝70%(n83)。極端な減額(<−50%)はむしろ弱化。
- 最強の絞り込み: **大きな増配(25-50%) × 中程度の来期下方(−30〜−17%) × 大引け後 → 翌寄り売り→当日引け買戻**。
  ただし絞るとn小→確定母体は『増配+来期下方(全体n239)』、程度の絞りは期待値を厚くする加点(過剰最適化注意)。
