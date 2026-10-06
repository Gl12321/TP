# Контракт браузера и приложения

Браузер получает факты, историю запросов и обсуждения из одного API. Модель работает в отдельном процессе; открытая вкладка не является владельцем задания. Все идентификаторы — строки UUID. Даты и время — ISO 8601, время UTC. Денежные значения передаются строками. Списки — JSON-массивы.

Базовый путь `/api/v1`. Ошибка имеет вид `{"error":{"code":"...","message":"..."}}`. Авторизация — HttpOnly cookie `razbor_session`. После входа клиент передаёт `X-CSRF-Token` из session во всех изменяющих запросах. Cookie отправляется с `credentials: "include"`.

Документ описывает договор текущего браузера и сервера. Входные ограничения объявлены в схемах каждого `api/`-модуля; JSON-спецификация доступна на `/api/openapi.json`, Swagger UI — на `/api/docs`. Последний использует CDN для своих ресурсов; приложение и JSON-спецификация работают без него. Правила авторизации и транзакций разобраны в [руководстве бэкенда](../docs/development/backend.md).

## Общие правила обмена

Обычная успешная операция возвращает 200, создание — 201, постановка вопроса или обновления отчёта в очередь — 202. Полученный 202 означает сохранённое задание, а не готовую таблицу. Все изменяющие JSON-запросы используют `Content-Type: application/json`. Неизвестные поля отклоняются; предельное фактическое тело запроса — два МиБ.

| HTTP | Типичная причина | Действие клиента |
| --- | --- | --- |
| 400 | Неверный период, курсор или прикладные условия | Исправить входные данные |
| 401 | `unauthenticated`, `invalid_credentials` | Показать вход или ошибку формы |
| 403 | `forbidden`, `scope_forbidden`, `source_forbidden`, `csrf`, `origin` | Обновить сессию/права; не повторять действие автоматически |
| 404 | Объект отсутствует либо недоступен в данном пространстве | Убрать недействующую ссылку; не раскрывать наличие чужого объекта |
| 409 | `version_conflict`, `idempotency_conflict`, `conversation_busy`, `source_not_ready` | Обновить состояние и устранить конкретный конфликт |
| 413 | `body_too_large` | Уменьшить тело запроса |
| 422 | `validation` или неполный аналитический результат из-за лимита | Показать ошибки полей либо предложить сузить область |
| 429 | Ограничение входа или квота активных заданий | Дождаться освобождения ресурса |
| 503 | `worker_unavailable`, `source_connection`, `source_busy` | Показать недоступный компонент, сохранив доступ к остальному интерфейсу |

Для `validation` ответ дополнительно содержит `error.fields:[{field,message}]`; `field` — путь поля через точку. Клиент ориентируется на `code`, а `message` показывает человеку. Ответы API имеют `Cache-Control: no-store`. Заголовок `X-Request-ID` помогает связать конкретный ответ с обращением по работе API.

## Вход

| Метод и путь | Тело / результат |
| --- | --- |
| GET `/auth/status` | `{bootstrap_required, bootstrap_token_required}` |
| POST `/auth/bootstrap` | `{email,password,name,workspace_name,bootstrap_token?}` → session |
| POST `/auth/login` | `{email,password}` → session |
| GET `/auth/session` | `{user:{id,email,name},csrf_token,workspaces:[{id,name,role,capabilities,all_stores,store_ids}]}` |
| POST `/auth/logout` | `{ok:true}` |
| POST `/auth/password` | `{current_password,new_password}` → новая session; прежние сессии отозваны |
| POST `/auth/sessions/revoke` | `{ok:true}`; отзывает остальные сессии |
| POST `/auth/invitations/accept` | `{token,name?,password?}` → session; существующий аккаунт должен войти под приглашённым адресом, новому нужны имя и пароль |
| POST `/workspaces` | `{name}` → workspace; текущий пользователь становится владельцем нового пространства |
| GET `/health` | `{status,worker_ready}` |

