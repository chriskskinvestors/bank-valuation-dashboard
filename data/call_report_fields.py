"""Any FDIC call-report field — and formulas over them — as a screener metric.

Owner directive 2026-09-30: "a way to do a screen and have any call report
field be an option." Stage 1 serves the ~2,200 numeric fields of the FDIC
BankFind financials API (catalog vendored in data/fdic_field_catalog.json —
see tools/build_fdic_field_catalog.py for how each field's KIND was proven).

Metric keys:
    "fdic:<CODE>"   a raw FDIC field, e.g. "fdic:CD3LES"
    "ffiec:<MDRM>"  a raw FFIEC Call Report line item from the full-report store
                    (data/call_report_full — every line item the quarterly
                    refresh-ffiec job downloads), e.g. "ffiec:RCONB530"
    "fx:<slug>"     a user formula over FDIC codes and/or MDRM codes

FFIEC line items: units come from the filing itself (USD facts are
$thousands → dollars here; non-monetary items as filed, labeled "as reported").
Multi-charter groups: $ items strict-sum; non-monetary items n/a.

Units (CLAUDE.md contract): FDIC reports dollar LEVELS in $thousands; they are
converted ×1000 to raw dollars here, at the boundary. Ratios and counts are
shown exactly as reported. "asis" fields (no proof of units) are shown as
reported and labeled so — never scaled.

Multi-charter holdcos (data/cert_group): a level is the strict SUM over the
group's charters (any charter missing → n/a, never a partial sum); a ratio is
rebuilt from summed components only where cert_group._EXACT_QUOTIENTS proves
the formula, else n/a; "asis" fields are n/a for groups. Formulas evaluate on
the resolved values, so a formula over levels is a ratio of sums.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

FIELD_PREFIX = "fdic:"
FFIEC_PREFIX = "ffiec:"
FORMULA_PREFIX = "fx:"
_FFIEC_CATALOG_TTL_S = 3600        # the store changes quarterly; catalog() scans it
_CATALOG_PATH = Path(__file__).parent / "fdic_field_catalog.json"
_VALUES_TTL_S = 7 * 86400          # a filed quarter is effectively immutable
_LATEST_TTL_S = 6 * 3600

# Counts that do NOT add across charters: two charters each in 7 states are
# not in 14 states (review 2026-10-02: WFC showed 42, BNY 32).
_NON_ADDITIVE = frozenset({"OFFSTATE"})
# Fields the pipeline's zero-nulling rules read (fdic_client.
# null_unreported_capital / null_undefined_quotients) — fetched alongside any
# FDIC ratio so a literal-0 ratio over a zero/absent denominator is n/a here
# exactly as everywhere else (review: 1,831 CBLR filers showed RBCRWAJ 0.00%).
_NULL_GUARD_FIELDS = ("RWAJ", "RBCT1J", "RBCT1", "RBCT1C", "NCLNLS")
# Formula guards: bounded size so a pasted/saved formula can never hang or
# crash a run (review: 1,000-term formulas raised RecursionError on Run).
_FORMULA_MAX_CHARS = 400
_FORMULA_MAX_NODES = 120
_FORMULA_MAX_CONST = 1e12

FORMULA_FORMATS = {"Number": ("number", 2), "Percent": ("pct", 2),
                   "Dollars": ("dollars_auto", 1), "Multiple (x)": ("ratio", 2)}


# ── Catalog ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def catalog() -> dict[str, dict]:
    """{CODE: {"title": str, "kind": "level"|"ratio"|"count"|"asis"}}."""
    try:
        return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[call_report_fields] catalog unavailable: {type(e).__name__}")
        return {}


_FFIEC_MEMO: dict = {"at": 0.0, "cat": {}}
_ERRORS: list[str] = []


def last_errors() -> list[str]:
    """Source failures during the most recent attach() — the caller shows
    them so an outage reads as an outage, not as banks with no data."""
    return list(_ERRORS)


def ffiec_catalog() -> dict[str, dict]:
    """{MDRM: {"title", "kind"}} for line items in the full-report store
    (memoised an hour). {} when the store is empty or unreachable — the
    picker then simply offers no FFIEC items; nothing is guessed."""
    import time
    ttl = _FFIEC_CATALOG_TTL_S if _FFIEC_MEMO.get("ok") else 60
    if time.time() - _FFIEC_MEMO["at"] < ttl:
        return _FFIEC_MEMO["cat"]
    cat: dict[str, dict] = {}
    ok = False
    try:
        from data import call_report_full
        for r in call_report_full.catalog():
            code = str(r.get("code") or "").upper()
            if code:
                cat[code] = {"title": r.get("title") or code,
                             "kind": "level" if r.get("unit") == "usd_thousands" else "asis"}
        ok = True
    except Exception as e:
        print(f"[call_report_fields] FFIEC catalog unavailable: {type(e).__name__}")
    # A failed read retries after a minute; a good one is kept an hour.
    _FFIEC_MEMO.update(at=time.time(), cat=cat, ok=ok)
    return cat


def _kind(code: str) -> str | None:
    c = catalog().get(code) or ffiec_catalog().get(code)
    return c.get("kind") if c else None


def field_key(code: str) -> str:
    return f"{FIELD_PREFIX}{code.upper()}"


def ffiec_key(code: str) -> str:
    return f"{FFIEC_PREFIX}{code.upper()}"


def is_dynamic(key) -> bool:
    return isinstance(key, str) and (key.startswith(FIELD_PREFIX)
                                     or key.startswith(FFIEC_PREFIX)
                                     or key.startswith(FORMULA_PREFIX))


def ffiec_label(code: str) -> str:
    c = ffiec_catalog().get(code, {})
    suffix = " ($)" if c.get("kind") == "level" else " (as reported)"
    return f"{code} · {c.get('title') or code} (FFIEC){suffix}"


def field_label(code: str) -> str:
    c = catalog().get(code, {})
    title = c.get("title") or code
    suffix = {"level": " ($)", "ratio": "", "count": " (count)",
              "asis": " (as reported)"}.get(c.get("kind"), " (as reported)")
    return f"{code} · {title}{suffix}"


def metric_def(key: str, formulas: dict | None = None) -> dict | None:
    """A config.METRICS-shaped definition for a dynamic key (None if unknown)."""
    if key.startswith(FIELD_PREFIX):
        code = key[len(FIELD_PREFIX):]
        c = catalog().get(code)
        if not c:
            return None
        fmt, dec = {"level": ("dollars_auto", 1), "ratio": ("number", 2),
                    "count": ("number", 0), "asis": ("number", 2)}[c["kind"]]
        return {"key": key, "label": field_label(code), "header": code,
                "source": "call_report", "format": fmt, "decimals": dec,
                "category": "Call report (FDIC)"}
    if key.startswith(FFIEC_PREFIX):
        code = key[len(FFIEC_PREFIX):]
        c = ffiec_catalog().get(code)
        # Unknown here (store empty in this environment) still gets a label:
        # values resolve to n/a, the saved definition is never lost.
        fmt, dec = ("dollars_auto", 1) if (c or {}).get("kind") == "level" else ("number", 2)
        return {"key": key, "label": ffiec_label(code), "header": code,
                "source": "call_report_ffiec", "format": fmt, "decimals": dec,
                "category": "Call report (FFIEC)"}
    if key.startswith(FORMULA_PREFIX):
        f = (formulas or {}).get(key)
        if not f:
            return None
        fmt, dec = FORMULA_FORMATS.get(f.get("fmt", "Number"), ("number", 2))
        return {"key": key, "label": f"ƒ {f['name']}", "source": "formula",
                "format": fmt, "decimals": dec, "category": "Formula",
                "formula": f["expr"]}
    return None


def register(keys, formulas: dict | None = None) -> None:
    """Make dynamic keys resolvable through config.METRICS_BY_KEY (labels,
    formats, export headers) for this process. Idempotent."""
    from config import METRICS_BY_KEY
    for k in keys:
        if is_dynamic(k):
            d = metric_def(k, formulas)
            if d:
                METRICS_BY_KEY[k] = d


# ── Formulas ──────────────────────────────────────────────────────────────

_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult,
            ast.Div, ast.USub, ast.UAdd, ast.Constant, ast.Name, ast.Load)


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return f"{FORMULA_PREFIX}{s[:40]}" if s else ""


def formula_key(name: str, expr: str, fmt: str = "Number") -> str:
    """Stable key for a formula DEFINITION: name slug + a hash of the
    normalised expression and display format. Two sessions with a same-name
    but different formula get different keys, so the process-wide metric
    registry (register) can never show one session's label/format/export
    header on the other's numbers; "CD %" vs "CD" no longer collide either."""
    base = slug(name)
    if not base:
        return ""
    norm = "".join((expr or "").upper().split())      # spacing is not identity
    h = hashlib.sha1(f"{norm}|{fmt}".encode("utf-8")).hexdigest()[:8]
    return f"{base}_{h}"


