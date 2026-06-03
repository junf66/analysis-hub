# kouaku 程度の死角 掃討 (magnitude三分位) (2026-06-02)

検証セル 48 (subpattern×開示時刻×magnitude三分位, 約定可能のみ, 各n≥30)。有利方向net (long0.20%/short0.15%控除) / 日付クラスタ頑健t / 全セル横断BH-FDR。

## FDR生存セル (3件) = 二値化で潰れていた隠れエッジ

| subpattern | 時刻 | 程度三分位 | 範囲 | 方向 | n | net EV | t_clust | 勝率 |
|---|---|---|---|---|---|---|---|---|
| zouhai_kahou_nx | 大引け後 | 中 | -30〜-17% | short | 81 | +1.34% | +3.70 | 68% |
| zouhai_kahou_nx | 大引け後 | 強(magnitude大) | -17〜-10% | short | 79 | +0.87% | +3.05 | 62% |
| kouhou_nx_genshu | 大引け後 | 強(magnitude大) | -48〜-10% | short | 502 | +0.40% | +3.00 | 57% |

## 強候補 上位20 (net>0.4%, t降順, FDR前)

| subpattern | 時刻 | 三分位 | 範囲 | 方向 | n | net EV | t_clust | 勝率 | FDR |
|---|---|---|---|---|---|---|---|---|---|
| zouhai_kahou_nx | 大引け後 | 中 | -30〜-17% | short | 81 | +1.34% | +3.70 | 68% | ★ |
| zouhai_kahou_nx | 大引け後 | 強(magnitude大) | -17〜-10% | short | 79 | +0.87% | +3.05 | 62% | ★ |
| kouhou_nx_genshu | 大引け後 | 強(magnitude大) | -48〜-10% | short | 502 | +0.40% | +3.00 | 57% | ★ |
| zouhai_genshu | 引け間際 | 強(magnitude大) | -17〜-10% | short | 42 | +0.89% | +2.07 | 71% |  |
| zouhai_genshu | 引け間際 | 中 | -28〜-18% | long | 40 | +1.33% | +2.06 | 52% |  |
| kouhou_seikyu | 引け間際 | 中 | +39〜+107% | short | 123 | +0.74% | +1.97 | 56% |  |
| kouhou_kahou_nx | 大引け後 | 中 | -46〜-24% | short | 395 | +0.40% | +1.75 | 56% |  |
| zouhai_genshu | 大引け後 | 中 | -38〜-20% | short | 74 | +0.55% | +1.65 | 65% |  |
| zouhai_kahou_nx | 引け間際 | 弱(magnitude小) | -330〜-34% | short | 54 | +0.62% | +1.59 | 57% |  |
| zouhai_genshu | 大引け後 | 強(magnitude大) | -20〜-10% | short | 78 | +0.59% | +1.47 | 56% |  |
| kouhou_seikyu | 大引け後 | 強(magnitude大) | +106〜+878100% | short | 235 | +0.54% | +1.46 | 58% |  |
| jisha_genshu | 引け間際 | 弱(magnitude小) | -27900〜-55% | long | 101 | +0.42% | +1.31 | 51% |  |
| zouhai_kahou_nx | 大引け後 | 弱(magnitude小) | -96〜-30% | short | 79 | +0.44% | +1.27 | 59% |  |
| kouhou_seikyu | 大引け後 | 中 | +42〜+105% | short | 239 | +0.41% | +1.13 | 56% |  |
| kouhou_genhai | 大引け後 | 中 | -50〜-27% | long | 54 | +0.53% | +0.89 | 46% |  |

## メモ
- 程度三分位で『弱/中/強』に割り、二値化タグで潰れていた magnitude 依存を炙り出す掃討。
- FDR生存セルのみ実弾水準候補。FDR前の強候補は過剰最適化注意(方向一貫性で判断)。
- 既知: zouhai_kahou_nx×大引け後short は中程度magnitudeが芯(本掃討でも確認)。