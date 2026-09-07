# 2026-08-31 suspend、rootfs 与崩溃提示事件

## 结论

这不是“重启 Plasma 把机器弄坏”，也不是一次已经证明的 NAND 损坏。
故障由两段组成：

1. Codex 使用了有竞态的 RTC-guarded deep suspend 脚本。`systemctl
   suspend` 返回只表示请求已排队，脚本却把它当成已经 resume，提前清掉了
   RTC wake alarm。
2. 后续唤醒时 UFS 存储路径失联，rootfs 读失败并被 ext4 以
   `emergency_ro` 保护。Codex、portal 和 Plasma 是下游受害者。

冷启动恢复了设备，但不证明底层 resume 问题已经修好。当前以禁用全部
suspend 隔离，不再重复做真机休眠测试。

## 精确时间线（JST）

- 04:57:34.653：`rtcwake -u -m no -s 25` 设置 04:58:01 alarm。
- 04:57:34.754：脚本调用 `systemctl suspend`。
- 04:57:34.830：命令以 `suspend_rc=0`、`elapsed_wall_s=0` 返回，脚本执行
  `rtcwake -m disable`，提前清除 alarm。
- 04:57:35.511：内核才记录 `PM: suspend entry (deep)`。
- 04:59:55 起：`/dev/sda` 出现多扇区 READ I/O error，均为
  `hostbyte=0x07`。
- 05:00:14：Codex SIGBUS。
- 05:00:21：Plasma SIGABRT；portal 随后 SIGBUS。
- 约 05:00:25：Synchronize Cache 失败；root `/dev/sda13` 进入
  `emergency_ro`。
- 正常 reboot 因二进制无法读取而失败，用户长按电源冷启动。

`hostbyte=0x07` 是通用 SCSI `DID_ERROR`。结合它只在 deep resume 后成组出现、
冷启动后完全消失，当前最合理的工作假设是 UFS host/PHY/电源/固件 resume
路径失联；证据不足以宣称闪存介质损坏。

## 后续“报错洪水”

冷启动登录时，Fedora/KDE 的 `drkonqi-coredump-pickup` 重放约 127 条历史
coredump，并留下 9 个上一 boot 的 KRunner 报错窗口。ABRT 同时扫描 31 个
历史 problem dir。18:33:59，DrKonqi launcher 因没有图形环境又自行
SIGABRT，报告器开始报告报告器。

这批提示并不等于当前 boot 同时发生 127 次崩溃。当前 boot 只有：

- 05:16:51：远程 `kscreen-doctor` 漏带 Wayland/X11 会话环境，SIGABRT；
- 18:33:59：上述 DrKonqi launcher 自身 SIGABRT。

前一条是本次维护探针使用错误，不是设备功能自行崩溃。

## 已部署隔离

事务名：`incident-containment-20260831-1`。

- `/etc/systemd/sleep.conf.d/80-pocketds-sleep.conf` 设置
  `AllowSuspend=no`；`CanSuspend` 实测返回 `no`，未实际 suspend。
- mask 仅 `drkonqi-coredump-pickup.service`，阻止下次登录重放旧账；
  `drkonqi-coredump-launcher.socket` 保持 active，真正的新崩溃仍可处理。
- 用户级同名 autostart 设置 `Hidden=true`，并阻止 ABRT applet 的 D-Bus
  再激活；系统 `abrtd` 与 `abrt-journal-core` 保持 active。
- 关闭当前历史 DrKonqi 窗口，重置一次瞬时 DNS 失败造成的
  `dnf-makecache.service` failed 状态。
- 不删除 ABRT、systemd-coredump、journal 或用户的 crash metadata。

部署后 smoke：Plasma active、`NRestarts=0`；两块屏幕 connected；
InputPlumber、keyboard、touchpad、GPU telemetry、Codex quota 和 GMU runtime
均 active；input mode 为 gamepad；Wi-Fi connected；root 为 `rw`；本 boot
存储错误计数 0；最近 5 分钟无新 error。

## 回滚

用户备份：

`~/.local/state/pocketds-linux-kit/backups/incident-containment-20260831-1`

root 备份：

`/var/lib/pocketds-linux-kit/backups/incident-containment-20260831-1`

回滚时：

1. 恢复 root 备份中的 `80-pocketds-sleep.conf`。
2. 删除本事务新增的两个用户 override；若备份目录中有旧文件则恢复旧文件。
3. `systemctl --user unmask drkonqi-coredump-pickup.service`。
4. system manager daemon-reload；下次登录应用 autostart 变化。

不要为验证回滚而执行 suspend。只有在受控 RTC 生命周期、防裸调用和恢复后
清理都实现后，才由用户在场做一次真机 smoke。

## 事实源限制

用户指定的 `work/POCKETDS-STATE.md` 在 main 与当前 worktree 均不存在。本记录
使用 clean main/worktree、live config、Codex rollout 与两次 boot journal 交叉
还原；不得把缺失文件当成已读取的事实源。

## 2026-09-01 follow-up：安全入口已部署，执行仍禁用

source HEAD `9481b1448308747183effe65369c9ced4cc5b590` 已通过事务
`deep-suspend-guard-20260901-1` 部署新的 RTC/login1 lifecycle guard、非 root suspend
polkit deny 和固定 dispatcher；三者 live SHA-256 分别为 `68877ca5...cf36f`、
`30ad2388...f402`、`636edb18...6103`。`AllowSuspend=no` 与 `CanSuspend=no` 未改变。

no-sleep smoke 的 `check` 为 safely-blocked、PDS-001 v3 为 not-ready，显式 60 秒入口
返回 1；前后 wakealarm 空、`PreparingForSleep=false`、boot 未变，没有 PM/UFS/ext4
新事件，也没有实际休眠。因此这里只能写 guard IMPLEMENTED + DEPLOYED、execution
DISABLED、拒绝路径 PASS；不能写 deep suspend 已修好或 SMOKE-PASSED。

下一候选固定为完整 `v7.1.12` 加 Pocket DS 补丁，并准备 PM_DEBUG/FTRACE、经审查的
ramoops/pstore 与实际外部 UART 取证。当前 e4e0 rollback bootimg、ABL payload 和 Mac
fastboot 均已 hash 锁定，但 unlock 与真实 RAM boot/恢复门仍未通过；已安装的 `20260717`
旧 kernel RPM 缺 `/boot` payload，不是回滚入口。PDS-031 的具体底层根因仍为 unknown。
