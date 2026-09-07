# PDS-004：隔离 XWayland 故障验收设计

日期：2026-08-28

## 结论

跨 X11 应用的“显示服务器故障”不能在日用 KWin/Xwayland 上注入。当前实现采用
设备已有的 KWin virtual backend 创建独立 compositor、独立 DBus session 和私有
XDG 目录，再由其 rootless Xwayland 运行一个无输入能力的 GTK3 probe。只有嵌套
Xwayland 会收到 SIGTERM；宿主 KWin/Xwayland 只做前后身份比较。

源码、16 项 fixture/static 测试和默认只读计划已完成。没有安装软件包、没有创建
嵌套图形会话、没有部署 live 文件；正式 3 轮仍为 **NOT RUN**，须等 PDS-008 24 小时
soak 结束。

## 上游依据

- [KWin 官方 GitHub 镜像](https://github.com/KDE/kwin)将 KWin 定义为 Wayland
  compositor/window manager。
- [KWin Wayland wrapper](https://github.com/KDE/kwin/blob/master/src/helpers/wayland_wrapper/kwin_wrapper.cpp)
  说明非零 compositor 退出的包装行为；因此故障测试必须隔离于宿主 wrapper。
- 设备上 `kwin_wayland --help` 明确提供 `--virtual`、`--xwayland`、`--socket`、
  `--width`、`--height`、`--scale`、`--no-lockscreen`、`--no-global-shortcuts`、
  `--no-kactivities` 和 `--exit-with-session`。实现只使用这些现场存在的参数。
- [GTK 3 运行时文档](https://docs.gtk.org/gtk3/running.html)说明可用
  `GDK_BACKEND=x11` 固定 backend；[GdkX11Display](https://docs.gtk.org/gdk3-x11/class.X11Display.html)
  是 X11 display 类型。X11 display 断开时 client 退出是被测故障结果，不把“不重启
  进程而原地重连”写成虚假要求。

## 设备基线

只读盘点得到：

- KWin 6.7.4，Fedora `kwin-6.7.4-2.fc44.aarch64`，设备文件 SHA-256
  `0a71f45484a97fecd44679baef3ef921c7cfc07fe0557fb63c7e0bb2a918f111`；
- Xwayland 24.1.13，Fedora `xorg-x11-server-Xwayland-24.1.13-1.fc44.aarch64`，设备
  文件 SHA-256 `8daa20f90ed3e34a9dd4868bd849ce41010e8e1a37f5e07beb053523a6db5cdd`；
- `/usr/bin/dbus-run-session`、`/usr/bin/systemctl` 与 `/usr/bin/timeout` 已存在；
- Weston、Xvfb、Xephyr、cage、labwc 均不存在，因此不为本测试引入新 compositor；
- GTK3 3.24.52、python3-gobject 3.56.3 已存在。

这些是现场只读观测，不是仓库对未来发行镜像的包依赖锁。

## 安全边界

默认入口只输出 PLAN。真正执行同时要求：

1. `--execute`；
2. 精确确认词 `POCKETDS-RUN-NESTED-XWAYLAND-FAILURE`；
3. 3～5 轮和一个尚不存在的输出路径。

执行前对五个固定 executable 做 owner/mode/link/大小/内容哈希验证。每轮均使用
`start_new_session=True`、私有 mode-0700 HOME/XDG 目录、固定 1024×768 virtual
backend 与 `GDK_BACKEND=x11`。probe 不可聚焦、不接收输入、不访问网络，只写一个
mode-0600、`O_EXCL` 的固定 schema readiness 文件。

寻找 nested KWin/Xwayland/probe 时同时要求 descendant、同一 session、当前 UID、
精确 executable 或 argv token、精确私有 `XDG_RUNTIME_DIR`。发信号前再次比较 PID、
session、starttime、UID、executable 和 runtime；缺失、重复、PID 重用或身份漂移均
失败关闭。清理只触及同一私有 session/runtime 的进程，并容忍目标在重验后自然退出。

最终报告为 mode-0600、拒绝覆盖，不记录 PID、路径、时间戳、应用名或 display 名。
PASS 需要至少 3 轮全部完成，并且宿主 KWin/Xwayland 集合及键盘、GPU 遥测、
InputPlumber 的 ActiveState/SubState/MainPID/NRestarts/InvocationID 前后完全一致。

## 尚未声称

- 尚未运行真实嵌套 KWin/Xwayland；
- 尚未证明 GTK3 client 能在同一进程内重连，这也不是目标；
- 尚未完成 UI 台账中的跨应用、触摸或实体肩键轮次；
- 尚未部署六文件 UI 事务，也未重启/重载任何桌面服务。

## 2026-08-28 源码验证记录

- 本地实现提交：`78d1bdbb9f3ac219a38ae7f8bd5b87b4f4f2b895`；
- 设备对应提交：`f8d0824acded96cf21bf74730f6b5d4ec402c2cc`；
- 两端精确 tree：`6c9865f74b50dae2dedac6f32a39012e92828df6`；
- 两端目标测试 16/16、lint、全量 `make test` 均 PASS；
- 设备默认 PLAN：`safe_to_execute=true`、固定 executable 5 项，明确为零信号、零输入
  注入、零服务重启、零网络；未提供 `--execute`；
- 验证前后 PDS-008 soak 均保持 `active/running`、同一 PID 162791、同一 InvocationID
  `e6d19f0a75fe4c54ad46c8eb2d9a7faa`、`NRestarts=0`；
- UI transaction root 在验证前后均不存在。

上述 PID/InvocationID 只保存在开发证据文档中；将来的机器报告仍按 schema 不输出
这些现场标识。真实故障轮次仍为 **NOT RUN**。
