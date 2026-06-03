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

## ❌ 契約外（403・使用不可）
- `/fins/details`（BS/PL/CF 明細）
- `/fins/dividend`（配当明細）
- `/listed/info`（→ 銘柄属性は `/equities/master` で代替）
- Pro 専用（自社株買い TDnet 等, `api.jquants-pro.com`）は Light 契約のため未取得

## 💡 未活用の伸びしろ
`/td/list` の **`Title`（開示タイトル全文）** はテキストとして最も使えるが現状ほぼ未活用。
タイトル文言で材料を直接分類（増配/減配・上方/下方修正・自己株取得 等）し、
既存の分類器（DiscItems コード＋好悪ルール）の死角を埋める新サブパターンを切り出せる可能性。
