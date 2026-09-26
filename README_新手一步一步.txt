Meme Scanner 云端版（新手版）
================================

你不需要让自己的电脑一直开着。
这个版本是“一次运行 -> 扫描 -> 保存数据 -> 发 Telegram -> 自动退出”，
由 Railway 每 5 分钟自动运行一次。

重要：
1. 你之前截图里暴露过 Telegram Bot Token。请先去 @BotFather 撤销/重新生成一个新的 Token。
2. 不要把 Token 写进代码，也不要发到群里。
3. 这个程序记录的是“它开始运行以后看到的 ATH”。如果某币在程序上线前已经到过 2000 万美元，
   仅靠 DexScreener 当前公开接口，程序无法凭空恢复那个历史 ATH。
4. “年龄”主要使用 DexScreener 的交易对创建时间作为代理，不等同于项目方真正的代币创建时间。
5. 发现源使用 DexScreener 的 latest token profiles / boosts，因此不是全链所有代币的完整历史数据库。

========================
你只需要做这 4 件事
========================

A. 注册 GitHub
1. 打开 https://github.com/
2. 注册/登录。
3. 新建一个 Repository，例如：meme-scanner-cloud
4. 把这个文件夹里的 5 个文件上传到仓库：
   - scanner.py
   - requirements.txt
   - Dockerfile
   - railway.toml
   - .env.example

B. 注册 Railway
1. 打开 https://railway.com/
2. 用 GitHub 登录。
3. New Project
4. 选择 Deploy from GitHub repo
5. 选择你刚才的 meme-scanner-cloud 仓库。

C. 加数据库
1. 在 Railway 项目页面点击 + New
2. 选择 Database
3. 选择 PostgreSQL
4. 等它部署完成。
Railway 会提供 DATABASE_URL 等数据库变量。

D. 给扫描器添加变量
进入“扫描器服务” -> Variables，添加：
TELEGRAM_BOT_TOKEN = 你重新生成的新 Token
TELEGRAM_CHAT_ID = 你的 Chat ID

然后添加：
DATABASE_URL = ${{Postgres.DATABASE_URL}}

如果你的 PostgreSQL 服务名字不是 Postgres，就点变量引用/Reference，
选择 PostgreSQL 服务里的 DATABASE_URL，不要手打错。

其他条件已经写好：
MAX_AGE_DAYS=30
MIN_ATH_MCAP=20000000
MAX_ATH_RATIO=0.30
MIN_LIQUIDITY=50000
MIN_VOLUME_24H=100000

部署后，Railway 会按照 railway.toml 每 5 分钟运行一次。

========================
怎么判断成功？
========================
进入 Railway -> 扫描器服务 -> Deployments / Logs。
你应该看到类似：

=== Meme Scanner 云端版：本次扫描开始 ===
发现候选 Token：xx
已处理候选 xx/xx
拿到有效交易对：xx
本次命中：0
=== 本次扫描结束，程序退出；Railway 会按计划再次运行 ===

即使你的电脑关机，Railway 仍会运行。

========================
关于你的原始条件
========================
1. ATH 市值至少 2000 万美元
2. 当前市值 <= ATH 的 30%，也就是从记录 ATH 回撤至少 70%
3. 年龄 <= 30 天
4. 流动性 >= 5 万美元
5. 24h 成交量 >= 10 万美元

程序只对第一次命中的 Token 发一次 Telegram。
