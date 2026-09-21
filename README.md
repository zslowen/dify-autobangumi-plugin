# AutoBangumi 追番订阅工具插件

> [!CAUTION]
> **个人学习与实验项目，非官方产品。**
>
> - 本项目由个人基于真实追番需求开发，用于学习和验证 Dify Tool Plugin、Agent 工具设计、外部系统集成、人工审批与写后验证等能力。
> - 本项目的代码、测试和文档有较大部分在 **OpenAI Codex** 辅助下生成或修改；需求定义、产品取舍、真实环境联调、Bad Case 分析与最终验收由项目维护者完成。AI 生成内容可能存在缺陷，使用者应自行审查、测试并评估风险。本声明不代表 OpenAI 对本项目提供官方认可、担保或支持。
> - 本项目与 AutoBangumi、Dify、蜜柑 Project 及其开发者或运营方不存在官方隶属、授权或背书关系。
> - 当前仅针对 **AutoBangumi 3.2.8** 开发和验证，不保证兼容其他版本、部署方式或未来接口变更。
> - 当前主要通过 **Dify Cloud 远程调试 + 本地插件进程** 运行，尚未完成生产化部署、高可用、长期运行和多用户隔离设计。
> - `subscribe_ab_rss` 是受控写入工具，调用 AutoBangumi 后可能创建 RSS、番剧规则并触发真实下载。请先理解预览内容，在测试环境或可控环境中验证，并自行承担使用风险。
> - 本仓库只包含追番订阅 Agent 的工具层，不包含完整 Dify 应用、模型 API、推荐系统、用户偏好记忆或生产级前端。
> - 项目按个人需求维护，不承诺通用适配、持续更新、技术支持或生产环境可用性。使用者应自行保护账号、密码、Cookie、Token、调试 Key 和私人 RSS。

这是“追番订阅 Agent”的确定性工具层，而不是一个独立的聊天机器人或推荐 Agent。

插件通过 Dify Tool Plugin 接口连接蜜柑 Project 的公开数据与局域网中的 AutoBangumi 3.2.8，向上层 Agent 提供作品检索、字幕组 RSS 检查、订阅预览、受控写入和写后验证能力。大模型负责理解用户意图和选择工具；本插件负责参数校验、安全边界、外部系统调用与结构化结果。

> 核心原则：确定性操作交给代码，模糊判断交给 Agent，产生实际影响的动作交给用户审批。

## 项目定位

```text
用户
  ↓ 自然语言目标与人工确认
Dify Agent / 大模型                 不在本仓库
  ↓ 结构化工具调用
本仓库：AutoBangumi Dify Tool Plugin
  ├─ 蜜柑公开数据连接器
  ├─ AutoBangumi 只读连接器
  ├─ 订阅预览与一致性校验
  └─ 受控写入与写后回查
       ↓
AutoBangumi 3.2.8                  负责 RSS、下载与媒体整理
```

这个仓库解决的是 Agent 的“手和安全护栏”问题：模型不直接拼接请求、不持有局域网凭据，写工具也会拒绝没有匹配预览指纹或内容已经变化的请求。用户是否已经明确确认仍由上层 Dify 编排负责，插件不会把 `preview_id` 误当成人工授权证明。

## 已实现能力

- 查询 AutoBangumi 状态、RSS 订阅、解析记录和番剧规则。
- 按作品名称搜索蜜柑条目，再按选定作品查询字幕组 RSS 候选。
- 独立检查候选 RSS 的可达性、条目数量、发布时间和样本标题。
- 识别重复 RSS、同作品季度规则、字幕组不一致和联合字幕组署名。
- 对标题、季度和年份调整生成结构化预览，并绑定确定性的 `preview_id`。
- 仅在传入的预览内容仍然一致时执行一次订阅写入；超时或断线后禁止自动重试。
- 写入后同时回查 RSS 与番剧规则，不把 HTTP 成功直接视为任务完成。
- 对 Cookie、Token、账号、密码、本地路径和私人 RSS 等字段进行脱敏。

当前基线：

| 项目 | 状态 |
| --- | --- |
| AutoBangumi 兼容版本 | `3.2.8` |
| 主流程工具 | 9 个 |
| 兼容旧流程工具 | 1 个 |
| 自动化测试 | 93 项通过 |
| Dify 运行形态 | Cloud 远程调试，本地插件主动连接 |
| 写入策略 | 上层人工确认、插件一致性校验、一次写入、禁止盲目重试、写后回查 |

## 工具清单

