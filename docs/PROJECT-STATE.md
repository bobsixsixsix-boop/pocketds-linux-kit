# 项目状态

> 公开版更新（2026-09-08）：语音已改为用户自备的 OpenAI 兼容 API。下文涉及旧语音后端的提交、测试数量和实机结果均为历史记录，不能作为新 API 的验收。旧服务身份与账户细节不公开。

最后更新：2026-09-04（Asia/Tokyo）

## 当前事实

- 重装内核缺失的 native lid 输入已由条件式 GPIO 166 bridge 恢复；实体测试进一步
  发现 PowerDevil 合盖路径未关闭下屏。当前合盖由 light-standby 对两块 DSI 执行并
  验证全局 DPMS，电源键仍由 PowerDevil 管理；新实体合盖复测待确认。
- Steam 重启验收暴露了不带 `-gamepadui` 却由保存偏好进入大屏的路径；
  受管 Gamescope 现统一强制 Deck mode，只有独立 KDE desktop service 可以
  启动 false-mode Steam。真机重建为 true/完整 Deck flags 后，12:30 真人菜单
  返回成功：Gamescope 退出、KDE/Panel 保持、桌面 Steam=`uimode=7`。
- 设备端仓库 `~/Projects/pocketds-linux-kit` 是源码事实源；Mac 上的旧副本不是
  自动同步的权威副本。
- 分支：`main`；本轮工程基线起点：`435b6a5`。
- 系统：Fedora 44 aarch64、KDE Plasma 6.7.4、KWin 6.7.4、定制 Linux 7.1。
- 2026-09-03 已修复干净安装后的 Panel 控制面：主安装器现在强制要求 TuneD 与
  `plasma-milou`，安装/启用三套 Pocket DS 性能档及两路独立亮度服务，并保留安装
  前实际亮度。当前内核的大核物理上限是 2.9568 GHz，全局 boost 开关不可用；旧档
  已据真机能力修正。真机性能三档矩阵、两屏 37→38→37、风扇、音量、输入模式、
  合盖定时与游戏限帧全部通过并恢复。Panel 还会独立报告亮度写能力，避免以后后端
  缺失时出现“滑杆能动但写不进去”。Milou 只新增一个包，未升级或删除受保护的
  Plasma/KWin/Mesa 栈。
- 2026-09-03 已恢复 AYANEO 原厂 Android，并完成经离线审计的 50/50 内置
  Android/Fedora 分区。拔除救援 TF 后首次内置 Fedora 启动已通过：根分区为
  `/dev/sda13`、`/boot` 为 `/dev/sda12`，Wi-Fi、双屏和核心用户服务正常。旧的
  Linux-only 记录仅保留为历史。
- 双向系统切换只允许修改 ROCKNIX ABL v1.1.8 的 4 KiB `devinfo` 中一个
  `BootMode` 字节，独立的 `BootSourceMode` 原样保留；写前校验整块模板并保存
  preimage，写后必须整块回读一致。Linux 端入口为二次确认的 Panel 次级操作，
  Android 端现已恢复为签名原生应用 `切换到 Linux`
  （`li.azka.pocketds.dualboot` 2.0），已安装到 AYANEO 启动器并通过当前内置
  Fedora 分区的真机只读检查；旧 root 脚本仅作救援后备，应用不依赖 TF 卡。
  v2 尚未实际按下切换，完整双向回环仍待机主现场确认。
- 当前启动只允许 `mem` + `deep`；`pm_async=0`。
- PDS-001 已有默认只读、双重显式解锁、RTC 15～300 秒救援和私有原子
  checkpoint 的 deep 循环验收器。v2 报告增加电池状态/外接电源类别、域分离 boot
  token、clean revision/helper hash 绑定及每轮源码不漂移门。四报告聚合器要求放电
  10×30 秒、充电 10×30 秒、满电 5×30 秒和放电 3×300 秒，共 28 轮；即使自动矩阵
  全过也固定保留声音/触控/手柄/Wi-Fi 流量和 >5 分钟长待机 NOT_EVALUATED，不能关闭
  PDS-001。单轮 12 项、聚合 9 项 fixture/static PASS；root helper 尚未部署，也未执行
  真实 suspend；正式 PDS-008 24 小时 soak 内没有执行 suspend。
- 因 s2idle 和锁屏路径仍不可靠，当前盒盖和电源键被安全设置为忽略，
  `Autolock=false`、`LockOnResume=false`。这只是故障隔离状态，不是最终方案。
- 未验证的自制锁屏 QML 不再由安装器覆盖 Plasma 系统包；实机已从校验匹配
  `plasma-workspace-6.7.4-1.fc44.aarch64` 的备份恢复 KDE 原文件，原型另行隔离保存。
- 本轮早期曾观测到启动后双屏 100%；当前硬件值约为上屏 56%、下屏 68%。
  PowerDevil/KWin D-Bus 却把两屏都报告为 100%，已从 6.7 上游源码确认：Linux
  backlight helper 会合并两路硬件背光，而 KWin 只把该设备分配给一路 internal
  output，另一路为软件亮度。独立 sysfs 已作为真值；`pocketds-brightness.service`
  已捕获当前 56%/68%，Panel 成功写硬件后原子保存。一般漂移连续两次才重放；
  两路被 PowerDevil 写成同一百分比时按已证实的全局覆写在首次观测恢复。
  KWin 已同步为 DSI-1=0.56、DSI-2=0.68；一次受控 PowerDevil 重启中，下屏
  约在 0.8 秒被覆写为 56%，1.8 秒采样前恢复为 68%，上屏始终为 56%。
  冷启动、resume 和 24 小时验收仍未执行。
- Panel 已接入内核电池百分比、充放电状态、外接电源、温度、健康度、循环、
  电压/电流与瞬时功率。驱动 `power_now` 约 53 W，与 V×I 明显矛盾，已禁止；
  V×I 与 UPower energy-rate 同量级并标记来源。energy/full 为 0，预计时间输出未知。
  现已从精确下游/当前上游/Qualcomm Android 三份源码锁定 PDS-017：SM8550 的
  property path 没有初始化 unit，导致 charge 属性被 mWh 默认值拒绝；charge 语义的
  单行 mAh 候选对精确源码 apply PASS，Fedora 44/aarch64 的 driver object、Image、
  319 modules、417 DTB/DTBO 构建 PASS；清空原 RPM buildroot initramfs 路径使它仍
  只是源码编译门，不是发行产物。
  已安装 UPower 1.91.3 的 charge→energy 路径也已锁定；因设备缺少设计电压而会
  使用实时电压，仍须验收 EnergyFull 漂移和 ETA。独立打包/启动未完成，且这是
  不得与 IFPC 合并的变量。
