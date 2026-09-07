# 休眠当前验收状态（2026-09-07，截至 native-02 通过）

**native-02 原生合盖→真实 deep→开盖唤醒整轮通过，用户确认双屏、两块触摸、
桌面图标和下屏 Panel 全部正常。**实际深睡 **29.69 秒**，Hall IRQ202 唤醒，
成功／失败计数 **1/0**；原始 Hall 开合间隔 **32.771 秒**，自动回执无 error。
当前保留 `sleep` 合盖选择，RTC 已清空，pending/BLOCK 均不存在。

`2d516a9` 已修复 native-01 暴露的异步熄屏准备顺序，ARM64 隔离测试 51 项
通过，本轮实机也确认先完成背光归零再进入睡眠。v4、PM 配置、PowerDevil
补丁与新 light 在新启动 `d84d307d-6b0d-4014-afaf-a6c86e1d6f57` 中保持生效。
此前正常重启未自动完成，用户强断电恢复；这个晚期关机问题原因未定，
不能因为本轮睡眠通过就写成正常 reboot 已修复。

RTC-02 曾取得 95.571 秒内 955 条闭盖全暗样本，但实际开盖超出观察器范围；
本轮 Hall 不补造这段 RTC 证据。RTC-01 真实闭盖亮屏失败、native-01 睡眠前
中止、早期 Hall 时间窗口失败均保留原记录，不追改历史回执。

本文记录当前节点的资格，取代此前“v4 仅在 RAM、磁盘仍是 v3”的当前状态描述；
旧研究文档中的实验结论保留其当时语境。

## RTC-01：闭盖定时返回仍会短暂点亮

本轮在同一 boot、起点成功计数 1/0、已落盘的正常 console 配置下执行。
程序于 03:21:20 JST 就绪，原始 Hall 事件记录 03:21:34 合盖，实际深睡
120.256 秒，由 RTC IRQ166 返回，最终计数 2/0。用户于 03:25:16 开盖，
距真实合盖 222.585 秒，晚于约定的 180 秒；但覆盖了恢复后 94.336 秒闭盖状态。

独立复核发现 `PrepareForSleep(false)` 后 0.473 秒起，上屏 `bl_power`
从 4 变成 0，约 0.4 秒后才回到 4。下屏也短暂进入 on 状态，但 raw 始终
保持 0。真实开盖约在 94 秒以后，故这 6 条坏样本与开盖事件延迟无关，
足以判定闭盖恢复未保持关闭。软件采样没有直接测量光输出，不把它描述为
已拍到的光学闪烁。

原始程序返回 `closed RTC return was not continuously blank in observed samples`，
该失败结论保留。第一条 thaw 后采样其实早于 D-Bus false 约 129 ms，
当时仍为 dark；这轮没有因较晚的 false 信号而漏掉更早的异常。
另发现的通用观察器时间边界问题须单独修正，不能拿来解释掉本轮真实坏样本。

精确 PowerDevil 6.7.3 源码只比较 `/sys/class/wakeup/*/wakeup_count` 来推断
来源；实机 11 项计数均为 0，因此已由内核 IRQ166 确认的 RTC 返回仍会被它
当作 Unknown。其 DPMS 恢复路径对 Unknown 直接发送 On，不检查闭盖；
现有 light 守护只能随后补发 Off。这与现场的异步 On/Off 竞争相符。
针对这一入口的同版、机型限定 PowerDevil 补丁已在本轮之后安装，详见下节。
补丁不修改来源分类、KWin 的 Hall 过滤或用户电源偏好；部署没有改变
RTC-01 的失败结论；修复后 RTC-02 的局部观测与记录缺口见后文。

RTC-01 结束时 RTC 与临时策略已清理，日常 opt-in 不存在。开盖后的静态截图确认桌面图标
及 Panel 完整，KWin/Plasma/light/brightness 保持原 PID、0 restart；没有新
DSI 或存储错误，完整 post-open deployment preflight 通过。此次截图不能
代替用户对实体显示、触摸的反馈，该反馈仍待补充。

证据为 `outputs/sleep-completion-20260907/rtc-01-evidence.tar`、
`rtc-01-independent-analysis.json`、`rtc-01-after-open.png`；后文 Hall-04
段落保留其当时计数；RTC-02 结束后当前计数已为 3/0，下一轮仍须重新读取。

## PowerDevil 修复部署与 RTC-02 睡眠前核验

