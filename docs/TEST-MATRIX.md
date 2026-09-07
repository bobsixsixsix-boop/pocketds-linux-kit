# 测试矩阵

> 公开版更新（2026-09-08）：语音已改为用户自备的 OpenAI 兼容 API。下文涉及旧语音后端的提交、测试数量和实机结果均为历史记录，不能作为新 API 的验收。旧服务身份与账户细节不公开。

结果使用 PASS、FAIL、BLOCKED、NOT RUN。测试报告写入被 Git 忽略的
`test-results/`，发行结论再摘要进文档。

| 层级 | 测试 | 当前 | 发行门槛 |
|---|---|---|---|
| 静态 | Python/C++/Shell/JSON/XML/desktop/凭据扫描 | PASS | 每个提交均 PASS |
| 单元 | Panel root helper 拒绝非法参数 | 新增 | 所有非法/注入输入被拒绝且无副作用 |
| 单元 | Panel status JSON schema/range | 新增 | 字段类型、范围和缺失策略稳定 |
| 服务 | keyboard/quota/GPU telemetry/brightness/fan/InputPlumber/TuneD/wait-online policy | 部署后目标服务 active；GPU telemetry 24h 同 PID/0 restart；冷启动复验待执行 | 干净启动无意外 failed unit |
| Panel 数据 | Codex quota app-server/cache/QML | collector 与 QML 已部署并 fail-closed；重建后 Codex ARM64 CLI 和 `~/.codex` 登录状态缺失，当前真机缓存为 `source=unavailable`。旧镜像 fresh `source=app-server` 仅是历史证据 | 重新锁定/安装官方 ARM64 CLI，由用户登录后 verifier 得到 fresh `source=app-server`；登录/未登录、单/双窗口、限流、超时和过期均不显示旧值；无凭据泄漏；真人屏幕验收 |
| 显示 | 两块 DRM connector、背光和 active mode | 165/59.999 曾目视 PASS；24h 报告同时见 165/59.999 和 120/59.999，固定单一模式门 FAIL，schema 2 不判定转换原因；DSI-2 物理/逻辑/scale 与四边铺满已 PASS | 165/60、60/60、单上屏各 2 小时；模式/Hz/变换与 Panel 一致，无持续 atomic EBUSY |
| 亮度 | 双硬件状态/Panel/漂移恢复 | 假 sysfs 全状态机 PASS；真机同值路由 PASS；PowerDevil 单次 restart PASS（下屏覆写约 1 秒，上屏无跳变） | 冷启动、PowerDevil restart 循环、resume、24 小时无误写；新状态 60%/60%；恢复 ≤2 秒 |
| 电池 | 百分比/状态/温度/V×I 功率/外接电源 | sysfs fixture 和监督台账 evaluator 12/12 PASS；真机满电+外接电源 PASS；PDS-017 charge-unit 来源/单变量/apply、ARM64 driver-object + Image + 319 modules + 417 DTB/DTBO 及 UPower 1.91.3 换算链门 PASS；PDS-035 已在不耗电的策略 smoke 中把因 suspend 禁用而从 Auto 降成 Ignore 的 2% 动作显式改为 `PowerOff`，UPower/PowerDevil active；独立打包/启动未做；拔插/充放电/独立 24h 台账 NOT RUN | 独立发行打包后可恢复启动确认 µAh、EnergyFull/实时电压漂移、ETA，放电/充电/满电/拔插循环；来源可信；未知字段不伪造；自然到 2% 时只观察一次 clean poweroff；24 小时稳定且无 Plasma 重启 |
| 输入 | joymouse/gamepad 精确切换 | 50 轮往返 PASS；profile/helper/observer 当前已部署；target 重建后 `held_modifiers=[]`、InputPlumber 同 PID/0 restart。监听器冷启动瞬态等待已实现并通过 mock/全套测试；Steam 干净退出后 whole-stack 事务 `20260904-204320-271097595d248a09` 以 manifest `441c5e1f…` 部署同代完整栈，source/live hash 匹配。随后受控冷启动第一次即回到 `gamepad`，监听器/InputPlumber active、0 restart；实体 R1/R2/LB/LT NOT RUN | 100 次切换无双重输入/漂移并恢复 joymouse；R1/R2/LB/LT 各 50 次真人操作无错键/卡键/方向错；Panel 说明匹配状态 |
| 键盘 | 显示/隐藏/输入/崩溃 | 手动 50/50 PASS；Konsole 单轮、Chromium entry 5/5 PASS；`panel-keyboard-polish-v1-20260829` 已部署并验 hash，真机截图确认 F 行压缩且主键区不缩；跨应用/实体触控/XWayland NOT RUN | 保留结果；跨至少 3 类应用 10 轮，show/hide ≤900 ms；nested XWayland 故障/有界 client 重启 3 轮且 host 不受影响；触控 50 次无错键/错位/越屏 |
| ChatGPT 内置语音 | 应用内麦克风与语音链 | 用户亲测端到端 PASS；物理 `HiFi__Mic__source` 已暴露/default。此项锁定为正常，不是自制键盘故障，也不应为诊断键盘而修改 launcher/权限 | 除非未来实际启动新内核且用户明确要求回归，否则不重复诊断或改动 |
| 语音 | 自制键盘 ASR 质量/冷启动/目标授权/私有产物 | 历史私有 HTTPS 语音后端、私有 provision、受限 worker、取消及七文件事务原候选 130/130 PASS 并已部署；一次真实 `parecord` 生成 valid WAV 并到达 cloud recognition。Q6APM 冷启动 `APM_CMD_GRAPH_START`/ASoC `-110` 仍偶发；首个 bounded retry NO-GO。v4 替代状态机独立审查无 P0/P1/P2，106 keyboard + 37 ASR + 9 voice-artifact 及设备布局/路由/事务测试 PASS，六文件事务已 apply/verify，服务 active、0 crash restart。此后一次 `CONNECTING` 重复激活触发旧取消语义；幂等候选 `c5d12f0`/`64acb62` 保留录音中点按停止，忽略其余 busy 阶段重复激活，111 keyboard + 37 ASR + 9 voice-artifact + 15 provisioner + 17 transaction 及 geometry/visibility/UI/lint/diff PASS。事务 `keyboard-asr-idempotence-v2-20260829` 已 stage/apply/verify，六项全 MATCH；键盘重启后 active、0 crash restart，KWin/GPU telemetry 未变，全程未自动录音。ChatGPT voice 用户实测锁定 PASS、未修改。自制键盘的 6 秒 readiness、自然 retry、取消、成功插入和质量/延迟/内存仍 NOT RUN | 原 target 在准备、停止、上传、提交、粘贴各阶段都必须精确有效；连续冷启动无 `-110`；用户在场测单击开始、准备中重复点按不取消、录音中单击停止及成功插入，再测短/长句、安静/断网与私有语料 CER/p95/RSS；修复 Klipper 历史污染；RPM 硬依赖 libseccomp；不重复诊断 ChatGPT voice |
| Panel Shell | PDS-005 下屏几何/退出/恢复 | `screenGeometry` 绘制后运行时 819x614、精确 crop 四边逐像素 PASS；恢复 19 项 mock 与真机 graceful 6/6 PASS；最新六文件事务 manifest `22bb68f2…`、Panel QML `6c1d31d2…`，截图确认 fan/Codex 图标及数字基线修正；真人触控/全屏/回桌面仍 NOT RUN | 冷启动保持铺满；真人触摸回桌面/重进、弹窗与活动窗口全屏可逆；真机 SIGTERM/SIGKILL/失败回退通过 |
| 音频 | speaker/mic/DP/reboot/resume | WSA MM1 `1,0` route/source-live hash PASS；DTS/capture PCM/VA DMIC0+1 与生产 UCM 已暴露物理 source/default；ChatGPT voice 和一次键盘 WAV 证明基本真实采集 PASS。一次冷启动在 `controlC0` 出现后、后端绑定完成前枚举，形成零 profile card；`b78918d` 的有界三样本 ALSA readiness barrier 与事务 `20260904-205653-b78918d` 已部署。下一次受控冷启动首次即得到 `HiFi (Mic, Speaker)`、实体 speaker/mic 和增强默认 sink，三服务 active/0 restart，未见 profile/auto-null/XRUN/Q6APM timeout。声学/通道/隐私、DP、其余 4 次连续冷启动和 deep-resume 仍 NOT RUN | 全矩阵通过，无 Q6APM graph-start timeout/`-110`、auto_null/monitor 误录、XRUN 或服务重启；不得把 mixer member 当物理左右声道 |
| 风扇 | quiet/balanced/performance/manual | 空闲 120 秒稳定；只读 collector fixture PASS，GPU cache 私有/有界/链接拒绝与证据 0600/no-clobber 已覆盖；8 样本真机短测 PASS；现场平滑控制器已逐字节纳入 `.pds1` 构建锁；视频复现 NOT RUN | PWM/目标 PWM/实际 RPM/温度/解码证据分离；安全地板与 ramp 无违反；用户在场视频无频繁突变 |
| 前端 | ES-DE 空库/合成库/真实库启动与扫描 | 空占位库 PASS 10/10；现场 174 普通文件/0 可识别游戏/0 错误，约 0.003 秒，最新脱敏日志 1.691 秒启动/干净退出/零解析错误，未见真实扫描卡死；合成 harness ROM 根契约已在 `17e67ee` 修复，13/13 与 5000 项 prepare-only PASS；隔离真实候选仅过 43,874 文件/31,122,917,449 bytes 快速门，未切 live/未验内容；合成 GUI/真实库 NOT RUN | 配置目录生效；先完成 5000 项合成库 10 轮，再对内容摘要绑定的真实库做可回滚切换和 10 轮；10 秒可交互、3 秒退出，日志非空且不改 ROM 内容 |
| 遥测 | DRM fdinfo GPU + 低频 KWin 显示采样器 | 正式 schema-2 24h：`complete=true`、`accepted=false`；43,200/43,200、0 failures/stale/regressions、gap 2,097 ms、age 667 ms；PID 126678/0 restart、memory -106,496 B、service 0.2323%/observer 0.0298%；观察到两组刷新率，`one_refresh_pair=false`，schema 2 不判定转换原因 | 固定刷新率重跑；新客户端 ≤10 秒；KWin 丢失/恢复降级；数值经合成负载校验；harness PASS 也不得覆盖 journal GPU 故障 |
| deep | 手动 suspend/resume | deep-only/pm_async=0/硬件动作忽略 preflight PASS；v2 单轮 12/12、四阶段 28 轮聚合 9/9 fixture PASS；helper 未部署，真 suspend NOT RUN | 四阶段自动矩阵通过后，另补声音/触控/手柄/Wi-Fi 真人恢复与 >5 分钟长待机；不同充电状态通过 |
| s2idle | suspend/resume | FAIL | 未修复前不得作为 fallback |
| 盒盖/电源键/空闲熄屏 | close/open、短按、5 分钟 idle、LCD 背光、Plasma 与唤醒画面 | SMOKE-PASSED：SY7758 的 DPMS off 非零背光缺陷由亮度服务兜底，blank 期间 250 ms 单 sysfs 等待唤醒。`22efdc1` 后盒盖/电源键闭环为 2529→`bl_power=4/raw=0/actual=0`→2529，唤醒 1.073 秒且真人目视桌面/Panel 正常。`9cca40f`/`bd51378` 恢复 AC/Battery 300 秒、LowBattery 120 秒 idle DPMS，保持不 dim、不锁屏、不休眠。首个一次性 smoke 漏测二次计时；raw DPMS wake 后现场复现 345 秒不关屏。`99c2b68` 让盒盖唤醒走 PowerDevil `wakeup()`→`refreshStatus()` 并验证双 DSI on。手工完整顺序第二轮、已部署 helper 实际调用第三轮均在 300 秒得到双屏 `bl_power=4` 和下屏 raw=0；随后受控冷启动 `b2544975…` 的无人操作轮再次在 300 秒得到双屏 `bl_power=4`、下屏 raw=0。2026-09-05 的 KWin/Mesa 崩溃恢复后，PowerDevil 曾因 clean exit 未被 vendor unit 拉起；`ab14490` 增加 2 秒 clean-exit 自愈，真机 PID 故障注入和下一轮 300 秒双屏硬关断再次 PASS；ARM64 全量测试通过 | 当前日用即可；以后必须至少验证两轮 idle-off，并同时验 DPMS、硬件背光、Plasma 所有权和非黑唤醒画面；维护唤醒禁止裸调 `kscreen-doctor --dpms on`。不进入 suspend/锁屏，Wi-Fi/后台与性能档保持；底层 KWin/Mesa crash 继续按 PDS-002 取证 |
| USB wake | Renesas 1912:0014 bind 后策略 | helper/rule 加强 mock 与来源绑定监督 evaluator 11/11 PASS；安装/源码哈希一致、udev verify PASS、当前会话 disabled；真实矩阵 NOT RUN | 5 次冷启动；内置键盘/手柄、双触屏、唯一 USB-C 的 USB2/USB3 枚举；30 次 deep 前后 disabled 且无 Renesas `-EBUSY`/xHCI error |
| GPU | Chromium/Steam/模拟器压力与显示矩阵 | 45 分钟 165/60 active baseline：fenced delay=1、KWin D-state 4 段；正式 24h journal：83 OOB、6 显式 hangcheck、5 preemption timeout、11 recover_worker、6 KWin offender/full reset、12 atomic EBUSY，0 HFI/fenced-register/SMMU/runtime GPU fault；11/11 recovery 前有 OOB burst。最后 recovery 后 255 ms 切到 `pocketds-performance`，随后 GPU governor/频率保持 performance/1 GHz，87,036.532 秒无新 OOB/recovery但有 1 次 fenced delay；这是支持 IFPC 假设的时序关联，不是稳定性 PASS 或可接受的高功耗日用修复。7/7 历史 lockup 同样由 OOB burst 领先；IFPC-only 候选未安装 | 先实测独立回滚；再完成 baseline/candidate × 3 boot rounds × dual165/60、dual60/60、upper-only165 共 18 份 45 分钟报告；候选无 GMU/HFI/hangcheck/fence D-state，EBUSY/SMMU/PSI/CPU 不回退，GPU ≤85°C 且配对升温 ≤3°C；诊断和恢复可用 |
| 安装 | apps/all 幂等和回滚 | 部分 PASS | 连续安装无额外 diff，备份可恢复 |
| 更新 | DNF5 硬件/图形保护、用户态计划、自动更新边界 | source/live SHA 同为 `c6c4dda8…`，root:root 0644 单链接；有效 exclude 精确等于硬件环+图形环 22 项且 `protect_running_kernel=1`；无 DNF automatic；stored-transaction evaluator 只可给 `AUDITED_NOT_RUN` | 普通用户态计划中不含两类保护环；新私有计划通过签名/vendor/磁盘/NEVRA/载荷与执行前二次 solver 门，受监督安装/回退和短 smoke 通过；内核不得走普通 DNF |
| 供应链 | Chromium V4L2 runtime/compat | 五个原包 SHA-256 与 detached signature 经官方 ALARM build key PASS；15 个元数据文件、4 个 Chromium 条目和 54 个 compat 条目一致；默认 verifier 只读/离线 | 在最终 composition 重跑 archive verifier 与 runtime verifier；回执、锁和镜像摘要共同签发 |
| 供应链 | PDS-002 kernel/source/boot | COPR binary/SRPM tag 268 均由固定 fingerprint PASS；SRPM 与精确 commit 的 99,850 项规范化树同 hash；运行 raw Image/config 与 RPM 相同；bootimg gzip/三 alias/DTB mirror PASS；盒盖源码补丁重建 DTB 与运行文件同 hash；隔离 Fedora 44/aarch64 + 历史 GCC 16.1.1 重建后 baseline 的 496/496 个 RPM 声明项和 499 项解包树全等；IFPC-only 候选再经双 buildroot 独立复现，两份不同 RPM 容器 header manifest/499 项 payload 全等，相对 baseline 仅 raw/boot Image 改变，29 个 raw 字节均锁定，323 模块/双 DTB/System.map/config 不变；live native-lid rollback artifact 静态组合预检 PASS；候选 runtime create/verify 两次为 `b29b7c34…`；真实非 UEFI ABL 路径、官方 v1.1.8 双槽 prefix、host fastboot/material 已锁定；RAM-only dispatch/build-ID/time attestation/boot-token 聚合/三别名事务共 56 项 fixture PASS；正式 A/B 的 notes/KWin/双 firmware/风扇控制器/逐样本性能和风扇配置/workload/报告复用/热门和受监督 runner 共 42 项测试 PASS，含不启动负载的全门 live-preflight；planner 仍拒绝零启动/恢复、无独立恢复路径和不足矩阵 | 用户在场完成 candidate-on-disk/baseline-in-RAM fastboot 实测、恢复三 alias 并正常启动；回滚验证报告与完整 18 报告 A/B 全部通过后才允许扩大安装 |
| 供应链 | `pocketds-userspace` 硬化 RPM | `.pds1` 13 source + 11 payload + 8 reproduction PASS，vendor/hardened 双 offline mock 与 payload-only rollback PASS；`.pds2` 移除不可解析的退休 Onboard 集成，15 source fixture、双 clean offline mock 逐字节一致（binary `457ee254…`/60 路径）；归档 verifier 12 项 fixture，真实依赖集 135/135 和完整目标集 321/321 Fedora 签名/项目 payload audit PASS；9 项 full-runner fixture + 真实全离线 install→remove→reinstall `187→323→322→323` PASS；empty-root runner 10 项 fixture + 真实本地 322 包 DNF/scriptlet/trigger/`dnf check`/cleanup PASS；repository verifier 10 项 fixture + 两份独立固定 revision/mtime/单 worker `createrepo_c` 结果逐字节一致，primary/filelists/other 精确绑定 322 包，`repomd.xml=c45ef780…` PASS；post-sign verifier 10 项 fixture 含独立 `.pds2` source/build lock 与 60 路径 payload 入口 PASS；final-repository verifier 10 项 fixture 锁定先签 RPM→单成员替换→双 metadata→同证书 detached repomd 顺序并拒绝复用 unsigned metadata PASS；DNF5 signed-repository smoke 10 项 fixture/static PASS，固定 package+repo GPG、`skip_if_unavailable=0`、322 行精确 query、空根 install/check/contract/cleanup；真实项目 RPM/repomd 签名和真实 smoke 尚无 | 选择 release key 并过 `.pds2` 真实 post-sign；用已签 RPM 重锁包集并重建/签名 repodata，过 final receipt 与真实 DNF5 smoke；在 image root 验 SELinux labels/services，再跑 mounted-root sudoers 三门；真机升级/回滚 |
| 供应链 | SPDX 3.0.1 离线语义验证 | 11 项 fixture/static PASS；锁定三份官方材料与 Fedora 44 jsonschema/rdflib/pyshacl/Python 包；官方 example 在隔离 aarch64 环境通过 JSON Schema + JSON-LD + OWL/SHACL；官方 schema 两个等值重复键仅有精确例外；不联网、不生成 SBOM、不推断授权 | 从最终 clean composition 生成完整 SBOM；用锁定材料真实验证；再与 THIRD-PARTY、LICENSE/NOTICE、源码提供、资产授权和 mounted-root image hash 交叉绑定 |
| 供应链 | Git tracked 源码/SPDX header 事实清单 | schema v2 的 6 项 fixture/static PASS；单 fd 读取并绑定路径/模式/字节/hash，权威资产证据路径固定为 `components/assets/ASSETS.json`；不联网、不改 Git、不推断许可，发行门恒 false | 在最终 clean revision 重跑并与源码归档、项目 LICENSE/NOTICE、THIRD-PARTY、最终 SBOM 和资产清单交叉绑定 |
| 资产来源 | 壁纸 C2PA 与安装 profile | C2PA 8 项 + installer profile 8 项 PASS；真实 signer/claim/data hash Trusted；默认 personal 精确覆盖，asset-free 仅清单/目录同空；TSA trust 与权利仍 false | 公开产物选可审计授权或独立 asset-free；不得用 C2PA 冒充许可，也不得删除日用副本制造产物 |
| 发行构建 | Fedora 44/aarch64 rootfs composition | 19 项 fixture/static PASS；模板已从陈旧 `.pds1` 切到当前 `.pds2`，真实模板按设计 exit 1，7 类缺口；签名仓库门只接受完整 DNF5 smoke；发行证据门逐文件绑定 LICENSE/NOTICE/THIRD-PARTY/SBOM/ASSETS、11-check offline mounted-root audit 与同 SBOM 的固定官方 Schema+OWL/SHACL 回执；rootfs smoke 门绑定稳定 policy、真实 locks、322 包/SELinux/audit 回执；三者均拒绝通用占位 evidence；所有证据唯一/内容哈希/拒绝 symlink；不可刷写、无启动/分区/身份/网络/构建副作用 | builder/RPM/仓库/证据全部签名锁定；真实 repository/rootfs smoke PASS 后预检 exit 0；ABL/分区仍不在此阶段 |
| 发行构建 | final `container.tar` offline rootfs smoke | 10 项 fixture/static PASS；绑定 artifact/稳定 composition policy/rootfs+build locks、Fedora 44/aarch64、322 包 RPMDB、`.pds2` payload/wants/legacy absence、SELinux labels 和同根 11-check mounted-root audit，并在结尾复核输入稳定；精确拒绝危险 composition 边界，仅接受 Fedora 标准 `os-release` 链接；不解包/挂载/安装/联网/启动服务，拒绝 live `/`；结构化回执已接入 composition gate | 签名 composition 产出后在独立 offline root 实跑并保存私有回执；真实服务启动另验 |
| 发行根 | PDS-020 sudoers/evidence/provenance/identity mounted-root audit | 18 项 fixture/static PASS；SPDX 预检已改为官方 flattened `@graph`/IRI 引用和 `software_*` profile 字段，拒绝旧内嵌 fixture、悬空/重复/漏列 package；非空占位、哈希篡改、漏列资产、symlink 逃逸均 fail-closed；显式 asset-free 仅在清单/目录同空时 PASS；最新真机审计按预期 exit 1，Chromium PASS/0 gap、其余 9 类阻断，报告 0600 | 干净离线 composition 审计 exit 0；第三方逐项授权；资产选逐项授权或独立空 profile；报告关联 commit/image digest；再消费独立官方 Schema + OWL/SHACL 回执 |
| 隐私 | 诊断包/发布包审计 | 唯一/限时/限量 allowlist 包真机 PASS；目录 0700、文件/归档 0600、双层 SHA PASS、tar owner 0/0、路径/本机身份二次脱敏 PASS；mounted-root 用户/系统状态分类器 PASS | 下次干净启动重复；无密钥、账号、SSID、ROM、Cookie、网络凭据、host key、随机种子或已初始化 machine-id |
| 性能 | PDS-022 Panel/TuneD/CPU/GPU/fan 三档 | 真机单轮三档 PASS；100-switch/中途失败恢复 mock PASS，真机 100 次 NOT RUN；最终恢复 balanced | 冷启动、游戏异常退出和更新后仍一致；100 次逐轮全状态核对且无分叉 |
| 网络 | firewalld/KDE Connect | firewalld running；public 隔离与 pocketds-home 规则 PASS；隐私最小化预检已自动化；手机 NOT RUN | nftables 无错误；只在可信区开放；配对、互传和 resume 重连通过 |
| 权限 | sudo/helper 最小权限 | 当前镜像 FAIL（全局 NOPASSWD）；硬化 RPM 已完成双 mock 签名前可复现构建和 payload 审计，尚未签名/安装 | 干净镜像无 `(ALL) NOPASSWD: ALL`；只保留白名单 helper；硬化 RPM 更新后不复活规则 |

