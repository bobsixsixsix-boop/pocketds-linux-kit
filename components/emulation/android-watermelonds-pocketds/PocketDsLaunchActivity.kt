package me.magnum.melonds.pocketds

import android.app.Activity
import android.app.ActivityOptions
import android.app.Application
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import me.magnum.melonds.ui.emulator.EmulatorActivity

/**
 * Pocket DS entry point used by frontends such as Pegasus.
 *
 * The frontend passes the persisted Storage Access Framework URI as a plain
 * string extra. This activity then launches WatermelonDS from inside its own
 * package so Android permits targeting the built-in lower display.
 */
class PocketDsLaunchActivity : Activity(), Application.ActivityLifecycleCallbacks {
    private var gameStarted = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        application.registerActivityLifecycleCallbacks(this)
        forwardToLowerDisplay(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        forwardToLowerDisplay(intent)
    }

    private fun forwardToLowerDisplay(source: Intent) {
        if (gameStarted) return

        val romUri = source.data
            ?: source.getStringExtra(EXTRA_URI)?.let(Uri::parse)
            ?: run {
                finishAndRemoveTask()
                return
            }

        val launchIntent = Intent(this, EmulatorActivity::class.java).apply {
            action = "$packageName.LAUNCH_ROM"
            data = romUri
            addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP)
            addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        val options = ActivityOptions.makeBasic().apply {
            setLaunchDisplayId(POCKET_DS_LOWER_DISPLAY_ID)
        }

        gameStarted = true
        try {
            startActivity(launchIntent, options.toBundle())
        } catch (error: RuntimeException) {
            gameStarted = false
            finishAndRemoveTask()
            throw error
        }
    }

    override fun onActivityDestroyed(activity: Activity) {
        if (gameStarted && activity is EmulatorActivity) {
            // Removing the lower-display task and revealing Pegasus in the same
            // frame makes Qt rebuild its surface against stale display state.
            // Let Android finish the display teardown before uncovering it.
            Handler(Looper.getMainLooper()).postDelayed(
                { if (!isFinishing && !isDestroyed) finishAndRemoveTask() },
                FRONTEND_RETURN_DELAY_MS,
            )
        }
    }

    override fun onDestroy() {
        application.unregisterActivityLifecycleCallbacks(this)
        super.onDestroy()
    }

    override fun onActivityCreated(activity: Activity, state: Bundle?) = Unit
    override fun onActivityStarted(activity: Activity) = Unit
    override fun onActivityResumed(activity: Activity) = Unit
    override fun onActivityPaused(activity: Activity) = Unit
    override fun onActivityStopped(activity: Activity) = Unit
    override fun onActivitySaveInstanceState(activity: Activity, state: Bundle) = Unit

    private companion object {
        private const val EXTRA_URI = "uri"
        private const val POCKET_DS_LOWER_DISPLAY_ID = 2
        private const val FRONTEND_RETURN_DELAY_MS = 2000L
    }
}
