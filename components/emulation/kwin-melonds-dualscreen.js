// Arrange melonDS's two native windows on Pocket DS's two internal displays.
// [w1] is the emulated DS top screen; [w2] is the touch/bottom screen.

function outputNamed(name) {
    const outputs = workspace.screens;
    for (let i = 0; i < outputs.length; ++i) {
        if (outputs[i].name === name) {
            return outputs[i];
        }
    }
    return null;
}

function targetFor(window) {
    if (window.resourceClass !== "net.kuribo64.melonDS") {
        return null;
    }
    if (window.caption.indexOf("[w1]") === 0) {
        return outputNamed("DSI-1");
    }
    if (window.caption.indexOf("[w2]") === 0) {
        return outputNamed("DSI-2");
    }
    return null;
}

function arrange(window) {
    const target = targetFor(window);
    if (!target) {
        return;
    }

    // A gamepad/joymouse target rebuild can make Qt put both fullscreen
    // surfaces back on the primary output. Re-home an already-fullscreen
    // window without disturbing melonDS's native fullscreen/menu state.
    if (!window.output || window.output.name !== target.name) {
        workspace.sendClientToScreen(window, target);
    }
    if (window.fullScreen) return;

    window.frameGeometry = {
        x: target.geometry.x,
        y: target.geometry.y,
        width: target.geometry.width,
        height: target.geometry.height
    };
    window.noBorder = true;

    console.info("pocketds-melonds: arranged " + window.caption +
                 " on " + target.name + " at " + target.geometry);
}

workspace.windowAdded.connect(function(window) {
    window.captionChanged.connect(function() {
        arrange(window);
    });
    window.outputChanged.connect(function() {
        arrange(window);
    });
    window.fullScreenChanged.connect(function() {
        arrange(window);
    });
    arrange(window);
});

const existing = workspace.windowList();
for (let i = 0; i < existing.length; ++i) {
    arrange(existing[i]);
}
