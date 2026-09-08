# Knowledge Distiller 实现状态

更新时间：2026-09-08（Asia/Shanghai）

## 当前结论

`knowledge-distiller` 已完成既有 adapter-contract 里程碑，以及 core dual-source loop 的 Task 1–7。Task 7 已打通同一 task 内 Lark 云文档与 Codex 本地会话的双源累计 ingestion；下一项为 Task 8 evidence-to-skill core loop。

Task 7 的独立规格终审为 `SPEC PASS`，独立质量终审为 `READY`，Critical、Important、Minor 均无遗留。计划指定套件为 84/84，独立与主进程严格全量验证均为 329/329；`compileall` 与 `git diff --check` 均通过。完整验证命令为：

```bash
PYTHONWARNINGS=error python3 -m unittest discover -s tests -v
```

精确 tuple `lark / 1.0.0 / 1.0.86 / docx-v1-raw-content-v1` 为 `normalizer-supported`；`codex / 1.0.0 / 0.153.0 / rollout-jsonl-v1` 已进入 event-graph allowlist 并通过 conformance。Task 7 提供 dependency-injected trusted ingestion runtime 边界，但没有默认 production runtime，也不授权未来读取；Claude Code 与 Trae session adapter 仍为 `blocked`。

## 分支与提交

- 当前分支：`feat/implement_knowledge_distiller`
- 最新实现提交：`77f5df0 feat: add atomic dual-source ingestion`
- Task 7 已提交、已完成独立规格与质量终审闭环
- 未 push、未 merge、未安装、未导出、未发布
- 本 completion record 提交后相对本地 `origin/main` ahead 52、behind 0

已完成的本地提交：

| Commit | 状态 | 内容 |
| --- | --- | --- |
| `b555242` | 已完成 | Knowledge Distiller 确定性 Foundation |
| `e8c87d8` | 已完成并通过独立评审 | durable checkpoint、journal、generation、recovery 与 task CLI |
| `1c87108` | 已完成 Task 1 中间规格/质量双审 | adapter compatibility gate 与 canonical contract |
| `37b9f91` | 已完成 Task 2 中间规格/质量双审 | synthetic-only canonical event graph validator |
| `4008c54` | 已完成 Task 3 初版 | `validate-event-graph` CLI |
| `12b9369` | 已提交 | event graph input boundary hardening |
| `df20cfd` | 已提交 | trust-anchor precedence、动态 parser memory budget、tiny sparse 与路径 alias 修复 |
| `1aac3c3` | 已提交 | 保留显式路径尾部语义并收窄 preflight 返回面 |
| `ca67171` | 已提交 | structural/string budget、真实 moving-file 与 character-device 测试补强 |
| `1a83de2` | 已提交 | 将 adapter conformance gate 整合进 SKILL 与 README |
| `42d8b92` | 已提交 | 将 conformance 示例改为 shell-safe 的合法 digest |
| `6e450f5` | 已提交 | 拆分 compatibility 与 canonical normalization guidance 路由 |
| `3f48383` | 已提交 | 拒绝 JSON lone surrogate 并补充回归覆盖 |
| `e385ab9` | 已提交 | 精确版本 Lark raw-content normalizer 与合成 fixture |
| `b293cf7` | 已提交 | 修复 JSON escape 脱敏顺序、typed snapshot 组合与 raw-content broker 边界 |
| `89edbe9` | 已完成并通过独立评审 | typed document 转换前资源预检与冗余收敛 |
| `1996505` | 已提交 | Task 5 completion record |
| `fafbd98` | 已完成并通过独立评审 | 精确版本 Codex rollout adapter、合成 fixture 与 fail-closed 因果边界 |
| `80cb90d` | 已提交 | Task 6 completion record |
| `77f5df0` | 已完成并通过独立评审 | 同一 task 的 Lark/Codex 双源原子 ingestion、完整 evidence/provenance 与 TOCTOU 防护 |

本次 completion-record 提交只记录计划与状态，不在文档中写入自引用 SHA。Task 1/2/3/4 的中间 review gate 和 Task 5 最终 review gate 均已完成。

## 已实现能力

### 1. Meta-skill Foundation

