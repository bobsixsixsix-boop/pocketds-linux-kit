# Panel 全界面与手柄测试部署记录

日期：2026-09-06。用户已明确授权部署、提交，随后要求同时恢复 Codex 额度显示。

## 部署范围

- `1a2e292`：当前原生 Panel、弹窗、键盘与触摸板细化，以及临时独占手柄测试。
- `d2f8bab`：实机验收发现并修复 D-Bus Properties.Set 的 variant 封装。
- 主 Panel applet ID 28 位于 screen 1，新三份 QML 已加载；新键盘和触摸板进程均已启动。
- 输入栈通过既有完整事务安装，41 个非保留输入文件与源码及权限完全相符；InputPlumber 未重启。

UI 与客户端通过现有通用 v1 原子事务引擎部署：先客户端/触摸板两个文件，再两组件/键盘/main 四个文件。所有旧前态与基线源码一致，旧 panelctl 用原文件名编译后亦逐字节匹配。默认 PDS 十文件计划包含本轮无关且普通用户无法读取的 polkit 文件，因此没有运行默认计划，也没有修改其权限；显式六文件计划没有放宽事务校验。

## 真实接管与恢复

第一次实机请求暴露此前模拟测试未覆盖的 D-Bus 编码错误。关闭自动 introspection 后，未标记 variant 的 UInt32 导致 Properties.Set 生成 `ssu`，接口需要 `ssv`。第一次请求被拒，原 InterceptMode 保持0，最终记录 idle/blocked=false；没有强写模式或删除记录。失败证据保留。

修复后新增真实 dbus-python 消息编码回归，验证进入2、恢复0/1均生成正确 `ssv`；Linux 隔离测试63项全部通过。然后通过输入栈事务部署修正版。

2026-09-06 16:22:15 JST 的实机短会话由 pocketds 用户通过正式 panelctl 发起：

- 开始前读取21次实体快照，所有按钮松开、摇杆和扳机中性。
- begin 返回 starting，随后 poll 返回 active；0.296秒时交叉核实 InterceptMode=2、会话 blocked=true，实体按钮及六轴状态可读。
- end 返回 draining；其后0.466秒内确认 idle、blocked=false、InterceptMode恢复0、输入锁可取得，两服务正常。
- 全程 owner `:1.10`、profile、targets、SourceDevicePaths 保持一致，无输入注入或 profile/target 重建。

这验证了真实独占及恢复链路，不等于已经逐个按下实体键验收高亮。副屏底沿三枚系统键仍缺可靠逐键映射，未猜测接入。

## 激活与保留状态

键盘从PID1757更新为239443，触摸板从1891更新为239462。有界 Plasma 重载最终启动PID239746，新 Panel applet 已核实存在。

重载期间，中间 shell PID239660 在启动中以 SIGABRT 退出，日志同时出现 QProcess 销毁与 QGuiApplication 初始化相关错误。旧恢复脚本存在 KDE quit、systemd 自动重启与后续 unit stop 的交错窗口，和本次现象相容，但未认定为崩溃根因。最终恢复报告 healthy=true、preserve_violations=[]，后续shell保持稳定。最终 NRestarts=0 已经过 reset-failed，不代表整个过程没有中间失败。没有新的 Pocket DS QML 类型或引用错误；系统 KDE 亮度组件仍有此前已存在的 PopupDialog.qml 报错。

输入栈安装前正常退出了无游戏进程的 Steam 桌面客户端，验收后按原 desktop 模式恢复。没有强制终止游戏。

Boot ID仍为 `72a9286e-7eae-46cf-966a-6afc7d2102be`。InputPlumber PID573、KWin服务PID1434、GPU采样PID1361、亮度服务PID105800及其InvocationID均保持不变。没有重启设备、改变内核或执行合盖/深睡试验。

## Codex 额度显示

`060a62c` 修复额度采集器：自动发现 Linux 桌面应用自带的 `/usr/lib/chatgpt/resources/codex`，兼容 app-server 新增的外层 accountId/rateLimitUpsell 元数据，并优先读取 `rateLimitsByLimitId.codex`。只有校验后的额度字段进入缓存，不保存账号标识或会话内容。接口依据：[官方 App Server 额度接口](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt)。

使用现有 `install-codex-quota.sh --activate` 部署；额度采集、缓存验证与安装回滚三组回归通过，正式激活接受 age=2 秒的实时 app-server 缓存。16:30 JST 的正式 panelctl 状态为 available=true、fresh=true、周窗口已用91%/剩余9%；未提供的第二窗口保持null。timer已启用并运行，每5分钟刷新一次。原Codex PID184591保持运行，没有重启正在工作的Codex。

额度备份：`/home/pocketds/.local/state/pocketds-linux-kit/backups/codex-quota.3Nx0ZN`。核验记录 `quota-deployed.json` 与其余部署证据一起保存。数据随账号使用继续变化，本记录是部署时快照。

## 备份与证据

设备总记录：`/home/pocketds/.local/state/pocketds-linux-kit/backups/20260906-panel-controller-1a2e292`。

- `before.json`、`after.json`：安装前后状态及六个UI/客户端文件哈希。
- `live-capture-smoke.json`：首次失败原始记录。
- `live-capture-smoke-fixed.json`：修正后实际独占和恢复证据。
- `live-artifact-check.json`：41个输入文件与实际 Panel applet 校验。
- `deploy_panel_controller.py` 与 `wrapper.sha256`：显式事务目标映射，恢复时使用同一映射调用通用 rollback_transaction；不要对自定义计划使用默认十文件CLI。

精确 UI 前态：`~/.local/state/pocketds-linux-kit/ui-transactions/panel-controller-1a2e292-side` 和 `panel-controller-1a2e292-ui`。恢复须先确保 capture idle，再恢复 UI 四文件、客户端两文件，最后激活旧 GTK 和 Plasma。

输入栈事务：`20260906-161208-ae5c7adac298fb81` 与修正后的 `20260906-162023-14903cdf17cc6e47`，位于 `~/.local/state/pocketds-linux-kit/game-input-transactions`。越过运行时提交边界后的恢复遵守其前向恢复规则，不把旧输入代码与新运行记录混装。

本地证据副本位于工作区 `outputs/controller-test/deployment`。预览仍使用模拟输入图片，不能作为实机逐键测试照片。
