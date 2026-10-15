"""Match a document to a member from its text, and link files to members by hand.

Rules, in order:
1. An explicit member ID (M-123) that exists in members wins.
2. Otherwise the name and date of birth found in the text must match exactly one member (first and last name,
   case-insensitive, and the same birth date).
3. Anything else needs a person: the result lists candidate members with the reason each one is offered.
"""

import re
from dataclasses import dataclass, field
from datetime import date

import psycopg

from .db import audit

MATCHED, NEEDS_MATCH = "matched", "needs_match"
MAX_CANDIDATES = 10

_MEMBER_ID = re.compile(r"\bM-\d{3,}\b", re.I)
_NAME_LABEL = re.compile(r"(?im)\b(?:patient(?:[ \t]+name)?|member(?:[ \t]+name)?|name|re)[ \t]*:[ \t]*([^\r\n]+)")
_NAME_END = re.compile(r"(?i)\s{2,}|\t|\||;|\bDOB\b|\bdate of birth\b|\bmember id\b|\bID\b|\bMRN\b|\bM-\d")
_NAME_TOKEN = re.compile(r"^[A-Za-z][A-Za-z'.\-]*$")
_DOB = re.compile(
    r"(?i)(?:\bDOB\b|\bD\.O\.B\.?|date[ \t]+of[ \t]+birth|birth[ \t]*date|\bborn\b)[ \t]*[:\-]?[ \t]*"
    r"(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})")


@dataclass
class MatchResult:
    """Outcome of matching text to a member."""
    member_id: str | None
    status: str                                   # matched | needs_match
    candidates: list[dict] = field(default_factory=list)   # {member_id, name, birth_date, reason}
    method: str | None = None                     # member_id | name_dob when matched


@dataclass(frozen=True)
class _Member:
    member_id: str
    first: str
    last: str
    birth_date: date

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"


def match_member(conn: psycopg.Connection, text: str) -> MatchResult:
    """Find the member a document belongs to. Returns matched with a member_id, or needs_match with candidates."""
    members = [_Member(r[0], r[1], r[2], r[3]) for r in conn.execute(
        "SELECT member_id, first_name, last_name, birth_date FROM members").fetchall()]
    by_id = {m.member_id: m for m in members}

    ids = list(dict.fromkeys(m.group(0).upper() for m in _MEMBER_ID.finditer(text)))
    known = [by_id[i] for i in ids if i in by_id]
    if len(known) == 1:
        return MatchResult(known[0].member_id, MATCHED, [], "member_id")
    if len(known) > 1:
        return MatchResult(None, NEEDS_MATCH,
                           [_candidate(m, "member ID appears in the text, but so do other member IDs")
                            for m in known[:MAX_CANDIDATES]])

    names = _extract_names(text) or _names_found_in_text(text, members)
    dobs = _extract_birth_dates(text)

    exact = [m for m in members for first, last in names
             if m.first.lower() == first.lower() and m.last.lower() == last.lower() and m.birth_date in dobs]
    exact = list({m.member_id: m for m in exact}.values())
    if len(exact) == 1:
        return MatchResult(exact[0].member_id, MATCHED, [], "name_dob")
    return MatchResult(None, NEEDS_MATCH, _candidates(members, names, dobs, exact))


def link_file(conn: psycopg.Connection, source_file_id: int, member_id: str, actor: str) -> None:
    """Manually link a file to a member (from the match queue). Raises LookupError for an unknown file or member."""
    row = conn.execute("SELECT member_id, match_status FROM source_files WHERE id = %s", (source_file_id,)).fetchone()
    if row is None:
        raise LookupError(f"file {source_file_id} not found")
    if conn.execute("SELECT 1 FROM members WHERE member_id = %s", (member_id,)).fetchone() is None:
        raise LookupError(f"member {member_id} not found")
    conn.execute("UPDATE source_files SET member_id = %s, match_status = 'matched' WHERE id = %s",
                 (member_id, source_file_id))
    conn.execute("UPDATE documents SET member_id = %s WHERE source_file_id = %s", (member_id, source_file_id))
    audit(conn, "source_file", str(source_file_id), "file.member_linked",
          {"member_id": member_id, "previous_member_id": row[0], "previous_match_status": row[1], "manual": True},
          actor=actor)


