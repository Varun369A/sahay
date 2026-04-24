package io.sahay.data.repository

import io.sahay.data.api.MemoryOut
import io.sahay.data.api.SahayApi
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Local cache of the user's "important contacts" — long-term memories with
 * category ∈ {people, work} and importance ≥ 8.
 *
 * Why cache:
 *   • The notification listener needs this on every incoming post to decide
 *     whether to announce during Quiet Hours. Hitting the backend each time
 *     would make announcements laggy and burn mobile data.
 *   • Important contacts change rarely (days/weeks, not seconds). A 1-hour
 *     TTL is more than fresh enough.
 *
 * Matching is first-name based: the notification title "Amirtha" matches a
 * memory value like "Amirtha is my IT colleague, SIM 1". We lowercase both
 * sides. For ambiguous first names (two Amirthas), this returns the highest
 * importance — a false-positive announce is better than missing a real one.
 */
@Singleton
class ImportantContactsRepository @Inject constructor(
    private val api: SahayApi,
) {

    private val mutex = Mutex()
    private var cache: List<MemoryOut> = emptyList()
    private var fetchedAtMs: Long = 0L

    /**
     * Importance score (1–10) for a sender, or -1 if they're not in the
     * important list. Called synchronously from the notification listener —
     * uses the last successful cache. Refresh is best-effort and async.
     */
    fun importanceForSender(senderTitle: String): Int {
        val q = senderTitle.lowercase().trim()
        if (q.isEmpty()) return -1

        var best = -1
        for (m in cache) {
            val haystack = (m.key + " " + m.value).lowercase()
            // We look for the sender's first token inside the key+value —
            // works for "contact:amirtha" + "Amirtha is boss's PA" style.
            val firstTok = q.split(' ', ',', '.', limit = 2).firstOrNull().orEmpty()
            if (firstTok.length >= 3 && haystack.contains(firstTok)) {
                if (m.importance > best) best = m.importance
            }
        }
        return best
    }

    /** Best-effort refresh; returns true if the cache was updated. */
    suspend fun refreshIfStale(nowMs: Long = System.currentTimeMillis()): Boolean =
        mutex.withLock {
            if (nowMs - fetchedAtMs < TTL_MS) return@withLock false
            runCatching { api.importantContacts(minImportance = 1) }
                .onSuccess { fresh ->
                    cache = fresh
                    fetchedAtMs = nowMs
                }
                .onFailure {
                    // Keep stale cache on failure — better than going blank.
                }
                .isSuccess
        }

    /** Force a refresh irrespective of TTL. Called on explicit user actions. */
    suspend fun forceRefresh(): Boolean = mutex.withLock {
        runCatching { api.importantContacts(minImportance = 1) }
            .onSuccess { fresh ->
                cache = fresh
                fetchedAtMs = System.currentTimeMillis()
            }
            .isSuccess
    }

    private companion object {
        const val TTL_MS = 60 * 60 * 1000L             // 1 hour
    }
}
