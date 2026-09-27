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
# ---- 撤离点名判定 ----
EVAC_ENTITY='撤离单'; EVACUATION_STATUSES=['blocked','reopened']; REPORT_STATUSES=['accounted','missing']
EVACUATE_ROLES=set(['incident_commander']); REPORT_ROLES=set(['field_commander','logistics']); REOPEN_ROLES=set(['incident_commander'])
def normalize_teams(value):
    if not isinstance(value,(list,tuple)) or not value: raise ValidationError("teams必须是非空班组列表")
    teams=[]
    for entry in value:
        if not isinstance(entry,str) or not entry.strip(): raise ValidationError("班组名称不能为空")
        name=entry.strip()
        if len(name)>100: raise ValidationError("班组名称不能超过100个字符")
        if name not in teams: teams.append(name)
    if len(teams)>50: raise ValidationError("班组数量不能超过50个")
    return teams
def normalize_report_status(value):
    if value not in REPORT_STATUSES: raise ValidationError("status必须是accounted或missing")
    return value
def classify_report(existing,team,status,equipment_returned):
    # 同号重复沿用首次结果；同号同状态补报装备归还则升级；矛盾状态留待核对且首次结果不变
    if existing is None: return 'new'
    if existing["team"]!=team or existing["status"]!=status: return 'conflict'
    if bool(existing["equipment_returned"])==bool(equipment_returned): return 'reuse'
    return 'supplement' if equipment_returned else 'conflict'
def _team_reports(team,reports): return [r for r in reports if r["team"]==team]
def accounted_teams(teams,reports): return [t for t in teams if any(r["status"]=="accounted" for r in _team_reports(t,reports))]
def unaccounted_teams(teams,reports):
    accounted=set(accounted_teams(teams,reports))
    return [t for t in teams if t not in accounted]
def equipment_pending_teams(teams,reports): return [t for t in teams if not any(r["equipment_returned"] for r in _team_reports(t,reports))]
def reopen_blockers(teams,reports):
    # 人员核销且装备归还后才能重新开放
    blockers=[]
    missing=unaccounted_teams(teams,reports)
    if missing: blockers.append("班组未核销："+"、".join(missing))
    pending=equipment_pending_teams(teams,reports)
    if pending: blockers.append("装备未归还："+"、".join(pending))
    return blockers
