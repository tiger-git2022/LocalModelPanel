# 更新日志

每个版本一节，标题写成 `## v版本号`。打标签发布时，GitHub Actions 会把对应的一节作为 Release 说明。

## v1.0.1

- 托盘图标的右键菜单改成亚克力样式：半透明磨砂背景、圆角，随深浅色主题变化（和 Claude 自动审批的托盘菜单一致）。

## v1.0.0

- 首次在 GitHub 发布，由 GitHub Actions 自动打包成 zip（`LocalModelPanel.exe` 单文件夹版）。
- 任务栏右键菜单（跳转列表）新增快捷项：显示面板、启动服务、停止服务、卸载模型。面板没打开时也能用，会在后台直接执行。
- 图标改成浅色版本：浅灰色机器人加深灰描边，深色和浅色任务栏上都看得清；托盘图标的天线小球仍按服务状态变色。
- llama-swap 文件夹、监听地址、API 密钥、模型目录都改成在“设置”里填写，不再写死在程序里。API 密钥留空时自动读取 `config.yaml` 的 `apiKeys`。
- 模型列表改为从 `config.yaml` 读取（显示 `name` 和 `description`）。
- 设置里新增“创建桌面和开始菜单快捷方式”。
- 设置保存到 `%APPDATA%\LocalModelPanel\settings.json`；第一次运行会自动找到 llama-swap 文件夹，并导入旧版的 `panel_prefs.json`。
