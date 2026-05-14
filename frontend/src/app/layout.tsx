import "@/styles/globals.css";
import "katex/dist/katex.min.css";

import { headers } from "next/headers";
import { type Metadata, type Viewport } from "next";

import { ThemeProvider } from "@/components/theme-provider";
import { I18nProvider } from "@/core/i18n/context";
import { detectLocaleServer } from "@/core/i18n/server";

export const metadata: Metadata = {
  title: "DeerFlow",
  description: "A LangChain-based framework for building super agents.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

function isMobileUA(userAgent: string | null): boolean {
  if (!userAgent) return false;
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
    userAgent,
  );
}

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const locale = await detectLocaleServer();
  const headersList = await headers();
  const ua = headersList.get("user-agent");
  const device = isMobileUA(ua) ? "mobile" : "desktop";
  return (
    <html
      lang={locale}
      data-device={device}
      suppressContentEditableWarning
      suppressHydrationWarning
    >
      <body>
        <ThemeProvider attribute="class" enableSystem disableTransitionOnChange>
          <I18nProvider initialLocale={locale}>{children}</I18nProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
