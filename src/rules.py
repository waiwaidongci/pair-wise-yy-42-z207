from __future__ import annotations
from .domain import ConflictError, ValidationError
TITLE='山火事件指挥与离线人员调度'; ENTITY='山火事件'; ID_PREFIX='WF'
SEVERITIES=['low', 'moderate', 'high', 'extreme']; STATES=['reported', 'active', 'contained', 'controlled', 'closed']; TRANSITIONS={'reported': ['active'], 'active': ['contained'], 'contained': ['controlled'], 'controlled': ['closed'], 'closed': []}; TRANSITION_ROLES={'active': ['incident_commander'], 'contained': ['incident_commander'], 'controlled': ['incident_commander'], 'closed': ['incident_commander']}
CREATE_ROLES=set(['field_commander']); RECORD_ROLES=set(['field_commander', 'logistics']); AUDIT_ROLES=set(['incident_commander', 'viewer']); VIEW_ROLES=set(['field_commander', 'incident_commander', 'logistics', 'viewer'])
SEVERITY_WEIGHT={'low': 1.0, 'moderate': 3.0, 'high': 6.0, 'extreme': 9.0}; DEADLINE_HOURS={'low': 72, 'moderate': 24, 'high': 8, 'extreme': 4}; TERMINAL_STATES=set(['closed'])
def priority_score(severity,quantity=0.0,threshold=1.0,open_records=0):
    if severity not in SEVERITY_WEIGHT: raise ValidationError("unknown severity")
    ratio=quantity/threshold if threshold>0 else 1.0
    return max(0,min(10,int(round(SEVERITY_WEIGHT[severity]+min(4.0,ratio*4.0)+min(3.0,float(open_records))))))
def response_deadline_hours(severity,quantity=0.0,threshold=1.0):
    if severity not in DEADLINE_HOURS: raise ValidationError("unknown severity")
    ratio=quantity/threshold if threshold>0 else 1.0
    return max(1,int(DEADLINE_HOURS[severity]/max(1.0,ratio)))
def escalation_required(severity,quantity=0.0,threshold=1.0):
    return severity==SEVERITIES[-1] or (threshold>0 and quantity>=threshold)
def can_transition(current,target): return target in TRANSITIONS.get(current,[])
def validate_transition(current,target):
    if current not in STATES or target not in STATES: raise ValidationError("未知状态")
    if not can_transition(current,target): raise ConflictError(f"不能从{current}转换到{target}")
def completion_blockers(target,open_records): return ["仍有未关闭事项"] if target in TERMINAL_STATES and open_records>0 else []
def role_for_transition(target): return set(TRANSITION_ROLES.get(target,[]))

# ---- 撤离点名与重新开放 ----
EVAC_ENTITY='撤离单'; DISPATCH_ENTITY='派单'
EVAC_ORDER_STATUSES=['active','reopened']
REPORT_STATUSES=['arrived','missing']
EVAC_CREATE_ROLES=set(['incident_commander']); REPORT_ROLES=set(['field_commander', 'logistics']); EQUIPMENT_ROLES=set(['logistics']); REOPEN_ROLES=set(['incident_commander']); DISPATCH_ROLES=set(['incident_commander'])
def evaluate_rollcall(crews,reports,reviews=None):
    """按班组汇总现场回报：同号重复沿用首次结果，矛盾状态留待核对。
    crews为登记班组名单，reports为按时间排序的回报记录，reviews为核对结论
    {班组:状态}（核对后以其为准），结果按名册顺序输出。"""
    first={}
    for report in reports:
        first.setdefault(report['crew'],report['status'])
    reviewed=dict(reviews or {})
    effective={c:reviewed.get(c,first.get(c)) for c in crews}
    arrived=[c for c in crews if effective.get(c)=='arrived']
    missing=[c for c in crews if effective.get(c)=='missing']
    silent=[c for c in crews if effective.get(c) is None]
    disputed=[c for c in crews if c in first and c not in reviewed
              and any(r['crew']==c and r['status']!=first[c] for r in reports)]
    unknown=sorted(set([r['crew'] for r in reports if r['crew'] not in crews]))
    pending=set(missing)|set(silent)|set(disputed)
    unaccounted=[c for c in crews if c in pending]
    return {'arrived':arrived,'missing':missing,'silent':silent,'disputed':disputed,'unknown':unknown,'reviewed':sorted(reviewed),'unaccounted':unaccounted,'accounted':not unaccounted and not unknown}
def reopen_blockers(rollcall,equipment_returned):
    """人员全部核销且装备归还后才允许重新开放。"""
    blockers=[]
    if rollcall['unaccounted']: blockers.append('存在未核销人员：'+','.join(rollcall['unaccounted']))
    if rollcall['unknown']: blockers.append('存在未登记班组回报：'+','.join(rollcall['unknown']))
    if not equipment_returned: blockers.append('装备未归还')
    return blockers
