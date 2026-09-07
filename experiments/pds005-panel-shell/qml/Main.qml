import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import org.kde.layershell 1.0 as LayerShell

Window {
    id: root

    visible: panelController.panelVisible && panelController.lowerScreenAvailable
    screen: panelController.lowerScreen
    color: "#0b111b"
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus

    // Opposing anchors delegate the current logical output geometry to KWin.
    // There is deliberately no captured physical/logical pixel size here.
    width: 0
    height: 0
    LayerShell.Window.scope: "pocketds-control-panel-experiment"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop
        | LayerShell.Window.AnchorBottom
        | LayerShell.Window.AnchorLeft
        | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.keyboardInteractivity:
        LayerShell.Window.KeyboardInteractivityNone
    LayerShell.Window.activateOnShow: false
    LayerShell.Window.wantsToBeOnActiveScreen: false
    LayerShell.Window.screenConfiguration:
        LayerShell.Window.ScreenFromQWindow

    Rectangle {
        anchors.fill: parent
        color: root.color

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 24
            spacing: 16

            Label {
                text: "Pocket DS Panel — PDS-005 实验壳"
                color: "#eef4ff"
                font.pixelSize: 24
                font.bold: true
            }
            Label {
                Layout.fillWidth: true
                text: "只验证 DSI-2 动态铺满与可逆隐藏；尚未接入正式 PanelContent。"
                color: "#9fadc1"
                wrapMode: Text.Wrap
                font.pixelSize: 17
            }
            Item { Layout.fillHeight: true }
            Button {
                Layout.fillWidth: true
                Layout.minimumHeight: 64
                text: "返回下屏桌面"
                onClicked: panelController.Hide()
            }
        }
    }
}
