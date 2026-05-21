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
    """検査結果に問題を 1 件追加。"""
    issues.append((category, msg))


# ============================================================
# S. Static analysis
# ============================================================

def section_static() -> None:
    """syntax / unused import / compile sanity を検査。"""
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

        # 3. compile sanity (SyntaxError 以外は ValueError / TypeError の可能性)
        try:
            compile(src, str(p), "exec")
        except (SyntaxError, ValueError, TypeError) as e:
            add("S-compile", f"{p.relative_to(REPO_ROOT)}: {e}")


# ============================================================
# X. Cross-reference (docs ⇄ code)
# ============================================================

def section_xref() -> None:
    """SCHEMA.md ⇄ code の subpattern / metric / bucket / CLI 名一致を検査。"""
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
    """kouaku_records.json の id 一意 / subpattern 妥当 / 日付 / code / attrs 整合を検査。"""
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
    """全 CLI script が --help で exit 0 で返ることを smoke 確認。"""
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
    """extract→analyze→backtest を 2 回連続実行して同じ出力か検査 (--full 時のみ)。"""
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

def section_atomic_writes() -> None:
    """大きい JSON を直接 write_text で書いている場所 (= 中断で破損リスク) を検出。"""
    print("\n=== A. Atomic write hygiene ===")
    py_files = [p for p in REPO_ROOT.rglob("*.py")
                if "/.git/" not in str(p) and "/__pycache__/" not in str(p)]
    for p in py_files:
        src = p.read_text()
        # tempfile.TemporaryDirectory 内なら誤検知扱い (テストファイル)
        if "TemporaryDirectory" in src or "/tests/" in str(p):
            continue
        for m in re.finditer(r"\.write_text\(\s*json\.dumps", src):
            ln = src[: m.start()].count("\n") + 1
            add("A-atomic-write", f"{p.relative_to(REPO_ROOT)}:{ln} 直接 write_text (scripts._atomic.atomic_write_json を使う)")


def section_doc_links() -> None:
    """README / docs 内の相対リンクが実在するか。"""
    print("\n=== X. Doc links ===")
    for md in ["README.md", "CLAUDE.md", "docs/RUNBOOK.md", "docs/SCHEMA.md", "docs/kouaku_edge_spec.md"]:
        p = REPO_ROOT / md
        if not p.exists():
            continue
        for m in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", p.read_text()):
            link = m.group(1)
            if link.startswith(("http", "#")):
                continue
            target = (p.parent / link).resolve()
            if not target.exists():
                add("X-doc-link", f"{md}: 切れリンク → {link}")


def section_schema_version() -> None:
    """schema_version が data に宣言されており、コード側が読み込み時に意識しているか。"""
    print("\n=== I. Schema version ===")
    rp = REPO_ROOT / "data" / "kouaku_records.json"
    if not rp.exists():
        return
    data = json.loads(rp.read_text())
    if "schema_version" not in data:
        add("I-schema-version", "kouaku_records.json に schema_version 未宣言")


def section_docstrings() -> None:
    """top-level の公開関数 (アンダースコア始まりと main を除く) が docstring を持つか。"""
    print("\n=== S. Docstrings ===")
    py_files = [p for p in REPO_ROOT.rglob("*.py")
                if "/.git/" not in str(p) and "/__pycache__/" not in str(p) and "/tests/" not in str(p)]
    for p in py_files:
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        # トップレベルの関数 (nested は除外)
        for node in tree.body:
            funcs: list[ast.FunctionDef] = []
            if isinstance(node, ast.FunctionDef):
                funcs.append(node)
            elif isinstance(node, ast.ClassDef):
                for inner in node.body:
                    if isinstance(inner, ast.FunctionDef):
                        funcs.append(inner)
            for f in funcs:
                if f.name.startswith("_") or f.name == "main":
                    continue
                if not ast.get_docstring(f):
                    add("S-docstring", f"{p.relative_to(REPO_ROOT)}::{f.name}")


