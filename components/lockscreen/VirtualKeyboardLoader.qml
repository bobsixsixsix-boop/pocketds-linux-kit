/*
 * EXPERIMENTAL Pocket DS lock-screen keyboard prototype.
 *
 * This file is archived for isolated greeter testing and MUST NOT be copied
 * over the RPM-owned Plasma component by the normal installer. A normal
 * GTK/X11 window cannot be shown above KScreenLocker by design, but being in
 * the greeter process alone does not make this prototype tested or trusted.
 */

import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtQuick.Templates as T
import org.kde.kirigami as Kirigami

Item {
    id: loader

    required property Item screenRoot
    required property T.StackView mainStack
    required property Item mainBlock
    required property T.TextField passwordField

    readonly property bool keyboardActive: state === "visible"
    property bool shifted: false
    property bool symbols: false

    function showHide() {
        state = state === "hidden" ? "visible" : "hidden"
        if (state === "visible") {
            passwordField.forceActiveFocus()
        }
    }

    function typeText(text) {
        passwordField.insert(passwordField.cursorPosition, text)
        passwordField.forceActiveFocus()
        if (shifted && !symbols) {
            shifted = false
        }
    }

    function backspace() {
        if (passwordField.selectionStart !== passwordField.selectionEnd) {
            passwordField.remove(passwordField.selectionStart, passwordField.selectionEnd)
        } else if (passwordField.cursorPosition > 0) {
            passwordField.remove(passwordField.cursorPosition - 1, passwordField.cursorPosition)
        }
        passwordField.forceActiveFocus()
    }

    anchors.left: parent.left
    anchors.right: parent.right
    height: Math.min(screenRoot.height * 0.58, 620)
    state: "hidden"
    z: 10

    component KeyButton: QQC2.Button {
        required property string keyText
        property real units: 1
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.preferredWidth: units
        text: keyText
        font.pixelSize: Math.max(20, Math.min(32, height * 0.38))
        focusPolicy: Qt.NoFocus
        onClicked: loader.typeText(keyText)
    }

    Rectangle {
        anchors.fill: parent
        color: "#11151c"
        border.color: "#465466"
        border.width: 2

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 7

            Repeater {
                model: loader.symbols
                    ? ["1234567890", "!@#$%^&*()", "-_+=[]{}\\/", "~:;'\",.<>?"]
                    : ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"]

                RowLayout {
                    required property string modelData
                    spacing: 6
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    Item { visible: !loader.symbols && index >= 2; Layout.preferredWidth: index === 2 ? 28 : 58 }
                    Repeater {
                        model: modelData.split("")
                        KeyButton {
                            keyText: loader.shifted && !loader.symbols ? modelData.toUpperCase() : modelData
                        }
                    }
                    Item { visible: !loader.symbols && index >= 2; Layout.preferredWidth: index === 2 ? 28 : 58 }
                }
            }

            RowLayout {
                spacing: 6
                Layout.fillWidth: true
                Layout.fillHeight: true

                QQC2.Button {
                    text: "⇧"
                    checkable: true
                    checked: loader.shifted
                    focusPolicy: Qt.NoFocus
                    Layout.fillHeight: true
                    Layout.preferredWidth: 110
                    onClicked: loader.shifted = checked
                }
                QQC2.Button {
                    text: "#+="
                    checkable: true
                    checked: loader.symbols
                    focusPolicy: Qt.NoFocus
                    Layout.fillHeight: true
                    Layout.preferredWidth: 110
                    onClicked: loader.symbols = checked
                }
                KeyButton { keyText: " "; units: 3 }
                QQC2.Button {
                    text: "⌫"
                    focusPolicy: Qt.NoFocus
                    Layout.fillHeight: true
                    Layout.preferredWidth: 120
                    onClicked: loader.backspace()
                }
                QQC2.Button {
                    text: "解锁"
                    focusPolicy: Qt.NoFocus
                    Layout.fillHeight: true
                    Layout.preferredWidth: 140
                    onClicked: {
                        loader.passwordField.forceActiveFocus()
                        loader.passwordField.accepted()
                    }
                }
                QQC2.Button {
                    text: "⌄"
                    focusPolicy: Qt.NoFocus
                    Layout.fillHeight: true
                    Layout.preferredWidth: 90
                    onClicked: loader.showHide()
                }
            }
        }
    }

    states: [
        State {
            name: "visible"
            PropertyChanges { mainStack.y: Math.min(0, screenRoot.height - loader.height - mainBlock.visibleBoundary) }
            PropertyChanges { loader.y: screenRoot.height - loader.height }
        },
        State {
            name: "hidden"
            PropertyChanges { mainStack.y: 0 }
            PropertyChanges { loader.y: screenRoot.height }
        }
    ]

    transitions: Transition {
        NumberAnimation {
            properties: "y"
            duration: Kirigami.Units.longDuration
            easing.type: Easing.InOutQuad
        }
    }
}