- 建立 `knowledge-distiller/SKILL.md`，覆盖 `discover`、`distill`、`update` 与 `resume` 的路由。
- 实现严格的任务状态机、typed transition facts、合法 phase 转移与终态保护。
- 明确区分 source grant、claim decision、version approval 和 mutation consent。
- 实现非可执行 domain-skill draft 的 closed-policy validator。
- 拒绝脚本、package manifest、归档、symlink、hardlink、特殊文件、未知根文件、危险命令文本和不受信任 asset。
- 保持私有 evidence 与可导出 skill draft 分离。

### 2. Durable checkpoints

- 实现 CRC32C、canonical JSON、长度前缀 framed journal、sequence、fencing epoch、payload digest 与 SHA-256 record chain。
- 仅允许截断物理不完整的最后一帧；checksum、sequence、hash 或内部 frame 损坏均 fail closed。
- 实现 `PREPARE → immutable generation → COMMIT → atomic pointer replacement`。
- 使用 owner-only `0700` 目录与 `0600` 文件。
- 使用 descriptor-relative I/O、`O_NOFOLLOW` 与 `flock`，抵御路径替换、symlink 和并发 writer。
- 支持 read-only inspect、torn-tail recovery、pending generation quarantine 和 stale pointer repair。
- 提供 `task-init`、`task-transition`、`task-inspect` 与 `task-inspect --recover`。
- checkpoint 只记录封闭的 typed facts 和 digest，不记录 source 内容、locator、secret 或自由文本。

该里程碑完成时，全量测试为 83/83；独立最终评审结论为 Ready，无 Critical/Important 遗留。

### 3. Adapter compatibility gate

- 新增 [adapter compatibility](../../knowledge-distiller/references/adapter-compatibility.md) 与 [canonical adapter contract](../../knowledge-distiller/references/adapter-contract.md)。
- 记录 2026-09-04 的官方资料和本地只读 CLI help 证据。
- 明确区分“产品存在某项能力”和“存在稳定、可安全解析的 native schema”。
- Lark 精确 raw-content tuple 为 `normalizer-supported`；Codex 精确 rollout tuple 为 `supported`；Claude Code、Trae 仍为 `blocked`。
- event graph 允许 synthetic 与精确 Codex tuple；canonical document 允许 synthetic 与精确 Lark tuple。
- Lark 只允许显式 `--as user` 的 Docx raw-content GET，并要求 trusted receipt 给出一致的 revision-before/after。
- Codex `0.153.0` 的 version-pinned 官方源码、合成 observed-shape fixture 与授权聚合 closed-prefix replay 已固定 `rollout-jsonl-v1`；旧版本及包含无法证明因果或内容投影的完整文件 fail closed。
- Claude Code 官方公开本地 JSONL 与 session API，但尚无满足本项目完整因果契约的版本化 fixture。
- TraeCode 的带连字符可执行文件 `trae-cli` 已确认存在；其版本输出为 `traecli 0.202.3(internal edition)`。不带连字符的 `traecli` 是另一个 Coco 程序，不能作为 TraeCode 证据。

### 4. Canonical event graph validator

- 实现 closed schema：root、adapter、owner、event、actor、workspace、content segment、artifact、outcome、edge/reference 和 fidelity loss。
- 未知字段、duplicate JSON keys、非法 scalar、bool-as-int、NaN/float、超限字段、unsupported version 均拒绝。
- 使用外部 `ValidationContext` 固定 expected owner 与 expected source snapshot，图内容不能自证身份或 digest。
- 支持 strict raw JSON decoder，错误不会回显 source 内容、未知 key、locator、路径或 anchor。
- 实现 event/native ID 唯一性、stream position、canonical digest 与 snapshot binding。
- 实现 14 类 forward causal edge 的闭合语义与 cardinality。
- 验证 DAG、tool call/chunk/result、message sent/delivered/consumed、edit/retry/fork/supersession、compaction、nested agent spawn/terminal/join。
- child lifecycle 使用 `(root_stream_id, actor.id, correlation_id)` 匹配，允许合法多跳关系，拒绝 orphan、重复 terminal 和未终止 child。
- 将 native stream 相邻 position 作为 validation-only 隐式顺序边，检测跨 stream 后重新进入时的因果顺序冲突。
- 使用 iterative depth/cycle guard 和 edge indexes，避免递归崩溃与重复全图扫描。
- 包含 minimal、tool flow、nested agent 三组 synthetic fixtures。

