"""市場（TOPIX）超過収益でエッジ候補を再検証する共通ユーティリティ。

候補の attrs に格納された `dN_ret` (% change, entry_open → +N営業日close) から
同期間の TOPIX リターンを引いた alpha_dN を計算して付与する。

beta=1 近似 (market-adjusted)。完全な β 推定は別途 daily_bars_universe.json
完了後に実施可能だが、まずは β=1 で市場効果を粗く除いて純エッジが残るかを確認。

入出力:
  - 入力 records は `attrs.entry_date` と `attrs.d{N}_ret` を持つこと
  - alpha_d{N}_ret を新規 attrs に追加 (TOPIX 取れない event は alpha も None=未設定)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"


def load_topix(path: Path = TOPIX_PATH) -> list[dict[str, Any]]:
    """TOPIX 日足を Date 昇順のリストで返す。"""
    rows = json.loads(path.read_text())["records"]
    rows.sort(key=lambda r: r["Date"])
    return rows


def _find_idx(topix: list[dict[str, Any]], date: str) -> int | None:
    """TOPIX 配列で date 以降の最初の取引日 index を返す (date と等しい日があれば優先)。"""
    lo, hi = 0, len(topix) - 1
    if not topix or date > topix[-1]["Date"]:
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        if topix[mid]["Date"] < date:
            lo = mid + 1
        else:
            hi = mid
    return lo


def topix_return(topix: list[dict[str, Any]], entry_date: str, n_days: int) -> float | None:
    """entry_date の Open から n_days 営業日後の Close までの TOPIX リターン %。

    entry_date が休場日なら直近翌営業日を採用 (Open ベース)。
    n_days 先が範囲外なら None。
    """
    i = _find_idx(topix, entry_date)
    if i is None:
        return None
    j = i + n_days  # 0-indexed: i=エントリ日、j=+n_days 後 = 同日 close で見るなら i+n_days
    if j >= len(topix):
        return None
    o = topix[i].get("O")
    c = topix[j].get("C")
    if not o or not c:
        return None
    return (c / o - 1.0) * 100.0


def topix_return_between(topix: list[dict[str, Any]], d_from: str, d_to: str) -> float | None:
    """d_from の Open から d_to の Open までの TOPIX リターン % (open→open)。

    可変保有 (RSI 等、エントリ日→エグジット日が trade ごとに違う) のベンチマーク用。
    両日とも休場なら直近翌取引日に丸める。範囲外/欠損は None。
    """
    i = _find_idx(topix, d_from)
    j = _find_idx(topix, d_to)
    if i is None or j is None or j < i:
        return None
    o = topix[i].get("O")
    c = topix[j].get("O")
    if not o or not c:
        return None
    return (c / o - 1.0) * 100.0


def enrich_with_alpha(records: list[dict[str, Any]], days: list[int],
                      topix_path: Path = TOPIX_PATH) -> dict[str, int]:
    """records の attrs に alpha_d{N}_ret を追加。戦績ステータス dict を返す。"""
    topix = load_topix(topix_path)
    ok, miss_date, miss_topix = 0, 0, 0
    for r in records:
        a = r.setdefault("attrs", {})
        ed = a.get("entry_date")
        if not ed:
            miss_date += 1
            continue
        for n in days:
            sr = a.get(f"d{n}_ret")
            if sr is None:
                continue
            tr = topix_return(topix, ed, n)
            if tr is None:
                miss_topix += 1
                continue
            a[f"alpha_d{n}_ret"] = sr - tr
            ok += 1
    return {"alpha_added": ok, "miss_entry_date": miss_date, "miss_topix": miss_topix}
