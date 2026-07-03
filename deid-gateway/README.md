# De-identification gateway — POC

A local MCP server that sits between your brokerage accounts and AI chat tools
(Claude Desktop, Claude Code). It pulls portfolio data, **de-identifies it at the
trust boundary**, and exposes only normalized, tokenized, allow-listed data as
read-only MCP tools. Names, account numbers, and exact dollar balances never
leave the gateway.

This POC ships with a **mock broker** so you can validate the full
de-id → MCP → Claude loop before wiring real broker auth.

## What's here

```
deid_gateway/
  deidentify.py   # the trust boundary: tokenize, normalize to ratios, egress allowlist + PII guard
  broker.py       # MockBroker (fixture w/ PII) + TastytradeBroker stub
  service.py      # hot cache, token<->account re-id map (local only), audit log
  server.py       # FastMCP server: read-only sanitized tools over stdio
tests/
  test_deid.py    # proves no PII / no exact $ / allowlist enforced
```

## Run it

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                       # 8 passing: the boundary holds
deid-gateway                    # starts the MCP server on stdio (Ctrl-C to stop)
```

The 8 tests are the real deliverable: they assert the holder name, account
number, and every exact dollar amount are absent from output, that the egress
allowlist blocks any non-approved key, and that a leaked identifier is caught.

## Register with Claude Desktop

Add this to `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`),
using the **absolute path** to the venv's `deid-gateway`:

```json
{
  "mcpServers": {
    "deid-portfolio-gateway": {
      "command": "/ABS/PATH/deid-gateway/.venv/bin/deid-gateway",
      "env": { "DEID_GATEWAY_SECRET": "choose-a-long-random-string" }
    }
  }
}
```

Restart Claude Desktop. Ask: *"List my accounts, then get my risk metrics and
tell me where I'm most exposed."* Claude calls the tools and reasons over
ratios only — it has no way to learn your balances or identity.

## Register with Claude Code

```bash
claude mcp add deid-portfolio-gateway \
  --env DEID_GATEWAY_SECRET=choose-a-long-random-string \
  -- /ABS/PATH/deid-gateway/.venv/bin/deid-gateway
```

## Tools exposed (all read-only, all sanitized)

| Tool | Returns |
|---|---|
| `list_accounts` | account tokens only |
| `get_risk_metrics` | portfolio-level: net delta-$ %, gross exposure %, theta/day %, vega, margin util %, concentration |
| `get_portfolio_snapshot` | the above **plus** per-position normalized detail |
| `get_positions` | per-position: weight / delta-$ / theta / vega, each as % of NAV |
| `get_risk_assessment` | proprietary engine verdict: risk score, flags, factor exposure, scenario P&L |
| `refresh_data` | re-pull from the broker into cache |

## Going live with tastytrade (credentials in the OS keychain)

The real connector is in `broker.py` (`TastytradeBroker`). Credentials go in the
**OS keychain** — macOS Keychain on Mac, Windows Credential Manager on Windows
(the `keyring` library handles both, one implementation). Nothing sensitive ever
touches the Claude Desktop config.

**1. Install deps and store the secrets** (prompted, hidden input — no plaintext,
no shell history):

```bash
pip install -e ".[tastytrade,keychain]"
deid-gateway-secrets set DEID_GATEWAY_SECRET
deid-gateway-secrets set TT_SECRET
deid-gateway-secrets set TT_REFRESH
deid-gateway-secrets check          # confirms all three are present
```

**2. Point the gateway at the keychain.** The config `env` block holds only
non-secret switches:

```json
"env": {
  "DEID_BROKER": "tastytrade",
  "DEID_SECRETS_SOURCE": "keychain"
}
```

On startup the gateway reads `TT_SECRET` / `TT_REFRESH` / `DEID_GATEWAY_SECRET`
from the keychain (macOS will prompt the first time to allow access); anything
absent falls back to an environment variable. The connector is read-only — it
never places an order. The same setup works on Windows unchanged — `keyring`
targets Credential Manager there automatically.

### Alternative: AWS Secrets Manager

If you'd rather keep credentials in AWS, store one JSON secret and point the
gateway at it instead:

```bash
aws secretsmanager create-secret --name deid-gateway/credentials \
  --secret-string '{"DEID_GATEWAY_SECRET":"...","TT_SECRET":"...","TT_REFRESH":"..."}'
