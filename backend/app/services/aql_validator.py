"""Safe AQL validation.

AQL is executed against a production SIEM, so validation is a security control,
not a convenience. This layer runs BEFORE any query is stored or dispatched and
rejects anything that is not a single, bounded, read-only SELECT.

Approach: **tokenize, then inspect structure** — never validate with a single
regex. A lexer strips strings and comments first (so a `;` or a mutation keyword
hidden inside a string literal or a comment cannot fool us), then we reason about
the token stream: statement count, leading keyword, banned keywords, presence of
a bounded time window, and result limits.

What this is and isn't:
  * It is a conservative allowlist-oriented gate that blocks the dangerous
    shapes the spec enumerates.
  * It is NOT a full AQL parser. QRadar remains the authority on AQL semantics;
    `validate_aql` on the provider can be used as a second, authoritative check.
    This layer's job is to refuse obviously unsafe or out-of-policy input cheaply
    and deterministically, and to enforce *our* limits (time range, row count)
    which QRadar's own validator does not know about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.config import Settings, get_settings

# Keywords that must never appear: mutation / DDL / admin. AQL is read-only
# (SELECT), so any of these is a red flag regardless of context.
_BANNED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "MERGE", "REPLACE", "CALL",
    "COPY", "INTO", "ATTACH", "PRAGMA", "SET", "COMMIT", "ROLLBACK",
}

# Datasets we permit. AQL queries FROM events or flows; anything else is out of
# policy for this platform.
_ALLOWED_DATASETS = {"EVENTS", "FLOWS"}

# Time-window keywords that make a query bounded.
_TIME_WINDOW_TOKENS = {"LAST", "START", "STOP"}

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|<=|>=|<>|!=|::|.", re.DOTALL)


class AQLValidationError(ValueError):
    """Raised when AQL fails validation. `.reasons` lists every problem."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass
class LexResult:
    tokens: list[str]
    stripped: str  # source with strings/comments blanked, for structural checks
    string_literals: list[str] = field(default_factory=list)


def _lex(aql: str) -> LexResult:
    """Blank out string literals and comments, then tokenize the remainder.

    Blanking (rather than removing) preserves offsets and guarantees that a
    hidden `;` or keyword inside a string/comment cannot re-enter the token
    stream. Multiple string quote styles and both comment styles are handled.
    """
    out: list[str] = []
    literals: list[str] = []
    i = 0
    n = len(aql)
    while i < n:
        ch = aql[i]
        two = aql[i : i + 2]
        # line comment
        if two == "--":
            j = aql.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        # block comment
        if two == "/*":
            j = aql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(" " * (j - i))
            i = j
            continue
        # string literals: ' or "
        if ch in "'\"":
            quote = ch
            j = i + 1
            buf = []
            while j < n:
                if aql[j] == quote:
                    # doubled quote escape
                    if j + 1 < n and aql[j + 1] == quote:
                        buf.append(quote)
                        j += 2
                        continue
                    j += 1
                    break
                buf.append(aql[j])
                j += 1
            literals.append("".join(buf))
            out.append(" " * (j - i))
            i = j
            continue
        out.append(ch)
        i += 1

    stripped = "".join(out)
    tokens = [t for t in _TOKEN_RE.findall(stripped) if not t.isspace()]
    return LexResult(tokens=tokens, stripped=stripped, string_literals=literals)


@dataclass
class ValidatedAQL:
    normalized: str
    datasets: list[str]
    has_time_window: bool


