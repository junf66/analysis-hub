"""全上場銘柄の財務情報サマリ (/fins/summary) を取得して保存する。

公式 kouaku 再構築の magnitude 源 (決算 NP/OP/OdP/Sales YoY・配当)。
equities_master の全コードを走査し checkpoint+resume で蓄積。
出力: data/edge_candidates/fins_summary.json (code → summary行リスト)。大型のため gitignore。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import _jquants
from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
OUT_PATH = REPO_ROOT / "data" / "edge_candidates" / "fins_summary.json"
# 再構築に使う列だけ残してサイズ削減
KEEP = ("DiscDate", "DiscTime", "DiscNo", "DocType", "CurPerType", "CurPerEn",
        "NxtFYSt", "NxtFYEn", "Sales", "OP", "OdP", "NP", "EPS", "DivFY")


def _load(out_path: Path) -> dict[str, list]:
    if out_path.exists():
        try:
            return json.loads(out_path.read_text())["by_code"]
        except (json.JSONDecodeError, OSError, KeyError):
            return {}
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()
    codes = sorted({r["Code"] for r in json.loads(MASTER_PATH.read_text())["records"]})
    by_code = _load(args.out)
    todo = [c for c in codes if c not in by_code]
    print(f"[fins_summary] 全{len(codes)}銘柄 / 残{len(todo)} 取得開始")
    for i, code in enumerate(todo, 1):
        try:
            rows = _jquants.get_list("/fins/summary", code=code)
            by_code[code] = [{k: r.get(k) for k in KEEP} for r in rows]
        except _jquants.JQuantsError:
            by_code[code] = []
        if i % 300 == 0:
            atomic_write_json(args.out, {"by_code": by_code, "count": len(by_code), "partial": True}, indent=0)
            print(f"  ...{i}/{len(todo)} (累計{len(by_code)})")
    atomic_write_json(args.out, {"by_code": by_code, "count": len(by_code)}, indent=0)
    print(f"wrote {args.out} ({len(by_code)}銘柄)")


if __name__ == "__main__":
    main()