| 工具 | 类型 | 用途 |
| --- | --- | --- |
| `get_ab_status` | 只读 | 查询 AutoBangumi 连接状态与版本 |
| `list_ab_subscriptions` | 只读 | 查询 RSS 订阅列表 |
| `get_ab_rss_records` | 只读 | 查询指定 RSS 的解析记录 |
| `list_ab_bangumi_rules` | 只读 | 按作品与季度查询番剧规则、执行订阅前查重 |
| `search_mikan_anime` | 只读 | 搜索蜜柑作品条目，处理同名与续作候选 |
| `list_mikan_rss_candidates` | 只读 | 查询选定作品下的字幕组 RSS 候选 |
| `inspect_mikan_rss_candidate` | 只读 | 检查单个公开 RSS 的内容、覆盖范围与可达性 |
| `prepare_ab_subscription` | 预览 | 解析 RSS、校验用户选择、查重并生成确认指纹 |
| `subscribe_ab_rss` | 受控写入 | 重新校验确认内容，执行一次订阅并回查结果 |
| `search_mikan_rss` | 兼容工具 | 旧版一阶段搜索；保留兼容，不建议挂载到新 Agent |

## 一次订阅任务如何闭环

```text
查询已有规则
→ 搜索并确认准确作品/季度
→ 列出字幕组 RSS 候选
→ 检查选定 RSS
→ 生成订阅预览与 preview_id
→ 用户明确确认
→ 重新解析、重新查重、重算 preview_id
→ 最多执行一次写入
→ 回查 RSS 与番剧规则
→ 返回 created_verified / partial_state / unknown_outcome 等明确状态
```

这套设计专门处理真实系统中的不确定性：AutoBangumi 的订阅接口是复合写操作，HTTP 200 不代表 RSS、下载与番剧规则都已按预期完成；超时也不代表服务端一定没有执行。因此工具必须依赖回查判断最终状态，而不能简单重试。

## 代表性边界案例

- 续作 RSS 可能被 AutoBangumi 解析为独立标题的第 1 季；工具同时保留来源解析结果和用户确认的系列标题/季度，并显式展示元数据调整。
- 用户选择的字幕组可能是联合署名中的一个成员；工具会保留完整联合署名并要求确认，不使用模糊子串匹配。
- `01-12` 一类合集 RSS 可能对外可访问，但不被 AutoBangumi 3.2.8 接受；工具会区分“RSS 可达”和“AutoBangumi 可解析”，不会把 406 简化成网络故障。
- 写入超时或连接中断时，工具先回查真实状态；无法确认则返回 `unknown_outcome`，不会自动重试造成重复下载。

## 能力边界

本仓库不包含：

- Dify Agent 应用本身、模型 API 或完整会话编排。
- 番剧推荐、用户偏好记忆和“该看什么”的决策能力。
- 字幕翻译质量、字幕组口碑或片源质量的主观评分。
- AutoBangumi 的下载、重命名和媒体库整理实现。
- 取消订阅、修改规则、自动换源等后续管理能力。
- 将局域网 AutoBangumi 暴露到公网的服务。

## 安全边界与接口白名单

自动化测试中的受控写入全部使用 Mock/Fake Connector，不连接或修改真实 AutoBangumi。

当前允许的 AutoBangumi 请求只有：

- 登录：`POST /api/v1/auth/login`
- 查询程序状态：`GET /api/v1/status`
- 读取 RSS 列表：`GET /api/v1/rss`
- 读取指定 RSS 的种子记录：`GET /api/v1/rss/torrent/{rss_id}`
- 读取番剧规则：`GET /api/v1/bangumi/get/all`

独立的订阅预览连接器额外只允许 `POST /api/v1/rss/analysis`。该接口会让 AutoBangumi 解析公开 RSS，
不会新增 RSS、番剧规则或下载任务；AutoBangumi 自身可能在解析过程中读取并缓存海报。

独立的受控写入连接器只在上述接口之外增加精确的 `POST /api/v1/rss/subscribe`。它不允许 add、collect、
refresh、update、delete 等相邻写入口，订阅请求不会自动重试。

其余请求会在本地白名单检查中被拒绝，不会发送到 AutoBangumi。AutoBangumi 3.2.8 中部分带副作用的接口使用 `GET`，因此连接器同时校验请求方法和完整路径，并禁止 HTTP 重定向。

## 使用

建议创建虚拟环境后安装：

```powershell
python -m pip install setuptools
python -m pip install -r requirements.txt
python -m pip install --no-build-isolation -e .
```

