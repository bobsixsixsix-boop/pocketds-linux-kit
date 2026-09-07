# 恢复正常 console suspend；Hall 对照仍待验证

后续 Hall-04 已取得双屏／触摸的人工成功，配置也已持久部署；时间窗口错误、
尚未完成的原生验证及当前资格见 [本轮汇总](2026-09-07-sleep-validation.md)。
下文保留这项源码变更提出时的对照依据，不把它当作最终验收结论。

2026-09-07，主代理在干净启动 `db67a328…` 确认
`/sys/module/printk/parameters/console_suspend=N`，启动参数仍包含诊断用的
`no_console_suspend ignore_loglevel loglevel=7`。Hall-03 因未在等待期内合盖而退出，
没有 `PrepareForSleep`，计数仍为 0/0；它不是休眠或唤醒成功记录。

Linux 官方 stable v7.1.12 的
[suspend_prepare](https://github.com/gregkh/linux/blob/v7.1.12/kernel/power/suspend.c#L379)
先执行 `pm_prepare_console()`，随后才冻结用户进程。这能解释为什么在
`PM: suspend entry` 与 `Freezing user space` 之间可能出现 fbcon 引起的显示准备。

[DRM fb helper](https://github.com/gregkh/linux/blob/v7.1.12/drivers/gpu/drm/drm_fb_helper.c#L461)
默认设置 `skip_vt_switch=true`，
[framebuffer 注册](https://github.com/gregkh/linux/blob/v7.1.12/drivers/video/fbdev/core/fbmem.c#L512)
据此声明不需要休眠 VT 切换。可是
[PM console](https://github.com/gregkh/linux/blob/v7.1.12/kernel/power/console.c#L112)
在 `console_suspend_enabled=false` 时仍强制切换。
[printk](https://github.com/gregkh/linux/blob/v7.1.12/kernel/printk/printk.c#L2754)
提供已有的可写 `console_suspend` 参数；`no_console_suspend` 会将它设为 false。

因此 `80-pocketds-pm.conf` 在正常启动时恢复 `console_suspend=Y`；daily guard 的
`full_check` 也要求读回精确的 `Y`，缺失、未知或 `N` 均拒绝准入。检查不修改该值，
普通 `eligible/check` 仍保持轻量。该配置不启用休眠，不修改启动参数、VT 锁或
内核驱动，也不改历史受控实验所锁定的 deep guard。

Hall-02 上屏黑屏伴随 DSI/encoder 超时，仍不能据此断言 VT 切换是根因。
下一次对照只恢复该参数，继续使用去掉上屏 `actual_brightness` 主动 DCS 读取的
观察器，并要求真正 Hall 唤醒、双屏物理显示与触摸确认。尚未取得这些结果前，
不得宣称修复完成或开放日常休眠。临时对照须记录原参数值，并在实验结束后按
原有实验事务处理恢复；本次源码变更没有执行实机写入。
