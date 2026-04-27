package io.sahay.engine

import android.util.Log
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Bridge between two utterances when the agent paused for clarification.
 *
 * Problem this fixes: today every spoken utterance kicks off
 * `AgentLoop.run(goal)` with a fresh conversation_id. When the agent
 * emits `ask_user` ("which app?"), the user's reply ("WhatsApp")
 * starts a brand-new run that has no memory of the original task —
 * so Sahay treats "WhatsApp" as the goal itself.
 *
 * What this changes: when AgentLoop ends with `asked_user`, the loop
 * stashes the conversation_id, the original goal, and the question
 * here. The next utterance from `TaskWorker` checks for a pending
 * clarification within the resume window; if present, the new
 * utterance is sent as `user_reply` on the SAME conversation, and
 * the agent continues the original task with the answer in hand.
 *
 * Lifecycle:
 *   • set()    — AgentLoop calls this when it returns "asked_user".
 *   • takeIfFresh()  — TaskWorker pops + returns the pending state
 *                      atomically (consume-on-read). Returns null when
 *                      empty or older than RESUME_WINDOW_MS.
 *   • clear()  — explicit reset (e.g. user said "cancel" / "nevermind").
 *
 * Thread-safe via @Synchronized — ask + reply happen on different
 * coroutines (wake-word capture vs agent-loop execution).
 *
 * Single-conversation only. If the user starts an entirely new task
 * before answering the previous question, the old pending state is
 * dropped at the moment the new utterance comes in (we replace
 * unconditionally on the next AgentLoop.run that does NOT consume).
 */
@Singleton
class PendingClarification @Inject constructor() {

    data class State(
        val conversationId: String,
        val originalGoal: String,
        val askedQuestion: String,
        val createdAtMs: Long = System.currentTimeMillis(),
    )

    @Volatile
    private var state: State? = null

    @Synchronized
    fun set(conversationId: String, originalGoal: String, askedQuestion: String) {
        state = State(conversationId, originalGoal, askedQuestion)
        Log.i(TAG, "pending_set conv=${conversationId.take(8)} q='${askedQuestion.take(60)}'")
    }

    /**
     * Atomically pop the pending state if it's still within the
     * resume window. Returns null when no pending clarification or it
     * has expired. The pending state is CONSUMED — a second caller
     * sees null. Designed so an utterance dispatcher can do:
     *
     *     val resume = pendingClarification.takeIfFresh()
     *     if (resume != null) agentLoop.resume(resume.conversationId, text)
     *     else                agentLoop.run(text)
     */
    @Synchronized
    fun takeIfFresh(): State? {
        val s = state ?: return null
        val ageMs = System.currentTimeMillis() - s.createdAtMs
        if (ageMs > RESUME_WINDOW_MS) {
            state = null
            Log.i(TAG, "pending_expired age_ms=$ageMs")
            return null
        }
        state = null
        Log.i(TAG, "pending_taken conv=${s.conversationId.take(8)} age_ms=$ageMs")
        return s
    }

    @Synchronized
    fun clear() {
        if (state != null) Log.i(TAG, "pending_cleared")
        state = null
    }

    /** Read-only peek for diagnostics — does NOT consume. */
    fun peek(): State? = state

    private companion object {
        const val TAG = "PendingClarification"
        // 90 seconds. Long enough that the user can think + answer; short
        // enough that a stale pending from a forgotten earlier session
        // doesn't hijack a fresh task minutes later.
        const val RESUME_WINDOW_MS = 90_000L
    }
}
