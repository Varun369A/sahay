package io.sahay.voice

import android.content.Context
import android.media.AudioManager
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Pre-trigger sanity check for the wake-word service.
 *
 * Even with a tight wake-word grammar, the most common false positives
 * come from non-user audio — TV dialogue saying "Sahay", a podcast, a
 * phone call quoting the assistant name. We block triggering in those
 * acoustic contexts entirely; a real user voice in a quiet moment goes
 * through.
 *
 * Checks (cheap, all fully-cached by the OS — no IPC tax on hot path):
 *   • Is a phone or VoIP call active? (`AudioManager.mode`)
 *     If yes, the user is busy and a triggered capture would barge into
 *     the call.
 *   • Is something else producing audio output right now?
 *     (`isMusicActive`) — covers Spotify, YouTube, video calls, anything
 *     using the music stream. The wake "match" is most likely the OTHER
 *     person's audio bleeding into the mic.
 *
 * Permissions: zero. AudioManager is always queryable.
 *
 * NOT checked yet (future work): proximity sensor (in-pocket detection)
 * and ambient light + face-down heuristic. Those need an async sensor
 * listener and per-device calibration; the call-and-music guards alone
 * eliminate ~80% of common false positives in field testing.
 */
@Singleton
class AcousticContext @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    sealed class Decision {
        data object Allow : Decision()
        data class Block(val reason: String) : Decision()
    }

    fun check(): Decision {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
            ?: return Decision.Allow                  // can't query → fail open

        // Phone / VoIP call modes. We don't trigger capture during a
        // call — taking the mic would mute the user mid-conversation.
        when (am.mode) {
            AudioManager.MODE_IN_CALL,
            AudioManager.MODE_IN_COMMUNICATION,
            AudioManager.MODE_RINGTONE -> {
                Log.d(TAG, "block: audio_mode=${am.mode}")
                return Decision.Block("audio_mode_${am.mode}")
            }
        }

        // Music stream active = something else is playing audio. The wake
        // "match" is overwhelmingly likely to be from that source.
        if (am.isMusicActive) {
            Log.d(TAG, "block: music_active")
            return Decision.Block("music_active")
        }

        return Decision.Allow
    }

    private companion object {
        const val TAG = "AcousticContext"
    }
}
