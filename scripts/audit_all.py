"""総点検スクリプト。

これまでの「目視チェックで見つけ漏れ」を防ぐため、プログラムで検査:
  S. Static analysis (syntax / unused imports / dead code)
  X. Cross-reference (docs ⇄ code 一致)
  I. Data invariants (kouaku_records の整合性)
  B. Behavior (全 CLI が --help で動く)
  D. Determinism (パイプライン 2 回走らせて同じ出力か)
  C. Coverage gaps (テストが触っていない module)

問題があれば issues に蓄積し、最後に総数と内訳を出力。
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

issues: list[tuple[str, str]] = []  # [(category, message), ...]


def add(category: str, msg: str) -> None:
    issues.append((category, msg))


# ============================================================
# S. Static analysis
# ============================================================

def section_static() -> None:
    print("\n=== S. Static analysis ===")
    py_files = list((REPO_ROOT).rglob("*.py"))
    py_files = [p for p in py_files if "/.git/" not in str(p) and "/__pycache__/" not in str(p)]
    print(f"  scanning {len(py_files)} .py files")

    for p in py_files:
        # 1. syntax
        try:
            src = p.read_text()
            tree = ast.parse(src, filename=str(p))
        except SyntaxError as e:
            add("S-syntax", f"{p.relative_to(REPO_ROOT)}: {e}")
            continue

        # 2. unused imports
        imported: dict[str, ast.AST] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    imported[name] = node
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    name = alias.asname or alias.name
                    imported[name] = node

        used_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                cur = node
                while isinstance(cur, ast.Attribute):
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    used_names.add(cur.id)
        # __all__ や string-eval 経由は捕捉できないので、明らかな未使用のみ警告
        for name in imported:
            if name.startswith("_"):
                continue
            if name not in used_names and name not in ("annotations",):
                # 文字列リテラル内での使用 (e.g. forward type ref) を雑に check
                if name not in src.replace(f"import {name}", "").replace(f"from {name}", ""):
                    continue
                # __future__ 除く
                add("S-unused-import", f"{p.relative_to(REPO_ROOT)}: '{name}'")

        # 3. compile sanity
        try:
            compile(src, str(p), "exec")
        except Exception as e:
            add("S-compile", f"{p.relative_to(REPO_ROOT)}: {e}")


# ============================================================
# X. Cross-reference (docs ⇄ code)
# ============================================================

def section_xref() -> None:
    print("\n=== X. Cross-reference ===")
    # SCHEMA.md の subpattern と _SUBPATTERN_RULES が一致するか
    from scripts.extract_mixed_disclosures import _SUBPATTERN_RULES
    code_subs = {name for name, _, _ in _SUBPATTERN_RULES} | {"other"}
    schema_md = (REPO_ROOT / "docs" / "SCHEMA.md").read_text()
    doc_subs = set(re.findall(r"`(jisha_\w+|fukuhai_\w+|zouhai_\w+|tokubai_\w+|kouhou_\w+|other)`", schema_md))
    miss_in_doc = code_subs - doc_subs
    miss_in_code = doc_subs - code_subs
    if miss_in_doc:
        add("X-subpattern-doc", f"code にあるが SCHEMA.md にない: {sorted(miss_in_doc)}")
    if miss_in_code:
        add("X-subpattern-code", f"SCHEMA.md にあるが code にない: {sorted(miss_in_code)}")

    # query_kouaku._METRIC_CHOICES と SCHEMA.md のメトリクス
    from scripts.query_kouaku import _METRIC_CHOICES
    code_metrics = set(_METRIC_CHOICES)
    doc_metrics = set(re.findall(r"`(gap_pct|next_day_\w+_ret)`", schema_md))
    if code_metrics - doc_metrics:
        add("X-metric-doc", f"code にあるが SCHEMA.md にない: {sorted(code_metrics - doc_metrics)}")
    if doc_metrics - code_metrics:
        add("X-metric-code", f"SCHEMA.md にあるが code にない: {sorted(doc_metrics - code_metrics)}")

    # _buckets.BUCKET_ORDER と SCHEMA.md の bucket
    from scripts._buckets import BUCKET_ORDER
    doc_buckets = set(re.findall(r"\|\s*(寄前|寄り中|場中|引け間際|大引け後)\s*\|", schema_md))
    code_buckets = set(BUCKET_ORDER) - {"unknown"}
    if code_buckets != doc_buckets:
        add("X-bucket", f"code {sorted(code_buckets)} vs doc {sorted(doc_buckets)}")

    # RUNBOOK / README に書かれている CLI コマンドが実在するか (scripts.X 形式)
    for md_name in ("README.md", "docs/RUNBOOK.md"):
        md = (REPO_ROOT / md_name).read_text()
        for m in re.finditer(r"python -m scripts\.([\w_]+)", md):
            modname = m.group(1)
            path = REPO_ROOT / "scripts" / f"{modname}.py"
            if not path.exists():
                add("X-cli-doc", f"{md_name} に書かれた `scripts.{modname}` が存在しない")


# ============================================================
# I. Data invariants
# ============================================================

def section_invariants() -> None:
    print("\n=== I. Data invariants ===")
    path = REPO_ROOT / "data" / "kouaku_records.json"
    if not path.exists():
        add("I-missing", f"{path} not found")
        return
    data = json.loads(path.read_text())
    records = data.get("records", [])

    # 1. id 一意性
    ids = [r.get("id") for r in records]
    dups = {x for x in ids if ids.count(x) > 1}
    if dups:
        add("I-dup-id", f"重複 id: {sorted(dups)[:5]}")

    # 2. subpattern が既知集合に入る
    from scripts.extract_mixed_disclosures import _SUBPATTERN_RULES
    valid_subs = {name for name, _, _ in _SUBPATTERN_RULES} | {"other"}
    bad_subs = {r.get("subpattern") for r in records} - valid_subs
    if bad_subs:
        add("I-bad-subpattern", f"未知 subpattern: {bad_subs}")

    # 3. ISO date
    for r in records:
        ed = r.get("event_date") or ""
        try:
            date.fromisoformat(ed)
        except ValueError:
            add("I-bad-date", f"{r.get('id')} event_date={ed}")

    # 4. code が 4-5 文字
    for r in records:
        c = r.get("code") or ""
        if not (4 <= len(c) <= 5):
            add("I-bad-code", f"{r.get('id')} code={c}")

    # 5. good_factors, bad_factors が >= 1 件
    for r in records:
        if not r.get("good_factors"):
            add("I-empty-good", f"{r.get('id')} good_factors 空")
        if not r.get("bad_factors"):
            add("I-empty-bad", f"{r.get('id')} bad_factors 空")

    # 6. attrs に SCHEMA で想定されてないキーが多数ないか
    schema_attr_keys = {
        "prev_close", "next_open", "next_high", "next_low", "next_close",
        "gap_pct", "next_day_open_to_close_ret", "next_day_open_to_high_ret",
        "next_day_open_to_low_ret", "next_day_full_ret", "event_bar_date",
        "next_bar_date", "limit_locked",
        "next_open_900", "next_open_first_time", "minute_error", "price_error",
        "subpattern", "good_factors", "bad_factors",  # normalizer 経由で入る場合
    } | {
        f"next_day_{tag}_ret" for tag in ("905", "910", "915", "930", "1000", "morning")
    }
    unknown_attr_keys: dict[str, int] = defaultdict(int)
    for r in records:
        for k in (r.get("attrs") or {}):
            if k not in schema_attr_keys:
                unknown_attr_keys[k] += 1
    if unknown_attr_keys:
        add("I-unknown-attrs", f"SCHEMA.md 未掲載の attrs key: {dict(unknown_attr_keys)}")

    # 7. limit_locked の整合: |gap|>=15 だが limit_locked=False のもの
    edge_cases = 0
    for r in records:
        a = r.get("attrs") or {}
        gap = a.get("gap_pct")
        nh = a.get("next_high"); nl = a.get("next_low"); no = a.get("next_open"); nc = a.get("next_close")
        if gap is None or nh is None or nl is None or no is None or nc is None:
            continue
        is_locked_shape = (nh == nl == no == nc) and abs(gap) >= 15.0
        if is_locked_shape != bool(a.get("limit_locked")):
            edge_cases += 1
    if edge_cases:
        add("I-limit-lock", f"limit_locked フラグと実データの不一致: {edge_cases} 件")

    # 8. subpattern_counts が現データと一致
    declared = data.get("subpattern_counts", {})
    actual = defaultdict(int)
    for r in records:
        actual[r.get("subpattern", "other")] += 1
    if dict(declared) != dict(actual):
        add("I-count-mismatch", f"declared={dict(declared)} actual={dict(actual)}")

    print(f"  records validated: {len(records)}")


# ============================================================
# B. Behavior: 全 CLI が --help で死なないか
# ============================================================

def section_behavior() -> None:
    print("\n=== B. Behavior smoke ===")
    scripts = [
        "scripts.fetch_disclosures",
        "scripts.extract_mixed_disclosures",
        "scripts.enrich_price_kouaku",
        "scripts.analyze_kouaku_edge",
        "scripts.backtest_kouaku",
        "scripts.query_kouaku",
        "scripts.data_health",
        "scripts.update_all",
        "scripts.noon_disclosure_experiment",
    ]
    for s in scripts:
        proc = subprocess.run(
            [sys.executable, "-m", s, "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=15,
        )
        if proc.returncode != 0:
            add("B-help", f"{s} --help failed: {proc.stderr.decode()[:200]}")


# ============================================================
# D. Determinism: extract→analyze→backtest を 2 回連続実行して同じか
# ============================================================

def section_determinism() -> None:
    print("\n=== D. Determinism ===")
    py = sys.executable
    out1: dict[str, str] = {}
    for stage in (1, 2):
        # extract → analyze → backtest をキャッシュから
        for mod in ("scripts.extract_mixed_disclosures", "scripts.analyze_kouaku_edge", "scripts.backtest_kouaku"):
            subprocess.run([py, "-m", mod], cwd=REPO_ROOT, capture_output=True, timeout=120)
        for f in ("data/kouaku_records.json", "reports/kouaku_analysis.md", "reports/kouaku_backtest.md"):
            p = REPO_ROOT / f
            if not p.exists():
                continue
            key = f
            content = p.read_text()
            if stage == 1:
                out1[key] = content
            else:
                if out1.get(key) != content:
                    add("D-nondeterm", f"{f} が 2 回実行で差分発生")


# ============================================================
# C. Coverage gaps: 各 module が何かしらの test に import されているか
# ============================================================

def section_coverage_gaps() -> None:
    print("\n=== C. Coverage gaps ===")
    # 全ての script module を列挙
    modules = sorted(p.stem for p in (REPO_ROOT / "scripts").glob("*.py")
                     if not p.stem.startswith("_") and p.stem != "__init__")

    test_src = ""
    for p in (REPO_ROOT / "tests").glob("*.py"):
        test_src += p.read_text()

    not_tested: list[str] = []
    for m in modules:
        if f"scripts.{m}" not in test_src and f"from scripts import {m}" not in test_src:
            not_tested.append(m)
    if not_tested:
        add("C-no-test", f"テスト未参照の module: {not_tested}")


# ============================================================
# Run
# ============================================================

def section_dead_code() -> None:
    """常に同じ値しか返さない関数・to-be-removed スタブを検出。"""
    print("\n=== S. Dead code / stub detection ===")
    py_files = [p for p in REPO_ROOT.rglob("*.py")
                if "/.git/" not in str(p) and "/__pycache__/" not in str(p)]
    for p in py_files:
        try:
            tree = ast.parse(p.read_text(), filename=str(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            # 本体が単一の Return None (or 単純 return) のみ
            body = node.body
            # docstring を除く
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                body = body[1:]
            if len(body) == 1 and isinstance(body[0], ast.Return):
                ret = body[0].value
                if ret is None or (isinstance(ret, ast.Constant) and ret.value is None):
                    add("S-stub", f"{p.relative_to(REPO_ROOT)}::{node.name}() は常に None を返す")


def section_repo_root_consistency() -> None:
    """REPO_ROOT が parent.parent で取れる構造になっているか。"""
    print("\n=== S. REPO_ROOT consistency ===")
    # 全ての .py で REPO_ROOT 定義を grep し、parents の数が一致するか
    pat = re.compile(r"REPO_ROOT\s*=\s*Path\(__file__\)\.resolve\(\)\.((?:parent\.)+parent)")
    for p in REPO_ROOT.rglob("*.py"):
        if "/.git/" in str(p) or "/__pycache__/" in str(p):
            continue
        rel = p.relative_to(REPO_ROOT)
        depth = len(rel.parts) - 1  # ファイル分を除いたディレクトリ深さ
        for m in pat.finditer(p.read_text()):
            parents = m.group(1).count("parent")
            # scripts/foo.py (深さ 1) は parent×2 (scripts/ → repo root) が正しい
            if parents != depth + 1:
                add("S-repo-root", f"{rel}: REPO_ROOT に parent×{parents} だがファイル深さ {depth} (正しくは parent×{depth+1})")


def main() -> None:
    section_static()
    section_dead_code()
    section_repo_root_consistency()
    section_xref()
    section_invariants()
    section_behavior()
    # Determinism は extract が data を上書きするのでスキップ可
    if "--full" in sys.argv:
        section_determinism()
    section_coverage_gaps()

    print(f"\n{'='*60}\n総検査結果: {len(issues)} 件\n{'='*60}")
    by_cat: dict[str, list[str]] = defaultdict(list)
    for cat, msg in issues:
        by_cat[cat].append(msg)
    for cat in sorted(by_cat):
        print(f"\n[{cat}] ({len(by_cat[cat])})")
        for m in by_cat[cat][:15]:
            print(f"  {m}")
        if len(by_cat[cat]) > 15:
            print(f"  ... +{len(by_cat[cat])-15} more")

    if issues:
        sys.exit(1)


if __name__ == "__main__":
    main()
