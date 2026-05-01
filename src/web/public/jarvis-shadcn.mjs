// jarvis-shadcn — curated shadcn-pattern primitives for the design tab.
// Single ESM file, no build step. Imported as `/jarvis-shadcn.mjs` from
// the iframe so the model doesn't have to re-implement Button/Card/Input
// every generation.
//
// Conventions:
// - All components consume the theme CSS variables: --bg, --fg, --accent,
//   --muted, --supporting. Define these in the entry HTML's :root and the
//   primitives inherit automatically.
// - Components use Tailwind arbitrary-value classes (`bg-[var(--bg)]`)
//   so they resolve at runtime via @tailwindcss/browser without needing
//   any tailwind.config customization.
// - Interactive components (Dialog, Tabs, Sheet) wrap Radix primitives.

import { jsx, jsxs, Fragment } from "https://esm.sh/react@18.3.1/jsx-runtime";
import { forwardRef } from "https://esm.sh/react@18.3.1?deps=react@18.3.1";
import * as DialogPrimitive from "https://esm.sh/@radix-ui/react-dialog@1?deps=react@18.3.1,react-dom@18.3.1";
import * as TabsPrimitive from "https://esm.sh/@radix-ui/react-tabs@1?deps=react@18.3.1,react-dom@18.3.1";
import * as TooltipPrimitive from "https://esm.sh/@radix-ui/react-tooltip@1?deps=react@18.3.1,react-dom@18.3.1";
import * as SeparatorPrimitive from "https://esm.sh/@radix-ui/react-separator@1?deps=react@18.3.1,react-dom@18.3.1";
import { X } from "https://esm.sh/lucide-react@0.469?deps=react@18.3.1";

// Tiny classNames helper — replaces clsx/cn for the bundle.
function cn(...parts) {
  return parts.filter(Boolean).join(" ");
}

// ─── Button ──────────────────────────────────────────────────────
const buttonVariants = {
  variant: {
    default:
      "bg-[var(--accent)] text-[var(--bg)] hover:opacity-90 focus-visible:ring-[var(--accent)]",
    outline:
      "border border-[var(--fg)]/20 bg-transparent text-[var(--fg)] hover:bg-[var(--supporting)] focus-visible:ring-[var(--fg)]/30",
    ghost:
      "bg-transparent text-[var(--fg)] hover:bg-[var(--supporting)] focus-visible:ring-[var(--fg)]/30",
    destructive:
      "bg-[#DC2626] text-white hover:bg-[#B91C1C] focus-visible:ring-[#DC2626]",
    secondary:
      "bg-[var(--supporting)] text-[var(--fg)] hover:opacity-90 focus-visible:ring-[var(--fg)]/30",
    link: "bg-transparent text-[var(--accent)] underline-offset-4 hover:underline p-0 h-auto",
  },
  size: {
    sm: "h-8 px-3 text-xs",
    md: "h-10 px-4 text-sm",
    lg: "h-12 px-6 text-base",
    icon: "h-10 w-10 p-0",
  },
};

export const Button = forwardRef(function Button(
  { variant = "default", size = "md", className, asChild, ...props },
  ref,
) {
  return jsx("button", {
    ref,
    className: cn(
      "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors duration-200",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
      "disabled:pointer-events-none disabled:opacity-50",
      buttonVariants.variant[variant] || buttonVariants.variant.default,
      buttonVariants.size[size] || buttonVariants.size.md,
      className,
    ),
    ...props,
  });
});

// ─── Card ────────────────────────────────────────────────────────
export const Card = forwardRef(function Card({ className, ...props }, ref) {
  return jsx("div", {
    ref,
    className: cn(
      "rounded-lg border border-[var(--fg)]/10 bg-[var(--supporting)] text-[var(--fg)]",
      className,
    ),
    ...props,
  });
});

export const CardHeader = forwardRef(function CardHeader(
  { className, ...props },
  ref,
) {
  return jsx("div", {
    ref,
    className: cn("flex flex-col space-y-1.5 p-6", className),
    ...props,
  });
});

