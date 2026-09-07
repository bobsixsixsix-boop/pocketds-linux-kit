# Wiliwili 输入路由与 GLFW 映射（2026-08-29）

## 结论

这是两个独立现象，不应通过再安装一个常驻映射守护进程来掩盖：

1. `joymouse` 是桌面模式。它把实体面键转换为 Enter、Escape 等键盘事件，
   Wiliwili/Borealis 再把 Enter/Escape 解释为确认/返回，因此应用内显示为 A/B。
   这不是可供游戏使用的原生手柄映射。
2. `gamepad` 模式创建的 InputPlumber Xbox Elite 2 虚拟设备可被 Flatpak
   沙箱内 SDL2 正确识别；但正式 Wiliwili 1.6.0 使用 GLFW，运行日志中的 GUID
   `030000005e040000000b000001000000` 不在它的内置数据库，因而只被当作 joystick，
   `glfwGetGamepadState` 无法提供标准 gamepad 状态。

Wiliwili 官方支持在配置目录放置 `gamecontrollerdb.txt`。仓库现在跟踪该精确 GUID
的标准 Xbox 映射。映射、共享会话管理器、三份 launcher、InputPlumber helper/profile
和两个桌面入口由 `install-game-input-stack.py` 作为同一代事务合并安装；已有的其他
映射会被保留。旧的四个局部安装入口只会转交完整事务，不能再混装协议代际。

## 运行时约束

- Wiliwili 需要重新启动一次才能加载新增数据库。
- Borealis 的 GLFW 后端在窗口失焦时不处理手柄状态。从下屏 Panel 切换模式可能
  让 Panel 获得焦点，所以可靠入口应在启动 Wiliwili 前切换到 `gamepad`，应用退出
  后恢复原模式；Panel 路径还应恢复先前上屏窗口焦点。
- SDL2 与 GLFW 在此设备上报告不同格式的 GUID。SDL2 探针 PASS 只能证明虚拟设备
  和 Flatpak 设备权限正常，不能证明 Wiliwili 的 GLFW 映射存在。

## 会话与恢复边界

- Guide 键、Panel 和游戏启动器只通过 root canonical helper 写 InputPlumber。
  `/run/pocketds-input-mode.lock` 串行化完整 profile/target 事务；同目录的状态记录每次
  变更都换随机 UUID，解决值回到原样时旧恢复仍误写的 ABA 问题。
- Wiliwili、ES-DE、RetroArch 共用一个 Python 会话管理器和
  `$XDG_RUNTIME_DIR/pocketds-game-session.lock`。launcher 只做应用专用参数/文件预检，
  不再各自实现模式切换、信号和恢复。
- 管理器在第一次 D-Bus 写入前取得 `pds-input-v1 PREVIOUS TOKEN` receipt，并在启动
  backend 或切性能档之前持久化。退出和 stale recovery 都使用幂等 `recover-token`；
  Guide/Panel 的后续选择会换 token，旧会话不能覆盖。若恢复被强杀，原 token 会保持
  可重试，只有输入和性能档均恢复成功后才删除 journal。
- 原生 ES-DE/RetroArch 位于精确的 transient user scope，清空 cgroup 后才恢复输入；
  Wiliwili 通过 Flatpak 官方 `--instance-id-fd` receipt 绑定唯一 numeric instance，并只
  终止该实例。Flatpak 1.18 的 `ps` 可能返回 `pid=0`，PID 只作可用时的交叉核对，不作
  身份依据；任一次实例查询歧义都会锁存为不确定，本轮不得恢复输入或删除 journal。
- ES-DE 启动的 RetroArch 用父 scope 内的随机 token 加入同一会话，不重复抢锁或重复
  保存/恢复输入；scope 外伪造 token 会失败关闭。性能档只由独立 RetroArch 会话切换，
  以 journal 和 compare-before-restore 恢复；嵌套 join 明确拒绝 `--performance`。

部署状态和真机自动验收写入 `CURRENT-STATE.md`；实体 A/B/X/Y、肩键和扳机始终单列为
attended gate，不能由 mock、SDL2 或 GLFW 识别日志代替。

## 长期架构选择

