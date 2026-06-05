"""mild_*.json に「同日 jisha 決定」の規模%(発行済株式数比)を添付する (in-place, idempotent)。

受け手 (stocks-Large-holding-report の kouaku ページ) が、自社株買い×軽い○○イベントの
規模% を確実に表示できるようにする。従来は受け手側で buyback_ratios.json を
(code, event_date == decision_date) で突合していたが、「決算発表日」と「買付決定日」が
別日のケースで紐付かなかった。ここで抽出側が同日決定の ratio を直接埋め込む。

突合キー: mild の event_date(=決算/開示日) を buyback_ratios の
  (code, decision_date or event_date) と突合する。
    - edinet 由来: decision_date(取締役会決議日)を持つ → これと突合
    - tdnet 由来: decision_date 無し、event_date(開示日=決議公表日)を使う
"jisha" を goods に持つ record のみ対象 (= 自社株買い×軽い○○)。ヒットすれば
  attrs.buyback_ratio_pct / buyback_source を入れ、無ければ両方 null を入れる
  (受け手は新フィールドを優先 lookup、null なら従来どおりテキストのみ表示)。

⚠️ extract_mild_good の再実行と違い、本スクリプトは既存 record を読み込んで attrs に
  キーを足すだけなので alpha_d3_ret(確定エッジ⑤が依存)等の既存フィールドを壊さない。

対象: data/edge_candidates/{mild_good,mild_bad,mild_genhai,mild_zouhai}.json (既定全部)。
  jisha goods を持たない record は素通り (no-op)。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_json

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = REPO_ROOT / "data" / "edge_candidates"
BUYBACK_PATH = OUT_DIR / "buyback_ratios.json"
DEFAULT_FILES = ["mild_good", "mild_bad", "mild_genhai", "mild_zouhai"]


def _norm_code(code: Any) -> str:
    """5桁(末尾0)→4桁正規化 (mild / buyback 両側のコード表記差を吸収)。"""
    code = str(code)
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def load_buyback_decision_map(path: Path = BUYBACK_PATH) -> dict[tuple[str, str], tuple[float, str | None]]:
    """(code, 決定日) → (buyback_ratio_pct, source)。決定日=decision_date or event_date(tdnet)。

    同一決定が EDINET 月次報告で複数行に出るため、最初に見つけた ratio を採用 (値は同一)。
    """
    if not path.exists():
        return {}
    recs = json.loads(path.read_text()).get("records", [])
    out: dict[tuple[str, str], tuple[float, str | None]] = {}
    for r in recs:
        ratio = r.get("buyback_ratio_pct")
        if ratio in (None, ""):
            continue
        dd = r.get("decision_date") or r.get("event_date")
        code = r.get("code")
        if not (code and dd):
            continue
        key = (_norm_code(code), dd)
        if key not in out:
            out[key] = (float(ratio), r.get("source"))
    return out


def enrich_record(rec: dict[str, Any], bbmap: dict[tuple[str, str], tuple[float, str | None]]) -> bool:
    """jisha goods を持つ record に buyback_ratio_pct / buyback_source を付与。変更したら True。

    idempotent: 何度実行しても結果は同じ。ヒットしなければ両フィールド null。
    """
    attrs = rec.get("attrs") or {}
    if "jisha" not in (attrs.get("goods") or []):
        return False
    hit = bbmap.get((_norm_code(rec.get("code")), rec.get("event_date")))
    attrs["buyback_ratio_pct"] = hit[0] if hit else None
    attrs["buyback_source"] = hit[1] if hit else None
    rec["attrs"] = attrs
    return True


def enrich_file(path: Path, bbmap: dict[tuple[str, str], tuple[float, str | None]]) -> tuple[int, int]:
    """mild_*.json を in-place で enrich。(対象jisha件数, ratio入った件数) を返す。"""
    doc = json.loads(path.read_text())
    n_jisha = n_hit = 0
    for rec in doc.get("records", []):
        if enrich_record(rec, bbmap):
            n_jisha += 1
            if (rec["attrs"].get("buyback_ratio_pct")) is not None:
                n_hit += 1
    atomic_write_json(path, doc, indent=0)
    return n_jisha, n_hit


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", default=",".join(DEFAULT_FILES), help="対象 mild ファイル名 (カンマ区切り)")
    args = ap.parse_args()
    bbmap = load_buyback_decision_map()
    print(f"[mild_buyback] 決定マップ {len(bbmap)} 件 (buyback_ratios.json)")
    for name in [s.strip() for s in args.files.split(",") if s.strip()]:
        path = OUT_DIR / f"{name}.json"
        if not path.exists():
            print(f"  {name}: (なし・skip)")
            continue
        n_jisha, n_hit = enrich_file(path, bbmap)
        print(f"  {name}: jisha {n_jisha} 件中 ratio 付与 {n_hit} 件 → {path}")


if __name__ == "__main__":
    main()