pip install -e ".[tastytrade,aws]"
```

```json
"env": {
  "DEID_BROKER": "tastytrade",
  "DEID_AWS_SECRET_ID": "deid-gateway/credentials",
  "DEID_AWS_REGION": "us-east-2",
  "AWS_PROFILE": "your-aws-profile"
}
```

Grant the gateway `secretsmanager:GetSecretValue` on just that ARN, and prefer
SSO / short-lived AWS credentials over long-lived access keys in `~/.aws`.

## Reference data — works for any portfolio

The engine needs each position's market **beta** and **sector** to compute factor
exposure, scenario shocks, and correlated-cluster concentration. Those are public
facts, not proprietary, so the gateway supplies them — not the engine. At snapshot
time the gateway enriches every position from a bundled dataset
(`deid_gateway/refdata.json`) via a local, **offline** lookup, so it works for any
user's holdings and no ticker ever leaves the machine.

Coverage is honest. An unknown ticker falls back to beta 1.0 / sector "Unknown"
and is counted in `reference_coverage_pct`; if a book is poorly covered the
assessment says so (the concentration/leverage/margin flags still hold, only the
beta-based numbers degrade). Leveraged and inverse ETFs (3x, inverse) are handled
explicitly in `refdata.py`, since vendor beta fields misreport them.

The shipped `refdata.json` is a hand-seeded starter (~75 names). Before
distributing, replace it with the **full active US universe** (every actively-
trading US equity + ETF, with measured betas) using your FMP key:

```bash
export FMP_API_KEY=...
python tools/build_refdata.py        # two bulk screener calls; writes refdata.json
```

This pulls thousands of symbols in two calls (not one-per-ticker), so it finishes
in seconds and covers essentially any retail book. Anything still missing degrades
gracefully and is reported via `reference_coverage_pct`.

## Proprietary risk engine (local compiled binary)

The risk *algorithms* live in a separate `risk_engine/` package you keep private.
It holds **no ticker knowledge** — it's portfolio-agnostic math that consumes the
beta/sector the gateway attaches. What's proprietary and compiled into the binary
is the part that's actually yours: the rule thresholds, scenario shocks, and
scoring weights. You compile it to a native binary and ship only that binary
inside this gateway — never the source. The `get_risk_assessment` tool runs it on
a de-identified snapshot and returns a risk score, threshold flags, factor (beta)
exposure, correlated-cluster concentration, and scenario-shock P&L.

Build it once per platform you distribute to (see `risk_engine/README.md`):

```bash
cd risk_engine && pip install cython setuptools wheel && ./build_engine.sh
```

That drops `_risk_engine.<platform>.so` into `deid_gateway/`. Until that binary is
present, `get_risk_assessment` cleanly reports the engine as unavailable and the
other tools keep working.

**Security, honestly:** compiling hides the source and the tables aren't dumpable
from the binary, but a local engine is tamper-*resistant*, not secret — a
determined user can still reverse-engineer or probe it. For true secrecy the
engine must run server-side; `engine_client.py` is left in place for that future
(remote) path. Set `DEID_ENGINE_URL` / `DEID_ENGINE_KEY` to use a remote engine
instead of the local binary.

## Encrypted audit log

Every sanitized payload that leaves the gateway (to the AI or the engine) is
recorded in an append-only, **Fernet-encrypted** log at `~/.deid_gateway/audit.log`.
Each entry holds a timestamp, tool name, account token, and a SHA-256 hash of the
payload — never raw data. The encryption key is derived from `DEID_GATEWAY_SECRET`
via PBKDF2, so the on-disk log is unreadable without it. Read it back for a
compliance trail with `AuditLog(secret).read()`.



1. **Package as a `.mcpb` bundle** — one-click install; mark any remaining config
   fields `sensitive: true` so Claude Desktop encrypts them in the OS keychain.
2. **Add a control UI** — a tray app / `127.0.0.1` page to manage the OAuth grant,
   tune normalization, and view the audit log (the only place real dollars appear,
   re-identified locally).
3. **Extend the surface** — more tools (COT/GEX overlays, scenario inputs), all
   passing through the same `enforce_egress()` gate.

## Security properties (already true in the POC)

- Read-only tool surface; no write tools, no tool returns a raw identifier — so
  a prompt-injection's worst case is "the model reads more sanitized data."
- Deny-by-default egress allowlist + PII scan on every payload that leaves.
- Append-only audit log records a payload **hash** per call, never raw data.
- Re-identification map stays inside the gateway and is never exposed.
