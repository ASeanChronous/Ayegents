export default function HomePage() {
  return (
    <main style={{ padding: 40, fontFamily: "system-ui" }}>
      <h1>Agent Ecosystem</h1>
      <p>
        MVP scaffold: single-agent builder loop (create agent → run → inspect).
        Multi-agent flows and the marketplace UI come in V1.
      </p>
      <p>
        API health check:{" "}
        <code>{process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/health</code>
      </p>
    </main>
  );
}
