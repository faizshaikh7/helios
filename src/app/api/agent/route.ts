import { ToolLoopAgent, isStepCount } from "ai";
import { NextResponse } from "next/server";
import { z } from "zod";
import { DEFAULT_MODELS, type ProviderId, resolveModel } from "@/lib/agent/models";
import { AGENT_INSTRUCTIONS, type CollectedCall, scienceTools } from "@/lib/agent/tools";

/**
 * Requests are proxied to the Python service for every other `/api` path, but this route is a
 * real filesystem route so Next.js serves it directly. The agent runs here, in TypeScript, and
 * calls the Python tools over HTTP.
 */

const RequestSchema = z.object({
  question: z.string().min(3).max(2000),
  provider: z.enum(["gemini", "grok", "ollama"]).optional(),
  model: z.string().max(120).optional(),
  /**
   * "grounded" gives the model the science tools. "baseline" runs the *same* model with no tools
   * at all, which is the control the accuracy chart is measured against.
   *
   * Holding the model fixed and varying only tool access is what makes the comparison mean
   * something: any difference is attributable to grounding rather than to model capability.
   */
  mode: z.enum(["grounded", "baseline"]).default("grounded"),
});

/** Instructions for the baseline: same task, no tools, and no invitation to refuse. */
const BASELINE_INSTRUCTIONS = `
You are an expert in orbital mechanics and spaceflight. Answer the user's question as accurately
as you can from your own knowledge. Give your best numeric estimate with units. Be concise.
`.trim();

/** Cap on tool-calling rounds, so a confused model cannot bill indefinitely. */
const MAX_STEPS = 8;

/**
 * Answer a natural-language question using the science tools.
 *
 * The response carries every tool call the agent made, not just its prose. That is the point of
 * the system: an answer without its computation is indistinguishable from a recalled guess, and
 * the tool trace is what makes the difference inspectable.
 */
export async function POST(request: Request): Promise<Response> {
  const parsed = RequestSchema.safeParse(await request.json().catch(() => null));

  if (!parsed.success) {
    return NextResponse.json(
      { error: { code: "invalid_request", message: parsed.error.issues[0]?.message ?? "bad input" } },
      { status: 422 },
    );
  }

  const provider = (parsed.data.provider ?? defaultProvider()) as ProviderId | null;

  if (!provider) {
    return NextResponse.json(
      {
        error: {
          code: "no_provider_configured",
          message:
            "No model provider is configured. Set GOOGLE_GENERATIVE_AI_API_KEY or XAI_API_KEY, " +
            "or run a local Ollama server and select the ollama provider.",
        },
      },
      { status: 503 },
    );
  }

  const model = parsed.data.model ?? DEFAULT_MODELS[provider];

  try {
    const grounded = parsed.data.mode === "grounded";

    const agent = new ToolLoopAgent({
      model: resolveModel({ provider, model }),
      instructions: grounded ? AGENT_INSTRUCTIONS : BASELINE_INSTRUCTIONS,
      tools: grounded ? scienceTools : {},
      stopWhen: isStepCount(grounded ? MAX_STEPS : 1),
    });

    const result = await agent.generate({ prompt: parsed.data.question });

    const calls: CollectedCall[] = [];
    for (const step of result.steps) {
      for (const call of step.toolCalls) {
        const matching = step.toolResults.find((item) => item.toolCallId === call.toolCallId);
        calls.push({
          tool: call.toolName,
          input: call.input,
          output: matching?.output ?? null,
        });
      }
    }

    return NextResponse.json({
      answer: result.text,
      provider,
      model,
      mode: parsed.data.mode,
      steps: result.steps.length,
      tool_calls: calls,
      // A zero here is the signal that matters: it means the model answered from memory rather
      // than from a computation, which is exactly the failure this project exists to prevent.
      grounded: calls.length > 0,
      usage: result.usage,
    });
  } catch (caught) {
    return NextResponse.json(
      {
        error: {
          code: "agent_failed",
          message: caught instanceof Error ? caught.message : String(caught),
        },
      },
      { status: 502 },
    );
  }
}

/** Pick a provider from whatever is configured, preferring hosted models over a local one. */
function defaultProvider(): ProviderId | null {
  if (process.env.GOOGLE_GENERATIVE_AI_API_KEY) return "gemini";
  if (process.env.XAI_API_KEY) return "grok";
  return "ollama";
}

/** Report which providers this deployment can use, so the UI can offer only working options. */
export async function GET(): Promise<Response> {
  return NextResponse.json({
    providers: {
      gemini: Boolean(process.env.GOOGLE_GENERATIVE_AI_API_KEY),
      grok: Boolean(process.env.XAI_API_KEY),
      ollama: true,
    },
    defaults: DEFAULT_MODELS,
  });
}
