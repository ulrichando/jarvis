import { Chat } from "@/components/chat/chat";

export default async function NewChatPage({
  searchParams,
}: {
  searchParams: Promise<{ seed?: string }>;
}) {
  const { seed } = await searchParams;
  // `?seed=` comes from the artifacts "New artifact" picker — prime the
  // composer (not auto-sent) so the user can tweak before building. The id
  // is derived from the seed so navigating from the gallery (a fresh <Chat>
  // mount) always seeds; a different category → different id → re-seeds.
  const prefillPrompt = seed ? { id: `seed:${seed}`, text: seed } : undefined;
  return <Chat prefillPrompt={prefillPrompt} />;
}
