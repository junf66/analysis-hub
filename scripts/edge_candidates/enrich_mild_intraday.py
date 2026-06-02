"""mild_good イベントに翌営業日の分足日中リターン(寄→9:10/9:15/9:30/10:00/11:30)を付与する。

15:30(引け)は d0_ret(寄→引)で既出。分足は 2024-05-21 以降のみ存在するため、それ以降の
entry_date のみ付与される(n は減る)。出口時刻別(①long/②short)検証の入力。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PATH = REPO_ROOT / "data" / "edge_candidates" / "mild_good.json"
TARGETS = [("09:10", "r_910"), ("09:15", "r_915"), ("09:30", "r_930"),
           ("10:00", "r_1000"), ("11:30", "r_1130")]
MINUTE_START = "2024-05-21"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=PATH)
    args = ap.parse_args()
    recs = json.loads(args.path.read_text())["records"]
    todo = [r for r in recs if (r.get("attrs") or {}).get("entry_date", "") >= MINUTE_START]
    print(f"[mild_intraday] 分足対象(2024-05以降) {len(todo)}/{len(recs)}件")
    done = 0
    for i, r in enumerate(todo, 1):
        a = r["attrs"]
        code = r["code"]
        code5 = code + "0" if len(code) == 4 else code
        try:
            mb = _jquants.get_list("/equities/bars/minute", code=code5,
                                   date=a["entry_date"].replace("-", ""))
        except _jquants.JQuantsError:
            continue
        mb = sorted([b for b in mb if b.get("C") is not None], key=lambda b: b.get("Time") or "")
        if not mb:
            continue
        o = mb[0].get("O") or mb[0].get("C")
        if not o:
            continue
        for t, key in TARGETS:
            b = next((x for x in mb if (x.get("Time") or "") >= t), None)
            if b and b.get("C"):
                a[key] = (b["C"] / o - 1.0) * 100.0
        done += 1
        if i % 200 == 0:
            atomic_write_json(args.path, {"records": recs, "count": len(recs), "partial": True}, indent=0)
            print(f"  ...{i}/{len(todo)}")
    atomic_write_json(args.path, {"records": recs, "count": len(recs)}, indent=0)
    print(f"[mild_intraday] 分足付与 {done}件")


if __name__ == "__main__":
    main()
