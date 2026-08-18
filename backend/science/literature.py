"""Scientific literature access, and the machinery for proving a citation is real.

**Why this module exists.** A language model asked for references produces citations that look
exactly like real ones: plausible authors, a plausible title, a well-formed identifier, a
plausible year. Nothing in the text distinguishes a fabricated citation from a genuine one.
That is the same failure this project addresses for numbers -- a remembered figure and a
computed one are indistinguishable once printed -- so it gets the same treatment: the value is
worthless without provenance, and provenance means something checked.

So the rule here is narrow and absolute: **a citation is only ever reported if this module
fetched it.** The agent may not cite from memory. Anything it produces unprompted is checked
against the source of record and marked unverified if it does not resolve.

**Source.** arXiv's public API. Chosen because it needs no account, no key and no quota
negotiation, it covers astrophysics and space engineering densely, and every record has a
stable, checkable identifier. It is a preprint server, which matters for tiering: presence on
arXiv means a paper *exists*, not that it is peer-reviewed or correct. That distinction is
carried into every value this module returns rather than glossed over.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest
from xml.etree import ElementTree

from science.provenance import Receipt, Tier, Value

ARXIV_API_URL = "http://export.arxiv.org/api/query"

REQUEST_TIMEOUT_S = 20
REQUEST_ATTEMPTS = 3

# arXiv asks for no more than one request every three seconds. Respected rather than raced: a
# research tool that gets itself blocked is worse than a slow one.
MIN_REQUEST_INTERVAL_S = 3.0

# Cached for a day. The literature does not change minute to minute, and repeated identical
# searches within one agent session are common.
CACHE_TTL_S = 86_400

MAX_RESULTS_CAP = 25

USER_AGENT = "helios/0.1 (orbital mechanics research tool)"

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_SCHEMA = "{http://arxiv.org/schemas/atom}"

# arXiv identifiers come in two eras: "2401.12345" (April 2007 onward, optionally versioned) and
# "astro-ph/0601001" (the original scheme). Both must be accepted, because a model asked for
# older literature will produce the old form and it is perfectly valid.
MODERN_ID = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$")
LEGACY_ID = re.compile(r"^([a-z-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$")


class LiteratureError(RuntimeError):
    """Raised when the literature service cannot be reached or returns nonsense."""


@dataclass(frozen=True)
class Paper:
    """One record as published, with nothing inferred.

    Attributes:
        arxiv_id: Bare identifier, version suffix stripped.
        title: Title as published.
        authors: Author names in listed order.
        summary: Abstract as published.
        published: ISO date of first submission.
        updated: ISO date of the most recent revision.
        categories: arXiv subject categories.
        doi: Journal DOI, when the record carries one.
        journal_ref: Journal reference, when published beyond the preprint.
    """

    arxiv_id: str
    title: str
    authors: list[str]
    summary: str
    published: str
    updated: str
    categories: list[str] = field(default_factory=list)
    doi: str | None = None
    journal_ref: str | None = None

    @property
    def url(self) -> str:
        """Canonical abstract page for this record."""
        return f"https://arxiv.org/abs/{self.arxiv_id}"

    @property
    def peer_reviewed(self) -> bool:
        """Whether the record shows evidence of publication beyond the preprint.

        A DOI or journal reference is evidence of a published version. Its absence is *not*
        evidence of absence -- arXiv metadata is often not updated after acceptance -- so this
        is reported as a positive signal only, never as "not peer reviewed".
        """
        return bool(self.doi or self.journal_ref)


_cache: dict[str, tuple[float, list[Paper]]] = {}
_last_request_at = 0.0


def normalise_id(raw: str) -> str | None:
    """Reduce an arXiv identifier to its bare, canonical form.

    Accepts the forms a model actually emits -- full URLs, an ``arXiv:`` prefix, a trailing
    version, trailing punctuation from prose -- because rejecting a real identifier over
    formatting would brand a genuine citation as fabricated, the more damaging of the two
    errors.

    Args:
        raw: Identifier in any common form.

    Returns:
        The bare identifier, or None if it is not a well-formed arXiv id.
    """
    text = raw.strip()

    text = text.strip("()[]{} ")

    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/", "arxiv.org/abs/"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break

    if text.lower().startswith("arxiv:"):
        text = text[len("arxiv:") :]

    # Strip prose punctuation from both ends. A model writing "(arXiv:2401.12345)." emits a
    # perfectly real identifier wrapped in sentence furniture; rejecting it would mark a genuine
    # citation as fabricated, which is the more damaging of the two possible errors.
    text = text.strip().strip("()[]{}.,;:'\"")

    for pattern in (MODERN_ID, LEGACY_ID):
        match = pattern.match(text)
        if match:
            return match.group(1)

    return None


def _throttle() -> None:
    """Wait, if needed, to honour the requested request interval."""
    global _last_request_at

    elapsed = time.monotonic() - _last_request_at
    if elapsed < MIN_REQUEST_INTERVAL_S:
        time.sleep(MIN_REQUEST_INTERVAL_S - elapsed)

    _last_request_at = time.monotonic()


def _fetch(params: dict[str, str | int]) -> str:
    """Query the arXiv API with timeout, throttling, and retry.

    Args:
        params: Query parameters.

    Returns:
        The Atom response body.

    Raises:
        LiteratureError: If every attempt fails.
    """
    url = f"{ARXIV_API_URL}?{urlparse.urlencode(params)}"
    last_error: Exception | None = None

    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        _throttle()
        try:
            request = urlrequest.Request(url, headers={"User-Agent": USER_AGENT})
            with urlrequest.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                return response.read().decode("utf-8", errors="replace")

        except urlerror.HTTPError as exc:
            # As in the catalog client: a 4xx describes the request and will not improve on
            # retry. Only server-side and transport faults are worth another attempt.
            if 400 <= exc.code < 500:
                raise LiteratureError(f"arXiv rejected the query (HTTP {exc.code}).") from exc
            last_error = exc

        except (urlerror.URLError, TimeoutError, OSError) as exc:
            last_error = exc

        if attempt < REQUEST_ATTEMPTS:
            time.sleep(1.0 * attempt)

    raise LiteratureError(
        f"Could not reach arXiv after {REQUEST_ATTEMPTS} attempts. ({last_error})"
    )


def _text(node: ElementTree.Element | None) -> str:
    """Collapse an element's text to a single clean line."""
    if node is None or node.text is None:
        return ""
    return " ".join(node.text.split())


