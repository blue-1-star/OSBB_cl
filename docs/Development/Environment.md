<!-- BEGIN: DEVELOPMENT-DOCS-V1 -->
# Development Environment

## Рабочая система

- Windows
- Visual Studio Code
- PowerShell

## Рабочий каталог

```text
G:\Programming\Py
```

## Структура

```text
G:\Programming\Py\
├── .git\
├── OSBB\
│   └── Docs\
└── OSBB_util\
    ├── Scripts\
    └── Patches\
```

## Project root

```text
G:\Programming\Py\OSBB
```

## Git root

```text
G:\Programming\Py
```

Project root и Git root — разные каталоги.

## Соглашения

Команды пишутся для PowerShell. Использовать `$env:USERPROFILE`, а не
`%USERPROFILE%`.

Предпочтительный запуск:

```powershell
python .\OSBB_util\Scripts\<script_name>.py
```

Перед операцией Assistant должен вывести текущий каталог, Git root, project
root, Python, обязательные файлы и каталог назначения.
<!-- END: DEVELOPMENT-DOCS-V1 -->
