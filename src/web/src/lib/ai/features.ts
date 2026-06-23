import {
  AudioLines,
  Bot,
  Brain,
  Code2,
  Compass,
  Crosshair,
  Eye,
  FileText,
  Film,
  FolderKanban,
  FolderPlus,
  Globe,
  Image as ImageIcon,
  Layers,
  ListTodo,
  Mic,
  Monitor,
  MousePointer,
  Package,
  Palette,
  PenLine,
  Presentation,
  Search,
  Table2,
  Terminal,
  Users,
  type LucideIcon,
} from "lucide-react";
import type { Provider } from "./models-meta";

export type Feature = {
  slug: string;
  label: string;
  icon: LucideIcon;
  description: string;
  /** When true, the sidebar hides the item under "More" instead of listing it. */
  overflow?: boolean;
  /** Small badge rendered next to the label (e.g., "Beta", "New"). */
  badge?: string;
  /**
   * Override sidebar link target. When set, the sidebar renders this href
   * instead of the default `/{provider}/{slug}` placeholder route — used for
   * features that have a real top-level page (e.g. Projects).
   */
  href?: string;
};

export const PROVIDER_FEATURES: Record<Provider, Feature[]> = {
  anthropic: [
    {
      slug: "projects",
      label: "Projects",
      icon: FolderKanban,
      href: "/projects",
      description:
        "Bundle chats, files, and instructions into reusable workspaces. Claude starts every session with the same context.",
    },
    {
      slug: "artifacts",
      label: "Artifacts",
      icon: Package,
      href: "/artifacts",
      description:
        "Versioned, sandboxed code and document blocks. React components, HTML, SVG, and markdown — rendered live in the preview pane.",
    },
    // "Code" feature removed from the anthropic provider list — the
    // sidebar's top-level CORE_NAV already has a Code entry linking to
    // /code, and rendering both produced a duplicate "Code" item in the
    // sidebar.
    {
      slug: "design",
      label: "Design",
      icon: Palette,
      href: "/design",
      description:
        "UI mockups, color explorations, and visual drafts. Rendered live in the preview pane.",
    },
    {
      slug: "computer-use",
      label: "Computer use",
      icon: Monitor,
      href: "/computer-use",
      badge: "Beta",
      description:
        "Let Jarvis see and drive your desktop — watch it click and type, approve actions, and take control anytime.",
    },
  ],

  openai: [
    {
      slug: "codex",
      label: "Codex",
      icon: Terminal,
      description:
        "Cloud sandboxes that run your code, tests, and commits against a repo.",
    },
    {
      slug: "gpts",
      label: "GPTs",
      icon: Bot,
      overflow: true,
      description:
        "Pre-configured assistants with tools and instructions. Save one per task — analyst, reviewer, teacher.",
    },
    {
      slug: "canvas",
      label: "Canvas",
      icon: PenLine,
      overflow: true,
      description:
        "Side-by-side editor for long writing and code. Comments, suggestions, inline rewrites.",
    },
    {
      slug: "deep-research",
      label: "Deep Research",
      icon: Compass,
      overflow: true,
      description:
        "Multi-step web research. Returns a sourced report, not a quick answer.",
    },
    {
      slug: "operator",
      label: "Operator",
      icon: MousePointer,
      overflow: true,
      description:
        "Browser automation agent. Books, fills, orders, reserves — on your instructions.",
    },
    {
      slug: "tasks",
      label: "Tasks",
      icon: ListTodo,
      overflow: true,
      description:
        "Scheduled and recurring jobs. Morning briefing, weekly digest, monthly report.",
    },
  ],

  google: [
    {
      slug: "deep-research",
      label: "Deep Research",
      icon: Compass,
      description:
        "Gemini runs a research plan, gathers sources, and writes you a full report.",
    },
    {
      slug: "canvas",
      label: "Canvas",
      icon: PenLine,
      description:
        "Live document editor with inline suggestions and version history.",
    },
    {
      slug: "audio-overview",
      label: "Audio Overview",
      icon: AudioLines,
      description:
        "Turn any chat or document into a conversational podcast you can listen to.",
    },
    {
      slug: "imagen",
      label: "Imagen",
      icon: ImageIcon,
      description:
        "Google's image model for product shots, illustrations, and visual drafts.",
    },
    {
      slug: "veo",
      label: "Veo",
      icon: Film,
      description:
        "Generate short video clips from a prompt — animate stills, storyboard scenes.",
    },
  ],

  groq: [
    {
      slug: "compound",
      label: "Compound",
      icon: Layers,
      description:
        "Groq's agent with built-in web search and code execution. Fast because Groq is fast.",
    },
    {
      slug: "speech",
      label: "Speech",
      icon: Mic,
      description:
        "Whisper-grade transcription and Kokoro/PlayAI TTS at Groq speeds.",
    },
    {
      slug: "vision",
      label: "Vision",
      icon: Eye,
      description:
        "Llama 4 + Vision models for image understanding — captions, OCR, diagrams.",
    },
  ],

  deepseek: [
    {
      slug: "search",
      label: "Search",
      icon: Search,
      description:
        "DeepSeek's web-augmented mode — grounded answers with sources.",
    },
    {
      slug: "think",
      label: "Think",
      icon: Brain,
      description:
        "Step-by-step reasoning with R1. Shows the chain of thought before the answer.",
    },
  ],

  kimi: [
    {
      slug: "slides",
      label: "Slides",
      icon: Presentation,
      description:
        "Generate full presentation decks from a prompt or document. Themed, paginated, editable.",
    },
    {
      slug: "websites",
      label: "Websites",
      icon: Globe,
      description:
        "Spin up a functional single-page site from a brief. Preview, iterate, export.",
    },
    {
      slug: "docs",
      label: "Docs",
      icon: FileText,
      description:
        "Long-form document workspace. Outlines, briefs, specs, one-pagers.",
    },
    {
      slug: "deepsearch",
      label: "Deep Research",
      icon: Compass,
      description:
        "Multi-hop web research across Kimi's 256k context. Returns a sourced answer.",
    },
    {
      slug: "sheets",
      label: "Sheets",
      icon: Table2,
      description:
        "Structured data workspace — tables, formulas, analysis from a prompt.",
    },
    {
      slug: "swarm",
      label: "Agent Swarm",
      icon: Users,
      badge: "Beta",
      description:
        "Spawn a team of sub-agents to divide a task. Researcher, writer, critic, editor.",
    },
    {
      slug: "code",
      label: "Kimi Code",
      icon: Code2,
      description:
        "Kimi's coding agent — reads your repo, plans, edits, tests.",
    },
    {
      slug: "klaw",
      label: "Kimi Claw",
      icon: Crosshair,
      badge: "Beta",
      description:
        "Agentic browser automation. Kimi drives a tab to complete a job for you.",
    },
  ],
  // Local (Ollama) — no provider landing-page features; it's a model option
  // in the picker only.
  ollama: [],
};