export const CardTitle = forwardRef(function CardTitle(
  { className, ...props },
  ref,
) {
  return jsx("h3", {
    ref,
    className: cn(
      "text-2xl font-semibold leading-tight tracking-tight",
      className,
    ),
    ...props,
  });
});

export const CardDescription = forwardRef(function CardDescription(
  { className, ...props },
  ref,
) {
  return jsx("p", {
    ref,
    className: cn("text-sm text-[var(--muted)]", className),
    ...props,
  });
});

export const CardContent = forwardRef(function CardContent(
  { className, ...props },
  ref,
) {
  return jsx("div", { ref, className: cn("p-6 pt-0", className), ...props });
});

export const CardFooter = forwardRef(function CardFooter(
  { className, ...props },
  ref,
) {
  return jsx("div", {
    ref,
    className: cn("flex items-center p-6 pt-0", className),
    ...props,
  });
});

// ─── Input ───────────────────────────────────────────────────────
export const Input = forwardRef(function Input(
  { className, type = "text", ...props },
  ref,
) {
  return jsx("input", {
    ref,
    type,
    className: cn(
      "flex h-10 w-full rounded-md border border-[var(--fg)]/15 bg-[var(--bg)] px-3 py-2 text-sm",
      "placeholder:text-[var(--muted)]",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2",
      "disabled:cursor-not-allowed disabled:opacity-50",
      className,
    ),
    ...props,
  });
});

// ─── Label ───────────────────────────────────────────────────────
export const Label = forwardRef(function Label(
  { className, ...props },
  ref,
) {
  return jsx("label", {
    ref,
    className: cn(
      "text-sm font-medium leading-none text-[var(--fg)] peer-disabled:cursor-not-allowed peer-disabled:opacity-70",
      className,
    ),
    ...props,
  });
});

// ─── Badge ───────────────────────────────────────────────────────
const badgeVariants = {
  default: "bg-[var(--accent)] text-[var(--bg)]",
  secondary: "bg-[var(--supporting)] text-[var(--fg)]",
  outline: "border border-[var(--fg)]/20 text-[var(--fg)]",
  destructive: "bg-[#DC2626] text-white",
};

export function Badge({ variant = "default", className, ...props }) {
  return jsx("div", {
    className: cn(
      "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold",
      badgeVariants[variant] || badgeVariants.default,
      className,
    ),
    ...props,
  });
}

// ─── Separator (Radix) ───────────────────────────────────────────
export const Separator = forwardRef(function Separator(
  { className, orientation = "horizontal", decorative = true, ...props },
  ref,
) {
  return jsx(SeparatorPrimitive.Root, {
    ref,
    decorative,
    orientation,
    className: cn(
      "shrink-0 bg-[var(--fg)]/10",
      orientation === "horizontal" ? "h-px w-full" : "h-full w-px",
      className,
    ),
    ...props,
  });
});

// ─── Avatar ──────────────────────────────────────────────────────
export const Avatar = forwardRef(function Avatar(
  { className, children, ...props },
  ref,
) {
  return jsx("span", {
    ref,
    className: cn(
      "relative flex h-10 w-10 shrink-0 overflow-hidden rounded-full bg-[var(--supporting)]",
      className,
    ),
    children,
    ...props,
  });
});

export function AvatarImage({ className, src, alt, ...props }) {
  return jsx("img", {
    src,
    alt,
    className: cn("aspect-square h-full w-full object-cover", className),
    ...props,
  });
}

export function AvatarFallback({ className, children, ...props }) {
  return jsx("span", {
    className: cn(
      "flex h-full w-full items-center justify-center bg-[var(--supporting)] text-[var(--fg)] text-sm font-medium",
      className,
    ),
    children,
    ...props,
  });
}

// ─── Tabs (Radix) ────────────────────────────────────────────────
export const Tabs = TabsPrimitive.Root;

export const TabsList = forwardRef(function TabsList(
  { className, ...props },
  ref,
) {
  return jsx(TabsPrimitive.List, {
    ref,
    className: cn(
      "inline-flex h-10 items-center justify-center rounded-md bg-[var(--supporting)] p-1 text-[var(--muted)]",
      className,
    ),
    ...props,
  });
});

