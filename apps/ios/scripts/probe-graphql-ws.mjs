const endpoint = process.argv[2];

if (!endpoint) {
  console.error("Usage: node probe-graphql-ws.mjs ws[s]://quantx-host/graphql");
  process.exit(64);
}

let url;
try {
  url = new URL(endpoint);
} catch {
  console.error("GraphQL WebSocket endpoint is not a valid URL.");
  process.exit(64);
}

if (url.protocol !== "ws:" && url.protocol !== "wss:") {
  console.error("GraphQL WebSocket endpoint must use ws:// or wss://.");
  process.exit(64);
}

const outcome = await new Promise((resolve) => {
  let settled = false;
  const websocket = new WebSocket(url, ["graphql-transport-ws"]);
  const timeout = setTimeout(() => {
    finish(2, "Timed out waiting for anonymous GraphQL WebSocket rejection.");
  }, 8_000);

  function finish(exitCode, message) {
    if (settled) return;
    settled = true;
    clearTimeout(timeout);
    try {
      websocket.close();
    } catch {
      // The socket may already be closed by the server.
    }
    resolve({ exitCode, message });
  }

  websocket.addEventListener("open", () => {
    websocket.send(JSON.stringify({ type: "connection_init" }));
  });

  websocket.addEventListener("message", (event) => {
    let message;
    try {
      message = JSON.parse(String(event.data));
    } catch {
      finish(1, "Server returned an invalid GraphQL WebSocket message.");
      return;
    }

    const errorCode = message.payload?.code;
    if (
      message.type === "connection_error" &&
      (errorCode === "UNAUTHENTICATED" || errorCode === "FORBIDDEN")
    ) {
      finish(0, `Anonymous GraphQL WebSocket rejected with ${errorCode}.`);
      return;
    }
    if (message.type === "connection_ack") {
      finish(1, "Server accepted an anonymous GraphQL WebSocket connection.");
      return;
    }
    finish(1, `Unexpected GraphQL WebSocket response: ${message.type ?? "unknown"}.`);
  });

  websocket.addEventListener("close", (event) => {
    if (event.code === 4401 || event.code === 4403) {
      finish(0, `Anonymous GraphQL WebSocket rejected with close code ${event.code}.`);
      return;
    }
    finish(
      2,
      `GraphQL WebSocket closed without a recognized authentication code (${event.code}).`
    );
  });

  websocket.addEventListener("error", () => {
    // WebSocket can emit a generic error immediately before the server's
    // authoritative authentication close code. Let "close" or the timeout
    // determine the result.
  });
});

console.error(outcome.message);
process.exitCode = outcome.exitCode;
