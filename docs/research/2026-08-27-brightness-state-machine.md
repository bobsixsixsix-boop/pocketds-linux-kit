# 双硬件背光状态机（2026-08-27）

## 目标和边界

PowerDevil/KWin 6.7 无法表达 Pocket DS 两路独立 internal backlight。本实现不修改
KWin 输出模式，不调用其 brightness D-Bus，也不轮询写盘；两个 sysfs backlight
是唯一硬件真值。

## 状态与更新协议

- 持久状态：`~/.config/pocketds/brightness.json`，schema 1、权限 0600。
- 取值：top/bottom 各 5～100；新镜像首次默认 60/60。
- 当前开发机使用 `install-brightness.sh --capture-current`，安装前捕获 56/68，避免
  升级时亮度跳变。
- Panel 的 `brightness` 命令不再直接调用 root helper，而是调用用户态状态机：
  root helper 成功后才更新状态；失败不保存虚假目标。
- 状态写入使用同目录临时文件、`fsync`、`os.replace` 和 flock。
- 损坏/不支持的状态在 initialize 时改名为带时间戳的 `invalid-*`，再捕获硬件或
  使用首次默认；原文件可恢复。

## 漂移恢复

用户服务每秒读取四个小型 sysfs 属性。当两路实际百分比相同、持久目标不同且
至少一路漂移时，按已观测的 PowerDevil 全局覆写模式在首次观测恢复。其他 raw
value 与目标换算值的漂移仍需连续两次才调用白名单 root helper；稳定状态不写硬件、
不写状态文件。这保留单次任意瞬态的过滤，同时缩短已证实的登录/PowerDevil restart 覆写窗口。

服务需要 sudo 执行白名单 helper，不能使用 `NoNewPrivileges`。sudoers 只允许
`pocketds-panel-root *`，而 helper 再严格校验 action、screen、整数范围 5～100。

## 测试结果

假的 sysfs/假 root helper 覆盖并通过：

- 捕获升级值 56/68；
- 无状态首次 60/60；
- Panel 修改、硬件成功后持久化；
- 4% 和非整数拒绝且无副作用；
- dry-run 不写硬件，reconcile 只修复漂移屏；
- 损坏 JSON 隔离并从当前硬件恢复；
- 文件权限 0600。

真机安装前后均为上屏 2293/4096、下屏 2774/4080。Panel 以同值走完整新调用链后
仍为 2293/2774，状态来源更新为 `panel`，drift 为空。服务重启没有产生 reconcile
日志或硬件变化。

2026-08-28 部署首次暴露安装器只用 `enable --now` 时不会替换已运行进程；安装器
现已锁定显式 `restart` 契约。重新部署后 PID 从 50521 变为 130449，仓库/安装脚本
SHA-256 一致，服务 active 且 `NRestarts=0`。随后受控重启 PowerDevil，200 ms 采样
显示下屏在 0.801 秒由 68% 被覆写为 56%，到 1.801 秒已恢复 68%；上屏全程 56%。
日志明确记录 `reason=powerdevil-global-clobber`，只恢复下屏，最终 drift 为空。

60.049 秒资源基线：CPU 0.138483 秒（0.231%）、MemoryCurrent 12,353,536 bytes、
MemoryPeak 12,623,872 bytes、restart delta 0。

## 未完成验证

尚未执行冷启动、PowerDevil restart 循环、resume 和 24 小时 soak；在这些测试
通过前，PDS-003 保持 VERIFICATION。

回滚：

```bash
systemctl --user disable --now pocketds-brightness.service
sudo cp /var/lib/pocketds-linux-kit/backups/<stamp>-brightness/usr/local/bin/pocketds-panelctl \
  /usr/local/bin/pocketds-panelctl
```

用户状态和 `brightness.json.invalid-*` 不会被自动删除。
