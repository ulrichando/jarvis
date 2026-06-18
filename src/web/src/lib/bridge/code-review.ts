import "server-only";
import { generateText } from "ai";
import { getModel } from "../ai/models";
import { getPrDiff, postPrComment } from "../connectors/github";

/**
 * Review a pull request (claude.ai/code Code Review, pragmatic single-pass):
 * fetch the PR's diff, have a model review it for real problems, and post the
 * findings as a PR comment. Triggerable manually (the review route) or from the
 * GitHub webhook on PR open. Inline-comment positioning + a check run are the
 * fuller version (deferred); a summary comment is the high-value 80%.
 */
export async function reviewPullRequest(
  repo: string,
  number: number,
  modelId = "",
): Promise<{ ok: true; url: string } | { ok: false; error: string }> {
  const diff = await getPrDiff(repo, number);
  if (!diff || !diff.trim()) {
    return { ok: false, error: "No diff to review (PR not found, empty, or GitHub not connected)." };
  }
  let text: string;
  try {
    const { model } = await getModel(modelId); // "" → the configured default
    const r = await generateText({
      model,
      prompt:
        "You are a senior engineer reviewing a pull request. Report only real problems — bugs, " +
        "security issues, and correctness/logic errors — and skip style, formatting, and pre-existing " +
        "issues. For each finding give the file and line, a one-line description, and a severity " +
        "(Important or Nit). If the change looks good, say so briefly. Be concise.\n\n" +
        "Unified diff:\n```diff\n" +
        diff +
        "\n```",
    });
    text = r.text.trim();
  } catch (e) {
    return { ok: false, error: `Review failed: ${String(e)}` };
  }
  if (!text) return { ok: false, error: "The model returned an empty review." };
  return postPrComment(repo, number, `## 🔍 Jarvis code review\n\n${text}\n\n<sub>Automated review by Jarvis.</sub>`);
}
