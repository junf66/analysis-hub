"""kouaku_mixed パイプライン単一エントリ。

実行順:
  1. (任意) fetch_disclosures: J-Quants /fins/summary 5y を取得 (--refresh-fins)
  2.        extract_mixed_disclosures: 好+悪同居レコードを集約
  3.        enrich_price_kouaku: 価格・分足 enrich (--refresh-prices で再取得)
  4.        analyze_kouaku_edge: レポート生成
  5.        backtest_kouaku: バックテスト生成

各ステップは独立に skip 可能。途中失敗しても次回再開できる。

Usage:
  python -m scripts.update_all                 # 通常運用 (キャッシュ活用)
  python -m scripts.update_all --refresh-fins  # /fins/summary を 5y 再 fetch
  python -m scripts.update_all --refresh-prices # 価格を再 fetch
  python -m scripts.update_all --skip-fetch    # ローカル集計のみ
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], *, allow_fail: bool = False) -> int:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    dt = time.time() - t0
    if proc.returncode != 0:
        if allow_fail:
            print(f"  ! returncode={proc.returncode} (allowed, {dt:.1f}s)")
        else:
            print(f"  ! returncode={proc.returncode} ({dt:.1f}s) — aborting")
            sys.exit(proc.returncode)
    else:
        print(f"  ok ({dt:.1f}s)")
    return proc.returncode


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh-fins", action="store_true", help="/fins/summary を 5y 再 fetch (~20 分)")
    ap.add_argument("--refresh-prices", action="store_true", help="kouaku_records 全件の価格 enrich を再実行")
    ap.add_argument("--skip-fetch", action="store_true", help="リモート fetch を一切しない (キャッシュのみ集計)")
    ap.add_argument("--cost", type=float, default=0.20, help="バックテスト往復コスト %% (既定 0.20)")
    args = ap.parse_args()

    py = sys.executable

    if args.refresh_fins and not args.skip_fetch:
        _run([py, "-m", "scripts.fetch_disclosures", "--skip-buyback"])

    _run([py, "-m", "scripts.extract_mixed_disclosures"])

    # enrich は idempotent (既存 attrs を skip)。デフォルトで毎回走らせて新規レコード分を埋める。
    if not args.skip_fetch:
        enrich_cmd = [py, "-m", "scripts.enrich_price_kouaku", "--sleep", "0.05"]
        if args.refresh_prices:
            enrich_cmd.append("--force")
        _run(enrich_cmd)

    _run([py, "-m", "scripts.analyze_kouaku_edge"])
    _run([py, "-m", "scripts.backtest_kouaku", "--cost", str(args.cost)])

    print("\n=== update_all done ===")
    print(f"  data:    {REPO_ROOT / 'data' / 'kouaku_records.json'}")
    print(f"  reports: {REPO_ROOT / 'reports'}")


if __name__ == "__main__":
    main()
