# Pocket DS 本地硬件知识库

状态标记：**已验证**表示本机读取或重复实测；**上游事实**表示来自官方/上游
资料；**待验证**表示合理推测，不能作为产品数据展示。

## 平台

- **已验证**：AYANEO Pocket DS，aarch64，Fedora 44。
- **已验证**：2026-09-04 重装后当前内核为
  `7.1.0-100.20260717114522.pocketds.fc44.aarch64`；此前带 native-lid DTB 的
  验证内核为 `7.1.0-100.20260730172655.pocketds.fc44.aarch64`。
- **已验证**：Adreno GPU 设备为 `soc@0/3d00000.gpu`。
- **已验证**：GPU devfreq 支持 220 MHz～1 GHz，提供 `cur_freq`。
- **已验证**：MSM DRM 客户端 fdinfo 暴露 `drm-engine-gpu`、`drm-cycles-gpu`
  和 `drm-maxfreq-gpu`；必须按 `drm-client-id` 去重。
- **待验证**：fdinfo 只能表示可观测 DRM 客户端负载，是否能覆盖全部硬件负载、以及
  低开销汇总器的准确度仍需验证。逐进程全扫描不适合 Panel 高频轮询。

## 显示与背光

- **已验证**：DSI-1 连接，内核模式为 1080×1920；桌面通过旋转呈横向。
- **已验证**：DSI-2 连接，内核模式为 768×1024；桌面通过旋转呈 1024×768。
- **已验证**：当前 KWin 同时运行 DSI-1 165 Hz 与 DSI-2 59.999 Hz；硬件和
  DRM/KWin 支持异刷新率双屏，浏览器 60 FPS 不是硬件异刷限制的证据。
- **已验证**：KWin 当前逻辑区为上屏 1280×720（scale 1.5）、下屏 819×614
  （scale 1.25）。下屏原生横向分辨率仍是 1024×768；逻辑尺寸不能写死为 820×615。
- **已验证**：KWin 日志称两块 DSI connector 均无 EDID。
- **已验证**：上屏背光 `ae94000.dsi.0`，最大值 4096。
- **已验证**：下屏背光 `sy7758-backlight`，最大值 4080。
- **已验证源码对照**：Android 原厂 `msm_drm.ko` 的 SY7758 八组初始化寄存器与
  当前 Linux 驱动完全一致。Android 12-bit 路径会把亮度低 8 位完整写入 `0x10`，
  Linux 当前仅写 `brightness & 0xf0` 且最大值为 4080；缺失的 15/4095 最大范围
  只有约 0.37%，属于正确性缺陷但不足以解释明显偏暗。
- **已验证**：直接读写内核 backlight 是目前可靠路径。
- **已验证软件叠乘风险**：DSI-2 允许 KWin SDR 软件亮度时，低于 1.0 的输出
  brightness 会在 SY7758 硬件亮度之上再次衰减；独立硬件背光方案的 KWin/PowerDevil
  中性值必须是 100%，不是 60% 或各自的硬件目标百分比。
- **已验证**：此镜像上不要使用 `kscreen-doctor` 修改 DSI-2 亮度。
- **已验证**：当前会话可从 KWin 输出状态取得 165000 mHz 和 59999 mHz；Panel
  应标注数据源并将物理模式、逻辑尺寸、刷新率和应用 FPS 分开。
- **已验证**：历史 boot 中 KWin 出现成百上千次 DRM atomic commit `EBUSY`；它
  与黑屏/卡顿高度相关，但因果关系仍需显示矩阵 A/B。

## 电源与电池

- **已验证**：`/sys/power/state` 支持 freeze、mem、disk。
- **已验证**：`/sys/power/mem_sleep` 当前选择 deep，同时内核也暴露 s2idle。
- **已验证**：当前策略设置 `pm_async=0`，规避 Goodix 与 I2C parent 的 suspend
  callback 竞争。
- **已验证**：GPIO Hall sensor 位于 active-low `gpiochip4` line 166。此前由
  native-lid DTB 暴露为 `EV_SW/SW_LID`；当前重装内核缺少该 DTB 节点，已由仅在
  原生开关缺失时运行的 gpiomon/uinput bridge 恢复标准 `SW_LID`。
- **已验证**：deep 曾成功 suspend/resume；s2idle 曾停在 `PM: suspend entry` 后
  需要硬重启，因此仍是发行阻断项。
- **已验证**：电池 sysfs 提供 capacity、status、voltage、current、power、temp、
  health；UPower 可提供百分比、温度和 energy-rate。
- **已验证**：此驱动的 `power_now` 当前约 53 W，但 4.40 V 与约 0.01 A 只对应
  0.04～0.06 W；`power_now` 不可信。电压×电流与 UPower energy-rate 同量级，
  Panel 只使用前者并显式标记来源。
