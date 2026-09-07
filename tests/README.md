# Tests

默认测试只读，不改变设备状态。`suspend-preflight.sh` 永久只有只读检查，没有
`--execute`、环境变量或直接 `systemctl suspend` 后门。真正的单次 suspend 只允许走
`pds001-deep-cycle.py → pocketds-panel-root → pocketds-deep-suspend` 固定链路，并且仍需
显式双确认、root guard、RTC 救援和人在设备旁。当前 `AllowSuspend=no` 会在 RTC arm
和 D-Bus 请求之前安全拒绝执行。

测试失败应保留命令输出和对应 Git commit。不要把 ROM、账户信息或原始网络配置
放入测试 fixture。

`touchpad-gestures.py`、`touchpad-raw.py`、`touchpad-reader-lifecycle.py`、
`touchpad-button-ownership.py`
进入默认测试，覆盖正常手势、触点坐标缓存与按钮松开归属；测试用假设备运行真实
回调，不打开或注入设备输入。实机的按键手感、触摸坐标与切换验收另行进行。

`gamepad-activity.py` 的 57 项纯函数/mock 测试覆盖摇杆漂移、持续按住、过期事件、
队列溢出、重连/睡眠、唯一虚拟手柄身份、屏幕/锁屏/合盖门、KWin unique-owner
验证及单次通知；测试本身不发送输入或活动通知。原生只读 idle probe 与显式单次
activity 诊断放在 `tools/gamepad-activity/`，不属于默认测试，也不会安装为服务。
API 的 IDLE → RESUMED → IDLE 闭环不能替代手柄连续玩五分钟后再放下的实体验收。

9 月 6 日审计回归已进入默认 `make test`：`brightness-daemon.py` 检查连续循环，
`daily-suspend-install-transaction.py` 检查安装部分失败与回滚，
`install-service-upgrade.py` 检查权限漂移、服务代际与中断后重试。
这些用例只操作临时文件并替换硬件、授权和服务调用。
`boot-switch-result.py` 另需 Node.js 执行实际 QML 结果函数；缺少时会明确跳过。
Fedora 完整验证应提供 Node.js，不能将跳过的界面状态测试算作通过。

`gpu-observer-smoke.py` 会短时运行只读 DRM fdinfo 观测器并验证 JSONL schema；
它不会调整 GPU 频率、显示模式或电源状态。

`display-telemetry.py` 用 KWin `supportInformation` fixture 验证 DSI-1/DSI-2 物理尺寸、
逻辑几何、缩放、刷新率和 Wayland/DRM/OpenGL/renderer 的展平 schema，覆盖
user bus 失败时的 null 回退、后台轮询不阻塞 GPU 采样、Panel 过期拒绝和默认
安装链。该测试固定“刷新率不是应用 FPS”，`display_app_fps` 必须为 `null`。

`panel-battery-fixture.py` 用隔离的 power-supply sysfs 树锁定整机功耗口径：在线且
数值合理的 USB 输入优先，否则只允许明确放电中的电池电压×电流估算；来源和值
必须一起失败关闭。fixture 还证明驱动暴露的异常 `power_now` 不会被当成整机功耗。

`pds017-battery-charge-unit.py` 锁定下游、当前上游和 Qualcomm Android SM8550
协议来源，验证候选只在 `qcom_battmgr` property 分支初始化 mAh、不添加
`ENERGY_*` 或 IFPC 改动，并对精确 probe context 做 `git apply`。设置
`PDS017_DRIVER_SOURCE` 可额外要求完整下游 driver SHA-256 与 patched SHA-256
完全匹配。测试还锁定设备已安装 UPower 1.91.3 的官方 tag、三个源码摘要和
charge→energy/缺字段 fallback 契约；默认测试不构建内核、不访问设备，也不授权
安装。实验清单另锁定隔离 Fedora 44/aarch64 的 driver object、Image、modules、
DTB 完整源码构建摘要及其非发行边界。

`brightness-powerdevil.py` 纯函数覆盖 PowerDevil 把两路实际亮度写成同值时的
首观测恢复判定，并保留任意单路瞬态的两次防抖。它使用内存 fake runner 验证
官方 `org.kde.ScreenBrightness.Display.SetBrightness(iu)` 计划，默认测试不会连接
会话 DBus，也不会写任何背光；同时锁定安装器必须显式重启已运行服务，避免只更新
磁盘文件却继续运行旧进程。

`pds002-baseline-smoke.py` 验证 stdout-only GPU/KWin 故障采集器的日志精确分类、
压力/D-state/双屏解析、突发统计、隐私裁剪和零设备写入约束。正式基线应通过
SSH 将 stdout 保存到另一台机器，不在故障设备上落盘。

正式 IFPC A/B 的 collector/evaluator/ledger/workload/runner 共 42 项测试，还会锁定
KWin、双 GPU firmware、实际风扇控制器，以及每个五秒样本中的
`pocketds-performance` TuneD 和 `aggressive` 风扇配置。runner 对不匹配配置只会
失败关闭，不会替用户切换性能或风扇档位。