该里程碑最终为 42/42 adapter tests，规格与质量复审均通过，无 Critical/Important 遗留。

### 5. 第一版 event graph CLI

- 新增命令：

```bash
python3 knowledge-distiller/scripts/kd.py validate-event-graph GRAPH.json \
  --expected-owner-id OWNER_ID \
  --expected-source-snapshot-id sha256:...
```

- owner/snapshot trust anchors 为必需参数，不从 graph 派生。
- 使用单一 descriptor 读取显式文件；拒绝 final-component symlink、目录和特殊文件。
- 64 MiB raw byte ceiling 在 JSON parsing 前检查。
- UTF-8/JSON syntax/input boundary 错误映射 exit `2`；duplicate key 与 contract violation 映射 exit `3`。
- 成功结果只输出 canonical manifest，不输出 source 内容。

初版提交时全量测试为 138/138，规格审查通过。随后质量审查发现 moving-file consistency 与 parser memory amplification 两个 Important；当前均已通过回归测试闭环。加固还覆盖 ancestor symlink、hardlink、所有非空 sparse file、FIFO/socket/character device、等价路径 alias、尾部目录语义、malformed anchors-before-read，以及 compound error precedence。

最终 Task 3 规格审查通过；最终质量审查独立运行定向 93/93、全量 158/158、`compileall` 与 `git diff --check`，结论 Ready，Critical/Important/Minor 均为零。

### 6. Adapter guidance integration（Task 4）

- `SKILL.md` 和 README 已加入 shell-safe 的 `validate-event-graph` 命令，以及 `--expected-owner-id`、`--expected-source-snapshot-id` 两个必需 trust anchors。
- compatibility 与 canonical normalization 被路由到 `adapter-compatibility.md`、`adapter-contract.md` 和本地 validator。
- 明确成功 event-graph validation 只证明 synthetic tuple/canonical contract conformance，不授权 source read 或 native tool invocation，也不让任何 native event-graph adapter 变为 supported。
- 该里程碑完成时 Lark、Codex、Claude Code、Trae 均为 blocked；Task 5 后仅精确 Lark document normalizer tuple 改为 `normalizer-supported`。
- skill TDD 基线确认旧 guidance 无法给出精确命令；新契约测试先 RED 后 GREEN。
- 首轮规格审查发现 angle-bracket digest placeholder 会触发 zsh 重定向；现已替换为合法 64 位小写十六进制 digest，并由 `shlex.split` 与正则测试保护。
- 最终规格与质量双审均为 Ready，无 Critical、Important 或 Minor 遗留。

## 本轮已落实的精简与冗余审查

- 删除无 confinement root 时多余的 `.`、`..`、重复 `/` blanket rejection，同时保留逐级 `O_NOFOLLOW`；尾部 `/`、`/.`、`//`、`/..` 不会被错误归一化成 regular file。
- 将 JSON preflight 的返回值从四项计数收窄为唯一被调用方使用的 total token count；其他计数只在 scanner 内执行 ceiling。
- 删除两处测试中的兼容性 `getattr(..., default)` 和一处被直接调用覆盖的 `hasattr` 断言。
- 将 guidance 中重复的 compatibility/canonical 路由拆分为各自精确入口，并用单一 shared safety boundary 收敛重复声明。
- 将 shell 示例中的 angle-bracket placeholder 收窄为可直接解析的固定格式 digest，避免示例与测试各自维护不同语义。
- 最终 simplification/redundancy verdict：无实质生产冗余。路径解析、descriptor 打开、读取校验三层职责分离是必要的安全边界，不应合并；测试与文档不存在仍需删除的重复或无用逻辑。

## 后续工作

当前 adapter-contract 里程碑已完成。对应计划见 [adapter-contract implementation plan](../plans/2026-09-04-knowledge-distiller-adapter-contract.md)。未经用户另行批准，不 push、merge、安装、导出或发布。