def _parse_feed(body: str) -> list[Paper]:
    """Parse an arXiv Atom feed into records.

    Args:
        body: Atom XML.

    Returns:
        The records in the feed, in the order returned.

    Raises:
        LiteratureError: If the body is not parseable Atom.
    """
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise LiteratureError(f"arXiv returned a response that is not valid XML: {exc}") from exc

    papers: list[Paper] = []
    for entry in root.findall(f"{ATOM}entry"):
        arxiv_id = normalise_id(_text(entry.find(f"{ATOM}id")))
        if arxiv_id is None:
            # An entry without a usable identifier cannot be verified later, so it is dropped
            # rather than passed on as an uncheckable citation.
            continue

        papers.append(
            Paper(
                arxiv_id=arxiv_id,
                title=_text(entry.find(f"{ATOM}title")),
                authors=[
                    _text(author.find(f"{ATOM}name"))
                    for author in entry.findall(f"{ATOM}author")
                ],
                summary=_text(entry.find(f"{ATOM}summary")),
                published=_text(entry.find(f"{ATOM}published")),
                updated=_text(entry.find(f"{ATOM}updated")),
                categories=[
                    category.attrib["term"]
                    for category in entry.findall(f"{ATOM}category")
                    if "term" in category.attrib
                ],
                doi=_text(entry.find(f"{ARXIV_SCHEMA}doi")) or None,
                journal_ref=_text(entry.find(f"{ARXIV_SCHEMA}journal_ref")) or None,
            )
        )

    return papers


def search(query: str, *, max_results: int = 8, use_cache: bool = True) -> list[Paper]:
    """Search the literature.

    Args:
        query: Free-text search terms.
        max_results: How many records to return, capped at `MAX_RESULTS_CAP`.
        use_cache: Whether a cached result may be served.

    Returns:
        Matching records, most relevant first.

    Raises:
        ValueError: If the query is empty.
        LiteratureError: If arXiv cannot be reached.
    """
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("query must not be empty")

    limit = max(1, min(max_results, MAX_RESULTS_CAP))
    key = f"search:{cleaned}:{limit}"

    if use_cache and key in _cache:
        stored_at, papers = _cache[key]
        if time.time() - stored_at < CACHE_TTL_S:
            return papers

    papers = _parse_feed(
        _fetch(
            {
                "search_query": f"all:{cleaned}",
                "start": 0,
                "max_results": limit,
                "sortBy": "relevance",
                "sortOrder": "descending",
            }
        )
    )

    _cache[key] = (time.time(), papers)
    return papers


def fetch_by_id(arxiv_id: str, *, use_cache: bool = True) -> Paper | None:
    """Look up one record by identifier.

    This is the verification path: it answers "does this citation exist", which is the only
    question that separates a real reference from a convincing invention.

    Args:
        arxiv_id: Identifier in any common form.
        use_cache: Whether a cached result may be served.

    Returns:
        The record, or None if the identifier is malformed or resolves to nothing.

    Raises:
        LiteratureError: If arXiv cannot be reached.
    """
    normalised = normalise_id(arxiv_id)
    if normalised is None:
        return None

    key = f"id:{normalised}"
    if use_cache and key in _cache:
        stored_at, papers = _cache[key]
        if time.time() - stored_at < CACHE_TTL_S:
            return papers[0] if papers else None

    # arXiv answers an unknown-but-well-formed id with an empty feed rather than an error, so an
    # empty list here means "no such record" and is cached as such.
    papers = _parse_feed(_fetch({"id_list": normalised, "max_results": 1}))

    _cache[key] = (time.time(), papers)
    return papers[0] if papers else None


