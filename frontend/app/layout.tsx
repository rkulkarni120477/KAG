import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "Northstar · Educational Knowledge", description: "Evidence-led learning, curriculum exploration, and academic guidance." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
