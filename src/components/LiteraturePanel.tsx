"use client";

import { useState } from "react";
import type {
  ApiError,
  CitationVerdict,
  CitationVerifyResponse,
  LiteratureSearchResponse,
  Paper,
} from "@/lib/types";

/** Visual treatment per verdict. Wording is the point; colour only reinforces it. */
const VERDICT_STYLE: Record<
  CitationVerdict["status"],
  { label: string; text: string; ring: string; blurb: string }
> = {
  verified: {
    label: "real",
    text: "text-observed",
    ring: "ring-observed/50",
    blurb: "Resolves to a record on arXiv.",
  },
  not_found: {
    label: "does not exist",
    text: "text-speculative",
    ring: "ring-speculative/50",
    blurb:
      "Correctly formed, but no such paper. This is what a fabricated citation looks like.",
  },
  malformed: {
    label: "not an identifier",
    text: "text-predicted",
    ring: "ring-predicted/50",
    blurb: "Not an arXiv identifier at all, so nothing can resolve it.",
  },
  unchecked: {
    label: "could not check",
    text: "text-muted",
    ring: "ring-edge",
    blurb: "arXiv was unreachable. This is not evidence either way.",
  },
};

/** Post JSON to the science service and surface a typed error as a thrown Error. */
async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const parsed = (await response.json().catch(() => null)) as ApiError | null;
    throw new Error(parsed?.error?.message ?? `HTTP ${response.status}`);
  }

  return response.json() as Promise<T>;
}

