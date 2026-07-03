# WFS Portfolio Risk Gateway — User Guide

Connect your brokerage accounts to Claude for private, on-device portfolio risk
analysis. Your account numbers, names, and dollar balances **never leave your
machine** — the gateway converts everything to anonymous tokens and ratios before
Claude (or anything else) sees it.

---

## 1. What you need

- **Claude Desktop** (macOS or Windows).
- **Python 3.11+**.
- A brokerage login: **tastytrade**, and/or a free **SnapTrade** account (which
  connects 20+ brokerages).
- *(Optional)* a **WFS subscription key** for the deterministic risk engine. Without
  it you still get Claude's AI risk analysis for free.

---

## 2. Install

1. **Download** the release zip for your platform from the Releases page and unzip
   it somewhere permanent (e.g. `~/wfs-gateway`).

2. **Create a virtual environment and install:**
   ```bash
   cd ~/wfs-gateway
   python3 -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\activate
   pip install -e .                 # -e = editable; the code you run is the code in the folder
   ```
   The base install includes everything except the broker SDKs. **Add the SDK for
   the broker(s) you'll connect:**
   ```bash
   pip install -e ".[tastytrade]"   # if connecting tastytrade
   pip install -e ".[all]"          # or grab every optional SDK at once
   ```
   (SnapTrade's free Personal key needs no extra SDK — only a SnapTrade
   *Commercial* key does, via `.[snaptrade]`.)

3. **Run the setup wizard:**
   ```bash
   deid-gateway-setup
   ```
   It will: generate your private encryption key, ask which broker(s) to connect,
   store credentials in your OS keychain, and register the gateway with Claude
   Desktop.

4. **Connect a broker:**
   - **tastytrade:** at my.tastytrade.com → My Profile → API, create a read-only
     OAuth app; paste the client secret and refresh token when the wizard asks.
   - **SnapTrade:** get a free Personal key at dashboard.snaptrade.com, paste the
     client id + consumer key, then connect your brokerage(s) in the SnapTrade
     dashboard. Verify with:
     ```bash
     deid-gateway-snaptrade accounts
     ```

5. *(Optional) Add your WFS subscription* for the deterministic engine:
   ```bash
   deid-gateway-secrets set DEID_ENGINE_URL
   deid-gateway-secrets set DEID_ENGINE_KEY_ID
   deid-gateway-secrets set DEID_ENGINE_SECRET
   ```

6. **Restart Claude Desktop completely** — Cmd+Q on macOS (right-click tray → Quit
   on Windows), not just closing the window — then reopen it and start a **new
   chat**.

---

## 3. Confirm it's connected

In a new chat, ask:

> **List my connected accounts.**

You should see your accounts as anonymous tokens (e.g. `acct_c8514eb3…`) plus a
`portfolio_all` combined view. If you connected more than one account, that
combined view spans all of them.

---

## 4. Run a risk assessment

The simplest ask:

> **Run a risk assessment on my whole portfolio.**

Claude will pull your de-identified snapshot and return a risk read. If you have a
WFS subscription, this is the deterministic engine score; if not, it's Claude's AI
interpretation of your metrics (with an invitation to subscribe for the
reproducible version).

### Get a full visual report

> **Run a risk assessment on `portfolio_all` and build me a visual report:
> a gauge for the overall risk score, a bar chart of the scenario P&L (mild /
> sharp / crash / rally), a breakdown of my single-name and sector concentration,
> and my net market exposure. Flag anything at a warning or critical level.**

Claude Desktop will render these as charts you can view inline.

### Drill into specific risks

> **Show me my scenario P&L as a bar chart — how much would this portfolio lose in
> a sharp selloff versus a crash?**

> **Break down my concentration: which single position and which sector is the
> biggest share of my portfolio? Show it as a pie chart.**

> **What's my net directional exposure — am I net long or short the market, and by
> how much on a beta-adjusted basis?**

### Compare accounts

> **Compare the risk of each of my accounts side by side, then show how the
> combined portfolio differs from the riskiest single account.**

### Understand the drivers

> **Explain in plain language what's driving my risk score and which factors are
> pulling it up the most.**

---

## 5. Ask for observations and things to consider

The gateway describes your risk **factually**. Claude can also highlight what to be
aware of and general, educational approaches — not personalized trade
recommendations.

> **Based on this assessment, what are the top risks I should be aware of, and what
> general approaches do people use to manage concentration and directional risk?
> Keep it educational, not personalized advice.**

> **My crash-scenario loss looks large — what generally drives that kind of tail
> risk in an options portfolio, and what are common ways people think about
> reducing it?**

> **Summarize my portfolio's risk in a short report I could review before my next
> planning session, with the key numbers and the open questions I should think
> about.**

> ⚠️ **Not investment advice.** The gateway and Claude provide risk *analysis* and
> education, not recommendations to buy or sell. For decisions about your money,
> consult a licensed financial advisor.

---

## 6. Keeping data fresh

Holdings are cached for 60 minutes by default (this keeps repeat questions fast and
avoids hammering your broker). To force a fresh pull mid-session:

> **Refresh my portfolio data, then re-run the risk assessment.**

To change the cache window, set `DEID_CACHE_TTL` (seconds) in the gateway's `env`
block in `claude_desktop_config.json` — e.g. `900` for 15 minutes, `0` to always
re-pull.

---

## 7. Free vs subscription

| | Free | WFS subscription |
|---|------|------------------|
| Private broker connection | ✅ | ✅ |
| De-identified metrics | ✅ | ✅ |
| Claude AI risk interpretation | ✅ | ✅ |
| Deterministic 0–100 risk score | — | ✅ |
| Reproducible scenario & factor engine | — | ✅ |

The free tier is labeled as an AI-generated estimate. The subscription unlocks the
deterministic, reproducible risk engine. Subscribe at
https://wealthfinancialsystem.com.

---

## 8. Troubleshooting

- **No accounts listed** — make sure you fully quit and reopened Claude Desktop and
  started a *new* chat. Check status at Claude Desktop → Settings → Developer.
- **A SnapTrade account is missing** — newly linked brokerages can take until the
  next daily sync to appear; holdings themselves are real-time once linked.
- **Assessment says "temporarily unreachable"** (subscribers) — the WFS engine
  couldn't be reached; you'll still get an AI interpretation. Try again shortly.
- **Everything else** — file an issue with your OS and the gateway status from the
  Developer settings panel.
