# 使用自己的语音转写 API

公开版不提供默认语音服务、API Key、模型名或私人授权恢复入口。
普通键盘无需配置语音。需要联网转写时，使用者自行选择服务并提供完整 HTTPS 地址、
API Key 和模型；每次语音操作只向该地址发送本次录音。

## 配置

在源码目录运行：

```sh
python3 scripts/pocketds-asr-api-provision.py provision
python3 scripts/pocketds-asr-api-provision.py check
```

配置工具依次提示输入以下三项，输入过程隐藏内容：

| 项目 | 应填写的内容 |
|---|---|
| 转写地址 | 服务商给出的完整 HTTPS 转写地址，例如 `https://api.example.invalid/v1/audio/transcriptions`；示例域名不可调用，不是默认值 |
| 模型 | 该服务实际支持的音频转写模型名，没有内置默认值 |
| API Key | 使用者自己的密钥，不要粘贴到命令行参数、源码、问题报告或日志 |

需要的是音频转写接口，而不是聊天补全接口。地址不能包含用户名、密码、查询参数或片段。
当前仅接受通过系统证书校验的 HTTPS，不支持明文 HTTP。工具不会自动补路径。

`provision` 将三个字段作为一份完整配置写入：
`~/.config/pocketds-keyboard/asr-api/config.json`。目录权限为 0700，配置与锁文件权限为
0600；运行时拒绝符号链接、硬链接、错误所有者及过宽权限。配置更新采用加锁和原子替换。
文件是本地明文私有配置，不属于源码或安装备份的分发内容。

`check` 只检查本地配置，不发送音频，也不验证账户、模型或额度。未配置时输出
`not_configured`。完成配置后，下次语音操作会读取新配置，不需要把密钥写入服务文件。

安装后的独立命令为 `~/.local/bin/pocketds-asr-api-provision`。重新运行 `provision` 可更换
配置；移除这套 API 配置使用：

```sh
python3 scripts/pocketds-asr-api-provision.py remove
```

移除操作只处理这套配置；已有请求持有共享锁时会等待请求完成。程序不会读取、迁移或
删除旧私人服务的配置。主安装器只部署程序，未配置语音不会阻断安装或普通键盘输入。

## 接口与隐私边界

接口遵循 [OpenAI 音频转写格式](https://developers.openai.com/api/reference/python/resources/audio/subresources/transcriptions/methods/create)：
POST multipart/form-data，包含 `file`（固定文件名 `audio.wav`）、`model` 与
`response_format=json`，使用 `Authorization: Bearer` 认证。成功响应需包含字符串 `text`；
允许额外的用量等字段，但只读取转写文本。当前不处理流式输出或说话人分段。

录音为完整、私有的 16 kHz 单声道 PCM WAV，当前键盘最长录音 15 秒。请求不发送设备 ID、
本地文件路径或账户绑定信息。密钥与音频通过私有管道交给受限进程，不放入参数或环境变量；
不跟随重定向，不展示服务端错误正文。取消会终止并回收请求进程，并阻止过期结果粘贴。
服务已接收的音频无法通过本地取消撤回，其保留政策由使用者选择的服务决定。

总请求时限为 75 秒；网络失败或 HTTP 502/503 最多重试两次，均使用同一配置快照和同一地址。
HTTP 401/403/429 等拒绝不自动重试。成功但无文本时提示未识别到语音。接口未配置或失败时，
只有使用者已自行安装并通过检查的本地 SenseVoice/Whisper 可参与回退，不切换到其他云服务。
录音与转写的本地临时产物按语音会话生命周期清理；结果仍受原输入目标与取消状态检查。

## 验证范围

客户端、配置存储、取消、超时与安装事务使用合成音频和模拟服务进行离线回归。
没有使用维护者的私人服务、API Key 或真实录音验证这一通用接口；真实服务兼容性、费用、
延迟及 Pocket DS 上的端到端体验需要使用者配置后验证。旧私有后端的实机记录不能替代这些检查。