`/health` доступен также без `/api/v1`. Отдельный абсолютный маршрут `/ready` возвращает `{status:"ready"}` либо 503 `{status:"not_ready"}` при недоступной прикладной таблице `app_setup`. Он не подтверждает готовность моделей, внешних источников или каждой миграции.

Роли: `director`, `regional_manager`, `franchise_owner`, `store_manager`, `analyst`, `admin`. Возможности: `analytics:read`, `assistant:use`, `reports:write`, `cases:write`, `plans:write`, `metrics:write`, `sources:manage`, `members:manage`. Интерфейс проверяет capabilities; API повторяет проверку независимо.

Новый пароль — от 12 до 256 символов; значимые пробелы сохраняются. Вход, bootstrap и принятие приглашения возвращают session и устанавливают cookie. При смене пароля клиент заменяет сохранённый CSRF-токен значением новой session. Bootstrap доступен один раз на прикладную БД. PATCH участника принимает идентификатор членства `id`, а `assignee_id` и `reader_ids` используют идентификатор пользователя `user_id`. Владельца нельзя ограничить через обычный PATCH участника (`owner_protected`).

## Пространство

Далее все пути начинаются с `/workspaces/{workspace_id}`.

| Метод и путь | Тело / результат |
| --- | --- |
| GET/POST `/stores` | `{id,name,code,city,owner_name,active}`; создание без id |
| GET `/stores/{id}/analytics?metric_id=...&date_from=...&date_to=...&grain=day` | `{store,metric,date_from,date_to,grain,series:[{date,value,weight}],warnings,calculation:{sql,execution},captured_at}`; grain `day`/`week` |
| PATCH `/stores/{id}` | `{name?,city?,owner_name?,active?}` |
| GET/POST `/metrics` | `{id,key,name,description,unit,source_id,table_schema,table_name,value_column,date_column,store_column,aggregation,version}`; aggregation `sum`/`count`/`avg`; при count value_column необязателен |
| GET/POST `/plans` | `{id,store_id,metric_id,period,amount,version}`; period `YYYY-MM-01`; повторное сохранение создаёт редакцию |
| GET `/overview?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&metric_id=...&store_ids=...` | `{date_from,date_to,metric,totals:{actual,plan,attainment},stores:[{store_id,name,city,actual,plan,attainment,status}],coverage:{available,total},warnings:[]}`; null отличается от нуля |
| GET/POST `/sources` | `{id,name,host,port,database,username,schemas,enabled,reader_ids,catalog_version,status,last_checked_at,error}`; POST также password, ssl_mode (`require`/`verify-full`/`disable`) |
| POST `/sources/{id}/test` | Проверка доступа и обновление каталога → source |
| PATCH `/sources/{id}` | `{name?,host?,port?,database?,username?,password?,schemas?,ssl_mode?,enabled?,reader_ids?}` → source; существенное изменение требует новой проверки |
| GET `/sources/{id}/catalog` | `[{schema,name,columns:[{name,data_type}],policy:{columns,store_column,shared}}]` |
| PUT `/sources/{id}/policies` | `{tables:[{schema,name,columns:[...],store_column:null,shared:false}]}`; пустой policy запрещает таблицу |
| GET/POST `/members` | `{id,user_id,email,name,role,all_stores,store_ids,active,data_access}`; создание `{email,name,password,role,all_stores,store_ids,data_access}` |
| PATCH `/members/{id}` | `{role?,all_stores?,store_ids?,active?,data_access?}` |
| GET `/participants?store_ids=...` | `[{id:user_id,name,role}]`; действующие участники с доступом ко всей выбранной области; требуется cases:write |
| GET `/invitations` | `[{id,email,role,expires_at}]`; только действующие приглашения |
| POST `/invitations` | `{email,role,all_stores,store_ids,data_access}` → `{id,email,expires_at,token}`; токен показывается один раз, срок семь дней |
| POST `/invitations/{id}/revoke` | `{ok:true}` |

