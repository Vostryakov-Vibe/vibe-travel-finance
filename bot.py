import os
import telebot
from telebot import types
from dotenv import load_dotenv
from colorama import Fore, init
import re
import sqlite3
import time

# 1. Инициализация окружения (берем ключи из .env)
load_dotenv()
init(autoreset=True)

# Импорт твоей логики из currency_app.py
from currency_app import TravelMoneyManager

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
# На сервере в Европе интернет свободный, работаем напрямую без хаков
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print(f"{Fore.RED}[ERROR] Токен бота не найден в .env!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = TravelMoneyManager()
# Набор часто используемых валют для конвертации
WORLD_CURRENCIES = [
    "RUB",
    "USD",
    "EUR",
    "CNY",
    "TRY",
    "AED",
    "GBP",
    "JPY",
    "CHF",
]

# Справочник валют для выбора (частые + популярные). Можно расширять.
CURRENCY_REFERENCE = [
    "RUB","USD","EUR","CNY","TRY","AED","GBP","JPY","CHF","KZT","UZS","GEL","AMD","AZN","BYN","UAH",
    "PLN","CZK","HUF","SEK","NOK","DKK","ISK","CAD","AUD","NZD","SGD","HKD","KRW","THB","VND","MYR","IDR","PHP","INR",
    "ILS","SAR","QAR","KWD","BHD","OMR","ZAR","EGP","MAD","TND","MXN","BRL","ARS","CLP","COP","PEN",
]

CURRENCY_PAGE_SIZE = 12


def _currency_list_keyboard(category, page=0):
    currs = sorted(set(CURRENCY_REFERENCE))
    total_pages = max(1, (len(currs) + CURRENCY_PAGE_SIZE - 1) // CURRENCY_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * CURRENCY_PAGE_SIZE
    end = start + CURRENCY_PAGE_SIZE
    slice_currs = currs[start:end]

    kb = types.InlineKeyboardMarkup(row_width=3)
    for code in slice_currs:
        kb.add(types.InlineKeyboardButton(code, callback_data=f"exp_curr_PICK:{category}:{code}"))

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅", callback_data=f"exp_other_list:{category}:{page-1}"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton("➡", callback_data=f"exp_other_list:{category}:{page+1}"))
    if nav:
        kb.row(*nav)

    kb.add(types.InlineKeyboardButton("⬅ Главное меню", callback_data="back_main"))
    return kb


def main_keyboard():
    """Главное меню с Inline-кнопками"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🌍 Новое путешествие", callback_data="trip_new"),
        types.InlineKeyboardButton("🛍️ Записать расход", callback_data="exp_new"),
        types.InlineKeyboardButton("💰 Мой баланс", callback_data="wallet_view"),
        types.InlineKeyboardButton("🔁 Конвертер/Обмен", callback_data="conv_start"),
        types.InlineKeyboardButton("📄 PDF отчёт", callback_data="report_pdf"),
    )
    return markup


def category_keyboard():
    """Меню выбора тематики расходов"""
    markup = types.InlineKeyboardMarkup(row_width=2)
    categories = ["🍴 Еда", "🚗 Транспорт", "🎉 Развлечения", "🛍️ Шопинг", "🎁 Прочее"]
    btns = [types.InlineKeyboardButton(cat, callback_data=f"cat_{cat}") for cat in categories]
    markup.add(*btns)
    return markup


@bot.message_handler(commands=["start"])
def welcome(message):
    """Приветственное сообщение и лог в консоль"""
    welcome_text = (
        f"🌟 *Приветствую, {message.from_user.first_name}!* \n\n"
        "Я твой цифровой кошелек для путешествий. Помогу отследить траты "
        "в любой валюте и не потерять ни одного цента. 🌍✈️"
    )
    print(f"{Fore.CYAN}[BOT] Запуск сессии для пользователя {message.chat.id}")
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown", reply_markup=main_keyboard())


@bot.callback_query_handler(func=lambda call: True)
def handle_calls(call):
    """Обработка всех нажатий в боте"""
    if call.data == "trip_new":
        # Показ списка поездок с возможностью создать новую
        user_id = call.message.chat.id
        trips = app.get_user_trips(user_id)

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(types.InlineKeyboardButton("➕ Новая поездка", callback_data="trip_new_create"))

        for t in trips:
            status = t["status"]
            title = t["name"] or f"Поездка #{t['id']}"
            label = f"{title}: {t['from_country']} → {t['to_country']} (статус: {status})"
            keyboard.add(types.InlineKeyboardButton(label, callback_data=f"trip_sel:{t['id']}"))

        keyboard.add(types.InlineKeyboardButton("⬅ Главное меню", callback_data="back_main"))

        bot.send_message(
            user_id,
            "Выберите поездку из списка или создайте новую:",
            reply_markup=keyboard,
        )

    elif call.data == "trip_new_create":
        msg = bot.send_message(
            call.message.chat.id,
            "Введите маршрут в формате: *Страна Отправления - Страна Назначения*",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, process_trip_route)

    elif call.data.startswith("trip_sel:"):
        user_id = call.message.chat.id
        trip_id = int(call.data.split(":", 1)[1])
        trips = app.get_user_trips(user_id)
        trip = next((t for t in trips if t["id"] == trip_id), None)
        if not trip:
            bot.send_message(user_id, "❌ Поездка не найдена.")
            return

        title = trip["name"] or f"Поездка #{trip['id']}"

        # Считаем «баланс поездки» по её бюджету и расходам в валюте бюджета
        trip_expenses = app.get_trip_expense_summary(user_id, trip_id)
        trip_curr_budget = trip["budget_currency"] or trip["currency"]
        budget = float(trip["budget_amount"] or 0.0)
        spent_in_trip = 0.0
        for category, curr, total in trip_expenses:
            if curr == trip_curr_budget:
                spent_in_trip += total
            else:
                rate = app.get_official_rate(curr, trip_curr_budget)
                if rate is not None:
                    spent_in_trip += total * rate
        remaining = budget - spent_in_trip if budget else None

        lines = [
            f"Название поездки: {title}",
            f"Маршрут: {trip['from_country']} → {trip['to_country']} ({trip['currency']})",
            f"Статус: {trip['status']}",
        ]
        if budget:
            lines.append(f"Бюджет поездки: {round(budget, 2)} {trip_curr_budget}")
            lines.append(f"Потрачено (в валюте бюджета): {round(spent_in_trip, 2)} {trip_curr_budget}")
            lines.append(f"Остаток бюджета: {round(remaining, 2)} {trip_curr_budget}")

        text = "\n".join(lines)
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton("Сделать активной", callback_data=f"trip_status:{trip_id}:active"),
            types.InlineKeyboardButton("Завершена", callback_data=f"trip_status:{trip_id}:finished"),
        )
        keyboard.add(
            types.InlineKeyboardButton("Запланирована", callback_data=f"trip_status:{trip_id}:planned"),
            types.InlineKeyboardButton("PDF по поездке", callback_data=f"trip_report:{trip_id}"),
        )
        keyboard.add(types.InlineKeyboardButton("📋 Расходы по поездке", callback_data=f"trip_expenses:{trip_id}"))
        keyboard.add(types.InlineKeyboardButton("🗑 Удалить поездку", callback_data=f"trip_del:{trip_id}"))
        keyboard.add(types.InlineKeyboardButton("⬅ Главное меню", callback_data="back_main"))
        bot.send_message(user_id, text, reply_markup=keyboard)

    elif call.data == "exp_new":
        bot.edit_message_text(
            "Выберите тематику расхода:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=category_keyboard(),
        )

    elif call.data.startswith("cat_"):
        # Выбор категории -> далее выберем валюту через inline-кнопки
        cat = call.data.split("_", 1)[1]
        user_id = call.message.chat.id
        active_trip = app.get_active_trip(user_id)
        trip_curr = active_trip["currency"] if active_trip else None

        keyboard = types.InlineKeyboardMarkup(row_width=2)
        buttons = [
            types.InlineKeyboardButton("RUB", callback_data=f"exp_curr_RUB:{cat}"),
        ]
        if trip_curr and trip_curr != "RUB":
            buttons.append(types.InlineKeyboardButton(trip_curr, callback_data=f"exp_curr_TRIP:{cat}:{trip_curr}"))
        buttons.append(types.InlineKeyboardButton("Другая валюта", callback_data=f"exp_other_list:{cat}:0"))
        keyboard.add(*buttons)
        keyboard.add(types.InlineKeyboardButton("⬅ Главное меню", callback_data="back_main"))

        bot.send_message(
            user_id,
            f"Категория: *{cat}*.\nВыберите валюту расхода:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    elif call.data == "wallet_view":
        user_id = call.message.chat.id
        active_trip = app.get_active_trip(user_id)
        if not active_trip:
            bot.send_message(
                user_id,
                "ℹ️ У вас нет *активной* поездки.\n"
                "Выберите поездку и сделайте её активной — тогда я покажу баланс по ней.",
                parse_mode="Markdown",
                reply_markup=main_keyboard(),
            )
            return

        trip_id = active_trip["id"]
        title = active_trip.get("name") or f"Поездка #{trip_id}"
        trip_curr = active_trip["currency"]

        budget_curr, budget, spent, remaining, ratio = app.get_active_trip_budget_status(user_id)
        trip_expenses = app.get_trip_expense_summary(user_id, trip_id)

        parts = []
        parts.append(f"🌍 *Активная поездка*: {title}")
        parts.append(f"Маршрут: {active_trip['from_country']} → {active_trip['to_country']} ({trip_curr})")

        if budget_curr and budget is not None:
            parts.append("")
            parts.append("💼 *Бюджет и остаток:*")
            parts.append(f"• Бюджет: {round(budget, 2)} {budget_curr}")
            parts.append(f"• Потрачено: {round(spent, 2)} {budget_curr}")
            parts.append(f"• Остаток: {round(remaining, 2)} {budget_curr}")

        if trip_expenses:
            parts.append("")
            parts.append("📊 *Расходы по категориям (эта поездка):*")
            for category, curr, total in trip_expenses:
                parts.append(f"• {category}: {round(total, 2)} {curr}")
        else:
            parts.append("")
            parts.append("📊 *Расходов по этой поездке пока нет.*")

        text = "\n".join(parts)
        bot.send_message(user_id, text, parse_mode="Markdown", reply_markup=main_keyboard())

    elif call.data == "conv_start":
        user_id = call.message.chat.id
        balances = app.get_user_balances(user_id)
        trip_curr = app.get_last_trip_currency(user_id)

        currs = set(WORLD_CURRENCIES)
        currs.update({c for c, _ in balances})
        if trip_curr:
            currs.add(trip_curr)
        if not currs:
            bot.send_message(user_id, "❌ Нет доступных валют для обмена.")
            return

        keyboard = types.InlineKeyboardMarkup(row_width=2)
        for c in sorted(currs):
            keyboard.add(types.InlineKeyboardButton(c, callback_data=f"conv_from:{c}"))
        keyboard.add(types.InlineKeyboardButton("⬅ Главное меню", callback_data="back_main"))

        bot.send_message(
            user_id,
            "Выберите валюту, из которой будем конвертировать:",
            reply_markup=keyboard,
        )

    elif call.data.startswith("exp_curr_"):
        # Обработка выбора валюты для расхода
        parts = call.data.split(":")
        kind = parts[0]     # exp_curr_RUB / exp_curr_TRIP / exp_curr_OTHER
        category = parts[1] if len(parts) > 1 else ""
        trip_curr = parts[2] if len(parts) > 2 else None

        if kind.startswith("exp_curr_RUB"):
            currency = "RUB"
            msg = bot.send_message(
                call.message.chat.id,
                f"Категория: *{category}*.\nВведите сумму расхода в {currency}:",
                parse_mode="Markdown",
            )
            bot.register_next_step_handler(msg, lambda m, c=category, curr=currency: save_expense_fixed(m, c, curr))
        elif kind.startswith("exp_curr_TRIP") and trip_curr:
            currency = trip_curr
            msg = bot.send_message(
                call.message.chat.id,
                f"Категория: *{category}*.\nВведите сумму расхода в {currency}:",
                parse_mode="Markdown",
            )
            bot.register_next_step_handler(msg, lambda m, c=category, curr=currency: save_expense_fixed(m, c, curr))
        else:  # OTHER (оставляем совместимость со старыми callback)
            bot.send_message(
                call.message.chat.id,
                f"Категория: *{category}*.\nВыберите валюту из списка:",
                parse_mode="Markdown",
                reply_markup=_currency_list_keyboard(category, page=0),
            )

    elif call.data.startswith("exp_other_list:"):
        _, category, page_str = call.data.split(":", 2)
        page = int(page_str)
        bot.send_message(
            call.message.chat.id,
            f"Категория: *{category}*.\nВыберите валюту расхода:",
            parse_mode="Markdown",
            reply_markup=_currency_list_keyboard(category, page=page),
        )

    elif call.data.startswith("exp_curr_PICK:"):
        _, category, currency = call.data.split(":", 2)
        msg = bot.send_message(
            call.message.chat.id,
            f"Категория: *{category}*.\nВведите сумму расхода в {currency}:",
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(msg, lambda m, c=category, curr=currency: save_expense_fixed(m, c, curr))

    elif call.data.startswith("conv_from:"):
        user_id = call.message.chat.id
        from_curr = call.data.split(":", 1)[1]

        balances = app.get_user_balances(user_id)
        currs = set(WORLD_CURRENCIES)
        currs.update({c for c, _ in balances})
        trip_curr = app.get_last_trip_currency(user_id)
        if trip_curr:
            currs.add(trip_curr)

        to_currs = [c for c in sorted(currs) if c != from_curr]
        if not to_currs:
            bot.send_message(user_id, "❌ Нет доступных целевых валют для обмена.")
            return

        keyboard = types.InlineKeyboardMarkup(row_width=2)
        for c in to_currs:
            keyboard.add(types.InlineKeyboardButton(c, callback_data=f"conv_to:{from_curr}:{c}"))
        keyboard.add(types.InlineKeyboardButton("⬅ Главное меню", callback_data="back_main"))

        bot.send_message(
            user_id,
            f"Исходная валюта: *{from_curr}*.\nВыберите валюту, в которую будем конвертировать:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    elif call.data.startswith("conv_to:"):
        _, from_curr, to_curr = call.data.split(":", 2)
        # Пробуем получить текущий биржевой курс для информации
        rate = app.get_official_rate(from_curr, to_curr)
        if rate is not None:
            rate_info = f"Текущий курс: 1 {from_curr} ≈ {rate:.4f} {to_curr}\n"
        else:
            rate_info = "Текущий курс не удалось получить по API.\n"

        text = (
            f"Конвертация: *{from_curr} → {to_curr}*.\n"
            f"{rate_info}"
            "Введите сумму и, при необходимости, свой курс.\n"
            "Примеры:\n"
            "- 100\n"
            "- 100 по 90,5"
        )
        msg = bot.send_message(
            call.message.chat.id,
            text,
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(
            msg, lambda m, f=from_curr, t=to_curr: convert_amount_wizard(m, f, t)
        )

    elif call.data == "report_pdf":
        user_id = call.message.chat.id
        file_path = f"report_{user_id}.pdf"
        app.generate_report_pdf(user_id, file_path)
        with open(file_path, "rb") as f:
            bot.send_document(user_id, f, caption="📄 Отчёт по балансу и тратам")

    elif call.data == "back_main":
        bot.send_message(call.message.chat.id, "Главное меню.", reply_markup=main_keyboard())

    elif call.data.startswith("trip_status:"):
        user_id = call.message.chat.id
        _, trip_id_str, status = call.data.split(":", 2)
        trip_id = int(trip_id_str)
        try:
            app.set_trip_status(user_id, trip_id, status)
            bot.answer_callback_query(call.id, "Статус поездки обновлён.")
        except Exception as e:
            print(f"{Fore.RED}[BOT ERROR] Не удалось обновить статус поездки: {e}")
            bot.answer_callback_query(call.id, "Ошибка при обновлении статуса.")

    elif call.data.startswith("trip_report:"):
        user_id = call.message.chat.id
        trip_id = int(call.data.split(":", 1)[1])
        file_path = f"report_trip_{user_id}_{trip_id}.pdf"
        app.generate_trip_report_pdf(user_id, trip_id, file_path)
        with open(file_path, "rb") as f:
            bot.send_document(user_id, f, caption="📄 Отчёт по выбранной поездке")

    elif call.data.startswith("trip_expenses:"):
        user_id = call.message.chat.id
        trip_id = int(call.data.split(":", 1)[1])
        expenses = []
        try:
            with sqlite3.connect(app.db_name) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT date, category, amount, currency
                    FROM expenses
                    WHERE user_id = ? AND trip_id = ?
                    ORDER BY datetime(date) DESC, id DESC
                    LIMIT 30
                    ''',
                    (user_id, trip_id),
                )
                expenses = cursor.fetchall()
        except Exception as e:
            print(f"{Fore.RED}[BOT ERROR] Не удалось получить расходы по поездке: {e}")

        if not expenses:
            bot.send_message(user_id, "ℹ️ Для этой поездки пока нет расходов.")
            return

        header = f"{'Дата':16} | {'Категория':12} | {'Сумма':10} | Валюта"
        lines = [header, "-" * len(header)]
        for date_str, category, amount, currency in expenses:
            cat_short = (category or "")[:12]
            line = f"{date_str[:16]:16} | {cat_short:12} | {round(amount, 2):10} | {currency}"
            lines.append(line)

        table_text = "Расходы по поездке (последние 30):\n```text\n" + "\n".join(lines) + "\n```"
        bot.send_message(user_id, table_text, parse_mode="Markdown")

    elif call.data.startswith("trip_del:"):
        user_id = call.message.chat.id
        trip_id = int(call.data.split(":", 1)[1])
        keyboard = types.InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            types.InlineKeyboardButton("✅ Да, удалить", callback_data=f"trip_del_yes:{trip_id}"),
            types.InlineKeyboardButton("❌ Нет", callback_data="back_main"),
        )
        bot.send_message(
            user_id,
            "Вы уверены, что хотите удалить эту поездку? Связанные расходы останутся, но будут отвязаны от неё.",
            reply_markup=keyboard,
        )

    elif call.data.startswith("trip_del_yes:"):
        user_id = call.message.chat.id
        trip_id = int(call.data.split(":", 1)[1])
        try:
            app.delete_trip(user_id, trip_id)
            bot.send_message(user_id, "✅ Поездка удалена.", reply_markup=main_keyboard())
        except Exception as e:
            print(f"{Fore.RED}[BOT ERROR] Не удалось удалить поездку: {e}")
            bot.send_message(user_id, "❌ Ошибка при удалении поездки.", reply_markup=main_keyboard())


def _parse_amount_and_currency(text):
    """
    Разбор суммы бюджета.

    Поддерживаемые примеры:
    - 10000
    - 10000 RUB
    - 10000RUB
    - 10 000,50 usd
    """
    raw = (text or "").strip()
    raw = raw.replace("\u00A0", " ")
    raw = re.sub(r"\s+", " ", raw)

    match = re.match(r"^\s*([\d\s]+(?:[.,]\d+)?)\s*([A-Za-z]{3})?\s*$", raw, re.IGNORECASE)
    if not match:
        return None, None

    amount_str, curr = match.groups()
    amount_str = amount_str.replace(" ", "").replace(",", ".")
    try:
        amount = float(amount_str)
    except ValueError:
        return None, None

    currency = curr.upper() if curr else None
    return amount, currency


def process_trip_route(message):
    """Шаг 1: ввод маршрута"""
    try:
        parts = message.text.split("-")
        if len(parts) != 2:
            raise ValueError("Неверный формат маршрута")

        start = parts[0].strip()
        end = parts[1].strip()
        if not start or not end:
            raise ValueError("Пустое название страны")

        from_curr = app.get_currency_by_country(start)
        to_curr = app.get_currency_by_country(end)

        msg = bot.send_message(
            message.chat.id,
            (
                "✅ Маршрут принят!\n"
                f"📍 Откуда: {start} (*{from_curr}*)\n"
                f"📍 Куда: {end} (*{to_curr}*)\n\n"
                "Теперь введите *название поездки*.\n"
                "Пример: *Отпуск в Китае весна 2026*"
            ),
            parse_mode="Markdown",
        )
        bot.register_next_step_handler(
            msg, lambda m: process_trip_name(m, start, end, from_curr, to_curr)
        )
        print(f"{Fore.YELLOW}[LOG] Маршрут пользователя {message.chat.id}: {start} -> {end} ({from_curr}->{to_curr})")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Ошибка формата. Используйте пример: Россия - Турция")
    except Exception as e:
        print(f"{Fore.RED}[BOT ERROR] Ошибка при создании поездки: {e}")
        bot.send_message(message.chat.id, "❌ Произошла внутренняя ошибка. Попробуйте еще раз позже.")


def process_trip_name(message, start, end, from_curr, to_curr):
    """Шаг 2: ввод названия поездки"""
    title = (message.text or "").strip()
    if not title:
        title = f"{start} → {end}"

    msg = bot.send_message(
        message.chat.id,
        (
            f"Название поездки: *{title}*.\n\n"
            "Теперь введите *бюджет поездки*.\n"
            "Пример: *10000* или *10000 RUB*"
        ),
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(
        msg, lambda m, t=title: process_trip_budget(m, start, end, from_curr, to_curr, t)
    )


def process_trip_budget(message, start, end, from_curr, to_curr, title):
    """Шаг 3: ввод бюджета и вывод курса/конвертации"""
    amount, budget_curr = _parse_amount_and_currency(message.text)
    if amount is None:
        bot.send_message(
            message.chat.id,
            "❌ Не удалось распознать сумму. Пример: *10000* или *10000 RUB*",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        return

    # Если валюту не указали — считаем, что бюджет в валюте страны отправления
    budget_curr = budget_curr or from_curr

    # Пытаемся посчитать «сколько будет» в валюте каждой страны
    budget_in_from = None
    budget_in_to = None
    rate_from_to = None

    if budget_curr == from_curr:
        budget_in_from = amount
        if from_curr == to_curr:
            budget_in_to = amount
            rate_from_to = 1.0
        else:
            rate_from_to = app.get_official_rate(from_curr, to_curr)
            budget_in_to = round(amount * rate_from_to, 2) if rate_from_to is not None else None
    elif budget_curr == to_curr:
        budget_in_to = amount
        if from_curr == to_curr:
            budget_in_from = amount
            rate_from_to = 1.0
        else:
            rate_to_from = app.get_official_rate(to_curr, from_curr)
            budget_in_from = round(amount * rate_to_from, 2) if rate_to_from is not None else None
            rate_from_to = (1.0 / rate_to_from) if rate_to_from else None
    else:
        # Бюджет введён в третьей валюте — считаем отдельно в обе
        rate_to_from_curr = app.get_official_rate(budget_curr, from_curr) if budget_curr != from_curr else 1.0
        rate_to_to_curr = app.get_official_rate(budget_curr, to_curr) if budget_curr != to_curr else 1.0
        budget_in_from = round(amount * rate_to_from_curr, 2) if rate_to_from_curr is not None else None
        budget_in_to = round(amount * rate_to_to_curr, 2) if rate_to_to_curr is not None else None
        rate_from_to = app.get_official_rate(from_curr, to_curr) if from_curr != to_curr else 1.0

    # Сохраняем поездку в БД вместе с бюджетом и названием
    app.add_trip(
        message.chat.id,
        start,
        end,
        budget_amount=amount,
        budget_currency=budget_curr,
        name=title,
    )

    lines = [
        "🌍 *Поездка создана!*",
        f"📍 Откуда: {start} (*{from_curr}*)",
        f"📍 Куда: {end} (*{to_curr}*)",
        f"💼 Бюджет: *{amount} {budget_curr}*",
    ]

    if rate_from_to is not None and from_curr != to_curr:
        lines.append(f"📈 Курс: 1 {from_curr} = *{rate_from_to:.4f} {to_curr}*")

    if budget_in_from is not None:
        lines.append(f"💵 В валюте страны отправления: *{budget_in_from} {from_curr}*")
    else:
        lines.append(f"💵 В валюте страны отправления: *н/д* (нет курса)")

    if budget_in_to is not None:
        lines.append(f"💶 В валюте страны назначения: *{budget_in_to} {to_curr}*")
    else:
        lines.append(f"💶 В валюте страны назначения: *н/д* (нет курса)")

    bot.send_message(
        message.chat.id,
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


def save_expense(message, category):
    """Сохранение расхода с указанием валюты и обновлением баланса"""
    amount, currency = _parse_amount_and_currency(message.text)
    if amount is None:
        bot.send_message(
            message.chat.id,
            "❌ Не удалось распознать сумму. Пример: *1000 RUB* или *50 USD*",
            parse_mode="Markdown",
        )
        return

    # Если валюту не указали — считаем, что тратим в RUB
    currency = currency or "RUB"

    app.add_expense(message.chat.id, amount, category, currency)
    bot.send_message(
        message.chat.id,
        f"✅ Сохранено в категории {category}: {amount} {currency}",
        reply_markup=main_keyboard(),
    )
    _maybe_warn_budget_drop(message.chat.id)


def save_expense_fixed(message, category, currency):
    """Сохранение расхода при заранее выбранной валюте (через inline-кнопки)"""
    amount, _ = _parse_amount_and_currency(message.text)
    if amount is None:
        bot.send_message(
            message.chat.id,
            "❌ Не удалось распознать сумму. Пример: *1000*",
            parse_mode="Markdown",
        )
        return

    app.add_expense(message.chat.id, amount, category, currency)
    bot.send_message(
        message.chat.id,
        f"✅ Сохранено в категории {category}: {amount} {currency}",
        reply_markup=main_keyboard(),
    )
    _maybe_warn_budget_drop(message.chat.id)


@bot.message_handler(func=lambda m: isinstance(m.text, str) and "t=" in m.text and "s=" in m.text)
def handle_qr_data(message):
    """Прием данных из QR-сканера (текстовая строка чека)"""
    # Парсинг суммы из строки s=12345 (в копейках/центах)
    match = re.search(r"s=(\d+)", message.text)
    if match:
        amount = float(match.group(1)) / 100
        app.add_expense(message.chat.id, amount, "🛍️ Шопинг", "RUB", message.text)
        bot.send_message(
            message.chat.id,
            f"📸 Чек обработан!\nСумма: {amount} RUB\nКатегория: Шопинг",
            reply_markup=main_keyboard(),
        )
        _maybe_warn_budget_drop(message.chat.id)
    else:
        bot.send_message(
            message.chat.id,
            "❌ Не удалось определить сумму в переданном тексте чека.",
            reply_markup=main_keyboard(),
        )


def convert_amount(message):
    """Конвертация средств в кошельке, например '100 USD в EUR' или '100 USD в EUR по 0.92'"""
    text = (message.text or "").strip()
    pattern = r"([\d.,]+)\s*([A-Za-z]{3})\s*(?:в|->)\s*([A-Za-z]{3})(?:\s*(?:по|курс)\s*([\d.,]+))?"
    match = re.match(pattern, text, re.IGNORECASE)

    if not match:
        bot.send_message(
            message.chat.id,
            "❌ Неверный формат. Используйте пример: 100 USD в EUR",
            reply_markup=main_keyboard(),
        )
        return

    amount_str, from_curr, to_curr, manual_rate_str = match.groups()
    try:
        amount = float(amount_str.replace(",", "."))
    except ValueError:
        bot.send_message(message.chat.id, "❌ Сумма должна быть числом.", reply_markup=main_keyboard())
        return

    from_curr = from_curr.upper()
    to_curr = to_curr.upper()

    # Если пользователь указал курс вручную — используем его
    rate = None
    if manual_rate_str:
        try:
            rate = float(manual_rate_str.replace(",", "."))
        except ValueError:
            bot.send_message(
                message.chat.id,
                "❌ Курс должен быть числом. Пример: 100 USD в EUR по 0.92",
                reply_markup=main_keyboard(),
            )
            return
    else:
        rate = app.get_official_rate(from_curr, to_curr)

    if rate is None:
        bot.send_message(
            message.chat.id,
            "❌ Не удалось получить курс для выбранной пары валют.\n"
            "Вы можете указать его вручную, например: *100 USD в RUB по 90,5*",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        return

    # Проверяем и применяем конвертацию к балансу
    success = app.convert_balance(message.chat.id, amount, from_curr, to_curr, rate)
    if not success:
        bot.send_message(
            message.chat.id,
            f"❌ Недостаточно средств. На балансе недостаточно {from_curr} для операции.",
            reply_markup=main_keyboard(),
        )
        return

    converted = round(amount * rate, 2)
    bot.send_message(
        message.chat.id,
        f"🔁 Конвертация выполнена:\n{amount} {from_curr} → *{converted} {to_curr}* (курс {rate:.4f})",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )
    _maybe_warn_budget_drop(message.chat.id)


def convert_amount_wizard(message, from_curr, to_curr):
    """Конвертация в режиме мастера, когда валюты уже выбраны."""
    text = (message.text or "").strip()
    pattern = r"([\d.,]+)(?:\s*(?:по|курс)\s*([\d.,]+))?"
    match = re.match(pattern, text, re.IGNORECASE)
    if not match:
        bot.send_message(
            message.chat.id,
            "❌ Неверный формат. Примеры: 100 или 100 по 90,5",
            reply_markup=main_keyboard(),
        )
        return

    amount_str, manual_rate_str = match.groups()
    try:
        amount = float(amount_str.replace(",", "."))
    except ValueError:
        bot.send_message(message.chat.id, "❌ Сумма должна быть числом.", reply_markup=main_keyboard())
        return

    # Курс
    if manual_rate_str:
        try:
            rate = float(manual_rate_str.replace(",", "."))
        except ValueError:
            bot.send_message(
                message.chat.id,
                "❌ Курс должен быть числом. Пример: 100 по 90,5",
                reply_markup=main_keyboard(),
            )
            return
    else:
        rate = app.get_official_rate(from_curr, to_curr)

    if rate is None:
        bot.send_message(
            message.chat.id,
            "❌ Не удалось получить курс для выбранной пары валют.",
            reply_markup=main_keyboard(),
        )
        return

    success = app.convert_balance(message.chat.id, amount, from_curr, to_curr, rate)
    if not success:
        bot.send_message(
            message.chat.id,
            f"❌ Недостаточно средств. На балансе недостаточно {from_curr} для операции.",
            reply_markup=main_keyboard(),
        )
        return

    converted = round(amount * rate, 2)
    bot.send_message(
        message.chat.id,
        f"🔁 Конвертация выполнена:\n{amount} {from_curr} → *{converted} {to_curr}* (курс {rate:.4f})",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )
    _maybe_warn_budget_drop(message.chat.id)


def _maybe_warn_budget_drop(user_id):
    """Предупреждение, если общий баланс в валюте поездки упал ниже 50% бюджета."""
    budget_curr, budget, spent, remaining, ratio = app.get_active_trip_budget_status(user_id)
    if not budget_curr or budget is None or ratio is None:
        return
    if ratio <= 0.5:
        bot.send_message(
            user_id,
            (
                f"⚠️ Внимание! Остаток бюджета по активной поездке снизился ниже 50%.\n"
                f"Остаток: {round(remaining, 2)} {budget_curr} из {round(budget, 2)} {budget_curr}."
            ),
        )


if __name__ == "__main__":
    print(f"{Fore.GREEN}{'=' * 40}")
    print(f"{Fore.GREEN}[STATUS] Бот успешно запущен на сервере!")
    print(f"{Fore.GREEN}{'=' * 40}")

    reconnect_attempt = 0
    while True:
        try:
            # На сервере используем стандартный метод
            bot.infinity_polling(timeout=120, long_polling_timeout=60, skip_pending=True)
            reconnect_attempt = 0
        except Exception as e:
            reconnect_attempt += 1
            # Экспоненциальная пауза с ограничением, чтобы не "долбить" Telegram при проблемах сети
            sleep_s = min(60, 5 * (2 ** (reconnect_attempt - 1)))
            print(f"{Fore.RED}[RECONNECT] Сетевая заминка: {e}. Перезапуск через {sleep_s} сек...")
            time.sleep(sleep_s)