## 统一入口

- `make lint`：静态检查
- `make test`：静态 + 安全单元 + Panel schema
- `make check`：现有运行态检查
- `make test-hardware`：只读硬件/服务快照与断言
- `make test-panel-geometry`：仅用临时 fixture 验证 PDS-005 原子应用/恢复与拒写边界
- `make test-phone-integration`：fixture 验证 PDS-013 固定只读命令、输出界限、网络/对端
  身份零采集以及失败关闭；`make observe-phone-integration` 才读取真机的非敏感布尔状态
- `make test-plasma-recovery`：仅用 mock 验证 PDS-005 独立 oneshot、有界超时、
  双重确认、request ID 和精确 unit-cgroup 边界，不操作现场 Plasma/KWin
- `make test-emulation`：只读检查 ES-DE 路径、输入模式和 ROM 树数量，不启动 GUI
- `make observe-emulation-log`：只读汇总 ES-DE 日志的级别、事件与启动耗时；
  不输出日志路径、原始行、系统名或 ROM 名称
- `make test-emulation-synthetic`：在临时 HOME 生成并验证 5000 项无版权合成库，
  不访问真实 ROM、不启动 GUI；真实窗口另有双重 opt-in 入口
- `POCKETDS_ALLOW_ESDE_GUI_TEST=YES CONFIRM=POCKETDS-RUN-ESDE-SYNTHETIC-GUI
  OUTPUT=/private/new-report.json make test-emulation-synthetic-gui`：由人在设备前
  确认 10 个新 HOME 均在 10 秒内可交互；同时要求内部 startup≤10 秒、退出≤3 秒、
  clean/error-free log 和每轮输入模式恢复，输出 0600 不覆盖报告
