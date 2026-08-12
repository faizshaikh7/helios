import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Script from "next/script";
import { THEME_INIT_SCRIPT } from "@/components/ThemeToggle";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Helios",
  description:
    "Orbital mechanics answers computed by real tools, with explicit assumptions and sources.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      {/*
       * suppressHydrationWarning on body as well as html: extensions such as password managers
       * and grammar checkers inject attributes into <body> before React hydrates, which React
       * reports as a mismatch on every load. The warning is real but not ours, and leaving it
       * buries genuine hydration bugs in noise.
       */}
      <body
        className="min-h-full flex flex-col bg-background text-foreground"
        suppressHydrationWarning
      >
        {/*
         * Applies a stored theme before hydration, so someone who chose dark never sees a white
         * flash first.
         *
         * Must go through next/script rather than a raw <script> tag: React 19 treats a bare
         * script element in the tree as a rendering error, which surfaces as a dev overlay.
         * `beforeInteractive` injects it into the document head regardless of placement here.
         */}
        <Script
          id="helios-theme-init"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }}
        />
        {children}
      </body>
    </html>
  );
}
