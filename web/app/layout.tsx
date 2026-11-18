import type { Metadata, Viewport } from "next";
import "./globals.css";

const title = "Wanderlake";
const description =
  "Global travel intelligence on a self-built open-source lakehouse, with AI.";

export const metadata: Metadata = {
  metadataBase: new URL("https://wanderlake.vercel.app"),
  title,
  description,
  openGraph: {
    title,
    description,
    url: "/",
    siteName: title,
    type: "website",
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f7f4" },
    { media: "(prefers-color-scheme: dark)", color: "#14161a" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
