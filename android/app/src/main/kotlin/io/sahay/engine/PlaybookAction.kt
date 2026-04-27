package io.sahay.engine

/**
 * Canonical playbook action vocabulary — Kotlin mirror of
 * `backend/src/sahay/domain/playbook_actions.py`.
 *
 * Single source of truth on the device side for what a playbook step can
 * be. Used by `PlaybookExecutor.executeStep` so unknown / mistyped action
 * strings fail loudly instead of silently dropping (which is the bug class
 * that produced "swipe_not_implemented_v1" in the wild).
 *
 * Parity rule: every value in the Python `PlaybookAction` enum MUST have a
 * matching Kotlin entry below, and `wireName` must equal the Python
 * `.value` exactly. Adding an action means editing both files in the same
 * commit.
 */
enum class PlaybookAction(val wireName: String) {
    LAUNCH("launch"),
    TAP("tap"),
    LONG_PRESS("long_press"),
    TYPE("type"),
    SWIPE("swipe"),
    WAIT("wait"),
    READ("read"),
    VERIFY("verify"),
    BACK("back"),
    HOME("home");

    companion object {
        private val BY_WIRE = entries.associateBy(PlaybookAction::wireName)

        /**
         * Look up an action by its wire name (case-sensitive). Returns null
         * if the name is unknown — caller must treat that as a hard failure
         * for the step rather than silently skipping.
         */
        fun fromWire(name: String?): PlaybookAction? = BY_WIRE[name]
    }
}
