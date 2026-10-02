"use client";

import { useEffect, useState } from "react";

/** One tool invocation the agent made while answering. */
type ToolCall = {
  tool: string;
  input: unknown;
  output: unknown;
};

type AgentAnswer = {
  answer: string;
  provider: string;
  model: string;
  steps: number;
  tool_calls: ToolCall[];
  grounded: boolean;
  usage?: Record<string, unknown>;
};

type ProviderStatus = {
  providers: Record<string, boolean>;
  defaults: Record<string, string>;
};

/** Questions that cannot be answered from memory, so the grounding is visible immediately. */
const EXAMPLES = [
  "When can I next see the ISS from Bangalore above 20 degrees?",
  "What is the ISS doing for power right now — how long is its longest eclipse today?",
  "Where is Hubble over the Earth at the moment, and how fast is it moving?",
  "Compare the orbital period and inclination of NOAA 19 and the ISS.",
  "Trade power against temperature for a 1 million km² Dyson swarm near Mercury.",
];

/**
 * Natural-language interface to the science tools.
 *
 * The tool trace is shown alongside the prose rather than hidden behind a toggle. An answer
 * without its computation is indistinguishable from a recalled guess, and for this project the
 * distinction is the entire point -- so "grounded in N tool calls" is displayed as prominently
 * as the answer itself.
 */
export function AskPanel() {
  const [question, setQuestion] = useState("");
  const [provider, setProvider] = useState<string>("");
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [answer, setAnswer] = useState<AgentAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/agent")
      .then((r) => r.json())
      .then((body: ProviderStatus) => {
        setStatus(body);
        const firstReady = Object.entries(body.providers).find(([, ready]) => ready)?.[0];
        setProvider(firstReady ?? "ollama");
      })
      .catch(() => setStatus(null));
  }, []);

  async function ask(text: string) {
    if (!text.trim()) return;

    setLoading(true);
    setError(null);
    setAnswer(null);

    try {
      const response = await fetch("/api/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text, provider: provider || undefined }),
      });

      const body = await response.json();
      if (!response.ok) throw new Error(body?.error?.message ?? `HTTP ${response.status}`);

      setAnswer(body as AgentAnswer);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }

  const anyConfigured = status ? Object.values(status.providers).some(Boolean) : true;

  return (
    <section id="atlas" className="scroll-mt-16 mt-8 overflow-hidden rounded-xl border border-edge bg-surface p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium text-derived">Grounded mission copilot</p>
          <h2 className="mt-1 text-lg font-medium tracking-tight text-foreground">Ask Atlas</h2>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted">
            Ask in plain English. Atlas chooses from twelve typed calculation tools, then shows
            the calculation path beside the answer instead of asking you to trust fluent prose.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span className="hidden text-[10px] text-faint sm:inline">Execution trace included</span>
          {status && (
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              className="rounded-md border border-edge bg-surface-inset px-2 py-1 font-mono text-xs text-foreground outline-none"
              aria-label="Model provider"
            >
              {Object.entries(status.providers).map(([id, ready]) => (
                <option key={id} value={id} className="bg-surface">
                  {id}
                  {ready ? "" : " (no key)"}
                </option>
              ))}
            </select>
          )}
        </div>
      </div>

      <div className="mt-5 flex gap-2 rounded-lg border border-edge-strong bg-background p-1.5 focus-within:border-accent">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !loading) ask(question);
          }}
          placeholder="Ask about an orbit, encounter, or speculative mission concept…"
          className="min-w-0 flex-1 bg-transparent px-2.5 py-2 text-sm text-foreground outline-none"
        />
        <button
          type="button"
          onClick={() => ask(question)}
          disabled={loading || !question.trim()}
          className="rounded-md bg-foreground px-5 py-2 text-sm font-medium text-background hover:opacity-85 disabled:opacity-50"
        >
          {loading ? "Thinking…" : "Ask"}
        </button>
      </div>

      <div className="mt-5 border-t border-edge pt-3">
        <span className="text-[10px] font-medium text-faint">Suggested questions</span>
        <div className="mt-2 grid gap-x-6 sm:grid-cols-2">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => {
                setQuestion(example);
                ask(example);
              }}
              disabled={loading}
              className="group flex items-start justify-between gap-3 border-b border-edge py-3 text-left text-[11px] leading-5 text-muted hover:text-foreground disabled:opacity-50"
            >
              <span>{example}</span>
              <span className="text-faint group-hover:text-foreground">→</span>
            </button>
          ))}
        </div>
      </div>

      {!anyConfigured && (
        <p className="mt-4 rounded-md border border-edge bg-surface-inset px-3 py-2 text-[11px] leading-5 text-muted">
          No hosted provider key is set. Add <code>GOOGLE_GENERATIVE_AI_API_KEY</code> or{" "}
          <code>XAI_API_KEY</code> to <code>.env.local</code>, or run{" "}
          <code>ollama serve</code> locally and select the ollama provider.
        </p>
      )}

      {error && (
        <p className="mt-4 rounded-md border border-danger bg-surface px-3 py-2 font-mono text-xs text-danger">
          {error}
        </p>
      )}

      {answer && (
        <div className="mt-5">
          <div className="mb-3 flex flex-wrap items-center gap-3 text-[11px]">
            <span
              className={`rounded-full px-2 py-0.5 ${
                answer.grounded
                  ? "bg-ok/15 text-observed"
                  : "bg-danger/15 text-danger"
              }`}
            >
              {answer.grounded
                ? `grounded — ${answer.tool_calls.length} tool call${
                    answer.tool_calls.length === 1 ? "" : "s"
                  }`
                : "ungrounded — answered without computing"}
            </span>
            <span className="font-mono text-faint">
              {answer.provider} · {answer.model} · {answer.steps} step
              {answer.steps === 1 ? "" : "s"}
            </span>
          </div>

          <div className="whitespace-pre-wrap text-sm leading-6 text-foreground">
            {answer.answer}
          </div>

          {answer.tool_calls.length > 0 && (
            <details className="mt-4">
              <summary className="cursor-pointer text-xs text-muted hover:text-foreground">
                Tool trace — what was actually computed
              </summary>
              <ol className="mt-2 space-y-2">
                {answer.tool_calls.map((call, index) => (
                  <li
                    key={index}
                    className="rounded-md border border-edge bg-surface-inset px-3 py-2"
                  >
                    <p className="font-mono text-xs text-derived">{call.tool}</p>
                    <pre className="mt-1 overflow-x-auto font-mono text-[11px] leading-relaxed text-muted">
                      {JSON.stringify(call.input, null, 2)}
                    </pre>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
