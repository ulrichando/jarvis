import { notFound } from "next/navigation";
import { and, asc, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { LOCAL_USER_ID, toUIMessages } from "@/lib/chat/persist";
import { Chat } from "@/components/chat/chat";

export default async function ChatByIdPage(props: PageProps<"/chat/[id]">) {
  const { id } = await props.params;

  if (!db) return <Chat chatId={id} />;

  const [conversation] = await db
    .select()
    .from(schema.conversations)
    .where(
      and(
        eq(schema.conversations.id, id),
        eq(schema.conversations.userId, LOCAL_USER_ID),
      ),
    )
    .limit(1);

  if (!conversation) return notFound();

  const rows = await db
    .select()
    .from(schema.messages)
    .where(eq(schema.messages.conversationId, id))
    .orderBy(asc(schema.messages.createdAt));

  return <Chat chatId={id} initialMessages={toUIMessages(rows)} />;
}
