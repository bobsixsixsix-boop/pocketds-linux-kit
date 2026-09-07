// Keep the single-window Pocket DS game runtime on the upper display.

const targetOutputName = "DSI-1";

function outputNamed(name) {
    const outputs = workspace.screens;
    for (let index = 0; index < outputs.length; ++index) {
        if (outputs[index].name === name) {
            return outputs[index];
        }
    }
    return null;
}

function isPocketDSGamescope(window) {
    const resourceClass = String(window.resourceClass || "").toLowerCase();
    const resourceName = String(window.resourceName || "").toLowerCase();
    return resourceClass === "gamescope" || resourceName === "gamescope";
}

function arrange(window) {
    if (!isPocketDSGamescope(window)) {
        return;
    }

    const target = outputNamed(targetOutputName);
    if (!target) {
        console.warn("pocketds-gamescope: missing output " + targetOutputName);
        return;
    }

    if (!window.output || window.output.name !== target.name) {
        workspace.sendClientToScreen(window, target);
    }
    if (!window.fullScreen) {
        window.frameGeometry = {
            x: target.geometry.x,
            y: target.geometry.y,
            width: target.geometry.width,
            height: target.geometry.height
        };
        window.noBorder = true;
        window.fullScreen = true;
    }
}

workspace.windowAdded.connect(function(window) {
    window.captionChanged.connect(function() { arrange(window); });
    window.outputChanged.connect(function() { arrange(window); });
    window.fullScreenChanged.connect(function() { arrange(window); });
    arrange(window);
});

const existing = workspace.windowList();
for (let index = 0; index < existing.length; ++index) {
    arrange(existing[index]);
}
