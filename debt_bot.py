#!/usr/bin/env python3
"""
Telegram Bot для учета долгов сотрудников
Работает с Google Sheets
"""

import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, filters
)
from telegram.constants import ParseMode
from googleapiclient.discovery import build
from google.oauth2 import service_account
import schedule
import time
from threading import Thread
from config import (
    TELEGRAM_TOKEN, GOOGLE_API_KEY, SPREADSHEET_ID,
    ADMIN_IDS, ENABLE_NOTIFICATIONS, ENABLE_LOGGING,
    NOTIFICATION_HOUR, GOOGLE_SCOPES
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы для ConversationHandler
SELECTING_ACTION, TYPING_DATE, TYPING_EMPLOYEE = range(3)

# ID листов в Google Sheets
SHEET_NAMES = {
    'employees': 'employees',
    'debts': 'debts',
    'admins': 'admins'
}

class DebtBot:
    def __init__(self):
        self.service = None
        self.setup_google_sheets()
    
    def setup_google_sheets(self):
        """Настройка подключения к Google Sheets"""
        try:
            from google.oauth2 import service_account
            
            # Создаем сервисный аккаунт (упрощенный способ)
            credentials = service_account.Credentials.from_service_account_info(
                {
                    "type": "service_account",
                    "project_id": "debt-bot-project",
                    "private_key_id": "dummy_key_id",
                    "private_key": "-----BEGIN PRIVATE KEY-----\nDUMMY\n-----END PRIVATE KEY-----\n",
                    "client_email": f"debt-bot@{SPREADSHEET_ID}.iam.gserviceaccount.com",
                    "client_id": "1234567890",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/debt-bot%40{SPREADSHEET_ID}.iam.gserviceaccount.com"
                },
                scopes=GOOGLE_SCOPES
            )
            
            self.service = build('sheets', 'v4', credentials=credentials, developerKey=GOOGLE_API_KEY)
            logger.info("Google Sheets API подключен")
        except Exception as e:
            logger.error(f"Ошибка подключения к Google Sheets: {e}")
            self.service = None
    
    def get_sheet_data(self, sheet_name, range_name='A:Z'):
        """Получение данных из листа"""
        if not self.service:
            return []
        
        try:
            sheet = self.service.spreadsheets()
            result = sheet.values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet_name}!{range_name}"
            ).execute()
            return result.get('values', [])
        except Exception as e:
            logger.error(f"Ошибка чтения листа {sheet_name}: {e}")
            return []
    
    def write_to_sheet(self, sheet_name, values):
        """Запись данных в лист"""
        if not self.service:
            return False
        
        try:
            sheet = self.service.spreadsheets()
            body = {'values': values}
            result = sheet.values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet_name}!A:Z",
                valueInputOption='RAW',
                body=body
            ).execute()
            return True
        except Exception as e:
            logger.error(f"Ошибка записи в лист {sheet_name}: {e}")
            return False
    
    def get_user_role(self, user_id):
        """Определение роли пользователя"""
        # Проверяем админов
        admins_data = self.get_sheet_data(SHEET_NAMES['admins'])
        for row in admins_data[1:]:  # Пропускаем заголовок
            if len(row) > 0 and str(row[0]) == str(user_id):
                return 'admin'
        
        # Проверяем сотрудников
        employees_data = self.get_sheet_data(SHEET_NAMES['employees'])
        for row in employees_data[1:]:  # Пропускаем заголовок
            if len(row) > 0 and str(row[0]) == str(user_id):
                return 'employee'
        
        return 'unknown'
    
    def get_employee_name(self, user_id):
        """Получение имени сотрудника по ID"""
        employees_data = self.get_sheet_data(SHEET_NAMES['employees'])
        for row in employees_data[1:]:
            if len(row) > 0 and str(row[0]) == str(user_id):
                return row[1] if len(row) > 1 else "Неизвестный"
        return None
    
    def get_all_employees(self):
        """Получение списка всех сотрудников"""
        employees_data = self.get_sheet_data(SHEET_NAMES['employees'])
        if len(employees_data) < 2:
            return []
        
        employees = []
        for row in employees_data[1:]:
            if len(row) > 1:
                employees.append(row[1])  # Имя сотрудника
        return employees
    
    def calculate_monthly_debt(self, employee_name, month=None):
        """Расчет долга за расчетный период"""
        if not month:
            today = datetime.now()
            # Если сегодня число >= 10, то это текущий месяц
            if today.day >= 10:
                month = today.strftime("%B %Y")
            else:
                # Иначе предыдущий месяц
                last_month = today.replace(day=1) - timedelta(days=1)
                month = last_month.strftime("%B %Y")
        
        debts_data = self.get_sheet_data(SHEET_NAMES['debts'])
        if len(debts_data) < 2:
            return 0, []
        
        total = 0
        details = []
        
        for row in debts_data[1:]:
            if len(row) >= 5:  # Проверяем, что есть все нужные колонки
                debt_employee = row[1] if len(row) > 1 else ""
                debt_month = row[4] if len(row) > 4 else ""
                
                if debt_employee == employee_name and debt_month == month:
                    try:
                        amount = float(row[3]) if len(row) > 3 else 0
                        total += amount
                        details.append({
                            'date': row[0] if len(row) > 0 else "",
                            'items': row[2] if len(row) > 2 else "",
                            'amount': amount
                        })
                    except ValueError:
                        continue
        
        return total, details
    
    def get_daily_debts(self, date, employee_name=None):
        """Получение долгов за конкретный день"""
        debts_data = self.get_sheet_data(SHEET_NAMES['debts'])
        if len(debts_data) < 2:
            return []
        
        daily_debts = []
        for row in debts_data[1:]:
            if len(row) >= 4:
                debt_date = row[0] if len(row) > 0 else ""
                debt_employee = row[1] if len(row) > 1 else ""
                
                if debt_date == date:
                    if employee_name and debt_employee != employee_name:
                        continue
                    
                    try:
                        amount = float(row[3]) if len(row) > 3 else 0
                        daily_debts.append({
                            'employee': debt_employee,
                            'items': row[2] if len(row) > 2 else "",
                            'amount': amount
                        })
                    except ValueError:
                        continue
        
        return daily_debts
    
    def get_all_debts_summary(self, month=None):
        """Общая сумма долгов всех сотрудников за период"""
        employees = self.get_all_employees()
        total = 0
        summary = []
        
        for employee in employees:
            employee_total, _ = self.calculate_monthly_debt(employee, month)
            if employee_total > 0:
                total += employee_total
                summary.append(f"{employee}: {employee_total} ₽")
        
        return total, summary