2026-09-07 **04:02:58 JST** 安装
`powerdevil-6.7.3-1.fc44.pocketds1.aarch64`。安装前 `rpm --test` 与安装后
`rpm -V powerdevil` 均返回 0，电源配置保持原样。版本仍为 Fedora 44 的
PowerDevil 6.7.3，仅增加本地包 Release 与恢复亮屏准入补丁；没有升级
Plasma 或设备上的 Qt。冻结 RPM SHA-256 为
`e450007842afc9283fad14679be6c6494ed64768e24b8cd0a5cd0256ba6bbd13`，
DPMS 插件为 73,504 字节，SHA-256 为
`49358da688b5c2a661af11c3a2f3533a68973584d9a8bb2c4dfe7c2a0a713744`。

补丁仅在 Pocket DS 恢复时额外查询真实 logind `LidClosed`，上限 1000 ms；
只有有效布尔 `false` 才发 On，闭盖或查询失败保持关闭，空闲计时仍恢复。
源码 tarball 的 KDE 签名已验证；下载的 Koji RPM/SRPM 本身未签名，不能把
RPM 摘要或文件验证成功描述为 Fedora 包签名通过。完整来源、构建身份和
真实 Qt／私有 D-Bus `%check` 通过记录见
[PowerDevil 组件说明](../../components/powerdevil/README.md)。

新的 daily guard 在实机验证了三个不同阶段：

1. 原版插件仍在磁盘时，因不匹配锁定插件 SHA 而拒绝。
2. RPM 已更新，但原 PowerDevil PID **1650** 仍映射删除的旧插件时，
   再次拒绝；没有把“新文件已安装”当作运行修复生效。
3. **04:03:11 JST** 只重启 PowerDevil，PID 变为 **25490**；随后
   `verify-deployment` 通过，实际 bus owner 为 `:1.1291`，插件映射
   device **8:13**、inode **5898388**，与上述已验证文件一致。

KWin **1455**、Plasma **11295**、keyboard **1799**、light **1801**
保持原进程，检查时这几项和新 PowerDevil 均 active、`NRestarts=0`。
该部署节点设备与本地源码均为 `851aabb`。该源码在 Linux 设备的独立验证中，daily
**56 项**、安装事务 **16 项**全部通过且无 skip，`scripts/lint.sh` 返回
`[lint] OK`；保留其第 292 行命令替换忽略 null 字节的已知非失败警告，
不把日志写成零警告。测试没有替代真实睡眠。
设备端 v5 根目录已暂存新工具，
只读 RTC-02 preflight 返回 PASS；它在开盖状态执行，
`closed_display_off_checked=false`，不是闭盖保持熄灭的验收。
工具身份与该次 preflight 绑定如下：

- `attended-lid-cycle.py`：`5dee48f6c676f475e7490fda65887e3e2c00b740d7043b321ca8ac221ae3f02a`。
- `observe-lid-cycle.py`：`ea03635ed79e0d9c9292772f4f5bab293573debb435f04cb875aadb6fc4a7c93`。
- `pocketds-daily-suspend.py`：`27023cbeadae9211458baa0caa8f63603d3beff86703418eba088b4558e83e59`。

此睡眠前部署节点为 boot `db67a328-d3a0-43d3-8573-bf803dfdee20`、
成功／失败计数 **2/0**、RTC 空、daily opt-in 不存在、桌面 `CanSuspend=false`。
这是 RTC-02 执行前的状态，后续周期结果及候选启用后的变化分别记录于下节。
安装、运行身份检查和离线测试通过均不替代实体检查。

安装事务及 v5 清单/preflight 保存在
`outputs/sleep-completion-20260907/powerdevil-install-and-v5-preflight.tar`；
安装后运行身份记录为 `powerdevil-runtime-verified.json`，源码测试记录为
`device-validation-851aabb.txt`。安装后、RTC-02 前的桌面截图
`powerdevil-patched-before-rtc02.png` 可见桌面图标和完整 Panel，但处于闲置
变暗状态，Panel 时钟仍缓存为 03:31；它不是采集时间或实际触摸恢复的证明。

## RTC-02：闭盖观测区间全暗，开盖超出记录范围

本轮在已验证的 patched PowerDevil 下完成 deep **120.793 秒**，由 RTC
**IRQ166** 返回，同 boot 成功／失败计数变为 **3/0**。guard 记录恰好一组
`PrepareForSleep(true→false)`，随后已清理自己的 RTC。

独立复核从 BOOTTIME−MONOTONIC 跳变后的第一个样本开始计入，共 **955 条**，
覆盖 **95.571 秒**。这些样本均为上、下屏 `bl_power=4`，下屏 raw/actual=0，
logind 仍为 closed；没有缺字段、无效／跨 thaw 窗口或非暗值，也没有读取上屏
`actual_brightness`。首条记录比 PFSfalse 早 **0.165 ms**，其实际读取开始
比 false 早 **0.598 ms**，已纳入检查；最大相邻读取空隙为 **105.932 ms**。