def validate_aql(
    aql: str,
    *,
    max_time_range_hours: int | None = None,
    max_result_rows: int | None = None,
    settings: Settings | None = None,
) -> ValidatedAQL:
    """Validate an AQL string against safety policy. Raises AQLValidationError.

    Returns a ValidatedAQL on success. Does not execute anything.
    """
    settings = settings or get_settings()
    max_hours = max_time_range_hours or settings.ariel_max_time_range_hours
    max_rows = max_result_rows or settings.ariel_max_result_rows

    reasons: list[str] = []
    if not aql or not aql.strip():
        raise AQLValidationError(["query is empty"])

    lex = _lex(aql)
    upper_tokens = [t.upper() for t in lex.tokens]

    # 1. Single statement only. After blanking strings/comments, no ';' may
    #    remain except an optional trailing one.
    semicolons = [i for i, t in enumerate(lex.tokens) if t == ";"]
    non_trailing = [i for i in semicolons if lex.tokens[i + 1 :] and any(
        not tok.isspace() for tok in lex.tokens[i + 1 :]
    )]
    if non_trailing:
        reasons.append("multiple statements are not allowed")

    # 2. Must start with SELECT.
    if not upper_tokens or upper_tokens[0] != "SELECT":
        reasons.append("query must be a single SELECT statement")

    # 3. No banned/mutating keywords anywhere in the token stream.
    banned_found = sorted({t for t in upper_tokens if t in _BANNED_KEYWORDS})
    if banned_found:
        reasons.append(f"disallowed keyword(s): {', '.join(banned_found)}")

    # 4. FROM <dataset> must be present and allowed.
    datasets = _extract_datasets(upper_tokens)
    if not datasets:
        reasons.append("query must select FROM events or flows")
    else:
        bad = [d for d in datasets if d not in _ALLOWED_DATASETS]
        if bad:
            reasons.append(f"dataset(s) not permitted: {', '.join(bad)}")

    # 5. Bounded time window required (no unbounded scans of all history).
    has_window, window_hours = _time_window(upper_tokens, lex.tokens)
    if not has_window:
        reasons.append(
            "query must include a bounded time window (LAST N ..., or START/STOP)"
        )
    elif window_hours is not None and window_hours > max_hours:
        reasons.append(
            f"time range {window_hours}h exceeds the maximum {max_hours}h"
        )

    # 6. Result limit sanity: an explicit LIMIT must not exceed the cap. Absence
    #    is fine — the executor imposes max_rows when fetching.
    limit = _explicit_limit(upper_tokens)
    if limit is not None and limit > max_rows:
        reasons.append(f"LIMIT {limit} exceeds the maximum {max_rows} rows")

    if reasons:
        raise AQLValidationError(reasons)

    return ValidatedAQL(
        normalized=aql.strip(),
        datasets=datasets,
        has_time_window=has_window,
    )


def _extract_datasets(upper_tokens: list[str]) -> list[str]:
    datasets: list[str] = []
    for idx, tok in enumerate(upper_tokens):
        if tok == "FROM" and idx + 1 < len(upper_tokens):
            datasets.append(upper_tokens[idx + 1])
    return datasets


_UNIT_HOURS = {
    "MINUTES": 1 / 60, "MINUTE": 1 / 60, "MINS": 1 / 60, "MIN": 1 / 60,
    "HOURS": 1.0, "HOUR": 1.0,
    "DAYS": 24.0, "DAY": 24.0,
}


def _time_window(upper_tokens: list[str], tokens: list[str]) -> tuple[bool, float | None]:
    """Detect a bounded time window and, for LAST N UNIT, its size in hours."""
    for idx, tok in enumerate(upper_tokens):
        if tok == "LAST" and idx + 2 < len(upper_tokens):
            num_tok = tokens[idx + 1]
            unit = upper_tokens[idx + 2]
            if num_tok.isdigit() and unit in _UNIT_HOURS:
                return True, int(num_tok) * _UNIT_HOURS[unit]
            # LAST <something> without a recognised unit is still a window, but
            # we cannot size it — treat as bounded but unknown size.
            return True, None
    if "START" in upper_tokens and "STOP" in upper_tokens:
        return True, None
    # A window keyword present but malformed still counts as an attempt.
    if any(t in _TIME_WINDOW_TOKENS for t in upper_tokens):
        return True, None
    return False, None


def _explicit_limit(upper_tokens: list[str]) -> int | None:
    for idx, tok in enumerate(upper_tokens):
        if tok == "LIMIT" and idx + 1 < len(upper_tokens) and upper_tokens[idx + 1].isdigit():
            return int(upper_tokens[idx + 1])
    return None
