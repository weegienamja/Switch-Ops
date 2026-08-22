import type { Metadata, Viewport } from "next";
import "./globals.css";
import AnimatedBackground from "@/components/AnimatedBackground";

export const metadata: Metadata = {
  title: "SwitchOps",
  description:
    "Local desktop network operations dashboard for the SWITCHOPS-TEST-SW1 Cisco Catalyst.",
};

export const viewport: Viewport = {
  themeColor: "#0a0f1a",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <AnimatedBackground />
        <div className="bg-fixed" aria-hidden />
        <div className="bg-grid" aria-hidden />
        {children}
      </body>
    </html>
  );
}