store_ids в GET — список UUID через запятую. Источник доступен ассистенту только после успешной проверки и настройки разрешённых таблиц. Неограниченные справочники требуют явного `shared:true`; таблица фактов требует store_column с внешним кодом точки (`Store.code`).

Тело POST источника: `{name,host,port?,database,username,password,schemas,ssl_mode?,reader_ids?}`. Поля `enabled`, версии и состояние создаёт сервер, передавать их в POST нельзя. Политика PUT полностью заменяет набор таблиц: отсутствие таблицы запрещает её, `tables:[]` закрывает весь каталог. Для разрешённой таблицы нужен непустой список колонок и ровно один вариант ограничения: `store_column` либо `shared:true`.

Проверка подключения возвращает профиль со статусом `ready` либо `error`; прикладная ошибка подключения может быть представлена HTTP 200 с `status:"error"` и `error`. Клиент проверяет состояние профиля, а не только HTTP-код. Изменение параметров подключения переводит профиль в `unchecked`; изменение только названия не меняет версии, изменение списка читателей меняет версии доступа без необходимости повторной проверки соединения.

Источник возвращает также `ssl_mode`, `policy_revision` и `permitted_table_count` — число настроенных разрешённых таблиц. Пользователь без `sources:manage` получает только id, name, enabled, status, catalog_version, permitted_table_count, last_checked_at. Аналитик получает каталог разрешённых колонок, администратор подключения — каталог для настройки политики. Нулевой `permitted_table_count` означает, что чтение ещё не настроено, даже если соединение успешно проверено.

`reader_ids: null` разрешает профиль всем участникам с `analytics:read`; массив ограничивает его указанными user_id, пустой массив закрывает чтение. Владение пространством не обходит этот список. Технический администратор продолжает обслуживать профиль, но не получает его финансовые результаты. Для `sources:manage` список источников содержит `can_read`; изменение списка читателей отзывает доступ и к сохранённым расчётам этого профиля.

В overview `date_to` включительно. `comparison` — null либо `{date_from,date_to,current,previous,delta,change_percent,comparable_count}`. Полный календарный месяц сравнивается с предыдущим календарным месяцем; блок полных месяцев — с предшествующим блоком того же числа месяцев. Для произвольного диапазона предыдущий период имеет ту же длительность в днях. Значения рассчитаны по совпадающему составу точек. В строках stores дополнительно `previous_actual`, `change_percent`, status `has_rows`/`no_data`. В totals дополнительно `planned_store_count`; отношение факта и плана вычисляется только по совпадающей области. Для средних общая сумма планов отсутствует. `coverage.available` означает наличие строк, а не доказанную полноту периода.

Обзор также возвращает `cities` с группировкой выбранной области по городам. Текущий и предыдущий периоды считываются одним SQL-запросом. Дневная динамика содержит все дни диапазона: `value:null` означает отсутствие значения, `0` — рассчитанный ноль. Суммы и средние по городам используют те же определения и веса, что общий обзор.

При выполненном расчёте обзор содержит `captured_at` и `calculation:{sql,execution}`. В строках точек есть `actual_weight` и `plan_versions` — редакции планов, на которых основано сравнение. Если нет показателя или доступных активных точек, возвращается пустое состояние с `metric:null` либо выбранным показателем, пустыми `stores` и предупреждением; `cities` и `calculation` в этом случае могут отсутствовать. Период обычной аналитики и измерения ограничен двумя годами.

POST показателя с прежним `key` создаёт новую версию с новым `id`; POST плана — новую редакцию для сочетания точки, `metric_id` и месяца. Методы изменения/удаления сохранённой версии не предусмотрены. GET возвращает последние редакции. Создание и изменение точки требует `members:manage`, показателя — `metrics:write`, плана — `plans:write`; обычное чтение аналитики требует `analytics:read`. Управление источниками требует `sources:manage`. Точные роли и сочетание с областью — в [описании доступа](app/access/README.md).

## Ассистент и отчёты

