# 2026-09-07 复审修复

基线 `6f32503`。本轮先恢复了上次 X/Y 专用部署遗漏的
`pocketds-mode-listener.service`：2026-09-07 00:31 JST 确认其恢复
`active/running`，InputPlumber 与手柄测试服务的 PID 均未改变。
X/Y 映射本身没有改动。

## 控制器更新的服务边界

`pocketds-mode-listener.service` 依赖 `pocketds-controller-test.service`。
停止后者会连带停止前者，单独重新启动后者不会自动恢复前者。
过去专用部署脚本只验证新后端已运行，漏掉了这一依赖。

新增 `scripts/controller_update_services.py` 的显式
`paused_controller_services()` 上下文，供确实要替换测试后端的部署调用：

1. 检查两项服务均已加载并处于稳定状态，保存各自是否运行。
2. 停止测试服务并确认两项都已停止，才进入文件更新。
3. 文件更新或回滚验证成功后，调用方须标记返回的 `checkpoint.files_verified()`。
   回滚本身失败或忘记标记时，两项服务保持停止，不能启动不完整文件。
4. 成功及异常退出都按依赖顺序恢复此前运行的两项服务，并逐项验证。
   不启动此前故意停止的服务，恢复失败不能报告部署成功。

文件事务 CLI 的行为保持为只写文件、不重启服务。完整输入栈安装器已有
显式恢复顺序；窄部署也必须使用上述流程，不能只检查后端 PID 或源文件哈希。
`tests/pds005-ui-update-transaction.py` 覆盖成功、文件回滚、部分停止失败、
原先停止及 listener 启动失败。全量测试末尾的实机健康检查新增这两项服务，
防止再次漏掉现场异常。

## Plasma 恢复的自动重启竞争

恢复 helper 现在直接请求 systemd 停止 Plasma 单元，保持有界等待、单元范围
SIGKILL 后备、单次启动和受保护服务检查。之前先用 KDE 退出接口关闭进程，
会与 `Restart=always / RestartSec=1s` 竞争：自动启动的新进程可能被后续
stop 再次停止。新增模拟覆盖这一策略，确保没有中间自动启动。

旧请求协议中的 `quit_timeout` 仍能读取，兼容已安装 worker/请求参数；
恢复执行不再走 KDE 单独退出路径。此修复不改变 KWin、屏幕布局、内核或睡眠策略。

## 其余修复及验收

- 语音源查询改为可取消后台会话，GTK 立即进入准备状态。查询期间松手即取消；
  排队回调与 generation 都重新检查，不能启动已取消的录音。
- 内置 ALSA 开始前检查静音与零音量，录音期间按 250 ms 间隔查询固定源；
  查询错误、源丢失或静音导致回收并丢弃。录音器退出后重新查询一次许可，
  然后再验证原输入目标，才允许进入识别。外置录音仍绑定原 Pulse 源。
- 风扇采样新增独立状态和采样龄，缺失、无效、未来时间或超过 7 秒时不可用；
  其他遥测及控件不受影响。额度缓存采用 64 KiB 有界非阻塞读取，严格验证
  平面 JSON 和当前 schema 后重新序列化，错误只让额度变成 null。
- 安装写入前检查三个录音工具；检查脚本区分缺工具和缺设备，并复用额度
  采集器发现 Codex 的路径。ASR 事务允许从 failed 状态修复，原失败留档，
  回滚恢复文件并保持停止，不重复触发旧代码崩溃。
- daily-suspend 测试不再误改源码路径；Panel 文案测试遵循当前交互契约；
  Panel schema 测试默认构建当前源码，不要求先部署新程序才能验证它。

设备隔离副本的完整 `scripts/test.sh` 已通过，包含新增缓存隔离、
205 项键盘适配器测试、安装回滚与服务状态检查。需要显式监督的硬件
实验按原测试入口跳过，真实改密、录音或休眠没有作为自动回归执行。

## 实机部署结果

代码提交 `ceab14a` 已部署并激活，五个文件均通过事务逐字节校验：键盘主程序、
键盘适配器、Panel helper、Panel QML、Plasma 恢复 helper。备份位于
`~/.local/state/pocketds-linux-kit/ui-transactions/audit-repair-20260907`。

键盘新 PID 423587、Plasma 新 PID 423623 均 `active/running`，自动重启计数
均为 0。新的恢复 worker 使用对应 request_id 回读完成，事件只有一次
`start-once`，没有 KDE 单独退出。KWin、InputPlumber、手柄测试、快捷键监听、
触控板、GPU 遥测、亮度、风扇及待机/活动监听的 PID 和状态全部保持不变。
boot ID 未变，Panel 仍是 applet 28、screen 1。

已安装 helper 的独立 schema 检查通过，风扇状态新鲜、亮度可写、额度有效。
`scripts/check.sh` 完成且为 **0 warnings**；此前误报的 Codex 路径已正确识别。
激活后的键盘及本项目 QML 没有新运行错误。KDE 自带亮度 PopupDialog.qml:96
有一条初始化 TypeError，同样信息在此前五次 Plasma 会话中已存在；它不属于
本次修改，也没有据此宣称整个系统日志无错误。

激活前后快照、恢复请求与结果、运行摘要保存在
`~/.local/state/pocketds-linux-kit/backups/audit-repair-20260907`。
本轮没有重新录音、输入密码或进行睡眠实验；这些不属于上述自动验收结论。
