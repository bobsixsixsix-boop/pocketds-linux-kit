        QtObject {
            id: controllerTransport
            property bool autoReply: true
            property var commands: []
            property var sample: ({status:"active", buttons:{}, axes:{lx:0,ly:0,rx:0,ry:0,lt:0,rt:0}, error:""})
            function connectSource(command) {
                commands = commands.concat([command]);
                if (autoReply) Qt.callLater(function() { deliver(command); });
            }
            function disconnectSource(command) {}
            function deliver(command, override) {
                const parts = command.split(" ");
                const operation = parts[parts.length-2], token = parts[parts.length-1];
                const reply = {token:token, status:operation === "end" ? "idle" : sample.status,
                    buttons:sample.buttons, axes:sample.axes, error:sample.error};
                if (override) for (const key in override) reply[key] = override[key];
                controllerSession.handleReply(command, {"stdout":JSON.stringify(reply), "exit code":0});
            }
        }

        TestCase { id:nativeTest; when:false; name:"ControllerCaptureSession" }
        Timer {
            interval:1000;running:true
            onTriggered: {
                fullRoot.Window.window.width=819;fullRoot.Window.window.height=614;
                fullRoot.Window.window.requestActivate();
                NativeChecks.run({transport:controllerTransport,session:controllerSession,
                    dialog:shortcutDialog,closeButton:closeShortcuts,nativeTest:nativeTest,surface:fullRoot});
            }
        }
