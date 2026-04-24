/**
 * Per-provider chat surface configuration. Each provider can override the
 * greeting, composer placeholder, suggestion chips, `+` menu, Tools-style
 * secondary button, model label trigger, and sidebar recents header —
 * mirroring the visual language of claude.ai, ChatGPT, Gemini, Kimi, etc.
 *
 * Providers without overrides fall back to the Anthropic-style defaults.
 */
import type { ReactNode } from "react";
import {
  Apple,
  AudioLines,
  BookOpen,
  Brain,
  Camera,
  Code2,
  Coffee,
  Compass,
  Database,
  Diamond,
  FileText,
  FilePen,
  Flower,
  GitBranch,
  Globe,
  GraduationCap,
  ImageIcon,
  Images,
  LayoutGrid,
  ListTodo,
  Music4,
  NotebookPen,
  Package,
  PenLine,
  Plug,
  Rocket,
  ScanSearch,
  Search,
  Sparkles,
  UserPlus,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useChatStore } from "@/stores/chat";
import { cn } from "@/lib/utils";
import type { Provider } from "./models-meta";

export type Chip = {
  label: string;
  icon?: LucideIcon;
  prompt: string;
};

export type MenuItem = {
  id: string;
  label: string;
  icon: LucideIcon;
  kind: "action" | "submenu" | "toggle";
  toggled?: boolean;
  badge?: string;
};

export type MenuGroup = {
  label?: string;
  badge?: string;
  items: MenuItem[];
};

export type GreetingCtx = { name?: string; hour: number };

export type InlineToggle = {
  id: string;
  label: string;
  icon: LucideIcon;
  defaultOn?: boolean;
};

export type ProviderUX = {
  placeholder: string;
  /** Renders the content above the composer on a fresh chat. */
  renderGreeting: (ctx: GreetingCtx) => ReactNode;
  /**
   * Optional control strip rendered ABOVE the composer. Used by DeepSeek for
   * the Instant/Expert mode toggle. Rendered both in empty-state and while
   * chatting.
   */
  renderPreComposer?: () => ReactNode;
  /** Suggestion chips rendered below the composer. Empty = none. */
  chips: Chip[];
  /** Click the `+` in the composer → this grouped menu. */
  plus: MenuGroup[];
  /**
   * Inline pill toggles rendered at the composer bottom-left *in place of*
   * the `+` menu. Used by DeepSeek for DeepThink + Search. Omit to use `plus`.
   */
  inlineToggles?: InlineToggle[];
  /** Optional secondary button (Tools / Agent) next to `+`. */
  secondary?: {
    label: string;
    icon: LucideIcon;
    groups: MenuGroup[];
  };
  /** Hide the model picker in the composer (e.g., DeepSeek uses its own). */
  hideComposerModelPicker?: boolean;
  /** Optional override for the sidebar recents header. Defaults to "Recents". */
  recentsLabel?: string;
  /** Optional shortener for the model label in the composer trigger. */
  modelShortLabel?: (fullLabel: string, modelId: string) => string | null;
};

// ─── Shared text greetings ────────────────────────────────────────────────

function ClaudeGreeting({ name, hour }: GreetingCtx) {
  const tod =
    hour < 5
      ? "Still up"
      : hour < 12
        ? "Good morning"
        : hour < 18
          ? "Good afternoon"
          : "Good evening";
  const main = name ? `${tod}, ${name}` : tod;
  return (
    <div className="flex flex-col items-start">
      <h1 className="font-serif text-4xl font-normal tracking-tight text-foreground md:text-5xl">
        <span className="text-primary">✻</span> <span>{main}</span>
      </h1>
    </div>
  );
}

function OpenAIGreeting(_ctx: GreetingCtx) {
  return (
    <div className="flex flex-col items-center">
      <h1 className="font-serif text-3xl font-normal tracking-tight text-foreground md:text-4xl">
        What are you working on?
      </h1>
    </div>
  );
}

function GeminiGreeting({ name }: GreetingCtx) {
  const pre = name ? `Hi ${name.split(" ").pop()}` : "Hi there";
  return (
    <div className="flex flex-col items-start">
      <p className="font-serif text-2xl font-normal text-foreground/70 md:text-3xl">
        {pre}
      </p>
      <h1 className="font-serif text-4xl font-normal tracking-tight text-foreground md:text-5xl">
        Where should we start?
      </h1>
    </div>
  );
}

function KimiGreeting(_ctx: GreetingCtx) {
  return (
    <div className="flex flex-col items-center py-6">
      <div className="flex items-baseline gap-1 font-serif text-6xl font-semibold tracking-tight md:text-7xl">
        <span className="bg-linear-to-br from-sky-400 to-blue-500 bg-clip-text text-transparent">
          K
        </span>
        <span className="bg-linear-to-br from-amber-400 to-rose-400 bg-clip-text text-transparent">
          i
        </span>
        <span className="bg-linear-to-br from-emerald-400 to-teal-400 bg-clip-text text-transparent">
          m
        </span>
        <span className="bg-linear-to-br from-rose-400 to-orange-400 bg-clip-text text-transparent">
          i
        </span>
      </div>
    </div>
  );
}