- `make verify-chromium-runtime`：离线只读核对 `/opt` 提取物和实际 `/usr/local/bin`
  wrapper，并验证仓库中收据/证书的固定哈希；`release_ready` 只代表 Chromium 子层
- `ARCHIVE_DIR=/path/to/packages SQV=/path/to/sqv make verify-chromium-provenance`：
  对五个固定原包执行 SHA-256、包内元数据/锁定条目和 detached signature 复核；
  不联网、不安装、不解包到系统，也不启动 Chromium
- `ARCHIVE_DIR=/private/rootfs-package-archives RESULT_A=/private/repo-a/repodata
  RESULT_B=/private/repo-b/repodata make verify-userspace-rpm-repository`：离线只读复核
  322 包完整归档、321 个 Fedora 签名、项目 payload、锁定 `createrepo_c` 及两份逐字节
  一致的 repodata；不生成仓库/密钥/签名，当前签名门和发行门仍为 false
- `RECEIPT=... RPM_POSTSIGN_RECEIPT=... SIGNED_RPM=... RELEASE_PUBLIC_KEY=...
  REPOMD_SIGNATURE=$RESULT_A/repomd.xml.asc UNSIGNED_ARCHIVE_DIR=...
  SIGNED_ARCHIVE_DIR=... RESULT_A=... RESULT_B=...
  make verify-userspace-rpm-repository-postsign`：离线只读验证未来最终签名链；不签名、
  不生成密钥、不安装，且 DNF5 `repo_gpgcheck` smoke/发行门仍为 false