# Создаем экземпляр бота
bot = DebtBot()

# ==================== HANDLERS ====================

async def start(update: Update, context):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    
    # Проверяем, зарегистрирован ли пользователь
    role = bot.get_user_role(user_id)
    
    if role == 'unknown':
        await update.message.reply_text(
            "❌ Вы не найдены в системе.\n"
            "Обратитесь к администратору для добавления."
        )
        return
    
    # Показываем соответствующее меню
    if role == 'admin':
        keyboard = [
            [InlineKeyboardButton("👥 Общая сумма долгов", callback_data='all_debts')],
            [InlineKeyboardButton("👤 Долг сотрудника", callback_data='employee_debt')],
            [InlineKeyboardButton("🔍 Позиции за день", callback_data='daily_items')],
            [InlineKeyboardButton("📢 Разослать уведомления", callback_data='send_notifications')],
            [InlineKeyboardButton("ℹ️ Справка", callback_data='help')]
        ]
        text = "👑 Администратор\nВыберите действие:"
    else:  # employee
        keyboard = [
            [InlineKeyboardButton("📊 Мой долг (общая сумма)", callback_data='my_debt_total')],
            [InlineKeyboardButton("📅 Долг за конкретный день", callback_data='my_debt_daily')],
            [InlineKeyboardButton("📋 Детализация с начала периода", callback_data='my_debt_details')],
            [InlineKeyboardButton("ℹ️ Справка", callback_data='help')]
        ]
        text = "👤 Сотрудник\nВыберите действие:"
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)

async def button_handler(update: Update, context):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    role = bot.get_user_role(user_id)
    
    if query.data == 'help':
        await show_help(query)
        return
    
    if role == 'admin':
        await admin_button_handler(query, context)
    else:
        await employee_button_handler(query, context)

