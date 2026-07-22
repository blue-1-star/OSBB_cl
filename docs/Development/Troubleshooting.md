<!-- BEGIN: DEVELOPMENT-DOCS-V1 -->
# Troubleshooting

## Шаблон записи

```text
## Название проблемы
### Симптомы
### Причина
### Диагностика
### Решение
### Предотвращение
```

## Assistant не запускается из ожидаемого каталога

### Симптомы

Python не находит файл, относительные пути ведут не туда, пользователь вручную
ищет нужный файл.

### Возможные причины

- команда запущена не из `G:\Programming\Py`;
- смешаны project root и Git root;
- используется жестко заданный путь;
- команда написана для `cmd.exe`, а запускается в PowerShell.

### Диагностика

```powershell
Get-Location
Test-Path .\OSBB
Test-Path .\OSBB_util
Get-ChildItem .\OSBB_util\Scripts
git rev-parse --show-toplevel
python --version
```

### Решение

Запускать из `G:\Programming\Py`, выводить обнаруженные пути и при необходимости
передавать `--project-root`.

### Предотвращение

Добавить `assistant where`, `assistant env`, `assistant doctor`.

## Документатор не находит Docs

### Решение

```powershell
python .\OSBB_util\Scripts\development_docs_v1.py `
  --project-root .\OSBB
```

## Повторный запуск дублирует документацию

Установщик должен использовать управляемые маркеры и заменять только содержимое
между ними, сохраняя ручной текст вне блока.
<!-- END: DEVELOPMENT-DOCS-V1 -->