- `make plan-userspace-rpm-repository-smoke`：只打印离线 DNF5 烟测边界；真实执行须提供
  与 final-repository verifier 相同的九类证据、私有空工作目录/新 0600 报告和精确确认词，
  并固定 `repo_gpgcheck=1`、`gpgcheck=1`、`skip_if_unavailable=0`；仅在 disposable root
  安装精确 322 包并清理，不触碰设备根，当前真实签名输入不存在所以 NOT RUN
- `make test-release-audit`：临时 fixture/static 验证 PDS-020 审计器的 sudoers
  解析、缺失证据 fail-closed、路径脱敏、私密状态分类与 live-root 确认边界
- `make audit-release-root RELEASE_ROOT=/mnt/pocketds-release`：只读审计离线挂载的
  发行根；exit 1 表示有阻断，绝不能把开发机隐私状态清理动作藏进该入口
- `make inventory-source-licenses`：只读枚举 Git 已跟踪文件及客观 SPDX header；
  输出不是许可证结论，永远不能单独把发行门改为 PASS
- `SBOM=... SPDX_CONTEXT=... SPDX_JSON_SCHEMA=... SPDX_MODEL=...
  OUTPUT=/private/new-report.json make verify-spdx-offline`：只用锁定本地材料依次跑
  官方 SPDX 3.0.1 JSON Schema 与 OWL/SHACL；不下载、不生成 SBOM、不推断授权，
  即使两层 PASS 也保持整个固件 `release_ready=false`
