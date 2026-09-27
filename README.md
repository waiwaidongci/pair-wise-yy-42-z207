# 山火事件指挥与离线人员调度

维护火线、风向、资源和任务区，合并离线现场记录并防止人员重复分配。

## 模块结构

- `app.py`：参数解析、依赖组装和HTTP服务启动。
- `src/domain.py`：数据结构、错误、状态和基础校验。
- `src/rules.py`：状态机、角色矩阵、优先级、期限和关闭不变量。
- `src/repository.py`：SQLite建表、事务、版本控制和审计链。
- `src/service.py`：权限检查、用例编排、并发控制和审计。
- `src/http_api.py`：JSON路由和统一错误响应。
- `src/audit.py`：UTC时间和SHA-256审计事件。
- `static/index.html`：最小演示页。
- `tests/`：完整流程、规则和失败测试。

## 初始化与启动

```bash
python3 app.py --db ./data.db --port 8319
```

默认端口为`8319`，首次启动自动建库。使用`X-Actor`和`X-Role`请求头传递身份。

## 主要接口

- `GET /health`
- `GET /api/items`
- `POST /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `GET /api/audit`

### 撤离点名与重新开放

- `POST /api/evacuations`：登记撤离单（任务区、现场单号、班组名册，incident_commander）
- `GET /api/evacuations`、`GET /api/evacuations/{id}`、`GET /api/evacuations/by-no/{order_no}`：查看点名结果
- `POST /api/evacuations/by-no/{order_no}/reports`：班组按现场单号回报`arrived`/`missing`（field_commander、logistics）；同号重复沿用首次结果，矛盾状态留待核对
- `POST /api/evacuations/{id}/reviews`：核对矛盾班组状态（incident_commander）
- `POST /api/evacuations/{id}/equipment-return`：装备归还登记（logistics）
- `POST /api/evacuations/{id}/reopen`：人员全部核销且装备归还后重新开放（incident_commander）
- `POST /api/dispatches`：新派单；封锁任务区存在未核销人员时返回409并在`details.missing_crews`列出缺失班组

允许角色：field_commander, incident_commander, logistics, viewer。火线长度、风向变化和离线记录数量影响风险等级；同一资源不能同时出现在多个活动任务中。点名判定在`src/rules.py`，台账存储在`src/repository.py`，请求入口在`src/http_api.py`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
