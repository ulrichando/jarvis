"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Thread } from "./thread";
import { Composer } from "./composer";
import { EmptyState } from "./empty-state";
import { Categories } from "./categories";
import { useChatStore } from "@/stores/chat";
import { useSettings } from "@/hooks/use-settings";
import { DEFAULT_MODEL, MODELS_META } from "@/lib/ai/models-meta";
import { getProviderUX } from "@/lib/ai/provider-ux";

type ChatProps = {
  chatId?: string;
  initialMessages?: UIMessage[];
};

export function Chat({ chatId, initialMessages }: ChatProps) {
  const qc = useQueryClient();
  const [input, setInput] = useState("");
  const model = useChatStore((s) => s.model);
  const { data: settings } = useSettings();

  const activeMeta = MODELS_META[model] ?? MODELS_META[DEFAULT_MODEL];
  const provider = activeMeta.provider;
  const ux = getProviderUX(provider);

  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        prepareSendMessagesRequest: ({ id, messages, body }) => ({
          body: { id, messages, model, ...body },
        }),
      }),
    [model],
  );

  const { messages, sendMessage, status, stop } = useChat({
    id: chatId,
    messages: initialMessages,
    transport,
    onFinish: () => {
      qc.invalidateQueries({ queryKey: ["conversations"] });
      if (chatId) qc.invalidateQueries({ queryKey: ["conversation", chatId] });
    },
  });

  const submit = (text?: string) => {
    const content = (text ?? input).trim();
    if (!content) return;
    setInput("");
    sendMessage({ text: content });
  };

  const isEmpty = messages.length === 0;

  if (isEmpty) {
    return (
      <div className="flex h-full flex-col items-center justify-center overflow-y-auto px-4 py-8">
        <div className="flex w-full max-w-3xl flex-col">
          <EmptyState name={settings?.user?.name} provider={provider} />
          <div className="mt-8">
            <Composer
              value={input}
              onChange={setInput}
              onSubmit={() => submit()}
              onStop={stop}
              status={status}
              provider={provider}
            />
          </div>
          <Categories chips={ux.chips} onPick={(p) => setInput(p)} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto">
        <Thread
          messages={messages}
          isStreaming={status === "streaming" || status === "submitted"}
        />
      </div>
      <Composer
        value={input}
        onChange={setInput}
        onSubmit={() => submit()}
        onStop={stop}
        status={status}
        provider={provider}
      />
    </div>
  );
}