原始 `result.json` 仍报 `missing or repeated physical lid close/open evidence`，
不能改写。观察器于 **04:22:39.981 JST** 结束，后续 logind 日志才在
**04:23:43.695708 JST** 记录开盖，相差 **63.714 秒**。它只录到 raw close，
没有 raw open；最后一条背光样本到该开盖日志约 **63.937 秒**未被覆盖。
因此结论只能是已记录区间持续为暗，不能补造完整开盖事件，也不能排除采样
间隔内或未记录尾段的光学闪亮。原始整轮自动验收继续标为 failed。

这次睡眠起点至所提供日志结束没有新 DSI／面板或存储错误，但保留 USB
HS-PHY、IRQ203 affinity、phy0 PM 警告，以及睡前 DPMS off 校验重试和
开盖后的 balanced profile 校验重试。没有 PowerDevil gate 分支的调试日志，
不能声称该分支已有独立日志确认；预先核对的实际插件与观测行为符合补丁预期。

用户随后明确反馈“我一开盖瞬间就亮了”，据此记为**物理开盖亮屏已确认**；
这次触摸没有明确复核。开盖后截图可见桌面图标及铺满下屏的键盘，软件回读
两屏 on。此时 PowerDevil／KWin 配置仍分别为原 SHA
`fe8d5dd592ab90cf9dc1f10b876c49cb3cb2fc1088a2a9431be29af0756219f7`、
`0bcd7440ea1374cf3de780a309039b4183fcf91402cc71d7b6bd4f7118735c74`。
该反馈作为后续人工记录补充，不回写原始回执中的 `physical_display_acceptance=pending`。

证据为 `rtc-02/pds-lid-cycle-20260907-rtc-02/`、`rtc-02-evidence.tar`、
`rtc-02-local-review.json`／`.md`、`rtc-02-after-open.json`／`.png`，均位于
工作区 `outputs/sleep-completion-20260907/`。局部复核保留输入文件 SHA，
没有改动原始 result 或观察记录。

## 原生日常合盖候选准备历史（native-01 之前）

RTC-02 结束后，在开盖下执行候选 enable 成功，回滚前像保存在
`/var/lib/pocketds-linux-kit/daily-suspend-20260906T192844Z-37076`。
安装器仅为 AC/Battery/LowBattery 三档固化 `SleepMode=1`，保持当时
`LidAction=0`、`PowerButtonAction=64`、`AutoSuspendAction=0`、`PowerDownAction=0`。

第一次从 SSH 调用 lid helper 的 `set sleep` 因不满足 local/active 授权被
正确拒绝，三档 LidAction 仍为 0。随后通过 user manager 的本地桌面上下文
调用成功，才将三档 `LidAction` 改为 1；上述电源键、空闲与 PowerDown 原值
继续保留。此时 PowerDevil 配置 SHA 为
`9c2640672a37e700858cc251a1598a40400bb8a12f4aecd2ebc85542252546b6`，
KWin 布局 SHA 未变。

选中 sleep 后的候选核验中，daily opt-in 已存在，PowerDevil `CanSuspend=b true`，
lid helper 回读 `mode=sleep`、`sleep_available=true`、`lid_open=true`。PowerDevil PID
**37410**、light **37377** 是安装器能力重读后的进程；KWin **1455**、
Plasma **11295**、keyboard **1799** 保持原进程。运行插件仍为批准的
`49358da6…a713744`，当前 owner `:1.1691`，计数 **3/0**，RTC 为空。

原生监督器的授权主体检查已由 `77d8c89` 修正完成：只改 native harness、
测试和说明，改为检查真正 PowerDevil 主体，未改变 daily 或 polkit 策略。
**39 项测试在 Mac 与 Linux 均通过**。v6 根目录中的原生工具 SHA-256 为
`54371ba4daf71b90bce0b57c4c9e98d4a9a360105234ce19eddd455c1bdd1f79`。
实机 `native check` 已 PASS，核对到 owner `:1.1691`、PID **37410**、
start time **595443**、UID **1000**，`pkcheck authorized=true`、
`CanSuspend=true`；返回 `write_executed=false`。

native-01 参数仍绑定同 boot、起点 **3/0**。**run 尚未执行，RTC 尚未预置**，
正在等待用户确认从实际合盖起 30 秒开盖的准备信号。只读 check 通过不是
一次原生合盖周期；当前 enable 属于受控候选准备，正式 daily 验收仍未完成。
v6 manifest、args 和 preflight 已归档至
`outputs/sleep-completion-20260907/native-v6-preflight.tar`。