下一实施计划已确定为 [core dual-source loop](../plans/2026-09-05-knowledge-distiller-core-dual-source-loop.md)：优先打通一个精确授权的 Lark 云文档与一个精确授权的 Codex 本地 CLI 会话，贯穿授权、只读 broker、版本化 normalization、确定性脱敏、evidence/provenance、critical-question policy 和非可执行 skill draft 编译。Claude Code 与 Trae 在该核心纵向闭环完成后复用同一契约补齐。

该计划的 Task 1 已完成：新增严格、不可变、纯验证的 `ContentGrant`、`AuthorityAttestation`、`AuthorizationContext`、`CanonicalDocument` 与 `SourceSnapshotManifest` 合约；session snapshot 复用既有 `EventGraphManifest` validator，没有复制或放宽事件图契约。snapshot ID 与 raw digest 均绑定原始 bytes 的 SHA-256；exact bounds 拒绝 Unicode control/format/line/paragraph separator；document 在 normalization 前执行 block、item、depth 与 canonical UTF-8 byte preflight。

Task 1 遵循 TDD，定向测试从 19 项增至 28 项；主进程最新完整回归为 194/194 通过。独立规格复审为 `SPEC PASS`，独立质量复审为 `READY`，Critical、Important、Minor 均无遗留；精简与冗余审查未发现应删除的生产逻辑。实现提交为 `0d95a95`、测试边界补强为 `2004a7a`、质量修复为 `9cd02bc`。

Task 2 已完成：新增同 writer/fencing 绑定的 private artifact transaction，把 `grants/`、`sources/`、`evidence/`、`provenance/`、`model/`、`decisions/` 与 `draft-skill/` 作为 opaque private snapshot 纳入既有 `PREPARE → immutable generation → COMMIT → atomic pointer` 协议。manifest 只保存路径、字节数、SHA-256、content class 与 role；私有内容和 replay key 不进入 journal。normal transition 会验证并继承私有快照，旧 generation 保持兼容。

Task 2 覆盖 owner-only 权限、descriptor-relative I/O、symlink/hardlink/sparse/special/xattr/moving-file 拒绝、资源上限、crash recovery、staging cleanup、exact replay、lineage/fencing、generation root 校验与取消时的 lock/FD 释放。主进程最新定向测试为 51/51，完整回归为 225/225，`compileall` 与 `git diff --check` 通过。独立规格复审为 `SPEC PASS`，质量复审为 `READY`，Critical、Important、Minor 均无遗留；冗余 callable assertions 已删除，未发现应进一步合并的生产 trust boundary。实现与修复提交为 `7dd9092`、`3c7bd2e`、`d451409`。

Task 3 已完成：将 `kd.py` 的显式路径、逐级 `O_NOFOLLOW`、regular/hardlink/sparse/special、权限/xattr、byte ceiling、moving-file 与 descriptor cleanup 逻辑提取为共享 `source_io`，旧 event-graph CLI 通过同一边界读取，不再维护重复 walker。新增 Lark 与 local-session broker policy：授权验证先于 credential/source I/O；Lark 只能构造固定 user-principal argv 和最小环境，通过注入的 trusted launcher 获取 opaque JSON transport bytes；本地 session 只允许从 byte 0 开始的 closed prefix，单一 descriptor 双次确认，stable append 排除在 snapshot 外，prefix 修改/截断/重排拒绝。

Task 3 的 broker snapshot 区分 active reader 与独立 attested `content_owner`，不会把技术读取者误标为内容所有者；broker 不调用 document/event adapter parser，也未添加任何 production runner 或 native support tuple。主进程最新定向测试为 115/115，完整回归为 256/256，`compileall` 与 `git diff --check` 通过。独立规格复审为 `SPEC PASS`，质量复审为 `READY`，Critical、Important、Minor 均无遗留；安全文件读取重复代码已移除，broker 的授权、credential、receipt 与 source trust layers 被确认是必要的独立边界。实现与修复提交为 `722b8b4`、`7286cd6`、`54ef068`。

Task 4 已完成：新增纯内存、无 I/O、资源有界且单次使用的 deterministic redactor。它在任何 semantic native normalization 或持久化之前处理 bounded source spans，覆盖私钥装甲、bearer/session token、cookie、credential assignment、邮箱、电话和显式 participant name/ID；placeholder 使用按类型分域的 HMAC-SHA256。每个结果都绑定外部 source binding、位置、owner context、run、grant/attestation 和 typed provenance chain，公开 validator 需要调用方提供完整 `expected_bindings`，可拒绝 reorder、duplicate、跨 source substitution 与篡改。只有 `actor_kind=user`、`actor_resolution=verified-owner` 且 actor ID 与 context owner 精确一致时才可作为 owner claim。

