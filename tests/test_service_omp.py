"""Smoke loads the real extension into installed OMP without making model calls."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.environ.get("BROWSER_INTEGRATION_TESTS") != "1", reason="Final integration verification opt-in")]


async def test_actual_omp_extension_load(tmp_path):
    if not shutil.which("omp"):
        pytest.skip("OMP not installed")
    probe = tmp_path / "probe.mjs"
    probe.write_text('export default function(pi) { pi.on("session_start", () => { const tool = pi.getAllTools().find(t => t.name === "browser_agent"); console.log(JSON.stringify({ browser_extension_probe: tool ? { name: tool.name, description: tool.description } : null })); }); }')
    extension = Path(__file__).resolve().parents[1] / "integrations/omp/browser-tools.mjs"
    process = await asyncio.create_subprocess_exec("omp", "--mode", "rpc", "--no-ui", "--no-session", "--no-extensions", "-e", str(extension), "-e", str(probe), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env={**os.environ, "BROWSER_AGENT_PYTHON": sys.executable})
    frames = []
    try:
        async with asyncio.timeout(30):
            while True:
                line = await process.stdout.readline()
                assert line, (await process.stderr.read()).decode()
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                frames.append(frame)
                assert frame.get("type") != "extension_error", frame
                if "browser_extension_probe" in frame:
                    assert frame["browser_extension_probe"]["name"] == "browser_agent"
                    break
            process.stdin.write((json.dumps({"id": "doctor-proof", "type": "prompt", "message": "/browser-agent-doctor"}) + "\n").encode())
            await process.stdin.drain()
            doctor_seen = False
            completed_locally = False
            while not (doctor_seen and completed_locally):
                line = await process.stdout.readline()
                assert line, frames
                frame = json.loads(line)
                frames.append(frame)
                assert frame.get("type") != "extension_error", frame
                if "browser-automation.doctor" in json.dumps(frame):
                    assert "local stdio" in json.dumps(frame), frame
                    doctor_seen = True
                if frame.get("id") == "doctor-proof" and (frame.get("agentInvoked") is False or frame.get("data", {}).get("agentInvoked") is False):
                    completed_locally = True
            (tmp_path / "omp-proof.json").write_text(json.dumps(frames, indent=2))
    finally:
        process.stdin.close()
        output, errors = await asyncio.wait_for(process.communicate(), 15)
    assert process.returncode == 0, errors.decode()
    assert b"extension_error" not in output


async def test_omp_extension_persistent_progress_callback(tmp_path, monkeypatch):
    """Invoke the real extension's public execute callback against its real worker.

    Installed OMP loader acceptance above is separate; this consumer isolates the
    documented onUpdate adapter without requiring an unavailable paid model.
    """
    from test_agent_surface import deterministic_fixture
    extension = Path(__file__).resolve().parents[1] / "integrations/omp/browser-tools.mjs"
    async with deterministic_fixture() as origin:
        script = tmp_path / "progress-consumer.mjs"
        script.write_text('''import extension from ''' + json.dumps(extension.as_uri()) + ''';
const chain = new Proxy(() => {}, { get: () => chain, apply: () => chain });
let tool; let shutdown;
extension({ zod: chain, registerTool(value) { tool = value; }, registerCommand() {}, on(name, callback) { if (name === "session_shutdown") shutdown = callback; } });
const invoke = async (command, arguments_, onUpdate) => {
  const result = await tool.execute("host-" + command, {command, arguments: arguments_}, undefined, onUpdate);
  return result.details;
};
try {
  const opened = await invoke("launch", {headless:true});
  const created = await invoke("new_tab", {session_id:opened.session_id,url:process.env.FIXTURE_ORIGIN});
  const args = {session_id:opened.session_id,tab_id:created.tab.id};
  let final = false; const updates = [];
  const result = await invoke("run", {...args,goal:"Read the Visible READY heading",screenshot:false,max_steps:3}, update => {
    if (final) throw new Error("Late progress");
    updates.push(update.details);
    console.log(JSON.stringify({interim:update.details}));
  });
  final = true;
  if (!updates.length || result.status !== "success") throw new Error("Missing progress or final result");
  if (new Set(updates.map(update => update.request_id)).size !== 1) throw new Error("Lost request IDs");
  if (!(await invoke("tabs", {session_id:opened.session_id})).tabs.some(tab => tab.id === created.tab.id)) throw new Error("Worker session lost");
  await invoke("close", {session_id:opened.session_id});
  console.log(JSON.stringify({final:result.status,updates:updates.length}));
} finally { shutdown(); }
''')
        env = {**os.environ, "BROWSER_AGENT_PYTHON": sys.executable, "FIXTURE_ORIGIN": origin, "OPENROUTER_API_KEY": "local-deterministic-fixture-not-live", "BROWSER_AGENT_DECISIONS_ENDPOINT": origin + "/decisions", "BROWSER_AGENT_VISION": "false"}
        process = await asyncio.create_subprocess_exec("node", str(script), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        output, errors = await asyncio.wait_for(process.communicate(), 45)
    assert process.returncode == 0, errors.decode()
    frames = [json.loads(line) for line in output.decode().splitlines()]
    assert "interim" in frames[0] and frames[-1]["final"] == "success"
    (tmp_path / "omp-progress-proof.json").write_text(json.dumps(frames, indent=2))