- PDS-006 现有严格私有的离线监督台账 evaluator：证据绑定测试 revision、panelctl
  和 Panel QML 哈希；要求拔电→放电 5 轮、插电→充电 5 轮、满电+外接 2 轮均无
  读取失败/缺字段/Plasma 重启，并要求独立 24 小时、至少 2880 样本、最大间隔
  ≤60 秒且覆盖时长自洽。模板/报告 0600、拒绝覆盖且不含原始值、路径、时间戳、
  设备标识或自由文本。12 项 fixture PASS；它不读硬件、不切电源，真实台账为
  **NOT RUN**；已完成的 PDS-008 soak 也不冒充电池连续性证据。
- DRM 暴露 DSI-1 的 1080×1920 模式和 DSI-2 的 768×1024 模式；KWin 对两块
  DSI 屏都报告无 EDID。
- 本次启动分别观察到 DSI-1/DSI-2 为 165/59.999 Hz 和 120/59.999 Hz；异刷不是
  硬件限制。schema-2 报告不提供两组模式之间的转换过程或原因。
- GPU devfreq 暴露频率表和 `cur_freq`；DRM fdinfo JSONL 观测器经过四轮优化。
  常驻 `pocketds-gpu-telemetry.service` 已以 2 秒原子缓存接入 Panel，真机显示负载、
  频率、GPU 热区和客户端数；现又以独立线程每 60 秒读取 KWin 双屏模式与渲染后端。
  68.028 秒实测 CPU 0.3173%、内存 14.3--16.1 MB、GPU 最大间隔 2007 ms、0 次重启；
  实机得到 DSI-1 165.0 Hz、DSI-2 59.999 Hz 与 Wayland/DRM/OpenGL/FD740。
  物理刷新率不冒充应用 FPS，后者保持不可用。受控重启 plasmashell 后，截图已
  目视确认 `165/60 Hz`、Wayland/DRM/OpenGL 和 GPU 卡片；遥测 PID/采样连续。
  正式 24 小时只读报告为 schema 2、`complete=true`、`accepted=false`：
  43,200/43,200 次读取和 43,200 个独立 GPU 时间戳，0 读取失败、过期或回退，
  最大间隔 2,097 ms、最大样本年龄 667 ms；43,200 个显示状态全部为 `ok`，
  应用 FPS 始终为 null。服务全程 PID 126678、0 重启，current memory 增长
  -106,496 字节，单核 CPU 0.2323%，soak observer 为 0.0298%。唯一失败的
  harness 门是 `one_refresh_pair`：报告同时看到 165.0/59.999 与 120.0/59.999；
  schema 2 不记录转换过程，不能从该报告推断第二组模式出现的原因。报告哈希为
  `c09f635b043bd9e20d1fa3425fafdef98102485cce7e87fd0bc44985912c097e`。
  该报告只证明遥测连续性和资源门。同一时段 journal 的独立只读分类为：83 次
  GMU `GPU_SET` OOB timeout、6 次显式 hangcheck、5 次 preemption timeout、11 次
  `recover_worker`、6 次 KWin offender/full graphics reset、12 次 atomic `EBUSY`；
  HFI、fenced-register、SMMU 和 runtime GPU fault 均为 0。11/11 recovery 前均有
  OOB burst；journal 时刻 13:20:45 后连续 52,730.946 秒没有新 OOB/recovery。
  KWin child PID 1011 全程未换，wrapper PID 1001、`NRestarts=0`。不能把 11 次
  `recover_worker` 全称为 hangcheck，也不能因后段无新事件声称系统稳定。
  后续只读时序复核发现，最后一次 recovery 在 monotonic 57136.773 结束，255 ms
  后 Panel 请求 `pocketds-performance`；此后 profile/GPU governor 一直为
  performance、GPU 固定 1 GHz，87,036.532 秒内没有新 OOB/recovery，但仍出现过
  1 次 fenced-register delay。它提示性能态与故障暴露时序有关，但不是因果证明，
  也不能作为日用修复：固定最高 GPU 频率会恶化功耗、温度和风扇；固定
  DRM/MSM 源码还证明 devfreq performance 不会清除 IFPC quirk 或改变 GMU idle
  level，因此静默尾段也可能只是负载时序或偶然。
  合成负载准确性和有救援的 KWin 丢失/恢复仍未执行。
  45 分钟 165/60 主动开发基线保持启动/显示签名不变，但观测到 1 次
  fenced-register delay 和 4 段可恢复 KWin D-state（其中 2 段约 10 秒）；
  采集器 CPU 1.556%。该轮结论为 observed，不是稳定性 PASS。24 小时运行已完成
  但未接纳；合成负载准确性验收仍未完成。
- `NetworkManager-wait-online.service` 没有开机关键消费者，原 5 秒 override 连续六次
  制造 failed unit；现已删除 override、恢复 Fedora vendor 语义并清除失败状态，
  冷启动验收仍待执行。
- firewalld 失败根因是 stock FedoraWorkstation zone 的 `samba-client` 依赖内核未编译
  的 `nf_conntrack_netbios_ns` helper。默认区已改为 public，KDE Connect 仅在
  `pocketds-home` 可信区开放；当前设备私有 Wi-Fi 绑定已生效，手机配对尚未测试。
- Renesas `1912:0014` xHCI 的 wake 策略已从过早的 PCI add 事件移到 driver bind
  后执行，并有严格设备身份 helper；当前会话为 disabled，冷启动/USB/suspend
  验收仍未执行。