def _candidate(member: _Member, reason: str) -> dict:
    return {"member_id": member.member_id, "name": member.name, "birth_date": member.birth_date.isoformat(),
            "reason": reason}


def _candidates(members: list[_Member], names: list[tuple[str, str]], dobs: set[date], exact: list[_Member]) -> list[dict]:
    """Members worth showing to a person: same name, same last name (first initial first) or same birth date."""
    reasons: dict[str, list[str]] = {}
    rank: dict[str, int] = {}

    def offer(m: _Member, reason: str, score: int) -> None:
        reasons.setdefault(m.member_id, [])
        if reason not in reasons[m.member_id]:
            reasons[m.member_id].append(reason)
        rank[m.member_id] = min(rank.get(m.member_id, 9), score)

    for m in members:
        for first, last in names:
            if m.last.lower() != last.lower():
                continue
            if m.first.lower() == first.lower():
                if m.birth_date in dobs:
                    offer(m, "name and date of birth match, but more than one member fits", 0)
                else:
                    offer(m, "name matches, date of birth missing or different", 1)
            elif len(first.rstrip(".")) == 1 and m.first.lower().startswith(first.rstrip(".").lower()):
                offer(m, "last name and first initial match", 2)
            else:
                offer(m, "same last name", 3)
        if m.birth_date in dobs and m.member_id not in reasons:
            offer(m, "same date of birth", 4)
    ordered = sorted(reasons, key=lambda mid: (rank[mid], mid))[:MAX_CANDIDATES]
    by_id = {m.member_id: m for m in members}
    return [_candidate(by_id[mid], "; ".join(reasons[mid])) for mid in ordered]


def _extract_names(text: str) -> list[tuple[str, str]]:
    """Names written after a label such as 'Patient:', 'Name:' or 'Re:'. Returns (first, last) pairs."""
    found: list[tuple[str, str]] = []
    for m in _NAME_LABEL.finditer(text):
        raw = m.group(1).strip()
        end = _NAME_END.search(raw)
        if end:
            raw = raw[:end.start()]
        name = _split_name(raw.strip(" ,"))
        if name and name not in found:
            found.append(name)
    return found


def _split_name(raw: str) -> tuple[str, str] | None:
    if "," in raw:
        last, _, rest = raw.partition(",")
        parts = [last.strip(), rest.strip().split(" ")[0] if rest.strip() else ""]
        if all(parts) and all(_NAME_TOKEN.match(p) for p in parts):
            return parts[1], parts[0]
        raw = last
    tokens = raw.split()
    if not 2 <= len(tokens) <= 4 or not all(_NAME_TOKEN.match(t) for t in tokens):
        return None
    return tokens[0], tokens[-1]


def _names_found_in_text(text: str, members: list[_Member]) -> list[tuple[str, str]]:
    """Fallback when no label gave a name: members whose full name appears as words in the text."""
    lowered = re.sub(r"\s+", " ", text.lower())
    found: list[tuple[str, str]] = []
    for m in members:
        first, last = re.escape(m.first.lower()), re.escape(m.last.lower())
        if re.search(rf"\b(?:{first} {last}|{last}, ?{first})\b", lowered):
            pair = (m.first, m.last)
            if pair not in found:
                found.append(pair)
    return found


def _extract_birth_dates(text: str) -> set[date]:
    dates: set[date] = set()
    for m in _DOB.finditer(text):
        parsed = _parse_date(m.group(1))
        if parsed:
            dates.add(parsed)
    return dates


def _parse_date(raw: str) -> date | None:
    try:
        if "-" in raw:
            y, mo, d = (int(p) for p in raw.split("-"))
            return date(y, mo, d)
        a, b, y = (int(p) for p in raw.split("/"))
        if y < 100:
            y += 1900 if y > 30 else 2000
        mo, d = (a, b) if a <= 12 else (b, a)  # US order; swap when the first number cannot be a month
        return date(y, mo, d)
    except ValueError:
        return None
