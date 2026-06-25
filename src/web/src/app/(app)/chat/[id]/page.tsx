import { notFound, redirect } from "next/navigation";
import { and, asc, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { toUIMessages } from "@/lib/chat/persist";
import { getUserId } from "@/lib/auth-helpers";
import { Chat } from "@/components/chat/chat";

export default async function ChatByIdPage(props: PageProps<"/chat/[id]">) {
  const { id } = await props.params;
  const search = (await props.searchParams) as
    | { seed?: string | string[] }
    | undefined;
  const rawSeed = search?.seed;
  const seed = Array.isArray(rawSeed) ? rawSeed[0] : rawSeed;

  if (!db) return <Chat chatId={id} seed={seed} />;

  const userId = await getUserId();
  if (!userId) redirect("/login");

  const [conversation] = await db
    .select()
    .from(schema.conversations)
    .where(
      and(
        eq(schema.conversations.id, id),
        eq(schema.conversations.userId, userId),
      ),
    )
    .limit(1);

  if (!conversation) return notFound();

  const rows = await db
    .select()
    .from(schema.messages)
    .where(eq(schema.messages.conversationId, id))
    .orderBy(asc(schema.messages.createdAt));

  return (
    <Chat
      chatId={id}
      initialMessages={toUIMessages(rows)}
      // Only seed if the conversation has no prior messages — avoids
      // double-sending if the user reloads the URL with the seed param
      // still in place.
      seed={rows.length === 0 ? seed : undefined}
    />
  );
}
