from __future__ import annotations

MESSAGES: dict[str, object] = {
    "brand": "JOPES VPN",
    "channel.title": "Jopes VPN | Новости и информация",
    "common.back": "◀️ Назад",
    "common.menu": "🏠 Меню",
    "common.refresh": "🔄 Обновить",
    "common.support": "🆘 Поддержка",
    "common.help": "🆘 Помощь",
    "common.more": "☰ Аккаунт и ещё",
    "common.about": "ℹ️ О сервисе",
    "common.cooperation": "🤝 Сотрудничество",
    "common.settings": "⚙️ Настройки",
    "common.guides": "📚 Инструкции",
    "common.devices": "📱 Мои устройства",
    "common.stats": "📊 Статистика",
    "common.plans": "💎 Тарифы",
    "common.subscription": "📄 Моя подписка",
    "common.cabinet": "👤 Личный кабинет",
    "common.connect": "🔌 Подключить VPN",
    "common.trial": "🎁 Получить пробный доступ",
    "common.cancel": "✖️ Отмена",
    "common.friend": "друг",
    "common.hide": "🙈 Скрыть",
    "common.page": "Страница",
    "common.next": "Следующий шаг ▶️",
    "common.previous": "◀️ Назад",
    "common.newer": "◀️ Новее",
    "common.older": "Старее ▶️",
    "common.other_app": "◀️ Другое приложение",
    "common.what_to_choose": "🤔 Не знаю, что выбрать",
    "common.status": "Статус",
    "common.tariff": "Тариф",
    "common.expires": "Действует до",
    "common.remaining": "Осталось",
    "common.traffic": "Трафик",
    "common.telegram_id": "Telegram ID",
    "common.jopes_id": "JopesVPN-ID",
    "common.unknown": "~",
    "common.dash": "—",
    "common.channel": "Канал",
    "common.page_of": "Страница: {page} из {pages}",
    "common.total_devices": "Всего устройств: {total}",
    "common.updated": "Обновлено",
    "common.list": "Список",
    "common.summary": "Сводка",
    "common.parameters": "Параметры",
    "common.important": "⚠️ Важно",
    "common.conditions": "Условия",
    "common.error": "⚠️ Ошибка",
    "common.no_connection": "📡 Нет связи",
    "common.no_access": "🔒 Нет доступа",
    "common.no_subscription": "🚫 Нет подписки",
    "common.not_active": "⏸ Доступ не активен",
    "common.problems": "🧰 Частые проблемы",
    "common.how_connect": "🛠 Как подключиться",
    "common.try_again": "Повторите попытку позже.",
    "common.changed_nothing": "Подписка и устройства не изменены.",
    "common.write_support": "Напишите в поддержку.",
    "common.refresh_hint": "Нажмите «🔄 Обновить» позже.",
    "common.open_menu": "Откройте меню: /start",
    "common.subscription_page": "🌐 Страница подписки",
    "docs.title": "📄 ДОКУМЕНТЫ JOPESVPN",
    "docs.intro": (
        "Перед использованием бота ознакомьтесь с политикой конфиденциальности "
        "и пользовательским соглашением."
    ),
    "docs.unconfigured": (
        "Ссылки на документы ещё не настроены. Сейчас нельзя принять согласие: "
        "сначала администратор должен опубликовать актуальные тексты."
    ),
    "docs.privacy_button": "Политика конфиденциальности",
    "docs.agreement_button": "Пользовательское соглашение",
    "docs.accept_button": "✅ Ознакомился и принимаю",
    "docs.accepted": "Спасибо. Согласие сохранено, версия документов: {version}.",
    "docs.version": "Версия документов: {version}.",
    "docs.accept_required": (
        "Чтобы продолжить пользоваться ботом, ознакомьтесь с документами и "
        "подтвердите согласие."
    ),
    "docs.accept_failed": "Не удалось сохранить согласие. Попробуйте ещё раз.",
    "docs.missing_link": "Ссылка на этот документ пока не настроена.",
    "docs.admin_menu": "📄 Ссылки на документы",
    "docs.admin_set_privacy": "Изменить ссылку на политику",
    "docs.admin_set_agreement": "Изменить ссылку на соглашение",
    "docs.admin_status": (
        "Политика: {privacy}\nСоглашение: {agreement}"
    ),
    "docs.admin_unset": "не настроена",
    "docs.admin_prompt_privacy": (
        "Отправьте HTTPS-ссылку на опубликованную политику. "
        "Чтобы убрать ссылку, отправьте /clear. Каждое сохранение повышает версию "
        "и запросит повторное согласие пользователей."
    ),
    "docs.admin_prompt_agreement": (
        "Отправьте HTTPS-ссылку на опубликованное соглашение. "
        "Чтобы убрать ссылку, отправьте /clear. Каждое сохранение повышает версию "
        "и запросит повторное согласие пользователей."
    ),
    "docs.admin_bad_url": (
        "Нужна корректная HTTPS-ссылка без логина, пароля и пробелов. "
        "Попробуйте ещё раз или отправьте /cancel."
    ),
    "docs.admin_saved": "Ссылка обновлена.",
    "docs.admin_cancelled": "Изменение ссылки отменено.",
    "referral.button": "🤝 Пригласить и получить процент",
    "referral.title": "🤝 СОТРУДНИЧЕСТВО",
    "referral.stats": "Ваша статистика",
    "referral.invited": "Пригласили: {count}",
    "referral.activated": "Запустили бота: {count}",
    "referral.paying": "Оплатили: {count}",
    "referral.purchases": "Покупок приглашённых: {count}",
    "referral.earned": "Начислено: {amount}",
    "referral.available": "Доступно к выводу: {amount}",
    "referral.program_title": "Условия программы",
    "referral.rate_first": "Первая покупка приглашённого: 70% суммы.",
    "referral.rate_second": "Вторая покупка приглашённого: 40% суммы.",
    "referral.rate_next": "Каждая следующая покупка: 20% суммы.",
    "referral.rate_topup": "Каждое пополнение баланса: 20% суммы.",
    "referral.rules": (
        "Процент считается от суммы покупки тарифа или пополнения баланса, "
        "которое оплатил приглашённый. Начисления зачисляются на реферальный "
        "счёт отдельно от рублёвого баланса. Вывод доступен от 250 ₽ через "
        "заявку в поддержку. Один Telegram-аккаунт учитывается только один раз. "
        "Повторная привязка к другому пригласившему невозможна."
    ),
    "referral.link": "Ваша ссылка:\n<code>{link}</code>",
    "referral.share": "Поделиться приглашением",
    "referral.new_visit": (
        "По вашей ссылке пришёл новый пользователь Jopes VPN. "
        "Реферальное начисление появится после его покупки или пополнения баланса."
    ),
    "referral.unavailable": "Статистика приглашений сейчас недоступна.",
    "referral.bot_username_missing": (
        "Не удалось сформировать ссылку. Попробуйте позже или напишите в поддержку."
    ),
    "referral.reward_notice": (
        "Реферальное начисление: +{amount} за {event}. Средства доступны "
        "к выводу в разделе «Баланс»."
    ),
    "referral.reward_purchase_event": "покупку приглашённого ({percent}%)",
    "referral.reward_topup_event": "пополнение приглашённого ({percent}%)",
    "referral.reward_pending_notice": (
        "Реферальное начисление будет зачислено после подтверждения оплаты."
    ),

    "wallet.title": "💰 БАЛАНС",
    "wallet.balance_title": "Средства",
    "wallet.balance_row": "Рублёвый баланс: {amount}",
    "wallet.referral_row": "Реферальный доход: {amount}",
    "wallet.withdrawable_row": "Доступно к выводу: {amount}",
    "wallet.pending_row": "Заявка на вывод: {amount} · № {request_id}",
    "wallet.history_title": "История операций",
    "wallet.history_empty": "Операций пока нет.",
    "wallet.history_row": "{label}: <b>{amount}</b> · {date}",
    "wallet.kind_topup": "Пополнение",
    "wallet.kind_purchase": "Оплата тарифа",
    "wallet.kind_referral": "Реферальное начисление",
    "wallet.kind_topup_refund": "Возврат пополнения",
    "wallet.kind_referral_refund": "Корректировка начисления",
    "wallet.kind_withdrawal": "Вывод средств",
    "wallet.kind_other": "Операция",
    "wallet.note": (
        "Рублёвый баланс оплачивает тарифы. Реферальный доход хранится "
        "отдельно и выводится через поддержку. Минимальная сумма вывода: 250 ₽."
    ),
    "wallet.topup_button": "➕ Пополнить баланс",
    "wallet.withdraw_button": "💸 Вывести реферальный доход",
    "wallet.withdraw_pending_button": "⏳ Заявка на вывод отправлена",
    "wallet.open_button": "💰 Баланс и история",
    "wallet.topup_title": "ПОПОЛНЕНИЕ БАЛАНСА",
    "wallet.topup_intro": (
        "Введите сумму от {minimum} до {maximum}. Бот рассчитает стоимость "
        "в Telegram Stars, а после оплаты зачислит рубли на баланс."
    ),
    "wallet.topup_rate_note": (
        "Курс соответствует тарифам каталога. Начисляется ровно введённая "
        "сумма в рублях. Оплата проходит через Telegram Stars."
    ),
    "wallet.topup_preset": "{amount} · {stars} ⭐",
    "wallet.topup_custom": "✍️ Ввести другую сумму",
    "wallet.topup_prompt": (
        "Введите сумму пополнения в рублях, например 500 или 750,50. "
        "Минимальная сумма: {minimum}."
    ),
    "wallet.topup_invalid": (
        "Не удалось распознать сумму. Введите число в рублях не меньше {minimum}."
    ),
    "wallet.topup_too_large": "Максимальная сумма пополнения: {maximum}.",
    "wallet.topup_invoice_title": "Пополнение Jopes VPN",
    "wallet.topup_invoice_description": "Зачисление {amount} на баланс",
    "wallet.topup_invoice_sent": (
        "Счёт на {amount} отправлен. После оплаты сумма появится на балансе."
    ),
    "wallet.topup_success": (
        "✅ Баланс пополнен на {amount}. Доступный баланс: {balance}."
    ),
    "wallet.topup_review": (
        "Оплата получена, но зачисление задержалось. Мы проверим операцию; "
        "повторно оплачивать не нужно."
    ),
    "wallet.refund_admin": (
        "ℹ️ Возврат Stars по пополнению <code>{request_id}</code> учтён. "
        "Баланс и реферальное начисление скорректированы."
    ),
    "wallet.refund_user_notice": (
        "Возврат пополнения выполнен. Сумма списана с рублёвого баланса. "
        "Если баланс уже израсходован, поддержка поможет урегулировать разницу."
    ),
    "wallet.withdraw_intro": (
        "Доступно к выводу: {amount}. Заявка будет передана в поддержку. "
        "Автоматическая выплата не выполняется. Минимальная сумма: {minimum}."
    ),
    "wallet.withdraw_support": (
        "Поддержка: {support}. Реквизиты выплаты согласуются в обращении."
    ),
    "wallet.withdraw_confirm": "Отправить заявку на {amount}",
    "wallet.withdraw_created": (
        "✅ Заявка на вывод {amount} создана. Номер: <code>{request_id}</code>. "
        "Поддержка свяжется с вами для согласования выплаты."
    ),
    "wallet.withdraw_admin": (
        "💸 Новая заявка на вывод\n"
        "Номер: <code>{request_id}</code>\n"
        "Пользователь: {name} · <code>{telegram_id}</code>\n"
        "Сумма: {amount}\n"
        "Выплата выполняется вручную после согласования реквизитов."
    ),
    "wallet.withdraw_pending_error": (
        "Заявка на вывод уже отправлена. Дождитесь ответа поддержки."
    ),
    "wallet.withdraw_minimum": (
        "Вывод доступен от 250 ₽. Сейчас к выводу: {amount}."
    ),
    "wallet.withdraw_unavailable": (
        "Сейчас нельзя создать заявку на вывод. Попробуйте позже."
    ),
    "wallet.unavailable": "Баланс сейчас недоступен. Попробуйте позже.",
    "wallet.insufficient": (
        "Недостаточно средств. Нужно {required}, на балансе {balance}. "
        "Пополните баланс и повторите оплату."
    ),
    "wallet.payment_blocked": (
        "Не удалось оплатить тариф с баланса. Средства не списаны. "
        "Проверьте баланс и повторите попытку."
    ),
    "wallet.payment_applied": (
        "Этот платёж уже обработан. Средства повторно не списывались."
    ),
    "trial.nudge": (
        "Вы открывали Jopes VPN, но ещё не подключились. Если хотите, можно "
        "получить бесплатный пробный доступ и настроить его через страницу подписки."
    ),
    "trial.nudge_button": "Попробовать бесплатно",
    "admin.panel_button": "🛡 Админ-меню",
    "admin.grant": "🎁 Выдать доступ",
    "admin.find": "🔎 Найти пользователя",
    "admin.status": "📊 Состояние сервиса",
    "admin.users": "👥 Все пользователи",
    "admin.logs": "📡 Логи подключений",
    "admin.broadcast": "📣 Рассылка",
    "admin.broadcast_send": "✅ Отправить всем",
    "admin.broadcast_prompt": (
        "Пришлите сообщение для рассылки. Можно отправить текст или медиа; "
        "сначала появится предпросмотр. Для отмены отправьте /cancel."
    ),
    "admin.broadcast_cancelled": "Рассылка отменена.",
    "admin.broadcast_preview_failed": "Не удалось подготовить предпросмотр. Попробуйте ещё раз.",
    "admin.broadcast_unavailable": "Не удалось запустить рассылку. Проверьте хранилище и повторите попытку.",
    "admin.broadcast_result": (
        "Рассылка завершена.\nДоставлено: {delivered} из {total}.\n"
        "Не доставлено: {failed}."
    ),
    "admin.open_chat": "💬 Перейти в личный чат",
    "admin.logs_title": "📡 ЛОГИ ПОДКЛЮЧЕНИЙ",
    "admin.logs_on": "🟢 Логи включены",
    "admin.logs_off": "Логи выключены",
    "admin.logs_enable": "▶️ Включить логи",
    "admin.logs_disable": "⏸ Выключить логи",
    "admin.logs_interval": "Проверка каждые {seconds} секунд.",
    "admin.logs_note": (
        "Panel проверяется раз в минуту. Уведомление означает новый HWID/IP "
        "в метаданных устройств, а не каждую VPN-сессию. Первичный сбор "
        "выполняется без уведомлений."
    ),
    "admin.logs_tracked": "Отслеживается аккаунтов: <b>{count}</b>",
    "admin.logs_box": "Мониторинг",
    "admin.logs_off_hint": "Включите логи, чтобы получать уведомления о подключениях.",
    "admin.logs_unavailable": (
        "Не удалось прочитать Panel или сохранить настройки. Логи не включены."
    ),
    "admin.log_event_title": "🔔 НОВОЕ УСТРОЙСТВО / АДРЕС",
    "admin.log_account": "Аккаунт",
    "admin.log_device": "Устройство",
    "admin.log_tg_name": "Telegram: {name}",
    "admin.log_tg_username": "Username: @{username}",
    "admin.log_tg_id": "Telegram ID: <code>{telegram_id}</code>",
    "admin.log_panel_id": "JopesVPN-ID: <b>{panel_id}</b>",
    "admin.log_tariff": "Тариф: {tariff}",
    "admin.log_status": "Статус: {status}",
    "admin.log_expires": "Действует до: {expires}",
    "admin.log_platform": "Платформа: {platform}",
    "admin.log_client": "Клиент: {client}",
    "admin.log_hwid": "HWID: <code>{hwid}</code>",
    "admin.log_ip": "IP: <code>{ip}</code>",
    "admin.log_node": "Узел: <code>{node}</code>",
    "admin.log_seen": "Время: {seen}",
    "admin.log_unknown": "~",
    "admin.log_same_ip": "⚠️ С этого IP уже замечено аккаунтов: {count}. Возможен мультиаккаунт или бот.",
    "admin.log_profile": "👤 Профиль",
    "admin.log_page": "🌐 Страница подписки",
    "admin.log_devices": "📱 Устройства",
    "admin.log_mute": "🔕 Выключить логи",
    "admin.devices_note": (
        "IP, клиент и HWID видны только администратору. Удаление устройств "
        "доступно пользователю в разделе «Мои устройства»."
    ),
    "admin.extend": "➕ Продлить",
    "admin.enable": "▶️ Включить",
    "admin.disable": "⏸ Отключить",
    "admin.revoke": "🔑 Сменить ссылку подписки",
    "admin.find_other": "🔎 Найти другого",
    "admin.admin_home": "🛡 В админку",
    "admin.confirm_disable": "Да, отключить",
    "admin.confirm_enable": "Да, включить",
    "admin.confirm_revoke": "Да, сменить ссылку",
    "admin.confirm": "Подтвердить",
    "admin.confirm_grant": "Да, выдать доступ",
    "admin.confirm_extend": "Да, продлить",
    "admin.cancel": "Отмена",
    "admin.title": "🛡 АДМИН-МЕНЮ",
    "admin.note": "Все операции выполняются через сервис и требуют подтверждения.",
    "admin.command_unavailable": "Команда недоступна.",
    "admin.status_title": "📊 СОСТОЯНИЕ СЕРВИСА",
    "admin.service_state": "Сервис: {state}",
    "admin.available": "🟢 Доступен",
    "admin.unavailable_state": "🔴 Недоступен",
    "admin.nodes": "Узлы",
    "admin.nodes_empty": "Узлы не зарегистрированы.",
    "admin.node_online": "{name}: 🟢 online",
    "admin.node_offline": "{name}: 🔴 offline",
    "admin.nodes_count": "Узлов: {count}",
    "admin.nodes_online": "Узлы online",
    "admin.service_box": "СЕРВИС",
    "admin.users_box": "ПОЛЬЗОВАТЕЛИ",
    "admin.nodes_box": "УЗЛЫ",
    "admin.panel_health": "Panel API",
    "admin.health_ok": "🟢 Работает",
    "admin.health_fail": "🔴 Не отвечает",
    "admin.users_total": "Пользователи",
    "admin.users_count": "Всего: {count}",
    "admin.users_total_line": "Всего: <b>{count}</b>",
    "admin.users_active": "🟢 Активных: {count}",
    "admin.users_disabled": "⏸ Отключённых: {count}",
    "admin.users_expired": "⚫ Истёкших: {count}",
    "admin.users_limited": "🟡 Ограниченных: {count}",
    "admin.traffic_total": "Трафик",
    "admin.traffic_used": "Израсходовано: {value}",
    "admin.dashboard_note": "Показатели получены напрямую из Panel.",
    "admin.list_title": "👥 ПОЛЬЗОВАТЕЛИ",
    "admin.list_summary": "Всего пользователей",
    "admin.list_page": "Страница {page} из {pages}",
    "admin.list_empty": "Пользователей пока нет.",
    "admin.list_loading": "Загружаю список…",
    "admin.list_hint": "Нажмите на пользователя, чтобы открыть карточку. Для поиска по ID нажмите «Найти пользователя» и отправьте Telegram ID.",
    "admin.list_no_telegram": "Пользователь без Telegram ID — карточка по кнопке недоступна.",
    "admin.user_page": "Страница {page}",
    "admin.user_back": "◀️ К списку",
    "admin.card_title": "КАРТОЧКА ПОЛЬЗОВАТЕЛЯ",
    "admin.card_box": "Полная информация",
    "admin.card_id": "JopesVPN-ID: <b>{id}</b>",
    "admin.card_name": "Имя: <b>{name}</b>",
    "admin.card_username": "Логин: <code>{username}</code>",
    "admin.card_username_none": "без логина",
    "admin.card_tag": "Тег: {tag}",
    "admin.card_tag_none": "~",
    "admin.card_devices": "Устройства: {devices}",
    "admin.card_created": "Создан: {value}",
    "admin.card_online": "Последняя активность: {value}",
    "admin.card_lifetime": "Трафик за всё время: {value}",
    "admin.lookup_title": "👤 ПОЛЬЗОВАТЕЛЬ",
    "admin.name_row": "Имя: <code>{name}</code>",
    "admin.user_not_found": "Пользователь с таким Telegram ID не найден.",
    "admin.api_denied": "Сервис отказал в доступе. Проверьте API-права.",
    "admin.data_unexpected": "Сервис вернул неожиданные данные. Попробуйте позже.",
    "admin.service_unavailable": "Сервис временно недоступен. Попробуйте позже.",
    "admin.usage_user": "Использование: /admin_user <telegram_id>",
    "admin.usage_grant": "Использование: /admin_grant <telegram_id> <days>",
    "admin.usage_extend": "Использование: /admin_extend <telegram_id> <days>",
    "admin.usage_action": "Использование: /{command} <telegram_id>",
    "admin.invalid_target": "Укажите положительный Telegram ID и срок 1–365 дней.",
    "admin.ask_grant_user": "Введите Telegram ID пользователя для выдачи доступа.",
    "admin.ask_extend_user": "Введите Telegram ID пользователя для продления.",
    "admin.ask_positive_id": "Введите положительный числовой Telegram ID.",
    "admin.ask_grant_days": "На сколько дней выдать доступ? Укажите число от 1 до 365.",
    "admin.ask_extend_days": "На сколько дней продлить доступ? Укажите число от 1 до 365.",
    "admin.invalid_days": "Срок должен быть числом от 1 до 365 дней.",
    "admin.flow_unavailable": "Сценарий сейчас недоступен. Повторите попытку.",
    "admin.squad_missing": "Выдача не настроена: задайте Internal Squad UUID.",
    "admin.user_exists": "Пользователь уже существует. Повторная выдача не выполнялась.",
    "admin.user_exists_no_dup": "Пользователь уже существует. Дубликат не создан.",
    "admin.grant_confirm_title": "ПОДТВЕРДИТЕ ВЫДАЧУ ДОСТУПА",
    "admin.term_row": "Срок: {term}",
    "admin.grant_confirm_note": (
        "Создание выполнится через сервис; повторная выдача существующему "
        "пользователю запрещена."
    ),
    "admin.extend_confirm_title": "ПОДТВЕРДИТЕ ПРОДЛЕНИЕ ДОСТУПА",
    "admin.current_expiry": "Текущий срок: {expires}",
    "admin.extend_for": "Продлить на: {days_word}",
    "admin.extend_note_disabled": "Статус DISABLED сохранится: продление не включает доступ.",
    "admin.extend_note_expired": "Статус EXPIRED сменится на ACTIVE и доступ восстановится.",
    "admin.extend_not_found": "Пользователь с таким Telegram ID не найден. Продлевать нечего.",
    "admin.action_for": "Действие для Telegram ID <code>{telegram_id}</code>",
    "admin.explain_disable": (
        "Отключение помечает учётную запись DISABLED; завершение текущих "
        "VPN-сессий не подтверждено."
    ),
    "admin.explain_enable": (
        "Включение вернёт доступ, если срок подписки ещё не истёк. "
        "У истёкшей подписки сначала продлите срок."
    ),
    "admin.explain_revoke": (
        "Замена ссылки подписки. Уже открытая VPN-сессия может продолжить работу."
    ),
    "admin.warn_disable": (
        "Учётная запись будет отключена. Завершение уже открытых "
        "VPN-сессий не подтверждено."
    ),
    "admin.warn_enable": (
        "Учётная запись будет включена, если срок подписки не истёк. "
        "Для истёкшей подписки сначала нужно продление."
    ),
    "admin.warn_revoke": (
        "Ссылка подписки будет заменена. Уже открытые VPN-сессии могут "
        "продолжить работу."
    ),
    "admin.confirm_action_title": "ПОДТВЕРДИТЕ ДЕЙСТВИЕ",
    "admin.message_unavailable": "Сообщение недоступно.",
    "admin.confirm_stale": "Подтверждение устарело. Начните заново.",
    "admin.grant_title": "ДОСТУП СОЗДАН",
    "admin.grant_failed": (
        "Не удалось подтвердить выдачу. Перед повтором найдите пользователя: "
        "операция могла завершиться без ответа."
    ),
    "admin.extend_title": "СРОК ПРОДЛЁН",
    "admin.extend_failed": (
        "Не удалось подтвердить продление. Перед повтором проверьте "
        "пользователя: срок мог измениться."
    ),
    "admin.status_remains_disabled": "Доступ остаётся отключённым (DISABLED).",
    "admin.already_disabled": "Пользователь уже отключён.",
    "admin.already_enabled": "Пользователь уже включён.",
    "admin.disable_title": "ДОСТУП ОТКЛЮЧЁН",
    "admin.enable_title": "ДОСТУП ВКЛЮЧЁН",
    "admin.enable_expired_title": "СРОК ДОСТУПА ИСТЁК",
    "admin.enable_limited_title": "ДОСТУП ОСТАЁТСЯ ОГРАНИЧЕННЫМ",
    "admin.revoke_title": "ССЫЛКА ОБНОВЛЕНА",
    "admin.disable_done": (
        "Disable подтверждён. Существующие VPN-сессии не проверены и могут "
        "не завершиться сразу."
    ),
    "admin.enable_done": "Статус доступа обновлён.",
    "admin.enable_expired": "Срок подписки истёк: продлите его, чтобы доступ заработал.",
    "admin.enable_limited": (
        "Панель по-прежнему отмечает доступ как ограниченный. Проверьте причину "
        "ограничения и остаток трафика."
    ),
    "admin.extend_blocked_limited": (
        "Доступ ограничен в панели. Продление срока само по себе не снимет "
        "ограничение. Проверьте карточку пользователя и причину статуса."
    ),
    "admin.revoke_done": (
        "Ссылка подписки заменена. Завершение открытых VPN-сессий этим "
        "не подтверждено."
    ),
    "admin.find_title": "ПОИСК ПОЛЬЗОВАТЕЛЯ",
    "admin.find_note": "Отправьте Telegram ID отдельным сообщением.",
    "admin.grant_ask_title": "ВЫДАЧА ДОСТУПА",
    "admin.grant_ask_note": "Отправьте Telegram ID пользователя.",
    "admin.extend_ask_title": "ПРОДЛЕНИЕ ДОСТУПА",
    "admin.extend_ask_note": "На сколько дней продлить? Укажите число от 1 до 365.",
    "admin.stale_button": "Кнопка устарела.",
    "admin.stale_button_menu": "Кнопка устарела. Откройте меню заново.",
    "admin.bad_button": "Некорректная кнопка.",
    "admin.status_row": "Статус: {status}",
    "admin.expires_row": "Действует до: {expires}",
    "admin.result_id": "Telegram ID: <code>{telegram_id}</code>",
    "plans.extend_button": "Продлить",

    "units.bytes": "{value} Б",
    "units.mb": "{value} МБ",
    "units.gb": "{value} ГБ",
    "plural.days": {
        "one": "{n} день",
        "few": "{n} дня",
        "many": "{n} дней",
    },
    "plural.hours": {
        "one": "{n} час",
        "few": "{n} часа",
        "many": "{n} часов",
    },
    "plural.minutes": {
        "one": "{n} минуту",
        "few": "{n} минуты",
        "many": "{n} минут",
    },
    "time.days_hours": "{day_word} {hours} ч",
    "time.hours": "{hours} ч",
    "time.expired": "срок закончился",
    "time.unknown": "неизвестно",
    "date.format": "%d.%m.%Y %H:%M UTC",

    "status.active": "● Активен",
    "status.disabled": "○ Отключён",
    "status.limited": "◐ Ограничен",
    "status.expired": "× Истёк",
    "status.unknown": "? {status}",
    "status.none": "○ Подписки нет",
    "status.no_connection": "× Нет связи",
    "tariff.trial": "Пробный",
    "tariff.admin": "Основной",
    "tariff.free": "FREE · 200 МБ в сутки",
    "tariff.unknown": "Название тарифа не указано",

    "support.link": "<b>Поддержка:</b> <a href=\"{url}\">написать в Telegram</a>",
    "support.code": "<b>Поддержка:</b> <code>{contact}</code>",
    "support.default": "<b>Поддержка:</b> создайте обращение через раздел «Помощь».",
    "support.ticket_hint": (
        "Нужна помощь? Откройте «Помощь» и создайте обращение — ответ придёт "
        "сюда, в бот."
    ),

    "home.greeting": "Здравствуйте, <b>{name}</b>.",
    "home.hint_active": (
        "Нажмите «Подключить VPN», чтобы открыть страницу подписки. Выберите "
        "устройство и следуйте инструкции — там же доступны загрузки."
    ),
    "home.hint_trial": (
        "Нажмите «Получить пробный доступ». После активации откроется страница "
        "подписки с инструкциями и загрузками для вашего устройства."
    ),
    "home.hint_no_trial": (
        "Чтобы начать, откройте тарифы. На странице подписки собраны "
        "инструкции и загрузки; если нужна помощь, напишите нам через тикет."
    ),
    "setup.start_button": "Начать настройку VPN",
    "setup.intro": (
        "Я помогу настроить VPN по шагам. Сначала получим доступ, затем "
        "установим приложение, добавим подписку и включим подключение."
    ),
    "setup.access_title": "<b>Шаг 1 из 4 · Получить доступ</b>",
    "setup.access_note": (
        "Для настройки сначала нужен активный доступ. Можно начать с "
        "бесплатного пробного периода или посмотреть тарифы."
    ),
    "setup.install_title": "<b>Шаг 2 из 4 · Установить приложение</b>",
    "setup.install_note": (
        "Откройте страницу подписки на этом устройстве. Там собраны "
        "инструкции и официальные ссылки для установки Happ."
    ),
    "setup.add_title": "<b>Шаг 3 из 4 · Добавить подписку</b>",
    "setup.add_note": (
        "Откройте страницу подписки и добавьте профиль в Happ по инструкции. "
        "Если приложение ещё не установлено, скачайте его там же."
    ),
    "setup.connect_title": "<b>Шаг 4 из 4 · Включить VPN</b>",
    "setup.connect_note": (
        "Откройте Happ и нажмите кнопку подключения. После подключения "
        "проверьте, что сайты открываются. Сам бот не может определить, "
        "включён ли VPN на устройстве."
    ),
    "setup.get_trial": "Получить пробный доступ",
    "setup.open_page": "Открыть страницу подписки",
    "setup.next": "Далее",
    "setup.finish": "Готово",
    "setup.access_required": (
        "Чтобы продолжить настройку, сначала получите активный доступ."
    ),
    "setup.storage_unavailable": (
        "Не удалось сохранить шаг настройки. Повторите позже."
    ),
    "more.title": "ВАШ АККАУНТ",
    "home.access": "Доступ",
    "cabinet.title": "👤 Ваш аккаунт",
    "cabinet.account": "Аккаунт",
    "cabinet.about": (
        "Jopes VPN помогает подключаться к интернету через Happ. "
        "Здесь можно управлять подпиской, устройствами и настройками."
    ),
    "cabinet.subscription_box": "Подписка",
    "cabinet.check": "Проверка",
    "cabinet.check_failed": "Не удалось получить данные о подписке.",
    "cabinet.check_safe": "Подписка и устройства не изменены.",
    "cabinet.check_retry": "Повторите проверку через минуту.",
    "cabinet.telegram_id": "Telegram ID: <code>{telegram_id}</code>",
    "cabinet.jopes_id": "JopesVPN-ID: <b>{panel_id}</b>",
    "cabinet.jopes_id_unknown": "JopesVPN-ID: —",
    "cabinet.status_row": "Статус: {status}",
    "cabinet.access": "Доступ",
    "cabinet.no_subscription_note": "Активной подписки пока нет.",
    "cabinet.trial_available": "Можно получить бесплатный пробный период.",
    "cabinet.plans_hint": "Тарифы и условия — в разделе «Тарифы».",
    "cabinet.tariff_row": "Тариф: {tariff}",
    "cabinet.expires_row": "Действует до: {expires}",
    "cabinet.remaining_row": "Осталось: {remaining}",
    "cabinet.traffic_row": "Трафик: {traffic}",
    "cabinet.devices_row": "Устройства: {devices}",
    "cabinet.note_expired": "Срок доступа закончился. Продлите доступ, чтобы подключиться.",
    "cabinet.note_disabled": (
        "Доступ отключён. Если это ошибка, напишите в поддержку — включение "
        "делает администратор."
    ),
    "cabinet.note_limited": (
        "Доступ ограничен. Проверьте устройства и при необходимости освободите "
        "слот или напишите в поддержку."
    ),
    "cabinet.note_active": "Доступ активен. Для подключения откройте «Подключить VPN».",
    "cabinet.note_unknown": (
        "Статус не распознан. Напишите в поддержку, чтобы проверили доступ."
    ),

    "subscription.title": "📄 МОЯ ПОДПИСКА",
    "subscription.status_row": "Статус: {status}",
    "subscription.tariff_row": "Тариф: {tariff}",
    "subscription.expires_row": "Действует до: {expires}",
    "subscription.remaining_row": "Осталось: {remaining}",
    "subscription.traffic_row": "Трафик: {traffic}",
    "subscription.devices_row": "Устройства: {devices}",
    "subscription.active_note": (
        "<b>Подключение</b>\n"
        "Основной способ — страница подписки. На ней собраны приложения, "
        "инструкции и ссылка для импорта."
    ),
    "subscription.page_missing": (
        "Страница подписки сейчас недоступна. Напишите в поддержку — мы "
        "поможем восстановить ссылку безопасным способом."
    ),
    "subscription.secret_hidden": (
        "Ссылка подключения не отправляется в чат. Откройте защищённую "
        "страницу подписки и добавьте профиль в Happ по инструкции."
    ),
    "subscription.expired_note": (
        "<b>Доступ закончился</b>\n"
        "Продление оформляет поддержка или администратор. После продления "
        "ссылка заработает снова."
    ),
    "subscription.disabled_note": (
        "<b>Доступ отключён</b>\nЕсли это ошибка, напишите в поддержку."
    ),
    "subscription.limited_note": (
        "<b>Доступ ограничен</b>\n"
        "Проверьте раздел «Мои устройства». Если ограничение не связано с "
        "лимитом, напишите в поддержку."
    ),
    "subscription.inactive_note": (
        "<b>Доступ не активен</b>\nПроверьте статус или напишите в поддержку."
    ),
    "no_subscription.title": "📄 МОЯ ПОДПИСКА",
    "no_subscription.status_box": "Статус",
    "no_subscription.none": "Активной подписки пока нет.",
    "no_subscription.trial": "Можно получить бесплатный пробный период.",
    "no_subscription.hint": (
        "Выбрать тариф можно в разделе «Тарифы». Если нужна помощь, откройте «Помощь»."
    ),

    "trial.title": "🎁 ПРОБНЫЙ ДОСТУП",
    "trial.conditions": "Условия",
    "trial.duration": "Срок: {hours_word}",
    "trial.once": "Выдаётся один раз на Telegram-аккаунт.",
    "trial.ends": "После окончания доступ отключается.",
    "trial.how": "<b>Как получить</b>",
    "trial.step_channel": "1. Подпишитесь на канал «{channel}».",
    "trial.step_check": "2. Вернитесь в бот и нажмите «Проверить подписку».",
    "trial.resources": (
        "<b>Официальные ресурсы JopesVPN</b>\n"
        "Канал: {channel}\n"
        "Общение: {community}\n"
        "Отзывы: {reviews}\n"
        "Подписку для получения пробного доступа проверяем только на канале."
    ),
    "trial.resource_channel": "JopesVPN",
    "trial.unavailable_title": "Недоступен",
    "trial.already_created": "Для этого Telegram-аккаунта уже создан доступ.",
    "trial.once_only": "Повторно пробный период не выдаётся.",
    "trial.check_subscription": "Откройте «Моя подписка», чтобы проверить статус и срок.",
    "trial.activated": "✅ ПРОБНЫЙ ДОСТУП АКТИВИРОВАН",
    "trial.activated_note": (
        "Дальше настроим подключение по шагам."
    ),
    "trial.not_configured": "Пробный период не настроен. Сообщите администратору.",
    "trial.channel_check_failed": (
        "Не удалось проверить подписку на канал. Бот должен быть "
        "администратором канала. Сообщите администратору."
    ),
    "trial.telegram_unavailable": (
        "Telegram временно не ответил при проверке подписки. Повторите попытку позже."
    ),
    "trial.channel_missing_title": "🔔 ПОДПИСКА НА КАНАЛ НЕ НАЙДЕНА",
    "trial.channel_missing_note": (
        "Подпишитесь на канал и нажмите «Проверить подписку» ещё раз."
    ),
    "trial.not_confirmed": (
        "Не удалось проверить доступ к этому действию. Попробуйте позже."
    ),
    "trial.unavailable_generic": (
        "Это действие сейчас недоступно. Откройте раздел «Тарифы» или «Помощь»."
    ),
    "trial.button_channel": "📣 Подписаться на канал",
    "trial.button_community": "💬 Сообщество",
    "trial.button_reviews": "⭐ Отзывы",
    "trial.button_resources": "📚 Все ресурсы JopesVPN",
    "trial.button_check": "✅ Проверить подписку",

    "traffic.data_unavailable": "Данные о трафике пока недоступны.",
    "traffic.unlimited": "Трафик без лимита",
    "traffic.used_unlimited": "Использовано {used} · лимита нет",
    "traffic.remaining_unknown": "Осталось — из {total} · объём использования неизвестен",
    "traffic.remaining": "Осталось {remaining} из {total} · использовано {used}",
    "traffic.valid_until_exhausted": "До исчерпания пакета",

    "plans.title": "💎 ТАРИФЫ",
    "plans.duration": "Срок: {days} дней",
    "plans.price_unknown": "Цена: уточните у поддержки",
    "plans.price_missing": "цена не задана",
    "plans.price": "Цена: {price}",
    "plans.minimum_duration": "Минимальный срок: {days} дн.",
    "plans.minimum_price": "Цена за минимальный срок: {price}",
    "plans.choose_term_text": (
        "<b>{plan}</b>\n{conditions}\nМинимальный срок: {minimum} дн.\n\n"
        "{cycle_note}\nВыберите срок. Полная цена в рублях показана в кнопке "
        "каждого срока."
    ),
    "plans.confirm_order_text": (
        "<b>{plan}</b>\n"
        "Срок: {days} дней.\n"
        "Выберите способ оплаты. Полная сумма указана на каждой кнопке."
    ),
    "plans.traffic_term": "{gb} ГБ, обновление: {period}",
    "plans.calendar_cycle_note": (
        "Лимит обновляется по календарному расписанию Panel, а не от даты "
        "покупки. Если подписка действует в следующем цикле, квота обновится "
        "по его расписанию. Уже использованный в текущем цикле трафик "
        "не сбрасывается при продлении."
    ),
    "plans.no_reset_cycle_note": (
        "Лимит не сбрасывается; объём действует в течение выбранного срока."
    ),
    "plans.period_no_reset": "на весь срок, без сброса",
    "plans.period_day": "ежедневно",
    "plans.period_week": "еженедельно",
    "plans.period_month": "ежемесячно",
    "plans.period_month_rolling": "каждые 30 дней",
    "plans.discount": "Скидка {percent}% · экономия {savings}",
    "plans.no_discount": "Скидки нет",
    "plans.admin_free": "Бесплатно · 0 ₽ / 0 ⭐",
    "plans.buy_button": "Купить тариф",
    "plans.category_time": "♾ Безлимит по времени",
    "plans.category_traffic": "📦 Лимит трафика",
    "plans.family_no_reset": "📦 Пакеты на срок · {count}",
    "plans.family_day": "☀️ Суточный лимит · {count}",
    "plans.family_week": "🗓 Недельный лимит · {count}",
    "plans.family_month": "📅 Месячный лимит · {count}",
    "plans.family_month_rolling": "🔄 Цикл 30 дней · {count}",
    "plans.family_note_no_reset": (
        "Общий объём на весь срок подписки, без периодического сброса."
    ),
    "plans.family_note_day": (
        "Квота обновляется по суточному расписанию Panel; остаток не переносится."
    ),
    "plans.family_note_week": (
        "Квота обновляется по недельному расписанию Panel; остаток не переносится."
    ),
    "plans.family_note_month": (
        "Квота обновляется по календарному месяцу Panel; остаток не переносится."
    ),
    "plans.family_note_month_rolling": (
        "Квота обновляется по циклу Panel; остаток не переносится."
    ),
    "plans.free_button": "🆓 FREE · 200 МБ в сутки",
    "plans.free_title": "🆓 FREE · 200 МБ в сутки",
    "plans.free_available": (
        "Нет действующей подписки? Можно подключить FREE: 200 МБ на сутки, "
        "квота обновляется каждый день по UTC."
    ),
    "plans.free_conditions": (
        "Бесплатно доступны 200 МБ в сутки. Квота обновляется по UTC, "
        "неиспользованный остаток не переносится. Доступ выдаётся, если "
        "в панели нет активной подписки."
    ),
    "plans.free_unavailable": (
        "FREE можно подключить, когда в панели нет действующей подписки. "
        "Откройте «Моя подписка» или попробуйте позже."
    ),
    "plans.free_confirm": "🆓 Подключить FREE",
    "plans.free_success": (
        "✅ FREE подключён. Квота {quota} обновляется раз в сутки по UTC. "
        "Статус и остаток можно посмотреть в разделе «Моя подписка»."
    ),
    "plans.free_already_active": (
        "У вас уже подключён FREE. Новая квота автоматически обновится "
        "по расписанию, повторно получать тариф не нужно."
    ),
    "plans.free_blocked": (
        "FREE сейчас нельзя подключить: в панели есть действующая, "
        "ограниченная или отключённая подписка. Напишите в поддержку, "
        "если считаете, что это ошибка."
    ),
    "plans.free_failed": (
        "Не удалось подтвердить выдачу FREE. Тариф мог примениться в панели, "
        "поэтому проверьте «Мою подписку» перед повторной попыткой."
    ),
    "plans.back_to_categories": "◀️ К выбору тарифа",
    "plans.choose_category": (
        "Выберите формат доступа:\n"
        "• безлимитный трафик на выбранный срок;\n"
        "• тариф с лимитом, который может обновляться по расписанию."
    ),
    "plans.time_section": "♾ Безлимитный трафик на срок",
    "plans.traffic_section": "📦 Тарифы с лимитом трафика",
    "plans.traffic_unavailable": (
        "Покупка пакетов временно не подключена. Сейчас можно выбрать "
        "безлимитный тариф по времени."
    ),
    "plans.traffic_never_expires": (
        "Пакет доступен до исчерпания трафика; крайняя дата доступа — 01.01.2099."
    ),
    "plans.switch_policy": (
        "Срок подписки и цикл лимита — разные условия. В тарифах DAY, WEEK и "
        "MONTH лимит обновляется по расписанию Panel, неиспользованный трафик "
        "на следующий цикл не переносится. Цикл задаётся расписанием Panel, "
        "а не датой покупки. В тарифах NO_RESET остаток сохраняется до окончания "
        "подписки. При смене тарифа лимит применяется по условиям нового тарифа."
    ),
    "plans.purchase_blocked": (
        "Сейчас нельзя оформить покупку для этого аккаунта. "
        "Если нужна помощь, создайте обращение в разделе «Помощь»."
    ),
    "plans.invoice_title": "Jopes VPN · {plan}",
    "plans.invoice_description": "{plan} · безлимитный доступ на {days} дней",
    "plans.invoice_description_traffic": (
        "{plan} · {gb} ГБ до исчерпания, доступ максимум до 01.01.2099"
    ),
    "plans.invoice_sent": "Счёт отправлен. Доступ будет выдан после подтверждения оплаты.",
    "plans.paid_review": (
        "Оплата получена, но автоматическая выдача доступа задержалась. "
        "Мы проверим заказ вручную; повторно оплачивать его не нужно."
    ),
    "plans.payment_success": (
        "✅ Подписка оформлена на {days} дней. Доступ до {expires}."
    ),
    "plans.payment_success_traffic": (
        "✅ Пакет на {gb} ГБ подключён. Пользуйтесь им до исчерпания трафика."
    ),
    "plans.payment_success_traffic_limited": (
        "✅ Пакет на {gb} ГБ подключён. Доступ действует до {expires}."
    ),
    "plans.admin_free_success": (
        "✅ Тариф «{plan}» назначен администратору без оплаты. {details}"
    ),
    "plans.admin_free_unavailable": (
        "Не удалось безопасно зарегистрировать выдачу. Тариф не менялся."
    ),
    "plans.admin_free_duplicate": (
        "Это нажатие уже обработано. Проверьте карточку тарифа перед повторной выдачей."
    ),
    "plans.admin_free_unrecorded": (
        "Panel подтвердила тариф, но запись защиты от повтора не обновилась. "
        "Не нажимайте кнопку снова: сначала проверьте карточку."
    ),
    "plans.payment_failed": (
        "Платёж подтверждён, но не удалось обновить доступ. "
        "Мы проверим заказ; не оплачивайте его повторно."
    ),
    "plans.payment_support": (
        "По вопросам оплаты укажите номер заказа: <code>{order_id}</code>. "
        "Мы проверим платёж и поможем."
    ),
    "plans.payment_support_title": "Поддержка по оплате",
    "plans.refund_done": "Возврат выполнен по заказу <code>{order_id}</code>.",
    "plans.refund_unavailable": "Не удалось оформить возврат. Заказ передан на проверку.",
    "plans.refund_active_user": (
        "Сначала приостановите действующую подписку и проверьте заказ вручную."
    ),
    "plans.refund_user_notice": (
        "Возврат оплаты Stars выполнен. Если у вас остались вопросы, откройте "
        "раздел «Помощь» в боте."
    ),
    "plans.refund_admin": "ℹ️ Возврат Stars подтверждён по заказу <code>{order_id}</code>.",
    "plans.payment_support_unavailable": (
        "Не удалось открыть сведения о заказе. Попробуйте позже или создайте "
        "обращение в разделе «Помощь»."
    ),
    "plans.invalid_invoice": "Счёт устарел или недействителен. Откройте тарифы и создайте новый.",
    "plans.payment_issue_admin": (
        "⚠️ Заказ <code>{order_id}</code> оплачен, но требует ручной проверки."
    ),
    "plans.payment_review_admin": (
        "⚠️ Оплаченный заказ требует проверки.\n"
        "Заказ: <code>{order_id}</code>\n"
        "Telegram ID: <code>{telegram_id}</code>\n"
        "Тариф: {plan} · {amount} ⭐"
    ),
    "plans.time_limit_note": (
        "Покупка продлевает срок активного доступа. Пакет трафика добавляется "
        "к оставшемуся объёму и действует до указанной даты."
    ),
    "plans.traffic_volume": "Объём: {gb} ГБ",
    "subscription.notice_granted": (
        "✅ <b>Доступ к Jopes VPN выдан</b>\n\nПодписка действует до {expires}."
    ),
    "subscription.notice_extended": (
        "✅ <b>Подписка продлена</b>\n\n"
        "Доступ к Jopes VPN продлён до {expires}. Продолжайте пользоваться VPN как обычно."
    ),
    "subscription.notice_extended_disabled": (
        "✅ <b>Срок подписки продлён</b>\n\n"
        "Подписка действует до {expires}, но доступ остаётся отключённым. "
        "Если это произошло по ошибке, откройте «Помощь»."
    ),
    "subscription.notice_disabled": (
        "⚠️ <b>Подписка отключена</b>\n\n"
        "Доступ к VPN приостановлен. Если считаете, что это ошибка, откройте «Помощь»."
    ),
    "subscription.notice_enabled": (
        "✅ <b>Доступ восстановлен</b>\n\n"
        "Подписка действует до {expires}. Можно подключаться как обычно."
    ),
    "subscription.notice_expired": (
        "⚠️ <b>Доступ включён, но срок подписки закончился</b>\n\n"
        "Срок действия: {expires}. Откройте раздел «Тарифы» или «Помощь»."
    ),
    "subscription.notice_limited": (
        "⚠️ <b>Доступ остаётся ограниченным</b>\n\n"
        "Так показывает панель управления. Проверьте остаток трафика и "
        "обратитесь в «Помощь», если ограничение неожиданное."
    ),
    "subscription.notice_revoked": (
        "⚠️ <b>Подписка отозвана</b>\n\n"
        "Данные подписки больше не действуют. Если нужна помощь, откройте «Помощь»."
    ),
    "plans.trial_title": "Пробный доступ",
    "plans.trial_note": "Бесплатно на {hours_word}.",
    "plans.disclaimer": (
        "Оплатить можно только Telegram Stars. До выставления счёта бот "
        "покажет полную цену в рублях, срок и сумму в Stars."
    ),
    "plans.pay_stars": "⭐ Telegram Stars · {amount} ⭐",
    "plans.pay_balance": "💰 Оплатить с баланса · {amount}",
    "plans.pay_sbp": "СБП · {amount} · техработы",
    "plans.pay_card": "Карта · {amount} · техработы",
    "plans.pay_crypto": "Криптовалюта · {amount} · техработы",
    "plans.method_sbp": "СБП",
    "plans.method_card": "Банковская карта",
    "plans.method_crypto": "Криптовалюта",
    "plans.payment_method_unavailable": (
        "Оплата через {method} пока недоступна, идут технические работы. "
        "Стоимость: {rubles}. Заказ и платёж не создавались. "
        "Выберите Telegram Stars или вернитесь к тарифам."
    ),
    "plans.payment_method_title": "Способ оплаты",
    "plans.payment_method_text": (
        "<b>{plan}</b>\nВыберите способ оплаты. Полная сумма указана "
        "на каждой кнопке."
    ),
    "plans.unlimited_traffic": "Безлимитный трафик",
    "plans.payment_unavailable": (
        "Этот способ оплаты пока не подключён. Заказ и платёж не создавались. "
        "Выберите Telegram Stars или вернитесь к тарифам."
    ),
    "plans.button_trial": "Бесплатный пробный доступ",
    "plans.button_how": "Как подключиться",
    "plans.button_support": "Связаться с поддержкой",

    "about.title": "О СЕРВИСЕ",
    "about.intro": (
        "Jopes VPN помогает безопасно пользоваться интернетом через VPN. "
        "Управлять подпиской и получать помощь можно в этом боте."
    ),
    "about.tariffs": "Тарифы",
    "about.tariff_process": (
        "В разделе «Тарифы» сначала выберите тариф и его условия, затем "
        "длительность. Полная цена в рублях указана на кнопке каждого срока, "
        "а сумма выбранного способа оплаты показана до выставления счёта. "
        "Доступна только оплата Stars. "
        "Если что-то непонятно, напишите в поддержку через /help."
    ),
    "about.unlimited_days": "безлимитный трафик на {days} дней",
    "about.traffic_package": (
        "{gb} ГБ до исчерпания; техническая крайняя дата доступа — 01.01.2099"
    ),
    "about.free_enabled": (
        "🎁 <b>FREE:</b> после завершения пробного периода без активной платной "
        "подписки доступен лимит 200 МБ в сутки. Суточный лимит обновляется "
        "раз в день, остаток не переносится."
    ),
    "about.free_unavailable": (
        "🎁 <b>FREE:</b> лимит 200 МБ в сутки временно выключен, пока не "
        "подтверждено расписание его обновления. Пробный и платные тарифы "
        "показываются в разделе «Тарифы»."
    ),
    "about.payment_title": "Оплата",
    "about.payment_status": (
        "Сейчас доступна оплата только через Telegram Stars. Полная сумма "
        "показывается на кнопке способа оплаты до выставления счёта."
    ),
    "about.connection": (
        "Подключение: выберите тариф, получите доступ, откройте инструкцию "
        "для своего устройства и добавьте профиль в приложение."
    ),
    "about.refunds": (
        "По вопросам оплаты и возврата напишите в поддержку через /help. "
        "Запрос рассмотрят по условиям оплаты и применимым правилам."
    ),
    "about.documents_ready": (
        "Пользовательское соглашение и политика конфиденциальности доступны "
        "по кнопкам ниже."
    ),
    "about.documents_missing": (
        "Ссылки на соглашение и политику пока не настроены. Напишите в "
        "поддержку, если они нужны до подключения."
    ),
    "about.document_missing": "Ссылка на этот документ пока не настроена.",
    "free.daily_report": (
        "📊 <b>FREE · статистика за прошлые сутки</b>\n"
        "Период начался: {day}\n\n"
        "Использовано: {used}\n"
        "Квота на сутки: {quota}\n"
        "Остаток: {remaining}"
    ),
    "traffic.alert_50": (
        "📊 Использовано {used} из {limit} ({percent}%). "
        "Осталось {remaining}."
    ),
    "traffic.alert_75": (
        "⚠️ Использовано уже {used} из {limit} ({percent}%). "
        "Осталось {remaining}."
    ),
    "traffic.alert_exhausted_limited": (
        "⛔ Пакет трафика исчерпан ({used} из {limit}). "
        "Панель ограничила доступ. Откройте тарифы, чтобы выбрать следующий пакет."
    ),
    "traffic.alert_exhausted_pending": (
        "⛔ Пакет трафика исчерпан ({used} из {limit}). "
        "Панель ещё обновляет статус доступа. Проверьте подписку или выберите тариф."
    ),

    "connect.title": "🔌 ПОДКЛЮЧЕНИЕ VPN",
    "connect.check_failed": "Не удалось проверить доступ.",
    "connect.check_retry": "Повторите попытку через минуту.",
    "connect.still_active": "Если доступ был активен, он не отключился.",
    "connect.no_subscription_note": "Сначала получите доступ.",
    "connect.trial_note": "Можно получить бесплатный пробный период.",
    "connect.tariff_note": (
        "Выберите тариф в разделе «Тарифы». Стоимость и условия показаны перед оформлением."
    ),
    "connect.page_after": "После выдачи доступа здесь появится страница подписки.",
    "connect.inactive_note": (
        "Пока доступ не активен, подключиться не получится. Сначала продлите "
        "или восстановите доступ."
    ),
    "connect.method_one": "<b>Начните со страницы подписки</b>",
    "connect.method_one_note": (
        "Там собраны подходящие приложения, официальные загрузки и пошаговые "
        "инструкции, в том числе добавление профиля в Happ."
    ),
    "connect.page_missing": "Страница сейчас недоступна — напишите в поддержку.",
    "connect.expires": "<b>Действует до:</b> {expires}",
    "connect.remaining": "<b>Осталось:</b> {remaining}",
    "connect.not_confirmed": (
        "Бот не проверяет, включён ли VPN на устройстве: подключение "
        "подтверждается только в приложении."
    ),
    "connect.button_page": "🌐 Открыть страницу подписки",
    "connect.button_problems": "🧰 Проблемы",
    "connect.button_support": "💬 Написать в поддержку",

    "guides.title": "📚 НАСТРОЙКА VPN",
    "guides.subscription_page_first": (
        "Сначала откройте страницу подписки: там уже собраны инструкции и "
        "загрузки для вашего устройства. Там же можно добавить профиль в Happ."
    ),
    "guides.choose_device": (
        "Выберите устройство, на котором хотите использовать Jopes VPN. Бот "
        "покажет подходящие приложения и проведёт по шагам."
    ),
    "guides.happ_hint": (
        "💡 Не знаете, какое приложение выбрать? Начните с Happ — он есть "
        "на Android, iPhone, Windows, macOS и Linux."
    ),
    "guides.platform_title": "📚 НАСТРОЙКА VPN · {platform}",
    "guides.apps_box": "Подходящие приложения",
    "guides.choose_app": (
        "Выберите приложение. Бот проведёт по шагам: установка, добавление "
        "подписки, подключение и проверка."
    ),
    "guides.happ_platform_hint": (
        "💡 Если не знаете, что выбрать, начните с Happ: он есть на всех "
        "поддерживаемых платформах."
    ),
    "guides.step_of": "Шаг {step} из {total}",
    "guides.secret_note": (
        "⚠️ Не публикуйте ссылку подписки: она даёт доступ к вашему VPN."
    ),
    "guides.download": "⬇️ Скачать приложение",
    "guides.not_working": "😕 Не получилось подключиться",
    "guides.platform_android": "Android",
    "guides.platform_ios": "iPhone / iPad",
    "guides.platform_windows": "Windows",
    "guides.platform_macos": "macOS",
    "guides.platform_linux": "Linux",
    "guides.name_android": "Android",
    "guides.name_ios": "iPhone и iPad",
    "guides.name_windows": "Windows",
    "guides.name_macos": "macOS",
    "guides.name_linux": "Linux",
    "guides.step1": (
        "<b>Шаг 1. Установите {name}</b>\n\n"
        "Скачайте приложение для {platform} по официальной ссылке кнопкой ниже. "
        "Не устанавливайте одноимённые приложения из случайных источников."
    ),
    "guides.step2": (
        "<b>Шаг 2. Добавьте подписку</b>\n\n"
        "Откройте {name}. {import_first}\n\n"
        "{import_second}\n\n"
        "Откройте страницу подписки: на ней есть инструкция для вашего "
        "устройства. Для Happ выполните добавление по этой инструкции."
    ),
    "guides.step3": (
        "<b>Шаг 3. Обновите серверы</b>\n\n"
        "После добавления подписки обновите список серверов: в большинстве "
        "клиентов это отдельная кнопка «Обновить» рядом с подпиской.\n\n"
        "В списке должен появиться сервер Jopes VPN. Если список пуст, проверьте, "
        "что ссылка добавлена именно как подписка, а не как отдельный ключ."
    ),
    "guides.step4": (
        "<b>Шаг 4. Подключитесь</b>\n\n"
        "Выберите сервер Jopes VPN и нажмите большую кнопку подключения.\n\n"
        "Система спросит разрешение на VPN-подключение — разрешите его.{ios_note}"
    ),
    "guides.ios_note": " На iPhone может понадобиться подтвердить добавление конфигурации.",
    "guides.step5": (
        "<b>Шаг 5. Проверьте соединение</b>\n\n"
        "Откройте любой сайт в браузере. Если он загружается, соединение работает.\n\n"
        "Если сайт не открывается: отключите другие VPN, переключите сервер, "
        "обновите подписку и попробуйте снова. Не удаляйте подписку до проверки."
    ),
    "guides.import.happ.first": (
        "Нажмите «+» в правом верхнем углу и выберите добавление подписки."
    ),
    "guides.import.happ.second": (
        "Вставьте ссылку подписки, которую вы скопировали из бота, и сохраните её."
    ),
    "guides.import.v2rayng.first": (
        "Нажмите «+» в правом верхнем углу и выберите «Импорт из буфера обмена»."
    ),
    "guides.import.v2rayng.second": (
        "Вставьте ссылку подписки, которую вы скопировали из бота, и подтвердите "
        "добавление."
    ),
    "guides.import.v2raytun.first": (
        "Нажмите «+» и выберите добавление подписки или импорт из буфера обмена."
    ),
    "guides.import.v2raytun.second": "Вставьте ссылку подписки и сохраните её.",
    "guides.import.streisand.first": (
        "Нажмите «+» в правом верхнем углу и выберите добавление подписки."
    ),
    "guides.import.streisand.second": "Вставьте ссылку подписки из бота и сохраните её.",

    "devices.title": "📱 МОИ УСТРОЙСТВА",
    "devices.no_access_note": "Сначала нужна активная подписка.",
    "devices.no_access_after": "После получения доступа здесь появится список устройств.",
    "devices.error_list": "Не удалось получить список устройств.",
    "devices.empty_title": "Список пуст",
    "devices.empty_hwid": (
        "Устройство появляется здесь после первого подключения, если клиент "
        "передаёт HWID."
    ),
    "devices.empty_clients": (
        "Happ и большинство современных клиентов делают это автоматически."
    ),
    "devices.empty_refresh": "Если вы уже подключались, нажмите «Обновить».",
    "devices.empty_old": (
        "Некоторые старые клиенты не передают HWID: VPN работает, но устройство "
        "не отображается."
    ),
    "devices.limit_reached": (
        "<b>Лимит устройств достигнут.</b> Новые устройства не смогут получить "
        "доступ, пока вы не удалите устройство из списка выше."
    ),
    "devices.limit_meaning": (
        "<b>Что означает лимит</b>\n"
        "Если в тарифе задан лимит, подключение разрешено только с этого числа "
        "устройств. При достижении лимита новое устройство не получит подписку. "
        "Удаление освобождает слот; после этого подключитесь на устройстве заново."
    ),
    "devices.ip_note": (
        "Удаляйте только свои устройства. IP-адреса и отдельные соединения "
        "не являются устройствами и здесь не показываются."
    ),
    "devices.delete_button": "🗑 Удалить: {label}",
    "devices.confirm_title": "🗑 УДАЛИТЬ УСТРОЙСТВО?",
    "devices.confirm_box": "Устройство",
    "devices.unnamed": "Устройство без названия",
    "devices.confirm_note": (
        "Оно потеряет доступ к VPN и освободит один слот. Чтобы вернуть "
        "устройство, подключитесь на нём заново — подписка останется прежней."
    ),
    "devices.confirm_irreversible": (
        "⚠️ Это действие нельзя отменить кнопкой «Назад»."
    ),
    "devices.confirm_yes": "✅ Да, удалить устройство",
    "devices.button_how": "🛠 Как подключить",
    "devices.page_prev": "◀️ Страница",
    "devices.page_next": "Ещё ▶️",

    "stats.title": "📊 СТАТИСТИКА",
    "stats.usage": "Использование",
    "stats.no_data": "Не удалось получить данные.",
    "stats.after_access": "Статистика появится после получения доступа к VPN.",
    "stats.status_row": "Статус: {status}",
    "stats.traffic_row": "Трафик: {traffic}",
    "stats.remaining_row": "Осталось: {remaining}",
    "stats.lifetime_row": "За всё время: {lifetime}",
    "stats.devices_row": "Устройства: {devices}",
    "stats.last_activity": "Последняя активность: {value}",
    "stats.first_connection": "Первое подключение: {value}",
    "stats.note": (
        "Данные обновляет сервис. Если статистика отстаёт, нажмите "
        "«🔄 Обновить»."
    ),

    "settings.title": "⚙️ НАСТРОЙКИ",
    "settings.account": "Аккаунт",
    "settings.reminders": "Напоминания",
    "settings.reminders_schedule": "Предупреждения за 3 дня, за 1 день и в день окончания.",
    "settings.reminders_once": "Не чаще одного раза в день.",
    "settings.reminders_state": "Сейчас: {state}",
    "settings.reminders_on": "включены",
    "settings.reminders_off": "выключены",
    "settings.access": "Доступ: {status}",
    "settings.language": "Язык: {name}",
    "settings.language_button": "🌐 Выбрать язык",
    "settings.language_title": "🌐 ВЫБЕРИТЕ ЯЗЫК",
    "settings.language_current": "Сейчас выбран: {flag} {name}",
    "settings.language_selected": "выбран",
    "settings.language_page": "Страница {page} из {pages}",
    "settings.language_note": (
        "🌍 Язык интерфейса меняется кнопками ниже."
    ),
    "settings.timezone": "Ваш часовой пояс: {offset}",
    "settings.timezone_button": "🕒 Выбрать часовой пояс",
    "settings.timezone_title": "🕒 ВЫБЕРИТЕ ЧАСОВОЙ ПОЯС",
    "settings.timezone_note": (
        "Время окончания доступа и сообщений будет показываться в выбранном "
        "поясе. Сейчас: {offset}."
    ),
    "settings.timezone_saved": "Часовой пояс сохранён: {offset}.",
    "settings.timezone_failed": "Не удалось сохранить часовой пояс.",
    "settings.timezone_unavailable": "Хранилище настроек сейчас недоступно.",
    "settings.storage_unavailable": (
        "<b>⚙️ НАСТРОЙКИ</b>\n\n<blockquote>┌ <b>Хранилище</b>\n"
        "├ Настройки недоступны.\n└ Напоминания сейчас не работают.</blockquote>"
    ),
    "settings.storage_temporary": (
        "<b>⚙️ НАСТРОЙКИ</b>\n\n<blockquote>┌ <b>Хранилище</b>\n"
        "├ Настройки временно недоступны.\n"
        "└ Напоминания сейчас не работают.</blockquote>"
    ),
    "settings.disable_reminders": "🔕 Выключить напоминания",
    "settings.enable_reminders": "🔔 Включить напоминания",

    "help.title": "🆘 ПОДДЕРЖКА И ПОМОЩЬ",
    "help.sections": "Разделы",
    "help.commands": (
        "Быстрые команды: /subscription · /account · /guides · /devices · "
        "/stats · /settings · /plans · /referrals."
    ),
    "help.cabinet": "Личный кабинет — статус, тариф, трафик и устройства.",
    "help.connect": "Подключить VPN — страница подписки и ссылка для приложения.",
    "help.devices": "Мои устройства — список устройств и лимит.",
    "help.guides": "Инструкции — Happ, v2rayNG, v2rayTun и Streisand.",
    "help.problems": "Частые проблемы — что проверить, если VPN не работает.",
    "help.contact": "<b>Поддержка:</b> {contact}",
    "help.contact_link": "<b>Поддержка:</b> <a href=\"{url}\">написать в Telegram</a>",
    "help.contact_default": "<b>Поддержка:</b> напишите администратору сервиса.",
    "help.note": (
        "Опишите, что не получается, и укажите устройство и приложение. "
        "Не отправляйте ссылку подписки, ключи или пароли."
    ),
    "help.ticket_note": (
        "Поддержка отвечает прямо здесь. Создайте обращение, выберите тему "
        "и дождитесь ответа в переписке. В любой момент можно продолжить "
        "или закрыть обращение."
    ),
    "ticket.open_button": "✉️ Написать в поддержку",
    "ticket.view_button": "📨 Открыть переписку",
    "ticket.portal_button": "🌐 Открыть на сайте",
    "ticket.my_button": "📨 Мои обращения",
    "ticket.admin_button": "🎫 Обращения поддержки",
    "ticket.open_title": "О ЧЁМ ВОПРОС?",
    "ticket.category_connection": "📡 Подключение",
    "ticket.category_account": "💳 Доступ и тариф",
    "ticket.category_other": "💬 Другой вопрос",
    "ticket.enter_message": (
        "Напишите одним сообщением, что случилось. Можно приложить скриншот. "
        "Не отправляйте ссылку подписки, VLESS-ключи, пароли или платёжные данные."
    ),
    "ticket.enter_reply": "Напишите ответ сообщением или приложите скриншот.",
    "ticket.cancel_button": "Отменить",
    "ticket.created": "Обращение #{ticket_id} создано. Ответ придёт сюда в чат.",
    "ticket.reply_saved": "Сообщение добавлено в обращение #{ticket_id}.",
    "ticket.message_too_long": "Сообщение слишком длинное. Сократите его до 4000 символов.",
    "ticket.message_empty": "Отправьте текст или скриншот.",
    "ticket.unavailable": "Не удалось сохранить обращение. Попробуйте ещё раз позже.",
    "ticket.admin_notify_failed": (
        "Обращение сохранено, но уведомление поддержки не доставлено. "
        "Оно осталось в очереди."
    ),
    "ticket.admin_reply_failed": (
        "Ответ сохранён, но уведомление пользователю не доставлено."
    ),
    "ticket.my_title": "📨 МОИ ОБРАЩЕНИЯ",
    "ticket.empty": "Обращений пока нет.",
    "ticket.admin_title": "🎫 ОБРАЩЕНИЯ ПОДДЕРЖКИ",
    "ticket.admin_empty": "Открытых обращений нет.",
    "ticket.admin_open_count": "Открыто: {count}",
    "ticket.admin_page": "Страница {page} из {pages}",
    "ticket.user_page": "Страница {page} из {pages}",
    "ticket.admin_rating": "Оценка поддержки: {average} ({count} оценок)",
    "ticket.title": "Обращение #{ticket_id}",
    "ticket.category": "Тема: {category}",
    "ticket.status": "Статус: {status}",
    "ticket.status_open": "открыто",
    "ticket.status_closed": "закрыто",
    "ticket.updated": "Последнее сообщение: {value}",
    "ticket.user": "Пользователь: {name} · ID <code>{user_id}</code>",
    "ticket.messages": "Переписка",
    "ticket.history_older": "Показаны последние сообщения. Более ранняя переписка скрыта.",
    "ticket.history_page": "Часть переписки {page} из {pages}.",
    "ticket.age_now": "только что",
    "ticket.age_value": "{value} назад",
    "ticket.sender_user": "Вы",
    "ticket.sender_admin": "Поддержка",
    "ticket.photo_attached": "[прикреплён скриншот]",
    "ticket.reply_button": "↩️ Ответить",
    "ticket.close_button": "✅ Закрыть обращение",
    "ticket.rate_button": "⭐ Оценить поддержку",
    "ticket.rate_prompt": "Оцените, пожалуйста, ответ поддержки от 1 до 5.",
    "ticket.rating_request": (
        "Обращение #{ticket_id} закрыто.\n\n"
        "Помог ли ответ решить вопрос? Оцените работу поддержки от 1 до 5."
    ),
    "ticket.rating_saved": "Ваша оценка: {rating}/5",
    "ticket.rating_thanks": "Спасибо за оценку!",
    "ticket.rating_not_available": "Оценку можно оставить после ответа поддержки.",
    "ticket.closed_user": "Вы закрыли обращение #{ticket_id}. Спасибо, что написали нам.",
    "ticket.closed_admin": (
        "Ваше обращение #{ticket_id} закрыто. Спасибо, что обратились. "
        "Оценить ответ можно в карточке обращения."
    ),
    "ticket.not_found": "Обращение не найдено или у вас нет к нему доступа.",
    "ticket.no_open": (
        "Это обращение закрыто, продолжить переписку нельзя. Создайте новое, "
        "если остались вопросы."
    ),
    "ticket.close_prompt": (
        "С момента последнего ответа поддержки в обращении #{ticket_id} "
        "прошло 12 часов. Вопрос решён? Вы можете закрыть обращение и оценить "
        "ответ. Оно не закроется автоматически."
    ),
    "ticket.new_admin": (
        "Новое обращение #{ticket_id}\nТема: {category}\n"
        "Пользователь: {name} · ID <code>{user_id}</code>"
    ),
    "ticket.new_message_admin": (
        "Новое сообщение в обращении #{ticket_id}\n"
        "Пользователь: {name} · ID <code>{user_id}</code>"
    ),
    "ticket.new_message_user": "Ответ поддержки в обращении #{ticket_id}.",
    "ticket.closed_notice_admin": "Пользователь закрыл обращение #{ticket_id}.",
    "ticket.closed_details_unavailable": (
        "Обращение закрыто, но переписку не удалось загрузить. Откройте «Мои "
        "обращения» и попробуйте ещё раз."
    ),
    "ticket.rating_admin": "Оценка {rating}/5 в обращении #{ticket_id}.",
    "ticket.admin_required": "Раздел доступен только администратору.",
    "ticket.cancelled": "Действие отменено.",
    "troubleshooting.title": "🧰 ЧАСТЫЕ ПРОБЛЕМЫ",
    "troubleshooting.add_title": "Подписка не добавляется",
    "troubleshooting.add_1": "Откройте страницу подписки и добавьте профиль в Happ.",
    "troubleshooting.add_2": "Добавьте её именно как подписку.",
    "troubleshooting.add_3": "Проверьте, что скопирована вся строка без пробелов.",
    "troubleshooting.servers_title": "Нет серверов",
    "troubleshooting.servers_1": "Обновите подписку.",
    "troubleshooting.servers_2": "Проверьте статус в «Моя подписка».",
    "troubleshooting.servers_3": "При истёкшей подписке серверы могут не выдаваться.",
    "troubleshooting.connect_title": "Не подключается",
    "troubleshooting.connect_1": "Проверьте интернет.",
    "troubleshooting.connect_2": "Отключите другие VPN.",
    "troubleshooting.connect_3": "Выберите другой сервер и переподключитесь.",
    "troubleshooting.connect_4": "Перезапустите приложение.",
    "troubleshooting.limit_title": "Лимит устройств",
    "troubleshooting.limit_1": "Откройте «Мои устройства».",
    "troubleshooting.limit_2": "Удалите устройство, которым больше не пользуетесь.",
    "troubleshooting.limit_3": "Подключитесь на новом устройстве.",
    "troubleshooting.status_title": "Статус «Истёк» или «Отключён»",
    "troubleshooting.status_1": "Ссылка не заработает без продления или включения доступа.",
    "troubleshooting.status_2": "Напишите в поддержку.",
    "troubleshooting.service_title": "Сервис временно недоступен",
    "troubleshooting.service_1": "Подождите минуту и нажмите «Обновить».",
    "troubleshooting.service_2": "Не удаляйте подписку из приложения.",
    "troubleshooting.service_3": "После восстановления сервиса она продолжит работать.",
    "troubleshooting.secret": (
        "⚠️ Никогда не публикуйте ссылку подписки: она даёт доступ к вашему VPN."
    ),

    "alert.private_chat": "Откройте бота в личном чате.",
    "alert.stale_button": "Кнопка устарела. Откройте меню заново.",
    "alert.stale_button_short": "Кнопка устарела.",
    "alert.subscription_missing": "Подписка не найдена.",
    "alert.device_list_changed": "Список устройств изменился. Нажмите «Обновить».",
    "alert.device_confirm_stale": (
        "Подтверждение устарело. Откройте список устройств заново."
    ),
    "alert.device_gone": "Устройство уже удалено или список изменился.",
    "alert.device_deleted": "Устройство удалено. Слот освобождён.",
    "alert.settings_unavailable": "Настройки временно недоступны.",
    "alert.settings_failed": "Не удалось сохранить настройку.",
    "alert.reminders_on": "Напоминания включены.",
    "alert.reminders_off": "Напоминания выключены.",
    "alert.unknown_command": "Не знаю такую команду. Откройте меню: /start",
    "alert.private_start": "Откройте бота в личном чате, чтобы продолжить.",
    "alert.private_help": "Помощь доступна в личном чате с ботом.",
    "alert.private_status": "Проверка доступа доступна в личном чате.",
    "alert.private_connect": "Ссылка подписки выдаётся только в личном чате.",
    "alert.device_delete_limited": (
        "Слишком много удалений подряд. Подождите минуту и обновите список."
    ),
    "alert.device_delete_failed": "Не удалось удалить устройство. Попробуйте позже.",
    "alert.trial_not_configured": "Пробный период не настроен. Сообщите администратору.",
    "alert.trial_channel_check": (
        "Не удалось проверить подписку на канал. Бот должен быть "
        "администратором канала. Сообщите администратору."
    ),
    "alert.trial_telegram": (
        "Telegram временно не ответил при проверке подписки. Повторите попытку позже."
    ),
    "alert.trial_channel_missing_title": "🔔 ПОДПИСКА НА КАНАЛ НЕ НАЙДЕНА",
    "alert.trial_channel_missing_channel": "Канал: <b>{channel}</b>",
    "alert.trial_channel_missing_note": (
        "Подпишитесь на канал и нажмите «Проверить подписку» ещё раз."
    ),
    "alert.trial_not_confirmed": (
        "Не удалось подтвердить выдачу пробного доступа. Перед повтором "
        "проверьте пользователя в сервисе: операция могла завершиться без ответа."
    ),
    "alert.private_use_start": "Используйте /start в личном чате с ботом.",
    "alert.panel_bad_devices": "Сервис вернул некорректный список устройств.",
    "alert.panel_data": "Сервис вернул неожиданные данные. Попробуйте обновить позже.",
    "alert.panel_access": "Проверка доступа временно недоступна. Обратитесь к администратору.",
    "alert.panel_unavailable": "Не удалось проверить доступ: сервис временно недоступен.",
    "alert.panel_not_found": (
        "Не нашёл доступ, привязанный к вашему Telegram-аккаунту. "
        "Если доступ должен быть — обратитесь к администратору."
    ),
    "alert.private_settings": "Настройки доступны в личном чате.",
    "alert.private_language": "Язык можно изменить в личном чате.",
    "alert.language_saved": "Язык сохранён.",
    "alert.language_failed": "Не удалось сохранить язык.",
    "alert.language_unavailable": "Этот язык пока недоступен.",

    "reminder.expired_title": "🔴 ПОДПИСКА JOPES VPN ЗАКОНЧИЛАСЬ",
    "reminder.expired_line_1": "Доступ к VPN остановлен.",
    "reminder.expired_line_2": "Продление оформляет поддержка или администратор.",
    "reminder.today_title": "⏰ СЕГОДНЯ ПОСЛЕДНИЙ ДЕНЬ ПОДПИСКИ",
    "reminder.today_line_1": "Доступ действует до конца дня.",
    "reminder.today_line_2": "Продлите подписку, чтобы не остаться без VPN.",
    "reminder.tomorrow_title": "⏳ ПОДПИСКА ЗАКАНЧИВАЕТСЯ ЗАВТРА",
    "reminder.tomorrow_line_1": "Остался 1 день.",
    "reminder.tomorrow_line_2": "Продлите подписку заранее.",
    "reminder.days_title": "⏳ ПОДПИСКА ЗАКАНЧИВАЕТСЯ ЧЕРЕЗ {days} ДНЯ",
    "reminder.days_line_1": "Осталось {days} дня.",
    "reminder.days_line_2": "Продлите подписку заранее.",
    "reminder.button_subscription": "Моя подписка",
    "reminder.button_support": "Поддержка",
}
