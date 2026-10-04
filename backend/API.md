# Контракт браузера и приложения

Браузер получает факты, историю запросов и обсуждения из одного API. Модель работает в отдельном процессе; открытая вкладка не является владельцем задания. Все идентификаторы — строки UUID. Даты и время — ISO 8601, время UTC. Денежные значения передаются строками. Списки — JSON-массивы.

Базовый путь `/api/v1`. Ошибка имеет вид `{"error":{"code":"...","message":"..."}}`. Авторизация — HttpOnly cookie `razbor_session`. После входа клиент передаёт `X-CSRF-Token` из session во всех изменяющих запросах. Cookie отправляется с `credentials: "include"`.

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

Роли: `director`, `regional_manager`, `franchise_owner`, `store_manager`, `analyst`, `admin`. Возможности: `analytics:read`, `assistant:use`, `reports:write`, `cases:write`, `plans:write`, `metrics:write`, `sources:manage`, `members:manage`. Интерфейс проверяет capabilities; API повторяет проверку независимо.

## Пространство

Далее все пути начинаются с `/workspaces/{workspace_id}`.

| Метод и путь | Тело / результат |
| --- | --- |
| GET/POST `/stores` | `{id,name,code,city,owner_name,active}`; создание без id |
| PATCH `/stores/{id}` | `{name?,city?,owner_name?,active?}` |
| GET/POST `/metrics` | `{id,key,name,description,unit,source_id,table_schema,table_name,value_column,date_column,store_column,aggregation,version}`; aggregation `sum`/`count`/`avg`; при count value_column необязателен |
| GET/POST `/plans` | `{id,store_id,metric_id,period,amount,version}`; period `YYYY-MM-01`; повторное сохранение создаёт редакцию |
| GET `/overview?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&metric_id=...&store_ids=...` | `{date_from,date_to,metric,totals:{actual,plan,attainment},stores:[{store_id,name,city,actual,plan,attainment,status}],coverage:{available,total},warnings:[]}`; null отличается от нуля |
| GET/POST `/sources` | `{id,name,host,port,database,username,schemas,enabled,catalog_version,status,last_checked_at,error}`; POST также password, ssl_mode (`require`/`verify-full`/`disable`) |
| POST `/sources/{id}/test` | Проверка доступа и обновление каталога → source |
| PATCH `/sources/{id}` | `{name?,host?,port?,database?,username?,password?,schemas?,ssl_mode?,enabled?}` → source; существенное изменение требует новой проверки |
| GET `/sources/{id}/catalog` | `[{schema,name,columns:[{name,data_type}],policy:{columns,store_column,shared}}]` |
| PUT `/sources/{id}/policies` | `{tables:[{schema,name,columns:[...],store_column:null,shared:false}]}`; пустой policy запрещает таблицу |
| GET/POST `/members` | `{id,user_id,email,name,role,all_stores,store_ids,active,data_access}`; создание `{email,name,password,role,all_stores,store_ids,data_access}` |
| PATCH `/members/{id}` | `{role?,all_stores?,store_ids?,active?,data_access?}` |
| GET `/participants?store_ids=...` | `[{id:user_id,name,role}]`; действующие участники с доступом ко всей выбранной области; требуется cases:write |
| GET `/invitations` | `[{id,email,role,expires_at}]`; только действующие приглашения |
| POST `/invitations` | `{email,role,all_stores,store_ids,data_access}` → `{id,email,expires_at,token}`; токен показывается один раз, срок семь дней |
| POST `/invitations/{id}/revoke` | `{ok:true}` |

store_ids в GET — список UUID через запятую. Источник доступен ассистенту только после успешной проверки и настройки разрешённых таблиц. Неограниченные справочники требуют явного `shared:true`; таблица фактов требует store_column с внешним кодом точки (`Store.code`).

Источник возвращает также `ssl_mode` и `policy_revision`. Пользователь без `sources:manage` получает только id, name, enabled, status, catalog_version, last_checked_at. Аналитик получает каталог разрешённых колонок, администратор подключения — каталог для настройки политики.