`kernel-ab-planner.py` 纯静态/mock 验证 PDS-002 内核 A/B manifest 的单变量、
对称补丁顺序、确定性哈希及 fail-closed 规则。fixture 中 firmware/config 哈希
是生产 CLI 必须拒绝的显式占位符；测试只在内存中将其替换为合成值。该工具不
包含 build/install 入口，也不访问 Git、设备或内核源码树。

`kernel-ab-evidence.py` 在临时根目录验证 PDS-002 运行证据采集器的内核版本、
设备树身份、双 config 一致性、固件闭集哈希、软链/权限拒绝和确定性输出。真实
入口固定读取仓库内的 Pocket DS spec，只向 stdout 输出；即使两个 planner 哈希
已测得，源码提交绑定和预验证回滚 artifact 仍保持失败门。

`kernel-ifpc-candidate.py` 用完整合成 SRPM/RPM manifest、解包树、raw Image 和
Android bootimg 覆盖 IFPC 单变量验证器。它拒绝额外 payload 改动、未锁定 raw
Image 字节、DTB 漂移、source member 漂移、spec 夹带改动、重复 patch log marker、
未知或重复 JSON 字段，并锁定双构建独立复现即使证据通过也不能越过回滚门授权安装。默认测试不构建
内核、不联网、不写设备或启动分区。

`kernel-rollback-preflight.py` 用分离的 package boot/native-lid live boot fixture
覆盖回滚静态预检。它同时绑定 runtime/source/baseline/candidate 四份报告、签名与
独立重建 RPM header、完整解包树、raw Image/gzip/custom DTB/Android image ID；拒绝
artifact、报告、RPM header、独立树漂移和任何伪造的启动/恢复成功。即使全 PASS，
恢复计数仍固定为 0，安装授权固定为 false。

`kernel-runtime-boot-compose.py` 用合成 Android bootimg v0 验证候选运行镜像合成器：
只保留候选 RPM 的锁定 gzip 内核流，替换为源码绑定的 native-lid DTB，并重算 kernel
size 和 SHA-1 image ID。11 个场景覆盖确定性 create/verify、0600/no-clobber、输入和
RPM DTB 漂移、链接/宽松权限目录以及候选/回滚报告越权；工具没有设备、刷写、重启或
安装入口，成功报告仍固定关闭回滚和安装门。

`kernel-recovery-host-preflight.py`、`kernel-recovery-dispatch.py` 和
`kernel-recovery-runtime-attest.py` 覆盖独立 fastboot RAM 回退链。host preflight
锁定 baseline/candidate、官方 ROCKNIX ABL v1.1.8 和 Mac platform-tools；dispatch
默认只输出计划，执行需精确确认、唯一设备和 `unlocked: yes`，唯一改变状态的命令是
`fastboot boot`，报告 0600/no-clobber 且不记录序列号。执行前会重新校验 baseline，
并把同一个只读文件描述符传给 fastboot，拒绝预检后的路径替换。运行态不信任相同的
`uname`，而用 `/sys/kernel/notes` build ID 证明“baseline 正在运行、三份磁盘文件仍是
candidate、开机 ≤15 分钟”。`kernel-recovery-evaluate.py` 再要求 RAM 启动和恢复磁盘使用
同一 boot token、恢复观测晚于独立启动观测，随后正常 baseline 启动必须换成新 token；
它只在六份 0600/单链接报告与两个 lock 全部一致时提高回滚预验证门，安装门仍固定 false。
`kernel-recovery-transaction.py` 另用 13 项 fixture 验证固定三别名、真实 VFAT/machine/release
绑定、候选只接受全 baseline 前像、baseline 可接管 candidate/混合态、同目录原子替换、
post-rename 故障自动恢复逐文件前像，以及执行前 0600 intent/最终回执原子交接。
整条链共 56 项 fixture 覆盖篡改、锁机、多设备、链接、过期、顺序、写入故障和普通重启误判；
真实 fastboot 启动尚未执行，当前回滚和安装门保持 false。

`pds023-usb-wake-evaluate.py` 用严格私有 fixture 覆盖 5 次冷启动、30 次 deep、
内置键盘/手柄、双触屏和唯一 USB-C 的 USB2/USB3 监督计数，并绑定 repo/helper/rule
哈希。它拒绝公开/链接/重复或非有限 JSON、未知字段、弱类型、矛盾计数和来源漂移；
不读取 sysfs、不休眠、不枚举或修改真实 USB。`usb-wake-policy-mock.sh` 另验证精确
PCI/driver/wake 状态、歧义拒绝、幂等和固定无可执行路径的 bind 规则。

`keyboard-visibility-state.py` 用确定性虚拟时钟覆盖手动/自动显示、焦点抖动、
取消后迟到的 GLib 回调以及连续 50 次手动切换；它不启动 GTK 或改动当前会话。

