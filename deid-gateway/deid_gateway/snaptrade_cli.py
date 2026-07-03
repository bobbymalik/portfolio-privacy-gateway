"""SnapTrade onboarding.

Personal key (free; clientId + consumerKey, no user/secret) -- the common case:
    deid-gateway-secrets set SNAPTRADE_CLIENT_ID
    deid-gateway-secrets set SNAPTRADE_CONSUMER_KEY
    # connect your brokerage(s) in the SnapTrade dashboard (dashboard.snaptrade.com)
    deid-gateway-snaptrade accounts     # auth test: list connected accounts

Commercial key (clientId + consumerKey + a user you register):
    deid-gateway-snaptrade register     # creates your SnapTrade user, stores secret
    deid-gateway-snaptrade link         # prints a portal URL to connect a broker
    deid-gateway-snaptrade accounts
"""

from __future__ import annotations

import sys
import uuid

SERVICE = "deid-gateway"


def _kr():
    import keyring
    return keyring


def _need(kr, key):
    v = kr.get_password(SERVICE, key)
    if not v:
        sys.exit(f"missing {key} — set it with: deid-gateway-secrets set {key}")
    return v


def _is_personal(kr) -> bool:
    """Personal key == no userSecret stored. Personal keys use the implicit user."""
    return not kr.get_password(SERVICE, "SNAPTRADE_USER_SECRET")


def _sdk(kr):
    from snaptrade_client import SnapTrade
    return SnapTrade(consumer_key=_need(kr, "SNAPTRADE_CONSUMER_KEY"),
                     client_id=_need(kr, "SNAPTRADE_CLIENT_ID"))


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    kr = _kr()

    if cmd == "register":
        if _is_personal(kr) and not kr.get_password(SERVICE, "SNAPTRADE_USER_ID"):
            # Try register; a Personal key will 400 (1012) and we explain instead.
            try:
                sdk = _sdk(kr)
                user_id = f"deid-{uuid.uuid4().hex[:12]}"
                resp = sdk.authentication.register_snap_trade_user(body={"userId": user_id})
                kr.set_password(SERVICE, "SNAPTRADE_USER_ID", user_id)
                kr.set_password(SERVICE, "SNAPTRADE_USER_SECRET", resp.body["userSecret"])
                print(f"registered Commercial user '{user_id}'; secret stored.")
                print("next: deid-gateway-snaptrade link")
            except Exception as e:
                if "personal" in str(e).lower() or "1012" in str(e):
                    print("This is a Personal key — no register needed. Its user is")
                    print("provisioned implicitly. Just connect your brokerage in the")
                    print("SnapTrade dashboard, then run: deid-gateway-snaptrade accounts")
                    return
                raise
        else:
            print("userId/userSecret already set (Commercial). next: link")

    elif cmd == "link":
        if _is_personal(kr):
            print("Personal key: connect brokerages directly in the SnapTrade dashboard")
            print("  https://dashboard.snaptrade.com  (or app.snaptrade.com)")
            print("then: deid-gateway-snaptrade accounts")
            return
        sdk = _sdk(kr)
        resp = sdk.authentication.login_snap_trade_user(query_params={
            "userId": _need(kr, "SNAPTRADE_USER_ID"),
            "userSecret": _need(kr, "SNAPTRADE_USER_SECRET")})
        url = resp.body.get("redirectURI") or resp.body.get("redirectUri")
        print(f"Open this URL to connect your brokerage (read-only):\n\n  {url}\n")

    elif cmd == "accounts":
        if _is_personal(kr):
            from .snaptrade_broker import _PersonalSigner
            sign = _PersonalSigner(_need(kr, "SNAPTRADE_CLIENT_ID"),
                                   _need(kr, "SNAPTRADE_CONSUMER_KEY"))
            accts = sign.get("/accounts")
        else:
            sdk = _sdk(kr)
            accts = sdk.account_information.list_user_accounts(
                user_id=_need(kr, "SNAPTRADE_USER_ID"),
                user_secret=_need(kr, "SNAPTRADE_USER_SECRET")).body
        if not accts:
            print("no connected accounts yet — connect a brokerage first.")
            return
        for a in accts:
            inst = a.get("institution_name") or a.get("brokerage", {}).get("name", "?")
            print(f"  {inst:20} {a.get('name','')}  id={a['id']}")

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
