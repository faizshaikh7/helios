"""Tests for literature access and citation verification.

**All tier 1, and all offline.** These assert properties that must hold regardless of what
arXiv returns, using the mock twin (`literature.seed_cache`) so CI is deterministic and an arXiv
outage cannot turn into a red build -- the same discipline as the catalog client.

The tests worth reading are the ones about *fabrication*. This module's reason for existing is
that a made-up citation is indistinguishable from a real one by inspection, so the checks here
are less about parsing and more about whether the distinction survives every path through the
code.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app
from science import literature
from science.provenance import Tier

client = TestClient(app)

# A real record, frozen. Carries a DOI so the peer-review signal has something to find.
REVIEWED = literature.Paper(
    arxiv_id="1806.01218",
    title="Differential Drag Control Scheme for Large Constellation of Planet Satellites",
    authors=["A. Researcher", "B. Colleague"],
    summary="A scheme for controlling relative motion using differential drag.",
    published="2018-06-04T00:00:00Z",
    updated="2018-06-04T00:00:00Z",
    categories=["astro-ph.IM"],
    doi="10.1000/example",
    journal_ref="J. Guid. Control Dyn. 41(11)",
)

# A preprint with no publication signal, which must not be reported as rejected.
PREPRINT = literature.Paper(
    arxiv_id="2508.19549",
    title="Modeling Orbital Decay of Low-Earth Orbit Satellites due to Atmospheric Drag",
    authors=["C. Author"],
    summary="A model of orbital decay under atmospheric drag.",
    published="2025-08-27T00:00:00Z",
    updated="2025-08-27T00:00:00Z",
    categories=["astro-ph.EP"],
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Give every test a clean cache, then install the frozen records.

    Without isolation one test's seeded records leak into the next, and a test that should have
    hit the network would pass silently against a stale entry.
    """
    literature.clear_cache()

    literature.seed_cache(f"id:{REVIEWED.arxiv_id}", [REVIEWED])
    literature.seed_cache(f"id:{PREPRINT.arxiv_id}", [PREPRINT])
    # A well-formed identifier that resolves to nothing: an empty list is the cached "no such
    # record", exactly as arXiv answers an unknown id.
    literature.seed_cache("id:2401.99999", [])
    literature.seed_cache("search:orbital decay:8", [PREPRINT, REVIEWED])

    yield

    literature.clear_cache()


# --------------------------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2401.12345", "2401.12345"),
        ("arXiv:2401.12345", "2401.12345"),
        ("arxiv:2401.12345v7", "2401.12345"),
        ("https://arxiv.org/abs/2401.12345", "2401.12345"),
        ("http://arxiv.org/abs/2401.12345v2", "2401.12345"),
        ("(arXiv:2401.12345).", "2401.12345"),
        ("[2401.12345]", "2401.12345"),
        ("astro-ph/0601001", "astro-ph/0601001"),
        ("astro-ph.CO/0601001v3", "astro-ph.CO/0601001"),
    ],
)
def test_real_identifiers_survive_the_forms_a_model_writes_them_in(
    raw: str, expected: str
) -> None:
    """A genuine identifier is recognised through prose formatting.

    This matters more than it looks. Failing to normalise a real identifier reports a genuine
    citation as fabricated -- a false accusation, and the more damaging of the two possible
    errors, because it teaches a reader to distrust the check itself.
    """
    assert literature.normalise_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "not-an-id", "10.1000/journal.2024", "doi:10.1000/x", "12345", "2401.123"],
)
def test_things_that_are_not_arxiv_identifiers_are_refused(raw: str) -> None:
    """Anything that is not an identifier is refused rather than guessed at."""
    assert literature.normalise_id(raw) is None


def test_a_malformed_identifier_never_reaches_the_network() -> None:
    """Lookup of a non-identifier returns None without a request.

    The cache is empty for this value, so any network call would raise or hang. Returning None
    proves the check happens before the fetch.
    """
    assert literature.fetch_by_id("obviously not an identifier") is None


# --------------------------------------------------------------------------------------------
# Fabrication is visible
# --------------------------------------------------------------------------------------------


def test_a_real_citation_verifies_and_carries_its_record() -> None:
    """A genuine identifier resolves and brings the record with it."""
    outcome = literature.verify_citations([REVIEWED.arxiv_id])

    assert outcome["summary"] == {"claimed": 1, "verified": 1, "unresolved": 0, "unchecked": 0}

    result = outcome["results"][0]
    assert result["status"] == "verified"
    assert result["paper"]["title"] == REVIEWED.title


def test_a_wellformed_but_nonexistent_citation_is_reported_not_dropped() -> None:
    """An identifier that resolves to nothing is named as such.

    This is the central case. A model that fabricates a reference produces exactly this: a
    correctly-shaped identifier pointing at no paper. Silently omitting it would leave the
    answer looking clean; the finding *is* that the citation does not exist.
    """
    outcome = literature.verify_citations(["2401.99999"])

    result = outcome["results"][0]
    assert result["status"] == "not_found"
    assert result["paper"] is None
    assert "fabricated" in result["note"]

    assert outcome["summary"]["verified"] == 0
    assert outcome["summary"]["unresolved"] == 1


def test_mixed_citations_are_counted_separately() -> None:
    """Real, fabricated, and malformed citations are distinguished, not lumped together."""
    outcome = literature.verify_citations(
        [REVIEWED.arxiv_id, "2401.99999", "Smith et al. 2019", PREPRINT.arxiv_id]
    )

    statuses = [item["status"] for item in outcome["results"]]
    assert statuses == ["verified", "not_found", "malformed", "verified"]

    assert outcome["summary"] == {"claimed": 4, "verified": 2, "unresolved": 2, "unchecked": 0}


