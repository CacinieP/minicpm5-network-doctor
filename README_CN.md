<p align="center">
  <strong>MiniCPM Network Doctor</strong>
</p>

<p align="center">
  让一个 10 亿参数的小模型 + 六个只读工具，在你自己的机器上诊断 DNS 劫持、端口不通、
  证书错误等真实网络问题。
</p>

<p align="center">
  <img src="https://img.shields.io/github/actions/workflow/status/CacinieP/minicpm5-network-doctor/ci.yml?branch=main&style=flat-square" alt="CI">
  <img src="https://img.shields.io/github/v/release/CacinieP/minicpm5-network-doctor?style=flat-square&color=blue" alt="Release">
  <img src="https://img.shields.io/badge/MiniCPM5-1B-blue" alt="MiniCPM5-1B">
  <img src="https://img.shields.io/badge/Python-3.10%2B-green" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="MIT License">
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="#看它工作">示例</a> ·
  <a href="#架构">架构</a> ·
  <a href="#安全边界">安全</a> ·
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

一次典型的运行——开发者反馈 `npm install` 超时。Agent 解析主机名，发现地址落入了本地代理
注入的 fake-ip 段，从而精准定位原因：

```text
$ minicpm-network-doctor \
    "npm install 从 registry.example.org 下载时超时，请检查最可能的网络原因"

Diagnosis: registry.example.org 的 DNS 正被一个运行在 fake-ip 模式的本地代理劫持。
主机名被解析进 198.18.0.0/15 保留段，而不是真实服务器地址，因此包请求被拦截或黑洞。

Evidence:
- resolve_dns (registry.example.org) -> address=198.18.0.42, classification=fake-ip (198.18.0.0/15)
  观察提示："commonly injected by Clash/Mihomo fake-ip DNS hijacking"
- test_tcp (registry.example.org:443) -> ok, peer=198.18.0.42, 3.1ms

Recommended action: 把 registry.example.org 加入代理的直连/绕过列表（或对该域名把代理
从 fake-ip 切换到 redir-host 模式）。回滚方式：删除该条目即可。

Verification: curl -v https://registry.example.org/  # 应到达真实 CDN IP，而非 198.x
```

上面每一个数值都由只读工具产生并原样呈现——模型绝不会伪造它没有执行过的检查。需要完整
工具轨迹时，加上 `--json`。

## 为什么做这个项目

- **本地优先**——模型准备到本地后，诊断不再依赖外部模型 API。
- **先证据、后建议**——模型提出原因之前必须先看到真实工具结果。
- **只读执行**——运行时只观察网络状态，不修改系统配置。
- **适合小模型**——代码控制调用循环、参数结构、步数限制和重复调用处理。
- **中英双语**——模型会按用户使用的语言回答。

## 架构

```text
开发者症状或错误
       │
       ▼
通过 SGLang 运行 MiniCPM5-1B
       │ OpenAI 兼容 tool_calls
       ▼
白名单 Python 工具
DNS · TCP · HTTP · TLS · 代理环境 · 系统信息
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
| `test_http` | 发送一次 HTTP/HTTPS HEAD 请求 |
| `inspect_tls` | 验证 TLS 并概括对端证书 |
| `inspect_proxy_environment` | 读取代理环境变量并隐藏凭据 |
| `system_network_context` | 报告操作系统信息和脱敏后的代理配置 |

项目不提供任意命令执行或端口扫描工具。

## Agent 行为

两层循环控制让小模型保持诚实和鲁棒：

- **首轮强制取证。** 第一轮模型请求以 `tool_choice="required"` 发送，模型必须先调用一个诊断工具才能作答。这能避免小模型常见的"凭印象回答"（例如声称"我没有这个工具"）而不去实际检查。如果后端拒绝 `required`，会自动退回 `auto` 重试一次。
- **不收敛时优雅返回。** 如果模型连续三轮调用同一个工具仍未结束，或者用尽了轮次上限，运行时会停止并返回**结构化的部分诊断**——仍然输出四个段落，但 Diagnosis 段会注明模型未收敛，Evidence 段列出已收集的全部结果。因此 CLI 会以退出码 0 返回已收集的证据，而不是直接报错。硬性的 `StepLimitError`（退出码 1）仅保留给"未收集到任何证据"的罕见情况。

## 快速开始

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
  "浏览器可以打开 HTTPS 页面，但包管理器报告证书错误"
```

