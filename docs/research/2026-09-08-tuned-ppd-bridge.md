# KDE 电源方案与 Panel 共用 TuneD

本文是 Alpha3 发布后的源码后续方案。已发布的 Alpha3 镜像不包含此桥，
本次也没有重新制作或替换镜像。安装入口是 `scripts/install.sh --apps` 或
`--all`；将来整合进 SD 镜像仍需补充离线安装映射与镜像验收。

## 提示的原因

2026-09-08 日用设备的 KDE“电源和电池”显示“电源管理方案不可用”。当时
`tuned.service` 正常运行，`pocketds-balanced` 通过实际参数校验，Panel 三档方案
仍然可用。缺失的是 KDE 使用的标准 Power Profiles D-Bus 接口。

原安装器为了避免 TuneD 与 `power-profiles-daemon` 同时调控设备，保留后者软件包
但禁用并 mask 其服务。这一保护有必要：`pocketds-base` 依赖该软件包，直接安装
互斥的 Fedora `tuned-ppd` 会牵涉设备支持元包。此前遗漏了向 KDE 提供兼容接口。
该提示与此前修复的 PowerDevil QML/Qt ABI 加载错误是两个问题。

## 实现

`pocketds-tuned-ppd.service` 通过一个薄启动器使用系统 `tuned` 软件包已提供的
官方 `tuned.ppd.controller` 和 D-Bus exporter，不复制其完整实现，不另外调频。
已核对的运行版本为 `tuned-2.27.0-1.fc44`。

| KDE 标准档位 | TuneD 现有方案 | Panel 档位 |
| --- | --- | --- |
| power-saver | pocketds-powersave | 省电 |
| balanced | pocketds-balanced | 均衡 |
| performance | pocketds-performance | 性能 |

CPU/GPU 参数和风扇方案全部沿用现有 profile。Panel 继续直接调用 TuneD；官方桥
监听其 `profile_changed` 信号，向 KDE 更新当前档位。KDE 切档则由同一桥交给
TuneD。原调控服务保持 masked/inactive。

配置继续关闭桥的 AC/DC 和 ACPI 自动选档。启动器先核对配置、文件权限、服务和
现有 D-Bus 名称占用，再把当前 Pocket DS 档位写入桥的 base state，防止官方启动
默认值覆盖用户当前选择。接口被其他进程占用、存在陌生 TuneD profile 或配置漂移
时，桥启动失败；Panel 和 TuneD 保持独立可用。

两个标准名称的激活文件安装到 `/usr/local/share/dbus-1/system-services/`，其
优先级高于原包的 `/usr/share` 文件。原软件包文件不被覆盖。自有 Polkit namespace
`org.pocketds.TunedPowerProfiles` 为活动本地会话授权切档和临时 profile hold，
拒绝非活动及远程会话的默认授权；避免与保留的软件包重复注册同一策略 ID。

KDE PowerDevil 6.7.3 在创建性能方案对象时只读取一次全部属性，首次失败后只监听
属性变化，不会因服务后来启动而自动重读。桥须在两个官方 exporter 都完成启动后，
广播官方 getter 返回的初始属性一次，让已运行的 KDE 也能恢复档位显示。此处不
伪造状态、不定时重发，也不重启整个桌面。

## 安装和检查

`scripts/install.sh --apps` 和 `--all` 都安装三档映射及桥。安装前检查官方 Python
模块是否存在，安装后确认标准接口报告保留的档位。`scripts/check.sh` 检查桥服务、
原 PPD 的 mask 和标准接口与 TuneD 当前档位是否一致。

桥的 `--check` 仅用于尚未启动时的只读预检；它不申请 D-Bus 名称或写 base state。
运行中的桥用标准 D-Bus 属性检查。重启会重新采用当前 TuneD 档位；临时 profile
hold 不跨桥进程持久化。SIGHUP 完成官方清理后触发受限重启，显式停止服务不会循环
重启。服务可以读硬件状态，但没有写内核调频节点的权限，实际硬件操作仍由 TuneD
承担。

验证需覆盖 KDE 实际进程的授权路径、Panel 反向同步、三档实际 CPU/GPU/风扇状态、
桥重启保档、旧服务保持停用，以及测试后恢复原档位。服务 active 或 root 写属性
成功，均不能替代这些检查。该修复不涉及 Qt 包、内核、合盖/休眠或显示布局。

## 2026-09-08 日用实机结果

桥已部署。KDE 原本返回空的档位列表和当前档位；收到桥的首次属性广播后，原有
PowerDevil 进程恢复为三档、当前均衡，没有重启电源服务或桌面。

- 经 KDE 实际 PowerDevil 进程走标准接口分别切换三档，全部成功；验证经过其
  本地会话的 Polkit 授权，没有用 root 直接写标准属性代替 KDE。
- 经 Panel 分别切换三档，KDE、两个标准接口和 Panel 当前档位均同步一致。
- 六次切档逐项核对 TuneD profile、三簇 CPU governor/频率上限、GPU governor/
  频率上限和风扇档，并通过 `tuned-adm verify`。
- 性能档下重启桥后仍为性能；桥停止期间用 Panel 选择省电，再启动桥后仍为省电，
  KDE 同步恢复。最后恢复原来的均衡档和 moderate 风扇方案。
- 初版临时验收脚本在 Panel 请求刚被接受时就读取文件，误把异步切换当作同步操作。
  修正为等待 TuneD 完成后，只补测该未完成用例；已通过的六次切档没有重复。保留
  首轮结果和修正后的补测证据，不把验收脚本的错误归因于设备。

11 项隔离启动/状态/授权接线测试通过，并纳入现有测试入口。上述部署和参数
检查来自维护记录；公开源码仅纳入实现、隔离测试和本节结论。本轮没有执行设备
重启或新的物理合盖测试，也没有将电池弹窗的目视效果记为已验收。

## 上游依据

- [TuneD 2.27 官方启动入口](https://github.com/redhat-performance/tuned/blob/v2.27.0/tuned-ppd.py)
- [官方 PPD Controller](https://github.com/redhat-performance/tuned/blob/v2.27.0/tuned/ppd/controller.py)
- [Fedora 采用 TuneD 和兼容接口的说明](https://fedoraproject.org/wiki/Changes/TunedAsTheDefaultPowerProfileManagementDaemon)
- [D-Bus 服务激活目录优先级](https://dbus.freedesktop.org/doc/dbus-specification.html#message-bus-starting-services-scope)
- [KDE PowerDevil 6.7.3 的初始化和属性监听](https://github.com/KDE/powerdevil/blob/v6.7.3/daemon/actions/bundled/powerprofile.cpp)
