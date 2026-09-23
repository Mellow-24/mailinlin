# Handoff：玲玲师傅 Voice Demo 项目现状

更新日期：2026-09-23（Asia/Shanghai）  
当前分支：`main`  
当前线上提交：`b41aa9e`（`Hide support launcher on voice demo`）  
GitHub：`git@github.com:Mellow-24/mailinlin.git`

> 本文用于把当前项目交给下一位开发者继续维护。文中只记录配置名称、位置和非敏感参数，不包含 DashScope Key、MiniStream Token、数据库密码、JWT、TURN Secret、Embed Token 或 SSH 私钥内容。

## 1. 项目目标与当前形态

当前产品是在 Dograh 开源项目基础上改造出的独立语音演示页，面向客户展示“香港风水师玲玲师傅”的粤语 AI 语音咨询体验。

核心体验：

- 页面角色名统一为“玲玲师傅”，定位是以香港风水师麦玲玲公开形象和专业领域为角色设定的 AI 语音角色，不冒充真人。
- 用户进入页面后，语音链路会在后台预连接；点击“开始语音咨询”后立即播放本地预置开场音频。
- 开场白结束且 STT、Agent 都准备好后，显示圆形录音按钮。
- 用户按一下开始录音，再按一下结束本轮；页面实时显示识别文字。
- 后端完成意图判断、规则计算、FAQ 检索和一次 LLM 生成；回答文字流式显示，同时由流式 TTS 播放粤语。
- 咨询流程最多问三问，第三次有效回答后必须给综合分析，不再无限追问。
- 问候、闲聊、道谢、告别和无关问题走普通对话，不强行套风水或生肖逻辑。

公网演示地址：`https://39.105.228.7/`

公网只开放演示根路径。`/voice-demo`、Dograh 后台、登录页、设置页、普通 API 和 MinIO 管理端都由 Nginx 返回 404。

## 2. 本地仓库与目录

当前本机仓库已经从旧桌面目录移动到：

```text
/Users/melo/Documents/编程/10-work/易水大师
```

旧路径 `/Users/melo/Desktop/易水大师` 已不存在。后续如果 Codex 或脚本仍以旧路径启动，需要先切换到新目录。

重要文件：

| 领域 | 文件 | 作用 |
| --- | --- | --- |
| Voice Demo 页面 | `ui/src/app/voice-demo/YishuiVoiceDemo.tsx` | 页面布局、状态机、开场白、录音按钮、文字气泡、等待动效 |
| Voice Demo 入口 | `ui/src/app/voice-demo/page.tsx` | 从服务端环境变量读取 Embed Token 并渲染页面 |
| 语音 Widget | `ui/public/embed/dograh-widget.js` | WebRTC、录音、消息事件、显式本轮结束信号、无 UI headless 模式 |
| 开场音频 | `ui/public/voice-demo/warm-audio/` | 7 条预置 WAV、单条 JSON 和 `openings.json` |
| 角色 Prompt | `scripts/optimize_yishui_agent.py` | 人设、普通对话分流、三问流程、时间规则、八字/风水边界 |
| Demo Token 配置 | `scripts/provision_yishui_voice_demo.py` | 创建/更新 headless Embed Token 和 fast-opening 设置 |
| 对话入口 | `api/services/pipecat/direct_final_transcript_processor.py` | 合并最终转写、每轮只触发一次 LLM、意图分类、轮次状态 |
| 三问保护 | `api/services/pipecat/consultation_response_guard_processor.py` | 控制前两轮只有一个问句、第三轮不再问、失败兜底 |
| 主语音管线 | `api/services/pipecat/run_pipeline.py` | 串联 STT、上下文、RAG、LLM、TTS、预热和 token 上限 |
| 本地 FAQ 检索 | `api/services/pipecat/direct_faq_retrieval.py` | 无 Embedding 的本地低延迟 FAQ 检索 |
| 历法/排盘规则 | `api/services/metaphysics/birth_chart_context.py` | 公历解析、农历生肖、立春年柱、四柱和本命年确定性判断 |
| 当前日期注入 | `api/services/workflow/initial_context.py` | 每轮注入香港当前日期，避免继续回答 2025 |
| 跨会话记忆 | `api/services/workflow/memory.py` | 显式同意后才允许读取最小摘要 |
| MiniStream TTS | `api/services/ministream/tts.py` | 北京算法团队的流式粤语 TTS WebSocket 实现 |
| 模型配置定义 | `api/services/configuration/options/`、`registry.py` | DashScope、MiniStream 的模型和端点配置 |
| FAQ 知识库 | `knowledge-base/麦玲玲AI_Agent_FAQ库_粤语_RAG.txt` | 当前 Voice Demo 的粤语 FAQ 文本库 |
| 云端部署 | `deploy/voice-demo/` | Compose、Caddy、Nginx、部署脚本、非敏感服务器参数 |