`keyboard-adapter.py` 覆盖 GLib/AT-SPI/语音生命周期接线、启动期切换 parity、
50 轮手动切换和窗口单一所有者，并静态核验 systemd notify/reload 就绪协议。

`keyboard-geometry.py` 用临时 runtime cache 验证键盘只接受新鲜、完整、启用且范围
合理的 DSI-2 逻辑几何；缺失、损坏、过期、partial 或越界时回退已验证的
`(283,720) 819×614`。它不连接 KWin、XWayland 或当前图形会话。

`ui-design-contract.py` 锁定 Panel/键盘共享 palette、WCAG AA 文本对比度、高频
遥测/按键无 tween、Panel 按压反馈不超过 160 ms、最小触点以及底排 `.`/`/`
直达键。它只读源码，不渲染或连接桌面。

`joymouse-profile.py` 锁定掌机桌面约定：右摇杆移动，RB 左键、RT 右键，LB/LT
滚动，并检查模拟扳机的 0.15 阈值仍存在。

`pds010-wiliwili-attended.py` 用纯 fixture 锁定 Wiliwili 单会话实体手柄验收器：从唯一
受管 Wiliwili 进程自己的 `/proc/PID/fd` 反查它实际打开的虚拟手柄，同时要求 sysfs
ancestry 精确落在 `/sys/devices/virtual/input/inputN`，并枚举全系统要求同一
VID/PID/name 的 direct-virtual event 唯一且就是该 FD；这会拒绝实体外接 Xbox 和
并存/残留的同名 uinput。由于上游 xpad target 没有 creator-specific phys/uniq，这不是
对唯一恶意替代 uinput 的密码学归因；真人提示仍只允许机身按键、要求断开外接控制器并
不得同时运行其它 uinput 工具。流程在内存中绑定
owner/Wiliwili PID+starttime、Flatpak instance、gamepad lease、InputPlumber identity
以及 helper/supervisor/launcher/mapping 的 source/live 字节。真人流程按显式 controls
顺序轮询 EVIOCGKEY/EVIOCGABS 当前状态，逐项要求用户确认 Wiliwili UI，再等待同一会话
自然退出并只读核验 journal 消失、新 joymouse lease、稳定 profile/targets 和未重启的
InputPlumber。工具不读取事件队列、不 grab、不切模式、不启动/终止/发信号给应用，也不
写报告或接受输出路径，因此没有跨进程 postflight、报告重放或误写运行文件的表面。
fixture 覆盖错误/额外/重复设备、PID 重用、错误租约、意外键、反向轴、采样到的电平
不稳定、缺 release、observer 后漂移、失败确认不可升级及退出超时。采样只能说明轮询
时刻的 level state，不能声称检测了采样间的 event-level 抖动/重复。Guide 会改变系统
模式，保留给独立 transition 验收，不能用本流程代替。真人入口为
`CONTROLS=a,b,x,y ROUNDS=3 CONFIRM=POCKETDS-OBSERVE-WILIWILI-CONTROLS
make accept-wiliwili-controls`。`ROUNDS` 上限为 50，PASS 行会包含实际 control 数和
rounds；发行前的肩键/扳机门必须以单次 `ROUNDS=50` 执行，默认 3 轮只属于 smoke。

`esde-config-migration.py` 在临时 home 中验证 ES-DE 3.x 路径迁移不会覆盖用户设置
或触碰 ROM，并拒绝畸形/重复/DTD/symlink/hardlink/过大配置；同时覆盖 ES-DE 当前
原生的多顶层片段格式和上游预留的 `<settings>` 根格式，缺失 `ROMDirectory` 时
保持原格式且 XML 特殊字符可往返。
`--check-only` 复用同一预检，只报告 would-change 且不创建配置或备份。
`input-mode-mock.sh` 验证模拟器使用精确 profile 而非盲目 toggle；
`input-install-policy.py` 锁定独立输入安装器的备份、显式激活和不重启 InputPlumber
边界；
`input-held-modifiers.py` 用合成 EVIOCGKEY bitmap 和静态边界验证只读 modifier
检测器，真机可用 `--require-clear` 把残留 Alt/Ctrl/Shift/Meta 变成明确失败；
`emulation-launcher-mock.sh` 验证信号转发、子进程退出、原输入模式恢复，以及
status/set 失败时绝不启动前端且仍尝试恢复原模式。
`esde-library-verify.py` 验证导入后 inventory 的路径、所有者、文件类型、大小和总计，
并显式声明它不等价于内容哈希；最终传输仍以 rsync checksum dry-run 补上内容门。
`retroarch-launcher-mock.sh` 验证 RetroArch 只经 Panel 闭集 helper 切换/恢复性能档，
且不会触碰无法安全恢复的外部 TuneD profile。
`emulation-install-policy.py` 防止主安装器重新写入 ES-DE 2.x 旧目录，并核验
launcher、prepare 与 InputPlumber helper 会作为同一条可复现安装链部署。
`rom-preflight.py` 用合成文件树验证扫描不跟随软链、不读取游戏内容、不输出文件名，
并对超时、条目上限和深度上限显式失败；它不是 ES-DE GUI 性能替身。