- **已验证**：`charge_full`/`charge_full_design` 返回无数据，UPower EnergyFull/Design
  为 0；在驱动修复前不得计算预计剩余/充满时间。
- **已验证源码根因**：SM8550 使用 SM8350-style property path；该路径注册并读取
  `BATT_CHG_FULL*`，但没有像 SC8280XP info path 那样赋值 `battmgr->unit`，零初始化
  的 mWh 因而让 `CHARGE_FULL*` 恒为 ENODATA。Qualcomm Android SM8550 同协议把
  两项直接映射为 charge；不得改报为 energy。
- **候选未运行**：PDS-017 单行补丁只在 property-protocol 分支初始化 mAh，已对
  精确下游源码 apply PASS，并在 Fedora 44/aarch64 完成 driver object、Image、319
  modules 和 417 DTB/DTBO 构建；因清空了原 RPM buildroot 专用 initramfs 路径，
  这是完整源码编译门而非可刷写发行产物。尚未启动或验证单位，且必须独立于 IFPC
  候选。
- **已验证用户态路径**：设备 UPower 1.91.3 会把 `charge_full*` 乘设计电压换算为
  Wh，并在缺少 `charge_now` 时按百分比反推当前能量；本机又缺少两项设计电压，
  因而会退回实时 `voltage_now`。候选启动后须把电压与 EnergyFull 联合记录，不能把
  “出现非零值”直接当作容量/ETA 正确。
- **已验证**：内部 Renesas xHCI 的 PCI `add` 发生在驱动 probe 前；内核
  `usb_hcd_pci_probe()` 随后会重新启用 wake，所以早期 add 规则无效。
- **待验证**：规则已改为精确匹配 `1912:0014` + `xhci-pci-renesas` 的 driver
  `bind` 事件，当前会话已严格核验身份后变为 `power/wakeup=disabled`；仍需冷启动、
  物理 USB 口与 deep suspend 回归。
- **已验证拓扑**：该 Renesas 控制器的 USB2 root 为 4 ports；port 1 是 AYANEO
  内置键盘（2 interfaces），port 2 是 AYANEO controller，USB3 root 当前为空。
  AYANEO 官方规格只有一个全功能 USB-C，因此物理验收按同一外部口分别测试 USB2
  与 USB3 枚举，不把内部 root port 数量误当外部接口数量。

## 音频

- **已验证**：UCM 路径为 `Qualcomm/sm8550/APS`。
- **已验证**：WSA smart amp、DisplayPort backend 和 MultiMedia frontend 的路由会
  影响 PipeWire 是否建立真实 sink。
- **待验证**：当前未提交的 MM1/hw:0,0 回退是否在扬声器、麦克风、DP 和 suspend
  后恢复四种场景都成立。

## 输入

- **已验证**：InputPlumber 0.75.2 管理掌机控制器。
- **已验证**：仓库包含 gamepad 与 joymouse 两份 profile。
- **已验证**：自制键盘通过 uinput 注入按键，GTK/XWayland 只负责触摸 UI。
- **已验证**：普通 GTK 窗口不能跨越 KScreenLocker 的可信边界显示在锁屏上。
- **已验证**：桌面键盘的 900 ms 焦点复检会主动隐藏手动打开的键盘；SIGUSR1
  初始化和 XWayland 连接中断还存在独立退出路径，不能统一描述为“一个闪退”。
- **待验证**：Panel 内 QML 键盘能否在不抢上屏焦点的情况下可靠驱动 uinput。

## 已知日志特征

- KWin：DSI connector 无 EDID。
- PowerDevil：DDC `/dev/i2c-*` 权限错误；内置 DSI 背光不应依赖 DDC。
- Goodix：缺少可选 firmware/config 和 dummy regulator 警告，需要与实际触摸故障
  分开判断。
- udev：部分 HID scancode 的 `EVIOCSKEYCODE` 返回 EINVAL，需确认是否影响实体键。
- GPU/KWin：历史上出现 GMU OOB timeout、HFI 错误、hangcheck lockup、
  `dma_fence_default_wait` 黑屏和 user manager 停机卡死。当前定制内核的 A740 IFPC
  与 GEM/dmabuf 无限 fence wait 补丁是高价值 A/B 假设，不是已证实根因。
- Netfilter：当前定制内核没有 `CONFIG_NF_CONNTRACK_NETBIOS_NS`，stock
  FedoraWorkstation/home zone 经 `samba-client` 请求该 helper 时会令 firewalld
  进入 active-but-failed。固件用户空间基线不得依赖这些 zone；最终内核配置仍需
  与 Fedora firewalld stock service 做完整兼容矩阵。

## 采证规则

新增硬件结论必须记录：命令或日志、内核/软件版本、测试时间、是否可重复、是否
改变硬件状态。一次成功不能升级为“稳定支持”。
