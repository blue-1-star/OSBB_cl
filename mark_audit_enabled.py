from audit_logger import audit_system_event

audit_id = audit_system_event(
    action_type="audit_enabled",
    source_context="OSBB audit logger enabled",
    comment="Включён журнал аудита ОСББ. Начиная с этой точки, изменения должны фиксироваться в operator_audit_log.",
)

print("Audit enabled row:", audit_id)