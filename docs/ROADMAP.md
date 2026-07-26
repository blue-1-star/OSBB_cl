# OSBB Roadmap

<!-- RESIDENT_IDENTITY_REFACTOR_V1:BEGIN -->

## P1. Resident Identity Refactor

**Status:** planned  
**Date decided:** 2026-07-01  
**Reason:** current resident/client/admin flow mixes several different concepts and creates confusing behavior.

### Problem

The current legacy flow around "Режим мешканця", "Змінити квартиру", apartment binding, resident verification, and admin testing is mixed.

It currently blends together:
- resident self-service cabinet;
- apartment binding and verification;
- operator work on behalf of a resident;
- super-admin diagnostic view;
- real Telegram ID to apartment binding.

This creates unclear states, especially for super-admins and operators.

### Decision

Split the old mixed flow into three separate flows.

#### 1. Resident Cabinet
Real resident acts for themselves. Resident can:
- view and confirm their apartment;
- maintain personal contact data;
- add, edit, or request changes to vehicles;
- submit remote/pult requests;
- see charges, payments, and service history.

#### 2. Operator Workspace
Operator acts on request of a resident, but never becomes that resident. All actions must be auditable.

#### 3. Super-admin Diagnostic Resident View
Super-admin can temporarily view the bot as a chosen apartment/resident for diagnostics.

### Legacy rule
The existing flow around "Змінити квартиру" and "Режим мешканця" is considered legacy and should not be expanded.

<!-- RESIDENT_IDENTITY_REFACTOR_V1:END -->

<!-- CORE_NEW_LAYER_V1:BEGIN -->

## P2. New Architecture Layer (core_new)

**Status:** completed (2026-07-13)

### What was done

Created a new architectural layer `core_new/` that separates business logic from interface code.

#### 1. Adapters
Created `DBAdapter` — a wrapper around the legacy `Bots/db_access.py`.

#### 2. Domain Models

| Model | Status | Tests |
|-------|--------|-------|
| `Vehicle` | ✅ | ✅ |
| `Resident` | ✅ | ✅ |
| `Apartment` | ✅ | ✅ |
| `Payment` | ✅ | ✅ |
| `VehicleCandidate` | ✅ | ✅ |

#### 3. Testing

All models have working tests in `tests/`.

#### 4. Documentation

- [ADR-2026-07-13-core-new-layer.md](Architecture/ADR-2026-07-13-core-new-layer.md)
- [Domain models](../Domain/README.md)
- [core_new code docs](../Code/core_new.md)

### Next steps

1. Replace legacy calls with new domain models
2. Integrate all models into Telegram bot
3. Remove duplicate code

<!-- CORE_NEW_LAYER_V1:END -->

<!-- CASHIER_VEHICLE_CANDIDATE_V1:BEGIN -->

## P3. Cashier and Vehicle Candidate (В работе)

**Status:** in progress (2026-07-14)

### Problem

Cashier cannot accept payments for vehicles without an apartment.

### Decision

Create a new entity: `vehicle_candidates`.

### Lifecycle

PENDING -> RESOLVED -> vehicle created in vehicles
PENDING -> REJECTED

### Next steps

1. Integrate `vehicle_candidates` into cashier UI
2. Add operator workspace for reviewing candidates
3. Link payments to candidates via `candidate_id`

<!-- CASHIER_VEHICLE_CANDIDATE_V1:END -->

<!-- OSBB-DOCS:BEGIN finance-core -->
## Finance Core

**Architectural direction:** separate money movement from service accounting.

Planned stages:

1. Inventory current receipt, expense, allocation, and balance paths.
2. Introduce explicit partial and unallocated receipt amounts.
3. Store allocations independently from the original money transaction.
4. Support auditable reallocation, advance, refund, and correction operations.
5. Connect service balances to allocation history.
6. Add reconciliation and audit reports.

Implementation details will be recorded in separate ADRs.
<!-- OSBB-DOCS:END finance-core -->

<!-- BEGIN: DEVELOPMENT-DOCS-V1 -->
## Development Docs v1

- [x] Создать `Docs/Development`.
- [x] Зафиксировать инженерные принципы.
- [x] Создать Lessons Learned и Troubleshooting.
- [x] Зафиксировать окружение.
- [x] Описать Presentation Layer и Query Library.
- [ ] Реализовать `assistant doctor`.
- [ ] Реализовать `assistant env`.
- [ ] Реализовать `assistant where`.
- [ ] Реализовать `assistant verify`.
- [ ] Научить Assistant сопровождать инженерную документацию.
<!-- END: DEVELOPMENT-DOCS-V1 -->


# Roadmap — OSBB_cl / Касса

**Назначение:** то, что сформулировано, обсуждено и признано нужным, но **не реализовано**. Сгруппировано по темам, не по приоритету — приоритет расставлять по факту, когда до пункта дойдёт очередь (по опыту этого проекта — реальные случаи ввода часто сами показывают, что действительно нужно первым).

---

## Финансовая логика (Finance Core)

### Автоматическое определение периода оплаты
Сейчас период платежа выбирается вольно — оператором или на основании слов плательщика ("плачу за август"). Согласован принцип: система должна **сама** определять самый давний неоплаченный период по истории платежей, а не доверять произвольному заявлению. Требует отдельного проектирования (как определять "неоплаченный период" по каждому конкретному авто/квартире, что делать при частичных оплатах).