def parse_formula(expr: str) -> tuple[ast.Expression, list[str]]:
    """Validate a formula; returns (tree, field codes used). Raises ValueError
    with a user-facing message. Only + − × ÷, parentheses, numbers and field
    codes are allowed — no calls, attributes or names outside the catalog."""
    text = (expr or "").strip()
    if not text:
        raise ValueError("Enter a formula, e.g. (CD3LES + CD3LESS) / DEPDOM * 100")
    if len(text) > _FORMULA_MAX_CHARS:
        raise ValueError(f"Formula too long (max {_FORMULA_MAX_CHARS} characters).")
    try:
        tree = ast.parse(text.upper(), mode="eval")
    except (SyntaxError, RecursionError, MemoryError, ValueError):
        raise ValueError("Formula syntax error — use field codes, numbers, + − * / and ( ).")
    nodes = list(ast.walk(tree))
    if len(nodes) > _FORMULA_MAX_NODES:
        raise ValueError("Formula too complex — split it into smaller formulas.")
    names = []
    for node in nodes:
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"Not allowed in a formula: {type(node).__name__}.")
        if isinstance(node, ast.Constant):
            v = node.value
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("Only numbers and field codes are allowed.")
            if abs(v) > _FORMULA_MAX_CONST or (isinstance(v, float) and not math.isfinite(v)):
                raise ValueError("Numbers in a formula must be finite and at most 1e12.")
        if isinstance(node, ast.Name):
            if node.id not in catalog() and node.id not in ffiec_catalog():
                raise ValueError(f"Unknown call report field: {node.id}.")
            names.append((node.col_offset, node.id))
    codes = list(dict.fromkeys(n for _, n in sorted(names)))   # source order
    if not codes:
        raise ValueError("A formula must use at least one call report field.")
    return tree, codes