| Метод и путь | Тело / результат |
| --- | --- |
| GET/POST `/conversations` | `{id,title,version,created_at,updated_at}`; POST `{title?}` |
| PATCH `/conversations/{id}` | `{title}` → conversation с увеличенной версией; только владелец личного диалога |
| GET `/conversations/{id}` | conversation + `{messages:[{id,role,content,run_id,created_at}],runs:[run]}` |
| POST `/conversations/{id}/messages` | `{question,source_id,store_ids:[],base_run_id:null,version,idempotency_key,date_from?,date_to?,metric_id?}` → run |
| GET `/runs/{id}` | `{id,conversation_id,question,source_id,store_ids,status,stage,sql,result,error,clarification,created_at,finished_at,conversation_version}` |
| GET `/runs/{id}/events?after=0` | SSE `id: sequence`, `event: update`, `data: {sequence,status,stage,message}`; GET run — источник актуального состояния |
| POST `/runs/{id}/cancel` | run |
| GET/POST `/reports` | `{id,title,description,run_id,created_at,created_by}`; POST `{title,description?,run_id,idempotency_key?}` |
| GET `/reports/{id}` | report + `{run,refreshes:[run],inaccessible_refreshes}`; история обновлений по времени создания |
| POST `/reports/{id}/refresh` | `{idempotency_key}` → новый run, выполнение сохранённого SQL без генерации |

Статусы run: `queued`, `running`, `cancel_requested`, `cancelled`, `succeeded`, `failed`, `needs_input`, `rejected`. result: `{columns:[{name,type}],rows:[[...]],truncated,row_count,execution:null|{sql,parameters}}`. `run.sql` хранит нормализованный SQL расчёта. `result.execution` показывает фактически исполненный запрос после ограничения таблиц разрешёнными строками и полями; parameters — значения параметров PostgreSQL `$1` и далее. Секретов подключения здесь нет. `error:{code,message}` — безопасная причина ошибки. result null до успешного завершения. clarification `{code,message}`. version проверяется при отправке, конфликт возвращает 409. Идемпотентный повтор возвращает прежний run, другой вопрос с тем же ключом — 409.

Вопрос имеет длину до 4000 символов, заголовок диалога — до 200. `idempotency_key` обязателен, от 8 до 100 символов. `row_count` — число возвращённых строк, а не полное число совпадений источника. `truncated:true` означает ограниченную выдачу. `cancel` разрешён автору запуска; для конечного состояния повтор возвращает его без изменений. В очереди отмена сразу даёт `cancelled`, во время работы сначала `cancel_requested`.

SSE принимает также `Last-Event-ID`, продолжает с максимального значения его и `after`, отправляет `: keepalive` во время ожидания. При отзыве сессии или прав приходит `event: access_revoked` с `{}` и поток закрывается. Финальное состояние нужно получать через GET запуска: SSE сообщает этапы, а не всю таблицу. Закрытие вкладки или обрыв потока не являются отменой.

Run дополнительно содержит `context` с выбранными date_from/date_to/metric_id, версиями показателя, каталога и политики. Это контекст пользователя, не доказанный результат разбора SQL. Оба края периода передаются вместе; конец включителен. Продолжение с отсутствующими полями периода или показателя наследует их от base_run_id; явно переданный null очищает выбор, для периода — обе границы одновременно. Область точек задаётся в каждом сообщении через store_ids, пустой список выбирает все доступные активные точки. Для ответа на уточнение используется base_run_id запуска needs_input и новые выбранные условия. При недоступном worker создание нового запуска возвращает 503 `worker_unavailable`; сохранённые результаты остаются доступны.

Обновление отчёта сохраняется в его истории независимо от открытой вкладки и автора обновления. Исходный снимок не заменяется. Доступ проверяется для каждой попытки отдельно; `inaccessible_refreshes` сообщает число скрытых попыток без раскрытия результата. Чтобы создать разбор из чужого общего отчёта, передайте его `report_id` вместе с `run_id`: наличие ссылки на отчёт проверяется на сервере и не делает личный диалог общим.

## Обсуждения

