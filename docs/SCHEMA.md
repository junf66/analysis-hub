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

## subpattern 命名規約 (動的)

`{positive_hint}_{negative_hint}` の組合せで動的に命名される。
hint 優先度は `extract_mixed_disclosures._POSITIVE_HINT_ORDER` /
`_NEGATIVE_HINT_ORDER` の宣言順。両方未マッチなら `other`。

例: 上方修正 + 減益 = `kouhou_genshu` / 増配 + 減益 = `zouhai_genshu` /
来期増益予想 + 当期下方修正 = `kouhou_nx_kahou` /
TOB 賛同 + 減配 = `tob_genhai` (TDnet 必要)。

## subpattern_hint 一覧 (好/悪材料の分類タグ)

### ポジティブ (POSITIVE_HINT_ORDER)

| hint | 由来 | 取得状態 |
|---|---|---|
| `jisha` | 自社株買い (TDnet) | TDnet only、現環境 0 |
| `tob` | TOB 賛同 (TDnet タイトル) | TDnet only、現環境 0 |
| `kouhou` | 業績上方修正 (EarnForecastRevision NP +3% 以上) または 決算短信 NP YoY +10% 以上 | ✅ |
| `kouhou_nx` | 来期増益予想 (決算短信FY 内 NxFNp vs 当期 NP +10% 以上) | ✅ |
| `zouhai` | 増配 (DividendForecastRevision または 決算短信 DivAnn YoY +3% 以上) | ✅ |
| `fukuhai` | 復配 (旧 DivAnn=0 → 新 >0) | ✅ |
| `tokubai` | 特別配当・記念配当 (TDnet タイトル) | TDnet 必要 |
| `yutai_new` | 株主優待新設・拡充 (TDnet タイトル) | TDnet only |
| `kabushiki_bunkatsu` | 株式分割 (TDnet タイトル) | TDnet only |

### ネガティブ (NEGATIVE_HINT_ORDER)

| hint | 由来 | 取得状態 |
|---|---|---|
| `kahou` | 業績下方修正 (EarnForecastRevision NP -3% 以下) | ✅ |
| `kahou_nx` | 来期減益予想 (決算短信FY 内 NxFNp vs 当期 NP -10% 以下) | ✅ |
| `genshu` | 減益決算 (決算短信 NP YoY -10% 以下) | ✅ |
| `genhai` | 減配 (DivAnn YoY -3% 以下) | ✅ |
| `muhai` | 無配転落 (旧 DivAnn>0 → 新 =0) | ✅ |
| `seikyu` | 公募増資・第三者割当・特別損失・減損・行政処分・訴訟等 (TDnet タイトル) | TDnet only |
| `yutai_end` | 株主優待廃止 (TDnet タイトル) | TDnet only |

### 中立 (subpattern 命名から除外される)

| hint | 由来 |
|---|---|
| `kessan` | 決算短信そのもの (前年比不明の場合) — polarity=neutral |

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
