# Выпуск #723: каталог и готовность

Дата фиксации: 2026-11-24. Ветка: feature/full-yandex-mail-disk-723.

## Каталог операций

«Кандидатный каталог» ниже означает изолированный tools/list бинарника runtime-коммита 5321cd8f4c3c164f8ef53811e912cc8d4a309e2e при разрешённых только в отдельном процессе capabilities этого выпуска. Он не доказывает, что production-служба или ChatGPT уже показывают эти инструменты.

| Область | Реализовано | Есть в изолированном каталоге | Проверено на аккаунте/синтетике | Ограничение |
|---|---|---|---|---|
| Mail: папки, список, чтение, поиск и offset-пагинация, checkpoint, список черновиков | Да | Да | В первом read-only цикле список/чтение первого письма, поиск и offset=1 прошли для dedts, ykkareliadom и dvabobra2014; после паузы короткий запрос списка первого письма прошёл для всех трёх. Данные писем в отчёт не сохранялись. | В повторном цикле были TLS reset для dedts и timeout для dvabobra2014. Один вызов mail.checkpoint для dedts закрыл IMAP-соединение; для двух других прошёл. Состояние legacy watcher/checkpoint не читалось и не менялось. Это нестабильность проверяемого пути, а не доказательство необходимости новой авторизации. |
| Mail: создать/переименовать папку, удалить только пустую пользовательскую папку, copy/move, flags/bulk, корзина и спам с восстановлением | Да, с UIDVALIDITY guards и review | Да | Одна синтетическая E2E-попытка остановилась на первом `mail.folder.create:IO_ERROR`, сразу после успешных проверок каталога и `disk.info`. Остальные изменяющие операции не запускались. Последующая read-only проверка production-каталога папок dedts не нашла имен с префиксом `YACLI-723-`. | Повторять мутацию без диагностики ошибки и надёжного cleanup нельзя. В production нужны mail.mutate и отдельно mail.delete; последнее необратимо удаляет только созданную пустую тестовую папку. Другие почтовые данные не затрагивались. |
| Mail: создать/обновить/переместить черновик в Trash | Да, исходный draft защищён SHA-256/UIDVALIDITY; отправки draft нет | Да | Нет. Черновики и письма не изменялись. | mail.mutate; любое E2E создаёт draft только для адреса того же аккаунта и никогда не вызывает SMTP send. |
| Mail: send/reply/forward, включая нативные файлы ChatGPT; экспорт attachment и .eml | Да; review привязан к источнику и SHA-256 файлов | Да | Только unit/CI; ChatGPT file flow и исходящая отправка по аккаунтам не проверялись. | mail.send; живую отправку не делали. Экспорт требует разрешённый download root и review. |
| Mail: локальные правила list/audit/preview/configure/run/undo | Да; локальный runner выключен по умолчанию, baseline, пакет до 50 UID, audit и undo | Да | Unit/CI; production-правила не читались и не менялись; синтетический сценарий остановился раньше этапа правил на создании папки. | mail.read для чтения, mail.mutate для изменений. Это локальные yacli-правила, не серверные фильтры Яндекса. Legacy mail-watch/state.json не трогался. |
| Disk: info/list/read, проверка публичного доступа (permissions), upload/download, mkdir, copy/move, Trash list/move/restore | Да; потоковые transfer и read с лимитом 1 MiB | Да | Unit/CI; live Disk операции и synthetic checksum не запускались. | В текущей области Disk разрешён только dedts. ykkareliadom требует OAuth владельца; dvabobra2014 остаётся без Disk. Нужны disk.write и disk.delete; последнее перемещает только в обратимую Trash. Персональные права пользователей не поддерживаются. Крупные provider transfers не проверены. |
| Contacts/CardDAV: list/search/export/import/create/update/delete | Да; write review-gated, update/delete с ETag | Да | Только unit/CI; ни один CardDAV аккаунт не проверялся. | Требуются owner app-password, contacts.enabled=true, отдельный credential ref и capabilities contacts.read/write. Эти данные сейчас не настроены. |
| Calendar | В область не входит | Выключен | Не проверялся и не нужен | Не расширять. |
| Hosted Яндекс 360 MCP, резерв для dedts | Документирован отдельный безопасный маршрут и официальные ссылки в operator guide | Нет подключённого endpoint | OAuth и tools/list не выполнены | Нет токена, пользовательский OAuth обязателен; paid-route не включали и не объявляем подключённым. |

