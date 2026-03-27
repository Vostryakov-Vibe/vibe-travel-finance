import sqlite3
import requests
import os
from datetime import datetime
from dotenv import load_dotenv
from colorama import Fore, init
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Инициализация настроек
init(autoreset=True)
load_dotenv()


class TravelMoneyManager:
    def __init__(self):
        self.db_name = os.getenv("DB_NAME", "travel_and_money.db")
        self.api_key = os.getenv("EXCHANGE_RATE_KEY")
        # Базовый URL для сервиса курсов валют.
        # Поддерживаются варианты:
        # - https://api.exchangerate.host/convert (from/to)
        # - http(s)://api.exchangerate.host/live (source/currencies)
        self.base_rate_url = os.getenv("EXCHANGE_RATE_URL", "https://api.exchangerate.host/convert")

        # Регистрация шрифта с поддержкой кириллицы для PDF
        self.pdf_font_name = "Helvetica"
        try:
            fonts_dir = os.getenv("WINDOWS_FONTS_DIR", r"C:\Windows\Fonts")
            arial_path = os.path.join(fonts_dir, "arial.ttf")
            if os.path.exists(arial_path):
                pdfmetrics.registerFont(TTFont("ArialCYR", arial_path))
                self.pdf_font_name = "ArialCYR"
        except Exception as e:
            print(f"{Fore.YELLOW}[PDF] Не удалось зарегистрировать кириллический шрифт, используется Helvetica: {e}")
        self.init_db()

    def init_db(self):
        """Создание таблиц БД: поездки, баланс, расходы, лог движений"""
        try:
            with sqlite3.connect(self.db_name) as conn:
                cursor = conn.cursor()
                # Таблица поездок: храним название, страны, валюту и бюджет
                cursor.execute(
                    '''CREATE TABLE IF NOT EXISTS trips (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        name TEXT,
                        from_country TEXT,
                        to_country TEXT,
                        currency TEXT,
                        budget_amount REAL,
                        budget_currency TEXT,
                        status TEXT DEFAULT 'planned',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )'''
                )
                # Таблица баланса: мультивалютный кошелек пользователя
                cursor.execute(
                    '''CREATE TABLE IF NOT EXISTS balance (
                        user_id INTEGER,
                        currency TEXT,
                        amount REAL DEFAULT 0,
                        PRIMARY KEY (user_id, currency)
                    )'''
                )
                # Таблица расходов: детальная история с категориями и данными QR
                cursor.execute(
                    '''CREATE TABLE IF NOT EXISTS expenses (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        trip_id INTEGER,
                        amount REAL,
                        category TEXT,
                        currency TEXT,
                        qr_raw TEXT,
                        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )'''
                )
                # Таблица движений средств для полного аудита
                cursor.execute(
                    '''CREATE TABLE IF NOT EXISTS movements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER,
                        trip_id INTEGER,
                        amount REAL,
                        currency TEXT,
                        kind TEXT,
                        description TEXT,
                        related_currency TEXT,
                        rate REAL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )'''
                )
                conn.commit()

                # Мягкая миграция старых БД: если таблица expenses уже существовала без нужных колонок
                cursor.execute("PRAGMA table_info(expenses)")
                existing_exp_cols = {row[1] for row in cursor.fetchall()}
                if "trip_id" not in existing_exp_cols:
                    cursor.execute("ALTER TABLE expenses ADD COLUMN trip_id INTEGER")
                if "qr_raw" not in existing_exp_cols:
                    cursor.execute("ALTER TABLE expenses ADD COLUMN qr_raw TEXT")
                if "date" not in existing_exp_cols:
                    cursor.execute("ALTER TABLE expenses ADD COLUMN date TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                conn.commit()

                # Миграция таблицы movements (если уже существовала без нужных колонок)
                cursor.execute("PRAGMA table_info(movements)")
                existing_mov_cols = {row[1] for row in cursor.fetchall()}
                if "trip_id" not in existing_mov_cols:
                    cursor.execute("ALTER TABLE movements ADD COLUMN trip_id INTEGER")
                if "related_currency" not in existing_mov_cols:
                    cursor.execute("ALTER TABLE movements ADD COLUMN related_currency TEXT")
                if "rate" not in existing_mov_cols:
                    cursor.execute("ALTER TABLE movements ADD COLUMN rate REAL")
                if "created_at" not in existing_mov_cols:
                    cursor.execute("ALTER TABLE movements ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                conn.commit()

                # Мягкая миграция старых БД: если таблица trips уже существовала без нужных колонок
                cursor.execute("PRAGMA table_info(trips)")
                existing_cols = {row[1] for row in cursor.fetchall()}
                if "name" not in existing_cols:
                    cursor.execute("ALTER TABLE trips ADD COLUMN name TEXT")
                if "currency" not in existing_cols:
                    cursor.execute("ALTER TABLE trips ADD COLUMN currency TEXT")
                if "budget_amount" not in existing_cols:
                    cursor.execute("ALTER TABLE trips ADD COLUMN budget_amount REAL")
                if "budget_currency" not in existing_cols:
                    cursor.execute("ALTER TABLE trips ADD COLUMN budget_currency TEXT")
                if "status" not in existing_cols:
                    cursor.execute("ALTER TABLE trips ADD COLUMN status TEXT DEFAULT 'planned'")
                if "created_at" not in existing_cols:
                    cursor.execute("ALTER TABLE trips ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                conn.commit()
            print(f"{Fore.GREEN}[DB] База {self.db_name} успешно инициализирована.")
        except Exception as e:
            print(f"{Fore.RED}[DB ERROR] Ошибка при создании таблиц: {e}")

    def add_trip(self, user_id, from_country, to_country, budget_amount=None, budget_currency=None, name=None):
        """Сохранение информации о поездке в БД и возврат валюты назначения.

        Одновременно инициализируем баланс по бюджету (если указан).
        """
        currency = self.get_currency_by_country(to_country)
        if budget_amount is not None:
            budget_amount = round(float(budget_amount), 2)
        trip_name = name or f"{from_country} → {to_country}"
        trip_id = None
        try:
            with sqlite3.connect(self.db_name) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''INSERT INTO trips (user_id, name, from_country, to_country, currency, budget_amount, budget_currency)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (user_id, trip_name, from_country, to_country, currency, budget_amount, budget_currency),
                )
                trip_id = cursor.lastrowid
                conn.commit()
            if budget_amount is not None and budget_currency:
                print(
                    f"{Fore.CYAN}[DB] Новая поездка: {from_country} -> {to_country} ({currency}), бюджет {budget_amount} {budget_currency}"
                )
                # Инициализируем баланс пользователя на сумму бюджета
                self.change_balance(user_id, budget_currency, budget_amount)
                self.log_movement(
                    user_id=user_id,
                    amount=budget_amount,
                    currency=budget_currency,
                    kind="trip_budget",
                    description=f"Бюджет поездки {from_country} -> {to_country} ({currency})",
                    related_currency=currency,
                    rate=None,
                    trip_id=trip_id,
                )
            else:
                print(f"{Fore.CYAN}[DB] Новая поездка: {from_country} -> {to_country} ({currency})")
        except Exception as e:
            print(f"{Fore.RED}[DB ERROR] Не удалось сохранить поездку: {e}")
        return currency

    def get_official_rate(self, from_curr, to_curr):
        """
        Получение актуального курса через API exchangerate.host.

        Поддерживает 2 формата:
        - /convert: ?from=USD&to=RUB&amount=1&access_key=...
        - /live: ?source=USD&currencies=RUB&format=1&access_key=...
        """
        if not self.api_key:
            print(f"{Fore.RED}[API ERROR] Не задан EXCHANGE_RATE_KEY, курс получить нельзя.")
            return None

        url = (self.base_rate_url or "").strip()
        url_l = url.lower()

        if "convert" in url_l:
            params = {
                "access_key": self.api_key,
                "from": from_curr,
                "to": to_curr,
                "amount": 1,
            }
            parser = "convert"
        else:
            params = {
                "access_key": self.api_key,
                "source": from_curr,
                "currencies": to_curr,
                "format": 1,
            }
            parser = "live"

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            if not data.get("success"):
                print(f"{Fore.RED}[API ERROR] Ошибка ответа API: {data}")
                return None

            if parser == "convert":
                # Обычно курс лежит в info.rate, иногда можно восстановить через result (при amount=1)
                rate = data.get("info", {}).get("rate")
                if rate is None:
                    result = data.get("result")
                    rate = result if isinstance(result, (int, float)) else None
                return float(rate) if rate is not None else None

            quotes = data.get("quotes") or {}
            key = f"{from_curr}{to_curr}"
            rate = quotes.get(key)
            if rate is None:
                print(f"{Fore.RED}[API ERROR] В ответе API нет курса для ключа {key}: {quotes}")
                return None

            return float(rate)
        except Exception as e:
            print(f"{Fore.RED}[API ERROR] Не удалось получить курс {from_curr}->{to_curr}: {e}")
            return None

    def get_currency_by_country(self, country_name):
        """Определение государственной валюты по названию страны (базовый справочник, регистронезависимый)"""
        mapping = {
            "россия": "RUB",
            "рф": "RUB",
            "сша": "USD",
            "usa": "USD",
            "германия": "EUR",
            "франция": "EUR",
            "турция": "TRY",
            "оаэ": "AED",
            "оае": "AED",
            "эмираты": "AED",
            "китай": "CNY",
        }
        key = country_name.strip().lower()
        return mapping.get(key, "USD")

    def add_expense(self, user_id, amount, category, currency, qr_data=None):
        """Запись транзакции в базу данных"""
        amount = round(float(amount), 2)
        # Определяем активную поездку (если есть), чтобы привязать расход
        trip = self.get_active_trip(user_id)
        trip_id = trip["id"] if trip else None

        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO expenses (user_id, trip_id, amount, category, currency, qr_raw) 
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (user_id, trip_id, amount, category, currency, qr_data),
            )
            conn.commit()

        # После записи расхода уменьшаем баланс по соответствующей валюте
        self.change_balance(user_id, currency, -amount)
        self.log_movement(
            user_id=user_id,
            amount=-amount,
            currency=currency,
            kind="expense",
            description=f"Расход: {category}",
            related_currency=None,
            rate=None,
            trip_id=trip_id,
        )

    def change_balance(self, user_id, currency, delta):
        """Изменение баланса пользователя по заданной валюте на delta (может быть отрицательной)."""
        delta = round(float(delta), 2)
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO balance (user_id, currency, amount)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id, currency) DO UPDATE SET amount = amount + excluded.amount''',
                (user_id, currency, delta),
            )
            conn.commit()

    def get_balance(self, user_id, currency):
        """Текущий баланс пользователя по конкретной валюте (может быть 0, если записи нет)."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT amount FROM balance WHERE user_id = ? AND currency = ?',
                (user_id, currency),
            )
            row = cursor.fetchone()
            return float(row[0]) if row else 0.0

    def convert_balance(self, user_id, amount, from_curr, to_curr, rate):
        """Конвертация средств между валютами кошелька с изменением балансов.

        Возвращает True при успешной операции, False — если баланс недостаточен.
        """
        if rate is None:
            return False
        amount = round(float(amount), 2)

        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            # Проверяем текущий баланс
            cursor.execute(
                'SELECT amount FROM balance WHERE user_id = ? AND currency = ?',
                (user_id, from_curr),
            )
            row = cursor.fetchone()
            current = float(row[0]) if row else 0.0

            # Если пользователь пытается конвертировать чуть больше, чем есть (из‑за округления),
            # но разница не больше 1 копейки/цента — считаем, что он хочет перевести весь баланс.
            if amount > current:
                if amount - current <= 0.01:
                    amount = current
                else:
                    return False

            converted = round(amount * rate, 2)

            # Списываем
            cursor.execute(
                '''INSERT INTO balance (user_id, currency, amount)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id, currency) DO UPDATE SET amount = amount + excluded.amount''',
                (user_id, from_curr, -amount),
            )
            # Зачисляем
            cursor.execute(
                '''INSERT INTO balance (user_id, currency, amount)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_id, currency) DO UPDATE SET amount = amount + excluded.amount''',
                (user_id, to_curr, converted),
            )
            conn.commit()

        # Логируем обе части конвертации с привязкой к активной поездке (если есть)
        trip = self.get_active_trip(user_id)
        trip_id = trip["id"] if trip else None
        self.log_movement(
            user_id=user_id,
            amount=-amount,
            currency=from_curr,
            kind="convert_out",
            description=f"Обмен {from_curr} -> {to_curr}",
            related_currency=to_curr,
            rate=rate,
            trip_id=trip_id,
        )
        self.log_movement(
            user_id=user_id,
            amount=converted,
            currency=to_curr,
            kind="convert_in",
            description=f"Обмен {from_curr} -> {to_curr}",
            related_currency=from_curr,
            rate=rate,
            trip_id=trip_id,
        )
        return True

    def get_user_balances(self, user_id):
        """Текущие балансы пользователя по валютам (только ненулевые)."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT currency, amount FROM balance WHERE user_id = ? AND amount != 0',
                (user_id,),
            )
            return cursor.fetchall()

    def get_expense_summary(self, user_id):
        """Суммарные расходы по категориям и валютам."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT category, currency, SUM(amount) AS total
                FROM expenses
                WHERE user_id = ?
                GROUP BY category, currency
                ORDER BY currency, category
                ''',
                (user_id,),
            )
            return cursor.fetchall()

    def get_last_trip_currency(self, user_id):
        """Валюта последней поездки пользователя (по полю created_at)."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT currency
                FROM trips
                WHERE user_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT 1
                ''',
                (user_id,),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def get_user_trips(self, user_id):
        """Список поездок пользователя с основными данными."""
        with sqlite3.connect(self.db_name) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT id, name, from_country, to_country, currency, budget_amount, budget_currency, status, created_at
                FROM trips
                WHERE user_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                ''',
                (user_id,),
            )
            return cursor.fetchall()

    def get_active_trip(self, user_id):
        """Текущая активная поездка пользователя (или None)."""
        with sqlite3.connect(self.db_name) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT *
                FROM trips
                WHERE user_id = ? AND status = 'active'
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT 1
                ''',
                (user_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def set_trip_status(self, user_id, trip_id, status):
        """Изменение статуса поездки. При установке 'active' остальные становятся 'planned'."""
        if status not in ("planned", "active", "finished"):
            raise ValueError("Недопустимый статус поездки")

        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            if status == "active":
                cursor.execute(
                    "UPDATE trips SET status = 'planned' WHERE user_id = ? AND id != ?",
                    (user_id, trip_id),
                )
            cursor.execute(
                "UPDATE trips SET status = ? WHERE user_id = ? AND id = ?",
                (status, user_id, trip_id),
            )
            conn.commit()

    def recompute_balance_for_user(self, user_id):
        """Пересчёт балансов пользователя на основе таблицы movements."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT currency, SUM(amount) as total
                FROM movements
                WHERE user_id = ?
                GROUP BY currency
                ''',
                (user_id,),
            )
            rows = cursor.fetchall()

            # Очищаем старые балансы пользователя
            cursor.execute("DELETE FROM balance WHERE user_id = ?", (user_id,))

            # Вставляем пересчитанные
            for currency, total in rows:
                total = round(float(total or 0.0), 2)
                cursor.execute(
                    "INSERT INTO balance (user_id, currency, amount) VALUES (?, ?, ?)",
                    (user_id, currency, total),
                )
            conn.commit()

    def delete_trip(self, user_id, trip_id):
        """Удаление поездки с каскадным удалением связанных данных."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()

            # Удаляем расходы по этой поездке
            cursor.execute(
                "DELETE FROM expenses WHERE user_id = ? AND trip_id = ?",
                (user_id, trip_id),
            )

            # Удаляем движения, связанные с этой поездкой
            cursor.execute(
                "DELETE FROM movements WHERE user_id = ? AND trip_id = ?",
                (user_id, trip_id),
            )

            # Удаляем саму поездку
            cursor.execute(
                "DELETE FROM trips WHERE user_id = ? AND id = ?",
                (user_id, trip_id),
            )

            # Проверяем, остались ли у пользователя какие‑либо поездки
            cursor.execute(
                "SELECT COUNT(*) FROM trips WHERE user_id = ?",
                (user_id,),
            )
            remaining = cursor.fetchone()[0] or 0

            if remaining == 0:
                # Если поездок больше нет — очищаем все данные пользователя
                cursor.execute("DELETE FROM expenses WHERE user_id = ?", (user_id,))
                cursor.execute("DELETE FROM movements WHERE user_id = ?", (user_id,))
                cursor.execute("DELETE FROM balance WHERE user_id = ?", (user_id,))

            conn.commit()

        # Если какие‑то поездки ещё остались — пересчитываем баланс из движений
        if remaining > 0:
            self.recompute_balance_for_user(user_id)

    def log_movement(self, user_id, amount, currency, kind, description=None, related_currency=None, rate=None, trip_id=None):
        """Запись любого движения средств с датой/временем."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO movements (user_id, trip_id, amount, currency, kind, description, related_currency, rate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, trip_id, amount, currency, kind, description, related_currency, rate),
            )
            conn.commit()

    def get_recent_movements(self, user_id, limit=20):
        """Последние движения средств пользователя."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT amount, currency, kind, description, related_currency, rate, created_at, trip_id
                FROM movements
                WHERE user_id = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT ?
                ''',
                (user_id, limit),
            )
            return cursor.fetchall()

    def get_trip_movements(self, user_id, trip_id):
        """Все движения средств, относящиеся к конкретной поездке."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT amount, currency, kind, description, related_currency, rate, created_at
                FROM movements
                WHERE user_id = ? AND trip_id = ?
                ORDER BY datetime(created_at) ASC, id ASC
                ''',
                (user_id, trip_id),
            )
            return cursor.fetchall()

    def get_trip_balances(self, user_id, trip_id):
        """
        Агрегированные балансы по конкретной поездке.

        Логика:
        - базовый остаток стартует с бюджета поездки (budget_amount в budget_currency),
        - затем применяются все движения по поездке (movements.trip_id = trip_id),
        - запись kind='trip_budget' не учитывается, чтобы не задвоить бюджет, если она есть в movements.

        Валюты с нулевым итоговым остатком также попадают в выборку.
        """
        balances = {}

        with sqlite3.connect(self.db_name) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Бюджет поездки (как стартовый баланс)
            cursor.execute(
                '''
                SELECT budget_amount, budget_currency, currency
                FROM trips
                WHERE user_id = ? AND id = ?
                ''',
                (user_id, trip_id),
            )
            trip = cursor.fetchone()
            if trip and trip["budget_amount"] is not None:
                budget_curr = trip["budget_currency"] or trip["currency"]
                balances[budget_curr] = balances.get(budget_curr, 0.0) + float(trip["budget_amount"] or 0.0)

            # Движения по поездке (кроме trip_budget, чтобы не задвоить бюджет)
            cursor.execute(
                '''
                SELECT currency, amount, kind
                FROM movements
                WHERE user_id = ? AND trip_id = ?
                ''',
                (user_id, trip_id),
            )
            for curr, amount, kind in cursor.fetchall():
                if kind == "trip_budget":
                    continue
                balances[curr] = balances.get(curr, 0.0) + float(amount or 0.0)

        return sorted([(curr, round(total, 2)) for curr, total in balances.items()], key=lambda x: x[0])

    def generate_report_pdf(self, user_id, file_path):
        """Формирование PDF-отчёта по балансам, расходам и конвертациям."""
        balances = self.get_user_balances(user_id)
        expenses = self.get_expense_summary(user_id)
        movements = self.get_recent_movements(user_id, limit=50)

        # Кэш названий поездок для ссылок в движениях
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, name, from_country, to_country FROM trips WHERE user_id = ?",
                (user_id,),
            )
            trip_rows = cursor.fetchall()
        trip_titles = {}
        for trip_id, name, from_c, to_c in trip_rows:
            base_title = name or f"Поездка #{trip_id}"
            trip_titles[trip_id] = f"{base_title} ({from_c} → {to_c})"

        c = canvas.Canvas(file_path, pagesize=A4)
        width, height = A4
        y = height - 40

        c.setFont(self.pdf_font_name, 16)
        c.drawString(40, y, "Отчёт по путешествиям и деньгам")
        y -= 25

        c.setFont(self.pdf_font_name, 10)
        c.drawString(40, y, f"Пользователь: {user_id}")
        y -= 15
        c.drawString(40, y, f"Дата формирования: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        y -= 25

        # Блок балансов
        c.setFont(self.pdf_font_name, 12)
        c.drawString(40, y, "Текущие балансы:")
        y -= 18
        c.setFont(self.pdf_font_name, 10)
        if balances:
            for curr, amount in balances:
                c.drawString(50, y, f"- {curr}: {round(amount, 2)}")
                y -= 14
                if y < 60:
                    c.showPage()
                    y = height - 40
        else:
            c.drawString(50, y, "Нет данных по балансам.")
            y -= 18

        # Блок расходов
        y -= 10
        c.setFont(self.pdf_font_name, 12)
        c.drawString(40, y, "Расходы по категориям:")
        y -= 18
        c.setFont(self.pdf_font_name, 10)
        if expenses:
            for category, curr, total in expenses:
                c.drawString(50, y, f"- {category}: {round(total, 2)} {curr}")
                y -= 14
                if y < 60:
                    c.showPage()
                    y = height - 40
        else:
            c.drawString(50, y, "Нет данных по расходам.")
            y -= 18

        # Блок движений
        y -= 10
        c.setFont(self.pdf_font_name, 12)
        c.drawString(40, y, "Последние движения средств:")
        y -= 18
        c.setFont(self.pdf_font_name, 9)
        if movements:
            for amount, curr, kind, desc, rel_curr, rate, created, trip_id in movements:
                # Читаемое русское описание типа операции
                if kind == "trip_budget":
                    kind_ru = "Бюджет поездки"
                elif kind == "expense":
                    kind_ru = "Расход"
                elif kind == "convert_out":
                    kind_ru = "Обмен (списание)"
                elif kind == "convert_in":
                    kind_ru = "Обмен (зачисление)"
                else:
                    kind_ru = kind or "Операция"

                sign = "-" if amount < 0 else "+"
                line = f"{created}: {kind_ru} {sign}{abs(round(amount, 2))} {curr}"
                if rel_curr:
                    line += f" (вторая валюта: {rel_curr})"
                if rate:
                    line += f", курс: {round(rate, 4)}"
                title = trip_titles.get(trip_id)
                if title:
                    line += f" — поездка: {title}"
                if desc:
                    line += f" — {desc}"
                c.drawString(50, y, line[:120])
                y -= 12
                if y < 60:
                    c.showPage()
                    y = height - 40
        else:
            c.drawString(50, y, "Нет зафиксированных движений.")

        c.save()

    def get_trip_expense_summary(self, user_id, trip_id):
        """Суммарные расходы по конкретной поездке."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT category, currency, SUM(amount) AS total
                FROM expenses
                WHERE user_id = ? AND trip_id = ?
                GROUP BY category, currency
                ORDER BY currency, category
                ''',
                (user_id, trip_id),
            )
            return cursor.fetchall()

    def generate_trip_report_pdf(self, user_id, trip_id, file_path):
        """
        PDF-отчёт по конкретной поездке.

        В отчёт попадают только данные по выбранной поездке:
        - бюджет и рассчитанный остаток по бюджету;
        - агрегированные балансы по валютам этой поездки (на основе movements);
        - расходы по категориям для этой поездки;
        - все движения средств, привязанные к этой поездке.
        """
        with sqlite3.connect(self.db_name) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT name, from_country, to_country, currency, budget_amount, budget_currency, status, created_at
                FROM trips
                WHERE user_id = ? AND id = ?
                ''',
                (user_id, trip_id),
            )
            trip = cursor.fetchone()

        # Балансы и движения считаем только по данной поездке
        trip_balances = self.get_trip_balances(user_id, trip_id)
        expenses = self.get_trip_expense_summary(user_id, trip_id)
        trip_movements = self.get_trip_movements(user_id, trip_id)

        # Считаем текущий баланс по поездке как бюджет - траты (в валюте бюджета).
        # Конвертация между валютами внутри кошелька не уменьшает бюджет, поэтому она в остатке не учитывается.
        remaining_str = None
        if trip and trip["budget_amount"]:
            budget_curr = trip["budget_currency"] or trip["currency"]
            budget = float(trip["budget_amount"] or 0.0)
            spent = 0.0
            for category, curr, total in expenses:
                if total is None:
                    continue
                if curr == budget_curr:
                    spent += float(total)
                else:
                    rate = self.get_official_rate(curr, budget_curr)
                    if rate is None:
                        continue
                    spent += float(total) * rate
            remaining = budget - spent
            remaining_str = f"{round(remaining, 2)} {budget_curr} (из {round(budget, 2)} {budget_curr})"

        c = canvas.Canvas(file_path, pagesize=A4)
        width, height = A4
        y = height - 40

        c.setFont(self.pdf_font_name, 16)
        c.drawString(40, y, "Отчёт по поездке")
        y -= 25

        c.setFont(self.pdf_font_name, 10)
        c.drawString(40, y, f"Пользователь: {user_id}")
        y -= 15
        c.drawString(40, y, f"Дата формирования: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        y -= 15

        if trip:
            trip_name = trip["name"] or f"Поездка #{trip_id}"
            c.drawString(40, y, f"Название поездки: {trip_name}")
            y -= 15
            c.drawString(40, y, f"Маршрут: {trip['from_country']} -> {trip['to_country']} ({trip['currency']})")
            y -= 15
            c.drawString(40, y, f"Статус: {trip['status']}")
            y -= 15
            if trip["budget_amount"]:
                c.drawString(
                    40,
                    y,
                    f"Бюджет: {round(trip['budget_amount'], 2)} {trip['budget_currency']}",
                )
                y -= 15
                if remaining_str:
                    c.drawString(40, y, f"Текущий баланс по поездке: {remaining_str}")
                    y -= 20
        else:
            c.drawString(40, y, "Поездка не найдена.")
            y -= 20

        # Блок балансов по поездке
        c.setFont(self.pdf_font_name, 12)
        c.drawString(40, y, "Текущие балансы по этой поездке (по валютам):")
        y -= 18
        c.setFont(self.pdf_font_name, 10)
        if trip_balances:
            for curr, amount in trip_balances:
                # В отчёте явно показываем и нулевые остатки
                c.drawString(50, y, f"- {curr}: {round(amount or 0.0, 2)}")
                y -= 14
                if y < 60:
                    c.showPage()
                    y = height - 40
        else:
            c.drawString(50, y, "Нет данных по движениям для этой поездки.")
            y -= 18

        # Блок расходов по поездке
        y -= 10
        c.setFont(self.pdf_font_name, 12)
        c.drawString(40, y, "Расходы по категориям для этой поездки:")
        y -= 18
        c.setFont(self.pdf_font_name, 10)
        if expenses:
            for category, curr, total in expenses:
                c.drawString(50, y, f"- {category}: {round(total or 0.0, 2)} {curr}")
                y -= 14
                if y < 60:
                    c.showPage()
                    y = height - 40
        else:
            c.drawString(50, y, "Нет расходов, привязанных к этой поездке.")
            y -= 18

        # Блок движений средств по поездке
        y -= 10
        c.setFont(self.pdf_font_name, 12)
        c.drawString(40, y, "Все движения средств по этой поездке:")
        y -= 18
        c.setFont(self.pdf_font_name, 9)
        if trip_movements:
            for amount, curr, kind, desc, rel_curr, rate, created in trip_movements:
                if kind == "trip_budget":
                    kind_ru = "Бюджет поездки"
                elif kind == "expense":
                    kind_ru = "Расход"
                elif kind == "convert_out":
                    kind_ru = "Обмен (списание)"
                elif kind == "convert_in":
                    kind_ru = "Обмен (зачисление)"
                else:
                    kind_ru = kind or "Операция"

                sign = "-" if amount < 0 else "+"
                line = f"{created}: {kind_ru} {sign}{abs(round(amount or 0.0, 2))} {curr}"
                if rel_curr:
                    line += f" (вторая валюта: {rel_curr})"
                if rate:
                    line += f", курс: {round(rate, 4)}"
                if desc:
                    line += f" — {desc}"

                c.drawString(50, y, line[:120])
                y -= 12
                if y < 60:
                    c.showPage()
                    y = height - 40
        else:
            c.drawString(50, y, "Нет зафиксированных движений по этой поездке.")

        c.save()

    def get_trip_budget_status(self, user_id):
        """Сводка по активной поездке: бюджет, траты и остаток (в валюте бюджета)."""
        return self.get_active_trip_budget_status(user_id)

    def get_active_trip_budget_status(self, user_id):
        """
        Возвращает сводку по активной поездке:
        - currency: валюта бюджета
        - budget: запланированный бюджет
        - spent: уже потрачено (по расходам, в валюте бюджета)
        - remaining: остаток бюджета = budget - spent
        - ratio: доля остатка от бюджета (remaining / budget)
        """
        trip = self.get_active_trip(user_id)
        if not trip or not trip.get("budget_amount"):
            return None, None, None, None, None

        trip_id = trip["id"]
        budget_currency = trip.get("budget_currency") or trip["currency"]
        budget = float(trip["budget_amount"] or 0.0)

        expenses = self.get_trip_expense_summary(user_id, trip_id)
        spent = 0.0
        for category, curr, total in expenses:
            if total is None:
                continue
            if curr == budget_currency:
                spent += float(total)
            else:
                rate = self.get_official_rate(curr, budget_currency)
                if rate is None:
                    continue
                spent += float(total) * rate

        remaining = budget - spent
        ratio = (remaining / budget) if budget > 0 else None
        return budget_currency, budget, spent, remaining, ratio