私钥装甲识别经多轮对抗评审后收敛为单次 O(n) ASCII-token DFA：精确 allowlisted envelope 才脱敏成功，fenced malformed/token split/token interruption 均 fail closed。最终精简修复删除 `unicodedata`、interruption flags 和重复分支，净删除 28 行状态复杂度。独立规格复审为 `SPEC PASS`，独立质量复审为 `READY`，Critical、Important、Minor 均无遗留；质量 reviewer 的 192 组 separator-position 组合探针全部通过。Task 4 定向边界测试为 60/60，严格完整回归为 287/287，`compileall` 与 `git diff --check` 通过。主要实现与收口提交为 `039abf0`、`1aeff8f`、`9b9afcd`、`4040cc3`、`ba2a8b2`、`4e8afa4`、`e4b51bb`、`af20c22`。

Task 5 已完成：在用户明确授权的单一 Wiki 当前页面范围内，验证 active user principal 与 Wiki creator/owner 一致，并固定 Docx revision `3365`。只使用 metadata GET 与 raw-content GET；未调用可能携带评论的 `docs +fetch`，也未遍历链接、嵌入、附件、子文档、评论或历史版本。真实正文只进入一次内存兼容性管道，没有显示、写入仓库或进入 fixture；仓库 fixture 为完全合成内容，只保留 observed schema shape、CLI version 和 revision 元数据。

Task 5 实现严格 envelope decoder，先解析 `ok/identity/data.content`，再只把 decoded content 交给 deterministic redactor，关闭 JSON `\u` escape 重建敏感内容的绕过。normalizer 仅接受认证后的 redaction result，绑定 owner、source snapshot、revision 与 digest-only native document locator，输出单一 unresolved/claim-ineligible block 和明确 formatting loss。typed `CanonicalDocument` 可直接进入 shared snapshot validator，并在任何 wire materialization 前执行与 wire payload 一致的 blocks/items 资源预检。

Task 5 独立规格终审为 `SPEC PASS`，独立质量终审为 `READY`，Critical、Important、Minor 均无遗留。质量评审明确复核了代码精简与冗余：删除单用途 record helper、不可达 span 检查、恒真/重复测试断言；新增 typed preflight 是必要且唯一的资源边界，没有剩余死代码、重复生产逻辑、重复测试或过度设计。实现与修复提交为 `e385ab9`、`b293cf7`、`89edbe9`。

Task 6 已完成主进程实现：新增纯内存、无 I/O 的 Codex `0.153.0 / rollout-jsonl-v1` 严格 JSONL parser/normalizer，要求首条唯一 `session_meta`、从零连续 ordinal、完整换行闭合 prefix、外部 owner/snapshot trust anchors 与认证 redaction binding。仅 `user.text` 成为 owner claim；assistant message、tool call/result 与 compaction 被映射到既有 canonical event graph。cross-agent native 记录无法显式绑定 message、recipient 与实际消费动作，因此与 fork、rollback/edit/retry、nested-agent lifecycle、unknown/mixed schema、非文本 output、未配对工具一并整份隔离，不做 best-effort record skip。

用户明确授权只读检查所有本地 Codex active/archived sessions 后，兼容性探针只输出聚合统计：初始快照为 83 个 JSONL、47,658 条合法 object record；35 个旧版本文件保持 blocked。最终 fail-closed 规则下，48 个 `0.153.0` 完整文件均因至少一个不支持的因果或内容投影而隔离；对精确、换行闭合前缀的 bounded probe 找到 1 个可支持的 root prefix（9 条 record，生成 1 个 canonical event）。因此 `supported` 精确指向可证明的闭合 prefix，而不是任意完整 session 文件。探针未输出或保留正文、路径、session ID；仓库 fixture 完全合成，只保存 bounded schema shape 和聚合元数据。这些数字是时点聚合观测，live session roots 可能变化。

