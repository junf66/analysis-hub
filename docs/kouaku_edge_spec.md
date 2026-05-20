# 好悪エッジ検証モジュール (kouaku_mixed)

## 1. 目的

**同日同銘柄に「好材料」と「悪材料」が両方発表されたとき、翌日寄り後にどんな期待値が立つか**を統計検証する。

直感: 「材料がぶつかった日は、初期反応が過剰/過小になりやすい → 翌日寄りから始まる ±90 分のリターンに偏りが残る」かを N と EV で検証する。

## 2. 既知 N=3 事例 (発見契機)

| code | サブパターン | 観測 |
|------|-------------|------|
| 7990 | jisha_genshu (自社株買い + 減益) | GU +10% |
| 4246 | jisha_genshu (自社株買い + 減益) | GU +12% |
| 5253 | (要確認、配当系もしくは業績修正系) | GD -7.6% → +5.5% |

これらが分類器・抽出器を通過することを **test 用 fixture でロックする**。

## 3. サブパターン定義

| key | 構成 | 検出条件 (タイトル正規表現) |
|-----|------|------------------------------|
| `jisha_genshu` | 自社株買い + 減益 | 好: `自己株式.*取得` / 悪: `決算短信`(NP YoY<0) または `業績.*下方修正` |
| `jisha_kahou` | 自社株買い + 下方修正 | 好: `自己株式.*取得` / 悪: `業績.*下方修正` |
| `fukuhai_genshu` | 復配 + 減益 | 好: `復配` / 悪: `決算短信`(NP YoY<0) または `下方修正` |
| `zouhai_genshu` | 増配 + 減益 | 好: `配当.*増配\|配当予想.*上方修正` / 悪: 同上 |
| `tokubai_kahou` | 特別配当 + 下方修正 | 好: `特別配当\|記念配当` / 悪: `業績.*下方修正` |
| `other` | 上記以外の好+悪同日同銘柄 | 辞書定義のいずれかが該当 |

ルール:
- 1日 1 銘柄から **複数の好/悪タイトル**が出ても、最初に該当したサブパターンを採用 (other は最終フォールバック)
- 「好材料・悪材料」両方が同日に **少なくとも 1件ずつ** あることが必須
- 売買時間外 (15:30 以降) 開示も翌営業日寄り起点で計算

## 4. データソース

### Phase 1 主軸: J-Quants Pro `/v2/markets/share_buyback_tdnet` 他
- 自社株買い (TDnet): `api.jquants-pro.com/v2/markets/share_buyback_tdnet`
- 業績修正/決算短信/配当: `api.jquants.com/v2/fins/summary` (DocType で識別)
- 価格: `api.jquants.com/v2/equities/bars/daily` + (将来) 分足

### Phase 2 フォールバック構造 (構造のみ用意、本実装はしない)
- EDINET API
- TDnet 公式検索 (release.tdnet.info)

## 5. 共通スキーマ準拠 (kouaku_records.json)

```json
{
  "id": "kouaku:7990:2025-08-12",
  "code": "7990",
  "event_date": "2025-08-12",
  "event_type": "kouaku_mixed",
  "source": "tdnet",
  "ref_id": "7990_2025-08-12",
  "subpattern": "jisha_genshu",
  "good_factors": [
    {"title": "自己株式取得に係る事項の決定に関するお知らせ", "disc_no": "...", "disc_time": "15:30:00"}
  ],
  "bad_factors": [
    {"title": "2026年3月期 第1四半期決算短信", "disc_no": "...", "disc_time": "15:00:00",
     "metric": "NP_YoY", "value_pct": -23.4}
  ],
  "attrs": {...}  // 価格 enrichment 後に補完
}
```

`attrs` (enrichment 後) には次が追加される:
- `prev_close` (event_date の終値)
- `next_open` / `next_open_905` / `next_open_910` / `next_open_915` / `next_open_close`
- `gap_pct = (next_open - prev_close) / prev_close * 100`
- `next_day_910_ret = (next_open_910 - next_open) / next_open * 100` (主戦略指標)
- `next_day_close_ret`

## 6. 検証期間

- 過去 5 年 (rolling 5y from today)
- N が小さければ全期間を併記

## 7. 出力

- `data/kouaku_records.json` (中間成果、git 管理)
- `reports/kouaku_analysis.md` (全体サマリ)
- `reports/kouaku_by_subpattern/{subpattern}.md` (サブパターン別)

## 8. 統計指標 (analyzers/po_edges.py の `_stats` を流用)

各サブパターンについて:
- n
- mean / median return (% 単位)
- σ (sample stdev)
- win rate (>0 比率)
- SE = σ/√n
- t = mean / SE

戦略の有効性判定: |t| > 2 をスクリーニング基準 (確証ではなく目印)

## 9. テスト

`tests/test_kouaku_known_cases.py`:
- 7990, 4246, 5253 の disclosure fixture (cache/disclosures/fixtures/*.json) を入力して
  classify_kouaku → extract_mixed_disclosures が期待通り subpattern を返すことを assert

## 10. 開発ブランチ

`claude/implement-sentiment-validation-jFnYn`
