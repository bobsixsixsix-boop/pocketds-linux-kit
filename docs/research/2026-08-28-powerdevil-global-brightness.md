# PowerDevil 双背光全局覆写（2026-08-28）

## 边界

本轮是 PDS-003 的有界实现和单次真机 PowerDevil 重启验收；不宣称冷启动、
restart 循环、resume 或长时运行通过。真机目标仍是用户状态的上屏 56%、
下屏 68%。

## 现场证据

- `kwinoutputconfig.json` 两路 brightness 都为 1.0 时，首次受控 PowerDevil
  restart 把两路硬件背光拉到 100%，约 1.6–1.8 秒后才被现有的
  1 秒轮询/两次失配策略恢复。
- 把 KWin 值同步为 DSI-1=0.56、DSI-2=0.68 后，PowerDevil restart 仍会
  把下屏硬件背光覆盖为 56%，约 1.0 秒后独立状态机恢复 68%。
- 部署首观测恢复判定后再次受控 restart：200 ms 采样在 0.801 秒首次看到
  56%/56%，1.801 秒已恢复 56%/68%。日志记录
  `reason=powerdevil-global-clobber`，只写回下屏；最终 drift 为空。
- 因此，当两路实际百分比相同、但持久目标不同时，这是已观测的全局覆写
  特征，而不能证明 KDE/KWin 已独立控制两路硬件背光。

## 上游依据

官方 PowerDevil v6.7.4 源码，commit
`7c97bbcd0e90ccb43eca1debd36191fc97f9f987`：

- [`powerdevilscreenbrightnessagent.cpp`](https://invent.kde.org/plasma/powerdevil/-/blob/v6.7.4/daemon/powerdevilscreenbrightnessagent.cpp)
  把控制器显示列表导出为 `/org/kde/ScreenBrightness/displayN` 子对象；这个
  DBus name 不是 DSI connector name。
- [`org.kde.ScreenBrightness.Display.xml`](https://invent.kde.org/plasma/powerdevil/-/blob/v6.7.4/daemon/dbus/org.kde.ScreenBrightness.Display.xml)
  定义 `MaxBrightness` 和 `SetBrightness(int,uint)`；flag `0x1` 表示不显示 OSD。
- [`screenbrightnesscontroller.cpp`](https://invent.kde.org/plasma/powerdevil/-/blob/v6.7.4/daemon/controllers/screenbrightnesscontroller.cpp)
  明确保留了“第一个 detector 的所有 display 使用同一亮度”的旧 API 兼容语义。

## 实现

`pocketds-brightness daemon` 现在每轮只读一次两路 snapshot。当持久目标不同、
至少一路存在漂移、且两路实际百分比相同时，第一次观测就执行已有
白名单 helper 恢复。其他单路或非同值漂移仍须连续观测两次。此判定不会
改写持久目标。

可选命令：

```bash
# 只读：列出 PowerDevil 当前所有 displayN 子对象的 100% 中和计划
~/.local/libexec/pocketds/pocketds-brightness sync-powerdevil-neutral

# 显式写入：通过官方 API 把所有子对象同步为中性 100%
~/.local/libexec/pocketds/pocketds-brightness sync-powerdevil-neutral --apply
```

命令先枚举 `DisplaysDBusNames`，再读完所有 `MaxBrightness`，全部类型、数量、
重复项和 object-path 元素校验成功后才开始任何写入。它不假设 connector 映射，
每次 `busctl` 都有 2 秒 DBus timeout 和 3 秒进程 timeout，失败时立即停止并报告
已完成的子对象。DBus 不提供跨对象原子事务，因此中途失败可能留下部分已写状态；
这个局限在输出中显式报告。独立 sysfs 状态机仍是物理背光的唯一权威。

2026-08-29 的只读现场复查发现，DSI-1/DSI-2 仍分别保存 0.56/0.68，且下屏
`allowSdrSoftwareBrightness=true`。下屏实际硬件 44% 时，这个 0.68 会形成额外的
软件乘法衰减；所谓“中性 60%”因此是错误命名和错误目标。命令已改为唯一真正中性
的 100%。部署后应先通过官方 API 把两个子对象设为 100%，再由独立状态机把本次
PowerDevil 全局硬件覆写恢复到持久的 35%/44%；最终门必须同时满足 D-Bus
10000/10000、KWin 1.0/1.0 和两路 sysfs 精确命中持久目标。

该命令没有被 service、installer 或默认测试调用，也没有增加常驻进程。默认测试
只使用内存 fake runner 验证参数和拒绝路径。

## 部署、回滚和待验证

已通过 `scripts/install-brightness.sh --capture-current` 进行带用户/root 备份的
受控安装。安装器显式 restart 已运行的服务；测试禁止退化回只执行
`enable --now`。本次备份为用户目录 `20260828-015603-brightness` 和 root 目录
同名项。回滚时从
`~/.local/state/pocketds-linux-kit/backups/<stamp>-brightness/` 恢复旧用户脚本和 unit；若只需
禁用新判定，恢复旧 `pocketds-brightness` 并 restart 用户服务即可，持久亮度文件
不需删除。

待执行：PowerDevil restart 循环、冷启动、deep suspend/resume 和 24 小时 soak。
完成前 PDS-003 仍为 VERIFICATION，不是 PASS。