function DeepSeekGreeting(_ctx: GreetingCtx) {
  return <DeepSeekGreetingInner />;
}

function DeepSeekGreetingInner() {
  const model = useChatStore((s) => s.model);
  const mode = model === "deepseek-reasoner" ? "Expert" : "Instant";
  return (
    <div className="flex items-center justify-center gap-3">
      <span className="text-4xl">🐳</span>
      <h1 className="text-2xl font-medium tracking-tight text-foreground md:text-3xl">
        Start chatting with <span className="text-primary">{mode}</span>
      </h1>
    </div>
  );
}

function DeepSeekPreComposer() {
  // Outer fn is the ReactNode factory consumed by Composer — it must NOT call
  // hooks itself (Composer would attribute them to its own hook order and
  // violate the Rules of Hooks). The inner component owns the hooks.
  return <DeepSeekPreComposerInner />;
}

function DeepSeekPreComposerInner() {
  const model = useChatStore((s) => s.model);
  const setModel = useChatStore((s) => s.setModel);

  const modes: Array<{
    id: "instant" | "expert";
    label: string;
    icon: LucideIcon;
    modelId: string;
  }> = [
    { id: "instant", label: "Instant", icon: Zap, modelId: "deepseek-chat" },
    { id: "expert", label: "Expert", icon: Diamond, modelId: "deepseek-reasoner" },
  ];

  const activeId = model === "deepseek-reasoner" ? "expert" : "instant";

  return (
    <div className="mb-4 flex items-center justify-center gap-1 rounded-full border border-border bg-card/40 p-1 w-fit mx-auto">
      {modes.map((m) => {
        const active = m.id === activeId;
        return (
          <button
            key={m.id}
            type="button"
            onClick={() => setModel(m.modelId)}
            className={cn(
              "flex items-center gap-1.5 rounded-full px-4 py-1.5 text-[13px] font-medium transition-colors",
              active
                ? "bg-primary/20 text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <m.icon
              className={cn(
                "size-3.5",
                active ? "text-primary" : "text-muted-foreground",
              )}
            />
            {m.label}
          </button>
        );
      })}
    </div>
  );
}

function GroqGreeting(_ctx: GreetingCtx) {
  return (
    <div className="flex flex-col items-center">
      <h1 className="font-mono text-2xl font-semibold uppercase tracking-[0.2em] text-foreground md:text-3xl">
        At Groq speed
      </h1>
    </div>
  );
}

// ─── Per-provider configuration ───────────────────────────────────────────

const DEFAULT_UX: ProviderUX = {
  placeholder: "How can I help you today?",
  renderGreeting: ClaudeGreeting,
  chips: [
    { label: "Write", icon: PenLine, prompt: "Help me write " },
    { label: "Learn", icon: GraduationCap, prompt: "Teach me about " },
    { label: "Code", icon: Code2, prompt: "Write a function that " },
    { label: "Life stuff", icon: Coffee, prompt: "Help me think through " },
    {
      label: "Claude's choice",
      icon: Apple,
      prompt:
        "Ask me three questions that'll get me unstuck on whatever I'm working on.",
    },
  ],
  plus: [
    {
      items: [
        { id: "files", label: "Add files or photos", icon: FilePen, kind: "action" },
        { id: "screenshot", label: "Take a screenshot", icon: Camera, kind: "action" },
      ],
    },
    {
      items: [
        { id: "project", label: "Add to project", icon: LayoutGrid, kind: "submenu" },
        { id: "github", label: "Add from GitHub", icon: GitBranch, kind: "action" },
      ],
    },
    {
      items: [
        { id: "skills", label: "Skills", icon: ListTodo, kind: "submenu" },
        { id: "connectors", label: "Connectors", icon: Plug, kind: "submenu" },
      ],
    },
    {
      items: [
        { id: "research", label: "Research", icon: ScanSearch, kind: "action" },
        {
          id: "web-search",
          label: "Web search",
          icon: Compass,
          kind: "toggle",
          toggled: true,
        },
        { id: "style", label: "Use style", icon: Sparkles, kind: "submenu" },
      ],
    },
  ],
};

const OPENAI_UX: ProviderUX = {
  placeholder: "Ask anything",
  renderGreeting: OpenAIGreeting,
  chips: [
    { label: "Create image", icon: Images, prompt: "Create an image of " },
    { label: "Write or edit", icon: PenLine, prompt: "Help me write " },
    { label: "Look something up", icon: Search, prompt: "Look up " },
  ],
  plus: [
    {
      items: [
        { id: "files", label: "Add photos & files", icon: ImageIcon, kind: "action" },
        { id: "recent", label: "Recent files", icon: FileText, kind: "submenu" },
      ],
    },
    {
      items: [
        { id: "create-image", label: "Create image", icon: Images, kind: "action" },
        { id: "deep-research", label: "Deep research", icon: Compass, kind: "action" },
        {
          id: "web-search",
          label: "Web search",
          icon: Search,
          kind: "toggle",
          toggled: true,
        },
      ],
    },
    {
      items: [
        { id: "more", label: "More", icon: Rocket, kind: "submenu" },
        { id: "projects", label: "Projects", icon: LayoutGrid, kind: "submenu" },
      ],
    },
  ],
};

const GEMINI_UX: ProviderUX = {
  placeholder: "Ask Gemini",
  renderGreeting: GeminiGreeting,
  chips: [
    { label: "Create image", icon: Images, prompt: "Create an image of " },
    { label: "Create music", icon: Music4, prompt: "Create music that " },
    { label: "Boost my day", icon: Sparkles, prompt: "Help me plan a great day. " },
    { label: "Write anything", icon: PenLine, prompt: "Help me write " },
    { label: "Help me learn", icon: BookOpen, prompt: "Teach me about " },
  ],
  plus: [
    {
      items: [
        { id: "upload", label: "Upload files", icon: FilePen, kind: "action" },
        { id: "drive", label: "Add from Drive", icon: Flower, kind: "action" },
        { id: "photos", label: "Photos", icon: Images, kind: "action" },
        { id: "notebook", label: "NotebookLM", icon: NotebookPen, kind: "action" },
      ],
    },
  ],
  secondary: {
    label: "Tools",
    icon: Package,
    groups: [
      {
        label: "Tools",
        items: [
          { id: "create-image", label: "Create image", icon: Images, kind: "action" },
          { id: "canvas", label: "Canvas", icon: PenLine, kind: "action" },
          { id: "deep-research", label: "Deep research", icon: Compass, kind: "action" },
          {
            id: "create-music",
            label: "Create music",
            icon: Music4,
            kind: "action",
            badge: "New",
          },
          { id: "learn", label: "Learn", icon: BookOpen, kind: "action" },
        ],
      },
      {
        label: "Experimental features",
        badge: "Labs",
        items: [
          {
            id: "personal-intelligence",
            label: "Personal Intelligence",
            icon: UserPlus,
            kind: "toggle",
            toggled: true,
          },
        ],
      },
    ],
  },
  modelShortLabel: (_label, id) => {
    if (id === "gemini-2.5-flash") return "Fast";
    if (id === "gemini-2.5-pro") return "Pro";
    return null;
  },
};

const KIMI_UX: ProviderUX = {
  placeholder: "Ask Anything...",
  renderGreeting: KimiGreeting,
  chips: [],
  plus: [
    {
      items: [
        { id: "files", label: "Add files & photos", icon: FilePen, kind: "action" },
        { id: "presets", label: "Presets", icon: Package, kind: "action" },
        { id: "pro-data", label: "Professional Data", icon: Database, kind: "action" },
        { id: "web-search", label: "Web search", icon: Compass, kind: "submenu" },
      ],
    },
  ],
  secondary: {
    label: "Agent",
    icon: Sparkles,
    groups: [
      {
        label: "Agent modes",
        items: [
          { id: "default", label: "Default", icon: Sparkles, kind: "action" },
          { id: "deep-research", label: "Deep Research", icon: Compass, kind: "action" },
          { id: "slides", label: "Slides", icon: Package, kind: "action" },
          { id: "websites", label: "Websites", icon: Code2, kind: "action" },
          { id: "docs", label: "Docs", icon: FileText, kind: "action" },
          { id: "sheets", label: "Sheets", icon: LayoutGrid, kind: "action" },
        ],
      },
    ],
  },
  recentsLabel: "Chat History",
};

const DEEPSEEK_UX: ProviderUX = {
  placeholder: "Message DeepSeek",
  renderGreeting: DeepSeekGreeting,
  renderPreComposer: DeepSeekPreComposer,
  chips: [],
  plus: DEFAULT_UX.plus,
  inlineToggles: [
    { id: "deepthink", label: "DeepThink", icon: Brain, defaultOn: false },
    { id: "search", label: "Search", icon: Globe, defaultOn: true },
  ],
  modelShortLabel: (_label, id) =>
    id === "deepseek-reasoner" ? "Expert" : "Instant",
};

const GROQ_UX: ProviderUX = {
  placeholder: "Ask — instantly",
  renderGreeting: GroqGreeting,
  chips: [
    { label: "Compound", icon: AudioLines, prompt: "Use Compound to " },
    { label: "Code", icon: Code2, prompt: "Write a function that " },
    { label: "Summarize", icon: FileText, prompt: "Summarize this: " },
  ],
  plus: DEFAULT_UX.plus,
};

export const PROVIDER_UX: Record<Provider, ProviderUX> = {
  anthropic: DEFAULT_UX,
  openai: OPENAI_UX,
  google: GEMINI_UX,
  kimi: KIMI_UX,
  deepseek: DEEPSEEK_UX,
  groq: GROQ_UX,
};

export function getProviderUX(provider: Provider): ProviderUX {
  return PROVIDER_UX[provider] ?? DEFAULT_UX;
}