- ES-DE 3.4.1 的旧路径已迁移到 `~/ES-DE`，保留非空用户 ROMDirectory；14 个
  自定义系统已安装。此前输入代际完成 50 轮 gamepad/joymouse 精确往返且无服务重启，
  并在幂等重建 keyboard target 后观测到 `held_modifiers=[]`；这些是历史基线，不替代
  当前游戏会话栈验收。
- 当前游戏输入栈已由设备 clean revision
  `b3bf26d188cc590b92590b83932c5b22fdf05ac8` 通过事务
  `20260829-181740-fbde0e12047eb9bc` 部署。它按真机实际
  `xbox-elite:Microsoft X-Box One Elite pad` 身份识别 target；部署后 3 轮
  `joymouse → gamepad → joymouse` 现场自动循环 PASS，InputPlumber 保持 PID 566、
  同一 invocation、0 restart。独立复审 GO 的 passive attended harness 与 60 秒响应
  修正已由设备 commit `e1278ab8f292` 跟踪，设备 30/30 focused tests、lint 和真机
  preflight PASS。首次 A 测试仍运行旧 10 秒窗口并 activation-timeout，未产生任何实体
  按键 PASS；随后 UI 正常退出，现场确认 Wiliwili/service 消失、模式恢复 joymouse、
  真实 `$XDG_RUNTIME_DIR/pocketds-game-session/session.json` 消失。退出恢复已闭环，
  A/B/X/Y、十字键、双摇杆、肩键、扳机和 Guide 仍需人在设备前验收。
- ES-DE 设置准备器现按官方源码同时解析当前 flat 多顶层格式和未来 `<settings>`
  包裹格式，全部输入先通过有界/无链接/无重复预检；launcher 在 status/set 失败时
  不启动并恢复精确旧输入模式。现场 `--check-only` 报告 settings/systems 均无需
  变更，前后文件哈希和备份计数一致；这未替代合成 GUI/真实库验收。
- ES-DE 空占位库 10/10 启动通过，后 9 次为 1.371～1.565 秒；全部 clean
  shutdown，无新增 core/KWin killer，输入恢复 gamepad。当前 ROM 树只有 `.txt`，
  因此载入 0 个游戏系统。后续隐私安全只读复核扫描 174 个普通文件、0 个可识别
  游戏、0 错误，约 0.003 秒；最新脱敏日志为 1.691 秒启动和 clean shutdown，
  仍无 warning/error/fatal/parse。当前没有“扫描卡死”的现场证据。测试仍可伴随
  atomic EBUSY，显示栈问题与配置错误独立。
- ES-DE 历史日志现已有专用脱敏摘要器：真机 53,306 字节/453 行日志
  只输出聚合计数，观测到 1 次 1.624 秒启动和 1 次干净退出，零
  warning/error/fatal 与零解析失败。该工具拒绝链接/非 UTF-8/超限输入，
  不输出原行、路径或 ROM 名称；此证据不等于真实库交互验收。
- PDS-012 合成 GUI harness 保留每轮人工“可交互”确认，但不再用按回车耗时冒充完整
  启动证据：每轮同时读取隔离日志的内部 startup、clean shutdown/error/parse 聚合，
  并在 launcher 退出后核对 InputPlumber 恢复。正式入口至少 5000 项/10 个新 HOME，
  需要环境 opt-in、精确确认词和新的 0600 报告；报告不含路径/ROM 名/原始日志/输入
  设备名，并绑定前后同一干净 Git revision。此前 fixture 错放到
  `$HOME/SyntheticLibrary`，与生产 launcher 强制的 `$HOME/ROMs` 冲突，导致真人
  harness 会在启动 ES-DE 前自行失败；`17e67ee` 已对齐路径并增加无 GUI 的真实
  launcher preflight 回归，13 项 fixture 和 5000 项 prepare-only PASS。隔离候选库
  的固定清单快速门为 43,874 个普通文件、31,122,917,449 logical bytes，无 extra/
  类型/大小偏差，但尚未切 live，也尚未完成逐内容摘要绑定。24 小时 soak 中没有
  启动 GUI，合成 GUI 与真实库结果仍为 NOT RUN。
- v2 stage 已核对 14 个系统、22,670 个 ROM/gamelist 条目、21,189 个媒体文件和
  71 项精选集；live 仍为 0 gamelist。隔离 `feat/pds012-esde-import-v1` 候选虽修复
  rollback-fence 与多个真实 SIGKILL 窗口，独立复审仍因 deterministic temp cleanup
  可删除 foreign regular file/symlink 及 user stat→unlink TOCTOU 判定 NO-GO。候选
  未提交、未部署，stage/live 未触碰；必须改成 transaction-scoped、持久来源绑定的
  temp identity 后重新审计。
- 键盘在真实 Panel 路径下手动显示/隐藏 50 轮通过，跨过 900 ms 焦点复检且
  PID 未变、0 次重启；Konsole 单轮自动显示已通过。隔离 Chromium 网页 entry
  首轮暴露应用退出后 defunct proxy identity 漂移导致键盘不隐藏，现以 500 ms
  低频 revalidation 和已捕获 focus ID 修复。复测 5/5 轮均稳定显示 1.1 秒，
  退出后 0.375--0.470 秒隐藏，同 PID、0 次重启。XWayland 重连仍未测。
- 键盘的 DSI-2 几何 cache 读取现已收紧为当前用户 0600、单链接、
  `O_NOFOLLOW`、≤256 KiB 单 fd JSON；损坏或替换时只回退已验证几何。
  现场 cache 契约只读 PASS；该源码现已随六文件事务部署，但不替代真机触摸/截图验收。