- `make test-asr-runtime`：fixture 验证本地 SenseVoice 候选、严格 JSON、artifact
  权限/链接/大小和 stdout/timeout 上限；不读取麦克风、不运行真实模型或上传音频
- `make test-asr-prepare`：fixture 验证两个精确归档、最小四文件 runtime、三份
  NOTICE/license、执行确认、目标漂移、链接成员、安装后中文 smoke 和失败回滚；
  真归档默认只读入口为 `python3 scripts/pds011-sensevoice-prepare.py
  --engine-archive ... --model-archive ...`，不联网、不安装，只有追加 `--execute
  --confirm POCKETDS-INSTALL-SENSEVOICE-V1.13.2` 才会写当前用户目录
- `LEDGER=/private/new-battery.json make prepare-battery-ledger`：生成绑定当前 revision、
  panelctl/QML 哈希的 0600 不覆盖监督台账；`EVIDENCE=/private/filled.json make
  evaluate-battery-matrix` 只离线评估拔插/充放电/满电和独立 24h 连续性，不读取
  sysfs、不切换电源、不启动或重启服务
- `LEDGER=/private/new-usb-wake.json make prepare-usb-wake-ledger`：生成绑定当前
  revision、wake helper 与 udev rule 哈希的 0600 不覆盖监督台账；
  `EVIDENCE=/private/filled.json make evaluate-usb-wake-matrix` 仅离线评估 5 次
  冷启动、30 次 deep、内置输入/双触屏与唯一 USB-C 的 USB2/USB3 枚举，不读
  sysfs、不休眠、不操作 USB 或服务
