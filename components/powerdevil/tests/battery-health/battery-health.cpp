// SPDX-License-Identifier: GPL-3.0-or-later
// Evaluate the actual upstream bindings without constructing power controllers.
#include <QCoreApplication>
#include <QFile>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QRegularExpression>
#include <QTextStream>
#include <memory>

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    if (app.arguments().size() != 2)
        return 2;
    QFile file(app.arguments().at(1));
    if (!file.open(QIODevice::ReadOnly))
        return 2;
    const QString source = QString::fromUtf8(file.readAll());
    const auto broken = QRegularExpression(QStringLiteral(
        "readonly property bool isBroken:\\s*([^\\n]+)")).match(source);
    const auto health = QRegularExpression(QStringLiteral(
        "readonly property bool healthRowVisible:\\s*([\\s\\S]*?)\\n\\s*LeftLabel\\s*\\{")).match(source);
    if (!broken.hasMatch() || !health.hasMatch())
        return 2;

    QQmlEngine engine;
    QQmlComponent component(&engine);
    component.setData((QStringLiteral(
        "import QtQml\nQtObject { id: root; property int batteryCapacity: 0; "
        "property bool batteryIsPowerSupply: false; readonly property bool isBroken: ")
        + broken.captured(1) + QStringLiteral("; readonly property bool healthRowVisible: ")
        + health.captured(1) + QStringLiteral("\n}\n")).toUtf8(), QUrl());
    if (component.isError()) {
        QTextStream(stderr) << component.errorString();
        return 2;
    }

    struct Case { int capacity; bool supply; bool row; bool warning; };
    const Case cases[] = {
        {-1, true, false, false}, {0, true, false, false},
        {1, true, false, true}, {49, true, false, true},
        {50, true, true, false}, {75, true, true, false},
        {100, true, true, false}, {0, false, false, false},
        {75, false, false, false},
    };
    int failures = 0;
    for (const auto &test : cases) {
        std::unique_ptr<QObject> object(component.createWithInitialProperties({
            {QStringLiteral("batteryCapacity"), test.capacity},
            {QStringLiteral("batteryIsPowerSupply"), test.supply},
        }));
        if (!object) {
            QTextStream(stderr) << component.errorString();
            return 2;
        }
        const bool row = object->property("healthRowVisible").toBool();
        const bool warning = object->property("isBroken").toBool();
        if (row != test.row || warning != test.warning) {
            ++failures;
            QTextStream(stderr) << "capacity=" << test.capacity << " supply=" << test.supply
                << " row=" << row << " warning=" << warning << '\n';
        }
    }
    QTextStream(stdout) << "battery-health: cases=9 failures=" << failures
                        << " runtime_qt=" << qVersion() << '\n';
    return failures ? 1 : 0;
}