仓库中当前还有用户自己的未跟踪目录 `docs/`，不要在未确认用途前删除、覆盖或顺手提交。

## 3. 前后端和语音链路

```text
浏览器
  ├─ HTTPS 页面/静态资源 ─> Caddy :443 ─> Nginx gateway :80 ─> Next.js UI :3010
  ├─ Embed/信令请求 ──────> Caddy ─> Nginx 白名单路由 ─> FastAPI :8000
  └─ WebRTC 音频 ─────────> Coturn :3478 + UDP 49152-49200 ─> Pipecat
                                                        ├─ DashScope STT
                                                        ├─ 本地规则 + 本地 FAQ
                                                        ├─ DashScope Qwen LLM
                                                        └─ MiniStream 流式粤语 TTS
```

Voice Demo 使用 `headless` Widget：Dograh Widget 负责连接、WebRTC 和事件，页面自行绘制全部 UI，不显示原框架的悬浮按钮。Chatwoot 支持气泡也已在该子页面屏蔽。

## 4. 页面状态与开场白

主要页面状态：

- `idle`：等待用户开始。
- `connecting`：语音链路连接中。
- `greeting`：播放开场白。
- `ready`：允许录音。
- `listening`：用户录音中。
- `processing`：识别结束，规则/RAG/LLM 处理中。
- `speaking`：AI 文字和语音输出中。
- `ended` / `error`：会话结束或异常。

页面挂载后会执行 `widget.init()` 并立即调用 `widget.start()` 做后台预连接，用户点击开始时通常复用已经建立的连接。线上 Embed 配置已验证：

- `embedMode=headless`
- `fastOpening=true`
- `directVoiceDemo=true`
- `recordingDrainMs=1200`

开场白当前有 7 条：`greetings-01.wav` 到 `greetings-07.wav`。页面每次咨询随机选择一条，全部使用 MiniStream 的 `mailinlin` 预置音色、粤语、`1.0` 原速生成，时长约 7.5～10.1 秒。

注意：页面目前在 `YishuiVoiceDemo.tsx` 中硬编码了 7 条音频 URL 和字幕，并没有直接读取 `openings.json`。如果以后修改开场文案或文件名，需要同时更新页面常量；如还修改默认开场契约，还要重新运行 `provision_yishui_voice_demo.py` 更新 Embed Token。

开场音频结束后，只有同时满足以下条件才亮起录音按钮：

```text
connectionStatus === "connected"
agentReady === true
phase === "ready"
```

如果出现“音频播完仍不能录音”，优先检查浏览器 `agentReady` 事件、STT 连接日志、TURN 端口和音频 `onEnded` 是否触发，不要只改前端计时器。

## 5. 当前线上模型配置

以下值于 2026-09-23 从线上组织 `organization_id=1` 的 `MODEL_CONFIGURATION_V2` 只读核对：

| 能力 | Provider | 当前配置 |
| --- | --- | --- |
| LLM | DashScope | `qwen-flash`，thinking 关闭，`max_tokens=320`，`temperature=0.1` |
| STT | DashScope | `fun-asr-realtime-2026-02-28`，语言提示 `zh`，16 kHz PCM；该模型用 `zh` 识别中文及粤语 |
| TTS | MiniStream | `ministream-tts`，`language=yue`，`generation_mode=preset_voice`，`voice_preset_key=mailinlin`，`playback_rate=1.0` |
| Embedding | DashScope | `qwen3.7-text-embedding`；平台配置仍保留，但 Voice Demo 的直接 FAQ 路径不调用它 |

MiniStream 运行时采用长连接流式合成，provider 侧 `buffer=off`，本地通过 FFmpeg 转为 16 kHz PCM。首次调用会预热 WebSocket；当前固定试用端点使用证书指纹校验。旧的 DashScope 克隆音色 TTS 已不再作为 Voice Demo 回答和开场白的主链路。

