# 在 Pocket DS 上开发

公开源码的安装前提先看 [INSTALL.md](INSTALL.md)。以下 `make install` 示例针对原有
个人开发树；公开候选使用明确的 `--asset-free` 入口，且主安装也会改变系统策略与部分服务。

许多 Python 静态、fixture 和离线测试可在其他 Linux/macOS 开发机运行；完整 lint 和
测试链包含 Linux 专用 C/C++ 代码，应在 Linux 环境验收。
只有设备树型号精确为 `AYANEO Pocket DS` 时才追加现场 systemd 服务健康门；普通
Fedora VM 即使运行 systemd 也不会因没有掌机专用服务而被误判。真机应另外保留
`make test-hardware`、`make test-suspend` 和各项监督验收，不能用 VM PASS 代替。

## 下屏控制台

编辑：

```bash
$EDITOR components/control-panel/plasmoid/contents/ui/main.qml
$EDITOR components/control-panel/pocketds-panelctl.cpp
make lint
make install
```

QML 没有刷新时可注销再登录；不要在正在运行大型下载或游戏时强杀
`plasmashell`。日志：

```bash
journalctl --user -f -u plasma-plasmashell.service
/usr/local/bin/pocketds-panelctl status | jq
```

## 下屏键盘

```bash
$EDITOR components/keyboard/pocketds-keyboard.py
make lint
make install
systemctl --user restart pocketds-keyboard.service
journalctl --user -f -u pocketds-keyboard.service
```

语音优先调用使用者自行配置的 **OpenAI 兼容 HTTPS 转写 API**，没有默认地址、模型或密钥。
配置说明见 [VOICE-API.md](VOICE-API.md)。该路径失败或未配置时，先检查已安装的
本地 SenseVoice 后端；它不可用或识别失败后，才尝试已有的 Whisper。成功返回但没有识别到
语音时直接反馈无文本，不自动重复调用其他后端。取消或输入目标变化时，也不能继续粘贴旧结果。

SenseVoice 需要对应的 sherpa-onnx 程序、运行库、模型与 tokens；Whisper 需要 `whisper-cli`
及模型。它们不会由键盘自动下载，也不随 Git 分发。没有授权或可用本地后端时，普通键盘输入
仍可使用。配置入口与依赖边界见 [INSTALL.md](INSTALL.md#可选中文语音与额度显示) 和
[DEPENDENCIES.md](DEPENDENCIES.md)。

## 风扇、音频和输入设备

这三类改动会影响整机。审阅 `make install-all` 对应的系统配置与事务后再执行。
InputPlumber 守护本身不由该事务重启，但输入监听、键盘、触摸板、亮度和 light 等服务
可能暂停或激活；完成后按实际变更范围验证，不将安装视作运行状态完全不变。

不带素材参数的安装命令默认选择 `personal-assets`，先核对个人壁纸哈希/回执。
公开导出树使用精确空素材清单，应按安装指南显式执行 `--apps --asset-free`。
带有个人壁纸的私有开发树不能直接选择 `--asset-free`；该开关不会删除素材，也不负责生成公开包。

## 回收现场改动

如果先在 KDE 设置或 `/etc` 中调试：

```bash
./scripts/import-live.sh --yes
git diff --check
git diff
```

导入脚本只读取仓库已知的白名单文件，不读取 WireGuard、SSH、浏览器、Codex
账户、Steam 账户或用户词库。

## 回滚

安装脚本为用户文件建立：

`~/.local/state/pocketds-linux-kit/backups/<时间>/`

系统文件备份位于：

`/var/lib/pocketds-linux-kit/backups/<时间>/`

Git 中的代码回滚用 `git log` 和 `git revert <commit>`；不要用会丢失未提交工作的
`git reset --hard`。

## Panel/键盘窄更新事务

正式 telemetry soak 期间只运行 `make plan-ui-update`。soak 通过后，先用
`make stage-ui-update` 建立新的私有事务，再显式 `make apply-ui-update`；两个入口
使用不同确认词。apply 只替换审计确认的 6 个文件，逐项绑定 stage 时的 live 前像，
不会 daemon-reload、重启键盘或重载桌面。随后先运行 `make verify-ui-update` 和
`make audit-ui-deployment-candidate`，再把桌面/键盘激活作为独立的真人验收步骤。

激活后用 `TRANSACTION=<name> LEDGER=<new-0600-path> make
prepare-ui-input-ledger` 生成绑定当前 clean revision、applied manifest、6 项 source/
payload/live/rollback 以及 joymouse source/live 的空白台账。完成 Panel/键盘/实体肩键
轮次后，以 `TRANSACTION=<name> EVIDENCE=<ledger> OUTPUT=<new-report> make
evaluate-ui-input` 离线评估。评估器不会启动、重启、注入或截图；缺项返回 INCOMPLETE，
任一文件/事务漂移直接拒绝。

若需要回退，`make rollback-ui-update` 只在所有 live 文件仍等于该事务 payload 时
恢复原 mode/字节；任何人工修改都会令它拒绝覆盖。不要用范围更广的 `make install`
代替这次窄更新。

Codex 额度采集不属于上述 6 个 UI 文件。用 `make install-codex-quota` 只备份并复制
collector/unit/timer，不 reload 或激活；需要现场验证时再显式运行
`./scripts/install-codex-quota.sh --activate`。激活门要求账户已登录的 Codex
app-server 在 120 秒内返回私有 `0600` 新缓存，session log 回退或陈旧值都不会冒充
部署成功。

## 隔离 XWayland 故障验收

`make test-xwayland-isolation` 只运行 fixture/static 单元测试；`make
plan-xwayland-isolation` 只核对设备端二进制、私有 runtime 和固定命令，不启动
KWin/Xwayland。真正的故障轮次必须等长时观测结束，并明确提供确认词、新的私有
输出文件和 3～5 轮次数后才可运行 `make accept-xwayland-isolation`。

验收只在独立 session、私有 `XDG_RUNTIME_DIR` 和 KWin virtual backend 中启动一个
无输入能力的 GTK3/X11 probe；只向逐项重验 PID/session/starttime/UID/executable/
runtime 的嵌套 Xwayland 发 SIGTERM。测试前后比较宿主 KWin/Xwayland 与键盘、GPU
遥测、InputPlumber 身份；身份缺失、歧义或漂移均失败关闭。它不测试“GTK 应用能
原地重连”——X11 连接断开后测试 client 预期退出，产品验收要求的是有界重启 client
且宿主会话不受影响。