- `make plan-ui-update`：只读列出严格限定的 6 项 UI 更新；stage/apply/
  rollback 各需不同确认词，`verify-ui-update` 只核对哈希与 mode，任何入口都不激活服务
- `make test-ui-input-matrix`：fixture 验证 PDS-004/005/010 私有真人台账、applied
  transaction/source/payload/live/rollback/joymouse 绑定和 11 个严格门，不操作现场 UI
- `make test-xwayland-isolation`：fixture/static 验证 PDS-004 嵌套 virtual KWin/
  rootless Xwayland 的 PID/session/starttime/UID/executable/private-runtime 信号边界；
  `make plan-xwayland-isolation` 仅做只读预检，不启动图形进程
- `CONFIRM=POCKETDS-RUN-NESTED-XWAYLAND-FAILURE OUTPUT=/private/new-report.json make
  accept-xwayland-isolation`：仅在用户在场时启动 3～5 轮独立嵌套会话；只终止精确
  Xwayland，不注入输入、不联网、不重启服务，并核对宿主进程/支持服务不变
- `TRANSACTION=<name> LEDGER=<new> make prepare-ui-input-ledger`：仅在六文件事务已
  应用且 live 精确匹配时建立 0600 不覆盖模板；`EVIDENCE=<filled> make
  evaluate-ui-input` 只离线评估，不启动/重启/注入输入