`esde-synthetic-acceptance.py` 在临时 HOME 中生成空文件和本地 gamelist，验证
ES-DE 合成系统配置、版权素材为空、路径隔离、逻辑哈希可复现，并锁定默认入口绝不
启动 GUI 或访问真实 `~/ROMs`。`make test-emulation-synthetic` 只生成并清理 5000
项临时库；真实窗口验收只能在人机终端显式执行
`POCKETDS_ALLOW_ESDE_GUI_TEST=YES make test-emulation-synthetic-gui`。每轮由操作员在
10 秒内确认可交互，脚本随后要求 launcher 在 3 秒内退出；Ctrl-C 或输入 `q` 会取消
并清理临时目录。

`chromium-runtime-lock.py` 用完整/缺回执的合成 runtime 覆盖 metadata、mtree、
主二进制、wrapper、compat 文件与 symlink 验证，并锁定“现场 hash 全匹配但包归档
和签名缺失”仍必须 fail closed。默认测试不读取 `/opt`、不联网、不下载、不安装也
不启动 Chromium；显式 `make verify-chromium-runtime` 才只读扫描现场，并默认核对
实际启动使用的 `/usr/local/bin` wrapper。锁中的 `release_ready` 仅代表 Chromium
子层的提取物与来源回执齐全，不代表固件已通过 PDS-020。

`chromium-provenance-verify.py` 以合成 xz 包覆盖包名/版本/架构、元数据、锁定文件、
软链、重复 member、epoch 文件名、哈希篡改和精确 signer 输出。真实入口必须显式给出
五个固定归档所在目录和可信 `sqv`：
`ARCHIVE_DIR=/path/to/packages SQV=/path/to/sqv make verify-chromium-provenance`。
它只读且无网络/下载/安装/启动路径；归档本体不会复制进 Git 或系统。

`diagnostic-privacy.py` 以 canary 数据验证诊断包对用户目录、IP、MAC、UUID、序列号、
本机名和本地用户名的脱敏，并静态约束采集器不得读取账户、网络配置、键盘/ASR 或
游戏库。采集命令有时间/文件大小上限；唯一临时目录通过脱敏和类型/总量检查后才
发布，tar owner 固定为数值 root，校验文件只记录归档 basename，旧证据不会被覆盖。

`pds013-phone-integration-status.py` 用 mock 锁定手机互联预检的固定只读命令、五秒/
4096 字节边界、严格状态值、精确 KDE Connect 端口和失败关闭。真机入口
`make observe-phone-integration` 不调用 `nmcli` 或对端枚举，只输出包/daemon/防火墙
布尔结果；它永远把手机配对、互传与 resume 验收标为 `not_run`。

`power-profile-config.py` 核验 Panel、TuneD PPD 映射、CPU/GPU 上限和风扇脚本共用
同一组 `pocketds-*` profile，防止按钮退化成只写 CPU governor。
`power-profile-live-mock.py` 在临时 sysfs 中跑完整三档/恢复流程；真机入口
`power-profile-live.sh --apply` 必须显式选择，并始终通过 Panel helper 恢复原档。
100-switch 模式另需 `POCKETDS_ALLOW_POWER_STRESS=YES` 和精确确认词；mock 会逐轮核对
三档状态，并在第 5 次动作注入失败，证明 EXIT trap 仍恢复原档。

`emulation-readonly.sh` 是独立的真机前置检查，只报告 AppImage 校验值、ES-DE
3.x 配置路径、输入模式和 ROM 树文件总数；它不会启动 GUI 或读取 ROM 内容。

`esde-log-summary.py` 以包含私有路径/系统名/游戏名的 canary 验证日志汇总只输出
级别、事件和启动耗时计数；输入必须是 ≤8 MiB 的单链接 UTF-8 普通文件，JSON
报告 0600 且拒绝覆盖。真机 `make observe-emulation-log` 不输出原始行或日志路径。

`pds005-applet-migration.py` 覆盖纯 Applet-33 可逆迁移状态模型，包括逐字节恢复、
歧义与并发修改拒绝，以及篡改检测；`pds005-standalone-contract.py` 静态核验
standalone LayerShell 实验默认不启用、仅绑定 DSI-2、不硬编码几何，且不包含
KWin 重启或 kill 路径。

`pds005-panel-geometry.py` 用临时配置与 telemetry fixture 核验现有 applet 的动态
下屏逻辑尺寸、唯一 owner、只改两个目标 token、symlink/权限/并发/活动 shell 拒写、
0600 hash bundle 以及逐字节恢复。`make test-panel-geometry` 不访问现场配置或 D-Bus。

