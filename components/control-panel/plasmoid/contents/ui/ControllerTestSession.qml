// SPDX-License-Identifier: GPL-2.0-or-later
import QtQuick
import org.kde.plasma.plasma5support as P5Support

Item {
    id: session
    visible: false
    property bool requested: false
    // The native preview disables transport and supplies explicit test fixtures.
    property bool transportEnabled: true
    property var transport: executable
    property string status: "idle"
    property string error: ""
    property bool ending: false
    property var buttons: ({})
    property var axes: ({})
    property string token: ""
    property string pendingOperation: ""
    property string pendingCommand: ""
    property double requestStartedMs: 0
    property double lastReplyMs: 0
    readonly property bool active: requested && status === "active" &&
        lastReplyMs > 0 && Date.now() - lastReplyMs < 1000
    readonly property bool inFlight: pendingCommand.length > 0
    readonly property string helper: "/usr/local/bin/pocketds-panelctl controller-test "

    function newToken() {
        let value = ""
        for (let i = 0; i < 32; ++i)
            value += Math.floor(Math.random() * 16).toString(16)
        return value
    }
    function clearInput() {
        buttons = ({})
        axes = ({})
        lastReplyMs = 0
    }
    function send(operation) {
        if (!transportEnabled || inFlight || token.length !== 32) return
        pendingOperation = operation
        pendingCommand = helper + operation + " " + token
        requestStartedMs = Date.now()
        transport.connectSource(pendingCommand)
    }
    function begin() {
        if (!requested || inFlight || ending) return
        if (!token.length) token = newToken()
        clearInput()
        error = ""
        status = "starting"
        send("begin")
    }
    function end() {
        ending = token.length > 0
        clearInput()
        if (inFlight) return // The reply path sends end after an outstanding begin/poll.
        if (token.length) send("end")
        else status = "idle"
    }
    function retry() {
        if (!inFlight && requested) begin()
    }
    function applyReply(reply, operation) {
        if (!reply || reply.token !== token ||
            ["starting", "active", "draining", "idle", "busy", "error"].indexOf(reply.status) < 0)
            throw new Error("invalid controller test reply")
        if (reply.status === "idle") {
            if (operation === "begin") throw new Error("capture was not acquired")
            token = ""
            status = "idle"
            error = ""
            ending = false
            clearInput()
            if (requested) begin()
            return
        }
        if (reply.status === "draining") {
            status = "draining"
            ending = true
            error = typeof reply.error === "string" ? reply.error : ""
            clearInput()
            return
        }
        if (operation === "end") {
            // Only IDLE proves release. Preserve a failed lease for an explicit
            // retry; the independent owner watchdog still handles recovery.
            status = reply.status === "busy" ? "busy" : "error"
            error = typeof reply.error === "string" && reply.error.length
                ? reply.error : "手柄输入恢复尚未确认"
            ending = false
            clearInput()
            return
        }
        status = reply.status
        error = typeof reply.error === "string" ? reply.error : ""
        clearInput()
        if (status === "active" && requested && !ending) {
            if (!reply.buttons || typeof reply.buttons !== "object" || Array.isArray(reply.buttons) ||
                !reply.axes || typeof reply.axes !== "object" || Array.isArray(reply.axes))
                throw new Error("invalid controller state")
            const cleanButtons = ({})
            for (const key of ["a", "b", "x", "y", "lb", "rb", "lc", "rc", "menu", "view", "l3", "r3", "aya", "guide", "dpad_up", "dpad_down", "dpad_left", "dpad_right", "brightness_up", "brightness_down", "left_paddle", "right_paddle"]) {
                const value = reply.buttons[key]
                if (value !== undefined && value !== 0 && value !== 1 && value !== false && value !== true)
                    throw new Error("invalid controller button")
                cleanButtons[key] = value === 1 || value === true
            }
            const cleanAxes = ({})
            for (const key of ["lx", "ly", "rx", "ry", "lt", "rt"]) {
                const value = reply.axes[key]
                if (typeof value !== "number" || !isFinite(value) || value < (key === "lt" || key === "rt" ? 0 : -1) || value > 1)
                    throw new Error("invalid controller axis")
                cleanAxes[key] = value
            }
            buttons = cleanButtons
            axes = cleanAxes
            lastReplyMs = Date.now()
        }
        if (!requested || ending) end()
    }
    function failed() {
        ending = false
        status = "error"
        error = "手柄测试连接中断"
        clearInput()
        if (!requested) end()
    }
    function handleReply(sourceName, data) {
        transport.disconnectSource(sourceName)
        if (sourceName !== pendingCommand) return
        const operation = pendingOperation
        pendingCommand = ""
        pendingOperation = ""
        try {
            const reply = JSON.parse(String(data["stdout"] || ""))
            const exitCode = Number(data["exit code"])
            if (!isFinite(exitCode) || (exitCode !== 0 && reply.status !== "error"))
                throw new Error("controller transport failed")
            applyReply(reply, operation)
        } catch (failure) {
            if (operation === "end") {
                ending = false
                status = "error"
                error = "手柄输入恢复尚未确认"
                clearInput()
            } else failed()
        }
    }
    onRequestedChanged: {
        if (requested) begin()
        else end()
    }
    Component.onDestruction: {
        // Best effort only: the privileged owner independently expires the lease.
        if (transportEnabled && token.length)
            transport.connectSource(helper + "end " + token)
    }

    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: function(sourceName, data) { session.handleReply(sourceName, data) }
    }

    Timer {
        interval: 50
        repeat: true
        running: session.transportEnabled && (session.requested || session.inFlight || session.ending)
        onTriggered: {
            const now = Date.now()
            if (session.lastReplyMs && now - session.lastReplyMs >= 1000) {
                session.clearInput()
                session.status = "error"
                session.error = "手柄测试连接中断"
            }
            if (session.inFlight) {
                if (now - session.requestStartedMs >= 3200) {
                    const operation = session.pendingOperation
                    session.transport.disconnectSource(session.pendingCommand)
                    session.pendingCommand = ""
                    session.pendingOperation = ""
                    if (operation === "end") {
                        session.ending = false
                        session.status = "error"
                        session.error = "手柄输入恢复尚未确认"
                        session.clearInput()
                    } else session.failed()
                }
                return
            }
            if (session.ending || (session.requested &&
                (session.status === "active" || session.status === "starting")))
                session.send("poll")
        }
    }
}
