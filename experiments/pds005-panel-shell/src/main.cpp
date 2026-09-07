#include <QCoreApplication>
#include <QDBusConnection>
#include <QGuiApplication>
#include <QObject>
#include <QPointer>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QScreen>

class PanelController final : public QObject
{
    Q_OBJECT
    Q_CLASSINFO("D-Bus Interface", "org.pocketds.Panel")
    Q_PROPERTY(QScreen *lowerScreen READ lowerScreen NOTIFY lowerScreenChanged)
    Q_PROPERTY(bool lowerScreenAvailable READ lowerScreenAvailable NOTIFY lowerScreenChanged)
    Q_PROPERTY(bool panelVisible READ panelVisible NOTIFY panelVisibleChanged)

public:
    explicit PanelController(QObject *parent = nullptr)
        : QObject(parent)
    {
        for (QScreen *screen : qGuiApp->screens()) {
            watchScreen(screen);
        }
        connect(qGuiApp, &QGuiApplication::screenAdded, this,
                [this](QScreen *screen) {
                    watchScreen(screen);
                    refreshLowerScreen();
                });
        connect(qGuiApp, &QGuiApplication::screenRemoved, this,
                [this](QScreen *) { refreshLowerScreen(); });
        refreshLowerScreen();
    }

    QScreen *lowerScreen() const { return m_lowerScreen; }
    bool lowerScreenAvailable() const { return !m_lowerScreen.isNull(); }
    bool panelVisible() const { return m_panelVisible; }

public Q_SLOTS:
    Q_SCRIPTABLE void Show() { setPanelVisible(true); }
    Q_SCRIPTABLE void Hide() { setPanelVisible(false); }
    Q_SCRIPTABLE void Toggle() { setPanelVisible(!m_panelVisible); }

Q_SIGNALS:
    void lowerScreenChanged();
    void panelVisibleChanged();

private:
    void watchScreen(QScreen *screen)
    {
        connect(screen, &QScreen::geometryChanged, this,
                [this] { Q_EMIT lowerScreenChanged(); });
        connect(screen, &QScreen::availableGeometryChanged, this,
                [this] { Q_EMIT lowerScreenChanged(); });
    }

    void refreshLowerScreen()
    {
        QScreen *found = nullptr;
        for (QScreen *screen : qGuiApp->screens()) {
            // Never fall back to the upper screen. If this connector is absent,
            // QML remains hidden until the exact lower output reappears.
            if (screen->name() == QLatin1String("DSI-2")) {
                found = screen;
                break;
            }
        }
        if (found == m_lowerScreen) {
            return;
        }
        m_lowerScreen = found;
        Q_EMIT lowerScreenChanged();
    }

    void setPanelVisible(bool visible)
    {
        if (m_panelVisible == visible) {
            return;
        }
        m_panelVisible = visible;
        Q_EMIT panelVisibleChanged();
    }

    QPointer<QScreen> m_lowerScreen;
    bool m_panelVisible = true;
};

int main(int argc, char **argv)
{
    QGuiApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("pocketds-panel-shell"));

    PanelController controller;
    QDBusConnection bus = QDBusConnection::sessionBus();
    if (!bus.registerService(QStringLiteral("org.pocketds.Panel"))) {
        qCritical("Unable to own org.pocketds.Panel: %s",
                  qPrintable(bus.lastError().message()));
        return 2;
    }
    if (!bus.registerObject(
            QStringLiteral("/org/pocketds/Panel"), &controller,
            QDBusConnection::ExportScriptableSlots
                | QDBusConnection::ExportScriptableSignals
                | QDBusConnection::ExportScriptableProperties)) {
        qCritical("Unable to export org.pocketds.Panel: %s",
                  qPrintable(bus.lastError().message()));
        return 3;
    }

    QQmlApplicationEngine engine;
    engine.rootContext()->setContextProperty(QStringLiteral("panelController"),
                                             &controller);
    QObject::connect(&engine, &QQmlApplicationEngine::objectCreationFailed,
                     &application, [] { QCoreApplication::exit(4); },
                     Qt::QueuedConnection);
    engine.loadFromModule(QStringLiteral("org.pocketds.panel"),
                          QStringLiteral("Main"));
    return application.exec();
}

#include "main.moc"
