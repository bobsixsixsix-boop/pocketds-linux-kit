# 2026-09-01 盒盖轻待机与 deep suspend 下一步

> 历史记录：2026-09-05 已核实此文下方的 `e4e0a38a…` 镜像不属于当前安装，
> 不得再作为现机救援镜像使用。当前基线为 `85b41b68…`；候选构建、独立启动、
> 一次实际 deep 的提前唤醒结果见
> [2026-09-05 实验记录](2026-09-05-deep-diagnostic-boot.md)。下方的旧 gate 状态
> 与路径只保留为历史，不覆盖新记录。

## 当前结论

盒盖轻待机已经部署并通过一次实体合盖/开盖 smoke。它不是系统休眠：合盖后只关闭 KWin 的两块屏幕并把
Pocket DS 切到 `pocketds-powersave`；开盖先恢复两块屏幕，再在没有外部改档的
前提下恢复合盖前的性能档。Wi-Fi、音频、输入、KDE 会话和文件系统保持运行。

新的 RTC/login1 lifecycle guard 已部署，但真正的 deep suspend 仍由 `AllowSuspend=no`
隔离，execution 为 DISABLED。guard 的拒绝路径已做一次 no-sleep smoke；本轮没有执行
suspend、reboot、内核安装、DTB 修改或 UFS sysfs 写入，不能把该 smoke 记成 deep
SMOKE-PASSED。

## 轻待机实现

- 盒盖事实源：login1 `Manager.LidClosed`，信号后 250 ms 防抖并重新读取。
- 关屏：`kscreen-doctor --dpms off`，不改输出 enable、布局、缩放或亮度。
- 省电：通过现有 `pocketds-panelctl power powersave` 进入同一 TuneD/风扇权威。
- 开盖：先 `--dpms on`；仅当服务确认自己拥有当前 powersave 状态时恢复旧档。
- 崩溃恢复：0600 原子状态文件
  `~/.local/state/pocketds-linux-kit/light-standby.json`。
- 安全边界：代码不调用 suspend、hibernate、rtcwake、logind sleep action，也不写
  `/sys/class/backlight`。

部署事务：`light-standby-20260901-1`。源码提交：`23a2f08aad5261e195d74e5877d80e331ee23383`。
设备启动检查为 lid open、双屏 DPMS on、`pocketds-balanced`、无遗留状态；服务
active/running、`NRestarts=0`。九项状态机测试、Python 编译、shell 语法、diff check 与仓库
lint 均通过。

实体 smoke 于 2026-09-01 00:41:47 合盖，00:42:09 完成开盖恢复。TuneD 的 active-profile
文件约慢 1 秒更新，守护进程按既有 5 秒恢复轮询自动重试并确认拥有 powersave；开盖后恢复
`pocketds-balanced`。DSI-1/DSI-2 均为 DPMS on，状态文件已清，服务 PID 未变且
`NRestarts=0`；Wi-Fi、双屏会话、输入、音频、风扇、亮度、键盘和触摸板服务仍 active，
rootfs 保持 rw，合盖后没有新增 UFS、EXT4 或 GPU 错误。状态升级为 SMOKE-PASSED。

回滚只涉及两个本轮新增文件：

1. `systemctl --user disable --now pocketds-light-standby.service`
2. 删除 `~/.config/systemd/user/pocketds-light-standby.service`
3. 删除 `~/.local/libexec/pocketds/pocketds-light-standby`
4. `systemctl --user daemon-reload`
5. 若当时屏幕仍黑，使用带正确 Wayland 会话环境的 `kscreen-doctor --dpms on`

不要删除亮度状态、KScreen 布局或 TuneD 配置来回滚本功能。

## 上次 deep 事故已经确定的部分

04:57:34.754 的旧入口调用 `systemctl suspend`；04:57:34.830 已收到 rc=0 并清掉
RTC alarm，但内核直到 04:57:35.511 才记录 `PM: suspend entry (deep)`。因此旧入口把
“logind 接受请求”误当成“系统已经 resume”，RTC 生命周期存在确定竞态。

systemd 的实现与待办也确认 suspend 的 logind 调用不等待 sleep job 完成。正确入口
必须先 arm RTC 并读回确认，失败时根本不发 Suspend 请求；随后订阅完整的
`PrepareForSleep` 生命周期，只有收到 `PrepareForSleep(false)` 后才能清 alarm。delay
inhibitor 只适合做最后的有界同步栅栏，超时后 logind 仍会继续，不能把它误当作取消或
fail-closed 机制。

## 已部署的 fail-closed 安全入口

源码 HEAD 为 `9481b1448308747183effe65369c9ced4cc5b590`，前一 PDS-005 提交为
`29a5c44`。事务 `deep-suspend-guard-20260901-1` 部署了：