随后 `dcb1798` 补齐监督器设置 RTC 后的超时／中断退出：尝试 arm 后任何
不完整退出先写本次启动的 BLOCK，再撤销订阅和释放 delay inhibitor，保留
RTC；已知迟到或无效的 prepare 也在释放 inhibitor 前阻止后续睡眠，已有
监督错误时不会因收到 true→false 而清除 RTC。正常完整开盖不写 BLOCK，
arm 前的拒绝不抢占外来 alarm，阻止措施本身写入失败则明确记录错误。
**42 项测试在 Mac 与实机 Linux 均通过**，并经独立代码复核。
新 v7 暂存工具 SHA-256 为
`e52c3464be81afacc3257d206108195db6fe871f98b301fb190e5d9db7d47cc5`。
v7 的 manifest、下一轮参数和等待状态保存在 `native-v7-waiting.tar`；没有
运行监督器或预置 RTC。v6 的通过记录保留其当时语境，下一轮应使用 v7，
在恢复已审阅的 sleep 选择后重新检查，不能把纯测试当作真实硬件周期。

用户尚未回复 30 秒步骤的准备确认。等待期间，已通过 user manager 的 lid
helper 执行 `set connected`，暂时收回自动合盖动作；最新回读为
`mode=connected`、`sleep_available=true`、`lid_open=true`，PowerDevil 配置
SHA 回到 `312e8ef97923e6125d98a9400db187d348ea7e86e3ce6888e7165c09c3fc483a`。
daily opt-in 和手动休眠能力仍保留，电源键 64、空闲 0 不变；不能描述为 daily
完全关闭。计数仍 **3/0**、RTC 空，native-01 未 run。

下一轮收到准备确认后，只需通过同一 user manager 上下文重新 `set sleep`，
核对配置恢复到已锁定的 `9c264067…52546b6`，再做 fresh check 和监督预置。
无需重复执行 enable installer；等待期间不得直接沿用旧的 sleep 状态检查。

准备记录为 `native-01-activation.json` 和 `native-candidate-preparation.json`；
前者首次 SSH 拒绝后的配置哈希与目前主动恢复 connected 相同，但这不表示
中间成功选中 sleep 的操作没有发生，必须按上述时间顺序区分状态。

## native-01：界面正常，但熄屏准备顺序导致睡眠未执行

2026-09-07 收到用户“开始”后，通过 user manager 恢复 `sleep`，重新
核对实际 PowerDevil 主体和部署条件，并运行 v7 原生监督器。
本轮使用同 boot `db67a328-d3a0-43d3-8573-bf803dfdee20`、起点计数 **3/0**；
08:48:14 JST 预置 300 秒后备 RTC，08:48:30.367 logind 记录真实合盖。

`PrepareForSleep(true)` 于 08:48:30.657 到达。light 发出 Off 后立即回读
DPMS，08:48:30.813 仍看到两屏 On，随后记录准备失败并释放自己的 delay
inhibitor。这里的命令提交成功不等于 KWin 的异步关屏已经完成。
08:48:31.133 的样本中两屏 `bl_power` 已为 4，但下屏 raw/actual 仍为
1591；08:48:31.560 根 `ExecStartPre` 正确拒绝
`closed displays are not physically blanked`。原路径因为早先 DPMS 校验失败，
连下屏 raw 归零补写也跳过了。实机 logind 延时预算为 **5 秒**。

08:48:31.945 收到 false，成功／失败计数仍为 **3/0**，没有新 deep 周期。
监督器在设备仍闭盖时先写 BLOCK，再清理自己持有的 RTC；随后 PowerDevil
重复尝试均被 BLOCK 拒绝。08:49:16.987 才收到真实开盖，17.645 两屏 On
核验通过。light 中较早的 `opened` 文本是状态恢复路径日志，不能当作物理
开盖证据。用户随后回答“已完成，全部正常”，明确确认双屏、两块触摸、
桌面图标和下屏 Panel；此反馈不覆盖未发生的实际睡眠。

原始 `result.json` 的 `kernel_cycle=incomplete`、闭盖返回错误与 failed
服务结果全部保留。原生事务 true→false 可以包含睡眠前中止，不能笼统
写成内核唤醒。针对 light 准备事务的修复见下节，根检查不放宽；维护等待时已
通过 user manager 切回 `connected`，失败 BLOCK 保留，RTC 空。

证据为 `native-01-evidence.tar` 和
`native-01/pds-lid-cycle-20260907-native-01/`，包含原始 supervisor、观察器、
服务日志、内核日志、结果及单独保留的 `runtime-block.json`。

## native-01 准备事务修复部署

`2d516a9` 将 light 的 PrepareForSleep 路径改成有界准备事务：只提交一次
Off（可以复用普通合盖路径刚提交的请求），等待 fresh DPMS Off，再尝试
下屏 raw 归零。既有 root backlight helper 在 `bl_power=0` 时明确拒绝，
所以这一步不能提前执行；若内核电源缓存仍未收敛，只重试下屏归零。
必须核对两屏 `bl_power=4`、下屏 brightness/actual 均为 0，再复查仍闭盖，
才报告准备成功并释放 delay FD。普通 light standby 的 1..4 判断保持原样。

