# 关羽之歌便携版 v1.0

## 作者：[@依然匹萨吧](https://space.bilibili.com/6297797) 

## 功能介绍

  - **[介绍视频](https://www.bilibili.com/video/BV1PwksB7E6i)**
  - 按下快捷键 Alt+G+Y 播放/停止音频
  - 语音中检测到关键词"释怀"自动播放（需要语音模型）
  - 支持自定义快捷键、音量调节、开机启动等

## 文件说明

  关羽之歌便携版.exe         - 主程序
  guanyu_song.mp3            - 音频文件（必需，请自行放置）
  guanyu_icon.ico            - 程序图标
  vosk-model-small-cn-0.22/  - 语音模型（可选）

## 快速开始

  1. 双击运行`build.bat`

  2. 或者手动输入：

  3. ```bash
     python -m venv .venv
     .venv\Scripts\activate (Windows)
     source .venv/bin/activate (macOS/Linux)
     
     pip install -r requirements.txt
     pip install pyinstaller
     
     pyinstaller --clean build.spec
     ```

  4. 在`dist`目录下找到`.exe`并运行

## 语音识别（可选）

  1. 下载模型：https://alphacephei.com/vosk/models
     选择：vosk-model-small-cn-0.22
  2. 解压到程序目录，保持文件夹名不变
  3. 运行程序，按下快捷键或麦克风检测到关键词"释怀"即可触发

## 快捷键设置

  默认快捷键：Alt + G + Y
  修改方法：点击"修改快捷键"按钮，按下新组合键
  恢复默认：点击"恢复默认"按钮

## 系统托盘

  - 关闭窗口 → 最小化到托盘
  - 双击托盘图标 → 显示窗口
  - 右键托盘图标 → 菜单选项
  - 选择"退出" → 完全退出程序

## 常见问题

  Q: 快捷键没反应？
  A: 检查是否有其他软件占用，或修改为其他组合

  Q: 语音识别不工作？
  A: 检查语音模型是否正确放置，麦克风是否正常

## 系统要求

  - Windows 7/8/10/11 (64位)
  - 内存 2GB 以上
  - 麦克风（语音识别需要）

## 配置文件

  位置：C:\Users\<用户名>\.guanyu_song_config.json
  删除此文件可重置所有设置

## 开源许可证

本项目采用 **[知识共享 署名 - 非商业性使用 - 相同方式共享 4.0 国际许可证 (CC BY-NC-SA 4.0)](LICENSE)** 授权。

### 核心条款说明

1. **允许的行为**：你可以自由复制、修改、分发本项目的代码 / 程序，前提是满足以下条件；
2. **禁止的行为**：严禁将本项目（包括修改后的衍生版本）用于任何商业目的（如出售、付费分发、商业运营等）；
3. 必须遵守：
   - 署名：必须保留原作者信息（[PizzaDark](https://space.bilibili.com/6297797)）；
   - 相同方式共享：若你修改 / 衍生本项目，必须采用与本协议相同的许可证发布。

### 协议完整文本

请查看官方协议全文：https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.zh

### 第三方组件许可证

本软件使用了以下开源组件：

  - PyQt5: GPL-3.0 / Commercial
  - pygame: LGPL-2.1
  - Vosk: Apache-2.0
  - pynput: LGPL-3.0
  - sounddevice: MIT

## 声明

  - 音乐版权归原作者(赵季平)所有，不包含在本许可证范围内
  - 语音模型 vosk-model-small-cn-0.22 遵循 Apache-2.0 许可证

如果觉得好用，欢迎到B站关注支持https://space.bilibili.com/6297797