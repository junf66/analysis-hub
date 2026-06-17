# J-Quants API アドオン エンドポイント リファレンス

> analysis-hub で利用可能な J-Quants v2 エンドポイント一覧（2026-06-03 ライブ稼働確認済み）。
> 接続: `scripts/_jquants.py`（`get_list(path, **params)`）。base=`https://api.jquants.com/v2`。
> 前提: 環境変数 `JQUANTS_API_KEY` と ネットワーク許可ドメイン `api.jquants.com`（Proは `api.jquants-pro.com`）。

## ✅ 使えるエンドポイント

### `/td/list`（params: `code` または `date`）── 適時開示インデックス
権威フィールド: `DiscDate` / `DiscTime` / `DiscItems`(公開項目コード)。
**テキスト最重要**: `Title`（開示タイトル全文。例「業績予想の修正に関するお知らせ」）。
その他: `DiscNo`（先頭8桁は採番日で公表日とは別）, `Code`, `Name`(銘柄名), `RevNo`。
yanoshin と日時 160/160 一致・主要材料捕捉率 99.6〜100%。

### `/td/bulk` ── 適時開示 5年一括（CSV.gz, 759k件）
`/td/list` と同内容を一括取得。`scripts/edge_candidates/fetch_tdnet_index.py` 参照。

### `/equities/master`（params: `date`）── 銘柄属性マスタ（全4,451銘柄, 1コール）
**テキスト豊富**:
- `CoName` / `CoNameEn`（社名）
- `S17` / `S17Nm`（17業種）, `S33` / `S33Nm`（33業種）
- `ScaleCat`（TOPIX規模区分: Core30/Large70/Mid400/Small1/Small2 → 大型/中型/小型）
- `Mkt` / `MktNm`（市場: プライム/スタンダード/グロース）
- `Mrgn` / `MrgnNm`（信用区分: 貸借/信用）, `ProdCat`
取得: `scripts/edge_candidates/fetch_equities_master.py` → `data/edge_candidates/equities_master.json`（永続化）。

### `/fins/summary`（params: `code`）── 財務サマリ（連続YoYが取れる）
数値: `Sales`/`OP`/`OdP`/`NP`/`EPS`/`DEPS`/`TA`/`Eq`/`EqAR`、配当(Div1Q〜DivFY)。
テキスト: `DocType`（決算種別, 例 1QFinancialStatements_Consolidated_US）, `CurPerType`(1Q/FY), 期間日付。

### `/indices/bars/daily/topix` ── TOPIX日次OHLC（β調整用）
`scripts/edge_candidates/fetch_topix.py` → `data/edge_candidates/topix_daily.json`。

### `/equities/bars/daily`（params: `code`, `from`, `to`）── 個別日足（β推定・価格検証用）
`AdjO/AdjH/AdjL/AdjC`（調整済）等。`fetch_daily_bars_universe.py` 参照。

### `/markets/calendar` ── 営業日カレンダー（`Date`/`HolDiv`）

### `/td/files` ── 適時開示ファイルDL URL（本文PDF）── 2026-06: パラメータ不明で400(要特定)。取れれば本文からPO日程/ロックアップ条件抽出可。

### `/markets/margin-interest` ── 週次信用取引残高（2016-・**全history**）── 2026-06確認
`code`指定。`LongVol`(信用買残)/`LongStdVol`(制度買残)/`LongNegVol`(一般買残)・`ShrtVol`系・`Date`(毎週金)・`IssType`。
信用期日プレー(26週)・取組の検証に。※検証結果=反発ロングは負け(正本 不採用)。

### `/markets/margin-alert` ── 日々公表信用取引残高＋**信用規制区分** ── 2026-06確認（高価値）
`date`指定で当日の規制銘柄。`TSEMrgnRegCls`＋`PubReason{Restricted/DailyPublication/Monitoring/
RestrictedByJSF(=日証金貸借停止≒売り禁)/PrecautionByJSF(注意喚起)}`・日次`LongStdOut`等。
**⑩Rの売り禁を自動判定できる**（daily_scanの規制フラグ自動化）。

### `/markets/short-sale-report` ── 空売り残高報告（0.5%以上）── 2026-06確認
`code`指定。`SSName`(空売り主)・`ShrtPosToSO`(対発行済%)・`ShrtPosShares`・`DiscDate`。機関の大口空売り→踏み上げ/オーバーハング検証。

### `/markets/short-ratio` ── 業種別空売り比率 ── 2026-06確認
`date`指定。`S33`(33業種)・`ShrtWithResVa`/`ShrtNoResVa`(価格規制有無別空売り額)・`SellExShortVa`。セクター需給。

### `/indices/bars/daily` ── 指数四本値（TOPIX以外も）── 2026-06確認（高価値）
`code`指定で各種指数のOHLC。**マザーズ/グロース/JASDAQ等の新興指数が取れる**＝⑩Rの地合いを
breadth代理でなく実新興指数で検証可能(びびり「新興一方通行」)。※index codeの対応表は要特定(0028等)。

### `/equities/investor-types` ── 投資部門別売買状況 ── 2026-06確認
`from`/`to`指定。海外/個人/銀行/証券自己 等の売買代金(`*Buy`/`*Sell`/`*Bal`)。**cis「フォース(機関継続買い)」の代理**フロー。

## ❌ 契約外（403・使用不可）
- `/fins/details`（BS/PL/CF 明細）・`/fins/dividend`（配当明細）── 2026-06再確認も403
- `/equities/trades`（ティック）── **403。板/歩み値=cis型は再現不能**
- `/markets/breakdown`（売買内訳）── 403
- `/derivatives/bars/daily/futures`・`/options`・`/options/225`（先物・オプション）── 403
- `/listed/info`（→ 銘柄属性は `/equities/master` で代替）
- Pro 専用（自社株買い TDnet 等, `api.jquants-pro.com`）は Light 契約のため未取得

> ⚠️ **取得範囲の制限**: `/equities/bars/daily` は `from` が約2017年より前だと **HTTP400(subscription)**。古い期間は2017-01-01開始で。

## 💡 未活用の伸びしろ
`/td/list` の **`Title`（開示タイトル全文）** で材料分類（Titleマイニング・2026-06実施）。
**新規(2026-06)**: `/indices/bars/daily`(新興指数→⑩R地合い実測)・`/equities/investor-types`(主体別→cisフォース)・
`/markets/margin-alert`(売り禁自動判定→⑩R執行)・`short-sale-report`/`short-ratio`(踏み上げ/需給)。
