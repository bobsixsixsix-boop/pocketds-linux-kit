# 安装、升级与撤回

本指南面向源码预览。当前安装器用于**已有匹配硬件支持的 Pocket DS Linux 环境**，
尚无从出厂机器开始的分区、刷机或一键建好桌面的流程。请先确认 [支持范围](SUPPORT.md)。

## 最小离线检查

先在源码根目录运行下面六组检查。需要 Python 3.10+、Git 与 Bash；不需要 Pocket DS、
个人素材、语音访问码、sudo 或外部服务登录。它们使用临时文件和模拟系统接口，
不执行正式安装器，不修改主机的服务或输入设备。

```sh
python3 tests/pds020-install-asset-preflight.py
python3 tests/asr-api-provision.py
python3 tests/install-privilege-order.py
python3 tests/install-service-upgrade.py
python3 tests/game-input-stack-installer-mock.py
python3 tests/asr-api-install-transaction.py
```

这些检查分别覆盖素材清单、用户 API 配置与无配置状态、依赖检查顺序、服务升级、输入栈事务及键盘事务。
素材测试会创建真实的临时 Git 仓库再 clone，验证空 `assets/` 目录未被带走时仍可通过
`asset-free` 预检。权限、服务启停和设备状态使用模拟接口，**通过不表示另一台设备已完成
全新安装**。

## 安装前的基线

- 设备为 AYANEO Pocket DS，运行 Fedora 44/aarch64 和 Plasma Wayland。
- 定制内核、固件、双屏、触摸、音频、InputPlumber 与 XWayland 已正常工作。
  本仓库不替代这些底层包，也不安装普通 Fedora 到设备。
- 当前事务要求桌面账户和私有组名均为 `pocketds`，家目录为 `/home/pocketds`。
  这是代码中的实际限制；其他用户名不能直接套用。
- 下屏已有合适的 Plasma 布局。主安装器更新 Panel 小部件文件，不创建桌面 containment、
  摆放小部件或复制维护者的整套桌面状态。
- 已准备 [外部依赖](DEPENDENCIES.md)。安装器检查录音工具、Python 的 `dbus`、`evdev`、
  `gi`、`pyatspi` 等，即使本次不使用语音也需要满足当前检查。
- 已退出游戏、模拟器和手柄测试，保存工作，并备份系统配置与匹配的启动恢复材料。
  手柄震动所需的固定 InputPlumber 扩展是外部产物；缺失时安装器会报告功能不完整。

不要直接批量升级依赖或删除设备支持元包。先按 [更新策略](UPDATE-POLICY.md) 预览包事务。

## 构建与源码预检

公开候选不附带预编译的 gamescope 帧率观察器。请在目标兼容的 Linux/aarch64 环境中，
准备 `cc`、`pkg-config`、`wayland-scanner` 及 `wayland-client`、`xcb` 开发文件，然后构建：

```sh
./scripts/build-gamescope-observer.sh components/game-runtime/pocketds-gamescope-observer
make lint
make test
```

观察器必须放在上述安装器读取的位置；脚本不带参数时默认写入 `build/`，单用默认路径
还不能满足安装事务。生成文件已被 Git 忽略。macOS 的系统头文件与 Linux 不同，不能将
完整 C/C++ 检查失败当作目标设备构建结果。

在公开的 `asset-free` 源码包中，运行只读素材预检：

```sh
python3 scripts/pds020-install-asset-preflight.py --profile asset-free
```

无需自己创建空 `assets/` 目录。预检仍要求 `components/assets/ASSETS.json` 是精确的空素材
清单；个人开发树和非空素材树不能靠增加 `--asset-free` 绕过它。此预检只验证素材，不验证
硬件、运行时或全部安装前提。

## 安装应用层

以已登录 Plasma 的桌面用户，在源码目录执行：

```sh
sudo -v
./scripts/install.sh --apps --asset-free
```

`sudo -v` 建立本次管理员认证；安装器随后使用非交互检查。不要用 `sudo` 启动整个脚本。
当前 `make install` 与 `make install-all` 未传素材参数，仍默认个人素材；公开候选请使用
上面的显式命令。

`--apps` 的实际改动包括：

- 部署 Panel、键盘、触摸板、遥测、亮度和音频等组件，更新并激活相关用户服务。
- 写入受参数限制的系统助手、sudoers/polkit 策略、输入配置和游戏启动器。
  输入事务协调手柄测试与模式监听器；不会自动重启 InputPlumber 主守护进程。
- 安装 TuneD 档位，停止并屏蔽 `power-profiles-daemon` 服务，启用 TuneD。
- 写入键盘布局规则、部分桌面/锁屏和输入法配置；现有 `powerdevilrc` 与有效语音私有配置
  保留。`--asset-free` 不安装或删除个人壁纸。