敏感 Key 不在前端代码中。DashScope 和 MiniStream 凭证保存在数据库模型配置；MiniStream 切换脚本只从进程环境变量 `MINISTREAM_TTS_API_KEY` 读取凭证，而且不会打印。

## 6. 对话逻辑

### 6.1 普通对话与咨询分流

`DirectFinalTranscriptProcessor` 使用本地正则做轻量意图判断，不额外调用分类模型：

- 明确包含风水、八字、生辰、生肖、流年、运势、择日、改名、号码、家宅、方位等主题时，进入咨询流程。
- 用户在咨询前只说“你好”“多谢”“再见”或一般闲聊时，直接自然回答，不检索 FAQ，不问出生日期、生肖或方位。
- 咨询已经开始后，前两次资料回答会继续归入本次咨询；咨询三问完成后，只有明显的相关追问才继续按咨询回答。
- 明确告别、拒绝继续或“冇其他问题”时，道别优先，不允许知识库兜底成风水分析。

### 6.2 三问封顶

固定开场最后的“今日想问咩呢？”算第 1 问，但只有用户真正提出风水命理相关咨询时才启动三问计数。

1. 第 1 次有效咨询回答：先解析用户内容，再只问 1 个第 2 问。
2. 第 2 次有效咨询回答：先结合前两轮解析，再只问 1 个最后问题。
3. 第 3 次有效咨询回答：立即输出详细综合分析，禁止继续提问。
4. 三问完成后的追问：结合当前通话上下文直接回答，不重新开始采集资料。

问题选择由主题决定：

- 一般运程、事业、财运、感情、添丁：优先补完整公历出生日期，再问最关注方面。
- 八字/生辰：完整公历出生日期，再补当地出生时间。
- 家宅风水：先问大门方位，再问想改善的家宅方面；普通风水问题不强行收八字。
- 八字配家宅：可同时参考命盘，但住宅坐向、平面布局和实际使用仍是风水判断核心。
- 择日：先问事项，再问日期范围。
- 改名：问为谁改名。
- 号码：问具体号码。

`ConsultationResponseGuardProcessor` 会在流式输出层执行保护：前两轮只保留一个最终问句；模型无输出或超时时生成本地兜底；第三轮及以后不再允许资料收集式问句。

### 6.3 单次模型调用和延迟策略

每个用户回合只发起一次 Qwen 生成：

- 工作流只保留一个对话节点，没有 Agent 工具跳转边。
- 关闭 post-call extraction，避免挂断后隐藏的第二次模型请求。
- 关闭 thinking，温度 `0.1`。
- 第 1～2 个咨询回合最多 100 token，第 3 回合最多 220 token，后续最多 160 token。
- FAQ 在 LLM 前本地检索并注入，同一回合不再让 LLM 决定是否调用检索工具。
- STT、LLM、TTS 都有预热逻辑；TTS 在管线启动时并行建立连接。
- 浏览器结束录音后保留 1.2 秒 drain，让 Fun-ASR 完成尾音；随后通过 `voice-turn-ended` 明确提交本轮，避免依赖复杂 VAD。

### 6.4 上下文与记忆

- 当前通话：共享同一个 `LLMContext`，保留多轮消息；用户更正信息后以最新内容为准。
- FAQ 参考和历法计算结果：每轮替换旧的 system message，不无限堆积。
- 页面在浏览器 `localStorage` 保存随机 `visitor_id`，供跨会话身份匹配。
- 工作流配置 `memory_configuration.enabled=true`，记忆模块只允许读取用户明确同意保存的最小摘要，并以哈希后的主体 ID 查询。
- 当前低延迟工作流把 `extraction_enabled` 设为 `false`，所以不能把“已启用记忆框架”误写成“当前一定会自动把每次通话摘要保存下来”。如要真正启用新摘要持久化，需要另行设计不阻塞通话的异步、显式同意写入流程。

## 7. 知识库与 RAG

知识库来源：

```text
knowledge-base/麦玲玲AI_Agent_FAQ库_粤语_RAG.txt
```

它由原 Excel FAQ 整理而来，目前约 1,462 行、107 KB、286 个 FAQ 条目。内容覆盖 2026 马年十二生肖运程、家居/流年风水、择日通胜、开运改运、玄学常识和产品说明。

Voice Demo 线上启用了：

```text
direct_local_faq_enabled=true
direct_consultation_question_limit=3
```