复制 `.env.example` 为项目根目录的 `.env`。在其中填写 Dify Cloud 的远程调试地址和
调试 Key，以及 AutoBangumi 凭据：

```powershell
INSTALL_METHOD=remote
REMOTE_INSTALL_URL=debug-plugin.dify.dev:5003
REMOTE_INSTALL_KEY=从 Dify Cloud 插件页复制的调试 Key
AB_BASE_URL=http://192.168.1.100:7892
AB_USERNAME=你的用户名
AB_PASSWORD=你的密码
AB_EXPECTED_VERSION=3.2.8
MIKAN_BASE_URL=https://mikanime.tv
```

启动远程调试：

```powershell
python -m main
```

请从项目根目录运行该命令；Dify Plugin SDK 和 AutoBangumi 连接器适配层都会自动读取这里的 `.env`（UTF-8）。

在 Dify Cloud 中打开“插件”，点击调试图标获取 Host:Port 与 Key。插件进程连上后，插件列表
会出现带调试标记的 **AutoBangumi Subscription**，并可用以下插件工具：

- `get_ab_status`
- `list_ab_subscriptions`
- `get_ab_rss_records`
- `list_ab_bangumi_rules`
- `search_mikan_anime`
- `list_mikan_rss_candidates`
- `inspect_mikan_rss_candidate`
- `prepare_ab_subscription`
- `subscribe_ab_rss`
- `search_mikan_rss`

在 provider 配置界面填写的 AutoBangumi 凭据优先于 `.env`；两者都不会返回给 Dify。
RSS URL、Cookie、令牌、账号与密码字段在返回前都会遮盖。

## AutoBangumi 番剧规则查询

`list_ab_bangumi_rules(title?, season?, limit?)` 用于追番前的只读查重。Agent 应先按番名和已知季数
查询本工具；没有匹配规则时，才进入蜜柑检索。它只读取 `GET /api/v1/bangumi/get/all`，每次读取前都会
验证已登录的 AutoBangumi 版本为 3.2.8。

番剧身份以 `official_title + season` 为主；`group_name` 代表同一作品/季度下的不同 RSS 来源。`title`
会在本地匹配 `official_title`、`title_raw` 和 JSON 格式的 `title_aliases`，`season` 只接受精确正整数。
默认最多返回 20 条，最大 50 条，并返回 `total_count`、`returned_count` 与 `truncated`。

每条规则只返回适合 Agent 判断的标题、季数、来源、字幕组、偏移建议与原始状态标记：`added`、`archived`、
`deleted`、`needs_review`，以及由插件计算的 `rule_ready`。`rule_ready` 仅在 `added` 为 true 且保存路径存在时为 true；
AutoBangumi 3.2.8 的真实或历史记录中 `rule_name` 可能为空。`added` 本身不表示“是否订阅”；`eps_collect` 不参与本工具的判断。为避免暴露路径、
个人 RSS 或内部规则细节，本工具不会返回 `rss_link`、`save_path`、`poster_link`、`rule_name`、`eps_collect`、
`filter`、`air_weekday`、`weekday_locked` 等字段，也不会将多个状态压缩成单一订阅状态。

## AutoBangumi 订阅预览

`prepare_ab_subscription(rss_url, expected_title, expected_season, expected_group, expected_year?)` 只处理一个用户已经选定的
蜜柑字幕组 RSS。`expected_title`、`expected_season` 和可选 `expected_year` 表示用户确认的媒体库最终元数据，
`expected_group` 表示已选 RSS 的字幕组。工具将 `.me`／`.tv` 地址规范化为 `MIKAN_BASE_URL`，严格校验 `/RSS/Bangumi`、
`bangumiId` 和 `subgroupid`，调用 AutoBangumi 的 `/api/v1/rss/analysis`，再读取现有 RSS 与番剧规则进行查重。

AutoBangumi 自动分析结果属于来源元数据，并不一定等于媒体库所需元数据。例如 RSS 可能将系列续作解析为独立标题 S1，
用户可以在 AutoBangumi 原生确认语义下把最终标题、季度和年份调整为系列口径。这不是绕过解析错误：工具会同时返回
`parsed`、`target`、结构化 `metadata_adjustment` 和 `group_match`，并将来源分析与最终目标一起绑定进 `preview_id`。
标题、季度或年份差异本身不会阻塞。字幕组使用完整成员匹配：完全一致为 `exact`；用户选择值与联合署名中的一个
独立成员一致为 `joint`；不存在完整成员匹配为 `mismatch`。匹配不使用子串，也不会按普通空格拆分组名。

