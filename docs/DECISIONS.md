# 工程决策记录

## ADR-001：设备仓库是事实源

状态：接受。Pocket DS 上的 `~/Projects/pocketds-linux-kit` 是当前权威工作树。
外部副本只能在确认设备工作树状态后同步，禁止用旧副本覆盖未提交现场改动。

## ADR-002：默认测试必须只读

状态：接受。`make test`、`make check`、`make test-hardware` 不改变亮度、输入模式、
音频、休眠或服务状态。破坏性/状态性测试使用单独入口和显式参数。

## ADR-003：遥测不伪造

状态：接受。频率不是利用率，刷新率不是 FPS，sysfs 原始数值在单位未验证前不能
直接展示。不可用字段显示未知并注明来源。

## ADR-004：DSI 亮度直接走 kernel backlight

状态：接受。当前镜像不使用 `kscreen-doctor` 写 DSI-2；所有值经受限 helper
校验后写独立 backlight。后续持久化也复用这一层。

## ADR-005：休眠安全优先于交互完整

状态：暂时接受。s2idle 和认证路径未可靠前，盒盖/电源键允许被安全忽略。该状态
必须在 UI 和项目状态中明确，不能被误称为“休眠已完成”。

## ADR-006：Panel 与可信锁屏边界分离

状态：接受。普通 GTK/Plasma 桌面组件不能作为 KScreenLocker 的可信输入。锁屏
键盘、桌面键盘和 Panel 内输入必须分别建模和测试。

## ADR-007：Android 发行目标独立规划

状态：接受。当前开发机 Linux-only 不代表发行镜像可以删除 Android。双系统设计
必须在分区/镜像副本中验证，并受单独的高风险操作批准约束。

## ADR-008：异刷新率不是待绕开的硬件限制

状态：接受。DRM/KWin 已验证 DSI-1 165 Hz 与 DSI-2 59.999 Hz 同时运行。浏览器
60 FPS 不能推导为硬件限制；先调查呈现节奏、XWayland/合成和 atomic commit
错误，再考虑牺牲上屏刷新率。

## ADR-009：手机互联分层而不是选一个万能工具

状态：被 ADR-011 修订。KDE Connect 作为局域网默认桌面互联，Tailscale 仅作为用户
主动启用的跨网远程接入。原先的 LocalSend 可选项因安全公告暂缓。

## ADR-010：下屏 UI 以运行时几何和高频无动效为准

状态：接受。Panel 以 `screenGeometry` 绘制；桌面键盘从新鲜 DSI-2 telemetry 取得
逻辑几何，只在异常时回退已验证的 819×614。键盘输入和两秒遥测刷新不播放动画；
Panel 低频按钮只保留 100-110 ms 的按压反馈。具体 token、触点和验收见
`UI-DESIGN.md`。

## ADR-011：LocalSend 在上游解决发现冒充前不进入固件

状态：接受；2026-08-29 复核不变。LocalSend 官方仓库的
CVE-2025-54792/GHSA-424h-5f6m-x63f 公告仍将 `<=1.17.0` 列为受影响且没有
patched version；新增的 Web Share 文件名 XSS（GHSA-34v6-52hh-x4r4）也对
`<=1.17.0` 无已列修复版本。HTTPS 传输不能修复未认证 UDP discovery 导致的同网设备冒充。
因此固件不安装、不开放 53317/tcp+udp，也不把它宣传为安全的大文件默认路径。
只有上游发布明确修复版本、固定来源并通过对抗发现/指纹验收后才重新评估。
KDE Connect 继续只在显式绑定的 `pocketds-home` 可信区开放；Tailscale 涉及常驻
daemon、TUN 和账户登录，仍只由用户主动安装、认证和启用。

## ADR-012：电池容量修复保留 charge 语义并独立验收

状态：接受。SM8550 的 `BATT_CHG_FULL*` 按 Qualcomm Android 协议映射为
`CHARGE_FULL*`；不得因 Linux 结构体默认成 mWh 就把同一数值重标为 energy，也不得
从型号或瞬时 `charge_counter` 伪造满充容量。PDS-017 只在 SM8350-style 分支初始化
mAh，作为已过 ARM64 driver object、Image、modules、DTB 源码编译但未进入发行打包/
启动的独立候选；不得与 PDS-002 IFPC 回退合并后做因果验收。
已安装 UPower 1.91.3 的 charge→energy 源码路径已锁定；由于设备缺少设计电压，
它会回退实时电压。真机确认单位、EnergyFull 漂移、ETA 和充放电矩阵前，预计时间
继续显示未知。

## ADR-013：双系统切换只改经验证的 ABL BootMode

状态：接受。Linux 与 Android 不重写 boot、super、userdata、GPT 或整块
`devinfo`，也不把启动源与目标系统混为一项。切换器只接受 AYANEO Pocket DS 上
与已备份 ROCKNIX ABL v1.1.8 模板一致的 4096 字节 `devinfo`，归一化比较时仅允许
`BootMode` 和 `BootSourceMode` 两个已知字节变化，实际写入只修改 `BootMode`，原样
保留 `BootSourceMode`。每次写入前保存完整 preimage，写后整块回读；任何机型、
大小、模板、模式或回读不一致均失败关闭且不重启。Linux 端需要 Panel 对话框二次
确认；Android 端只经 AYANEO 提供的 root 脚本入口执行。