直接检索逻辑：

- 按 `FAQ 编号：` 拆分文本块。
- 从“分类”“用户问法（口语）”“粤语参考回答”提取字段。
- 对当前一轮用户文字做 2～4 字 n-gram 匹配。
- 最多返回 2 段，每段最多 700 字。
- 本地读取和匹配，不走网络、不调用 Embedding，优先保证语音首包速度。
- 只使用当前一轮作为检索 query，避免上一轮生肖命中一直黏住后续回答。
- 普通问候、闲聊和告别不执行检索。

平台仍保留向量知识库和 DashScope Embedding 能力，但当前专用 Voice Demo 的热路径刻意绕开向量检索。若未来换成大规模知识库，可以再启用向量检索，但要继续保留 450 ms 级别硬超时和“检索失败仍正常单次生成”的降级策略。

FAQ 只提供解释素材，不能负责生肖换算、农历转换、四柱排盘或当前年份判断。确定性计算必须由规则引擎完成。

## 8. 出生日期、生肖和八字规则

规则实现：`api/services/metaphysics/birth_chart_context.py`  
依赖：`lunar_python==1.4.8`

当前约定：

- 默认用户提供的是公历出生日期；引导文案明确问“完整公历出生日期”。
- 支持识别阿拉伯数字、常见中文数字、两位年份，以及跨轮提供资料，例如先说“1995 年”，下一轮说“6 月 27 日”。
- 完整公历日期会确定性转换成农历日期。
- 日常生肖按农历正月初一换年。
- 八字年柱和流年按精确立春节气换年。
- 只有年份、没有月日时，不猜 1～2 月出生者的生肖。
- 有完整日期但没时间：可以给农历和日常生肖，但禁止输出完整四柱、时柱、十神和大运起运岁数。
- 有完整日期和当地时间：可输出四柱；当前按 UTC+8 民用时间、节气月、晚子时 Sect 2，未做真太阳时校正。
- 出生地不在 UTC+8、处于节气边界或 23:00 边界时，必须提示可能变盘。
- 本命年必须把用户生肖与当前日常生肖年/八字流年年支做确定性比较；六合、三合、相冲、相刑、相破、相害都不能被写成本命年。

2026 年示例：当前为丙午马年；属马才是本命年。属羊与马是六合，不是本命年。该结论由规则上下文注入，优先级高于 FAQ 和模型自由发挥。

每个用户回合还会注入香港时区的实时公历日期，明确“今年/去年/明年”。当前 FAQ 是 2026 版本，不能把其“今年”内容用于其他年份，也不能声称知识库会自动更新。

## 9. 阿里云部署现状

| 项目 | 当前值 |
| --- | --- |
| ECS 实例 | `i-2zeffwblsszsq6sj527l` |
| 公网 IP | `39.105.228.7` |
| 服务器仓库 | `/opt/mailinlin` |
| Git 分支 | `main` |
| 当前线上提交 | `b41aa9e` |
| Compose 文件 | `/opt/mailinlin/deploy/voice-demo/docker-compose.yml` |
| 公网入口 | Caddy `80/443` |
| 内部演示网关 | `127.0.0.1:18080` |
| API/UI | 仅 Docker 内网 `8000/3010` |
| 数据库/缓存/对象存储 | PostgreSQL、Redis、MinIO，仅 Docker 内网 |
| WebRTC | Coturn `3478` TCP+UDP，`49152-49200` UDP |

2026-09-23 只读检查结果：`api`、`ui`、`gateway`、`edge`、`postgres`、`redis`、`minio` 均为 running/healthy，`coturn` running；`http://127.0.0.1:18080/healthz` 和公网根路径都返回 200。

虽然新的 Compose 文件声明项目名为 `mailinlin-voice-demo`，线上现存容器标签和名称仍是 `dograh-*`，例如 `dograh-api-1`、`dograh-ui-1`。日常操作优先使用完整 `docker compose ... -f deploy/voice-demo/docker-compose.yml` 命令，不要依赖猜测容器名。

服务器同时承载其他服务。Voice Demo 的 Postgres、Redis、MinIO、API、UI 不映射公网端口；只有 Caddy 的 80/443、Coturn 的 3478 和媒体 UDP 范围对外，避免与其他服务发生域名或端口冲突。

安全组至少要有：

- TCP `80`、`443`
- TCP + UDP `3478`
- UDP `49152-49200`
- UDP `443` 只用于 Caddy HTTP/3，可选；核心 HTTPS 仍是 TCP 443