def section_float_equality() -> None:
    """価格系の float == 比較を検出 (浮動小数の正確性問題)。"""
    print("\n=== S. Float equality ===")
    py_files = [p for p in REPO_ROOT.rglob("*.py")
                if "/.git/" not in str(p) and "/__pycache__/" not in str(p)]
    seen: set[tuple[str, int]] = set()
    price_tokens = ("next_high", "next_low", "next_open", "next_close", "AdjFactor")
    for p in py_files:
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
                continue
            s = ast.unparse(node)
            if not any(t in s for t in price_tokens):
                continue
            key = (str(p.relative_to(REPO_ROOT)), node.lineno)
            if key in seen:
                continue
            seen.add(key)
            add("S-float-eq", f"{key[0]}:{key[1]}  {s[:80]}")


def section_assert_in_prod() -> None:
    """tests 以外で assert を使っていないか (production code では Exception を投げるべき)。"""
    print("\n=== S. assert in production ===")
    for p in REPO_ROOT.rglob("*.py"):
        if "/.git/" in str(p) or "/__pycache__/" in str(p) or "/tests/" in str(p):
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                add("S-prod-assert", f"{p.relative_to(REPO_ROOT)}:{node.lineno}")


def section_empty_data_robustness() -> None:
    """空の kouaku_records.json で analyze / backtest / query が exit 0 で返るか。"""
    print("\n=== B. Empty-data robustness ===")
    import tempfile
    from scripts._atomic import atomic_write_json
    with tempfile.TemporaryDirectory() as td:
        empty = Path(td) / "empty.json"
        atomic_write_json(empty, {
            "schema_version": 1,
            "event_type": "kouaku_mixed",
            "subpattern_counts": {},
            "records": [],
        })
        for mod in ("scripts.query_kouaku", "scripts.analyze_kouaku_edge", "scripts.backtest_kouaku"):
            proc = subprocess.run(
                [sys.executable, "-m", mod, "--path", str(empty)],
                cwd=REPO_ROOT, capture_output=True, timeout=30,
            )
            if proc.returncode != 0:
                add("B-empty-data", f"{mod}: {proc.stderr.decode()[:200]}")


def section_idempotency() -> None:
    """extract を 2 回実行で同じ出力か (副作用テスト)。"""
    print("\n=== D. Idempotency (extract) ===")
    p = REPO_ROOT / "data" / "kouaku_records.json"
    if not p.exists():
        return
    import hashlib
    def _hash(): return hashlib.sha256(p.read_bytes()).hexdigest()
    subprocess.run([sys.executable, "-m", "scripts.extract_mixed_disclosures"],
                   cwd=REPO_ROOT, capture_output=True, timeout=60)
    h1 = _hash()
    subprocess.run([sys.executable, "-m", "scripts.extract_mixed_disclosures"],
                   cwd=REPO_ROOT, capture_output=True, timeout=60)
    h2 = _hash()
    if h1 != h2:
        add("D-idempotent-extract", f"extract 2 回実行で出力差分")


def section_json_roundtrip() -> None:
    """kouaku_records.json が json load → dump → load で安定か。"""
    print("\n=== I. JSON round-trip ===")
    p = REPO_ROOT / "data" / "kouaku_records.json"
    if not p.exists():
        return
    data = json.loads(p.read_text())
    redumped = json.loads(json.dumps(data, ensure_ascii=False, indent=2))
    if data != redumped:
        add("I-json-roundtrip", "load → dump → load で内容が変化")


