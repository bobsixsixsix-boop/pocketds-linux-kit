# 外部依赖

## Fedora 软件包

既有 Pocket DS 适配环境使用的主要开发与运行包包括：

```text
git gcc-c++ make diffutils python3 python3-evdev python3-dbus python3-gobject
python3-pyatspi at-spi2-core kf6-kpackage jq tuned plasma-milou inputplumber
libgpiod-utils libseccomp pipewire-utils pulseaudio-utils alsa-utils cracklib-dicts
```

这不是在普通 Fedora 上补齐整个设备环境的包清单。安装器还使用基线已有的
`sudo`、`systemctl`、`rpm`、`kdialog`、`kwriteconfig6` 等工具；实际安装前提见
[INSTALL.md](INSTALL.md)。包安装或升级应先按 [UPDATE-POLICY.md](UPDATE-POLICY.md)
检查事务，不能直接拿清单进行无条件全量升级。

Panel 直接调用 TuneD 的系统 D-Bus 并安装三套 Pocket DS profile，因此这里需要的是
`tuned`。本机的 `pocketds-base` 明确依赖 `power-profiles-daemon`；不要安装会与它冲突的
`tuned-ppd`，也不要为了 `tuned-ppd` 删除设备支持元包。保留
`power-profiles-daemon` 软件包但 mask 其 systemd 单元：它由 `graphical.target`
和系统 D-Bus 激活，只做 disable 仍会在登录 KDE 时与 TuneD 竞速并将其停止。
TuneD 必须是 Panel 性能档唯一的运行时权威。

`plasma-milou` 提供 KWin 概览和 Plasma 搜索使用的 `org.kde.milou` QML 模块。
Pocket DS 的图形栈保护策略会排除通配的 `plasma*` 更新；安装前必须先预览事务，并且
只在事务为“新增 `plasma-milou`、零升级、零删除”时临时清空该次命令的 exclude，不能
删除或放宽持久保护配置。

`cracklib-dicts` 提供 `passwd` 经 `pam_pwquality` 检查新密码时需要的字典。它必须是
显式依赖，不能依赖最小镜像或关闭 weak dependencies 的安装流程自动补齐；只有
`cracklib` 和 `libpwquality` 库仍会在缺少字典时导致改密失败。`scripts/check.sh`
用固定的非秘密字符串只读验证字典能够加载，不读取或修改用户密码。

`libseccomp.so.2` 是 语音 API disposable HTTPS worker 的运行时安全依赖；缺失时
worker 会在读取凭据 pipe 之前 fail closed。最终 RPM/composition 必须把 `libseccomp`
写成显式依赖，不能只依赖开发镜像碰巧已经安装。

`pipewire-utils` 提供 PDS-018 物理麦克风事务使用的固定 `/usr/bin/pw-dump` 图审计器；
设备已安装 `1.6.8-1.fc44.aarch64`，事务仍必须在任何音频端点变更前核验包与文件身份。

键盘录音另需 `pulseaudio-utils` 提供的 `pactl`、`parecord`，以及 `alsa-utils`
提供的 `arecord`。前者选择系统麦克风并录制蓝牙／USB 输入，后者保留内置麦克风的
专用通路。主安装器在写入前检查三条命令，自检也分别报告缺失工具；仅有 PipeWire
服务或键盘服务处于 active 并不证明录音依赖齐全。

建议另装 `ripgrep` 方便本机开发。

## 安装所需的本地构建产物

公开源码不分发预编译的 `pocketds-gamescope-observer`。它是输入栈事务中的必需文件，
缺失、文件权限不符或不是 ELF 文件会阻断事务；即使本次不启动 gamescope，也不能跳过。

在兼容目标设备的 Linux/aarch64 环境准备 `cc`、`pkg-config`、`wayland-scanner`，以及
`pkg-config` 可找到的 `wayland-client` 和 `xcb` 开发文件后运行：

```sh
./scripts/build-gamescope-observer.sh components/game-runtime/pocketds-gamescope-observer
```

安装器从这个路径读取产物。构建脚本默认输出到 `build/`，因此安装前需要显式指定路径。
构建只使用仓库源码和本地开发依赖，不下载预编译观察器。

## 中文输入

