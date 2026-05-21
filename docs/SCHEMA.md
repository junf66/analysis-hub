# SCHEMA reference

## 共通スキーマ (各ソース共通の最小フィールド)

| フィールド | 型 | 意味 |
|---|---|---|
| `id` | str | 源泉プレフィックス付き一意 ID (例: `kouaku:7203:2025-01-22`) |
| `code` | str | 4 桁文字列、先頭ゼロ保持 (例: `"4502"`)。5桁末尾0 の J-Quants からは末尾を落とす |
| `event_date` | str | ISO date `YYYY-MM-DD` |
| `event_type` | str | `po_announce` / `holdings_filing` / `kouaku_mixed` 等 |
| `source` | str | `po-tracker` / `edinet` / `tdnet+fins` |
| `ref_id` | str | ソース固有の参照 ID |
| `attrs` | dict | ソース固有のフィールド一式 |

## `data/kouaku_records.json` のレコード

```jsonc
{
  "id": "kouaku:7203:2025-01-22",
  "code": "7203",
  "event_date": "2025-01-22",
  "event_type": "kouaku_mixed",
  "source": "tdnet+fins",
  "ref_id": "7203_2025-01-22",
  "subpattern": "kouhou_genshu",        // 下表参照
  "good_factors": [                      // 好材料イベント (>=1 件)
    {
      "title": "EarnForecastRevision",
      "subpattern_hint": "kouhou",
      "reason": "EarnForecastRevision NP+15.3%",
      "disc_no": "FIN_7203_2025-01-22",
      "disc_time": "15:30:00",
      "metric": {"NP_revision_pct": 15.3}
    }
  ],
  "bad_factors": [...],                  // 悪材料イベント (>=1 件)
  "attrs": {
    // ---- enrich_price_kouaku 由来 (日足) ----
    "prev_close": 2915.5,                // event_date 終値
    "next_open":  2893.0,                // 翌営業日始値
    "next_high":  2928.0,
    "next_low":   2857.0,
    "next_close": 2925.0,
    "gap_pct":    -0.77,                 // (next_open - prev_close) / prev_close * 100
    "next_day_open_to_close_ret": 1.11,  // (next_close - next_open) / next_open * 100
    "next_day_open_to_high_ret":  1.21,
    "next_day_open_to_low_ret":  -1.24,
    "next_day_full_ret":          0.33,
    "event_bar_date": "2025-01-22",
    "next_bar_date":  "2025-01-23",
    "limit_locked": false,               // 値幅制限ロック (寄=高=安=引 かつ |gap|>=15)

    // ---- enrich_price_kouaku 由来 (分足、2024-05-21 以降のみ) ----
    "next_open_900":      2893.0,        // 翌営業日 9:00 (= 最初の bar の O)
    "next_open_first_time": "09:00",     // illiquid 銘柄では 09:00 でない可能性
    "next_day_905_ret":  -0.90,          // 翌寄り → 9:05 close
    "next_day_910_ret":  -0.45,
    "next_day_915_ret":  -0.66,
    "next_day_930_ret":  -0.54,
    "next_day_1000_ret": -0.48,
    "next_day_morning_ret": 0.24,        // 翌寄り → 11:30 (前場引)

    // ---- 失敗時のみ ----
    "price_error": "no bars",            // 上場廃止等で daily 取得不可
    "minute_error": "..."                // 分足取得不可 (契約範囲外等)
  }
}
```

## subpattern 一覧

| subpattern | 構成 | 説明 |
|---|---|---|
| `jisha_kahou` | 自社株買い + 業績下方修正 | 現状 Light 契約で 0 件 |
| `jisha_genshu` | 自社株買い + 減益決算 | 同上 |
| `fukuhai_genshu` | 復配 + 減益 | 配当復活と減益が同日 |
| `zouhai_genshu` | 増配 + 減益 | 配当増額と減益が同日 |
| `tokubai_kahou` | 特別配当 + 下方修正 | 一時的還元 + 業績悪化 |
| `kouhou_genshu` | 業績上方修正 + 減益決算 | 「次期は明るいが今期は減益」 |
| `kouhou_kahou` | 業績上方修正 + 業績下方修正 | 同日に上方と下方が混在 |
| `kouhou_muhai` | 業績上方修正 + 無配転落 | 上方なのに配当ゼロ |
| `kouhou_genhai` | 業績上方修正 + 減配 | 上方なのに減配 |
| `other` | 上記いずれにも当てはまらない | 残余 |

## subpattern_hint 一覧 (好/悪材料の分類タグ)

| hint | polarity | 由来 |
|---|---|---|
| `jisha` | good | 自社株買い (TDnet) |
| `fukuhai` | good | 復配 (DividendForecastRevision で旧0→新>0) |
| `zouhai` | good | 増配 (DividendForecastRevision Div +3% 以上) |
| `tokubai` | good | 特別配当 (タイトルマッチ) |
| `kouhou` | good | 業績上方修正 (EarnForecastRevision NP +3% 以上) または 決算短信 NP YoY +10% 以上 |
| `kahou` | bad | 業績下方修正 (EarnForecastRevision NP -3% 以下) |
| `genshu` | bad | 減益決算 (決算短信 NP YoY -10% 以下) |
| `kessan` | neutral | 決算短信 (中立、prior と比較できない場合) |
| `muhai` | bad | 無配転落 (DividendForecastRevision で旧>0→新0) |
| `genhai` | bad | 減配 (DividendForecastRevision Div -3% 以下) |
| `seikyu` | bad | 公募増資・特別損失等 (タイトルマッチ、現状未活性) |

## メトリクス一覧 (query_kouaku の `--metric` で指定可能)

| key | 意味 | データ源 |
|---|---|---|
| `gap_pct` | 前日終 → 翌寄り | 日足 |
| `next_day_open_to_close_ret` | 翌寄り → 翌引け | 日足 |
| `next_day_open_to_high_ret` | 翌寄り → 翌高値 | 日足 |
| `next_day_open_to_low_ret` | 翌寄り → 翌安値 | 日足 |
| `next_day_full_ret` | 前日終 → 翌引け | 日足 |
| `next_day_905_ret` | 翌寄り → 9:05 | 分足 |
| `next_day_910_ret` | 翌寄り → 9:10 | 分足 |
| `next_day_915_ret` | 翌寄り → 9:15 | 分足 |
| `next_day_930_ret` | 翌寄り → 9:30 | 分足 |
| `next_day_1000_ret` | 翌寄り → 10:00 | 分足 |
| `next_day_morning_ret` | 翌寄り → 前場引 (11:30) | 分足 |

## DiscTime バケット

| bucket | 範囲 |
|---|---|
| 寄前 | 00:00 - 08:59 |
| 寄り中 | 09:00 - 10:59 |
| 場中 | 11:00 - 14:59 |
| 引け間際 | 15:00 - 15:29 |
| 大引け後 | 15:30+ |
