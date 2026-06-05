"""確定エッジ④ zouhai_kahou_nx の規模別(特に中型)を β=1 TOPIX-demean で再検証する。

正本の残課題(HANDOFF #1 / edge_playbook): ④を規模別に割ると小型が母体(n197/+0.83%)、
中型は +1.29%(n小≈30) と単純tでは強く見えるが、TOPIX β 交絡が未確認で「中型が本物か
相場ベータの逆風/順風か」切り分けできていなかった(⑦と同じ宿題)。

⑦(PO decide)は daily_bars_po で β 実推定したが、④は kouaku の翌寄り→翌引け 1日保有の
ショートで、対象銘柄の daily_bars が手元に無い。ここでは split エッジ等で採用済みの
**β=1 近似(保守側)**で α = 個別(翌O→翌C) − TOPIX(同日 翌O→翌C) を取り、規模別に
raw と α の net 期待値(ショート・方向別コスト)・日付クラスタ頑健 t を並べて、
中型の +1.29% が「相場の同日ベータを除いても残るか」を確認する。

β=1 は個別 β を過大評価しがち(=α を保守的に削る)ため、α が raw とほぼ同等以上に
残れば β 交絡は否定方向。逆に α で大きく消えるなら相場ベータ由来の疑いが強い。
n が小さい(中型≈30)ため確定判断ではなく交絡の一次切り分けに留める。

出力: reports/zouhai_kahou_nx_beta.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from analyzers.stats import clustered_se, t_to_p
from scripts._atomic import atomic_write_text
from scripts._buckets import disc_bucket

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KOUAKU_PATH = REPO_ROOT / "data" / "kouaku_records.json"
TOPIX_PATH = REPO_ROOT / "data" / "edge_candidates" / "topix_daily.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
REPORT_PATH = REPO_ROOT / "reports" / "zouhai_kahou_nx_beta.md"

SUBPATTERN = "zouhai_kahou_nx"
SHORT_COST = 0.15
MIN_CELL_N = 10            # 中型は n≈30 のため緩める(交絡切り分け目的)
SCALE_ORDER = ["大型", "中型", "小型", "不明"]


def _norm_code(code: str) -> str:
    """5桁(末尾0)→4桁正規化 (kouaku の英数字コード 130A0→130A にも対応)。"""
    code = str(code)
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def load_topix_oc(path: Path) -> dict[str, tuple[float, float]]:
    """date → (Open, Close) を topix_daily から構成。"""
    recs = json.loads(path.read_text())["records"]
    return {r["Date"]: (r["O"], r["C"]) for r in recs if r.get("O") and r.get("C")}


def load_scale_band(path: Path) -> dict[str, str]:
    """正規化コード → scale_band(大型/中型/小型) を equities_master から構成。"""
    recs = json.loads(path.read_text())["records"]
    return {_norm_code(r.get("Code")): (r.get("scale_band") or "不明") for r in recs}


def short_cell(obs: list[tuple[str, float]]) -> dict[str, Any] | None:
    """ショート観測列 (date, ret%) の net 期待値・クラスタ頑健 t・勝率を返す。"""
    if len(obs) < MIN_CELL_N:
        return None
    nets = [-v - SHORT_COST for _, v in obs]
    net_ev = statistics.fmean(nets)
    cse = clustered_se(nets, [d for d, _ in obs])
    t = net_ev / cse if cse else 0.0
    win = sum(1 for x in nets if x > 0) * 100.0 / len(nets)
    return {"n": len(nets), "net_ev": net_ev, "t_clust": t, "win": win, "p": t_to_p(t)}


def build_rows(records: list[dict[str, Any]], topix: dict[str, tuple[float, float]],
               scale: dict[str, str], bucket: str | None) -> dict[str, dict[str, list]]:
    """scale_band → {raw:[(date,ret)], alpha:[(date,alpha)]} を構成。"""
    groups: dict[str, dict[str, list]] = defaultdict(lambda: {"raw": [], "alpha": []})
    for r in records:
        if r.get("subpattern") != SUBPATTERN:
            continue
        if bucket and disc_bucket(r) != bucket:
            continue
        a = r.get("attrs") or {}
        if a.get("limit_locked"):
            continue
        ret = a.get("next_day_open_to_close_ret")
        nbd = a.get("next_bar_date")
        d = r.get("event_date")
        if ret is None or not d:
            continue
        band = scale.get(_norm_code(r.get("code")), "不明")
        groups[band]["raw"].append((d, float(ret)))
        oc = topix.get(nbd)
        if oc and oc[0]:
            tret = (oc[1] - oc[0]) / oc[0] * 100.0
            groups[band]["alpha"].append((d, float(ret) - tret))
    return groups


def render(by_bucket: dict[str, dict[str, dict[str, list]]]) -> str:
    """raw vs α(β=1) の規模別ショート net 期待値表を Markdown 化する。"""
    L = ["# 確定エッジ④ zouhai_kahou_nx 規模別 β=1 TOPIX-demean 再検証", "",
         "ショート(翌寄り→翌引け) net=−EV−0.15%。α=個別−TOPIX(同日 翌O→翌C, β=1近似)。",
         "**β=1 は α を保守的に削るので、α が raw と同等以上に残れば β 交絡は否定方向**。",
         "n が小さい(中型≈30)ため交絡の一次切り分け(確定は validate_edges)。", ""]
    for bkt, groups in by_bucket.items():
        L.append(f"## 開示時刻: {bkt}")
        L.append("| 規模 | 種別 | n | net EV% | クラスタt | 勝率% | p |")
        L.append("|---|---|---:|---:|---:|---:|---:|")
        for band in SCALE_ORDER:
            g = groups.get(band)
            if not g:
                continue
            for kind, label in (("raw", "raw"), ("alpha", "α(β=1)")):
                s = short_cell(g[kind])
                if s:
                    L.append(f"| {band} | {label} | {s['n']} | {s['net_ev']:+.2f} | "
                             f"{s['t_clust']:+.2f} | {s['win']:.0f} | {s['p']:.3f} |")
        L.append("")
    L += ["## 読み方",
          "中型の raw net がプラスで、α(β=1) でも符号・大きさが概ね保たれていれば、",
          "『中型の超過リターンは同日 TOPIX ベータの産物ではない』=④中型は β 交絡否定方向。",
          "α で大きく消える/反転するなら相場ベータ由来の疑い。いずれも n 小につき一次判断。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(REPORT_PATH))
    args = ap.parse_args()
    records = json.loads(KOUAKU_PATH.read_text())["records"]
    topix = load_topix_oc(TOPIX_PATH)
    scale = load_scale_band(MASTER_PATH)
    # 確定エッジ④の本体(大引け後)と、規模n確保のための全時刻の2面を出す。
    by_bucket = {
        "大引け後 (確定エッジ④の timing)": build_rows(records, topix, scale, "大引け後"),
        "全時刻 (規模n確保)": build_rows(records, topix, scale, None),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, render(by_bucket))
    print(f"[zouhai_kahou_nx β] → {out}")
    for bkt, groups in by_bucket.items():
        print(f"  [{bkt}]")
        for band in SCALE_ORDER:
            g = groups.get(band)
            if not g:
                continue
            sr, sa = short_cell(g["raw"]), short_cell(g["alpha"])
            if sr:
                a_txt = f"α{sa['net_ev']:+.2f}/t{sa['t_clust']:+.2f}" if sa else "α n<min"
                print(f"    {band:4s} raw net{sr['net_ev']:+.2f}/t{sr['t_clust']:+.2f}/n{sr['n']}  {a_txt}")


if __name__ == "__main__":
    main()
