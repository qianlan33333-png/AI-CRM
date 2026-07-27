# image-library-curator

AI-CRM 图片素材库的 AI 编排器，让 Claude Code 通过 MCP 协议读写 CRM 的图片素材库，做自动打标 + 聊天上下文推荐。

## 它做什么

CRM 服务（AI-CRM 主仓库）只负责**存图 + 存元数据 + 暴露 MCP 工具**，不调任何 LLM。本 Skill 在 Claude Code 会话里用 vision 能力理解图片、写元数据、做推荐选图，所有 AI 决策都在 Skill 侧。

具体三条工作流：

- **批量打标**：素材库里历史堆积的未打标图，一次拉 20 张让 Claude 看图后写 description / tags / category
- **上传时打标**：用户贴图给 Claude Code，Skill 自动分析 + 直传 CRM
- **聊天推荐**：用户给一段客户对话，Skill 从素材库找几张匹配的图回应

详见 [SKILL.md](SKILL.md)。

## 安装

### 1. 把 Skill 装到 Claude Code

把整个 `image-library-curator/` 目录拷到 `~/.claude/skills/`：

```bash
cp -r skills/image-library-curator ~/.claude/skills/
```

或者建软链：

```bash
ln -s "$(pwd)/skills/image-library-curator" ~/.claude/skills/image-library-curator
```

确认 `claude` CLI 能识别：

```bash
claude --list-skills 2>&1 | grep image-library
```

### 2. 配置 CRM MCP 端点

CRM 主仓库的 `/mcp` endpoint 已经把 `image_library_*` 5 个工具暴露出去了（PR-C）。你需要在 Claude Code 的 `.mcp.json`（项目级或用户级）里加一段：

```json
{
  "mcpServers": {
    "ai-crm": {
      "type": "http",
      "url": "https://<crm-host>/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_BEARER_TOKEN}"
      }
    }
  }
}
```

替换：
- `url`：CRM 服务的 `/mcp` 地址。本地开发可以使用本机端口；线上地址从私有 ops handoff 获取，不在主仓文档中记录。
- `MCP_BEARER_TOKEN`：CRM 服务器 `.env` 里配置的 `MCP_BEARER_TOKEN` 值

### 3. 验证连通

启动一个 Claude Code 会话，让它跑：

```
调用 image_library_facets 看看
```

如果返回 `{"ok": true, "categories": [...], "tags": [...]}` 就通了。

如果返回 `401 missing internal token` / `invalid internal token`：
- 检查 `Authorization: Bearer ...` 头部
- 检查 token 字符串是否完整（CRM 的 `MCP_BEARER_TOKEN` env 跟你 .mcp.json 里的一致）

如果工具列表里没有 `image_library_*`：
- CRM 服务版本太老，没合并 [PR-C](https://github.com/qianlan333/AI-CRM/pull/238)
- 或 url 配错（不是 /mcp endpoint）

## 触发 Skill

在 Claude Code 会话里说类似的话，会自动激活本 Skill：

| 场景 | 示例输入 |
|---|---|
| 批量打标 | "给图片素材库里没打标的图自动分析一下" |
| 单图打标 | "图片库里 #234 这张帮我加一下标签和描述" |
| 上传 + 打标 | （贴图）+"上传到素材库，AI 直接分类" |
| 聊天推荐 | "客户在质疑产品效果，从素材库找 3 张合适的好评截图" |

## 安全 / 边界

- **写操作不需要 approval token**：图片库不涉及客户外发或 production 表写入，AI 自由打标的成本可控
- **`overwrite=false` 是默认**：批量打标永远保护人工已编辑的字段，除非用户明确说"重写"
- **不复述敏感原文**：图里的客户姓名 / 手机号即使 OCR 能识别，description 也不应该复述原文（脱敏概括）
- **失败要透**：不重试到用户感知不到

## 常见问题

**Q：CRM 服务器跑在内网，怎么用？**

A：按私有 ops handoff 获取当前访问方式；主仓文档不记录具体 SSH alias、host 或转发命令。`.mcp.json` 里 url 可以配置为本机转发后的 `/mcp` endpoint。

**Q：能不能多人共享一个 token？**

A：能，但所有调用都打到同一个审计入口（CRM mcp logger 没区分调用方）。如果团队多人用建议未来 PR-E 加多 token + scope 区分（不在本期范围）。

**Q：vision 分析准吗？需不需要人工 review？**

A：默认 `overwrite=false` 已经在保护人工编辑过的字段。但 AI 写的 tags / category 还是建议每周抽样 review，重点看：(1) 是不是造了一堆碎片化新词（应该复用 facets 池）；(2) 描述里有没有混入图片里的客户敏感原文。

**Q：能不能扩展工作流？**

A：可以。在 `references/` 加新 markdown，在 `SKILL.md` 顶部"何时触发"和"三条工作流"区块加一段。如果是通用 CRM 能力，先在主仓库当前 Next 边界中扩展：图片素材读写在 `aicrm_next/engagement/media_library/`，MCP 暴露在 `aicrm_next/channels/integration_gateway/`。不要新增或恢复已退场的 `wecom_ability_service/` 路径。

## 相关

- 后端：[AI-CRM 主仓库 #235](https://github.com/qianlan333/AI-CRM/pull/235) — 数据字段
- 前端：[AI-CRM 主仓库 #236](https://github.com/qianlan333/AI-CRM/pull/236) — 管理页 UI
- MCP：[AI-CRM 主仓库 #238](https://github.com/qianlan333/AI-CRM/pull/238) — MCP 工具
- Skill 自身：本目录