export function getFeature(
  provider: string,
  slug: string,
): { provider: Provider; feature: Feature } | null {
  if (!(provider in PROVIDER_FEATURES)) return null;
  const p = provider as Provider;
  const feature = PROVIDER_FEATURES[p].find((f) => f.slug === slug);
  if (!feature) return null;
  return { provider: p, feature };
}

/**
 * Optional grouped sections rendered beneath the main provider nav.
 * When a provider defines sections, the sidebar draws labeled groups
 * (e.g., "GPTs", "Projects") with their own items and an optional
 * footer action like "Explore GPTs" or "New project".
 */
export type SidebarItem = {
  label: string;
  href: string;
  icon: LucideIcon;
  /** Small thumbnail glyph/color stripe on the left, e.g., saved GPT avatars. */
  hueClass?: string;
};

export type SidebarSection = {
  label: string;
  items: SidebarItem[];
  footer?: SidebarItem;
};

export const PROVIDER_SECTIONS: Partial<Record<Provider, SidebarSection[]>> = {
  openai: [
    {
      label: "GPTs",
      items: [
        {
          label: "Image generator",
          href: "/openai/gpts",
          icon: ImageIcon,
          hueClass: "bg-emerald-500/30",
        },
        {
          label: "Veo 3 · Text → Video",
          href: "/openai/gpts",
          icon: Film,
          hueClass: "bg-violet-500/30",
        },
      ],
      footer: {
        label: "Explore GPTs",
        href: "/openai/gpts",
        icon: Bot,
      },
    },
    {
      label: "Projects",
      items: [],
      footer: {
        label: "New project",
        href: "/openai/tasks",
        icon: FolderPlus,
      },
    },
  ],
};
