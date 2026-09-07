// SPDX-License-Identifier: GPL-2.0-or-later

import QtQuick
import QtQuick.Window
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import QtQuick.Shapes
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.plasma.plasmoid

PlasmoidItem {
    id: root

    readonly property color pageColor: "#090c0d"
    readonly property color surfaceColor: "#151a1b"
    readonly property color surfaceRaisedColor: "#1d2425"
    readonly property color surfacePressedColor: "#273031"
    readonly property color cardBorder: "#303839"
    readonly property color dividerColor: "#2a3233"
    readonly property color textColor: "#f3f0e9"
    readonly property color mutedColor: "#899392"
    readonly property color controlTextColor: "#c9d0cf"
    readonly property color accentColor: "#62d8d5"
    readonly property color accentMutedColor: "#17302f"
    readonly property color performanceColor: "#ff6a2b"
    readonly property color performanceMutedColor: "#3b2318"
    readonly property color successColor: "#62d8d5"
    readonly property color dangerColor: "#ff7257"
    readonly property string uiFontFamily: "Noto Sans CJK SC"
    readonly property string numericFontFamily: "Noto Sans Mono"

    // Phosphor Icons Regular (MIT), Copyright (c) 2023 Phosphor Icons.
    // Verified reference and local adaptations: ../../PHOSPHOR-SOURCES.json.
    // Complete notice: ../../licenses/Phosphor-Icons-MIT.txt.
    // Permission is hereby granted, free of charge, to any person obtaining a
    // copy of this software and associated documentation files (the
    // "Software"), to deal in the Software without restriction, including
    // without limitation the rights to use, copy, modify, merge, publish,
    // distribute, sublicense, and/or sell copies of the Software, and to
    // permit persons to whom the Software is furnished to do so, subject to
    // inclusion of this notice. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT
    // WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
    // MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
    // IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
    // CLAIM, DAMAGES OR OTHER LIABILITY ARISING FROM THE SOFTWARE OR ITS USE.
    readonly property var phosphorIconPaths: ({
        "x": "M205.66,194.34a8,8,0,0,1-11.32,11.32L128,139.31,61.66,205.66a8,8,0,0,1-11.32-11.32L116.69,128,50.34,61.66A8,8,0,0,1,61.66,50.34L128,116.69l66.34-66.35a8,8,0,0,1,11.32,11.32L139.31,128Z",
        "arrows-clockwise": "M224,48V96a8,8,0,0,1-8,8H168a8,8,0,0,1,0-16h28.69L182.06,73.37a79.56,79.56,0,0,0-56.13-23.43h-.45A79.52,79.52,0,0,0,69.59,72.71,8,8,0,0,1,58.41,61.27a96,96,0,0,1,135,.79L208,76.69V48a8,8,0,0,1,16,0ZM186.41,183.29a80,80,0,0,1-112.47-.66L59.31,168H88a8,8,0,0,0,0-16H40a8,8,0,0,0-8,8v48a8,8,0,0,0,16,0V179.31l14.63,14.63A95.43,95.43,0,0,0,130,222.06h.53a95.36,95.36,0,0,0,67.07-27.33,8,8,0,0,0-11.18-11.44Z",
        "battery-charging": "M200,56H32A24,24,0,0,0,8,80v96a24,24,0,0,0,24,24H200a24,24,0,0,0,24-24V80A24,24,0,0,0,200,56Zm8,120a8,8,0,0,1-8,8H32a8,8,0,0,1-8-8V80a8,8,0,0,1,8-8H200a8,8,0,0,1,8,8Zm48-80v64a8,8,0,0,1-16,0V96a8,8,0,0,1,16,0ZM138.81,123.79a8,8,0,0,1,.35,7.79l-16,32a8,8,0,0,1-14.32-7.16L119.06,136H100a8,8,0,0,1-7.16-11.58l16-32a8,8,0,1,1,14.32,7.16L112.94,120H132A8,8,0,0,1,138.81,123.79Z",
        "battery-high": "M200,56H32A24,24,0,0,0,8,80v96a24,24,0,0,0,24,24H200a24,24,0,0,0,24-24V80A24,24,0,0,0,200,56Zm8,120a8,8,0,0,1-8,8H32a8,8,0,0,1-8-8V80a8,8,0,0,1,8-8H200a8,8,0,0,1,8,8ZM144,96v64a8,8,0,0,1-16,0V96a8,8,0,0,1,16,0Zm-40,0v64a8,8,0,0,1-16,0V96a8,8,0,0,1,16,0ZM64,96v64a8,8,0,0,1-16,0V96a8,8,0,0,1,16,0Zm192,0v64a8,8,0,0,1-16,0V96a8,8,0,0,1,16,0Z",
        "battery-warning": "M256,96v64a8,8,0,0,1-16,0V96a8,8,0,0,1,16,0ZM224,80v96a24,24,0,0,1-24,24H32A24,24,0,0,1,8,176V80A24,24,0,0,1,32,56H200A24,24,0,0,1,224,80Zm-16,0a8,8,0,0,0-8-8H32a8,8,0,0,0-8,8v96a8,8,0,0,0,8,8H200a8,8,0,0,0,8-8Zm-92,52a8,8,0,0,0,8-8V96a8,8,0,0,0-16,0v28A8,8,0,0,0,116,132Zm0,12a12,12,0,1,0,12,12A12,12,0,0,0,116,144Z",
        "caret-right": "M181.66,133.66l-80,80a8,8,0,0,1-11.32-11.32L164.69,128,90.34,53.66a8,8,0,0,1,11.32-11.32l80,80A8,8,0,0,1,181.66,133.66Z",
        "code": "M69.12,94.15,28.5,128l40.62,33.85a8,8,0,1,1-10.24,12.29l-48-40a8,8,0,0,1,0-12.29l48-40a8,8,0,0,1,10.24,12.3Zm176,27.7-48-40a8,8,0,1,0-10.24,12.3L227.5,128l-40.62,33.85a8,8,0,1,0,10.24,12.29l48-40a8,8,0,0,0,0-12.29ZM162.73,32.48a8,8,0,0,0-10.25,4.79l-64,176a8,8,0,0,0,4.79,10.26A8.14,8.14,0,0,0,96,224a8,8,0,0,0,7.52-5.27l64-176A8,8,0,0,0,162.73,32.48Z",
        "cpu": "M152,96H104a8,8,0,0,0-8,8v48a8,8,0,0,0,8,8h48a8,8,0,0,0,8-8V104A8,8,0,0,0,152,96Zm-8,48H112V112h32Zm88,0H216V112h16a8,8,0,0,0,0-16H216V56a16,16,0,0,0-16-16H160V24a8,8,0,0,0-16,0V40H112V24a8,8,0,0,0-16,0V40H56A16,16,0,0,0,40,56V96H24a8,8,0,0,0,0,16H40v32H24a8,8,0,0,0,0,16H40v40a16,16,0,0,0,16,16H96v16a8,8,0,0,0,16,0V216h32v16a8,8,0,0,0,16,0V216h40a16,16,0,0,0,16-16V160h16a8,8,0,0,0,0-16Zm-32,56H56V56H200v95.87s0,.09,0,.13,0,.09,0,.13V200Z",
        "device-tablet": "M192,24H64A24,24,0,0,0,40,48V208a24,24,0,0,0,24,24H192a24,24,0,0,0,24-24V48A24,24,0,0,0,192,24ZM56,72H200V184H56Zm8-32H192a8,8,0,0,1,8,8v8H56V48A8,8,0,0,1,64,40ZM192,216H64a8,8,0,0,1-8-8v-8H200v8A8,8,0,0,1,192,216Z",
        "fan": "M233,135a60,60,0,0,0-89.62-35.45l16.39-65.44a8,8,0,0,0-3.45-8.68A60,60,0,1,0,95.69,128.91L30.82,147.44a8,8,0,0,0-5.8,7.32,60,60,0,0,0,44.42,60.66,60.52,60.52,0,0,0,15.62,2.07,60.07,60.07,0,0,0,59.88-62l48.48,46.92a8,8,0,0,0,9.25,1.35A60,60,0,0,0,233,135Zm-121-7a16,16,0,1,1,16,16A16,16,0,0,1,112,128ZM80,76a44,44,0,0,1,62.75-39.82L127.77,96A32,32,0,0,0,99.85,112.8,43.85,43.85,0,0,1,80,76Zm27,119.57a44,44,0,0,1-65.86-34.43l59.31-16.94A32,32,0,0,0,128,160l.91,0A43.82,43.82,0,0,1,107,195.57Zm106.17-23a43.92,43.92,0,0,1-13,14.14l-44.32-42.89a31.91,31.91,0,0,0-.59-32.57,44,44,0,0,1,57.91,61.32Z",
        "game-controller": "M176,112H152a8,8,0,0,1,0-16h24a8,8,0,0,1,0,16ZM104,96H96V88a8,8,0,0,0-16,0v8H72a8,8,0,0,0,0,16h8v8a8,8,0,0,0,16,0v-8h8a8,8,0,0,0,0-16ZM241.48,200.65a36,36,0,0,1-54.94,4.81c-.12-.12-.24-.24-.35-.37L146.48,160h-37L69.81,205.09l-.35.37A36.08,36.08,0,0,1,44,216,36,36,0,0,1,8.56,173.75a.68.68,0,0,1,0-.14L24.93,89.52A59.88,59.88,0,0,1,83.89,40H172a60.08,60.08,0,0,1,59,49.25c0,.06,0,.12,0,.18l16.37,84.17a.68.68,0,0,1,0,.14A35.74,35.74,0,0,1,241.48,200.65ZM172,144a44,44,0,0,0,0-88H83.89A43.9,43.9,0,0,0,40.68,92.37l0,.13L24.3,176.59A20,20,0,0,0,58,194.3l41.92-47.59a8,8,0,0,1,6-2.71Zm59.7,32.59-8.74-45A60,60,0,0,1,172,160h-4.2L198,194.31a20.09,20.09,0,0,0,17.46,5.39,20,20,0,0,0,16.23-23.11Z",
        "gauge": "M207.06,72.67A111.24,111.24,0,0,0,128,40h-.4C66.07,40.21,16,91,16,153.13V176a16,16,0,0,0,16,16H224a16,16,0,0,0,16-16V152A111.25,111.25,0,0,0,207.06,72.67ZM224,176H119.71l54.76-75.3a8,8,0,0,0-12.94-9.42L99.92,176H32V153.13c0-3.08.15-6.12.43-9.13H56a8,8,0,0,0,0-16H35.27c10.32-38.86,44-68.24,84.73-71.66V80a8,8,0,0,0,16,0V56.33A96.14,96.14,0,0,1,221,128H200a8,8,0,0,0,0,16h23.67c.21,2.65.33,5.31.33,8Z",
        "graphics-card": "M232,48H16a8,8,0,0,0-8,8V208a8,8,0,0,0,16,0V192H40v16a8,8,0,0,0,16,0V192H72v16a8,8,0,0,0,16,0V192h16v16a8,8,0,0,0,16,0V192H232a16,16,0,0,0,16-16V64A16,16,0,0,0,232,48Zm0,128H24V64H232Zm-56-16a40,40,0,1,0-40-40A40,40,0,0,0,176,160Zm-24-40a23.74,23.74,0,0,1,2.35-10.34l32,32A23.74,23.74,0,0,1,176,144,24,24,0,0,1,152,120Zm48,0a23.74,23.74,0,0,1-2.35,10.34l-32-32A23.74,23.74,0,0,1,176,96,24,24,0,0,1,200,120ZM80,160a40,40,0,1,0-40-40A40,40,0,0,0,80,160ZM56,120a23.74,23.74,0,0,1,2.35-10.34l32,32A23.74,23.74,0,0,1,80,144,24,24,0,0,1,56,120Zm48,0a23.74,23.74,0,0,1-2.35,10.34l-32-32A23.74,23.74,0,0,1,80,96,24,24,0,0,1,104,120Z",
        "hand-tap": "M56,76a60,60,0,0,1,120,0,8,8,0,0,1-16,0,44,44,0,0,0-88,0,8,8,0,1,1-16,0Zm140,44a27.9,27.9,0,0,0-13.36,3.39A28,28,0,0,0,144,106.7V76a28,28,0,0,0-56,0v80l-3.82-6.13a28,28,0,0,0-48.41,28.17l29.32,50A8,8,0,1,0,78.89,220L49.6,170a12,12,0,1,1,20.78-12l.14.23,18.68,30A8,8,0,0,0,104,184V76a12,12,0,0,1,24,0v68a8,8,0,1,0,16,0V132a12,12,0,0,1,24,0v20a8,8,0,0,0,16,0v-4a12,12,0,0,1,24,0v36c0,21.61-7.1,36.3-7.16,36.42a8,8,0,0,0,3.58,10.73A7.9,7.9,0,0,0,208,232a8,8,0,0,0,7.16-4.42c.37-.73,8.85-18,8.85-43.58V148A28,28,0,0,0,196,120Z",
        "keyboard": "M224,48H32A16,16,0,0,0,16,64V192a16,16,0,0,0,16,16H224a16,16,0,0,0,16-16V64A16,16,0,0,0,224,48Zm0,144H32V64H224V192Zm-16-64a8,8,0,0,1-8,8H56a8,8,0,0,1,0-16H200A8,8,0,0,1,208,128Zm0-32a8,8,0,0,1-8,8H56a8,8,0,0,1,0-16H200A8,8,0,0,1,208,96ZM72,160a8,8,0,0,1-8,8H56a8,8,0,0,1,0-16h8A8,8,0,0,1,72,160Zm96,0a8,8,0,0,1-8,8H96a8,8,0,0,1,0-16h64A8,8,0,0,1,168,160Zm40,0a8,8,0,0,1-8,8h-8a8,8,0,0,1,0-16h8A8,8,0,0,1,208,160Z",
        "lightning": "M215.79,118.17a8,8,0,0,0-5-5.66L153.18,90.9l14.66-73.33a8,8,0,0,0-13.69-7l-112,120a8,8,0,0,0,3,13l57.63,21.61L88.16,238.43a8,8,0,0,0,13.69,7l112-120A8,8,0,0,0,215.79,118.17ZM109.37,214l10.47-52.38a8,8,0,0,0-5-9.06L62,132.71l84.62-90.66L136.16,94.43a8,8,0,0,0,5,9.06l52.8,19.8Z",
        "monitor": "M208,40H48A24,24,0,0,0,24,64V176a24,24,0,0,0,24,24H208a24,24,0,0,0,24-24V64A24,24,0,0,0,208,40Zm8,136a8,8,0,0,1-8,8H48a8,8,0,0,1-8-8V64a8,8,0,0,1,8-8H208a8,8,0,0,1,8,8Zm-48,48a8,8,0,0,1-8,8H96a8,8,0,0,1,0-16h64A8,8,0,0,1,168,224Z",
        "moon-stars": "M240,96a8,8,0,0,1-8,8H216v16a8,8,0,0,1-16,0V104H184a8,8,0,0,1,0-16h16V72a8,8,0,0,1,16,0V88h16A8,8,0,0,1,240,96ZM144,56h8v8a8,8,0,0,0,16,0V56h8a8,8,0,0,0,0-16h-8V32a8,8,0,0,0-16,0v8h-8a8,8,0,0,0,0,16Zm72.77,97a8,8,0,0,1,1.43,8A96,96,0,1,1,95.07,37.8a8,8,0,0,1,10.6,9.06A88.07,88.07,0,0,0,209.14,150.33,8,8,0,0,1,216.77,153Zm-19.39,14.88c-1.79.09-3.59.14-5.38.14A104.11,104.11,0,0,1,88,64c0-1.79,0-3.59.14-5.38A80,80,0,1,0,197.38,167.86Z",
        "mouse-simple": "M144,16H112A64.07,64.07,0,0,0,48,80v96a64.07,64.07,0,0,0,64,64h32a64.07,64.07,0,0,0,64-64V80A64.07,64.07,0,0,0,144,16Zm48,160a48.05,48.05,0,0,1-48,48H112a48.05,48.05,0,0,1-48-48V80a48.05,48.05,0,0,1,48-48h32a48.05,48.05,0,0,1,48,48ZM136,64v48a8,8,0,0,1-16,0V64a8,8,0,0,1,16,0Z",
        "network": "M232,112H136V88h8a16,16,0,0,0,16-16V40a16,16,0,0,0-16-16H112A16,16,0,0,0,96,40V72a16,16,0,0,0,16,16h8v24H24a8,8,0,0,0,0,16H56v32H48a16,16,0,0,0-16,16v32a16,16,0,0,0,16,16H80a16,16,0,0,0,16-16V176a16,16,0,0,0-16-16H72V128H184v32h-8a16,16,0,0,0-16,16v32a16,16,0,0,0,16,16h32a16,16,0,0,0,16-16V176a16,16,0,0,0-16-16h-8V128h32a8,8,0,0,0,0-16ZM112,40h32V72H112ZM80,208H48V176H80Zm128,0H176V176h32Z",
        "speaker-high": "M155.51,24.81a8,8,0,0,0-8.42.88L77.25,80H32A16,16,0,0,0,16,96v64a16,16,0,0,0,16,16H77.25l69.84,54.31A8,8,0,0,0,160,224V32A8,8,0,0,0,155.51,24.81ZM32,96H72v64H32ZM144,207.64,88,164.09V91.91l56-43.55Zm54-106.08a40,40,0,0,1,0,52.88,8,8,0,0,1-12-10.58,24,24,0,0,0,0-31.72,8,8,0,0,1,12-10.58ZM248,128a79.9,79.9,0,0,1-20.37,53.34,8,8,0,0,1-11.92-10.67,64,64,0,0,0,0-85.33,8,8,0,1,1,11.92-10.67A79.83,79.83,0,0,1,248,128Z",
        "speaker-slash": "M53.92,34.62A8,8,0,1,0,42.08,45.38L73.55,80H32A16,16,0,0,0,16,96v64a16,16,0,0,0,16,16H77.25l69.84,54.31A8,8,0,0,0,160,224V175.09l42.08,46.29a8,8,0,1,0,11.84-10.76ZM32,96H72v64H32ZM144,207.64,88,164.09V95.89l56,61.6Zm42-63.77a24,24,0,0,0,0-31.72,8,8,0,1,1,12-10.57,40,40,0,0,1,0,52.88,8,8,0,0,1-12-10.59Zm-80.16-76a8,8,0,0,1,1.4-11.23l39.85-31A8,8,0,0,1,160,32v74.83a8,8,0,0,1-16,0V48.36l-26.94,21A8,8,0,0,1,105.84,67.91ZM248,128a79.9,79.9,0,0,1-20.37,53.34,8,8,0,0,1-11.92-10.67,64,64,0,1,1,11.92-10.67A79.83,79.83,0,0,1,248,128Z",
        "speedometer": "M114.34,154.34l96-96a8,8,0,0,1,11.32,11.32l-96,96a8,8,0,0,1-11.32-11.32ZM128,88a63.9,63.9,0,0,1,20.44,3.33,8,8,0,1,0,5.11-15.16A80,80,0,0,0,48.49,160.88,8,8,0,0,0,56.43,168c.29,0,.59,0,.89-.05a8,8,0,0,0,7.07-8.83A64.92,64.92,0,0,1,64,152,64.07,64.07,0,0,1,128,88Zm99.74,13a8,8,0,0,0-14.24,7.3,96.27,96.27,0,0,1,5,75.71l-181.1-.07A96.24,96.24,0,0,1,128,56h.88a95,95,0,0,1,42.82,10.5A8,8,0,1,0,179,52.27a112,112,0,0,0-156.66,137A16.07,16.07,0,0,0,37.46,200H218.53a16,16,0,0,0,15.11-10.71,112.35,112.35,0,0,0-5.9-88.3Z",
        "sun": "M120,40V16a8,8,0,0,1,16,0V40a8,8,0,0,1-16,0Zm72,88a64,64,0,1,1-64-64A64.07,64.07,0,0,1,192,128Zm-16,0a48,48,0,1,0-48,48A48.05,48.05,0,0,0,176,128ZM58.34,69.66A8,8,0,0,0,69.66,58.34l-16-16A8,8,0,0,0,42.34,53.66Zm0,116.68-16,16a8,8,0,0,0,11.32,11.32l16-16a8,8,0,0,0-11.32-11.32ZM192,72a8,8,0,0,0,5.66-2.34l16-16a8,8,0,0,0-11.32-11.32l-16,16A8,8,0,0,0,192,72Zm5.66,114.34a8,8,0,0,0-11.32,11.32l16,16a8,8,0,0,0,11.32-11.32ZM48,128a8,8,0,0,0-8-8H16a8,8,0,0,0,0,16H40A8,8,0,0,0,48,128Zm80,80a8,8,0,0,0-8,8v24a8,8,0,0,0,16,0V216A8,8,0,0,0,128,208Zm112-88H216a8,8,0,0,0,0,16h24a8,8,0,0,0,0-16Z",
        "warning-circle": "M128,24A104,104,0,1,0,232,128,104.11,104.11,0,0,0,128,24Zm0,192a88,88,0,1,1,88-88A88.1,88.1,0,0,1,128,216Zm-8-80V80a8,8,0,0,1,16,0v56a8,8,0,0,1-16,0Zm20,36a12,12,0,1,1-12-12A12,12,0,0,1,140,172Z"
    })

    property real cpuPercent: -1
    property real previousCpuTotal: 0
    property real previousCpuIdle: 0
    property real previousRx: 0
    property real previousTx: 0
    property double previousSampleMs: 0
    property real cpuGHz: -1
    property int cpuCores: 8
    property int temperature: -1
    property real gpuPercent: -1
    property real gpuFrequencyMHz: -1
    property real gpuTemperature: -1
    property int gpuClients: -1
    property string gpuStatus: "unavailable"
    property string displayStatus: "unavailable"
    property string displaySession: ""
    property string displayBackend: ""
    property string displayCompositor: ""
    property string displayRenderer: ""
    property real topFps: -1
    property string topFpsStatus: "unavailable"
    property string topFpsSource: ""
    property real topRefreshHz: -1
    property real bottomRefreshHz: -1
    property real topScale: -1
    property real bottomScale: -1
    property int topLogicalWidth: -1
    property int topLogicalHeight: -1
    property int bottomLogicalWidth: -1
    property int bottomLogicalHeight: -1
    property int topPhysicalPixelWidth: -1
    property int topPhysicalPixelHeight: -1
    property int bottomPhysicalPixelWidth: -1
    property int bottomPhysicalPixelHeight: -1
    property int topPhysicalWidthMm: -1
    property int topPhysicalHeightMm: -1
    property int bottomPhysicalWidthMm: -1
    property int bottomPhysicalHeightMm: -1
    property int fanPercent: -1
    property string fanProfile: "unavailable"
    property string fanStatus: "unavailable"
    property real fanSampleAgeMs: -1
    property real downloadRate: -1
    property real uploadRate: -1
    property int topBrightness: -1
    property int bottomBrightness: -1
    property string brightnessWriteStatus: "unavailable"
    property int volumePercent: -1
    property bool muted: false
    property string powerProfile: "unavailable"
    property int lidAutoPoweroffMinutes: 0
    property string lidAutoPoweroffStatus: "unavailable"
    property string lidMode: "unknown"
    property bool lidSleepAvailable: false
    property string lidSleepReason: "unavailable"
    property bool lidOpen: false
    property bool lidModeValid: false
    property double lidModeSampleMs: 0
    property double lidModeSampleRequestMs: 0
    property real lidModeAgeMs: -1
    property double lidModeRequestMs: 0
    property double lidModeAttemptMs: 0
    readonly property bool lidModeHealthy: lidModeValid &&
        lidModeAgeMs >= 0 && lidModeAgeMs < 15000
    readonly property bool lidModeWritable: lidModeHealthy && lidOpen &&
        !actionPending("standby")
    property int gameFpsLimit: 0
    property string gameFpsLimitStatus: "unavailable"
    property bool gameFpsRuntimeActive: false
    property int batteryPercent: -1
    property string batteryState: "unavailable"
    property int batteryExternalPower: -1
    property real batteryTemperature: -1
    property real batteryVoltage: -1
    property real batteryCurrent: 0
    property real batteryPower: -1
    property string batteryPowerSource: "unavailable"
    property string batteryHealth: ""
    property int batteryCycles: -1
    property real systemPower: -1
    property string systemPowerSource: "unavailable"
    property bool displayPowered: true
    property bool quotaAvailable: false
    property bool quotaFresh: false
    property int quotaPrimaryRemaining: -1
    property int quotaSecondaryRemaining: -1
    property double quotaPrimaryReset: 0
    property double quotaSecondaryReset: 0
    property int quotaPrimaryWindowMinutes: -1
    property int quotaSecondaryWindowMinutes: -1
    property string quotaName: "未同步"
    property string currentTime: Qt.formatTime(new Date(), "hh:mm")
    property string inputMode: "unavailable"
    property string recoveryStatus: "none"
    property string recoveryRequestStatus: "none"
    property string recoveryError: ""
    property real recoveryAgeMs: -1
    property bool recoveryPending: false
    property bool panelExpanded: true
    property bool statusReady: false
    property bool statusSchemaValid: false
    property bool fanTelemetryValid: false
    property bool topBrightnessValid: false
    property bool bottomBrightnessValid: false
    property bool volumeTelemetryValid: false
    property bool powerTelemetryValid: false
    property bool lidAutoPoweroffTelemetryValid: false
    property bool gameFpsLimitTelemetryValid: false
    property bool displayTelemetryValid: false
    property bool quotaTelemetryValid: false
    property bool batteryTelemetryValid: false
    property bool systemPowerTelemetryValid: false
    property double lastStatusMs: 0
    property real telemetryAgeMs: -1
    property bool statusRequestInFlight: false
    property double statusRequestStartedMs: 0
    property int statusFailureCount: 0
    property double nextStatusAttemptMs: 0
    property string lastStatusError: ""
    property var pendingActions: ({})
    property var actionErrors: ({})
    property bool bootSwitchPending: false
    property bool bootSwitchCommitted: false
    property string bootSwitchError: ""
    readonly property string panelctlPath: "/usr/local/bin/pocketds-panelctl"
    readonly property string statusCommand: panelctlPath + " status"
    readonly property string lidModeCommand: panelctlPath + " lid-mode status"
    readonly property string bootSwitchCommand: panelctlPath + " boot-android CONFIRM"
    readonly property int activeStatusIntervalMs: 2000
    readonly property int standbyStatusIntervalMs: 5000
    readonly property int statusIntervalMs: displayPowered ? activeStatusIntervalMs : standbyStatusIntervalMs
    readonly property int statusTimeoutMs: 5000
    readonly property int statusRetryCapMs: 10000
    readonly property int quotaMaximumAgeS: 360
    readonly property int actionCommandTimeoutMs: 10000
    readonly property int actionConfirmationTimeoutMs: 10000
    readonly property bool telemetryHealthy: statusReady && statusSchemaValid &&
        telemetryAgeMs >= 0 && telemetryAgeMs <= 6500
    readonly property bool fanHealthy: telemetryHealthy && fanTelemetryValid &&
        fanStatus === "ok" && fanSampleAgeMs >= 0 &&
        fanSampleAgeMs + telemetryAgeMs <= 7000
    readonly property bool topBrightnessHealthy: telemetryHealthy && topBrightnessValid
    readonly property bool bottomBrightnessHealthy: telemetryHealthy && bottomBrightnessValid
    readonly property bool topBrightnessWritable: topBrightnessHealthy &&
        brightnessWriteStatus === "ok"
    readonly property bool bottomBrightnessWritable: bottomBrightnessHealthy &&
        brightnessWriteStatus === "ok"
    readonly property bool volumeHealthy: telemetryHealthy && volumeTelemetryValid
    readonly property bool volumeWritable: volumeHealthy
    readonly property bool powerHealthy: telemetryHealthy && powerTelemetryValid
    readonly property bool lidAutoPoweroffHealthy: telemetryHealthy &&
        lidAutoPoweroffTelemetryValid
    readonly property bool lidAutoPoweroffWritable: lidAutoPoweroffHealthy &&
        lidAutoPoweroffStatus !== "unavailable"
    readonly property bool gameFpsLimitWritable: telemetryHealthy &&
        gameFpsLimitTelemetryValid && gameFpsLimitStatus !== "unavailable"
    readonly property bool displayHealthy: telemetryHealthy && displayTelemetryValid &&
        displayStatus === "ok"
    readonly property bool topFpsHealthy: telemetryHealthy && topFpsStatus === "ok" &&
        topFps >= 0
    readonly property bool quotaHealthy: telemetryHealthy && quotaTelemetryValid &&
        quotaAvailable && quotaFresh
    readonly property bool batteryHealthy: telemetryHealthy && batteryTelemetryValid
    readonly property bool systemPowerHealthy: telemetryHealthy && systemPowerTelemetryValid &&
        systemPower >= 0
    readonly property bool inputHealthy: telemetryHealthy &&
        (inputMode === "joymouse" || inputMode === "gamepad")

    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Plasmoid.icon: "speedometer"
    preferredRepresentation: fullRepresentation
    implicitWidth: 819
    implicitHeight: 614

    function clamp(value, low, high) {
        return Math.max(low, Math.min(high, value))
    }

    function isRecord(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value)
    }

    function isFiniteNumber(value) {
        return typeof value === "number" && isFinite(value)
    }

    function isIntegerBetween(value, low, high) {
        return isFiniteNumber(value) && Math.floor(value) === value &&
            value >= low && value <= high
    }

    function isNumberBetween(value, low, high) {
        return isFiniteNumber(value) && value >= low && value <= high
    }

    function isOptionalNumberBetween(value, low, high) {
        return value === null || isNumberBetween(value, low, high)
    }

    function isOptionalPositiveInteger(value) {
        return value === null || isIntegerBetween(value, 1, 525600)
    }

    function isOptionalText(value) {
        return value === null || (typeof value === "string" && value.length <= 256)
    }

    function isOneOf(value, choices) {
        if (typeof value !== "string") return false
        return choices.indexOf(value) >= 0
    }

    function validStatusEnvelope(s) {
        return isRecord(s) &&
            isNumberBetween(s.cpu_total, 0, 9e15) &&
            isNumberBetween(s.cpu_idle, 0, s.cpu_total) &&
            isNumberBetween(s.cpu_ghz, 0, 10) &&
            isIntegerBetween(s.cpu_cores, 1, 512) &&
            isNumberBetween(s.rx_bytes, 0, 9e15) &&
            isNumberBetween(s.tx_bytes, 0, 9e15)
    }

    function validFanFields(s) {
        if (!isOneOf(s.fan_status, ["ok", "stale", "unavailable", "invalid"]) ||
            !isOptionalNumberBetween(s.fan_sample_age_ms, 0, 600000))
            return false
        if (s.fan_status !== "ok")
            return s.fan_percent === null && s.temp_c === null &&
                s.fan_profile === "unavailable"
        return isNumberBetween(s.fan_sample_age_ms, 0, 7000) &&
            isIntegerBetween(s.fan_percent, 0, 100) &&
            isIntegerBetween(s.temp_c, -50, 150) &&
            isOneOf(s.fan_profile,
                    ["quiet", "moderate", "aggressive", "custom", "auto", "off"])
    }

    function validBrightnessField(value) {
        return isIntegerBetween(value, 0, 100)
    }

    function validBrightnessWriteStatus(value) {
        return isOneOf(value, ["ok", "unavailable", "invalid"])
    }

    function validVolumeFields(s) {
        return isIntegerBetween(s.volume, 0, 150) && typeof s.muted === "boolean"
    }

    function validPowerFields(s) {
        return isOneOf(s.power_profile,
                       ["powersave", "balanced", "performance"])
    }

    function validLidAutoPoweroffFields(s) {
        if (!isIntegerBetween(s.lid_auto_poweroff_minutes, 0, 480) ||
            [0, 60, 120, 240, 480].indexOf(s.lid_auto_poweroff_minutes) < 0 ||
            !isOneOf(s.lid_auto_poweroff_status,
                     ["default", "ok", "invalid", "unavailable"]))
            return false
        if (s.lid_auto_poweroff_status !== "ok")
            return s.lid_auto_poweroff_minutes === 0
        return true
    }

    function validGameFpsLimitFields(s) {
        if (!isIntegerBetween(s.game_fps_limit, 0, 120) ||
            [0, 24, 30, 40, 60, 120].indexOf(s.game_fps_limit) < 0 ||
            !isOneOf(s.game_fps_limit_status,
                     ["default", "ok", "invalid", "unavailable"]) ||
            typeof s.game_fps_runtime_active !== "boolean")
            return false
        if (s.game_fps_limit_status !== "ok")
            return s.game_fps_limit === 0
        return true
    }

    function validBatteryFields(s) {
        return isOptionalNumberBetween(s.battery_percent, 0, 100) &&
            (s.battery_percent === null || Math.floor(s.battery_percent) === s.battery_percent) &&
            isOneOf(s.battery_state,
                    ["charging", "discharging", "full", "not-charging",
                     "unknown", "unavailable"]) &&
            (s.battery_external_power === null ||
             typeof s.battery_external_power === "boolean") &&
            isOptionalNumberBetween(s.battery_temp_c, -20, 100) &&
            isOptionalNumberBetween(s.battery_voltage_v, 1, 30) &&
            isOptionalNumberBetween(s.battery_current_a, -20, 20) &&
            isOptionalNumberBetween(s.battery_power_w, 0, 200) &&
            isOneOf(s.battery_power_source,
                    ["unavailable", "voltage-current"]) &&
            isOptionalText(s.battery_health) &&
            (s.battery_cycles === null ||
             isIntegerBetween(s.battery_cycles, 0, 10000000))
    }

    function validSystemPowerFields(s) {
        if (!isOneOf(s.system_power_source,
                     ["unavailable", "usb-input", "battery-discharge"]))
            return false
        if (s.system_power_source === "unavailable")
            return s.system_power_w === null
        return isNumberBetween(s.system_power_w, 0.01, 200)
    }

    function validDisplayFields(s) {
        if (!isOneOf(s.display_status, ["ok", "partial", "stale", "unavailable"]))
            return false
        const scalarFields = [
            s.display_dsi1_physical_width_px,
            s.display_dsi1_physical_height_px,
            s.display_dsi1_physical_width_mm,
            s.display_dsi1_physical_height_mm,
            s.display_dsi1_logical_width,
            s.display_dsi1_logical_height,
            s.display_dsi2_physical_width_px,
            s.display_dsi2_physical_height_px,
            s.display_dsi2_physical_width_mm,
            s.display_dsi2_physical_height_mm,
            s.display_dsi2_logical_width,
            s.display_dsi2_logical_height
        ]
        for (let i = 0; i < scalarFields.length; ++i) {
            if (!isOptionalNumberBetween(scalarFields[i], 1, 100000)) return false
        }
        if (!isOptionalNumberBetween(s.display_dsi1_scale, 0.25, 8) ||
            !isOptionalNumberBetween(s.display_dsi2_scale, 0.25, 8) ||
            !isOptionalNumberBetween(s.display_dsi1_refresh_hz, 1, 1000) ||
            !isOptionalNumberBetween(s.display_dsi2_refresh_hz, 1, 1000))
            return false
        if (!isOptionalText(s.display_session) || !isOptionalText(s.display_backend) ||
            !isOptionalText(s.display_compositor) || !isOptionalText(s.display_renderer))
            return false
        if (!isOptionalNumberBetween(s.display_app_fps, 0, 1000) ||
            !isOptionalNumberBetween(s.display_app_fps_age_ms, 0, 600000) ||
            !isOneOf(s.display_app_fps_status,
                     ["ok", "starting", "stale", "unavailable", "invalid"]) ||
            !isOneOf(s.display_app_fps_source,
                     ["gamescope-presented", "mangohud-retroarch",
                      "mangohud-steam", "moonlight-rendered"]))
            return false
        if ((s.display_app_fps_status === "ok") !== (s.display_app_fps !== null))
            return false
        if (s.display_status !== "ok") return true
        return s.display_dsi1_refresh_hz !== null &&
            s.display_dsi2_refresh_hz !== null
    }

    function validQuotaFields(quota) {
        if (quota === null) return true
        if (!isRecord(quota) || typeof quota.available !== "boolean" ||
            typeof quota.fresh !== "boolean" ||
            !isIntegerBetween(quota.synced_at, 1, 9e15))
            return false
        if (!isOptionalNumberBetween(quota.primary_used_percent, 0, 100) ||
            !isOptionalNumberBetween(quota.secondary_used_percent, 0, 100) ||
            !isOptionalNumberBetween(quota.primary_reset, 0, 9e15) ||
            !isOptionalNumberBetween(quota.secondary_reset, 0, 9e15) ||
            !isOptionalPositiveInteger(quota.primary_window_minutes) ||
            !isOptionalPositiveInteger(quota.secondary_window_minutes) ||
            !isOptionalText(quota.limit_name))
            return false
        if (!quota.available)
            return quota.fresh === false && quota.primary_used_percent === null &&
                quota.secondary_used_percent === null
        return quota.primary_used_percent !== null ||
            quota.secondary_used_percent !== null
    }

    function copyMap(source) {
        let result = {}
        for (const key in source) result[key] = source[key]
        return result
    }

    function actionPending(domain) {
        return pendingActions[domain] !== undefined
    }

    function pendingActionCount() {
        let count = 0
        for (const domain in pendingActions) ++count
        return count
    }

    function hasPendingConfirmation() {
        for (const domain in pendingActions) {
            if (pendingActions[domain].commandComplete) return true
        }
        return false
    }

    function actionError(domain) {
        return actionErrors[domain] === undefined ? "" : actionErrors[domain]
    }

    function pendingActionKind(domain) {
        return actionPending(domain) ? pendingActions[domain].expected.kind : ""
    }

    function pendingExpectedValue(domain, fallback) {
        const action = pendingActions[domain]
        return action === undefined ? fallback : action.expected.value
    }

    function presentedPowerProfile() {
        return pendingExpectedValue("power", powerProfile)
    }

    function presentedLidAutoPoweroffMinutes() {
        return pendingExpectedValue("standby", lidAutoPoweroffMinutes)
    }

    function presentedLidMode() {
        return pendingExpectedValue("lid-mode", lidModeHealthy ? lidMode : "unknown")
    }

    function requestLidModeStatus() {
        if (lidModeRequestMs > 0) return
        lidModeRequestMs = Date.now()
        lidModeAttemptMs = lidModeRequestMs
        executable.connectSource(lidModeCommand)
    }

    function applyLidModeStatus(s, requestedMs) {
        if (!s || !isOneOf(s.mode, ["connected", "sleep", "unknown"]) ||
            typeof s.sleep_available !== "boolean" || !isOneOf(s.reason_code,
                ["ready", "kernel_unverified", "not_enabled", "previous_cycle_failed",
                 "guard_unavailable", "suspend_denied", "unsupported_sleep_mode",
                 "config_unavailable", "query_failed"]) ||
            (s.lid_open !== null && typeof s.lid_open !== "boolean")) return false
        if (s.sleep_available !== (s.reason_code === "ready") ||
            (s.sleep_available && s.lid_open === null)) return false
        lidMode = s.mode
        lidSleepAvailable = s.sleep_available
        lidSleepReason = s.reason_code
        lidOpen = s.lid_open === true
        lidModeValid = true
        lidModeSampleMs = Date.now()
        lidModeSampleRequestMs = typeof requestedMs === "number" ? requestedMs : 0
        lidModeAgeMs = 0
        confirmPendingActions()
        return true
    }

    function lidSleepUnavailableText() {
        if (!lidModeHealthy) return lidModeRequestMs > 0
            ? "正在读取休眠状态…" : "合盖状态暂不可用。"
        if (lidSleepReason === "kernel_unverified")
            return "开盖唤醒尚未通过验证，休眠暂未开放。"
        if (lidSleepReason === "previous_cycle_failed")
            return "上次休眠恢复异常，休眠已暂停。"
        if (!lidSleepAvailable) return "系统尚未开放合盖休眠。"
        if (!lidOpen) return "请打开上盖后再修改。"
        return ""
    }

    function chooseLidMode(mode) {
        if (mode !== "connected" && mode !== "sleep") return false
        if (mode === "sleep" && !lidSleepAvailable) return false
        return beginAction("lid-mode", "lid-mode " + mode,
                           {"kind": "mode", "value": mode})
    }

    function presentedGameFpsLimit() {
        return pendingExpectedValue("game-limit", gameFpsLimit)
    }

    function gameFpsLimitChoiceText(value) {
        return value === 0 ? "自动" : value + " FPS"
    }

    function gameFpsLimitSummary() {
        if (actionPending("game-limit"))
            return "正在切到 · " + gameFpsLimitChoiceText(presentedGameFpsLimit())
        if (actionError("game-limit").length > 0) return "限帧设置失败"
        if (!gameFpsLimitWritable) return "限帧不可用"
        return displayFpsDetailText() + " · " +
            (presentedGameFpsLimit() === 0
                ? "自动" : "限 " + presentedGameFpsLimit())
    }

    function lidAutoPoweroffChoiceText(minutes) {
        if (minutes === 0) return "永不"
        return Math.round(minutes / 60) + " 小时"
    }

    function lidAutoPoweroffSummary() {
        if (actionPending("standby")) return "正在保存…"
        if (actionError("standby").length > 0) return "设置失败 · 点按重试"
        if (!lidAutoPoweroffHealthy || lidAutoPoweroffStatus === "unavailable")
            return "状态未知"
        if (lidAutoPoweroffStatus === "invalid") return "设置异常 · 点按修复"
        const minutes = presentedLidAutoPoweroffMinutes()
        return minutes === 0 ? "联网保活 · 不自动关机"
                             : "联网保活 · " + lidAutoPoweroffChoiceText(minutes) + "后关机"
    }

    function presentedFanProfile() {
        return pendingExpectedValue("fan", fanProfile)
    }

    function presentedInputMode() {
        return pendingExpectedValue("input", inputMode)
    }

    function profileSelected(domain, telemetryValue, buttonValue) {
        const value = pendingExpectedValue(domain, telemetryValue)
        return value === buttonValue ||
            (domain === "fan" && buttonValue === "moderate" && value === "auto")
    }

    function domainHealthy(domain) {
        if (domain === "fan") return fanHealthy
        if (domain === "top") return topBrightnessWritable
        if (domain === "bottom") return bottomBrightnessWritable
        if (domain === "volume") return volumeWritable
        if (domain === "power") return powerHealthy
        if (domain === "standby") return lidAutoPoweroffWritable &&
            presentedLidMode() === "connected" && !actionPending("lid-mode")
        if (domain === "lid-mode") return lidModeWritable
        if (domain === "game-limit") return gameFpsLimitWritable
        if (domain === "input") return inputHealthy
        return false
    }

    function setActionError(domain, message) {
        const next = copyMap(actionErrors)
        if (message.length > 0) next[domain] = message
        else delete next[domain]
        actionErrors = next
    }

    function beginAction(domain, args, expected) {
        if (!domainHealthy(domain) || actionPending(domain)) return false
        const command = panelctlPath + " " + args
        const now = Date.now()
        const next = copyMap(pendingActions)
        next[domain] = {
            "command": command,
            "expected": expected,
            "startedMs": now,
            "commandCompletedMs": 0,
            "deadlineMs": now + (domain === "lid-mode" ? 25000 : actionCommandTimeoutMs),
            "commandComplete": false
        }
        pendingActions = next
        setActionError(domain, "")
        executable.connectSource(command)
        return true
    }

    function removePendingAction(domain, error) {
        const action = pendingActions[domain]
        if (action === undefined) return
        if (!action.commandComplete) executable.disconnectSource(action.command)
        const next = copyMap(pendingActions)
        delete next[domain]
        pendingActions = next
        setActionError(domain, error)
    }

    function actionMatchesTelemetry(domain, expected) {
        if (domain === "fan") {
            if (!fanHealthy) return false
            if (expected.kind === "profile")
                return fanProfile === expected.value ||
                    (expected.value === "moderate" && fanProfile === "auto")
            return expected.kind === "manual" && fanProfile === "custom" &&
                Math.abs(fanPercent - expected.value) <= 1
        }
        if (domain === "top")
            return topBrightnessValid && Math.abs(topBrightness - expected.value) <= 1
        if (domain === "bottom")
            return bottomBrightnessValid && Math.abs(bottomBrightness - expected.value) <= 1
        if (domain === "volume") {
            if (!volumeTelemetryValid) return false
            if (expected.kind === "mute") return muted === expected.value
            return expected.kind === "level" &&
                Math.abs(volumePercent - expected.value) <= 1
        }
        if (domain === "power")
            return powerTelemetryValid && powerProfile === expected.value
        if (domain === "standby")
            return lidAutoPoweroffTelemetryValid &&
                lidAutoPoweroffStatus === "ok" &&
                lidAutoPoweroffMinutes === expected.value
        if (domain === "lid-mode")
            return lidModeHealthy && lidMode === expected.value &&
                pendingActions[domain] !== undefined &&
                lidModeSampleRequestMs > pendingActions[domain].commandCompletedMs
        if (domain === "game-limit")
            return gameFpsLimitTelemetryValid &&
                gameFpsLimitStatus === "ok" &&
                gameFpsLimit === expected.value
        if (domain === "input")
            return inputHealthy && inputMode === expected.value
        return false
    }

    function confirmPendingActions() {
        let next = copyMap(pendingActions)
        let changed = false
        for (const domain in pendingActions) {
            const action = pendingActions[domain]
            if (action.commandComplete && actionMatchesTelemetry(domain, action.expected)) {
                delete next[domain]
                changed = true
            }
        }
        if (changed) pendingActions = next
    }

    function expirePendingActions(now) {
        let expired = []
        for (const domain in pendingActions) {
            if (now >= pendingActions[domain].deadlineMs)
                expired.push(domain)
        }
        for (let i = 0; i < expired.length; ++i) {
            const action = pendingActions[expired[i]]
            const message = action.commandComplete
                ? "后端未在时限内确认，已回退到遥测值"
                : "命令执行超时，已回退到遥测值"
            removePendingAction(expired[i], message)
        }
    }

    function formatRate(bytesPerSecond) {
        if (bytesPerSecond < 0)
            return "—"
        if (bytesPerSecond >= 1048576)
            return (bytesPerSecond / 1048576).toFixed(1) + " MB/s"
        if (bytesPerSecond >= 1024)
            return Math.round(bytesPerSecond / 1024) + " KB/s"
        return Math.round(bytesPerSecond) + " B/s"
    }

    function formatCompactRate(bytesPerSecond) {
        return formatRate(bytesPerSecond).replace(" ", "")
    }

    function formatReset(epochSeconds) {
        if (!epochSeconds)
            return "—"
        const date = new Date(epochSeconds * 1000)
        return Qt.formatDateTime(date, "MM-dd hh:mm")
    }

    function quotaWindowText(minutes) {
        if (minutes <= 0)
            return "额度"
        if (minutes === 10080)
            return "周"
        if (minutes % 1440 === 0)
            return (minutes / 1440) + "天"
        if (minutes % 60 === 0)
            return (minutes / 60) + "h"
        return minutes + "分钟"
    }

    function quotaDisplayRemaining() {
        if (!quotaHealthy) return -1
        return quotaSecondaryRemaining >= 0
            ? quotaSecondaryRemaining : quotaPrimaryRemaining
    }

    function quotaDetailText() {
        if (!telemetryHealthy)
            return statusReady ? "额度遥测中断" : "等待额度遥测"
        if (!quotaTelemetryValid)
            return "额度数据格式异常"
        if (!quotaAvailable)
            return "等待本机 Codex 登录"
        if (!quotaFresh)
            return "额度数据已过期"
        let fields = []
        if (quotaPrimaryRemaining >= 0)
            fields.push(quotaWindowText(quotaPrimaryWindowMinutes) + " " + quotaPrimaryRemaining + "%")
        if (quotaSecondaryRemaining >= 0)
            fields.push(quotaWindowText(quotaSecondaryWindowMinutes) + " " + quotaSecondaryRemaining + "%")
        return fields.length > 0 ? fields.join(" · ") : "等待实时额度"
    }

    function quotaTooltipText() {
        if (!telemetryHealthy)
            return statusReady ? "额度遥测已中断；旧值未继续显示" : "等待第一份额度遥测"
        if (!quotaTelemetryValid)
            return "额度字段无效；未把异常值当成可用额度"
        if (!quotaAvailable)
            return "Codex CLI 已登录后会自动读取额度"
        if (!quotaFresh)
            return quotaName + "\n本地缓存已经过期，未冒充实时额度"
        let lines = [quotaName]
        if (quotaPrimaryRemaining >= 0)
            lines.push(quotaWindowText(quotaPrimaryWindowMinutes) + "额度重置 " + formatReset(quotaPrimaryReset))
        if (quotaSecondaryRemaining >= 0)
            lines.push(quotaWindowText(quotaSecondaryWindowMinutes) + "额度重置 " + formatReset(quotaSecondaryReset))
        return lines.join("\n")
    }

    function telemetryStateText() {
        if (!statusReady) return "等待遥测"
        return telemetryHealthy ? "实时" : "遥测中断"
    }

    function telemetryStateColor() {
        if (!statusReady) return mutedColor
        return telemetryHealthy ? successColor : dangerColor
    }

    function displayFpsText() {
        if (!topFpsHealthy) return "— FPS"
        return Math.round(topFps) + " FPS"
    }

    function displayFpsDetailText() {
        if (!telemetryHealthy) return statusReady ? "帧率遥测中断" : "等待帧率遥测"
        if (topFpsStatus === "ok") {
            if (topFpsSource === "moonlight-rendered")
                return "Moonlight 串流 · 实时"
            if (topFpsSource === "gamescope-presented")
                return "当前游戏 · Gamescope"
            return "当前游戏 · 实时"
        }
        if (topFpsStatus === "starting") return "正在获取帧率"
        if (topFpsStatus === "stale") return "帧率已中断"
        if (topFpsStatus === "invalid") return "帧率数据无效"
        return "等待上屏游戏"
    }

    function displayBackendText() {
        if (!telemetryHealthy)
            return statusReady ? "显示遥测中断" : "等待显示遥测"
        if (!displayTelemetryValid)
            return "显示字段无效"
        if (displayStatus !== "ok")
            return displayStatus === "partial" ? "显示数据不完整" : "显示数据未知"
        let fields = []
        if (displaySession.length > 0) fields.push(displaySession)
        if (displayBackend.length > 0) fields.push(displayBackend)
        if (displayCompositor.length > 0) fields.push(displayCompositor)
        return fields.length > 0 ? fields.join(" · ") : "显示后端未知"
    }

    function displayTelemetryTooltip() {
        let lines = []
        if (topFpsHealthy) {
            if (topFpsSource === "moonlight-rendered")
                lines.push("Moonlight 客户端呈现 " + topFps.toFixed(1) + " FPS")
            else
                lines.push("当前游戏 " + topFps.toFixed(1) + " FPS")
        } else {
            lines.push(displayFpsDetailText())
        }
        if (topFpsSource === "moonlight-rendered") {
            lines.push("FPS 来自 Moonlight 客户端实际呈现帧率")
            lines.push("不等同于远端游戏引擎 FPS")
        } else if (topFpsSource === "gamescope-presented") {
            lines.push("FPS 来自 Gamescope 当前游戏的提交间隔")
            lines.push("不依赖游戏、模拟器或图形 API")
        } else {
            lines.push("FPS 来自受管 OpenGL/Vulkan 游戏的帧交换采样")
        }
        lines.push("物理刷新率不冒充游戏 FPS")
        if (!telemetryHealthy)
            return lines.concat([statusReady ? "显示遥测已中断" : "等待第一份显示遥测"]).join("\n")
        if (!displayTelemetryValid)
            return lines.concat(["显示字段格式或范围无效"]).join("\n")
        if (displayStatus !== "ok")
            return lines.concat(["显示状态不是 ok"]).join("\n")
        lines.push("物理输出 DSI-1 " + Math.round(topRefreshHz) + " Hz · DSI-2 " +
                   Math.round(bottomRefreshHz) + " Hz")
        lines.push(displayBackendText())
        if (topLogicalWidth > 0 && topLogicalHeight > 0)
            lines.push("DSI-1 逻辑 " + topLogicalWidth + "×" + topLogicalHeight +
                       (topScale > 0 ? " · " + topScale + "×" : ""))
        if (bottomLogicalWidth > 0 && bottomLogicalHeight > 0)
            lines.push("DSI-2 逻辑 " + bottomLogicalWidth + "×" + bottomLogicalHeight +
                       (bottomScale > 0 ? " · " + bottomScale + "×" : ""))
        if (topPhysicalPixelWidth > 0 && topPhysicalPixelHeight > 0)
            lines.push("DSI-1 物理像素 " + topPhysicalPixelWidth + "×" + topPhysicalPixelHeight)
        if (bottomPhysicalPixelWidth > 0 && bottomPhysicalPixelHeight > 0)
            lines.push("DSI-2 物理像素 " + bottomPhysicalPixelWidth + "×" + bottomPhysicalPixelHeight)
        if (topPhysicalWidthMm > 0 && topPhysicalHeightMm > 0)
            lines.push("DSI-1 尺寸 " + topPhysicalWidthMm + "×" + topPhysicalHeightMm + " mm")
        if (bottomPhysicalWidthMm > 0 && bottomPhysicalHeightMm > 0)
            lines.push("DSI-2 尺寸 " + bottomPhysicalWidthMm + "×" + bottomPhysicalHeightMm + " mm")
        if (displayRenderer.length > 0) lines.push("渲染器 " + displayRenderer)
        return lines.join("\n")
    }

    function batteryStateText() {
        if (!telemetryHealthy)
            return statusReady ? "电池遥测中断" : "等待电池遥测"
        if (!batteryTelemetryValid)
            return "电池字段无效"
        if (batteryState === "charging") return "充电中"
        if (batteryState === "discharging") return "使用电池"
        if (batteryState === "full") return "已充满"
        if (batteryState === "not-charging") return batteryExternalPower === 1 ? "外接电源" : "未充电"
        return batteryState === "unavailable" ? "电池未知" : "状态未知"
    }

    function batteryTooltip() {
        if (!telemetryHealthy)
            return statusReady ? "电池遥测已中断；控制保持锁定" : "等待第一份电池遥测"
        if (!batteryTelemetryValid)
            return "电池字段格式或范围无效；旧值未继续显示"
        let lines = [batteryStateText()]
        if (batteryPower >= 0)
            lines.push((batteryState === "charging" ? "充电" : batteryState === "discharging" ? "放电" : "瞬时") +
                       "功率 " + batteryPower.toFixed(2) + " W（电压×电流）")
        if (batteryTemperature >= 0)
            lines.push("温度 " + batteryTemperature.toFixed(1) + "°C")
        if (batteryVoltage >= 0)
            lines.push("电压 " + batteryVoltage.toFixed(3) + " V")
        if (batteryHealth.length > 0)
            lines.push("健康 " + (batteryHealth === "Good" ? "良好" : batteryHealth))
        if (batteryCycles >= 0)
            lines.push("循环 " + batteryCycles + " 次")
        lines.push("预计时间：驱动数据不可用")
        return lines.join("\n")
    }

    function systemPowerSourceText() {
        if (!telemetryHealthy)
            return statusReady ? "功耗遥测中断" : "等待功耗遥测"
        if (!systemPowerTelemetryValid)
            return "功耗字段无效"
        if (systemPowerSource === "usb-input") return "USB 输入"
        if (systemPowerSource === "battery-discharge") return "电池放电"
        return "暂无可信样本"
    }

    function systemPowerTooltip() {
        if (!telemetryHealthy)
            return statusReady ? "整机功耗遥测已中断" : "等待第一份整机功耗遥测"
        if (!systemPowerTelemetryValid)
            return "整机功耗字段格式、来源或范围无效；旧值未继续显示"
        if (systemPowerSource === "usb-input")
            return "USB 端输入功率（电压×电流）\n包含整机运行与电池充电，不等同于仅 SoC 功耗\n条形刻度 0–30 W"
        if (systemPowerSource === "battery-discharge")
            return "电池端放电估算（电压×电流）\n仅在驱动明确报告放电时启用\n条形刻度 0–30 W"
        return "USB 输入与电池放电均无可信样本"
    }

    function inputModeText() {
        const mode = presentedInputMode()
        if (mode === "joymouse") return "桌面鼠标模式"
        if (mode === "gamepad") return "游戏手柄模式"
        if (mode === "other") return "其他输入配置"
        return "输入模式未知"
    }

    function inputModeShortText() {
        const mode = presentedInputMode()
        if (mode === "joymouse") return "鼠标模式"
        if (mode === "gamepad") return "手柄模式"
        return "输入模式"
    }

    function powerModeText() {
        const profile = presentedPowerProfile()
        if (profile === "powersave") return "省电"
        if (profile === "balanced") return "均衡"
        if (profile === "performance") return "性能"
        return "未知"
    }

    function powerModeDescription() {
        if (!powerHealthy) return "暂时读不到系统模式"
        const profile = presentedPowerProfile()
        if (profile === "powersave") return "降低功耗和发热，适合轻量使用"
        if (profile === "balanced") return "日常推荐，自动兼顾响应、温度与续航"
        if (profile === "performance") return "优先游戏响应，会带来更高功耗和风扇声"
        return "当前不是 Pocket DS 预设模式"
    }

    function fanModeDescription() {
        if (!fanHealthy) return "暂时读不到散热状态"
        const profile = presentedFanProfile()
        if (profile === "quiet") return "降低噪声；温度升高时安全温控仍会自动提速"
        if (profile === "moderate" || profile === "auto")
            return "日常推荐，自动平衡机身温度和风扇声"
        if (profile === "aggressive") return "优先压低温度，风扇会更积极"
        if (profile === "custom") return "当前是手动转速；选择任一模式即可恢复自动调节"
        return "当前散热策略未知"
    }

    function recoveryTooltip() {
        if (recoveryRequestStatus === "stale")
            return "发现超过 90 秒的孤儿恢复请求；需要确认后清理"
        if (recoveryRequestStatus === "pending") return "桌面恢复请求正在交接"
        if (recoveryRequestStatus === "invalid") return "恢复请求状态异常；请用 SSH 检查"
        if (recoveryStatus === "ok") return "最近一次桌面恢复成功"
        if (recoveryStatus === "failed")
            return "最近一次桌面恢复失败" + (recoveryError.length > 0 ? "：" + recoveryError : "")
        if (recoveryStatus === "invalid") return "恢复结果格式异常"
        return "有界恢复 KDE 桌面"
    }

    function exec(args) {
        executable.connectSource(panelctlPath + " " + args)
    }

    function beginAndroidBoot() {
        if (bootSwitchPending) return
        bootSwitchPending = true
        bootSwitchError = ""
        executable.connectSource(bootSwitchCommand)
    }

    function finishAndroidBoot(stdout, exitCode) {
        // BootMode emits its verified result before the separate reboot request.
        // A failed command cannot undo that durable write (or prove rollback).
        let committed = bootSwitchCommitted
        try {
            const result = JSON.parse(stdout)
            committed = committed || (result.status === "ok" && result.to === "android")
        } catch (error) {}
        const accepted = isFinite(exitCode) && exitCode === 0
        bootSwitchCommitted = committed || accepted
        bootSwitchPending = accepted
        bootSwitchError = accepted ? "" : committed
            ? "启动目标已设为 Android，自动重启失败。可重试重启，或稍后手动重启。"
            : "未能确认启动目标；请通过 SSH 检查后再重启。"
    }

    function requestStatus(force) {
        const now = Date.now()
        const pendingCount = pendingActionCount()
        const priority = force || hasPendingConfirmation()
        if ((!panelExpanded && pendingCount === 0) || statusRequestInFlight ||
            (now < nextStatusAttemptMs && !priority))
            return false
        statusRequestInFlight = true
        statusRequestStartedMs = now
        executable.connectSource(statusCommand)
        return true
    }

    function recordStatusFailure(message) {
        statusRequestInFlight = false
        statusRequestStartedMs = 0
        statusSchemaValid = false
        statusFailureCount = Math.min(statusFailureCount + 1, 8)
        const retryDelay = Math.min(statusRetryCapMs,
                                    statusIntervalMs * Math.pow(2, statusFailureCount - 1))
        nextStatusAttemptMs = Date.now() + retryDelay
        lastStatusError = message
    }

    function recordStatusSuccess() {
        statusRequestInFlight = false
        statusRequestStartedMs = 0
        statusFailureCount = 0
        nextStatusAttemptMs = Date.now() + statusIntervalMs
        lastStatusError = ""
    }

    function resetDisplayReadings() {
        displaySession = ""
        displayBackend = ""
        displayCompositor = ""
        displayRenderer = ""
        topRefreshHz = -1
        bottomRefreshHz = -1
        topScale = -1
        bottomScale = -1
        topLogicalWidth = -1
        topLogicalHeight = -1
        bottomLogicalWidth = -1
        bottomLogicalHeight = -1
        topPhysicalPixelWidth = -1
        topPhysicalPixelHeight = -1
        bottomPhysicalPixelWidth = -1
        bottomPhysicalPixelHeight = -1
        topPhysicalWidthMm = -1
        topPhysicalHeightMm = -1
        bottomPhysicalWidthMm = -1
        bottomPhysicalHeightMm = -1
    }

    function resetBatteryReadings() {
        batteryPercent = -1
        batteryState = "unavailable"
        batteryExternalPower = -1
        batteryTemperature = -1
        batteryVoltage = -1
        batteryCurrent = 0
        batteryPower = -1
        batteryPowerSource = "unavailable"
        batteryHealth = ""
        batteryCycles = -1
    }

    function resetSystemPowerReadings() {
        systemPower = -1
        systemPowerSource = "unavailable"
    }

    function applyStatus(s) {
        if (!validStatusEnvelope(s)) return false
        const now = Date.now()
        const total = s.cpu_total
        const idle = s.cpu_idle
        if (previousCpuTotal > 0 && total > previousCpuTotal) {
            const totalDelta = total - previousCpuTotal
            const idleDelta = idle - previousCpuIdle
            cpuPercent = idleDelta >= 0 && idleDelta <= totalDelta
                ? clamp(Math.round(100 * (totalDelta - idleDelta) / totalDelta), 0, 100)
                : -1
        } else {
            cpuPercent = -1
        }
        previousCpuTotal = total
        previousCpuIdle = idle

        const rx = s.rx_bytes
        const tx = s.tx_bytes
        if (previousSampleMs > 0 && now > previousSampleMs) {
            const seconds = (now - previousSampleMs) / 1000
            downloadRate = rx >= previousRx ? (rx - previousRx) / seconds : -1
            uploadRate = tx >= previousTx ? (tx - previousTx) / seconds : -1
        } else {
            downloadRate = -1
            uploadRate = -1
        }
        previousRx = rx
        previousTx = tx
        previousSampleMs = now

        cpuGHz = s.cpu_ghz
        cpuCores = s.cpu_cores
        displayPowered = typeof s.display_powered === "boolean"
            ? s.display_powered : true
        gpuStatus = String(s.gpu_status || "unavailable")
        gpuPercent = gpuStatus === "ok" && isNumberBetween(s.gpu_percent, 0, 100)
            ? clamp(Number(s.gpu_percent), 0, 100) : -1
        gpuFrequencyMHz = gpuStatus === "ok" && isNumberBetween(s.gpu_freq_hz, 0, 1e12)
            ? Number(s.gpu_freq_hz) / 1000000 : -1
        gpuTemperature = gpuStatus === "ok" && isNumberBetween(s.gpu_temp_c, -50, 200)
            ? Number(s.gpu_temp_c) : -1
        gpuClients = gpuStatus === "ok" && isIntegerBetween(s.gpu_clients, 0, 1000000)
            ? Number(s.gpu_clients) : -1

        displayTelemetryValid = validDisplayFields(s)
        displayStatus = isOneOf(s.display_status,
                                ["ok", "partial", "stale", "unavailable"])
            ? s.display_status : "unavailable"
        topFpsStatus = s.display_app_fps_status
        topFpsSource = s.display_app_fps_source
        topFps = topFpsStatus === "ok" && isNumberBetween(s.display_app_fps, 0, 1000)
            ? Number(s.display_app_fps) : -1
        if (displayTelemetryValid && displayStatus === "ok") {
            displaySession = s.display_session === null ? "" : s.display_session
            displayBackend = s.display_backend === null ? "" : s.display_backend
            displayCompositor = s.display_compositor === null ? "" : s.display_compositor
            displayRenderer = s.display_renderer === null ? "" : s.display_renderer
            topRefreshHz = s.display_dsi1_refresh_hz
            bottomRefreshHz = s.display_dsi2_refresh_hz
            topScale = s.display_dsi1_scale === null ? -1 : s.display_dsi1_scale
            bottomScale = s.display_dsi2_scale === null ? -1 : s.display_dsi2_scale
            topLogicalWidth = s.display_dsi1_logical_width === null ? -1 : s.display_dsi1_logical_width
            topLogicalHeight = s.display_dsi1_logical_height === null ? -1 : s.display_dsi1_logical_height
            bottomLogicalWidth = s.display_dsi2_logical_width === null ? -1 : s.display_dsi2_logical_width
            bottomLogicalHeight = s.display_dsi2_logical_height === null ? -1 : s.display_dsi2_logical_height
            topPhysicalPixelWidth = s.display_dsi1_physical_width_px === null ? -1 : s.display_dsi1_physical_width_px
            topPhysicalPixelHeight = s.display_dsi1_physical_height_px === null ? -1 : s.display_dsi1_physical_height_px
            bottomPhysicalPixelWidth = s.display_dsi2_physical_width_px === null ? -1 : s.display_dsi2_physical_width_px
            bottomPhysicalPixelHeight = s.display_dsi2_physical_height_px === null ? -1 : s.display_dsi2_physical_height_px
            topPhysicalWidthMm = s.display_dsi1_physical_width_mm === null ? -1 : s.display_dsi1_physical_width_mm
            topPhysicalHeightMm = s.display_dsi1_physical_height_mm === null ? -1 : s.display_dsi1_physical_height_mm
            bottomPhysicalWidthMm = s.display_dsi2_physical_width_mm === null ? -1 : s.display_dsi2_physical_width_mm
            bottomPhysicalHeightMm = s.display_dsi2_physical_height_mm === null ? -1 : s.display_dsi2_physical_height_mm
        } else {
            resetDisplayReadings()
        }

        fanTelemetryValid = validFanFields(s)
        fanStatus = fanTelemetryValid ? s.fan_status : "invalid"
        fanSampleAgeMs = fanTelemetryValid && s.fan_sample_age_ms !== null
            ? s.fan_sample_age_ms : -1
        fanPercent = fanTelemetryValid && fanStatus === "ok" ? s.fan_percent : -1
        fanProfile = fanTelemetryValid && fanStatus === "ok" ? s.fan_profile : "unavailable"
        temperature = fanTelemetryValid && fanStatus === "ok" ? s.temp_c : -1
        topBrightnessValid = validBrightnessField(s.top_brightness)
        topBrightness = topBrightnessValid ? s.top_brightness : -1
        bottomBrightnessValid = validBrightnessField(s.bottom_brightness)
        bottomBrightness = bottomBrightnessValid ? s.bottom_brightness : -1
        brightnessWriteStatus = validBrightnessWriteStatus(s.brightness_write_status)
            ? s.brightness_write_status : "invalid"
        volumeTelemetryValid = validVolumeFields(s)
        volumePercent = volumeTelemetryValid ? s.volume : -1
        muted = volumeTelemetryValid ? s.muted : false
        powerTelemetryValid = validPowerFields(s)
        powerProfile = powerTelemetryValid ? s.power_profile : "unavailable"
        lidAutoPoweroffTelemetryValid = validLidAutoPoweroffFields(s)
        lidAutoPoweroffMinutes = lidAutoPoweroffTelemetryValid
            ? s.lid_auto_poweroff_minutes : 0
        lidAutoPoweroffStatus = lidAutoPoweroffTelemetryValid
            ? s.lid_auto_poweroff_status : "unavailable"
        gameFpsLimitTelemetryValid = validGameFpsLimitFields(s)
        gameFpsLimit = gameFpsLimitTelemetryValid ? s.game_fps_limit : 0
        gameFpsLimitStatus = gameFpsLimitTelemetryValid
            ? s.game_fps_limit_status : "unavailable"
        gameFpsRuntimeActive = gameFpsLimitTelemetryValid
            ? s.game_fps_runtime_active : false
        batteryTelemetryValid = validBatteryFields(s)
        if (batteryTelemetryValid) {
            batteryPercent = s.battery_percent === null ? -1 : s.battery_percent
            batteryState = s.battery_state
            batteryExternalPower = s.battery_external_power === null
                ? -1 : (s.battery_external_power ? 1 : 0)
            batteryTemperature = s.battery_temp_c === null ? -1 : s.battery_temp_c
            batteryVoltage = s.battery_voltage_v === null ? -1 : s.battery_voltage_v
            batteryCurrent = s.battery_current_a === null ? 0 : s.battery_current_a
            batteryPower = s.battery_power_w === null ? -1 : s.battery_power_w
            batteryPowerSource = s.battery_power_source
            batteryHealth = s.battery_health === null ? "" : s.battery_health
            batteryCycles = s.battery_cycles === null ? -1 : s.battery_cycles
        } else {
            resetBatteryReadings()
        }
        systemPowerTelemetryValid = validSystemPowerFields(s)
        if (systemPowerTelemetryValid) {
            systemPower = s.system_power_w === null ? -1 : s.system_power_w
            systemPowerSource = s.system_power_source
        } else {
            resetSystemPowerReadings()
        }
        inputMode = String(s.input_mode || "unavailable")
        recoveryStatus = String(s.recovery_status || "none")
        recoveryRequestStatus = String(s.recovery_request_status || "none")
        recoveryError = s.recovery_error == null ? "" : String(s.recovery_error)
        recoveryAgeMs = s.recovery_age_ms == null ? -1 : Number(s.recovery_age_ms)
        if (recoveryRequestStatus !== "pending")
            recoveryPending = false

        const quota = s.quota === undefined ? undefined : s.quota
        quotaTelemetryValid = validQuotaFields(quota)
        const quotaAgeS = quotaTelemetryValid && quota !== null
            ? Math.floor(Date.now() / 1000) - quota.synced_at : -1
        const quotaAgeValid = quotaAgeS >= 0 && quotaAgeS <= quotaMaximumAgeS
        quotaAvailable = quotaTelemetryValid && quota !== null ? quota.available : false
        quotaFresh = quotaTelemetryValid && quota !== null && quota.fresh && quotaAgeValid
        quotaPrimaryRemaining = quotaAvailable && quota.primary_used_percent !== null
            ? Math.round(100 - quota.primary_used_percent) : -1
        quotaSecondaryRemaining = quotaAvailable && quota.secondary_used_percent !== null
            ? Math.round(100 - quota.secondary_used_percent) : -1
        quotaPrimaryReset = quotaAvailable && quota.primary_reset !== null
            ? quota.primary_reset : 0
        quotaSecondaryReset = quotaAvailable && quota.secondary_reset !== null
            ? quota.secondary_reset : 0
        quotaPrimaryWindowMinutes = quotaAvailable && quota.primary_window_minutes !== null
            ? quota.primary_window_minutes : -1
        quotaSecondaryWindowMinutes = quotaAvailable && quota.secondary_window_minutes !== null
            ? quota.secondary_window_minutes : -1
        quotaName = quotaTelemetryValid && quota !== null && quota.limit_name !== null &&
            quota.limit_name.length > 0 ? quota.limit_name : "Codex"
        lastStatusMs = now
        telemetryAgeMs = 0
        statusReady = true
        statusSchemaValid = true
        confirmPendingActions()
        return true
    }

    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: function(sourceName, data) {
            disconnectSource(sourceName)
            const stdout = String(data["stdout"] || "").trim()
            const exitCode = Number(data["exit code"])
            if (sourceName === root.lidModeCommand) {
                const requestedMs = root.lidModeRequestMs
                root.lidModeRequestMs = 0
                let valid = false
                if (exitCode === 0 && stdout.length < 2048) {
                    try { valid = root.applyLidModeStatus(JSON.parse(stdout), requestedMs) }
                    catch (error) { valid = false }
                }
                if (!valid) root.lidModeValid = false
                if (root.actionPending("lid-mode") &&
                    root.pendingActions["lid-mode"].commandComplete &&
                    requestedMs <= root.pendingActions["lid-mode"].commandCompletedMs)
                    root.requestLidModeStatus()
                return
            }
            if (sourceName === root.statusCommand) {
                root.statusRequestInFlight = false
                root.statusRequestStartedMs = 0
                if (!isFinite(exitCode) || exitCode !== 0) {
                    root.recordStatusFailure("status 命令失败")
                    return
                }
                if (stdout.length === 0) {
                    root.recordStatusFailure("status 返回空数据")
                    return
                }
                try {
                    if (root.applyStatus(JSON.parse(stdout))) root.recordStatusSuccess()
                    else root.recordStatusFailure("status schema 无效")
                } catch (error) {
                    console.warn("Pocket DS panel status parse failed:", error)
                    root.recordStatusFailure("status JSON 无效")
                }
                return
            }

            if (sourceName === root.bootSwitchCommand) {
                root.finishAndroidBoot(stdout, exitCode)
                return
            }

            let actionDomain = ""
            for (const domain in root.pendingActions) {
                if (root.pendingActions[domain].command === sourceName) {
                    actionDomain = domain
                    break
                }
            }
            if (actionDomain.length > 0) {
                if (!isFinite(exitCode) || exitCode !== 0) {
                    root.removePendingAction(actionDomain, actionDomain === "lid-mode"
                        ? "未能修改合盖设置，请保持开盖后重试。"
                        : "命令失败，已回退到遥测值")
                    if (actionDomain === "lid-mode") root.requestLidModeStatus()
                    return
                }
                const next = root.copyMap(root.pendingActions)
                const current = next[actionDomain]
                const completedAt = Date.now()
                current.commandComplete = true
                current.commandCompletedMs = completedAt
                current.deadlineMs = completedAt + root.actionConfirmationTimeoutMs
                next[actionDomain] = current
                root.pendingActions = next
                if (actionDomain === "lid-mode") root.requestLidModeStatus()
            }
            if (isFinite(exitCode) && exitCode === 0) {
                statusConfirmationTimer.restart()
            }
        }
    }

    Timer {
        id: refreshTimer
        interval: 1000
        repeat: true
        running: root.panelExpanded || root.statusRequestInFlight ||
            root.pendingActionCount() > 0
        triggeredOnStart: true
        onTriggered: {
            const now = Date.now()
            root.lidModeAgeMs = root.lidModeSampleMs > 0
                ? now - root.lidModeSampleMs : -1
            if (root.lidModeRequestMs > 0 && now - root.lidModeRequestMs >= 10000) {
                executable.disconnectSource(root.lidModeCommand)
                root.lidModeRequestMs = 0
                root.lidModeValid = false
            }
            if (root.panelExpanded && now - root.lidModeAttemptMs >= 5000)
                root.requestLidModeStatus()
            root.currentTime = Qt.formatTime(new Date(), "hh:mm")
            root.telemetryAgeMs = root.lastStatusMs > 0
                ? now - root.lastStatusMs : -1
            root.expirePendingActions(now)
            if (root.statusRequestInFlight &&
                now - root.statusRequestStartedMs >= root.statusTimeoutMs) {
                executable.disconnectSource(root.statusCommand)
                root.recordStatusFailure("status 请求超时")
            }
            root.requestStatus(root.hasPendingConfirmation())
        }
    }

    Timer {
        id: statusConfirmationTimer
        interval: 80
        repeat: false
        onTriggered: root.requestStatus(true)
    }

    component Meter: Rectangle {
        required property real amount
        property color fillColor: root.accentColor
        property bool valid: true
        implicitHeight: 4
        radius: 2
        color: valid ? root.dividerColor : "transparent"
        Rectangle {
            visible: parent.valid
            width: parent.width * root.clamp(parent.amount, 0, 100) / 100
            height: parent.height
            radius: parent.radius
            color: parent.fillColor
        }
    }

    component TouchSlider: QQC2.Slider {
        id: sliderControl
        required property real telemetryValue
        required property bool telemetryValid
        required property bool controlHealthy
        required property bool actionPending
        property bool userInteraction: false
        readonly property bool valueVisible: telemetryValid || userInteraction || actionPending
        readonly property real presentedValue: userInteraction || actionPending
            ? value : telemetryValue
        signal committed(real newValue)
        Layout.minimumHeight: 48
        Layout.preferredHeight: 48
        implicitHeight: 48
        enabled: controlHealthy && !actionPending
        live: true
        Binding {
            target: sliderControl
            property: "value"
            restoreMode: Binding.RestoreNone
            value: sliderControl.telemetryValid
                ? root.clamp(sliderControl.telemetryValue,
                             sliderControl.from, sliderControl.to)
                : sliderControl.from
            when: !sliderControl.userInteraction && !sliderControl.actionPending
        }
        onPressedChanged: {
            if (pressed) {
                userInteraction = true
            } else if (userInteraction) {
                const candidate = value
                if (controlHealthy && telemetryValid && !actionPending &&
                    root.telemetryHealthy)
                    committed(candidate)
                userInteraction = false
            }
        }

        background: Rectangle {
            x: sliderControl.leftPadding
            y: sliderControl.topPadding + sliderControl.availableHeight / 2 - height / 2
            width: sliderControl.availableWidth
            height: 8
            radius: 4
            color: root.dividerColor
            Rectangle {
                visible: sliderControl.valueVisible
                width: sliderControl.visualPosition * parent.width
                height: parent.height
                radius: parent.radius
                color: root.accentColor
            }
        }

        handle: Rectangle {
            visible: sliderControl.valueVisible
            x: sliderControl.leftPadding + sliderControl.visualPosition * (sliderControl.availableWidth - width)
            y: sliderControl.topPadding + sliderControl.availableHeight / 2 - height / 2
            implicitWidth: 30
            implicitHeight: 30
            radius: 15
            color: sliderControl.pressed ? root.textColor : root.accentColor
            border.width: 2
            border.color: root.pageColor
        }
    }

    component PanelIcon: Item {
        id: panelIcon

        required property string name
        property color iconColor: root.textColor

        implicitWidth: 18
        implicitHeight: 18

        Shape {
            anchors.centerIn: parent
            width: 256
            height: 256
            scale: Math.min(panelIcon.width, panelIcon.height) / 256
            transformOrigin: Item.Center
            antialiasing: true

            ShapePath {
                fillColor: panelIcon.iconColor
                strokeColor: "transparent"

                PathSvg {
                    path: root.phosphorIconPaths[panelIcon.name] ||
                          root.phosphorIconPaths["warning-circle"]
                }
            }
        }
    }

    component PanelText: Text {
        font.family: root.uiFontFamily
        font.hintingPreference: Font.PreferFullHinting
        renderType: Text.QtRendering
        verticalAlignment: Text.AlignVCenter
        Layout.alignment: Qt.AlignVCenter
    }

    component UtilityReadout: Item {
        required property string iconName
        required property string labelText
        required property string valueText
        property bool failed: false
        property bool numericValue: false
        property bool showChevron: false
        ColumnLayout {
            anchors.centerIn: parent
            width: parent.width
            spacing: 2
            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                spacing: 5
                PanelIcon {
                    name: iconName
                    implicitWidth: 14
                    implicitHeight: 14
                    iconColor: root.accentColor
                }
                PanelText {
                    text: labelText
                    color: root.mutedColor
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
            }
            PanelText {
                Layout.fillWidth: true
                text: valueText
                horizontalAlignment: Text.AlignHCenter
                color: failed ? root.dangerColor : root.textColor
                font.family: numericValue ? root.numericFontFamily : root.uiFontFamily
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
        }
        PanelIcon {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            visible: showChevron
            name: "caret-right"
            width: 14
            height: 14
            iconColor: root.mutedColor
        }
    }

    component ControlLabel: RowLayout {
        required property string iconName
        required property string labelText

        Layout.minimumWidth: 78
        Layout.preferredWidth: 78
        spacing: 6

        PanelIcon {
            name: iconName
            implicitWidth: 15
            implicitHeight: 15
            iconColor: root.mutedColor
        }
        PanelText {
            text: labelText
            color: root.mutedColor
            font.pixelSize: 13
            font.weight: Font.DemiBold
        }
    }

    component MetricItem: Rectangle {
        required property string title
        required property string iconName
        required property string valueText
        required property string detailText
        required property real meterValue
        property color meterColor: root.accentColor
        property color valueColor: root.textColor
        property int valueSize: 26
        property bool showDivider: false
        property bool meterValid: true
        Layout.fillWidth: true
        Layout.fillHeight: true
        radius: 20
        color: root.surfaceColor
        border.width: 1
        border.color: root.cardBorder

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            anchors.topMargin: 11
            anchors.bottomMargin: 11
            spacing: 3
            RowLayout {
                spacing: 5
                PanelIcon {
                    name: iconName
                    implicitWidth: 16
                    implicitHeight: 16
                    iconColor: root.mutedColor
                }
                PanelText { text: title; color: root.mutedColor; font.pixelSize: 15; font.weight: Font.Medium }
            }
            PanelText {
                text: valueText
                color: valueColor
                font.family: root.numericFontFamily
                font.pixelSize: valueSize
                font.weight: Font.DemiBold
            }
            PanelText {
                Layout.fillWidth: true
                text: detailText
                color: root.mutedColor
                font.pixelSize: 14
                elide: Text.ElideRight
            }
            Item { Layout.fillHeight: true }
            Meter {
                Layout.fillWidth: true
                amount: meterValue
                fillColor: meterColor
                valid: meterValid
            }
        }

        Rectangle {
            visible: parent.showDivider
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            anchors.topMargin: 10
            anchors.bottomMargin: 10
            width: 1
            color: root.dividerColor
        }
    }

    component SectionCard: Rectangle {
        radius: 14
        color: root.surfaceColor
        border.color: root.cardBorder
        border.width: 1
    }

    component ProfileButton: QQC2.Button {
        id: profileControl
        required property string profileValue
        required property bool selected
        required property bool controlHealthy
        required property bool pending
        property color selectedColor: root.accentColor
        property color selectedFill: root.accentMutedColor
        property int textSize: 16
        Layout.fillWidth: true
        Layout.minimumHeight: 48
        Layout.preferredHeight: 48
        checkable: false
        enabled: controlHealthy && !pending
        opacity: controlHealthy ? 1 : 0.45
        scale: pressed ? 0.98 : 1
        Behavior on scale {
            NumberAnimation { duration: 100; easing.type: Easing.OutCubic }
        }
        background: Rectangle {
            radius: 10
            color: profileControl.pressed ? root.surfacePressedColor
                : profileControl.selected ? profileControl.selectedFill : root.surfaceRaisedColor
            border.width: profileControl.activeFocus || profileControl.selected ? 2 : 1
            border.color: profileControl.activeFocus ? root.textColor
                : profileControl.selected ? profileControl.selectedColor : root.cardBorder
        }
        contentItem: PanelText {
            text: profileControl.text
            color: profileControl.selected || profileControl.pressed
                ? root.textColor : profileControl.pending ? root.mutedColor : root.controlTextColor
            font.pixelSize: profileControl.textSize
            font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    component HeaderAction: QQC2.Button {
        id: headerControl
        required property string iconName
        property bool emphasized: false
        property bool failed: false
        Accessible.description: failed ? root.actionError("input") : ""
        Layout.fillWidth: true
        Layout.minimumWidth: 60
        Layout.preferredWidth: 160
        Layout.minimumHeight: 54
        Layout.preferredHeight: 54
        background: Rectangle {
            radius: 11
            color: headerControl.emphasized ? root.accentMutedColor
                : headerControl.pressed ? root.surfacePressedColor : root.surfaceRaisedColor
            border.width: headerControl.activeFocus || headerControl.emphasized ? 2 : 1
            border.color: headerControl.failed ? root.dangerColor
                : headerControl.activeFocus ? root.textColor
                : headerControl.emphasized ? root.accentColor : root.cardBorder
        }
        contentItem: RowLayout {
            spacing: 9
            Item { Layout.fillWidth: true }
            PanelIcon {
                name: headerControl.failed ? "warning-circle" : headerControl.iconName
                implicitWidth: 20
                implicitHeight: 20
                iconColor: headerControl.failed ? root.dangerColor
                    : !headerControl.enabled ? root.cardBorder
                    : headerControl.emphasized ? root.accentColor : root.textColor
            }
            PanelText {
                text: headerControl.text
                color: headerControl.enabled ? root.textColor : root.mutedColor
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
        }
    }

    component PrimaryButton: QQC2.Button {
        id: primaryControl
        required property string iconName
        Layout.fillWidth: true
        Layout.minimumHeight: 64
        Layout.preferredHeight: 64
        scale: pressed ? 0.98 : 1
        Behavior on scale {
            NumberAnimation { duration: 110; easing.type: Easing.OutCubic }
        }
        background: Rectangle {
            radius: 18
            color: primaryControl.pressed ? "#b7ffe8" : root.accentColor
            border.width: primaryControl.activeFocus ? 3 : 0
            border.color: root.textColor
        }
        contentItem: RowLayout {
            spacing: 8
            Item { Layout.fillWidth: true }
            PanelIcon {
                name: primaryControl.iconName
                implicitWidth: 21
                implicitHeight: 21
                iconColor: root.pageColor
            }
            PanelText {
                text: primaryControl.text
                color: root.pageColor
                font.pixelSize: 17
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
        }
    }

    component DialogButton: QQC2.Button {
        id: dialogAction
        property string tone: "neutral"
        property bool busy: false
        Layout.minimumWidth: 120
        Layout.minimumHeight: 52
        Layout.preferredHeight: 52
        implicitHeight: 52
        leftPadding: 18
        rightPadding: 18
        topPadding: 0
        bottomPadding: 0
        focusPolicy: Qt.StrongFocus
        opacity: enabled || busy ? 1 : 0.42
        Accessible.name: text
        background: Rectangle {
            radius: 10
            color: !dialogAction.enabled && !dialogAction.busy ? root.surfaceRaisedColor
                : dialogAction.pressed ? root.surfacePressedColor
                : dialogAction.tone === "primary" ? root.accentColor
                : dialogAction.tone === "danger" ? root.performanceMutedColor
                : root.surfaceRaisedColor
            border.width: dialogAction.activeFocus ? 2 : 1
            border.color: !dialogAction.enabled && !dialogAction.busy ? root.cardBorder
                : dialogAction.activeFocus ? root.textColor
                : dialogAction.tone === "danger" ? root.dangerColor
                : dialogAction.tone === "primary" ? root.accentColor : root.cardBorder
        }
        contentItem: RowLayout {
            spacing: 8
            Item { Layout.fillWidth: true }
            PanelIcon {
                visible: dialogAction.busy
                name: "arrows-clockwise"
                implicitWidth: 20
                implicitHeight: 20
                iconColor: root.accentColor
                RotationAnimator on rotation {
                    running: dialogAction.busy
                    from: 0
                    to: 360
                    duration: 1100
                    loops: Animation.Infinite
                }
            }
            PanelText {
                text: dialogAction.text
                color: !dialogAction.enabled && !dialogAction.busy ? root.mutedColor
                    : dialogAction.tone === "primary" && !dialogAction.pressed
                    ? root.pageColor : root.textColor
                font.pixelSize: 16
                font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter
            }
            Item { Layout.fillWidth: true }
        }
    }

    component DialogFooter: Item {
        default property alias actions: actionRow.data
        property alias spacing: actionRow.spacing
        implicitHeight: 76
        Rectangle {
            x: 20
            width: parent.width - 40
            height: 1
            color: root.dividerColor
        }
        RowLayout {
            id: actionRow
            anchors.fill: parent
            anchors.leftMargin: 20
            anchors.rightMargin: 20
            anchors.topMargin: 8
            anchors.bottomMargin: 16
            spacing: 10
        }
    }

    component PanelDialog: QQC2.Dialog {
        id: panelDialog
        property string iconName: "game-controller"
        property bool canDismiss: true
        property Item initialFocusItem: null
        property Item returnFocusItem: null
        focus: true
        modal: true
        leftPadding: 20
        rightPadding: 20
        topPadding: 16
        bottomPadding: 16
        spacing: 0
        closePolicy: QQC2.Popup.CloseOnEscape
        onAboutToShow: {
            const window = parent ? parent.Window.window : null
            returnFocusItem = window ? window.activeFocusItem : null
        }
        onOpened: {
            if (initialFocusItem && initialFocusItem.enabled)
                initialFocusItem.forceActiveFocus(Qt.PopupFocusReason)
        }
        onClosed: {
            if (returnFocusItem && returnFocusItem.visible && returnFocusItem.enabled)
                returnFocusItem.forceActiveFocus(Qt.PopupFocusReason)
        }
        QQC2.Overlay.modal: Rectangle { color: "#99090c0d" }
        background: Rectangle {
            radius: 16
            color: root.surfaceColor
            border.width: 1
            border.color: root.cardBorder
        }
        header: Item {
            implicitHeight: 64
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 12
                anchors.topMargin: 10
                anchors.bottomMargin: 10
                spacing: 10
                PanelIcon {
                    name: panelDialog.iconName
                    implicitWidth: 22
                    implicitHeight: 22
                    iconColor: root.accentColor
                }
                PanelText {
                    Layout.fillWidth: true
                    text: panelDialog.title
                    color: root.textColor
                    font.pixelSize: 20
                    font.weight: Font.DemiBold
                }
                DialogButton {
                    objectName: "dialogClose"
                    leftPadding: 12
                    rightPadding: 12
                    Layout.minimumWidth: 44
                    Layout.preferredWidth: 44
                    Layout.maximumWidth: 44
                    Layout.minimumHeight: 44
                    Layout.preferredHeight: 44
                    text: "关闭"
                    enabled: panelDialog.canDismiss
                    onClicked: panelDialog.close()
                    contentItem: PanelIcon { name: "x"; implicitWidth: 18; implicitHeight: 18 }
                }
            }
            Rectangle {
                x: 20
                y: parent.height - 1
                width: parent.width - 40
                height: 1
                color: root.dividerColor
            }
        }
    }

    component TelemetryLine: Item {
        required property string label
        required property string iconName
        required property string valueText
        property color valueColor: root.textColor
        property int valueSize: 14
        property bool meterValid: false
        property real meterValue: 0
        property color meterColor: root.accentColor
        implicitHeight: 44
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true

        ColumnLayout {
            anchors.fill: parent
            spacing: 4
            RowLayout {
                Layout.fillWidth: true
                spacing: 5
                PanelIcon {
                    name: iconName
                    implicitWidth: 13
                    implicitHeight: 13
                    iconColor: root.mutedColor
                }
                PanelText {
                    text: label
                    color: root.mutedColor
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                }
                Item {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 3
                }
                PanelText {
                    text: valueText
                    color: valueColor
                    font.family: root.numericFontFamily
                    font.pixelSize: valueSize
                    font.weight: Font.DemiBold
                }
            }
            Meter {
                visible: meterValid
                Layout.fillWidth: true
                amount: meterValue
                fillColor: meterColor
                valid: meterValid
            }
        }
    }

    fullRepresentation: Item {
        id: fullRoot
        onVisibleChanged: {
            if (!visible) shortcutDialog.close()
        }
        // Plasma 6.7 clamps the live desktop Widget.geometry to 816x608 on
        // this fractionally scaled 819x614 output even when both stored
        // geometry tokens are exact.  Paint the surface to the authoritative
        // screen geometry; child items are not clipped by the applet host.
        readonly property real surfaceWidth: Math.max(width, root.screenGeometry.width)
        readonly property real surfaceHeight: Math.max(height, root.screenGeometry.height)

        Rectangle {
            id: panelSurface
            x: 0
            y: 0
            width: fullRoot.surfaceWidth
            height: fullRoot.surfaceHeight
            color: root.pageColor

            Rectangle {
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                height: 3
                color: root.accentColor
            }
            Rectangle {
                x: -210
                y: -280
                width: 620
                height: 620
                radius: 310
                color: root.accentColor
                opacity: 0.035
            }
            Rectangle {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.rightMargin: -180
                anchors.bottomMargin: -260
                width: 520
                height: 520
                radius: 260
                color: root.performanceColor
                opacity: 0.025
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                anchors.topMargin: 12
                anchors.bottomMargin: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    Layout.minimumHeight: 42
                    Layout.preferredHeight: 42
                    Layout.maximumHeight: 42
                    spacing: 10

                    Rectangle {
                        Layout.minimumWidth: 34
                        Layout.preferredWidth: 34
                        Layout.maximumWidth: 34
                        Layout.minimumHeight: 34
                        Layout.preferredHeight: 34
                        radius: 9
                        color: root.accentMutedColor
                        border.width: 1
                        border.color: root.accentColor
                        PanelIcon {
                            anchors.centerIn: parent
                            name: "game-controller"
                            implicitWidth: 20
                            implicitHeight: 20
                            iconColor: root.accentColor
                        }
                    }
                    PanelText {
                        text: "POCKET DS"
                        color: root.textColor
                        font.pixelSize: 17
                        font.weight: Font.Bold
                        font.letterSpacing: 1.8
                    }
                    Rectangle {
                        Layout.leftMargin: 4
                        implicitWidth: statusRow.implicitWidth + 18
                        implicitHeight: 28
                        radius: 8
                        color: root.surfaceColor
                        border.width: 1
                        border.color: root.cardBorder
                        RowLayout {
                            id: statusRow
                            anchors.centerIn: parent
                            spacing: 6
                            Rectangle {
                                implicitWidth: 7
                                implicitHeight: 7
                                radius: 4
                                color: root.telemetryStateColor()
                            }
                            PanelText {
                                text: root.telemetryStateText()
                                color: root.telemetryStateColor()
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }

                    RowLayout {
                        spacing: 6
                        PanelIcon {
                            name: root.batteryExternalPower === 1
                                ? "battery-charging"
                                : root.batteryPercent >= 0 && root.batteryPercent <= 15
                                ? "battery-warning" : "battery-high"
                            implicitWidth: 18
                            implicitHeight: 18
                            iconColor: !root.batteryHealthy ? root.mutedColor :
                                   root.batteryPercent >= 0 && root.batteryPercent <= 15
                                   ? root.dangerColor : root.accentColor
                        }
                        PanelText {
                            text: !root.batteryHealthy || root.batteryPercent < 0
                                ? "—" : root.batteryPercent + "%"
                            color: root.textColor
                            font.family: root.numericFontFamily
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                        }
                        QQC2.ToolTip.visible: batteryHover.hovered
                        QQC2.ToolTip.text: root.batteryTooltip()
                        HoverHandler { id: batteryHover }
                    }
                    Rectangle {
                        width: 1
                        Layout.fillHeight: true
                        Layout.topMargin: 8
                        Layout.bottomMargin: 8
                        color: root.dividerColor
                    }
                    PanelText {
                        text: root.currentTime
                        color: root.textColor
                        font.family: root.numericFontFamily
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 10

                    SectionCard {
                        Layout.minimumWidth: 218
                        Layout.preferredWidth: 218
                        Layout.maximumWidth: 218
                        Layout.fillHeight: true

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 5

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 7
                                PanelIcon {
                                    name: "monitor"
                                    implicitWidth: 16
                                    implicitHeight: 16
                                    iconColor: root.accentColor
                                }
                                PanelText {
                                    text: "上屏"
                                    color: root.textColor
                                    font.pixelSize: 15
                                    font.weight: Font.DemiBold
                                }
                                Item { Layout.fillWidth: true }
                                Rectangle {
                                    implicitWidth: 7
                                    implicitHeight: 7
                                    radius: 4
                                    color: root.topFpsHealthy
                                        ? root.accentColor : root.mutedColor
                                }
                            }
                            PanelText {
                                text: root.displayFpsText()
                                color: root.topFpsHealthy
                                    ? root.textColor : root.mutedColor
                                font.family: root.numericFontFamily
                                font.pixelSize: 42
                                font.weight: Font.Bold
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                height: 1
                                color: root.dividerColor
                            }

                            TelemetryLine {
                                label: "CPU"
                                iconName: "cpu"
                                valueText: !root.telemetryHealthy || root.cpuPercent < 0
                                    ? "—" : Math.round(root.cpuPercent) + "%" +
                                      (root.cpuGHz < 0 ? "" : "  " + root.cpuGHz.toFixed(1) + "G") +
                                      (!root.fanHealthy || root.temperature < 0 ? "" : "  " + root.temperature + "°")
                                valueColor: root.telemetryHealthy && root.cpuPercent >= 0
                                    ? root.textColor : root.mutedColor
                                meterValid: root.telemetryHealthy && root.cpuPercent >= 0
                                meterValue: Math.max(0, root.cpuPercent)
                            }
                            TelemetryLine {
                                label: "GPU"
                                iconName: "graphics-card"
                                valueText: !root.telemetryHealthy || root.gpuPercent < 0
                                    ? "—" : Math.round(root.gpuPercent) + "%" +
                                      (root.gpuFrequencyMHz < 0 ? ""
                                       : "  " + Math.round(root.gpuFrequencyMHz) + "M") +
                                      (root.gpuTemperature < 0 ? ""
                                       : "  " + root.gpuTemperature.toFixed(0) + "°")
                                valueColor: root.telemetryHealthy && root.gpuPercent >= 0
                                    ? root.textColor : root.mutedColor
                                meterValid: root.telemetryHealthy && root.gpuPercent >= 0
                                meterValue: Math.max(0, root.gpuPercent)
                            }
                            TelemetryLine {
                                label: "POWER"
                                iconName: "lightning"
                                valueText: root.systemPowerHealthy
                                    ? (root.systemPowerSource === "battery-discharge" ? "−" : "+") +
                                      root.systemPower.toFixed(2) + " W" : "—"
                                valueColor: root.systemPowerHealthy
                                    ? root.textColor : root.mutedColor
                                meterValid: root.systemPowerHealthy
                                meterValue: root.systemPowerHealthy
                                    ? Math.min(100, root.systemPower / 30 * 100) : 0
                                meterColor: root.systemPowerSource === "usb-input"
                                    ? root.successColor : root.accentColor
                            }
                            TelemetryLine {
                                label: "NET"
                                iconName: "network"
                                valueSize: 12
                                valueText: root.telemetryHealthy
                                    ? "↓" + root.formatCompactRate(root.downloadRate) +
                                      "  ↑" + root.formatCompactRate(root.uploadRate) : "—"
                                valueColor: root.telemetryHealthy
                                    ? root.textColor : root.mutedColor
                            }
                            TelemetryLine {
                                label: "CODEX"
                                iconName: "code"
                                valueText: !root.quotaHealthy ||
                                    root.quotaDisplayRemaining() < 0
                                    ? "—" : root.quotaDisplayRemaining() + "%"
                                valueColor: root.quotaHealthy
                                    ? root.textColor : root.mutedColor
                                meterValid: root.quotaHealthy &&
                                    root.quotaDisplayRemaining() >= 0
                                meterValue: root.quotaHealthy
                                    ? root.quotaDisplayRemaining() : 0
                            }

                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 10

                        SectionCard {
                            Layout.fillWidth: true
                            Layout.minimumHeight: 208
                            Layout.preferredHeight: 208
                            Layout.maximumHeight: 208

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 14
                                anchors.rightMargin: 14
                                anchors.topMargin: 11
                                anchors.bottomMargin: 9
                                spacing: 2

                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.minimumHeight: 24
                                    spacing: 7
                                    PanelIcon {
                                        name: "sun"
                                        implicitWidth: 18
                                        implicitHeight: 18
                                        iconColor: root.accentColor
                                    }
                                    PanelText {
                                        text: "亮度与声音"
                                        color: root.textColor
                                        font.pixelSize: 18
                                        font.weight: Font.DemiBold
                                    }
                                    Item { Layout.fillWidth: true }
                                    PanelText {
                                        visible: root.telemetryHealthy &&
                                            root.brightnessWriteStatus !== "ok"
                                        text: root.brightnessWriteStatus === "unavailable"
                                            ? "控制服务未就绪" : "控制状态异常"
                                        color: root.performanceColor
                                        font.pixelSize: 11
                                        font.weight: Font.Medium
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    spacing: 10
                                    ControlLabel {
                                        iconName: "monitor"
                                        labelText: "上屏"
                                    }
                                    TouchSlider {
                                        id: topSlider
                                        Layout.fillWidth: true
                                        from: 5
                                        to: 100
                                        stepSize: 1
                                        telemetryValue: root.topBrightness
                                        telemetryValid: root.topBrightnessHealthy
                                        controlHealthy: root.topBrightnessWritable
                                        actionPending: root.actionPending("top")
                                        onCommitted: function(newValue) {
                                            const target = Math.round(newValue)
                                            root.beginAction("top",
                                                "brightness top " + target,
                                                {"kind": "level", "value": target})
                                        }
                                    }
                                    PanelText {
                                        Layout.minimumWidth: 60
                                        Layout.preferredWidth: 60
                                        horizontalAlignment: Text.AlignRight
                                        text: topSlider.userInteraction ||
                                              topSlider.actionPending
                                            ? Math.round(topSlider.presentedValue) + "%"
                                            : root.actionError("top").length > 0
                                            ? "失败"
                                            : root.topBrightnessHealthy
                                            ? root.topBrightness + "%" : "—"
                                        color: root.actionError("top").length > 0
                                            ? root.dangerColor
                                            : topSlider.actionPending
                                            ? root.accentColor : root.textColor
                                        font.family: root.numericFontFamily
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    spacing: 10
                                    ControlLabel {
                                        iconName: "device-tablet"
                                        labelText: "下屏"
                                    }
                                    TouchSlider {
                                        id: bottomSlider
                                        Layout.fillWidth: true
                                        from: 5
                                        to: 100
                                        stepSize: 1
                                        telemetryValue: root.bottomBrightness
                                        telemetryValid: root.bottomBrightnessHealthy
                                        controlHealthy: root.bottomBrightnessWritable
                                        actionPending: root.actionPending("bottom")
                                        onCommitted: function(newValue) {
                                            const target = Math.round(newValue)
                                            root.beginAction("bottom",
                                                "brightness bottom " + target,
                                                {"kind": "level", "value": target})
                                        }
                                    }
                                    PanelText {
                                        Layout.minimumWidth: 60
                                        Layout.preferredWidth: 60
                                        horizontalAlignment: Text.AlignRight
                                        text: bottomSlider.userInteraction ||
                                              bottomSlider.actionPending
                                            ? Math.round(bottomSlider.presentedValue) + "%"
                                            : root.actionError("bottom").length > 0
                                            ? "失败"
                                            : root.bottomBrightnessHealthy
                                            ? root.bottomBrightness + "%" : "—"
                                        color: root.actionError("bottom").length > 0
                                            ? root.dangerColor
                                            : bottomSlider.actionPending
                                            ? root.accentColor : root.textColor
                                        font.family: root.numericFontFamily
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    spacing: 10
                                    ControlLabel {
                                        iconName: root.muted
                                            ? "speaker-slash" : "speaker-high"
                                        labelText: "音量"
                                    }
                                    TouchSlider {
                                        id: volumeSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        telemetryValue: root.volumePercent
                                        telemetryValid: root.volumeHealthy
                                        controlHealthy: root.volumeWritable
                                        actionPending: root.actionPending("volume")
                                        onCommitted: function(newValue) {
                                            const target = Math.round(newValue)
                                            root.beginAction("volume",
                                                "volume " + target,
                                                {"kind": "level", "value": target})
                                        }
                                    }
                                    PanelText {
                                        Layout.minimumWidth: 48
                                        Layout.preferredWidth: 48
                                        horizontalAlignment: Text.AlignRight
                                        text: volumeSlider.userInteraction ||
                                              (volumeSlider.actionPending &&
                                               root.pendingActionKind("volume") !== "mute")
                                            ? Math.round(volumeSlider.presentedValue) + "%"
                                            : root.actionError("volume").length > 0
                                            ? "失败"
                                            : !root.volumeHealthy ? "—"
                                            : root.muted ? "静音"
                                            : root.volumePercent + "%"
                                        color: root.actionError("volume").length > 0
                                            ? root.dangerColor
                                            : volumeSlider.actionPending
                                            ? root.accentColor : root.textColor
                                        font.family: root.numericFontFamily
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                    }
                                    QQC2.Button {
                                        id: volumeMuteButton
                                        Layout.minimumWidth: 34
                                        Layout.preferredWidth: 34
                                        Layout.minimumHeight: 34
                                        Layout.preferredHeight: 34
                                        text: root.muted ? "取消静音" : "静音"
                                        enabled: root.volumeHealthy &&
                                            !root.actionPending("volume")
                                        onClicked: root.beginAction("volume", "mute",
                                            {"kind": "mute", "value": !root.muted})
                                        background: Rectangle {
                                            radius: 8
                                            color: volumeMuteButton.pressed
                                                ? root.surfacePressedColor : "transparent"
                                        }
                                        contentItem: Item {
                                            PanelIcon {
                                                anchors.centerIn: parent
                                                width: 20
                                                height: 20
                                                name: root.muted
                                                    ? "speaker-slash" : "speaker-high"
                                                iconColor: volumeMuteButton.enabled
                                                    ? root.textColor : root.cardBorder
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        SectionCard {
                            Layout.fillWidth: true
                            Layout.fillHeight: true

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 12
                                spacing: 12
                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    spacing: 12
                                    ColumnLayout {
                                        Layout.minimumWidth: 94
                                        Layout.preferredWidth: 94
                                        Layout.maximumWidth: 94
                                        spacing: 2
                                        RowLayout {
                                            spacing: 7
                                            PanelIcon {
                                                name: "gauge"
                                                implicitWidth: 18
                                                implicitHeight: 18
                                                iconColor: root.accentColor
                                            }
                                            PanelText {
                                                text: "性能"
                                                color: root.textColor
                                                font.pixelSize: 16
                                                font.weight: Font.DemiBold
                                            }
                                        }
                                        PanelText {
                                            Layout.leftMargin: 25
                                            text: root.actionPending("power")
                                                ? "切换中"
                                                : root.actionError("power").length > 0
                                                ? "失败" : root.powerModeText()
                                            color: root.actionError("power").length > 0
                                                ? root.dangerColor
                                                : root.actionPending("power")
                                                ? root.accentColor : root.textColor
                                            font.pixelSize: 14
                                            font.weight: Font.DemiBold
                                        }
                                    }
                                    ProfileButton {
                                        Layout.minimumHeight: 60
                                        Layout.preferredHeight: 60
                                        profileValue: "powersave"
                                        text: "省电"
                                        selected: root.powerHealthy &&
                                            root.profileSelected("power",
                                                root.powerProfile, profileValue)
                                        controlHealthy: root.powerHealthy
                                        pending: root.actionPending("power")
                                        onClicked: root.beginAction("power",
                                            "power " + profileValue,
                                            {"kind": "profile", "value": profileValue})
                                    }
                                    ProfileButton {
                                        Layout.minimumHeight: 60
                                        Layout.preferredHeight: 60
                                        profileValue: "balanced"
                                        text: "均衡"
                                        selected: root.powerHealthy &&
                                            root.profileSelected("power",
                                                root.powerProfile, profileValue)
                                        controlHealthy: root.powerHealthy
                                        pending: root.actionPending("power")
                                        onClicked: root.beginAction("power",
                                            "power " + profileValue,
                                            {"kind": "profile", "value": profileValue})
                                    }
                                    ProfileButton {
                                        Layout.minimumHeight: 60
                                        Layout.preferredHeight: 60
                                        profileValue: "performance"
                                        text: "性能"
                                        selectedColor: root.performanceColor
                                        selectedFill: root.performanceMutedColor
                                        selected: root.powerHealthy &&
                                            root.profileSelected("power",
                                                root.powerProfile, profileValue)
                                        controlHealthy: root.powerHealthy
                                        pending: root.actionPending("power")
                                        onClicked: root.beginAction("power",
                                            "power " + profileValue,
                                            {"kind": "profile", "value": profileValue})
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    spacing: 12
                                    ColumnLayout {
                                        Layout.minimumWidth: 94
                                        Layout.preferredWidth: 94
                                        Layout.maximumWidth: 94
                                        spacing: 2
                                        RowLayout {
                                            spacing: 7
                                            PanelIcon {
                                                name: "fan"
                                                implicitWidth: 18
                                                implicitHeight: 18
                                                iconColor: root.accentColor
                                            }
                                            PanelText {
                                                text: "风扇"
                                                color: root.textColor
                                                font.pixelSize: 16
                                                font.weight: Font.DemiBold
                                            }
                                        }
                                        PanelText {
                                            Layout.leftMargin: 25
                                            text: root.actionPending("fan")
                                                ? "切换中"
                                                : root.actionError("fan").length > 0
                                                ? "失败"
                                                : root.fanHealthy
                                                ? root.fanPercent + "%" : "—"
                                            color: root.actionError("fan").length > 0
                                                ? root.dangerColor
                                                : root.actionPending("fan")
                                                ? root.accentColor : root.textColor
                                            font.family: root.numericFontFamily
                                            font.pixelSize: 14
                                            font.weight: Font.DemiBold
                                        }
                                    }
                                    ProfileButton {
                                        Layout.minimumHeight: 60
                                        Layout.preferredHeight: 60
                                        profileValue: "quiet"
                                        text: "静音"
                                        selected: root.fanHealthy &&
                                            root.profileSelected("fan",
                                                root.fanProfile, profileValue)
                                        controlHealthy: root.fanHealthy
                                        pending: root.actionPending("fan")
                                        onClicked: root.beginAction("fan",
                                            "fan-profile " + profileValue,
                                            {"kind": "profile", "value": profileValue})
                                    }
                                    ProfileButton {
                                        Layout.minimumHeight: 60
                                        Layout.preferredHeight: 60
                                        profileValue: "moderate"
                                        text: "均衡"
                                        selected: root.fanHealthy &&
                                            root.profileSelected("fan",
                                                root.fanProfile, profileValue)
                                        controlHealthy: root.fanHealthy
                                        pending: root.actionPending("fan")
                                        onClicked: root.beginAction("fan",
                                            "fan-profile " + profileValue,
                                            {"kind": "profile", "value": profileValue})
                                    }
                                    ProfileButton {
                                        Layout.minimumHeight: 60
                                        Layout.preferredHeight: 60
                                        profileValue: "aggressive"
                                        text: "强冷"
                                        selectedColor: root.performanceColor
                                        selectedFill: root.performanceMutedColor
                                        selected: root.fanHealthy &&
                                            root.profileSelected("fan",
                                                root.fanProfile, profileValue)
                                        controlHealthy: root.fanHealthy
                                        pending: root.actionPending("fan")
                                        onClicked: root.beginAction("fan",
                                            "fan-profile " + profileValue,
                                            {"kind": "profile", "value": profileValue})
                                    }
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: 1
                                    color: root.dividerColor
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    QQC2.Button {
                                        id: fpsLimitButton
                                        leftPadding: 10
                                        rightPadding: 10
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 254
                                        Layout.preferredWidth: 254
                                        Layout.minimumHeight: 56
                                        Layout.preferredHeight: 56
                                        enabled: root.gameFpsLimitWritable
                                        opacity: enabled || root.actionPending("game-limit") ? 1 : 0.45
                                        onClicked: fpsLimitPopup.open()
                                        background: Rectangle {
                                            radius: 10
                                            color: fpsLimitButton.pressed
                                                ? root.surfacePressedColor
                                                : root.surfaceRaisedColor
                                            border.width: 1
                                            border.color: root.actionError("game-limit").length > 0
                                                ? root.dangerColor : root.cardBorder
                                        }
                                        contentItem: UtilityReadout {
                                            iconName: "speedometer"
                                            labelText: "帧率"
                                            numericValue: true
                                            showChevron: true
                                            failed: root.actionError("game-limit").length > 0
                                            valueText: root.actionPending("game-limit")
                                                ? "切换中"
                                                : root.actionError("game-limit").length > 0
                                                ? "失败"
                                                : root.gameFpsLimitChoiceText(
                                                    root.presentedGameFpsLimit()) +
                                                  "  " + root.displayFpsText()
                                        }
                                    }
                                    QQC2.Button {
                                        id: lidStandbyButton
                                        leftPadding: 10
                                        rightPadding: 10
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 144
                                        Layout.preferredWidth: 144
                                        Layout.minimumHeight: 56
                                        Layout.preferredHeight: 56
                                        enabled: true
                                        opacity: 1
                                        onClicked: {
                                            root.requestLidModeStatus()
                                            standbyDialog.open()
                                        }
                                        background: Rectangle {
                                            radius: 10
                                            color: lidStandbyButton.pressed
                                                ? root.surfacePressedColor
                                                : root.surfaceRaisedColor
                                            border.width: 1
                                            border.color: (root.actionError("standby").length > 0 || root.actionError("lid-mode").length > 0)
                                                ? root.dangerColor : root.cardBorder
                                        }
                                        contentItem: UtilityReadout {
                                            iconName: "moon-stars"
                                            labelText: "合盖"
                                            failed: root.actionError("standby").length > 0 || root.actionError("lid-mode").length > 0
                                            valueText: root.actionPending("standby") || root.actionPending("lid-mode")
                                                ? "保存中"
                                                : (root.actionError("standby").length > 0 || root.actionError("lid-mode").length > 0)
                                                ? "失败"
                                                : root.lidModeHealthy && root.lidMode === "sleep" ? "休眠"
                                                : root.lidModeHealthy && root.lidMode === "unknown" ? "自定义"
                                                : root.lidModeHealthy && root.lidAutoPoweroffHealthy
                                                ? root.lidAutoPoweroffChoiceText(
                                                    root.presentedLidAutoPoweroffMinutes())
                                                : "—"
                                        }
                                    }
                                    QQC2.Button {
                                        id: recoveryUtility
                                        leftPadding: 10
                                        rightPadding: 10
                                        Layout.minimumWidth: 112
                                        Layout.preferredWidth: 112
                                        Layout.maximumWidth: 112
                                        Layout.minimumHeight: 56
                                        Layout.preferredHeight: 56
                                        enabled: !root.recoveryPending
                                        onClicked: recoveryDialog.open()
                                        background: Rectangle {
                                            radius: 10
                                            color: recoveryUtility.pressed
                                                ? root.surfacePressedColor
                                                : root.surfaceRaisedColor
                                            border.width: 1
                                            border.color: root.recoveryStatus === "failed"
                                                ? root.dangerColor : root.cardBorder
                                        }
                                        contentItem: RowLayout {
                                            spacing: 6
                                            PanelIcon {
                                                Layout.alignment: Qt.AlignVCenter
                                                name: root.recoveryStatus === "failed"
                                                    ? "warning-circle" : "arrows-clockwise"
                                                implicitWidth: 17
                                                implicitHeight: 17
                                                iconColor: root.recoveryStatus === "failed"
                                                    ? root.dangerColor : root.accentColor
                                            }
                                            PanelText {
                                                Layout.alignment: Qt.AlignVCenter
                                                text: "桌面修复"
                                                color: root.textColor
                                                font.pixelSize: 14
                                                font.weight: Font.DemiBold
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                SectionCard {
                    Layout.fillWidth: true
                    Layout.minimumHeight: 64
                    Layout.preferredHeight: 64
                    Layout.maximumHeight: 64
                    radius: 14
                    color: "#111617"

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 5
                        spacing: 8

                        HeaderAction {
                            Layout.fillWidth: false
                            Layout.minimumWidth: 149
                            Layout.preferredWidth: 149
                            Layout.maximumWidth: 149
                            text: "键盘与语音"
                            iconName: "keyboard"
                            onClicked: root.exec("keyboard")
                        }
                        HeaderAction {
                            Layout.fillWidth: false
                            Layout.minimumWidth: 149
                            Layout.preferredWidth: 149
                            Layout.maximumWidth: 149
                            text: "触摸板"
                            iconName: "hand-tap"
                            onClicked: root.exec("touchpad")
                        }
                        HeaderAction {
                            Layout.fillWidth: false
                            Layout.minimumWidth: 149
                            Layout.preferredWidth: 149
                            Layout.maximumWidth: 149
                            text: root.actionPending("input")
                                ? "正在切换" : root.inputModeShortText()
                            iconName: root.presentedInputMode() === "joymouse"
                                ? "mouse-simple" : "game-controller"
                            emphasized: root.inputHealthy
                            failed: root.actionError("input").length > 0
                            enabled: root.inputHealthy && !root.actionPending("input")
                            onClicked: {
                                const target = root.inputMode === "joymouse"
                                    ? "gamepad" : "joymouse"
                                root.beginAction("input", "input-toggle",
                                    {"kind": "mode", "value": target})
                            }
                        }
                        HeaderAction {
                            Layout.fillWidth: false
                            Layout.minimumWidth: 149
                            Layout.preferredWidth: 149
                            Layout.maximumWidth: 149
                            text: "实体按键"
                            iconName: "game-controller"
                            onClicked: shortcutDialog.open()
                        }
                        HeaderAction {
                            Layout.fillWidth: false
                            Layout.minimumWidth: 153
                            Layout.preferredWidth: 153
                            Layout.maximumWidth: 153
                            text: "切到安卓"
                            iconName: "arrows-clockwise"
                            onClicked: androidBootDialog.open()
                        }
                    }
                }
            }
        }

        PanelDialog {
            id: standbyDialog
            parent: fullRoot
            iconName: "moon-stars"
            initialFocusItem: standbyDone
            width: Math.min(720, fullRoot.surfaceWidth - 32)
            height: Math.min(root.actionPending("standby") || root.actionError("standby").length > 0 || !root.lidAutoPoweroffHealthy || root.lidAutoPoweroffStatus === "invalid" ? 526 : 486, fullRoot.surfaceHeight - 32)
            x: Math.round((fullRoot.surfaceWidth - width) / 2)
            y: Math.round((fullRoot.surfaceHeight - height) / 2)
            title: "合盖设置"
            closePolicy: QQC2.Popup.CloseOnEscape


            contentItem: ColumnLayout {
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    ProfileButton {
                        id: lidConnectedChoice
                        Layout.minimumWidth: 0
                        Layout.preferredWidth: 1
                        text: "联网待机"
                        profileValue: "connected"
                        selected: root.presentedLidMode() === "connected"
                        controlHealthy: root.lidModeWritable
                        pending: root.actionPending("lid-mode")
                        onClicked: root.chooseLidMode("connected")
                    }
                    ProfileButton {
                        id: lidSleepChoice
                        Layout.minimumWidth: 0
                        Layout.preferredWidth: 1
                        text: "休眠"
                        profileValue: "sleep"
                        selected: root.presentedLidMode() === "sleep"
                        controlHealthy: root.lidModeWritable && root.lidSleepAvailable
                        pending: root.actionPending("lid-mode")
                        onClicked: root.chooseLidMode("sleep")
                    }
                }
                PanelText {
                    Layout.fillWidth: true
                    text: root.presentedLidMode() === "sleep"
                        ? "合盖后暂停运行，更省电；开盖后继续。"
                        : root.presentedLidMode() === "connected"
                        ? (root.lidSleepAvailable
                            ? "合盖先关闭双屏并保持联网；自动休眠仍按系统设置执行。"
                            : "合盖后关闭双屏，Wi‑Fi 和后台任务继续运行。")
                        : "请选择合盖后的行为。"
                    color: root.textColor
                    font.pixelSize: 15
                    wrapMode: Text.WordWrap
                }
                PanelText {
                    Layout.fillWidth: true
                    visible: root.lidSleepUnavailableText().length > 0 ||
                        root.actionError("lid-mode").length > 0 || root.actionPending("lid-mode")
                    text: root.actionPending("lid-mode") ? "正在保存合盖方式…"
                        : root.actionError("lid-mode").length > 0 ? root.actionError("lid-mode")
                        : root.lidSleepUnavailableText()
                    color: root.actionError("lid-mode").length > 0 ? root.dangerColor : root.mutedColor
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: root.dividerColor
                }
                PanelText {
                    Layout.topMargin: 4
                    text: "联网待机 · 自动关机"
                    color: root.textColor
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    ProfileButton {
                        Layout.minimumWidth: 0
                        Layout.preferredWidth: 1
                        Layout.minimumHeight: 60
                        Layout.preferredHeight: 60
                        profileValue: "0"; text: "不关机"
                        selected: root.lidAutoPoweroffTelemetryValid &&
                            root.lidAutoPoweroffStatus !== "invalid" &&
                            root.presentedLidAutoPoweroffMinutes() === 0
                        controlHealthy: root.lidAutoPoweroffWritable &&
                            root.presentedLidMode() === "connected" && !root.actionPending("lid-mode")
                        pending: root.actionPending("standby")
                        onClicked: root.beginAction("standby", "lid-auto-poweroff 0",
                                                    {"kind": "minutes", "value": 0})
                    }
                    ProfileButton {
                        Layout.minimumWidth: 0
                        Layout.preferredWidth: 1
                        Layout.minimumHeight: 60
                        Layout.preferredHeight: 60
                        profileValue: "60"; text: "1 小时"
                        selected: root.lidAutoPoweroffTelemetryValid &&
                            root.lidAutoPoweroffStatus !== "invalid" &&
                            root.presentedLidAutoPoweroffMinutes() === 60
                        controlHealthy: root.lidAutoPoweroffWritable &&
                            root.presentedLidMode() === "connected" && !root.actionPending("lid-mode")
                        pending: root.actionPending("standby")
                        onClicked: root.beginAction("standby", "lid-auto-poweroff 60",
                                                    {"kind": "minutes", "value": 60})
                    }
                    ProfileButton {
                        Layout.minimumWidth: 0
                        Layout.preferredWidth: 1
                        Layout.minimumHeight: 60
                        Layout.preferredHeight: 60
                        profileValue: "120"; text: "2 小时"
                        selected: root.lidAutoPoweroffTelemetryValid &&
                            root.lidAutoPoweroffStatus !== "invalid" &&
                            root.presentedLidAutoPoweroffMinutes() === 120
                        controlHealthy: root.lidAutoPoweroffWritable &&
                            root.presentedLidMode() === "connected" && !root.actionPending("lid-mode")
                        pending: root.actionPending("standby")
                        onClicked: root.beginAction("standby", "lid-auto-poweroff 120",
                                                    {"kind": "minutes", "value": 120})
                    }
                    ProfileButton {
                        Layout.minimumWidth: 0
                        Layout.preferredWidth: 1
                        Layout.minimumHeight: 60
                        Layout.preferredHeight: 60
                        profileValue: "240"; text: "4 小时"
                        selected: root.lidAutoPoweroffTelemetryValid &&
                            root.lidAutoPoweroffStatus !== "invalid" &&
                            root.presentedLidAutoPoweroffMinutes() === 240
                        controlHealthy: root.lidAutoPoweroffWritable &&
                            root.presentedLidMode() === "connected" && !root.actionPending("lid-mode")
                        pending: root.actionPending("standby")
                        onClicked: root.beginAction("standby", "lid-auto-poweroff 240",
                                                    {"kind": "minutes", "value": 240})
                    }
                    ProfileButton {
                        Layout.minimumWidth: 0
                        Layout.preferredWidth: 1
                        Layout.minimumHeight: 60
                        Layout.preferredHeight: 60
                        profileValue: "480"; text: "8 小时"
                        selected: root.lidAutoPoweroffTelemetryValid &&
                            root.lidAutoPoweroffStatus !== "invalid" &&
                            root.presentedLidAutoPoweroffMinutes() === 480
                        controlHealthy: root.lidAutoPoweroffWritable &&
                            root.presentedLidMode() === "connected" && !root.actionPending("lid-mode")
                        pending: root.actionPending("standby")
                        onClicked: root.beginAction("standby", "lid-auto-poweroff 480",
                                                    {"kind": "minutes", "value": 480})
                    }
                }
                PanelText {
                    Layout.fillWidth: true
                    text: root.presentedLidMode() === "sleep"
                        ? "休眠时不使用此计时；切回联网待机后保留原设置。"
                        : "仅在电池供电、持续合盖时计时；开盖或充电即取消。"
                    color: root.mutedColor
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }
                PanelText {
                    Layout.fillWidth: true
                    visible: root.actionPending("standby") || root.actionError("standby").length > 0 || !root.lidAutoPoweroffHealthy || root.lidAutoPoweroffStatus === "invalid"
                    text: root.actionPending("standby") ? "保存中"
                        : root.actionError("standby").length > 0 ? root.actionError("standby")
                        : root.lidAutoPoweroffStatus === "invalid" ? "设置异常，请重新选择自动关机时间。"
                        : "自动关机设置暂不可用。"
                    color: root.actionPending("standby") ? root.accentColor : root.dangerColor
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }

            }

            footer: DialogFooter {
                Item { Layout.fillWidth: true }
                DialogButton {
                    id: standbyDone
                    tone: "primary"
                    Layout.minimumWidth: 132
                    text: "完成"
                    onClicked: standbyDialog.close()
                }
            }
        }

        QQC2.Popup {
            id: fpsLimitPopup
            focus: true
            parent: fullRoot
            modal: false
            width: Math.min(680, fullRoot.surfaceWidth - 32)
            height: root.actionError("game-limit").length > 0 ? 220 : 192
            x: Math.round((fullRoot.surfaceWidth - width) / 2)
            onAboutToShow: y = Math.max(20, fpsLimitButton.mapToItem(fullRoot, 0, 0).y - height - 12)
            padding: 20
            closePolicy: QQC2.Popup.CloseOnEscape |
                         QQC2.Popup.CloseOnPressOutside
            background: Rectangle {
                radius: 16
                color: root.surfaceColor
                border.width: 1
                border.color: root.cardBorder
            }
            contentItem: ColumnLayout {
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    PanelIcon { name: "speedometer"; implicitWidth: 22; implicitHeight: 22; iconColor: root.accentColor }
                    PanelText {
                        text: "游戏限帧"
                        color: root.textColor
                        font.pixelSize: 18
                        font.weight: Font.DemiBold
                    }
                    Item { Layout.fillWidth: true }
                    PanelText {
                        text: root.gameFpsRuntimeActive
                            ? "本局即时生效 · 后续沿用" : "下次单屏游戏生效"
                        color: root.mutedColor
                        font.pixelSize: 13
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 7
                    Repeater {
                        id: fpsChoices
                        model: [
                            {"value": 0, "label": "自动"},
                            {"value": 24, "label": "24"},
                            {"value": 30, "label": "30"},
                            {"value": 40, "label": "40"},
                            {"value": 60, "label": "60"},
                            {"value": 120, "label": "120"}
                        ]
                        delegate: ProfileButton {
                            Layout.minimumWidth: 0
                            Layout.preferredWidth: 1
                            textSize: 22
                            Layout.minimumHeight: 60
                            Layout.preferredHeight: 60
                            required property var modelData
                            profileValue: String(modelData.value)
                            text: modelData.label
                            selected: root.gameFpsLimitTelemetryValid &&
                                root.presentedGameFpsLimit() === modelData.value
                            controlHealthy: root.gameFpsLimitWritable
                            pending: root.actionPending("game-limit")
                            onClicked: {
                                const argument = modelData.value === 0
                                    ? "off" : String(modelData.value)
                                if (root.beginAction(
                                        "game-limit", "game-limit " + argument,
                                        {"kind": "fps", "value": modelData.value}))
                                    fpsLimitPopup.close()
                            }
                        }
                    }
                }
                PanelText {
                    Layout.fillWidth: true
                    visible: root.actionError("game-limit").length > 0
                    text: root.actionError("game-limit")
                    color: root.dangerColor
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                }
            }
        }

        PanelDialog {
            id: androidBootDialog
            parent: fullRoot
            iconName: "arrows-clockwise"
            initialFocusItem: cancelAndroidBoot
            width: Math.min(680, fullRoot.surfaceWidth - 32)
            height: Math.min(root.bootSwitchError.length > 0 ? 384 : 344, fullRoot.surfaceHeight - 32)
            x: Math.round((fullRoot.surfaceWidth - width) / 2)
            y: Math.round((fullRoot.surfaceHeight - height) / 2)
            title: "切换到 Android"
            canDismiss: !root.bootSwitchPending
            closePolicy: root.bootSwitchPending ? QQC2.Popup.NoAutoClose
                                                : QQC2.Popup.CloseOnEscape


            contentItem: ColumnLayout {
                spacing: 14
                PanelText {
                    Layout.fillWidth: true
                    text: root.bootSwitchCommitted
                        ? (root.bootSwitchPending ? "启动记录已经校验完成，已请求重启。"
                                                  : "下一次启动目标已设为 Android。")
                        : "确认后将切换到原厂 Android，并立即重启。\nLinux 和游戏数据会保留。"
                    color: root.textColor
                    font.pixelSize: 17
                    font.weight: Font.Medium
                    wrapMode: Text.WordWrap
                }
                PanelText {
                    Layout.fillWidth: true
                    text: "请先保存正在进行的工作，并保持电量充足。\n首次进入 Android 时，系统可能需要完成初始化。"
                    color: root.mutedColor
                    font.pixelSize: 15
                    wrapMode: Text.WordWrap
                }
                PanelText {
                    Layout.fillWidth: true
                    visible: root.bootSwitchError.length > 0
                    text: root.bootSwitchError
                    color: root.dangerColor
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }
                Item { Layout.fillHeight: true }
            }

            footer: DialogFooter {
                Item { Layout.fillWidth: true }
                DialogButton {
                    Layout.minimumHeight: 52
                    id: cancelAndroidBoot
                    text: root.bootSwitchCommitted ? "关闭" : "取消"
                    enabled: !root.bootSwitchPending
                    onClicked: androidBootDialog.close()
                }
                DialogButton {
                    id: confirmAndroidBoot
                    tone: "danger"
                    busy: root.bootSwitchPending
                    Layout.minimumWidth: 200
                    Layout.minimumHeight: 52
                    text: root.bootSwitchPending
                        ? (root.bootSwitchCommitted ? "正在重启…" : "正在验证…")
                        : root.bootSwitchCommitted ? "重试重启" : "切换并重启"
                    enabled: !root.bootSwitchPending


                    onClicked: root.beginAndroidBoot()
                }
            }
        }

        PanelDialog {
            id: recoveryDialog
            parent: fullRoot
            iconName: "arrows-clockwise"
            initialFocusItem: cancelRecovery
            width: Math.min(700, fullRoot.surfaceWidth - 32)
            height: Math.min(root.recoveryStatus === "failed" || root.recoveryRequestStatus === "invalid" ? 400 : 344, fullRoot.surfaceHeight - 32)
            x: Math.round((fullRoot.surfaceWidth - width) / 2)
            y: Math.round((fullRoot.surfaceHeight - height) / 2)
            title: "恢复 KDE 桌面"
            closePolicy: QQC2.Popup.CloseOnEscape


            contentItem: ColumnLayout {
                spacing: 14
                PanelText {
                    Layout.fillWidth: true
                    text: root.recoveryRequestStatus === "stale"
                        ? "上一次恢复请求已超时。请先清理请求，再重新打开这里恢复桌面；仍在处理的请求不会被清理。"
                        : root.recoveryRequestStatus === "pending"
                        ? "已有恢复请求正在交接，请等待。"
                        : root.recoveryRequestStatus === "invalid"
                        ? "恢复请求状态异常，暂时无法安全恢复。请通过 SSH 检查后再试。"
                        : "重新启动 KDE 桌面界面。设备和正在运行的应用不会重启。"
                    color: root.textColor
                    font.pixelSize: 16
                    wrapMode: Text.WordWrap
                }
                PanelText {
                    Layout.fillWidth: true
                    text: root.recoveryTooltip()
                    color: root.recoveryStatus === "failed" ? root.dangerColor : root.mutedColor
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                }
                Item { Layout.fillHeight: true }
            }

            footer: DialogFooter {
                Item { Layout.fillWidth: true }
                DialogButton {
                    visible: root.recoveryRequestStatus === "stale"
                    Layout.minimumHeight: 52
                    tone: "danger"
                    text: "确认清理超时请求"
                    onClicked: {
                        recoveryDialog.close()
                        root.exec("desktop-recovery-clear-stale CONFIRM")
                    }
                }
                DialogButton {
                    Layout.minimumHeight: 52
                    id: cancelRecovery
                    text: "取消"
                    onClicked: recoveryDialog.close()
                }
                DialogButton {
                    Layout.minimumHeight: 52
                    id: confirmRecovery
                    tone: "primary"
                    text: "确认恢复桌面"
                    enabled: !root.recoveryPending && root.recoveryRequestStatus === "none"
                    onClicked: {
                        root.recoveryPending = true
                        recoveryDialog.close()
                        root.exec("desktop-recover CONFIRM")
                    }
                }
            }
        }

        ControllerTestSession {
            id: controllerSession
            requested: shortcutDialog.visible && fullRoot.visible
        }

        Connections {
            target: fullRoot.Window.window
            function onActiveChanged() {
                if (target && !target.active && shortcutDialog.opened)
                    shortcutDialog.close()
            }
        }

        PanelDialog {
            id: shortcutDialog
            parent: fullRoot
            iconName: "game-controller"
            initialFocusItem: closeShortcuts
            width: Math.min(775, fullRoot.surfaceWidth - 32)
            height: Math.min(574, fullRoot.surfaceHeight - 32)
            x: Math.round((fullRoot.surfaceWidth - width) / 2)
            y: Math.round((fullRoot.surfaceHeight - height) / 2)
            title: "手柄测试"
            closePolicy: QQC2.Popup.CloseOnEscape
            onVisibleChanged: {
                if (visible && fullRoot.Window.window)
                    fullRoot.Window.window.requestActivate()
            }

            contentItem: ColumnLayout {
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Layout.minimumHeight: 30
                    spacing: 8
                    Rectangle {
                        width: 7
                        height: 7
                        radius: 3.5
                        color: controllerSession.active ? root.accentColor
                            : controllerSession.status === "error" || controllerSession.status === "busy"
                            ? root.dangerColor : root.mutedColor
                    }
                    PanelText {
                        Layout.fillWidth: true
                        text: controllerSession.active ? "已接管"
                            : controllerSession.status === "starting" ? "正在接管…"
                            : controllerSession.status === "draining" ? "请松开按键"
                            : controllerSession.error.length ? controllerSession.error : "手柄未连接"
                        color: controllerSession.status === "error" || controllerSession.status === "busy"
                            ? root.dangerColor : root.controlTextColor
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                    }
                    PanelText {
                        text: root.inputMode === "joymouse" ? "鼠标模式" : "手柄模式"
                        color: root.mutedColor
                        font.pixelSize: 14
                    }
                }
                ControllerDiagram {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    buttons: controllerSession.buttons
                    axes: controllerSession.axes
                    active: controllerSession.active
                }
            }

            footer: DialogFooter {
                DialogButton {
                    visible: controllerSession.status === "error" || controllerSession.status === "busy"
                    enabled: !controllerSession.inFlight
                    text: "重试"
                    onClicked: controllerSession.retry()
                }
                Item { Layout.fillWidth: true }
                DialogButton {
                    id: closeShortcuts
                    text: "关闭"
                    onClicked: shortcutDialog.close()
                }
            }
        }
    }
}
