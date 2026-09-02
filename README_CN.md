<p align="center">
  <strong>MiniCPM Network Doctor</strong>
</p>

<p align="center">
  让一个 10 亿参数的小模型 + 七个只读工具，在你自己的机器上调查异常 DNS 映射、端口不通、
  证书错误等网络问题。
</p>

<p align="center">
  <img src="https://img.shields.io/github/actions/workflow/status/CacinieP/minicpm5-network-doctor/ci.yml?branch=main&style=flat-square" alt="CI">
  <img src="https://img.shields.io/github/v/release/CacinieP/minicpm5-network-doctor?style=flat-square&color=blue" alt="Release">
  <img src="https://img.shields.io/badge/MiniCPM5-1B-blue" alt="MiniCPM5-1B">
  <img src="https://img.shields.io/badge/Python-3.10%2B-green" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tests-105%20passing-success" alt="Tests">
  <img src="https://img.shields.io/badge/coverage-88%25-success" alt="Coverage">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="MIT License">
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="#看它工作">示例</a> ·
  <a href="#架构">架构</a> ·
  <a href="#安全边界">安全</a> ·
  <a href="./CHANGELOG.md">更新记录</a> ·
  <a href="./docs/benchmark-plan.md">评测计划</a> ·
  <a href="#已知局限">局限</a> ·
  <a href="#开发">开发</a>
</p>

---