简体中文/Fcitx5 的实机安装使用 `scripts/install-chinese-input.sh`，固定为 Fedora
44 aarch64 的 `fcitx5`、`fcitx5-autostart`、`fcitx5-gtk`、`fcitx5-rime`、
`librime-lua`、`glibc-langpack-zh` 和 Noto CJK Sans/Mono 字体。安装器显式关闭
weak dependencies，避免 `langpacks-zh_CN` 元包额外拉入 IBus；也不安装与设备锁定
Qt 6.11.1 私有 ABI 不兼容的 `fcitx5-qt6`。Qt/Plasma/内核/固件保护集合在事务前后
必须逐项一致。原始最小镜像把已安装 RPM 的大部分 `zh_CN` payload 标记为未安装；
`scripts/restore-chinese-catalogs.py` 从 Fedora Koji 下载 rpmdb 所指向的精确 NEVRA，
先核验 Fedora RPM 签名、包身份和 payload，再只安装当前缺失的中文 catalog。它不
重新安装或升级 RPM，也不覆盖既有翻译文件。

## 不随仓库分发

- Chromium ARM64 V4L2 运行时：`/opt/pocketds-chromium-v4l2-151.0.7922.137`。
  仓库保存 fail-closed hash lock、官方 Arch Linux ARM build key 证书和五个原包
  的固定回执/签名；原包本体不进 Git。取得锁中精确归档后可用
  `make verify-chromium-provenance` 离线复核。该证据证明当前提取层来自签名包，
  不自动授予再分发权，也不替代最终镜像许可证/SBOM/源码提供义务
- Steam ARM64/Switchdeck 与 Proton：由 `pocketds-steam` 软件包和 Steam 用户目录管理
- ES-DE AppImage：`~/Applications/ES-DE_aarch64.AppImage`
- RetroArch 二进制、核心、系统文件和 assets
- ROM、BIOS、存档和预览媒体
- 雾凇拼音完整上游数据；仓库只保存本机覆盖配置与
  `components/locale/rime-ice.lock.json` 来源锁。安装时从官方仓库取得锁定提交，
  校验 Git 对象后再复制到 Fcitx5 用户目录
- 用户自行提供 OpenAI 兼容转写 API 的完整 HTTPS 地址、API Key 与模型名。
  用 `python3 scripts/pocketds-asr-api-provision.py provision` 隐藏输入配置；不自动读取
  旧服务配置、环境密钥或个人预设，不发送设备 ID。配置文件为 0600，目录为 0700。
  普通键盘不依赖 API 配置；详见 [VOICE-API.md](VOICE-API.md)。
- Whisper CLI 与中文模型（语音识别回滚后端）
- sherpa-onnx `sherpa-onnx-offline` aarch64 binary、同版 `libonnxruntime.so`、
  SenseVoice int8 模型和 `tokens.txt`。Git 只保存官方 URL/大小/SHA-256/归档成员锁、
  完整许可证快照和离线 prepare 工具；两个官方归档已在开发机与 Fedora 44/aarch64
  隔离环境通过验证，但不进 Git、尚未部署到设备，真实麦克风质量仍需验收
- WireGuard 配置以及任何登录令牌、Cookie、私钥

## 缺失依赖时的实际行为

| 缺失项 | 当前行为 |
|---|---|
| 主安装器要求的命令、Fedora 包或 Python 模块 | 报错并停止；录音工具当前也是安装前置条件，不因未配置语音而跳过 |
| 安装事务清单中的源码或本地观察器产物 | 校验失败，不能当作可选外部应用继续安装 |
| 固定的 InputPlumber 震动 sidecar 或配套文件 | 主安装器警告并在末尾标记 `INCOMPLETE`；其他步骤可继续，但不能据此宣称震动功能已就绪 |
| Rime Ice 数据、Chromium V4L2、Steam 用户运行时 | 主安装器警告，跳过对应数据覆盖或仅安装包装器，不代表应用可用 |
| 个人语音预设/访问码 | 普通键盘仍可安装；语音无可用后端时显示未配置或失败 |
| Codex 登录、语音模型、游戏/模拟器内容 | 由使用者自行准备，影响对应可选功能；不能用基础安装成功代替其验收 |

主安装器不会自动下载这些外部运行时。中文安装器等明确的独立入口会联网取得锁定来源；
运行前应阅读该入口的说明。已有外部内容保留在设备原位置，源码仓库不复制账户或游戏目录。