联合署名安全拆分支持半角或全角的 `&`、`+`、`×`、`/`，也支持文本形式的 `&amp;`；每个成员继续经过
Unicode NFKC、大小写和空白规范化。例如用户选择 `LoliHouse`，AutoBangumi 分析为 `千夏字幕组&LoliHouse` 时，
预览不会阻断，而会返回 `match_type=joint`、`confirmation_required=true` 和 `group_expansion` warning，要求用户在最终
预览中确认联合制作署名。`ani` 不会匹配 `ANi-One`，`Loli` 也不会匹配 `LoliHouse`。

工具返回状态：

- `ready_for_confirmation`：解析一致且没有重复项。
- `conflict_requires_confirmation`：同作品和季度已有其他字幕组来源，需要用户明确决定。
- `blocked`：RSS 或分析无效、关键字段缺失、字幕组不匹配、待复核状态、相同 RSS 或相同目标规则。

输出包含脱敏后的 `requested`／`target`、`parsed`、`group_match`、`metadata_adjustment`、重复检查结果、`warnings` 和确定性的 `preview_id`。不会返回
`rss_link`、海报地址、本地路径、过滤规则、Cookie 或 Token。`preview_id` 只证明本次预览内容一致，
不会创建订阅，也不证明用户已经授权后续写入。

`requested.group` 和 `requested.selected_group` 保留用户选择；`target.group` 和 `target.effective_group` 表示
AutoBangumi 实际保存的完整署名。为兼容现有 Dify 参数，`expected_group` 的名称与含义不变。`preview_id` 会绑定
selected_group、effective_group、match_type、成员顺序、目标元数据和完整分析结果；联合署名成员、顺序或匹配类型变化时，
旧预览会在 POST 前失效。

预览失败会标明 `failure_stage`。URL 白名单拒绝使用 `mikan_url_validation`；AutoBangumi 分析失败使用
`autobangumi_analysis`；分析成功后的 RSS/规则读取失败使用 `autobangumi_readback`。上游 HTTP 状态可安全取得时，
返回 `upstream_status_code`。例如 `406` 只表示 AutoBangumi 对该次 `/rss/analysis` 请求分析失败，不能据此断言
公开 RSS 无效。只有有限长度、UTF-8 JSON 中的安全纯文本错误才会作为 `upstream_message` 返回；HTML、URL、
请求头和疑似凭据内容会被丢弃。

## AutoBangumi 受控订阅

`subscribe_ab_rss` 是高风险工具，应只挂到具有可靠人工确认步骤的 Agent。它要求传入与
已确认预览相同的 RSS、最终作品名、最终季数、可选最终年份、字幕组和 `preview_id`。执行前会重新解析、重新查重，并用与
`prepare_ab_subscription` 相同的算法重算指纹。指纹或解析结果变化时返回 `analysis_mismatch`；发现相同
RSS 或同最终作品、季度、字幕组时返回 `already_exists_noop`，不会发出写请求。

通过校验后，工具只覆盖分析对象中的 `official_title`、`season` 和用户明确填写的 `year`，保留 `title_raw`、
`group_name`、`rss_link`、过滤规则、字幕、清晰度和来源等分析字段；联合署名时保留 AutoBangumi 分析得到的完整
`group_name`。随后最多发送一次 `/api/v1/rss/subscribe`，
并按最终标题、季度、字幕组及 RSS URL 只读回查验证结果。
查重与写后回查使用 `effective_group`，不会用用户选择的单个联合成员代替实际署名。
HTTP 成功不会直接视为成功；统一状态包括 `created_verified`、`partial_state`、`unknown_outcome` 和
`write_rejected`。超时或断线后禁止自动重试。输出不会包含 Cookie、Token、本地路径、过滤规则或私人下载链接。

## 蜜柑 Project RSS 搜索

新流程应按以下顺序调用：

1. `search_mikan_anime(title)` 返回作品/季度候选、`bangumi_id`、蜜柑页面显示的作品名与来源页。它最多返回 8 个不同候选；页面没有可靠的独立年份或季度字段时，工具不会推测这些字段，应由 Agent 根据显示名称追问或让用户选择。
2. `list_mikan_rss_candidates(bangumi_id, fansub_group?, subtitle_language?, resolution?, limit?)` 读取已选作品的字幕组候选。`limit` 默认 5、最大 10，返回 `candidate_total`、`returned_count` 和 `truncated`。语言和分辨率来自公开 RSS 标题中的可识别标记；未识别时为空，因此不能替代用户确认。
3. 同一字幕组出现多个候选时，`inspect_mikan_rss_candidate(rss_url)` 直接读取每个公开 RSS。它返回归一化 URL、
   `bangumi_id`、`subgroup_id`、可达性、HTTP 状态、Feed 标题、总条目数、可解析的最新发布时间，以及按 Feed 原始顺序
   截取的前 5 条样本。空 Feed、标题或时间缺失会放进 `diagnostics`；无效 XML 与 HTTP 失败有独立状态。