- PDS-011 已从候选链彻底移除 WXASR 目录客户端，改为当时的私有 HTTPS 语音后端
  主后端。凭据只由 hidden-input、断电可恢复 provisioner 写入私有文件，request worker
  在读 pipe 前关闭 dump 并用 no-new-privileges、RLIMIT_NPROC、seccomp 禁止派生进程；
  父进程持共享凭据锁跨越全部请求/重试，取消路径 TERM→KILL→reap。主程序、状态机、
  adapter、geometry、voice、user unit 和 provisioner 由同一个七文件事务发布，active
  preimage 缺失时写入前拒绝；WAV 进入 systemd 管理的 0700 RuntimeDirectory。Mac 与
  Pocket DS/Fedora aarch64 最终定向矩阵 130/130 PASS，独立审计 GO/P0=0/P1=0。
  七文件事务 `asr-runtime-20260829` 已在设备 apply/verify 并激活，键盘服务 0 restart，
  ASR 更新后 KWin 监听仍只见稳定 820x615。PipeWire 已暴露并默认使用物理
  `HiFi__Mic__source`；用户亲测 ChatGPT 内置语音端到端 PASS，锁定为正常且不作为
  键盘诊断目标。自制键盘已完成一次 valid WAV→cloud recognition，但 Q6APM 冷启动
  `APM_CMD_GRAPH_START`/ASoC `-110` 仍偶发；首个 retry 候选因 UI 有界性和上传前
  target 授权缺口 NO-GO。v4 替代状态机经独立审查无 P0/P1/P2，106+37+9 定向
  测试及设备布局/路由/事务测试 PASS，并通过六文件事务
  `keyboard-asr-retry-v4-20260829` apply/verify；键盘服务 active、0 crash restart。
  用户触发的 6 秒 readiness、单次自然 retry、取消与成功插入仍待现场验收。
  此后唯一新日志是 `CONNECTING` 阶段收到第二次激活后执行取消；现有证据不能区分
  二次触摸与 toolkit 重复信号。候选 `c5d12f0`/`64acb62` 仅把准备、停止收尾、识别、
  待粘贴阶段的重复激活改为幂等忽略，`RECORDING` 中一次点按停止的语义不变，并以
  generation/state 去重隐私安全日志。111 项 keyboard、37 ASR、9 voice-artifact、
  15 provisioner、17 transaction 及 geometry/visibility/UI/lint/diff 均 PASS。
  六文件事务 `keyboard-asr-idempotence-v2-20260829` 从干净 revision stage，六项
  preimage 完整，manifest `8988bb37…`，apply/verify 后六项全 MATCH；只读 plan 中仅
  keyboard main 有 drift，现 source/live hash 均为 `631a07c5…`。键盘只重启一次，
  PID 527927→548490、active、0 crash restart，KWin 1001 与 GPU telemetry 126678 未变。
  全程未自动录音；重复激活与成功插入仍待用户现场触摸验收。ChatGPT voice 仍是用户
  实测锁定 PASS，配置未动。
  Klipper 历史污染仍为 P2。
- 本地 fallback 保留 sherpa-onnx/SenseVoice int8 和 Whisper：四项外部 artifact 必须
  当前用户所有、单链接、非组/全局可写且大小有界；调用固定为中文、ITN、4 线程，
  只接收 256 KiB 内单个 `<|zh|>`/`<|Speech|>` JSON。SenseVoice/Whisper stdout
  均限时限量。runtime 现在同时要求同目录的
  `libonnxruntime.so`，避免 binary 存在却动态加载失败。官方 1.13.2 aarch64 shared
  归档与 2024-07-17 int8 模型已按 GitHub digest、成员、文件 hash 验证；隔离
  Fedora 44/aarch64 的最小 binary+library 布局用官方中文样本连续 5/5 正确。
  10 项产物、9 项 prepare 和 30 项键盘测试 PASS；本地 fallback 尚未部署/测量，物理 mic、
  私有语料和发行时 FunASR 1.1 协议复核仍开放。
- PDS-011 现已有私有语料/后端结果的离线评估器，能在不输出原文、转写、
  音频哈希或路径的情况下比较微平均 CER、p50/p95 延迟、峰值 RSS 和失败数。
  7 项 fixture PASS；尚无真实语料/结果，因此不声称任何后端达标。
- PDS-005 独立 Plasma 恢复 oneshot 已通过 19 项 mock、设备完整测试和 5/5 次
  真机 KDE graceful 恢复；后四次经已安装的 Panel 闭集动作触发。每次约 1 秒，
  KWin/键盘/GPU 遥测 PID 不变、0 重启且无 Pocket DS QML 加载错误。Panel 现已
  接入双确认恢复、KWin 原生全屏切换、动态 InputPlumber 说明和可逆收起入口。
  现场还发现存储的 819x614 token 精确时，Plasma 运行时仍可能夹到 816x608；
  改为按 `Plasmoid.screenGeometry` 绘制后，运行时恢复 819x614，精确 DSI-2 crop
  四边每个像素均覆盖、输出外相邻像素透明。物理触摸收起/重进、弹窗、活动窗口
  全屏，真机 SIGTERM/SIGKILL/启动失败回退及冷启动仍未验收。动态几何 helper
  已通过实机 dry-run 和活跃 shell fail-closed 门，尚未实际 apply/restore；独立
  LayerShell 继续为默认禁用实验。
- PDS-005/PDS-011 的六文件差异已通过窄事务
  `visible-fixes-20260829-0402` 应用并验证；事务 manifest SHA-256 为
  `56d7e1c6174a1310e52de789103dbe63bd7beec3ae7f920393ead18e157efee6`。
  现场 Panel QML 为
  `e530d601cd79010e5f6607e49907cb186148d4bb1254662894bdb611f12401da`，
  键盘主程序为
  `06cd83cf511645651d484b1d02c800fbc0a938ca9647d07981b16b5d1dd41b2a`；
  source/live 已逐字节相符。部署完成不是 UI 真人验收，触摸、截图、全屏和回桌面
  仍保持 NOT RUN。
- 后续 Panel/键盘视觉修正已在独立分支冻结为 `242539a`，独立复核结论
  P0/P1/P2 均为 0，并通过窄事务 `panel-keyboard-polish-v1-20260829` 应用和验证；
  manifest 为 `22bb68f2c983afc2231260a29c5e9836c58b4ce1d7453caa7f90d0ee16e5e254`，
  现场 Panel QML/键盘主程序分别为 `6c1d31d2…`/`4678102c…`。
  有界 Plasma graceful reload 与键盘单次 restart 均成功，KWin/键盘/GPU 遥测
  健康且无 crash restart；真机截图确认 F 行缩短、主键区不缩、坏图标消失、
  Codex 图标和风扇数字基线修正。实体触摸及动态 ASR 标签仍需用户在场验收。
