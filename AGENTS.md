# 开发约定

## 写文档

- `README.md`、`AGENTS.md` 和笔记模板写中文，其他文档写简洁的英文；一个段落写成一行。
- 文档只写现在这一个版本，改代码时一起改；直接说是什么、怎么做，不写辩解，也不写删了也不影响意思的话。

## 各部分放在哪

- `README.md` 写安装方法和产品上的取舍，本文件写开发时要守的规矩。主技能在 `skills/yanhe-agent/`：`SKILL.md` 写命令和工作流程，`references/files.md` 写文件格式，`references/notes.md` 写笔记模板，`references/setup.md` 写找不到 uv 时怎么装。每次调用技能都会把 `SKILL.md` 整篇读进上下文，所以它只写 Agent 做判断要用的东西；命令输出和报错已经说清楚的不再重复，不常用的说明放进 `references/`。
- `skills/init`、`recent`、`ppt`、`review` 是只由用户调用的命令技能，装成插件 `yanhe-agent` 后叫 `/yanhe-agent:init` 等。它们的描述写中文、正文写英文，正文只指向主技能里的流程；用 `disable-model-invocation` 和 `agents/openai.yaml` 关掉自动调用。
- 命令行只有一个入口 `scripts/yanhe_agent.py`，依赖写在它开头的 PEP 723 块里，新功能加成子命令。其他模块只提供函数：`autoslides.py` 调 AutoSlides 的命令行、读它保存的延河 2.0 登录，`client.py` 调平台接口，`auth.py` 走统一身份认证，`server.py` 是本地登录页，`download.py` 做快速字幕概览和经 AutoSlides 的下载，`jobs.py` 管后台任务，`transcribe.py` 是转录流程，`bcut.py` 调必剪，`doctor.py` 做环境和平台自检，`transcript.py` 管字幕正本 `字幕.srt` 和它的副本、课程纠错表、查询和嵌入，`slides.py` 做截帧、PPT 拼图、相似分组和 PDF，`workspace.py` 管工作区文件、日志和 `Ticker`。

## 实现

- 只从延河 2.0 取课程信息和字幕。录像和 PPT 由 AutoSlides 从延河课堂下载、提取；yanhe-agent 通过它的命令行下指令，按教学班代码和开课时间把两个平台的课次对上，只记下文件路径，不挪它的文件。AutoSlides 由用户从快捷方式打开，脚本不去启动它。
- 脚本只做费事的机械活，比如拼图、找相似页、批量替换，默认值简单，其他用明确的选项指定。工作区的结构和各个产物放在哪由 `SKILL.md` 和 `references/` 定下来，Agent 在这个框架里按用户的要求选择流程、工具和深度。
- 每次改动尽量小，一件事只用一种实现方式，辅助函数等真有多处用到再抽出来；优先用成熟的库，但不引入 Playwright 这样的大依赖。验证只针对改动真正影响的行为。
- 脚本要在各个系统和 Harness 里直接能用：读写文本一律指定 UTF-8，要用户打开的网址打印出来。
- 输出一行一件事，以 `课程/课次: ` 开头，不缩进，课次里的路径写相对课次文件夹的路径；`query` 像 ripgrep 那样先写课次再写匹配的片段。耗时的命令按日志格式打印，每行开头是时间；长的步骤用 `Ticker`，在状态变化或进度每跨过 10% 时打印，其余时间每 10 秒打印一次，两次之间至少隔 0.5 秒，打印的数值在打印那一刻读取。
- 要联网的命令先用 AutoSlides 保存的延河 2.0 登录，再用自己保存的登录状态和统一身份认证的会话；都失效时报错，让 Agent 运行 `login`。AutoSlides 的令牌只读，不另存。`login` 在单独的进程里打开本地登录页并马上返回网址；钥匙串里的密码只在这个页面的进程里使用，因为用它登录可能要验证码或短信；账号密码只经过本地页面和它的后端。
- 把课堂录音交给第三方（现在只有必剪）之前要用户同意，同意记在 `~/.config/yanhe-agent/config.json` 的 `asr`；没记的时候，`sync`、`transcribe` 和其中的转录步骤都报错，并说明该问用户什么，Agent 不能替用户同意。
- 耗时的流程（`sync`）作为后台任务运行：命令马上返回，工作进程的状态、心跳和日志写在工作区的 `.yanhe-agent/jobs/`，`wait` 报告进度；进程被 Harness 结束后，再运行同一条命令就接着做。

## 登录页

- 本地页面只管登录。后端用 Flask，用 SSE 把状态推给页面；`/api/` 下的请求要核对 Host 和启动时生成的 token。
- 页面就是 `scripts/ui/index.html` 一个文件，样式和脚本都写在里面，不用构建；按状态只显示三个步骤之一：账号密码、短信验证码、完成。

## 检查和测试

- 改了登录页，就用一个把 `SignIn` 换掉的本地服务，在浏览器里把三个步骤走一遍，看页面和按钮是否正常。
- Python 代码提交前运行 `uvx ruff format skills/yanhe-agent/scripts tests` 和 `uvx ruff check skills/yanhe-agent/scripts tests`，风格按 `ruff.toml`。
- 测试命令是 `uv run --no-project --with-requirements skills/yanhe-agent/scripts/yanhe_agent.py --with pytest pytest tests`，依赖取自脚本的 PEP 723 块；GitHub Actions 在 Windows、macOS 和 Linux 上跑同样的检查。测试只用本地的数据：录像用随附的 ffmpeg 合成，AutoSlides 由 `tests/fake_autoslides.py` 扮演，平台接口的回应取自 `tests/fixtures/`。这些样例由 `tests/record_fixtures.py` 用保存的登录状态从平台录下来，再去掉个人信息：人名、手机号、地址、链接路径、课堂上说的话，以及能看出本人选了哪些课的课程名和课程代码。仓库是公开的，提交前每个样例都要看一遍。平台接口有变化时重新录样例，并让 `doctor` 的平台检查覆盖新读到的字段。