未部署 TURN TLS `5349`，配置为 `TURN_TLS_PORT=0`，浏览器不应收到不可用的 5349 地址。线上 `FORCE_TURN_RELAY=true`。

## 10. 部署和更新操作

当前工作站使用的 ECS SSH 私钥路径是：

```text
/Users/melo/.ssh/id_ed25519_mailinlin_ecs
```

只记录路径，不要复制或提交私钥内容。服务器到 GitHub 的 Deploy Key 已配置，可在服务器直接拉取 `main`。

登录：

```bash
ssh -i /Users/melo/.ssh/id_ed25519_mailinlin_ecs root@39.105.228.7
```

完整更新：

```bash
cd /opt/mailinlin
git pull --ff-only origin main
./deploy/voice-demo/deploy.sh
```

部署脚本会校验配置、拉取基础服务镜像，然后执行：

```bash
docker compose \
  --env-file deploy/voice-demo/.env \
  --env-file deploy/voice-demo/server.env \
  -f deploy/voice-demo/docker-compose.yml \
  up -d --build --remove-orphans
```

重要坑：API 大量源码通过“单文件 bind mount”挂进容器。`git pull` 替换文件 inode 后，单纯执行 `docker compose restart api` 可能仍然看到旧文件。修改这些挂载文件后至少要强制重建 API 容器：

```bash
docker compose \
  --env-file deploy/voice-demo/.env \
  --env-file deploy/voice-demo/server.env \
  -f deploy/voice-demo/docker-compose.yml \
  up -d --no-deps --force-recreate api
```

前端代码或开场音频变化需要重新构建并重建 UI：

```bash
docker compose \
  --env-file deploy/voice-demo/.env \
  --env-file deploy/voice-demo/server.env \
  -f deploy/voice-demo/docker-compose.yml \
  build ui

docker compose \
  --env-file deploy/voice-demo/.env \
  --env-file deploy/voice-demo/server.env \
  -f deploy/voice-demo/docker-compose.yml \
  up -d --no-deps --force-recreate ui
```

Prompt、工作流配置或模型配置是数据库状态，单纯部署代码不会自动重新发布工作流。相关脚本需要在正确的 API 环境和数据库连接下显式执行：

- `python -m scripts.optimize_yishui_agent --workflow-id 1`
- `python -m scripts.provision_yishui_voice_demo --workflow-id 1 --organization-id 1 --user-id 1 --allowed-domain 39.105.228.7 --quiet`
- `python -m scripts.apply_yishui_performance_profile --organization-id 1`
- `python -m scripts.set_yishui_ministream_tts_playback_rate --organization-id 1`

不要在命令行参数中传 Key。MiniStream 切换脚本只接受环境变量 `MINISTREAM_TTS_API_KEY`。

## 11. 配置与敏感数据

服务器部署配置：

- `deploy/voice-demo/server.env`：已纳入 Git，仅包含公网 IP、端口、基础镜像、日志级别等非敏感参数。
- `deploy/voice-demo/.env`：被 `.gitignore` 忽略，包含 PostgreSQL/Redis/MinIO 密码、TURN Secret、JWT Secret、Voice Demo Embed Token 等敏感值。
- DashScope 与 MiniStream API Key：保存在 PostgreSQL 的组织模型配置中，不在浏览器 bundle 和服务器公开环境接口中。
- 数据库卷、Caddy 证书卷、MinIO 卷：由 Docker named volume 保存，不提交 Git。
- SSH 私钥和 GitHub Deploy Key：只保存在各自机器，不提交 Git。

任何 handoff、截图、日志或问题单都不应复制完整 Key、Token、Cookie、Authorization Header、数据库连接串或私钥。

## 12. 日志与排障

先定义统一 Compose 命令：

```bash
cd /opt/mailinlin
COMPOSE="docker compose --env-file deploy/voice-demo/.env --env-file deploy/voice-demo/server.env -f deploy/voice-demo/docker-compose.yml"
```

状态和日志：

```bash
$COMPOSE ps
$COMPOSE logs --since=30m --tail=300 api
$COMPOSE logs --since=30m --tail=200 ui gateway edge coturn
```

健康检查：