- Codex 额度 collector 和 Panel 数据面仍已部署，但重建后的现场没有
  `~/.local/bin/codex`，也没有 `~/.codex` 登录状态，当前缓存按设计为
  `source=unavailable`。旧系统曾通过的 fresh `source=app-server` 只能作为历史证据，
  不能描述现状；恢复需要重新锁定 ARM64 CLI，并由用户完成账户登录。
- Panel 性能档已统一路由 TuneD 管理；当前内置 Fedora 的 7.1 Pocket DS 内核报告
  大核物理上限为 2.9568 GHz，且全局 `cpufreq/boost` 节点不接受状态切换。三档配置
  已按这份真机能力修正，不再写无效 boost 值；powersave/balanced/performance 矩阵
  核对 CPU、GPU、风扇和 Panel 状态后恢复 `pocketds-balanced`。
- PDS-022 的 live harness 现有严格 100-switch 模式：必须同时提供环境 opt-in 与精确
  确认词，按 powersave→balanced→performance 确定性循环 100 次；每次都等待 TuneD/
  fan 落稳、执行 `tuned-adm verify`、核对三组 CPU 上限/governor、GPU 上限/
  governor 和 Panel 标签。mock 已覆盖完整 100 次及第 5 次注入失败后的 EXIT trap
  恢复；默认三档模式保持兼容。24 小时 soak 中未运行真机压力序列，结果为 NOT RUN。
- 风扇只读验收器现已严格区分目标 PWM、控制器 PWM、hwmon 实际 PWM 与
  `fan1_input` 真实 RPM，并锁定现场下游控制器哈希。当前 8 样本短测维持
  `moderate`、PWM 51、2021--2052 RPM、0 次 ramp/安全地板违反，服务同 PID、
  0 次重启；本轮没有视频 decoder fd，声学体验保持 NOT_EVALUATED，未改曲线。
- 风扇证据的 GPU cache 现仅接受当前用户 0600 级别、单链接、≤256 KiB
  单 fd JSON；持久 JSONL 创建为 0600 新文件并拒绝覆盖。这只加固证据，
  不改风扇调度。
- PDS-009 已增加私有 0600 噪声标记与离线关联器：用户听到突发噪声时只记录
  时间点，事后才把标记夹在两侧采样之间，输出温度、目标/实际 PWM、RPM、
  CPU/GPU 与 decoder fd 的变化。聚合报告不含绝对时间戳、进程名或视频标识，
  且始终将声学因果关系标成 NOT_DETERMINED；fixture 已通过，尚无真人视频标记。
- 现场平滑风扇守护进程此前未进入事实源。现在与 live `9f04ff71…` 逐字节相同的
  GPL 源码已纳入 `.pds1` 输入；准备器锁定原/新 Source70 并要求 spec/源码成对输出，
  binary gate 强制最终 0755 payload 哈希。12+11 项 fixture PASS，未构建或部署。
- PDS-005/PDS-011 已把 UI 部署面收敛成 6 项窄事务：Panel QML/root
  helper、键盘 main/adapter/geometry/voice。stage 绑定干净 revision、payload 哈希
  和 live 前像；apply 逐项验证并在异常时自动回滚；显式 rollback 拒绝覆盖未知
  post-apply 改动。8 项 fixture PASS；现场事务 `visible-fixes-20260829-0402` 已应用、
  verify PASS，回滚边界保留。
- PDS-004/005/010 新增统一真人 UI/input 台账：必须绑定 clean revision、已应用
  六文件事务、source/payload/live/rollback 精确字节及 joymouse live profile，才评估
  Panel 几何/桌面/全屏/恢复、键盘跨应用/XWayland/触控和 RB/RT/LB/LT/模式循环。
  11 项 fixture/static PASS；模板/报告均 0600 不覆盖且无自由文本/路径/时间/应用名。
  现场事务已应用，但真人台账尚未填写，因此所有实体结果仍保持 NOT RUN。
- PDS-004 另有不依赖现场 display 的嵌套故障 harness：固定使用 KWin virtual backend、
  rootless Xwayland、独立 session/DBus 与私有 XDG 目录，只运行一个不可聚焦、无输入
  能力的 GTK3 X11 probe。发送信号前重验 PID/session/starttime/UID/executable/runtime，
  并比较宿主 KWin/Xwayland 与键盘/GPU/InputPlumber 前后身份；16 项 fixture/static
  PASS。执行入口需确认词、新 0600 报告和 3～5 轮；24 小时 soak 中保持 NOT RUN。
- 音频已记录 WSA MM1 的机器可写路由语义 `1,0`，不把第二个不可写 member
  当作左/右声道证据。DTS、运行 capture PCM、VA DMIC0/1 与官方双 decimator
  序列已锁定；生产 UCM 已暴露 `Mic`，PipeWire 物理 source/default 和基本真实
  采集 PASS。一次受控重启发现 `controlC0` 早于 ASoC 后端完成绑定，导致
  WirePlumber 首次枚举成零 profile card；`b78918d` 的 30 秒有界、三稳定样本
  playback/capture/mixer readiness barrier 已通过 ARM64 全量测试并以事务
  `20260904-205653-b78918d` 部署。下一次冷启动首次即创建物理 speaker/mic 和
  增强默认 sink，三项音频服务 active、0 restart，未见 profile inconsistency、
  auto-null、XRUN 或 Q6APM timeout。ChatGPT voice 保持锁定 PASS；仍开放的是
  声学质量、通道、隐私、DP、其余连续冷启动和 deep-resume，而不是重新证明
  source 存在。
- PDS-018 完整矩阵现有私有离线 evaluator：账本必须绑定测试提交与 UCM 哈希，
  且左右/立体声、实体麦克风、DP 20 轮、冷启动 5 轮、deep-resume 10 轮全部满足
  无服务重启、auto-null、XRUN 和路由漂移才会 PASS。12 项 fixture 已通过；
  evaluator 本身不播放、录音、切路由或改电源，真实监督账本尚未生成。
