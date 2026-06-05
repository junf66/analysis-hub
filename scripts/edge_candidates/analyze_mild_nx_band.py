"""来期予想 magnitude の中立帯(mild_kahou_nx / mild_kouhou_nx)を炙り出す。

【背景の訂正 (2026-06)】CLAUDE.md / HANDOFF は「来期予想NP(NxFNp)は /fins/summary に
無く /fins/details は403 → mild_kahou_nx/mild_kouhou_nx は復元不能」と記していたが誤り。
**/fins/summary は NxFNp(来期予想当期純利益)を持ち、extract_mixed_disclosures は既に
これを使って kahou_nx(<=-10%)/kouhou_nx(>=+10%) を分類している**。中間帯
(-10% < nx_delta < +10%)だけが閾値で捨てられており、これが #4 の死角。

本スクリプトは fins_summary の全 FY 決算行について
  nx_delta = (NxFNp - NP) / |NP| * 100   (来期予想 vs 今期実績の増減率)
を計算し、以下のバンドに割って分布を出す。中間帯を mild として明示し、
価格 enrich 用にイベント一覧(code, DiscDate, DiscTime, nx_delta, band)を JSON 出力する。
EV/FDR 検証は、この一覧を kouaku と同じ翌寄り→翌引け timing で price enrich してから
evaluate_cells に通す(別ステップ・要 J-Quants daily bars)。

バンド定義 (NP_YOY_BAD_THRESHOLD_PCT=-10 と整合):
  kahou_nx       : nx_delta <= -10        来期 大幅減益見通し (既存・確定エッジ④の核)
  mild_kahou_nx  : -10 < nx_delta < 0     来期 軽い減益見通し  (死角=本タスク)
  mild_kouhou_nx : 0 <= nx_delta < +10    来期 軽い増益見通し  (死角=本タスク)
  kouhou_nx      : nx_delta >= +10        来期 大幅増益見通し (既存)

出力: reports/mild_nx_band.md, data/edge_candidates/mild_nx_events.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from analyzers.stats import clustered_se, t_to_p
from scripts._atomic import atomic_write_json, atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KOUAKU_PATH = REPO_ROOT / "data" / "kouaku_records.json"
LONG_COST, SHORT_COST = 0.20, 0.15
MIN_CELL_N = 30
# kouaku パイプラインの fins キャッシュ(by_date・全フィールド=NxFNp 在り)を使う。
# ※ data/edge_candidates/fins_summary.json は KEEP 列で slim 化され NxFNp を落とすため不可。
FINS_PATH = REPO_ROOT / "cache" / "disclosures" / "fins_summary.json"
REPORT_PATH = REPO_ROOT / "reports" / "mild_nx_band.md"
EVENTS_PATH = REPO_ROOT / "data" / "edge_candidates" / "mild_nx_events.json"

EXTREME = 10.0   # NP_YOY_BAD_THRESHOLD_PCT の絶対値と整合


def _f(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def band_of(nx_delta: float) -> str:
    """nx_delta(%) を来期 magnitude バンド名に割る。"""
    if nx_delta <= -EXTREME:
        return "kahou_nx"
    if nx_delta < 0:
        return "mild_kahou_nx"
    if nx_delta < EXTREME:
        return "mild_kouhou_nx"
    return "kouhou_nx"


def _norm_code(code: str) -> str:
    """5桁(末尾0)→4桁に正規化 (kouaku_records と突合できる表記に揃える)。"""
    code = str(code)
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def build_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """FY 決算行(NP/NxFNp/DiscDate 揃い)から (NxFNp vs NP) を計算しバンド付与。"""
    out: list[dict[str, Any]] = []
    for r in rows:
        if (r.get("CurPerType") or "") != "FY":
            continue
        np_, nx = _f(r.get("NP")), _f(r.get("NxFNp"))
        dd = r.get("DiscDate")
        if np_ is None or nx is None or np_ == 0 or not dd:
            continue
        nx_delta = (nx - np_) / abs(np_) * 100.0
        out.append({
            "code": _norm_code(r.get("Code") or r.get("code")),
            "event_date": dd,
            "disc_time": r.get("DiscTime"),
            "nx_delta": round(nx_delta, 2),
            "band": band_of(nx_delta),
        })
    out.sort(key=lambda e: (e["event_date"], e["code"]))
    return out


def load_fins_rows(path: Path) -> list[dict[str, Any]]:
    """fins キャッシュを行リストに平坦化 (by_date / by_code / records 各形に対応)。"""
    d = json.loads(path.read_text())
    if isinstance(d, dict) and "by_date" in d:
        return [r for rows in d["by_date"].values() for r in rows]
    if isinstance(d, dict) and "by_code" in d:
        return [r for rows in d["by_code"].values() for r in rows]
    return d.get("records", []) if isinstance(d, dict) else d


def load_kouaku_index(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """(code, event_date) → {ret, has_zouhai} を kouaku_records から構成 (price 済の母数)。

    mild_nx イベントを既存の翌寄り→翌引けリターンに結合するためのインデックス。
    増配(zouhai) hint 有無も持ち、確定エッジ④の mild 帯延伸を検証できるようにする。
    """
    idx: dict[tuple[str, str], dict[str, Any]] = {}
    for r in json.loads(path.read_text())["records"]:
        a = r.get("attrs") or {}
        if a.get("limit_locked"):
            continue
        v = a.get("next_day_open_to_close_ret")
        if v is None:
            continue
        hints = {f.get("subpattern_hint") for f in (r.get("good_factors") or [])}
        idx[(_norm_code(r.get("code")), r.get("event_date"))] = {
            "ret": float(v), "has_zouhai": "zouhai" in hints,
        }
    return idx


def _cell(obs: list[tuple[str, float]]) -> dict[str, Any] | None:
    """(date, ret) 観測列から有利方向の net EV / クラスタt / 勝率を返す。"""
    if len(obs) < MIN_CELL_N:
        return None
    rets = [v for _, v in obs]
    direction = "long" if statistics.fmean(rets) >= 0 else "short"
    cost = LONG_COST if direction == "long" else SHORT_COST
    nets = [(v - cost) if direction == "long" else (-v - cost) for v in rets]
    net_ev = statistics.fmean(nets)
    cse = clustered_se(nets, [d for d, _ in obs])
    t = net_ev / cse if cse else 0.0
    win = sum(1 for x in nets if x > 0) * 100.0 / len(nets)
    return {"n": len(nets), "dir": direction, "net_ev": net_ev, "t_clust": t,
            "win": win, "p": t_to_p(t)}


def ev_by_band(events: list[dict[str, Any]],
               idx: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    """各バンドを kouaku 価格に結合し net EV を出す (全体 / 増配サブセット)。"""
    by_band: dict[str, list[tuple[str, float]]] = defaultdict(list)
    by_band_zouhai: dict[str, list[tuple[str, float]]] = defaultdict(list)
    matched = 0
    for e in events:
        hit = idx.get((e["code"], e["event_date"]))
        if not hit:
            continue
        matched += 1
        by_band[e["band"]].append((e["event_date"], hit["ret"]))
        if hit["has_zouhai"]:
            by_band_zouhai[e["band"]].append((e["event_date"], hit["ret"]))
    cells = {b: _cell(obs) for b, obs in by_band.items()}
    cells_z = {b: _cell(obs) for b, obs in by_band_zouhai.items()}
    return {"matched": matched, "total": len(events), "all": cells, "zouhai": cells_z}


def render(events: list[dict[str, Any]], ev: dict[str, Any] | None = None) -> str:
    """バンド分布(全体・中立帯の占有率)を Markdown にする。"""
    bands = Counter(e["band"] for e in events)
    total = len(events)
    if total == 0:
        return "# 来期予想 magnitude 中立帯 (mild_nx) 分布\n\nFY決算行(NxFNp 在り)が見つからない。fins キャッシュを確認。\n"
    mild = bands["mild_kahou_nx"] + bands["mild_kouhou_nx"]
    lines = ["# 来期予想 magnitude 中立帯 (mild_nx) 分布", "",
             f"FY決算 {total} 件 (NxFNp と NP が揃う行)。",
             f"**従来捨てられていた中立帯 mild_nx = {mild} 件 ({mild/total*100:.1f}%)** "
             "= #4 の死角(=価格検証の母数)。", "",
             "| バンド | 定義(来期予想 vs 今期NP) | 件数 | 占有% |",
             "|---|---|---:|---:|"]
    defs = {
        "kahou_nx": "≤ -10% (大幅減益見通し・既存④核)",
        "mild_kahou_nx": "-10〜0% (軽い減益見通し・死角)",
        "mild_kouhou_nx": "0〜+10% (軽い増益見通し・死角)",
        "kouhou_nx": "≥ +10% (大幅増益見通し・既存)",
    }
    for b in ("kahou_nx", "mild_kahou_nx", "mild_kouhou_nx", "kouhou_nx"):
        n = bands[b]
        lines.append(f"| {b} | {defs[b]} | {n} | {n/total*100:.1f} |")
    if ev:
        lines += ["", "## EV (kouaku 価格に結合・翌寄り→翌引け / net 方向別コスト)",
                  f"結合できたのは {ev['matched']}/{ev['total']} 件 "
                  "(同日に他の kouaku 材料があり price 済の分のみ=選択バイアス注意)。", "",
                  "### 全体 (バンド別)",
                  "| バンド | n | 方向 | net EV% | クラスタt | 勝率% | p |",
                  "|---|---:|---|---:|---:|---:|---:|"]
        for b in ("kahou_nx", "mild_kahou_nx", "mild_kouhou_nx", "kouhou_nx"):
            s = ev["all"].get(b)
            if s:
                lines.append(f"| {b} | {s['n']} | {s['dir']} | {s['net_ev']:+.2f} | "
                             f"{s['t_clust']:+.2f} | {s['win']:.0f} | {s['p']:.3f} |")
        lines += ["", "### 増配(zouhai)サブセット = 確定エッジ④の mild 帯延伸チェック",
                  "| バンド | n | 方向 | net EV% | クラスタt | 勝率% | p |",
                  "|---|---:|---|---:|---:|---:|---:|"]
        for b in ("kahou_nx", "mild_kahou_nx", "mild_kouhou_nx", "kouhou_nx"):
            s = ev["zouhai"].get(b)
            if s:
                lines.append(f"| {b} | {s['n']} | {s['dir']} | {s['net_ev']:+.2f} | "
                             f"{s['t_clust']:+.2f} | {s['win']:.0f} | {s['p']:.3f} |")
        lines += ["", "※ これは既存 price との結合による一次検証(単一バンドの |t|)。",
                  "確定判断は validate_edges への事前登録(独立FDR + walk-forward OOS)が必要。"]
    else:
        lines += ["", "## 次ステップ (EV/FDR 検証)",
                  "`mild_nx_events.json` を kouaku と同じ翌寄り→翌引け timing で price enrich し、",
                  "band × 開示時刻で evaluate_cells(方向別コスト+クラスタt+FDR+OOS)に通す。"]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fins", default=str(FINS_PATH))
    args = ap.parse_args()
    events = build_events(load_fins_rows(Path(args.fins)))
    atomic_write_json(EVENTS_PATH, {"records": events, "count": len(events)}, indent=0)
    ev = ev_by_band(events, load_kouaku_index(KOUAKU_PATH)) if KOUAKU_PATH.exists() else None
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(REPORT_PATH, render(events, ev))
    bands = Counter(e["band"] for e in events)
    print(f"[mild_nx] FY決算 {len(events)} 件 / バンド {dict(bands)}")
    print(f"  → {EVENTS_PATH}\n  → {REPORT_PATH}")


if __name__ == "__main__":
    main()
