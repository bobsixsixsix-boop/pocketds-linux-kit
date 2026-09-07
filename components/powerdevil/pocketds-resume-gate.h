/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#pragma once

#include <QByteArray>
#include <QDBusConnection>
#include <QDBusMessage>
#include <QDBusVariant>
#include <QFile>
#include <QMetaType>

namespace PocketDsResumeGate
{
inline constexpr int lidQueryTimeoutMs = 1000;
inline constexpr qint64 maximumCompatibleBytes = 4096;

inline bool isPocketDs(const QByteArray &compatible)
{
    // Device-tree compatible is a list of complete, NUL-terminated strings.
    return !compatible.isEmpty() && compatible.size() <= maximumCompatibleBytes && compatible.endsWith('\0')
        && compatible.split('\0').contains(QByteArrayLiteral("ayaneo,pocketds"));
}

inline bool confirmsOpenLid(const QDBusMessage &reply)
{
    if (reply.type() != QDBusMessage::ReplyMessage || reply.arguments().size() != 1) {
        return false;
    }
    const QVariant argument = reply.arguments().constFirst();
    if (argument.metaType() != QMetaType::fromType<QDBusVariant>()) {
        return false;
    }
    const QVariant value = qvariant_cast<QDBusVariant>(argument).variant();
    return value.metaType() == QMetaType::fromType<bool>() && !value.toBool();
}

inline bool mayTurnOn(const QByteArray &compatible, const QDBusConnection &connection)
{
    if (!isPocketDs(compatible)) {
        return true;
    }
    QDBusMessage request = QDBusMessage::createMethodCall(QStringLiteral("org.freedesktop.login1"),
                                                        QStringLiteral("/org/freedesktop/login1"),
                                                        QStringLiteral("org.freedesktop.DBus.Properties"),
                                                        QStringLiteral("Get"));
    request.setArguments({QStringLiteral("org.freedesktop.login1.Manager"), QStringLiteral("LidClosed")});
    // Do not dispatch a late async reply after a subsequent lid/sleep transition.
    // Unknown, malformed, and timed-out replies all leave the display off.
    return confirmsOpenLid(connection.call(request, QDBus::Block, lidQueryTimeoutMs));
}

inline bool mayTurnOn()
{
    QFile compatible(QStringLiteral("/proc/device-tree/compatible"));
    const QByteArray identity = compatible.open(QIODevice::ReadOnly) ? compatible.read(maximumCompatibleBytes + 1) : QByteArray();
    if (!isPocketDs(identity)) {
        return true;
    }
    return mayTurnOn(identity, QDBusConnection::systemBus());
}
}
