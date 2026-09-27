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
- `POST /api/items/{id}/evacuations`，登记撤离单（`zone`任务区+`teams`班组），仅incident_commander
- `GET /api/items/{id}/evacuations`，撤离单与点名进度
- `GET /api/evacuations/{id}`，撤离单详情与点名台账
- `POST /api/evacuations/{id}/reports`，班组按现场单号`report_ref`回报（`status`为accounted/missing，`equipment_returned`标记装备归还）
- `POST /api/evacuations/{id}/reopen`，重新开放，仅incident_commander
- `GET /api/audit`

允许角色：field_commander, incident_commander, logistics, viewer。火线长度、风向变化和离线记录数量影响风险等级；同一资源不能同时出现在多个活动任务中。

## 撤离点名与重新开放

- 撤离单登记任务区与班组后，该任务区进入封锁（blocked）；`kind=dispatch`的派单记录必须携带`zone`。
- 班组按现场单号回报状态：同号重复沿用首次结果（返回`reused`）；同号同状态补报装备归还记为`supplemented`；同号矛盾状态标记`needs_review`留待核对，首次结果不变，核对后请用新现场单号补报。
- 封锁区有人未核销时，新派单返回409冲突并在`details.missing_teams`中列出缺失班组。
- 所有班组人员核销（accounted）且装备归还后才能重新开放（reopened）；重新开放后停止点名，派单恢复。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
