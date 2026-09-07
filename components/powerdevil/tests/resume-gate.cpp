/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "pocketds-resume-gate.h"

#include <QCoreApplication>
#include <QDBusError>
#include <QDBusVirtualObject>
#include <QElapsedTimer>
#include <QProcess>
#include <QTimer>
#include <iostream>
#include <stdexcept>
#include <utility>

using namespace PocketDsResumeGate;

static void require(bool condition, const char *description)
{
    if (!condition) {
        throw std::runtime_error(description);
    }
    std::cout << "PASS " << description << '\n';
}

static QDBusMessage replyWith(const QVariant &value)
{
    return QDBusMessage::createMethodCall(QStringLiteral("org.example.Test"), QStringLiteral("/"),
                                          QStringLiteral("org.example.Test"), QStringLiteral("Get"))
        .createReply({QVariant::fromValue(QDBusVariant(value))});
}

class Login1Fixture final : public QDBusVirtualObject
{
public:
    explicit Login1Fixture(QString mode) : m_mode(std::move(mode)) {}

    QString introspect(const QString &) const override { return {}; }

    bool handleMessage(const QDBusMessage &message, const QDBusConnection &connection) override
    {
        if (message.interface() != QStringLiteral("org.freedesktop.DBus.Properties") || message.member() != QStringLiteral("Get")
            || message.arguments() != QVariantList{QStringLiteral("org.freedesktop.login1.Manager"), QStringLiteral("LidClosed")}) {
            connection.send(message.createErrorReply(QDBusError::InvalidArgs, QStringLiteral("Unexpected property request")));
            return true;
        }
        if (m_mode == QStringLiteral("timeout")) {
            message.setDelayedReply(true);
            return true; // Deliberately leave the real D-Bus call unanswered.
        }
        if (m_mode == QStringLiteral("error")) {
            connection.send(message.createErrorReply(QDBusError::AccessDenied, QStringLiteral("Fixture error")));
            return true;
        }
        const QVariant value = m_mode == QStringLiteral("invalid") ? QVariant(QStringLiteral("false")) : QVariant(m_mode == QStringLiteral("closed"));
        connection.send(message.createReply({QVariant::fromValue(QDBusVariant(value))}));
        return true;
    }

private:
    QString m_mode;
};

static int runService(QCoreApplication &app, const QString &mode)
{
    // Tests use a fresh dbus-run-session bus, never the host system bus.
    auto connection = QDBusConnection::sessionBus();
    Login1Fixture object(mode);
    if (!connection.registerVirtualObject(QStringLiteral("/org/freedesktop/login1"), &object)
        || !connection.registerService(QStringLiteral("org.freedesktop.login1"))) {
        return 2;
    }
    QTimer::singleShot(5000, &app, &QCoreApplication::quit);
    std::cout << "READY\n" << std::flush;
    return app.exec();
}

static bool queryFixture(const QString &mode, qint64 *elapsedMs = nullptr)
{
    QProcess service;
    service.setProcessChannelMode(QProcess::ForwardedErrorChannel);
    service.start(QCoreApplication::applicationFilePath(), {QStringLiteral("--service"), mode});
    if (!service.waitForStarted(3000) || !service.waitForReadyRead(3000) || service.readLine().trimmed() != "READY") {
        throw std::runtime_error("Private login1 fixture did not become ready");
    }
    QElapsedTimer elapsed;
    elapsed.start();
    const bool result = mayTurnOn(QByteArrayLiteral("ayaneo,pocketds\0qcom,sm8550\0"), QDBusConnection::sessionBus());
    if (elapsedMs) {
        *elapsedMs = elapsed.elapsed();
    }
    service.terminate();
    if (!service.waitForFinished(1000)) {
        service.kill();
        service.waitForFinished(1000);
    }
    return result;
}

int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    if (app.arguments().size() == 3 && app.arguments().at(1) == QStringLiteral("--service")) {
        return runService(app, app.arguments().at(2));
    }
    try {
        require(isPocketDs(QByteArrayLiteral("ayaneo,pocketds\0")), "exact compatible token matches");
        require(isPocketDs(QByteArrayLiteral("qcom,sm8550\0ayaneo,pocketds\0")), "compatible token may follow another token");
        require(!isPocketDs(QByteArrayLiteral("ayaneo,pocketds-pro\0")), "compatible suffix does not match");
        require(!isPocketDs(QByteArrayLiteral("prefix-ayaneo,pocketds\0")), "compatible prefix does not match");
        require(!isPocketDs(QByteArrayLiteral("ayaneo,pocketds")), "unterminated compatible is not a token");
        require(!isPocketDs(QByteArrayLiteral("ayaneo,pocketds\0") + QByteArray(4096, '\0')), "oversize compatible is rejected");
        require(confirmsOpenLid(replyWith(false)), "variant bool false confirms open");
        require(!confirmsOpenLid(replyWith(true)), "variant bool true does not permit On");
        require(!confirmsOpenLid(replyWith(QStringLiteral("false"))), "string false does not permit On");
        require(!confirmsOpenLid(replyWith(0)), "integer zero does not permit On");
        require(!confirmsOpenLid(replyWith(QVariant())), "invalid variant does not permit On");
        require(!confirmsOpenLid(QDBusMessage::createError(QDBusError::AccessDenied, QStringLiteral("denied"))), "error does not permit On");
        require(!confirmsOpenLid(QDBusMessage::createError(QDBusError::NoReply, QStringLiteral("timeout"))), "NoReply does not permit On");
        QDBusMessage malformed = replyWith(false);
        malformed.setArguments({false});
        require(!confirmsOpenLid(malformed), "unwrapped bool does not permit On");
        malformed.setArguments({});
        require(!confirmsOpenLid(malformed), "empty reply does not permit On");
        malformed.setArguments({QVariant::fromValue(QDBusVariant(false)), QVariant::fromValue(QDBusVariant(false))});
        require(!confirmsOpenLid(malformed), "extra reply arguments do not permit On");
        require(!confirmsOpenLid(QDBusMessage()), "invalid message does not permit On");

        const QDBusConnection disconnected(QStringLiteral("pocketds-fixture-disconnected"));
        require(mayTurnOn({}, disconnected), "absent machine identity keeps upstream behavior without querying D-Bus");
        require(mayTurnOn(QByteArrayLiteral("other,machine\0"), disconnected), "other machine keeps upstream behavior without querying D-Bus");
        require(!mayTurnOn(QByteArrayLiteral("ayaneo,pocketds\0"), disconnected), "Pocket DS disconnected bus keeps display off");

        require(QDBusConnection::sessionBus().isConnected(), "private session bus is available");
        require(queryFixture(QStringLiteral("open")), "real Properties.Get open reply permits On");
        require(!queryFixture(QStringLiteral("closed")), "real Properties.Get closed reply blocks On");
        require(!queryFixture(QStringLiteral("invalid")), "real Properties.Get wrong type blocks On");
        require(!queryFixture(QStringLiteral("error")), "real Properties.Get error blocks On");
        qint64 elapsedMs = 0;
        require(!queryFixture(QStringLiteral("timeout"), &elapsedMs), "unanswered real Properties.Get blocks On");
        // The call requests exactly 1000 ms; allow scheduling overhead in the fixture.
        require(lidQueryTimeoutMs == 1000 && elapsedMs >= 800 && elapsedMs < 2500, "real D-Bus timeout stays bounded near one second");
        std::cout << "Timeout elapsed: " << elapsedMs << " ms\n";
    } catch (const std::exception &error) {
        std::cerr << "FAIL " << error.what() << '\n';
        return 1;
    }
    return 0;
}