准备期间取消普通 debounce，以代际标识隔离旧回调。false 到达时先撤销旧
事务，再获取下一次 delay FD，旧回调不能继续熄屏或释放新锁。开盖只取消
light 的闭盖准备，不冒称可以否决系统睡眠；开盖手动睡眠仍由原生策略处理。
本地 4 秒期限从收到回调起算，子调用共用剩余预算；此前普通同步调用可能
拖延回调的既有边界未重写，不能声称始终拥有 logind 全局 5 秒中的完整
4 秒。最终根物理检查仍保留，不因本组件超时而放宽。

在设备私有隔离目录中使用实际 Gio/GLib，**51 项测试全部通过、无 skip**。
fixtures 分开模拟 KWin 过渡、内核 power 变化与下屏归零，覆盖持锁、早期
root 拒绝及重试、开盖、迟到回调、新 FD 和共享期限。Mac 为 50 通过、1 项
真实 Gio ABI skip；不能把 Mac 数字写成无 skip。设备 `scripts/lint.sh`
返回 `[lint] OK`，保留已知第 292 行 null 字节警告。

**09:07:33 JST** 仅原子替换已核对前像的 light helper 并重启此服务。
安装源码 SHA-256 为
`9da73427dc4bc85a7871df4883d0fb828c0b0897532ab225aa71a0733c93bb00`；
light PID 从 **37377** 变为 **113053**，active、0 restart，09:07:34 记录
开盖 ready；logind 清单确认新 PID 已取得 sleep delay inhibitor。
KWin **1455**、Plasma **11295**、PowerDevil **37410**、keyboard **1799**
保持原进程，KWin 与 connected 电源配置 SHA 不变。安装后
`verify-deployment` 再次通过，计数仍 **3/0**；这不是新休眠周期。

备份与安装回执位于设备
`/home/pocketds/.local/state/pocketds-linux-kit/light-prepare-native01-20260907`。
native-01 原始归档另持久保存在 v7 根目录，BLOCK 没有删除、RTC 为空。
等待用户保存工作并确认 USB 就绪后正常重启，验证持久条件；新一轮参数须
重新绑定 boot、计数与上述 light SHA，不能复用 native-01 的原参数。
本机新增证据：`light-prepare-install-result.json`、`light-prepare-arm-tests.txt`、
`light-prepare-lint.txt`、`light-prepare-review.patch`、`native-01-review.json`。

### 正常重启尚未完成

用户确认保存工作、保持开盖及 USB 已连接后，主代理请求正常 reboot。
请求前再次确认原失败归档已持久保存在设备 v7 目录，且与本机副本 SHA
`ee675fd13076987bff03089f35f47a12d881e2be9c8f57fb2949eb2575b4a835`
一致；失败 BLOCK 保留，RTC 空，修复 helper 摘要匹配，原始救援镜像
`85b41b68…7252673` 也再次核对。

随后数分钟 SSH 与 ping 无响应。用户照片可见 `Reached target reboot.target`，
以及 `systemd-shutdown: Syncing filesystems and block devices`、
`Sending SIGTERM to remaining processes`，最后是 journald 收到 PID1 的
SIGTERM。它仍是旧 boot 的关机收尾，不能据此归因于新内核启动失败；
画面没有可见 I/O 错误，也不能据此证明最终卸载或落盘已经完成。
照片保存为 `reboot-shutdown-stall-photo.png`。

主代理给出单次人工恢复步骤后，用户明确确认长按关机再正常开机，随后进入
桌面。没有操作 fastboot boot/flash，没有删除 BLOCK 或做文件系统修复。
旧失败记录已归档；新启动的 `/run` 自然重新建立，不是人工清除旧保护重试。

### 强断电后的启动核验

新 boot 为 `d84d307d-6b0d-4014-afaf-a6c86e1d6f57`，计数 **0/0**。
先取易失内核日志，未见 UFS／EXT4／I/O 读写错误，root 与 boot 均正常 rw，
pstore 空；journal 报非正常结束并自动更名重建，与强断电相符，但不是关机
故障的根因证据。启动时的时钟及 Q6APM 警告在旧启动也出现过。
独立核对持久配置、v4、实际 PowerDevil 插件与 KWin Hall 过滤全部通过，
`console_suspend=Y`、`pm_async=0`、deep 均保留，light SHA 仍为已测试的新版本。

KWin **1420**、Plasma **1566**、PowerDevil **1600**、keyboard **1752**、
light **1763** 均由本次启动自动启动，active、0 restart，没有为通过检查
再次重启它们。KDE 活动仍为 `90559774-0de3-4310-b361-b4f32b543519`，
双屏 On、原布局配置 SHA 不变；实际截图可见 13 图标和完整下屏 Panel。
这些证明此次开机与配置持久性，不代表正常 reboot 流程已修好或真实触摸已验收。

