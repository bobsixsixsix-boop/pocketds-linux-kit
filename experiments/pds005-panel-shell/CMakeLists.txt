cmake_minimum_required(VERSION 3.22)

project(pocketds-panel-shell-experiment VERSION 0.1 LANGUAGES CXX)

find_package(Qt6 6.7 REQUIRED COMPONENTS Core DBus Gui Qml Quick)
qt_standard_project_setup(REQUIRES 6.7)

qt_add_executable(pocketds-panel-shell
    src/main.cpp
)
qt_add_qml_module(pocketds-panel-shell
    URI org.pocketds.panel
    VERSION 1.0
    QML_FILES qml/Main.qml
)
target_compile_features(pocketds-panel-shell PRIVATE cxx_std_20)
target_link_libraries(pocketds-panel-shell PRIVATE
    Qt6::Core Qt6::DBus Qt6::Gui Qt6::Qml Qt6::Quick
)

qt_add_executable(pocketds-panel-client src/panel-client.cpp)
target_compile_features(pocketds-panel-client PRIVATE cxx_std_20)
target_link_libraries(pocketds-panel-client PRIVATE Qt6::Core Qt6::DBus)

install(TARGETS pocketds-panel-shell pocketds-panel-client
    RUNTIME DESTINATION libexec
)
install(FILES systemd/pocketds-panel-shell.service
    DESTINATION lib/systemd/user
)
install(FILES dbus/org.pocketds.Panel.service
    DESTINATION share/dbus-1/services
)
install(FILES desktop/org.pocketds.Panel.desktop
    DESTINATION share/applications
)
