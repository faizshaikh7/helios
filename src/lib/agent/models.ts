import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { createXai } from "@ai-sdk/xai";
import type { LanguageModel } from "ai";

/**
 * Providers this project can run the agent on.
 *
 * Model choice is deliberately not hardcoded. The evaluation harness scores whichever model is
 * configured, so "which model" becomes a measured result rather than an assumption -- and the
 * same question set can be replayed across providers to compare them on identical ground truth.
 */
export type ProviderId = "gemini" | "grok" | "ollama";

export type ModelChoice = {
  provider: ProviderId;
  /** Model identifier as the provider names it. */
  model: string;
};

/**
 * Defaults per provider, chosen for tool-calling capability rather than raw size.
 *
 * Gemini defaults to the *lite* variant deliberately. On the free tier, `gemini-3.5-flash`
 * allows only 20 requests per day (metric `generate_content_free_tier_requests`), which a single
 * evaluation pass exhausts many times over -- and the resulting error reads as a rate limit
 * rather than a daily cap, so it looks like a pacing problem that no amount of pacing fixes.
 * The lite variant has its own, far larger allowance.
 *
 * Capability trade-off accepted: a lighter model is weaker at tool selection. That is a fair
 * comparison regardless, because the grounded and baseline runs use the *same* model, so the
 * measured difference is still attributable to tool access alone.
 */
export const DEFAULT_MODELS: Record<ProviderId, string> = {
  gemini: "gemini-3.5-flash-lite",
  grok: "grok-4.5",
  // Whatever is pulled locally; qwen3 is a reasonable tool-calling default but users vary, so
  // this is overridable and the provider is probed before use.
  ollama: "qwen3",
};

/**
 * Models a caller may request, per provider.
 *
 * The agent endpoint is public, and the model name arrives from the request body. Without an
 * allowlist a stranger chooses what this deployment's key pays for -- and the expensive choice
 * and the cheap one are one string apart. Only models this project has actually run are listed;
 * adding one is a deliberate act, not a side effect of someone else's request.
 *
 * Ollama is unrestricted (empty list) because it runs on the caller's own machine against their
 * own hardware. There is no shared quota to protect, and which models are pulled locally varies
 * per user, so an allowlist there would only break working setups.
 */
export const ALLOWED_MODELS: Record<ProviderId, readonly string[]> = {
  // The lite variant is the default; the full flash model is permitted for a local run against a
  // paid key, where its 20-per-day free tier cap does not apply.
  gemini: ["gemini-3.5-flash-lite", "gemini-3.5-flash"],
  grok: ["grok-4.5"],
  ollama: [],
};

/**
 * Check whether a provider may be asked for a given model.
 *
 * @param provider - Provider the request selected.
 * @param model - Model identifier from the request.
 * @returns True if the model is permitted for that provider.
 */
export function isModelAllowed(provider: ProviderId, model: string): boolean {
  const allowed = ALLOWED_MODELS[provider];
  return allowed.length === 0 || allowed.includes(model);
}

/** Where a local Ollama server listens, using its OpenAI-compatible surface. */
const OLLAMA_BASE_URL = process.env.OLLAMA_BASE_URL ?? "http://127.0.0.1:11434/v1";

/**
 * Report which providers are usable in this environment.
 *
 * Ollama needs no key, so it is always listed as *configured*; whether it is actually running is
 * a separate question answered by `probeOllama`. Distinguishing the two matters: "no key set" and
 * "server not running" need different fixes, and collapsing them into one error message wastes
 * the reader's time.
 */
export function configuredProviders(): ProviderId[] {
  const available: ProviderId[] = [];

  if (process.env.GOOGLE_GENERATIVE_AI_API_KEY) available.push("gemini");
  if (process.env.XAI_API_KEY) available.push("grok");
  available.push("ollama");

  return available;
}

/**
 * Check whether a local Ollama server is reachable.
 *
 * @returns The models it reports, or null if it is not running.
 */
export async function probeOllama(): Promise<string[] | null> {
  try {
    const response = await fetch(`${OLLAMA_BASE_URL}/models`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!response.ok) return null;

    const body = (await response.json()) as { data?: { id: string }[] };
    return (body.data ?? []).map((item) => item.id);
  } catch {
    return null;
  }
}

/**
 * Resolve a provider and model name into a language model instance.
 *
 * @param choice - Provider and model to use.
 * @returns A model ready to pass to the agent.
 * @throws If the provider needs a key that is not set.
 */
export function resolveModel(choice: ModelChoice): LanguageModel {
  switch (choice.provider) {
    case "gemini": {
      const apiKey = process.env.GOOGLE_GENERATIVE_AI_API_KEY;
      if (!apiKey) {
        throw new Error("GOOGLE_GENERATIVE_AI_API_KEY is not set");
      }
      return createGoogleGenerativeAI({ apiKey })(choice.model);
    }

    case "grok": {
      const apiKey = process.env.XAI_API_KEY;
      if (!apiKey) {
        throw new Error("XAI_API_KEY is not set");
      }
      return createXai({ apiKey })(choice.model);
    }

    case "ollama": {
      // Ollama exposes an OpenAI-compatible API, so no dedicated provider package is needed.
      // The key is required by the interface but unused by Ollama.
      return createOpenAICompatible({
        name: "ollama",
        baseURL: OLLAMA_BASE_URL,
        apiKey: "ollama",
      })(choice.model);
    }
  }
}