| Метод и путь | Тело / результат |
| --- | --- |
| GET/POST `/cases` | `{id,title,description,status,store_ids,run_id,created_by,assignee_id,conclusion,created_at,updated_at}`; POST `{title,description?,store_ids,run_id?,report_id?,assignee_id?,measurement?:{metric_id,date_from,date_to},idempotency_key?}` |
| GET `/cases/{id}` | case + `{comments:[{id,author_id,author_name,body,created_at}],questions:[{id,body,assignee_id,status,answer,created_at}],measurements:[measurement]}` |
| POST `/cases/{id}/measurements` | `{date_from,date_to,idempotency_key}` → новый снимок по исходному определению показателя |
| POST `/cases/{id}/comments` | `{body}` → comment |
| POST `/cases/{id}/questions` | `{body,assignee_id}` → question |
| POST `/cases/{id}/questions/{question_id}/answer` | `{answer}` → question |
| POST `/cases/{id}/close` | `{conclusion}` → case |
| GET `/notifications` | `[{id,kind,title,body,case_id,source_issue_id,read_at,created_at}]`; заполнена ровно одна ссылка |
| POST `/notifications/{id}/read` | `{ok:true}` |

Получатель обсуждения должен иметь доступ ко всей его области. Прикреплённый запуск проверяется отдельно. Личный диалог никогда не становится общим автоматически.

`/participants` подбирает участников по области точек. При создании назначения или адресного вопроса сервер дополнительно проверяет доступ адресата к прикреплённому запуску и всем измерениям карточки, включая список читателей источника. Недоступный адресат даёт 403 `recipient_scope`; карточка, измерение и уведомление такого создания не сохраняются.

GET case также содержит `run:null|run` для разрешённого прикреплённого результата. Адресный вопрос возвращает также `created_by`; его состояния — `open` и `answered`, состояния разбора — `open` и `closed`. Ответить может только адресат, закрыть — автор, ответственный, `director` или `regional_manager` с доступом к карточке. После закрытия новые комментарии, вопросы и ответы запрещены. Отдельного маршрута повторного открытия разбора нет.

Список уведомлений рассматривает последние 100 записей адресата и скрывает недоступные цели; курсорная пагинация для него не предусмотрена. API не отправляет почту: браузер формирует ссылку принятия приглашения с токеном во фрагменте URL, чтобы он не попадал в журнал HTTP-пути.

Список разборов добавляет `pending_for_me` и `pending_assignee_ids` для неотвеченных адресных вопросов. `measurement` содержит `{id,metric,store_ids,date_from,date_to,created_at,created_by,overview,change_from_initial}`. Первое измерение сохраняет факты, план и определение в одной карточке; последующее использует то же определение и добавляет сравнение с начальным снимком. Оно не перезаписывает историю. После закрытия повторное измерение может добавить автор разбора. Сужение прав на точки или источник повторно ограничивает чтение измерений.

## Технические обращения

Если проблема в подключении или свежести данных, участник обращается к администратору через профиль источника. Этот сценарий передаёт описание и состояние подключения; финансовый результат, SQL и область точек автоматически не прикрепляются. Администратору достаточно `sources:manage`, доступ к аналитике не требуется.

| Метод и путь | Тело / результат |
| --- | --- |
| GET `/source-issues/participants` | `[{id:user_id,name}]`; действующие участники с `sources:manage` |
| GET `/source-issues` | Страница доступных обращений по времени изменения; filter `all`/`active`/`mine`/`resolved`, поиск q |
| POST `/source-issues` | `{source_id,title,body,assignee_id?,idempotency_key?}` → обращение со статусом `open` |
| GET `/source-issues/{id}` | Обращение + `comments:[{id,author_id,author_name,body,created_at}]` |
| POST `/source-issues/{id}/comments` | `{body}` → комментарий |
| PATCH `/source-issues/{id}` | `{status?,assignee_id?,resolution?}` → обращение; только `sources:manage` |

