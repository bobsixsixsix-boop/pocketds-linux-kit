# Panel 电池遥测（2026-08-27）

## 现场数据

真机满电、接入 USB-C 电源时：

- capacity 100，status Full，temp 270（27.0°C），health Good，cycle_count 1；
- voltage_now 约 4,397,000 µV，current_now 约 -10,000 µA；
- `power_now=53,232,155`，若按 power_supply 常见 µW 解释则约 53 W，明显不可能；
- V×|I| 为约 0.04～0.06 W；UPower EnergyRate 同期约 0.05 W；
- `charge_full` 和 `charge_full_design` 读取失败，UPower Energy/EnergyFull/Design 为 0，
  TimeToEmpty/TimeToFull 也为 0。

结论：忽略 `power_now`；使用 V×|I| 作为瞬时功率并标注来源，不计算预计时间。

## Panel schema

新增 nullable 字段：

- `battery_percent`
- `battery_state`：charging/discharging/full/not-charging/unknown/unavailable
- `battery_external_power`
- `battery_temp_c`、`battery_voltage_v`、`battery_current_a`
- `battery_power_w` 与 `battery_power_source`
- `battery_health`、`battery_cycles`
- `battery_time_to_empty_s`、`battery_time_to_full_s`（当前固定 null）

读取时拒绝超出合理范围的容量、温度、电压、电流和功率；缺字段保持 null。外接电源
遍历所有非 Battery power_supply 的 `online`，任意一路在线即为 true。

## UI

电量放在标题栏，避免把 1024×768 下屏的五张指标卡继续压窄。标题栏显示百分比和
“充电中/使用电池/已充满/外接电源”；悬浮详情显示 V×I 功率、温度、电压、循环数，
并明确写出“预计时间：驱动数据不可用”。低于等于 15% 变红，充电变绿。

## 测试

假 power_supply fixture 覆盖：

- 42%、放电、31.5°C、4 V、-1.5 A 正确得到 6 W；
- USB online 0/1 正确映射外接电源；
- 非数字 current 使电流/功率为 null，来源 unavailable；
- 整个 battery 节点缺失时 state unavailable，其余值不伪造；
- 预计时间在容量字段修复前必须保持 null。

真机候选与安装后的 JSON 均得到 100%、full、external power true、27°C、约 4.397 V、
0.04～0.05 W、Good、1 次循环，整套 `make test` 通过。尚未人为拔掉电源或消耗电量，
充电/放电/拔插实机矩阵保持 VERIFICATION。

## Panel 状态调用开销

电池 sysfs 本身不需要新进程，但审计发现原有 status 每 2 秒都会派生 `wpctl`。真机
连续 100 次完整 status 的初始结果：wall 3.111 秒、mean 31.114 ms、p95 41.100 ms、
child CPU 2.267 秒。

新增 5 秒 runtime 音量缓存，Panel 主动调音量/静音会立即失效缓存。相同 100 次：
wall 0.941 秒、mean 9.408 ms、p95 12.915 ms、child CPU 0.302 秒；child CPU 约降
86.7%。按真实 2 秒周期调用 7 次，初始读取后只发生 2 次缓存刷新，即约每 6 秒调用
一次 `wpctl`。缓存测试同时验证 volume 命令后会失效并重新读取。
