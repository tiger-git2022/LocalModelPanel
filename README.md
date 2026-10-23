# 本地模型管理（LocalModelPanel）

[![最新版本](https://img.shields.io/github/v/release/tiger-git2022/LocalModelPanel?label=%E6%9C%80%E6%96%B0%E7%89%88%E6%9C%AC)](https://github.com/tiger-git2022/LocalModelPanel/releases/latest)
[![编译发布](https://github.com/tiger-git2022/LocalModelPanel/actions/workflows/release.yml/badge.svg)](https://github.com/tiger-git2022/LocalModelPanel/actions/workflows/release.yml)
![平台](https://img.shields.io/badge/%E5%B9%B3%E5%8F%B0-Windows%2010%20%2F%2011-blue)

给 [llama-swap](https://github.com/mostlygeek/llama-swap) 做的 Windows 图形控制面板，Win11 Fluent 风格。用来在本机跑翻译模型（比如腾讯混元 Hy-MT2）、通用模型和 Whisper 语音识别，对外提供一个 OpenAI 兼容接口，给翻译软件、手机或其他程序调用。

```
翻译软件 / 手机 ──HTTP（OpenAI 兼容接口）──► llama-swap ──按需启动──► llama-server（翻译 / 通用模型）
                                                 ▲                   └► whisper-server（语音识别）
                                  本面板：启动/停止服务、加载/卸载模型、看显存
```

## 功能

- 一键启动、停止 llama-swap 服务；显示运行状态、当前加载的模型和显存占用（NVIDIA 显卡）。
- 模型卡片：从 `config.yaml` 读出所有模型，可以手动加载或卸载。
- 托盘图标：点 × 缩小到托盘，服务继续运行。机器人天线小球的颜色表示状态：绿色运行中，黄色启动中，红色已停止。
- **任务栏右键菜单**：显示面板、启动服务、停止服务、卸载模型。把面板固定到任务栏后，面板没打开也能用。
- 开机自动启动服务（只在后台启动，不打开面板）。
- 浅色 / 深色 / 跟随系统主题；云母、亚克力背景，亚克力遮罩可调。

## 下载

到 [Releases](https://github.com/tiger-git2022/LocalModelPanel/releases/latest) 下载最新的 `LocalModelPanel-版本号.zip`，解压到任意位置，双击 `LocalModelPanel\LocalModelPanel.exe`。

- 整个 `LocalModelPanel` 文件夹约 200 多 MB，exe 要和旁边的 `_internal` 文件夹放在一起，不能单独拷出来。
- 只支持 Windows 10 / 11（背景特效需要 Windows 11）。首次运行如果 SmartScreen 拦截，点“更多信息 → 仍要运行”。
- 推荐放在 llama-swap 文件夹里，比如 `D:\llama-swap\panel\`，这样第一次运行会自动找到上一级的 `llama-swap.exe`。

各版本改了什么见 [更新日志](CHANGELOG.md)。

## 需要自己准备的

面板本身不带模型和推理程序，需要先准备好：

1. **llama-swap**：从 [llama-swap Releases](https://github.com/mostlygeek/llama-swap/releases) 下载 Windows 版，解压出 `llama-swap.exe`。
2. **llama.cpp**：从 [llama.cpp Releases](https://github.com/ggml-org/llama.cpp/releases) 下载带 CUDA 的 Windows 版，里面有 `llama-server.exe`。
3. **模型文件**（`.gguf`），比如 [Hy-MT2-7B](https://huggingface.co/tencent) 的量化版。
4. 可选：**whisper.cpp** 的 `whisper-server.exe` 和语音识别模型，用来做语音转文字。
5. **`config.yaml`**：参考本仓库的 [`config.example.yaml`](config.example.yaml)，改好路径后放到 `llama-swap.exe` 旁边。

## 第一次使用

1. 打开面板，进入 **设置**：
   - **llama-swap 文件夹**：选 `llama-swap.exe` 所在的文件夹（自动找到了就不用改）。
   - **监听地址**：默认 `127.0.0.1:8080`，只有本机能访问。想让手机或别的电脑访问，填本机对应网卡的 IP，比如 Tailscale 的 `100.x.x.x:8080`，这时一定要在 `config.yaml` 里设 `apiKeys`。
   - **API 密钥**：留空会自动读取 `config.yaml` 里 `apiKeys` 的第一个。
   - 点 **桌面和开始菜单快捷方式 → 创建**，然后把面板固定到任务栏，右键就有快捷项。
2. 回到 **主页**，点 **启动服务**。状态变成绿色“运行中”后，就可以用 `http://监听地址/v1` 调用了，模型会在第一次调用时自动加载。

设置保存在 `%APPDATA%\LocalModelPanel\settings.json`，面板出错的记录在同一文件夹的 `panel.log`，llama-swap 自己的日志在 llama-swap 文件夹的 `llama-swap.log`。

## 命令行参数

任务栏右键菜单的快捷项就是用这些参数启动 exe 的，也可以自己在脚本里用：

| 参数 | 作用 |
| --- | --- |
| （无） / `--show` | 打开面板 |
| `--start` | 启动服务 |
| `--stop` | 停止服务（连同已加载的模型进程） |
| `--unload` | 卸载所有已加载的模型 |
| `--autostart` | 后台启动服务后退出，开机自启用；网络没就绪时会重试约 4 分钟 |

面板已经打开时，这些操作会交给正在运行的面板执行；没打开时直接在后台执行。

## 从源码运行和打包

需要 Python 3.11 或更新版本：

```bash
python -m pip install -r requirements.txt
pythonw swap_panel.pyw
```

本机打包：再装 `pyinstaller`，然后双击 `build.bat`，生成 `dist\LocalModelPanel\LocalModelPanel.exe`。

发布新版本：改 `swap_panel.pyw` 里的 `APP_VERSION`，在 `CHANGELOG.md` 加一节 `## v版本号`，然后合并（或推送）到 `main`。GitHub Actions 发现这个版本号还没有 `v版本号` 标签，就会自动打包、发布到 Releases 并打上标签；版本号没变时只打包检查。也可以自己推标签 `v版本号` 来发布。

## 许可

本仓库的源码按 [MIT](LICENSE) 许可。Releases 里打包好的 exe 包含 [PySide6](https://doc.qt.io/qtforpython-6/)（LGPLv3）和 [PySide6-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets)（GPLv3），整个打包版按 GPLv3 分发。