- `make observe-gpu DURATION=1800 INTERVAL=2`：生成只读 GPU/DRM JSONL 观测报告
- `make observe-fan SAMPLES=80 INTERVAL=1.5`：stdout 输出只读风扇/温度/RPM/
  CPU/GPU/video-node JSONL；不启动负载或改变 profile
- `make mark-fan-noise MARKERS=/private/new-or-existing.jsonl`：用户听见突发噪声时
  追加一个私有 0600 时间标记；不采音、不读取媒体，也不改变风扇
- `make analyze-fan-noise EVIDENCE=/private/fan.jsonl MARKERS=/private/markers.jsonl`：
  离线对齐标记两侧样本，只报告变化量且不推断声学因果
- `make evaluate-audio-matrix EVIDENCE=/private/audio-matrix.json`：离线核验绑定当前
  revision/UCM 哈希的监督音频账本；不会播放、录音、切换路由或改变电源
- `make diagnostic-bundle`：在唯一私有临时目录限时/限量采集，脱敏并校验后才生成
  owner/路径去身份化且不覆盖旧证据的本地诊断包
- `make test-suspend`：仅做 suspend 前置检查；真实 suspend 必须显式授权参数
- `POCKETDS_ALLOW_POWER_STRESS=YES CONFIRM=POCKETDS-POWER-100 make
  test-power-stress-live`：仅在用户在设备前运行 100 次可逆三档序列；每次核对
  TuneD/CPU/GPU/fan/Panel，异常或中断经 EXIT trap 恢复原档
