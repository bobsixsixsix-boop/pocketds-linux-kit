#include <QCoreApplication>
#include <QDBusInterface>
#include <QDBusMessage>
#include <QTextStream>

int main(int argc, char **argv)
{
    QCoreApplication application(argc, argv);
    const QStringList arguments = application.arguments();
    if (arguments.size() != 2) {
        QTextStream(stderr) << "usage: pocketds-panel-client show|hide|toggle\n";
        return 2;
    }

    const QString action = arguments.at(1).toLower();
    QString method;
    if (action == QLatin1String("show")) {
        method = QStringLiteral("Show");
    } else if (action == QLatin1String("hide")) {
        method = QStringLiteral("Hide");
    } else if (action == QLatin1String("toggle")) {
        method = QStringLiteral("Toggle");
    } else {
        QTextStream(stderr) << "unknown action: " << action << '\n';
        return 2;
    }

    QDBusInterface panel(QStringLiteral("org.pocketds.Panel"),
                         QStringLiteral("/org/pocketds/Panel"),
                         QStringLiteral("org.pocketds.Panel"),
                         QDBusConnection::sessionBus());
    panel.setTimeout(3000);
    const QDBusMessage reply = panel.call(method);
    if (reply.type() == QDBusMessage::ErrorMessage) {
        QTextStream(stderr) << reply.errorName() << ": "
                            << reply.errorMessage() << '\n';
        return 1;
    }
    return 0;
}
