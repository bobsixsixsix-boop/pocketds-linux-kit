# 2026-08-27 真机基线审计

范围：只读审计。未改配置、未重启、未启动前端、未执行休眠，未读取 ROM 内容。
结论对应 PDS-001～009、012～014、020～023；详细状态以问题表为准。

## 电源、显示和 GPU

- `/sys/power/mem_sleep` 当前为 `s2idle [deep]`；日常 lid/power key 被安全策略
  忽略，不能把它描述为休眠功能完成。
- Hall sensor 能上报 `SW_LID`；历史 suspend 存在 Goodix `-11`、Renesas PCI
  `-16`、xHCI `-22`，且内部 Renesas xHCI wake 规则当前未生效。
- KWin 当前运行上屏 165 Hz、下屏 59.999 Hz，证明异刷可用。历史 boot 出现
  大量 DRM atomic commit `EBUSY`，需要 165/60、60/60、单屏矩阵 A/B。
- systemd-backlight 保存约 57%/76%，KWin 输出配置保存 100%，当前硬件也是 100%。
  高可信链路是 Plasma 会话在开机早期恢复后再次覆盖。
- DRM fdinfo 提供逐客户端 GPU engine/cycle/maxfreq 计数；必须去重、缓存，并明确
  这不是完整硬件全局负载。
- 历史日志包含 GMU OOB timeout、HFI、hangcheck 与 `dma_fence_default_wait`。
  定制内核的 A740 IFPC 和 GEM/dmabuf 无限 fence wait 补丁只列为待 A/B 假设。

## Panel、键盘、风扇和性能

- 下屏原生横向为 1024×768；当前 scale 1.25 后 KWin 逻辑区为 819×614。
  Plasma applet 占 816×608，QML/键盘/窗口规则又写死 820×615，解释了边缘不满。
- 键盘手动打开后 900 ms 焦点复检会主动隐藏；SIGUSR1 初始化和 XWayland 断连
  是另外两条退出路径。
- 风扇调用链真实生效，审计时 state 为 PWM 51/255、约 2080 RPM。Panel 展示的是
  PWM 百分比而非转速；低温时 quiet/balanced 曲线相同，因此主观上像“没作用”。
- Panel 性能按钮只写 CPU governor，绕过 TuneD/PPD，不能代表真实的 CPU/GPU/
  风扇 profile。

## ES-DE、RetroArch 和互联

- ES-DE 3.4.1 AArch64 AppImage hash 与官方发布一致。两次无响应都由 KWin killer
  最终 SIGABRT；主线程在 join，16 线程/RSS 约 190 MiB，并非高 CPU 扫描。
- ROM 根只有 174 个系统说明 `.txt`，总计约 95 KiB，无符号链接或实际游戏；已
  排除海量库和链接循环。
- 首轮审计时主安装器写入 `~/.emulationstation`，ES-DE 实际读取 `~/ES-DE`；自定义系统和 ROM
  路径从未生效。ES-DE 运行在 XWayland/GLX，并持有 InputPlumber 已删除 event fd。
- RetroArch 1.22.2 曾正常运行并退出，15 个声明 core 都存在；live 配置与模板分叉。
- KDE Connect 已安装运行，但 firewalld/nftables 实际失败。默认方案是修复防火墙后
  使用 KDE Connect；LocalSend 可选；Tailscale 不默认安装或登录。

## 发行阻断

- 当前设备存在全局 `(ALL) NOPASSWD: ALL`；公开固件必须改成白名单 helper。
- 壁纸、外部二进制和 vendored 脚本需要来源、校验和与再分发许可证；发布前增加
  VERSION、CHANGELOG、LICENSE、第三方清单/SBOM。
- 诊断包不得包含 ROM 文件名/内容/hash、SSID/MAC/IP、WireGuard/Tailscale、
  SSH/Codex/Steam/浏览器凭据；默认不上传。

## 官方与上游资料

- [Linux sleep states](https://docs.kernel.org/admin-guide/pm/sleep-states.html)
- [DRM client usage statistics](https://dri.freedesktop.org/docs/drm/gpu/drm-usage-stats.html)
- [Pocket DS Linux 状态页](https://azka.li/pocketds/)
- [linux-pocketds 项目组](https://gitlab.com/linux-pocketds)
- [A740 IFPC 定制提交](https://gitlab.com/linux-pocketds/linux/-/commit/d84f65cc8cd7af0bf62cca52d13583394851cfe8)
- [GEM/dmabuf 无限 fence wait 定制提交](https://gitlab.com/linux-pocketds/linux/-/commit/9f8fbaaeb9e8d6c11bca695de77fd6ee2778fa02)
- [ES-DE user guide](https://gitlab.com/es-de/emulationstation-de/-/blob/master/USERGUIDE.md)
- [ES-DE install/custom systems](https://gitlab.com/es-de/emulationstation-de/-/blob/master/INSTALL.md)
- [ES-DE 3.4.1 release manifest](https://gitlab.com/es-de/emulationstation-de/-/raw/master/latest_release.json)
- [KDE Connect](https://kdeconnect.kde.org/)
- [KDE Connect firewall guide](https://userbase.kde.org/KDEConnect/en)
- [LocalSend Flathub](https://flathub.org/apps/org.localsend.localsend_app)
- [Tailscale Linux installation](https://tailscale.com/docs/install/linux)

## 下一轮最小实验顺序

1. 无状态运行 30～60 分钟，统计 atomic EBUSY、GMU/HFI、温度与频率。
2. 修复 ES-DE 配置目录和 InputPlumber 稳定等待，用空库/合成自由内容回归。
3. 解决亮度双真值源，在注销/登录验证后再做冷启动和 resume。
4. 保留 SSH/控制台/已知可启动内核后，才做显示矩阵、`pm_test` 与内核补丁 A/B。
