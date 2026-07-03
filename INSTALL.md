# Install

Requirements: Python 3.10+ and (for the keychain) a desktop login session.

> **Install editable (`-e`).** The `-e` flag links the package to this folder so
> the code you run is the code in the folder. Without it, `pip` copies a frozen
> snapshot into the environment, and later edits to the source have **no effect**
> until you reinstall -- a confusing trap when patching or updating. Always use
> `pip install -e .`; if you ever do a plain `pip install .`, remember any code
> change needs a reinstall to take effect.

## macOS

```bash
# in the unzipped release folder
python3 -m venv .venv
source .venv/bin/activate
pip install -e .                 # base gateway (editable -- see note below)
pip install -e ".[tastytrade]"   # add if connecting tastytrade (or -e ".[all]")
deid-gateway-setup               # guided: keychain + Claude Desktop registration
```

If `pip install -e .` complains about a C compiler, run `xcode-select --install`
once (the engine is already compiled in the release; this is only for
dependencies).

## Windows

```powershell
# in the unzipped release folder
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
pip install -e ".[tastytrade]"   # add if connecting tastytrade (or -e ".[all]")
deid-gateway-setup
```

## What the setup wizard does

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

## Finish

Fully quit Claude Desktop (Cmd+Q on macOS; right-click the tray icon → Quit on
Windows — closing the window isn't enough), reopen it, then start a **new chat**
and ask it to run a risk assessment on your account.

## Updating the market reference data (optional, maintainers)

The release bundles a market reference dataset (ticker betas/sectors), so no
market-data key is needed to run the gateway. Maintainers regenerating that
bundled dataset can supply any beta/sector source of their choice via
`tools/build_refdata.py`:

```bash
python tools/build_refdata.py
```

## Trouble

- Tool not showing up? You must fully quit/reopen Claude Desktop and start a new
  chat. Check Settings → Developer for the gateway's status and logs.
- Re-run `deid-gateway-setup` any time to fix credentials or re-register.

## Configuration

- **`DEID_CACHE_TTL`** (seconds, default `3600` = 60 minutes). How long a broker
  pull and any derived risk assessment are reused before the gateway re-pulls.
  Within the window, repeated calls serve from cache — so an assistant running
  several assessments in one session makes one broker pull and one engine call, not
  many. Set it lower for fresher data, higher to further reduce pulls, or `0` to
  disable reuse (re-pull every call). Set it in the `env` block of the gateway's
  entry in `claude_desktop_config.json`.
