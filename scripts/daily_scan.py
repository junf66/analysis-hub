"""引け後スキャナ: 翌営業日に仕掛ける1日完結エッジの候補を抽出 (毎日Actions想定)。

前日(=当日引け後)に確定情報から『明日の候補』を出せる3本のみ対象:
  ⑩R 小型貸借S高ショート : 当日S高引け(非プライム小型×貸借) → 翌朝 中GU で寄り売り。
                           ★ライブ: /equities/bars/daily(本日)の UL=1 から直接抽出。市場S高総数=
                             breadth で厚/薄を判定(≤9厚・9-15中・>15薄/見送り)。
                           対象=スタ/グロ等の個人の場のみ(現プライム小型はnullゆえ除外)。
  ④ 増配+来期下方ショート : 当日大引け後に zouhai_kahou_nx 開示 → 翌寄り売り。data/kouaku_records.json。
  ①B 中型PO・GD買い       : 当日 普通株PO発表(中型) → 翌営業日GD(≤-0.5%)で寄れば買い。data/po_records.json。

⑦(決定日)/⑥(受渡日)は po_records が前方予定日(発行価格決定日/受渡日)を構造化保持しないため
v1非対応(announceは前方=翌営業日が判るので①Bのみ対応)。売り禁(新規売建可否)はJ-Quants外=手動。

出力: --out に Markdown(Issue本文用)。候補0なら本文に『なし』を出し終了コードで知らせる(workflowが
Issue抑制)。当日が非営業日/データ未更新なら『新規シグナルなし』。
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
from typing import Any

from scripts._atomic import atomic_write_text

REPO = Path(__file__).resolve().parent.parent
MASTER_PATH = REPO / "data" / "edge_candidates" / "equities_master.json"
KOUAKU_PATH = REPO / "data" / "kouaku_records.json"
PO_PATH = REPO / "data" / "po_records.json"

# ⑩R: 機関の場(プライム/東証一部)・ETF/PRO を除外＝個人の場(スタ/グロ等)のみ。
# ※現プライム(post-2022)小型はPIT精査で null(+0.01%/t0.0/OOS−1.38)＝除外が正しい。
# (一時「プライム小型+0.97%」と別枠化したが、その+は再編前東証一部=今のスタ級の混入アーティファクトだった)
_DROP_MKT = {"プライム", "東証一部", "その他", "TOKYO PRO MARKET", None}


def _c5(code: str) -> str:
    code = str(code)
    return code if len(code) == 5 else code + "0"


def _disc_after_close(r: dict) -> bool:
    """大引け後(15:30以降)開示か(最早 disc_time >= 15:30)。"""
    times = [f.get("disc_time") for f in (r.get("bad_factors") or []) + (r.get("good_factors") or [])]
    times = [t for t in times if t]
    return bool(times) and min(times) >= "15:30"


def jst_today() -> str:
    """JST の本日 (CI は UTC 実行ゆえ +9h)。"""
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).date().isoformat()


def _margin_banned(target_date: str) -> set[str]:
    """margin-alert から『制度信用で新規売り建て不可』の銘柄コード集合を取得(取れなければ空)。

    真の売り禁は **RestrictedByJSF=1**(日証金=貸株の新規貸付停止)のみ。以下は売建を禁止しないので除外:
      - Restricted(取引所の増担保規制): 保証金率引上げだけ。両方向に課され売建は可能。
      - DailyPublication(日々公表銘柄): 信用残の毎日公表=監視段階。売買自由。
      - PrecautionByJSF(日証金の貸株注意喚起): 在庫タイトの警告(=逆日歩リスク)。停止ではない。
    """
    try:
        from scripts import _jquants
        rows = _jquants.get_list("/markets/margin-alert", date=target_date)
    except Exception:  # noqa: BLE001
        return set()
    return {str(r.get("Code")) for r in rows
            if (r.get("PubReason") or {}).get("RestrictedByJSF") == "1"}


def scan_10R(master: dict[str, dict], target_date: str) -> tuple[list[dict[str, Any]], int, str]:
    """ライブ daily bars から当日S高引けの ⑩R 候補と市場breadth。"""
    from scripts import _jquants
    try:
        bars = _jquants.get_list("/equities/bars/daily", date=target_date)
    except Exception:  # noqa: BLE001  非営業日/未更新は空扱い
        return [], 0, "取得不可(非営業日 or データ未更新)"
    if not bars:
        return [], 0, "データなし(非営業日)"
    breadth = sum(1 for b in bars if b.get("UL") == "1")
    banned = _margin_banned(target_date)  # 売り禁(新規売建停止)コード集合
    cands = []
    for b in bars:
        if b.get("UL") != "1":
            continue
        m = master.get(str(b["Code"]))
        if not m or m.get("scale_band") != "小型" or m.get("MrgnNm") != "貸借" or m.get("MktNm") in _DROP_MKT:
            continue
        c = b.get("AdjC") or b.get("C")
        h = b.get("AdjH") or b.get("H")
        # S高型: 終値=高値=S高引け(本命) / 終値<高値=タッチ剥がれ(弱・対象外)
        sh_close = bool(c and h and abs(c - h) < 1e-9)
        cands.append({"code": str(b["Code"])[:4], "name": m.get("CoName", "?"),
                      "close": c, "mkt": m.get("MktNm"), "sh_close": sh_close,
                      "banned": str(b["Code"]) in banned or str(b["Code"])[:4] in banned})
    tier = ("閑散=厚張りOK(+3.18%/勝64%)" if breadth <= 9
            else "中位(+2.69%/勝61%)" if breadth <= 15
            else "過熱=薄く/見送り(+1.70%/勝52%・非有意)")
    return cands, breadth, tier


def scan_zouhai(target_date: str) -> list[dict[str, Any]]:
    """data/kouaku_records.json から当日大引け後の zouhai_kahou_nx(④) を抽出。"""
    if not KOUAKU_PATH.exists():
        return []
    recs = json.loads(KOUAKU_PATH.read_text())["records"]
    return [{"code": r["code"], "name": (r.get("name") or "?")}
            for r in recs if r.get("event_date") == target_date
            and r.get("subpattern") == "zouhai_kahou_nx" and _disc_after_close(r)]


def scan_po_announce(master: dict[str, dict], target_date: str) -> list[dict[str, Any]]:
    """data/po_records.json の当日 普通株PO発表(中型)＝翌営業日GD買い(①B)候補。"""
    if not PO_PATH.exists():
        return []
    recs = json.loads(PO_PATH.read_text())["records"]
    out = []
    for r in recs:
        if r.get("stage") != "announce" or r.get("po_type") != "普通" or r.get("event_date") != target_date:
            continue
        m = master.get(_c5(r["code"]))
        if m and m.get("scale_band") == "中型":
            out.append({"code": r["code"], "name": (r.get("name") or "?")})
    return out


def _r10_table(title: str, cands: list[dict[str, Any]]) -> list[str]:
    """⑩R の1ティア(本体 or プライム小型)を S高引け本命を上にしてテーブル化。"""
    if not cands:
        return [f"### {title}", "", "該当なし。", ""]
    L = [f"### {title}", "", "| コード | 銘柄 | 市場 | S高終値 | 型 | 売り禁 |", "|---|---|---|--:|---|---|"]
    for c in sorted(cands, key=lambda x: (not x.get("sh_close"), x.get("banned"))):
        typ = "✅S高引け" if c.get("sh_close") else "△剥がれ(対象外)"
        ban = "🚫売り禁" if c.get("banned") else "可"
        L.append(f"| {c['code']} | {c['name']} | {c['mkt']} | {c['close']:,.0f} | {typ} | {ban} |")
    return L + [""]


def build_body(target_date: str, r10: tuple, z4: list, b1: list) -> tuple[str, int]:
    """Issue 本文(Markdown)と候補総数を返す。"""
    cands, breadth, tier = r10
    n = len(cands) + len(z4) + len(b1)
    L = [f"# 引け後スキャン {target_date}（翌営業日の1日完結エッジ候補）", "",
         f"候補 **{n}件**。すべて寄り成行→当日引け(楽天「大引不成」)。ショートは楽天信用・**売り禁スキップ(手動確認)**。", ""]

    L += [f"## ⑩R 小型貸借S高ショート（市場S高 {breadth}件＝{tier}）", "",
          "翌朝 **+5〜10%(中GU)** で寄れば**寄り成行売り→当日引け買戻**(極小サイズ)。GD/小GU/大GU(>10%)は見送り。",
          "**S高引け(C=上限)が本命**。タッチ剥がれ(終値<高値)は弱く対象外。売り禁(margin-alert)はスキップ。", ""]
    L += _r10_table("非プライム小型（スタ/グロ＝個人の場・+2.56%）※現プライム小型はnullゆえ除外", cands)
    nstrong = sum(1 for c in cands if c.get("sh_close") and not c.get("banned"))
    L += [f"→ 本命(S高引け×売建可)= **{nstrong}件**。"
          + ("" if breadth <= 15 else " ⚠️ 過熱日(S高>15)＝⑩Rは非有意・踏み上げ増。**薄く or 見送り**。")]

    L += ["", "## ④ 増配+来期下方ショート（当日大引け後 zouhai_kahou_nx）", ""]
    if z4:
        L += ["翌営業日 **寄り成行売り→当日引け買戻**。来期下方−30〜−17%・増配大ほど強い。", "",
              "| コード | 銘柄 |", "|---|---|"]
        L += [f"| {r['code']} | {r['name']} |" for r in z4]
    else:
        L += [f"該当なし（kouaku_records 最新={_latest(KOUAKU_PATH)}・鮮度に注意）。"]

    L += ["", "## ①B 中型PO・GD買い（当日 普通株PO発表＝翌営業日が対象）", ""]
    if b1:
        L += ["翌営業日が **−0.5%以下のGD** で寄れば **寄り成行買い→当日引け売り**。GU/フラットは見送り。", "",
              "| コード | 銘柄 |", "|---|---|"]
        L += [f"| {r['code']} | {r['name']} |" for r in b1]
    else:
        L += [f"該当なし（po_records 最新={_latest(PO_PATH)}・鮮度に注意）。"]

    L += ["", "---",
          "⑦(決定日)/⑥(受渡日)は日程データ未構造化のため本スキャンは非対応(手動でPOカレンダー確認)。",
          "詳細手順: docs/daily_checklist.md / 正本: docs/edge_playbook.md。"]
    return "\n".join(L) + "\n", n


def _latest(path: Path) -> str:
    """data ファイルの最新 event_date(鮮度表示用)。"""
    if not path.exists():
        return "なし"
    ds = [r.get("event_date", "") for r in json.loads(path.read_text())["records"]]
    return max((d for d in ds if d), default="なし")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None, help="対象日(既定=JST本日)。当日引け後に当日分を見る。")
    ap.add_argument("--out", type=Path, default=REPO / "reports" / "daily_scan.md",
                    help="Issue本文(Markdown)の出力先 (既定 reports/daily_scan.md)")
    args = ap.parse_args()
    target = args.date or jst_today()
    master = {str(r["Code"]): r for r in json.loads(MASTER_PATH.read_text())["records"]}

    r10 = scan_10R(master, target)
    z4 = scan_zouhai(target)
    b1 = scan_po_announce(master, target)
    body, n = build_body(target, r10, z4, b1)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(args.out, body)
    print(body)
    print(f"[daily_scan] {target} 候補{n}件 → {args.out}")
    # workflow が Issue 作成可否を判断できるよう件数を GITHUB_OUTPUT に出す
    import os
    gho = os.environ.get("GITHUB_OUTPUT")
    if gho:
        with open(gho, "a", encoding="utf-8") as fh:
            fh.write(f"candidates={n}\n")


if __name__ == "__main__":
    main()
