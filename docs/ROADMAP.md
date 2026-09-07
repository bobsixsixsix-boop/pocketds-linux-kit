# PocketDS Linux Firmware 路线图

> 公开版更新（2026-09-08）：语音已改为用户自备的 OpenAI 兼容 API。下文涉及旧语音后端的提交、测试数量和实机结果均为历史记录，不能作为新 API 的验收。旧服务身份与账户细节不公开。

本路线图以“可日用、可复现、可回滚、最终可发布”为准绳。问题编号统一使用
`PDS-xxx`；状态和验收标准见 [KNOWN-ISSUES.md](KNOWN-ISSUES.md)。

## M0：工程底座与基线（进行中）

目标：任何新会话都能从仓库恢复上下文，任何失败都留下可复现证据。

- [x] 建立设备端 Git 仓库和分组件目录
- [x] 建立系统文件备份和幂等安装入口
- [x] 建立凭据扫描和基础运行态检查
- [x] 建立路线图、项目状态、硬件知识库、决策记录和发行门槛
- [x] 建立只读单元/服务/硬件测试和脱敏诊断包入口
- [x] PDS-014：在当前设备启动上完成所有新测试并生成首份脱敏诊断包
- [ ] PDS-014：提交工程底座，并在下一次干净启动重复测试保存基线报告
- [ ] 审核并拆分当前未提交的休眠安全回退、锁屏键盘和音频路由改动

退出条件：`make test`、`make test-hardware` 可重复运行；诊断包不含密钥、账户
或 ROM；所有未提交现场改动都有明确归属。

## M1：电源、显示与 GPU 可靠性（发行阻断）

- PDS-001：deep suspend、盒盖、唤醒和待机功耗
- PDS-002：Adreno/KWin `dma_fence` 黑屏与分级恢复
- PDS-023：修复内部 Renesas xHCI wake 策略并纳入回归
- PDS-003：开机、登录、唤醒时恢复上下屏 60% 亮度
- PDS-016：消除或解释 NetworkManager wait-online 启动失败
- PDS-019：恢复安全可用的唤醒认证路径
- PDS-018：扬声器、物理麦克风、DP 与重启/resume 音频矩阵

退出条件：没有未解释的立即唤醒或 s2idle 卡死；Wi-Fi、声音、触摸、手柄和亮度
在循环测试后恢复；GPU 恢复按钮不会制造永久黑屏。

当前进度：PDS-002 的 45 分钟 165/60 基线只能判定为 observed，其中有
1 次 fenced-register delay 和 4 段可恢复 KWin D-state。fail-closed 内核 A/B
规划器和真机运行 raw Image/config/A740 firmware/bootimg/DTB 采集器已提交；
官方 COPR binary/SRPM 签名、SRPM 与精确 commit 的 99,850 项树一致性、本地盒盖
DTB 源码补丁及精确重建均已验证。7/7 历史 lockup 前均有成组 `GPU_SET` OOB timeout，
官方 `msm-next` 又未给同一 A740 启用下游 IFPC/A750 reglist；单变量 revert 已在两个
隔离的锁定 buildroot 独立构建，两份不同 RPM 容器的 header manifest 和 499 项 payload
全等；相对 baseline 只有 raw/boot Image 改变，29 个 raw Image 差异字节均有锁定解释。
A/B schema v2 把回滚 bootimg/报告双哈希、成功启动与
独立恢复测试、至少三轮匹配运行设为硬门。完整 baseline 内核已在独立 Fedora
44/aarch64 buildroot 中用历史 GCC 16.1.1 达成 496/496 个 RPM 声明项完全一致（解包树
补入 3 个隐式父目录后共 499 项）；RPM 容器
差异没有被冒充 payload 差异。真实预验证回滚 artifact 和实验内核安装仍未完成，候选
未触碰设备。正式测试现另有锁定 Chromium/WebGL2 合成负载、同版本内核 notes 指纹、
KWin/双 firmware/风扇控制器固定控制，以及逐样本固定的
`pocketds-performance`/`aggressive` 运行配置；18 报告/9 对/6 boot 私有验收器包含
85°C/3°C 热门。执行器默认只规划，真机入口遇到配置不符只拒绝，不会自行切屏、切档、
重启或安装；另有不落报告、不启动 Chromium/collector 的全门只读 live-preflight。
PDS-018 已完成 WSA `1,0` 路由语义、DTS/capture PCM、DMIC0/1 与官方双
decimator 锁定；生产 UCM 已暴露物理 PipeWire source/default，ChatGPT voice 和
一次键盘 WAV 证明基本真实采集。当前阻塞已转为 Q6APM 偶发 cold-start `-110`，以及
声学/通道/隐私、DP、连续冷启动、reboot/deep-resume 的完整监督矩阵。

PDS-001 已完成一套默认只读、必须双重显式解锁、限制为 deep 且带
RTC 救援的循环验收器 fixture。正式 24 小时 soak 结束前不部署 helper、
不尝试真实 suspend。

## M2：Panel、遥测与掌机交互

- PDS-005：下屏真正铺满、可退出、可重新进入、全屏切换和安全恢复桌面
- PDS-006：电池百分比、状态、温度、功率及可用的预计时间
- PDS-007：真实 GPU 频率/负载数据源
- PDS-008：每块屏幕的活动模式、刷新率、渲染后端和低开销 FPS 观测
- PDS-009：验证并重做风扇 profile/迟滞/反馈闭环
- PDS-010：RB 左键、RT 右键和动态快捷键说明页
- PDS-022：性能按钮切换真实 TuneD/PPD，而不是只改 CPU governor