- 当前开发镜像仍存在 vendor RPM 的全局 `NOPASSWD: ALL` 和许可证/SBOM/资产来源
  清单缺口，PDS-020 仍是发行阻断。Chromium 子层的缺口已经关闭：离线 verifier
  使用官方 Arch Linux ARM build key 验过 Chromium 加 4 组 compat 的五个原包签名，
  核对三类包元数据、4 个 Chromium 提取物和 54 个 compat 条目，结果全部 PASS。
  锁中的 `release_ready=true` 只表示 Chromium 子层可复核，不得当成整个开发镜像
  可发布。最新 live-root 审计确认 Chromium 已 PASS/0 gap，整机仍以 9 类阻断 exit 1。
  全局规则来自必需的 `pocketds-userspace`；已锁定其 COPR 签名 SRPM，并准备出只移除
  该规则、改用独立 `.pds1` Release、替换为锁定现场平滑 Source70 的确定性变换；
  完整 67 文件/148,381 字节提取树也已绑定；13 项 source 与 11 项只读 binary payload
  fixture 门已通过。两个独立 Fedora 44/aarch64 离线 mock 结果中的 source RPM、
  binary RPM 和 buildroot 包清单现已逐字节一致；8 项只读可复现门在真实结果上 PASS，
  binary SHA-256 为 `cd60625f…`，71 项 payload manifest 为 `fdd27a68…`。这仍只是签名前
  证据：构建参数尚无签名证明，硬化 RPM 尚未签名、安装、升级回归或进入 mounted-root。
  另有 10 项 isolated post-sign receipt fixture/static PASS：只把 receipt 锁定公钥导入
  临时 keyring，强制一个 Header OpenPGP 签名、拒绝 legacy/多签名，并重新跑完整
  payload audit；新增 `.pds2` 专用入口固定其 source/build lock 和 60 路径 payload，
  不会误验历史 `.pds1`；真实项目签名 key/receipt/artifact 仍未建立。
  vendor 原始包另经两次 offline mock 重建，binary/source RPM 与 buildroot 包清单全等；
  新的确认门在 disposable mock root 完成 vendor→hardened→vendor→hardened 四阶段，
  两次硬化态都删除全局 sudoers、恢复锁定平滑风扇并维持唯一包身份，最后清理 root。
  该轮显式 `--nodeps --noscripts --notriggers`，只作为 `.pds1` payload ownership 历史证据。
  新增 `.pds2` 精确变换已移除现场被 mask、无进程且被自研键盘替代的 Onboard 全套集成；
  两次 clean offline mock 的 source/binary/buildroot manifest 逐字节一致，binary 为
  `457ee254…`、60 项 payload 为 `e7d14b81…`。135 个 Fedora 依赖 RPM、Fedora 44
  主钥匙和项目 RPM 已组成 81,798,562 字节的严格离线归档：逐包文件/payload 哈希、
  NEVRA 和 135/135 官方签名均通过，项目 RPM 仍明确为未签名。正式 full transaction
  以本地 136 包正常执行 DNF 依赖和 scriptlet/trigger，完成全程离线
  install→remove→reinstall，锁定包数 `187→323→322→323`，无 Onboard/全局 sudo，
  root 已清理。随后又把基线 186 RPM 并入完整目标包归档：322 RPM/151,701,195 字节、
  321/321 Fedora 签名通过；完全空 installroot 只用这些本地包安装出 322 包，manifest
  `7cdf60c9…`、`dnf check`、payload 合约和清理均 PASS。项目签名、签名 repo metadata、
  SELinux label、image 服务和真机升级仍开放。repo metadata 已进一步用官方 Fedora
  `createrepo_c` 固定 revision/mtime/参数做两份独立结果，4 文件逐字节一致；primary 的
  322 个位置/NEVRA/包哈希与归档全等。`repomd.xml` 为 `c45ef780…`，但 detached 签名
  明确不存在，未选择/生成发行密钥。新增 10 项 final-repository receipt fixture：强制
  先验 `.pds2` 签名、只替换一个项目容器、重建两份完整 metadata，再用 RPM receipt
  同一公钥验两份目录内的 `repomd.xml.asc`；当前 unsigned metadata 不能直接冒充最终
  结果。DNF5 signed-repository smoke 已实现 10 项 fixture/static 门：固定只启用本地
  `pds2`、同时启用 package/repo GPG、强制 `skip_if_unavailable=0`，先精确核对 322 行
  repoquery，再在空根运行正常 scriptlet/trigger 安装、`dnf check`、payload contract 和
  清理；报告绑定最终 receipt/RPM/repomd/signature/archive 哈希。真实签名输入不存在，
  因而实际 smoke 仍为 NOT RUN。
  干净 composition 已锁定为 Fedora 44/aarch64 的非启动、不可刷写 rootfs 暂存边界；
  模板已从历史 `.pds1` 修正到当前 `.pds2`。其 19 项 fixture/static 门进一步规定
  repository gate 不能用通用四字段占位 JSON，必须逐字段消费上述真实 DNF5 smoke
  receipt；release-evidence gate 同样不能占位，必须同时绑定五个仓库证据文件、
  11-check offline mounted-root audit、固定 validator lock 及同一 SBOM 的官方
  Schema+OWL/SHACL PASS 回执。项目 RPM、签名仓库、发行证据与 smoke 未完成时预检
  必须失败。未来成品
  仍须重新跑 mounted-root audit，ABL/分区/启动链继续属于另行批准阶段。
  最终 `container.tar` 现另有 10 项 fixture/static 的只读 offline rootfs smoke：绑定
  artifact/locks/322 包 manifest，验证 `.pds2` payload、遗留 absence、service wants、
  SELinux 标签并在同一非 live 根内跑 11-check mounted-root audit；真实 artifact 尚无，
  因而实际运行仍是 NOT RUN。其结构化回执已被 composition 的
  `rootfs_smoke_passed` 门逐字段消费，但真实门仍保持 false。
  两张壁纸的 OpenAI Media Service / GPT Image C2PA 签名、claim 与 data hash 已由
  官方 trust list 离线验证并锁定 receipt；这只关闭生成来源/完整性疑问，不代表
  许可证、作者或角色权利。公开 composition 也可走严格 asset-free profile，但必须
  同时证明清单与真实资产目录为空；安装器已用显式双 profile 复用该门。当前日用树
  默认 personal-assets 并保留壁纸，故发行门仍 BLOCKED。
