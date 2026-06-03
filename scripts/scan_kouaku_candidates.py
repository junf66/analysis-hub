"""好悪材料(kouaku)の全次元を自動総当たりして期待値プラスの候補を抽出する。

PO版 scan_po_candidates の kouaku 版。(subpattern × 開示時刻 × 程度 × 規模 × 業種 × 信用)
の単一＋2軸掛け合わせセルを evaluate_cells に乗せ、方向別コスト＋日付クラスタ頑健t
＋walk-forward OOS＋全セル横断 BH-FDR で評価し、候補を一覧化する。

メトリクス: 翌寄り→翌引け (next_day_open_to_close_ret)。limit_locked 除外。
方向(long/short)は生EV符号で自動判定。検算として既知④⑤(zouhai_kahou_nx×大引け後 等)を
自動再発見できるはず。

出力: reports/kouaku_candidate_scan.md
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, Callable

from analyzers.stats import evaluate_cells
from scripts._buckets import disc_bucket

REPO_ROOT = Path(__file__).resolve().parent.parent
KOUAKU_PATH = REPO_ROOT / "data" / "kouaku_records.json"
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
REPORT_PATH = REPO_ROOT / "reports" / "kouaku_candidate_scan.md"

LONG_COST = 0.20
SHORT_COST = 0.15
MIN_N = 30
TC_CANDIDATE = 1.5


def _to5(code: str) -> str:
    return code + "0" if len(code) == 4 else code


def primary_mag(r: dict[str, Any]) -> float | None:
    """レコードの主たる程度(最初の pct metric)を返す。"""
    for fac in (r.get("bad_factors") or []) + (r.get("good_factors") or []):
        for k, v in (fac.get("metric") or {}).items():
            if isinstance(v, (int, float)) and "pct" in k.lower():
                return float(v)
    return None


def mag_bucket(r: dict[str, Any]) -> str | None:
    """程度を符号付き粗バンドに割り当てる。pct metric が無ければ None。"""
    m = primary_mag(r)
    if m is None:
        return None
    for lo, hi, lab in [(-1e9, -30, "程度:深(≤-30%)"), (-30, -10, "程度:中(-30〜-10%)"),
                        (-10, 0, "程度:浅(-10〜0%)"), (0, 10, "程度:小(0〜10%)"),
                        (10, 1e9, "程度:大(≥10%)")]:
        if lo <= m < hi:
            return lab
    return None


def load_kouaku() -> list[dict[str, Any]]:
    """kouaku records を返す。"""
    data = json.loads(KOUAKU_PATH.read_text())
    return data.get("records", []) if isinstance(data, dict) else data


def load_master() -> dict[str, dict[str, Any]]:
    """code5 → equities_master レコード。"""
    if not MASTER_PATH.exists():
        return {}
    return {m["Code"]: m for m in json.loads(MASTER_PATH.read_text()).get("records", [])}


# 分析軸: record(+master) → ラベル (None で除外)
AXES: dict[str, Callable[[dict[str, Any], dict[str, Any]], str | None]] = {
    "型": lambda r, m: f"型:{r.get('subpattern')}" if r.get("subpattern") else None,
    "開示時刻": lambda r, m: f"時刻:{disc_bucket(r)}",
    "程度": lambda r, m: mag_bucket(r),
    "規模": lambda r, m: f"規模:{m.get('scale_band')}" if m.get("scale_band") else None,
    "業種": lambda r, m: f"業種:{m.get('S17Nm')}" if m.get("S17Nm") else None,
    "信用": lambda r, m: f"信用:{m.get('MrgnNm')}" if m.get("MrgnNm") else None,
}


def _eligible(r: dict[str, Any]) -> float | None:
    """limit_locked でなく翌寄→翌引けがあるレコードの ret(%)。無ければ None。"""
    a = r.get("attrs") or {}
    if a.get("limit_locked"):
        return None
    ret = a.get("next_day_open_to_close_ret")
    return float(ret) if ret is not None else None


def day_means(records: list[dict[str, Any]]) -> dict[str, float]:
    """日付ごとの『好悪ユニバース平均リターン』。クロスセクション demean 用。"""
    import statistics
    by_date: dict[str, list[float]] = {}
    for r in records:
        ret = _eligible(r)
        if ret is None:
            continue
        by_date.setdefault(r.get("event_date") or "", []).append(ret)
    return {d: statistics.fmean(v) for d, v in by_date.items() if v}


def build_observations(records: list[dict[str, Any]], master: dict[str, dict[str, Any]],
                       max_combo: int = 2, demean: bool = False) -> list[dict[str, Any]]:
    """(軸の組合せ) を cell とする観測リストを作る。limit_locked 除外。

    demean=True で各観測から『その日の好悪ユニバース平均』を引く(日次クロスセクション中立化)。
    = 相場/基線ドリフトを除いた cell 固有のαで評価でき、相関スライスの水増しを排除する。
    """
    dm = day_means(records) if demean else {}
    obs: list[dict[str, Any]] = []
    for r in records:
        ret = _eligible(r)
        if ret is None:
            continue
        date = r.get("event_date")
        if demean:
            ret = ret - dm.get(date or "", 0.0)
        m = master.get(_to5(r.get("code", ""))) or {}
        code = r.get("code")
        active = [fn(r, m) for fn in AXES.values()]
        active = [lab for lab in active if lab is not None]
        obs.append({"cell": ("全体",), "ret": ret, "date": date, "code": code})
        for k in range(1, max_combo + 1):
            for combo in itertools.combinations(active, k):
                obs.append({"cell": combo, "ret": ret, "date": date, "code": code})
    return obs


def scan(records: list[dict[str, Any]], master: dict[str, dict[str, Any]],
         demean: bool = False) -> list[dict[str, Any]]:
    """全セルを評価し候補(ev_net>0 かつ t_clustered≥下限)を返す。demean=基線超過評価。"""
    obs = build_observations(records, master, demean=demean)
    results = evaluate_cells(obs, long_cost=LONG_COST, short_cost=SHORT_COST, min_n=MIN_N)
    cands = [r for r in results if r["ev_net"] > 0 and r["t_clustered"] >= TC_CANDIDATE]
    cands.sort(key=lambda r: r["t_clustered"], reverse=True)
    return cands


def build_report(records: list[dict[str, Any]], master: dict[str, dict[str, Any]]) -> str:
    """kouaku 候補スキャン結果レポートを生成。"""
    L: list[str] = []
    L.append("# 好悪材料(kouaku)候補スキャン ── 全次元 自動総当たり (2026-06-03)")
    L.append("")
    L.append(f"分析軸 {len(AXES)}種 (型×開示時刻×程度×規模×業種×信用) の単一＋2軸掛け合わせを機械評価。")
    L.append(f"メトリクス=翌寄→翌引け / 方向自動 / コスト long{LONG_COST}%・short{SHORT_COST}% / "
             f"セル最小n={MIN_N} / クラスタt / walk-forward OOS / 全セルBH-FDR。")
    L.append(f"**候補条件**: net EV>0 かつ t_clustered≥{TC_CANDIDATE}。")
    L.append("")
    import statistics
    base = statistics.fmean([_eligible(r) for r in records if _eligible(r) is not None])
    L.append(f"⚠️ **基線注意**: 好悪材料の翌寄→引けは平均{base:+.2f}%（=何でもショートで+{-base:.2f}%出る下方ドリフト）。")
    L.append("生スキャンの FDR★ の多くはこの基線を大集合に当てた相関スライス。下表は**日次クロスセクションで")
    L.append("demean（その日の好悪平均を控除）した『基線超過α』での評価**＝相場/基線を中立化した真の候補。")
    L.append("")

    raw = scan(records, master, demean=False)
    dem = scan(records, master, demean=True)
    raw_fdr = sum(1 for r in raw if r.get("fdr_significant"))
    dem_fdr = sum(1 for r in dem if r.get("fdr_significant"))
    L.append(f"- 生スキャン: 候補{len(raw)}件 / FDR★{raw_fdr}件（基線込み・水増しあり）")
    L.append(f"- **基線超過(demean)スキャン: 候補{len(dem)}件 / FDR★{dem_fdr}件**（これが実体）")
    L.append("")

    top = dem[:30]
    L.append(f"## 基線超過α 候補（demean後・上位{len(top)} / t_clust降順）")
    L.append("")
    L.append("| 条件（軸の掛け合わせ） | 方向 | n | 超過α net | t_clust | OOS test | FDR★ |")
    L.append("|---|---|---|---|---|---|---|")
    for r in top:
        cell_disp = " & ".join(r["cell"])
        oos = r.get("test_ev_net")
        oos_disp = f"{oos:+.2f}%" if oos is not None else "—"
        fdr = "★" if r.get("fdr_significant") else ""
        L.append(f"| {cell_disp} | {r['direction']} | {r['n']} | {r['ev_net']:+.2f}% | "
                 f"{r['t_clustered']:+.2f} | {oos_disp} | {fdr} |")
    L.append("")
    L.append("## 読み方")
    L.append("")
    L.append("- **超過α net**: その日の好悪平均を引いた後の net。基線(何でもショート)を超える固有の効き。")
    L.append("- demean で生き残る＝『相場/基線で説明できない本物の候補』。生スキャンで★でも demean で消えるものは基線の水増し。")
    L.append("- 確定採用は validate_edges 事前登録(独立FDR+OOS)で。本スキャンは候補出しの一次フィルタ。")
    return "\n".join(L)


if __name__ == "__main__":
    records = load_kouaku()
    master = load_master()
    REPORT_PATH.write_text(build_report(records, master))
    print(f"wrote {REPORT_PATH}")
