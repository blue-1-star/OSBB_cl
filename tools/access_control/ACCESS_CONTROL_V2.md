# OSBB Access Control v2

This release uses the existing OSBB RBAC/ACL tables.

It does not rebuild or replace:

- `access_roles`
- `access_role_permissions`
- `access_user_roles`
- `access_user_permissions`

## Main adapter

```python
has_permission(telegram_user_id, resource, action, scope_type="ALL", scope_value="*")
```

## Role bridge

Telegram UI roles are mapped to existing role codes:

| UI role | access role_code |
|---|---|
| resident | RESIDENT |
| guard | GUARD_O |
| cashier | FINANCE_OPERATOR |
| operator | ACCESS_MANAGER |
| admin | ACCESS_MANAGER |
| super_admin | SUPER_ADMIN |

## Admin UI

`🔑 Права доступа` shows real `access_roles` and real `access_role_permissions`.

`📖 Справочник возможностей` shows `access_permissions`.
