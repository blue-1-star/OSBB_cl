# Фактическое состояние подсистемы заказов услуг и уведомлений

**Subsystem:** Service Request & Notification Workflow  
**Status:** reconstructed from available schema and documentation  
**Version:** 0.1

## 1. Подтверждённая общая структура

```text
service_catalog
→ service_items
→ service_item_workflows
→ service_workflow_profiles
→ service_workflow_steps
→ service_order_interests
→ service_orders
→ service_order_steps
→ service_order_events
```

К заказу могут присоединяться:

```text
service_order_payment_links → payments
service_order_charge_links  → charges
```

## 2. Каталог и варианты услуг

`service_catalog` хранит базовый вид услуги.

`service_items` хранит конкретный вариант услуги: новый пульт, повторная выдача, перепрошивка, телефонный доступ и другие варианты.

`service_item_workflows` связывает вариант услуги с профилем рабочего процесса.

## 3. Шаблоны процесса

`service_workflow_profiles` задаёт профиль процесса.

`service_workflow_steps` задаёт последовательность эталонных шагов, их тип, обязательность и активность.

## 4. Предварительный интерес

`service_order_interests` сохраняет:

- жителя и Telegram-пользователя;
- квартиру;
- услугу и вариант;
- снимок названия;
- профиль процесса;
- количество;
- снимок цены и суммы;
- статус;
- ссылку на уведомление об оплате;
- ссылку на платёж;
- ссылку на созданный заказ;
- комментарий жителя.

Фактическая роль таблицы:

```text
намерение
→ возможное ожидание оплаты
→ создание service_order
```

Точный программный переход в доступных материалах не найден.

## 5. Заказ

`service_orders` является основной таблицей заказа.

Она хранит номер заказа, жителя, квартиру, услугу, вариант услуги, снимок названия, профиль процесса, количество, цену, сумму, статусы заказа, оплаты и исполнения, комментарии, даты завершения и отмены.

Снимки названия и цены защищают старый заказ от последующих изменений каталога.

## 6. Шаги и события

`service_order_steps` — экземпляры шагов конкретного заказа.

`service_order_events` — журнал жизненного цикла заказа с типом события, актором, ролью, контекстом, деталями и временем.

В исследованной базе обе таблицы содержат данные, поэтому механизм не является только заготовкой.

## 7. Пульты

Подтверждены:

- `remote_order_details`;
- `remote_order_issued_assets`;
- `remote_assets`;
- `remote_asset_movements`;
- `remote_supplier_batches`;
- `remote_supplier_batch_links`;
- `remote_handover_events`;
- `remote_requests`.

Поток данных:

```text
service_orders
→ remote_order_details
→ supplier batch
→ issued assets
→ remote_assets
→ movements / handover
```

Реализация учитывает не только заявку, но и жизненный цикл физического пульта.

## 8. Телефонный доступ

Подтверждены:

- `phone_access_requests`;
- `phone_access_request_points`;
- `phone_access_subscriptions`;
- `phone_access_subscription_points`;
- `phone_access_subscription_charges`.

Поток:

```text
request
→ access points
→ subscription
→ subscription points
→ subscription charges
```

Также существуют:

- `phone_barrier_access_points`;
- `phone_barrier_access_interests`;
- `phone_barrier_access_interest_points`;
- `phone_barrier_access_order_points`;
- `access_points`;
- `access_external_commands`;
- `access_operation_journal`;
- `access_debt_warnings`;
- `barrier_phone_access`.

Это указывает на развитие более нового ядра управления доступом рядом с прежней моделью.

## 9. Политики доступа

Подтверждены:

- `access_policy_versions`;
- `access_policy_values`;
- `access_debt_warnings`;
- `access_role_permissions`;
- `access_user_permissions`;
- `access_user_roles`;
- `access_audit_log`.

`access_policy_versions` хранит версию, период действия, статус, утверждение и причину изменения.

`access_policy_values` хранит параметры версии.

`access_debt_warnings` прямо поддерживает:

- `PARKING_ARREARS`;
- `PHONE_ACCESS_SUBSCRIPTION_ARREARS`.

Также сохраняются сумма долга, источник, версия политики, льготный период, дата возможного отключения и статус предупреждения.

Подтверждён поток:

```text
долг
→ действующая версия политики
→ предупреждение
→ льготный период
→ возможное отключение
```

Не подтверждён единый программный результат:

```text
ALLOW
ALLOW_WITH_WARNING
DENY
```

Не найден модуль, который гарантированно применяет разные правила к новому пульту, перепрошивке и телефонному доступу.

## 10. Уведомления

Единой общей таблицы `notifications` в подтверждённой схеме нет.

Вместо неё существуют специализированные структуры:

- `payment_notices`;
- `resident_payment_notices`;
- `commercial_notifications`;
- `access_debt_warnings`;
- `operator_task_queue`;
- `service_order_events`.

Фактическое состояние:

```text
оплата услуги
→ payment_notices / resident_payment_notices

коммерческий договор
→ commercial_notifications

долг по доступу
→ access_debt_warnings

задача оператору
→ operator_task_queue

история заказа
→ service_order_events
```

## 11. Адресаты

Для коммерческих уведомлений подтверждена адресатная таблица:

- `commercial_contract_recipients`.

Для общего заказа таблица вида `notification_recipients` не найдена.

Адресаты, вероятно, определяются через resident account, Telegram user, квартиру, staff principals и специализированные очереди, но общий алгоритм не подтверждён.

## 12. Подтверждённые модули

В `tools/cashier_v2_telegram/cashier_v2_ui.py` подтверждён выбор услуг по группам:

- remote;
- phone;
- common;
- parking;
- commercial;
- actual.

Полный набор модулей, создающих заказы, применяющих политики, создающих уведомления, формирующих адресатов и отправляющих Telegram-сообщения, в доступных файлах не найден.

## 13. Итоговая оценка

### Реализовано

- каталог и варианты услуг;
- шаблоны workflow;
- заказы;
- шаги;
- события;
- связи с начислениями и платежами;
- развитая модель пультов;
- развитая модель телефонного доступа;
- версии политик;
- долговые предупреждения;
- специализированные уведомительные структуры.

### Частично подтверждено

- переход interest → order;
- автоматическое создание шагов;
- применение политики к конкретному service item;
- определение адресатов;
- доставка уведомлений;
- единый интерфейс оператора.

### Не подтверждено

- единый Policy Engine;
- единый Notification Engine;
- единая таблица уведомлений;
- единая таблица адресатов;
- единый канонический поток для всех услуг.