### Пазл суммы ↔ тарифов — уже частично реализовано, есть куда расширять
Реализовано: подмножество авто квартиры + кратность месяцев. Не реализовано: связь с "минимальным неоплаченным периодом" выше, обработка исторических изменений тарифа (если тариф менялся в середине проверяемого диапазона).

---

## Классификация качества данных

### Единая система кодов полноты/корректности данных
Предложена пользователем иерархия кодов не только для платежей, а для **любой сущности** в базе:
- **АВТО:** статус (активен/архив), не паркуется, проверить номер, режим парковки, привязка квартиры, ФИО, марка, цвет, другое.
- **КВАРТИРА:** владелец/арендатор/родственник, площадь, тип (жилой/нежилой/техническое/коммерческое), реконструкция (объединённые квартиры).
- **ФИО:** написание на трёх языках, статус (владелец/арендатор/родственник), телефон, telegram id, viber.
- Задел на будущее расширение: несовершеннолетние дети (количество), домашние животные, льготы/пенсионер.

`verification_journal.issue_type` — узкий частный случай этой же идеи, ограниченный моментом ввода платежа. Не спроектировано: как эти две вещи соотносятся структурно (объединять или оставить раздельными).

**Решение:** не проектировать заранее, дать реальным случаям накопиться при обычной работе с вводом платежей.

---

## Журнал согласования (verification_journal) — доработки

### Автоматическое закрытие записей
Сейчас `resolve_verification_task()` — чисто пассивная, ручная функция. Причина попадания записи в журнал может быть устранена где угодно (кассир/админ/охранник, в любом другом месте системы), и никто не обязан вспомнить связать "я починил X" с "а по X есть открытая запись". Иначе записи копятся вечно.

Два пути, не выбран ни один:
1. **Дешёвый:** кнопка "закрыть связанные записи" непосредственно в месте починки.
2. **Дорогой, но настоящий:** запись ссылается на конкретную сущность и поле (не свободный текст) — тогда можно периодически перепроверять условие и снимать запись автоматически.

### Связь с удалёнными платежами
`related_payment_id` в записях журнала сейчас всегда `NULL` (не заполняется ни в одном сценарии). Открытый вопрос: если платёж, к которому теоретически привязана запись, удалён хирургически — должна ли запись как-то отреагировать (закрыться автоматически / остаться открытой для проверки)?

### Согласование со структурой из profile_verification_core.py
Найден действующий, проверенный прецедент того же паттерна (`resident_profile_change_requests`, статусы `PENDING_OPERATOR → APPROVED/REJECTED/ACCEPTED_MANUAL`). Не решено: перенимать ли эту структуру для `verification_journal`, объединять ли обе подсистемы в одну общую "подсистему согласования".

### Роли и адресация уведомлений
Упомянута система ролей ("супервайзер", который раздаёт уведомления разным сторонам процесса — охраннику, кассиру, админу). Сейчас `assigned_role` в `verification_journal` есть как поле, но реальной логики показа "нужному человеку нужное" не реализовано — только фильтрация по одной роли в `list_open_verification_tasks()`.

---

## Пользовательский интерфейс кассы

### Сопроводительная карточка квартиры ("тень платежа")
При приёме платежа кассир должен сразу видеть контекст квартиры: оплачено/не оплачено по периодам, долг/переплата, открытые вопросы по авто и тарифам — не отходя от кассы, в момент разговора с жильцом. По сути — агрегатор уже существующих кусков (`parking_debtors()`, `apartments_with_missing_parking_mode()`, решатель пазла), а не новая логика. Подтверждена как насущная минимум двумя живыми случаями (кв.31 с третьим авто, кв.32 со спорной привязкой).

### Облегчённое редактирование уже внесённого платежа
Нужна возможность поправить сумму / привязку к авто / период у **уже существующего** платежа — без повторного проведения факта оплаты и без полноценного хирургического удаления. Отдельная, третья операция (наряду с "новый платёж" и "хирургическое удаление").

### Экран создания нового тарифа
Есть экран **изменения** существующего тарифа (`💵 Тарифы`). Экрана **создания нового** тарифа (новый `service_code`, не правка суммы существующего) — нет. Место размещения не определено.

### Расширение поиска фирм по второму имени
У коммерческого юнита оказалось два разных имени одновременно: `apartments.display_name` ("Комбинат") и `commercial_contracts.counterparty_name` ("Макаревич"). Текущий фильтр `🏢 Фирма` ищет только по второму. Не реализовано: поиск по обоим сразу.

---

## Общая чистка / инфраструктура (низкий приоритет, сознательно отложено)

- Судьба папки `Runner/` — не разбирались, отложено при переключении фокуса на кассу.
- `commercial_contract_editor.py` — упомянут как вероятный редактор коммерческих договоров, содержимое не проверено.
- Известные ограничения хирургического удаления из `Surgical_Delete.md`, ещё не устранённые: нет `dry-run`, нет автоматической проверки известных внешних ссылок, позиционное (не гарантированное) сопоставление платёж↔чек при пакетном удалении, не утверждена политика хранения исходного аудита при удалении.
- Пакетный сценарий хирургического удаления (`collect_batch_chain`, несколько платежей разом) — по признанию пользователя, ни разу не проверялся вживую, только одиночный.
