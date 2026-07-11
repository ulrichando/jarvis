// Scheduled-task SETUP interview (claude.ai parity). When the home "Daily
// brief" / "Weekly review" templates launch, the chat runs an interview:
// Jarvis asks structured questions (rendered as inline option cards), then
// emits a create-block that the client turns into the real scheduled task.
//
// The transport is the same text-block pattern <jarvisArtifact> uses — the
// model emits XML blocks in its reply, the client parses + renders them and
// strips them from the visible Markdown. No client-side tool round-trip
// plumbing needed on top of the hand-rolled SSE reader.

export type InterviewOption = {
  label: string;
  /** Optional sublabel shown dimmed after the label. */
  hint?: string;
  /** Marks the "(Recommended)" option, styled + sorted first by the model. */
  recommended?: boolean;
};

export type InterviewQuestion = {
  question: string;
  multiSelect: boolean;
  options: InterviewOption[];
};

export type CreateSchedule = {
  name: string;
  prompt: string;
  cron: string;
  label: string;
};

const QUESTION_RE = /<jarvisQuestion>([\s\S]*?)<\/jarvisQuestion>/i;
const CREATE_RE = /<jarvisCreateSchedule>([\s\S]*?)<\/jarvisCreateSchedule>/i;

// Tolerant JSON extraction: the model sometimes wraps the block body in a
// ```json fence or trails prose. Grab the outermost {...} and parse that.
function parseJsonBlock(body: string): unknown | null {
  const start = body.indexOf("{");
  const end = body.lastIndexOf("}");
  if (start === -1 || end === -1 || end < start) return null;
  try {
    return JSON.parse(body.slice(start, end + 1));
  } catch {
    return null;
  }
}

export function parseInterviewQuestion(text: string): InterviewQuestion | null {
  const m = QUESTION_RE.exec(text);
  if (!m) return null;
  const obj = parseJsonBlock(m[1]) as Partial<InterviewQuestion> | null;
  if (!obj || typeof obj.question !== "string" || !Array.isArray(obj.options)) {
    return null;
  }
  const options = obj.options
    .map((o) =>
      typeof o === "string"
        ? { label: o }
        : o && typeof (o as InterviewOption).label === "string"
          ? (o as InterviewOption)
          : null,
    )
    .filter((o): o is InterviewOption => o !== null);
  if (options.length === 0) return null;
  return {
    question: obj.question,
    multiSelect: obj.multiSelect === true,
    options,
  };
}

export function parseCreateSchedule(text: string): CreateSchedule | null {
  const m = CREATE_RE.exec(text);
  if (!m) return null;
  const obj = parseJsonBlock(m[1]) as Partial<CreateSchedule> | null;
  if (
    !obj ||
    typeof obj.name !== "string" ||
    typeof obj.prompt !== "string" ||
    typeof obj.cron !== "string"
  ) {
    return null;
  }
  return {
    name: obj.name.trim(),
    prompt: obj.prompt.trim(),
    cron: obj.cron.trim(),
    label: (obj.label ?? obj.cron).trim(),
  };
}

// Seed prompts the template chips auto-send to open the interview.
export const SCHEDULE_TEMPLATES: Record<string, { title: string; seed: string }> = {
  "daily-brief": {
    title: "Daily brief",
    seed: "Set up a scheduled task that gives me a morning brief each weekday: what's on my plate, anything that needs my attention, and one thing worth reading.",
  },
  "weekly-review": {
    title: "Weekly review",
    seed: "Set up a scheduled task that reviews what I worked on each week and drafts a short status update.",
  },
};

// Interview instructions — appended to the system prompt ONLY when the chat
// is in schedule-setup mode (client sends scheduleSetup:true). Off by
// default so normal chats never get scheduling behavior.
export function buildScheduleInterviewPrompt(): string {
  return `

# SCHEDULED TASK SETUP MODE

You are helping the user create a RECURRING SCHEDULED TASK (a prompt that runs automatically on a schedule; each run becomes a normal chat in their history). Interview them briefly, then create it.

RULES:
- Ask ONE question at a time. Keep it to 1-3 questions total — only what you actually need (when it should run; and if the request is vague, what it should focus on). Don't ask about integrations you can't verify.
- Emit each question as a block EXACTLY like this (raw JSON, no code fence), then STOP your turn and wait for the answer:
<jarvisQuestion>
{"question":"What time should it run each weekday?","multiSelect":false,"options":[{"label":"6:00 AM"},{"label":"7:00 AM","recommended":true},{"label":"8:00 AM"}]}
</jarvisQuestion>
- The client renders that block as tappable options and sends the user's choice back as their next message. A short sentence of context before the block is fine.
- Once you have what you need, CREATE the task by emitting this block (raw JSON, no code fence). Use a standard 5-field cron in the user's local time:
<jarvisCreateSchedule>
{"name":"Daily brief","prompt":"Give me a concise weekday morning brief: ...","cron":"0 7 * * 1-5","label":"Weekdays at 7:00 AM"}
</jarvisCreateSchedule>
- The "prompt" is what runs each time — write it as a complete standalone instruction, not a reference to this conversation. After the create block, confirm in one sentence what you set up.
- Do not claim the task is created until you have emitted the <jarvisCreateSchedule> block.`;
}
