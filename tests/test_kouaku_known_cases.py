"""既知 N=3 事例 (7990 / 4246 / 5253) の分類・抽出ロジックを fixture でロックする。

実際の TDnet 開示・/fins/summary レコードは実 fetch しないと正確なフィールドが
得られないため、ここでは「分類器/抽出器が期待挙動を返すか」だけを最小 fixture で
確認する。実 fetch 後に test_kouaku_real_data.py を別途追加する。

実行:
  python -m unittest tests.test_kouaku_known_cases -v
"""
from __future__ import annotations

import unittest

from scripts.classify_kouaku import (
    classify_buyback_record,
    classify_by_title,
    load_rules,
)
from scripts.extract_mixed_disclosures import (
    aggregate_mixed,
    classify_all,
    decide_subpattern,
)


class TestClassifierKeywords(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_rules()

    def test_jisha_kabukai(self) -> None:
        for title in (
            "自己株式取得に係る事項の決定に関するお知らせ",
            "自己株式の取得状況に関するお知らせ",
            "自社株買いの実施について",
        ):
            pol, hint, _ = classify_by_title(title, rules=self.rules)
            self.assertEqual(pol, "good", title)
            self.assertEqual(hint, "jisha", title)

    def test_kahou(self) -> None:
        title = "通期業績予想の下方修正に関するお知らせ"
        pol, hint, _ = classify_by_title(title, rules=self.rules)
        self.assertEqual(pol, "bad")
        self.assertEqual(hint, "kahou")

    def test_fukuhai(self) -> None:
        title = "復配のお知らせ"
        pol, hint, _ = classify_by_title(title, rules=self.rules)
        self.assertEqual(pol, "good")
        self.assertEqual(hint, "fukuhai")

    def test_zouhai(self) -> None:
        title = "配当予想の修正(増配)に関するお知らせ"
        pol, hint, _ = classify_by_title(title, rules=self.rules)
        self.assertEqual(pol, "good")
        self.assertEqual(hint, "zouhai")

    def test_tokubai(self) -> None:
        title = "特別配当の実施に関するお知らせ"
        pol, hint, _ = classify_by_title(title, rules=self.rules)
        self.assertEqual(pol, "good")
        self.assertEqual(hint, "tokubai")

    def test_neutral(self) -> None:
        title = "代表取締役の異動に関するお知らせ"
        pol, _, _ = classify_by_title(title, rules=self.rules)
        self.assertEqual(pol, "neutral")


class TestSubpatternDecision(unittest.TestCase):
    def test_jisha_kahou(self) -> None:
        self.assertEqual(decide_subpattern({"jisha"}, {"kahou"}), "jisha_kahou")

    def test_jisha_genshu_via_kessan(self) -> None:
        self.assertEqual(decide_subpattern({"jisha"}, {"genshu"}), "jisha_genshu")
        self.assertEqual(decide_subpattern({"jisha"}, {"kessan"}), "jisha_genshu")

    def test_fukuhai_genshu(self) -> None:
        self.assertEqual(decide_subpattern({"fukuhai"}, {"genshu"}), "fukuhai_genshu")

    def test_zouhai_genshu(self) -> None:
        self.assertEqual(decide_subpattern({"zouhai"}, {"kessan"}), "zouhai_genshu")

    def test_tokubai_kahou(self) -> None:
        self.assertEqual(decide_subpattern({"tokubai"}, {"kahou"}), "tokubai_kahou")

    def test_other(self) -> None:
        self.assertEqual(decide_subpattern({"jisha"}, set()), "other")
        self.assertEqual(decide_subpattern({"kouhou"}, {"seikyu"}), "other")


class TestExtractMixed(unittest.TestCase):
    """既知 N=3 を fixture 入力で再現する。"""

    def _buyback(self, code: str, date_: str, title: str) -> dict:
        return {"Code": f"{code}0", "DiscDate": date_, "Title": title, "DiscNo": f"DUMMY_{code}_{date_}"}

    def _fins(
        self,
        code: str,
        date_: str,
        doctype: str,
        **fields,
    ) -> dict:
        base = {"Code": f"{code}0", "DiscDate": date_, "DocType": doctype, "DiscNo": f"FIN_{code}_{date_}"}
        base.update(fields)
        return base

    def test_7990_jisha_genshu(self) -> None:
        """7990: 自社株買い + 1Q決算短信 (NP YoY 大幅減益) が同日。"""
        buyback = [self._buyback("7990", "2025-08-12", "自己株式取得に係る事項の決定")]
        fins = [
            # 前年同期 (1Q FY2024)
            self._fins(
                "7990", "2024-08-09", "1QFinancialStatements_Consolidated_JP",
                CurPerType="1Q", CurPerSt="2024-04-01", CurPerEn="2024-06-30",
                CurFYSt="2024-04-01", CurFYEn="2025-03-31",
                NP="1000000000",
            ),
            # 当年 1Q (NP -30%)
            self._fins(
                "7990", "2025-08-12", "1QFinancialStatements_Consolidated_JP",
                CurPerType="1Q", CurPerSt="2025-04-01", CurPerEn="2025-06-30",
                CurFYSt="2025-04-01", CurFYEn="2026-03-31",
                NP="700000000",
            ),
        ]
        classified = classify_all(buyback, fins)
        records = aggregate_mixed(classified)
        self.assertEqual(len(records), 1, f"expected 1 mixed record, got {records}")
        r = records[0]
        self.assertEqual(r["code"], "7990")
        self.assertEqual(r["event_date"], "2025-08-12")
        self.assertEqual(r["subpattern"], "jisha_genshu")
        self.assertTrue(any(g["subpattern_hint"] == "jisha" for g in r["good_factors"]))
        self.assertTrue(any(b["subpattern_hint"] == "genshu" for b in r["bad_factors"]))

    def test_4246_jisha_genshu(self) -> None:
        """4246: 自社株買い + 通期決算短信 (NP YoY -20%) が同日。"""
        buyback = [self._buyback("4246", "2024-05-15", "自己株式取得に関するお知らせ")]
        fins = [
            self._fins(
                "4246", "2023-05-15", "FYFinancialStatements_Consolidated_JP",
                CurPerType="FY", CurPerSt="2022-04-01", CurPerEn="2023-03-31",
                CurFYSt="2022-04-01", CurFYEn="2023-03-31",
                NP="500000000",
            ),
            self._fins(
                "4246", "2024-05-15", "FYFinancialStatements_Consolidated_JP",
                CurPerType="FY", CurPerSt="2023-04-01", CurPerEn="2024-03-31",
                CurFYSt="2023-04-01", CurFYEn="2024-03-31",
                NP="400000000",
            ),
        ]
        classified = classify_all(buyback, fins)
        records = aggregate_mixed(classified)
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["code"], "4246")
        self.assertEqual(r["subpattern"], "jisha_genshu")

    def test_5253_jisha_kahou(self) -> None:
        """5253: 自社株買い + 業績予想下方修正 (NP-25%)。"""
        buyback = [self._buyback("5253", "2024-11-08", "自己株式の取得に関するお知らせ")]
        fins = [
            # 前回予想 (前期決算で公表)
            self._fins(
                "5253", "2024-05-10", "FYFinancialStatements_Consolidated_JP",
                CurPerType="FY", CurPerSt="2023-04-01", CurPerEn="2024-03-31",
                CurFYSt="2023-04-01", CurFYEn="2024-03-31",
                NxtFYSt="2024-04-01", NxtFYEn="2025-03-31",
                NxFSales="100000000000", NxFOP="10000000000", NxFOdP="10000000000", NxFNp="8000000000",
            ),
            # 下方修正発表
            self._fins(
                "5253", "2024-11-08", "EarnForecastRevision",
                CurFYSt="2024-04-01", CurFYEn="2025-03-31",
                FSales="95000000000", FOP="7000000000", FOdP="7000000000", FNP="6000000000",
            ),
        ]
        classified = classify_all(buyback, fins)
        records = aggregate_mixed(classified)
        self.assertEqual(len(records), 1, records)
        r = records[0]
        self.assertEqual(r["subpattern"], "jisha_kahou")


class TestBuybackRecordClassifier(unittest.TestCase):
    def test_pads_code_to_4(self) -> None:
        cd = classify_buyback_record({"Code": "79900", "DiscDate": "2025-08-12", "Title": "自己株式取得"})
        self.assertEqual(cd.code, "7990")
        self.assertEqual(cd.polarity, "good")
        self.assertEqual(cd.subpattern_hint, "jisha")


if __name__ == "__main__":
    unittest.main(verbosity=2)
