#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "deepseek-copy-validation" / "web-cases-100.json"

PROJECT = "/Users/pippo/Desktop/my-project"
PYTHON = "/Users/pippo/.agent_qt/runtime/python/bin/python"

CATEGORIES: list[tuple[str, list[str]]] = [
    (
        "web-html-css",
        [
            "创建一个单文件 HTML landing page，主题是海边咖啡馆，要求有响应式 CSS、至少 3 个 section，用占位符协议写入 sea-cafe.html，并用 wc -l 验证。",
            "创建一个移动端优先的任务看板页面 todo-board.html，包含 CSS Grid、状态列、暗色浅色变量，用占位符协议写文件并 head -5 验证。",
            "生成一个产品价格页 pricing.html，包含三档套餐、CSS 变量、hover 效果和无障碍按钮，用占位符协议写入并 tail -5 验证。",
            "生成一个信息科公告页 notice.html，要求中文排版、表格、打印样式 @media print，用占位符协议写入并 grep title 验证。",
            "生成一个个人作品集首页 portfolio.html，包含 hero、项目卡片、联系区、CSS 动画，用占位符协议写入并 wc -l 验证。",
            "生成一个数据仪表盘 dashboard.html，包含 SVG 背景、CSS chart bars、响应式布局，用占位符协议写入并 grep dashboard 验证。",
            "生成一个登录页 login.html，要求不要外链资源，CSS focus 状态明显，用占位符协议写入并 head -8 验证。",
            "生成一个餐厅菜单页 menu.html，包含中文菜单、价格、标签、移动端适配，用占位符协议写入并 wc -l 验证。",
            "生成一个活动报名页 event.html，包含表单、CSS 校验提示样式、FAQ，用占位符协议写入并 grep form 验证。",
            "生成一个帮助中心页面 help-center.html，包含搜索框视觉、分类卡、CSS only accordion，用占位符协议写入并 tail -8 验证。",
        ],
    ),
    (
        "svg-docs",
        [
            "生成一个 SVG 图标文件 logo-mark.svg，内容是几何猫头鹰，用占位符协议写入 SVG，并用 file 或 wc -c 验证。",
            "生成一个 SVG 流程图 workflow.svg，展示 Agent 输入、Provider、执行器、结果四步，用占位符协议写入并 grep svg 验证。",
            "生成一个 SVG 游戏金币 sprite coin.svg，包含渐变和高光，用占位符协议写入并 head -5 验证。",
            "生成一个 SVG 空状态插画 empty-state.svg，主题是等待任务完成，用占位符协议写入并 wc -l 验证。",
            "生成一个 SVG 地图 pin 图标 map-pin.svg，要求 viewBox、title、desc 完整，用占位符协议写入并 grep title 验证。",
            "生成一个 SVG 数据图表 mini-chart.svg，含折线和点，用占位符协议写入并 tail -5 验证。",
            "生成一个 SVG 机器人头像 bot-avatar.svg，用占位符协议写入，要求无外链并 grep linearGradient 验证。",
            "生成一个 SVG 天气卡图标 weather-card.svg，包含太阳和云，用占位符协议写入并 wc -c 验证。",
            "生成一个 SVG 进度环 progress-ring.svg，要求 stroke-dasharray，用占位符协议写入并 grep dasharray 验证。",
            "生成一个 SVG 奖杯 trophy.svg，包含可访问 title，用占位符协议写入并 head -6 验证。",
        ],
    ),
    (
        "python-scripts",
        [
            "写一个 Python 脚本 stats.py，读取内置列表计算平均值/中位数/最大值，用占位符协议写入并运行。",
            "写一个 Python 脚本 json_report.py，生成 JSON 报告到 report.json，再 cat 验证。",
            "写一个 Python 脚本 csv_demo.py，生成 CSV 文件并读取汇总，用占位符协议写入并运行。",
            "写一个 Python 脚本 rename_preview.py，只打印批量重命名预览，不真实改名，用占位符协议写入并运行。",
            "写一个 Python 脚本 btc_parse_demo.py，解析一段内置 Kraken JSON 字符串输出 BTC 价格，用占位符协议写入并运行。",
            "写一个 Python 脚本 markdown_toc.py，从内置 markdown 文本提取标题目录，用占位符协议写入并运行。",
            "写一个 Python 脚本 image_manifest.py，扫描当前工作区 html/svg 文件生成 manifest JSON，用占位符协议写入并运行。",
            "写一个 Python 脚本 validate_paths.py，验证几个绝对路径是否在项目根目录内，用占位符协议写入并运行。",
            "写一个 Python 脚本 word_count.py，统计内置中文段落字数和标点数，用占位符协议写入并运行。",
            "写一个 Python 脚本 schedule_preview.py，计算从当前时间开始每 5 分钟的下 3 次触发时间，用占位符协议写入并运行。",
        ],
    ),
    (
        "games-canvas",
        [
            "创建一个单文件 HTML Canvas 贪吃蛇小游戏 snake-mini.html，包含键盘控制、计分和重开按钮，用占位符协议写入并 wc -l 验证。",
            "创建一个单文件 HTML 俄罗斯方块风格小游戏 tetris-lite.html，包含 Canvas、方块下落、分数，用占位符协议写入并 head -5 验证。",
            "创建一个单文件 HTML 打砖块小游戏 brick-breaker.html，包含碰撞、关卡文字，用占位符协议写入并 grep canvas 验证。",
            "创建一个单文件 HTML 记忆翻牌游戏 memory-cards.html，包含 CSS 动画和 JS 状态，用占位符协议写入并 wc -l 验证。",
            "创建一个单文件 HTML match-3 消除游戏 match3.html，包含网格、交换、消除检测，用占位符协议写入并 grep match 验证。",
            "创建一个单文件 HTML 飞船躲避游戏 space-dodge.html，包含 Canvas、敌人、生命值，用占位符协议写入并 tail -5 验证。",
            "创建一个单文件 HTML 点击气球游戏 balloon-pop.html，包含随机生成、倒计时，用占位符协议写入并 wc -l 验证。",
            "创建一个单文件 HTML 井字棋 tic-tac-toe.html，包含胜负判断和重置，用占位符协议写入并 grep winner 验证。",
            "创建一个单文件 HTML 拼图小游戏 puzzle.html，包含 3x3 拼图逻辑，用占位符协议写入并 head -6 验证。",
            "创建一个单文件 HTML 节奏点击游戏 rhythm-tap.html，包含节拍条和分数，用占位符协议写入并 wc -l 验证。",
        ],
    ),
    (
        "terminal-extensions",
        [
            "把文件 /Users/pippo/Desktop/my-project/monopoly-game.html 发到微信，要求只输出包含 wx send_file 的 bash 命令块。",
            "查看当前定时计划列表，要求输出 schedule list 的 bash 命令块，可以有一句说明。",
            "暂停名为 每5分钟查比特币价格 的计划，输出 schedule update JSON，JSON 里 enabled=false。",
            "恢复名为 每6小时生成手机小游戏 的计划，输出 schedule update JSON，JSON 里 enabled=true。",
            "创建一个今晚 23:30 提醒检查 BTC 的计划，输出 schedule create JSON，prompt 简短。",
            "删除名为 测试计划 的计划，输出 schedule delete 测试计划。",
            "先查看计划再发送 report.html 到微信，命令块里包含 schedule list 和 wx send_file 两行。",
            "创建每小时检查一次项目状态的计划，repeat_every_seconds=3600，输出 schedule create JSON。",
            "更新第三个计划的 prompt 为 查询 BTC 价格并简短汇报，输出 schedule update JSON。",
            "发送两个文件 a.html,b.html 到微信，用英文逗号分隔路径。",
        ],
    ),
    (
        "shell-composition",
        [
            "输出一个 bash 命令块，统计 match-crush-game.html 行数、前 3 行、后 3 行，用 && 链接。",
            "输出一个 bash 命令块，用 if 判断 /Users/pippo/Desktop/my-project 是否存在并 echo 状态。",
            "输出一个 bash 命令块，用 here-doc 临时运行 Python 打印三行中文。",
            "输出一个 bash 命令块，用 find 查找项目根目录下一层 html 文件并排序显示前 10 个。",
            "输出一个 bash 命令块，用 printf 生成三行文本到 quick-note.txt 并 cat 验证。",
            "输出一个 bash 命令块，用 grep -R 搜索 TODO，但失败也不要中断，最后 echo done。",
            "输出一个 bash 命令块，用 test -f 检查 monopoly-game.html，不存在则 echo missing。",
            "输出一个 bash 命令块，用 awk 统计一个 here-doc 里的字段数量。",
            "输出一个 bash 命令块，包含 cd /Users/pippo/Desktop/my-project 后 pwd 和 ls。",
            "输出一个 bash 命令块，用 curl --version | head -1 检查 curl 版本。",
        ],
    ),
    (
        "multi-file-placeholders",
        [
            "创建 index.html 和 styles.css 两个文件，HTML 引用 CSS，使用两个占位符代码块，最后 wc -l 两个文件。",
            "创建 app.py 和 README.md 两个文件，Python 打印说明，使用占位符协议，最后运行 app.py。",
            "创建 game.html 和 game-data.json，HTML 内 fetch 同级 JSON，使用占位符协议，最后 ls -l 验证。",
            "创建 icon.svg 和 preview.html，HTML 嵌入 SVG 文件路径，使用占位符协议，最后 grep svg preview.html。",
            "创建 script.js 和 page.html，page 引用 script.js，使用占位符协议，最后 head -5 page.html。",
            "创建 data.csv 和 analyze.py，Python 读取 CSV 汇总，使用占位符协议并运行。",
            "创建 theme.css 和 demo.html，CSS 变量明显，使用占位符协议并 grep -- --accent。",
            "创建 manifest.json 和 service-worker.js，使用占位符协议，最后 python -m json.tool 验证 manifest。",
            "创建 notes.md 和 toc.py，toc.py 读取 notes.md 生成目录，使用占位符协议并运行。",
            "创建 config.yaml 和 validate_config.py，Python 简单解析冒号键值，使用占位符协议并运行。",
        ],
    ),
    (
        "mixed-language-output",
        [
            "先用自然语言解释两句，再输出 bash 命令块创建 hello.txt，后面再给 plaintext TODO 代码块。",
            "输出一个 markdown 示例代码块 python 供阅读，再输出真正执行用 bash 命令块。",
            "输出一个 html 展示代码块，再输出 bash 命令块把它写入文件。",
            "输出一个 json 配置示例代码块，再输出 bash 命令块保存为 config.json。",
            "输出一个 svg 示例代码块，再输出 bash 命令块保存为 icon.svg。",
            "先列出简短计划，然后输出 bash 命令块运行 Python here-doc。",
            "输出 bash 命令块后，再用一句话说明等待执行结果，不要声称已完成。",
            "输出 bash 命令块中包含中文 echo，再跟一个 plaintext TODO。",
            "输出 shell 语言代码块而不是 bash，内容是 pwd 和 ls。",
            "输出 zsh 语言代码块，内容是 print -r -- hello。",
        ],
    ),
    (
        "data-doc-generation",
        [
            "生成一个 Markdown 周报 weekly.md，包含表格和清单，用占位符协议写入并 wc -l。",
            "生成一个 CSV 示例 sales.csv 和 Python 汇总脚本，用占位符协议写入并运行。",
            "生成一个 JSON 数据文件 users.json，并用 Python 校验长度，用占位符协议。",
            "生成一个 Markdown API 文档 api.md，包含 bash 示例代码块，但真正执行命令也要有 bash 命令块。",
            "生成一个 HTML 报告 report.html，包含表格和内联 CSS，用占位符协议写入并 grep table。",
            "生成一个 Mermaid 文档 diagram.md，包含 mermaid fenced code，并用 bash 写入文件。",
            "生成一个纯文本 checklist.txt，包含 10 条检查项，用 bash here-doc 写入并 tail -3。",
            "生成一个 Python 脚本把内置数据渲染成 markdown 表格，用占位符协议并运行。",
            "生成一个 package.json 示例并用 python -m json.tool 校验，用占位符协议。",
            "生成一个 README.md，包含安装、使用、验证三节，用占位符协议写入并 head -20。",
        ],
    ),
    (
        "edge-cases",
        [
            "输出一个 bash 命令块，里面有双引号、单引号和美元符号，确保 shell 引号闭合。",
            "输出一个 bash 命令块，使用 Python -c 一行解析 JSON 字符串，确保嵌套引号正确。",
            "输出一个 bash 命令块，使用多行 python here-doc，EOF 名称不要用 PYEOF，改用 CODEEND。",
            "输出一个 bash 命令块，包含注释行 # <desc 检查网络> 和 curl -I 示例。",
            "输出一个 bash 命令块，命令中包含中文路径变量但不要真的创建带空格路径。",
            "输出一个 bash 命令块，包含 false || echo fallback 的低风险连接符。",
            "输出一个 bash 命令块，包含 set -e 后执行两条安全 echo。",
            "输出一个 bash 命令块，包含 trap 'echo cleanup' EXIT。",
            "输出一个 bash 命令块，包含 case 语句并正确 esac。",
            "输出一个 bash 命令块，包含 for 循环并正确 done。",
        ],
    ),
]


def main() -> None:
    cases = []
    idx = 1
    for category, prompts in CATEGORIES:
        for prompt in prompts:
            cases.append({
                "id": f"web-case-{idx:03d}",
                "category": category,
                "prompt": prompt,
            })
            idx += 1
    if len(cases) != 100:
        raise SystemExit(f"expected 100 cases, got {len(cases)}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print("cases=100")


if __name__ == "__main__":
    main()