`pds005-plasma-recovery.py` 用 mock systemctl、bus owner 与虚拟时钟核验有界
plasmashell 恢复：正常 KDE quit、SIGTERM 超时后的精确 unit-cgroup SIGKILL、单次启动、
独立 oneshot cgroup、双重确认、互斥/交接竞态、request ID 结果关联以及键盘/遥测不变量。
`make test-plasma-recovery` 不连接会话 DBus、不启动或停止任何现场服务；安装链只加载
oneshot unit，绝不 enable 或自动启动。

`pds005-panel-actions.py` 编译 Panel helper 并锁定触摸动作边界：恢复/清理都要求
固定确认 token，KWin 全屏只调用 KDE 6 注册的 `Window Fullscreen` 动作，Panel
折叠为透明桌面状态且保留重新打开按钮，快捷说明按当前 InputPlumber mode 切换。
它不会执行全屏、恢复或清理动作。

`boot-mode-switch.py` 使用恢复出的 ROCKNIX ABL v1.1.8 4 KiB `devinfo` fixture，
覆盖两个 BootMode 与两个允许的 BootSourceMode。测试证明事务只修改 BootMode、
保留启动源、留下逐字节一致的备份，并拒绝任何无关模板变动；不会访问真机分区或
执行重启。

`pds005-ui-deployment-audit.py` 以单 fd、`O_NOFOLLOW`、owner/mode/link/尺寸门比较
Panel、recovery 与键盘的 11 个仓库/现场文件；只输出逻辑 ID 和 SHA-256，不输出
绝对路径。编译型 panelctl 必须由调用方显式提供当前源码候选，否则固定为
`NOT_EVALUATED`，不能把旧二进制冒充为已部署。审计器不构建、安装或重启服务。

`pds008-telemetry-soak.py` 验证长时只读采集器的私有缓存读取、单实例锁、原子
0600 checkpoint、服务计数解析和最终门槛。正式入口 `make observe-telemetry-soak`
只读既有 telemetry cache 与 systemd accounting；`OUTPUT` 必须是新路径，默认
24 小时且不会重启 KWin、Plasma、遥测服务或改变显示/性能/电源状态。

`pds001-deep-cycle.py` 用 fixture 验证 deep/串行 callback/RTC/双屏/背光/音频/
电池与关键服务的 fail-closed 前后快照，并覆盖报告拒绝覆盖、链接拒绝、双重显式
解锁、电源状态类别、域分离 boot token 和 clean revision/helper 绑定。
`make observe-deep-preflight` 默认只读；真实 suspend 绝不进入默认测试。

`pds001-deep-series.py` 只离线读取四份成功的私有 v2 报告，要求同一 source binding，
并唯一匹配放电 10×30 秒、充电 10×30 秒、满电 5×30 秒及放电 3×300 秒。输出不含
boot token/路径/时间戳/原始电池值；即使自动矩阵通过，声音、触摸、手柄、Wi-Fi
流量和 >5 分钟长待机仍固定为 NOT_EVALUATED，`pds001_complete` 永远为 false。
这套 v2 四报告矩阵只为读取历史证据而保留，明确属于 legacy；新的 v3 guard 报告不会
送入它，也不再把 10 次/多电源状态循环作为本阶段完成门。恢复与取证条件齐备后只做
一次有人值守 smoke。

`voice-artifacts.py` 的 10 项测试覆盖语音 summary 的新鲜时间、私有权限、所有者、
单链接、尺寸、UTF-8/JSON/文本界限和仅删除自有产物；还覆盖 SenseVoice 三文件
安全门、固定中文/ITN argv、严格 speech JSON、Unicode 归一、重复/非有限/错语言/
错事件拒绝，以及本地命令 stdout/timeout 上限。测试使用临时文本/WAV/子进程，
不读取设备麦克风、不启动录音、真实 ASR 或网络。

`pds011-asr-evaluate.py` 用私有 0600 语料/结果 fixture 验证中文字符错误率、
p50/p95 延迟、峰值 RSS、后端失败和显式门槛。输出只有汇总指标，不包含
语料、转写、音频哈希或路径；评估器不启动模型或读取 WAV 内容。

`pds014-post-boot-acceptance.py` 验证开机后 15 分钟窗口、精确双重确认、固定只读
命令计划、仓库干净/提交有效、前后 system/user 服务 PID 不变且零重启、诊断包生成
以及 0600 不覆盖报告。默认只列计划；实际入口不包含 reboot/suspend 执行参数、
网络或安装命令，报告不输出 boot ID、命令原文、诊断路径或主机身份。

`pds020-release-root-audit.py` 以临时 mounted-root fixture 验证 sudoers 规则解析、
广域 NOPASSWD 拒绝、外部 include 拒绝、隐私类别去路径化、home symlink/扫描错误
fail-closed、系统身份清理、发行证据缺失与 live-root 精确确认门。证据 fixture 还
覆盖 SPDX 3.0.1 flattened `@graph`、IRI 引用、document/SBOM/package profile 字段、
第三方条目的固定来源/许可证/
再分发许可/哈希证据，以及 `assets/` 的逐文件完整覆盖。互斥的 `asset-free` profile
只有在清单和真实目录同时为空时通过；任一隐藏文件、未知 profile/字段、非空
`TBD`/`NOASSERTION` 或 `redistribution=false` 仍必须失败。
`make audit-release-root RELEASE_ROOT=/mnt/image` 只读离线根，不执行 sudo、网络、
服务命令、删除或修复；退出 1 表示镜像仍有发行 blocker。