旧 boot 关机记录独立复核：light 于 **09:09:37.985592** 停止，耗时约 **7 ms**；
PowerDevil、KWin 及用户会话随后正常停止，没有停止超时或卡死调用栈。
持久日志止于 Syncing，但用户照片已经进入后续 SIGTERM 阶段，不能把日志
末行误认成实际故障点。晚期关机未自动完成的原因仍未确定，不能归咎于
已正常退出的新 light，也不能根据一次未知故障盲目追加内核补丁。

当前保留 connected 等待用户实际测试就绪；v7 中新增 `native-02-args.json`
绑定新 boot、计数 0、light `9da73427…c93bb00` 和新 `/run/...native-02` 回执，
没有运行监督器、请求睡眠或预置 RTC。本机证据位于 `recovered-boot/`：
三份内核／关机日志、`validation.json`、`review.json` 及 `recovered-boot.png`。

## native-02：原生合盖休眠与开盖唤醒通过

用户再次回复“开始”后，在开盖、新 boot 计数 **0/0**、RTC 空的状态重新
核验部署，并通过 user manager 选中 sleep。v7 `native check` 检查实际
PowerDevil owner `:1.39`、PID **1600**、start tick **1868**、UID1000，
原生 suspend 授权及 `CanSuspend` 均为 true，然后才运行监督器。
本轮 unit invocation 为 `49d2ad0120724589bc0c310bb9641fe7`，临时后备
RTC 为 **1788759760**；日常路径没有安装定时唤醒。

原始 observer 记录恰好两个 Hall 事件，使用 BOOTTIME 时钟，关→开间隔
**32.770625 秒**。唯一一组 PrepareForSleep true→false 的 BOOTTIME 与
MONOTONIC 差为 **29.689557 秒**，与系统日志的真实 deep、计数由 0/0 增至
**1/0**一致。唤醒源为 **IRQ202 / msmgpio166 / Lid Switch**，不是后备 RTC。
开盖后继续观察约 35 秒，没有第二次睡眠；监督器正常结束，unit `Result=success`。
匹配回执源码 SHA 的独立离线判定重放同样通过。

本轮 light 先等 DPMS，再完成下屏归零；PFStrue 后约 **1.269 秒**已有两屏
`bl_power=4`、下屏 raw/actual=0，冻结前持续成立。14:38:05.700 记录
`sleep-display-prepared okay=true reason=dark`，root pre 随后通过。
保留一次下屏 blank 子命令 0.5 秒超时日志：后续读取证明实际状态已完成，
没有凭命令返回值推定成功。仍有原有 USB HS-PHY、IRQ203 affinity、phy0 PM
警告及控制工具 AT-SPI 文本，不能描述为零警告；本周期没有新 DSI／存储错误。

用户随后明确回答“已完成，全部正常”，确认双屏、两块触摸、桌面图标和
完整下屏 Panel。原始 result 中物理字段的 `pending` 保留，由独立
`native-02-user-acceptance.json` 记录人工结果。`closed_black=not exercised`
表示本轮 Hall 不覆盖闭盖 RTC 返回；嵌套的 `prior_hall_physical_acceptance=FAILED`
是旧布局检查器的历史标签，不是本轮计算失败。

最终 installed daily `verify` 通过；`last-resume.json` 为 `resume=passed`，
29.69 秒、1/0，RTC 空、pending/BLOCK 无，lid helper 回读 `mode=sleep`、
`sleep_available=true`、开盖。KWin **1420**、Plasma **1566**、PowerDevil
**1600**、keyboard **1752**、light **1763** 保持原进程，active、0 restart，
没有用重启服务掩盖恢复异常。KWin 配置 SHA 不变，PowerDevil 配置为选中
sleep 的 `9c264067…52546b6`，电源键／空闲偏好继续保留。

原始证据为 `native-02-evidence.tar` 及 `native-02/pds-lid-cycle-20260907-native-02/`；
另保留 `native-02-light-journal.txt`、`native-02-service-journal.txt`、完整及摘出的
kernel 日志、`native-02-final-state.txt` 和独立用户验收 JSON。原始回执不改写。

## 实际部署与运行身份

- 已通过 `tools/kernel-ab/install-dsc-v4.py` 安装事务将 v4 持久写入
  `/boot/boot/Image`。目标 boot 容器 SHA-256 为
  `57322cb6dc3bce822289bcd6bde5e5934362efc2547120ebc88be0eea7a89d92`。
  这不同于 raw `Image` 的哈希，不能混用。
