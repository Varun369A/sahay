package io.sahay.engine

/**
 * MIRROR of backend/src/sahay/domain/payment_blocklist.py.
 * Keep these in sync. Blocking happens in BOTH places (defense in depth).
 *
 * The accessibility service refuses to inject events when foreground app
 * matches this list, regardless of what the server returned.
 */
object PaymentBlocklist {

    val PACKAGES: Set<String> = setOf(
        // UPI / wallets
        "com.google.android.apps.nbu.paisa.user",
        "com.phonepe.app",
        "net.one97.paytm",
        "in.org.npci.upiapp",
        "com.csam.icici.bank.imobile",
        "com.amazon.mShop.android.shopping",
        "com.mobikwik_new",
        "com.freecharge.android",
        "com.cred.android",
        "com.dreamplug.androidapp",
        "com.whatsapp.payments",

        // Banks (sample — extend over time)
        "com.sbi.lotusintouch",
        "com.snapwork.hdfc",
        "com.icicibank.iLite",
        "com.axis.mobile",
        "com.kotak.mobilebanking",
        "com.csam.icici.bank.imobilex",
        "com.bandhanbank.app",
        "com.idfcfirstbank.optimus",
        "com.indusind.mobile",
        "com.fbl.mobilebanking",
        "com.canarabank.mobility",
        "com.unionbank.unionmobile",
        "com.barodaconnect",
        "com.pnb.onepnb",
        "com.unionbank.ecommerce.mobile.legacy",
        "com.YesBank",
        "com.indianbank.IndianBankUpiPay",

        // Global
        "com.paypal.android.p2pmobile",
        "com.venmo",
        "com.squareup.cash",
        "com.revolut.revolut",
        "com.wise",
        "com.coinbase.android",
        "com.binance.dev",
        "co.mona.android",
    )

    fun isBlocked(packageName: String?): Boolean =
        packageName != null && packageName in PACKAGES
}