`pds020-source-license-inventory.py` 只枚举 Git 已跟踪文件，以单 fd 边界记录相对
路径、SHA-256、模式、文本/二进制和文件头中客观存在的 SPDX 表达式，并统计四个
根级法律/供应链文件及权威 `components/assets/ASSETS.json` 是否已跟踪。它不联网、
不改 Git，不从缺失或存在的 header 推断许可证、
作者权利或再分发许可；`release_ready` 永远为 false。

`pds020-spdx-offline-validate.py` 用 11 项 fixture/static 测试锁定 SPDX 3.0.1
context/JSON Schema/OWL+SHACL model 和 Fedora 44 的 Python/jsonschema/rdflib/
pyshacl 身份。它要求 JSON Schema 与语义两层同时通过，把唯一顶层 context 换成本地
已验哈希对象后解析，禁止嵌套 context、OWL import 和网络。官方 Schema 自带的两个
等值重复键只在整件哈希匹配后精确放行；SBOM/lock/context 的任何重复键仍失败。
输出只含哈希/计数，固定不推断许可证或发行权限。

`pds005-ui-update-transaction.py` 用 8 项合成根/static 测试锁定 soak 后唯一允许的
6 项 Panel/键盘更新。它覆盖私有 stage、revision/哈希/live 前像绑定、模式修正、
缺失文件安装、apply 后逐项验证、完整回滚、rename 后故障自动恢复和未知改动拒写。
默认入口只读；stage/apply/rollback 使用三个不同的精确确认词，且整个工具没有
桌面、键盘或 telemetry 服务激活路径。

`pds005-ui-input-evaluate.py` 用 11 项 fixture/static 测试锁定 PDS-004/005/010
真人台账。评估前必须同时匹配 clean revision、applied marker、六项 repository source/
private payload/live target/rollback preimage 与 joymouse source/live profile；任一漂移、
回滚或缺项都不能 PASS。十一场景只接受计数、布尔值和最终输入模式，报告不含自由
文本、路径、时间戳、设备标识或应用名，工具也没有 UI/服务/输入操作入口。

`pds020-asset-c2pa-verify.py` 用 8 项 fixture/static 测试核对两张壁纸的原图哈希、
PNG 尺寸、完整资产覆盖、锁定 c2patool、官方 signer/TSA trust list、OpenAI Media
Service claim、GPT Image creation action、签名和 validation code。篡改、untrusted
signer、生成器漂移、未列资产、symlink 或把 C2PA 夸大成许可证都会失败。真实离线
入口为 `C2PATOOL=... C2PA_TRUST_LIST=... C2PA_TSA_TRUST_LIST=... make
verify-asset-c2pa`；provenance PASS 仍明确返回 `redistribution_ready=false`。

`pds020-install-asset-preflight.py` 用 8 项 fixture/static 测试锁定安装器的显式
`personal-assets`/`asset-free` 双 profile。个人模式必须由 C2PA receipt 精确覆盖并
保持权利门为 false；公开空模式必须同时具有空目录和精确空清单。篡改、额外/隐藏
文件、symlink、清单漂移、profile 交叉或重复 CLI 开关均在安装前失败。默认日用安装
仍是个人模式；空模式不安装也不删除已有壁纸，且永远不声称整个固件已可发布。

`pds020-composition-preflight.py` 用 19 项 fixture/static 测试锁定 Fedora 44/aarch64
的非启动 `generic-container` rootfs 暂存边界。真实模板当前必须退出 1：项目 RPM、
硬化 userspace RPM、签名仓库快照、发行证据、builder digest 和 rootfs smoke 均未
完成。测试证明即使重新计算模板哈希，也不能嵌入账号/主机身份、重复包、可刷写/分区
语义、浮动 builder tag、证据目录 symlink 逃逸，或无唯一内容哈希证据的完成门。
仓库门必须消费完整 DNF5 smoke；发行证据门必须绑定五个真实证据文件、11-check
offline mounted-root audit、同一 SBOM 的官方 Schema+OWL/SHACL 报告和固定 validator
lock，不能用通用四字段 JSON 代替。
`rootfs_smoke_passed` 也只接受下述结构化 final-root 回执，并交叉核对稳定 composition
policy、rootfs/build locks、322 包 manifest、SELinux 与同根 audit 字段。
`make preflight-composition` 只读、离线且不启动构建。

