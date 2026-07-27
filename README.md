# agentline-sdks

Source-of-truth repo for the official **AgentLine** SDKs, generated with
[Fern](https://buildwithfern.com) and modeled on the
[AgentMail](https://github.com/agentmail-to) SDK experience.

| Language | Install | Usage |
|----------|---------|-------|
| Python | `pip install agentline-ai` | `from agentline_ai import AgentLine` |
| Node / TypeScript | `npm i agentline` | `import { AgentLineClient } from "agentline"` |

> PyPI `agentline` is already taken, so the Python package is `agentline-ai`
> (import `agentline_ai`). npm `agentline` is free. Adjust `package-name` in
> [`fern/generators.yml`](fern/generators.yml) if you own the name or prefer
> another.

The generated code lives in two **separate repos** (one per language) and is
published to PyPI / npm automatically:

- `AgentLineHQ/agentline-python` → PyPI `agentline-ai`
- `AgentLineHQ/agentline-node` → npm `agentline`

This repo only holds the **generation config** + the curated OpenAPI spec — it
contains no hand-written runtime code.

## How it works

```
AgentLine API  (serves /openapi.json — the raw spec)
        │
        ▼  scripts/export_openapi.py   (keeps the public surface, stamps
        │                               x-fern-sdk-group-name / -method-name)
        ▼
fern/openapi/openapi.json  (curated spec — committed)
        │
        ▼  fern generate  (reads generators.yml)
        ├─► github.com/AgentLineHQ/agentline-python  →  PyPI `agentline-ai`
        └─► github.com/AgentLineHQ/agentline-node    →  npm `agentline`
```

The curated spec keeps only the **core developer surface**: agents, numbers,
calls, messages, events, webhooks, billing, and voice. Internal endpoints
(SignalWire callbacks, debug/health, the email-OTP auth flow, x402 crypto
top-ups) are excluded so they never reach the SDK.

## The resulting developer experience

```python
from agentline_ai import AgentLine

client = AgentLine(api_key="sk_live_...")

agent = client.agents.create(name="Support Bot", system_prompt="...")
number = client.numbers.buy(agent_id=agent.id, country="US", area_code="415")
call = client.calls.create(agent_id=agent.id, to_number="+12125557890")
client.calls.hangup(call.id)
print(client.calls.get_transcript(call.id))
```

```typescript
import { AgentLineClient } from "agentline";

const client = new AgentLineClient({ apiKey: "sk_live_..." });

const agent = await client.agents.create({ name: "Support Bot", systemPrompt: "..." });
const number = await client.numbers.buy({ agentId: agent.id, country: "US", areaCode: "415" });
const call = await client.calls.create({ agentId: agent.id, toNumber: "+12125557890" });
await client.calls.hangup(call.id);
console.log(await client.calls.getTranscript(call.id));
```

The full operation map is in [`scripts/export_openapi.py`](scripts/export_openapi.py)
(`SDK_SURFACE`).

## CI

[`.github/workflows/fern.yml`](.github/workflows/fern.yml):
- Pulls the raw spec from the live API, curates it, commits any change.
- Runs `fern check`, then `fern generate` — pushing to the two SDK repos and
  publishing to PyPI / npm.
- Also runs daily on a schedule so the SDKs track the deployed API.

## Local development

```bash
# Curate the spec from a running AgentLine API (or pass --input <raw.json>)
python scripts/export_openapi.py --url https://api.agentline.cloud/openapi.json

npm install -g fern-api
fern generate --local     # preview the SDKs in fern/generated/sdks (Docker)
```

## Bumping generators

```bash
fern add fern-python-sdk     # pins latest fernapi/fern-python-sdk
fern add fern-typescript-sdk # pins latest fernapi/fern-typescript-sdk
```

## Customizing the surface

Edit `SDK_SURFACE` in [`scripts/export_openapi.py`](scripts/export_openapi.py),
rerun it, and commit the updated `fern/openapi/openapi.json`.
