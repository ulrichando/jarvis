import { Chat } from "@/components/chat/chat";
import { SCHEDULE_TEMPLATES } from "@/lib/scheduled/interview";

export default async function NewChatPage({
  searchParams,
}: {
  searchParams: Promise<{ seed?: string; incognito?: string; schedule?: string }>;
}) {
  const { seed, incognito, schedule } = await searchParams;
  // `?schedule=<template>` — the Home "Scheduled" templates launch the
  // setup interview: auto-send the template's seed prompt and flag the chat
  // into schedule-setup mode so the model runs the ask-then-create flow.
  const tpl = schedule ? SCHEDULE_TEMPLATES[schedule] : undefined;
  // `?seed=` comes from the artifacts "New artifact" picker — prime the
  // composer (not auto-sent) so the user can tweak before building. The id
  // is derived from the seed so navigating from the gallery (a fresh <Chat>
  // mount) always seeds; a different category → different id → re-seeds.
  const prefillPrompt = tpl
    ? { id: `schedule:${schedule}`, text: tpl.seed, autoSend: true }
    : seed
      ? { id: `seed:${seed}`, text: seed }
      : undefined;
  // `?incognito=1` — the ghost button routes here from mid-conversation to
  // start a fresh unsaved chat.
  return (
    <Chat
      prefillPrompt={prefillPrompt}
      initialIncognito={incognito === "1"}
      initialScheduleSetup={!!tpl}
      initialTaskTitle={tpl?.title}
    />
  );
}