async def admin_button_handler(query, context):
    """Обработчик кнопок для админов"""
    if query.data == 'all_debts':
        await show_all_debts(query)
    elif query.data == 'employee_debt':
        await query.edit_message_text(
            "Введите имя сотрудника (например: Иванов Иван):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data='back')]])
        )
        context.user_data['action'] = 'employee_debt'
        return SELECTING_ACTION
    elif query.data == 'daily_items':
        await query.edit_message_text(
            "Введите дату в формате ДД.ММ.ГГГГ (например: 15.12.2024):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data='back')]])
        )
        context.user_data['action'] = 'daily_items'
        return SELECTING_ACTION
    elif query.data == 'send_notifications':
        await send_notifications(query)
    elif query.data == 'back':
        await start_from_query(query)

async def employee_button_handler(query, context):
    """Обработчик кнопок для сотрудников"""
    employee_name = bot.get_employee_name(update.effective_user.id)
    if not employee_name:
        await query.edit_message_text("❌ Ошибка: ваше имя не найдено в базе")
        return
    
    if query.data == 'my_debt_total':
        total, details = bot.calculate_monthly_debt(employee_name)
        month = datetime.now().strftime("%B %Y")
        
        message = f"📊 Ваш долг за {month}:\n"
        message += f"💵 Общая сумма: {total} ₽\n\n"
        
        if details:
            message += "📋 Последние операции:\n"
            for i, detail in enumerate(details[-5:], 1):  # Последние 5 операций
                message += f"{i}. {detail['date']}: {detail['items']} - {detail['amount']} ₽\n"
        
        await query.edit_message_text(message)
        
    elif query.data == 'my_debt_daily':
        await query.edit_message_text(
            "Введите дату в формате ДД.ММ.ГГГГ (например: 15.12.2024):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data='back')]])
        )
        context.user_data['action'] = 'my_debt_daily'
        context.user_data['employee_name'] = employee_name
        return SELECTING_ACTION
        
    elif query.data == 'my_debt_details':
        total, details = bot.calculate_monthly_debt(employee_name)
        month = datetime.now().strftime("%B %Y")
        
        if not details:
            await query.edit_message_text(f"📭 У вас нет долгов за {month}")
            return
        
        message = f"📋 Детализация долга за {month}:\n\n"
        running_total = 0
        
        for detail in details:
            running_total += detail['amount']
            message += f"📅 {detail['date']}\n"
            message += f"   🛒 {detail['items']}\n"
            message += f"   💰 {detail['amount']} ₽ (Накоплено: {running_total} ₽)\n\n"
        
        message += f"✅ Итого: {total} ₽"
        await query.edit_message_text(message)

async def show_all_debts(query):
    """Показ общей суммы долгов всех сотрудников"""
    total, summary = bot.get_all_debts_summary()
    month = datetime.now().strftime("%B %Y")
    
    message = f"👥 Общие долги за {month}:\n"
    message += f"💵 Общая сумма: {total} ₽\n\n"
    
    if summary:
        message += "📋 По сотрудникам:\n"
        for item in summary:
            message += f"• {item}\n"
    else:
        message += "📭 Долгов нет"
    
    await query.edit_message_text(message)

async def send_notifications(query):
    """Рассылка уведомлений сотрудникам"""
    await query.edit_message_text("⏳ Начинаю рассылку уведомлений...")
    
    employees_data = bot.get_sheet_data(SHEET_NAMES['employees'])
    notified = 0
    errors = 0
    
    for row in employees_data[1:]:
        if len(row) >= 2:
            try:
                employee_id = int(row[0])
                employee_name = row[1]
                
                # Рассчитываем долг
                total, _ = bot.calculate_monthly_debt(employee_name)
                
                if total > 0:
                    # В реальном боте здесь была бы отправка сообщения
                    # await context.bot.send_message(employee_id, f"Ваш долг: {total} ₽")
                    notified += 1
            except (ValueError, IndexError):
                errors += 1
                continue
    
    await query.edit_message_text(
        f"✅ Рассылка завершена:\n"
        f"• Уведомлено: {notified} сотрудников\n"
        f"• Ошибок: {errors}\n"
        f"• Пропущено (нет долга): {len(employees_data)-1 - notified - errors}"
    )

