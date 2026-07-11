import { z } from "zod";
import { requireUserId, Unauthenticated } from "@/lib/auth-helpers";
import {
  deleteScheduledChat,
  updateScheduledChat,
} from "@/lib/scheduled/store";

export const runtime = "nodejs";

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

const PatchBody = z.object({
  name: z.string().trim().min(1).max(120).optional(),
  prompt: z.string().trim().min(1).max(8000).optional(),
  cron: z.string().trim().min(9).max(100).optional(),
  label: z.string().trim().min(1).max(120).optional(),
  paused: z.boolean().optional(),
});

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const denied = await gate(req);
  if (denied) return denied;
  const { id } = await params;
  const parsed = PatchBody.safeParse(await req.json().catch(() => null));
  if (!parsed.success) {
    return Response.json({ error: "invalid body" }, { status: 400 });
  }
  const task = updateScheduledChat(id, parsed.data);
  if (!task) return Response.json({ error: "not found" }, { status: 404 });
  return Response.json({ task });
}

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const denied = await gate(req);
  if (denied) return denied;
  const { id } = await params;
  if (!deleteScheduledChat(id))
    return Response.json({ error: "not found" }, { status: 404 });
  return Response.json({ ok: true });
}
