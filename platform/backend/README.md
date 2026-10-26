# StarCoPlay Backend

Python 异步后端服务，承载 WebSocket 网关、HTTP API、LLM 路由、Agent 子进程管理等运行时能力。

## 启动

```bash
cd platform/backend
pip install -r requirements.txt
python -m app
```

## 目录结构

```
app/          → 启动入口、bootstrap、生命周期管理
modules/      → 业务模块（gateway、http_api、llm、runtime、config、health）
common/       → 共享基础设施（settings、db、auth、errors）
tests/        → 测试
```

## 端口

| 服务 | 端口 | 协议 |
|------|------|------|
| WebSocket Gateway | 8765 | WS |
| HTTP API | 8766 | HTTP |

## 环境变量

参见 `.env.example`。
