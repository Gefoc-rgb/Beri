import os
import random
import logging
import uuid
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Updater, CommandHandler, CallbackContext, CallbackQueryHandler,
    MessageHandler, Filters, ConversationHandler
)
from sqlalchemy import create_engine, Column, Integer, String, Boolean, func
from sqlalchemy.orm import sessionmaker, declarative_base

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")

# Константы
VIDEO_PRICE = 2
REFERRAL_REWARD = 10
ADMIN_STATES = {}

# Инициализация БД
Base = declarative_base()
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True)
    username = Column(String(100), default="")
    first_name = Column(String(100), default="")
    balance = Column(Integer, default=0)
    referral_code = Column(String(10), unique=True)
    invited_by = Column(Integer, default=0)
    is_admin = Column(Boolean, default=False)
    is_subscribed = Column(Boolean, default=False)
    join_date = Column(String(10), default=datetime.now().strftime("%Y-%m-%d"))

class Video(Base):
    __tablename__ = 'videos'
    id = Column(Integer, primary_key=True)
    file_id = Column(String(200))
    added_date = Column(String(10), default="")

# Создание таблиц
Base.metadata.create_all(engine)

# Вспомогательные функции
def check_subscription(user_id: int, context: CallbackContext) -> bool:
    if not CHANNEL_ID:
        return True
    try:
        member = context.bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Subscription check failed: {e}")
        return False

def subscription_required(func):
    def wrapper(update: Update, context: CallbackContext):
        user_id = update.effective_user.id
        
        if update.message.text.startswith('/start'):
            return func(update, context)
            
        session = Session()
        user = session.query(User).filter_by(user_id=user_id).first()
        
        if not user:
            update.message.reply_text("⚠️ Пожалуйста, начните с /start")
            return
            
        if not user.is_subscribed:
            user.is_subscribed = check_subscription(user_id, context)
            session.commit()
            
        if not user.is_subscribed:
            show_subscription_alert(update)
            return
            
        return func(update, context)
    return wrapper

