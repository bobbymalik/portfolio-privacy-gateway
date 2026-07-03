# Portfolio Risk Gateway — Public Beta

A local tool that lets an AI assistant analyze your **options portfolio risk**
without ever seeing your name, account number, or dollar balances. It connects to
your brokerage, de-identifies everything on your own machine, and exposes only
normalized ratios to the assistant — plus a risk engine that scores concentration,
leverage, sector clustering, and scenario losses.

> **Beta · not investment advice · no warranty.** Please read
> [DISCLAIMER.md](DISCLAIMER.md) before using.

## How it protects your data

- **De-identification happens locally.** Raw broker data never leaves your
  machine. Account numbers become opaque tokens; every dollar amount becomes a
  percentage of net liquidating value. The AI sees structure, never identity or
  balances.
- **Deny-by-default egress.** Only an allow-listed set of normalized fields can
  ever leave the gateway; a PII scan backstops it.
- **Credentials stay in your OS keychain.** You provide your own read-only broker
  API tokens; they're stored by macOS Keychain / Windows Credential Manager, not
  in any config file.
- **Encrypted local audit log.** Every payload that leaves the gateway is recorded
  (hashed, never raw) in an encrypted log you control.
- **No third-party market-data key.** Prices come from your own brokerage
  connection; the gateway has no external data dependency.

## What it tells you

A risk assessment with a 0–100 score, threshold flags (single-name concentration,
net directional leverage, margin use, gross exposure, sector concentration),
market-beta factor exposure, correlated-cluster concentration, and scenario P&L
for several market/vol shocks. Each snapshot also reports `data_as_of` so you know
how fresh the holdings are.

## Honest about security and what this is

The deterministic risk engine runs as a **paid service on WFS infrastructure** — it
is never shipped to your machine, so its model can't be extracted from a local
binary. The free tier gives you an **AI-interpreted** assessment of your
de-identified metrics instead of the deterministic score. De-identification runs on
your machine, so raw data never leaves it; when you use the paid engine, only the
already de-identified snapshot (tokens and ratios) is sent to WFS, which retains
nothing.

**This is beta software, provided as-is, and is not investment advice.** Please
read [DISCLAIMER.md](DISCLAIMER.md) before using it, and see
[LICENSE.txt](LICENSE.txt) (Apache 2.0) for the code license. You use it at your own
risk; any financial decision is your own.

## Install

1. Download the release zip for your platform (macOS Apple Silicon, macOS Intel,
   or Windows) from the Releases page.
2. Follow [INSTALL.md](INSTALL.md) — create a virtual environment,
   `pip install .`, then run `deid-gateway-setup` and follow the prompts.
3. Fully quit and reopen Claude Desktop, start a new chat, and ask it to run a
   risk assessment.

## Supported

- **Assistant:** Claude Desktop (local MCP).
- **Brokers:** tastytrade (read-only OAuth), and 20+ brokerages via a free
  SnapTrade Personal key. Connect one or both; the gateway analyzes each account
  and a combined whole-portfolio view.
- **Platforms:** macOS (arm64 + Intel), Windows.

## Known limitations (beta)

- **SnapTrade account discovery is daily.** Holdings are real-time, but the list
  of connected accounts refreshes once a day — a newly linked brokerage may take
  until the next sync to appear.
- **Futures options aren't priced.** Greeks are computed for equity options only;
  futures-option positions show in weight/NAV but with zero greeks.
- **Greeks are model values.** Option greeks are Black-Scholes fits to each
  option's mark, close to but not identical to a broker's published greeks.

## Feedback

This is a beta — please file issues with what broke or confused you. Include your
OS and the gateway's status from Claude Desktop → Settings → Developer.