- 安装后正常启动的 boot ID 依次为
  `6847be2d-cd54-4df4-8a9e-86e33ae7172a`（Hall-01／02）和
  `db67a328-d3a0-43d3-8573-bf803dfdee20`（Hall-03／04）。
  release 为 `7.1.12-pdsdiag.20260905.aarch64`，v4 notes SHA-256 为
  `05ffb67550d15176df5302978a51cf1834fa70869e663f8e01c440c253116092`。
- 新 libinput quirk、light-standby、brightness 和 lid-mode 组件已部署。
  当前 KWin 的 gpio-keys 实例实测 `lidSwitch=false`、`keyboard=true`、
  `enabled=true`；这次有运行中 KWin 的证据，不仅是新建 libinput context 的检查。
  quirk 只过滤 compositor 看到的 `SW_LID`，不删除原始内核 Hall 事件。
- Hall-04 当时只使用运行时 `console_suspend=Y`。随后主代理已将
  `80-pocketds-pm.conf` 持久部署到 `/etc/tmpfiles.d/80-pocketds-pm.conf`，
  只对该文件执行 `systemd-tmpfiles --create`，读回 `console_suspend=Y`、
  `pm_async=0`。持久配置已经落盘，随后 RTC-01 已实际睡眠但闭盖验收失败；
  **部署后尚未再次正常重启验证**，不能把 Hall-04 写成持久部署后的验收。
- RTC-02 期间 daily 仍关闭，结束后 RTC 清空。随后为原生合盖测试才启用
  daily 候选并选择过 `sleep`；等待用户准备时已主动恢复 `connected`，
  保留原电源键／空闲偏好。native-01 随后在睡眠前中止；当前 connected 与
  失败 BLOCK 状态见上节，不再沿用测试前的可用性结论。

持久 PM 配置的前／后 SHA-256 分别为
`166b7666359f25ca5538866bda947626154cbe9cba06c1e2a6aa57a39920faa8`、
`d63098bf6c2304eec09ca9c4e31f976f9428dc9547c3ce3a73ea165ba6988a96`；
设备端备份及结果位于 `/var/lib/pocketds-linux-kit/console-suspend-20260907`。
该次部署时设备仓库为 `080e85b` 且工作树干净；Linux 隔离测试中 daily 38 项、
native 24 项通过，设备 `scripts/lint.sh` 也完成并返回 `[lint] OK`。
这些是代码／事务测试，不是原生睡眠或硬件验收。
后续 PowerDevil 部署节点已同步至 `851aabb`，其运行身份见上一节，
不要把早期源码版本或测试计数当作当前安装清单。

## 四次尝试的有效结论

| 尝试 | 实际结果 | 验收资格 |
| --- | --- | --- |
| Hall-01 | 未在等待期内合盖；`physical-close window expired` | 未形成睡眠周期，不计成功或唤醒验证 |
| Hall-02 | 实际 deep 23.171 秒，IRQ 202（GPIO 166 / Lid Switch）唤醒，成功／失败计数 1/0；用户确认上屏黑、下屏正常 | 物理显示未通过，且观察器主动 DSI 读污染了样本，不能用于干净的驱动归因 |
| Hall-03 | 干净重启后使用被动观察器，仍因未在等待期内合盖超时 | 没有睡眠周期，不是被动观察器或显示恢复的成功对照 |
| Hall-04 | 被动观察器 + 运行时 `console_suspend=Y`；实际 deep 35.46 秒，IRQ 202 唤醒，计数 1/0；用户确认双屏与触摸正常 | 有效的单次人工物理确认；程序报 `Hall opening did not follow the agreed 30-second schedule`，自动验收仍为 error |

Hall-02 醒后虽然 KWin 两个输出均 enabled/on、两个 `bl_power=0`，
用户看到的上屏仍然不亮。这再次说明逻辑输出状态不能替代物理显示确认。
其 KWin 配置 SHA-256 仍为
`0bcd7440ea1374cf3de780a309039b4183fcf91402cc71d7b6bd4f7118735c74`，
不能归因于输出布局文件被改坏。

Hall-04 的采集记录未发现 DSI DMA／frame timeout 或存储错误；显示和触摸的
“正常”来自用户后续实机反馈。原始 `result.json` 保留当时的
`physical_display_acceptance=pending` 和时间窗口 error，不追改原始凭据。
该周期不能替代原生 PowerDevil／日常 guard 整条合盖链路的验收。

## 观察器与内核候选的边界

Hall-02 旧观察器每 100 ms 读取上屏 `actual_brightness`。ICNA35xx 的这个
属性调用真实 DCS 读，会先发送 `0x37`（Set Maximum Return Packet Size）；
它不是被动缓存字段。现场出现对应的 DMA 超时，且当前驱动错误返回会遗留
被清除的 LPM 标志。因此该观察方式既可能产生额外错误，也可能干扰后续
面板命令；不能把 Hall-02 当成“完全无干扰仍复现黑屏”的证据。

