# KDE 电池健康度显示修复 · 2026-09-10

本次源码更新包含两个相互独立的问题的处理：驱动恢复容量读取，KDE 正确处理未知健康度。补丁在已有维护设备上验证过；这里没有发布新 SD 镜像，也不自动替换使用者的内核或 PowerDevil 包。

## 原因与修复

高通 `qcom_battmgr` 的属性协议分支未初始化容量单位，导致 `charge_full` 和 `charge_full_design` 返回 `ENODATA`。UPower 因而保留未知健康度值 0；这不是电池实际损坏至 0%。已有[内核单位补丁](../../experiments/kernel/battery-charge-unit/0001-power-supply-qcom-battmgr-initialize-property-unit.patch)将该分支单位初始化为 mAh，让驱动返回固件提供的容量。

另一个问题在 PowerDevil 6.7.3：`batteryCapacity` 为整数，健康行却用 `!== ""` 判断是否有数值，未知值 0 仍会显示。[新增补丁](../../components/powerdevil/0002-hide-unknown-battery-health.patch)改为 `> 0`，同时保留原有低健康度警告。即使某块电池仍不提供容量，也不会因此显示误导性的 0%。

## 已验证范围

维护设备使用定制 7.1.12 内核、Qt 6.11.1 和 PowerDevil 6.7.3。内核模块按实际配置单独编译，未修改启动镜像，备份原模块后通过正常重启加载，未热卸载电池驱动。

- KDE 绑定测试：旧源码的零值和负值两个案例失败，修复后九个案例全部通过。
- PowerDevil：22 个已安装 ELF 文件的加载检查、两种 QML 模块的冷加载及已有休眠组件检查通过。
- 正常重启后，三次充电样本和三次离电样本均返回满充容量 6358 mAh、设计容量 6025 mAh。
- 两者比值约 105.5%，UPower 将健康度封顶为 100%；没有写死健康度。用户确认 KDE 显示 100%，双屏、Panel 和拔电切换正常。

这些是单台已有维护设备的验证结果，不代表所有 Pocket DS 的电池健康度均为 100%。本轮没有完整充放电校准、过夜测量或新增合盖休眠验收。设计电压仍缺失，Wh 和剩余时长估算需要单独研究；不能将健康度修复当作续航提升。

## 版本与更新边界

[PowerDevil 说明](../../components/powerdevil/README.md)记录 pocketds3 的构建版本、补丁和验证入口。公开源码保留 Alpha3 的 Qt 6.11.2 / pocketds1、已有 Qt 6.11.1 / pocketds2 配对，增加 Qt 6.11.1 / pocketds3 的精确文件校验。未知组合继续拒绝启用受保护的深度睡眠；通用安装器不安装这些补丁包，深度睡眠也仍需显式启用。

原实验目录的旧 manifest 继续记录当时的构建和未部署状态；本页记录后续维护设备验证，不将旧构建产物改写成已验收产物。现有 Alpha3 镜像、标签和附件保持原样。本次同步只涉及电池修复，不包含未完成的触摸板实验。

参考：[PowerDevil 6.7.3 BatteryItem.qml](https://github.com/KDE/powerdevil/blob/v6.7.3/applets/batterymonitor/BatteryItem.qml)、[内核补丁来源与协议依据](../../experiments/kernel/battery-charge-unit/manifest.json)。