- `make observe-deep-preflight`：只读检查 deep/RTC/双屏/背光/音频/电池/
  服务和已安装 helper 哈希；本入口永远不触发 suspend
- `REPORT1=... REPORT2=... REPORT3=... REPORT4=... OUTPUT=/private/new-deep-series.json
  make evaluate-deep-series`：离线严格聚合放电/充电/满电短轮与放电五分钟四份 v2
  报告；只给 automated partial，永远不把未测的物理交互和长待机标成完成
- `make post-boot-plan`：只列 PDS-014 冷启动验收步骤，不执行命令
- `CONFIRM=POCKETDS-POST-BOOT-READONLY OUTPUT=/private/new-report.json make accept-post-boot`：
  仅在开机 15 分钟内运行已有只读测试与诊断包，并在前后核对 0600 双背光目标和
  唯一 Renesas xHCI wake=disabled；报告只保存不可逆 boot token，不重启、不休眠、不联网安装
- `REPORT1=... REPORT2=... REPORT3=... REPORT4=... REPORT5=... OUTPUT=/private/new-series.json
  make evaluate-post-boot-series`：离线严格读取五份当前用户 0600 v2 报告；只在 boot
  token 全不同、revision 相同且每轮所有门 PASS 时接受，聚合输出不含 token、路径或时间
- `make audit-ui-deployment`：只读比较 Panel/键盘直拷源码与现场文件；panelctl
  保持 NOT_EVALUATED。提供当前源码编译候选后用 `make audit-ui-deployment-candidate`

任何会改变电源状态、声音路由、输入 profile、亮度或服务状态的测试都不能藏在
默认 `make test` 中。
