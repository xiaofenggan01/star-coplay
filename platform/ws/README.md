# ws/ 已迁移到 backend/

本目录的代码已迁移至 `platform/backend/`。

新的启动方式：

```bash
cd platform/backend
python -m app
```

`ws/scripts/run_local.py` 保留为兼容入口，会打印 deprecation warning 后转调 backend。
