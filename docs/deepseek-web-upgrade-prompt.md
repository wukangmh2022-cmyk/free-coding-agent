# DeepSeek Web Upgrade Prompt

当 DeepSeek 网页改版，导致 Provider 抓取、复制、发送、忙碌态判断或自动化测试失效时，优先使用这份提示词，让 AI 在当前仓库内做结构化升级，而不是临时打补丁。

## 相关资产

- 测试用例生成: [scripts/generate_deepseek_web_validation_cases.py](/Users/pippo/github-repo/free-coding-agent/scripts/generate_deepseek_web_validation_cases.py)
- 真网页回归脚本: [scripts/run_deepseek_copy_validation.py](/Users/pippo/github-repo/free-coding-agent/scripts/run_deepseek_copy_validation.py)
- 100 条网页 case: [tests/fixtures/deepseek-copy-validation/web-cases-100.json](/Users/pippo/github-repo/free-coding-agent/tests/fixtures/deepseek-copy-validation/web-cases-100.json)
- Provider 入口: [plugins/web_provider/backend/app/deepseek_local_provider.py](/Users/pippo/github-repo/free-coding-agent/plugins/web_provider/backend/app/deepseek_local_provider.py)
- DeepSeek 网页桥接: [plugins/web_provider/backend/packages/harness/deerflow/models/deepseek_web_bridge.py](/Users/pippo/github-repo/free-coding-agent/plugins/web_provider/backend/packages/harness/deerflow/models/deepseek_web_bridge.py)
- Agent 解析器: [agent_qt.py](/Users/pippo/github-repo/free-coding-agent/agent_qt.py)

## 升级目标

页面样式、DOM 结构、按钮位置、tooltip 文案、输入框结构、发送按钮结构、忙碌提示文案、代码块复制行为都可能变化。升级时优先保证：

1. 消息能真实提交，不是假发。
2. AI 输出结束后能尽快拿到完整复制结果。
3. 不要误抓用户消息复制按钮、代码块复制按钮或输入区按钮。
4. 不要把 busy 文本、免责声明、UI 噪声当成最终回复。
5. Markdown fence 到 EOF 的合法情况要继续兼容。
6. 升级后必须跑真实网页 case，而不是只做静态字符串测试。

## 升级流程

1. 先读当前实现和测试资产。
2. 打开真实 DeepSeek 页面，检查最新 DOM。
3. 重点确认这些结构有没有变化：
   - AI 消息底部 action row
   - 消息级复制按钮
   - 用户消息复制/修改按钮
   - 代码块复制/下载按钮
   - 输入区按钮
   - 忙碌提示文案
   - 停止生成 / 继续生成按钮
4. 优先修复 `deepseek_web_bridge.py` 的选择器、tooltip 校验和稳定等待逻辑。
5. 如果页面文案变了，同步修复 `deepseek_local_provider.py` 的 busy 检测。
6. 如果 Markdown/命令提取规则受影响，同步修复 `agent_qt.py`。
7. 先跑 1 条真实 case 验证提交和复制路径。
8. 再跑 10 条真实 case，边跑边人工阅读 `assistant-content-combined.txt`。
9. 人工判断时优先看这些问题：
   - 代码块或命令是否半截
   - 是否有异常插入的 UI 文本
   - 是否有免责声明混入
   - 是否复制错位到用户消息或代码块按钮
   - 是否出现 busy 文本被当成正式回复
10. 成功后再提交代码。

## 给 AI 的可直接使用提示词

```text
你正在维护 free-coding-agent 仓库里的 DeepSeek Web Provider。DeepSeek 网页可能已经改版，导致 DOM、按钮、tooltip、输入框、发送按钮、busy 文案或复制行为变化。

请在当前仓库内完成一次结构化升级，目标是恢复真实网页自动化，不要只做静态字符串修补。

必须先读取这些文件：
- scripts/run_deepseek_copy_validation.py
- scripts/generate_deepseek_web_validation_cases.py
- tests/fixtures/deepseek-copy-validation/web-cases-100.json
- plugins/web_provider/backend/app/deepseek_local_provider.py
- plugins/web_provider/backend/packages/harness/deerflow/models/deepseek_web_bridge.py
- agent_qt.py

执行要求：
- 先检查真实网页 DOM，再改代码。
- 优先区分 3 类复制按钮：AI 消息级复制、用户消息复制、代码块复制。
- 不要把 busy 文本、免责声明、用户消息按钮、输入区按钮误判成 AI 结果。
- EOF 结束的未闭合 Markdown fenced code block 仍然算合法代码块。
- 先跑 1 条真实 case，再跑 10 条真实 case。
- 10 条 case 跑测时，不要只看程序 verdict；必须人工阅读 assistant-content-combined.txt，判断有没有半截代码、UI 噪声、免责声明、复制错位。
- 如果发现问题，直接修复并复测到通过率稳定。

输出要求：
- 先说明你发现的页面结构变化。
- 再说明你改了哪些文件和为什么。
- 最后给出 10 条真实 case 的人工判断结果与失败 case 对话 URL。
```

## 维护建议

- 保留这套 case 资产，不要把它们当临时脚本删掉。
- 页面结构一旦改版，先用 10 条真网页 case 快速回归，再决定要不要扩到 100 条。
- 如果以后支持多个网页 Provider，这份流程可以复制成通用模板。