```bash
curl -fsS http://127.0.0.1:18080/healthz
$COMPOSE exec -T api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status)"
curl -fsS -o /dev/null -w '%{http_code}\n' https://39.105.228.7/
curl -fsS -o /dev/null -w '%{http_code}\n' https://39.105.228.7/voice-demo
```

预期根路径为 200，`/voice-demo` 为 404。

常见故障判断：

- 开场白后一直不能录音：先看 STT 是否 connected、`agentReady` 是否下发、TURN 安全组是否完整、浏览器麦克风权限和 WebRTC ICE 状态。
- 发送后一直“分析中”：看 API 日志中本轮是否收到最终 Transcription、`voice-turn-ended`、Qwen 首 token、TTS 首包和 `BotStoppedSpeakingFrame`；不要只看前端动画。
- 多轮后卡住：检查 `_response_in_flight` 是否在 `BotStoppedSpeakingFrame` 或错误/结束帧后复位，以及 TTS WebSocket 是否正常结束本次 request。
- 回答每轮相同：确认只用当前 utterance 做 FAQ query，旧 FAQ system message被替换，并确认用户最终转写没有延迟到下一轮才提交。
- 文字已出但语音很晚：重点看 MiniStream socket 是否预热、文本是否按句 flush、FFmpeg decoder 是否启动、provider 首包耗时，不要重新引入整段文字生成完再调用 TTS 的模式。
- 线上代码看起来没更新：不要只 restart API；对 bind-mounted 文件强制 recreate，对前端必须重新 build UI。

## 13. 测试入口

与当前改造直接相关的测试：

- `ui/src/app/voice-demo/YishuiVoiceDemo.test.tsx`
- `api/tests/test_direct_final_transcript_processor_unittest.py`
- `api/tests/test_consultation_response_guard_unittest.py`
- `api/tests/test_direct_faq_retrieval.py`
- `api/tests/test_birth_chart_context.py`
- `api/tests/test_client_opening_unittest.py`
- `api/tests/test_ministream_tts.py`
- `api/tests/test_ministream_opening_generator.py`
- `api/tests/test_workflow_memory.py`
- `api/tests/test_yishui_time_prompt_unittest.py`
- `api/tests/test_yishui_voice_demo_provisioner.py`

另有流程检查脚本：

- `scripts/verify_yishui_direct_three_question_flow.py`
- `scripts/verify_yishui_three_question_flow.py`
- `scripts/verify_yishui_multiturn.py`

## 14. 当前已知边界与下一步建议

1. 当前 FAQ 是 2026 年版本。进入新年份前必须更新 FAQ 和年份政策，并做生肖/本命年回归测试。
2. 本地 n-gram 检索优先速度而非语义精度。FAQ 规模继续增长时，应增加按类别预索引或轻量 BM25，而不是直接把每轮改回远程 Embedding。
3. 开场白元数据在页面和 JSON manifest 两处存在，后续最好改成页面读取 `openings.json`，减少双份维护。
4. 跨会话记忆框架存在，但当前低延迟配置关闭了 post-call extraction；需要单独补一个不阻塞语音主链路、显式同意的异步摘要写入方案。
5. MiniStream 使用试用端点和固定证书指纹；算法团队更换证书、端点、协议字段或 Token 前，必须同步更新配置并先做握手/首包测试。
6. 八字规则目前能做公历转农历、生肖和四柱基础计算，但没有实现真太阳时、出生地时区自动换算、完整大运起运规则和流派差异。不要让 Prompt 声称已经提供这些精确能力。
7. 产品是传统文化演示，不构成医疗、法律、投资、建筑结构或人身安全专业意见；页面和 Prompt 中的免责声明应保留。

## 15. 最近关键提交

```text
b41aa9e Hide support launcher on voice demo
66990c9 Reuse parsed birth date across voice turns
d6c0508 Make current zodiac and benming deterministic
08d4f35 Mount voice turn boundary module in demo
5c47947 Commit each push-to-talk turn once
71ba7e3 Remove contradictory birth intake guidance
d423090 Add deterministic birth chart context
556244c Require TURN relay for cloud voice demo
2a61ac8 Serve IP certificate without SNI
fdf9e87 Use public IP short-lived TLS certificate
```

接手时建议先阅读本文件，再按顺序查看 `YishuiVoiceDemo.tsx`、`direct_final_transcript_processor.py`、`run_pipeline.py`、`direct_faq_retrieval.py`、`birth_chart_context.py` 和 `deploy/voice-demo/README.md`。
