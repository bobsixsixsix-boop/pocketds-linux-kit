# Panel GPU 遥测与双内屏亮度审计（2026-08-27）

## GPU 实施结果

- 新增用户服务 `pocketds-gpu-telemetry.service`，每 2 秒读取 MSM DRM fdinfo。
- 按 `drm-client-id` 去重；完整扫描每 300 秒，新进程扫描每 10 秒。
- 原子写入 `%t/pocketds-gpu-status.json`，权限 0600；Panel 超过 7 秒视为过期。
- Panel 显示可观测客户端负载、`cur_freq`、最高 GPU thermal zone 和客户端数。
- 不可用/首个差值样本/过期值均为 JSON `null`，UI 显示 `—`。
- 真机 60.086 秒：CPU 0.211219 秒（0.352%）、MemoryCurrent 12,120,064 bytes、
  MemoryPeak 12,324,864 bytes、restart delta 0。
- systemd 的 mount-namespace 型加固会让同 UID 桌面进程的 `/proc/PID/fd` DRM
  链接不可发现，因此服务不使用 `ProtectSystem`、`ProtectHome`、`PrivateTmp` 或
  `ReadWritePaths`；保留 `NoNewPrivileges`、低 CPU 权重、96 MB 内存上限、禁止
  SUID/SGID、实时调度和非本机 syscall architecture。

## 亮度现场证据

- 硬件背光：上屏 `2293/4096`（约 56%），下屏 `2774/4080`（约 68%）。
- systemd-backlight 保存值：上屏 2334，下屏 3100。
- `kwinoutputconfig.json`：DSI-1/DSI-2 的 `brightness` 均为 1；上屏
  `allowSdrSoftwareBrightness=false`，下屏为 `true`。
- `org.kde.ScreenBrightness` 暴露 display0/display1，但两者 Brightness/MaxBrightness
  都是 10000/10000，标签也都只是“内置屏幕”，与硬件当前值不一致。

## 上游根因

设备为 PowerDevil 6.7.4 / KWin 6.7.4。对照上游当前实现：

1. [PowerDevil ScreenBrightnessController](https://invent.kde.org/plasma/powerdevil/-/blob/6017d69c3933fb0082a31d4bf42ae5dd9e4c74cb/daemon/controllers/screenbrightnesscontroller.cpp)
   以 KWin 输出作为公开的逐屏亮度对象；内核 backlight 检测结果通过 external
   brightness controller 提供给 KWin。
2. [PowerDevil Linux backlight helper](https://invent.kde.org/plasma/powerdevil/-/blob/6017d69c3933fb0082a31d4bf42ae5dd9e4c74cb/daemon/controllers/backlighthelper_linux.cpp)
   把单个请求按第一块设备的比例写入枚举到的所有 backlight。这不支持 Pocket DS
   两个面板保持不同百分比。
3. [KWin brightness-device assignment](https://invent.kde.org/plasma/kwin/-/blob/da9e1dd26c7a8d50bb57523841ed385927308adc/src/workspace.cpp)
   把每个外部 brightness device 分配给一个最先匹配的 internal output，然后从
   candidates 中移除；合并后的单设备不可能同时表达两个独立硬件通道。

因此，调用官方 `org.kde.ScreenBrightness.Display.SetBrightness` 会让一路经合并
helper 同时写两块硬件，另一路则只做 KWin 软件亮度。它不能解决本机的独立背光
持久化，反而会重新制造双真值。

## 决策与下一步

- 两个 `/sys/class/backlight` 设备是本机唯一硬件真值；KWin 软件亮度保持 1.0。
- Panel 继续通过参数白名单 root helper 独立写 top/bottom。
- 下一步单独实现状态文件和事件驱动的重放：首次默认 60%，之后保存用户值；等待
  PowerDevil 初始化后重放，并处理 resume/output reconfigure。
- 在真机启用前必须有 `--dry-run`、损坏状态回退、5% 安全下限、幂等测试和明确
  回滚；本轮没有为了验证而改变任何背光值。