Task 6 独立规格终审为 `SPEC PASS`，质量终审在发现两项 Important 后完成修复复审并给出 `READY`：cross-agent native shape 无法显式绑定 message、recipient 与实际消费动作，现全部 fail closed，不再臆造 sent/delivered/consumed；task lifecycle 增加 pinned scalar、非负范围、完成时序、唯一 start 与 `started_at` binding。代码精简审查删除了 60 余行不可安全到达的 cross-agent event/loss/追踪逻辑，以及 `item_completed` 在必然隔离前的冗余 schema 解析。最终无 Critical/Important/Minor 遗留。

Task 7 已完成实现：新增 dependency-injected `ingest-source` orchestration 与明确的 Lark/Codex `AcquisitionDispatch`。私有 request 只由 trusted host decoder 解释；未注入 runtime 时 CLI 在读取 request 文件前拒绝。每次命令只调用一个 source-specific acquisition handler，并在 source I/O 前完成 grant/attestation、generation、phase、source kind、dispatch 与 redaction key 校验。

同一 task 现在通过 `SOURCE_SNAPSHOTTED` 自环累计第一份来源，仍停留在 `INGEST`；第二个不同 source 成功后才以 `SOURCES_SNAPSHOTTED` 进入 `CAPABILITY_REVIEW`。第二次事务先验证并继承第一代私有 artifacts，Lark/Codex 各自使用独立 grant/source/evidence/provenance 路径。重复来源、错误 phase、stale generation 和不完整 artifact 分组均在新 source read 前拒绝；第二来源失败或 crash 不会替换已提交的第一代。

ingestion lease 覆盖 acquisition、normalization 与 commit，阻止并发重复读取。callback 只能获得 request/context/grant/attestation 的隔离重建副本，返回后所有 snapshot binding、redaction、provenance 与持久化都使用 acquisition 前固定的私有副本，关闭 `object.__setattr__` TOCTOU。broker snapshot 的 raw/source/selector digest、owner、revision/range、Codex prefix、product/version/schema 和嵌套 exact types 均重新验证；原始 source 不进入 journal 或 CLI，原子私有 generation 仅保存 normalized/redacted snapshot、native evidence、完整五段 derivation provenance 和按来源区分的授权记录。

Task 7 全部回归只使用 synthetic fixture；没有再次读取真实 Lark 文档或本地 Codex session。规格终审为 `SPEC PASS`，质量终审为 `READY`，Critical、Important、Minor 均无遗留。质量评审要求持续包含代码精简与冗余检查；pinning/clone、双层 runtime/library validation 与 lease 内 artifact 继承均被确认是必要安全边界，已抽取共享 dispatch/native-request helper 并使用固定路径映射。

### 产品里程碑

- Claude Code、Trae session adapters 的 content-authorized fixtures 与版本兼容性验证。
- DiscoveryGrant、MetadataGrant、ContentGrant、AuthorityAttestation 的持久化与 broker binding。
- Lark production credential runtime 与 Codex local-session identity runtime 的默认 wiring。
- provenance/evidence/claim/capability knowledge model。
- critical-question policy 与 skill compiler。
- sealed evaluator、ApprovalSubject、VersionApproval。
- export consent、purge、audit 与 stale-export repair。

以上剩余项尚未完成；其中任何真实来源读取都需要精确 ContentGrant/AuthorityAttestation，不会因本地 validator 或 redactor 通过而自动获得授权。

## 安全与范围记录

- 仅按明确授权读取过指定 Wiki 页面的当前 Docx metadata/raw content，用于内存兼容性验证；未读取评论、链接目标、嵌入、附件、子文档或历史版本。
- 真实 Lark 正文未显示、未写入仓库、未保存在测试 fixture；仓库内只有合成内容与 bounded schema 元数据。
- 已按用户明确授权对本地 Codex active/archived session roots 做只读聚合兼容性 probe；没有输出或保留正文、路径、session ID，未读取 credentials/config/cache，也未修改原生文件。
- 除上述单一已授权 Lark probe 与 Codex 聚合兼容性 probe 外，只使用官方资料、CLI `--help`/`--version` 和 synthetic fixtures。
- 未执行 push、merge、skill 安装、导出、发布或外部写入。
- 未进行更大范围的环境变更；观察到的 CLI 版本/skill notice 未触发升级或配置修改。
