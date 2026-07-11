import { z } from "zod";
import { requireUserId, Unauthenticated } from "@/lib/auth-helpers";
import {
  createScheduledChat,
  listScheduledChats,
} from "@/lib/scheduled/store";

export const runtime = "nodejs";

// Scheduled CHATS (Home). Auth-gated like /api/conversations; the store is
// box-global (single-user app), the session just proves it's the owner.
async function gate(req: Request): Promise<Response | null> {
  try {
    await requireUserId(req.headers);
    return null;
  } catch (e) {
    if (e instanceof Unauthenticated)
      return new Response("Unauthorized", { status: 401 });
    throw e;
  }
}

export async function GET(req: Request) {
  const denied = await gate(req);
  if (denied) return denied;
  return Response.json({ tasks: listScheduledChats() });
}

const CreateBody = z.object({
  name: z.string().trim().min(1).max(120),
  prompt: z.string().trim().min(1).max(8000),
  cron: z.string().trim().min(9).max(100),
  label: z.string().trim().min(1).max(120),
  at: z.number().int().positive().optional(),
  model: z.string().trim().max(80).optional(),
});

export async function POST(req: Request) {
  const denied = await gate(req);
  if (denied) return denied;
  const parsed = CreateBody.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return Response.json(
      { error: parsed.error.issues[0]?.message ?? "invalid body" },
      { status: 400 },
    );
  }
  return Response.json({ task: createScheduledChat(parsed.data) });
}
