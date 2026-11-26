# A 股智能盯盘 · 收盘复盘 · 黄金价格监控 (`gemini-a-stock-monitor`)

基于 **AKShare + MiniMax 大模型 + 飞书交互卡片** 的全天候市场监控系统，涵盖三大核心模块：
1. **A 股盘中半小时智能盯盘**（交易日 `10:00~15:00` 每半小时）
2. **A 股每日收盘复盘 & 龙虎榜**（交易日 `15:30` 收盘复盘 + `18:00` 龙虎榜）
3. **黄金价格监控**（上海金 `Au99.99` + `COMEX` 黄金 + 伦敦金：`09:30 / 21:00` 定时简报 + 每 5 分钟异动/关键价位预警）

> 已内置 **AKShare 1.20.44+ 新旧接口自动兼容层** 及 **东方财富 push2 限流熔断 + 新浪/同花顺/乐咕乐股多源降级**，避免单源限流导致数据缺失。

---

## 核心功能

### 1. A 股盘中半小时盯盘（蓝色常规 / 红色放量上涨 / 绿色放量下跌）
- **四大核心指数**：上证指数(`000001`)、深证成指(`399001`)、创业板指(`399006`)、科创50(`000688`)
- **量价指标**：5 分钟 K 线分时均价 (VWAP) 偏离度、累计量能同比昨日同时刻、近 30 分钟边际放量
- **市场广度与双维度资金**：全市场涨跌家数/活跃度，行业板块与概念板块主力净流入/净流出 Top N
- **量化预警 & 60 分钟冷却**：放量上涨（红）/ 放量下跌（绿）持久化至 `data/alert_state.json`，同向 60 分钟冷却防刷屏，趋势反转立即突破冷却

### 2. 每日收盘复盘 & 龙虎榜（紫色复盘卡片 / 靛蓝龙虎榜卡片）
- **15:30 收盘复盘**（交易日）：
  - 四大指数收盘、两市成交额与全天量比、全市场涨跌家数与活跃度
  - **涨停板生态**：涨停 / 炸板 / 跌停家数、封板率、最高连板高度、连板梯队、昨日涨停今日溢价、涨停集中行业
  - **资金全景**：两市主力净流入（东方财富 / 同花顺汇总双源）、南向成交净买额、沪市两融余额（T-1）、行业与概念资金 Top N
  - **市场热度与情绪评分（0~100）**：综合上涨占比、指数表现、涨跌停比、封板率、连板高度、昨日涨停溢价，辅以个股人气榜 Top
  - **MiniMax 四段式收盘复盘**：今日盘面总结 / 资金流向与主线 / 市场情绪与热度 / 明日展望与策略
- **18:00 龙虎榜复盘**（交易日）：上榜个股数、机构席位买入数、龙虎榜净买入 / 净卖出 Top

### 3. 黄金价格监控（金色常规简报 / 红色急涨突破 / 绿色急跌跌破）
- **多品种行情**：上海金 `Au99.99`（元/克）、`COMEX` 黄金（美元/盎司）、伦敦金（美元/盎司）及 `USD/CNY` 汇率
- **国内外价差**：自动折算国际金价人民币每克，并计算国内黄金溢价/折价
- **双重推送机制**：
  - 定时简报：周一至周五 `09:30`、`21:00`（可配置 `GOLD_BRIEF_TIMES`）
  - 异动与价位预警：每 5 分钟轮询（周六 06:00 ~ 周一 06:00 休市自动跳过），支持 **30 分钟急涨急跌（默认 ±1%）**、**日内大涨大跌（默认 ±2%）**、**自定义关键价位提醒**（如 `GOLD_PRICE_LEVELS=AU9999:below:750,COMEX:above:3700`），同样具备 60 分钟防刷屏冷却

### 4. MiniMax 大模型解盘 & 飞书交互卡片
- 默认接入 `https://api.minimaxi.com/v1`，默认模型 `MiniMax-M3.1-Flash`，`max_tokens=2048`，自动过滤 `<think>` 标签，超时或未配置 Key 自动降级规则摘要
- 原生支持飞书自定义机器人 HMAC-SHA256 签名校验（配置 `FEISHU_WEBHOOK_SECRET` 自动生效）

---

## 快速开始

### 1. 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 MINIMAX_API_KEY、FEISHU_WEBHOOK_URL 及可选 FEISHU_WEBHOOK_SECRET、GOLD_PRICE_LEVELS
```

### 3. 本地演练与手动触发

```bash
# 盘中盯盘演练（normal / surge / plunge）
stock-monitor --once --job intraday --mock --mock-scenario surge --dry-run

# 每日收盘复盘演练（紫色卡片）
stock-monitor --once --job review --mock --dry-run --print-json

# 龙虎榜复盘演练（靛蓝卡片）
stock-monitor --once --job lhb --mock --dry-run

# 黄金简报 & 黄金异动预警演练
stock-monitor --once --job gold --mock --dry-run
stock-monitor --once --job gold-poll --mock --mock-scenario surge --dry-run

# 使用真实 AKShare 数据演练（不发飞书）
stock-monitor --once --job review --dry-run
stock-monitor --once --job gold --dry-run

# 启动常驻定时调度（盘中盯盘 + 15:30 复盘 + 18:00 龙虎榜 + 黄金简报与 5 分钟预警）
stock-monitor --schedule
```

---

## 云服务器部署

### Docker Compose（推荐）

```bash
cp .env.example .env
# 修改 .env 中的密钥与 GOLD_PRICE_LEVELS 等配置
docker compose up -d --build
docker compose logs -f
```

已有部署更新代码：

```bash
git pull && docker compose up -d --build
```

### Systemd 后台服务

```bash
sudo cp deploy/a-stock-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now a-stock-monitor
sudo journalctl -u a-stock-monitor -f
```

---

## 运行单元测试

```bash
pytest
```
