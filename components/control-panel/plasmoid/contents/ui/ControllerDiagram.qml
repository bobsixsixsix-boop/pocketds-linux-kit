// SPDX-License-Identifier: GPL-2.0-or-later

import QtQuick
import QtQuick.Shapes

Item {
    id: diagram

    property var buttons: ({})
    property var axes: ({})
    property bool active: false

    readonly property color bodyColor: "#151a1b"
    readonly property color keyColor: "#252d2e"
    readonly property color rimColor: "#455052"
    readonly property color labelColor: "#d4dcdb"
    readonly property color accentColor: "#62d8d5"
    readonly property color accentDarkColor: "#102a29"
    readonly property string labelFont: "Noto Sans CJK SC"

    implicitWidth: 711
    implicitHeight: 380

    function pressed(key) {
        return active && buttons !== null && buttons !== undefined && buttons[key] === true;
    }

    function axis(key, minimum, maximum) {
        if (!active || axes === null || axes === undefined)
            return 0;
        var value = axes[key];
        return typeof value === "number" && isFinite(value)
                ? Math.max(minimum, Math.min(maximum, value)) : 0;
    }

    // Drawing only: controller state comes from the exclusive input session.
    // No pointer or keyboard handlers belong in this component.
    component KeyCap: Rectangle {
        id: keyCap
        property string keyId: ""
        property string label: ""
        property int labelSize: 16
        readonly property bool down: diagram.pressed(keyId)
        objectName: "controller-key-" + keyId
        color: down ? diagram.accentColor : diagram.keyColor
        border.width: down ? 2 : 1.5
        border.color: down ? diagram.accentColor : diagram.rimColor
        radius: height / 2
        antialiasing: true

        Text {
            anchors.centerIn: parent
            text: keyCap.label
            font.family: diagram.labelFont
            font.pixelSize: keyCap.labelSize
            font.weight: Font.DemiBold
            color: keyCap.down ? diagram.accentDarkColor : diagram.labelColor
        }
    }

    component Trigger: Rectangle {
        id: trigger
        property string axisId: ""
        property string label: ""
        readonly property real depth: diagram.axis(axisId, 0, 1)
        objectName: "controller-axis-" + axisId
        width: 67
        height: 46
        radius: 12
        color: diagram.keyColor
        border.width: depth > 0 ? 2 : 1.5
        border.color: depth > 0 ? diagram.accentColor : diagram.rimColor
        antialiasing: true

        Rectangle {
            x: 6
            y: parent.height - 6 - height
            width: parent.width - 12
            height: (parent.height - 12) * trigger.depth
            radius: Math.min(6, height / 2)
            color: diagram.accentColor
            opacity: 0.3
        }
        Text {
            anchors.centerIn: parent
            text: trigger.label
            font.family: diagram.labelFont
            font.pixelSize: 16
            font.weight: Font.DemiBold
            color: trigger.depth > 0 ? diagram.accentColor : diagram.labelColor
        }
    }

    component Stick: Item {
        id: stick
        property string xAxis: ""
        property string yAxis: ""
        property string clickKey: ""
        property string label: ""
        readonly property real xValue: diagram.axis(xAxis, -1, 1)
        readonly property real yValue: diagram.axis(yAxis, -1, 1)
        readonly property bool down: diagram.pressed(clickKey)
        readonly property bool moved: Math.abs(xValue) > 0.03 || Math.abs(yValue) > 0.03
        objectName: "controller-stick-" + clickKey
        width: 84
        height: 84

        Rectangle {
            anchors.fill: parent
            radius: width / 2
            color: "#0c1112"
            border.color: stick.down ? diagram.accentColor : "#364244"
            border.width: stick.down ? 3 : 1.5
            antialiasing: true
        }
        Rectangle {
            anchors.centerIn: parent
            width: 64
            height: 64
            radius: 32
            color: "transparent"
            border.width: 1
            border.color: "#253133"
            antialiasing: true
        }
        Rectangle {
            x: (parent.width - width) / 2 + stick.xValue * 18
            y: (parent.height - height) / 2 + stick.yValue * 18
            width: 46
            height: 46
            radius: width / 2
            color: stick.down ? diagram.accentColor : diagram.keyColor
            border.width: stick.moved || stick.down ? 2 : 1.5
            border.color: stick.moved || stick.down ? diagram.accentColor : diagram.rimColor
            antialiasing: true

            Text {
                anchors.centerIn: parent
                text: stick.label
                font.family: diagram.labelFont
                font.pixelSize: 13
                font.weight: Font.DemiBold
                color: stick.down ? diagram.accentDarkColor : diagram.labelColor
            }
        }
    }

    component Direction: Item {
        id: direction
        property string keyId: ""
        readonly property bool down: diagram.pressed(keyId)
        objectName: "controller-key-" + keyId
        width: 32
        height: 42

        Shape {
            anchors.fill: parent
            antialiasing: true
            ShapePath {
                strokeWidth: 1.5
                strokeColor: direction.down ? diagram.accentColor : diagram.rimColor
                fillColor: direction.down ? diagram.accentColor : diagram.keyColor
                startX: 7; startY: 1
                PathLine { x: 25; y: 1 }
                PathQuad { x: 31; y: 7; controlX: 31; controlY: 1 }
                PathLine { x: 31; y: 27 }
                PathLine { x: 16; y: 41 }
                PathLine { x: 1; y: 27 }
                PathLine { x: 1; y: 7 }
                PathQuad { x: 7; y: 1; controlX: 1; controlY: 1 }
            }
            ShapePath {
                strokeWidth: 2
                strokeColor: direction.down ? diagram.accentDarkColor : "#8c999a"
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                startX: 11; startY: 14
                PathLine { x: 16; y: 9 }
                PathLine { x: 21; y: 14 }
            }
        }
    }

    Item {
        id: canvas
        width: 711
        height: 380
        anchors.centerIn: parent
        scale: Math.min(diagram.width / width, diagram.height / height)

        // Rear controls are unfolded above the front face, keeping their sides.
        Trigger { x: 51; y: 9; axisId: "lt"; label: "LT" }
        Trigger { x: 593; y: 9; axisId: "rt"; label: "RT" }
        KeyCap { x: 51; y: 62; width: 75; height: 34; keyId: "lb"; label: "LB" }
        KeyCap { x: 135; y: 64; width: 49; height: 30; keyId: "lc"; label: "LC"; labelSize: 14 }
        KeyCap { x: 527; y: 64; width: 49; height: 30; keyId: "rc"; label: "RC"; labelSize: 14 }
        KeyCap { x: 585; y: 62; width: 75; height: 34; keyId: "rb"; label: "RB" }

        Rectangle {
            x: 192; y: 75; width: 327; height: 18
            z: -1
            radius: 5
            color: "#242c2d"
            border.color: "#3b4749"
            border.width: 1
        }
        Rectangle {
            x: 24; y: 86; width: 663; height: 283
            z: -2
            radius: 36
            color: diagram.bodyColor
            border.color: "#3b4749"
            border.width: 1.5
            antialiasing: true
        }
        Rectangle {
            x: 191; y: 101; width: 329; height: 245
            radius: 7
            color: "#222b2c"
            border.color: "#394547"
            border.width: 1

            Rectangle {
                x: 4.5; y: 2.5; width: 320; height: 240
                radius: 4
                color: "#0a1011"
                border.color: "#111b1c"
                border.width: 1
            }
        }

        Stick { x: 66; y: 105; xAxis: "lx"; yAxis: "ly"; clickKey: "l3"; label: "L3" }
        Stick { x: 561; y: 205; xAxis: "rx"; yAxis: "ry"; clickKey: "r3"; label: "R3" }

        Item {
            x: 58; y: 193; width: 100; height: 100
            Rectangle {
                anchors.centerIn: parent
                width: 100; height: 100; radius: 50
                color: "#101617"
                border.width: 1
                border.color: "#293537"
                antialiasing: true
            }
            Direction { x: 34; y: 5; keyId: "dpad_up" }
            Direction { x: 53; y: 29; rotation: 90; keyId: "dpad_right" }
            Direction { x: 34; y: 53; rotation: 180; keyId: "dpad_down" }
            Direction { x: 15; y: 29; rotation: 270; keyId: "dpad_left" }
        }

        KeyCap { x: 583; y: 97; width: 40; height: 40; keyId: "y"; label: "Y"; labelSize: 19 }
        KeyCap { x: 554; y: 126; width: 40; height: 40; keyId: "x"; label: "X"; labelSize: 19 }
        KeyCap { x: 612; y: 126; width: 40; height: 40; keyId: "b"; label: "B"; labelSize: 19 }
        KeyCap { x: 583; y: 155; width: 40; height: 40; keyId: "a"; label: "A"; labelSize: 19 }

        // Menu / View and AYA / = follow the diagonal pairs on the Pocket DS.
        KeyCap {
            id: menuCap
            x: 119; y: 298; width: 32; height: 32; keyId: "menu"
            Column {
                anchors.centerIn: parent
                spacing: 3
                Repeater {
                    model: 3
                    Rectangle { width: 13; height: 1.6; radius: 0.8; color: menuCap.down ? diagram.accentDarkColor : diagram.labelColor }
                }
            }
        }
        KeyCap {
            id: viewCap
            x: 81; y: 315; width: 32; height: 32; keyId: "view"
            Rectangle { x: 9; y: 8; width: 11; height: 9; radius: 1; color: "transparent"; border.width: 1.4; border.color: viewCap.down ? diagram.accentDarkColor : diagram.labelColor }
            Rectangle { x: 12; y: 12; width: 11; height: 9; radius: 1; color: viewCap.color; border.width: 1.4; border.color: viewCap.down ? diagram.accentDarkColor : diagram.labelColor }
        }
        KeyCap { x: 560; y: 298; width: 36; height: 36; keyId: "aya"; label: "AYA"; labelSize: 13 }
        KeyCap { x: 608; y: 315; width: 32; height: 32; keyId: "guide"; label: "="; labelSize: 20 }

        // These shallow front-edge slots identify the real hardware silhouette.
        // The three system keys are intentionally unbound until physical event
        // identities are verified. Power / volume are outside controller input.
        Rectangle { x: 205; y: 352; width: 54; height: 8; radius: 4; color: "#222c2d" }
        Rectangle { x: 231; y: 353; width: 1; height: 6; color: "#101718" }
        Rectangle { x: 277; y: 352; width: 25; height: 8; radius: 4; color: "#222c2d" }
        Rectangle { x: 414; y: 352; width: 27; height: 8; radius: 4; color: "#0b1112"; border.color: "#293536"; border.width: 1 }
        Rectangle { x: 457; y: 352; width: 47; height: 8; radius: 4; color: "#222c2d" }
        Rectangle { x: 480; y: 353; width: 1; height: 6; color: "#101718" }

        Repeater {
            model: 5
            Rectangle { required property int index; x: 126 + index * 6; y: 352; width: 2.5; height: 8; radius: 1; color: "#374244" }
        }
        Repeater {
            model: 5
            Rectangle { required property int index; x: 558 + index * 6; y: 352; width: 2.5; height: 8; radius: 1; color: "#374244" }
        }
    }
}
