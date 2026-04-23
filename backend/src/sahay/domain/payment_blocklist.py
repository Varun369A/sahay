"""Hardcoded blocklist of payment + banking apps.

Sahay refuses to plan or execute actions in these apps. Period.
This file is the single source of truth — also embedded into the Android
client at build time to enforce client-side too (defense in depth).

Reasoning: payment failures are catastrophic for trust. Banking apps have
sensitive screens (account numbers, OTPs) that must never be intercepted by
an AI agent. Lifting this blocklist requires explicit human review + a
separate, tightly-scoped product (not generic agent access).
"""

from __future__ import annotations

# Indian payment + banking apps — extend over time.
PAYMENT_APP_PACKAGES: frozenset[str] = frozenset({
    # UPI / wallets
    "com.google.android.apps.nbu.paisa.user",  # Google Pay (India)
    "com.phonepe.app",                          # PhonePe
    "net.one97.paytm",                          # Paytm
    "in.org.npci.upiapp",                       # BHIM
    "com.csam.icici.bank.imobile",              # ICICI iMobile
    "com.amazon.mShop.android.shopping",        # Amazon Pay (lives inside Amazon)
    "com.mobikwik_new",                         # MobiKwik
    "com.freecharge.android",                   # Freecharge
    "com.cred.android",                         # CRED
    "com.dreamplug.androidapp",                 # CRED (alt)
    "com.whatsapp.payments",                    # WhatsApp Pay (separate package on some builds)

    # Banks (sample — extend as discovered)
    "com.sbi.lotusintouch",                     # SBI YONO
    "com.snapwork.hdfc",                        # HDFC Bank
    "com.icicibank.iLite",                      # ICICI Mobile
    "com.axis.mobile",                          # Axis Mobile
    "com.kotak.mobilebanking",                  # Kotak
    "com.csam.icici.bank.imobilex",
    "com.bandhanbank.app",
    "com.idfcfirstbank.optimus",
    "com.indusind.mobile",
    "com.fbl.mobilebanking",                    # Federal Bank
    "com.canarabank.mobility",
    "com.unionbank.unionmobile",
    "com.barodaconnect",                        # Bank of Baroda
    "com.pnb.onepnb",                           # PNB
    "com.unionbank.ecommerce.mobile.legacy",
    "com.YesBank",
    "com.indianbank.IndianBankUpiPay",

    # Global
    "com.paypal.android.p2pmobile",
    "com.venmo",
    "com.squareup.cash",
    "com.revolut.revolut",
    "com.wise",
    "com.coinbase.android",
    "com.binance.dev",
    "co.mona.android",                          # Crypto.com
})


def is_payment_app(package_name: str) -> bool:
    """True if the given Android package is hard-blocked."""
    return package_name in PAYMENT_APP_PACKAGES


def assert_not_payment_app(package_name: str) -> None:
    """Raise if the app is on the payment blocklist.

    Use at the entry point of any planner/executor flow.
    """
    if is_payment_app(package_name):
        raise PaymentAppBlocked(package_name)


class PaymentAppBlocked(Exception):
    """Raised when a payment / banking app is targeted by an action."""

    def __init__(self, package_name: str) -> None:
        super().__init__(
            f"App '{package_name}' is on the payment blocklist. "
            "Sahay does not control payment or banking apps."
        )
        self.package_name = package_name