- PDS-020 新增全 Git tracked source/SPDX 事实清单器：逐文件记录相对路径、哈希、
  mode、文本/二进制和客观 header，检查四个根级证据与权威
  `components/assets/ASSETS.json` 是否已跟踪，但固定保持
  `legal_conclusion=NOT_DETERMINED`、`release_ready=false`。schema v2 不再把资产
  清单误报成根 `ASSETS.json`，6 项 fixture PASS；
  它不替代项目许可证、上游来源或资产再分发的人工决定。
- PDS-020 的 SPDX 3.0.1 官方验证链已有 11 项 fixture/static PASS，并在隔离 Fedora
  44/aarch64 上用锁定 jsonschema/rdflib/pyshacl 验过官方 example：Schema、JSON-LD、
  OWL/SHACL 全部通过。三份官方材料按 URL/size/SHA 锁定且只离线读取；官方 Schema
  自带的两个等值重复键只在整件哈希验证后精确放行，其他输入仍严格拒绝重复键。
  最终 composition SBOM 尚未生成，工具固定不推断许可证/权利且不改变发行门。
  mounted-root 的旧 SPDX 内嵌对象 preflight 也已纠正为官方 flattened `@graph` 和
  IRI 引用语义，要求 `software_*` profile 字段、完整 SBOM→package membership 及
  有效 CreationInfo；对应 release-root fixture/static 已增至 18 项。
- PDS-014 诊断采集已改为唯一私有暂存、限时/限量、单 fd 脱敏和拒绝覆盖；当前
  启动生成的 11,492 字节归档通过内容/归档双 SHA、0600/0700 权限、tar 数值
  owner 0/0 与逐文件二次脱敏扫描。`make test`、`make test-hardware` 和不会实际
  休眠的 `make test-suspend` 同轮 PASS；下一次干净启动复验仍开放。
- PDS-014 新增冷启动后只读编排器：默认只展示计划，只有开机 15 分钟窗口内、
  精确确认和新 0600 输出路径齐全时才运行 `make test/test-hardware/test-suspend/
  diagnostic-bundle`，并比较前后仓库 revision/clean 状态以及关键 system/user
  服务 PID 与零重启状态。9 项
  fixture PASS；源码阶段没有重启、休眠或执行这套真机冷启动矩阵。
- PDS-014 冷启动报告已升级为 v2：原始 boot ID 只在进程内使用，报告写入带 schema
  domain separation 的 SHA-256 token；同时在矩阵前后只读核对当前用户 0600 亮度状态
  与两块 backlight raw 目标、唯一 Renesas 1912:0014 控制器/driver/wake 状态。新的纯
  离线聚合器只接受五份不同 boot token、同一 revision、全部 PASS 的私有报告，且聚合
  输出不含 token/路径/时间。目标测试 22 项 PASS；真实五次冷启动仍为 NOT RUN。

## 2026-08-28 证据提交

- `88d307f`：记录 45 分钟 observed 基线、键盘 50 轮、输入模式 50 轮与 TuneD
  真机矩阵；其中未完成的自动焦点、实体按键和长时验收仍保持开放。
- `abd0638`：加入默认禁用、未安装的 PDS-005 standalone Panel 实验。
- `35aa9b2` 与 `7d6c92b`：分别固化 WSA `1,0` 路由语义和默认静音音频验收门。
- `ac6c31d`：记录 GPU、音频和发行安全审计；`cf0f88c`仅加入 fail-closed
  内核 A/B 规划器，未构建或安装内核。
- `8496a3d`（设备 `f463594`）：加固 PDS-014 诊断证据生成；当前启动完整只读矩阵
  与二次隐私扫描 PASS，干净启动重复仍待执行。
- `616a186`（设备 `e3e7aec`）：增加 PDS-012 隐私最小化日志摘要；真机历史
  日志完整会话/1.624 秒/零错误，目标测试与全量 `make test` PASS。
- `e68147a`（设备 `44cd652`）：加入 PDS-001 受限 deep 循环验收器；真机只读
  预检 14 个现场门通过，未部署 helper 哈希门按设计失败，全量测试 PASS，
  没有执行 RTC 或 suspend。
- `85fde32`（设备 `28d278e`）：加固键盘几何 cache 读取；真机源码读取
  返回 `(283,720) 819×614`，目标与全量测试 PASS，未部署、未重启键盘。
- `6c9c1a1`（设备 `6f610ca`）：加固语音产物新鲜度、私有读取和清理；
  真机源码目标/全量测试 PASS，已安装键盘仍为旧版且 PID 136145/0 重启。
- `d4d9fd6`（设备 `64c24a0`）：增加隐私最小化中文 ASR CER/延迟/RSS 评估；
  真机 fixture 7/7 与全量测试 PASS，未运行模型或读取真实 WAV。
- `6535989`（设备 `1be49ef`）：加固风扇 GPU cache/证据输出；真机单样本
  当前为 quiet/PWM 51/2052 RPM/40°C/policy PASS，全量测试 PASS，未改 profile。

## 现场改动整理状态

原始现场已保存在设备快照 `~/Projects/pocketds-linux-kit.backup-20260827-2040`。
已完成拆分：

- Speaker MM1 路由已单独提交并通过短音与静态配置测试；完整音频矩阵仍未完成。
- 盒盖/电源键忽略、deep-only 和关闭 resume 认证作为安全基线单独管理。
- trusted lock-screen QML 原型未通过验收，不进入默认安装路径；实机包文件已恢复。

## 当前工作范围

