package io.sahay.voice

import android.content.Context
import android.util.Log
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.io.FileOutputStream
import java.net.URL
import java.util.zip.ZipInputStream
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.vosk.Model

/**
 * Downloads + caches the Vosk wake-word model on first launch.
 *
 * Model: vosk-model-small-en-in-0.4 (Indian English, ~40MB unpacked)
 * Hosted at alphacephei.com/vosk/models/.
 *
 * Storage: internal app dir → not user-visible, deleted on uninstall.
 */
@Singleton
class VoskModelManager @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    sealed class State {
        data object NotDownloaded : State()
        data class Downloading(val progress: Int) : State()
        data class Ready(val path: String) : State()
        data class Error(val message: String) : State()
    }

    private val _state = MutableStateFlow<State>(State.NotDownloaded)
    val state: StateFlow<State> = _state.asStateFlow()

    // Serializes concurrent ensureModel() calls. Without this, multiple
    // startup callers (WakeWordService init, MainActivity onCreate,
    // foreground service ensureRunning, etc.) race into the same
    // download, interleaving writes into vosk-model.zip and producing
    // a corrupt archive that fails unzip with "invalid code lengths
    // set". The lock turns N callers into 1 download + N fast-paths.
    private val ensureLock = Mutex()

    private val modelDir get() = File(context.filesDir, MODEL_DIR_NAME)
    private val modelMarker get() = File(modelDir, "am/final.mdl")

    init { detectExisting() }

    private fun detectExisting() {
        if (modelMarker.exists()) {
            _state.value = State.Ready(modelDir.absolutePath)
            Log.i(TAG, "model_already_present at ${modelDir.absolutePath}")
        }
    }

    val isReady: Boolean get() = _state.value is State.Ready

    /** Idempotent — fast-returns if model already present. */
    suspend fun ensureModel(): State = withContext(Dispatchers.IO) {
        if (isReady) return@withContext _state.value

        // Single-flight: only one call performs the download. Concurrent
        // callers wait at the lock; when they wake up, the inner re-check
        // of `isReady` short-circuits them to the cached success state.
        ensureLock.withLock {
            if (isReady) return@withContext _state.value

            try {
                modelDir.deleteRecursively()
                modelDir.mkdirs()
                _state.value = State.Downloading(0)

                val zipFile = File(context.cacheDir, "vosk-model.zip")
                if (zipFile.exists()) zipFile.delete()      // any leftover from a crashed prior attempt
                val expectedBytes = downloadTo(MODEL_URL, zipFile)

                // Reject obviously-truncated downloads BEFORE we hand the
                // file to ZipInputStream. A zip parser given a truncated
                // payload throws "invalid code lengths set" / "invalid
                // stored block lengths" deep in inflate — opaque to the
                // user. Fail loudly here instead.
                val actualBytes = zipFile.length()
                if (expectedBytes > 0 && actualBytes != expectedBytes) {
                    zipFile.delete()
                    val msg = "zip_truncated expected=$expectedBytes actual=$actualBytes"
                    Log.e(TAG, msg)
                    _state.value = State.Error(msg)
                    return@withContext _state.value
                }
                if (actualBytes < MIN_PLAUSIBLE_ZIP_BYTES) {
                    zipFile.delete()
                    val msg = "zip_too_small actual=$actualBytes (min=$MIN_PLAUSIBLE_ZIP_BYTES)"
                    Log.e(TAG, msg)
                    _state.value = State.Error(msg)
                    return@withContext _state.value
                }

                unzip(zipFile, modelDir)
                zipFile.delete()

                // Model unzips into a nested dir like vosk-model-small-en-in-0.4/
                // — flatten so files sit directly under modelDir.
                val nested = modelDir.listFiles()?.firstOrNull { it.isDirectory }
                if (nested != null && nested.name.startsWith("vosk-model")) {
                    nested.listFiles()?.forEach { it.renameTo(File(modelDir, it.name)) }
                    nested.deleteRecursively()
                }

                if (!modelMarker.exists()) {
                    _state.value = State.Error("model_marker_missing_after_unzip")
                    return@withContext _state.value
                }

                _state.value = State.Ready(modelDir.absolutePath)
                Log.i(TAG, "model_ready bytes=$actualBytes")
            } catch (t: Throwable) {
                Log.e(TAG, "model_download_failed", t)
                _state.value = State.Error(t.message ?: "unknown")
            }
            _state.value
        }
    }

    /** Loads the Vosk Model into RAM. Call from off-main thread. */
    suspend fun loadModel(): Model? = withContext(Dispatchers.IO) {
        val s = _state.value
        if (s !is State.Ready) return@withContext null
        try { Model(s.path) } catch (t: Throwable) {
            Log.e(TAG, "model_load_failed", t)
            null
        }
    }

    // ─── Internals ────────────────────────────────────────────────────

    /**
     * Streams the URL to `dest`. Returns the server-advertised
     * content length (-1 when unknown) so the caller can validate
     * we actually read the whole file. Caller is responsible for
     * deleting `dest` if validation fails — we don't auto-clean here
     * so the bytes-on-disk remain inspectable for forensics.
     */
    private fun downloadTo(url: String, dest: File): Long {
        Log.i(TAG, "downloading $url → ${dest.absolutePath}")
        val conn = URL(url).openConnection().apply {
            connectTimeout = 15_000
            readTimeout = 60_000
        }
        val advertised = conn.contentLengthLong       // -1 if server didn't send Content-Length
        val totalForProgress = if (advertised > 0) advertised else 1L
        conn.getInputStream().use { input ->
            FileOutputStream(dest).use { output ->
                val buf = ByteArray(64 * 1024)
                var read = 0L
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    output.write(buf, 0, n)
                    read += n
                    val pct = ((read * 100) / totalForProgress).toInt().coerceIn(0, 100)
                    val cur = _state.value
                    if (cur is State.Downloading && cur.progress != pct) {
                        _state.value = State.Downloading(pct)
                    }
                }
                output.flush()                        // ensure disk close before size check
                output.fd.sync()
            }
        }
        return advertised
    }

    private fun unzip(zip: File, target: File) {
        ZipInputStream(zip.inputStream().buffered()).use { zis ->
            var entry = zis.nextEntry
            while (entry != null) {
                val outFile = File(target, entry.name)
                if (entry.isDirectory) {
                    outFile.mkdirs()
                } else {
                    outFile.parentFile?.mkdirs()
                    FileOutputStream(outFile).use { out -> zis.copyTo(out) }
                }
                zis.closeEntry()
                entry = zis.nextEntry
            }
        }
    }

    companion object {
        private const val TAG = "VoskModel"
        private const val MODEL_DIR_NAME = "vosk-model-en-in"

        // Indian English small model — best for Indian users + Tamil-accented English.
        private const val MODEL_URL =
            "https://alphacephei.com/vosk/models/vosk-model-small-en-in-0.4.zip"

        // Sanity floor for the downloaded archive. The real Vosk small-en-in
        // zip is ~37MB; anything under 1MB is unambiguously truncated /
        // mid-stream-disconnected and we should fail loudly instead of
        // letting ZipInputStream bury the truth in an "invalid block
        // lengths" exception. Reject early.
        private const val MIN_PLAUSIBLE_ZIP_BYTES = 1L * 1024 * 1024
    }
}
