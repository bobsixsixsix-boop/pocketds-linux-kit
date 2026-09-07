// Keep RetroArch's single-screen fullscreen surface on Pocket DS's upper panel.

const targetOutputName = "DSI-1";

function outputNamed(name) {
    const outputs = workspace.screens;
    for (let i = 0; i < outputs.length; ++i) {
        if (outputs[i].name === name) {
            return outputs[i];
        }
    }
    return null;
}

function isRetroArch(window) {
    const resourceClass = String(window.resourceClass || "").toLowerCase();
    const resourceName = String(window.resourceName || "").toLowerCase();
    return resourceClass === "com.libretro.retroarch" ||
           resourceName === "retroarch";
}

function arrange(window) {
    if (!isRetroArch(window)) {
        return;
    }

    const target = outputNamed(targetOutputName);
    if (!target) {
        console.warn("pocketds-retroarch: missing output " + targetOutputName);
        return;
    }

    let changed = false;
    if (!window.output || window.output.name !== target.name) {
        workspace.sendClientToScreen(window, target);
        changed = true;
    }
    if (!window.fullScreen) {
        window.frameGeometry = {
            x: target.geometry.x,
            y: target.geometry.y,
            width: target.geometry.width,
            height: target.geometry.height
        };
        window.noBorder = true;
        changed = true;
    }

    if (changed) {
        console.info("pocketds-retroarch: arranged " + window.caption +
                     " on " + target.name + " at " + target.geometry);
    }
}

workspace.windowAdded.connect(function(window) {
    window.captionChanged.connect(function() { arrange(window); });
    window.outputChanged.connect(function() { arrange(window); });
    window.fullScreenChanged.connect(function() { arrange(window); });
    arrange(window);
});

const existing = workspace.windowList();
for (let i = 0; i < existing.length; ++i) {
    arrange(existing[i]);
}
