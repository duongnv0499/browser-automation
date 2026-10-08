"""MCP JSON-RPC stdio transport, with persistent browser sessions and image blocks."""
from __future__ import annotations
import asyncio
import json
import sys
from .service import BrowserService, error_payload


def schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}

S = {"type": "string"}
B = {"type": "boolean"}
I = {"type": "integer"}
SESSION = {"session_id": S}
TAB = {**SESSION, "tab_id": S}
ACTION = schema({"observation_id": S, "operation": {"type": "string", "enum": ["click", "fill", "select", "scroll", "press", "hover", "drag", "wait", "back", "forward"]}, "target": S, "text": S, "value": S, "key": S, "x": {"type": "number"}, "y": {"type": "number"}, "delta": {"type": "number"}}, ("observation_id", "operation"))
TOOLS = [
    ("doctor", "Dependency/key presence only; no keys or page data.", schema()),
    ("launch", "Launch isolated browser; not your logged-in profile. Executable is host-configured only.", schema({"headless": B})),
    ("connect", "Attach to consented logged-in Chrome at explicit loopback CDP URL.", schema({"endpoint": S}, ("endpoint",))),
    ("connect_default", "Discover consent-enabled local Chrome; never isolated fallback.", schema()),
    ("tabs", "List persistent session tabs.", schema(SESSION, ("session_id",))),
    ("new_tab", "Create owned tab in session.", schema({**SESSION, "url": S}, ("session_id",))),
    ("observe", "Get indexed DOM snapshot. Use its revision for act. Screenshot is a separate image block.", schema({**TAB, "screenshot": B, "max_text": I}, tuple(TAB))),
    ("text", "Read cached snapshot text continuation without changing revision; capped source reports truncation.", schema({**SESSION, "observation_id": S, "offset": I, "limit": I}, ("session_id", "observation_id"))),
    ("act", "Execute snapshot-bound action, rejecting stale or covered targets; no selectors or JS.", schema({**TAB, "action": ACTION}, (*TAB, "action"))),
    ("approved_act", "Execute exact paused action after host approval; no approval minting. Then run goal again to verify.", schema({**TAB, "observation_id": S, "approval_token": S}, (*TAB, "observation_id", "approval_token"))),
    ("run", "Autonomous goal, bounded steps and separate completion verification. Risky actions pause for host approval.", schema({**TAB, "goal": S, "provider": {"type": "string", "enum": ["openrouter", "openai"]}, "model": S, "max_steps": I, "screenshot": B}, (*TAB, "goal"))),
    ("upload", "Exact host approval and host directory required. Missing approval returns binding for user.", schema({**TAB, "observation_id": S, "target": S, "paths": {"type": "array", "items": S}, "approval_token": S}, (*TAB, "observation_id", "target", "paths"))),
    ("download", "Snapshot-bound download, exact host approval and directory scope required.", schema({**TAB, "action": ACTION, "destination": S, "approval_token": S}, (*TAB, "action", "destination"))),
    ("close_tab", "Close only service-owned tab; cannot close preexisting user tab.", schema(TAB, tuple(TAB))),
    ("close", "Disconnect attached browser without killing user Chrome. Close isolated owned browser.", schema(SESSION, ("session_id",))),
]


def tool_result(result):
    data = dict(result)
    screenshot = data.pop("screenshot", None)
    if isinstance(data.get("observation"), dict):
        data["observation"] = dict(data["observation"])
        screenshot = data["observation"].pop("screenshot", None) or screenshot
    content = [{"type": "text", "text": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}]
    if screenshot:
        content.append({"type": "image", "data": screenshot, "mimeType": "image/png"})
    return {"content": content, "structuredContent": data, "isError": False}


async def serve():
    service = BrowserService()
    tasks = {}
    def send(response):
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
    async def handle(request):
        rid = request.get("id")
        method = request.get("method")
        try:
            if method == "initialize":
                version = request.get("params", {}).get("protocolVersion", "2024-11-05")
                result = {"protocolVersion": version if version in {"2024-11-05", "2025-03-26", "2025-06-18"} else "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "browser-automation", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": name, "description": description, "inputSchema": spec} for name, description, spec in TOOLS]}
            elif method == "tools/call":
                params = request.get("params", {})
                if params.get("name") not in {t[0] for t in TOOLS}:
                    raise ValueError("Unknown tool")
                try:
                    result = tool_result(await service.dispatch(params["name"], params.get("arguments", {})))
                except Exception as exc:
                    result = {"content": [{"type": "text", "text": json.dumps({"error": error_payload(exc)})}], "structuredContent": {"error": error_payload(exc)}, "isError": True}
            else:
                if rid is not None:
                    send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}})
                return
            if rid is not None:
                send({"jsonrpc": "2.0", "id": rid, "result": result})
        except asyncio.CancelledError:
            if rid is not None:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32800, "message": "Request cancelled"}})
        except Exception as exc:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": str(exc)}})
        finally:
            tasks.pop(rid, None)
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                    raise ValueError("Expected JSON-RPC 2.0 object")
            except Exception:
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC request"}})
                continue
            if request.get("method") == "notifications/cancelled":
                pending = tasks.get(request.get("params", {}).get("requestId"))
                if pending:
                    pending.cancel()
            elif "id" in request:
                rid = request["id"]
                if rid in tasks:
                    send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32600, "message": "Duplicate request id"}})
                else:
                    tasks[rid] = asyncio.create_task(handle(request))
    finally:
        pending = list(tasks.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await service.close()


def main():
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
