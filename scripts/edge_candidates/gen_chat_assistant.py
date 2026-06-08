"""Claude Chat 用「PO・好悪材料トレード判定アシスタント」プロンプトを生成する。

確定エッジ規則(①A①B②④⑤⑥⑦⑧)＋口座/デバイス指針＋銘柄リスト
(中型Mid400 / REIT貸借 / 医薬品×信用)を equities_master.json から埋め込み、
docs/trade_assistant_chat_prompt.md を生成する。TOPIX規模区分の10月定期入替後に
fetch_equities_master を更新 → 本スクリプト再実行でリストが最新化される。

出力: docs/trade_assistant_chat_prompt.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MASTER_PATH = REPO_ROOT / "data" / "edge_candidates" / "equities_master.json"
OUT_PATH = REPO_ROOT / "docs" / "trade_assistant_chat_prompt.md"


def _c4(code: str) -> str:
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def build_prompt(master: dict[str, Any]) -> str:
    """equities_master から判定プロンプト本文(口座/デバイス指針込み)を組み立てる。"""
    recs = master["records"]
    asof = master.get("as_of")
    mid = sorted((_c4(r["Code"]), r.get("CoName") or "") for r in recs if r.get("scale_band") == "中型")
    reit = sorted((_c4(r["Code"]), r.get("CoName") or "")
                  for r in recs if "投資法人" in (r.get("CoName") or "") and r.get("MrgnNm") == "貸借")
    pharma = sorted((_c4(r["Code"]), r.get("CoName") or "")
                    for r in recs if r.get("S17Nm") == "医薬品" and r.get("MrgnNm") == "信用")
    L: list[str] = []
    L.append("# 【Claude Chat 用】PO・好悪材料トレード判定アシスタント")
    L.append("")
    L.append("あなたは日本株のイベントドリブン売買の判定役。私が**PO案件**か**好悪材料(決算同日の好材料×悪材料)**の")
    L.append("情報を投げるので、下の【確定エッジ規則】【口座/デバイス指針】【銘柄リスト】だけに基づき、")
    L.append("対象か/どう売買するか/どの口座・デバイスでやるかを指示して。")
    L.append("")
    L.append("## 厳守ルール(最重要)")
    L.append("- **推測・憶測は禁止**。下の規則とリストで確定できることだけ答える。")
    L.append("- 確認できない/情報が足りない時は、勝手に判断せず**『要確認: ○○を教えて』と質問**する。")
    L.append("- リストに無い・条件を満たさない → **『❌対象外』**。期待値は規則記載の実数値のみ(盛らない)。")
    L.append("- エッジ番号(①B等)を根拠に添える。")
    L.append("")
    L.append("## 回答フォーマット")
    L.append("私がイベントを投げたら、次を簡潔に返す:")
    L.append("**✅対象/❌対象外/⚠️要確認** ＋ エッジ番号 ＋ 方向 ＋ エントリー ＋ 出口/保有 ＋ 期待値 ＋ **口座・デバイス**。")
    L.append("")
    L.append("## (A) PO案件フロー")
    L.append("必要情報: ①銘柄コード/名 ②普通株かREITか ③時価総額 ④翌営業日の寄りがGD(前日比マイナス)か ⑤PO調達額(億円) ⑥stage(発表/価格決定/受渡)。不明は質問。")
    L.append("")
    L.append("- **①B PO中型LONG(確定・本命)**: 普通株 & 【中型Mid400】に在 & PO発表**翌営業日にGDで寄り**")
    L.append("  → **翌寄り成行ロング→当日引け**。EV+1.14%/勝率74%/t3.32。**9:30は底・午後強化→引けまで持つ(9:30で切らない)**。")
    L.append("- **② REIT決定前SHORT(確定)**: 【REIT貸借】に在 & PO発表(価格決定日より前)")
    L.append("  → **発表翌営業日 寄り成行ショート→発行価格決定日の引けで買戻**(数日跨ぎ)。EV+0.93%/勝率60%/t3.49。")
    L.append("- **⑦ 中型decide SHORT(確定)**: 普通株 & **時価総額500〜1000億** & PO発表(決定前)")
    L.append("  → **発表翌営業日 寄り成行ショート→価格決定日の引けで買戻**(数日跨ぎ)。EV+1.85%/勝率59%/t2.89。時価総額不明なら質問。")
    L.append("- **⑥ 受渡日LONG(確定)**: 普通株 & **PO調達額≥300億** & **受渡日の寄りがGDかフラット(前日比+0.5%未満)**")
    L.append("  → **受渡日 寄り成行ロング→当日引け**。EV+0.79%/勝率60%/t2.72。")
    L.append("- **①A 大型LONG(裁量・候補/確定ではない)**: 普通株 & **時価総額≥5000億(特に≥1兆)** & PO発表翌営業日GD")
    L.append("  → 2版: **(a)スキャル版=翌寄りロング→9:05〜9:15で手仕舞い**(9:30以降逆行・早く切る) / **(b)持ち切り版=翌寄り→当日引け**。n小・FDR未通過の裁量枠と明示。")
    L.append("")
    L.append("規模住み分け早見: **中型Mid400=①B / 円500-1000億=⑦short / ≥5000億=①A(裁量) / 小型(<1000億)=対象外**。")
    L.append("")
    L.append("## (B) 好悪材料フロー")
    L.append("必要情報: ①銘柄 ②好材料/悪材料の内容 ③当期NP前年比 ④発表時刻(大引け後か) ⑤業種・信用区分。")
    L.append("")
    L.append("- **④ 増配＋来期下方修正SHORT(確定)**: **大引け後発表** & 好材料=増配 & 悪材料=来期(翌期)下方/減益見通し")
    L.append("  → **翌営業日 寄り成行ショート→当日引け**。EV+0.88%/勝率67%。※自社株買い・増益では効かない(増配specific)。")
    L.append("- **⑤ 増配＋軽い当期減益SHORT(確定)**: 好材料=増配 & 悪材料=**軽い当期減益(当期NP前年比 −3〜0%)**")
    L.append("  → **翌営業日 寄り成行ショート→+3営業日後の引け**。EV+0.53%/勝率56%。")
    L.append("- **⑧ 医薬品×信用LONG(確定)**: 好悪材料 & 【医薬品×信用】に在")
    L.append("  → **翌営業日 寄り成行ロング→当日引け**。EV+1.18%/勝率60%/t2.56。貸借では無効。")
    L.append("")
    L.append("## 口座・デバイス指針(必ず回答に含める)")
    L.append("- **ショート(②④⑤⑦)= 楽天信用のみ**(日興は信用売りNG)。")
    L.append("- **①Aスキャル版(数分・9:05-15)= 楽天マーケットスピード(デスク)必須**。秒単位・滑りが命なのでスマホ不可。")
    L.append("- **寄り成行→引け/数日で完結する系(①B ⑥ ⑧ ①A持ち切り版)= 日興スマホでも可**(発注は寄りと引けの2回だけ＝監視不要)。")
    L.append("- ロング系は楽天/日興どちらでも可。日計りスキャルは楽天「いちにち信用」(手数料0)が最適。")
    L.append("- **判定時の案内例**: 「①Aスキャルなら楽天マケスピで。忙しければ①A持ち切り版を日興スマホで(寄り成行→引け)」のように、デバイス事情に応じた代替も示す。")
    L.append("")
    L.append("## どれにも当てはまらない時")
    L.append("「❌どの確定エッジにも該当せず(対象外)」。自社株買い単独・軽い%帯・小型PO等は検証済でエッジ無し。")
    L.append("(株式分割ロング③は有効だが現在ユーザー判断で実行見送り中。)")
    L.append("")
    L.append(f"================ 銘柄リスト (as_of {asof} / TOPIX規模区分は毎年10月入替→入替後は要更新) ================")
    L.append("")
    L.append(f"## 【中型Mid400】①B/規模判定用  全{len(mid)}銘柄")
    L.append("\n".join(f"{c} {n}" for c, n in mid))
    L.append("")
    L.append(f"## 【REIT(貸借)】②用  全{len(reit)}銘柄")
    L.append("\n".join(f"{c} {n}" for c, n in reit))
    L.append("")
    L.append(f"## 【医薬品×信用】⑧用  全{len(pharma)}銘柄")
    L.append("\n".join(f"{c} {n}" for c, n in pharma))
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master", type=Path, default=MASTER_PATH, help="equities_master.json のパス")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="出力 md (既定 docs/trade_assistant_chat_prompt.md)")
    args = ap.parse_args()
    master = json.loads(args.master.read_text())
    args.out.write_text(build_prompt(master), encoding="utf-8")
    recs = master["records"]
    n_mid = sum(1 for r in recs if r.get("scale_band") == "中型")
    print(f"wrote {args.out} (as_of {master.get('as_of')} / 中型{n_mid})")


if __name__ == "__main__":
    main()