Фактический кандидатный tools/list содержал 48 названий. SHA-256 сериализованного списка: 7ec3a7e7f5c2e1ccee37c7576c570e2e8acca155d648b7156a973d3b853b7da9. Account enum/default и read-only annotations прошли. disk.publish и Calendar не выставлялись.

Имена, снятые с кандидатного каталога:

- Mail: yacli.mail.attachment.export, yacli.mail.bulk, yacli.mail.checkpoint, yacli.mail.copy, yacli.mail.draft.create, yacli.mail.draft.list, yacli.mail.draft.trash, yacli.mail.draft.update, yacli.mail.eml.export, yacli.mail.flags, yacli.mail.folder.create, yacli.mail.folder.delete, yacli.mail.folder.rename, yacli.mail.folders, yacli.mail.forward, yacli.mail.list, yacli.mail.move, yacli.mail.read, yacli.mail.reply, yacli.mail.rules.audit, yacli.mail.rules.configure, yacli.mail.rules.list, yacli.mail.rules.preview, yacli.mail.rules.run, yacli.mail.rules.undo, yacli.mail.search, yacli.mail.send, yacli.mail.send_published_link, yacli.mail.special_folder.
- Disk: yacli.disk.copy, yacli.disk.download, yacli.disk.info, yacli.disk.list, yacli.disk.mkdir, yacli.disk.move, yacli.disk.permissions, yacli.disk.read, yacli.disk.trash.list, yacli.disk.trash.move, yacli.disk.trash.restore, yacli.disk.upload.
- Contacts: yacli.contacts.create, yacli.contacts.delete, yacli.contacts.export, yacli.contacts.import, yacli.contacts.list, yacli.contacts.search, yacli.contacts.update.

## Приёмка и выкладка

- Runtime-кандидат: 5321cd8f4c3c164f8ef53811e912cc8d4a309e2e; CI-артефакт yacli 0.5.5, SHA-256 a1da2ed087d94aafdd97571d8743167a78c36cca24962d1a2deeefb28d352b34.
- Rust tests, clippy и Linux x86_64 build/smoke прошли на этом runtime-коммите. Полный workflow 45399751707 не получил зелёный итог: Homebrew job отменялся, точечный повтор остаётся в очереди. Полную матрицу повторно не запускали.
- Unit-тесты ops/devcat-yandex/test_install_verified.py: 7/7 прошли. Python syntax и git diff --check прошли.
- Кандидатный tools/list прошёл. Read-only сеть была нестабильна при повторных подключениях, затем краткий запрос списка писем восстановился на всех трёх аккаунтах. Одна review-gated синтетическая Mail+Disk цепочка была запущена, но остановилась на `mail.folder.create:IO_ERROR`; до черновиков и Disk мутаций не дошла. После неё production-проверка папок не обнаружила синтетического префикса. Изменяющие операции не приняты и не выкачены.
- Production yacli-devcat.service, Secure MCP Tunnel, production capability environment и ChatGPT каталог не менялись. Выпуск не выкатывался; ChatGPT acceptance не подтверждена.
- Подготовлен точный systemd override в ops/devcat-yandex/yacli-devcat-capabilities.conf. Он нужен для полного каталога и не меняет OAuth scopes, Disk roots или tunnel. disk.publish остаётся выключенным.
- Локальный процесс не имеет privileged host route: текущий UID 1000, sudo -n недоступен. Для выкладки уполномоченному оператору нужно после успешной приёмки применить capability override штатным root/controller маршрутом, затем выполнить ops/devcat-yandex/install-verified.py --manifest … --apply и проверить loopback/Tunnel/ChatGPT каталог. Не сливать PR автоматически.
- Issue dedtsss/agent-dispatch#723 прочитан через штатный `gh`; актуальная контрольная точка опубликована в [комментарии #7133698106](https://github.com/dedtsss/agent-dispatch/issues/723#issuecomment-7133698106). PR #5 синхронизирован прямым `gh` маршрутом; прежняя ошибка connector не является текущим ограничением.

Pull request #5 остаётся draft и не объединён. Изменений в рабочее дерево /home/codex/yandex-mcp-551 и OCR-задачу не вносилось.
