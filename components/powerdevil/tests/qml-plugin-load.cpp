// Explicit, read-only smoke test for the installed PowerDevil QML modules.
// Registers types without constructing battery/brightness controller objects.
#include <QCoreApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLibrary>
#include <QPluginLoader>
#include <QQmlEngine>
#include <QTextStream>
#include <qqml.h>

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    if (app.arguments() != QStringList{app.arguments().first(), QStringLiteral("--check-installed")}) {
        QTextStream(stderr) << "Use --check-installed to load the installed battery and brightness QML plugins.\n";
        return 2;
    }

    struct Module {
        const char *name;
        const char *library;
        const char *type;
    };
    const Module modules[] = {
        {"org.kde.plasma.private.batterymonitor",
         "/usr/lib64/qt6/qml/org/kde/plasma/private/batterymonitor/libbatterymonitorplugin.so",
         "InhibitionControl"},
        {"org.kde.plasma.private.brightnesscontrolplugin",
         "/usr/lib64/qt6/qml/org/kde/plasma/private/brightnesscontrolplugin/libbrightnesscontrolplugin.so",
         "ScreenBrightnessControl"},
    };
    QJsonArray results;
    bool passed = true;
    for (const auto &module : modules) {
        QPluginLoader loader(QString::fromLatin1(module.library));
        loader.setLoadHints(QLibrary::ResolveAllSymbolsHint | QLibrary::PreventUnloadHint);
        const bool loaded = loader.load();
        // qmlTypeId imports and registers a module if it is not registered yet.
        // Do not call singletonInstance or instantiate these control objects.
        const int typeId = loaded ? qmlTypeId(module.name, 254, 0, module.type) : -1;
        QJsonObject result{
            {"module", QString::fromLatin1(module.name)},
            {"library", QString::fromLatin1(module.library)},
            {"loaded", loaded},
            {"type_registered", typeId >= 0},
        };
        if (!loaded)
            result.insert("error", loader.errorString());
        results.append(result);
        passed = passed && loaded && typeId >= 0;
    }
    QTextStream(stdout) << QJsonDocument(QJsonObject{
        {"status", passed ? "pass" : "fail"},
        {"runtime_qt", QString::fromLatin1(qVersion())},
        {"modules", results},
    }).toJson(QJsonDocument::Compact) << '\n';
    return passed ? 0 : 1;
}