Inspector 是独立只读工具，不调用 AutoBangumi，也不自动重试。Agent 可以各检查一次不同候选；这与禁止自动重试
`subscribe_ab_rss` 写入无关。条目数量只能帮助判断 Feed 覆盖范围，不能代表字幕质量或字幕组口碑。
对于 `01-12` 一类合集候选，AutoBangumi 3.2.8 可能在 analysis 阶段返回 406。Agent 应说明公开 RSS 与
AutoBangumi 解析能力是两件事，并排除该候选、检查其他不同候选；不要重复请求失败候选，也不要绕过 analysis。
候选选择应以内容证据说明，不应让用户仅凭 subgroup_id 做选择。

`search_mikan_rss` 保留为兼容旧 Dify 配置的工具。它一次完成作品与字幕组搜索，候选跨季度时不适合作为新 Agent 的首选入口。

`search_mikan_rss` 接受必填的 `anime_title` 与可选的 `fansub_group`。它只匿名访问公开的
蜜柑搜索页、番剧详情页和独立 RSS，并返回候选的番剧名、字幕组、清晰度、字幕语言、公开 RSS
地址和来源详情页。字幕组未指定时可能返回多个候选；应让用户确认后再进入任何未来的订阅流程。

返回结构如下；`rss_url` 与 `source_page` 仅在它们是验证过的公开蜜柑地址时保留：

```json
{
  "anime_title": "用户查询",
  "fansub_group": "可选字幕组筛选或 null",
  "candidate_count": 1,
  "candidates": [
    {
      "anime_title": "蜜柑页面番剧名",
      "bangumi_id": 42,
      "fansub_group": "字幕组名",
      "subgroup_id": 200,
      "resolutions": ["1080p"],
      "subtitle_languages": ["简体中文", "繁体中文"],
      "rss_url": "https://mikanime.tv/RSS/Bangumi?bangumiId=42&subgroupid=200",
      "source_page": "https://mikanime.tv/Home/Bangumi/42",
      "rss_item_count": 2
    }
  ]
}
```

当前只允许 `https://mikanani.me` 与 `https://mikanime.tv` 的以下公开 GET 路径：

- `/Home/Search?searchstr=…`
- `/Home/Bangumi/{id}`
- `/RSS/Bangumi?bangumiId=…&subgroupid=…`

不会访问个人订阅 RSS、登录或任何写入入口。页面结构变动、网络错误和无结果会返回可理解的错误或空候选结果。RSS 标题不包含可识别的语言或清晰度时，对应字段会为空。
`MIKAN_BASE_URL` 默认是 `https://mikanime.tv`；它决定搜索入口和所有返回的 RSS URL。即使公开页面中出现
`.me` 链接，工具也会在保留路径与查询参数的前提下规范化为该配置的镜像，并按规范化后的 RSS URL 去重。
已用 `.tv` 的公开 RSS 做过只读抽样：RSS 本身可获取，但样本条目的 `link`／`enclosure` 目标仍为
`mikanani.me`。这不影响本工具返回给后续订阅流程的独立 RSS 地址；若下载器需要访问这些条目目标，仍需
让其具备到 `.me` 的网络可达性。当前不会代理、改写或绕过这些下载链接。
为避免过宽查询给公开站点带来不必要负担，单次搜索最多处理 8 个番剧结果；单个番剧未指定字幕组时最多处理 20 个字幕组。超出限制时请提供更具体的番名或字幕组。清晰度和字幕语言来自 RSS 发布标题，是启发式结果，不能替代用户确认。

在 Dify 的插件调试页中选择 **AutoBangumi Subscription**，调用 `search_mikan_rss` 并填写番剧名；可选填写字幕组名缩小候选。此工具只提供候选信息，不会创建蜜柑或 AutoBangumi 订阅。

Dify Agent 需要追加的候选检查和跨轮确认规则见 [DIFY_AGENT_PROMPT_ADDENDUM.md](DIFY_AGENT_PROMPT_ADDENDUM.md)。

## 开发验证

```powershell
python -m unittest discover -s tests -v
```
