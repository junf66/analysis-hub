"""⑤ zouhai_genshu (増配＋軽い当期減益) の +3営業日リターン付与。

kouaku_records には翌営業日(寄→引)しか無いため、⑤の確定エグジット
「翌営業日寄り売り → +3営業日後引け 買戻」用に +3日リターンを別途付与する。
scale_band (equities_master) も結合し、規模別検証を可能にする。

出力: data/edge_candidates/zouhai_genshu_d3.json
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scripts import _jquants
from scripts._atomic import atomic_write_json
from scripts.edge_candidates import topix_adjust
from scripts.edge_candidates.enrich_common import returns_from_bars

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KOUAKU_PATH = REPO_ROOT / "data" / "kouaku_records.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "zouhai_genshu_d3.json"
SUBPATTERN = "zouhai_genshu"
DAYS = [1, 3, 5]


def _to5(code: str) -> str:
    """4桁 code を equities_master の5桁形式へ。"""
    return code + "0" if len(code) == 4 else code


def select_events(kouaku: list[dict[str, Any]], master: dict[str, dict]) -> list[dict[str, Any]]:
    """zouhai_genshu を共通スキーマ event へ展開 (scale_band 結合, 価格未付与)。"""
    out: list[dict[str, Any]] = []
    for r in kouaku:
        if r.get("subpattern") != SUBPATTERN:
            continue
        code = r.get("code")
        ed = r.get("event_date")
        if not code or not ed:
            continue
        m = master.get(_to5(code)) or {}
        out.append({
            "code": code,
            "event_date": ed,
            "event_type": "zouhai_genshu",
            "source": "kouaku_records",
            "attrs": {
                "scale_band": m.get("scale_band"),
                "scale_cat": m.get("ScaleCat"),
                "mrgn": m.get("MrgnNm"),
                "limit_locked": (r.get("attrs") or {}).get("limit_locked"),
            },
        })
    return out


def enrich(events: list[dict[str, Any]], *, out_path: Path = OUT_PATH,
           checkpoint_every: int = 50) -> None:
    """各 event に翌寄り→+N日引けの素リターン + TOPIX-α を付与し保存。"""
    for i, rec in enumerate(events, 1):
        a = rec["attrs"]
        code, ed = rec["code"], rec["event_date"]
        code5 = _to5(code)
        ev = date.fromisoformat(ed)
        try:
            bars = _jquants.get_list("/equities/bars/daily", code=code5,
                                     **{"from": (ev - timedelta(days=15)).isoformat(),
                                        "to": (ev + timedelta(days=20)).isoformat()})
        except _jquants.JQuantsError as e:
            a["price_error"] = str(e)
            bars = []
        if bars:
            a.update(returns_from_bars(bars, ed, DAYS, skip_bars=0))
        if i % checkpoint_every == 0:
            atomic_write_json(out_path, {"records": events, "count": len(events),
                                         "partial": True}, indent=0)
            print(f"  ...{i}/{len(events)}")
    topix_adjust.enrich_with_alpha(events, DAYS)
    atomic_write_json(out_path, {"records": events, "count": len(events)}, indent=0)
    print(f"wrote {out_path} ({len(events)}件)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kouaku", type=Path, default=KOUAKU_PATH)
    ap.add_argument("--master", type=Path, default=MASTER_PATH)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    kouaku = json.loads(args.kouaku.read_text()).get("records", [])
    master = {m["Code"]: m for m in json.loads(args.master.read_text())["records"]}
    events = select_events(kouaku, master)
    print(f"[zouhai_genshu_d3] {len(events)}件 enrich開始")
    enrich(events, out_path=args.out)


if __name__ == "__main__":
    main()
