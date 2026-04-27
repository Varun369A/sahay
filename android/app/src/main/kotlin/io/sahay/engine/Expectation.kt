package io.sahay.engine

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * Step post-condition. Mirror of `backend/src/sahay/domain/expectations.py`.
 *
 * After a playbook step's action runs, `StepVerifier` polls this predicate
 * up to `timeoutMs`. If it never becomes true, the step is treated as
 * failed (`expect_failed:<kind>`) — which is the signal for retry / replan
 * / failure-classifier downstream.
 *
 * Parity rule (locked by `tests/test_expectation_parity.py`): the `kind`
 * wire names below must match the Python `ExpectKind` enum exactly.
 */
@Serializable
data class Expectation(
    val kind: String,
    val text: String? = null,
    val selector: JsonObject? = null,
    @SerialName("timeout_ms") val timeoutMs: Int = 3000,
)

enum class ExpectKind(val wireName: String) {
    SCREEN_CONTAINS("screen_contains"),
    NODE_APPEARS("node_appears"),
    NODE_DISAPPEARS("node_disappears"),
    SCREEN_CHANGED("screen_changed"),
    FIELD_VALUE("field_value");

    companion object {
        private val BY_WIRE = entries.associateBy(ExpectKind::wireName)
        fun fromWire(name: String?): ExpectKind? = BY_WIRE[name]
    }
}
