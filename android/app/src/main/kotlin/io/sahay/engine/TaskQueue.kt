package io.sahay.engine

import android.content.Context
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import io.sahay.service.SahayForegroundService
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

/**
 * Queue of voice-originated tasks waiting to execute.
 *
 * Designed for the "always-listening" model:
 *   • Wake-word service / mic loop pushes Task.Utterance(text, locale)
 *     immediately when STT finishes — does NOT wait for execution.
 *   • TaskWorker pops one at a time, parses intent via backend,
 *     dispatches to SahayForegroundService for execution.
 *   • Mic continues capturing utterance N+1 while task N is executing.
 *
 * Single-worker by design — Android's Accessibility Service can only
 * touch one app at a time. Multi-worker would race on screen state.
 */
@Singleton
class TaskQueue @Inject constructor() {

    sealed class Task {
        /** Raw user utterance, needs intent parsing. */
        data class Utterance(val text: String, val locale: String) : Task()

        /** Pre-parsed action (for chained / programmatic dispatch). */
        data class DispatchedAction(
            val appPackage: String,
            val intent: String,
            val inputs: JsonObject,
        ) : Task()
    }

    private val channel = Channel<Task>(capacity = Channel.UNLIMITED)

    suspend fun submit(task: Task) {
        Log.i(TAG, "queue_submit ${task::class.simpleName}")
        channel.send(task)
    }

    /** Suspending iterator the worker uses. */
    fun receiveChannel() = channel

    private companion object { const val TAG = "TaskQueue" }
}


@Singleton
class TaskWorker @Inject constructor(
    @ApplicationContext private val context: Context,
    private val queue: TaskQueue,
    private val parser: IntentParser,
    private val json: Json,
    private val pendingClarification: PendingClarification,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var loopJob: Job? = null

    /** Idempotent — call from foreground service onStartCommand. */
    fun ensureRunning() {
        if (loopJob?.isActive == true) return
        loopJob = scope.launch {
            for (task in queue.receiveChannel()) {
                runCatching { handle(task) }
                    .onFailure { Log.e(TAG, "task_failed", it) }
            }
        }
    }

    fun stop() {
        loopJob?.cancel()
        loopJob = null
    }

    private suspend fun handle(task: TaskQueue.Task) {
        when (task) {
            is TaskQueue.Task.Utterance -> {
                // Conversation continuity: if the agent recently asked
                // the user a clarifying question, treat THIS utterance
                // as the answer to that question — same conversation,
                // no fresh task. Otherwise, fresh agent run.
                val pending = pendingClarification.takeIfFresh()
                if (pending != null) {
                    Log.i(
                        TAG,
                        "handle_utterance_resume conv=${pending.conversationId.take(8)} " +
                            "reply='${task.text.take(80)}' " +
                            "original='${pending.originalGoal.take(60)}'",
                    )
                    SahayForegroundService.runResume(
                        context = context,
                        conversationId = pending.conversationId,
                        originalGoal = pending.originalGoal,
                        userReply = task.text,
                    )
                } else {
                    // Full-auto: agent loop drives — no regex intent parsing.
                    Log.i(TAG, "handle_utterance text=${task.text}")
                    SahayForegroundService.runAgent(context = context, goal = task.text)
                }
            }
            is TaskQueue.Task.DispatchedAction -> {
                // Legacy path — reuse old runner for pre-structured actions.
                Log.i(TAG, "handle_dispatched ${task.appPackage}/${task.intent}")
                SahayForegroundService.runIntent(
                    context = context,
                    appPackage = task.appPackage,
                    intent = task.intent,
                    inputsJson = json.encodeToString(JsonObject.serializer(), task.inputs),
                )
            }
        }
    }

    private companion object { const val TAG = "TaskWorker" }
}