当前已完成 PDS-003/PDS-006/PDS-007 的可验证底层组件，并将 PDS-004、
PDS-010、PDS-018、PDS-022 推进到分项验证阶段。PDS-002 已完成观测、A/B 规划工具，
并以只读采集器锁定运行 raw Image、双 config、A740 firmware、三份 bootimg 与定制
DTB；COPR binary/SRPM 签名、SRPM=精确 commit 的规范化树及盒盖 DTB 源码/精确重建
也已验证。7/7 lockup 均由成组 `GPU_SET` OOB timeout 领先，且官方 `msm-next`
明确未给同一 A740 启用 IFPC。精确 IFPC revert 已在两个独立的锁定 Fedora 44/aarch64
buildroot 完整构建；两个不同的未签名 RPM 容器拥有相同 RPM header manifest 和相同
499 项 payload，候选只改变 raw Image 与其派生 boot Image。raw Image 的 29 个差异字节
全部被锁定到 IFPC quirk、移除的 IFPC reglist 指针、build ID 与 RELR 派生位置；323 个
模块、双 DTB、System.map、config 和模块元数据均逐字节不变。
规划 schema v2 现要求回滚 bootimg/报告双哈希、目标设备/版本绑定、成功启动/恢复测试和
独立恢复路径，且每个 P0 A/B 至少三轮。独立 Fedora 44/aarch64 构建已用历史 GCC
16.1.1 复现签名 RPM 的 496/496 个声明项（解包树补 3 个隐式父目录后为 499 项；
341 文件、323 模块、raw Image、bootimg、
System.map、config、DTB 均逐字节一致）；验证器保留 RPM 容器非同一的边界。当前真机
native-lid boot 组合已另存为内容锁定回滚 artifact，并同时绑定运行三 alias、签名源码、
独立 baseline payload 重建、native-lid 源码补丁/DTB 重建与候选证据；静态预检 PASS。
候选 RPM 的精确 gzip 内核流现已与同一 native-lid DTB 对称组合并重算 Android v0
size/ID；现有镜像与工具从零生成的副本均为 17,090,560 字节、SHA-256 `b29b7c34...`，
11 项 fixture 拒绝输入/DTB/报告/路径漂移和覆盖。真实启动/恢复测试均为 0、独立恢复
路径仍为 false，因此候选未安装且 planner 回滚门仍关闭。恢复设计已从错误的 PC/UEFI
假设改为真实 ROCKNIX ABL 链：机器无 EFI runtime，v1.1.8 两个 ABL 分区的 258,048
字节 prefix 均等于官方 payload；Mac 的 baseline/candidate 与 fastboot 36.0.2 host
preflight PASS。RAM-only dispatcher 只允许唯一 unlocked 设备上的 `fastboot boot`，运行态
以 `/sys/kernel/notes` build ID 证明 baseline-in-RAM/candidate-on-disk；聚合器还绑定同一次
RAM boot 的恢复顺序和随后新 boot token 的正常启动；固定三 alias 事务又覆盖全 baseline
候选前态、candidate/混合态恢复、post-rename 精确回滚及持久 intent；五工具 56 项 fixture
PASS，真实 `/boot`/fastboot 序列仍 NOT RUN。正式 A/B 又加入同版本内核 notes 指纹、
KWin/双 firmware/风扇控制器固定控制，以及每个样本的
`pocketds-performance`/`aggressive` 运行控制；18 报告/9 对/6 boot 私有矩阵含
85°C 上限与 3°C 配对温差门；
离线 WebGL2+48 合成层负载锁定真实 Chromium V4L2 wrapper/binary，计划型执行器不会自行
切屏、切档或换内核，并提供不启动负载的全门 live-preflight；相关
collector/evaluator/ledger/workload/runner 共 42 项测试 PASS；
PDS-005 已完成当前会话 widget 精确几何、Panel 动作接线及 5 次有界 graceful
恢复，仍缺物理触摸、活动窗口全屏、故障回退和冷启动验收；PDS-020 仍阻断发行。
Q6APM 独立版本内核候选已双构建，运行 payload 离线一致并通过独立 offline-integrity
审查；它没有在设备安装、启动或热替换。独立 fastboot RAM 恢复尚未实测，因此
runtime/deployment 仍为 NO-GO。

## 下一步

1. PDS-001：在 RTC/SSH/控制台救援到位后做 deep suspend 循环，不自动尝试 s2idle；
   恢复盒盖动作前先完成声音、网络、输入、双屏与亮度恢复矩阵。
2. PDS-002：45 分钟 165/60 基线为 observed，不是 PASS。完整 baseline payload
   可复现构建、A740 IFPC 单变量候选的双 buildroot 独立复现及 native-lid runtime
   镜像确定性合成均已通过；24h soak 已完成但因观察到两组刷新率被拒绝，且 journal 仍有
   GPU 故障。下一步在用户在场时先让 candidate 保持
   在磁盘、用 Mac fastboot RAM-only 启动 baseline 并验证 build ID，再恢复三 alias 和
   正常启动。回滚门通过后才做
   三配置、每配置三轮的 18 报告匹配 A/B；固定 workload、台账、执行器和离线 evaluator
   已就绪。dmabuf-release 无限等待保持为第二个独立变量。仅在明确授权、救援通道
   就绪后执行，当前候选未在设备安装。
3. PDS-003：状态机、故障注入和一次 PowerDevil 重启实测已完成，已在约 1 秒
   覆写窗口内恢复独立目标。下一步用 PDS-014 v2 收集五次不同启动，再执行 resume 和 24 小时
   验收，再研究彻底阻止 PowerDevil 合并 helper 的上游/设备级方案。
4. PDS-012：空占位库 10/10 和 5000 项 prepare-only 已通过，harness 路径契约已修；
   下一步先做 5000 项合成库 10 轮真人 GUI，再对隔离真实库补内容摘要、可回滚切换
   和 10 轮启动验收，不能把文件数/大小快速门冒充内容或可交互证据。
5. PDS-004：保留手动 50 轮和 Chromium 5/5 自动焦点结果；先执行 3 轮
   隔离 Xwayland 故障/有界 client 重启，再补跨应用 10 轮；PDS-005 实验在完成编译、真实 1024×768 几何和回滚验收前
   继续默认禁用。
6. PDS-021/PDS-023：分别修复 firewalld 缺失 conntrack helper 的 zone 兼容问题，
   以及完成 Renesas xHCI 的 5 冷启动/30 deep/内置输入/双触屏/唯一 USB-C 监督台账；
   PDS-023 的 bind 时序源码、当前现场和离线 evaluator 已完成，真实矩阵尚未执行。
7. PDS-009：保持只读 collector 非常驻；用户在场时按固定视频协议对齐温度、
   目标/实际 PWM、RPM、CPU/GPU、decoder fd 和主观突变时间，再决定是否调整曲线。