def eval_formula(tree: ast.Expression, vals: dict[str, float | None]) -> float | None:
    """Evaluate on resolved values. Any missing input, or a zero divisor, is
    n/a — never a guessed 0."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.Name):
            v = vals.get(n.id)
            if v is None:
                raise _Missing
            return v
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        a, b = ev(n.left), ev(n.right)
        if isinstance(n.op, ast.Add):
            return a + b
        if isinstance(n.op, ast.Sub):
            return a - b
        if isinstance(n.op, ast.Mult):
            return a * b
        if b == 0:
            raise _Missing
        return a / b
    try:
        out = ev(tree)
    except (_Missing, OverflowError, RecursionError, ZeroDivisionError):
        return None
    # inf/nan would render "$infT" and sort unstably — not a number to show.
    return out if math.isfinite(out) else None


class _Missing(Exception):
    pass


# ── Values ────────────────────────────────────────────────────────────────

def latest_repdte() -> str | None:
    """Newest quarter FDIC has published (YYYYMMDD), cached 6h."""
    from data import cache
    from data.http import get_with_retry
    from data.fdic_client import FDIC_FINANCIALS_URL
    try:
        hit = cache.get("fdic_latest_repdte:v1", max_age_s=_LATEST_TTL_S)
        if hit and hit.get("repdte"):
            return hit["repdte"]
        r = get_with_retry(FDIC_FINANCIALS_URL, {
            "filters": "CERT:628", "fields": "REPDTE",
            "sort_by": "REPDTE", "sort_order": "DESC", "limit": 1})
        rows = r.json().get("data", []) if r is not None else []
        rep = str(rows[0]["data"]["REPDTE"]) if rows else None
        if rep:
            cache.put("fdic_latest_repdte:v1", {"repdte": rep})
        return rep
    except Exception as e:
        print(f"[call_report_fields] latest REPDTE failed: {type(e).__name__}")
        return None


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def fetch_quarter(codes, repdte: str) -> dict[str, dict[int, float | None]]:
    """{CODE: {cert: raw FDIC value}} for EVERY filer of quarter ``repdte``
    (YYYYMMDD). One request covers all codes not yet cached; per-code results
    are cached for a week."""
    from data import cache
    from data.http import get_with_retry
    from data.fdic_client import FDIC_FINANCIALS_URL
    codes = sorted({c for c in codes if c})
    out: dict[str, dict[int, float | None]] = {}
    missing = []
    for c in codes:
        hit = cache.get(f"fdic_field:v1:{repdte}:{c}", max_age_s=_VALUES_TTL_S)
        if hit is not None and isinstance(hit.get("v"), dict):
            out[c] = {int(k): v for k, v in hit["v"].items()}
        else:
            missing.append(c)
    for i in range(0, len(missing), 40):
        chunk = missing[i:i + 40]
        got = {c: {} for c in chunk}
        offset = 0
        while True:
            try:
                r = get_with_retry(FDIC_FINANCIALS_URL, {
                    "filters": f"REPDTE:{repdte}", "fields": "CERT," + ",".join(chunk),
                    "limit": 10000, "offset": offset, "sort_by": "CERT",
                    "sort_order": "ASC"}, timeout=60)
                rows = r.json().get("data", []) if r is not None else None
            except Exception as e:     # 5xx / timeout / bad JSON: n/a, never a crash
                print(f"[call_report_fields] FDIC fetch failed: {type(e).__name__}")
                rows = None
            if rows is None:           # failed or rate-limited: leave uncached
                _ERRORS.append(f"FDIC unavailable for {', '.join(chunk[:3])}"
                               f"{'…' if len(chunk) > 3 else ''}")
                got = None
                break
            for row in rows:
                d = row.get("data", {})
                cert = d.get("CERT")
                if cert is None:
                    continue
                for c in chunk:
                    got[c][int(cert)] = _num(d.get(c))
            if len(rows) < 10000:
                break
            offset += 10000
        if got is None:
            continue
        for c, m in got.items():
            out[c] = m
            if m:                       # never cache an empty (unpublished) quarter
                cache.put(f"fdic_field:v1:{repdte}:{c}",
                          {"v": {str(k): v for k, v in m.items()}})
    return out


def _resolve(code: str, certs: list[int], table: dict) -> float | None:
    """One field for one bank (its charter group), in DISPLAY units."""
    from data.cert_group import _EXACT_QUOTIENTS
    kind = _kind(code)
    col = table.get(code, {})
    if len(certs) == 1:
        cert = certs[0]
        v = col.get(cert)
        if v is not None and kind == "ratio":
            # The SAME rules the metrics pipeline applies to every record:
            # FDIC's literal-0 ratio over a zero/absent denominator is n/a.
            from data.fdic_client import null_undefined_quotients, null_unreported_capital
            rec = {k: m.get(cert) for k, m in table.items() if cert in m}
            v = null_undefined_quotients(null_unreported_capital(rec)).get(code)
        return None if v is None else (v * 1000 if kind == "level" else v)
    if code in _NON_ADDITIVE:
        return None
    if kind in ("level", "count"):      # both are summable across charters
        vals = [col.get(c) for c in certs]
        if any(v is None for v in vals):
            return None
        return sum(vals) * (1000 if kind == "level" else 1)
    if kind == "ratio" and code in _EXACT_QUOTIENTS:
        num_k, den_k, scale = _EXACT_QUOTIENTS[code]
        nums = [table.get(num_k, {}).get(c) for c in certs]
        dens = [table.get(den_k, {}).get(c) for c in certs]
        if any(v is None for v in nums + dens) or sum(dens) <= 0:
            return None
        return sum(nums) / sum(dens) * scale
    return None     # count/asis/unproven ratio for a group: n/a, never a sum


def codes_needed(keys, formulas: dict | None = None) -> list[str]:
    """Every FDIC code the dynamic keys need, incl. exact-quotient parts."""
    from data.cert_group import _EXACT_QUOTIENTS
    codes: list[str] = []
    for k in keys:
        if k.startswith(FIELD_PREFIX):
            codes.append(k[len(FIELD_PREFIX):])
        elif k.startswith(FFIEC_PREFIX):
            codes.append(k[len(FFIEC_PREFIX):])
        elif k.startswith(FORMULA_PREFIX) and (formulas or {}).get(k):
            try:
                codes += parse_formula(formulas[k]["expr"])[1]
            except ValueError:
                pass
    for c in list(codes):
        if c in _EXACT_QUOTIENTS:
            codes += list(_EXACT_QUOTIENTS[c][:2])
    if any(catalog().get(c, {}).get("kind") == "ratio" for c in codes):
        from data.fdic_client import _DEN_ZERO_REPORTED_AS_ZERO
        codes += list(_NULL_GUARD_FIELDS)
        codes += [_EXACT_QUOTIENTS[r][1] for r in _DEN_ZERO_REPORTED_AS_ZERO
                  if r in _EXACT_QUOTIENTS]
    return sorted(set(codes))


def row_certs(row: dict) -> list[int]:
    """The FDIC charters behind a screener row: an as-of row's own cert (the
    point-in-time builder works per charter), else the ticker's whole charter
    group, largest first. [] → every dynamic value is n/a."""
    cert = row.get("_fdic_cert")
    if cert is not None:            # as-of rows are per-charter entities
        return [int(cert)]
    tk = row.get("ticker")
    if tk:
        from data.cert_group import get_cert_group
        try:
            g = get_cert_group(tk)
        except Exception:
            g = []
        if g:
            return [int(c) for c in g]
    return []


def values_for(keys, certs: list[int], table: dict,
               formulas: dict | None = None) -> dict[str, float | None]:
    """{key: display value} for one bank from a fetched quarter table."""
    out: dict[str, float | None] = {}
    codes_vals: dict[str, float | None] = {}

    def code_val(c):
        if c not in codes_vals:
            codes_vals[c] = _resolve(c, certs, table) if certs else None
        return codes_vals[c]

    for k in keys:
        if k.startswith(FIELD_PREFIX):
            out[k] = code_val(k[len(FIELD_PREFIX):])
        elif k.startswith(FFIEC_PREFIX):
            out[k] = code_val(k[len(FFIEC_PREFIX):])
        elif k.startswith(FORMULA_PREFIX) and (formulas or {}).get(k):
            try:
                tree, used = parse_formula(formulas[k]["expr"])
            except ValueError:
                out[k] = None
                continue
            out[k] = eval_formula(tree, {c: code_val(c) for c in used})
    return out


def quarter_table(codes, repdte: str, certs) -> dict[str, dict[int, float | None]]:
    """{CODE: {cert: raw value}} for one quarter (YYYYMMDD) from BOTH sources:
    FDIC financials (every filer) for FDIC codes, the FFIEC full-report store
    (the requested certs) for MDRM codes. FDIC wins a name clash."""
    codes = list(dict.fromkeys(codes))
    fdic_codes = [c for c in codes if c in catalog()]
    ffiec_codes = [c for c in codes if c not in catalog()]
    table = fetch_quarter(fdic_codes, repdte) if fdic_codes else {}
    if ffiec_codes and certs:
        try:
            from data import call_report_full
            iso = (f"{repdte[:4]}-{repdte[4:6]}-{repdte[6:8]}"
                   if len(str(repdte)) == 8 and str(repdte).isdigit() else repdte)
            got = call_report_full.values(ffiec_codes, iso, list(certs))
        except Exception as e:
            print(f"[call_report_fields] FFIEC values unavailable: {type(e).__name__}")
            _ERRORS.append("FFIEC store unavailable")
            got = {}
        for c in ffiec_codes:
            table[c] = {int(cert): vals.get(c) for cert, vals in got.items()}
    return table


def attach(rows: list[dict], keys, repdte: str | None,
           formulas: dict | None = None) -> list[dict]:
    """Copies of ``rows`` with every dynamic key's value for quarter
    ``repdte`` (YYYYMMDD) attached. Rows are copied — the cached universe
    snapshot is never mutated. No quarter → every dynamic value is n/a."""
    _ERRORS.clear()
    keys = [k for k in dict.fromkeys(keys) if is_dynamic(k)]
    if not keys:
        return list(rows)
    certs_of = [row_certs(r) for r in rows]
    all_certs = sorted({c for cs in certs_of for c in cs})
    table = quarter_table(codes_needed(keys, formulas), repdte, all_certs) if repdte else {}
    out = []
    for r, certs in zip(rows, certs_of):
        r2 = dict(r)
        r2.update(values_for(keys, certs, table, formulas))
        out.append(r2)
    return out


def series_for(row: dict, keys, repdtes: list[str],
               formulas: dict | None = None,
               tables: dict | None = None) -> dict[str, list]:
    """{key: [v_latest, v_q-1, …]} over ``repdtes`` (newest first) for the
    screen engine's change/trend primitives. ``tables`` ({repdte: quarter
    table}) lets a caller fetch each quarter ONCE for every bank instead of
    re-reading it per bank."""
    keys = [k for k in keys if is_dynamic(k)]
    certs = row_certs(row)
    codes = codes_needed(keys, formulas)
    out = {k: [] for k in keys}
    for rep in repdtes:
        table = (tables or {}).get(rep)
        if table is None:
            table = quarter_table(codes, rep, certs)
        vals = values_for(keys, certs, table, formulas)
        for k in keys:
            out[k].append(vals.get(k))
    return out