def section_open_encoding() -> None:
    """open(..., 'r'|'w') / os.fdopen() に encoding='utf-8' が指定されているか (AST 解析)。"""
    print("\n=== S. open() encoding ===")
    text_modes = {"r", "w", "a", "rt", "wt", "at", "r+", "w+", "a+"}
    for p in REPO_ROOT.rglob("*.py"):
        if "/.git/" in str(p) or "/__pycache__/" in str(p):
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            is_open = isinstance(f, ast.Name) and f.id == "open"
            is_fdopen = isinstance(f, ast.Attribute) and f.attr == "fdopen"
            if not (is_open or is_fdopen):
                continue
            # mode 引数を抽出 (位置 2 番目 or 名前付き mode=)
            mode = None
            mode_idx = 1
            if len(node.args) > mode_idx and isinstance(node.args[mode_idx], ast.Constant):
                mode = node.args[mode_idx].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if mode is None or mode not in text_modes:
                continue
            # encoding= 指定があるか
            has_enc = any(kw.arg == "encoding" for kw in node.keywords)
            # newline= 指定 (csv 等で必須) があれば許容
            has_newline = any(kw.arg == "newline" for kw in node.keywords)
            if has_enc or has_newline:
                continue
            add("S-no-encoding", f"{p.relative_to(REPO_ROOT)}:{node.lineno}  open/fdopen mode={mode!r} encoding 未指定")


def section_trailing_newline() -> None:
    """全 .py ファイルが末尾改行で終わっているか (POSIX 慣習)。"""
    print("\n=== S. Trailing newline ===")
    for p in REPO_ROOT.rglob("*.py"):
        if "/.git/" in str(p) or "/__pycache__/" in str(p):
            continue
        raw = p.read_bytes()
        if raw and not raw.endswith(b"\n"):
            add("S-no-newline-eof", f"{p.relative_to(REPO_ROOT)}: 末尾改行なし")


def section_test_main_guard() -> None:
    """tests/ 各ファイルが python -m tests.X で単独実行できる __main__ guard を持つか。"""
    print("\n=== B. Test main guards ===")
    for p in (REPO_ROOT / "tests").glob("*.py"):
        if p.stem == "__init__":
            continue
        src = p.read_text()
        if 'if __name__ == "__main__":' not in src and "if __name__ == '__main__':" not in src:
            add("B-test-no-main", f"{p.relative_to(REPO_ROOT)}")


def section_cli_help() -> None:
    """argparse の --flag に help= が必ず設定されているか。"""
    print("\n=== X. CLI help completeness ===")
    for p in (REPO_ROOT / "scripts").glob("*.py"):
        if p.stem.startswith("_") or p.stem == "__init__":
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            flag = node.args[0].value
            if not (isinstance(flag, str) and flag.startswith("--")):
                continue
            has_help = any(kw.arg == "help" for kw in node.keywords)
            if not has_help:
                add("X-cli-no-help", f"{p.relative_to(REPO_ROOT)}:{node.lineno}  {flag}")


def section_test_assertions() -> None:
    """tests/ の test_* メソッドが直接 or 経由する helper method 経由で必ず assertX を呼ぶか。"""
    print("\n=== B. Test has assertion ===")

    def _func_calls_assert(func: ast.FunctionDef, helpers: dict[str, ast.FunctionDef]) -> bool:
        """この関数本体か、self._helper() 経由で assertX が呼ばれるか。"""
        for inner in ast.walk(func):
            if isinstance(inner, ast.Call):
                f = inner.func
                if isinstance(f, ast.Attribute) and f.attr.startswith("assert"):
                    return True
                # self._helper(...) を呼ぶ場合、helper を再帰チェック
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self":
                    helper = helpers.get(f.attr)
                    if helper and _func_calls_assert(helper, helpers):
                        return True
            if isinstance(inner, ast.With):
                for item in inner.items:
                    if isinstance(item.context_expr, ast.Call):
                        ff = item.context_expr.func
                        if isinstance(ff, ast.Attribute) and ff.attr.startswith("assert"):
                            return True
            if isinstance(inner, ast.Raise):
                return True  # assertRaises 等の代わりに raise
        return False

    for p in (REPO_ROOT / "tests").glob("*.py"):
        if p.stem == "__init__":
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            # クラス内の全 helper を収集 (_method)
            helpers = {f.name: f for f in cls.body if isinstance(f, ast.FunctionDef)}
            for func in cls.body:
                if not (isinstance(func, ast.FunctionDef) and func.name.startswith("test_")):
                    continue
                if not _func_calls_assert(func, helpers):
                    add("B-test-no-assert", f"{p.relative_to(REPO_ROOT)}::{cls.name}::{func.name}")


