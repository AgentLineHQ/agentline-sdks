#!/usr/bin/env python3
"""
Generate the curated OpenAPI spec consumed by Fern to build the `agentline`
Python and Node SDKs.

The AgentLine FastAPI app serves its full OpenAPI document at /openapi.json.
That document includes internal endpoints (SignalWire callbacks, debug/health,
the email-OTP auth flow, x402 crypto top-ups, ...) that must NOT leak into the
public SDK. This script:

  1. Reads the RAW OpenAPI document from a live API URL (default) or a file.
  2. Keeps only the operations in SDK_SURFACE (the public core surface).
  3. Stamps each operation with `x-fern-sdk-group-name` and
     `x-fern-sdk-method-name` so the generated SDKs read like:
         client.agents.create(...)
         client.calls.hangup(...)
  4. Declares a Bearer security scheme + base server URL.
  5. Writes fern/openapi/openapi.json (the file Fern reads).

Usage:
  python scripts/export_openapi.py                      # fetch live spec
  python scripts/export_openapi.py --url https://.../openapi.json
  python scripts/export_openapi.py --input /tmp/raw.json
  AGENTLINE_OPENAPI_URL=https://... python scripts/export_openapi.py

If the live API is unreachable the script warns and exits 0 WITHOUT
overwriting the committed spec, so Fern can still build from the last good
committed version.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://api.agentline.cloud/openapi.json"

# ── Public SDK surface ─────────────────────────────────────────────────────
# operationId -> (resource group, SDK method name)
# Mirrors the AgentMail / AgentPhone DX: client.<group>.<method>(...)
SDK_SURFACE: dict[str, tuple[str, str]] = {
    # Agents
    "create_agent": ("agents", "create"),
    "list_agents": ("agents", "list"),
    "get_agent": ("agents", "get"),
    "update_agent": ("agents", "update"),
    "delete_agent": ("agents", "delete"),
    # Numbers
    "buy_phone_number": ("numbers", "buy"),
    "list_phone_numbers": ("numbers", "list"),
    "get_phone_number": ("numbers", "get"),
    "reassign_number": ("numbers", "reassign"),
    # Calls
    "make_outbound_call": ("calls", "create"),
    "list_calls": ("calls", "list"),
    "get_call_details": ("calls", "get"),
    "get_call_transcript": ("calls", "get_transcript"),
    "hangup_call": ("calls", "hangup"),
    "push_call_context": ("calls", "push_context"),
    # Messages
    "send_sms": ("messages", "send"),
    "list_messages": ("messages", "list"),
    "list_conversations": ("messages", "list_conversations"),
    # Events
    "poll_events": ("events", "poll"),
    "peek_events": ("events", "peek"),
    # Webhooks
    "get_webhook": ("webhooks", "list"),
    "set_webhook": ("webhooks", "set"),
    "delete_webhook": ("webhooks", "delete"),
    "test_webhook": ("webhooks", "test"),
    # Billing
    "get_account_balance": ("billing", "get_balance"),
    "get_expenditure_breakdown": ("billing", "get_expenditure"),
    "get_spending_summary": ("billing", "get_summary"),
    "get_call_charges": ("billing", "list_call_charges"),
    "get_number_charges": ("billing", "list_number_charges"),
    # Voice
    "list_available_voices": ("voice", "list"),
    "get_account_voice": ("voice", "get"),
    "set_account_voice": ("voice", "set"),
    "reset_account_voice": ("voice", "reset"),
}

# Operations that do NOT require a Bearer key. (None in the curated surface.)
PUBLIC_OPERATIONS: set[str] = set()


def load_raw(url: str | None, path: str | None) -> dict | None:
    if path:
        print(f"Reading raw OpenAPI from file: {path}")
        return json.loads(Path(path).read_text(encoding="utf-8"))

    # Empty-string env (e.g. unset GitHub secret rendered as "") counts as unset.
    target = url or os.environ.get("AGENTLINE_OPENAPI_URL") or DEFAULT_URL
    print(f"Fetching raw OpenAPI from: {target}")
    try:
        with urllib.request.urlopen(target, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: could not fetch/parse raw spec ({exc}).\n"
            "Leaving committed fern/openapi/openapi.json unchanged so Fern "
            "builds from the last good version.",
            file=sys.stderr,
        )
        return None


def build_spec(raw: dict, server_url: str) -> dict:
    spec = json.loads(json.dumps(raw))  # deep copy; never mutate input

    components = spec.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})

    # Reuse the raw spec's existing bearer scheme if present (Fern dedupes
    # structurally identical schemes and keeps the first, so adding a second
    # "BearerAuth" would silently drop our x-fern-bearer extension).
    bearer_name = next(
        (
            name
            for name, sch in schemes.items()
            if isinstance(sch, dict)
            and sch.get("type") == "http"
            and sch.get("scheme") == "bearer"
        ),
        None,
    )
    if bearer_name is None:
        bearer_name = "BearerAuth"
        schemes[bearer_name] = {"type": "http", "scheme": "bearer"}

    bearer = schemes[bearer_name]
    bearer["bearerFormat"] = "API key (sk_live_...)"
    bearer["description"] = (
        "AgentLine API key. Get one via the email OTP flow "
        "(POST /v1/auth/otp then POST /v1/auth/verify). "
        "Pass as: Authorization: Bearer sk_live_..."
    )
    # Fern: name the constructor credential `apiKey` (default is `token`)
    # and let it fall back to the AGENTLINE_API_KEY env var.
    bearer["x-fern-bearer"] = {"name": "apiKey", "env": "AGENTLINE_API_KEY"}

    spec["security"] = [{bearer_name: []}]
    spec["servers"] = [{"url": server_url, "description": "Production"}]
    spec["info"] = {
        **spec.get("info", {}),
        "title": "AgentLine",
        "summary": "Phone numbers, voice, and SMS for AI agents.",
        "description": (
            "AgentLine gives every AI agent a real phone number, a "
            "human-like voice, and the ability to make and receive calls "
            "and SMS autonomously. This SDK covers the core developer "
            "surface: agents, numbers, calls, messages, events, webhooks, "
            "billing, and voice."
        ),
        "contact": {"name": "AgentLine", "url": "https://agentline.cloud"},
        "license": {"name": "MIT", "url": "https://opensource.org/license/mit"},
    }

    kept: dict[str, dict] = {}
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if not isinstance(op, dict) or "operationId" not in op:
                continue
            op_id = op["operationId"]
            if op_id not in SDK_SURFACE:
                continue

            group, fn_name = SDK_SURFACE[op_id]
            op["x-fern-sdk-group-name"] = [group]
            op["x-fern-sdk-method-name"] = fn_name

            # Drop any explicit `authorization` header param — auth is handled
            # by the BearerAuth security scheme (avoids a Fern conflict warning).
            params = op.get("parameters")
            if isinstance(params, list):
                op["parameters"] = [
                    p
                    for p in params
                    if not (
                        isinstance(p, dict)
                        and p.get("in") == "header"
                        and str(p.get("name", "")).lower() == "authorization"
                    )
                ]

            if op_id in PUBLIC_OPERATIONS:
                op["security"] = []
            else:
                op["security"] = [{bearer_name: []}]
                rb = op.get("requestBody")
                if isinstance(rb, dict):
                    rb.setdefault("required", True)
                    content = rb.setdefault("content", {})
                    content.setdefault(
                        "application/json", {"schema": {"type": "object"}}
                    )

            kept.setdefault(path, {})[method] = op

    spec["paths"] = dict(sorted(kept.items()))

    op_count = sum(len(m) for m in spec["paths"].values())
    present = {
        op.get("operationId")
        for subs in raw.get("paths", {}).values()
        for op in (subs.values() if isinstance(subs, dict) else [])
        if isinstance(op, dict)
    }
    missing = sorted(set(SDK_SURFACE) - present)
    print(f"Curated OpenAPI: {op_count} operations across {len(spec['paths'])} paths.")
    if missing:
        print("WARNING: surface operations missing from source:", ", ".join(missing))
    return spec


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the curated Fern OpenAPI spec.")
    ap.add_argument("--url", help="Raw OpenAPI URL (default: live API).")
    ap.add_argument("--input", help="Raw OpenAPI file path (overrides --url).")
    ap.add_argument(
        "--server",
        default=os.environ.get("AGENTLINE_API_URL", DEFAULT_URL.replace("/openapi.json", "")),
        help="Base server URL written into the spec.",
    )
    args = ap.parse_args()

    raw = load_raw(args.url, args.input)
    if raw is None:
        return 0  # keep committed spec as-is

    spec = build_spec(raw, args.server)
    out_path = ROOT / "fern" / "openapi" / "openapi.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Wrote {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