export const TabsTrigger = forwardRef(function TabsTrigger(
  { className, ...props },
  ref,
) {
  return jsx(TabsPrimitive.Trigger, {
    ref,
    className: cn(
      "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1.5 text-sm font-medium transition-all",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
      "disabled:pointer-events-none disabled:opacity-50",
      "data-[state=active]:bg-[var(--bg)] data-[state=active]:text-[var(--fg)] data-[state=active]:shadow-sm",
      className,
    ),
    ...props,
  });
});

export const TabsContent = forwardRef(function TabsContent(
  { className, ...props },
  ref,
) {
  return jsx(TabsPrimitive.Content, {
    ref,
    className: cn(
      "mt-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
      className,
    ),
    ...props,
  });
});

// ─── Dialog (Radix) ──────────────────────────────────────────────
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogPortal = DialogPrimitive.Portal;
export const DialogClose = DialogPrimitive.Close;

const DialogOverlay = forwardRef(function DialogOverlay(
  { className, ...props },
  ref,
) {
  return jsx(DialogPrimitive.Overlay, {
    ref,
    className: cn(
      "fixed inset-0 z-50 bg-black/70 backdrop-blur-sm",
      "data-[state=open]:animate-in data-[state=closed]:animate-out",
      "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className,
    ),
    ...props,
  });
});

export const DialogContent = forwardRef(function DialogContent(
  { className, children, ...props },
  ref,
) {
  return jsxs(DialogPortal, {
    children: [
      jsx(DialogOverlay, {}),
      jsxs(DialogPrimitive.Content, {
        ref,
        className: cn(
          "fixed left-1/2 top-1/2 z-50 grid w-full max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4 p-6",
          "rounded-lg border border-[var(--fg)]/10 bg-[var(--supporting)] text-[var(--fg)] shadow-lg",
          className,
        ),
        ...props,
        children: [
          children,
          jsxs(DialogPrimitive.Close, {
            className:
              "absolute right-4 top-4 rounded-sm opacity-70 ring-offset-2 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-[var(--accent)]",
            children: [jsx(X, { className: "h-4 w-4" }), jsx("span", { className: "sr-only", children: "Close" })],
          }),
        ],
      }),
    ],
  });
});

export function DialogHeader({ className, ...props }) {
  return jsx("div", {
    className: cn("flex flex-col space-y-1.5 text-left", className),
    ...props,
  });
}

export function DialogFooter({ className, ...props }) {
  return jsx("div", {
    className: cn(
      "flex flex-col-reverse gap-2 sm:flex-row sm:justify-end",
      className,
    ),
    ...props,
  });
}

export const DialogTitle = forwardRef(function DialogTitle(
  { className, ...props },
  ref,
) {
  return jsx(DialogPrimitive.Title, {
    ref,
    className: cn("text-lg font-semibold leading-none tracking-tight", className),
    ...props,
  });
});

export const DialogDescription = forwardRef(function DialogDescription(
  { className, ...props },
  ref,
) {
  return jsx(DialogPrimitive.Description, {
    ref,
    className: cn("text-sm text-[var(--muted)]", className),
    ...props,
  });
});

// ─── Tooltip (Radix) ─────────────────────────────────────────────
export const TooltipProvider = TooltipPrimitive.Provider;
export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

export const TooltipContent = forwardRef(function TooltipContent(
  { className, sideOffset = 4, ...props },
  ref,
) {
  return jsx(TooltipPrimitive.Content, {
    ref,
    sideOffset,
    className: cn(
      "z-50 overflow-hidden rounded-md bg-[var(--fg)] text-[var(--bg)] px-3 py-1.5 text-xs",
      "data-[state=open]:animate-in data-[state=closed]:animate-out",
      className,
    ),
    ...props,
  });
});

// ─── Section helper (common landing-page wrapper) ─────────────────
export function Section({ id, className, children, container = true, ...props }) {
  return jsx("section", {
    id,
    className: cn("relative py-16 md:py-24", className),
    ...props,
    children: container
      ? jsx("div", {
          className: "mx-auto max-w-7xl px-4 md:px-8",
          children,
        })
      : children,
  });
}

// Re-export for convenience
export { cn };
