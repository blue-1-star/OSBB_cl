<!-- BEGIN: DEVELOPMENT-DOCS-V1 -->
# Assistant Development

## Назначение

Assistant должен стать единым инженерным интерфейсом проекта OSBB, а не
набором разрозненных скриптов.

## Цели

Assistant постепенно объединяет:

- Environment Detector;
- Project Doctor;
- Document Installer;
- Patch Engine;
- Diff Analyzer;
- Backup Engine;
- Query Engine;
- Verification Engine;
- журнал инженерного опыта.

## Базовый принцип

Assistant должен объяснять, что обнаружено, что будет изменено, почему это
безопасно, что изменилось и как проверить или отменить результат.

## Этапы

### 0.x — прототипы

Отдельные сервисные скрипты, первые проверки окружения, установка документации,
патчи и проверка файлов.

### 1.x — единая консоль

```text
assistant doctor
assistant env
assistant where
assistant check
assistant diff
assistant backup
assistant apply
assistant verify
assistant log
```

### 2.x — Query Engine

```text
assistant query list
assistant query show <name>
assistant query run <name>
```

### 3.x — сопровождение документации

```text
assistant docs check
assistant docs install
assistant docs verify
assistant docs lesson
assistant docs roadmap
assistant docs adr
```

## Обязательные свойства

- идемпотентность;
- dry-run;
- проверяемость;
- понятная диагностика;
- обратимость опасных изменений.

## Ближайший технический долг

- стабилизировать поиск project root и Git root;
- стандартизировать Windows-пути;
- добавить `doctor`, `env`, `where`, `verify`;
- исключить ручной поиск файлов пользователем.
<!-- END: DEVELOPMENT-DOCS-V1 -->
