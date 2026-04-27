import type { Metadata } from "next";
import { Inter, Fraunces, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import "katex/dist/katex.min.css";
import { Providers } from "@/components/providers";

const inter = Inter({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

// Fraunces is the closest free stand-in for Anthropic's Copernicus serif.
// Used for headings and the chat greeting, mirroring claude.ai.
const fraunces = Fraunces({
  variable: "--font-serif",
  subsets: ["latin"],
  display: "swap",
  axes: ["SOFT", "opsz"],
});

const jetbrains = JetBrains_Mono({
  variable: "--font-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Jarvis",
  description: "Personal AI workbench.",
  icons: {
    icon: "/jarvis-logo.svg",
    apple: "/jarvis-logo.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body
        className={`${inter.variable} ${fraunces.variable} ${jetbrains.variable} font-sans antialiased bg-background text-foreground min-h-screen`}
        // Some browser extensions (ColorZilla's `cz-shortcut-listen`,
        // Grammarly's `data-gramm`, etc.) inject attributes onto <body>
        // BEFORE React hydrates, causing a server/client mismatch
        // warning that's harmless but noisy. Suppressing here mirrors
        // the suppression on <html>.
        suppressHydrationWarning
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
