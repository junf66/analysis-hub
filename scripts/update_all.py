"""kouaku_mixed + PO パイプライン単一エントリ。

--source で対象ソースを切り替え (既定 all = kouaku + po)。

kouaku 実行順:
  1. (任意) fetch_disclosures: J-Quants /fins/summary + yanoshin TDnet を取得
                              (--refresh-fins / --refresh-tdnet)
  2.        extract_mixed_disclosures: 好+悪同居レコードを集約
  3.        enrich_price_kouaku: 価格・分足 enrich (--refresh-prices で再取得)
  4.        analyze_kouaku_edge: レポート生成
  5.        backtest_kouaku: バックテスト生成
  6.        export_kouaku_site: サイト表示用 slim JSON を書き出し

PO 実行順:
  1. (任意) fetchers.po: po-tracker から raw JSON を取得 (--refresh-po-raw)
  2.        extract_po: 1 PO → 最大 3 events に展開
  3.        analyze_po_edge: レポート生成
  4.        backtest_po: バックテスト生成

holdings 実行順:
  1.        fetchers.holdings: holdings-tracker raw を取得 (--skip-fetch で省略)
  2.        extract_holdings: 共通スキーマ events に展開 (価格 enrich 済)
  3.        analyze_holdings_edge: レポート生成
  4.        backtest_holdings: バックテスト生成

各ステップは独立に skip 可能。途中失敗しても次回再開できる。

Usage:
  python -m scripts.update_all                       # 全ソース (キャッシュ活用)
  python -m scripts.update_all --source kouaku      # kouaku のみ
  python -m scripts.update_all --source po          # PO のみ
  python -m scripts.update_all --source holdings    # 大量保有のみ
  python -m scripts.update_all --refresh-fins       # /fins/summary を 5y 再 fetch
  python -m scripts.update_all --refresh-tdnet      # yanoshin TDnet を 5y 再 fetch
  python -m scripts.update_all --refresh-prices     # 価格を再 fetch
  python -m scripts.update_all --refresh-po-raw     # po-tracker raw を再取得
  python -m scripts.update_all --skip-fetch         # ローカル集計のみ
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


def _run_kouaku(py: str, args: argparse.Namespace) -> None:
    if args.refresh_fins and not args.skip_fetch:
        _run([py, "-m", "scripts.fetch_disclosures", "--skip-buyback", "--skip-tdnet"])

    if args.refresh_tdnet and not args.skip_fetch:
        _run([py, "-m", "scripts.fetch_disclosures", "--skip-buyback", "--skip-fins"])

    _run([py, "-m", "scripts.extract_mixed_disclosures"])

    # enrich は idempotent (既存 attrs を skip)。デフォルトで毎回走らせて新規レコード分を埋める。
    if not args.skip_fetch:
        enrich_cmd = [py, "-m", "scripts.enrich_price_kouaku", "--sleep", "0.05"]
        if args.refresh_prices:
            enrich_cmd.append("--force")
        _run(enrich_cmd)

    _run([py, "-m", "scripts.analyze_kouaku_edge"])
    _run([py, "-m", "scripts.backtest_kouaku", "--cost", str(args.cost)])
    # サイト表示用 slim JSON (data/kouaku_site.json) を書き出し
    _run([py, "-m", "scripts.export_kouaku_site", "--cost", str(args.cost)])

    # 好悪同日材料ページ用 mild_* 補完 (軽い○○×反対材料)。fins/td_bulk 由来。
    # ⚠️ mild_good は alpha_d3_ret(確定エッジ⑤が依存)を持つため update_all では再生成しない
    #    (extract_mild_good は最小スキーマで上書きし alpha を失うため手動運用のまま)。新3ケースのみ。
    if not args.skip_fetch:
        _run([py, "-m", "scripts.edge_candidates.extract_mild_cases"], allow_fail=True)


def _run_po(py: str, args: argparse.Namespace) -> None:
    if args.refresh_po_raw and not args.skip_fetch:
        # po-tracker からの取得 (cache/po/ にダウンロード)
        _run([py, "-m", "fetchers.po"])

    _run([py, "-m", "scripts.extract_po"])
    _run([py, "-m", "scripts.analyze_po_edge"])
    _run([py, "-m", "scripts.backtest_po", "--cost", str(args.cost)])


def _run_holdings(py: str, args: argparse.Namespace) -> None:
    # holdings raw は keyless な公開 JSON のダウンロード。skip-fetch でなければ毎回取得。
    # (取得しないとローカル extract が空 cache を踏むため、安全側で fetch する)
    if not args.skip_fetch:
        _run([py, "-m", "fetchers.holdings"])

    _run([py, "-m", "scripts.extract_holdings"])
    _run([py, "-m", "scripts.analyze_holdings_edge"])
    _run([py, "-m", "scripts.backtest_holdings", "--cost", str(args.cost)])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["all", "kouaku", "po", "holdings"], default="all",
                    help="どのソースを処理するか (既定 all = kouaku + po + holdings)")
    ap.add_argument("--refresh-fins", action="store_true", help="/fins/summary を 5y 再 fetch (~20 分)")
    ap.add_argument("--refresh-tdnet", action="store_true", help="yanoshin TDnet 全タイトルを 5y 再 fetch (~10 分)")
    ap.add_argument("--refresh-prices", action="store_true", help="kouaku_records 全件の価格 enrich を再実行")
    ap.add_argument("--refresh-po-raw", action="store_true", help="po-tracker raw を再取得")
    ap.add_argument("--skip-fetch", action="store_true", help="リモート fetch を一切しない (キャッシュのみ集計)")
    ap.add_argument("--cost", type=float, default=0.20, help="バックテスト往復コスト %% (既定 0.20)")
    args = ap.parse_args()

    py = sys.executable

    if args.source in ("all", "kouaku"):
        print("\n### kouaku パイプライン ###")
        _run_kouaku(py, args)

    if args.source in ("all", "po"):
        print("\n### PO パイプライン ###")
        _run_po(py, args)

    if args.source in ("all", "holdings"):
        print("\n### holdings パイプライン ###")
        _run_holdings(py, args)

    # 全ソース横断のエッジ検証 (FDR + walk-forward)。欠損ソースは自動 skip。
    # validate_edges は方向別コスト (--long-cost / --short-cost) を採り、
    # backtest_* の単一往復 --cost とは引数体系が異なる。ここでは canonical な
    # 方向別デフォルト (short 0.15% / long 0.20%) をそのまま使わせる
    # (以前 --cost を渡して returncode=2 で常に失敗していたバグを修正)。
    print("\n### エッジ検証 (過剰最適化ガード) ###")
    _run([py, "-m", "scripts.validate_edges"], allow_fail=True)

    print("\n=== update_all done ===")
    print(f"  kouaku data:   {REPO_ROOT / 'data' / 'kouaku_records.json'}")
    print(f"  po data:       {REPO_ROOT / 'data' / 'po_records.json'}")
    print(f"  holdings data: {REPO_ROOT / 'data' / 'holdings_records.json'}")
    print(f"  reports:       {REPO_ROOT / 'reports'}")


if __name__ == "__main__":
    main()