def show_subscription_alert(update: Update):
    keyboard = [
        [InlineKeyboardButton("🔥 Подписаться", url=f"https://t.me/{CHANNEL_ID[1:]}")],
        [InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sub")]
    ]
    update.message.reply_text(
        "📢 Для использования бота подпишитесь на наш канал:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def create_main_menu(is_admin=False):
    menu = [
        ["🎬 Получить видео", "💰 Баланс"],
        ["👥 Рефералы", "ℹ️ Мои данные"]
    ]
    if is_admin:
        menu.append(["⚙️ Админ-панель"])
    return ReplyKeyboardMarkup(menu, resize_keyboard=True)

def notify_referrer(referrer_id: int, new_user: str, context: CallbackContext):
    try:
        context.bot.send_message(
            referrer_id,
            f"🎉 Новый реферал: {new_user}!\n"
            f"💎 +{REFERRAL_REWARD} монет на ваш баланс!"
        )
    except Exception as e:
        logger.error(f"Referral notification failed: {e}")

# Основные функции бота
@subscription_required
def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name
    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    
    # Регистрация нового пользователя
    if not user:
        ref_code = context.args[0] if context.args else None
        referral_code = str(uuid.uuid4())[:8]
        invited_by = 0
        
        if ref_code:
            inviter = session.query(User).filter_by(referral_code=ref_code).first()
            if inviter:
                invited_by = inviter.user_id
                inviter.balance += REFERRAL_REWARD
                session.commit()
                notify_referrer(inviter.user_id, user_name, context)
        
        new_user = User(
            user_id=user_id,
            username=update.effective_user.username,
            first_name=update.effective_user.first_name,
            referral_code=referral_code,
            invited_by=invited_by,
            is_admin=(user_id == ADMIN_ID)
        session.add(new_user)
        session.commit()
        user = new_user
    
    # Формирование ответа
    ref_link = f"https://t.me/{context.bot.username}?start={user.referral_code}"
    menu = create_main_menu(user.is_admin)
    
    update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        f"💎 Баланс: {user.balance} монет\n"
        f"🔗 Реферальная ссылка: {ref_link}\n\n"
        f"🎬 Видео: {VIDEO_PRICE} монет | 👥 Реферал: +{REFERRAL_REWARD} монет",
        reply_markup=menu
    )

@subscription_required
def get_video(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    
    if not user:
        return start(update, context)
        
    if user.balance < VIDEO_PRICE:
        update.message.reply_text(
            f"❌ Недостаточно монет! Нужно: {VIDEO_PRICE}\n"
            f"💎 Ваш баланс: {user.balance}\n\n"
            f"👥 Пригласите друга и получите +{REFERRAL_REWARD} монет!"
        )
        return
        
    video = session.query(Video).order_by(func.random()).first()
    if not video:
        update.message.reply_text("😢 Видео временно нет в базе")
        return
        
    context.bot.send_video(
        chat_id=user_id,
        video=video.file_id,
        caption=f"🎥 Ваше видео!\n💎 Списано: {VIDEO_PRICE} монет"
    )
    
    user.balance -= VIDEO_PRICE
    session.commit()
    
    update.message.reply_text(
        f"✅ Видео отправлено!\n💎 Остаток: {user.balance} монет\n\n"
        "Хотите ещё? Нажмите 🎬 Получить видео"
    )

@subscription_required
def user_info(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    
    if not user:
        return start(update, context)
        
    ref_count = session.query(User).filter_by(invited_by=user_id).count()
    
    update.message.reply_text(
        f"👤 Ваши данные:\n\n"
        f"🆔 ID: {user.user_id}\n"
        f"👤 Имя: {user.first_name}\n"
        f"💎 Баланс: {user.balance} монет\n"
        f"👥 Рефералов: {ref_count}\n"
        f"🔗 Код: {user.referral_code}\n"
        f"📅 Регистрация: {user.join_date}"
    )

# Админ-функции
@subscription_required
def admin_panel(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    session = Session()
    user = session.query(User).filter_by(user_id=user_id).first()
    
    if not user or not user.is_admin:
        update.message.reply_text("🚫 Доступ запрещён")
        return
        
    keyboard = [
        ["📊 Статистика", "👤 Пользователи"],
        ["🎬 Видео", "💎 Выдать монеты"],
        ["🔙 Главное меню"]
    ]
    update.message.reply_text(
        "⚙️ Админ-панель:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

@subscription_required
def admin_stats(update: Update, context: CallbackContext):
    session = Session()
    total_users = session.query(User).count()
    total_videos = session.query(Video).count()
    new_today = session.query(User).filter(
        User.join_date == datetime.now().strftime("%Y-%m-%d")
    ).count()
    
    update.message.reply_text(
        "📊 Статистика бота:\n\n"
        f"👤 Пользователей: {total_users}\n"
        f"👤 Новых сегодня: {new_today}\n"
        f"🎬 Видео в базе: {total_videos}"
    )

@subscription_required
def add_coins_start(update: Update, context: CallbackContext):
    update.message.reply_text("👤 Введите ID пользователя:")
    return "GET_USER"

def add_coins_get_user(update: Update, context: CallbackContext):
    try:
        user_id = int(update.message.text)
        session = Session()
        user = session.query(User).filter_by(user_id=user_id).first()
        
        if not user:
            update.message.reply_text("❌ Пользователь не найден")
            return ConversationHandler.END
            
        ADMIN_STATES[update.effective_user.id] = {"target_user": user_id}
        update.message.reply_text(f"👤 Пользователь: {user.first_name}\n💎 Введите количество монет:")
        return "GET_AMOUNT"
    except ValueError:
        update.message.reply_text("❌ Неверный ID. Введите число:")
        return "GET_USER"

def add_coins_get_amount(update: Update, context: CallbackContext):
    try:
        amount = int(update.message.text)
        if amount <= 0:
            update.message.reply_text("❌ Введите положительное число:")
            return "GET_AMOUNT"
            
        admin_id = update.effective_user.id
        user_id = ADMIN_STATES.get(admin_id, {}).get("target_user")
        
        if not user_id:
            update.message.reply_text("❌ Ошибка сессии")
            return ConversationHandler.END
            
        session = Session()
        user = session.query(User).filter_by(user_id=user_id).first()
        admin = session.query(User).filter_by(user_id=admin_id).first()
        
        if user and admin:
            user.balance += amount
            session.commit()
            
            # Уведомляем пользователя
            try:
                context.bot.send_message(
                    user_id,
                    f"🎉 Администратор выдал вам {amount} монет!\n"
                    f"💎 Новый баланс: {user.balance}"
                )
            except:
                pass
            
            update.message.reply_text(
                f"✅ Выдано {amount} монет пользователю {user.first_name}!\n"
                f"💎 Новый баланс: {user.balance}"
            )
        else:
            update.message.reply_text("❌ Пользователь не найден")
    except ValueError:
        update.message.reply_text("❌ Введите число:")
        return "GET_AMOUNT"
        
    return ConversationHandler.END

@subscription_required
def add_video(update: Update, context: CallbackContext):
    update.message.reply_text("📤 Отправьте видео файлом:")
    return "GET_VIDEO"

def handle_video(update: Update, context: CallbackContext):
    video = update.message.video
    if not video:
        update.message.reply_text("❌ Пожалуйста, отправьте видео файлом")
        return "GET_VIDEO"
        
    session = Session()
    new_video = Video(
        file_id=video.file_id,
        added_date=datetime.now().strftime("%Y-%m-%d")
    )
    session.add(new_video)
    session.commit()
    
    total_videos = session.query(Video).count()
    update.message.reply_text(f"✅ Видео добавлено! Всего видео: {total_videos}")
    return ConversationHandler.END

# Главная функция
def main():
    # Проверка конфигурации
    if not TOKEN:
        logger.error("Токен бота не установлен!")
        return
        
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    # Обработчики команд
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("admin", admin_panel))
    
    # Обработчики сообщений
    dp.add_handler(MessageHandler(Filters.regex(r'🎬 Получить видео'), get_video))
    dp.add_handler(MessageHandler(Filters.regex(r'💰 Баланс'), user_info))
    dp.add_handler(MessageHandler(Filters.regex(r'ℹ️ Мои данные'), user_info))
    dp.add_handler(MessageHandler(Filters.regex(r'⚙️ Админ-панель'), admin_panel))
    dp.add_handler(MessageHandler(Filters.regex(r'📊 Статистика'), admin_stats))
    
    # Обработчики подписки
    dp.add_handler(CallbackQueryHandler(
        lambda u,c: check_subscription_callback(u,c), 
        pattern="^check_sub$"
    ))
    
    # Админ-диалоги
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(Filters.regex(r'💎 Выдать монеты'), add_coins_start),
            MessageHandler(Filters.regex(r'🎬 Видео'), add_video)
        ],
        states={
            "GET_USER": [MessageHandler(Filters.text, add_coins_get_user)],
            "GET_AMOUNT": [MessageHandler(Filters.text, add_coins_get_amount)],
            "GET_VIDEO": [MessageHandler(Filters.video, handle_video)]
        },
        fallbacks=[]
    )
    dp.add_handler(conv_handler)

    # Запуск бота
    if 'RENDER' in os.environ:
        # Для Render.com и аналогичных
        port = int(os.environ.get('PORT', 5000))
        updater.start_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TOKEN,
            webhook_url=f"https://your-app-name.onrender.com/{TOKEN}"
        )
        logger.info("Бот запущен в режиме webhook")
    else:
        # Для локального тестирования
        updater.start_polling()
        logger.info("Бот запущен в режиме polling")

    updater.idle()

if __name__ == "__main__":
    main()