以 JSON 输出完整工具轨迹：

```bash
minicpm-network-doctor --json "检查 https://example.com 返回错误的原因"
```

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

- 只有声明的六个工具可以执行。
- 主机、端口、URL、超时和工具参数都会经过校验。
- 诊断 URL 不能包含凭据。
- 代理凭据会在结果进入模型前被隐藏。
- 重复工具调用会被拦截。
- 超时有明确上限，不支持大范围扫描。
- 系统提示明确禁止关闭证书验证。
- 配置修改只作为建议输出，运行时不会自动应用。

## 已知局限

MiniCPM5-1B 是一个 10 亿参数的小模型。运行时添加了多重防护来弥补，但部分失败模式属于模型
固有缺陷，无法在代码层面彻底解决：

- **工具调用并非总可靠。** 第一轮强制了 `tool_choice="required"`，但部分后端（尤其是
  Ollama）会接受该值却偶发返回不带 `tool_calls` 的纯文本。此时模型凭印象作答而非实际检查。
  这在 `Q4_K_M` 下影响约少数比例的运行，不更换更强模型无法根治。
- **量化会降低工具调用稳定性。** 高度量化的 GGUF（`Q4_K_M`）会生成更长的思维链，更容易漂移或
  被截断，不如 `F16` 稳定。诊断循环推荐使用 `F16`。`--thinking` 对疑难场景有帮助，但也会把更多
  token 预算花在推理上，本身也可能截断答案。
- **无写权限。** Agent 只做观察，不能刷新 DNS、切换代理、重启服务或应用任何修复。给出的建议都
  是需要用户自己执行的可逆操作。
- **无深度报文或路由检查。** 工具覆盖 DNS、TCP、HTTP、TLS 和代理环境变量。没有 `traceroute`、
  `tcpdump`、证书链锁定检查，也无法检查被状态防火墙静默丢弃的连接。
- **单主机、症状驱动。** 每次诊断只针对用户报告的主机和症状。不支持批量或持续监控，也刻意不
  支持大范围扫描。
- **服务端随机性。** 同一温度下，不同运行的诊断质量会有波动。若某次运行停滞，运行时会返回
  结构化的部分诊断（见 [Agent 行为](#agent-行为)），已收集的证据不会丢失；可用 `--thinking`
  重试或提供更具体的症状以获得更精准的结果。
- **后端差异。** SGLang（Linux / NVIDIA GPU）是参考后端。Ollama 在 macOS 上可用，但不透传
  `chat_template_kwargs.enable_thinking` 字段，因此思考模式取决于服务器自身的模板默认值。

当模型表现不稳定时，最有效的升级是换用更强的模型（例如 MiniCPM5 4B/8B）并搭配能可靠解析
工具调用的后端，而非继续增加循环层面的防护。

## 项目结构

```text
minicpm-network-doctor/
├── src/minicpm_network_doctor/
│   ├── agent.py               # 工具调用循环
│   ├── cli.py                 # 命令行界面
│   ├── system_prompt.md       # 面向小模型的诊断策略
│   └── tools.py               # 只读诊断工具
├── skills/
│   └── minicpm-network-doctor/
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
pytest
python /path/to/skill-creator/scripts/quick_validate.py \
  skills/minicpm-network-doctor
```

单元测试使用假的 OpenAI 兼容客户端，不会下载模型，也不需要 GPU。真实的端到端诊断
需要一个正在运行且工具调用解析正常的 MiniCPM5 接口。

## 致谢

- [OpenBMB/MiniCPM](https://github.com/OpenBMB/MiniCPM)：MiniCPM5-1B 及其工具调用部署指南。
- [CacinieP/network-troubleshoot-skill](https://github.com/CacinieP/network-troubleshoot-skill)：
  本项目所采用的“先证据、后诊断”网络排障工作流来源。

## 许可证

[MIT](LICENSE)
