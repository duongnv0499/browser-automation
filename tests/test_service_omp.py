"""Smoke loads the real extension into installed OMP without making model calls."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.environ.get("BROWSER_INTEGRATION_TESTS") != "1", reason="Final integration verification opt-in")]


async def test_actual_omp_extension_load(tmp_path):
    if not shutil.which("omp"):
        pytest.skip("OMP not installed")
    probe = tmp_path / "probe.mjs"
    probe.write_text('export default function(pi) { pi.on("session_start", () => { const tool = pi.getAllTools().find(t => t.name === "browser_agent"); console.log(JSON.stringify({ browser_extension_probe: tool ? { name: tool.name, description: tool.description } : null })); }); }')
    extension = Path(__file__).resolve().parents[1] / "integrations/omp/browser-tools.mjs"
    process = await asyncio.create_subprocess_exec("omp", "--mode", "rpc", "--no-ui", "--no-session", "--no-extensions", "-e", str(extension), "-e", str(probe), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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
    finally:
        process.stdin.close()
        output, errors = await asyncio.wait_for(process.communicate(), 15)
    assert process.returncode == 0, errors.decode()
    assert b"extension_error" not in output