`pds020-rootfs-smoke.py` 用 10 项 fixture/static 测试锁定未来 final
`container.tar` 与同一 offline mounted/extracted root。它绑定 artifact/三份 lock，
要求 Fedora 44/aarch64、精确 322 包 manifest、`.pds2` NEVRA/RPM verify/关键 payload/
遗留路径/service wants 契约、三个关键文件 SELinux 标签和同根 11-check mounted-root
audit；结尾复核 artifact/lock/root inode/package manifest 防止中途替换。工具不解包、
不挂载、不安装、不启动服务、不联网并拒绝 `/`；PASS 仍不是整机发行授权。

`pds020-userspace-rpm-audit.py` 用 11 项 fixture/static 测试约束硬化 binary RPM 的
pre-sign payload：精确 `.pds1` NEVRA/来源包、SHA-256 header/newc 对照、root owner、
路径/模式/尺寸/内容、流式输出上限、padding/CRC、锁定风扇控制器、sudoers 和
scriptlet。路径逃逸、特殊文件、symlink sudoers、全局 `NOPASSWD: ALL`、控制器
摘要分叉或旧 release 均失败。
`BINARY_RPM=... make audit-userspace-rpm` 只读且不安装；PASS 仍返回
`signature_verified=false`、`release_ready=false`，不能替代签名或 mounted-root audit。

`pds020-userspace-srpm-prepare.py` 用合成 SRPM/spec fixture 验证当前 COPR
`pocketds-userspace` 的离线硬化链：原 SRPM 大小/哈希、两个 RPM 签名、payload digest、
NEVR/归档名/Release 变换、完整 67 文件提取树、spec、危险源文件、原 Source70 和仓库
平滑 Source70 都必须匹配；上游行漂移、验签窗口替换、篡改、只请求一个输出、重复目标
或输出覆盖一律失败。13 项测试锁定独立 `.pds1` Release、删除全局 wheel
`NOPASSWD: ALL` 四个 spec 引用并
成对输出新 spec/Source70；不下载、不安装、不构建。真实只读入口为
`SOURCE_RPM=/path/to/src.rpm EXTRACTED_DIR=/path/to/extracted make verify-userspace-source`。

`pds020-userspace-rpm-repro.py` 用 8 项离线测试锁定两个私有 mode-0700 mock 结果目录、
source/binary RPM、`installed_pkgs.log`、RPM header/payload digest 和既有 binary audit。
真实 Fedora 44/aarch64 offline mock 结果对已通过：两份 mock 输出 source RPM、binary
RPM 和 buildroot 包清单逐字节一致。`RESULT_A=... RESULT_B=... make
verify-userspace-rpm-reproduction` 只读、不构建、不安装、不联网；输出明确保持
`signature_verified=false`、`release_ready=false`，不能替代签名、builder attestation、
升级回归或 mounted-root audit。

`pds020-userspace-rpm-postsign.py` 用 10 项离线测试锁定签名后的 receipt 门：签名 RPM、
canonical build lock、payload、公钥文件/完整 fingerprint、单一 Header OpenPGP 签名和
isolated temporary RPM keyring 必须一致；unsigned、legacy/多签名、header/payload 漂移、
弱类型、重复/非有限 JSON、不安全文件和验证中漂移均失败。真实签名产物尚不存在，因此目前只有
fixture/static PASS。`RECEIPT=... SIGNED_RPM=... RELEASE_PUBLIC_KEY=... make
verify-userspace-rpm-postsign` 不签名、不生成密钥、不安装；PASS 也只允许进入签名仓库
暂存，整个固件仍保持 `release_ready=false`。独立
`make verify-userspace-rpm-postsign-pds2` 入口固定覆盖 `.pds2` source/build locks 和
60 路径 payload，防止误用历史 `.pds1` 证据。

`pds020-userspace-rpm-transaction.py` 用 9 项 fixture/static 测试锁定 disposable mock
事务：默认只给计划，执行需要精确确认、两份锁定 RPM 和新 0600 报告；固定 offline
`uniqueext` 依次做 vendor install、hardened upgrade、vendor rollback、hardened
re-upgrade，逐阶段核对唯一 NEVRA、路径数、sudoers 和风扇控制器，并在成功或失败后
清理 mock root。真实四阶段 payload-only 运行已 PASS。因为明确使用 `--nodeps
--noscripts --notriggers`，它不冒充依赖、scriptlet、SELinux、服务或整机升级验收。

`pds020-userspace-rpm-archive-verify.py` 用 12 项离线测试锁定 `.pds2` 的 136 包依赖归档
及 322 包完整目标归档：
目录/文件权限、精确清单、逐包 NEVRA/文件与 payload 哈希、Fedora 44 公钥和单一
Header OpenPGP V4 签名均失败关闭；唯一项目 RPM 必须保持 unsigned pre-sign 边界并
重跑完整 payload audit。真实 135/135 Fedora 包验签及项目 audit 已 PASS；verifier
不联网、不下载、不安装，不能把未签名项目 RPM或整个固件标为 release-ready。