退出条件：Panel 在 24 小时观察期内无崩溃、无明显泄漏；所有显示数据可追溯到
明确数据源；触摸目标适合下屏；无效功能不会占据 UI。

当前进度：PDS-005 的 Plasma widget 已按运行时 `screenGeometry` 819x614 通过
四边逐像素验收，独立有界 oneshot 通过 19 项 mock 和 5/5 次真机 graceful
恢复；Panel 确认、收起、动态说明与全屏 UI 已接线，但真人触摸、活动窗口全屏、
故障回退和冷启动仍未验收，standalone LayerShell 继续默认禁用。PDS-010 的 50 轮模式往返已通过，但实体
RB/RT 按键/抖动和动态说明页未测。PDS-022 真机 TuneD 三档矩阵已通过并
恢复 balanced；冷启动、异常退出和 100 次切换仍是验收项。PDS-008 后端已
实机输出 165/59.999 Hz、双屏逻辑/scale 和 Wayland/DRM/OpenGL/FD740，QML 目视、
user-bus 降级和 60 分钟资源门已通过；KWin 丢失/恢复与 24 小时仍待验收。

PDS-017 已锁定为 SM8550 property path 的 charge-unit 初始化缺口；单行 mAh 候选
对精确下游源码 apply PASS，ARM64 driver object + Image + 319 modules + 417
DTB/DTBO 构建 PASS；该构建清空了 RPM buildroot 专用 initramfs 路径，不能冒充
发行包。UPower 1.91.3 换算路径已锁定，但实时电压 fallback 仍需漂移验收。后续须
通过独立打包候选和用户在场恢复路径验证 sysfs/UPower/充放电，不能与 PDS-002 IFPC
候选合并。

## M3：输入、语音、游戏与互联

- PDS-004：停止键盘切换闪退
- PDS-011：原型验证 Panel 内键盘和新中文离线 ASR
- PDS-012：定位并修复 ES-DE 扫描卡死
- PDS-013：选择 KDE Connect/LocalSend/Tailscale 的合理组合
- PDS-021：先修复 firewalld/nftables，再启用 KDE Connect 默认路径

退出条件：键盘或语音至少有一套达到日用门槛；前端扫描可取消且有进度；游戏退出
后输入、性能、声音和 Panel 状态均恢复。

当前进度：PDS-012 已完成 ES-DE 3.x 路径迁移、专用系统映射、输入失败恢复和真实
游戏启动链。2026-09-04 全量 Android/Linux 共用库已从固定清单部署：14 类、22,670
ROM、21,189 media、71 条无重复精选，43,890 个受管文件全量卡上 SHA-256 PASS。
Linux 真实 KDE 会话在 5,124 ms 内载入 22,644 个可见游戏且零解析错误；余下仅为
Android 天马 G 的分类目视和一次实际启动验收，不再用合成占位结果代替真机结论。

PDS-004 已通过真实 Panel 手动显示/隐藏 50 轮，但 Chromium/Konsole 自动
焦点和 XWayland 重连未执行，因此仍未达到退出条件。

PDS-011 的当时的私有语音后端、私有 provisioner、受限 disposable worker 和
七文件发布/回滚已通过 Pocket DS aarch64 130/130 及独立 GO；WXASR 已退休。
七文件事务 `asr-runtime-20260829` 已部署并验证；一次键盘录音已生成 valid WAV 并
到达 cloud recognition。用户亲测 ChatGPT 内置语音端到端 PASS，锁定为正常且不再
诊断。v4 replacement retry 状态机已独立审查、设备本机测试并通过六文件事务部署，
服务 active 且 0 crash restart。下一步只针对自制键盘完成用户触发的 6 秒 readiness、
自然失败后单次 retry、取消、连续冷启动、原 target 上传/插入、中文语料和
clipboard-history 验收。随后一次 `CONNECTING` 阶段重复激活触发了旧取消语义；
幂等候选 `c5d12f0`/`64acb62` 已通过 111 keyboard、37 ASR、9 voice-artifact、
15 provisioner、17 transaction 及几何/可见性/UI/lint/diff 门，并通过六文件事务
`keyboard-asr-idempotence-v2-20260829` stage/apply/verify；六项全 MATCH，键盘仅重启
一次且 active/0 crash restart，KWin/GPU telemetry 未重启。下一步是用户现场单击开始、
准备中重复激活不取消、录音中单击停止及原目标插入。ChatGPT voice 继续锁定 PASS，
不参与该验收。

PDS-013 已确认 KDE Connect 包和 daemon 正常，firewalld 只在显式可信的
`pocketds-home` 开放 1714-1764/tcp+udp；2026-08-29 复核 LocalSend 的 discovery
冒充与 Web Share XSS 公告仍无 patched version，继续不纳入固件；Tailscale 保持用户主动选择。手机配对、双向互传
和 resume 重连仍需真人验收。

## M4：Android 双系统与发行

- PDS-015：在不依赖当前 Linux-only 开发机分区状态的前提下设计双系统
- 制作可重复安装/升级/恢复流程
- 许可证、校验和、变更日志、已知问题和救援文档
- PDS-020：收紧 sudo 权限，建立第三方来源、许可证、SBOM 与校验链
- 长时间待机、压力和真实日用验收

当前开发镜像因 vendor 全局 `NOPASSWD: ALL`、许可证/来源/SBOM 缺口、壁纸
再分发证据不足和 `/opt` Chromium runtime 缺可重现回执而被 PDS-020 阻断发行。
不应在唯一救援开发机上原地删除访问路径；需在干净发行 composition 中验收。

任何分区、ABL、Bootloader 或整盘镜像操作都必须在单独的已批准阶段进行。
