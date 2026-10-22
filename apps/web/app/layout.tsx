export const metadata = {
  title: "Agent Ecosystem",
  description: "Build, run, and discover AI agents.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
