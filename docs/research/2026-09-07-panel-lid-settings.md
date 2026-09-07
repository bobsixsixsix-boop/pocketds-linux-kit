# Panel 数值对齐与合盖设置（2026-09-07）

本次沿用当前原生 Plasmoid，调整帧率与合盖小块，并增加受现有系统许可约束的合盖方式接口。

| 位置 | Before | After |
| --- | --- | --- |
| 首页帧率、合盖 | 标签与数值横排，数值挤向右侧 | 同样的 56 像素高度，标题在上、数值在下，分别相对整个小块居中 |
| 合盖弹窗 | 只设置联网待机后的自动关机时间 | 上方选择联网待机／休眠，下方单独设置联网待机自动关机时间 |
| 不可用休眠 | 无入口，也无状态说明 | 读取当前可用性，禁用不可用选项并说明原因 |

## 本次 UI 部署时的休眠状态（后续进展见新记录）

下段保留 UI 部署当时的状态。后续已持久安装 v4 并取得新的 Hall 实测，
日常休眠仍未开放；以 [9 月 7 日休眠验证记录](2026-09-07-sleep-validation.md)
为当前状态，不再把下段的 RAM-only 描述作为现状。

实机仍运行 v4 RAM 候选，内核 notes 为 `05ffb675…116092`；持久启动镜像仍为 v3。现有日常休眠 guard 拒绝该候选，opt-in 不存在，logind `CanSuspend=no`。这不是仅漏改版本号：真实 Hall 唤醒的上屏恢复仍未通过验收；后续成功是 RTC 先唤醒、再开盖。

详见 [Hall 唤醒记录](2026-09-05-hall-wake-disabled-output.md) 和 [已作废的旧日常休眠状态](2026-09-05-daily-deep-standby.md)。本次不修改 guard、启动镜像或系统休眠许可，也不执行睡眠实验。

## 行为与升级

- `pocketds-panelctl lid-mode status|connected|sleep` 调用用户态 `pocketds-lid-mode`。状态独立查询，失败或超时不阻塞主遥测，也不能继续使用旧的可用状态。
- 模式来自 AC、Battery、LowBattery 的实际 `LidAction`。切换只改这三个键，保留电源键、空闲、SleepMode 等设置；备份、锁、原子写入以及失败回滚覆盖配置更新。刷新前重新检查开盖状态。
- 选择休眠必须通过既有 daily guard、普通内存休眠模式和 logind 许可。Panel 的成功状态由命令完成后发起的新回读确认。
- 联网待机是合盖时熄屏并继续运行，保留系统的自动休眠规则；系统开放休眠时，弹窗会显示这一点。没有增加新的睡眠抑制器。
- light-standby 仅在实际选择休眠且许可通过时让出控制权；查询后重新读取开合状态，避免在查询过程中已经开盖却按旧状态熄屏。
- 主安装器保留已有 PowerDevil 偏好；单独运行日常休眠安装器时，新 helper 也纳入同一备份／回滚事务。

## 验证

- 当前 QML 通过真实 Plasma／Qt 离屏软件渲染，819×614。最终首页的帧率数值中心 x=385、合盖数值中心 x=598.5，与各自卡片中心一致；卡片仍为 56 像素高。正常弹窗 720×486，长错误提示弹窗 720×526，未发现截断、溢出或 QML 错误。截图使用模拟输入／遥测，不是实机交互截图。
- Linux 检查在独立 checkout 中执行。主脚本前半部分通过，旧 UI 契约的两项断言仍期待旧标题／表达式；同步这些测试后，从 UI 契约继续执行其余检查至 `[test] PASS`。没有重复执行已通过且未受测试修订影响的前半部分。
- 合盖 helper 26 项（包含真实 KConfig 临时文件读写）、Panel 异步／参数与偏好保留 15 项、light-standby 状态机 28 项、日常安装事务 8 项通过。安装后主遥测 schema 通过，`scripts/check.sh` 为 0 条警告。

## 部署记录

运行代码为 `38529d4`（功能提交 `1fe6f53`，后续补齐新 helper 的可执行位）。4 个安装文件均核对匹配：Panel QML、Panel C++ helper、用户态 lid-mode、light-standby。初次暂存因 source 执行位不匹配而在写入前拒绝；修正后使用新事务完成安装。

成功事务备份：`~/.local/state/pocketds-linux-kit/ui-transactions/panel-lid-20260907-v2`；运行验证：`~/.local/state/pocketds-linux-kit/backups/panel-lid-20260907`。

只重载 light-standby 与 Plasma。键盘、触摸板、KWin、InputPlumber 和手柄服务保持原进程；PowerDevil 偏好逐字节未变，开机 ID、休眠计数与系统许可未变。安装后的休眠设置请求以 `kernel_unverified` 拒绝，没有更改配置或执行睡眠。

本次没有新增 Panel QML 报错。Plasma 启动时仍有已在先前进程出现的 KDE 自带 brightness/PopupDialog.qml:96 空值错误；已单独归类，没有把它写成 Panel 新回归。
