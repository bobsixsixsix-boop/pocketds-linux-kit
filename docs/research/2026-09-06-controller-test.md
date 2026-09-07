# 实体按键说明改为手柄测试

用户要求：重绘说明图、去掉备注；打开页面时暂时接管手柄，实体按键按下时对应图形亮起，能够确认实际输入。

## 页面

旧的双模式说明和组合键注释被手柄测试图替代。图形依据 AYANEO 官方 Pocket DS 实机照片重新绘制，保留真实的不对称布局：菜单/视图及 AYA/等号斜排，左上摇杆、左下十字键、右上 ABXY、右下摇杆，肩键和扳机位于机身上方。没有图内备注、映射说明或“待确认”文字。页面只保留必要的接管状态和关闭/异常重试动作。

`ControllerDiagram.qml` 是独立的声明式图形组件，只接受实际按钮和轴状态。18 个已确认的数字按钮分别高亮；双摇杆帽随四轴移动，L3/R3 按下另有反馈；两个扳机显示连续深度。图形没有 MouseArea 或键盘模拟输入，也不会因为用户点击图形就伪造亮键。未取得接管确认或状态过期时，图形回中立。

副屏下沿三枚系统键的真实位置已核实，但尚无可靠的物理键与原始编号对应记录。它们仅保留低对比轮廓，尚未接通高亮；已向用户提出逐键采集请求。没有把编号猜测写成已验证映射。独立电源/指纹及音量键不属于 Composite 手柄源，不纳入独占测试。

官方几何依据：[前面与底边照片](https://cdn.ayaneo.com/ayaneo/article/2025/08/18/41e71202508182118021515.jpeg)。更多已检查照片及被排除的原厂 APK 复用图记录在工作区 `work/controller-test-preview/layout-evidence.md`。

## 接管实现

通过设备已部署的 InputPlumber d3932cb4 的 InterceptMode=2 阻止 Composite 输出，保持当前 profile、targets 和用户选择的手柄/鼠标模式不变。该版本的拦截在 profile 翻译之后：仅监听翻译后的 D-Bus 信号会合并或丢失物理键、右摇杆等状态，而且 ui_osk/模式切换等 D-Bus 动作仍会发出。因此这里同时实现：

- 专用 root 服务持有现有输入切换锁，为页面管理短租约；Unix socket 只接收固定 begin/poll/end 请求，校验调用方和32位会话标识。
- 从当前 Composite 的 SourceDevicePaths 获取原始 evdev 节点，严格核验真实 sysfs 来源及 VID/PID。用只读 EVIOCGKEY/EVIOCGABS 快照取实体状态，不与 InputPlumber 争抢抓取，不注入输入。
- 在 root mode-listener 与用户键盘的硬件动作入口及延迟执行点检查共享拦截状态，并清空旧组合键和退出确认。测试中不会同时触发切换模式、窗口动作、亮度或呼出键盘。
- root 状态放在 `/run/pocketds-controller-test` 的不可由普通用户替换的目录内；模块读者不能自行按超时解除拦截。恢复由持有输入状态的服务决定。
- 页面每50ms通过小型本地接口续租并读取状态，最多一个请求在途；物理采样60Hz，短按高亮最低保留120ms。实际中性判断不使用这个视觉保持时间。
- 关闭、隐藏或停止续租后，先保持拦截，等待所有按键松开、摇杆与扳机中性稳定300ms，再恢复先前的拦截状态。300ms覆盖已核实的上游延迟释放队列。
- 快速关开复用正在排空的旧租约，确认旧会话 idle 后才创建新会话。过期响应不能点亮已关闭页面，也不能取消新请求。恢复失败不被前端标成成功。
- 独立服务的启动恢复与 ExecStopPost 处理异常退出留下的记录；恢复失败保留拦截，源断开按同一服务身份重新枚举，不能向另一个 InputPlumber 实例写回旧状态。

实现依据为已部署精确提交的源码，而非仅依赖最新 API 概述：[Composite 拦截流程](https://github.com/ShadowBlip/InputPlumber/blob/d3932cb4e0de3eb47688e381b5eb5334ebac6254/src/input/composite_device/mod.rs)、[D-Bus 事件转换](https://github.com/ShadowBlip/InputPlumber/blob/d3932cb4e0de3eb47688e381b5eb5334ebac6254/src/input/event/dbus.rs)。

## 实机只读核验

本轮在设备上读取到当前 SourceDevicePaths 为 event6、hidraw2、event5；InterceptMode 始终保持原来的0，没有启动测试独占或重启输入服务。sudo只读 ioctl 成功读取 InputPlumber 已抓取的实体节点：

| 来源 | 设备身份 | 已核实字段 |
| --- | --- | --- |
| 主手柄 event6 | 4001:0428，真实 USB sysfs 路径 | ABXY、LB/RB、菜单/视图、L3/R3；左杆0/1、右杆2/5、LT/RT为10/9、十字16/17 |
| MCU event5 | 1c4f:0002，真实 USB sysfs 路径 | 支持BTN0–BTN7；既有映射确认LC=259、RC=260、AYA=261、等号=262 |

LC/RC 的内部逻辑槽名称左右相反，图形依据实体编号，不据槽名称猜测。右摇杆使用ABS_Z/ABS_RZ，而非通用示例的RX/RY。证据保存在工作区 `work/controller-test-preview/physical-readonly.json`。

## 安装和验证

新 root 服务与共享 gate 模块纳入现有输入栈事务，旧捕获服务须恢复并停止后才能替换；新服务就绪后再恢复监听器。输入栈安装移到键盘服务升级之前，避免缺共享依赖。主安装、遥测更新、PDS-005 UI 事务、离线安装与导入实机代码等所有发布入口均包含两个新QML组件；安装时组件先于引用它们的main.qml发布。PDS-005 当前事务包含10项，保留通用v1格式与旧ASR事务兼容；旧8项UI包只允许验证和回滚，不能作为当前计划安装。

UI增量入口只保证页面文件完整，不会自行安装并启动捕获后端。完整功能须通过主安装的输入栈事务安装新root服务、gate模块及监听器，并发布更新后的panelctl与键盘代码。离线部署不在宿主机启动目标设备服务。

现有整体UI修改继续保留。设计阶段的原生图像使用真实QML和显式模拟输入传输。2026-09-06 已按用户要求部署；真实独占短会话与恢复通过，详见 [部署记录](2026-09-06-panel-controller-deployment.md)。实体逐键验收仍未完成。

最终验证计数、源码哈希与运行日志保存在工作区 `outputs/controller-test/verification.json`；新旧图形与状态对照为 `outputs/controller-test/panel-controller-test.html`。