- 放置双系统切换助手，包括 `/boot/PocketDS-Switch-to-Linux.sh`；安装本身不执行切换、
  刷写内核或重启机器。

因此这条命令包含系统改动。`--all --asset-free` 还会安装 UCM、udev、电源、网络和系统服务
配置，应逐项检查 `scripts/install.sh` 的 `install_system` 后再决定是否适用。
主安装器不自动准备或启用经过验收的深度睡眠环境。

安装结束先检查全部输出，再运行：

```sh
make check
systemctl --user --no-pager status pocketds-keyboard.service pocketds-touchpad.service
```

`make check` 包含源码检查和当前设备的运行检查；要阅读其中的 `WARN`，不能只看进程退出码。
随后实际确认 Panel、双屏触摸、键盘与触摸板切换、手柄模式及声音。首次应用输入法环境配置
需要正常重新登录；`--all` 还涉及需正常重启生效的规则。睡眠验收应按专门流程进行。

## 升级已有安装

1. 保留当前源码提交号、用户配置和上次安装输出；退出游戏并保存工作。
2. 获取要使用的新版源码，检查变更及 [已知问题](KNOWN-ISSUES.md)。不要混用不同版本的
   主程序、共享 Python 模块和系统助手。
3. 在新版本目录重新构建观察器，运行上述离线/构建检查。满足基线后再次执行
   `./scripts/install.sh --apps --asset-free`；若认证已过期先运行 `sudo -v`。
4. 阅读安装输出和事务位置，重新运行 `make check` 并复测改动涉及的功能。

安装器会备份发生变化的已有文件，并让更新过的服务加载新代码。它不是包管理器，
部分配置（如 `kwinrulesrc`、Fcitx 配置）会按仓库版本覆盖；升级前请保存自己的修改。
保留旧源码目录有助于比对，但仅 `git checkout` 或 `git revert` 不会撤销已安装的文件或服务状态。

## 可选中文、语音与额度显示

中文与 Rime 的独立联网安装入口是 `make install-chinese-input`，会取得锁定的上游数据。
完成后正常重新登录，让 Plasma、GTK 和 Qt 继承一致的输入法环境。

语音为可选功能，主安装器只部署程序，不写入或恢复任何私人语音配置。
需要使用时，在终端运行配置工具，依次隐藏输入自己的完整 HTTPS 转写地址、模型名与 API Key：

```sh
python3 scripts/pocketds-asr-api-provision.py provision
python3 scripts/pocketds-asr-api-provision.py check
```

接口须兼容 OpenAI 音频转写格式；地址与模型均无默认值，项目不提供 API Key。
`check` 仅检查本地配置，未配置时输出 `not_configured`，不发送录音或验证账户额度。
完整字段、安全存储、移除配置及兼容范围见 [语音 API 配置](VOICE-API.md)。
Codex 额度需要自己安装、登录 Codex；无登录不影响普通 Panel 控制。其他程序和模型见
[外部依赖](DEPENDENCIES.md)。

## 失败恢复与卸载

**目前没有完整的通用卸载器，也没有一条命令能把整机恢复到安装前。**

| 记录 | 位置与范围 |
|---|---|
| 用户文件备份 | `~/.local/state/pocketds-linux-kit/backups/<时间>/`，保留已有文件的安装前版本 |
| 系统文件备份 | `/var/lib/pocketds-linux-kit/backups/<时间>/`；`--apps` 同样可能产生，只是不在末尾单独打印 |
| 输入栈事务 | `~/.local/state/pocketds-linux-kit/game-input-transactions/`，以实际事务输出为准 |
| 键盘/语音运行时事务 | `~/.local/state/pocketds-linux-kit/asr-api-transactions/`，有各自 stage/apply/verify/rollback 记录 |

这些记录不是整机快照；新建文件、软件包状态、服务屏蔽/启用和跨组件改动需要分别处理。
窄事务的回退只覆盖自己的清单，不能视为撤销整次 `--apps`。完整撤回需要按本次安装记录
核对新增文件、恢复已有文件及服务原状态，并验证恢复后的同一代组件。

遇到失败先保留输出和事务目录，定位失败阶段。完成回退前不要删除备份或整个状态目录。
若只需暂时停止键盘与触摸板排查问题，可使用系统的用户服务管理；这不是卸载，后续快捷键
或重新登录仍可能启动组件。

内核和启动布局的恢复必须使用匹配代际与内容摘要的材料。历史三别名实验不能套用到当前
单一 `/boot/boot/Image` 布局。没有独立备份或匹配恢复路径时，先进行源码阅读与离线测试。
