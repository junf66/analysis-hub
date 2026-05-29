"""8エッジ候補の宣言的設定 (ロング・デイ〜数日)。fetch/validate をこの設定で駆動する。

各候補:
  cid, name        : 識別子・名称
  source           : データ源 (buyback_reuse / td_category / weekly_margin /
                     short_selling / dividend_ex)
  disc_items       : TDnet DiscItems コード候補 (td_category時。?は要API discovery)
  title_any        : タイトル部分一致条件 (いずれか含む)
  exclude_bad      : 同日に悪材料(減益/下方修正/特別損失/第三者割当等)を含むものを除外
  threshold        : 需給候補の前週比しきい値%
  exits            : 検証する出口グリッド [(metric_key, label)]
  caveat_beta      : 数日保有=TOPIX未調整。通過しても「保留・要ベータ検証」にする
  dedup            : 既存戦略との重複回避メモ
"""
from __future__ import annotations

from scripts.edge_candidates.lib import INTRADAY_EXITS


def _multiday(days: list[int]) -> list[tuple[str, str]]:
    """+N日保有の出口メトリクス [(dN_ret, +N日)] を返す (multiday enrich が生成)。"""
    return [(f"d{n}_ret", f"+{n}日") for n in days]


CANDIDATES: list[dict] = [
    {"cid": "#1", "name": "上方修正発表翌日ロング", "source": "td_category",
     "disc_items": ["?業績予想修正"], "title_any": ["上方修正"], "exclude_bad": False,
     "exits": INTRADAY_EXITS, "caveat_beta": False,
     "dedup": "kouakuの上方修正(悪材料併発)とは別=単独の上方修正"},

    {"cid": "#2", "name": "自社株買い単独(悪材料なし)ロング", "source": "buyback_reuse",
     "exclude_bad": True, "exits": INTRADAY_EXITS, "caveat_beta": False,
     "dedup": "既存kouaku jisha_genshu(重い減益併発)・検証中④キッコーマン型(軽い減益)"
              "とは別集合=悪材料完全なし。増配/上方修正の追加好材料はあってもよい"},

    {"cid": "#3", "name": "増配単独(悪材料なし)ロング", "source": "td_category",
     "disc_items": ["?配当予想修正"], "title_any": ["増配", "配当予想の修正"],
     "exclude_bad": True, "exits": INTRADAY_EXITS, "caveat_beta": False,
     "dedup": "kouakuの増配(悪材料併発 zouhai_*)とは別=単独増配"},

    {"cid": "#4", "name": "株式分割発表ロング", "source": "td_category",
     "disc_items": ["?株式分割"], "title_any": ["株式分割"], "exclude_bad": False,
     "exits": _multiday([5, 10]), "caveat_beta": True,
     "dedup": "数日〜2週保有=ベータ汚染。通過しても保留・要TOPIX再検証"},

    {"cid": "#5", "name": "業務提携・大型受注ロング", "source": "td_category",
     "disc_items": ["?提携", "?受注"],
     "title_any": ["資本業務提携", "業務提携", "受注"], "exclude_bad": False,
     "exits": INTRADAY_EXITS, "caveat_beta": False},

    {"cid": "#7", "name": "信用買残激減(売り尽くし)ロング", "source": "weekly_margin",
     "threshold": -30.0, "exits": _multiday([1, 3, 5]), "caveat_beta": True,
     "dedup": "数日保有=ベータ汚染。しきい値(-30%)は検証中に最適化。通過しても保留"},

    {"cid": "#8", "name": "空売り残急増(踏み上げ)ロング", "source": "short_selling",
     "threshold": 50.0, "exits": _multiday([1, 3, 5]), "caveat_beta": True,
     "dedup": "数日保有=ベータ汚染。しきい値(+50%)は検証中に最適化。通過しても保留"},

    {"cid": "#9", "name": "配当権利落ち過剰下落ロング", "source": "dividend_ex",
     "exits": [("next_day_open_to_close_ret", "落ち日引け")] + _multiday([1]),
     "caveat_beta": False,
     "dedup": "権利落ち日のGDを寄り買い→当日引け or +1日"},
]


def by_id(cid: str) -> dict:
    """cid から候補設定を返す (無ければ KeyError)。"""
    for c in CANDIDATES:
        if c["cid"] == cid:
            return c
    raise KeyError(cid)
