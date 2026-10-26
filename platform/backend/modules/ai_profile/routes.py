# TODO: 管理态 HTTP 路由（供 Vercel 前端调用）
# 代理以下只读接口（NewAPI 不对公网开放，由此服务转发）：
#   GET /internal/relay/users/{relay_user_id}
#   GET /internal/relay/users/{relay_user_id}/available-models
#   GET /internal/relay/users/{relay_user_id}/quota
#   GET /internal/relay/users/{relay_user_id}/quota-adjustments
#   GET /internal/relay/users/{relay_user_id}/usage-summary
#   GET /internal/relay/users/{relay_user_id}/usage-timeseries
#   GET /internal/relay/health
#   GET /internal/relay/models/{model}/availability
