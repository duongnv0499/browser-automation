"""Persistent JSON-lines tools and practical isolated-browser commands."""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
import os
import time
import uuid
from pathlib import Path
from .service import BrowserService, error_payload


async def json_lines(service=None):
    owned = service is None
    service = service or BrowserService()
    tasks = {}
    def send(response):
        print(json.dumps(response, ensure_ascii=False), flush=True)
    async def handle(request):
        rid = request.get("id")
        try:
            async def report(event):
                if not asyncio.current_task().cancelling():
                    send({"id": rid, "progress": event})
            options = {"on_progress": report} if request.get("progress") is True else {}
            result = await service.dispatch(request["command"], request.get("arguments", {}), **options)
            send({"id": rid, "result": result})
        except asyncio.CancelledError:
            send({"id": rid, "error": {"code": "cancelled", "message": "Request cancelled; session retained"}})
        except Exception as exc:
            send({"id": rid, "error": error_payload(exc)})
        finally:
            tasks.pop(rid, None)
    try:
        while line := await asyncio.to_thread(sys.stdin.readline):
            request = {}
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Expected request object")
                if request.get("command") == "cancel":
                    target = request.get("arguments", {}).get("request_id")
                    if target in tasks:
                        tasks[target].cancel()
                    continue
                rid = request.get("id")
                if rid in tasks:
                    raise ValueError("Duplicate pending request id")
                tasks[rid] = asyncio.create_task(handle(request))
            except Exception as exc:
                send({"id": request.get("id") if isinstance(request, dict) else None, "error": error_payload(exc)})
    finally:
        pending = list(tasks.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if owned:
            await service.close()


async def once(args):
    service = BrowserService()
    try:
        if args.command == "doctor":
            result = await service.dispatch("doctor")
        else:
            setup = "connect" if args.endpoint else ("launch" if args.command == "launch" or args.isolated else "connect_default")
            setup_args = {"endpoint": args.endpoint} if setup == "connect" else ({"headless": args.headless} if setup == "launch" else {})
            opened = await service.dispatch(setup, setup_args)
            if args.command == "run":
                tab = (await service.dispatch("new_tab", {"session_id": opened["session_id"], "url": args.url, "wait_until": args.wait_until, "timeout_ms": args.timeout_ms}))["tab"]
                async def report(event):
                    print(json.dumps({"progress": event}, ensure_ascii=False), flush=True)
                run_args = {"session_id": opened["session_id"], "tab_id": tab["id"], "goal": args.goal, "provider": args.provider, "max_steps": args.max_steps, "interpret_visual": args.interpret_visual}
                if args.model is not None:
                    run_args["model"] = args.model
                if args.screenshot is not None:
                    run_args["screenshot"] = args.screenshot
                result = await service.dispatch("run", run_args, on_progress=report if args.progress else None)
            else:
                print(json.dumps(opened), flush=True)
                await json_lines(service)
                return
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        await service.close()


def host_approve(args):
    if not sys.stdin.isatty():
        raise ValueError("Approval requires an interactive host terminal, never agent stdio")
    binding = json.loads(Path(args.binding_file).read_text())
    if "binding" in binding:
        binding = binding["binding"]
    print(json.dumps(binding, indent=2))
    if input("Approve exactly this action for five minutes? Type APPROVE: ") != "APPROVE":
        raise ValueError("Approval declined")
    path = Path(args.approval_file)
    records = json.loads(path.read_text()) if path.exists() else []
    record = {"token": uuid.uuid4().hex, "binding": binding, "expires_at": time.time() + 300}
    records.append(record)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump(records, file)
    os.chmod(path, 0o600)
    print(json.dumps(record))

def main():
    parser = argparse.ArgumentParser(description="Local browser tools. serve/launch/connect retain sessions until stdin EOF; JSON-lines on stdin/stdout.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Persistent JSON-lines worker")
    commands.add_parser("doctor", help="Report dependencies and key presence, never key values")
    from .mcp import add_transport_arguments
    add_transport_arguments(commands.add_parser("mcp", help="MCP server; stdio or authenticated Streamable HTTP"))
    approve = commands.add_parser("approve", help="HOST ONLY interactive exact-action approval; not an MCP tool")
    approve.add_argument("--binding-file", required=True)
    approve.add_argument("--approval-file", required=True)
    for name in ("launch", "connect", "run"):
        p = commands.add_parser(name)
        p.add_argument("--endpoint", help="Explicit loopback CDP URL; attach never launches a substitute browser")
        p.add_argument("--isolated", action="store_true", help="Explicit isolated browser instead of native discovery")
        p.add_argument("--consent", action="store_true", help="Host consent to native current-profile discovery")
        p.add_argument("--profile-dir", help="Chrome user-data directory for native discovery")
        p.add_argument("--headless", action="store_true")
        p.add_argument("--executable-path")
        if name == "run":
            p.add_argument("goal")
            p.add_argument("--url", default="about:blank")
            p.add_argument("--provider", choices=["openrouter", "openai"], default="openrouter")
            p.add_argument("--model")
            p.add_argument("--max-steps", type=int, default=50)
            p.add_argument("--wait-until", choices=["commit", "domcontentloaded", "load", "networkidle"], default="domcontentloaded")
            p.add_argument("--timeout-ms", type=int, default=15000)
            p.add_argument("--interpret-visual", action="store_true", help="Paid semantic screenshot interpretation with explicit provenance")
            p.add_argument("--progress", action="store_true", help="Emit semantic progress JSON before final result")
            vision = p.add_mutually_exclusive_group()
            vision.add_argument("--screenshot", dest="screenshot", action="store_true", default=None, help="Explicitly enable visual observations")
            vision.add_argument("--no-screenshot", dest="screenshot", action="store_false", default=None, help="Text-only observations; default follows provider capability")
    args = parser.parse_args()
    if args.command == "approve":
        host_approve(args)
        return
    if getattr(args, "consent", False):
        os.environ["BROWSER_NATIVE_CONSENT"] = "1"
    if getattr(args, "profile_dir", None):
        os.environ["BROWSER_NATIVE_PROFILE_DIRECTORY"] = args.profile_dir
    if getattr(args, "executable_path", None):
        os.environ["BROWSER_EXECUTABLE_PATH"] = args.executable_path
    if args.command == "mcp":
        from .mcp import main as mcp_main
        mcp_main(args=args)
        return
    try:
        asyncio.run(json_lines() if args.command == "serve" else once(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(json.dumps({"error": error_payload(exc)}), file=sys.stderr)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