В overview `date_to` включительно. `comparison` — null либо `{date_from,date_to,current,previous,delta,change_percent,comparable_count}`. Полный календарный месяц сравнивается с предыдущим календарным месяцем; блок полных месяцев — с предшествующим блоком того же числа месяцев. Для произвольного диапазона предыдущий период имеет ту же длительность в днях. Значения рассчитаны по совпадающему составу точек. В строках stores дополнительно `previous_actual`, `change_percent`, status `has_rows`/`no_data`. В totals дополнительно `planned_store_count`; отношение факта и плана вычисляется только по совпадающей области. Для средних общая сумма планов отсутствует. `coverage.available` означает наличие строк, а не доказанную полноту периода.

## Ассистент и отчёты

| Метод и путь | Тело / результат |
| --- | --- |
| GET/POST `/conversations` | `{id,title,version,created_at,updated_at}`; POST `{title?}` |
| GET `/conversations/{id}` | conversation + `{messages:[{id,role,content,run_id,created_at}],runs:[run]}` |
| POST `/conversations/{id}/messages` | `{question,source_id,store_ids:[],base_run_id:null,version,idempotency_key,date_from?,date_to?,metric_id?}` → run |
| GET `/runs/{id}` | `{id,conversation_id,question,source_id,store_ids,status,stage,sql,result,error,clarification,created_at,finished_at,conversation_version}` |
| GET `/runs/{id}/events?after=0` | SSE `id: sequence`, `event: update`, `data: {sequence,status,stage,message}`; GET run — источник актуального состояния |
| POST `/runs/{id}/cancel` | run |
| GET/POST `/reports` | `{id,title,description,run_id,created_at,created_by}`; POST `{title,description?,run_id}` |
| GET `/reports/{id}` | report + `{run}` |
| POST `/reports/{id}/refresh` | `{idempotency_key}` → новый run, выполнение сохранённого SQL без генерации |

Статусы run: `queued`, `running`, `cancel_requested`, `cancelled`, `succeeded`, `failed`, `needs_input`, `rejected`. result: `{columns:[{name,type}],rows:[[...]],truncated,row_count,execution:null|{sql,parameters}}`. `run.sql` хранит нормализованный SQL расчёта. `result.execution` показывает фактически исполненный запрос после ограничения таблиц разрешёнными строками и полями; parameters — значения параметров PostgreSQL `$1` и далее. Секретов подключения здесь нет. `error:{code,message}` — безопасная причина ошибки. result null до успешного завершения. clarification `{code,message}`. version проверяется при отправке, конфликт возвращает 409. Идемпотентный повтор возвращает прежний run, другой вопрос с тем же ключом — 409.

Run дополнительно содержит `context` с выбранными date_from/date_to/metric_id, версиями показателя, каталога и политики. Это контекст пользователя, не доказанный результат разбора SQL. Оба края периода передаются вместе; конец включителен. Продолжение с отсутствующими полями периода или показателя наследует их от base_run_id; явно переданный null очищает выбор, для периода — обе границы одновременно. Область точек задаётся в каждом сообщении через store_ids, пустой список выбирает все доступные активные точки. Для ответа на уточнение используется base_run_id запуска needs_input и новые выбранные условия. При недоступном worker создание нового запуска возвращает 503 `worker_unavailable`; сохранённые результаты остаются доступны.

## Обсуждения

| Метод и путь | Тело / результат |
| --- | --- |
| GET/POST `/cases` | `{id,title,description,status,store_ids,run_id,created_by,assignee_id,conclusion,created_at,updated_at}`; POST `{title,description?,store_ids,run_id?,assignee_id?}` |
| GET `/cases/{id}` | case + `{comments:[{id,author_id,author_name,body,created_at}],questions:[{id,body,assignee_id,status,answer,created_at}]}` |
| POST `/cases/{id}/comments` | `{body}` → comment |
| POST `/cases/{id}/questions` | `{body,assignee_id}` → question |
| POST `/cases/{id}/questions/{question_id}/answer` | `{answer}` → question |
| POST `/cases/{id}/close` | `{conclusion}` → case |
| GET `/notifications` | `[{id,kind,title,body,case_id,read_at,created_at}]` |
| POST `/notifications/{id}/read` | `{ok:true}` |

Получатель обсуждения должен иметь доступ ко всей его области. Прикреплённый запуск проверяется отдельно. Личный диалог никогда не становится общим автоматически.

GET case также содержит `run:null|run` для разрешённого прикреплённого результата. API не отправляет почту: браузер формирует ссылку принятия приглашения с токеном во фрагменте URL, чтобы он не попадал в журнал HTTP-пути.
