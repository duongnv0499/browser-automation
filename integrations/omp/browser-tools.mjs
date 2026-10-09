import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

// Each extension factory owns a worker; no browser state shared between OMP sessions.
export default function browserTools(pi) {
  const z = pi.zod;
  let worker;
  let sequence = 0;
  const pending = new Map();
  function start() {
    if (worker) return;
    worker = spawn(process.env.BROWSER_AGENT_PYTHON || "python", ["-m", "browser_automation", "serve"], { stdio: ["pipe", "pipe", "ignore"], env: process.env });
    const child = worker;
    const lines = createInterface({ input: child.stdout });
    lines.on("line", line => {
      let response;
      try { response = JSON.parse(line); } catch { return; }
      const waiter = pending.get(response.id);
      if (!waiter || waiter.child !== child) return;
      if (response.progress !== undefined) {
        waiter.onUpdate?.({ content: [{ type: "text", text: JSON.stringify({ progress: response.progress }) }], details: { request_id: response.id, progress: response.progress } });
        return;
      }
      pending.delete(response.id);
      waiter.resolve(response.error ? { status: "error", error: response.error } : response.result);
    });
    const fail = error => {
      for (const [id, waiter] of pending) {
        if (waiter.child === child) { pending.delete(id); waiter.reject(error); }
      }
      if (worker === child) worker = undefined;
    };
    child.on("error", fail);
    child.on("exit", code => { lines.close(); fail(new Error(`Browser worker exited (${code}); previous session IDs are invalid`)); });
  }
  const browserTool = {
    name: "browser_agent",
    label: "Browser Agent",
    description: "Persistent browser sessions. Choose isolated launch or consented native attach, retain IDs, then run an explicit goal with independent verification. Observe diagnostics; interpret_visual opt-in charges provider. network_start_many/network_list_many/network_stop_many capture selected/current/future tabs; network_detail/network_body return sanitized headers, query, payload and chunked response data. network_call/network_replay use browser cookies: safe same-origin reads execute directly, consequential/foreign requests require exact host approval via network_execute; prepare_only inspects. Sensitive disclosure requires host consent. API responses are not rendered UI proof. No selectors/JS or safety bypass. Attached close preserves user Chrome.",
    parameters: z.object({
      command: z.enum(["doctor", "launch", "connect", "connect_default", "tabs", "new_tab", "observe", "act", "approved_act", "text", "run", "upload", "download", "close_tab", "close", "network_start", "network_list", "network_stop", "network_start_many", "network_list_many", "network_stop_many", "network_detail", "network_body", "network_call", "network_replay", "network_execute", "websocket_start", "websocket_list", "websocket_stop"]),
      arguments: z.record(z.string(), z.unknown()).optional(),
    }),
    async execute(_id, params, signal, onUpdate) {
      if (signal?.aborted) throw new Error("Cancelled");
      start();
      const child = worker;
      const id = ++sequence;
      const result = await new Promise((resolve, reject) => {
        const abort = () => {
          pending.delete(id);
          child.stdin.write(JSON.stringify({ command: "cancel", arguments: { request_id: id } }) + "\n");
          reject(new Error("Cancelled; browser session retained"));
        };
        signal?.addEventListener("abort", abort, { once: true });
        const finish = fn => value => { signal?.removeEventListener("abort", abort); fn(value); };
        pending.set(id, { child, resolve: finish(resolve), reject: finish(reject), onUpdate });
        child.stdin.write(JSON.stringify({ id, command: params.command, arguments: params.arguments || {}, progress: typeof onUpdate === "function" }) + "\n", error => {
          if (error) { pending.delete(id); finish(reject)(error); }
        });
      });
      let { screenshot, ...data } = result;
      if (data.observation) {
        data.observation = { ...data.observation };
        screenshot = data.observation.screenshot || screenshot;
        delete data.observation.screenshot;
      }
      const content = [{ type: "text", text: JSON.stringify(data) }];
      if (screenshot) content.push({ type: "image", data: screenshot, mimeType: "image/png" });
      return { content, details: data };
    },
  };
  pi.registerTool(browserTool);
  pi.registerCommand("browser-agent-doctor", {
    description: "Check the browser worker through the same registered tool, without a model call",
    async handler() {
      const result = await browserTool.execute("host-doctor", { command: "doctor" });
      pi.sendMessage({ customType: "browser-automation.doctor", content: result.content, display: true, details: result.details });
    },
  });
  pi.on("session_shutdown", () => { worker?.stdin.end(); });
}
