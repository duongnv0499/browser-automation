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
                    catalog = json.dumps(frame["browser_extension_probe"])
                    for name in ("network_detail", "network_body", "network_call", "network_replay", "network_execute", "network_start_many", "network_list_many", "network_stop_many"):
                        assert name in catalog, catalog
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
  const failed = await invoke("act", {...args,action:{observation_id:"expired-revision",operation:"wait",seconds:0.1}});
  if (failed.status !== "error" || failed.error.code !== "unknown_observation" || failed.error.recommended_next_action !== "reobserve") throw new Error("Structured real-worker error lost");
  console.log(JSON.stringify({structured_error:failed}));
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


async def test_omp_changed_region_error_visible_in_result(tmp_path):
    """Fault-only transport fixture, not fabricated browser-success evidence."""
    extension = Path(__file__).resolve().parents[1] / "integrations/omp/browser-tools.mjs"
    worker = tmp_path / "diagnostic-worker"
    worker.write_text("#!" + sys.executable + "\nimport json,sys\nfor line in sys.stdin:\n request=json.loads(line)\n print(json.dumps({'id':request['id'],'error':{'code':'stale_observation','message':'Observed target pixels changed','diagnostic':{'code':'target_pixels_changed','changed_region':{'x':10,'y':20,'width':30,'height':40}},'recommended_next_action':'reobserve'}}),flush=True)\n")
    worker.chmod(0o700)
    script = tmp_path / "diagnostic-consumer.mjs"
    script.write_text('''import extension from ''' + json.dumps(extension.as_uri()) + ''';
const chain = new Proxy(() => {}, {get:() => chain,apply:() => chain});
let tool; let shutdown;
extension({zod:chain,registerTool(value){tool=value;},registerCommand(){},on(name,callback){if(name==="session_shutdown")shutdown=callback;}});
try {
 const result=await tool.execute("fault-proof",{command:"act",arguments:{}});
 const text=JSON.parse(result.content.find(part=>part.type==="text").text);
 if(text.status!=="error" || text.error.diagnostic.changed_region.width!==30 || text.error.recommended_next_action!=="reobserve")throw new Error("Parent text diagnostics lost");
 if(result.details.error.diagnostic.code!=="target_pixels_changed" || result.details.error.diagnostic.changed_region.height!==40)throw new Error("Parent structured diagnostics lost");
 console.log(JSON.stringify(result));
} finally {shutdown();}
''')
    process = await asyncio.create_subprocess_exec("node", str(script), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env={**os.environ, "BROWSER_AGENT_PYTHON": str(worker)})
    output, errors = await asyncio.wait_for(process.communicate(), 15)
    assert process.returncode == 0, errors.decode()
    result = json.loads(output)
    assert result["details"]["error"]["diagnostic"]["changed_region"] == {"x": 10, "y": 20, "width": 30, "height": 40}
    (tmp_path / "omp-structured-error-proof.json").write_text(json.dumps(result, indent=2))


async def test_omp_network_cookie_context_and_binary_body(tmp_path):
    from test_network_surface import network_site, NETWORK_TOOLS
    extension = Path(__file__).resolve().parents[1] / "integrations/omp/browser-tools.mjs"
    async with network_site() as (origin, seen):
        script = tmp_path / "network-consumer.mjs"
        script.write_text('''import extension from ''' + json.dumps(extension.as_uri()) + ''';
let commands = []; let tool; let shutdown;
const chain = new Proxy(() => {}, { get: (_target, key) => key === "enum" ? values => { commands = values; return chain; } : chain, apply: () => chain });
extension({zod:chain,registerTool(value){tool=value;},registerCommand(){},on(name,callback){if(name==="session_shutdown")shutdown=callback;}});
const invoke = async (command, arguments_) => {
 const result = await tool.execute("network-"+command,{command,arguments:arguments_});
 if(result.details.status === "error") throw new Error(JSON.stringify(result.details));
 return result.details;
};
try {
 if(commands.length!==29)throw new Error("Wrong command catalog");
 const opened=await invoke("launch",{headless:true});
 const first=await invoke("new_tab",{session_id:opened.session_id,url:process.env.FIXTURE_ORIGIN});
 const second=await invoke("new_tab",{session_id:opened.session_id,url:process.env.FIXTURE_ORIGIN});
 const args={session_id:opened.session_id,tab_id:first.tab.id};
 await invoke("network_start_many",{session_id:opened.session_id,tab_ids:[first.tab.id,second.tab.id]});
 const response=await invoke("network_call",{...args,url:process.env.FIXTURE_ORIGIN+"/binary"});
 if(response.provenance!=="browser_context_api_request" || response.status!==200)throw new Error("Wrong API provenance/status");
 const body=await invoke("network_body",{...args,request_id:response.request_id,limit:2048});
 if(body.encoding!=="base64" || Buffer.from(body.data,"base64").length!==2048)throw new Error("Binary body lost");
 const plan=await invoke("network_call",{...args,url:process.env.FIXTURE_ORIGIN+"/api",method:"POST",json_body:{ordinary:"omp"}});
 if(plan.status!=="approval_required")throw new Error("Consequential call sent without approval");
 await invoke("close",{session_id:opened.session_id});
 console.log(JSON.stringify({commands,provenance:response.provenance,binary_bytes:2048,approval_status:plan.status}));
}finally{shutdown();}
''')
        process = await asyncio.create_subprocess_exec("node", str(script), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env={**os.environ, "BROWSER_AGENT_PYTHON": sys.executable, "FIXTURE_ORIGIN": origin})
        output, errors = await asyncio.wait_for(process.communicate(), 45)
        assert process.returncode == 0, errors.decode()
        result = json.loads(output)
        assert NETWORK_TOOLS <= set(result["commands"])
        binary = [request for request in seen if request["target"] == "/binary"]
        assert len(binary) == 1 and "fixture_session=private-cookie" in binary[0]["headers"]["cookie"]
        assert not any(request["method"] == "POST" for request in seen)
