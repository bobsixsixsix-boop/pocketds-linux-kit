// SPDX-License-Identifier: GPL-2.0-or-later
.pragma library
function run(c) {
    const controllerTransport=c.transport, controllerSession=c.session,
          shortcutDialog=c.dialog, closeShortcuts=c.closeButton,
          nativeTest=c.nativeTest, fullRoot=c.surface;
    function check(name, condition) {
        console.warn("CONTROLLER_TEST", name, condition);
        if (!condition) throw new Error(name);
    }
            controllerTransport.autoReply = true;
            controllerTransport.sample = {status:"active",buttons:{},axes:{lx:0,ly:0,rx:0,ry:0,lt:0,rt:0},error:""};
            shortcutDialog.close();nativeTest.tryCompare(shortcutDialog,"visible",false,1000);nativeTest.wait(100);
            controllerTransport.autoReply = false;
            shortcutDialog.open();nativeTest.tryCompare(shortcutDialog,"opened",true,1000);
            check("initial-focus-is-close", closeShortcuts.activeFocus);
            const begin = controllerSession.pendingCommand;
            check("opening-starts-capture", controllerSession.pendingOperation === "begin");
            check("no-false-highlight-before-ack", !controllerSession.active);
            shortcutDialog.close();nativeTest.tryCompare(shortcutDialog,"visible",false,1000);
            check("closing-stops-wanting-input", !controllerSession.requested);
            controllerTransport.deliver(begin,{buttons:{a:1}});
            check("late-begin-cannot-light-closed-page", !controllerSession.active && Object.keys(controllerSession.buttons).length === 0);
            check("late-begin-is-immediately-ended", controllerSession.pendingOperation === "end");
            controllerTransport.deliver(controllerSession.pendingCommand);
            check("end-clears-lease-token", controllerSession.token === "");
            controllerTransport.autoReply = true;
            shortcutDialog.open();nativeTest.tryCompare(shortcutDialog,"opened",true,1000);nativeTest.wait(100);
            check("acknowledged-session-is-active", controllerSession.active);
            nativeTest.keyClick(Qt.Key_Escape);nativeTest.tryCompare(shortcutDialog,"visible",false,1000);nativeTest.wait(100);
            check("escape-ends-capture", !controllerSession.active && controllerSession.token === "");
            shortcutDialog.open();nativeTest.tryCompare(shortcutDialog,"opened",true,1000);nativeTest.wait(100);
            controllerTransport.autoReply = false;nativeTest.wait(80);
            const waiting = controllerSession.pendingCommand;
            controllerSession.handleReply(waiting+"stale",{"stdout":"{}","exit code":0});
            check("stale-response-cannot-cancel-current-request", controllerSession.pendingCommand === waiting);
            controllerTransport.deliver(waiting,{axes:{lx:99,ly:0,rx:0,ry:0,lt:0,rt:0}});
            check("invalid-axis-clears-highlight", controllerSession.status === "error" && !controllerSession.active);
            controllerSession.retry();controllerTransport.deliver(controllerSession.pendingCommand,{buttons:{a:1}});
            check("retry-uses-real-state-reply", controllerSession.active && controllerSession.buttons.a);
            controllerSession.lastReplyMs=Date.now()-1100;nativeTest.wait(70);
            check("stale-state-stops-highlighting", !controllerSession.active && controllerSession.status === "error");
            controllerTransport.autoReply=true;
            controllerSession.retry();nativeTest.wait(100);
            fullRoot.visible=false;nativeTest.wait(300);
            check("hidden-panel-releases-session", !controllerSession.requested && !shortcutDialog.visible && !controllerSession.active);
            fullRoot.visible=true;
            nativeTest.wait(120);
            controllerTransport.autoReply=false;
            shortcutDialog.open();nativeTest.tryCompare(shortcutDialog,"opened",true,1000);
            controllerTransport.deliver(controllerSession.pendingCommand);
            const oldToken=controllerSession.token;
            shortcutDialog.close();nativeTest.tryCompare(shortcutDialog,"visible",false,1000);
            if(controllerSession.pendingOperation!=="end") controllerTransport.deliver(controllerSession.pendingCommand);
            check("close-sends-end-before-release",controllerSession.pendingOperation==="end");
            controllerTransport.deliver(controllerSession.pendingCommand,{status:"draining"});
            check("draining-retains-old-lease",controllerSession.token===oldToken && controllerSession.ending);
            shortcutDialog.open();nativeTest.tryCompare(shortcutDialog,"opened",true,1000);
            check("rapid-reopen-does-not-race-new-token",controllerSession.token===oldToken && controllerSession.pendingOperation==="poll");
            controllerTransport.deliver(controllerSession.pendingCommand,{status:"idle"});
            check("neutral-release-starts-new-session",controllerSession.pendingOperation==="begin" && controllerSession.token!==oldToken);
            controllerTransport.deliver(controllerSession.pendingCommand);
            check("rapid-reopen-resumes-capture",controllerSession.active);
            shortcutDialog.close();nativeTest.tryCompare(shortcutDialog,"visible",false,1000);
            if(controllerSession.pendingOperation!=="end") controllerTransport.deliver(controllerSession.pendingCommand);
            const failedEndToken=controllerSession.token;
            controllerTransport.deliver(controllerSession.pendingCommand,{status:"error",error:"恢复失败"});
            check("failed-end-is-not-reported-idle",controllerSession.status==="error" && controllerSession.token===failedEndToken && !controllerSession.ending);
            controllerTransport.autoReply=true;
            controllerSession.end();nativeTest.wait(120);
            console.warn("CONTROLLER_COMPLETE");
        }