/** One search result. */
function PaperCard({ paper }: { paper: Paper }) {
  const authors =
    paper.authors.length > 4
      ? `${paper.authors.slice(0, 4).join(", ")} et al.`
      : paper.authors.join(", ");

  return (
    <li className="rounded-md border border-edge bg-surface-inset px-3 py-2.5">
      <a
        href={paper.url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sm leading-6 text-foreground underline decoration-edge underline-offset-4 hover:decoration-accent"
      >
        {paper.title}
      </a>

      <p className="mt-1 text-[11px] leading-5 text-muted">
        {authors} · {paper.published.slice(0, 10)} ·{" "}
        <span className="font-mono">{paper.arxiv_id}</span>
      </p>

      <p className="mt-1 text-[11px] leading-5 text-faint">
        {paper.peer_reviewed_signal ? (
          <span className="text-observed">
            Has a published version{paper.journal_ref ? ` — ${paper.journal_ref}` : ""}.
          </span>
        ) : (
          <>
            Preprint: no DOI or journal reference in the record. That is not evidence it was
            rejected.
          </>
        )}
      </p>
    </li>
  );
}

/**
 * Literature search and citation verification.
 *
 * The verifier is the part that matters. A fabricated reference is indistinguishable from a real
 * one by reading it — the authors look right, the title sounds right, the identifier is
 * well-formed — so the only way to tell is to resolve it. This panel makes that check something
 * a reader can run rather than something they have to take on faith.
 */
export function LiteraturePanel() {
  const [query, setQuery] = useState("differential drag constellation control");
  const [results, setResults] = useState<LiteratureSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [claimed, setClaimed] = useState("");
  const [verdicts, setVerdicts] = useState<CitationVerifyResponse | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);

  async function runSearch() {
    setSearching(true);
    setSearchError(null);
    try {
      setResults(
        await post<LiteratureSearchResponse>("/api/literature/search", {
          query,
          max_results: 6,
        }),
      );
    } catch (caught) {
      setSearchError(caught instanceof Error ? caught.message : String(caught));
      setResults(null);
    } finally {
      setSearching(false);
    }
  }

  async function runVerify() {
    // Split on anything that separates citations in prose, so a pasted reference list works
    // without the reader having to reformat it first.
    const identifiers = claimed
      .split(/[\n,;]+/)
      .map((item) => item.trim())
      .filter(Boolean)
      .slice(0, 20);

    if (identifiers.length === 0) return;

    setChecking(true);
    setCheckError(null);
    try {
      setVerdicts(
        await post<CitationVerifyResponse>("/api/literature/verify", {
          arxiv_ids: identifiers,
        }),
      );
    } catch (caught) {
      setCheckError(caught instanceof Error ? caught.message : String(caught));
      setVerdicts(null);
    } finally {
      setChecking(false);
    }
  }

  return (
    <section className="mt-10 rounded-lg border border-edge bg-surface p-5">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium tracking-tight text-foreground">Literature</h2>
        <span className="text-[11px] text-faint">a citation you cannot resolve is not evidence</span>
      </header>

      <p className="mt-2 max-w-2xl text-xs leading-6 text-muted">
        A language model asked for references produces citations that look exactly like real
        ones — plausible authors, a plausible title, a well-formed identifier. Nothing in the
        text tells them apart. Atlas may only cite what this tool returned, and anything else can
        be checked below.
      </p>

      {/* Search */}
      <div className="mt-5 flex flex-wrap gap-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void runSearch();
          }}
          placeholder="Search the literature…"
          aria-label="Literature search terms"
          className="min-w-0 flex-1 rounded-md border border-edge bg-surface-inset px-2.5 py-1.5 text-sm text-foreground outline-none focus:border-accent"
        />
        <button
          type="button"
          onClick={() => void runSearch()}
          disabled={searching || query.trim().length < 3}
          className="rounded-md bg-accent px-4 py-1.5 text-sm text-accent-contrast disabled:opacity-50"
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </div>

      {searchError && (
        <p className="mt-3 rounded-md border border-speculative/40 bg-surface-inset px-3 py-2 text-xs text-speculative">
          {searchError}
        </p>
      )}

      {results && (
        <>
          <ul className="mt-4 grid gap-2">
            {results.papers.map((paper) => (
              <PaperCard key={paper.arxiv_id} paper={paper} />
            ))}
          </ul>
          {results.count === 0 && (
            <p className="mt-4 text-xs text-muted">
              No records matched. Saying so beats offering something adjacent.
            </p>
          )}
          <p className="mt-3 text-[11px] leading-5 text-faint">{results.caveat}</p>
        </>
      )}

      {/* Verification */}
      <div className="mt-8 border-t border-edge pt-5">
        <h3 className="text-xs font-medium tracking-tight text-foreground">Check a citation</h3>
        <p className="mt-1 max-w-2xl text-[11px] leading-5 text-muted">
          Paste arXiv identifiers — one per line, or comma-separated. Try a real one alongside an
          invented one such as <span className="font-mono">2401.99999</span>: both look equally
          legitimate written down, and only one resolves.
        </p>

        <div className="mt-3 flex flex-wrap gap-2">
          <textarea
            value={claimed}
            onChange={(event) => setClaimed(event.target.value)}
            rows={3}
            placeholder={"1806.01218\n2401.99999\nSmith et al. 2019"}
            aria-label="Claimed arXiv identifiers"
            className="min-w-0 flex-1 rounded-md border border-edge bg-surface-inset px-2.5 py-1.5 font-mono text-xs text-foreground outline-none focus:border-accent"
          />
          <button
            type="button"
            onClick={() => void runVerify()}
            disabled={checking || claimed.trim().length === 0}
            className="h-fit rounded-md border border-edge px-4 py-1.5 text-sm text-foreground hover:border-accent disabled:opacity-50"
          >
            {checking ? "Checking…" : "Check"}
          </button>
        </div>

        {checkError && (
          <p className="mt-3 rounded-md border border-speculative/40 bg-surface-inset px-3 py-2 text-xs text-speculative">
            {checkError}
          </p>
        )}

        {verdicts && (
          <>
            <ul className="mt-4 grid gap-2">
              {verdicts.results.map((verdict, index) => {
                const style = VERDICT_STYLE[verdict.status];
                return (
                  <li
                    key={`${verdict.claimed}-${index}`}
                    className={`rounded-md bg-surface-inset px-3 py-2 ring-1 ${style.ring}`}
                  >
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                      <span className="font-mono text-xs text-foreground">{verdict.claimed}</span>
                      <span className={`text-[11px] uppercase tracking-wide ${style.text}`}>
                        {style.label}
                      </span>
                    </div>
                    <p className="mt-1 text-[11px] leading-5 text-muted">
                      {verdict.paper ? verdict.paper.title : style.blurb}
                    </p>
                  </li>
                );
              })}
            </ul>

            <p className="mt-3 text-[11px] leading-5 text-faint">
              {verdicts.summary.verified} of {verdicts.summary.claimed} resolved
              {verdicts.summary.unresolved > 0 && (
                <>
                  {" · "}
                  <span className="text-speculative">
                    {verdicts.summary.unresolved} did not
                  </span>
                </>
              )}
              {verdicts.summary.unchecked > 0 && ` · ${verdicts.summary.unchecked} unchecked`}.
            </p>
          </>
        )}
      </div>
    </section>
  );
}
