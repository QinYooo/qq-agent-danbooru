# QQ Agent Danbooru Toolkit

QQ Agent 的本地收图、筛选归档和图库点图插件，附带可选的本地图库管理网页。

- `danbooru-archive`：自动接收群图片及合并转发中的图片，用本地模型筛选并按角色、SFW/NSFW 保存。
- `character-gallery`：按中文角色名或画面标签检索已有图库，随机发送，支持组合条件、数据库统计和近期图片降权。
- 本地管理网页：预览、改标签和分类、回收站恢复、批量导入，与插件使用同一份数据库。

不使用 QQ 的独立版本见 [danbooru-local-manager](https://github.com/QinYooo/danbooru-local-manager)。

## 安装

需要已安装的 QQ Agent，以及可用的 SnowLuma。当前接口在 QQ Agent `0.4.8.5` / SnowLuma `1.14.22` 上开发；其他宿主版本可能需要适配。

Windows PowerShell 一行安装：

```powershell
irm https://raw.githubusercontent.com/QinYooo/qq-agent-danbooru/v1.0.0/bootstrap.ps1 | iex
```

或下载 [Release ZIP](https://github.com/QinYooo/qq-agent-danbooru/releases)，解压后双击 `install.cmd`。

安装器默认查找 `%LOCALAPPDATA%\Programs\QQ Agent`，创建 Python 3.11 环境，安装依赖、下载校验模型，并将两个模块放入宿主 `plugins/`。环境和模型位于 `%LOCALAPPDATA%\DanbooruToolkit`，不会放进受热重载监听的插件目录。

```powershell
# QQ Agent 安装在其他位置，或复用已有图库
.\install.ps1 -QQAgentDir 'D:\Apps\QQ Agent' -LibraryPath 'D:\MyLibrary'

# 不自动打开管理网页
.\install.ps1 -NoLaunch
```

安装后打开 QQ Agent 的「插件」页，对两个插件执行「信任并重试」并启用。插件需要文件和子进程访问权，因此使用宿主原有的信任机制；安装器不自动修改信任列表。更新安装前会备份原插件目录。

自动收图需要 NVIDIA 显卡及驱动。安装器使用 CUDA 12.6 的 PyTorch，模型权重约 1.95 GB。无显卡时可以使用 `-BrowseOnly` 只配置图库浏览和整理，自动收图保持不可用。首次下载请为 Python、PyTorch、模型和缓存预留约 8 GB。

## 收图与归档

开启归档插件后，自动处理机器人所在群的新图片。默认覆盖合并消息和两层嵌套转发；所有群模式保留群黑名单及成员屏蔽规则，不扩大宿主的发言白名单。

默认短边至少 768，总像素至少 786432。完整插画判断、截图和梗图过滤、人物判断、角色置信度、分级及全库去重都通过后才收录。角色阈值为 0.85，每张图片只按最高分角色保存一份；未识别角色的合格人物插画进入 `_unknown_character`。普通标签也存入数据库。

近似去重使用 pHash、dHash、颜色与结构联合比对，可将距离设为 0 关闭近似去重。分级使用明确内容标签与模型分级共同判定。误识别和漏检仍可能发生，网页用于人工复核。

## 点图与统计

在已允许发言的群里实际 @机器人：

```text
来几张莉音
来5张穿着丝袜的莉音
来几张莉波或者爱弥斯的图
来几张白发黑丝的图，不要眼镜
随机来几张图

查看数据库
查看数据库 角色 2
查看数据库 搜索 丝袜
查看数据库 -全部
查看数据库 标签 -全部
```

默认 3 张，最多 9 张，SFW/NSFW 一起随机抽取。已发图片在本群默认 24 小时降权，角色、标签和全库随机共用记录；其他群独立。候选不足时仍可抽到近期图片。

明确角色和标签优先本地检索。模糊或复合描述由 QQ Agent 当前配置的模型规划数据库中的真实标签条件；只提供标签、别名和计数，不发送原图。因此模糊点图可能产生宿主原有模型费用，图片分类本身在本机运行。

图片默认逐张发送，插件额外间隔 0.5 秒；宿主队列与上传另有耗时。缺少 QQ 发送回执时，可按设置生成左上角 5×5 RGB 减 10 的无损副本，仅补发一次。回执缺失不证明消息未送达，补发有重复的可能；可以关闭该选项。失败图会暂避并记录结果，不无限重试。所有 `-全部` 统计使用合并转发。

## 网页管理与数据

双击 `start.cmd`，或使用安装器创建的桌面入口，打开 `http://127.0.0.1:8766`。网页可独立启动，`stop.cmd` 停止网页服务，不关闭 QQ Agent。

图库默认放在用户 `Pictures\Danbooru`。图片位于 `SFW/<角色>/` 和 `NSFW/<角色>/`，元数据集中在 `_state/archive.sqlite3`，没有逐图 JSON/TXT。网页修改会更新文件路径及数据库索引；移出图库先进入可恢复回收站。

Python、模型、SQLite、图片和本机配置不随仓库发布。`config.json` 由安装器生成；不要将含有 QQ 账号、消息来源或附件 URL 的运行数据提交到仓库。

## 开发与测试

```bash
python -m pip install -r requirements-web.txt
python -B -m unittest discover -s tests -p 'test_*.py' -v
node --test --test-timeout=10000 tests/*.test.mjs
```

Node 测试使用 Node 22+，运行时使用宿主提供的 Node 与 `ws`。测试的 QQ 发送端为模拟对象，不向真实群发消息。目录 `engine/` 是独立归档流水线，`plugins/` 是宿主模块，`web/` 是本地管理网页。

## 许可证

项目代码使用 MIT。第三方模型、中文译名和名称数据的说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。模型在安装时按固定版本下载并检查 SHA-256，未确认再分发许可的上游角色语料不放进发布包。