InputPlumber 继续作为唯一的系统输入路由器：设备能力修正、虚拟目标和逐场景 profile
都属于这一层。OpenGamepadUI 与 InputPlumber 原生集成并提供逐游戏 profile，但上游
仍明确标注为早期开发，应先做可卸载 PoC，不作为当前日用固件的关键依赖。
AntiMicroX、input-remapper、HHD 等第二套 grab/remap 服务与 InputPlumber 常驻叠加会
增加双输入、抢设备和恢复顺序问题，不用于修复本故障。

真机当前 InputPlumber 为 0.75.2。上游 0.78.1 已提供 aarch64 RPM，并含卡键/target
顺序相关修复，但升级属于另一项可回滚验证，不混入本次 Wiliwili 修复。HHD 官方设备表
没有 Pocket DS；Steam 官方 Linux 客户端仍以 x86_64/AMD64 为支持边界；AntiMicroX
和 input-remapper 只保留为单应用/被明确排除的外接设备兼容工具。

## 发行前验证

1. 日志不再出现缺少自定义 gamepad database，GLFW 将虚拟设备识别为 gamepad。
2. 在 Wiliwili 启动前进入 `gamepad`，真人检查 A/B/X/Y、十字键、双摇杆、肩键和扳机。
3. 验证启动器退出后准确恢复原 `joymouse`/`gamepad`；运行时再按 Guide 作出的新选择
   不得被退出中的旧 launcher 覆盖。
4. 重复点击入口、启动器并发、`flatpak ps` 失败、切换途中退出、应用忽略退出信号、
   Flatpak PID/instance 歧义和原生 scope 客户端先退均应失败关闭，不得遗留错误模式、
   性能档或孤儿进程。
5. 单独验证运行中热插拔，以及从下屏 Panel 切换后的焦点恢复；两者未通过前不宣称
   任意时刻热切换可靠。

## 固定证据与上游依据

- 现场探针日志：`/home/pocketds/.local/state/pocketds-linux-kit/wiliwili-input-probe-20260829.log`
  （0600；只在设备上保存）。其中正式 Flatpak 先报告 `Using platform GLFW`，随后
  报告加载自定义数据库及 `joystick 0 is gamepad: "InputPlumber Xbox Elite 2"`。
- Wiliwili 正式版：v1.6.0，仓库提交 `88e5876`；其 Borealis gitlink 为 `5f08b286`。
- [Wiliwili 官方自定义手柄说明](https://github.com/xfangfang/wiliwili/wiki#自定义手柄支持)
- [Borealis 固定版本 GLFW 输入实现](https://github.com/xfangfang/borealis/blob/5f08b286f3df737f3321d2247a6fe633fcead03c/library/lib/platforms/glfw/glfw_input.cpp)
- [InputPlumber 固定版本 Xbox 虚拟设备标识](https://github.com/ShadowBlip/InputPlumber/blob/23f84b78521a4e12f2ff7b16fa9e0dc03ccb1846/src/input/target/xpad.rs#L91-L113)
- [GLFW 官方 gamepad mapping 说明](https://github.com/glfw/glfw/blob/master/docs/input.md#gamepad-input)
- [InputPlumber 官方 profile/DBus 用法](https://github.com/ShadowBlip/InputPlumber/blob/main/docs/usage.md)
- [Flatpak 1.18 instance-id-fd 官方实现](https://github.com/flatpak/flatpak/blob/1.18.1/common/flatpak-run.c#L1713-L1737)
- [InputPlumber v0.78.1](https://github.com/ShadowBlip/InputPlumber/releases/tag/v0.78.1)
- [OpenGamepadUI 官方状态与 InputPlumber 集成](https://github.com/ShadowBlip/OpenGamepadUI)
- [HHD 官方支持设备表](https://github.com/hhd-dev/hhd#supported-devices)
- [input-remapper Fedora 包与 evdev 工作方式](https://packages.fedoraproject.org/pkgs/input-remapper/input-remapper/)
- [AntiMicroX 上游能力与 Wayland 边界](https://github.com/AntiMicroX/antimicrox)
- [Valve Steam Linux 架构要求](https://github.com/ValveSoftware/steam-for-linux#hardware-and-software-requirements)