def test_an_unreachable_service_is_not_treated_as_evidence_of_fabrication(monkeypatch) -> None:
    """A transport failure yields 'unchecked', never 'not_found'.

    Conflating the two would let an arXiv outage brand every real citation in an answer as
    invented -- false confidence in the opposite direction, and precisely the failure this
    project exists to avoid.
    """

    def _explode(*_args, **_kwargs):
        raise literature.LiteratureError("arXiv is down")

    monkeypatch.setattr(literature, "fetch_by_id", _explode)

    outcome = literature.verify_citations(["2401.12345"])

    result = outcome["results"][0]
    assert result["status"] == "unchecked"
    assert "could not be checked" in result["note"].lower()

    # Not counted against the citation either way.
    assert outcome["summary"]["unresolved"] == 0
    assert outcome["summary"]["unchecked"] == 1


# --------------------------------------------------------------------------------------------
# Tiering
# --------------------------------------------------------------------------------------------


def test_a_citation_is_observed_but_says_so_only_about_the_record() -> None:
    """The observed tier covers the record's existence, never the paper's conclusions.

    Letting `observed` extend to a preprint's claims would launder an unreviewed assertion into
    a measured fact, which is exactly the blurring the trust taxonomy exists to prevent.
    """
    value = literature.citation_value(PREPRINT)

    assert value.tier is Tier.OBSERVED
    assert value.receipt.dataset["source"] == "arxiv"

    basis = value.receipt.uncertainty["basis"]
    assert "does not extend to the paper's conclusions" in basis
    assert "endorsement" in value.receipt.notes


def test_a_missing_doi_is_not_reported_as_rejection() -> None:
    """Absence of a publication signal is stated as absence of evidence.

    arXiv records are frequently not updated after journal acceptance, so "no DOI" means the
    metadata is silent, not that the work failed review. Saying otherwise would be a claim about
    a real paper that this tool has no basis for.
    """
    assert PREPRINT.peer_reviewed is False
    assert REVIEWED.peer_reviewed is True

    note = literature.citation_value(PREPRINT).receipt.uncertainty["peer_review"]
    assert "not" in note and "evidence of rejection" in note


# --------------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------------


def test_feed_entries_without_a_usable_identifier_are_dropped() -> None:
    """An entry with no parseable id is discarded rather than passed on.

    A citation that cannot be checked later is worse than no citation: it looks like evidence
    and cannot be audited.
    """
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://example.com/not-an-arxiv-id</id>
        <title>Unverifiable</title>
        <summary>No usable identifier.</summary>
        <published>2024-01-01T00:00:00Z</published>
        <updated>2024-01-01T00:00:00Z</updated>
      </entry>
      <entry>
        <id>http://arxiv.org/abs/2401.12345v1</id>
        <title>Perfectly  fine</title>
        <summary>Has an identifier.</summary>
        <published>2024-01-01T00:00:00Z</published>
        <updated>2024-01-01T00:00:00Z</updated>
        <author><name>D. Author</name></author>
      </entry>
    </feed>"""

    papers = literature._parse_feed(feed)

    assert len(papers) == 1
    assert papers[0].arxiv_id == "2401.12345"
    # Whitespace inside titles is collapsed, so a wrapped title does not render with a gap.
    assert papers[0].title == "Perfectly fine"


def test_an_unparseable_response_raises_rather_than_returning_nothing() -> None:
    """Malformed XML is an error, not an empty result set.

    Returning `[]` would be indistinguishable from "no papers matched", turning a broken
    upstream into a confident, wrong "nothing found".
    """
    with pytest.raises(literature.LiteratureError):
        literature._parse_feed("<feed><entry>truncated")


def test_an_empty_query_is_refused() -> None:
    """A blank search is rejected before it reaches the network."""
    with pytest.raises(ValueError):
        literature.search("   ")


# --------------------------------------------------------------------------------------------
# API contract
# --------------------------------------------------------------------------------------------


def test_search_endpoint_returns_records_with_citations_and_a_caveat() -> None:
    """The route returns records, tiered citations, and states what arXiv is."""
    response = client.post(
        "/api/literature/search", json={"query": "orbital decay", "max_results": 8}
    )
    assert response.status_code == 200

    body = response.json()
    assert body["count"] == 2
    assert len(body["citations"]) == 2
    assert all(item["tier"] == "observed" for item in body["citations"])

    # The preprint caveat is part of the contract, not a nicety: a reader must not take presence
    # on arXiv as peer review.
    assert "preprint" in body["caveat"].lower()


def test_verify_endpoint_separates_real_from_invented() -> None:
    """The verification route reports each verdict and explains the vocabulary."""
    response = client.post(
        "/api/literature/verify",
        json={"arxiv_ids": [REVIEWED.arxiv_id, "2401.99999", "Smith 2019"]},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["summary"] == {"claimed": 3, "verified": 1, "unresolved": 2, "unchecked": 0}
    assert "fabricated" in body["interpretation"]


def test_verify_endpoint_rejects_an_empty_list() -> None:
    """Nothing to check is a bad request, not an empty success."""
    assert client.post("/api/literature/verify", json={"arxiv_ids": []}).status_code == 422


def test_search_endpoint_rejects_a_trivial_query() -> None:
    """A two-character query is refused rather than returning noise."""
    assert client.post("/api/literature/search", json={"query": "x"}).status_code == 422
