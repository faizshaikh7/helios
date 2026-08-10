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

/** Defaults per provider, chosen for tool-calling capability rather than raw size. */
export const DEFAULT_MODELS: Record<ProviderId, string> = {
  gemini: "gemini-3.5-flash",
  grok: "grok-4.5",
  // Whatever is pulled locally; qwen3 is a reasonable tool-calling default but users vary, so
  // this is overridable and the provider is probed before use.
  ollama: "qwen3",
};

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