MiniCPM Network Doctor 将本地运行的
[MiniCPM5-1B](https://github.com/OpenBMB/MiniCPM) 模型与一组精简的只读网络工具结合起来。
模型判断应该执行哪项检查；确定性的 Python 代码负责检查并返回证据。

项目刻意保持聚焦：在不给模型任意 Shell 权限的前提下，诊断开发者遇到的 DNS、TCP、
HTTP、TLS、本地端口、代理环境和包下载问题。

## 看它工作

一次典型的运行——开发者反馈 `npm install` 超时。Agent 解析主机名，识别出由代理管理的
fake-IP 映射，并明确说明现有证据能够和不能证明什么：

```text
$ minicpm-network-doctor \
    "npm install 从 registry.example.org 下载时超时，请检查最可能的网络原因"

Diagnosis: registry.example.org 被解析为本地代理管理的 fake-IP 映射，说明 DNS 处于代理
管理路径。与该映射地址的 TCP 连接成功，因此现有证据不能证明该映射或代理导致了 npm 超时。

Evidence:
- resolve_dns (registry.example.org) -> address=198.18.0.42, classification=fake-ip (198.18.0.0/15)
  观察提示："proxy-managed DNS path; mapping alone does not establish a connectivity failure"
- test_tcp (registry.example.org:443) -> ok, peer=198.18.0.42, 3.1ms

Recommended action: 只为 registry.example.org 临时增加直连/绕过规则并重试一次，作为
受控对照。回滚方式：删除这一条临时规则。

Verification: curl -v --connect-timeout 10 https://registry.example.org/
```

Evidence 段中的数值全部由只读工具产生并原样呈现。模型的诊断仍可能存在不确定性，不得把
特殊用途地址分类直接升级为未经证实的根因结论。需要完整工具轨迹时，加上 `--json`。

## 为什么做这个项目

- **本地优先**——模型准备到本地后，诊断不再依赖外部模型 API。
- **先证据、后建议**——模型提出原因之前必须先看到真实工具结果。
- **只读执行**——运行时只观察网络状态，不修改系统配置。
- **适合小模型**——代码控制调用循环、参数结构、步数限制和重复调用处理。
- **中英双语**——模型会按用户使用的语言回答。

## 0.3 版更新

- **运行时目标限域**——只有用户原始描述中明确出现的主机才能被网络工具访问，安全边界不再
  依赖模型是否遵守提示词。
- **没有证据就没有诊断**——后端忽略强制工具调用时会被纠正并重试，纯文本猜测不会被当成完成
  的诊断。
- **更准确的 HTTP 检查**——服务以 405/501 拒绝 `HEAD` 时，自动改用 Range `GET`，且不读取
  响应正文；跳转到未报告主机的重定向会被拦截。
- **本地覆盖检测**——新增 `inspect_hosts_file`，只返回与报告主机匹配的 hosts 条目，不暴露其他
  本地映射。
- **更适合运维与自动化**——新增 `--check-server`、`--progress`、`--max-tool-calls`、稳定 JSON
  状态和模型列表探测。
- **本地端点连接可靠**——回环模型服务默认绕过环境/系统代理，远程端点仍可使用代理；可通过
  `--use-model-proxy` 和 `--no-model-proxy` 显式覆盖。
- **适配后端的思考控制**——请求同时发送 SGLang 的模板开关和当前 Ollama 支持的 OpenAI 兼容
  `reasoning_effort` 字段。

## 架构

```text
开发者症状或错误
       │
       ▼
通过 SGLang 运行 MiniCPM5-1B
       │ OpenAI 兼容 tool_calls
       ▼
白名单 Python 工具
DNS · TCP · HTTP · TLS · 代理环境 · hosts 文件 · 系统信息
       │
       ▼
证据 → 诊断 → 一个可逆建议 → 验证
```

推荐使用 SGLang 作为后端，因为 MiniCPM5 官方的 `minicpm5` 解析器可以把模型生成的
XML 风格调用转换为标准 OpenAI 兼容 `tool_calls`。

## 只读工具

| 工具 | 用途 |
|------|------|
| `resolve_dns` | 解析一个主机名并报告 IPv4/IPv6 结果 |
| `test_tcp` | 尝试连接指定主机和端口一次 |
| `test_http` | 发送 HEAD，拦截跨主机跳转；遇到 405/501 时使用不读取正文的 Range GET |
| `inspect_tls` | 验证 TLS 并概括对端证书 |
| `inspect_proxy_environment` | 读取环境及平台实际生效的代理并隐藏凭据 |
| `inspect_hosts_file` | 检查一个报告主机是否被本地 hosts 文件覆盖 |
| `system_network_context` | 报告操作系统信息和脱敏后的代理配置 |

项目不提供任意命令执行或端口扫描工具。

## Agent 行为

四层运行时控制让小模型保持诚实和鲁棒：

- **首轮强制取证。** 第一轮模型请求以 `tool_choice="required"` 发送，模型必须先调用一个诊断工具才能作答。如果后端拒绝 `required`，会自动退回 `auto` 重试；如果后端接受却无视要求，其无证据回答会被丢弃并再次要求取证。
- **目标作用域强制执行。** 运行时从原始报告中提取主机、IP 和 URL；任何超出白名单的网络调用都会收到 `target_out_of_scope`，不会真正执行。
- **工作量有界。** 每轮最多接受四次调用，整次诊断默认最多实际执行 12 次工具调用（最高可配置为 24）。
- **不收敛时优雅返回。** 如果模型连续三轮调用同一个工具仍未结束，或者用尽了轮次上限，运行时会停止并返回**结构化的部分诊断**——仍然输出四个段落，但 Diagnosis 段会注明模型未收敛，Evidence 段列出已收集的全部结果。因此 CLI 会以退出码 0 返回已收集的证据，而不是直接报错。硬性的 `StepLimitError`（退出码 1）仅保留给"未收集到任何证据"的罕见情况。

## 快速开始

> **分发状态：** 正式版本通过 GitHub Releases 分发。本项目目前未发布到 PyPI；请按下文从仓库安装。

这是一个独立的社区项目，与 OpenBMB 不存在隶属关系，也未获得其官方背书。

### 1. 启动支持工具调用解析的 MiniCPM5

MiniCPM 官方部署 Skill 当前建议从 `main` 安装 SGLang，以获得 MiniCPM5 解析器：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "git+https://github.com/sgl-project/sglang.git@main#subdirectory=python"

python -m sglang.launch_server \
  --model-path openbmb/MiniCPM5-1B \
  --served-model-name openbmb/MiniCPM5-1B \
  --port 30000 \
  --tool-call-parser minicpm5
```

这条 SGLang 路线面向 NVIDIA GPU 环境。其他 OpenAI 兼容运行时也可以接入，但需要把
MiniCPM5 调用作为原生 `tool_calls` 返回。

<details>
<summary><b>替代后端：Ollama（macOS / 无 NVIDIA GPU）</b></summary>

SGLang 只发布 Linux wheel，因此在 macOS（Apple Silicon）或任何没有 NVIDIA GPU 的机器上，
可以使用 [Ollama](https://ollama.com) 来提供同样的模型并暴露 OpenAI 兼容接口。工具调用依赖
`minicpm5` 聊天模板；建议使用 F16 GGUF 以获得稳定的工具调用。

```bash
# 拉取 F16 GGUF（推荐，工具调用最稳定）
ollama pull hf.co/openbmb/MiniCPM5-1B-GGUF:F16

# OpenAI 兼容端点为 http://127.0.0.1:11434/v1
```

然后把 Network Doctor 指向它：

```bash
minicpm-network-doctor \
  --base-url http://127.0.0.1:11434/v1 \
  --api-key ollama \
  --model hf.co/openbmb/MiniCPM5-1B-GGUF:F16 \
  "npm install 从 registry.npmjs.org 下载时超时"
```

> **Ollama 量化提示：** 高度量化的版本（如 `Q4_K_M`）虽然能产生工具调用，但可能不稳定。
> 诊断循环推荐使用 `F16`。

</details>

### 2. 安装 Network Doctor

```bash
git clone https://github.com/CacinieP/minicpm5-network-doctor.git
cd minicpm5-network-doctor

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. 开始诊断

```bash
minicpm-network-doctor \
  "npm install 从 registry.npmjs.org 下载时超时，请检查最可能的网络原因"
```

复杂问题可以开启 MiniCPM5 思考模式：

```bash
minicpm-network-doctor --thinking \
  "浏览器能访问 registry.npmjs.org，但包管理器报告证书错误"
```

以 JSON 输出完整工具轨迹：

```bash
minicpm-network-doctor --json "检查 https://example.com 返回错误的原因"
```

在进入较慢的模型推理前检查服务，或实时观察取证进度：

```bash
minicpm-network-doctor --check-server
minicpm-network-doctor --progress "检查 https://example.com 的 TLS 错误"
```

Ollama 一类运行在 `127.0.0.1` 的回环端点默认直连，避免桌面代理截获模型请求；远程端点仍会
沿用代理设置。可用 `--use-model-proxy` 或 `--no-model-proxy` 覆盖自动判断。模型 API 默认不
重试，以免本地慢请求成倍等待；远程端点确有需要时再设置 `--max-retries`。

`--json` 稳定输出 `schema_version`、`status`（`complete`、`partial` 或 `error`）、`targets`、
`warnings` 和完整 `tool_events`。进度只写入 stderr，因此可与 stdout 上的 JSON 安全组合。

## 配置

| 环境变量 | 默认值 |
|----------|--------|
| `MINICPM_BASE_URL` | `http://127.0.0.1:30000/v1` |
| `MINICPM_MODEL` | `openbmb/MiniCPM5-1B` |
| `MINICPM_API_KEY` | `not-needed` |

也可以通过对应的 CLI 参数配置：

```bash
minicpm-network-doctor --help
```

## Agent Skill

仓库包含一个兼容 Codex/Claude Code 风格的 Agent Skill：

```text
skills/minicpm-network-doctor/
├── SKILL.md
└── agents/
    └── openai.yaml
```

在 Codex 中，可以将这个目录复制到个人技能目录：

```bash
cp -R skills/minicpm-network-doctor ~/.codex/skills/
```

该 Skill 会引导 Agent 收集准确症状、调用本地 Doctor、保留工具证据，并把诊断和验证
步骤分开呈现。

## 安全边界

维护范围与漏洞报告方式见 [SECURITY.md](SECURITY.md)。

- 只有声明的七个工具可以执行。
- 网络工具在运行时被严格限制为原始问题中明确出现的目标。
- 主机、端口、URL、超时和工具参数都会经过校验。
- 诊断 URL 不能包含凭据。
- HTTP 检查不会跟随跳转到原始报告以外主机的重定向。
- 模型预检不会把 API key 转发到其他重定向源；回环端点默认绕过代理，除非用户明确开启。
- 代理凭据会在结果进入模型前被隐藏。
- 重复工具调用会被拦截。
- 单轮及整次诊断的工具预算都有上限，不支持大范围扫描。
- 系统提示明确禁止关闭证书验证。
- 配置修改只作为建议输出，运行时不会自动应用。

## 已知局限

MiniCPM5-1B 是一个 10 亿参数的小模型。运行时添加了多重防护来弥补，但部分失败模式属于模型
固有缺陷，无法在代码层面彻底解决：

- **工具调用并非总可靠。** 部分后端（尤其是 Ollama）会接受 `tool_choice="required"` 却偶发返回
  纯文本。运行时会拒绝这种无证据回答并重试；如果后端持续不兼容，最终会明确报出“没有工具证据”，
  而不是给出猜测式诊断。
- **量化会降低工具调用稳定性。** 高度量化的 GGUF（`Q4_K_M`）会生成更长的思维链，更容易漂移或
  被截断，不如 `F16` 稳定。诊断循环推荐使用 `F16`。`--thinking` 对疑难场景有帮助，但也会把更多
  token 预算花在推理上，本身也可能截断答案。
- **fake-IP 映射不是根因。** 它只说明 DNS 处于代理管理路径，该路径也可能完全正常。没有受控的
  直连/绕过对照时，Agent 必须把代理是否导致故障标记为未确认。
- **无写权限。** Agent 只做观察，不能刷新 DNS、切换代理、重启服务或应用任何修复。给出的建议都
  是需要用户自己执行的可逆操作。
- **无深度报文或路由检查。** 工具覆盖 DNS、TCP、HTTP、TLS 和代理环境变量。没有 `traceroute`、
  `tcpdump`、证书链锁定检查，也无法检查被状态防火墙静默丢弃的连接。
- **单主机、症状驱动。** 每次诊断只针对用户报告的主机和症状。不支持批量或持续监控，也刻意不
  支持大范围扫描。
- **服务端随机性。** 同一温度下，不同运行的诊断质量会有波动。若某次运行停滞，运行时会返回
  结构化的部分诊断（见 [Agent 行为](#agent-行为)），已收集的证据不会丢失；可用 `--thinking`
  重试或提供更具体的症状以获得更精准的结果。
- **后端差异。** SGLang（Linux / NVIDIA GPU）是参考后端。当前 Ollama 可识别 OpenAI 兼容的
  `reasoning_effort`，SGLang 使用 `chat_template_kwargs.enable_thinking`；旧版或其他运行时仍可能
  忽略其中一个或全部控制字段。

当模型表现不稳定时，最有效的升级是换用更强的模型（例如 MiniCPM5 4B/8B）并搭配能可靠解析
工具调用的后端，而非继续增加循环层面的防护。

## 项目结构

```text
minicpm-network-doctor/
├── src/minicpm_network_doctor/
│   ├── agent.py               # 工具调用循环
│   ├── cli.py                 # 命令行界面
│   ├── scope.py               # 运行时目标提取与限域
│   ├── system_prompt.md       # 面向小模型的诊断策略
│   └── tools.py               # 只读诊断工具
├── skills/
│   └── minicpm-network-doctor/
├── docs/benchmark-plan.md     # 规划中的真实模型端到端评测
├── tests/
├── .github/workflows/ci.yml
├── pyproject.toml
└── README.md
```

## 开发

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

ruff check .
ruff format --check .
pytest --cov=minicpm_network_doctor
python /path/to/skill-creator/scripts/quick_validate.py \
  skills/minicpm-network-doctor
```

单元测试使用假的 OpenAI 兼容客户端，不会下载模型，也不需要 GPU。一次人工端到端运行只能
证明传输链路和工具循环能工作，不能证明诊断准确率或多次运行稳定性。只有完成
[真实模型评测计划](docs/benchmark-plan.md)后，才应对外声明可靠性指标。

## 致谢

- [OpenBMB/MiniCPM](https://github.com/OpenBMB/MiniCPM)：MiniCPM5-1B 及其工具调用部署指南。
- [CacinieP/network-troubleshoot-skill](https://github.com/CacinieP/network-troubleshoot-skill)：
  本项目所采用的“先证据、后诊断”网络排障工作流来源。

## 许可证

[MIT](LICENSE)