def as_dict(paper: Paper) -> dict[str, object]:
    """Render a record for transport."""
    return {
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "authors": paper.authors,
        "summary": paper.summary,
        "published": paper.published,
        "updated": paper.updated,
        "categories": paper.categories,
        "doi": paper.doi,
        "journal_ref": paper.journal_ref,
        "url": paper.url,
        "peer_reviewed_signal": paper.peer_reviewed,
    }


def verify_citations(identifiers: list[str]) -> dict[str, object]:
    """Check claimed citations against the source of record.

    The point of this function is to make fabrication *visible*. A model that invents references
    produces identifiers that are well-formed and resolve to nothing, or that are not
    identifiers at all. Both outcomes are reported explicitly rather than quietly dropped,
    because "this citation does not exist" is the finding.

    Args:
        identifiers: Claimed arXiv identifiers.

    Returns:
        Per-identifier verdicts and a summary count.
    """
    results: list[dict[str, object]] = []

    for claimed in identifiers:
        normalised = normalise_id(claimed)

        if normalised is None:
            results.append(
                {
                    "claimed": claimed,
                    "status": "malformed",
                    "paper": None,
                    "note": "Not a well-formed arXiv identifier, so nothing can resolve it.",
                }
            )
            continue

        try:
            paper = fetch_by_id(normalised)
        except LiteratureError as exc:
            results.append(
                {
                    "claimed": claimed,
                    "status": "unchecked",
                    "paper": None,
                    # Kept distinct from "not found" deliberately: an unreachable service is not
                    # evidence against a citation, and reporting it as such would be its own
                    # kind of false confidence.
                    "note": f"Could not be checked: {exc}",
                }
            )
            continue

        if paper is None:
            results.append(
                {
                    "claimed": claimed,
                    "status": "not_found",
                    "paper": None,
                    "note": (
                        "Well-formed but resolves to no record. Treat as fabricated unless "
                        "shown otherwise."
                    ),
                }
            )
            continue

        results.append(
            {
                "claimed": claimed,
                "status": "verified",
                "paper": as_dict(paper),
                "note": None,
            }
        )

    verified = sum(1 for item in results if item["status"] == "verified")
    checkable = sum(1 for item in results if item["status"] != "unchecked")

    return {
        "results": results,
        "summary": {
            "claimed": len(identifiers),
            "verified": verified,
            "unresolved": checkable - verified,
            "unchecked": len(identifiers) - checkable,
        },
    }


def citation_value(paper: Paper) -> Value:
    """Wrap a record as a tiered value with its receipt.

    Tiering here is a deliberate, narrow claim. **Observed** applies to the record's existence
    and metadata: this paper was fetched from arXiv and says what it says. It emphatically does
    not extend to the paper's conclusions -- a preprint's claims are its authors', unreviewed
    unless the record shows otherwise, and inheriting `observed` onto them would let a citation
    launder an unverified assertion into a measured fact.

    Args:
        paper: The record to wrap.

    Returns:
        The citation as a value, tiered observed, with provenance.
    """
    return Value(
        value=f"{paper.title} ({paper.arxiv_id})",
        unit="none",
        tier=Tier.OBSERVED,
        receipt=Receipt(
            tool="literature_search",
            inputs={"arxiv_id": paper.arxiv_id},
            frame=None,
            time_scale=None,
            dataset={
                "source": "arxiv",
                "url": paper.url,
                "published": paper.published,
                "updated": paper.updated,
                "doi": paper.doi,
                "journal_ref": paper.journal_ref,
            },
            equation=None,
            uncertainty={
                "basis": (
                    "Observed refers to the record's existence and metadata, which were "
                    "fetched from arXiv. It does not extend to the paper's conclusions."
                ),
                "peer_review": (
                    "Carries a DOI or journal reference, indicating a published version."
                    if paper.peer_reviewed
                    else (
                        "No DOI or journal reference in the arXiv metadata. That is not "
                        "evidence of rejection -- records are often not updated after "
                        "acceptance -- but as far as this record shows, the claims are "
                        "unreviewed."
                    )
                ),
            },
            notes=(
                f"Retrieved from arXiv. Authors: {', '.join(paper.authors[:6])}"
                + (" et al." if len(paper.authors) > 6 else "")
                + ". Citing this record is not an endorsement of its conclusions."
            ),
        ),
    )


def seed_cache(key: str, papers: list[Paper], *, ttl_s: float = CACHE_TTL_S) -> None:
    """Install records into the cache directly.

    The mock twin, matching `catalog.seed_cache`: it lets tests exercise the full parse, tier
    and transport path against fixed records without touching the network, so CI stays
    deterministic and an arXiv outage cannot turn into a red build.

    Args:
        key: Cache key -- ``search:<query>:<limit>`` or ``id:<arxiv_id>``.
        papers: Records to install.
        ttl_s: How long the entry stays fresh.
    """
    _cache[key] = (time.time() + (ttl_s - CACHE_TTL_S), papers)


def clear_cache() -> None:
    """Empty the cache. Used by tests to guarantee isolation."""
    _cache.clear()
