# Knowledge Distiller 实现状态

更新时间：2026-09-05（Asia/Shanghai）

## 当前结论

`knowledge-distiller` 已完成设计、确定性基础设施、持久化 checkpoint、适配器兼容性门禁、canonical event graph validator、第一版本地验证 CLI、输入边界安全加固和 adapter guidance integration。Task 3 与 Task 4 均已通过规格与质量双审，结论均为 Ready；Task 5 最终独立评审同样为 Ready，Critical、Important、Minor 均无遗留。

主进程与最终独立 reviewer 均运行完整测试套件，166/166 通过；`compileall`、`git diff --check` 和 repository cache 检查全部通过。完整验证命令为：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning \
  -m unittest discover -s tests -v
```

四个原生来源适配器仍全部为 `blocked`，没有把任何产品或 native schema 宣称为已支持。

## 分支与提交

- 当前分支：`feat/implement_knowledge_distiller`
- 最新实现提交：`3f48383 fix: reject lone json surrogates`
- 未 push、未 merge、未安装、未导出、未发布
- 相对 `origin/main` 有 13 个既有实现/设计提交，另加本次 completion-record 提交

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
- Lark、Codex、Claude Code、Trae 四行 readiness 均严格为 `blocked`。
- 唯一允许的版本元组为 synthetic fixture：`synthetic / 1.0.0 / synthetic-1 / synthetic-1`。
- Lark 仅确认显式 `--as user|bot` 与 revision 参数；未确认稳定 principal-bound 输出 schema。
- Codex 仅确认 resume/fork 与运行时 JSON event 能力；未确认稳定的历史 transcript 导出 schema。
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
- 明确成功 validation 只证明 synthetic tuple/canonical contract conformance，不授权 source read 或 native tool invocation，也不让任何 native adapter 变为 supported。
- Lark、Codex、Claude Code、Trae 仍全部为 blocked/unimplemented。
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

该计划目前仅完成设计与任务拆分，所有 checkbox 均未开始；尚未读取真实来源，也尚未把任何 native adapter 从 `blocked` 改为 `supported`。Lark 与 Codex 的真实 fixture 捕获分别设有精确 selector、revision/range、ContentGrant 与 AuthorityAttestation 审批门。计划的最终评审包含独立的代码精简与冗余检查。

### 产品里程碑

- 四个 native adapters 的 content-authorized redacted fixtures 与版本兼容性验证。
- DiscoveryGrant、MetadataGrant、ContentGrant、AuthorityAttestation 的持久化与 broker binding。
- Lark trusted request broker 和本地 session descriptor broker。
- 隔离 parser、deterministic redaction 与 ingestion persistence。
- provenance/evidence/claim/capability knowledge model。
- critical-question policy 与 skill compiler。
- sealed evaluator、ApprovalSubject、VersionApproval。
- export consent、purge、audit 与 stale-export repair。

这些后续项均未开始实现；其中任何真实来源读取都需要精确 ContentGrant/AuthorityAttestation，不会因本地 validator 通过而自动获得授权。

## 安全与范围记录

- 尚未读取任何真实 Lark 云文档。
- 尚未读取任何真实 Codex、Claude Code 或 Trae session 内容、索引或目录。
- 研究阶段只使用官方公开资料、CLI `--help`/`--version` 和 synthetic fixtures。
- 未执行 push、merge、skill 安装、导出、发布或外部写入。
- 未进行更大范围的环境变更；观察到的 CLI 版本/skill notice 未触发升级或配置修改。
