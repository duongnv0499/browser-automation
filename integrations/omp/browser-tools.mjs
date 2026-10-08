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
    const lines = createInterface({ input: worker.stdout });
    lines.on("line", line => {
      let response;
      try { response = JSON.parse(line); } catch { return; }
      const waiter = pending.get(response.id);
      if (!waiter) return;
      pending.delete(response.id);
      response.error ? waiter.reject(new Error(`${response.error.code}: ${response.error.message}`)) : waiter.resolve(response.result);
    });
    const fail = error => {
      for (const waiter of pending.values()) waiter.reject(error);
      pending.clear();
      worker = undefined;
    };
    worker.on("error", fail);
    worker.on("exit", code => { lines.close(); fail(new Error(`Browser worker exited (${code}); previous session IDs are invalid`)); });
  }
  pi.registerTool({
    name: "browser_agent",
    label: "Browser Agent",
    description: "Persistent local browser sessions. First launch isolated or connect consented loopback Chrome, then retain session_id/tab_id/observation revision. Observe screenshot, act bound targets, paginate text, run Luna goal. File operations require exact HOST approval; no approval mint tool. No selectors/JS. Closing attached session preserves user Chrome.",
    parameters: z.object({
      command: z.enum(["doctor", "launch", "connect", "connect_default", "tabs", "new_tab", "observe", "act", "approved_act", "text", "run", "upload", "download", "close_tab", "close"]),
      arguments: z.record(z.string(), z.unknown()).optional(),
    }),
    async execute(_id, params, signal) {
      if (signal?.aborted) throw new Error("Cancelled");
      start();
      const id = ++sequence;
      const result = await new Promise((resolve, reject) => {
        const abort = () => {
          pending.delete(id);
          // SIGINT lets asyncio cancel active work and run owned-resource cleanup.
          worker?.kill("SIGINT");
          reject(new Error("Cancelled; browser worker disconnecting, re-open session"));
        };
        signal?.addEventListener("abort", abort, { once: true });
        const finish = fn => value => { signal?.removeEventListener("abort", abort); fn(value); };
        pending.set(id, { resolve: finish(resolve), reject: finish(reject) });
        worker.stdin.write(JSON.stringify({ id, command: params.command, arguments: params.arguments || {} }) + "\n", error => {
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
  });
  pi.on("session_shutdown", () => { worker?.stdin.end(); });
}
