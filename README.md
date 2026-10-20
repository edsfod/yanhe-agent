# yanhe-agent：在 Agent 里用延河课堂

yanhe-agent 是一个 Agent 插件，让 Claude Code、Codex 这类 Agent 替你处理北理工延河课堂的录播课。它和桌面应用 [AutoSlides](https://github.com/bit-admin/Yanhekt-AutoSlides) 搭配使用：AutoSlides 负责登录、下载录像、从屏幕录像里提取 PPT，也是你看录播和 PPT 的地方；yanhe-agent 读新版延河课堂（<https://aita.yanhekt.cn>）的课程和字幕，把录音转成更准的字幕嵌进录像，再整理成笔记、回答你关于课程的问题。

本项目最初受 [windlandneko/yanhekit](https://github.com/windlandneko/yanhekit) 启发，现在的代码和文档由 [edsfod](https://github.com/edsfod) 编写和维护。

## 能做什么

- 查最近几节课有没有点名、留了什么作业、提没提考试。
- 一键整理一门课：下载录播和 PPT，转录字幕并改掉课程术语的常见错字，给每节课写笔记，汇总成课程总结。
- 把每节课的 PPT 挑选、去重后合成课件 PDF。
- 按课程笔记写复习提纲和自测题，回答具体的课程问题并给出处的时间点和 PPT。

## 准备

1. 安装 [AutoSlides](https://github.com/bit-admin/Yanhekt-AutoSlides)，打开并登录。yanhe-agent 要用它的命令行，命令行只在 2026-10-18 之后的 main 里，正式版 5.1.0 还没有，所以要按它 README 的「开发者指南」从 main 构建（Windows 用 `npm run make:win` 生成安装包）。在它的用户菜单里看一眼「延河 2.0 账号」是否也显示已登录。之后用 yanhe-agent 时让 AutoSlides 开着。
2. 在你用的 Harness 里装插件：

   - Claude Code：在会话里输入

     ```
     /plugin marketplace add edsfod/yanhe-agent
     /plugin install yanhe-agent@edsfod
     ```

     也可以在终端里运行 `claude plugin install yanhe-agent --marketplace edsfod/yanhe-agent`。

   - Codex：在终端里运行

     ```bash
     codex plugin marketplace add edsfod/yanhe-agent
     codex plugin add yanhe-agent@edsfod
     ```

   - 其他 Harness：运行 `npx skills add edsfod/yanhe-agent -g -s yanhe-agent`，或者把 `skills/yanhe-agent/` 复制到它的技能目录（例如 DeepSeek Harness 的 `~/.dsh/skills`）。这样只装上主技能，用自然语言提要求即可；下面那几条命令只有装成插件才有，因为 `init` 这类名字单独装会和 Harness 自己的命令撞名。

3. 第一次用时，如果电脑上没有 [uv](https://docs.astral.sh/uv/)，Agent 会先问你，同意后运行插件自带的安装脚本：装好 uv，再下载 Python 和所有依赖（包括 ffmpeg）。脚本先比一比官方源和国内镜像哪个快，先用快的，失败再换另一个。不想用 uv 的话，也可以在 Python 3.11 以上的虚拟环境里装好 `skills/yanhe-agent/scripts/yanhe_agent.py` 开头列出的包，用这个环境的 Python 运行脚本。

关于批准命令：在 Claude Code 里，yanhe-agent 的命令由技能自动放行，不用你一条条点。Codex 的默认沙盒既不能联网，也写不了 uv 的缓存目录，所以每条命令都会问你；第一次问的时候选「以后同类命令都允许」那一项，之后就不再问了。也可以在 `~/.codex/config.toml` 里放开这两项，但这样 Codex 里所有命令都能联网：

```toml
[sandbox_workspace_write]
network_access = true
writable_roots = ["D:\\uv\\cache"]  # 改成 `uv cache dir` 打印出的目录
```

## 怎么用

新建一个文件夹当作工作区，在里面和 Agent 对话就行。下次打开同一个工作区接着用；每门课、每节课的材料各放在自己的文件夹里，可以直接打开查看。比如问它：

- 上节课点我名了吗？
- 昨天留的作业是什么，截止到什么时候？
- 明天考试了，这门课还一章没看，帮我列个复习提纲

装成插件后，还可以用命令直接开始常用的流程，后面接课程名和要求，例如 `/yanhe-agent:init 计算机新技术专题 只处理 10月14日`。在 Claude Code 里输入 `/yanhe-agent:` 会列出所有命令，在 Codex 里输入 `$` 或 `@yanhe-agent` 选择。这些命令只在你调用时才运行：

- `/yanhe-agent:init <课程> [课次或要求]`：整理一门课。先看平台自带的快速字幕，去掉放假和没上课的空课；再下载录播、转录、修正常见错字、嵌入字幕；最后挑 PPT、写每节课的笔记和课程总结。以后再运行只处理新的课次。
- `/yanhe-agent:recent [课程]`：只看平台的快速字幕，查最近几节课的点名、作业、考试和通知。
- `/yanhe-agent:ppt <课程> [课次]`：把 AutoSlides 提取的 PPT 挑选、去重，合成课件 PDF。
- `/yanhe-agent:review <课程> [范围]`：根据笔记写复习提纲和自测题。

## 工作方式

- **登录**：命令先用 AutoSlides 里登录好的延河 2.0 账号，只读它保存的登录状态，不复制。它失效时，再用 yanhe-agent 自己保存的登录状态和统一身份认证的会话；都不行时，Agent 会打开一个本地网页，你在网页上输入账号密码、完成短信验证。密码只在这个网页里用，Agent 看不到。yanhe-agent 自己的登录状态存在 `~/.config/yanhe-agent/`，密码默认存在系统钥匙串。
- **下载**：录播由 AutoSlides 从延河课堂下载，它自己会走校园内网。yanhe-agent 按教学班代码和开课时间，把新版延河课堂的每节课对应到延河课堂的同一节课，交给 AutoSlides 下载，然后记下文件放在哪。录像和 PPT 都留在 AutoSlides 的输出文件夹里，在它的课程库里也能直接看。延河课堂上没有的课次会单独告诉你。
- **后台任务**：下载、转录、嵌入字幕在后台运行，Agent 随时能查看进度。会话结束导致任务中断的话，再运行一次就从中断的地方接着做。
- **PPT 和字幕**：PPT 由 AutoSlides 提取、去重并用 AI 筛选，Agent 再看图挑出每页最完整的一版，合成只有图片的 PDF。字幕用 B 站必剪转录，比延河课堂自带的准；课程术语的常见错字会批量改掉，然后作为软字幕嵌进录像。
- **隐私**：转录要把每节课的录音上传到 B 站必剪。第一次转录前 Agent 会先问你，你同意后它才运行 `config --asr bcut` 记下来；不同意的话，也可以只下载录播和 PPT，不要字幕。
- **自检**：`doctor` 命令检查 AutoSlides 有没有开、用的是哪个登录、网络和校园网是否通。

## 免责声明

- **用途**：本工具只供个人学习和研究，不能用于商业或违法用途。
- **课程内容**：录播里有老师和同学的个人信息和著作权。下载和使用时请遵守法律法规和学校的规定，不要传给未经授权的人，也不要发到其他平台上。
- **转录**：必剪转录会把录音上传到哔哩哔哩的服务，请确认你有权这样使用这些录音。转录结果难免有错，作业和通知等重要信息请以原视频为准。

## 许可

以 [MIT 许可](LICENSE)发布。

## 鸣谢

- [windlandneko/yanhekit](https://github.com/windlandneko/yanhekit)：本项目最初的灵感来源
- [bit-admin/AutoSlides](https://github.com/bit-admin/Yanhekt-AutoSlides)
- [Fangwenky/yanhekt-downloader](https://github.com/Fangwenky/yanhekt-downloader)
- [BIT101-dev/BIT-Login](https://github.com/BIT101-dev/BIT-Login)
