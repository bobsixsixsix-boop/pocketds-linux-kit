# 键盘终端快捷键的隔离继承问题

日期：2026-09-06。用户从键盘快捷键打开 Konsole 后，运行 `passwd`，输入现有密码却收到认证失败。

## 根因

实机单次核对确认现有密码有效；密码程序及其校验 helper 的属主和 setuid 权限正常，失败锁定记录为空。日志记录 `passwd` 的认证阶段仍为 `euid=1000`，以及 `unix_chkpwd` 无法完成用户校验。

键盘服务通过 `ReadOnlyPaths=` 保护语音授权文件。该用户服务具有独立的 user namespace 和 mount namespace，UID 映射只有 `1000 → 1000`，没有系统 root 的映射。旧快捷键直接在键盘进程下启动 `gtk-launch`，新建 Konsole 继承了这套隔离。实机确认键盘与故障 Konsole 的两个 namespace 均相同，而用户服务管理器仍在具有完整 UID 映射的初始 user namespace 中。

因此，终端内的 setuid 密码程序无法取得系统 root 身份来完成校验。这不是密码被近期部署修改，也不是 `NoNewPrivileges` 导致：故障 Konsole 实测 `NoNewPrivs=0`、`Seccomp=0`，键盘服务也没有启用 `NoNewPrivileges=`。诊断不需要重置密码或修改系统认证文件。

## 修复

终端快捷键现在通过 `systemd-run --user --quiet --collect --service-type=exec`，委托用户服务管理器启动固定命令 `/usr/bin/konsole --separate`。新的服务从管理器继承正常桌面环境；不使用仍会继承调用者隔离的 `--scope`。`--separate` 确保创建独立进程，避免复用此前已处于键盘隔离内的 Konsole，语义见 [KDE 官方命令行说明](https://docs.kde.org/trunk_kf6/en/konsole/konsole/command-line-options.html)。

键盘服务的授权文件只读保护保持不变。快捷键仍先收起键盘，只将首个新建终端窗口放到上屏，不移动已有窗口。

## 验证状态

本地 191 项键盘检查和 20 项 UI 检查通过。新增两项行为回归：

- 验证终端通过用户管理器启动独立 Konsole，固定参数中包含 `--separate`。
- 模拟启动器无法创建进程，验证清理临时窗口规则和防重复点击状态，且不回退为直接启动终端。

`934c908` 已部署，实机验收为 `SMOKE-PASSED`。实际点击键盘终端按钮，新建 Konsole PID 379466 及其 bash PID 379519，与用户服务管理器 PID 1333 共享初始 user namespace `4026531837`、mount namespace `4026531832` 和完整 UID 映射。新窗口位于 DSI-1，矩形为 `[0, 0, 819, 431]`。键盘 PID 379164 仍保持独立 user namespace 和仅 `1000 → 1000` 的 UID 映射。

通过同一用户服务管理器启动独立 PTY，执行真实 `passwd`，现有密码认证成功并进入 `New password` 提示。没有输入新密码，验证前后 `passwd -S` 状态相同。该验证进程没有响应 Ctrl-C 和 SIGTERM，最终仅终止本次创建的验证子进程；未修改密码，也未终止用户的终端。

部署备份位于设备 `~/.local/state/pocketds-linux-kit/backups/terminal-launch-fix-934c908`，其中 `passwd-validation.json` 和 `live-launch.json` 保存认证及窗口、namespace 验证结果。旧 Konsole PID 376823 保留原状；它仍处在旧隔离内，用户应从修复后的键盘按钮新开终端修改密码。

## 后续发现：新密码字典缺失

用户随后提供的实机画面显示，旧密码已通过认证，但输入新密码后出现 `/usr/share/cracklib/pw_dict.pwd.gz` 不存在及 `error loading dictionary`。前一次只验到新密码输入提示，没有覆盖此后的质量检查。

实机已安装的 `cracklib` 和 `libpwquality` 文件校验正常，但没有安装独立的 `cracklib-dicts` 包。通过 Fedora updates 仓库补装 `cracklib-dicts-2.10.3-1.fc44.aarch64`，本次事务仅新增这一个包。它提供未压缩的 `pw_dict.hwm`、`pw_dict.pwd`、`pw_dict.pwi`，无需人为创建报错中的 `.gz` 文件，也未修改 PAM 策略。

包文件校验通过，固定非秘密字符串的 `cracklib-check` 探针返回 `OK`。通过用户管理器启动的独立 PTY 运行真实 `passwd`：旧密码认证成功，临时随机候选通过新密码质量检查，并到达 `Retype new password` 提示。没有确认候选，随后终止本次验证子进程；用户密码未更改，验证前后 `passwd -S` 状态相同。

安装器预检与依赖文档现在明确要求 `cracklib-dicts`，运行检查同时验证实际字典加载，避免仅检查程序存在而漏掉数据包。实机安装及验证记录位于 `~/.local/state/pocketds-linux-kit/backups/passwd-dictionary-20260906`。