- `/usr/local/libexec/pocketds-deep-suspend`：root:root 0755，SHA-256
  `68877ca5eb98bde3ebe5f4d1e01953b2e732965619cd0da75c97058dfcdcf36f`；
- `/etc/polkit-1/rules.d/90-pocketds-deep-suspend.rules`：root:root 0644，SHA-256
  `30ad238859699c91b57864209d9e41a3c3507b6bf0e7544bf656e5460709f402`；
- `/usr/local/libexec/pocketds-panel-root`：root:root 0755，SHA-256
  `636edb188f05b7d609d4ed8392386974663c8ea79e826e3a595a848e29756103`。

旧 dispatcher 为 `ce748eefc13af4c57a86b7d9037a37498a8088b8a4401bd9b7df8ad1c92678f0`；
新 guard 与 polkit 文件此前不存在。guard 在请求前 arm/readback RTC，以 delay inhibitor
衔接 login1，只在收到本次请求的 `PrepareForSleep(true -> false)` 后清 alarm；普通非 root
login1 suspend 被拒绝。

no-sleep smoke 中，`check` 为 `safely_blocked=true`、`execution_ready=false`，PDS-001
v3 为 `ready=false`，显式 `deep-suspend 60` 返回 1；前后 wakealarm 空、
`PreparingForSleep=false`、`CanSuspend=no`、boot 未变，0 条 PM/UFS/ext4 新事件、0 个
inhibitor。这个结果只证明入口 fail-closed，没有实际休眠，也没有验证 resume。

## 当前 UFS 证据

- 运行平台：SM8550，UFS host `1d84000.ufshc`，QMP UFS PHY `phy@1d80000`。
- runtime PM：level 3，即 device SLEEP + link HIBERN8。
- system PM：level 5，即 device POWERDOWN + link OFF。
- SM8550 没有 UFS PHY retention；上游因此明确要求 system PM 使用 level 5。把
  `spm_lvl` 临时改成 3 会绕过这项平台修复，可能直接产生 Hibern8 exit failure，不能
  当作首个日用候选。
- 当前 exact kernel 已包含 level-5 PHY reinit、SM8550 no-retention、device reset 后
  10–11 ms OCP 等待和 Shared ICE v5 同步 reset；不能重复套这些旧补丁。
- 上次只保存了通用 SCSI `DID_ERROR`（`hostbyte=0x07`）和整盘读/flush 失败。原始
  ufshcd、PHY、ICE、regulator 领先日志缺失，因此具体根因仍是 unknown，不能据此断言
  NAND、PHY 或某条 rail 单独损坏。

## 高价值候选

候选基线固定为完整 `v7.1.12`，commit
`badc4fe5a9c1a3671ec5ae1294d49ace55128f7b`。它的 ancestry 已包含：

1. UFS START STOP timeout/SCSI EH 修复 `01d5e237...` 的 7.1.5 backport
   `36f3faeffefbb871afd745e3365b9ad8a8d3d65f`；
2. EH runtime-PM 引用修复 `872f4862...` 的 7.1.10 backport
   `e83eed1bea142c8fe852fd53156845b88252ecd8`；
3. ICE iface-clock driver 修复 `0d5dc581...`（已在 7.1 final）及 SM8550 DT 修复
   `52696dbb...` 的 7.1.5 backport `e873e3cad2a96a2e8fc915c6fa2b3153440dae46`。

在该基线上重放 Pocket DS 的 46 个提交（从 `7fd2df204f342fc17d1a0bfcd474b24232fb0f32`
到 `a4975ce7c8eec079bd0cf7ec3890e100e016813f`），再加 native-lid
`b88375cc700e9dd3684cd3b3f5d3edbcc023b364`；逐项记录已上游、适用和冲突结果，不重复
cherry-pick 已在 stable ancestry 的修复。候选仍为 PLANNED / NOT BUILT；相关修复有价值，
但不能据此宣称 PDS-031 根因已经确定或修好。不要先改 UFS PM level、rail、gear、OCP
等待或关闭 runtime PM。

## 诊断与独立回滚门

当前 live config 为 `CONFIG_LOG_BUF_SHIFT=17`，没有 PM_DEBUG、FTRACE 或 PRINTK_CALLER；
`CONFIG_PSTORE=y`、`CONFIG_PSTORE_RAM=m`，pstore console/pmsg 未启用，已挂载的
`/sys/fs/pstore` 没有记录。首个诊断候选的最小增量为：

