[English](quickstart.md) · **Русский**

# Быстрый старт

Используйте Python 3.13. Клонируйте `https://github.com/k4raga/merovinger-memory`,
создайте и активируйте виртуальное окружение, затем выполните:

~~~sh
python -m pip install .
~~~

Импортируйте `agent_native_memory`. Имя пакета и namespace импорта намеренно
различаются: импорта `merovinger_memory` нет.
В [README](../README.ru.md#подключение) приведён полный небольшой пример.

## Явное сжатие

Используйте объект `memory` из README.

`prepare()` возвращает `empty`, `ready`, `needs_compaction` или `cannot_fit`.
При `allow_compaction=False` необязательные целые группы могут не включаться
в контекст, чтобы уложиться в лимит; обязательная группа никогда не обрезается
молча. `needs_compaction` содержит ограниченный вход для сжатия,
а не готовую историю для основного вызова модели.

~~~python
prepared = memory.prepare(byte_limit=4096)
if prepared.status == "needs_compaction":
    job = memory.begin_compaction(prepared)
    # Приложение вызывает свой summarizer с job.input.
    # Здесь текст задан напрямую; реальная модель не вызывается.
    text = "В предыдущих сообщениях обсуждался общий план."
    outcome = memory.complete_compaction(
        job.job_id, text, expected_revision=job.revision
    )
    if outcome == "applied":
        context = memory.prepare(byte_limit=4096, allow_compaction=False)
~~~

Проверяйте исход: `applied`, `duplicate`, `collision` и `stale` имеют разные
значения. Устаревший результат не считается опубликованным.
Если исход внешнего вызова неизвестен, вызовите `memory.mark_unknown(job.job_id)`
и выясните его явно. Не объявляйте неизвестность окончательной ошибкой
только ради повторного вызова.

## Пример сохранения

Исполняемый пример содержит полную историю, защищённую открытую группу,
одну принятую сводку и попытку UNKNOWN. Фиксированный текст сводки
предоставляет вызывающее приложение.

Оболочка Linux/macOS после установки:

~~~sh
mkdir -p .runtime/example
python -I -B examples/checkpoint.py --smoke --report "$PWD/.runtime/example/create.json" --checkpoint "$PWD/.runtime/example/chat.json"
python -I -B examples/checkpoint.py --restore-smoke --report "$PWD/.runtime/example/restore.json" --checkpoint "$PWD/.runtime/example/chat.json"
~~~

Windows PowerShell после активации окружения:

~~~powershell
New-Item -ItemType Directory -Force .runtime/example | Out-Null
$directory = (Resolve-Path .runtime/example).Path
python -I -B examples/checkpoint.py --smoke --report "$directory/create.json" --checkpoint "$directory/chat.json"
python -I -B examples/checkpoint.py --restore-smoke --report "$directory/restore.json" --checkpoint "$directory/chat.json"
~~~

Команды запускают два отдельных процесса. Второй не изменяет снимок.
Пример записывает обычные файлы вызывающего приложения, не реализуя
сохранность при сбое или надёжные транзакции.
Не коммитьте снимки реальных диалогов.
