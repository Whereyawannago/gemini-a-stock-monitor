# A 股半小时智能盯盘程序 (`gemini-a-stock-monitor`)

面向 A 股盘中交易时段的半小时级智能盯盘系统：基于 **AKShare** 采集四大指数实时行情与 5 分钟 K 线、全市场涨跌家数以及行业/概念双维度主力资金流向；内置**放量上涨/放量下跌量化预警与 60 分钟防刷屏冷却机制**；接入 **MiniMax（OpenAI 兼容接口）** 生成结构化 4 段式盘中解盘（自动过滤 `<think>` 标签并支持超时降级规则摘要）；通过 **飞书 Webhook 交互卡片** 实时推送（常规蓝色简报 / 放量上涨红色预警 / 放量下跌绿色预警，支持签名校验）。

---

## 核心功能

1. **多维度盘面监控（AKShare）**
   - **四大核心指数**：上证指数(`000001`)、深证成指(`399001`)、创业板指(`399006`)、科创50(`000688`)
   - **5 分钟 K 线分时均价 (VWAP)**：基于当日 5m K 线典型价与成交额加权计算指数点位 VWAP 及现价偏离度
   - **累计量能同比昨日同时刻**：精确截断昨日相同 `HH:MM` 时点累计成交额进行同口径量比测算
   - **近 30 分钟边际放量**：统计近 6 根 5m K 线量能，优先对比昨日同时段 30 分钟量能（消除 A 股日内 U 型成交分布偏差）
   - **全市场涨跌家数**：实时统计上涨/下跌/平盘、涨停/跌停家数、上涨占比与全市场成交额
   - **双维度板块资金流向**：行业板块与概念板块主力净流入/净流出 Top N

2. **量化趋势预警 & 60 分钟防刷屏冷却**
   - 综合**累计量能同比 / 近 30 分钟边际放量**、**指数涨跌幅**、**VWAP 站上/跌破状态**、**全市场上涨家数占比**四重因子
   - 触发 **放量上涨（红色预警）** 或 **放量下跌（绿色预警）** 时持久化记录到 `data/alert_state.json`
   - 同一方向预警在 **60 分钟冷却期** 内自动降级为常规蓝色简报（标注冷却剩余时间）；若出现**趋势反转**（如放量上涨转放量下跌），则立即突破冷却发送预警

3. **MiniMax 大模型 4 段式盘中解盘**
   - 默认接入 `https://api.minimaxi.com/v1`，默认模型 `MiniMax-M3.1-Flash`，`max_tokens=2048`
   - 自动清洗 `<think>...</think>` 推理标签（含未闭合标签边界保护）
   - 固定输出结构化 4 段式解盘：**一、大盘与量能格局 / 二、双维度板块资金 / 三、盘面强弱与风险信号 / 四、后续盯盘关注点**
   - 未配置 Key、网络超时或接口异常时自动降级为规则引擎 4 段式摘要，确保盯盘永不中断

4. **飞书 Webhook 交互卡片**
   - 三色主题卡片：常规简报（蓝色 `blue`）、放量上涨预警（红色 `red`）、放量下跌预警（绿色 `green`）
   - 原生支持飞书机器人 HMAC-SHA256 签名校验（配置 `FEISHU_WEBHOOK_SECRET` 自动生效）

5. **交易日历定时调度与云部署**
   - 基于 `APScheduler` + `Asia/Shanghai` 时区，仅在 A 股交易日（AKShare 交易日历 + 本地缓存 + 工作日兜底）的 `10:00, 10:30, 11:00, 11:30, 13:30, 14:00, 14:30, 15:00` 触发
   - 提供 `Dockerfile`、`docker-compose.yml` 与 `deploy/a-stock-monitor.service`（systemd 模板）

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
# 编辑 .env，填入 MINIMAX_API_KEY、FEISHU_WEBHOOK_URL 及可选 FEISHU_WEBHOOK_SECRET
```

### 3. 本地演练与测试（无需真实飞书推送）

```bash
# 常规蓝色简报演练（模拟数据 + 不发飞书）
stock-monitor --once --mock --dry-run

# 放量上涨红色预警演练 + 打印飞书卡片 JSON
stock-monitor --once --mock --mock-scenario surge --dry-run --print-json

# 放量下跌绿色预警演练
stock-monitor --once --mock --mock-scenario plunge --dry-run

# 使用真实 AKShare 数据演练一次（不发飞书）
stock-monitor --once --dry-run

# 启动交易日历定时调度（10:00~15:00 每半小时）
stock-monitor --schedule
```

---

## 云服务器部署

### 方式一：Docker Compose（推荐）

```bash
cp .env.example .env
# 修改 .env 中的密钥配置
docker compose up -d --build
docker compose logs -f
```

### 方式二：Systemd 后台服务

```bash
# 1. 将项目部署到 /opt/gemini-a-stock-monitor 并创建 .venv 与 .env
# 2. 复制并按需修改 User/WorkingDirectory
sudo cp deploy/a-stock-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now a-stock-monitor
sudo systemctl status a-stock-monitor
sudo journalctl -u a-stock-monitor -f
```

---

## 运行单元测试

```bash
pytest
```

---

## 目录结构

```text
gemini-a-stock-monitor/
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── deploy/
│   └── a-stock-monitor.service
├── src/stock_monitor/
│   ├── config.py           # 环境变量配置与默认指数/调度时间表
│   ├── models.py           # 数据模型定义
│   ├── data_fetcher.py     # AKShare 采集与 VWAP/同比/30m边际放量计算
│   ├── mock_data.py        # --mock 高仿真三场景生成器
│   ├── alert_engine.py     # 量化预警规则与 60 分钟冷却状态持久化
│   ├── llm_analyzer.py     # MiniMax 解盘、<think>过滤与超时降级规则摘要
│   ├── feishu_notifier.py  # 飞书 Webhook 签名校验与三色交互卡片构建
│   ├── calendar_utils.py   # A 股交易日历与本地缓存
│   ├── scheduler.py        # APScheduler 10:00~15:00 半小时定时调度
│   ├── pipeline.py         # 核心监控流水线编排
│   └── main.py             # CLI 入口
└── tests/                  # 单元测试套件
```