观察器已去掉上屏 `actual_brightness` 读取，Hall-03／04 的被动版本 SHA-256 为
`442516f3ccbe7a723030c3d929d133521345d470dc935550cadaa5921bf3566d`。
后续继续读取缓存亮度与电源状态，不重新引入上屏主动亮度查询。

候选 [0005](../../tools/kernel-ab/patches/0005-panel-icna35xx-preserve-backlight-mode-flags.patch)
仅修复两个亮度回调在成功／失败后恢复进入时 `mode_flags` 的问题。
它通过了精确匹配 v4 面板源码上的离线应用／反向恢复检查，**尚未编译、安装或运行**，
没有参与 Hall-04。锁、关屏缓存与并发限制见
[候选说明](../../tools/kernel-ab/ICNA35XX-BRIGHTNESS-CANDIDATE.md)。

Hall-03 未真正睡眠，Hall-04 又同时具备被动观察和 `console_suspend=Y`；
当前只有这一组合的一次物理成功，不能分别证明观察器或 console 参数就是
此前上屏黑屏的唯一根因。

## 后续桌面异常的独立处理

Hall-04 后反馈的桌面图标缺失、Panel 露边，主代理核对为 Plasma 启动前已有的
失效 KDE 活动状态。该状态先于此次 Hall 周期，不能据出现时间把它归因于休眠。
主代理将 `SetCurrentActivity` 指向唯一有效活动，并执行有时间边界的 Plasma
恢复；后续实机截图确认 13 个图标与完整 Panel 均恢复。没有用此次桌面恢复
操作来替代 Hall-04 的双屏物理唤醒确认。

## 已完成与保留的边界

1. 持久 PM、v4、运行插件、KWin 输入身份和 light 已在新启动中核对；
   该次通过来自强断电后的开机，正常 reboot 的晚期停滞仍单独待查。
2. native-01 准备顺序修复已通过 51 项 ARM 测试和 native-02 真机完整周期，
   实际 deep、Hall 唤醒与双屏／触摸／图标／Panel 人工验收均通过。
   当前合盖 sleep 已保留，不需要为本次基础链路继续重复相同测试。
3. RTC-02 的记录尾段缺口和原始 failed 保留；本轮 Hall 不替代闭盖 RTC
   场景，也不把一次约 30 秒睡眠扩大为任意时长或全部外设的稳定性结论。

## 本机证据索引

工作区 `outputs/sleep-completion-20260907/`（不属于仓库内路径）：

- `boot-and-preflight.txt`：持久安装目标／哈希及首次正常启动 preflight。
- `pre-deployment.json`：部署前状态快照，不能作为部署后的最终状态。
- `hall-01-02-evidence.tar`：Hall-01／02 参数、观察记录、结果及黑屏现场采集。
- `hall-03-04-evidence.tar`：Hall-03／04 原始结果、被动观察记录及 console 参数事务。
- `rtc-01-evidence.tar`、`rtc-01-independent-analysis.json`：RTC-01 闭盖失败的原始记录及独立复核。
- `rtc-02-evidence.tar`、`rtc-02/`、`rtc-02-local-review.json`／`.md`：RTC-02 原始失败回执和 955 条全暗样本的局部复核，保留未记录尾段。
- `rtc-02-after-open.json`／`.png`：开盖后的双屏软件状态、原配置哈希和桌面／下屏键盘截图。
- `native-01-activation.json`、`native-candidate-preparation.json`：候选 enable、首次授权拒绝和最终选中 sleep 的状态，均非原生睡眠回执。
- `native-v6-preflight.tar`：77d8c89 修正后的 v6 manifest、native-01 参数与实际只读 check PASS；未执行 run、未预置 RTC。
- `powerdevil-install-and-v5-preflight.tar`：修复包安装、配置保留、RPM 验证、v5 工具身份和 RTC-02 只读 preflight。
- `powerdevil-runtime-verified.json`：新 PowerDevil 的 bus/PID/实际插件映射、服务状态、原配置哈希和关闭的 daily/空 RTC。
- `device-validation-851aabb.txt`：Linux 上 daily 56 项、安装事务 16 项无 skip 通过及 lint 结果。
- `powerdevil-patched-before-rtc02.png`：修复包部署后、下一轮实际睡眠前的桌面截图。
- `hall-04-ui.png`、`hall-04-ui-activity-restored.png`、
  `hall-04-ui-shell-restored.png`：桌面异常与恢复后的实际截图。

用户物理显示／触摸反馈与主代理的后续状态核对属于本轮会话证据；它们与上述
原始机器记录分别保留，尤其不将 Hall-04 的时间窗口 error 改写成自动 PASS。