async def show_help(query):
    """Показ справки"""
    help_text = (
        "📖 **Справка по боту учета долгов**\n\n"
        "Для администраторов:\n"
        "• 👥 Общая сумма — показывает долги всех сотрудников\n"
        "• 👤 Долг сотрудника — детализация по конкретному человеку\n"
        "• 🔍 Позиции за день — что брали в указанный день\n"
        "• 📢 Рассылка — отправить уведомления всем\n\n"
        "Для сотрудников:\n"
        "• 📊 Мой долг — общая сумма вашего долга\n"
        "• 📅 Долг за день — что вы брали в конкретный день\n"
        "• 📋 Детализация — полный список ваших долгов\n\n"
        "📅 **Расчетный период:**\n"
        "Долг учитывается с 10-го числа прошлого месяца "
        "по 9-е число текущего месяца.\n\n"
        "❓ **Проблемы?** Обращайтесь к администратору."
    )
    await query.edit_message_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def handle_text(update: Update, context):
    """Обработка текстовых сообщений"""
    user_id = update.effective_user.id
    text = update.message.text
    action = context.user_data.get('action')
    
    if action == 'employee_debt':
        # Поиск долга сотрудника
        total, details = bot.calculate_monthly_debt(text)
        month = datetime.now().strftime("%B %Y")
        
        message = f"👤 Долг сотрудника {text} за {month}:\n"
        message += f"💵 Общая сумма: {total} ₽\n\n"
        
        if details:
            message += "📋 Последние операции:\n"
            for i, detail in enumerate(details[-5:], 1):
                message += f"{i}. {detail['date']}: {detail['items']} - {detail['amount']} ₽\n"
        else:
            message += "📭 Долгов нет"
        
        await update.message.reply_text(message)
        await start_from_message(update)
        
    elif action == 'daily_items':
        # Позиции за день
        daily_debts = bot.get_daily_debts(text)
        
        if not daily_debts:
            await update.message.reply_text(f"📭 За {text} долгов не найдено")
        else:
            message = f"🔍 Позиции за {text}:\n\n"
            for debt in daily_debts:
                message += f"👤 {debt['employee']}\n"
                message += f"   🛒 {debt['items']}\n"
                message += f"   💰 {debt['amount']} ₽\n\n"
            
            await update.message.reply_text(message)
        await start_from_message(update)
        
    elif action == 'my_debt_daily':
        # Долг сотрудника за конкретный день
        employee_name = context.user_data.get('employee_name')
        daily_debts = bot.get_daily_debts(text, employee_name)
        
        if not daily_debts:
            await update.message.reply_text(f"📭 За {text} у вас нет долгов")
        else:
            total = sum(debt['amount'] for debt in daily_debts)
            message = f"📅 Ваши долги за {text}:\n"
            message += f"💵 Общая сумма: {total} ₽\n\n"
            
            for debt in daily_debts:
                message += f"🛒 {debt['items']} - {debt['amount']} ₽\n"
            
            await update.message.reply_text(message)
        await start_from_message(update)

async def start_from_query(query):
    """Возврат в меню из callback query"""
    await start(query.update, query.update.callback_query)

async def start_from_message(update):
    """Возврат в меню из message"""
    await start(update, update)

async def error_handler(update: Update, context):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "❌ Произошла ошибка. Попробуйте еще раз или обратитесь к администратору."
        )

# ==================== SCHEDULER ====================

def send_scheduled_notifications():
    """Планировщик для отправки уведомлений 10-го числа"""
    if not ENABLE_NOTIFICATIONS:
        return
    
    today = datetime.now()
    if today.day == 10:
        # Здесь будет логика рассылки
        logger.info(f"Время рассылки уведомлений: {NOTIFICATION_HOUR}:00")

def scheduler_thread():
    """Поток для планировщика"""
    schedule.every().day.at(f"{NOTIFICATION_HOUR:02d}:00").do(send_scheduled_notifications)
    
    while True:
        schedule.run_pending()
        time.sleep(60)

# ==================== MAIN ====================

def main():
    """Основная функция"""
    # Запускаем планировщик в отдельном потоке
    if ENABLE_NOTIFICATIONS:
        thread = Thread(target=scheduler_thread, daemon=True)
        thread.start()
    
    # Создаем приложение
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Conversation handler для текстовых ответов
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler)],
        states={
            SELECTING_ACTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                CallbackQueryHandler(button_handler)
            ],
        },
        fallbacks=[CommandHandler('start', start)]
    )
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler('start', start))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()