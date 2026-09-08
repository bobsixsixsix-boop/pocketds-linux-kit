# Alpha3 的 PowerDevil / Qt ABI 补充检查

已发布的 SD Alpha3 使用 **Qt 6.11.2、KF 6.29.0、Plasma 6.7.4 和 PowerDevil pocketds1**。2026-09-08 的检查没有在这套固定运行库上重现另一个 Qt 6.11.1 系统中报告的符号缺失。Alpha3 镜像和发布标签保持原样。

| 检查对象 | 测试运行库 | 22 个 ELF 重定位检查 | 电池与亮度 QML 冷加载、类型注册 |
| --- | --- | --- | --- |
| Alpha3 随附的 `pocketds1` | Qt 6.11.2 | 22/22 通过 | 两个模块均通过 |
| 针对 Qt 6.11.1 构建的 `pocketds2`，仅在第二个临时副本测试 | Qt 6.11.2 | 20/22 通过，两个 QML 插件失败 | 两个模块均失败 |

`pocketds2` 在后一环境缺少 `QUntypedPropertyBinding(QPropertyBindingPrivate*)` 的 `Qt_6.11_PRIVATE_API` 符号，不能作为 Alpha3 的通用替换包。RPM 依赖检查能通过也不足以证明这些插件兼容；必须在实际目标运行库中完成全部 ELF 与显式 QML 检查。

正向测试在独立副本中进行。随后另一个只读检查器将 **2,229 条路径记录（2,015 个解析后的路径）**与实际已发布 raw 镜像逐项对应，核对路径、符号链接解析、大小和 SHA-256。这些记录覆盖全部 22 个 PowerDevil ELF、其解析到的动态库、已安装 PowerDevil / QtBase / QtDeclarative 文件，以及加载器和 RPM 元数据。原始镜像检查前后 SHA-256 均为 `aac48a404f392efbdf5ecf90ad2e3044355915783e22b09204ca8ee1dba50a0f`，临时挂载和专用 loop 已清理。

QML 检查使用新进程、空的私有 HOME 与缓存、关闭的磁盘缓存和断开的总线，加载插件并注册 `InhibitionControl`、`ScreenBrightnessControl` 类型，**没有构造控制对象或执行亮度、休眠操作**。这份证据只说明列明二进制与运行库的加载兼容性，不是桌面外观、SD 实机开机或睡眠唤醒验收。

精确版本、镜像、探针和证据摘要见[机器可读记录](alpha3-powerdevil-abi-20260908.json)。
