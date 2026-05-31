"""TDnet /td/list を期間走査し、エッジ候補に関係する開示だけ索引化する。

#1上方修正 / #3増配 / #4株式分割 / #5業務提携・受注 のイベント源、および
#2自社株買い単独の「悪材料なし」判定用 (同一 code+date の悪材料開示) のデータ源。
ハング耐性: 日次走査をチェックポイント保存し、再実行で last_date の続きから resume。
出力: data/edge_candidates/tdnet_index.json
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "tdnet_index.json"

# タイトル部分一致 → 分類タグ (good=好材料候補 / bad=悪材料=#2フィルタ用)
KEYWORDS: dict[str, str] = {
    "上方修正": "good_kessan_up", "増配": "good_zouhai",
    "配当予想の修正": "good_div_rev", "株式分割": "good_split",
    "資本業務提携": "good_teikei", "業務提携": "good_teikei", "受注": "good_juchu",
    "自己株式の取得": "good_jisha",
    "下方修正": "bad_kessan_down", "減配": "bad_genpai",
    "特別損失": "bad_tokuson", "減損": "bad_genson", "第三者割当": "bad_daisansha",
}


def classify_title(title: str) -> list[str]:
    """タイトルに含まれるキーワードの分類タグ一覧を返す (該当なしは空)。"""
    t = title or ""
    return sorted({tag for kw, tag in KEYWORDS.items() if kw in t})


def to_record(row: dict[str, Any]) -> dict[str, Any] | None:
    """/td/list の1行を索引レコードに変換 (関係キーワードを含む行のみ、code4桁化)。"""
    tags = classify_title(row.get("Title") or "")
    if not tags:
        return None
    code = str(row.get("Code") or "")
    if not code or not row.get("DiscDate"):
        return None
    code4 = code[:4] if len(code) == 5 and code.endswith("0") else code
    return {"code": code4, "event_date": str(row["DiscDate"])[:10],
            "disc_no": row.get("DiscNo"), "disc_time": row.get("DiscTime"),
            "title": row.get("Title"), "disc_items": row.get("DiscItems"), "tags": tags}


def _load_checkpoint(out_path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not out_path.exists():
        return [], None
    try:
        d = json.loads(out_path.read_text())
        return d.get("records", []), d.get("last_date")
    except (json.JSONDecodeError, OSError):
        return [], None


def fetch_index(date_from: str, date_to: str, *, out_path: Path = OUT_PATH,
                checkpoint_every: int = 100) -> list[dict[str, Any]]:
    """/td/list を日次走査し関係開示を索引化。checkpoint 保存 + last_date から resume。"""
    records, last = _load_checkpoint(out_path)
    start = date.fromisoformat(last) + timedelta(days=1) if last else date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if last:
        print(f"[tdnet_index] resume: {len(records)}件 / {last} の続きから")
    d = start
    scanned = 0
    while d <= end:
        try:
            rows = _jquants.get_list("/td/list", date=d.isoformat())
        except _jquants.JQuantsError:
            rows = []
        for r in rows:
            rec = to_record(r)
            if rec:
                records.append(rec)
        scanned += 1
        if scanned % checkpoint_every == 0:
            atomic_write_json(out_path, {"records": records, "count": len(records),
                                         "last_date": d.isoformat(), "partial": True}, indent=0)
            print(f"  ...{d.isoformat()} 索引{len(records)}件 (checkpoint)")
        d += timedelta(days=1)
    atomic_write_json(out_path, {"records": records, "count": len(records),
                                 "last_date": end.isoformat()}, indent=0)
    print(f"[tdnet_index] 完了 {len(records)}件 ({date_from}〜{date_to})")
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2021-01-01", help="走査開始日")
    ap.add_argument("--until", default="2026-12-31", help="走査終了日")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="出力 JSON パス")
    args = ap.parse_args()
    fetch_index(args.since, args.until, out_path=args.out)


if __name__ == "__main__":
    main()
