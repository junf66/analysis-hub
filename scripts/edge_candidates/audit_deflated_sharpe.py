"""試行回数を織り込んだ Deflated Sharpe / MinBTL でエッジを再監査する。

うちは BH-FDR で多重検定を補正しているが、これは「相対順位」の補正。
PBO論文 (Bailey-Borwein-López de Prado-Zhu) と Deflated Sharpe (Bailey-LdP)
が指摘するのは別物=「何セル叩いた結果の Sharpe か」を Sharpe 自身に
織り込む絶対補正。~45+ ネタ叩いている以上、これも当てる価値がある。

手順:
  1. 探索ユニバース (kouaku subpattern×時刻 / PO stage×type×lending / holdings)
     の全セル(n≥min_n)を「試行」とみなし、試行間 Sharpe のばらつき sr_std と
     試行数 N を測る。
  2. SR0 = 偶然得られる Sharpe 最大値の期待値 (N と sr_std から)。
  3. 確定/登録エッジ各々に DSR = P(真SR>SR0) と MinBTL を出す。
     DSR>0.95 なら試行回数補正後も有意 (FDR の上に重ねる絶対判定)。

出力: reports/deflated_sharpe.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Callable, Iterable

from analyzers.stats import (deflated_sharpe, expected_max_sharpe,
                             min_track_record_length, sharpe_moments)
from scripts._atomic import atomic_write_text
from scripts.validate_edges import (HOLDINGS_PATH, KOUAKU_PATH, PO_PATH,
                                    holdings_observations, kouaku_observations,
                                    new_edges_observations, po_named_observations,
                                    po_observations)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORT_PATH = REPO_ROOT / "reports" / "deflated_sharpe.md"
LONG_COST = 0.20
SHORT_COST = 0.15


def _cell_net_returns(obs: Iterable[dict[str, Any]], min_n: int) -> dict[Any, list[float]]:
    """observations を cell→net 損益列(方向別コスト控除)に畳む。n<min_n は捨てる。"""
    raw: dict[Any, list[float]] = {}
    for o in obs:
        if o.get("ret") is None:
            continue
        raw.setdefault(o["cell"], []).append(float(o["ret"]))
    out: dict[Any, list[float]] = {}
    for cell, rets in raw.items():
        if len(rets) < min_n:
            continue
        short = statistics.fmean(rets) < 0
        cost = SHORT_COST if short else LONG_COST
        out[cell] = [(-r if short else r) - cost for r in rets]
    return out


def _trial_universe(min_n: int) -> tuple[int, float]:
    """探索ユニバース全セルの試行数 N と試行間 Sharpe の標準偏差を返す。"""
    srs: list[float] = []
    sources: list[tuple[Path, Callable]] = [
        (KOUAKU_PATH, kouaku_observations),
        (PO_PATH, po_observations),
        (HOLDINGS_PATH, holdings_observations),
    ]
    for path, adapter in sources:
        if not path.exists():
            continue
        recs = json.loads(path.read_text()).get("records", [])
        for cell, nets in _cell_net_returns(adapter(recs), min_n).items():
            sr, _, _, _ = sharpe_moments(nets)
            srs.append(sr)
    n = len(srs)
    sr_std = statistics.stdev(srs) if n > 1 else 0.0
    return n, sr_std


def _registered_edges(min_n: int) -> dict[Any, list[float]]:
    """確定/登録エッジ (新エッジ事前登録 + PO既知3エッジ監査) の cell→net 列。"""
    out: dict[Any, list[float]] = {}
    for path, adapter in ((KOUAKU_PATH, new_edges_observations),
                          (PO_PATH, po_named_observations)):
        if not path.exists():
            continue
        recs = json.loads(path.read_text()).get("records", [])
        out.update(_cell_net_returns(adapter(recs), min_n))
    return out


def build_report(*, min_n: int) -> str:
    """Deflated Sharpe / MinBTL 監査 md を返す。"""
    n_trials, sr_std = _trial_universe(min_n)
    sr0 = expected_max_sharpe(sr_std, n_trials)
    L = ["# Deflated Sharpe / MinBTL 監査 (試行回数補正)", "",
         f"探索ユニバース試行数 N={n_trials} (kouaku+PO+holdings の n≥{min_n} 全セル) / "
         f"試行間 Sharpe 標準偏差 σ_SR={sr_std:.3f}",
         f"→ 偶然の Sharpe 最大期待値 **SR0={sr0:.3f}** (per-trade)。"
         "登録エッジの per-trade Sharpe がこれを有意に超えるかを DSR で判定。", "",
         "Sharpe は per-trade(年率化しない)。DSR=P(真SR>SR0)、**>0.95 で試行回数補正後も生存**。",
         "MinBTL=SR0 を有意超えするのに要る最小トレード数 (現 n がこれ未満=データ不足)。", "",
         "| 登録エッジ | n | SR(per-trade) | skew | kurt | DSR | MinBTL | 判定 |",
         "|---|--:|--:|--:|--:|--:|--:|:--:|"]
    rows = []
    for cell, nets in _registered_edges(min_n).items():
        sr, skew, kurt, n = sharpe_moments(nets)
        dsr = deflated_sharpe(sr, n, skew, kurt, sr0)
        mbtl = min_track_record_length(sr, skew, kurt, sr0)
        rows.append((dsr, cell, n, sr, skew, kurt, mbtl))
    rows.sort(key=lambda x: -x[0])
    for dsr, cell, n, sr, skew, kurt, mbtl in rows:
        name = " × ".join(str(x) for x in cell) if isinstance(cell, tuple) else str(cell)
        verdict = "✅" if dsr > 0.95 else ("△" if dsr > 0.5 else "✗")
        mbtl_s = f"{mbtl:.0f}" if mbtl is not None else "到達不能"
        nflag = "" if (mbtl is None or n >= mbtl) else " ⚠n不足"
        L.append(f"| {name} | {n} | {sr:+.3f} | {skew:+.2f} | {kurt:.1f} | "
                 f"{dsr:.3f} | {mbtl_s}{nflag} | {verdict} |")
    L += ["",
          "## 読み方",
          "- **✅ DSR>0.95**: ~45ネタ叩いた試行回数を割り引いても Sharpe が有意。FDR の上で更に堅い。",
          "- **△/✗**: 平均αは正でも、試行回数で割り引くとノイズと区別しにくい。実運用は慎重に。",
          "- MinBTL に **⚠n不足** が付くセルは、SR0 超えを統計的に主張するにはサンプルがまだ足りない。",
          "- これは FDR(相対) と独立の絶対補正。両方通るものが最も信頼できる。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-n", type=int, default=30, help="試行/エッジの最小 n (既定 30)")
    ap.add_argument("--out", type=Path, default=REPORT_PATH, help="出力 md")
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, build_report(min_n=args.min_n))
    print(f"[deflated_sharpe] → {args.out}")


if __name__ == "__main__":
    main()
