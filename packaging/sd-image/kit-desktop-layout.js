// SPDX-License-Identifier: GPL-3.0-or-later
// Plasma 6 desktop scripting API: https://develop.kde.org/docs/plasma/scripting/api/
// Called on a clean image's first desktop login; preserves the incumbent Panel.
(function () {
    var lower = screenForConnector("DSI-2");
    if (lower < 0) throw new Error("The lower display is not ready");
    var desktop = desktopForScreen(lower);
    if (!desktop) throw new Error("The lower desktop is not ready");
    var geometry = screenGeometry(lower);
    if (geometry.width < 320 || geometry.height < 240)
        throw new Error("The lower display geometry is not ready");
    var widgets = desktop.widgets();
    var widget = null;
    for (var i = 0; i < widgets.length; ++i) {
        if (widgets[i].type === "org.pocketds.controlpanel.v3") {
            if (widget) throw new Error("The lower desktop has duplicate Panels");
            widget = widgets[i];
        }
    }
    if (!widget) widget = desktop.addWidget("org.pocketds.controlpanel.v3");
    if (!widget) throw new Error("The Panel package is unavailable");
    widget.geometry = QRectF(0, 0, geometry.width, geometry.height);
    desktop.wallpaperPlugin = "org.kde.color";
    desktop.currentConfigGroup = ["Wallpaper", "org.kde.color", "General"];
    desktop.writeConfig("Color", "#090c0d");
    print("POCKETDS-DESKTOP-READY");
}());