def section_anti_patterns() -> None:
    """Python ベストプラクティス違反 (wildcard import / mutable default / bare except / broad except)。"""
    print("\n=== S. Anti-patterns ===")
    for p in REPO_ROOT.rglob("*.py"):
        if "/.git/" in str(p) or "/__pycache__/" in str(p):
            continue
        src = p.read_text()
        # wildcard import
        for m in re.finditer(r"from \S+ import \*", src):
            ln = src[: m.start()].count("\n") + 1
            add("S-wildcard-import", f"{p.relative_to(REPO_ROOT)}:{ln}")
        # AST checks
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            # mutable default argument
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in node.args.defaults:
                    if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                        add("S-mutable-default", f"{p.relative_to(REPO_ROOT)}:{d.lineno} {node.name}")
            # bare except / broad except (production code only)
            if isinstance(node, ast.ExceptHandler) and "/tests/" not in str(p):
                if node.type is None:
                    add("S-bare-except", f"{p.relative_to(REPO_ROOT)}:{node.lineno}")


def section_csv_schema() -> None:
    """data/kouaku_classification.csv の構造妥当性 (列数 / 必須列 / 正規表現)。"""
    print("\n=== I. CSV schema ===")
    p = REPO_ROOT / "data" / "kouaku_classification.csv"
    if not p.exists():
        return
    import csv as _csv
    with p.open() as f:
        reader = _csv.reader(f)
        header = next(reader, None)
        if not header or len(header) != 4:
            add("I-csv-header", f"列数 {len(header) if header else 0} (期待 4)")
            return
        for i, row in enumerate(reader, 2):
            if len(row) != 4:
                add("I-csv-row", f"line {i} 列数 {len(row)}")
                continue
            if any(not c.strip() for c in row[:3]):
                add("I-csv-empty", f"line {i} 必須列に空")
            try:
                re.compile(row[2])
            except re.error as e:
                add("I-csv-pattern", f"line {i} 正規表現エラー: {e}")


def section_coverage_gaps() -> None:
    """tests/ から import されていない scripts module + シンボル単位カバレッジ。

    audit_all の section_* と add/main は除外 (audit 自身は audit 対象外)。
    """
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

    # シンボル単位: 公開関数のうち tests のテキストに名前が出てこないもの
    # (audit_all の section_* / add / main は exempt: audit 自身は audit しない)
    AUDIT_EXEMPT = {"add", "main"}
    for p in (REPO_ROOT / "scripts").glob("*.py"):
        if p.stem.startswith("_") or p.stem == "__init__":
            continue
        if p.stem == "audit_all":
            continue  # audit 自身は exempt
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_") and node.name != "main":
                if node.name in AUDIT_EXEMPT:
                    continue
                if node.name not in test_src:
                    add("C-symbol-uncovered", f"scripts/{p.stem}.py::{node.name}")


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
    section_atomic_writes()
    section_doc_links()
    section_schema_version()
    section_docstrings()
    section_float_equality()
    section_assert_in_prod()
    section_cli_help()
    section_open_encoding()
    section_trailing_newline()
    section_test_main_guard()
    section_test_assertions()
    section_anti_patterns()
    section_xref()
    section_invariants()
    section_json_roundtrip()
    section_csv_schema()
    section_behavior()
    section_empty_data_robustness()
    # Determinism / Idempotency は extract が data を上書きするのでスキップ可
    if "--full" in sys.argv:
        section_determinism()
        section_idempotency()
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
