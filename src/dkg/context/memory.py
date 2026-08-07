"""Persist an answered question and its answer as a re-ingestible document.

The loop this closes. A question gets asked, the graph answers it, and the
answer is thrown away. The next person asks the same question and pays for it
again. Worse, nobody can tell later what the system said or what it said it from.

An answered question is written here as an ordinary Markdown document with a
provenance header, in the DKG home under ``memory/``. Because it is an ordinary
document it can be ingested by the ordinary document path, which means an answer
becomes searchable alongside the sources it was derived from, and a later
question can find it.

Three things this deliberately does NOT do.

It does not present a remembered answer as current. Every record carries the
time it was written and the graph revision it was derived from, and a reader is
told plainly that the code may have changed since. A cached answer offered as
fact is worse than no cache.

It does not merge or summarise. Each answer is one document. Summarising several
answers into one would create a claim nobody made.

It does not write into the graph directly. The document is written to disk and
ingested through the same path any other document takes, so it carries the same
provenance envelope and the same evidence rules. A back door that inserted
entities without provenance would make the ledger incomplete.

Answers are addressed by a content hash of the question, so re-answering the
same question supersedes rather than accumulating near-duplicates.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MEMORY_DIR_NAME = "memory"

# Recorded in every document so a reader knows what kind of thing they have.
RECORD_KIND = "answered-question"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class AnsweredQuestion:
    """One question, its answer, and where the answer came from."""

    question: str
    answer: str
    # Canonical names the answer rests on, so a reader can check it against the
    # graph rather than having to trust it.
    sources: list[str] = field(default_factory=list)
    # The tool or route that produced it (for example "dkg.code.impact").
    method: str = ""
    # Anything the caller wants preserved: depth, budget, detector, and so on.
    parameters: dict = field(default_factory=dict)
    # The graph revision the answer was derived from, when known.
    graph_revision: str = ""

    def question_id(self) -> str:
        """Stable identity for a question, so re-answering supersedes."""
        normalised = " ".join(self.question.split()).casefold()
        return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def memory_dir(home: Path | str) -> Path:
    return Path(home) / MEMORY_DIR_NAME


def _slug(text: str, limit: int = 60) -> str:
    slug = _SLUG_RE.sub("-", text.casefold()).strip("-")
    return (slug[:limit].rstrip("-")) or "question"


def render(record: AnsweredQuestion, *, written_at: str) -> str:
    """The document body. Markdown, so it reads as well as it ingests."""
    lines = [
        f"# {record.question.strip()}",
        "",
        "> This is a RECORDED ANSWER, not a live one. It was true of the graph at",
        f"> the revision below, on {written_at}. The code may have changed since;",
        "> re-run the query rather than relying on this if that matters.",
        "",
        "## Answer",
        "",
        record.answer.strip(),
        "",
        "## Provenance",
        "",
        f"- Kind: {RECORD_KIND}",
        f"- Question id: {record.question_id()}",
        f"- Written at: {written_at}",
        f"- Method: {record.method or 'unrecorded'}",
        f"- Graph revision: {record.graph_revision or 'unrecorded'}",
    ]
    if record.parameters:
        lines.append(f"- Parameters: `{json.dumps(record.parameters, sort_keys=True)}`")
    if record.sources:
        lines += ["", "## Derived from", ""]
        lines += [f"- `{s}`" for s in sorted(record.sources)]
    lines += [
        "",
        "## Limits",
        "",
        "- The code graph this was derived from is structural and its reference",
        "  resolution is name-based, so anything here inherited that",
        "  over-approximation.",
        "- Nothing here was summarised across answers. It is one question and the",
        "  one answer given to it.",
        "",
    ]
    return "\n".join(lines)


def write_answer(
    home: Path | str,
    record: AnsweredQuestion,
    *,
    now: datetime | None = None,
) -> Path:
    """Write one answered question as a document and return its path.

    Deterministic in its naming: the same question always writes the same file,
    so re-answering supersedes the previous answer rather than leaving two
    documents that disagree and no way to tell which is current.
    """
    if not record.question.strip():
        from ..core.errors import ValidationError

        raise ValidationError("an answered question needs a question")
    if not record.answer.strip():
        from ..core.errors import ValidationError

        raise ValidationError("an answered question needs an answer")

    stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    directory = memory_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.question_id()}-{_slug(record.question)}.md"
    path.write_text(render(record, written_at=stamp), encoding="utf-8")
    return path


def list_answers(home: Path | str) -> list[Path]:
    directory = memory_dir(home)
    if not directory.exists():
        return []
    return sorted(directory.glob("*.md"))


def ingest_answers(db, home: Path | str, *, tenant_id: str = "local") -> dict:
    """Ingest every recorded answer through the ordinary document path.

    Ordinary on purpose: the answers get the same provenance envelope, chunking,
    and search indexing any other document gets. Nothing is written into the
    graph by a side channel, so the evidence ledger stays complete.
    """
    from ..ingest.base import ingest_path

    paths = list_answers(home)
    ingested: list[str] = []
    failed: list[dict] = []
    for path in paths:
        try:
            # The same entry point `dkg ingest` uses. Nothing bespoke: an answer
            # is a Markdown document and is read as one.
            report = ingest_path(db, path, tenant_id=tenant_id)
            if report.get("documents_added"):
                ingested.append(path.name)
            else:
                # Already present is not a failure, but it is not an ingest
                # either, and calling it one would overstate what happened.
                failed.append({"file": path.name, "error": "no document was added"})
        except Exception as e:
            # Reported, never swallowed: an answer that failed to ingest is
            # missing from search, and pretending otherwise would make the
            # memory loop quietly lossy.
            failed.append({"file": path.name, "error": repr(e)})
    return {
        "ingested": ingested,
        "failed": failed,
        "total": len(paths),
        "why": (
            "Recorded answers are ingested as ordinary documents so they carry the "
            "same provenance and evidence rules as any other source. Each is "
            "labelled a recorded answer with the time and graph revision it was "
            "derived from; none is presented as a live result."
        ),
    }
