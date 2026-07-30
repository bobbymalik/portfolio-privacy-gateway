# Install

Requirements: Python 3.10+ and (for the keychain) a desktop login session.

> **Install editable (`-e`).** The `-e` flag links the package to this folder so
> the code you run is the code in the folder. Without it, `pip` copies a frozen
> snapshot into the environment, and later edits to the source have **no effect**
> until you reinstall -- a confusing trap when patching or updating. Always use
> `pip install -e .`; if you ever do a plain `pip install .`, remember any code
> change needs a reinstall to take effect.

## 1. Get the code

**From the release zip** (recommended) -- download `gateway.zip` from the Releases
page and unzip it somewhere permanent (e.g. `~/wfs-gateway`). There is one zip for
every platform: the gateway is pure Python, so nothing is compiled per-OS.
`pyproject.toml` sits at the top level of the unzipped folder, so install from
there directly.

**From a git clone** -- the repo keeps the Python package one level down, so you
must `cd deid-gateway` before installing. This is the step people miss:

```bash
git clone https://github.com/bobbymalik/portfolio-privacy-gateway.git
cd portfolio-privacy-gateway/deid-gateway     # <-- pyproject.toml lives here
```

Everything below runs from whichever folder contains `pyproject.toml`.

## 2. Install

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .                 # base gateway (editable -- see note above)
pip install -e ".[tastytrade]"   # add if connecting tastytrade (or -e ".[all]")
deid-gateway-setup               # guided: keychain + Claude Desktop registration
```

If `pip install -e .` complains about a C compiler, run `xcode-select --install`
once (the engine is already compiled in the release; this is only for
dependencies).

### Windows

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install -e ".[tastytrade]"   # add if connecting tastytrade (or -e ".[all]")
deid-gateway-setup
```

`deid-gateway-setup` is a console script created by `pip install`. It only exists
inside the activated virtual environment -- if the shell reports "command not
found", activate `.venv` first.

## 3. What the setup wizard does

- asks which broker (tastytrade or SnapTrade),
- generates a strong gateway secret and stores it in your OS keychain,
- collects that broker's **read-only** credentials and stores them in the keychain,
- registers the gateway in your `claude_desktop_config.json` (a backup is kept),
- checks that the engine binary and reference data loaded.

**tastytrade:** create the credentials at my.tastytrade.com → My Profile → API:
create an OAuth application (the client secret is `TT_SECRET`), then OAuth
Applications → Manage → Create Grant (the refresh token is `TT_REFRESH`). Request
**read-only** scope.

**SnapTrade** (power-user / self-host): SnapTrade is a B2B aggregation API, so you
supply your own `clientId`/`consumerKey` from dashboard.snaptrade.com. After the
wizard, connect your brokerage(s) in the SnapTrade dashboard
(dashboard.snaptrade.com), then verify:

```bash
deid-gateway-snaptrade accounts     # lists your connected accounts
```

