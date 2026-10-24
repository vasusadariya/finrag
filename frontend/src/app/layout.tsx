import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FinRAG - cited answers from SEC 10-K filings",
  description: "Ask questions about 8 large companies' annual reports and get answers with sources.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