`pds020-userspace-rpm-full-transaction.py` 用 9 项 fixture/static 测试约束确认门和
全离线 mock 流程。真实运行从预热 mock cache 离线初始化，安装归档中全部 136 个
本地 RPM，正常执行依赖解算、scriptlet/trigger，再离线 remove/reinstall 项目 RPM；
四阶段精确清单 `187→323→322→323` PASS，成功/失败均清理 root。它仍不覆盖完整
base-OS 仓库快照、SELinux labels、image 服务或真机升级。

`pds020-userspace-rpm-rootfs-transaction.py` 用 10 项 fixture/static 测试锁定 empty-root
确认门、私有 work/output、DNF5 身份、仅本地 archive 参数、失败清理和 inode 复验。
真实 Fedora 44/aarch64 运行在仓库/插件禁用且 cache-only 的 DNF5 下，仅用 322 个本地
RPM 安装到空根；322 包 manifest `7cdf60c9…`、`dnf check`、项目 payload 和清理 PASS。
项目签名、repo metadata 签名、SELinux labels、服务启动和 boot 仍明确为 false/NOT RUN。

`pds020-userspace-rpm-repository-verify.py` 用 10 项离线测试锁定 repository lock、私有且
互异的两个 repodata 目录、XZ 压缩/开放内容、严格 repomd、322 包 primary 位置/NEVRA/
大小/哈希、filelists/other 完整 pkgid 集及 Fedora `createrepo_c` 二进制/NVR/`rpm -V`。
真实两份固定 revision/mtime/单 worker 结果四文件逐字节一致，`repomd.xml` 为
`c45ef780…`；入口还会重跑 321/321 Fedora 包验签和项目 payload audit。它只读、不联网、
不生成 metadata/密钥/签名、不安装；项目 RPM、detached repomd 与发行门仍为 false。

`pds020-userspace-rpm-repository-postsign.py` 用 10 项离线 fixture/static 测试锁定未来
最终仓库的顺序和内容链：`.pds2` RPM 必须先完成 post-sign，最终 322 包集合只替换该
项目容器，两份 primary/filelists/other 必须绑定新包哈希且不能复用 unsigned metadata，
`repomd.xml.asc` 必须同时位于两份私有 repodata 结果并由 RPM receipt 的同一公钥验证。
证书先以 `--show-keys` 拒绝 secret/multiple key，再只导入临时 keyring；bad/expired/
revoked/multiple/wrong-key/wrong-version/wrong-hash status 均失败。工具不签名、不生成密钥、
不联网、不构建或安装；未来 PASS 也固定保留 DNF5 `repo_gpgcheck` smoke 和发行门为 false。

`audio-acceptance-mock.py` 核验静默音频验收器不会播放、录音、切换 route/profile、
写 mixer 或重启服务，并锁定 WSA MM1 的机器前端路由语义。`make test-audio`
只读对比仓库/现场 UCM、服务、默认 sink/source 与 mixer；基础配置没有物理麦克风时
明确 SKIP，只有显式实验模式才把缺少物理 source 判为失败。它还只读锁定运行内核、
SM8550-APS MultiMedia3/4 capture、Fedora UCM 包和十个 VA DMIC0/1 控件；任何版本、
PCM 或控件漂移都会在尝试开流前失败，当前 mixer 值只作诊断而不算录音成功。

`pds018-audio-matrix-evaluate.py` 离线核验私有监督账本，固定左右/立体声、实体麦克风、
DP 热插拔、冷启动和 deep-resume 门槛，并要求 PipeWire/WirePlumber 无重启、无
auto-null/XRUN/路由漂移。账本必须绑定当前提交和两份 UCM 哈希；报告不含音频、
转写、时间戳、路径或设备标识。评估器没有播放、录音、路由或电源操作能力。

`pds006-battery-matrix-evaluate.py` 离线核验绑定当前提交、panelctl 和 Panel QML
哈希的私有监督台账。拔电→放电、插电→充电和满电+外接电源必须达到固定轮数且每轮
遥测完整、无读取失败、无 Plasma 重启；24 小时证据必须至少 2880 个样本、最大间隔
≤60 秒且样本/时长覆盖自洽。模板与报告均 0600、不覆盖，不含原始电池值、路径、
时间戳、设备标识或自由文本；评估器不探测硬件、不切电源也不操作服务。

`fan-acceptance.py` 用假 sysfs/proc/run 覆盖真实 RPM 可用和不可用、PWM/目标 PWM
分离、CPU/GPU/video decoder 证据及关键来源缺失时失败关闭。默认 collector 只采一次
并写 stdout；`make observe-fan` 仍只读，绝不启动负载、浏览器、音频或修改 profile。

`pds009-fan-correlate.py` 覆盖私有 0600 噪声时间标记、采集证据的严格解析、标记两侧
样本对齐、失配/策略失败时不完成，以及报告不覆盖和不泄漏绝对时间戳、进程名或视频
标识。它只证明观测是否对齐，始终保持声学因果关系 `NOT_DETERMINED`。