Option greeks are computed locally (SnapTrade doesn't provide them). The
underlying prices they need come from SnapTrade's own quote endpoint over the same
connection — no external market-data key required.

## 4. Connect your assistant

The gateway is a standard stdio MCP server, so any MCP client can run it. All
three clients below need the same three things:

| | value |
|---|---|
| **Command** | the **venv** Python -- not your system Python |
| **Arguments** | `-m deid_gateway.server` |
| **Env** | `DEID_BROKER`, `DEID_SECRETS_SOURCE=keychain` |

Get the exact command path with the venv activated:

```bash
python -c "import sys; print(sys.executable)"
# macOS/Linux -> /Users/you/wfs-gateway/.venv/bin/python
# Windows     -> C:\Users\you\wfs-gateway\.venv\Scripts\python.exe
```

`DEID_BROKER` accepts `tastytrade`, `snaptrade`, or `multi` (loads every broker
you have credentials for -- the safe default). Credentials themselves are **never**
put in these config files; `DEID_SECRETS_SOURCE=keychain` tells the gateway to read
them from your OS keychain, where the wizard stored them.

This repo ships [`mcp.json`](mcp.json) as a ready-made config in the standard
`mcpServers` format. Copy it, replace the `command` placeholder with your venv
Python path, and point your client at it.

### Claude Desktop

Nothing to do -- `deid-gateway-setup` already wrote the entry into
`claude_desktop_config.json`. To do it by hand, or to check the wizard's work:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

The file uses exactly the format in `mcp.json`, so you can paste the
`deid-portfolio-gateway` block straight into its `mcpServers` object.

Then fully quit Claude Desktop (Cmd+Q on macOS; right-click the tray icon → Quit
on Windows — closing the window isn't enough), reopen it, and start a **new chat**.

### Claude Code

Register it once for all your projects:

```bash
claude mcp add deid-portfolio-gateway --scope user \
  --env DEID_BROKER=multi \
  --env DEID_SECRETS_SOURCE=keychain \
  -- /ABSOLUTE/PATH/TO/.venv/bin/python -m deid_gateway.server
```

Use `--scope project` instead to share it via a checked-in `.mcp.json`, or copy
this repo's `mcp.json` to `.mcp.json` in your project root -- same format, Claude
Code just expects the leading dot.

Verify with `claude mcp list`, or `/mcp` inside a session.

### Amazon Quick

In Quick, open **Add MCP**. Either connection type works.

**Import** (easiest -- reuses `mcp.json`): choose **Import**, and give the
**Config file path** of your edited copy, e.g.
`~/wfs-gateway/mcp.json`. Quick reads the standard `mcpServers` format. If Quick
already lists Claude Code under *Detected on this system*, you can import that
config instead and it will pick up the gateway you registered above.

**Local** (fill the form by hand): choose **Local** and enter

- **Name:** `deid-portfolio-gateway`
- **Command:** `/ABSOLUTE/PATH/TO/.venv/bin/python`
- **Arguments:** `-m deid_gateway.server`
- **Description:** De-identified portfolio risk analysis — exposes only anonymous
  tokens and ratios, never account numbers or balances.
- **Environment variables:** `DEID_BROKER=multi`, `DEID_SECRETS_SOURCE=keychain`

The **Paste JSON** button at the top of the Local form accepts the
`deid-portfolio-gateway` block from `mcp.json` and fills the fields for you.

Quick must run on the **same machine and user account** as the install — the
gateway reads your credentials from that user's OS keychain, and the *Remote*
connection type can't reach it. That is by design: the raw broker data never
leaves your machine.

## 5. Finish

Start a new chat in your assistant and ask it to run a risk assessment on your
account. It should call `list_accounts` and `get_risk_assessment` and come back
with tokens and percentages — never a real account number or dollar balance.

## Updating the market reference data (optional, maintainers)

The release bundles a market reference dataset (ticker betas/sectors), so no
market-data key is needed to run the gateway. Maintainers regenerating that
bundled dataset can supply any beta/sector source of their choice via
`tools/build_refdata.py`:

```bash
python tools/build_refdata.py
```

## Trouble

- **`deid-gateway-setup: command not found`** — activate the virtual environment
  first (`source .venv/bin/activate`), and confirm you ran `pip install -e .` from
  the folder containing `pyproject.toml` (in a git clone that's `deid-gateway/`).
- **Tool not showing up in Claude Desktop?** You must fully quit/reopen Claude
  Desktop and start a new chat. Check Settings → Developer for the gateway's
  status and logs.
- **Client starts the server but no tools appear** — the `command` is almost
  always pointing at a system Python that doesn't have the package. It must be the
  `.venv` Python from step 2.
- **Accounts list is empty / shows mock data** — `DEID_BROKER` is unset (it
  defaults to `mock`) or the keychain has no credentials for the broker you chose.
  Re-run `deid-gateway-setup` any time to fix credentials or re-register.

## Configuration

- **`DEID_BROKER`** — `tastytrade`, `snaptrade`, `multi`, or `mock` (default).
- **`DEID_SECRETS_SOURCE`** — `keychain` (default for the wizard), `aws`, or `env`.
- **`DEID_CACHE_TTL`** (seconds, default `3600` = 60 minutes). How long a broker
  pull and any derived risk assessment are reused before the gateway re-pulls.
  Within the window, repeated calls serve from cache — so an assistant running
  several assessments in one session makes one broker pull and one engine call, not
  many. Set it lower for fresher data, higher to further reduce pulls, or `0` to
  disable reuse (re-pull every call). Set it in the `env` block of the gateway's
  entry in your client's MCP config.
