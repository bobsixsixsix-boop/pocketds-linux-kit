# PDS-056：正常重启复查

2026-09-07 15:59 JST，在用户确认工作已保存、保持开盖且 USB 已连接后，按当前配置请求一次 `sudo -n systemctl reboot`。约 **31.046 秒**后首次读到新 boot，系统为 running，Plasma 与键盘服务均 active。用户确认全程自动进入桌面，未操作电源键，双屏、桌面图标和下屏 Panel 正常。

本轮一次正常重启验收通过，历史关机停顿没有复现；PDS-056 的原因仍未定位。没有因此修改内核、驱动、电源配置、日志级别或 watchdog，也未请求第二次重启。

## 两轮证据对照

| 项目 | 09:09 历史停顿 | 15:59 本轮正常重启 |
|---|---|---|
| 关机前 boot | `db67a328-d3a0-43d3-8573-bf803dfdee20` | `d84d307d-6b0d-4014-afaf-a6c86e1d6f57` |
| light、PowerDevil、KWin、用户会话 | 均已正常停止，无停止超时 | 均已正常停止，无停止超时 |
| `/boot` 与交换空间 | 已卸载／停用，已到达 umount.target | 已卸载／停用，已到达 umount.target |
| 持久日志尾段 | 09:09:40.468 的 Syncing；照片已进入之后的 SIGTERM | 15:59:22.759 Syncing → 22.837 SIGTERM → Journal stopped |
| 进入桌面 | 用户长按关机后重新开机 | 用户确认完全自动，无电源键干预 |
| 新启动 journal | 上次强断电恢复后有非正常结束／重建提示 | 本轮没有相同提示 |

本次成功轮同样在 SIGTERM 后停止 journal，说明该画面本身不能定位历史故障。历史缺口位于日志停止后的最终收尾：残留进程、根文件系统收尾或设备关闭尚不能区分。现有记录没有支持服务停止超时、存储读写故障或某一驱动卡死的调用栈。

历史失败 boot 有两条 GMU fenced register delay，分别距关机同步约 19 分 52 秒及 1 分 53 秒；本轮关机前和新启动导出的内核记录没有同类警告。这是保留的关联线索，没有形成 GPU 导致重启停顿的因果证据，不能据此修改图形驱动。

## 本轮保全与恢复状态

重启前将完整本 boot journal、内核日志、挂载类型、进程状态与等待位置、服务状态及内核命令行复制到 Mac。重启前后快照均没有 D 状态任务，重启前后均无 failed system units；这不排除历史故障当时出现过短暂或晚期阻塞。

新 boot 为 `9d15fb4c-b3bb-4bca-bc29-58877852e0fd`，内核仍为 `7.1.12-pdsdiag.20260905.aarch64`，启动命令行前后一致。KWin PID 1347、Plasma PID 1491、PowerDevil PID 1516、键盘 PID 1706、light PID 1708 均 active、NRestarts=0。用户可见的桌面及 Panel 持久性检查通过。

`/sys/fs/pstore` 与 `/var/lib/systemd/pstore` 均为空，systemd-pstore 因无记录跳过，未取得历史晚期关机内容。空目录不能排除历史故障。本轮未发现新增的可见存储错误；既有 Qt 插件、音频及显示提交告警仍有出现，不能写成全系统日志没有异常。

设备原始记录位于 `~/.cache/pds-reboot-20260907-01/`。Mac 维护工作区为 `outputs/reboot-investigation-20260907/`：`normal-reboot-01.jsonl` 保存一次重启请求及重连时间，`before/`、`after/` 保存日志，`result.json` 保存用户确认与日志 SHA-256。

若自然使用再次复现，优先保留 SIGTERM 后的连续屏幕记录，区分等待进程、卸载或最终内核重启阶段；恢复后先取 pstore。systemd 的关机等待实现可输出具体残留进程，必要时另行授权的单次诊断可暂时提高 PID 1 日志级别，现阶段不增加循环重启或强制重启策略。[systemd 等待实现](https://github.com/systemd/systemd/blob/v259/src/shared/killall.c#L142)、[关机日志参数传递](https://github.com/systemd/systemd/blob/v259/src/core/main.c#L1633)。
