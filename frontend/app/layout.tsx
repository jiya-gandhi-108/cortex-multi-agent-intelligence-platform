import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Cortex: cited research from autonomous agents", template: "%s · Cortex" },
  description:
    "Cortex plans research, searches the web, verifies every claim against its sources and writes a cited report.",
};

export const viewport: Viewport = { themeColor: "#16171b", colorScheme: "dark" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