```text
CONFIG_PM_DEBUG=y
CONFIG_FTRACE=y
CONFIG_FUNCTION_TRACER=y
CONFIG_PSTORE=y
CONFIG_PSTORE_RAM=y
CONFIG_PSTORE_CONSOLE=y
CONFIG_PSTORE_FTRACE=y
CONFIG_PRINTK_CALLER=y
CONFIG_LOG_BUF_SHIFT=20
```

ramoops 还必须配套经过审查的 DT reserved-memory carveout；地址不能猜。首个候选不默认
启用 DPM watchdog 或 PM_TEST_SUSPEND。DT 仅证明
`serial0=/soc@0/geniqup@ac0000/serial@a9c000`、`qcom,geni-debug-uart` 和
`/dev/ttyMSM0` 存在；当前 cmdline 只有 `console=tty0`。物理 UART 接线、参数和实际收日志
均未验证，不能把设备节点存在写成外部救援已就绪。

主机独立材料为：

- baseline bootimg `work/pds002-recovery-private-v1/pds002-live-baseline-rollback-v2.img`，
  17,090,560 bytes，SHA-256
  `e4e0a38ab2f247dc67b6b4521f2fcc57123528ea50edda46ea08d305d9336c94`，与 live 三 alias
  相同；
- ABL payload `work/pocketds-abl-v1.1/abl_signed-SM8550.elf`，258,048 bytes，SHA-256
  `5f1018211feaa109563d5890ab52dccfb31db90f4bbe9e6b7fb39e94f860f8d4`；设备两槽只确认
  前 258,048 bytes 匹配；
- fastboot `/opt/homebrew/Caskroom/android-platform-tools/36.0.2/platform-tools/fastboot`，
  SHA-256 `b635fe0fd3aadf83451b4166332bd6d0e218304265f7e892021a5a6d26b2b3d9`，version
  `36.0.2-14143358`。

这些只证明材料身份。`live_fastboot_unlock_verified`、`rollback_artifact_prevalidated`、
`independent_runtime_boot_observed` 与 `candidate_install_authorized` 仍为 false。已安装的
`20260717` 旧 kernel RPM 缺少 `/boot` Image/config/System.map/DTB，不是回滚入口。

## 下一次 deep 之前

1. 保持 `AllowSuspend=no` 和自动休眠禁用；已部署 guard 不等于允许执行。
2. 在独立构建环境完成 distinct-release `v7.1.12` Image、DTB、modules 和诊断配置；RPM
   scriptlet 不得碰 `/boot/Image` alias。
3. 用户在场，先确认 exactly one unlocked fastboot device，只用 e4e0 baseline 做一次
   `fastboot boot`；确认独立 runtime、恢复 alias 和一次正常 baseline 启动后，才可把
   rollback prevalidation 置 true。
4. 当前内核没有 `CONFIG_PM_DEBUG`，所以没有 `pm_test` 节点。诊断内核可准备一次
   `pm_test=devices`，但它仍会真实执行 UFS level-5 powerdown/link-off，可能让 rootfs
   失联，不能称为无风险预检。
5. 以上救援与取证门全部通过后，才由用户在场做一次典型 deep，不做循环。
6. 若再次出现 UFS I/O error，立即停止写入，不重启 Plasma、不扫盘；先取外部/pstore
   证据，再硬电源逃生并从已验证 baseline 冷启动。

## 主要上游依据

- systemd inhibitor 与 `PrepareForSleep`：
  https://github.com/systemd/systemd/blob/main/docs/INHIBITOR_LOCKS.md
- systemctl 的 logind sleep 调用：
  https://github.com/systemd/systemd/blob/main/src/systemctl/systemctl-logind.c
- SM8550 no-PHY-retention / system PM level 5：
  https://git.zx2c4.com/wireguard-linux/commit/?id=3b2f56860b05bf0cea86af786fd9b7faa8fe3ef3
- Qualcomm UFS reset/OCP 等待：
  https://git.zx2c4.com/wireguard-linux/commit/drivers/ufs?id=5127be409c6c3815c4a7d8f6d88043e44f9b9543
- 当前缺失候选：
  https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/patch/?id=01d5e237b33931b970dd190dd6a19c5ef32c105d
  https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/patch/?id=872f486259ae0bc6b73ca4735a15d013241f73e9
  https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/patch/?id=0d5dc5818191b55e4364d04b1b898a14a2ccac38
  https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/patch/?id=52696dbbe7bbe0c8fc8c17133ffb5133b8cf37a6

## 事实源限制

用户指定的 `work/POCKETDS-STATE.md` 在本轮开始时于 clean main 和活动 worktree 均
不存在。本轮已依据 clean main、活动 worktree、live 配置、历史会话和上游源码重建，
并新建该状态文件供后续任务作为主要事实源。