Обращение: `{id,source_id,source_name,title,body,status,created_by,assignee_id,resolution,source_status,source_error,last_checked_at,current_source_status,created_at,updated_at}`. `source_status`, `source_error`, `last_checked_at` сохраняют состояние при создании; `current_source_status` показывает актуальное состояние. Статусы: `open`, `in_progress`, `resolved`. Завершение требует текста `resolution`. Повторное открытие очищает текущее решение, сохраняя прежнее в комментариях истории; новое завершение снова требует решения. Назначения и изменения статуса тоже записываются в историю.

Обычный участник видит обращения только к доступным ему профилям источников. Технический администратор видит обращения пространства независимо от права финансового чтения. Отзыв чтения профиля скрывает обращение и связанные уведомления. Новое обращение и комментарии без действующего ответственного уведомляют администраторов; при действующем назначении — ответственного и автора, исключая того, кто выполняет действие. Если ответственный деактивирован или лишён права обслуживания источников, уведомления получают действующие администраторы; историческое назначение не мешает завершить обращение. Тип уведомления `source_issue` ведёт по `source_issue_id`.

## Страницы истории и поиск

GET `/cases`, `/reports`, `/source-issues` и `/conversations` возвращают JSON-массив. Все четыре списка принимают `limit` от 1 до 100, по умолчанию 50, `q` до 200 символов и необязательный непрозрачный `cursor`. При продолжении сервер добавляет заголовок `X-Next-Cursor`; браузер передаёт его следующему запросу без изменений. Отсутствие заголовка означает конец списка. CORS открывает этот заголовок разрешённому браузерному origin.

| Список | Фильтры и поиск |
| --- | --- |
| `/cases` | `filter=all/open/closed/mine/waiting`, `store_id`; q ищет в заголовке и описании |
| `/reports` | q ищет в заголовке и описании |
| `/source-issues` | `filter=all/active/mine/resolved`; q ищет в заголовке, описании проблемы и имени источника |
| `/conversations` | q ищет в разрешённых заголовках личных диалогов |

По умолчанию `filter=all`. «Мои» включает автора и назначенного ответственного; `waiting` означает неотвеченный вопрос текущему участнику. Фильтр точки проверяет её в области разбора, поэтому карточка точки получает всю доступную историю этой точки. Символы `%`, `_` и обратная косая черта в q означают буквальный текст, а не шаблон SQL.

Порядок — время изменения по убыванию и id по убыванию при совпадении времени; для отчётов используется время создания. Курсор шифруется и связан с пользователем, маршрутом, пространством и фильтрами. При смене поиска, фильтра или точки нужно начать с первой страницы; некорректный или чужой курсор возвращает 400 `invalid_cursor`. Права проверяются на каждой странице.

Один запрос просматривает до 500 записей с учётом скрытых по правам. Если они не доступны участнику, возможен пустой массив с `X-Next-Cursor`; клиент сохраняет возможность загрузить следующую страницу. Поэтому старый доступный отчёт или разбор не теряется за большим числом новых чужих объектов. Изменение объекта во время просмотра может переместить его к началу списка; обновление списка загружает актуальную первую страницу.

## Повтор создания после сетевой ошибки

POST `/reports`, `/cases` и `/source-issues` принимают `idempotency_key` длиной от 8 до 100 символов. Браузер сохраняет ключ вместе с отправляемой формой и использует его при повторе. Одинаковый запрос одного автора в одном пространстве возвращает прежний объект с тем же id и актуальным состоянием. Повтор не создаёт дополнительные уведомления, измерения или записи. Другой автор или другое пространство имеют собственную область ключей.

Изменённое тело либо другая операция с уже использованным ключом возвращает 409 `idempotency_conflict`. Для нового сохранения нужен новый ключ. Ошибка первоначального создания откатывает и объект, и резервирование ключа; исправленный запрос можно отправить снова. Права на сохранённый объект проверяются при каждом повторе, поэтому ключ не восстанавливает отозванный доступ. Без ключа POST сохраняет обычное поведение отдельного создания.
