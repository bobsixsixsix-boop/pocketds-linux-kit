# Fcitx 开机 Wayland 诊断

2026-09-07，实机 Fcitx 5.1.21 在每次 Plasma Wayland 登录约十秒后报告：原生 Wayland 输入法已工作，但同时设置了 `GTK_IM_MODULE=fcitx` 和 `QT_IM_MODULE=fcitx`。这条建议适用于当前设备；不是输入法故障的泛化猜测，也不应仅屏蔽通知。[Fcitx KDE Plasma 指南](https://fcitx-im.org/wiki/Using_Fcitx_5_on_Wayland/en#KDE_Plasma)。

## 实机证据

修复前 boot `9d15fb4c-b3bb-4bca-bc29-58877852e0fd`：

- 实际 KWin DBus 进程 PID 1356 启动 Fcitx PID 1412，后者具有 `WAYLAND_SOCKET`，日志确认原生输入协议为 1。
- Fedora `fcitx5-autostart` 提供的 `/etc/profile.d/fcitx5.sh` 设置了上述两个全局变量。KWin、Plasma、Fcitx 和用户服务管理器均继承了它们；`QT_IM_MODULES`、`SDL_IM_MODULE` 未设置。
- 15:59:52，XDG 自启动又运行 Fcitx PID 1942，因 DBus 名已占用而退出。15:59:55，KWin 管理的实例记录污染变量并触发诊断。
- 当前未安装 `fcitx5-qt6`，强制选择不存在的 Qt 输入模块还可能妨碍原生 Wayland 中文输入。保留现有 Qt 平台包，不安装 ABI 不兼容的模块来掩盖配置问题。

## 修复

`89ab7a4` 增加用户级 Plasma 登录脚本，仅在 Wayland 中导出空的 GTK、Qt 输入模块变量，保留 `XMODIFIERS=@im=fcitx`。这里必须显式赋空值：Plasma 在子 shell 中执行脚本，再导入仍存在的键；仅 `unset` 不会删除父进程原有值。[Plasma 6.7.3 导入实现](https://github.com/KDE/plasma-workspace/blob/v6.7.3/startkde/startplasma.cpp#L156)。

用户级 XDG `Hidden=true` 覆盖阻止重复启动；KWin 仍使用系统 applications 目录中的原输入法入口。GTK3 的 `settings.ini` 增加单个 `gtk-im-module=fcitx` 键，供 X11 兼容路径使用，其他设置保留。SDL 环境完全不变：SDL2 区别未设置与空字符串，不能把 GTK、Qt 的做法套用给它。[SDL2 实现](https://github.com/libsdl-org/SDL/blob/release-2.32.10/src/core/linux/SDL_ime.c#L57)。

两个安装器同步部署该策略。中文安装器不再独立启动或替换 Fcitx，只重载经过 DBus 归属、父进程和 Wayland 环境核对的 KWin 子进程；旧会话不满足条件时明确记录 `pending_session`。导入工具在修改源码前拒绝回灌强制 GTK、Qt 全局变量。用户词库、Rime 配置、键盘和语音服务不变。

## 验证与恢复

安装策略、实际 shell 环境导入、X11/SDL 保持、进程归属与 pending 状态测试通过；相关安装升级、资源预检和权限顺序测试通过。实机 Fedora 全量 lint 通过。部署后重复的 XDG 自启动 unit 为 `not-found`，原 KWin 输入法进程继续运行。

16:37 JST，专属窗口在显式 `GTK_IM_MODULE=''`、`QT_IM_MODULE=''` 环境下，分别用 `nihao` 与空格实际输入“你好”，三项通过：

| 程序路径 | 实际观测 | 结果 |
|---|---|---|
| GTK3 原生 Wayland | `GdkWaylandDisplay`；GTK 自动选择 `wayland`；有拼音预编辑事件 | 正文 `你好` |
| GTK3 XWayland | `GdkX11Display`；GTK 选择 `fcitx`；有拼音预编辑事件 | 正文 `你好` |
| Qt6 原生 Wayland Konsole | 专属进程加载 `libqwayland.so`；输入接收程序父链属于该 Konsole | 接收到完整行 `你好` |

所有键事件仅在专属 PID、窗口 ID 与焦点一致时发送，不使用剪贴板或直接设置文本。首轮测试在临时输入设备创建仅 400 ms 后发送首键，收到 `ihao`，未验收通过；增加设备发现等待至 1.5 秒并保持按键 40 ms 后，第二轮三项均通过。保留首轮原始回执，不将它冒充产品输入验收。第二轮 `input-before-login-02` 是上述表格的依据。

## 重启后验收：通过

2026-09-07 19:07 JST，用户明确要求重启后，执行一次正常重启。请求后 **34.774 秒**首次读到新 boot `0be23752-835e-4bc0-8e23-6010b6ec1e78`，系统 running，桌面与键盘 active。用户确认“没有再弹，桌面也正常”。

新会话的实际 KWin PID 1440、Plasma PID 1565、Fcitx PID 1495 与用户服务管理器均继承空的 GTK/QT 变量，`QT_IM_MODULES`、`SDL_IM_MODULE` 仍未设置，`XMODIFIERS` 保留。Fcitx 是 KWin 的直接子进程并具有 `WAYLAND_SOCKET`，只有这一份进程；重复的 XDG 自启动 unit 不存在，新 boot 日志没有第二份实例争抢 DBus。

19:07:39，同一 Fcitx PID 实际执行了自检，日志明确记录原生输入协议为 1、GTK/QT 值为空。这证明诊断条件已消除，并非只凭“没有日志”判断。未设置禁用自诊断的用户覆盖。

重启后 GTK3 Wayland、GTK3 XWayland 均再次通过实际拼音输入“你好”。首次 Konsole 验收在输入期间被 `xwaylandvideobridge` 取得焦点，测试立即停止，不记为通过；仅重新运行独立 Konsole 验收后，原生 Qt Wayland 也收到完整的“你好”行，未为通过测试更改产品配置。输入证据为 `input-after-login-01` 的两项 GTK 回执和 `input-after-login-qt-02` 的 Qt 回执。

KWin、Plasma、键盘服务均 active、NRestarts=0。汇总为 `verification-after-login.json`；此次正常重启通过不意味着 PDS-056 的历史晚期关机停顿根因已经定位。

原文件备份与原始记录保存在设备 `~/.local/state/pocketds-linux-kit/fcitx-wayland-20260907/`；维护工作区为 `outputs/fcitx-wayland-20260907/`。备份清单记录原文件是否存在、权限和 SHA-256。回退时恢复 GTK 设置备份，移除本轮新增的 Plasma 环境脚本和 XDG 用户覆盖，再重新登录；不修改系统包文件或禁用 Fcitx 自诊断。

验证范围针对本机 Plasma Wayland、系统 GTK3 和 Qt6。未据此宣称所有自带 Qt/XCB 的第三方程序或另一种桌面会话均已覆盖。
